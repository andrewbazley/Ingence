"""Answer a question in a person's words with experiments.

``ask`` reads a question the way the search box reads a query. It uses the same tokeniser, the same
Dictionary and the same match routes (``query``, ``expand``). It drops the words that only say a
question is being asked, such as ``does``, ``which`` and ``compare``. These are the Dictionary's
question words. It widens a class word to its members, so ``nuclear stain`` becomes ``dapi`` and
``hoechst``. It then answers with experiments rather than units, in three tiers:

- ``named``: the experiment's words match every word that the question is answered with. The
  experiment's words are its folder's name and its files' names. The match comes after spelling,
  plural and class expansion. An unknown word is said and not required. An unknown word is one that
  neither the run nor the Dictionary knows.
- ``associated``: the experiment is not named, and its words include a word that comes with the
  question's words across the project. These are the strongest Dictionary-bound words that occur
  with the named experiments' words, at most ``associated_words`` of them, and each is shown. This
  tier is off where the setting ``search.associated_tier`` is off.
- ``same_theme``: the experiment is not named or associated, and it is in the same theme (line of
  work) as at least two named experiments.

Every experiment says why. It lists the words it matched and how: as written, another spelling of
a sense, a word starting with it, or a near spelling, in its own name or in a file's name. Or it
lists the co-occurring words it holds, or the theme it shares. A question writes nothing. It is
answered from the run's search projection, which the first use builds, as a search builds it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ingence.search.engine import load_concepts, match, prepare_projection
from ingence.search.expand import CLASS_HOW, FOUND_BY_ORDER, FoundBy, MatchRoute, expand
from ingence.search.query import QUESTION_KIND, parse_query

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.store import DictionaryStore
    from ingence.search.expand import Concept, Expansion
    from ingence.storage.catalog import Catalog

__all__ = [
    "DEFAULT_ASSOCIATED_WORDS",
    "DEFAULT_DROPPED_KINDS",
    "AskReading",
    "AssociatedWord",
    "ExperimentAsked",
    "ExperimentMatch",
    "Filters",
    "WordMatched",
    "as_payload",
    "ask",
    "read_filters",
    "unpunctuated",
]

#: The most co-occurring words that the associated tier reads. A handful is what a person can see.
DEFAULT_ASSOCIATED_WORDS = 5
#: The fewest named experiments that a co-occurring word must stand in. Two is the least that is a
#: pattern and not one name. The search uses the same minimum for words that come with a query.
DEFAULT_ASSOCIATED_MINIMUM = 2
#: The number of named experiments that an experiment must share its theme with to be in the same
#: theme.
SAME_THEME_MINIMUM = 2
#: The kinds that a question's word is dropped as, by default. They are the question words, the
#: connectives, and the kinds that say how a file was made and not what it is about. The themes'
#: own ``how_kinds`` are these kinds. A route passes ``how_kinds`` from the settings in force, and
#: this set is their default.
DEFAULT_DROPPED_KINDS = frozenset(
    {
        QUESTION_KIND, "connective", "channel", "count", "dataset", "date", "experiment",
        "file_role", "format", "identifier", "measurement", "part", "process", "project",
        "quality", "quantifier", "replicate", "stage", "statistic", "status", "unit", "version",
    }
)  # fmt: skip

Tier = Literal["named", "associated", "same_theme"]

#: Theme kinds that a co-occurring word is never drawn from. Each kind says how a file was made or
#: counted, and none says what an experiment is about. These are the search's hidden kinds plus the
#: question words.
_NEVER_ASSOCIATED_KINDS = frozenset(
    {
        "date", "version", "identifier", "unit", "format", "file_role", "replicate", "count",
        "read_structure", "position", "connective", "stage", QUESTION_KIND,
    }
)  # fmt: skip

#: How a matched word is said, by the way it was found.
_WAY_SAID: dict[FoundBy, str] = {
    FoundBy.SPELLING: "as written",
    FoundBy.SENSE: "another spelling of the same sense",
    FoundBy.STARTS_WITH: "a word starting with it",
    FoundBy.PLAIN_NAME: "inside a longer word",
    FoundBy.NEAR_SPELLING: "a near spelling",
}


@dataclass(frozen=True, slots=True)
class ExperimentAsked:
    """One experiment that the question may be answered with, as a route knows it before asking."""

    experiment_id: str
    label: str
    theme: str | None = None
    theme_label: str | None = None
    #: The experiment's folder relative to its root, and its first date (YYYY-MM-DD). The filters
    #: that a question names (an area, a year) use them.
    folder: str = ""
    first: str | None = None


@dataclass(frozen=True, slots=True)
class Filters:
    """The filters read from a question. Each is None where the question names none."""

    years: tuple[int, ...] | None = None
    types: tuple[str, ...] | None = None
    areas: tuple[str, ...] | None = None
    #: The concepts that the filters were read from. They are left out of the words that an
    #: experiment must match.
    used: frozenset[int] = frozenset()


#: The file-role words that a question names a file type with. Each is keyed by the Dictionary term
#: it binds to. The value lists the pieces of an extension's term (the formats' terms) that spell
#: that type: ``images`` names ``image_format``, ``tables`` names ``tabular_format``, ``scripts``
#: names ``python_source``, and so on. The question reads the word as a filter only where the run
#: holds such a term. Otherwise the word stays a word.
TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "image": ("image",),
    "table": ("tabular",),
    "movie": ("movie", "video"),
    "script": ("source", "macro", "notebook"),
    "document": ("document",),
}
#: The years that a bare four-digit number may name.
YEAR_FROM, YEAR_TO = 1990, 2100
#: The Dictionary kind of the words that name a file type.
FILE_ROLE_KIND = "file_role"


@dataclass(frozen=True, slots=True)
class WordMatched:
    """One word an experiment matched, and how, in words."""

    word: str
    how: str
    #: The word's Dictionary class, or its theme kind. The class is the sense's term where the word
    #: is a class word. A page can then say "nucleotide" beside ATP and ADP. None where the word is
    #: unbound.
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class AssociatedWord:
    """A word that comes with the question's words across the named experiments."""

    word: str
    #: The number of named experiments that hold the word, and the number of experiments in the
    #: whole pool that hold it.
    named_with: int
    in_pool: int
    pmi: float


