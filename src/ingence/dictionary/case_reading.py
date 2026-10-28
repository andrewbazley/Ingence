"""When a capital inside a word divides it: whole or pieces, read per spelling for the whole run.

A word the Dictionary knows is a first guess. Counts of how the pool writes the pieces decide
whether a split is justified. A glued name that the laboratory uses as a unit is one word, and its
pieces stay linked for search (a qualifier glued before a reagent's name, a construct written as
one label). Two ordinary words joined at a capital are two words (a word before the data it names,
a program before the table it writes).

Where it is read: the tokeniser reads one name alone and cuts ``zibKelt`` into ``zib`` and
``Kelt``. It marks both pieces with the whole spelling
(:attr:`~ingence.dictionary.normalize.Atom.case_whole`, ``NameSlot.case_whole``), so no component's
identity depends on how many other names exist. The binder chooses, once per spelling for the
whole run, from what the run's names show, the Dictionary and the Record. The choice is an
interpretation, reproducible from (Index, Dictionary, Record), and every rebind recomputes it.

The tiers, in order (:func:`decide_case`):

1. A person's answer settles it: "one word" or "separate words", an event on the target
   ``split:<spelling>`` whose payload's ``reading`` is ``whole`` or ``pieces``
   (:func:`answered_cases`).
2. If the Dictionary knows the whole spelling, the spelling reads whole (``mNeonGreen``, a
   fluorescent protein that the microscopy pack spells whole). The reading asks a person where the
   counts disagree.
3. The run's counts decide next. A glued spelling that the laboratory writes at least as often as
   it writes its rarest piece alone is a unit, and reads whole. Where every piece is written alone
   more often than the spelling is glued, the pieces are two words. The ratio is a setting
   (``dictionary.case_reading.whole_over_alone``, default one half).

Counts decide, never a probability: tiers decide and scores break ties. Each reading carries the
counts that chose it, in words a person can check by hand.
"""

from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ingence.core.schema import RecordEventKind
from ingence.dictionary.normalize import AtomKind
from ingence.record.targets import SPLIT_PREFIX, split_target

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import RecordEvent
    from ingence.dictionary.normalize import Atom

__all__ = [
    "CASE_TARGET_PREFIX",
    "CaseCounts",
    "CaseEvidence",
    "CaseReading",
    "Reading",
    "answered_cases",
    "case_target",
    "decide_case",
]

#: The Record target of a person's answer about a spelling a capital divides (``record/targets``).
CASE_TARGET_PREFIX = SPLIT_PREFIX

#: The answers a person gives, as their payload says them: ``reading`` is ``whole`` or
#: ``pieces``. An ``answer`` in words ("one word", "separate words") is read the same.
_WHOLE_WORDS = frozenset({"one word", "one", "whole"})
_PIECE_WORDS = frozenset({"separate words", "two", "pieces", "separate"})


class Reading(StrEnum):
    """How a spelling a capital divides is read."""

    #: As one word: every piece binds as the whole spelling, except that where the Dictionary
    #: does not know the whole and the counts (not a person) chose this reading, a piece it knows
    #: keeps its own sense under ``dictionary.case_reading.unknown_whole: pieces``, the default.
    WHOLE = "whole"
    #: As its pieces, each bound alone, as the tokeniser cuts every such spelling.
    PIECES = "pieces"


def case_target(spelling: str) -> str:
    """The Record target of an answer about one spelling, casefolded (``split:<spelling>``)."""
    return split_target(spelling)


@dataclass(frozen=True, slots=True)
class CaseEvidence:
    """What a run's names show about one spelling, as counts a person could recount."""

    #: The spelling as first written.
    written: str
    #: Its pieces as the tokeniser cut them.
    pieces: tuple[str, ...]
    #: Names carrying the spelling glued (divided at a capital, or written as one word).
    whole_names: int
    #: Per piece: names carrying it as a word of its own.
    alone: tuple[int, ...]
    #: Per piece: how many different words it is glued to at a capital, this spelling's included.
    partners: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CaseReading:
    """The reading chosen for one spelling, the tier that chose it, and whether to ask."""

    written: str
    reading: Reading
    #: ``answered``, ``dictionary``, ``unit`` or ``words``.
    tier: str
    #: Whether a person should be asked (the Dictionary and the counts disagree).
    ask: bool
    #: The counts, in words.
    because: str


