"""``GET /api/v1/words/queue``: the words to teach the engine, most files first.

The Words section of the Questions page lists this. A person who says what a word means, and
which spellings are one thing, makes the engine read their folders better and sharpens the
groupings, which are made of words. Every row is a word the run read, with what the engine
knows of it, the spellings that may be the same word, and the answer in force if somebody gave
one. Nothing here is stored. The queue is computed on request from the run's tables, the
Dictionary as it loads now and the Record, and a read writes nothing.

Rows: by default the words the engine is unsure of.

- ``unknown``: a word no Dictionary sense holds;
- ``contested``: a spelling two or more senses claim level, which the lookup resolves to neither;
- ``glued``: a spelling a capital divides (``zibKelt``), which the case reading read whole or as
  its pieces, or asked about;
- ``slip``: an unknown word one or two letters from a word the Dictionary knows;
- ``variant``: an unknown word that is another unknown word in different clothes (capitals,
  separators, a plural, initials, a spelling taking the other's place);
- ``understood``: only with ``understood=show``, so a person can confirm or correct those too.

Order: the files carrying the word, most first, with occurrences beside it. A word that many
names carry teaches the engine more than one a single name repeats. Ties go to the word, so the
order is stable. No word gets a row only because a pack holds it. Every shipped domain section
loads in every project, so a word that a domain section binds is understood, as any other bound
word is.

The header carries the meter. It holds the reading figure (``reading``, in counts that say what
they count, always the whole run's). For each row it holds the unknown word parts covered by
every row up to and including it (``covered``), over the unknown word parts the meter counts
(``unknown_parts``). Under ``folder`` both are the folder's own: the denominator is the unknown
word parts in names below it, so the last row covers the whole of it. The meter is a count and
its denominator, never a share alone.

Filters: ``state`` (one of the states above), ``kind`` (a row holding a sense of this kind),
``folder`` (a folder path: only words some name below it carries), ``answered`` (``hide``, the
default, ``show`` or ``only``) and ``understood`` (``hide`` or ``show``; a word a person answered
is never hidden as understood, so their answers stay listed once a run reads them). ``limit`` and
``offset`` page the rows; ``total`` counts the rows after the filters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, one, pinned_catalog
from ingence.api.v1.words import FOLDERS_SHOWN
from ingence.core.schema import DictionaryOrigin, RecordEventKind
from ingence.dictionary.variants import (
    GluedSpelling,
    RunWord,
    Variant,
    VariantIndex,
    distinct_pairs,
)
from ingence.dictionary.written import written_forms
from ingence.record.log import answer_digest, latest_by_target, read_events, withdrawable
from ingence.record.targets import SPLIT_PREFIX, distinct_target
from ingence.scan.reading_figure import reading_figure, reading_said

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.core.settings import VariantSettings
    from ingence.dictionary.store import DictionaryStore, Sense
    from ingence.storage.catalog import Catalog

__all__ = [
    "ANSWERED_CHOICES",
    "QUEUE_STATES",
    "QueueAnswer",
    "QueueFolder",
    "QueueGlued",
    "QueueReading",
    "QueueRow",
    "QueueSense",
    "QueueVariant",
    "QueueWords",
    "queue",
]

#: The states a row may be in, in the order a page lists them.
QUEUE_STATES: tuple[str, ...] = (
    "unknown",
    "contested",
    "glued",
    "slip",
    "variant",
    "understood",
)

#: What ``answered`` may be: leave answered words out, list them with the rest, or list only them.
ANSWERED_CHOICES: tuple[str, ...] = ("hide", "show", "only")

#: How many rows a page lists when the request does not say, and the most it may ask for.
DEFAULT_LIMIT = 50
MOST_ROWS = 500

type QueueState = Literal["unknown", "contested", "glued", "slip", "variant", "understood"]
type SenseRole = Literal["in_force", "tied", "outranked"]
type VariantOffer = Literal["same", "kind"]


class QueueSense(ApiModel):
    """One meaning the Dictionary holds for the word."""

    term: str
    theme_kind: str
    #: ``builtin`` (the shipped vocabulary), ``pack``, ``discovered`` or ``user`` (a person).
    origin: str
    #: The vocabulary file or pack section it is written in.
    section: str
    #: Where it applies: ``project``, or ``folder:<path>``.
    scope: str
    #: ``in_force`` (it wins), ``tied`` (it stands level with the winner and the lookup resolves to
    #: neither), or ``outranked`` (it matched and lost on precedence).
    role: SenseRole
    #: The shipped domain section the sense belongs to, where it belongs to one.
    pack: str | None = None


class QueueVariant(ApiModel):
    """A spelling that may be the same word as the row's, with why and how many files carry it."""

    spelling: str
    #: Names in the run carrying it. It is 0 for a word that the Dictionary knows and no name here
    #: writes.
    files: Count
    occurrences: Count
    #: A code: ``slip``, ``case``, ``plural``, ``glued``, ``initials`` or ``same_slot``.
    reason: str
    #: The reason in plain words: "one letter apart", "the same word in capitals", "a plural of
    #: it", "its pieces", "its initials", "they take each other's place".
    reason_said: str
    #: How many edits apart, for a slip. It is None where a distance does not apply.
    distance: Count | None = None
    #: The Dictionary term the spelling is bound to, where it is bound (a slip's target is), and
    #: the kind of that term: what "the same thing" is answered with (an assignment to the term, on
    #: its kind).
    known_as: str | None = None
    known_kind: str | None = None
    #: Which choice the page offers first for the pair: ``same`` (the same thing) or ``kind``
    #: (different things of one kind). Two spellings that take each other's place are as often two
    #: levels of one condition as one word spelled twice, so those offer ``kind``.
    offer: VariantOffer
    #: The Record target of "unrelated" for this pair (``distinct:<a>+<b>``).
    distinct_target: str


