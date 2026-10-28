"""Predicates that constrain the decoder by expressing impossibility.

A constraint is not a large negative weight. This is the reason the effect class exists. A score
cannot express impossibility, because a large enough pile of weak support always outvotes a
finite penalty. A contradiction encoded as a penalty is a contradiction that a verbose enough
filename can buy its way out of. A constraint removes the option instead.

Impossibility encoded as a number fails in two common ways. In the first, a conflicting
biological component gets the weight ``-1.0`` and the sum is then clamped into the unit interval.
A candidate that carries a conflict and enough weak support then scores zero and is not refused,
and zero is a number that a threshold somewhere can be moved below. In the second, a fixed penalty
is subtracted from a completeness figure for a folder whose name signals a provenance concern.
In both cases the thing being said is "this cannot be right" and the thing being stored is "this
is worth less". Those differ exactly when it matters.

So every constraint here is a refusal. A refused candidate is recorded with the rule that refused
it. That makes the refusal attributable: a user who asks why two files were not linked gets a
named, versioned sentence and not a score that they cannot interpret.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import Claim, DictionaryBinding, RuleEffect
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import (
    CO_MENTION_INDEX,
    Predicate,
    PredicateNotEvaluableError,
    Reads,
    Refusal,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "CandidatesAreBoundedPerSubject",
    "ComponentsDoNotConflict",
    "DerivationIsAcyclic",
    "EndpointHoldsAnEligibleRole",
    "EndpointIsNotOfAnExcludedClass",
    "RelationFollowsTheClock",
    "SubstitutionRestsOnContent",
    "SuccessionStaysWithinOneSeries",
]


class DerivationIsAcyclic:
    """Nothing is derived from itself, however long the chain.

    :mod:`ingence.core.invariants` already enforces this as a publication check, and the rule is
    declared here as well on purpose. The invariant stops an incoherent graph from reaching a
    reader. The rule makes the refusal attributable, so a user who asks why two candidates were
    both rejected gets a named, versioned rule and not an engine that silently dropped them.

    This is not the same property implemented twice. The invariant checks a finished graph, and
    this rule constrains a decoder that chooses among candidates. They run at different moments on
    different objects. If a decoder never proposed a cycle, the invariant would pass vacuously and
    not redundantly.

    Declared and not evaluable. It constrains a decoder, and none is built.
    """

    name = "derivation_is_acyclic"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "derivation_is_acyclic constrains a decoder choosing among candidate "
            "edges, and no decoder is built"
        )
        raise PredicateNotEvaluableError(message)


class RelationFollowsTheClock:
    """A source may not postdate what is derived from it.

    A filesystem modification time moves when a file is copied, touched or re-saved. An engine
    that reads only that time would refuse real derivations by the thousand on any tree that
    somebody has archived and restored. What makes the constraint safe is the copy-robustness
    ladder. Tiers one to three travel with the bytes, and tiers four and five describe this copy on
    this disk. So the rule requires that both endpoints carry a clock above a stated rung before it
    refuses anything at all.

    The rule abstains and does not refuse where either clock is too weak. This is the whole of its
    safety. A constraint that fires on missing evidence is worse than no constraint, because it
    converts an absence into an impossibility, and absence is a state of its own.

    The tolerance is a parameter. Two clocks at the same rung can legitimately differ by seconds.
    A pipeline can write an output while it still holds the input open. A filesystem can have
    one-second resolution. A document's metadata can be stamped before its last page is rendered.
    A tolerance admits those cases and still refuses a source written a week after its output.

    Declared and not evaluable. It constrains a candidate pair, and the proposing phase records it
    on every candidate of its primitive. What is missing is the comparison itself: reading both
    endpoints' clocks and the tiers they sit at.
    """

    name = "relation_follows_the_clock"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "relation_follows_the_clock constrains a candidate pair against both "
            "endpoints' clocks, and comparing the two clocks is not implemented"
        )
        raise PredicateNotEvaluableError(message)


class ComponentsDoNotConflict:
    """Two entries disagreeing on a component that cannot vary are not related.

    Two files can name different values of a component that identifies what was measured and not
    how it was processed. Then no amount of shared vocabulary, adjacency or naming similarity makes
    one derived from the other. A file about one subject is not a projection of a file about
    another, however alike the two names are.

    Which components cannot vary is a parameter. The engine cannot know from outside which axes of
    a corpus identify the subject and which are incidental. A guess would make the rule wrong in
    the second laboratory that uses it. So the rule declares the theme kinds that it treats as
    hard, a user changes the list, and the change is visible in the rendered sentence.

    Absence is not disagreement. If one file names a value and the other names none, that is
    silence and not conflict. Refusing on silence would make the rule fire hardest on the sparsely
    named files that it understands least.

    The rule is evaluable on a pair. A term is the sense that a name's word resolved to on an
    identity-bearing axis. So a word that the Dictionary could not settle names nothing on the
    axis, and silence stays silence.
    """

    name = "components_do_not_conflict"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return a refusal where the two ends name different terms on an identity-bearing axis."""
        kinds = _strings(parameters.get("identity_bearing_kinds"))
        source = _terms(getattr(subject, "source", None), kinds)
        target = _terms(getattr(subject, "target", None), kinds)
        conflicts = sorted(
            kind
            for kind in kinds
            if kind in source and kind in target and set(source[kind]) != set(target[kind])
        )
        if not conflicts:
            return None
        described = "; ".join(
            f"{kind} {', '.join(sorted(source[kind]))} against {', '.join(sorted(target[kind]))}"
            for kind in conflicts
        )
        records = sorted(
            {
                identifier
                for kind in conflicts
                for terms in (source[kind], target[kind])
                for identifiers in terms.values()
                for identifier in identifiers
            }
        )
        return Refusal(
            reason=f"the two ends name different terms: {described}", records=tuple(records)
        )


