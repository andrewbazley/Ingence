"""Evaluating one rule on one subject, and saying what that came to.

A predicate returns a value or abstains. What the pass records is richer than that, so that a
starved rule and a wrong rule do not look alike. Every evaluation comes to one of these:

========================================  ======================  ==============
What happened                             Status                  Failure class
========================================  ======================  ==============
concluded                                 resolved                none
abstained, with nothing there to read     examined_no_evidence    none
abstained, the input unreadable           examined_no_evidence    comprehension
abstained, the input never produced       unexamined              none
the input points more than one way        examined_conflicting    interpretation
========================================  ======================  ==============

A predicate that cannot be evaluated at all raises
:class:`~ingence.rulebook.registry.PredicateNotEvaluableError`, which is a fact about a rule and
not about a subject. The caller records it once per rule per run, not this module.

Only the predicate knows which of its inputs mattered, so the explanation of an abstention comes
from the predicate, through an optional ``explain_abstention``, read with a default. One case needs
no predicate to say it. A subject reading bindings whose entry has components and no bindings at
all was never looked at by the binder, because the binder writes a binding row for every component
whenever it runs, including when it declines.

A grouping rule's verdict is a class key, read by :func:`key_on_entry`. Its abstentions come to the
same outcomes by the same route.

A proposing rule's verdict is its support, a constraint's is a refusal, and a bound's is a count.
Each is read by a function of its own that refuses any other return. A predicate that answered with
a score where a refusal was due fails loudly and is not weighed.

A flagging rule's verdict is what it raised. :func:`raise_on_entry` reads it with the records it
rests on, which must be the entry's own. :func:`raise_on_scope` reads it where the fact is about a
region of the pool and not about any one file. Both refuse a finding that cites a record its
subject does not carry: one entry's own for an entry, and any of the scope's entries' own for a
scope.

A classification that no handed record carries comes with its records. The predicate returns
:class:`~ingence.rulebook.registry.Concluded`, which :func:`evaluate_on_entry` reads, and those
records must be the entry's own as well.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.schema import FailureClass, SupportComponent
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import (
    PREDICATES,
    Abstention,
    ClassKey,
    Concluded,
    DeclaredParameters,
    Raised,
    Reads,
    Refusal,
    parameters_of,
    reads_no_name,
)
from ingence.rulebook.render import value_in_force

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from ingence.core.schema import Rule
    from ingence.infer.subjects import CandidateSet, EntryRecords, PairRecords, ScopeRecords
    from ingence.rulebook.registry import Predicate

__all__ = [
    "Outcome",
    "abstention_of",
    "cut_for_subject",
    "evaluate_on_entry",
    "key_on_entry",
    "parameters_in_force",
    "raise_on_entry",
    "raise_on_scope",
    "refusal_of_pair",
    "support_for_pair",
    "verdict_values",
]


@dataclass(frozen=True, slots=True)
class Outcome:
    """What evaluating one rule on one entry came to."""

    rule_id: str
    subject: str
    status: ResolutionStatus
    #: The concluded values, in canonical order. Empty unless the status is resolved.
    values: tuple[str, ...] = ()
    failure_class: FailureClass | None = None
    #: The records the conclusion or the abstention rests on.
    inputs: tuple[str, ...] = ()
    reason: str = ""

    @property
    def concluded(self) -> bool:
        """Whether the rule reached a verdict on this subject."""
        return self.status is ResolutionStatus.RESOLVED


def parameters_in_force(rule: Rule) -> DeclaredParameters:
    """Return a rule's parameters at the values in force, decoded from storage.

    The read goes through :func:`~ingence.rulebook.render.value_in_force`, so the value a predicate
    is handed and the value the rendered sentence shows are read by one function and cannot
    disagree. A parameter is readable only as the predicate declares it. A predicate reaching for a
    parameter its registration does not list fails on the read and does not take a default nobody
    can see.
    """
    return DeclaredParameters(
        {specification.name: value_in_force(specification) for specification in rule.parameters},
        predicate=rule.predicate,
        declared=parameters_of(PREDICATES.declaration(rule.predicate)),
    )


def verdict_values(verdict: object, *, rule_id: str) -> tuple[str, ...]:
    """Return what a verdict concluded: nothing, one value, or several.

    Refuses anything else, because a predicate returning a number or a flag where a value was
    expected is a defect in the predicate, and reading ``True`` as the value ``"True"`` would
    publish it.
    """
    if verdict is None:
        return ()
    if isinstance(verdict, str):
        return (verdict,) if verdict else ()
    if isinstance(verdict, (tuple, list, set, frozenset)) and all(
        isinstance(value, str) for value in verdict
    ):
        return tuple(sorted({value for value in verdict if value}))
    message = (
        f"rule {rule_id!r} was handed a verdict of type {type(verdict).__name__}; a "
        f"predicate returns a value, several values, or abstains with None"
    )
    raise TypeError(message)


def abstention_of(
    predicate: Predicate, subject: object, parameters: Mapping[str, object]
) -> Abstention:
    """Return why a predicate reached no verdict, in the predicate's own words where it has them.

    A predicate with nothing to say about its abstentions abstained on nothing. That is the honest
    default and not a guess about which input was missing.
    """
    explain = getattr(predicate, "explain_abstention", None)
    if explain is None:
        return Abstention()
    explanation = explain(subject, parameters)
    if not isinstance(explanation, Abstention):
        message = (
            f"predicate {predicate.name!r} explained an abstention with "
            f"{type(explanation).__name__} rather than an Abstention"
        )
        raise TypeError(message)
    return explanation


def evaluate_on_entry(
    rule: Rule, predicate: Predicate, records: EntryRecords, *, reads: Reads
) -> Outcome:
    """Evaluate one rule on one entry's records and say what that came to.

    Lets :class:`~ingence.rulebook.registry.PredicateNotEvaluableError` through, because it is a
    fact about the rule, and the caller records it once and not once per entry. A
    :class:`~ingence.rulebook.registry.Concluded` verdict carries its own records, and one that is
    not the entry's own is refused.
    """
    subject = records.narrowed(reads)
    parameters = parameters_in_force(rule)
    verdict = predicate.evaluate(subject, parameters)
    if isinstance(verdict, Concluded):
        _refuse_records_not_its_own(
            records, verdict.inputs, said=f"classifying rule {rule.rule_id!r} concluded"
        )
        return Outcome(
            rule_id=rule.rule_id,
            subject=records.entry.entry_id,
            status=ResolutionStatus.RESOLVED,
            values=tuple(sorted(set(verdict.values))),
            inputs=tuple(sorted(set(verdict.inputs))),
        )
    values = verdict_values(verdict, rule_id=rule.rule_id)
    if values:
        return Outcome(
            rule_id=rule.rule_id,
            subject=records.entry.entry_id,
            status=ResolutionStatus.RESOLVED,
            values=values,
            inputs=records.carrying(values, reads),
        )
    return _abstained(rule, predicate, records, subject, parameters, reads=reads)


def key_on_entry(
    rule: Rule, predicate: Predicate, records: EntryRecords, *, reads: Reads
) -> Outcome:
    """Evaluate one grouping rule on one entry: the class it places the entry in, or why none.

    The outcome's one value is the class key, and its inputs are the records the predicate says the
    key was built from. An abstention comes to the same outcomes a classification's does, by the
    same route.
    """
    subject = records.narrowed(reads)
    parameters = parameters_in_force(rule)
    verdict = predicate.evaluate(subject, parameters)
    if verdict is None:
        return _abstained(rule, predicate, records, subject, parameters, reads=reads)
    if not isinstance(verdict, ClassKey):
        message = (
            f"grouping rule {rule.rule_id!r} was handed a verdict of type "
            f"{type(verdict).__name__}; a grouping predicate returns a class key or "
            f"abstains with None"
        )
        raise TypeError(message)
    return Outcome(
        rule_id=rule.rule_id,
        subject=records.entry.entry_id,
        status=ResolutionStatus.RESOLVED,
        values=(verdict.key,),
        inputs=tuple(sorted(set(verdict.inputs))),
    )


def raise_on_entry(
    rule: Rule, predicate: Predicate, records: EntryRecords, *, reads: Reads
) -> Outcome:
    """Evaluate one flagging rule on one entry: what it raised, or why nothing.

    A flagging predicate answers with :class:`~ingence.rulebook.registry.Raised`, which carries the
    records the finding rests on, or with plain values where it also serves as a defeater and is
    read as a classification is. Those records must be the entry's own, and any other is refused. A
    finding about one entry citing another's records would carry a fact across entries, which only
    ``identical`` licenses. The INV-7 check in ``core/invariants.py`` (substitutivity is confined to
    ``identical``) reads claims and not findings.
    """
    subject = records.narrowed(reads)
    parameters = parameters_in_force(rule)
    verdict = predicate.evaluate(subject, parameters)
    if not isinstance(verdict, Raised):
        values = verdict_values(verdict, rule_id=rule.rule_id)
        if not values:
            return _abstained(rule, predicate, records, subject, parameters, reads=reads)
        return Outcome(
            rule_id=rule.rule_id,
            subject=records.entry.entry_id,
            status=ResolutionStatus.RESOLVED,
            values=values,
            inputs=records.carrying(values, reads),
        )
    _refuse_records_not_its_own(
        records, verdict.inputs, said=f"flagging rule {rule.rule_id!r} raised a finding"
    )
    return Outcome(
        rule_id=rule.rule_id,
        subject=records.entry.entry_id,
        status=ResolutionStatus.RESOLVED,
        values=tuple(sorted(set(verdict.values))),
        inputs=tuple(sorted(set(verdict.inputs))),
        reason=verdict.reason,
    )


def raise_on_scope(rule: Rule, predicate: Predicate, scope: ScopeRecords) -> Outcome:
    """Evaluate one flagging rule on one scope: what it raised about the region, or why nothing.

    The subject is the scope's own token, so the finding says what region it is about, and two runs
    over an unchanged pool raise the same finding identifier.

    The records it may cite are the scope's entries' own. A scope is a wider subject than an entry,
    and the check is the same one widened. A finding about one region resting on a record from
    another would carry a fact across regions, which is what :func:`raise_on_entry` refuses one
    entry away.

    A scope is never narrowed. A predicate declaring ``Reads.SCOPE`` asked for the region and its
    entries, and there is no smaller part of it to hand over.
    """
    parameters = parameters_in_force(rule)
    verdict = predicate.evaluate(scope, parameters)
    if not isinstance(verdict, Raised):
        values = verdict_values(verdict, rule_id=rule.rule_id)
        if not values:
            abstention = abstention_of(predicate, scope, parameters)
            return Outcome(
                rule_id=rule.rule_id,
                subject=scope.token,
                status=abstention.status,
                failure_class=abstention.failure_class,
                inputs=abstention.inputs,
                reason=abstention.reason,
            )
        message = (
            f"flagging rule {rule.rule_id!r} raised {len(values)} values about scope "
            f"{scope.token!r} without the records they rest on; a scope has no single "
            f"record a value can be matched against, so a scope flag answers with Raised"
        )
        raise TypeError(message)
    foreign = sorted(set(verdict.inputs) - scope.owns())
    if foreign:
        message = (
            f"flagging rule {rule.rule_id!r} raised a finding on {scope.token!r} citing "
            f"records that no entry in that scope carries: {', '.join(foreign)}"
        )
        raise ValueError(message)
    return Outcome(
        rule_id=rule.rule_id,
        subject=scope.token,
        status=ResolutionStatus.RESOLVED,
        values=tuple(sorted(set(verdict.values))),
        inputs=tuple(sorted(set(verdict.inputs))),
        reason=verdict.reason,
    )


def support_for_pair(
    rule: Rule, predicate: Predicate, pair: PairRecords
) -> tuple[SupportComponent, ...]:
    """Evaluate a proposing rule on one pair: its support, or nothing where it abstained.

    Refuses any other return, because support is a vector of named, family-tagged
    components, and a predicate returning a number would be a score by another name.
    """
    verdict = predicate.evaluate(pair, parameters_in_force(rule))
    if verdict is None:
        return ()
    if (
        isinstance(verdict, tuple)
        and verdict
        and all(isinstance(component, SupportComponent) for component in verdict)
    ):
        return verdict
    message = (
        f"proposing rule {rule.rule_id!r} was handed a verdict of type "
        f"{type(verdict).__name__}; a proposing predicate returns its support or abstains "
        f"with None"
    )
    raise TypeError(message)


def refusal_of_pair(rule: Rule, predicate: Predicate, pair: PairRecords) -> Refusal | None:
    """Evaluate a constraint on one pair: its refusal, or None where it has nothing to refuse."""
    verdict = predicate.evaluate(pair, parameters_in_force(rule))
    if verdict is None:
        return None
    if isinstance(verdict, Refusal):
        return verdict
    message = (
        f"constraint {rule.rule_id!r} was handed a verdict of type {type(verdict).__name__}; "
        f"a constraint refuses a pair or returns None, and is never a penalty"
    )
    raise TypeError(message)


def cut_for_subject(rule: Rule, predicate: Predicate, candidates: CandidateSet) -> int:
    """Evaluate a bound on one subject's candidates: how many counterparts to cut, or zero."""
    verdict = predicate.evaluate(candidates, parameters_in_force(rule))
    if isinstance(verdict, bool) or not isinstance(verdict, int) or verdict < 0:
        message = (
            f"bound {rule.rule_id!r} was handed a verdict of {verdict!r}; a bound returns how "
            f"many counterparts to cut, and zero where none"
        )
        raise TypeError(message)
    return verdict