class QueueGlued(ApiModel):
    """How the case reading handled a spelling a capital divides, with the counts that decided."""

    written: str
    pieces: list[str]
    #: Names carrying the glued spelling.
    whole_files: Count
    #: Per piece: names carrying it as a word of its own.
    alone_files: list[Count]
    #: ``whole`` (one word) or ``pieces`` (separate words).
    reading: Literal["whole", "pieces"]
    #: ``answered`` (a person), ``dictionary`` (it knows the whole), ``unit`` or ``words`` (counts).
    tier: str
    #: Whether the Dictionary and the counts disagree, so a person is worth asking.
    asked: bool
    #: The counts in words, as a person could recount them.
    because: str
    #: The Record target of the answer ("one word" or "separate words"): ``split:<spelling>``.
    target: str


class QueueFolder(ApiModel):
    """A folder holding names that carry the word, for the "keep it below a folder" choice."""

    path: str
    files: Count


class QueueAnswer(ApiModel):
    """The decision in force on the word, and who made it."""

    #: The decision's place in the Record and its digest: what a withdrawal names it by, so a page
    #: that read an answer the Record has since changed cannot withdraw something else.
    sequence: Count
    digest: str
    #: Whether a person may withdraw this answer on its own (an answer about a word on a ``term:``,
    #: ``split:``, ``distinct:``, ``sense:`` or ``surface:`` target). The page offers the withdraw
    #: only where it is.
    withdrawable: bool
    actor: str
    occurred_at: str
    #: The event kind: ``assign``, ``define``, ``reject``, ``confirm`` or ``annotate``.
    kind: str
    #: The Record target the decision is on.
    target: str
    #: The meaning given, where the decision names one.
    term: str | None = None
    theme_kind: str | None = None
    #: The folder the meaning is kept below, or None for the whole project.
    below: str | None = None
    #: The decision in plain words: "means standard_deviation (statistic)", "not a word".
    said: str


class QueueRow(ApiModel):
    """One word."""

    #: The word as the census stores it (case-folded).
    word: str
    #: Up to three spellings as the names write it.
    examples: list[str]
    #: Names carrying the word, and the word's occurrences across them (for a spelling a capital
    #: divides, the times the names write it whole).
    files: Count
    occurrences: Count
    #: The experiments (as the themes and Years view count them) holding a name that carries the
    #: word. None means one thing: this run concluded no experiment at all, which is not zero.
    #: A row of a run that concluded some always has its count, a glued spelling's included.
    experiments: Count | None
    state: QueueState
    #: For a ``slip`` or a ``variant``: the word it may be a slip or a variant of.
    of: str | None = None
    senses: list[QueueSense]
    variants: list[QueueVariant]
    glued: QueueGlued | None = None
    #: The folders holding the most names with the word, most first, as a word's page lists them.
    where: list[QueueFolder]
    where_not_shown: Count
    #: The unknown word parts this row covers (its occurrences where its word is unknown, else 0),
    #: and every row's so far in this order, this one included.
    unknown_parts: Count
    covered: Count
    #: The decision in force on the word, if any, and whether it is waiting for Re-run.
    answered: QueueAnswer | None = None
    waiting: bool = False
    #: The Record target an answer about the word is written against.
    target: str


class QueueReading(ApiModel):
    """The run's reading of names, in counts that say what they count (``scan/reading_figure``)."""

    #: Parts of names read, words and the rest.
    parts: Count
    #: Words by outcome: ``read``, ``understood``, ``unknown``, ``contested``, ``not_looked_up``.
    words: dict[str, Count]
    #: The parts read as something other than a word, by kind.
    also: dict[str, Count]
    #: The figure in a sentence.
    said: str


