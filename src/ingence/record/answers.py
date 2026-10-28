"""The answers that inference reads about sets of copies and proposed pairs.

Inference reads a person's answer on a set of copies or on a proposed pair. The answer counts the
way every answer inference reads counts: it is read from the Record, never guessed, and nothing is
written on anybody's behalf.

- A set answered "Unrelated" is not called copies. The latest decision on a class of copies that
  rejects it (``not_copies``, or ``related`` as older versions stored it) is a fired defeater on
  every member's copy claim. It names the rule that reads a person's copies answers and cites the
  decision's event. The claims are published as conflicting (a defeated claim is published and never
  silently removed), and the page does not ask about the set again.
- A set answered "Copies" stands as a person's answer, as an approved group of experiments does. A
  rule of its own groups the files a person confirmed, keyed by the set
  (``rulebook/predicates/answered.py``).
- A pair with any answer is not proposed again unless what proposed it changed. The answer stores
  the pair's evidence, which is the proposing rule and what it read. :func:`unanswered_pairs` is the
  one function the proposing phase publishes through. It leaves out a proposal of an answered pair
  where the proposal's evidence is the evidence that was answered.

Every answer is read at the run's Record watermark (a run is reproducible from the Index, Rulebook,
Dictionary and Record). :func:`events_at` replays the Record as it stood at the sequence the run
recorded, so the next run reads a decision that was written while a run was under way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.ids import canonical_json
from ingence.core.schema import RecordEventKind
from ingence.record.log import latest_by_target, read_at, read_events
from ingence.record.targets import CANDIDATE_PREFIX, CLASS_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import Candidate, RecordEvent
    from ingence.storage.catalog import Catalog

    #: Spells the records a support cites (a claim by what it concluded), given their identifiers.
    type Spelling = Callable[[Collection[str]], Mapping[str, str]]

__all__ = [
    "CLAIMS_CLASS",
    "CLAIM_CLASS_WORDS",
    "COPIES_CONFIRMED",
    "COPIES_REFUSED",
    "DIRECTION_CONFIRMED",
    "AnswersRead",
    "ClaimClassAnswer",
    "CopiesAnswer",
    "PairAnswer",
    "answered_as",
    "answers_read",
    "carried_confirmations",
    "catalog_spelling",
    "claim_spelled",
    "events_at",
    "evidence_matches",
    "inference_reads",
    "pair_evidence",
    "rejected_by_a_person",
    "stored_evidence",
    "unanswered_pairs",
]

#: The words confirming a pair's relation, in a direction: such a pair stays published, answered.
DIRECTION_CONFIRMED = frozenset({"derived", "reversed"})
#: The word saying a set is copies of one file.
COPIES_CONFIRMED = frozenset({"copies"})
#: The words saying a set is not copies: "Unrelated", and "Related, not copies" as older versions
#: stored it.
COPIES_REFUSED = frozenset({"not_copies", "related"})
#: A class of claims answered on its handle: every claim that one rule made with one value. The
#: answer's payload says ``of: claims``. "Correct" confirms the rule's reading of the class. "Wrong"
#: refuses it, and inference records the refusal, fired, on every claim of the class.
CLAIMS_CLASS = "claims"
CLAIM_CLASS_WORDS = frozenset({"correct", "wrong"})


@dataclass(frozen=True, slots=True)
class CopiesAnswer:
    """The decision in force on one class of copies: its word, and the event that said it."""

    target: str
    word: str
    event_id: str
    sequence: int
    #: The files the person was shown. None for an answer stored by an older version, which named
    #: none and is read as about every file the class holds.
    members: frozenset[str] | None = None

    @property
    def confirms(self) -> bool:
        return self.word in COPIES_CONFIRMED


@dataclass(frozen=True, slots=True)
class ClaimClassAnswer:
    """The decision in force on one class of claims: its word, and the event."""

    target: str
    word: str
    event_id: str
    sequence: int
    #: The files the class held when the answer was given. A file joining it later is read as the
    #: class is, and said to have joined after the answer. None for an answer that named none.
    members: frozenset[str] | None = None

    @property
    def refuses(self) -> bool:
        return self.word == "wrong"


@dataclass(frozen=True, slots=True)
class PairAnswer:
    """The decision in force on one proposed pair, with the evidence it was given on."""

    candidate_id: str
    word: str
    ends: frozenset[str]
    rule_id: str
    #: The evidence as :func:`pair_evidence` spells it, canonically. None for an answer stored by an
    #: older version, which named none.
    evidence: str | None
    #: The pair's two files in the order proposed. A confirmation says which way round.
    source: str = ""
    target: str = ""


@dataclass(frozen=True, slots=True)
class AnswersRead:
    """What the Record says, at one watermark, about sets of copies and proposed pairs."""

    watermark: int
    #: The latest copies answer on each class target, "Can't say" left out (it decides nothing).
    copies: Mapping[str, CopiesAnswer] = field(default_factory=dict)
    #: Every answered pair, by its two ends.
    pairs: Mapping[frozenset[str], tuple[PairAnswer, ...]] = field(default_factory=dict)
    #: The latest answer on each class of claims that a person answered on its handle.
    claim_classes: Mapping[str, ClaimClassAnswer] = field(default_factory=dict)

    def refused_claim_classes(self) -> dict[str, ClaimClassAnswer]:
        """Each class of claims a person answered a rule read wrongly, by target."""
        return {target: held for target, held in self.claim_classes.items() if held.refuses}

    def refused_copies(self) -> dict[str, CopiesAnswer]:
        """Each class of copies a person answered is not copies, by target."""
        return {target: answer for target, answer in self.copies.items() if not answer.confirms}

    def confirmed_copies(self) -> dict[str, CopiesAnswer]:
        """Each class of copies a person answered is copies of one file, by target."""
        return {target: answer for target, answer in self.copies.items() if answer.confirms}


def events_at(project: Path, watermark: int) -> tuple[RecordEvent, ...]:
    """The Record in force as it stood at a watermark: every event up to it, replayed.

    A withdrawal after the watermark does not reach back, and a decision after it is not read,
    so two readings at one watermark agree however much has been written since. The one bound
    every reader of a run reads through (``record.log.read_at``).
    """
    with read_at(project, watermark):
        return read_events(project)


def _payload(event: RecordEvent) -> dict[str, Any]:
    try:
        said = json.loads(event.payload or "{}")
    except (TypeError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


def inference_reads(event: RecordEvent) -> bool:
    """Whether the next inference reads this decision: a copies answer, or any answer on a pair.

    For the list of what waits for Re-run (``workspace/state.py``), which keys the answers a run
    reads by their target's prefix: ``class`` where the word says copies or not, ``candidate``
    for every word, "Can't say" too, since a pair answered anything is not proposed again.
    """
    if event.kind in (RecordEventKind.NOTE, RecordEventKind.WITHDRAW):
        return False
    prefix = event.target.partition(":")[0]
    if prefix == CANDIDATE_PREFIX:
        return True
    if prefix == CLASS_PREFIX:
        said = _payload(event)
        word = str(said.get("verdict") or "")
        if said.get("of") == CLAIMS_CLASS:
            # A proven reading's class is recorded and never changed, so nothing waits for it.
            return word in CLAIM_CLASS_WORDS and said.get("retractable") is not False
        return word in COPIES_CONFIRMED or word in COPIES_REFUSED
    return False


def answers_read(events: Sequence[RecordEvent], *, watermark: int = 0) -> AnswersRead:
    """Read the copies answers and the pair answers in force from these events."""
    copies: dict[str, CopiesAnswer] = {}
    pairs: dict[frozenset[str], list[PairAnswer]] = {}
    claim_classes: dict[str, ClaimClassAnswer] = {}
    class_prefix = f"{CLASS_PREFIX}:"
    candidate_prefix = f"{CANDIDATE_PREFIX}:"
    for target, event in latest_by_target(events).items():
        if target.startswith(class_prefix):
            said = _payload(event)
            word = str(said.get("verdict") or "")
            shown = said.get("members")
            if said.get("of") == CLAIMS_CLASS:
                if word in CLAIM_CLASS_WORDS:
                    claim_classes[target] = ClaimClassAnswer(
                        target,
                        word,
                        event.event_id,
                        event.sequence,
                        frozenset(str(item) for item in shown) if isinstance(shown, list) else None,
                    )
                continue
            if word in COPIES_CONFIRMED or word in COPIES_REFUSED:
                copies[target] = CopiesAnswer(
                    target,
                    word,
                    event.event_id,
                    event.sequence,
                    frozenset(str(item) for item in shown) if isinstance(shown, list) else None,
                )
        elif target.startswith(candidate_prefix):
            said = _payload(event)
            ends = frozenset(str(said[end]) for end in ("source_id", "target_id") if said.get(end))
            if len(ends) != 2:
                continue
            stored = said.get("evidence")
            pairs.setdefault(ends, []).append(
                PairAnswer(
                    candidate_id=target[len(candidate_prefix) :],
                    word=str(said.get("verdict") or event.kind.value),
                    ends=ends,
                    rule_id=str(said.get("rule_id") or ""),
                    evidence=canonical_json(stored) if isinstance(stored, dict) else None,
                    source=str(said["source_id"]),
                    target=str(said["target_id"]),
                )
            )
    return AnswersRead(
        watermark=watermark,
        copies=copies,
        pairs={ends: tuple(held) for ends, held in pairs.items()},
        claim_classes=claim_classes,
    )


def claim_spelled(subject: str, rule_id: str, value: str | None) -> str:
    """A claim a support cites, spelled by what it concluded: its subject, rule and value.

    It is not spelled by its identifier, which names the rule's version. A stage rule declared again
    that concludes the same stage about the same file has told the pair nothing new.
    """
    return f"{subject} {rule_id} {value or ''}".strip()


def _read_by(component: object) -> tuple[str, str, tuple[str, ...]]:
    """One support component: its name, its family and the records it read, as stored."""
    if isinstance(component, dict):
        name, family = component.get("name"), component.get("family")
        read = component.get("observations") or ()
    else:
        name, family = getattr(component, "name", ""), getattr(component, "family", "")
        read = getattr(component, "observations", ()) or ()
    return str(name or ""), str(family or ""), tuple(str(item) for item in read)


def _cited(support: Iterable[object]) -> set[str]:
    return {record for component in support for record in _read_by(component)[2]}


def pair_evidence(
    rule_id: str, support: Iterable[object], spelled: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """What a pair was proposed on: the proposing rule, and each piece of support with what it read.

    ``spelled`` gives a cited record's spelling where it has one (a claim, by what it concluded:
    :func:`claim_spelled`). A record without one is its identifier. Neither the rule's version nor a
    support's value is part of the evidence. A rule declared again that reads the same things has
    found nothing new, and a value is computed from what was read. The evidence is canonical, so one
    pair proposed twice on one reading has one evidence.
    """
    said = spelled or {}
    return {
        "rule_id": rule_id,
        "support": sorted(
            [name, family, sorted(said.get(record, record) for record in read)]
            for name, family, read in (_read_by(item) for item in support)
        ),
    }


def catalog_spelling(catalog: Catalog) -> Spelling:
    """Spell the claims a run's supports cite, from the run's own claims table."""

    def spell(wanted: Collection[str]) -> Mapping[str, str]:
        if not wanted:
            return {}
        return {
            str(claim_id): claim_spelled(str(subject), str(rule_id), value)
            for claim_id, subject, rule_id, value in catalog.query(
                "SELECT claim_id, subject, rule_id, object_reference FROM claims "
                "WHERE claim_id IN (SELECT unnest(?::VARCHAR[]))",
                [sorted(wanted)],
            ).fetchall()
        }

    return spell


def stored_evidence(catalog: Catalog, candidate_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Each proposed pair's evidence in the current run, by candidate, as an answer stores it."""
    rows = catalog.query(
        "SELECT candidate_id, rule_id, support_json FROM candidates "
        "WHERE candidate_id IN (SELECT unnest(?::VARCHAR[]))",
        [sorted(set(candidate_ids))],
    ).fetchall()
    supports = {
        str(candidate_id): (str(rule_id), json.loads(stored or "[]"))
        for candidate_id, rule_id, stored in rows
    }
    spelled = catalog_spelling(catalog)(
        {record for _, support in supports.values() for record in _cited(support)}
    )
    return {
        candidate_id: pair_evidence(rule_id, support, spelled)
        for candidate_id, (rule_id, support) in supports.items()
    }


