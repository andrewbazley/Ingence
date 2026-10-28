"""The one function a route calls: a query in, result cards out, everything explained.

The flow has eight steps, each a module of its own:

1. read the query with the census's own tokeniser (``query``);
2. expand each concept to its sense, spellings and variants (``expand``);
3. match in SQL: the concepts become a small temporary table joined to the projection's
   ``terms``, sorted by token so Parquet prunes to the row groups that can match;
4. roll up each match to the unit it belongs to through ``homes``. The unit is its experiment,
   analysis folder or program, or a product file of its own. This step is SQL. Only a match that
   belongs to none of these rolls up every ancestor folder through ``closure``;
5. choose units: homes as they are, and homeless matches by the smallest covering folder
   (``units``);
6. rank by the tuple (``rank``), leaving out what a person hid from similar searches
   (``hidden``) and counting it;
7. present: nest, collapse copies, fold series, cap products (``units``);
8. tag and explain the cards on this page only (``tags``, and the explanation query here).

A card's contents are closed. The response carries counts, chips and the first few matching places.
The page asks the Explore page's children route for a unit's files only when a person opens the
unit.

Every step that could grow is bounded. Steps 3 and 4 are SQL over the projection and never
materialise rows in Python. Step 5 reads one row per (folder, concept) for the folders that
matched. On a common word that can be thousands of rows, and on a specific query it is tens. Steps 6
to 8 touch only the page. A query whose candidates exceed ``max_candidates`` says so in the
response and does not truncate silently.

The search covers the catalog's run, and the response names it. While a definition's inference
runs, the server hands a search the settled run, as it does for every page built on inference. The
search covers the words that a definition just taught once the projection of the new run is built,
and the response says so too.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from ingence.core.extensions import canonical_extension
from ingence.dictionary.normalize import AMBIGUOUS_DATE_JOINER
from ingence.dictionary.quality_marks import cancel_pattern, quality_marks
from ingence.search.expand import FOUND_BY_ORDER, ROUTE_ORDER, FoundBy, MatchRoute, expand
from ingence.search.hidden import DEFAULT_SIMILAR_AT, concept_keys, hidden_for, read_hidden
from ingence.search.projection import (
    UNIT_DOCUMENT_SQL,
    PoolVocabulary,
    build_projection,
    held_projection,
    hold_folder,
    hold_projection,
    projection_is_current,
    read_projection,
)
from ingence.search.query import parse_query, read_day
from ingence.search.rank import found_by, rank, tie_path
from ingence.search.scale import DEFAULT_SCALE, ScaleParameters, UnitKind, scale_from_dictionary
from ingence.search.tags import DEFAULT_TIME_RULE, OWN_CLOCK_SQL, readable_title, tags_for
from ingence.search.units import HitRow, UnitFacts, evidence_from_rows, present, smallest_covering
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from collections.abc import Collection, Generator, Mapping, Sequence
    from pathlib import Path

    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.quality_marks import QualityMark
    from ingence.dictionary.store import DictionaryStore
    from ingence.search.expand import Concept
    from ingence.search.projection import Projection
    from ingence.search.query import FieldFilter
    from ingence.search.rank import UnitEvidence
    from ingence.storage.catalog import Catalog

__all__ = [
    "DEFAULT_CO_OCCURRING_LIMIT",
    "DEFAULT_CO_OCCURRING_MINIMUM",
    "DEFAULT_FALLBACK_REACH",
    "DEFAULT_INSIDE_MIN_LENGTH",
    "DEFAULT_MAX_CANDIDATES",
    "load_concepts",
    "match",
    "merge_detail",
    "prepare_projection",
    "search",
    "search_phases",
    "suggest",
]

#: The most candidate folders that step 5 reads. This is a parameter, and the response says when
#: the search reached it.
DEFAULT_MAX_CANDIDATES = 5000

#: How far beneath a plain folder a match may lie and still count for it. This is the fallback for a
#: match that belongs to no unit. Without a limit, a top-level container "covers" everything typed
#: by adding up matches in unrelated experiments far beneath it, and outranks every real answer.
#: A measurement on a pool of experiments that the rules did not all place found exactly that. The
#: value follows that measurement and has no other measurement behind it. A large value removes the
#: limit.
DEFAULT_FALLBACK_REACH = 2

#: The number of direct files that two folders must share by name and size before the search shows
#: one as a copy of the other. A single-file folder that agrees with another says almost nothing.
DEFAULT_COPY_MINIMUM_FILES = 3

#: The fewest matched units that a word must share with the query to be listed beside it.
#: Pointwise mutual information is largest for the rarest words. A word seen in one matched unit
#: and nowhere else scores as high as a word can, from a single coincidence. Two is the least count
#: that is a pattern and not one name. No measurement supports the value beyond that reason.
DEFAULT_CO_OCCURRING_MINIMUM = 2

#: The number of words that come with the query and are listed. They form a strip beside the
#: results and not a page.
DEFAULT_CO_OCCURRING_LIMIT = 12

#: The shortest typed word that the search finds at the start of a name's word or inside a name.
#: The default is four letters. The comment beside the ``search`` setting gives the measured cost of
#: three letters. The three defaults here are the project setting ``search``'s values
#: (``core/settings.py``), which the route passes. A caller of the engine alone gets them.
DEFAULT_INSIDE_MIN_LENGTH = 4


def _believable_day(started_at: object) -> str | None:
    """Return the run's own start as a timestamp that a query can compare with, or None.

    The result is None where the value is not a time. The caller writes a run's ``started_at``, and
    it is not guaranteed to parse. A test's stand-in, for example, is a single letter. A value that
    is not a time bounds nothing, and it does not fail every search.
    """
    text = str(started_at or "").replace("T", " ").removesuffix("Z")[:19]
    try:
        dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return text


def _load_concepts(catalog: Catalog, concepts: Sequence[Concept]) -> None:
    rows: list[tuple[Any, ...]] = []
    for concept in concepts:
        for match in concept.matches:
            rows.append(
                (
                    concept.index,
                    concept.excluded,
                    match.route.value,
                    ROUTE_ORDER.index(match.route),
                    match.token,
                    match.prefix,
                    match.term_id,
                    concept.token,
                )
            )
    catalog.query(
        "CREATE OR REPLACE TEMP TABLE q (concept INTEGER, excluded BOOLEAN, route VARCHAR, "
        "route_rank INTEGER, token VARCHAR, prefix BOOLEAN, term_id VARCHAR, typed VARCHAR)"
    )
    if rows:
        catalog.connection.executemany("INSERT INTO q VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


#: How a stored word was found, as a way that a card says. The SQL works from the route that
#: reached the word and from the stored word against the typed one. It runs over ``q`` and the
#: matched word ``{word}``. A sense reached through the very spelling typed is the spelling. A
#: plural or singular that the pool uses reads as starts-with. A piece of a word kept whole is
#: starts-with where the word begins with it, and plain name otherwise. The numbers are positions
#: in ``FOUND_BY_ORDER``.
_WAY_SQL = """CASE
    WHEN q.route IN ('token', 'quantity', 'date') THEN 0
    WHEN q.route IN ('sense', 'ambiguous_sense') AND {word} = q.typed THEN 0
    WHEN q.route IN ('sense', 'ambiguous_sense') THEN 1
    WHEN q.route IN ('starts_with', 'variant') THEN 2
    WHEN q.route = 'piece' AND starts_with({word}, q.typed) THEN 2
    WHEN q.route IN ('piece', 'plain_name') THEN 3
    ELSE 4