def _refuse_records_not_its_own(
    records: EntryRecords, inputs: Collection[str], *, said: str
) -> None:
    """Refuse a verdict about one entry that cites a record not the entry's own.

    A verdict carrying its own records names them and is checked here, because a conclusion
    or a finding about one entry resting on another's records would carry a fact across
    entries, which only ``identical`` licenses.
    """
    own = {
        records.entry.entry_id,
        *(component.component_id for component in records.components),
        *(binding.binding_id for binding in records.bindings),
        *(observation.observation_id for observation in records.observations),
        *(claim.claim_id for claim in records.claims),
    }
    foreign = sorted(set(inputs) - own)
    if foreign:
        message = (
            f"{said} on {records.entry.entry_id!r} citing records that are not its own: "
            f"{', '.join(foreign)}"
        )
        raise ValueError(message)


def _reads_names(rule: Rule, reads: Reads) -> bool:
    """Whether a rule's predicate reads an entry's name.

    Components and bindings are a name's. Whole records are too, unless the predicate declares that
    it reads no name (``reads_no_name``, on its registration). The result is read once per
    predicate, because the abstention path is the hot one.
    """
    return reads is not Reads.RECORDS or not _declares_no_name(rule.predicate)


@functools.cache
def _declares_no_name(predicate: str) -> bool:
    """Whether a predicate's registration declares it reads no name."""
    try:
        return reads_no_name(PREDICATES.declaration(predicate))
    except KeyError:
        return False