@dataclass(frozen=True, slots=True)
class ExperimentMatch:
    """One experiment matching the question, at its tier, with why."""

    experiment: ExperimentAsked
    tier: Tier
    matched: tuple[WordMatched, ...]
    #: The number of matched words that stand in the experiment's own folder name.
    in_own_name: int = 0
    #: The shared theme, for the same-theme tier.
    via_theme: str | None = None
    #: The number of known words in the question, for "n of m".
    of_words: int = 0


@dataclass(frozen=True, slots=True)
class AskReading:
    """A question read and answered."""

    q: str
    kept: tuple[str, ...]
    dropped: tuple[str, ...]
    unknown: tuple[str, ...]
    expanded: tuple[Expansion, ...]
    refused: tuple[str, ...]
    named: tuple[ExperimentMatch, ...]
    associated: tuple[ExperimentMatch, ...]
    same_theme: tuple[ExperimentMatch, ...]
    associated_words: tuple[AssociatedWord, ...] = ()
    associated_tier: bool = True
    projection_built_now: bool = False
    #: The filters read from the question.
    filters: Filters = field(default_factory=Filters)
    #: The concepts as read, for a caller that wants the routes.
    concepts: tuple[Concept, ...] = field(default=(), repr=False)

    @property
    def experiments(self) -> tuple[ExperimentMatch, ...]:
        """The named then the associated matches, best first."""
        return (*self.named, *self.associated)


#: A question's own punctuation. A mark before a space or the end (``cells?``, ``taxol,``) carries
#: nothing that a name carries. The tokeniser reads a mark inside a word (``2.5h``, ``v1.2``).
_PUNCTUATION = re.compile(r"[?!.,;:]+(?=\s|$)")


def unpunctuated(text: str) -> str:
    """Return the question without the marks that a sentence ends or divides with."""
    return _PUNCTUATION.sub("", text)


def _year_of(concept: Concept) -> int | None:
    """The year a concept names: a date's year, or a bare four-digit number in the years."""
    token = concept.token
    if concept.kind == "date" and len(token) >= 4 and token[:4].isdigit():
        return int(token[:4])
    if concept.kind == "number" and len(token) == 4 and token.isdigit():
        year = int(token)
        return year if YEAR_FROM <= year <= YEAR_TO else None
    return None


def _extension_terms(catalog: Catalog) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in catalog.query(
            "SELECT DISTINCT term FROM terms_v WHERE component_type = 'extension' "
            "AND term IS NOT NULL"
        ).fetchall()
    )