END"""


def _match(catalog: Catalog, projection: Projection) -> None:
    """Step 3: find every stored token that matches a concept.

    The result has one row per (entry, concept, route).
    """
    terms = projection.table("terms")
    way = _WAY_SQL.format(word="t.token")
    selected = (
        "t.entry_id, t.parent_id, t.entry_type, t.source, t.raw_value, "
        f"q.concept, q.excluded, q.route, q.route_rank, t.token, {way} AS way_rank"
    )
    # Only these two routes match by more than equality. Every other route matches by its token.
    by_equality = "q.route NOT IN ('starts_with', 'plain_name')"
    parts = [
        f"SELECT {selected} FROM {terms} t JOIN q ON t.token = q.token "  # noqa: S608
        f"WHERE q.token IS NOT NULL AND NOT q.prefix AND {by_equality}",
        f"SELECT {selected} FROM {terms} t JOIN q ON t.term_id = q.term_id "  # noqa: S608
        "WHERE q.term_id IS NOT NULL",
        # A day and a month that could swap name both readings, in the name's field order:
        # ``a or b``. A date typed matches either reading, the second after its joiner.
        f"SELECT {selected} FROM {terms} t JOIN q ON q.prefix AND t.component_type = 'date' "  # noqa: S608
        f"AND (starts_with(t.token, q.token) OR contains(t.token, '{AMBIGUOUS_DATE_JOINER}' "
        "|| q.token))",
        # A word in a name that begins with the word typed. This covers a name's words only.
        f"SELECT {selected} FROM {terms} t JOIN q ON q.route = 'starts_with' "  # noqa: S608
        "AND t.source = 'name' AND starts_with(t.token, q.token) AND t.token <> q.token",
        # The word typed anywhere inside a name. This covers the names as written, casefolded.
        "SELECT e.entry_id, e.parent_entry_id AS parent_id, e.entry_type, 'name' AS source, "  # noqa: S608
        "e.name AS raw_value, q.concept, q.excluded, q.route, q.route_rank, q.token, "
        f"{_WAY_SQL.format(word='q.token')} AS way_rank FROM entries e JOIN q "
        "ON q.route = 'plain_name' AND NOT e.is_deleted AND contains(lower(e.name), q.token)",
    ]
    if (projection.path / "notes.parquet").is_file():
        notes = projection.table("notes")
        parts.append(
            "SELECT n.entry_id, NULL AS parent_id, n.entry_type, 'note' AS source, n.raw_value, "  # noqa: S608
            f"q.concept, q.excluded, q.route, q.route_rank, n.token, "
            f"{_WAY_SQL.format(word='n.token')} AS way_rank FROM {notes} n "
            f"JOIN q ON n.token = q.token WHERE q.token IS NOT NULL AND NOT q.prefix "
            f"AND {by_equality}"
        )
    catalog.query("CREATE OR REPLACE TEMP TABLE hit AS " + " UNION ALL ".join(parts))


#: The two steps that a question's reading shares with a search (``search/ask.py``).
load_concepts = _load_concepts
match = _match


def _roll_up(
    catalog: Catalog, projection: Projection, *, max_candidates: int, fallback_reach: int
) -> list[dict[str, Any]]:
    """Step 4: return each (unit, concept) that a match reaches, with how and where it was found.

    A match with a home counts for its home alone. A product file with no home is its own unit. Only
    a match with neither rolls up every ancestor folder.

    A unit's own name carries a word only as typed, by a sense or as a piece of it. A name that
    holds the word at the start of a longer word, or inside one, does not make the unit ``named``
    for the word. The roll-up counts each entry by its strongest way (``ways``). A unit lists
    another way only where another of its entries was found only that way.
    """
    closure = projection.table("closure")
    homes = projection.table("homes")
    products = projection.table("products")
    cursor = catalog.query(
        f"""
        WITH best AS (
            SELECT entry_id, any_value(entry_type) AS entry_type, concept,
                   any_value(excluded) AS excluded, min(route_rank) AS route_rank,
                   bool_or(source IN ('name', 'note')) AS in_name,
                   bool_or(source IN ('name', 'note')
                           AND route NOT IN ('starts_with', 'plain_name')) AS names_it,
                   min(way_rank) AS way_rank
            FROM hit GROUP BY entry_id, concept
        ),
        frequency AS (
            SELECT concept,
                   count(DISTINCT CASE WHEN entry_type = 'directory' THEN entry_id
                                       ELSE parent_id END) AS folders
            FROM hit GROUP BY concept
        ),
        alone AS (SELECT entry_id FROM {products} WHERE home_id IS NULL),
        homed AS (
            SELECT h.home_id AS unit_id, b.concept, b.excluded, b.route_rank, b.in_name,
                   b.entry_id = h.home_id AS own, b.way_rank, b.names_it
            FROM best b JOIN {homes} h ON h.entry_id = b.entry_id
        ),
        own_units AS (
            SELECT b.entry_id AS unit_id, b.concept, b.excluded, b.route_rank, b.in_name,
                   true AS own, b.way_rank, b.names_it
            FROM best b WHERE b.entry_id IN (SELECT entry_id FROM alone)
        ),
        loose AS (
            SELECT b.* FROM best b
            WHERE b.entry_id NOT IN (SELECT entry_id FROM {homes})
              AND b.entry_id NOT IN (SELECT entry_id FROM alone)
        ),
        placed AS (
            SELECT * FROM homed
            UNION ALL SELECT * FROM own_units
            UNION ALL
            SELECT entry_id, concept, excluded, route_rank, in_name, true, way_rank, names_it
            FROM loose WHERE entry_type = 'directory'
            UNION ALL
            SELECT c.ancestor_id, l.concept, l.excluded, l.route_rank, l.in_name, false,
                   l.way_rank, l.names_it
            FROM loose l JOIN {closure} c ON c.entry_id = l.entry_id AND c.distance <= ?
        )
        SELECT p.unit_id AS folder_id, p.concept, any_value(p.excluded) AS excluded,
               min(p.route_rank) AS route_rank,
               bool_or(p.own AND p.names_it) AS named,
               NOT bool_or(p.in_name) AS content_only,
               any_value(f.folders) AS folder_frequency,
               min(p.way_rank) AS way_rank,
               list_sort(list(DISTINCT p.way_rank)) AS ways
        FROM placed p JOIN frequency f USING (concept)
        GROUP BY p.unit_id, p.concept
        ORDER BY p.unit_id, p.concept
        """,  # noqa: S608
        [fallback_reach],
    )
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _unit_filter(
    filters: Sequence[FieldFilter],
    *,
    stage_rules: Sequence[str],
    stage_values: Mapping[str, Sequence[str]],
    early_stages: Sequence[str],
    time_rule: str | None,
    plausible_from: str,
    neighbourhood_rules: Sequence[str] = (),
    quality: Mapping[str, QualityMark] | None = None,
    topics: Mapping[str, Collection[str]] | None = None,
) -> tuple[list[str], list[Any]]:
    """Return each filter as an SQL condition on a candidate folder ``u.folder_id``.

    A file filter reads "the unit holds at least one file that ...". A person who types
    ``FRAP ext:nd2`` wants experiments with ND2 files in them, and not the ND2 files alone.

    ``stage:`` reads the stage rules' claims alone, with the value as the Dictionary spells the
    stage (``stage_values``). A stage where data enters finds every such stage: ``stage:raw`` finds
    a file that the rules placed at ``acquisition``.

    ``after:`` and ``before:`` read a file's earliest credible time where ``time_rule`` concluded
    in the run. A time that the rule set aside counts as no time. They read a file's own time only
    where no rule reads time, and then only from ``plausible_from`` on. The file's own time is its
    content's clock or its modification time, and never the date that its name spells. A name's
    date only widens the range, where ``neighbourhood_rules`` name the rules whose claims hold the
    neighbourhood that a name gives (``name_date_neighbourhood``). A unit that holds an entry
    (itself included) whose neighbourhood reaches into the range matches too, and the caller
    labels it so.

    ``mark:`` reads the quality marks that ``quality`` holds. A unit matches when its own name, or a
    name beneath it, carries a word of that mark that the word before it does not cancel
    (``not sick``). The filter is a person's choice to narrow and never withholds anything.

    ``topic:`` keeps a unit that is, or lies inside, an experiment of the person's topic.
    ``topics`` gives each typed value (casefolded) the experiments of the topic or topics that it
    names. The search reads them from the Record when it runs.
    """
    clauses: list[str] = []
    parameters: list[Any] = []
    # ``reach_v`` is the closure with every entry's own row added, so a product unit (a file) is
    # tested as itself and a folder unit through everything beneath it. A file is one that the
    # filesystem holds. A part recovered from inside a workbook is beneath the workbook's folders in
    # the closure, and it is never one of their files.
    holds = (
        "EXISTS (SELECT 1 FROM reach_v c JOIN entries e ON e.entry_id = c.entry_id "  # noqa: S608
        "WHERE c.ancestor_id = u.folder_id AND e.entry_type = 'file' "
        f"AND {filesystem_entry('e')} AND {{condition}})"
    )
    for item in filters:
        condition: str | None = None
        values: list[Any] = []
        if item.name == "ext":
            condition, values = "e.extension = ?", [canonical_extension(item.value)]
        elif item.name == "type":
            condition = (
                "EXISTS (SELECT 1 FROM terms_v t WHERE t.entry_id = e.entry_id "
                "AND t.component_type = 'extension' AND t.term ILIKE ?)"
            )
            values = [f"%{item.value}%"]
        elif item.name == "stage":
            wanted = list(stage_values.get(item.value.casefold(), (item.value.casefold(),)))
            if set(wanted) & set(early_stages):
                wanted = sorted(set(wanted) | set(early_stages))
            # Only runs of "?" are interpolated. The values are bound (S608).
            rule_marks = ", ".join("?" for _ in stage_rules) or "NULL"
            value_marks = ", ".join("?" for _ in wanted)
            condition = (
                "EXISTS (SELECT 1 FROM claims cl WHERE cl.subject = e.entry_id "  # noqa: S608
                "AND cl.resolution_status = 'resolved' "
                f"AND cl.rule_id IN ({rule_marks}) "
                f"AND lower(cl.object_reference) IN ({value_marks}))"
            )
            values = [*stage_rules, *wanted]
        elif item.name in ("after", "before"):
            comparison = ">=" if item.name == "after" else "<"
            seen = (
                "(SELECT min(epoch_ns(TRY_CAST(json_extract_string(cl.object_reference, '$.at') "
                "AS TIMESTAMP))) FROM claims cl WHERE cl.subject = e.entry_id AND cl.rule_id = ? "
                "AND cl.resolution_status = 'resolved')"
                if time_rule
                else f"(CASE WHEN {OWN_CLOCK_SQL} >= "
                f"epoch_ns(CAST(? AS TIMESTAMP)) THEN {OWN_CLOCK_SQL} END)"
            )
            condition = f"{seen} {comparison} epoch_ns(CAST(? AS TIMESTAMP))"
            day = read_day(item.value) or item.value
            values = [time_rule or plausible_from, day]
            if neighbourhood_rules and not item.negated:
                # A neighbourhood only widens a range and never hides a unit. A negated date filter
                # reads the files' times alone. The condition takes the neighbourhood's last day on
                # or after the start, or its first day before the end. ISO days compare as text.
                edge, reaches = ("$.to", ">=") if item.name == "after" else ("$.from", "<")
                # Only runs of "?" and the two fixed spellings above are interpolated (S608).
                rule_marks = ", ".join("?" for _ in neighbourhood_rules)
                named = (
                    "EXISTS (SELECT 1 FROM reach_v c JOIN claims cl ON cl.subject = c.entry_id "  # noqa: S608
                    f"WHERE c.ancestor_id = u.folder_id AND cl.rule_id IN ({rule_marks}) "
                    "AND cl.resolution_status = 'resolved' "
                    f"AND json_extract_string(cl.object_reference, '{edge}') {reaches} ?)"
                )
                clauses.append(
                    ("NOT " if item.negated else "")
                    + f"({holds.format(condition=condition)} OR {named})"
                )
                parameters.extend([*values, *neighbourhood_rules, day[:10]])
                continue
        elif item.name == "mark":
            chosen = [
                held for held in (quality or {}).values() if held.mark == item.value.casefold()
            ]
            # Only runs of "?" are interpolated. The terms and the pattern are bound (S608).
            term_marks = ", ".join("?" for _ in chosen) or "NULL"
            pattern = cancel_pattern(chosen)
            cancelled = "AND NOT regexp_matches(lower(x.name), ?)" if pattern else ""
            marked = (
                "EXISTS (SELECT 1 FROM reach_v c JOIN terms_v t ON t.entry_id = c.entry_id "  # noqa: S608
                "JOIN entries x ON x.entry_id = c.entry_id WHERE c.ancestor_id = u.folder_id "
                "AND t.source = 'name' AND t.resolution_status = 'resolved' "
                f"AND t.term IN ({term_marks}) {cancelled})"
            )
            clauses.append(("NOT " if item.negated else "") + marked)
            parameters.extend([*(held.term for held in chosen), *([pattern] if pattern else [])])
            continue
        elif item.name == "in":
            # The unit's own path: a folder's, or for a product (a file) the folders it sits in.
            clauses.append(
                ("NOT " if item.negated else "")  # noqa: S608
                + "EXISTS (SELECT 1 FROM entries x WHERE x.entry_id = u.folder_id "
                "AND x.relative_path ILIKE ?)"
            )
            parameters.append(f"%{item.value}%")
            continue
        elif item.name == "topic":
            # The unit's own folder, or a folder above which it lies: an experiment of the topic.
            clauses.append(
                ("NOT " if item.negated else "")  # noqa: S608
                + "EXISTS (SELECT 1 FROM reach_v c WHERE c.entry_id = u.folder_id "
                "AND list_contains(?::VARCHAR[], c.ancestor_id))"
            )
            parameters.append(sorted((topics or {}).get(item.value.casefold(), ())))
            continue
        elif item.name == "kind":
            condition = (
                "EXISTS (SELECT 1 FROM terms_v t WHERE t.entry_id = e.entry_id "
                "AND t.theme_kind = ? AND t.resolution_status = 'resolved')"
            )
            values = [item.value.casefold()]
        elif item.name == "has":
            condition = (
                "EXISTS (SELECT 1 FROM terms_v t WHERE t.entry_id = e.entry_id "
                "AND t.theme_kind IN ('replicate', 'count'))"
            )
        if condition is None:
            continue
        clauses.append(("NOT " if item.negated else "") + holds.format(condition=condition))
        parameters.extend(values)
    return clauses, parameters


def topics_typed(
    project: Path, run_id: str, filters: Sequence[FieldFilter]
) -> tuple[dict[str, frozenset[str]], list[str], list[str]]:
    """Return what each typed ``topic:`` value names, read from the Record when the search runs.

    A value names the topics whose name it spells (ignoring case) or whose identifier it is. Two
    topics of one name are both meant. The function returns three things:

    - each value (casefolded) with the experiments of the topics that it names in the run;
    - the values that name no topic, in the order typed, so the search can say so and leave them
      out;
    - a sentence for each value that names a topic holding no experiment in this run, or one that
      could not be read, so the search can say that nothing matches because of that.

    Inference never reads a topic. The search reads it here, for this search alone. It changes which
    units are shown and never what any claim says.
    """
    wanted = [item.value for item in filters if item.name == "topic"]
    if not wanted:
        return {}, [], []
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.topics import resolve

    try:
        found = resolve(project, run_id, compared=None, suggestions=False)
    except ComponentNotAvailableError:
        return {}, list(dict.fromkeys(wanted)), []
    named: dict[str, frozenset[str]] = {}
    empty: list[str] = []
    for value in dict.fromkeys(wanted):
        matching = [
            reading
            for reading in found.topics
            if reading.topic.name.casefold() == value.casefold() or reading.topic.topic_id == value
        ]
        if not matching:
            continue
        named[value.casefold()] = frozenset(
            experiment for reading in matching for experiment in reading.experiments
        )
        if not named[value.casefold()]:
            unread = any(not reading.complete for reading in matching)
            reason = (
                "could not be read in this scan" if unread else "holds no experiment in this scan"
            )
            empty.append(f"The collection {value} {reason}, so no result is inside it.")
    unknown = [value for value in dict.fromkeys(wanted) if value.casefold() not in named]
    return named, unknown, empty


def _stage_values(store: DictionaryStore) -> dict[str, tuple[str, ...]]:
    """Return each spelling of a workflow stage.

    Each spelling comes with the stage values that a stage rule concludes for it.
    """
    found: dict[str, set[str]] = {}
    for sense in store.senses():
        if sense.theme_kind != "stage":
            continue
        for spelling in {*sense.surfaces, sense.term, sense.canonical}:
            found.setdefault(str(spelling).casefold(), set()).update(
                {sense.term.casefold(), str(sense.canonical).casefold()}
            )
    return {spelling: tuple(sorted(values)) for spelling, values in found.items()}


def _time_rule(catalog: Catalog, predicate: str) -> tuple[str | None, str]:
    """Return the rule that reads a file's earliest credible time, and the first believable day.

    The rule is the one that concluded in the run, or None. The first believable day is the first
    day of the rule's ``earliest_credible_year``. The function finds the rule by its predicate, as
    the Groupings page finds its rule, so a revision that renames the rule is still read. Where the
    run holds no such rule, the first year is the engine's own.
    """
    from ingence.api.routes.groupings import rule_parameters
    from ingence.rulebook.predicates.classify import EARLIEST_CREDIBLE_YEAR

    rule_id, held = rule_parameters(catalog, predicate)
    year = held.get("earliest_credible_year", EARLIEST_CREDIBLE_YEAR)
    first = int(year) if isinstance(year, int) else EARLIEST_CREDIBLE_YEAR
    ran = rule_id is not None and _time_rule_ran(catalog, rule_id)
    return (rule_id if ran else None), f"{first:04d}-01-01"


#: The predicate whose claims hold the date neighbourhood that a name gives an entry.
NEIGHBOURHOOD_PREDICATE = "name_date_neighbourhood"


def _rules_that_ran(catalog: Catalog, predicate: str) -> tuple[str, ...]:
    """Return the rules on one predicate, in the run's rules table, that concluded anything."""
    return tuple(
        str(row[0])
        for row in catalog.query(
            "SELECT r.rule_id FROM rules r WHERE r.predicate = ? AND EXISTS (SELECT 1 FROM "
            "claims cl WHERE cl.rule_id = r.rule_id AND cl.resolution_status = 'resolved') "
            "ORDER BY 1",
            [predicate],
        ).fetchall()
    )