def _abstained(
    rule: Rule,
    predicate: Predicate,
    records: EntryRecords,
    subject: object,
    parameters: Mapping[str, object],
    *,
    reads: Reads,
) -> Outcome:
    """Say why a rule reached no verdict on one entry.

    Unexamined where the input was never produced, which the pass can tell without
    the predicate; otherwise the predicate's own account.
    """
    entry_id = records.entry.entry_id
    names = _reads_names(rule, reads)
    if (
        names
        and reads in {Reads.COMPONENTS, Reads.BINDINGS, Reads.RECORDS}
        and not records.components
        and records.entry.entry_kind == "derived"
    ):
        # A member recovered from a container is promoted with a name and never
        # tokenised, so nothing about its name was looked at. Reading that as no
        # evidence would count every archive member against every name rule.
        return Outcome(
            rule_id=rule.rule_id,
            subject=entry_id,
            status=ResolutionStatus.UNEXAMINED,
            reason="the name of a container member is not tokenised",
        )
    if (
        names
        and reads in {Reads.BINDINGS, Reads.RECORDS}
        and records.components
        and not records.bindings
    ):
        return Outcome(
            rule_id=rule.rule_id,
            subject=entry_id,
            status=ResolutionStatus.UNEXAMINED,
            reason="the binder did not run for this entry",
        )
    abstention = abstention_of(predicate, subject, parameters)
    return Outcome(
        rule_id=rule.rule_id,
        subject=entry_id,
        status=abstention.status,
        failure_class=abstention.failure_class,
        inputs=abstention.inputs,
        reason=abstention.reason,
    )