def _area_names(catalog: Catalog) -> dict[str, str]:
    """The top-level folders beneath every root, by their casefolded name: the areas."""
    rows = catalog.query(
        "SELECT e.name FROM entries e WHERE e.entry_type = 'directory' AND NOT e.is_deleted "
        "AND e.parent_entry_id IN (SELECT entry_id FROM entries WHERE parent_entry_id IS NULL)"
    ).fetchall()
    return {str(name).casefold(): str(name) for (name,) in rows if name}


def read_filters(
    concepts: Sequence[Concept], *, store: DictionaryStore, catalog: Catalog
) -> Filters:
    """Return the filters that a question names.

    The filters are years (a date or a four-digit number), file types (a file-role word that the
    run's extensions spell) and areas (a top-level folder's name).
    """
    years: list[int] = []
    types: list[str] = []
    areas: list[str] = []
    used: set[int] = set()
    held_terms: frozenset[str] | None = None
    held_areas: dict[str, str] | None = None
    for concept in concepts:
        if concept.excluded:
            continue
        year = _year_of(concept)
        if year is not None:
            years.append(year)
            used.add(concept.index)
            continue
        if concept.kind != "word":
            continue
        hit = store.look_up(concept.typed)
        if hit is not None and hit.sense.theme_kind == "file_role" and hit.sense.term in TYPE_WORDS:
            if held_terms is None:
                held_terms = _extension_terms(catalog)
            pieces = TYPE_WORDS[hit.sense.term]
            if any(piece in term for term in held_terms for piece in pieces):
                types.append(hit.sense.term)
                used.add(concept.index)
                continue
        if held_areas is None:
            held_areas = _area_names(catalog)
        area = held_areas.get(concept.typed.casefold())
        if area is not None:
            areas.append(area)
            used.add(concept.index)
    return Filters(
        years=tuple(sorted(set(years))) or None,
        types=tuple(dict.fromkeys(types)) or None,
        areas=tuple(dict.fromkeys(areas)) or None,
        used=frozenset(used),
    )


def _passing(
    catalog: Catalog, experiments: Sequence[ExperimentAsked], filters: Filters
) -> list[ExperimentAsked]:
    """Return the experiments that the filters keep.

    An experiment passes when it is dated in a year named, beneath an area named, and holds a file
    of a type named.
    """
    kept = list(experiments)
    if filters.years is not None:
        kept = [
            item
            for item in kept
            if item.first and item.first[:4].isdigit() and int(item.first[:4]) in filters.years
        ]
    if filters.areas is not None:
        heads = {area.casefold() for area in filters.areas}
        kept = [item for item in kept if item.folder.split("/", 1)[0].casefold() in heads]
    if filters.types is not None and kept:
        catalog.query("CREATE OR REPLACE TEMP TABLE ask_typed (experiment_id VARCHAR)")
        catalog.connection.executemany(
            "INSERT INTO ask_typed VALUES (?)", [(item.experiment_id,) for item in kept]
        )
        pieces = [piece for wanted in filters.types for piece in TYPE_WORDS.get(wanted, ())]
        conditions = " OR ".join("t.term ILIKE ?" for _ in pieces) or "FALSE"
        rows = catalog.query(
            f"""
            SELECT DISTINCT x.experiment_id FROM ask_typed x
            WHERE EXISTS (
                SELECT 1 FROM reach_v r JOIN terms_v t ON t.entry_id = r.entry_id
                WHERE r.ancestor_id = x.experiment_id AND t.component_type = 'extension'
                  AND ({conditions})
            )
            """,  # noqa: S608
            [f"%{piece}%" for piece in pieces],
        ).fetchall()
        holding = {str(row[0]) for row in rows}
        kept = [item for item in kept if item.experiment_id in holding]
    return kept


def _bound_kind(concept: Concept, store: DictionaryStore) -> str | None:
    hit = store.look_up(concept.typed) if concept.kind == "word" else None
    return None if hit is None else hit.sense.theme_kind


def _kind_of(concept: Concept, store: DictionaryStore) -> str | None:
    """Return a word's class.

    The class is the sense's term where the word was read as a class word. Otherwise it is the
    theme kind of the sense that the word binds to, or None.
    """
    for expansion in concept.expansions:
        if expansion.how == CLASS_HOW:
            return concept.token
    hit = store.look_up(concept.typed)
    return hit.sense.theme_kind if hit is not None else None


