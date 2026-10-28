"""Turning what a rule came to into what a run publishes: a claim, its defeaters, or a failure.

One place builds every claim a rule licenses. A test holds that this module and the census are the
only two places a ``Claim`` is constructed, on the precedent of the test holding that only the
loader constructs a ``Rule``. One place lets the engine keep three promises that a claim makes
about its rule, and not merely hope for them:

- Its tier is exactly the rule's tier. A rule cannot emit a claim at a stronger tier than it
  declares. Here there is no argument through which a caller could ask for another tier.
- It names what would retract it, where its tier says it can be retracted. What it names are rules
  that a person can open and read. A rule declaring no defeater licenses no claim, and no reason
  is invented to satisfy the record.
- Its inputs are the records it rests on, so any conclusion walks back to the bytes mechanically.

A rule that could not decide publishes a failure instead, which populates the interpretation class.
A flagging rule publishes a finding, which is built here too and is never a claim.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, claim_id, finding_id, rule_failure_id
from ingence.core.schema import Claim, Defeater, FailureClass, Finding, FindingSubject, RuleFailure
from ingence.core.tiers import INFERENCE_TIER_PROPERTIES, ResolutionStatus
from ingence.rulebook.render import render_rule

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from ingence.core.schema import Rule
    from ingence.infer.evaluate import Outcome
    from ingence.rulebook.registry import Refusal

__all__ = [
    "SharedDefeaters",
    "claim_for",
    "claim_predicate",
    "constraint_defeater",
    "defeater_for",
    "failure_for",
    "finding_for",
    "truncation_claim_for",
    "why_not_claim_ready",
]


def claim_predicate(rule: Rule) -> str:
    """Return what a claim licensed by this rule asserts, as the claim's predicate.

    The predicate is one string for every claim of the rule. It is interned, so a pass holding a
    claim per entry per rule holds the predicate once per rule and not once per claim.

    The predicate is the effect and the rule's identifier. It uses the rule and not an axis,
    because nothing in a rule declares the axis its conclusion is on, and a name invented here
    would be a second vocabulary nobody declared. A consumer wanting "the stage of this file"
    matches the values, which are sense names, and the rule stays exactly attributable.
    """
    return sys.intern(f"{rule.effect.value}.{rule.rule_id}")


def why_not_claim_ready(rule: Rule, in_force: Collection[str]) -> str | None:
    """Return why a rule may not license a claim, or None where it may.

    This reads ``InferenceTierProperties.requires_defeaters``, which declares that a defeasible or
    abductive claim must name what would retract it. A rule at such a tier that names no defeater,
    or names one that is not in force, still runs, so its outcomes and its failures are recorded.
    It publishes no claim, and says why.
    """
    if not INFERENCE_TIER_PROPERTIES[rule.tier].requires_defeaters:
        return None
    if not rule.defeated_by:
        return f"a {rule.tier.value} rule that names nothing which would defeat its claims"
    missing = sorted(set(rule.defeated_by) - set(in_force))
    if missing:
        return f"names defeaters that are not in force: {', '.join(missing)}"
    return None


def defeater_for(defeater: Rule, outcome: Outcome | None) -> Defeater:
    """Return what one defeating rule says about one subject, fired or not.

    The defeater is fired where the defeating rule concluded on the same subject, with the records
    it concluded from as the observations that constitute the defeat. It is not fired where the
    rule abstained, and not fired where the rule could not be evaluated at all. That is the
    defeater recorded as what *would* retract the claim. The reason is the defeating rule's own
    sentence, rendered, so there is no second copy of it to drift.
    """
    fired = outcome is not None and outcome.concluded
    return Defeater(
        reason=render_rule(defeater),
        rule_id=defeater.rule_id,
        rule_version=defeater.version,
        observations=outcome.inputs if fired and outcome is not None else (),
        fired=fired,
    )


class SharedDefeaters:
    """What each defeating rule says about each subject, rendered once per pass.

    A defeater's reason is its rule's sentence, and :func:`defeater_for` renders it afresh each
    time. Without this class, a pass would hold one copy of a sentence several hundred characters
    long for every claim naming the rule. A defeater that did not fire is the same record for every
    subject, so it is built once per rule and shared (a record is frozen). A fired one keeps its own
    records and shares only the sentence. The result is equal, field for field, to what
    :func:`defeater_for` returns, so nothing published moves.

    The class is keyed by rule identifier, which is unique among the rules in force, so one instance
    serves one pass and is dropped with it.
    """

    def __init__(self) -> None:
        self._unfired: dict[str, Defeater] = {}
        self._all_unfired: dict[str, tuple[Defeater, ...]] = {}

    def defeater(self, defeater: Rule, outcome: Outcome | None) -> Defeater:
        """Return what one defeating rule says about one subject, fired or not."""
        unfired = self._unfired.get(defeater.rule_id)
        if unfired is None:
            unfired = defeater_for(defeater, None)
            self._unfired[defeater.rule_id] = unfired
        if outcome is None or not outcome.concluded:
            return unfired
        return Defeater(
            reason=unfired.reason,
            rule_id=defeater.rule_id,
            rule_version=defeater.version,
            observations=outcome.inputs,
            fired=True,
        )

    def of(self, rule: Rule, defeaters: tuple[Defeater, ...]) -> tuple[Defeater, ...]:
        """Return one rule's defeaters on one subject, the same tuple wherever none fired."""
        if any(held.fired for held in defeaters):
            return defeaters
        shared = self._all_unfired.get(rule.rule_id)
        if shared is None or shared != defeaters:
            self._all_unfired[rule.rule_id] = defeaters
            return defeaters
        return shared