def evidence_matches(stored: object, current: Mapping[str, Any]) -> bool:
    """Whether an answer's stored evidence is the pair's evidence now (True where none is stored).

    An answer stored by an older version named no evidence, and is taken as given on this one.
    """
    if not isinstance(stored, dict):
        return True
    return canonical_json(stored) == canonical_json(dict(current))


def answered_as(
    ends: frozenset[str],
    rule_id: str,
    evidence: Mapping[str, Any],
    answers: AnswersRead,
    *,
    besides: str = "",
) -> tuple[PairAnswer, ...]:
    """The answers given on a pair's two files on the evidence the pair rests on now.

    An answer matches on the evidence it stored. An answer stored by an older version with no
    evidence matches on the proposing rule alone. ``besides`` leaves out the answer on the very
    proposal named.
    """
    spelled = canonical_json(dict(evidence))
    return tuple(
        answer
        for answer in answers.pairs.get(ends, ())
        if answer.candidate_id != besides
        and (
            answer.evidence == spelled if answer.evidence is not None else answer.rule_id == rule_id
        )
    )


def carried_confirmations(
    proposals: Iterable[tuple[str, str, str, str]],
    answers: AnswersRead,
    evidence_of: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """The answers that a proposal with no answer of its own reads, by candidate.

    ``proposals`` are ``(candidate, source, target, proposing rule)``. ``evidence_of`` gives their
    evidence now. A confirmation ("Made from it", "The other way round") is carried to a proposal of
    the same two files on the evidence it was given on, the same way round. It is never carried to
    the other direction. A proposal the other way round is asked, as any unanswered proposal is.
    Each value names the targets that the answers were written on.
    """
    carried: dict[str, tuple[str, ...]] = {}
    for candidate, source, target, rule_id in proposals:
        found = answered_as(
            frozenset((source, target)),
            rule_id,
            evidence_of.get(candidate, {}),
            answers,
            besides=candidate,
        )
        named = tuple(
            f"{CANDIDATE_PREFIX}:{answer.candidate_id}"
            for answer in found
            if answer.word in DIRECTION_CONFIRMED
            and (answer.source, answer.target) == (source, target)
        )
        if named:
            carried[candidate] = named
    return carried


#: On a pair proposed as related, "Related" confirms it too. The pair stays published, answered, as
#: a confirmed direction does on a proposed derivation.
RELATED_CONFIRMED = frozenset({"related"})
#: The primitive a pair proposed as related is proposed as (``record.verdicts.RELATED_RELATION``).
_RELATED_PRIMITIVE = "concerns"


def _confirming(candidate: Candidate) -> frozenset[str]:
    """The words that keep a candidate published, answered: a direction, and on a related pair,
    "Related" as well."""
    if str(getattr(candidate, "primitive", "")) == _RELATED_PRIMITIVE:
        return DIRECTION_CONFIRMED | RELATED_CONFIRMED
    return DIRECTION_CONFIRMED


def rejected_by_a_person(candidate: Candidate, answers: AnswersRead | None) -> bool:
    """Whether the latest answer on this very proposal refused it.

    The phase after proposing reads a derivation that a person has not refused. A derivation
    answered "Unrelated", "Related" or "The other way round" raises no question about the stage or
    the time it would disagree with.
    """
    if answers is None:
        return False
    from ingence.record.verdicts import kind_for

    own = [
        answer
        for answer in answers.pairs.get(frozenset((candidate.source_id, candidate.target_id)), ())
        if answer.candidate_id == candidate.candidate_id
    ]
    if not own:
        return False
    word = own[-1].word
    return kind_for(word, primitive=str(candidate.primitive)) is RecordEventKind.REJECT


def unanswered_pairs(
    candidates: Sequence[Candidate], answers: AnswersRead, spell: Spelling | None = None
) -> tuple[tuple[Candidate, ...], int]:
    """The candidates to publish, and how many were left out as answered on the same evidence.

    A pair that a person answered is not proposed again unless its evidence changed. A candidate
    whose two files a person answered about, on the evidence it rests on now, is left out where that
    answer refused it ("Unrelated", "Related") or said "Can't say". This holds however the proposal
    is spelled now (another version of the rule, the other direction).

    A pair that a person confirmed a direction of ("Made from it", "The other way round") stays
    published. The page shows it answered by that answer (``pages._carried_answers``), so it is
    neither asked again nor lost from Proposed pairs and the Graph. The very proposal answered is
    kept, answered, and a page asks it again only where its evidence changed.

    The proposing phase publishes through this function, whichever way its pairs were drawn.
    ``spell`` spells the claims that a support cites (:func:`claim_spelled`), as the answer route
    spelled them.
    """
    asked: list[tuple[Candidate, tuple[PairAnswer, ...]]] = []
    for candidate in candidates:
        held = answers.pairs.get(frozenset((candidate.source_id, candidate.target_id)))
        if held and all(answer.candidate_id != candidate.candidate_id for answer in held):
            asked.append((candidate, held))
    if not asked:
        return tuple(candidates), 0
    wanted = {record for candidate, _ in asked for record in _cited(candidate.support)}
    spelled = spell(wanted) if spell is not None else {}
    left_out: set[str] = set()
    for candidate, held in asked:
        found = answered_as(
            frozenset(held[0].ends),
            candidate.rule_id,
            pair_evidence(candidate.rule_id, candidate.support, spelled),
            answers,
        )
        if found and not any(answer.word in _confirming(candidate) for answer in found):
            left_out.add(candidate.candidate_id)
    kept = tuple(candidate for candidate in candidates if candidate.candidate_id not in left_out)
    return kept, len(left_out)