def _said(way_rank: int, own: bool) -> str:
    way = FOUND_BY_ORDER[way_rank] if 0 <= way_rank < len(FOUND_BY_ORDER) else FoundBy.SPELLING
    where = "in its name" if own else "in a file's name"
    return f"{_WAY_SAID.get(way, 'found')}, {where}"


def _coverage(
    catalog: Catalog, experiment_ids: Sequence[str]
) -> dict[str, dict[int, tuple[int, bool]]]:
    """Return each experiment's covered concepts.

    For each concept, the result holds the strongest way it was found and whether it was found in
    the experiment folder's own name. A hit anywhere at or beneath the folder counts for the
    experiment.
    """
    catalog.query("CREATE OR REPLACE TEMP TABLE ask_experiments (experiment_id VARCHAR)")
    if experiment_ids:
        catalog.connection.executemany(
            "INSERT INTO ask_experiments VALUES (?)", [(eid,) for eid in experiment_ids]
        )
    rows = catalog.query(
        """
        SELECT e.experiment_id, h.concept, min(h.way_rank) AS way_rank,
               bool_or(h.entry_id = e.experiment_id AND h.source = 'name'
                       AND h.route NOT IN ('starts_with', 'plain_name')) AS own
        FROM hit h
        JOIN reach_v r ON r.entry_id = h.entry_id
        JOIN ask_experiments e ON e.experiment_id = r.ancestor_id
        WHERE NOT h.excluded
        GROUP BY e.experiment_id, h.concept
        """
    ).fetchall()
    covered: dict[str, dict[int, tuple[int, bool]]] = {}
    for experiment_id, concept, way_rank, own in rows:
        covered.setdefault(str(experiment_id), {})[int(concept)] = (int(way_rank), bool(own))
    return covered


def _associated_words(
    catalog: Catalog,
    projection_tables: Mapping[str, str],
    *,
    named_ids: Sequence[str],
    documents: int,
    store: DictionaryStore,
    own_tokens: frozenset[str],
    minimum: int,
    limit: int,
) -> list[AssociatedWord]:
    """Return the Dictionary-bound words that come with the question across the named experiments.

    The words are ranked by pointwise mutual information over unit documents, as the search ranks
    the words it lists beside results. The question's own spellings are left out.
    """
    if not named_ids or documents <= 0:
        return []
    catalog.query("CREATE OR REPLACE TEMP TABLE ask_named (experiment_id VARCHAR)")
    catalog.connection.executemany(
        "INSERT INTO ask_named VALUES (?)", [(eid,) for eid in named_ids]
    )
    rows = catalog.query(
        f"""
        WITH docs AS (
            SELECT DISTINCT w.doc_id
            FROM {projection_tables["unit_words"]} w
            JOIN reach_v r ON r.entry_id = w.doc_id
            JOIN ask_named n ON n.experiment_id = r.ancestor_id
        ),
        together AS (
            SELECT w.token, count(DISTINCT w.doc_id) AS both_units
            FROM {projection_tables["unit_words"]} w JOIN docs USING (doc_id)
            GROUP BY w.token HAVING count(DISTINCT w.doc_id) >= ?
        )
        SELECT t.token, t.both_units, c.units, (SELECT count(*) FROM docs) AS matched
        FROM together t JOIN {projection_tables["unit_word_counts"]} c USING (token)
        """,  # noqa: S608
        [minimum],
    ).fetchall()
    found: list[AssociatedWord] = []
    for token, both, holding, matched in rows:
        word = str(token)
        if not matched or not holding or word in own_tokens:
            continue
        if any(word.startswith(own) or own.startswith(word) for own in own_tokens if len(own) >= 4):
            continue
        hit = store.look_up(word)
        if hit is None or hit.sense.theme_kind in _NEVER_ASSOCIATED_KINDS:
            continue
        score = math.log2(documents * int(both) / (int(matched) * int(holding)))
        if score <= 0:
            continue
        found.append(
            AssociatedWord(
                word=word, named_with=int(both), in_pool=int(holding), pmi=round(score, 3)
            )
        )
    found.sort(key=lambda row: (-row.pmi, -row.named_with, row.word))
    return found[:limit]


