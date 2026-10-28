"""The Review queue: what the engine could not decide, ranked, and stored nowhere.

The queue ranks the questions that a person can answer, so that a person's input and
clarification reach the uncertain terms first. The ranking, called salience, is computed here and
stored nowhere. ``Finding`` has no field for it. The queue changes no finding. It only puts the
questions in order.

Reordering a queue is not an interpretation and must never become one. Nothing in this module
writes, and nothing it computes is persisted. So the ranking can change without a schema change,
without a re-run and without a decision record. The Dashboard holds no truth of its own (see
``views.py``). A ranking that writes nothing keeps that promise, and that is why this module may
compute one.

The unit of the queue is a question, not a finding. A finding is raised per file. One uncertain
term raised on every file that holds it is one question asked once per file. Grouping by the rule
that raised a finding gives no useful order, because on real laboratory folders nearly every
finding comes from one rule. Grouping by what was raised turns an unusable list into a page that a
person can finish. Each question carries every finding it covers, so the grouping hides nothing and
a person can still walk to any one file.

A ranking is a tuple of named dimensions, ordered lexicographically. It is never a score. A
scoring number such as ``0.87`` looks like a probability, and a person cannot argue with a queue
sorted by it, while every element of a tuple can be argued with. So nothing has a weight and
nothing is summed. The dimensions are counts and a tier. Each can be cited alone, and the queue
shows each one beside the question it ranked. Each dimension declares which direction is more
urgent, so a reader does not have to guess.

A recommended default is a tier and never a score. Where the engine has no preference, the queue
says so. Every tie between senses is such a case, because a tie means that nothing about the
token separates the senses. The queue does not offer the first sense as if it were a suggestion,
because picking one would be the arbitration that the flagging rule exists to refuse.

A question raised only where a defeater fired is set aside, and the queue shows it as set aside.
A finding records the defeaters its rule names, whether they fired or not, and is raised all the
same. Suppose every finding behind a question carries a defeater that fired. For example, every
file raising a term lies beneath a folder laid out as an installed program. Then the term is a
code identifier's and not a laboratory's, and asking a person about it spends their attention on
the program. So the question leaves the ranked list and is returned apart, with the rule that set
it aside and its counts. It is never hidden and never asked. A question raised both beneath a
program and outside one is asked, and says how many of its findings were set aside.

The question is readable because the run persisted its own vocabulary. A binding's alternatives
carry term identifiers and not names. To ask whether ``sd`` means spinning disk or standard
deviation, the queue joins them to the run's ``dictionary_entries``. Every inference carries that
table forward, which makes the join possible. Without it the queue could only ask about
``term_3a42a719...``.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.core.tiers import INFERENCE_TIER_PROPERTIES, InferenceTier
from ingence.rulebook.revision_5.names import ASKS_NOTHING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ingence.storage.catalog import Catalog

__all__ = [
    "DEFAULT_ORDER",
    "DIMENSIONS",
    "LIMIT",
    "SUBJECT_SAMPLE",
    "Dimension",
    "UnknownDimensionError",
    "review",
]

#: How many questions one page of the queue returns. A person answers one at a time, and a
#: page they cannot finish is the same failure as an unbounded query.
LIMIT = 100

#: How many of a question's own files and findings travel with it. The counts are exact. The
#: samples are for walking to one example, and a question covering a great many files must
#: not put every one of their identifiers on a page.
SUBJECT_SAMPLE = 20


class UnknownDimensionError(Exception):
    """A request named a dimension the queue does not compute.

    The queue refuses the request. The Rulebook loader refuses an unknown name in the same way. If
    the queue dropped the dimension, it would order the questions differently from the request.
    The reader could not tell.
    """


@dataclass(frozen=True, slots=True)
class Dimension:
    """One thing a question is ranked on, and which way round it runs."""

    name: str
    #: ``more`` where a larger number is more urgent, ``fewer`` where a smaller one is.
    #: Declared rather than implied, because two of these run opposite ways and a reader
    #: guessing wrong reads the queue backwards.
    direction: str
    meaning: str


#: The dimensions a question is ranked on, ordered by how much a person's answer buys.
DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "entries_affected",
        "more",
        "How many files ask this same question. A term standing between two senses that "
        "many files use is worth more than one used twice.",
    ),
    Dimension(
        "conclusions_blocked",
        "more",
        "How many rule-and-file pairs could not decide, counted from the interpretation "
        "failures on the files this question covers. A conclusion that would follow from "
        "an answer is the sharpest reading of what rests on it.",
    ),
    Dimension(
        "classes_touched",
        "more",
        "How many classes the affected files are claimed into. An uncertain term inside a "
        "grouped set beats one in a file nothing else depends on.",
    ),
    Dimension(
        "senses_offered",
        "fewer",
        "How many meanings the Dictionary already knows for it. Two is one click and seven "
        "is a study; none is either a term nobody has defined, which is typing, or a "
        "question of another shape, and both are worth less attention than a tie a click "
        "settles. So none sorts last rather than first.",
    ),
    Dimension(
        "default_standing",
        "more",
        "The standing of the level the raising rule carries, never a score. It says "
        "how much weight the engine's own recommendation has where it offers one.",
    ),
)

#: The order applied when a request names none. A request can change it, because the ranking
#: only changes how the queue is shown.
DEFAULT_ORDER: tuple[str, ...] = tuple(dimension.name for dimension in DIMENSIONS)

_BY_NAME = {dimension.name: dimension for dimension in DIMENSIONS}

#: Sorts a question offering no ready-made sense behind every question that offers one,
#: under a dimension that otherwise runs smallest-first. Larger than any vocabulary.
_NO_SENSES_LAST = 1 << 30


def _rows(
    catalog: Catalog, sql: str, parameters: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, list(parameters or []))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _checked_order(order: Sequence[str] | None) -> tuple[str, ...]:
    if order is None:
        return DEFAULT_ORDER
    named = tuple(order)
    if not named:
        message = "a ranking names at least one dimension; an empty order sorts nothing"
        raise UnknownDimensionError(message)
    unknown = [name for name in named if name not in _BY_NAME]
    if unknown:
        known = ", ".join(_BY_NAME)
        message = f"the queue computes no dimension named {unknown[0]!r}. It computes: {known}"
        raise UnknownDimensionError(message)
    return named


def _sort_value(question: dict[str, Any], name: str) -> tuple[int, int]:
    """Return the ordering key for one dimension of one question.

    A tuple so that ``senses_offered``'s zero can sort last under a dimension that
    otherwise runs smallest-first, without the reported count being altered to make the
    sort come out. What is shown is the fact; what is sorted on is stated here.
    """
    value = int(question.get(name) or 0)
    dimension = _BY_NAME[name]
    if name == "senses_offered" and value == 0:
        value = _NO_SENSES_LAST
    return (-value, 0) if dimension.direction == "more" else (value, 0)


def _changes_the_words(catalog: Catalog) -> frozenset[str]:
    """The rules whose answer changes the words other questions are asked in.

    The rule set comes from what each rule's predicate declares it reads, never from a list of
    names. A predicate handed the vocabulary packs that the project does not load proposes loading
    one. A pack that is loaded changes the words of the lattice's group questions. So, among the
    questions asked first, the questions of these rules come first. This also moves a pack's
    question ahead of a deviation.
    """
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.registry import PREDICATES, VocabularyRead, vocabulary_of

    load_builtins()
    leading: set[str] = set()
    for row in _rows(catalog, "SELECT rule_id, predicate FROM rules"):
        try:
            declared = PREDICATES.declaration(str(row["predicate"] or ""))
        except KeyError:  # a predicate this engine does not register reads nothing it knows
            continue
        if VocabularyRead.UNLOADED_PACKS in vocabulary_of(declared):
            leading.add(str(row["rule_id"]))
    return frozenset(leading)


def _asked_first(catalog: Catalog) -> frozenset[str]:
    """The rules declaring that their questions lead the queue.

    The declaration is a rule's own parameter, never a list of names here. The parameter
    ``asked_first``, when true, puts every question the rule raised ahead of the ranked rest, in
    their own ranked order. It serves a deviation from a group that a person approved, which is
    its own question and is ranked first. Like any setting, it can be switched on the rule.
    """
    rows = _rows(catalog, "SELECT rule_id, parameters_json FROM rules")
    leading: set[str] = set()
    for row in rows:
        try:
            declared = json.loads(row["parameters_json"] or "[]")
        except (TypeError, ValueError):
            continue
        for parameter_declared in declared:
            if parameter_declared.get("name") != "asked_first":
                continue
            try:
                value = json.loads(parameter_declared.get("default"))
            except (TypeError, ValueError):
                value = parameter_declared.get("default")
            if value is True:
                leading.add(str(row["rule_id"]))
    return frozenset(leading)


def _tier_standing(tier: str) -> int:
    try:
        return INFERENCE_TIER_PROPERTIES[InferenceTier(tier)].standing
    except (KeyError, ValueError):
        return 0


#: Whether a finding records a defeater that fired, from its stored defeaters.
_FIRED = (
    "coalesce(list_bool_or(CAST(json_extract(coalesce(f.defeaters_json, '[]'), '$[*].fired') "
    "AS BOOLEAN[])), FALSE)"
)


def _records_defeaters(catalog: Catalog) -> bool:
    """Whether this run's findings record their defeaters.

    A run whose findings table has no ``defeaters_json`` column does not.
    """
    columns = {str(row["column_name"]) for row in _rows(catalog, "DESCRIBE SELECT * FROM findings")}
    return "defeaters_json" in columns


#: The rules whose findings ask nothing, as SQL. The questions leave them out.
_ASKS_NOTHING_SQL = (
    "f.rule_id NOT IN (" + ", ".join(f"'{rule}'" for rule in sorted(ASKS_NOTHING)) + ")"
)


def _questions(catalog: Catalog) -> list[dict[str, Any]]:
    """Group every finding by the rule that raised it and the thing it raised.

    A finding that asks nothing is not a question (see :data:`ASKS_NOTHING`). A folder's domain
    profile and a spelling read as a slip are stated assumptions. The assumptions route lists them
    and this function leaves them out.
    """
    fired = _FIRED if _records_defeaters(catalog) else "FALSE"
    return _rows(
        catalog,
        f"""
        WITH asked AS (
            SELECT f.rule_id, f.rule_version, u.value AS raised, f.subject, f.reason,
                   {fired} AS fired
            FROM findings f, unnest(f.raised) AS u(value)
            WHERE {_ASKS_NOTHING_SQL}
        )
        SELECT
            rule_id,
            rule_version,
            raised,
            count(*)                              AS findings,
            count(DISTINCT subject)               AS entries_affected,
            min(reason)                           AS reason,
            count(*) FILTER (WHERE fired)         AS findings_set_aside
        FROM asked
        GROUP BY rule_id, rule_version, raised
        """,  # noqa: S608 - the one interpolation is a constant expression declared above
    )


def _set_aside_by(catalog: Catalog) -> dict[tuple[str, str], list[str]]:
    """Return, per question, the rules whose defeaters fired on its findings."""
    if not _records_defeaters(catalog):
        return {}
    rows = _rows(
        catalog,
        """
        SELECT DISTINCT f.rule_id, u.value AS raised,
               json_extract_string(d.value, '$.rule_id') AS defeater
        FROM findings f, unnest(f.raised) AS u(value), json_each(f.defeaters_json) AS d
        WHERE coalesce(CAST(json_extract(d.value, '$.fired') AS BOOLEAN), FALSE)
        ORDER BY 1, 2, 3
        """,
    )
    found: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        found.setdefault((str(row["rule_id"]), str(row["raised"])), []).append(str(row["defeater"]))
    return found


def _blocked(catalog: Catalog) -> dict[tuple[str, str], dict[str, int]]:
    """Count the interpretation failures standing on each question's files.

    An interpretation failure is a rule that could not decide, which is exactly a
    conclusion an answer would unblock. Counted per rule and file pair rather than per
    file, because one file can starve two rules and answering helps both. Access and
    comprehension failures are not counted: those are a mount or a missing extractor and
    no amount of a person's attention to a term fixes either.
    """
    rows = _rows(
        catalog,
        """
        WITH asked AS (
            SELECT DISTINCT f.rule_id, u.value AS raised, f.subject
            FROM findings f, unnest(f.raised) AS u(value)
        )
        SELECT
            a.rule_id,
            a.raised,
            count(DISTINCT x.rule_id || '|' || x.subject) AS conclusions_blocked,
            count(DISTINCT x.rule_id)                     AS rules_blocked
        FROM asked a
        LEFT JOIN rule_failures x
               ON x.subject = a.subject AND x.failure_class = 'interpretation'
        GROUP BY a.rule_id, a.raised
        """,
    )
    return {
        (str(row["rule_id"]), str(row["raised"])): {
            "conclusions_blocked": int(row["conclusions_blocked"] or 0),
            "rules_blocked": int(row["rules_blocked"] or 0),
        }
        for row in rows
    }


def _classes(catalog: Catalog) -> dict[tuple[str, str], int]:
    """Count the classes each question's files are claimed into.

    A grouping claim's predicate is ``groups.<rule_id>`` and its object is the class key,
    so a distinct count of keys is a count of classes without the class phase existing.
    """
    rows = _rows(
        catalog,
        """
        WITH asked AS (
            SELECT DISTINCT f.rule_id, u.value AS raised, f.subject
            FROM findings f, unnest(f.raised) AS u(value)
        )
        SELECT a.rule_id, a.raised,
               count(DISTINCT c.rule_id || '|' || coalesce(c.object_reference, '')) AS classes
        FROM asked a
        JOIN claims c
          ON c.subject = a.subject
         AND c.predicate LIKE 'groups.%'
         AND c.resolution_status = 'resolved'
        GROUP BY a.rule_id, a.raised
        """,
    )
    return {(str(row["rule_id"]), str(row["raised"])): int(row["classes"] or 0) for row in rows}


def _senses(catalog: Catalog) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Return the meanings the Dictionary already knows for each raised term.

    The alternatives on a binding carry identifiers and not names, so the
    readable half comes from the run's own vocabulary snapshot. A sense the snapshot does
    not hold is reported under its identifier rather than dropped, because a question
    missing one of its answers is worse than an ugly one.
    """
    rows = _rows(
        catalog,
        """
        WITH asked AS (
            SELECT DISTINCT f.rule_id, u.value AS raised, i.input AS binding_id
            FROM findings f, unnest(f.raised) AS u(value), unnest(f.inputs) AS i(input)
        )
        SELECT
            a.rule_id,
            a.raised,
            alt.term_id                       AS term_id,
            coalesce(d.term, alt.term_id)     AS term,
            alt.theme_kind                    AS theme_kind,
            alt.origin                        AS origin,
            alt.shadowed                      AS shadowed,
            count(*)                          AS bindings
        FROM asked a
        JOIN dictionary_bindings b ON b.binding_id = a.binding_id
        CROSS JOIN unnest(from_json(b.alternatives_json, '[{"term_id":"VARCHAR",
                   "theme_kind":"VARCHAR","origin":"VARCHAR","shadowed":"BOOLEAN"}]')) AS t(alt)
        LEFT JOIN dictionary_entries d ON d.term_id = alt.term_id
        GROUP BY a.rule_id, a.raised, alt.term_id, d.term, alt.theme_kind, alt.origin,
                 alt.shadowed
        ORDER BY a.rule_id, a.raised, term
        """,
    )
    offered: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["rule_id"]), str(row["raised"]))
        offered.setdefault(key, []).append(
            {
                "term": row["term"],
                "term_id": row["term_id"],
                "theme_kind": row["theme_kind"],
                "origin": row["origin"],
                "shadowed": bool(row["shadowed"]),
                "bindings": int(row["bindings"] or 0),
            }
        )
    return offered