class CaseCounts:
    """The counts one run's names give, gathered name by name from any thread.

    The census reads names on several threads, so every update holds a lock: a name's counts are
    worked out without it and added in one step.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._whole_names: Counter[str] = Counter()
        self._alone: Counter[str] = Counter()
        self._partners: defaultdict[str, set[str]] = defaultdict(set)
        self._written: dict[str, str] = {}
        self._pieces: dict[str, tuple[str, ...]] = {}

    def observe(self, atoms: Sequence[Atom]) -> None:
        """Count one name's words: those standing alone, and each spelling a capital divided."""
        alone: set[str] = set()
        glued: dict[str, list[Atom]] = {}
        for atom in atoms:
            if atom.kind is not AtomKind.WORD:
                continue
            if atom.case_whole is None:
                alone.add(atom.text.casefold())
            else:
                glued.setdefault(atom.case_whole, []).append(atom)
        wholes = {spelling.casefold() for spelling in glued} | alone
        with self._lock:
            self._alone.update(alone)
            self._whole_names.update(wholes)
            for spelling, pieces in glued.items():
                folded = spelling.casefold()
                self._written.setdefault(folded, spelling)
                cut = tuple(piece.text for piece in pieces[: _pieces_in(spelling, pieces)])
                self._pieces.setdefault(folded, cut)
                folded_cut = [piece.casefold() for piece in cut]
                for index, piece in enumerate(folded_cut):
                    self._partners[piece].update(folded_cut[:index] + folded_cut[index + 1 :])

    def evidence(self) -> dict[str, CaseEvidence]:
        """Every spelling a capital divided in the run, with its counts, by casefolded spelling."""
        with self._lock:
            return {
                folded: CaseEvidence(
                    written=self._written[folded],
                    pieces=pieces,
                    whole_names=self._whole_names[folded],
                    alone=tuple(self._alone[piece.casefold()] for piece in pieces),
                    partners=tuple(len(self._partners[piece.casefold()]) for piece in pieces),
                )
                for folded, pieces in sorted(self._pieces.items())
            }


def _pieces_in(spelling: str, pieces: Sequence[Atom]) -> int:
    """How many of a name's atoms carrying one spelling make one writing of it (the first)."""
    length = 0
    for count, piece in enumerate(pieces, start=1):
        length += len(piece.text)
        if length >= len(spelling):
            return count
    return len(pieces)


def decide_case(
    evidence: CaseEvidence,
    *,
    whole_known: bool,
    answered: Reading | None = None,
    whole_over_alone: float = 0.5,
) -> CaseReading:
    """The reading of one spelling, by the tiers in the module's docstring."""
    written = evidence.written
    rarest = min(evidence.alone, default=0)
    unit = evidence.whole_names >= whole_over_alone * rarest
    because = f"{_plural(evidence.whole_names, 'name')} carry {written}; " + "; ".join(
        f"{piece} stands alone in {_plural(alone, 'name')} and is glued to "
        f"{_plural(partners, 'word')}"
        for piece, alone, partners in zip(
            evidence.pieces, evidence.alone, evidence.partners, strict=True
        )
    )
    if answered is not None:
        return CaseReading(written, answered, "answered", ask=False, because="a person's answer")
    if whole_known:
        return CaseReading(written, Reading.WHOLE, "dictionary", ask=not unit, because=because)
    if unit:
        return CaseReading(written, Reading.WHOLE, "unit", ask=False, because=because)
    return CaseReading(written, Reading.PIECES, "words", ask=False, because=because)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def answered_cases(events: Iterable[RecordEvent]) -> dict[str, Reading]:
    """What a person said each spelling is, by casefolded spelling, the latest answer winning.

    An event on ``split:<spelling>`` whose payload's ``reading`` is ``whole`` says one word and
    ``pieces`` separate words; a payload's ``answer`` in words counts the same, and so does the
    event's kind where it is ``merge`` (one word) or ``split`` (separate words). Events are
    replayed in the Record's order. A note decides nothing and is passed over, as
    :func:`~ingence.record.log.latest_by_target` passes it; a withdrawal, or any other decision on
    the target, leaves the spelling to the evidence. The spelling is casefolded however the target
    was written.
    """
    latest: dict[str, RecordEvent] = {}
    prefix = f"{CASE_TARGET_PREFIX}:"
    ordered = sorted(
        events, key=lambda event: (event.sequence, str(getattr(event, "event_id", "") or ""))
    )
    for event in ordered:
        if event.kind is RecordEventKind.NOTE or not event.target.startswith(prefix):
            continue
        latest[event.target.removeprefix(prefix).strip().casefold()] = event
    found: dict[str, Reading] = {}
    for spelling, event in sorted(latest.items()):
        reading = _reading_of(event)
        if reading is not None:
            found[spelling] = reading
    return found


def _reading_of(event: RecordEvent) -> Reading | None:
    try:
        payload = json.loads(event.payload or "{}")
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, dict):
        for key in ("reading", "answer"):
            said = str(payload.get(key) or "").strip().casefold()
            if said in _WHOLE_WORDS:
                return Reading.WHOLE
            if said in _PIECE_WORDS:
                return Reading.PIECES
    if event.kind is RecordEventKind.MERGE:
        return Reading.WHOLE
    if event.kind is RecordEventKind.SPLIT:
        return Reading.PIECES
    return None


def project_answers(project: Path) -> dict[str, Reading]:
    """A project's answers about spellings, read from its Record."""
    from ingence.record.log import read_events

    return answered_cases(read_events(project))


def readings_for(
    evidence: Mapping[str, CaseEvidence],
    *,
    knows_whole: Mapping[str, bool],
    answers: Mapping[str, Reading],
    whole_over_alone: float,
) -> dict[str, CaseReading]:
    """The reading of every spelling in a run, by casefolded spelling."""
    return {
        folded: decide_case(
            counted,
            whole_known=knows_whole.get(folded, False),
            answered=answers.get(folded),
            whole_over_alone=whole_over_alone,
        )
        for folded, counted in evidence.items()
    }