def _holding(
    catalog: Catalog, projection_tables: Mapping[str, str], words: Sequence[str]
) -> dict[str, set[str]]:
    """Return which experiments, among ``ask_experiments``, hold each word anywhere beneath them."""
    if not words:
        return {}
    placeholders = ", ".join("?" for _ in words)
    rows = catalog.query(
        f"""
        SELECT DISTINCT e.experiment_id, w.token
        FROM {projection_tables["unit_words"]} w
        JOIN reach_v r ON r.entry_id = w.doc_id
        JOIN ask_experiments e ON e.experiment_id = r.ancestor_id
        WHERE w.token IN ({placeholders})
        """,  # noqa: S608
        list(words),
    ).fetchall()
    held: dict[str, set[str]] = {}
    for experiment_id, token in rows:
        held.setdefault(str(experiment_id), set()).add(str(token))
    return held


def ask(
    catalog: Catalog,
    project: Path,
    text: str,
    *,
    normaliser: Normaliser,
    store: DictionaryStore,
    experiments: Sequence[ExperimentAsked],
    dictionary_hash: str | None = None,
    inside_min_length: int = 4,
    associated_tier: bool = True,
    associated_words: int = DEFAULT_ASSOCIATED_WORDS,
    associated_minimum: int = DEFAULT_ASSOCIATED_MINIMUM,
    dropped_kinds: Collection[str] = DEFAULT_DROPPED_KINDS,
    at_least: int | None = None,
) -> AskReading:
    """Answer one question with the run's experiments, in tiers, each with why.

    ``experiments`` are the run's experiments as the caller knows them, with their folders'
    entries, labels and themes. The answer uses only these experiments. ``at_least`` lowers the
    named tier to experiments that match at least that many of the known words, which gives
    "n of m". None requires every known word. The same run, Dictionary and question give the same
    answer.
    """
    if catalog.run_directory is None:
        message = "no run has been published"
        raise LookupError(message)
    projection, built_now, vocabulary, _ = prepare_projection(
        catalog, project, normaliser=normaliser, store=store, dictionary_hash=dictionary_hash
    )
    # A file-role word (``images``, ``tables``) is read before it is dropped, because it may be a
    # filter. One that is not a filter is dropped as a question's how-word, as the caller asked.
    parsed = parse_query(
        unpunctuated(text),
        normaliser=normaliser,
        store=store,
        question=True,
        dropped_kinds=frozenset(dropped_kinds) - {FILE_ROLE_KIND},
    )
    concepts = expand(
        parsed, store=store, vocabulary=vocabulary, inside_min_length=inside_min_length
    )
    # The filters that a question names narrow which experiments the answer uses. The words they
    # were read from are not words that an experiment must match.
    filters = read_filters(
        [concept for concept in concepts if not concept.excluded], store=store, catalog=catalog
    )
    dropped = list(parsed.dropped)
    wanted = []
    for concept in concepts:
        if concept.excluded:
            continue
        if (
            FILE_ROLE_KIND in dropped_kinds
            and concept.index not in filters.used
            and _bound_kind(concept, store) == FILE_ROLE_KIND
        ):
            dropped.append(concept.typed)
            continue
        wanted.append(concept)
    kept = tuple(concept.typed for concept in wanted)
    unknown = tuple(
        concept.typed
        for concept in wanted
        if not concept.known and concept.index not in filters.used
    )
    required = frozenset(
        concept.index for concept in wanted if concept.known and concept.index not in filters.used
    )
    expanded = tuple(expansion for concept in wanted for expansion in concept.expansions)
    by_id = {item.experiment_id: item for item in _passing(catalog, experiments, filters)}
    ids = sorted(by_id)
    if not required or not ids:
        return AskReading(
            q=text,
            kept=kept,
            dropped=tuple(dropped),
            unknown=unknown,
            expanded=expanded,
            refused=tuple(parsed.refused),
            named=(),
            associated=(),
            same_theme=(),
            associated_tier=associated_tier,
            projection_built_now=built_now,
            concepts=concepts,
            filters=filters,
        )
    load_concepts(catalog, concepts)
    match(catalog, projection)
    covered = _coverage(catalog, ids)
    typed_of = {concept.index: concept.typed for concept in wanted}
    kind_of = {concept.index: _kind_of(concept, store) for concept in wanted}

    needed = len(required) if at_least is None else max(1, min(at_least, len(required)))
    named: list[ExperimentMatch] = []
    for experiment_id in ids:
        found = covered.get(experiment_id, {})
        matched_indexes = sorted(required & set(found))
        if len(matched_indexes) < needed:
            continue
        matched = tuple(
            WordMatched(word=typed_of[index], how=_said(*found[index]), kind=kind_of[index])
            for index in matched_indexes
        )
        named.append(
            ExperimentMatch(
                experiment=by_id[experiment_id],
                tier="named",
                matched=matched,
                in_own_name=sum(1 for index in matched_indexes if found[index][1]),
                of_words=len(required),
            )
        )
    named.sort(
        key=lambda item: (
            -len(item.matched),
            -item.in_own_name,
            item.experiment.label.casefold(),
            item.experiment.experiment_id,
        )
    )
    named_ids = [item.experiment.experiment_id for item in named]
    placed = set(named_ids)

    tables = {
        "unit_words": projection.table("unit_words"),
        "unit_word_counts": projection.table("unit_word_counts"),
    }
    associated: list[ExperimentMatch] = []
    words: list[AssociatedWord] = []
    if associated_tier and named_ids and (projection.path / "unit_words.parquet").is_file():
        own_tokens = frozenset(
            token
            for concept in wanted
            for token in (
                concept.token,
                *(
                    m.token
                    for m in concept.matches
                    if m.token and m.route is not MatchRoute.PLAIN_NAME
                ),
            )
            if token
        )
        words = _associated_words(
            catalog,
            tables,
            named_ids=named_ids,
            documents=int(projection.meta.get("unit_documents") or 0),
            store=store,
            own_tokens=own_tokens,
            minimum=associated_minimum,
            limit=associated_words,
        )
        held = _holding(catalog, tables, [word.word for word in words])
        for experiment_id in ids:
            if experiment_id in placed or experiment_id not in held:
                continue
            with_words = [word for word in words if word.word in held[experiment_id]]
            if not with_words:
                continue
            associated.append(
                ExperimentMatch(
                    experiment=by_id[experiment_id],
                    tier="associated",
                    matched=tuple(
                        WordMatched(
                            word=word.word,
                            how=(
                                f"comes with the question's words: in {word.named_with} of "
                                f"{len(named_ids)} named experiments, {word.in_pool} in the pool"
                            ),
                        )
                        for word in with_words
                    ),
                    in_own_name=len(with_words),
                )
            )
        associated.sort(
            key=lambda item: (
                -item.in_own_name,
                item.experiment.label.casefold(),
                item.experiment.experiment_id,
            )
        )
        placed.update(item.experiment.experiment_id for item in associated)

    themes_named: dict[str, int] = {}
    for item in named:
        if item.experiment.theme is not None:
            themes_named[item.experiment.theme] = themes_named.get(item.experiment.theme, 0) + 1
    shared = {theme for theme, count in themes_named.items() if count >= SAME_THEME_MINIMUM}
    same_theme: list[ExperimentMatch] = []
    for experiment_id in ids:
        held_item = by_id[experiment_id]
        if experiment_id in placed or held_item.theme not in shared:
            continue
        same_theme.append(
            ExperimentMatch(
                experiment=held_item,
                tier="same_theme",
                matched=(),
                via_theme=held_item.theme_label or held_item.theme,
            )
        )
    same_theme.sort(
        key=lambda item: (item.experiment.label.casefold(), item.experiment.experiment_id)
    )

    return AskReading(
        q=text,
        kept=kept,
        dropped=tuple(dropped),
        unknown=unknown,
        expanded=expanded,
        refused=tuple(parsed.refused),
        named=tuple(named),
        associated=tuple(associated),
        same_theme=tuple(same_theme),
        associated_words=tuple(words),
        associated_tier=associated_tier,
        projection_built_now=built_now,
        concepts=concepts,
        filters=filters,
    )


def as_payload(reading: AskReading) -> dict[str, Any]:
    """The reading as plain values, for a report or a test."""
    return {
        "q": reading.q,
        "kept": list(reading.kept),
        "dropped": list(reading.dropped),
        "unknown": list(reading.unknown),
        "expanded": [
            {"word": item.word, "to": list(item.to), "how": item.how} for item in reading.expanded
        ],
        "named": [item.experiment.experiment_id for item in reading.named],
        "associated": [item.experiment.experiment_id for item in reading.associated],
        "same_theme": [item.experiment.experiment_id for item in reading.same_theme],
        "associated_words": [word.word for word in reading.associated_words],
    }