def claim_for(
    rule: Rule,
    outcome: Outcome,
    value: str,
    *,
    run_id: str,
    defeaters: Sequence[Defeater],
) -> Claim:
    """Return the claim one concluded value licenses.

    A claim whose defeater fired is still published, as conflicting. Evidence raises a claim's
    tier, leaves it unchanged, or introduces a defeater, which is a visible event and not a quiet
    erosion. Deleting the claim would make the retraction invisible, and the conflict between the
    two readings is the finding.
    """
    if not outcome.concluded or value not in outcome.values:
        message = f"rule {rule.rule_id!r} did not conclude {value!r} on {outcome.subject!r}"
        raise ValueError(message)
    predicate = claim_predicate(rule)
    fired = any(defeater.fired for defeater in defeaters)
    return Claim(
        claim_id=claim_id(
            subject=outcome.subject,
            predicate=predicate,
            object_reference=value,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            scope=rule.scope,
        ),
        run_id=run_id,
        subject=outcome.subject,
        predicate=predicate,
        object_reference=value,
        inference_tier=rule.tier,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        scope=rule.scope,
        inputs=outcome.inputs,
        defeaters=tuple(defeaters),
        resolution_status=(
            ResolutionStatus.EXAMINED_CONFLICTING if fired else ResolutionStatus.RESOLVED
        ),
    )


def failure_for(rule: Rule, outcome: Outcome, *, run_id: str) -> RuleFailure:
    """Return the record of a rule unable to decide on one subject.

    Refuses an outcome that is not an interpretation failure, because the two other
    classes belong to the scan layer and the record refuses them in turn.
    """
    if outcome.failure_class is not FailureClass.INTERPRETATION:
        message = f"rule {rule.rule_id!r} did not fail to decide on {outcome.subject!r}"
        raise ValueError(message)
    return RuleFailure(
        failure_id=rule_failure_id(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            subject=outcome.subject,
            scope=rule.scope,
        ),
        run_id=run_id,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        scope=rule.scope,
        subject=outcome.subject,
        failure_class=outcome.failure_class,
        inputs=outcome.inputs,
        reason=outcome.reason,
    )


def finding_for(
    rule: Rule,
    outcome: Outcome,
    *,
    run_id: str,
    subject_kind: FindingSubject = FindingSubject.ENTRY,
    defeaters: Sequence[Defeater] = (),
) -> Finding:
    """Return what one flagging rule raised about one subject.

    The result is a finding and never a claim. A flagging rule concludes nothing: it names what
    drew attention to a subject and cites the records that did, and what that means is a person's
    to say. So a finding carries no tier and no status, and a rule raises one without naming what
    would retract it, as a proposing rule licenses a candidate without one. Where the rule does
    name defeaters, they are recorded on it, fired or not, and the finding is raised all the same.

    The caller passes the subject kind and the function does not infer it from the subject string,
    because an entry identifier and a scope token are both strings and reading one for the other
    would file a fact about a region under a file. The phase knows which it evaluated and says so.
    """
    if not outcome.concluded:
        message = f"rule {rule.rule_id!r} raised nothing on {outcome.subject!r}"
        raise ValueError(message)
    return Finding(
        finding_id=finding_id(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            subject=outcome.subject,
            scope=rule.scope,
        ),
        run_id=run_id,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        scope=rule.scope,
        subject=outcome.subject,
        raised=outcome.values,
        subject_kind=subject_kind,
        inputs=outcome.inputs,
        reason=outcome.reason,
        defeaters=tuple(defeaters),
    )


def constraint_defeater(constraint: Rule, refusal: Refusal | None) -> Defeater:
    """Return what one constraint says about one candidate, fired or not.

    The constraint is fired where it refused the pair, with the records it read as the observations
    that constitute the refusal. It is not fired where it had nothing to refuse or could not be
    evaluated, which records it as what would refuse the candidate. The reason is the constraint's
    own sentence, rendered, as a defeating rule's is.
    """
    return Defeater(
        reason=render_rule(constraint),
        rule_id=constraint.rule_id,
        rule_version=constraint.version,
        observations=refusal.records if refusal is not None else (),
        fired=refusal is not None,
    )


def truncation_claim_for(
    rule: Rule,
    *,
    subject: str,
    within: str,
    cut: int,
    inputs: Sequence[str],
    run_id: str,
) -> Claim:
    """Return the claim that a bound cut one subject's candidates within one index.

    A bounded answer and a complete one are different answers, so a cut is published and never
    silent. Its object names the index and how many counterparts were cut. It cites the records
    that placed the subject in the index, so the claim walks back to why the subject had so many
    counterparts to begin with.
    """
    predicate = claim_predicate(rule)
    value = canonical_json({"cut": cut, "within": within})
    return Claim(
        claim_id=claim_id(
            subject=subject,
            predicate=predicate,
            object_reference=value,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            scope=rule.scope,
        ),
        run_id=run_id,
        subject=subject,
        predicate=predicate,
        object_reference=value,
        inference_tier=rule.tier,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        scope=rule.scope,
        inputs=tuple(sorted(set(inputs))),
    )