class EndpointHoldsAnEligibleRole:
    """An edge's endpoints must be the kinds of thing that edge relates.

    A figure is not derived from a license file. A raw acquisition is not derived from a report.
    Stating which file roles may stand at each end of a relation turns candidate generation from a
    quadratic sweep into a bounded one. Candidate generation must never be all-pairs.

    This is a constraint and not a classifier. It does not decide what a file is. It says which
    decisions are admissible, given what something else already decided. A file whose role nothing
    established is ineligible and not wrong. The remedy is a classifying rule or a vocabulary
    entry: the fix is coverage and not a looser threshold here.

    The rule is evaluable on a pair. An end's roles are the values of the resolved classification
    claims about it, so a stage and a file role both count, and a role that a defeater retracted
    does not.

    Version 2 treats a script's own calls as roles too, where the rule says so
    (``script_calls_are_roles``). A pair drawn from one script's mentions whose calls read the
    source is the script's input. A pair whose calls write the target is its output. The script
    said which kind of thing stands at each end, which is what the roles are for. The reading
    comes from the pair's resolutions, whose direction the script's calls recorded. With the
    parameter off, the rule reads as version 1 does.
    """

    name = "endpoint_holds_an_eligible_role"
    version = "2"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return a refusal naming each end that holds no eligible role, or None."""
        source = _classified(getattr(subject, "source", None))
        target = _classified(getattr(subject, "target", None))
        called = _script_calls(subject) if parameters.get("script_calls_are_roles") else set()
        ends = {
            end: getattr(getattr(getattr(subject, end, None), "entry", None), "entry_id", None)
            for end in ("source", "target")
        }
        refused = []
        if not set(source) & _strings(parameters.get("source_roles")) and (
            (ends["source"], "read") not in called
        ):
            refused.append("the source holds none of the source roles")
        if not set(target) & _strings(parameters.get("target_roles")) and (
            (ends["target"], "written") not in called
        ):
            refused.append("the target holds none of the target roles")
        if not refused:
            return None
        return Refusal(
            reason="; ".join(refused),
            records=tuple(sorted({*source.values(), *target.values()})),
        )


class EndpointIsNotOfAnExcludedClass:
    """An edge's endpoints may not be of a class the relation never joins.

    The engine finds a derivation from its own evidence before it assigns stages.
    :class:`EndpointHoldsAnEligibleRole` admits a derivation only where each end holds a stage or
    a role from a list. So it refuses a file that states neither as either end of any derivation,
    and the stage decides which pairs stand. This rule reads no stage. It refuses only an end that
    is concluded to be of a class that the relation never joins, ``excluded_classes``. An example
    is a program, which performs a step and is neither what the step read nor what it made. Raw
    data is not derived from a Python script, and the script is not derived from raw data.

    The rule is evaluable on a pair. An end's classes are the values of its resolved
    classification claims, so a class that a defeater retracted is none. The refusal cites the
    claims that it read.
    """

    name = "endpoint_is_not_of_an_excluded_class"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return a refusal naming each end of an excluded class, or None."""
        excluded = _strings(parameters.get("excluded_classes"))
        refused: list[str] = []
        records: list[str] = []
        for end in ("source", "target"):
            held = {
                value: claim
                for value, claim in _classified(getattr(subject, end, None)).items()
                if value in excluded
            }
            if held:
                refused.append(f"the {end} is {', '.join(sorted(held))}")
                records.extend(held.values())
        if not refused:
            return None
        return Refusal(
            reason="; ".join(refused) + ", which is never an end of this relation",
            records=tuple(sorted(records)),
        )


