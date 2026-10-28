"""What the inference pass does with a person's answers on copies and pairs.

``record/answers.py`` reads the answers at the run's Record watermark, and this module applies
them. The pass itself calls three functions and holds nothing of the answers' shape:

- :func:`refusal_defeaters`: a person may answer that a set is unrelated. Every member's copy claim
  that can be retracted (never the entailed whole-file one) then gets a fired defeater. The
  defeater names the rule that reads a person's copies answers and cites the decision's event. The
  claim is published as conflicting and never removed.
- :func:`with_answered_copies`: the files of each set that a person answered are copies are handed
  the set, so that rule can group them once the rules forming copies have claimed their classes.
- ``record.answers.unanswered_pairs``: the proposing phase calls it. The proposals of a pair that a
  person answered are left out where the evidence is the one answered.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ingence.core.schema import Defeater
from ingence.core.tiers import InferenceTier
from ingence.record.answers import answers_read, claim_spelled, events_at
from ingence.record.targets import class_target
from ingence.rulebook.predicates.answered import (
    ANSWERED_CLAIM_CLASS_PREDICATE,
    ANSWERED_COPIES_PREDICATE,
)
from ingence.rulebook.render import render_rule

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence
    from pathlib import Path

    from ingence.core.schema import Claim, Rule
    from ingence.infer.subjects import EntryRecords
    from ingence.record.answers import AnswersRead, Spelling

#: The tiers a person's "Unrelated" may defeat, as ``Rule`` allows defeaters only on them.
_RETRACTABLE = frozenset({InferenceTier.DEFEASIBLE, InferenceTier.ABDUCTIVE})

__all__ = [
    "JOINED_AFTER",
    "ClassRefusals",
    "Refusal",
    "answer_reader",
    "answers_for_pass",
    "claim_class_reader",
    "claims_spelling",
    "refusal_defeaters",
    "with_answered_copies",
]


def answer_reader(rules: Iterable[Rule]) -> Rule | None:
    """The rule in force reading a person's copies answers, the first by identifier, or None."""
    readers = sorted(
        (rule for rule in rules if rule.predicate == ANSWERED_COPIES_PREDICATE),
        key=lambda rule: rule.rule_id,
    )
    return readers[0] if readers else None


def answers_for_pass(
    project_path: Path, watermark: int, *, reads_copies: bool, proposes: bool
) -> AnswersRead | None:
    """The answers a pass reads, at its watermark, or None where no rule in force reads any.

    Copies answers are read where a rule reads them; pair answers wherever a rule proposes pairs,
    since every proposal is published through the filter.
    """
    if not reads_copies and not proposes:
        return None
    return answers_read(events_at(project_path, watermark), watermark=watermark)


def claims_spelling(published: Sequence[Claim]) -> Spelling:
    """Spell the claims a pair's support cites from the claims this pass published so far.

    What the answer route reads from a run's claims table (``record.answers.catalog_spelling``),
    read here from the pass itself, one scan per call and only for the claims asked about.
    """

    def spell(wanted: Collection[str]) -> Mapping[str, str]:
        if not wanted:
            return {}
        return {
            claim.claim_id: claim_spelled(claim.subject, claim.rule_id, claim.object_reference)
            for claim in published
            if claim.claim_id in wanted
        }

    return spell


def refusal_defeaters(
    rule: Rule, keys: Iterable[str], answers: AnswersRead | None, reader: Rule | None
) -> dict[str, Refusal]:
    """The defeater a person's "Unrelated" puts on each class of one rule they refused, by key.

    The defeater is fired. It names the rule reading the answers and cites the decision's event as
    the record the defeat rests on. It applies only where that rule is in force: with no rule
    reading the answers, no answer is read. That is how every answer the pass reads is reached.

    Only a claim that can be retracted gets a defeater (the tiers ``Rule`` itself allows defeaters
    on). The whole-file digest's ``identical`` claim is entailed, so it is a fact, and "Unrelated"
    grants it ("distinct files that happen to agree"). Byte-identical files stay identical, whatever
    a person answers.
    """
    if answers is None or reader is None or rule.tier not in _RETRACTABLE:
        return {}
    refused = answers.refused_copies()
    if not refused:
        return {}
    reason = render_rule(reader)
    found: dict[str, Refusal] = {}
    for key in keys:
        answer = refused.get(class_target(rule.rule_id, key))
        if answer is not None:
            found[key] = Refusal(
                Defeater(
                    reason=reason,
                    rule_id=reader.rule_id,
                    rule_version=reader.version,
                    observations=(answer.event_id,),
                    fired=True,
                ),
                answer.members,
            )
    return found


@dataclass(frozen=True, slots=True)
class Refusal:
    """A person's "Unrelated" on one class: the defeater, and the files they were shown.

    ``shown`` is None for an answer stored before answers recorded the files shown. Such an answer
    names no files and applies to every member.
    """

    defeater: Defeater
    shown: frozenset[str] | None

    def on(self, subject: str) -> tuple[Defeater, ...]:
        """The defeater on one member's claim, or none for a file the person was not shown."""
        return (self.defeater,) if self.shown is None or subject in self.shown else ()