def _tiers(catalog: Catalog) -> dict[str, str]:
    """Return the tier each rule in this run's Rulebook carries."""
    rows = _rows(catalog, "SELECT rule_id, tier FROM rules")
    return {str(row["rule_id"]): str(row["tier"]) for row in rows}


def _examples(catalog: Catalog, *, sample: int) -> dict[tuple[str, str], dict[str, list[str]]]:
    """Return a bounded sample of each question's own files and findings."""
    rows = _rows(
        catalog,
        """
        WITH asked AS (
            SELECT f.rule_id, u.value AS raised, f.subject, f.finding_id
            FROM findings f, unnest(f.raised) AS u(value)
        )
        SELECT rule_id, raised, subject, finding_id
        FROM asked ORDER BY rule_id, raised, subject, finding_id
        """,
    )
    examples: dict[tuple[str, str], dict[str, list[str]]] = {}
    for row in rows:
        key = (str(row["rule_id"]), str(row["raised"]))
        held = examples.setdefault(key, {"subjects": [], "finding_ids": []})
        if len(held["subjects"]) < sample:
            held["subjects"].append(str(row["subject"]))
        if len(held["finding_ids"]) < sample:
            held["finding_ids"].append(str(row["finding_id"]))
    return examples


def _modified(nanoseconds: int | None) -> str | None:
    """A modification time as a person reads it: to the second, and saying it is UTC."""
    if nanoseconds is None:
        return None
    try:
        moment = dt.datetime.fromtimestamp(nanoseconds // 1_000_000_000, tz=dt.UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _files(catalog: Catalog, identifiers: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return the file behind each identifier a question shows: its path, size and date.

    A question about an entry identifier cannot be answered without opening the Index to see
    what the entry is, and a stub is judged by its folder, its size and when it was last
    written. So wherever the queue shows an identifier, either a raised value that is an entry's
    or a sampled subject, the file is joined in beside the identifier and never instead of it,
    because the identifier is what the Record and every other view cite.

    A single SQL join finds every file. A folder scope's subject is ``folder:<entry id>`` and a
    root's is ``root:<root id>`` (``Scope.token``), so the code takes the prefix off before it
    joins the entries. A root is named by its mount path alone. The path is built from strings and
    no file is opened. An identifier this run holds no entry or root for is left out, and the
    page shows it bare.
    """
    wanted = sorted(set(identifiers))
    if not wanted:
        return {}
    rows = _rows(
        catalog,
        """
        WITH shown AS (SELECT unnest(?::VARCHAR[]) AS identifier)
        SELECT s.identifier, r.mount_path, e.relative_path, e.entry_type, e.size_bytes,
               e.modified_ns
        FROM shown s
        LEFT JOIN entries e ON e.entry_id = regexp_replace(s.identifier, '^folder:', '')
        LEFT JOIN roots r
               ON r.root_id = coalesce(e.root_id, regexp_replace(s.identifier, '^root:', ''))
        WHERE e.entry_id IS NOT NULL OR r.root_id IS NOT NULL
        """,
        [wanted],
    )
    return {
        str(row["identifier"]): {
            "id": str(row["identifier"]),
            "path": str(Path(str(row["mount_path"] or ""), str(row["relative_path"] or ""))),
            # Where it lies under its root, for a page that never shows the machine's own path.
            "relative_path": str(row["relative_path"] or ""),
            "entry_type": row["entry_type"],
            "size_bytes": row["size_bytes"],
            "modified": _modified(row["modified_ns"]),
        }
        for row in rows
    }


def review(
    catalog: Catalog,
    *,
    order: Sequence[str] | None = None,
    limit: int = LIMIT,
    offset: int = 0,
    sample: int = SUBJECT_SAMPLE,
) -> dict[str, Any]:
    """Return one page of the salience-ranked queue.

    ``order`` names the dimensions to rank on, outermost first, and defaults to ``DEFAULT_ORDER``.
    Naming a different order tunes the ranking. It reorders a page and changes no claim, so it
    needs no re-run and leaves nothing behind.

    Nothing here is written anywhere. The queue is recomputed on every request, which is
    what makes it safe to change.
    """
    applied = _checked_order(order)
    if not catalog.has_run:
        return {
            "dimensions": _declared(),
            "order": list(applied),
            "theme_kinds": [],
            "totals": {"questions": 0, "findings": 0, "set_aside": 0},
            "questions": [],
            "set_aside": [],
        }

    blocked = _blocked(catalog)
    classes = _classes(catalog)
    senses = _senses(catalog)
    tiers = _tiers(catalog)
    examples = _examples(catalog, sample=sample)
    asked = _questions(catalog)
    set_aside_by = _set_aside_by(catalog)
    leading = _asked_first(catalog)
    before_groups = _changes_the_words(catalog) & leading
    files = _files(
        catalog,
        [str(row["raised"]) for row in asked]
        + [subject for held in examples.values() for subject in held["subjects"]],
    )

    questions: list[dict[str, Any]] = []
    for row in asked:
        key = (str(row["rule_id"]), str(row["raised"]))
        offered = senses.get(key, [])
        tier = tiers.get(key[0], "")
        sampled = examples.get(key, {"subjects": [], "finding_ids": []})
        questions.append(
            {
                "rule_id": row["rule_id"],
                "rule_version": row["rule_version"],
                "raised": row["raised"],
                "reason": row["reason"],
                "findings": int(row["findings"] or 0),
                "entries_affected": int(row["entries_affected"] or 0),
                "conclusions_blocked": blocked.get(key, {}).get("conclusions_blocked", 0),
                "rules_blocked": blocked.get(key, {}).get("rules_blocked", 0),
                "classes_touched": classes.get(key, 0),
                "senses_offered": len(offered),
                "senses": offered,
                "tier": tier,
                "default_standing": _tier_standing(tier),
                "recommended": _recommendation(offered),
                "raised_file": files.get(key[1]),
                "subjects": sampled["subjects"],
                "subject_files": [
                    files.get(subject, {"id": subject, "path": None})
                    for subject in sampled["subjects"]
                ],
                "finding_ids": sampled["finding_ids"],
                "findings_set_aside": int(row["findings_set_aside"] or 0),
                "set_aside_by": set_aside_by.get(key, []),
                "asked_first": key[0] in leading,
                "asked_before_groups": key[0] in before_groups,
            }
        )

    # Set aside where every finding behind the question carries a defeater that fired.
    aside: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for question in questions:
        whole = question["findings"] and question["findings_set_aside"] == question["findings"]
        (aside if whole else kept).append(question)
    questions = kept
    aside.sort(
        key=lambda question: (
            -int(question["entries_affected"]),
            str(question["raised"]),
            str(question["rule_id"]),
        )
    )

    questions.sort(
        key=lambda question: (
            0 if question["asked_before_groups"] else 1 if question["asked_first"] else 2,
            tuple(_sort_value(question, name) for name in applied),
            str(question["rule_id"]),
            str(question["raised"]),
        )
    )
    page = questions[offset : offset + limit]
    return {
        "dimensions": _declared(),
        "order": list(applied),
        "theme_kinds": _theme_kinds(catalog),
        "totals": {
            "questions": len(questions),
            "findings": sum(int(question["findings"]) for question in questions),
            "set_aside": len(aside),
        },
        "questions": page,
        "set_aside": aside,
    }


def _theme_kinds(catalog: Catalog) -> list[str]:
    """The axes a person may define a term onto, from the run's own vocabulary.

    The axes come from the snapshot and never from a text field. Every rule that reads a sense
    reads an axis, so a sense defined onto an axis nothing names is a sense no rule reads and the
    answer changes nothing. Offering exactly the axes the run consulted is what stops a typo
    becoming that.
    """
    rows = _rows(
        catalog,
        "SELECT DISTINCT theme_kind FROM dictionary_entries "
        "WHERE theme_kind IS NOT NULL AND theme_kind <> '' ORDER BY theme_kind",
    )
    return [str(row["theme_kind"]) for row in rows]


def _declared() -> list[dict[str, str]]:
    """Return the dimensions as the page shows them, so a reader can see what ranked it."""
    return [
        {"name": dimension.name, "direction": dimension.direction, "meaning": dimension.meaning}
        for dimension in DIMENSIONS
    ]


def _recommendation(offered: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Say what the engine would suggest, and say plainly where it would suggest nothing.

    A tie between senses is the engine having no preference: nothing about the token
    separates them, which is why the rule raised it instead of resolving it. Offering the
    first sense would be arbitration by list order, which the flagging rule exists to refuse.
    Where one sense shadowed the others, the engine did choose, and saying which is a real
    recommendation.
    """
    shadowed = [sense for sense in offered if not sense["shadowed"]]
    if offered and len(shadowed) == 1:
        return {
            "value": shadowed[0]["term"],
            "because": "every other sense lost on precedence rather than standing equal",
        }
    if offered:
        return {
            "value": None,
            "because": (
                f"{len(offered)} senses stand equal, so nothing about the word separates "
                "them and a suggestion would be arbitration by list order"
            ),
        }
    return {"value": None, "because": "the engine offers no ready-made answer to this"}