def _with_date_match(
    tags: dict[str, Any] | None,
    *,
    dated: bool,
    by_name: bool,
    time_rule: str | None,
    excluding: bool = False,
) -> dict[str, Any] | None:
    """Return a card's tags with what matched a date filter, in plain words, where one was typed.

    With ``excluding``, every date filter typed excludes a range (``-before:``). The card then says
    that none of its files' times falls in the range, and a name's date plays no part.
    """
    if not dated:
        return tags
    times = "earliest credible times" if time_rule else "own times"
    by = "earliest_credible_time" if time_rule else "own_time"
    if excluding:
        match = {"by": by, "said": f"none of its files' {times} falls in the range excluded"}
    elif by_name:
        match = {
            "by": "name_date",
            "said": (
                "only a date in a name here falls in the range: a neighbourhood read from the "
                "name, never the files' own time"
            ),
        }
    elif time_rule:
        match = {
            "by": by,
            "said": "its files' earliest credible times fall in the range",
        }
    else:
        match = {
            "by": by,
            "said": (
                "its files' own times fall in the range: a clock their content states, or their "
                "modification, never a date a name spells"
            ),
        }
    return {**(tags or {}), "date_match": match}


def _time_rule_ran(catalog: Catalog, rule_id: str) -> bool:
    """Return whether the run holds any claim of the rule for a file's earliest credible time."""
    return (
        catalog.query("SELECT 1 FROM claims WHERE rule_id = ? LIMIT 1", [rule_id]).fetchone()
        is not None
    )