def with_answered_copies(
    subjects: Sequence[EntryRecords], published: Iterable[Claim], answers: AnswersRead | None
) -> tuple[EntryRecords, ...]:
    """Hand each file of a set a person answered are copies the set it belongs to.

    The set's members are the claims of its class in this pass, so the pass must have run the rules
    forming copies first. Copies of one file is an equivalence, so two answered sets sharing a file
    are one set, named by its least class target. A file in none is handed nothing.
    """
    confirmed = answers.confirmed_copies() if answers is not None else {}
    if not confirmed:
        return tuple(subjects)
    targets: dict[tuple[str, str], str] = {}
    members: dict[str, set[str]] = {}
    for claim in published:
        if not claim.predicate.startswith("groups.") or claim.object_reference is None:
            continue
        spelled = (claim.rule_id, claim.object_reference)
        target = targets.get(spelled)
        if target is None:
            target = targets[spelled] = class_target(*spelled)
        shown = confirmed[target].members if target in confirmed else None
        if target in confirmed and (shown is None or claim.subject in shown):
            # Only the files the person was shown.
            members.setdefault(target, set()).add(claim.subject)
    set_of = _joined(members)
    if not set_of:
        return tuple(subjects)
    return tuple(
        replace(held, answered_copies=set_of[held.entry.entry_id])
        if held.entry.entry_id in set_of
        else held
        for held in subjects
    )


def _joined(members: Mapping[str, set[str]]) -> dict[str, str]:
    """Each file's set, where sets sharing a file are one, named by the set's least target."""
    parent: dict[str, str] = {}

    def root(item: str) -> str:
        while parent.setdefault(item, item) != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for target in sorted(members):
        for subject in sorted(members[target]):
            first, second = root(target), root(f"entry:{subject}")
            if first != second:
                low, high = sorted((first, second))
                parent[high] = low
    named: dict[str, str] = {}
    for target in sorted(members):
        for subject in members[target]:
            named[subject] = root(target)
    # A root spelled for an entry never names a set: every entry joined a target first.
    return {subject: name for subject, name in named.items() if not name.startswith("entry:")}


def claim_class_reader(rules: Iterable[Rule]) -> Rule | None:
    """The rule in force reading a person's answers on classes of claims, or None."""
    readers = sorted(
        (rule for rule in rules if rule.predicate == ANSWERED_CLAIM_CLASS_PREDICATE),
        key=lambda rule: rule.rule_id,
    )
    return readers[0] if readers else None


#: What a defeater says beside the rule's sentence on a file that joined its class after the answer
#: was given. Errors come in whole classes, so the answer reaches such a file, and the defeater
#: says so.
JOINED_AFTER = "This file joined the class after the answer was given."


class ClassRefusals:
    """The defeater a person's "Wrong" on a class of claims puts on each member.

    A class is every claim one rule made with one value, answered on its handle, the Record's class
    target (``record/targets.class_target``). Its members follow one definition
    (``review/claim_classes.belongs``: outside installed programs, no other defeater fired), which
    the pass asks of each claim. The defeater is fired on each member's claim that can be retracted.
    It names the rule that reads the answers and cites the decision's event. The claim is then
    published as conflicting and never removed. A file not among those the answer was given about
    joined the class later, and its defeater says so. The defeater applies only where that rule is
    in force: with no rule reading the answers, no answer is read. Each defeater is built once per
    pass.
    """

    def __init__(self, answers: AnswersRead | None, reader: Rule | None) -> None:
        self._refused = (
            answers.refused_claim_classes() if answers is not None and reader is not None else {}
        )
        self._reader = reader
        self._reason = render_rule(reader) if self._refused and reader is not None else ""
        self._held: dict[tuple[str, str, bool], tuple[Defeater, ...]] = {}

    @property
    def readers(self) -> frozenset[str]:
        """The rule a person's answer on a class is recorded as, by identifier."""
        return frozenset({self._reader.rule_id}) if self._reader is not None else frozenset()

    def refused(self, rule: Rule, value: str | None) -> bool:
        """Whether a person answered that ``rule`` read the class of ``value`` wrongly."""
        if not self._refused or value is None or rule.tier not in _RETRACTABLE:
            return False
        return class_target(rule.rule_id, value) in self._refused

    def on(self, rule: Rule, value: str | None, subject: str = "") -> tuple[Defeater, ...]:
        """The defeater on one member's claim of ``rule`` concluding ``value``, or none."""
        if not self.refused(rule, value) or value is None or self._reader is None:
            return ()
        answer = self._refused[class_target(rule.rule_id, value)]
        joined = answer.members is not None and subject not in answer.members
        key = (rule.rule_id, value, joined)
        if key not in self._held:
            self._held[key] = (
                Defeater(
                    reason=f"{self._reason} {JOINED_AFTER}" if joined else self._reason,
                    rule_id=self._reader.rule_id,
                    rule_version=self._reader.version,
                    observations=(answer.event_id,),
                    fired=True,
                ),
            )
        return self._held[key]