class CandidatesAreBoundedPerSubject:
    """No subject generates more than a stated number of candidates.

    This is the backstop behind every bounded index, and it exists because the indexes can
    eventually be wrong. Candidates are generated by looking up a shared key. A key that turns out
    to be common on some real corpus produces a bucket of ten thousand files and a quadratic sweep
    inside it. A cap turns that into a truncated answer and not a run that never finishes.

    Truncation is recorded and never silent. A subject whose candidates were cut carries that fact,
    because a bounded result and a complete one are different answers. The record of the cut also
    makes the parameter tunable by evidence. A corpus where the cap fires constantly is a corpus
    whose indexes need narrowing, and that is a finding and not a setting to raise.

    Ranking before cutting is not a threshold. ``Candidate.tiebreak`` orders candidates within one
    decision tier, and it is annotated as never being an acceptance threshold. Using it to decide
    which of too many candidates to keep is ordering, and the cut is on count.

    The rule is evaluable on a subject's candidate set. It says how many counterparts to cut, zero
    where they are within the bound, and never which. The canonical order decides which survive.
    """

    name = "candidates_are_bounded_per_subject"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return how many of a subject's counterparts exceed the bound."""
        maximum = parameters.get("maximum_candidates")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            message = f"expected a positive integer bound, found {maximum!r}"
            raise TypeError(message)
        return max(0, len(getattr(subject, "counterparts", ())) - maximum)


def _strings(value: object) -> frozenset[str]:
    """Return a string-list parameter as a set, tolerating the single-string spelling."""
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, (list, tuple)):
        return frozenset(str(item) for item in value)
    return frozenset()


def _terms(records: object, kinds: frozenset[str]) -> dict[str, dict[str, list[str]]]:
    """Return the terms an end's name resolved to on each stated axis, with the bindings."""
    found: dict[str, dict[str, list[str]]] = {}
    for binding in getattr(records, "bindings", ()):
        if not isinstance(binding, DictionaryBinding) or binding.constraint is not None:
            continue
        kind, term = binding.theme_kind, binding.term
        if binding.resolution_status is not ResolutionStatus.RESOLVED or kind not in kinds:
            continue
        if kind is None or term is None:
            continue
        found.setdefault(kind, {}).setdefault(term, []).append(binding.binding_id)
    return found


def _script_calls(subject: object) -> set[tuple[str | None, str]]:
    """Each end a pair's describing script names by a call, with the call's direction."""
    if getattr(subject, "within", None) != CO_MENTION_INDEX:
        return set()
    describing = getattr(getattr(subject, "describing", None), "entry", None)
    script = getattr(describing, "entry_id", None)
    return {
        (resolution.target_id, resolution.mention.direction)
        for resolution in getattr(subject, "resolutions", ())
        if getattr(resolution.mention, "direction", "") and resolution.mention.entry_id == script
    }


def _classified(records: object) -> dict[str, str]:
    """Return what the classifying phase concluded about an end, each value with its claim."""
    found: dict[str, str] = {}
    for claim in sorted(getattr(records, "claims", ()), key=lambda claim: claim.claim_id):
        if (
            isinstance(claim, Claim)
            and claim.predicate.startswith(f"{RuleEffect.CLASSIFIES.value}.")
            and claim.resolution_status is ResolutionStatus.RESOLVED
            and claim.object_reference is not None
        ):
            found.setdefault(claim.object_reference, claim.claim_id)
    return found


class SubstitutionRestsOnContent:
    """Byte equality is asserted from bytes, and from nothing else.

    ``identical`` is the only substitutive primitive: it licenses transferring a fact from one
    endpoint to the other. Everything downstream that collapses duplicates, counts a pool's real
    size or treats one file as a stand-in for another rests on it. An ``identical`` edge asserted
    from agreement of name, size and timestamps would propagate a wrong substitution silently and
    everywhere.

    A schema that records metadata-identical entries with a field such as
    ``content_verified: False`` beside them keeps a correct record under the wrong label. The group
    is called a duplicate group, and the field that says it is not verified is one column that a
    consumer can forget to read. Here the two are different relations. Metadata agreement is
    ``equivalent`` in a stated respect, which groups and substitutes nothing. Byte equality is
    ``identical``, and this is the rule that refuses to let the first become the second.

    A digest of part of a file is not a digest of the file. The paired fingerprints that the reuse
    model computes are a cheap test for change and were built for that. Promoting one to an
    identity claim would assert equality from a sample.

    Declared and not evaluable. The full-content identity method exists, and the proposing phase
    records a pair constraint on every candidate of its primitive. What is missing is this
    predicate's reading of what a candidate's support rests on. No shipped rule proposes
    ``identical``, so none has been offered to it.
    """

    name = "substitution_rests_on_content"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "substitution_rests_on_content refuses an identical candidate supported by "
            "anything but a content digest, and reading a candidate's support is not "
            "implemented"
        )
        raise PredicateNotEvaluableError(message)