def _unit_facts(
    catalog: Catalog,
    projection: Projection,
    *,
    folder_ids: Sequence[str],
    copy_minimum: int,
    newest_rule: str | None = None,
    root_places: Mapping[str, int] | None = None,
) -> dict[str, UnitFacts]:
    """Return what each candidate unit is, where it sits, and why it has its kind.

    Where the ``newest`` order is asked for and the run holds the claims of ``newest_rule``, the
    function dates each unit by the latest earliest credible time anywhere beneath it. A time that
    the rule set aside counts as no time. Without ``newest_rule``, it dates each unit by the newest
    of its own files' times.
    """
    if not folder_ids:
        return {}
    catalog.query("CREATE OR REPLACE TEMP TABLE cand (folder_id VARCHAR)")
    catalog.connection.executemany("INSERT INTO cand VALUES (?)", [(f,) for f in folder_ids])
    closure = projection.table("closure")
    folders = projection.table("folders")
    units = projection.table("units")
    products = projection.table("products")
    cursor = catalog.query(
        f"""
        WITH lineage AS (
            SELECT c.entry_id AS folder_id, list(c.ancestor_id ORDER BY c.distance) AS ancestors
            FROM {closure} c JOIN cand ON cand.folder_id = c.entry_id GROUP BY 1
        ),
        direct AS (
            SELECT c.ancestor_id AS folder_id, count(*) AS files,
                   md5(string_agg(e.name || ':' || coalesce(CAST(e.size_bytes AS VARCHAR), '?'),
                                  '|' ORDER BY e.name)) AS signature,
                   max({OWN_CLOCK_SQL}) AS newest
            FROM {closure} c JOIN cand ON cand.folder_id = c.ancestor_id
            JOIN entries e ON e.entry_id = c.entry_id
            WHERE c.distance = 1 AND e.entry_type = 'file'
            GROUP BY 1
        ),
        folder_units AS (
            SELECT f.folder_id, f.parent_id, f.name, f.relative_path, f.depth, f.subtree_files,
                   coalesce(u.unit_kind, 'folder') AS unit_kind, u.basis,
                   coalesce(u.doubted, false) AS doubted, coalesce(u.good, false) AS good,
                   CAST(NULL AS VARCHAR) AS made_as
            FROM {folders} f JOIN cand USING (folder_id) LEFT JOIN {units} u USING (folder_id)
        ),
        product_units AS (
            SELECT p.entry_id AS folder_id, p.parent_id, p.name, p.relative_path,
                   CAST(NULL AS BIGINT) AS depth, 1 AS subtree_files, 'product' AS unit_kind,
                   'made to be read: ' || replace(p.made_as, '_', ' ') AS basis,
                   p.doubted, false AS good, p.made_as
            FROM {products} p JOIN cand ON cand.folder_id = p.entry_id WHERE p.home_id IS NULL
        ),
        credible AS (
            SELECT cl.subject AS entry_id,
                   min(epoch_ns(TRY_CAST(json_extract_string(cl.object_reference, '$.at')
                                         AS TIMESTAMP))) AS seen
            FROM claims cl
            WHERE ? AND cl.rule_id = ? AND cl.resolution_status = 'resolved'
            GROUP BY 1
        ),
        recent AS (
            SELECT r.ancestor_id AS folder_id, max(cr.seen) AS newest
            FROM reach_v r JOIN cand ON cand.folder_id = r.ancestor_id
            JOIN credible cr ON cr.entry_id = r.entry_id
            WHERE ?
            GROUP BY 1
        )
        SELECT x.folder_id, x.parent_id, x.name, x.relative_path, x.depth, x.subtree_files,
               coalesce(l.ancestors, []) AS ancestors,
               CASE WHEN d.files >= ? THEN d.signature END AS signature,
               CASE WHEN ? THEN rc.newest ELSE d.newest END AS newest,
               x.unit_kind, x.basis, x.doubted, x.good, x.made_as, re.root_id
        FROM (SELECT * FROM folder_units UNION ALL SELECT * FROM product_units) x
        LEFT JOIN lineage l USING (folder_id) LEFT JOIN direct d USING (folder_id)
        LEFT JOIN recent rc USING (folder_id)
        LEFT JOIN entries re ON re.entry_id = x.folder_id
        """,  # noqa: S608
        [
            newest_rule is not None,
            newest_rule or "",
            newest_rule is not None,
            copy_minimum,
            newest_rule is not None,
        ],
    )
    facts: dict[str, UnitFacts] = {}
    for row in cursor.fetchall():
        (folder_id, parent, name, path, depth, files, ancestors, signature, newest, kind, basis,
         doubted, good, made_as, root) = row  # fmt: skip
        lineage = tuple(ancestors or ())
        facts[folder_id] = UnitFacts(
            folder_id=folder_id,
            parent_id=parent,
            name=name,
            relative_path=path,
            depth=int(depth) if depth is not None else len(lineage),
            subtree_files=int(files or 0),
            ancestors=lineage,
            copy_signature=signature,
            newest_ns=newest,
            kind=UnitKind(kind),
            basis=basis,
            doubted=bool(doubted),
            good=bool(good),
            made_as=made_as,
            root_place=_root_place(root, root_places),
        )
    return facts


def _root_places(project: Path) -> dict[str, int]:
    """Each declared root's place in the project's list of roots: what every build shares."""
    from ingence.core.project import Project

    try:
        declared = Project.open(project).identity.roots
    except Exception:  # a project that cannot be opened orders its ties by identifier alone
        return {}
    return {declaration.root_id: place for place, declaration in enumerate(declared)}


def _root_place(root: object, places: Mapping[str, int] | None) -> int:
    """A unit's root's place, after every declared root where its root is not declared."""
    held = places or {}
    return held.get(str(root), len(held)) if root is not None else len(held)