@register("words_queue")
class QueueWords(ApiModel):
    """``GET /api/v1/words/queue``: the words to teach, one page, with the meter's counts."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    reading: QueueReading
    #: The meter's denominator: the unknown word parts of the run, or, under ``folder``, those in
    #: names below it (the figure in ``reading`` stays the run's).
    unknown_parts: Count
    #: Rows after the filters, where the page starts, how many it may hold, and how many follow it.
    total: Count
    offset: Count
    limit: Count
    left_out: Count
    #: Rows by state, after every filter but ``state`` (the page's chips).
    by_state: dict[str, Count]
    #: The filters as served.
    answered_filter: Literal["hide", "show", "only"]
    understood_filter: Literal["hide", "show"]
    #: The kinds of thing a meaning may be, for the meaning's choice.
    theme_kinds: list[str]
    #: How many answers wait for Re-run among the words listed.
    waiting: Count
    rows: list[QueueRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# The words of the run.
# ---------------------------------------------------------------------------------------------

#: The spellings a row shows as written, at most.
EXAMPLES_SHOWN = 3

#: A word the Dictionary could not place, as the bindings state it.
_NO_EVIDENCE = "examined_no_evidence"
_CONFLICTING = "examined_conflicting"
_RESOLVED = "resolved"


@dataclass
class _Word:
    """One word of the run, with what the bindings say of it and what the queue makes of it."""

    word: str
    files: int = 0
    occurrences: int = 0
    #: The occurrences the bindings state as unknown: this row's share of the meter.
    unknown_parts: int = 0
    statuses: set[str] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    term: str | None = None
    domain: str | None = None
    origin: str | None = None
    state: str = "unknown"
    of: str | None = None
    #: A spelling a capital divided: not a component, so not counted in the meter.
    glued: bool = False
    variants: tuple[Variant, ...] = ()

    @property
    def bound(self) -> bool:
        return self.statuses == {_RESOLVED}


def _below(folder: str) -> tuple[str, list[object]]:
    """The join that keeps the names below ``folder`` (none for no folder), and its values."""
    if not folder:
        return "", []
    return (
        "JOIN entries e ON e.entry_id = c.entry_id "
        "AND (e.relative_path = ? OR starts_with(e.relative_path, ? || '/'))",
        [folder, folder],
    )


def _words_of_run(catalog: Catalog, folder: str) -> dict[str, _Word]:
    """Every word the run read, with its counts and how the bindings stand it, one pass."""
    join, values = _below(folder)
    rows = catalog.query(
        f"""
        SELECT lower(c.normalized_value) AS word,
               coalesce(b.resolution_status, 'unexamined') AS status,
               count(DISTINCT c.entry_id) AS files,
               sum(c.occurrences) AS occurrences,
               list(DISTINCT b.theme_kind) FILTER (WHERE b.theme_kind IS NOT NULL) AS kinds,
               any_value(b.term) AS term,
               any_value(d.domain) AS domain,
               any_value(d.origin) AS origin
        FROM components c
        {join}
        LEFT JOIN dictionary_bindings b ON b.component_id = c.component_id
        LEFT JOIN dictionary_entries d ON d.term_id = b.term_id
        WHERE c.component_type = 'word' AND c.source_scope = 'name'
        GROUP BY 1, 2
        """,  # noqa: S608 (the only fragment is built here from fixed text)
        values,
    ).fetchall()
    found: dict[str, _Word] = {}
    for word, status, files, occurrences, kinds, term, domain, origin in rows:
        held = found.setdefault(str(word), _Word(word=str(word)))
        held.files += int(files)
        held.occurrences += int(occurrences or 0)
        held.statuses.add(str(status))
        if status == _NO_EVIDENCE:
            held.unknown_parts += int(occurrences or 0)
        held.kinds.update(str(kind) for kind in kinds or ())
        if term and held.term is None:
            held.term = str(term)
        if domain and held.domain is None:
            held.domain = str(domain)
        if origin and held.origin is None:
            held.origin = str(origin)
    return found


def _glued_of_run(catalog: Catalog, folder: str) -> dict[str, tuple[str, int, int]]:
    """Each spelling a capital divided: its casefolded whole to the way it is written, the names
    carrying it and the times they write it. Empty where the run recorded no name shapes.

    A time is one group of the name's parts. The pieces of one written whole share a group, so
    a name writing the spelling twice counts twice and each time counts once, however many pieces
    it was divided into.
    """
    join, values = _below(folder)
    try:
        rows = catalog.query(
            f"""
            SELECT lower(w) AS whole, min(w) AS written, count(DISTINCT c.entry_id) AS files,
                   count(DISTINCT c.entry_id || ':' || c.g) AS occurrences
            FROM (
                SELECT n.entry_id, t.s.case_whole AS w, t.s."group" AS g
                FROM name_shapes n,
                     unnest(from_json(n.slots_json,
                                      '[{{"case_whole":"VARCHAR","group":"INTEGER"}}]')) AS t(s)
                WHERE n.source_scope = 'name'
            ) c
            {join}
            WHERE w IS NOT NULL GROUP BY 1
            """,  # noqa: S608
            values,
        ).fetchall()
    except Exception:  # a run whose name shapes were not recorded
        return {}
    return {
        str(whole): (str(written), int(files), int(times)) for whole, written, files, times in rows
    }


def _glued_pieces(
    catalog: Catalog, wholes: Mapping[str, tuple[str, int, int]]
) -> dict[str, GluedSpelling]:
    """The pieces each glued spelling was cut into, read off one name that carries it."""
    if not wholes:
        return {}
    example = dict(
        catalog.query(
            """
            SELECT lower(w) AS whole, min(entry_id) AS entry_id FROM (
                SELECT entry_id,
                       unnest(from_json(json_extract(slots_json, '$[*].case_whole'),
                                        '["VARCHAR"]')) AS w
                FROM name_shapes WHERE source_scope = 'name'
            ) WHERE w IS NOT NULL GROUP BY 1
            """
        ).fetchall()
    )
    shapes = dict(
        catalog.query(
            "SELECT entry_id, slots_json FROM name_shapes WHERE source_scope = 'name' "
            "AND entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(set(example.values()))],
        ).fetchall()
    )
    needed: dict[str, list[str]] = {}
    for whole, entry in example.items():
        if whole not in wholes:
            continue
        try:
            slots = json.loads(shapes[entry])
        except (KeyError, TypeError, ValueError):
            continue
        needed[whole] = [
            str(slot["component_id"])
            for slot in slots
            if str(slot.get("case_whole") or "").casefold() == whole
        ]
    ids = sorted({component for held in needed.values() for component in held})
    normal = dict(
        catalog.query(
            "SELECT component_id, normalized_value FROM components "
            "WHERE component_id IN (SELECT unnest(?::VARCHAR[]))",
            [ids],
        ).fetchall()
    )
    found: dict[str, GluedSpelling] = {}
    for whole, components in needed.items():
        pieces = tuple(str(normal[c]) for c in components if c in normal)
        if len(pieces) >= 2:
            found[whole] = GluedSpelling(wholes[whole][0], pieces)
    return found


def _initials_spellings(
    catalog: Catalog, words: Mapping[str, _Word], lowest: int, highest: int
) -> dict[str, tuple[str, ...]]:
    """The capital-letter spellings the names write for the short unknown words, as written.

    Only for initials: a short spelling the run writes in capitals only. Keyed by the word.
    """
    unknown = sorted(
        word for word, held in words.items() if not held.bound and lowest <= len(word) <= highest
    )
    if not unknown:
        return {}
    rows = catalog.query(
        """
        SELECT lower(normalized_value) AS word, list(DISTINCT raw_value) AS raws
        FROM components
        WHERE component_type = 'word' AND source_scope = 'name'
          AND lower(normalized_value) IN (SELECT unnest(?::VARCHAR[]))
          AND raw_value IS NOT NULL AND raw_value = upper(raw_value)
        GROUP BY 1
        """,
        [unknown],
    ).fetchall()
    return {str(word): tuple(sorted(str(raw) for raw in raws)) for word, raws in rows}


#: How far from a word, in the words of one name, its company is read: the two words before and the
#: two after it. A word's place in a name is what its neighbors say, so two spellings that take
#: each other's place have the same words at the same distances (unmeasured, flagged).
COMPANY_REACH = 2


def _company(
    catalog: Catalog, words: Mapping[str, _Word], settings: VariantSettings
) -> dict[str, tuple[frozenset[int], frozenset[str], frozenset[str]]]:
    """For the unknown words compared for taking each other's place: the names carrying each, the
    words beside it in those names (each tagged with its distance: ``+1:dapi`` is the next word,
    ``-2:dapi`` the second before), and the folders the names lie in. Most files first, bounded."""
    ranked = sorted(
        (held for held in words.values() if not held.bound and not held.glued),
        key=lambda held: (-held.files, held.word),
    )[: settings.same_slot_words]
    if len(ranked) < 2:
        return {}
    wanted = [held.word for held in ranked]
    carrying = (
        "SELECT entry_id FROM components WHERE component_type = 'word' "
        "AND source_scope = 'name' AND lower(normalized_value) IN (SELECT unnest(?::VARCHAR[]))"
    )
    try:
        spelled = dict(
            catalog.query(
                f"SELECT component_id, lower(normalized_value) FROM components "  # noqa: S608
                f"WHERE component_type = 'word' AND source_scope = 'name' "
                f"AND entry_id IN ({carrying})",
                [wanted],
            ).fetchall()
        )
        shapes = catalog.query(
            f"SELECT n.entry_id, n.slots_json, e.relative_path "  # noqa: S608
            f"FROM name_shapes n JOIN entries e ON e.entry_id = n.entry_id "
            f"WHERE n.source_scope = 'name' AND n.entry_id IN ({carrying}) ORDER BY n.entry_id",
            [wanted],
        ).fetchall()
    except Exception:  # a run whose name shapes were not recorded: no word's place is known
        return {}
    names: dict[str, set[int]] = {word: set() for word in wanted}
    beside: dict[str, set[str]] = {word: set() for word in wanted}
    places: dict[str, set[str]] = {word: set() for word in wanted}
    for number, (_, slots_json, path) in enumerate(shapes):
        try:
            slots = sorted(json.loads(slots_json), key=lambda slot: int(slot.get("ordinal", 0)))
        except (TypeError, ValueError):
            continue
        spoken = [
            spelled[str(slot["component_id"])]
            for slot in slots
            if str(slot.get("component_id")) in spelled
        ]
        place = str(path).rpartition("/")[0]
        for at, word in enumerate(spoken):
            if word not in names:
                continue
            names[word].add(number)
            places[word].add(place)
            for offset in range(-COMPANY_REACH, COMPANY_REACH + 1):
                if offset and 0 <= at + offset < len(spoken):
                    beside[word].add(f"{offset:+d}:{spoken[at + offset]}")
    return {
        word: (frozenset(names[word]), frozenset(beside[word]), frozenset(places[word]))
        for word in wanted
    }


# ---------------------------------------------------------------------------------------------
# What a person has said, and what waits.
# ---------------------------------------------------------------------------------------------


def _said(event: RecordEvent, payload: Mapping[str, Any]) -> str:
    """A decision in plain words."""
    if event.target.startswith(f"{SPLIT_PREFIX}:"):
        return "one word" if payload.get("reading") == "whole" else "separate words"
    if event.kind is RecordEventKind.REJECT:
        return "not a word"
    if event.kind in (RecordEventKind.ASSIGN, RecordEventKind.DEFINE):
        kind = str(payload.get("theme_kind") or "").replace("_", " ")
        return f"means {payload.get('answer')}" + (f" ({kind})" if kind else "")
    return "cannot say"


def _answers_in_force(project: Path) -> dict[str, QueueAnswer]:
    """The decision in force on each word: its meaning (for the whole project, ahead of a folder's),
    its not-a-word, or how its divided spelling reads. One pass over the Record."""
    from ingence.textword.questions import RULE_ID

    asked = f"question:{RULE_ID}:"
    found: dict[str, QueueAnswer] = {}
    for target, event in sorted(latest_by_target(read_events(project)).items()):
        below: str | None = None
        if target.startswith("term:"):
            word, joined, folder = target.removeprefix("term:").partition(" below ")
            below = folder if joined else None
        elif target.startswith(asked):
            word = target.removeprefix(asked)
        elif target.startswith(f"{SPLIT_PREFIX}:"):
            word = target.removeprefix(f"{SPLIT_PREFIX}:")
        else:
            continue
        if event.kind is RecordEventKind.NOTE or not word:
            continue
        try:
            payload = json.loads(event.payload or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        existing = found.get(word)
        if existing is not None and below is not None and existing.below is None:
            continue  # the whole project's meaning is the word's answer, ahead of a folder's
        found[word] = QueueAnswer(
            sequence=int(event.sequence),
            digest=answer_digest(event),
            withdrawable=withdrawable(event),
            actor=event.actor,
            occurred_at=event.occurred_at,
            kind=event.kind.value,
            target=target,
            term=str(payload["answer"]) if payload.get("answer") else None,
            theme_kind=str(payload["theme_kind"]) if payload.get("theme_kind") else None,
            below=below,
            said=_said(event, payload),
        )
    return found


def _waiting_sequences(project: Path) -> frozenset[int]:
    """The Record sequences of the answers waiting for Re-run: what the badge counts."""
    from ingence.workspace.state import pending_rerun

    waiting = pending_rerun(project)
    if waiting is None:
        return frozenset()
    return frozenset(change.sequence for change in waiting.changes if change.sequence is not None)


# ---------------------------------------------------------------------------------------------
# One row.
# ---------------------------------------------------------------------------------------------


def _sense_item(sense: Sense, role: str) -> QueueSense:
    pack = sense.domain if sense.origin is DictionaryOrigin.BUILTIN and sense.domain else None
    return QueueSense(
        term=sense.term,
        theme_kind=sense.theme_kind,
        origin=sense.origin.value,
        section=sense.section,
        scope=sense.scope.token,
        role=role,
        pack=pack,
    )


def _senses(store: DictionaryStore, word: str) -> list[QueueSense]:
    hit = store.look_up(word)
    if hit is None:
        return []
    if hit.ambiguous:
        found = [_sense_item(sense, "tied") for sense in (hit.sense, *hit.alternatives)]
    else:
        found = [_sense_item(hit.sense, "in_force")]
    return [*found, *(_sense_item(sense, "outranked") for sense in hit.shadowed)]


def _variant_item(
    variant: Variant, word: str, store: DictionaryStore | None = None
) -> QueueVariant | None:
    """One variant as the page reads it, or None for a spelling that is the word itself.

    A spelling that casefolds to the word is no other word to call unrelated (a pair is two
    different spellings, :func:`~ingence.record.targets.distinct_target`), so the page has no
    choice to offer for it. ``store`` names the kind of the term a variant is bound to.
    """
    try:
        unrelated = distinct_target(word, variant.spelling)
    except ValueError:
        return None
    sense = store.sense_for_term(variant.known_as) if store and variant.known_as else None
    return QueueVariant(
        spelling=variant.spelling,
        files=variant.files,
        occurrences=variant.occurrences,
        reason=variant.reason,
        reason_said=variant.reason_said,
        distance=variant.distance,
        known_as=variant.known_as,
        known_kind=sense.theme_kind if sense is not None else None,
        offer=variant.offer,
        distinct_target=unrelated,
    )


def _glued_item(
    catalog: Catalog,
    store: DictionaryStore,
    project: Path,
    whole: str,
    pieces: Mapping[str, GluedSpelling],
    files: int,
) -> QueueGlued | None:
    """How the case reading handled one spelling, recomputed from the counts it used."""
    from ingence.core.project import Project
    from ingence.dictionary.case_reading import CaseEvidence, decide_case, project_answers
    from ingence.dictionary.discover import UNIT_KINDS

    held = pieces.get(whole)
    if held is None:
        return None
    alone: list[int] = []
    for piece in held.pieces:
        found = catalog.query(
            """
            SELECT count(DISTINCT n.entry_id) FROM name_shapes n,
              unnest(from_json(n.slots_json,
                               '[{"component_id":"VARCHAR","case_whole":"VARCHAR"}]')) AS t(s)
            WHERE n.source_scope = 'name' AND s.case_whole IS NULL
              AND s.component_id IN
                  (SELECT component_id FROM components WHERE lower(normalized_value) = ?)
            """,
            [piece.casefold()],
        ).fetchone()
        alone.append(int(found[0]) if found else 0)
    partners = tuple(
        len({other for other, glued in pieces.items() if piece in glued.pieces and other != whole})
        + 1
        for piece in held.pieces
    )
    reading = decide_case(
        CaseEvidence(
            written=held.written,
            pieces=held.pieces,
            whole_names=files,
            alone=tuple(alone),
            partners=partners,
        ),
        whole_known=store.look_up(held.written, excluded_kinds=UNIT_KINDS) is not None,
        answered=project_answers(project).get(whole),
        whole_over_alone=Project.open(project).settings.dictionary.case_reading.whole_over_alone,
    )
    return QueueGlued(
        written=held.written,
        pieces=list(held.pieces),
        whole_files=files,
        alone_files=alone,
        reading=reading.reading.value,
        tier=reading.tier,
        asked=reading.ask,
        because=reading.because,
        target=f"{SPLIT_PREFIX}:{whole}",
    )


def _folders_and_examples(
    catalog: Catalog, wanted: Sequence[str]
) -> tuple[dict[str, list[tuple[str, int]]], dict[str, list[str]]]:
    """For the words on the page: the folders holding most of their names, and the spellings
    the names write them in. Two queries for the whole page."""
    if not wanted:
        return {}, {}
    folders: dict[str, list[tuple[str, int]]] = {}
    for word, folder, files in catalog.query(
        """
        SELECT lower(c.normalized_value) AS word,
               coalesce(nullif(regexp_replace(e.relative_path, '/[^/]*$', ''),
                               e.relative_path), '') AS folder,
               count(DISTINCT c.entry_id) AS files
        FROM components c JOIN entries e ON e.entry_id = c.entry_id
        WHERE c.component_type = 'word' AND c.source_scope = 'name'
          AND lower(c.normalized_value) IN (SELECT unnest(?::VARCHAR[]))
        GROUP BY 1, 2 ORDER BY word, files DESC, folder
        """,
        [list(wanted)],
    ).fetchall():
        folders.setdefault(str(word), []).append((str(folder), int(files)))
    examples: dict[str, list[str]] = {}
    for word, raw, _ in catalog.query(
        """
        SELECT lower(normalized_value) AS word, raw_value, count(*) AS n
        FROM components
        WHERE component_type = 'word' AND source_scope = 'name' AND raw_value IS NOT NULL
          AND lower(normalized_value) IN (SELECT unnest(?::VARCHAR[]))
        GROUP BY 1, 2 ORDER BY word, n DESC, raw_value
        """,
        [list(wanted)],
    ).fetchall():
        spelled = examples.setdefault(str(word), [])
        if len(spelled) < EXAMPLES_SHOWN:
            spelled.append(str(raw))
    return folders, examples


def _experiments_of(
    catalog: Catalog, wanted: Sequence[str], glued: Sequence[str] = ()
) -> dict[str, int] | None:
    """For the words on the page, and the glued spellings among them: the experiments (as the
    themes and Years view count them) holding a name that carries each. None where this run
    concluded no experiment: not zero."""
    from ingence.experiment_units import run_units

    if not wanted and not glued:
        return None
    units = run_units(catalog).units
    if not units:
        return None
    reached: dict[str, set[str]] = {word: set() for word in (*wanted, *glued)}

    def reach(rows: Sequence[tuple[Any, ...]]) -> None:
        for word, entry, kind, parent in rows:
            folder = str(entry) if str(kind) == "directory" else (str(parent) if parent else None)
            unit = units.get(folder) if folder is not None else None
            if unit is not None:
                reached[str(word)].add(unit)

    if wanted:
        reach(
            catalog.query(
                """
                SELECT lower(c.normalized_value), e.entry_id, e.entry_type, e.parent_entry_id
                FROM components c JOIN entries e ON e.entry_id = c.entry_id
                WHERE c.component_type = 'word' AND c.source_scope = 'name'
                  AND lower(c.normalized_value) IN (SELECT unnest(?::VARCHAR[]))
                """,
                [list(wanted)],
            ).fetchall()
        )
    if glued:
        # A spelling a capital divides is no component: the names carrying it are in the shapes.
        reach(
            catalog.query(
                """
                SELECT lower(s.w), e.entry_id, e.entry_type, e.parent_entry_id
                FROM (
                    SELECT entry_id,
                           unnest(from_json(json_extract(slots_json, '$[*].case_whole'),
                                            '["VARCHAR"]')) AS w
                    FROM name_shapes WHERE source_scope = 'name'
                ) s JOIN entries e ON e.entry_id = s.entry_id
                WHERE s.w IS NOT NULL AND lower(s.w) IN (SELECT unnest(?::VARCHAR[]))
                """,
                [list(glued)],
            ).fetchall()
        )
    return {word: len(found) for word, found in reached.items()}


# ---------------------------------------------------------------------------------------------
# The route.
# ---------------------------------------------------------------------------------------------


def _choice(
    query: Mapping[str, Sequence[str]], name: str, allowed: Sequence[str], default: str | None
) -> str | None:
    """A parameter that is one of a few words, refused in words otherwise."""
    said = (one(query, name) or "").strip()
    if not said:
        return default
    if said not in allowed:
        message = f"{name} is one of {', '.join(allowed)}; {said!r} is none of them"
        raise ApiError(message, parameter=name)
    return said


def _head_of(held: _Word, words: Mapping[str, _Word]) -> Variant | None:
    """The word an unknown word is a slip or a variant of: a known word it is a slip of, or a more
    common unknown spelling alike once capitals, separators or a plural are set aside."""
    candidates = []
    for variant in held.variants:
        if variant.reason not in ("slip", "case", "separators", "plural", "singular"):
            continue
        other = words.get(variant.spelling.casefold()) or words.get(variant.spelling)
        if variant.known_as is not None:
            candidates.append(variant)
        elif other is not None and (-other.files, other.word) < (-held.files, held.word):
            candidates.append(variant)  # more files, or as many and sorting first
    return min(candidates, key=Variant.order) if candidates else None


@route("words/queue", model=QueueWords)
def queue(context: V1Context, query: Mapping[str, Sequence[str]]) -> QueueWords:
    """The queue of words to teach, most files first. Nothing is written."""
    from ingence.api.routes.review import _theme_kinds
    from ingence.core.project import Project
    from ingence.dictionary.store import DictionaryStore
    from ingence.textword.questions import spelling_index

    state = _choice(query, "state", QUEUE_STATES, None)
    kind = (one(query, "kind") or "").strip() or None
    folder = (one(query, "folder") or "").strip().strip("/")
    answered_filter = _choice(query, "answered", ANSWERED_CHOICES, "hide")
    understood_filter = _choice(query, "understood", ("hide", "show"), "hide")
    limit = integer(query, "limit", default=DEFAULT_LIMIT, low=1, high=MOST_ROWS)
    offset = integer(query, "offset", default=0, low=0, high=10_000_000)

    project = context.project
    settings = Project.open(project).settings.dictionary
    with pinned_catalog(context) as (catalog, run_id):
        figure = reading_figure(catalog)
        words = _words_of_run(catalog, folder)
        # Under a folder, the meter's denominator is the folder's own: the unknown word parts
        # in names below it, which is what ``covered`` counts toward. Otherwise it is the run's
        # whole figure (the bindings and the figure count the same parts).
        meter = (
            sum(held.unknown_parts for held in words.values())
            if folder
            else int(figure["words"]["unknown"])
        )
        wholes = _glued_of_run(catalog, folder)
        pieces = _glued_pieces(catalog, wholes) if settings.variants.glued else {}
        for whole, (_, files, times) in wholes.items():
            if whole not in words:
                words[whole] = _Word(
                    word=whole, files=files, occurrences=times, glued=True, statuses={"glued"}
                )
        store = DictionaryStore.for_project(project)

        # How each word stands, then the spellings that may be the same word.
        for held in words.values():
            if _CONFLICTING in held.statuses:
                held.state = "contested"
            elif held.glued:
                held.state = "glued"
            elif held.bound:
                held.state = "understood"
        company = _company(catalog, words, settings.variants) if settings.variants.same_slot else {}
        capitals = (
            _initials_spellings(
                catalog,
                words,
                settings.variants.initials_minimum_letters,
                settings.variants.initials_maximum_letters,
            )
            if settings.variants.initials
            else {}
        )
        run_words = {
            word: RunWord(
                word=word,
                files=held.files,
                occurrences=held.occurrences,
                raw=capitals.get(word, ()),
                term=held.term if held.bound else None,
                names=company[word][0] if word in company else frozenset(),
                neighbors=company[word][1] if word in company else frozenset(),
                places=company[word][2] if word in company else frozenset(),
            )
            for word, held in words.items()
        }
        index = VariantIndex(
            run_words,
            settings=settings.variants,
            spelling=settings.spelling,
            known=spelling_index(catalog, settings.spelling) if settings.variants.slips else None,
            glued=pieces,
            distinct=distinct_pairs(project),
        )
        answers = _answers_in_force(project)
        for held in words.values():
            # A word the Dictionary understands has variants only through its initials and its
            # pieces, which a page shows beside a pack's word as well. A word shown only on
            # request (``understood``) is not looked at where it is not shown.
            if (
                held.state == "understood"
                and understood_filter == "hide"
                and held.word not in answers
            ):
                continue
            held.variants = index.of(held.word)
            if held.state == "unknown":
                head = _head_of(held, words)
                if head is not None:
                    held.state = "slip" if head.known_as is not None else "variant"
                    held.of = head.spelling

        rows = list(words.values())
        if understood_filter == "hide":
            # A word a person answered stays in view once a run understands it. It is understood
            # because they said what it means, and the page lists their answers (and offers to
            # withdraw one) whether or not the engine is sure of the word now.
            rows = [held for held in rows if held.state != "understood" or held.word in answers]
        if answered_filter == "hide":
            rows = [held for held in rows if held.word not in answers]
        elif answered_filter == "only":
            rows = [held for held in rows if held.word in answers]
        if kind is not None:
            rows = [held for held in rows if kind in held.kinds]
        by_state: dict[str, int] = {}
        for held in rows:
            by_state[held.state] = by_state.get(held.state, 0) + 1
        if state is not None:
            rows = [held for held in rows if held.state == state]
        # By files, most first, then the word.
        rows.sort(key=lambda held: (-held.files, held.word))

        # The meter: the unknown word parts covered by every row up to and including each one.
        covered: dict[str, int] = {}
        running = 0
        for held in rows:
            running += held.unknown_parts
            covered[held.word] = running

        page = rows[offset : offset + limit]
        wanted = [held.word for held in page if not held.glued]
        folders, examples = _folders_and_examples(catalog, wanted)
        experiments = _experiments_of(catalog, wanted, [held.word for held in page if held.glued])
        sequences = _waiting_sequences(project) if answers else frozenset()
        written = written_forms(catalog)
        out: list[QueueRow] = []
        for held in page:
            said = answers.get(held.word)
            listed = folders.get(held.word, [])
            glued_item = (
                _glued_item(catalog, store, project, held.word, pieces, held.files)
                if held.glued
                else None
            )
            out.append(
                QueueRow(
                    word=held.word,
                    examples=(
                        [pieces[held.word].written]
                        if held.glued and held.word in pieces
                        else examples.get(held.word, [written.get(held.word, held.word)])
                    ),
                    files=held.files,
                    occurrences=held.occurrences,
                    experiments=None if experiments is None else experiments.get(held.word, 0),
                    state=held.state,
                    of=held.of,
                    senses=[] if held.glued else _senses(store, held.word),
                    variants=[
                        item
                        for variant in held.variants
                        if (item := _variant_item(variant, held.word, store)) is not None
                    ],
                    glued=glued_item,
                    where=[
                        QueueFolder(path=path, files=files)
                        for path, files in listed[:FOLDERS_SHOWN]
                    ],
                    where_not_shown=max(0, len(listed) - FOLDERS_SHOWN),
                    unknown_parts=held.unknown_parts,
                    covered=covered[held.word],
                    answered=said,
                    waiting=said is not None and said.sequence in sequences,
                    target=f"{SPLIT_PREFIX}:{held.word}" if held.glued else f"term:{held.word}",
                )
            )
        theme_kinds = _theme_kinds(catalog)
    waiting = sum(
        1
        for held in words.values()
        if held.word in answers and answers[held.word].sequence in sequences
    )
    return QueueWords(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        reading=QueueReading(
            parts=int(figure["parts"]),
            words={name: int(count) for name, count in figure["words"].items()},
            also={name: int(count) for name, count in figure["also"].items()},
            said=reading_said(figure),
        ),
        unknown_parts=meter,
        total=len(rows),
        offset=offset,
        limit=limit,
        left_out=max(0, len(rows) - offset - len(out)),
        by_state=dict(sorted(by_state.items())),
        answered_filter=answered_filter,
        understood_filter=understood_filter,
        theme_kinds=list(theme_kinds),
        waiting=waiting,
        rows=out,
    )