class SuccessionStaysWithinOneSeries:
    """Order holds inside a series and says nothing across two.

    ``succeeds`` is total within its series and undefined outside it. This property separates it
    from ``embeds`` in the primitive signature. Two files that carry an index are ordered only
    once something establishes that they index the same thing. ``rep2`` of one condition does not
    come after ``rep1`` of another, and the two indexes are not comparable at all.

    The rule prevents a chain of nonsense that looks like a timeline. An engine that orders every
    indexed file against every other produces a single long sequence through unrelated work.
    Because ``succeeds`` is transitive, the sequence then licenses statements about pairs at its
    two ends that nothing in the corpus supports. Requiring a stated series keeps the
    transitivity honest.

    Declared and not evaluable. The series is an equivalence class, and the proposing phase can
    draw a pair within one. What is missing is this predicate's check that both ends of a
    ``succeeds`` candidate were drawn from one series.
    """

    name = "succession_stays_within_one_series"
    version = "1"

    def evaluate(self, subject: object, parameters: object) -> object:
        message = (
            "succession_stays_within_one_series refuses an order across two series, and "
            "checking that a pair's ends share one series is not implemented"
        )
        raise PredicateNotEvaluableError(message)


@declare(
    "derivation_is_acyclic",
    version="1",
    summary="Refuse any candidate set closing a cycle in derives",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.CANDIDATES,
    parameters=("max_chain_length",),
)
def _build_derivation_is_acyclic() -> Predicate:
    return DerivationIsAcyclic()


@declare(
    "relation_follows_the_clock",
    version="1",
    summary="Refuse an edge whose source postdates its target on copy-robust clocks",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.PAIR,
    parameters=("minimum_clock_tier", "tolerance_seconds"),
)
def _build_relation_follows_the_clock() -> Predicate:
    return RelationFollowsTheClock()


@declare(
    "components_do_not_conflict",
    version="1",
    summary="Refuse an edge between entries disagreeing on an identity-bearing component",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.PAIR,
    parameters=("identity_bearing_kinds",),
)
def _build_components_do_not_conflict() -> Predicate:
    return ComponentsDoNotConflict()


@declare(
    "endpoint_holds_an_eligible_role",
    version="2",
    summary="Refuse an edge whose endpoints do not hold roles the relation admits",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.PAIR,
    parameters=("script_calls_are_roles", "source_roles", "target_roles"),
)
def _build_endpoint_holds_an_eligible_role() -> Predicate:
    return EndpointHoldsAnEligibleRole()


@declare(
    "endpoint_is_not_of_an_excluded_class",
    version="1",
    summary="Refuse an edge either of whose endpoints is of a class the relation never joins",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.PAIR,
    parameters=("excluded_classes",),
    reach="pair",
)
def _build_endpoint_is_not_of_an_excluded_class() -> Predicate:
    return EndpointIsNotOfAnExcludedClass()


@declare(
    "candidates_are_bounded_per_subject",
    version="1",
    summary="Refuse to generate more than a stated number of candidates for one subject",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.CANDIDATES,
    parameters=("maximum_candidates",),
)
def _build_candidates_are_bounded_per_subject() -> Predicate:
    return CandidatesAreBoundedPerSubject()


@declare(
    "substitution_rests_on_content",
    version="1",
    summary="Refuse an identical edge asserted from anything but a full content digest",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.PAIR,
    parameters=("permitted_methods",),
)
def _build_substitution_rests_on_content() -> Predicate:
    return SubstitutionRestsOnContent()


@declare(
    "succession_stays_within_one_series",
    version="1",
    summary="Refuse an order between entries no equivalence places in one series",
    effect=RuleEffect.CONSTRAINT,
    reads=Reads.PAIR,
    parameters=("series_equivalence",),
)
def _build_succession_stays_within_one_series() -> Predicate:
    return SuccessionStaysWithinOneSeries()