def _explain(
    catalog: Catalog,
    projection: Projection,
    *,
    units: Sequence[str],
    fallback_reach: int = DEFAULT_FALLBACK_REACH,
    examples: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Step 8: explain, for each shown unit and concept, what matched, how, where, and a few files.

    The explanation places a match where the ranking placed it (:func:`_roll_up`). That is at the
    unit itself, beneath its home unit, or, for a match with no home, within ``fallback_reach``
    levels of a plain folder. A match that the ranking did not count is never shown as a reason.
    """
    if not units:
        return {}
    closure = projection.table("closure")
    homes = projection.table("homes")
    products = projection.table("products")
    marks = ", ".join("?" for _ in units)
    cursor = catalog.query(
        f"""
        WITH placed AS (
            SELECT h.entry_id AS unit, h.* FROM hit h WHERE h.entry_id IN ({marks})
            UNION ALL
            SELECT hm.home_id AS unit, h.* FROM hit h
            JOIN {homes} hm ON hm.entry_id = h.entry_id WHERE hm.home_id IN ({marks})
            UNION ALL
            SELECT c.ancestor_id AS unit, h.* FROM hit h
            JOIN {closure} c ON c.entry_id = h.entry_id
            WHERE c.ancestor_id IN ({marks}) AND c.distance <= ?
              AND h.entry_id NOT IN (SELECT entry_id FROM {homes})
              AND h.entry_id NOT IN (SELECT entry_id FROM {products} WHERE home_id IS NULL)
        )
        SELECT p.unit, p.concept, p.route, p.source, p.way_rank, min(p.raw_value) AS matched,
               count(DISTINCT coalesce(d.entry_id, p.entry_id)) AS entries,
               bool_or(p.route = 'date' AND contains(p.token, '{AMBIGUOUS_DATE_JOINER}'))
                   AS one_of_two,
               list(DISTINCT coalesce(d.name, e.name)
                    ORDER BY coalesce(d.name, e.name))[1:{examples}] AS examples
        FROM placed p LEFT JOIN entries e ON e.entry_id = p.entry_id
        -- A part recovered from inside a file is its document's content. The file that a hit
        -- names, and counts, is the document and never the part (``word/document.xml``).
        LEFT JOIN entries d ON d.entry_id = e.parent_entry_id AND NOT {filesystem_entry("e")}
        WHERE NOT p.excluded
        GROUP BY p.unit, p.concept, p.route, p.source, p.way_rank
        ORDER BY p.unit, p.concept, p.way_rank, p.route, p.source
        """,  # noqa: S608
        [*units, *units, *units, fallback_reach],
    )
    found: dict[str, list[dict[str, Any]]] = {}
    for (unit, concept, route, source, way_rank, matched, entries, one_of_two,
         names) in cursor.fetchall():  # fmt: skip
        line: dict[str, Any] = {
            "concept": concept,
            "route": route,
            # How this match was found, in the card's words.
            "found_by": FOUND_BY_ORDER[int(way_rank)].value,
            "where": source,
            "matched": matched,
            "entries": entries,
            "examples": list(names or []),
        }
        if one_of_two:
            line["one_of_two_readings"] = True
            line["said"] = (
                "a date written with its day and month either way round: the day typed is one of "
                "its two readings"
            )
        found.setdefault(unit, []).append(line)
    return found


#: The predicate whose claims name the sets of words that a person approved as groups.
APPROVED_GROUP_PREDICATE = "resolved_group_across_experiments"


def _approved_groups(
    catalog: Catalog, units: Sequence[str], typed: Mapping[int, str]
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    """Return each shown unit's approved groups whose words the query names, as reasons it matches.

    A search for an approved set's words brings up every experiment that holds the set, whether or
    not a person approved that experiment directly. Each experiment stays its own result. The run's
    claims of the rules that group experiments by an approved set (at each scale) name the set. A
    unit that holds an entry of such a set, where the query names one of the set's words, is given
    the reason ``approved group {<the set>}``. The function only reads, and only for the units on
    the page. It says a failure to read the groups and never swallows it: the second value is a
    warning for the response, None where all went well.
    """
    if not units:
        return {}, None
    words = {text.casefold(): index for index, text in typed.items() if text}
    # A group made of terms is found by the spellings that its files write (``groups.words_as``).
    # A typed spelling also stands for the Dictionary term that the run binds it to, which is the
    # word that the group's key holds.
    from ingence.grouping.lattice_words import TERMS, WordMap, run_words_as

    # This applies only where the run read terms. A run that read spellings keys its groups by
    # them.
    if run_words_as(catalog) == TERMS:
        for spelling, term in WordMap.of_run(catalog, words_as=TERMS).terms().items():
            if spelling in words:
                words.setdefault(term, words[spelling])
    marks = ", ".join("?" for _ in units)
    try:
        rows = catalog.query(
            f"""
            SELECT r.ancestor_id AS unit, c.object_reference AS approved,
                   count(DISTINCT c.subject) AS entries
            FROM claims c JOIN reach_v r ON r.entry_id = c.subject
            WHERE r.ancestor_id IN ({marks}) AND c.resolution_status = 'resolved'
              AND c.rule_id IN (SELECT rule_id FROM rules WHERE predicate = ?)
            GROUP BY unit, approved ORDER BY unit, approved
            """,  # noqa: S608
            [*units, APPROVED_GROUP_PREDICATE],
        ).fetchall()
    except Exception as error:  # a run without these rules or claims has no approved group to name
        return {}, f"the approved groups could not be read as reasons: {error}"
    found: dict[str, list[dict[str, Any]]] = {}
    for unit, approved, entries in rows:
        spelled = str(approved)
        named = [words[word] for word in spelled.split() if word in words]
        if not named:
            continue
        found.setdefault(str(unit), []).append(
            {
                "concept": min(named),
                "route": "held by",
                "where": f"approved group {{{spelled}}}",
                "matched": "words a person approved as a group",
                "entries": int(entries),
                "examples": [],
            }
        )
    return found, None


#: A unit's chips, held. What a card shows beside its title is a function of the unit and the run's
#: projection alone, and never of what was typed. Reading it is most of a search's time on a large
#: census, because every chip query reads the whole run's claims. So the search reads each unit's
#: chips once per projection and holds them. A later search that shows the same experiments reads
#: only the units that it has not shown before. This works like the document summaries of a search
#: engine.
_TAGS: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
_TAGS_HELD = 4096
_TAGS_GUARD = threading.Lock()


def _held_tags(
    catalog: Catalog,
    projection: Projection,
    *,
    units: Sequence[str],
    normaliser: Normaliser,
    plausible_until: str | None,
    time_rule: str,
    time_rule_ran: bool,
    plausible_from: str,
    stage_rules: tuple[str, ...],
    dictionary_hash: str | None = None,
    quality: Mapping[str, QualityMark] | None = None,
) -> dict[str, Any]:
    """Return each unit's chips, read once for everything they are a function of and held.

    The hold is keyed on everything that a chip reads:

    - the projection, by what its meta records (the run, the Dictionary it was built under, the
      notes it holds) and not by its file's time alone, because a coarse filesystem clock can leave
      that time unchanged across a rebuild;
    - the Dictionary in force;
    - the tokeniser, its version and its settings, because a title is read with it;
    - the quality marks in force, which a chip shows.
    """
    meta = projection.path / "meta.json"
    try:
        stamp = meta.stat().st_mtime_ns
    except OSError:
        stamp = 0
    built = projection.meta
    base = (
        str(projection.path),
        stamp,
        built.get("run_id"),
        built.get("dictionary_hash"),
        built.get("note_events"),
        dictionary_hash,
        normaliser.version,
        normaliser.years,
        normaliser.read_on,
        tuple(sorted((quality or {}).values(), key=lambda mark: mark.term)),
        plausible_until,
        time_rule,
        time_rule_ran,
        plausible_from,
        stage_rules,
    )
    found: dict[str, Any] = {}
    with _TAGS_GUARD:
        for unit in units:
            held = _TAGS.get((*base, unit))
            if held is not None:
                _TAGS.move_to_end((*base, unit))
                found[unit] = held
    missing = [unit for unit in units if unit not in found]
    if missing:
        read = tags_for(
            catalog,
            projection,
            units=missing,
            normaliser=normaliser,
            plausible_until=plausible_until,
            time_rule=time_rule,
            time_rule_ran=time_rule_ran,
            plausible_from=plausible_from,
            stage_rules=stage_rules,
            quality=quality,
        )
        with _TAGS_GUARD:
            for unit, tags in read.items():
                _TAGS[(*base, unit)] = tags
            while len(_TAGS) > _TAGS_HELD:
                _TAGS.popitem(last=False)
        found.update(read)
    return found


def _load_ranked(catalog: Catalog, unit_ids: Sequence[str]) -> None:
    """Register every ranked unit as a table that the strip and the words beside the results read.

    The table is a registered Arrow table. It is never filled one row at a time, and it is never a
    long list bound as one parameter. On a pool of some ten thousand entries, inserting a hundred
    units one by one took 150 ms. DuckDB parses a list parameter of thousands as one value on every
    call.
    """
    import pyarrow as pa

    catalog.connection.register(
        "ranked_units", pa.table({"folder_id": pa.array(sorted(set(unit_ids)), pa.string())})
    )


def _co_occurring(
    catalog: Catalog,
    projection: Projection,
    *,
    minimum: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return the words that come with the query across the matched units, by PMI.

    The computation runs over unit documents (``projection.UNIT_DOCUMENT_SQL``). Each name's words
    belong to one unit. The matched documents are those that hold a match that counts for a ranked
    unit. A word's score is its pointwise mutual information with the query,
    ``log2(N * both / (matched * holding))``. Here ``N`` is the number of unit documents in the
    pool, ``matched`` the number that hold the query, ``holding`` the number that hold the word, and
    ``both`` the number that hold both. Chance gives a score of zero. The score is not a
    probability, and the page shows it beside its counts. The result leaves out a word that scores
    zero or less, because it appears no more often with the query than anywhere. It also leaves out
    a word in fewer than ``minimum`` matched units, however rare the word is. The query's own
    spellings (a word matched, or one that holds what was typed) are never words that come with it.
    The result is ordered by score, then by count, then by the word.
    """
    words_path = projection.path / "unit_words.parquet"
    documents = int(projection.meta.get("unit_documents") or 0)
    if not words_path.is_file() or documents <= 0:
        return []
    homes = projection.table("homes")
    products = projection.table("products")
    document = UNIT_DOCUMENT_SQL.format(homes="h", alone="p", row="x")
    rows = catalog.query(
        f"""
        WITH matched AS (
            SELECT DISTINCT {document} AS doc_id
            FROM hit x
            LEFT JOIN {homes} h ON h.entry_id = x.entry_id
            LEFT JOIN {products} p ON p.entry_id = x.entry_id AND p.home_id IS NULL
            WHERE NOT x.excluded AND x.source IN ('name', 'note')
              AND EXISTS (SELECT 1 FROM reach_v r JOIN ranked_units u
                          ON u.folder_id = r.ancestor_id WHERE r.entry_id = x.entry_id)
        ),
        own AS (SELECT DISTINCT token FROM hit WHERE NOT excluded),
        together AS (
            SELECT w.token, count(DISTINCT m.doc_id) AS both_units
            FROM {projection.table("unit_words")} w JOIN matched m USING (doc_id)
            WHERE w.token NOT IN (SELECT token FROM own)
              AND NOT EXISTS (SELECT 1 FROM q WHERE NOT q.excluded AND q.typed <> ''
                              AND contains(w.token, q.typed))
            GROUP BY w.token HAVING count(DISTINCT m.doc_id) >= ?
        )
        SELECT t.token, t.both_units, c.units, (SELECT count(doc_id) FROM matched) AS matched
        FROM together t JOIN {projection.table("unit_word_counts")} c USING (token)
        """,  # noqa: S608
        [minimum],
    ).fetchall()
    found: list[dict[str, Any]] = []
    for token, both, holding, matched in rows:
        if not matched or not holding:
            continue
        score = math.log2(documents * int(both) / (int(matched) * int(holding)))
        if score <= 0:
            # No more often with the query than anywhere: it does not come with it.
            continue
        found.append(
            {
                "word": str(token),
                "units": int(both),
                "units_in_pool": int(holding),
                "pmi": round(score, 3),
            }
        )
    found.sort(key=lambda row: (-row["pmi"], -row["units"], row["word"]))
    return found[:limit]


def _months_between(first: str, last: str) -> list[str]:
    """Every month from ``first`` to ``last`` inclusive, as ``YYYY-MM``."""
    year, month = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    months: list[str] = []
    while (year, month) <= end:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _by_month(
    catalog: Catalog,
    *,
    time_rule: str | None,
    plausible_from: str,
    plausible_until: str | None,
) -> dict[str, Any]:
    """Return the matched units counted by month.

    The result counts the undated units apart and never drops them.

    A unit's month is its earliest believed time. That is the earliest credible time of any file in
    the unit, where the rule that reads time concluded in the run. Otherwise it is a file's own
    time: a clock that its content states, or its modification time, and never a date that a name
    spells. Nothing outside the plausible span counts, exactly as a card's span reads it. A unit
    none of whose files has such a time is undated, and the count sits beside the strip. The result
    lists all the months between the first and the last, with an empty month as zero, so the strip
    reads as time.
    """
    bounds = [plausible_from, plausible_until or "9999-12-31"]
    rows = catalog.query(
        f"""
        WITH reach AS (
            SELECT r.ancestor_id AS unit, r.entry_id
            FROM reach_v r JOIN ranked_units u ON u.folder_id = r.ancestor_id
        ),
        credible AS (
            SELECT cl.subject AS entry_id,
                   TRY_CAST(json_extract_string(cl.object_reference, '$.at') AS TIMESTAMP) AS seen
            FROM claims cl
            WHERE ? AND cl.rule_id = ? AND cl.resolution_status = 'resolved'
        ),
        timed AS (
            SELECT r.unit,
                   coalesce(
                       cr.seen,
                       CASE WHEN NOT ? THEN make_timestamp({OWN_CLOCK_SQL} // 1000) END
                   ) AS seen
            FROM reach r JOIN entries e ON e.entry_id = r.entry_id AND e.entry_type = 'file'
            LEFT JOIN credible cr ON cr.entry_id = r.entry_id
        )
        SELECT u.folder_id,
               strftime(min(seen) FILTER (WHERE seen BETWEEN CAST(? AS TIMESTAMP)
                                                         AND CAST(? AS TIMESTAMP)), '%Y-%m')
                   AS month
        FROM ranked_units u LEFT JOIN timed t ON t.unit = u.folder_id
        GROUP BY u.folder_id
        """,  # noqa: S608
        [time_rule is not None, time_rule or "", time_rule is not None, *bounds],
    ).fetchall()
    counted: dict[str, int] = {}
    undated = 0
    for _, month in rows:
        if month is None:
            undated += 1
        else:
            counted[str(month)] = counted.get(str(month), 0) + 1
    months = (
        [
            {"month": month, "units": counted.get(month, 0)}
            for month in _months_between(min(counted), max(counted))
        ]
        if counted
        else []
    )
    return {
        "by": "earliest_credible_time" if time_rule else "own_time",
        "units": len(rows),
        "dated": len(rows) - undated,
        "undated": undated,
        "months": months,
    }


def _paths_of(catalog: Catalog, entry_ids: Sequence[str]) -> dict[str, tuple[str, str | None]]:
    """Return each entry's path within its root and its root.

    The function reads many entries at once through a registered Arrow table.
    """
    import pyarrow as pa

    catalog.connection.register(
        "cut_units", pa.table({"entry_id": pa.array(sorted(set(entry_ids)), pa.string())})
    )
    return {
        str(entry): (str(path or ""), str(root) if root is not None else None)
        for entry, path, root in catalog.query(
            "SELECT e.entry_id, e.relative_path, e.root_id FROM entries e "
            "JOIN cut_units USING (entry_id)"
        ).fetchall()
    }


def _found_by_said(way: FoundBy, concept: Concept, *, in_content: bool = False) -> str:
    """Return how one typed word was found in a unit, in a sentence that a card can show.

    With ``in_content``, the word was found only inside files' content and never in a name. The
    sentence then says so and does not call it a name.
    """
    sense = next((match.term for match in concept.matches if match.term), None)
    holder = "a file's content" if in_content else "a name"
    if way is FoundBy.SPELLING:
        return f"{holder} holds {concept.typed!r} as typed"
    if way is FoundBy.SENSE:
        named = f" ({sense})" if sense else ""
        return f"{holder} holds another spelling of what {concept.typed!r} means{named}"
    if way is FoundBy.STARTS_WITH:
        return f"a word in {holder} begins with {concept.typed!r}"
    if way is FoundBy.PLAIN_NAME:
        return f"{concept.typed!r} stands inside {holder}"
    return f"{holder} holds a near spelling of {concept.typed!r}"


def _found_by_card(evidence: UnitEvidence, concepts: Sequence[Concept]) -> dict[str, Any]:
    """Return how a result was found: the ranking's way, the others, and each word's way.

    ``found_by`` is the ranking's own ``found_by``, which is the weakest of each covered word's
    strongest way. So a card never says that it was found more strongly than it is ranked.
    ``also_found_by`` lists every other way that any of its matches took, strongest first.
    """
    way = found_by(evidence)
    by_index = {concept.index: concept for concept in concepts}
    words = [
        (by_index[index], held)
        for index, held in sorted(evidence.best_way.items())
        if index in by_index
    ]
    return {
        "found_by": way.value if way is not None else None,
        "found_by_said": "; ".join(
            _found_by_said(held, concept, in_content=concept.index in evidence.content_only)
            for concept, held in words
        ),
        "also_found_by": [
            other.value for other in FOUND_BY_ORDER if other in evidence.ways and other != way
        ],
        "found_by_word": [
            {"word": concept.typed, "found_by": held.value} for concept, held in words
        ],
    }


def prepare_projection(
    catalog: Catalog,
    project: Path,
    *,
    normaliser: Normaliser,
    store: DictionaryStore,
    dictionary_hash: str | None = None,
    scale: ScaleParameters = DEFAULT_SCALE,
) -> tuple[Projection, bool, PoolVocabulary, ScaleParameters]:
    """Return the run's search projection, current or built now.

    The function also creates the temporary views that every search reads (``closure_v``,
    ``reach_v``, ``terms_v`` and ``folders_v``) and the pool's vocabulary. A search and a question's
    reading (``search/ask.py``) share it.

    It returns the projection, whether it was built now, the vocabulary, and the scale as the
    Dictionary in force reads it. The catalog's run is the one projected, and the caller has checked
    that there is one.
    """
    assert catalog.run_directory is not None
    run_id = catalog.run_directory.name
    scale = scale_from_dictionary(store, scale)
    # A reader that writes nothing under the project (the agent interface) builds and holds the
    # projection outside it.
    from ingence.themes.projection import cache_writes_allowed

    writes = cache_writes_allowed()
    projection = read_projection(project, run_id)
    if projection is None and not writes:
        projection = held_projection(project, run_id)
    built_now = False
    if projection is None or not projection_is_current(
        projection, project, dictionary_hash=dictionary_hash, scale=scale
    ):
        projection = build_projection(
            catalog,
            tokenise=lambda words: [(a.normalised, a.text) for a in normaliser.atoms(words)],
            normalizer_version=normaliser.version,
            dictionary_hash=dictionary_hash,
            scale=scale,
            outside=None if writes else hold_folder(),
        )
        if not writes:
            hold_projection(project, projection)
        built_now = True
    catalog.query(
        f"CREATE OR REPLACE TEMP VIEW closure_v AS SELECT * FROM {projection.table('closure')}"  # noqa: S608
    )
    catalog.query(
        "CREATE OR REPLACE TEMP VIEW reach_v AS SELECT entry_id, ancestor_id FROM closure_v "
        "UNION ALL SELECT entry_id, entry_id FROM entries WHERE NOT is_deleted"
    )
    catalog.query(
        f"CREATE OR REPLACE TEMP VIEW terms_v AS SELECT * FROM {projection.table('terms')}"  # noqa: S608
    )
    catalog.query(
        f"CREATE OR REPLACE TEMP VIEW folders_v AS SELECT * FROM {projection.table('folders')}"  # noqa: S608
    )
    return projection, built_now, PoolVocabulary.from_projection(catalog, projection), scale


def search(
    catalog: Catalog,
    project: Path,
    text: str,
    **options: Any,
) -> dict[str, Any]:
    """Return one page of results for one query over the catalog's run, as a plain dict.

    Every phase of :func:`search_phases` merged: the ranked cards, then each card's chips and
    reasons. The keyword options are :func:`search_phases`'s.
    """
    merged: dict[str, Any] | None = None
    for part in search_phases(catalog, project, text, **options):
        if merged is None:
            merged = {key: value for key, value in part.items() if key != "phase"}
        elif part.get("phase") == "detail":
            merge_detail(merged, part)
    assert merged is not None
    return merged


def merge_detail(merged: dict[str, Any], detail_part: Mapping[str, Any]) -> None:
    """Fold a ``detail`` part into the cards part that it follows, in place.

    The detail part holds each card's chips and reasons, and the warnings that only the second phase
    learned. Every caller uses this one merge, so the whole answer and a streamed one never say
    different things.
    """
    for result, detail in zip(merged["results"], detail_part["details"], strict=True):
        result["tags"] = detail["tags"]
        result["why"] = detail["why"]
    for beside in ("co_occurring_words", "by_month"):
        if beside in detail_part:
            merged[beside] = detail_part[beside]
    later = list(detail_part.get("warnings") or [])
    if later:
        merged["warnings"] = [*(merged.get("warnings") or []), *later]


def search_phases(
    catalog: Catalog,
    project: Path,
    text: str,
    *,
    normaliser: Normaliser,
    store: DictionaryStore,
    order: Sequence[str] | None = None,
    limit: int = 25,
    offset: int = 0,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    fallback_reach: int = DEFAULT_FALLBACK_REACH,
    copy_minimum: int = DEFAULT_COPY_MINIMUM_FILES,
    dictionary_hash: str | None = None,
    show_hidden: bool = False,
    similar_at: float = DEFAULT_SIMILAR_AT,
    hidden_by: str | None = None,
    scale: ScaleParameters = DEFAULT_SCALE,
    time_predicate: str = DEFAULT_TIME_RULE,
    co_occurring_minimum: int = DEFAULT_CO_OCCURRING_MINIMUM,
    co_occurring_limit: int = DEFAULT_CO_OCCURRING_LIMIT,
    inside_min_length: int = DEFAULT_INSIDE_MIN_LENGTH,
) -> Generator[dict[str, Any], None, None]:
    """Yield one page of results for one query over the catalog's run, in two phases.

    The first phase yields the ranked cards (``phase: cards``). It holds the reading of the query,
    the counts, and each card's place, kind, path, what it covers and misses, and what is folded
    inside it. Everything but the chips and the reasons is there. The chips and the reasons are
    ``None`` and ``[]``.

    The second phase yields the details (``phase: detail``). It holds each card's chips (``tags``)
    and reasons (``why``), in the cards' order. The first phase is most of what a person reads to
    choose. The second phase takes most of the time, because the chips read the whole run's claims.
    A measurement on a large project put it at over half of a search. A page shows the cards as soon
    as they come and fills each one in after, as a search engine returns its first results before
    their snippets. A query that is answered before any card (no run, no word read) is one phase,
    ``complete``.

    ``show_hidden`` shows the results that a person hid from similar searches, each marked. Without
    it, the search leaves them out and counts them. ``hidden_by`` applies only one person's hides.
    ``time_predicate`` names the rule whose claims hold a file's earliest credible time. The date
    filters, the ``newest`` order and the cards' spans read it.

    Two more items come beside the results, in the second phase. ``co_occurring_words`` holds the
    words that come with the query across every matched unit, by pointwise mutual information. Each
    word listed stands in at least ``co_occurring_minimum`` matched units, and at most
    ``co_occurring_limit`` words are listed.
    ``by_month`` counts every matched unit by the month of its earliest believed time, and counts
    the undated units apart. Both read every ranked unit and not only the page's.
    """
    if catalog.run_directory is None:
        yield {
            "phase": "complete",
            "query": text,
            "results": [],
            "reading": [],
            "note": "no run has been published",
        }
        return
    run_id = catalog.run_directory.name
    from ingence.api.run_names import project_run_names

    run_name = project_run_names(project).get(run_id, {}).get("label")
    projection, built_now, vocabulary, scale = prepare_projection(
        catalog,
        project,
        normaliser=normaliser,
        store=store,
        dictionary_hash=dictionary_hash,
        scale=scale,
    )
    parsed = parse_query(text, normaliser=normaliser, store=store)
    concepts = expand(
        parsed, store=store, vocabulary=vocabulary, inside_min_length=inside_min_length
    )
    run_versions = projection.meta.get("run_normalizer_versions", [])
    warnings: list[str] = []
    if run_versions and normaliser.version not in run_versions:
        warnings.append(
            f"this run's names were split into words by method {', '.join(run_versions)} and the "
            f"search by {normaliser.version}; some may be missed until the names are read again"
        )
    response: dict[str, Any] = {
        "query": text,
        "run_id": run_id,
        "run_name": run_name,
        "projection_built_now": built_now,
        "reading": [
            {
                "concept": concept.index,
                "typed": concept.typed,
                "reading": concept.reading,
                "excluded": concept.excluded,
                "did_you_mean": list(concept.suggestions),
            }
            for concept in concepts
        ],
        "filters": [
            {"name": item.name, "value": item.value, "negated": item.negated}
            for item in parsed.filters
        ],
        "phrases": [list(phrase) for phrase in parsed.phrases],
        "dropped": list(parsed.dropped),
        "refused": list(parsed.refused),
        "warnings": warnings,
        "order": list(order) if order else None,
    }
    if parsed.phrases:
        warnings.append(
            "a quoted phrase is searched as its words, each required; that they stand next to "
            "each other in a name is not required yet"
        )
    if not parsed.concepts:
        yield {"phase": "complete", **response, "results": [], "total_units": 0}
        return
    # The rule reading a file's earliest credible time, where it concluded in this run.
    time_rule, plausible_from = _time_rule(catalog, time_predicate)

    _load_concepts(catalog, concepts)
    _match(catalog, projection)
    rows = _roll_up(
        catalog, projection, max_candidates=max_candidates, fallback_reach=fallback_reach
    )
    excluded_here = {row["folder_id"] for row in rows if row["excluded"] and row["named"]}
    wanted = [row for row in rows if not row["excluded"] and row["folder_id"] not in excluded_here]

    # The search ranks the candidates that cover most of what was typed. Where more folders match
    # than the search ranks, it keeps the ones that cover the most concepts and whose own names
    # carry the most. It never chooses by the order that the identifiers sort in.
    covering: dict[str, set[int]] = {}
    naming: dict[str, int] = {}
    for row in wanted:
        covering.setdefault(row["folder_id"], set()).add(int(row["concept"]))
        naming[row["folder_id"]] = naming.get(row["folder_id"], 0) + int(bool(row["named"]))
    folder_ids = sorted(
        covering, key=lambda folder: (-len(covering[folder]), -naming[folder], folder)
    )
    root_places = _root_places(project)
    if len(folder_ids) > max_candidates:
        # A tie at the cut is broken by the unit's path, then its root's place. Every build of one
        # pool shares these. The identifier alone would not break the tie, because it differs with
        # where the pool sits.
        paths = _paths_of(catalog, folder_ids)
        folder_ids = sorted(
            covering,
            key=lambda folder: (
                -len(covering[folder]),
                -naming[folder],
                tie_path(paths.get(folder, ("", None))[0]),
                _root_place(paths.get(folder, ("", None))[1], root_places),
                folder,
            ),
        )
        warnings.append(
            f"{len(folder_ids)} folders matched and the {max_candidates} covering most of what "
            "was typed were ranked; a more specific query, or a filter, reads them all"
        )
        folder_ids = folder_ids[:max_candidates]
    folder_ids = sorted(folder_ids)
    facts = _unit_facts(
        catalog,
        projection,
        folder_ids=folder_ids,
        copy_minimum=copy_minimum,
        newest_rule=time_rule if "newest" in (order or ()) else None,
        root_places=root_places,
    )

    # A name's date widens a date filter as a neighbourhood, where the rules that read it concluded
    # in this run. A unit found only that way is labelled on its card.
    dated = any(item.name in ("after", "before") for item in parsed.filters)
    # Only a date filter that asks for a range says that a unit's times "fall in" it. A filter that
    # excludes a range says that none of the unit's files' times does.
    excluding = dated and all(
        item.negated for item in parsed.filters if item.name in ("after", "before")
    )
    marks_in_force = quality_marks(store)
    neighbourhood_rules = _rules_that_ran(catalog, NEIGHBOURHOOD_PREDICATE) if dated else ()
    # A typed topic that names none is said and left out, as a filter it cannot read is.
    topic_experiments, unknown_topics, empty_topics = topics_typed(project, run_id, parsed.filters)
    filters = [
        item
        for item in parsed.filters
        if item.name != "topic" or item.value.casefold() in topic_experiments
    ]
    response["refused"] = [
        *response["refused"],
        *(f"topic:{value}: no collection of yours is called that" for value in unknown_topics),
    ]
    warnings.extend(f"{said} Its card in Topics says why." for said in empty_topics)
    clauses, parameters = _unit_filter(
        filters,
        stage_rules=scale.stage_rules,
        stage_values=_stage_values(store),
        early_stages=scale.early_stages,
        time_rule=time_rule,
        plausible_from=plausible_from,
        neighbourhood_rules=neighbourhood_rules,
        quality=marks_in_force,
        topics=topic_experiments,
    )
    by_name_date: set[str] = set()
    if clauses and folder_ids:
        passing = {
            row[0]
            for row in catalog.query(
                "SELECT u.folder_id FROM cand u WHERE " + " AND ".join(clauses),  # noqa: S608
                parameters,
            ).fetchall()
        }
        wanted = [row for row in wanted if row["folder_id"] in passing]
        if neighbourhood_rules:
            strict, strict_parameters = _unit_filter(
                filters,
                stage_rules=scale.stage_rules,
                stage_values=_stage_values(store),
                early_stages=scale.early_stages,
                time_rule=time_rule,
                plausible_from=plausible_from,
                quality=marks_in_force,
                topics=topic_experiments,
            )
            by_clock = {
                row[0]
                for row in catalog.query(
                    "SELECT u.folder_id FROM cand u WHERE " + " AND ".join(strict),  # noqa: S608
                    strict_parameters,
                ).fetchall()
            }
            by_name_date = passing - by_clock

    hit_rows = [
        HitRow(
            folder_id=row["folder_id"],
            concept=int(row["concept"]),
            route=ROUTE_ORDER[int(row["route_rank"])],
            named=bool(row["named"]),
            content_only=bool(row["content_only"]),
            folder_frequency=int(row["folder_frequency"] or 1),
            way=FOUND_BY_ORDER[int(row["way_rank"])],
            ways=frozenset(FOUND_BY_ORDER[int(rank)] for rank in row["ways"] or ()),
        )
        for row in wanted
        if row["folder_id"] in facts
    ]
    evidence = evidence_from_rows(hit_rows, facts=facts)
    candidates = smallest_covering(evidence, facts=facts)
    folders_in_pool = int(projection.meta.get("counts", {}).get("folders", 1)) or 1
    ranked = rank(candidates, folders_in_pool=folders_in_pool, order=order)

    # Step 6, first half: leave out what a person hid from searches like this one.
    keys = concept_keys(
        (
            next((m.term_id for m in concept.matches if m.route is MatchRoute.SENSE), None),
            concept.typed,
        )
        for concept in concepts
        if not concept.excluded
    )
    hides = hidden_for(keys, read_hidden(project), similar_at=similar_at, actor=hidden_by)
    left_out = [unit for unit in ranked if unit.evidence.folder_id in hides]
    if not show_hidden:
        ranked = [unit for unit in ranked if unit.evidence.folder_id not in hides]
    response["concept_keys"] = sorted(keys)
    response["hidden"] = {
        "count": len(left_out),
        "shown": show_hidden,
        "units": [
            {
                "folder_id": unit.evidence.folder_id,
                "hidden_from": hides[unit.evidence.folder_id].typed,
                "by": hides[unit.evidence.folder_id].actor,
                "at": hides[unit.evidence.folder_id].at,
                "concepts": sorted(hides[unit.evidence.folder_id].concepts),
            }
            for unit in left_out[:50]
        ],
    }
    cards = present(ranked, facts=facts, limit=offset + limit)[offset:]

    typed_by_concept = {concept.index: concept.typed for concept in concepts}
    results: list[dict[str, Any]] = []
    for card in cards:
        folder_id = card.group_parent or card.unit.evidence.folder_id
        known = facts.get(card.unit.evidence.folder_id)
        results.append(
            {
                "position": card.unit.position,
                "folder_id": folder_id,
                "unit_kind": (
                    "products" if card.more_products else (known.kind.value if known else None)
                ),
                "basis": known.basis if known and not card.group_parent else None,
                "made_as": known.made_as if known else None,
                "doubted": known.doubted if known else False,
                # This holds only what the folder's own name says. It is never a claim that the
                # folder is good.
                "marked_good": known.good if known else False,
                "hidden_from_similar": folder_id in hides,
                # The contents stay closed until a person opens the card. The page then asks the
                # children route.
                "contents": "closed",
                "path": known.relative_path if known and not card.group_parent else None,
                # The folder's name as a person reads it, so the card has its title before its
                # chips. The chips themselves come in the second phase.
                "title": (
                    readable_title(known.name, normaliser)[0]
                    if known and not card.group_parent and known.name
                    else None
                ),
                "tags": None,
                "ranked_by": card.unit.dimensions,
                "tiebreak": card.unit.tiebreak,
                **_found_by_card(card.unit.evidence, concepts),
                "covered": [typed_by_concept[c] for c in sorted(card.unit.evidence.covered)],
                "missing": [
                    typed_by_concept[c]
                    for c in parsed.concepts
                    if c not in card.unit.evidence.covered
                ],
                "why": [],
                "inside": [
                    {"folder_id": inner.evidence.folder_id, "position": inner.position}
                    for inner in card.inside[:10]
                ],
                "inside_count": len(card.inside),
                "folded": [
                    {
                        "folder_id": member.evidence.folder_id,
                        "name": facts[member.evidence.folder_id].name
                        if member.evidence.folder_id in facts
                        else None,
                    }
                    for member in card.folded[:50]
                ],
                "folded_count": len(card.folded),
                "also_at": card.also_at,
            }
        )
    yield {"phase": "cards", **response, "results": results, "total_units": len(ranked)}

    # The second phase: each card's chips and its reasons, on this page only.
    shown_ids = sorted(
        {card.unit.evidence.folder_id for card in cards}
        | {card.group_parent for card in cards if card.group_parent}
    )
    # The last believable time is the run's own start. A file cannot predate nothing, and it
    # cannot postdate the census that listed it.
    started = catalog.query("SELECT started_at FROM run LIMIT 1").fetchone()
    plausible_until = _believable_day(started[0] if started else None)
    tags = _held_tags(
        catalog,
        projection,
        units=shown_ids,
        normaliser=normaliser,
        dictionary_hash=dictionary_hash,
        plausible_until=plausible_until,
        time_rule=time_rule or time_predicate,
        time_rule_ran=time_rule is not None,
        plausible_from=plausible_from,
        stage_rules=tuple(scale.stage_rules),
        quality=marks_in_force,
    )
    explained = _explain(catalog, projection, units=shown_ids, fallback_reach=fallback_reach)
    # What only the second phase learns travels in that phase. An approved group's reasons join each
    # card's reasons. A failure to read them becomes a warning in this part, so the streamed answer
    # and the whole one say the same thing. The cards' own warnings went out with the cards, and
    # the search never adds to them after.
    approved_reasons, unread = _approved_groups(catalog, shown_ids, typed_by_concept)
    for unit, lines in approved_reasons.items():
        explained.setdefault(unit, []).extend(lines)
    # Beside the results: what every matched unit holds besides the query, and when each was made.
    _load_ranked(catalog, [unit.evidence.folder_id for unit in ranked])
    co_occurring = _co_occurring(
        catalog, projection, minimum=co_occurring_minimum, limit=co_occurring_limit
    )
    by_month = _by_month(
        catalog,
        time_rule=time_rule,
        plausible_from=plausible_from,
        plausible_until=plausible_until,
    )
    details: list[dict[str, Any]] = []
    for card in cards:
        folder_id = card.group_parent or card.unit.evidence.folder_id
        details.append(
            {
                "folder_id": folder_id,
                # This is a copy. The search holds the unit's chips for later searches
                # (``_held_tags``), and what matched a date filter depends on the query. So the
                # search adds it to the copy only.
                "tags": _with_date_match(
                    copy.deepcopy(tags[folder_id].as_payload()) if folder_id in tags else None,
                    dated=dated,
                    excluding=excluding,
                    by_name=card.unit.evidence.folder_id in by_name_date,
                    time_rule=time_rule,
                ),
                "why": [
                    {**line, "typed": typed_by_concept.get(line["concept"], "")}
                    for line in explained.get(card.unit.evidence.folder_id, [])
                ],
            }
        )
    yield {
        "phase": "detail",
        "details": details,
        "warnings": [unread] if unread else [],
        "co_occurring_words": co_occurring,
        "by_month": by_month,
    }


def suggest(
    catalog: Catalog,
    project: Path,
    prefix: str,
    *,
    store: DictionaryStore,
    limit: int = 10,
) -> dict[str, Any]:
    """Return the words that the pool uses and that begin with what has been typed.

    The most used word comes first, and each word comes with its sense. The search box's drop-down
    uses this function. It reads the vocabulary only and never the terms table, so it answers as
    fast as a person types.
    """
    if catalog.run_directory is None:
        return {"prefix": prefix, "words": []}
    projection = read_projection(project, catalog.run_directory.name)
    if projection is None:
        return {"prefix": prefix, "words": [], "note": "the search projection is not built yet"}
    rows = catalog.query(
        f"SELECT token, entries, example, term, theme_kind FROM {projection.table('vocabulary')} "  # noqa: S608
        "WHERE starts_with(token, ?) ORDER BY entries DESC, token LIMIT ?",
        [prefix.casefold(), limit],
    ).fetchall()
    words = [
        {"token": token, "files": entries, "example": example, "term": term, "kind": kind}
        for token, entries, example, term, kind in rows
    ]
    senses = []
    folded = prefix.casefold()
    for sense in store.senses():
        if sense.term.startswith(folded) or any(
            s.casefold().startswith(folded) for s in sense.surfaces
        ):
            senses.append(
                {"term": sense.term, "kind": sense.theme_kind, "surfaces": list(sense.surfaces[:6])}
            )
        if len(senses) >= limit:
            break
    return {"prefix": prefix, "words": words, "senses": senses, "route": MatchRoute.TOKEN.value}
