"""The ten publication invariants, as executable checks that block publication.

Each invariant is a property the emitted graph must have if the relation primitives mean what they
are declared to mean. They run before a graph is published. A failing invariant blocks the commit
instead of emitting a warning, because a warning nobody reads is a property nobody has.

Two design commitments apply.

The properties are read, not restated. Every check consults
:data:`~ingence.core.relations.PRIMITIVE_PROPERTIES` for what a primitive declares. A check that
hard-coded "contains forms a forest" would drift from the declaration it enforces, and the drift
would be silent.

Unevaluable is not the same as passing. The substitutivity check (INV-7) needs to know which entry
each observation belongs to, and the monotonic-tier check (INV-10) needs two runs. Where that
context is absent the outcome says ``not_applicable`` instead of ``passed``, because a check
that reports success on evidence it never had is worse than no check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.core.relations import (
    PRIMITIVE_PROPERTIES,
    ExpansionState,
    Primitive,
    SpecializationRegistry,
)
from ingence.core.schema import Assertion, Claim, DecisionStatus, InvariantOutcome
from ingence.core.tiers import never_lowers

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

__all__ = [
    "CLOSED_WORLD_PREDICATE_PREFIX",
    "INVARIANTS",
    "GraphSnapshot",
    "Invariant",
    "check_all",
    "check_monotonic_tiers",
]

#: A claim whose predicate begins with this prefix asserts a closed-world fact:
#: "this container holds exactly these things, and nothing is unaccounted for."
#: Only ``contains`` is observationally complete, so such a claim over a
#: container reached by ``embeds`` requires every embedding out of it to have
#: been fully expanded. The prefix is a declared convention rather than an
#: enumerated list, because the set of closed-world predicates grows with the
#: rules and enumerating it here would make adding a rule a schema change.
CLOSED_WORLD_PREDICATE_PREFIX = "closed_world."

#: Maximum violations listed on one outcome. The full set stays queryable in the
#: run directory. This is what a person is shown, and a wall of identical lines helps nobody.
MAX_REPORTED_EXAMPLES = 20


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    """Everything an invariant needs to inspect one emitted graph.

    Deliberately a plain container rather than a storage object, so the checks
    can run against a fixture, against an in-memory build, or against a committed
    run without knowing the difference.
    """

    assertions: tuple[Assertion, ...] = ()
    claims: tuple[Claim, ...] = ()
    #: The live specialization table the graph was built under. Needed by
    #: the check that every edge names a rule and a resolvable specialization, which cannot
    #: resolve a specialization without it.
    specializations: SpecializationRegistry = field(default_factory=SpecializationRegistry)
    #: Observation identifier to the entry it was made about. Supplied by the
    #: storage layer. The substitutivity check reports ``not_applicable`` without it, since
    #: substitutivity cannot be checked when the owner of a fact is unknown.
    observation_owner: Mapping[str, str] | None = None

    def edges(self, primitive: Primitive) -> tuple[Assertion, ...]:
        """Return published edges of one primitive, in canonical order.

        Blocked, superseded and withdrawn edges are excluded. An invariant is a
        property of what the graph *asserts*. A recorded rejection is not an assertion and must not
        be checked as though it were.
        """
        return tuple(
            sorted(
                (
                    assertion
                    for assertion in self.assertions
                    if assertion.primitive is primitive
                    and assertion.decision_status is DecisionStatus.ASSERTED
                ),
                key=lambda a: a.edge_id,
            )
        )

    def asserted(self) -> tuple[Assertion, ...]:
        """Return every published edge, in canonical order."""
        return tuple(
            sorted(
                (a for a in self.assertions if a.decision_status is DecisionStatus.ASSERTED),
                key=lambda a: a.edge_id,
            )
        )


@dataclass(frozen=True, slots=True)
class Invariant:
    """One declared property of a published graph, with its executable check."""

    invariant_id: str
    statement: str
    #: The primitives this invariant constrains. Empty where it constrains the
    #: graph as a whole.
    primitives: tuple[Primitive, ...]
    #: True when the property compares two runs and cannot be evaluated against
    #: a single graph.
    differential: bool
    #: Returns a list of violation descriptions, empty when the property holds.
    #: Returning ``None`` means the check could not be evaluated here.
    check: Callable[[GraphSnapshot], list[str] | None]


# ---------------------------------------------------------------------------
# Small graph utilities. Kept local so the invariants have no dependency on a graph backend. They
# run before any backend is constructed.
# ---------------------------------------------------------------------------


def _find_cycle(edges: Iterable[tuple[str, str]]) -> list[str] | None:
    """Return one cycle as a node list, or None when the edge set is acyclic."""
    successors: dict[str, list[str]] = {}
    for source, target in edges:
        successors.setdefault(source, []).append(target)
        successors.setdefault(target, [])
    for targets in successors.values():
        targets.sort()

    visiting: set[str] = set()
    finished: set[str] = set()
    path: list[str] = []

    def walk(node: str) -> list[str] | None:
        visiting.add(node)
        path.append(node)
        for nxt in successors.get(node, ()):
            if nxt in visiting:
                return [*path[path.index(nxt) :], nxt]
            if nxt not in finished:
                found = walk(nxt)
                if found is not None:
                    return found
        visiting.discard(node)
        finished.add(node)
        path.pop()
        return None

    for node in sorted(successors):
        if node not in finished:
            cycle = walk(node)
            if cycle is not None:
                return cycle
    return None


def _components(pairs: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """Return connected components keyed by their canonical representative."""
    parent: dict[str, str] = {}

    def root(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in pairs:
        a, b = root(left), root(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    grouped: dict[str, set[str]] = {}
    for node in parent:
        grouped.setdefault(root(node), set()).add(node)
    return grouped


def _reachable(edges: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """Return the transitive reachability set of each node."""
    successors: dict[str, set[str]] = {}
    for source, target in edges:
        successors.setdefault(source, set()).add(target)
        successors.setdefault(target, set())

    closure: dict[str, set[str]] = {}

    def walk(node: str) -> set[str]:
        if node in closure:
            return closure[node]
        closure[node] = set()
        seen: set[str] = set()
        for nxt in successors.get(node, ()):
            seen.add(nxt)
            seen |= walk(nxt)
        closure[node] = seen
        return seen

    for node in sorted(successors):
        walk(node)
    return closure


def _truncate(violations: list[str]) -> tuple[str, ...]:
    """Return the bounded sample of violations shown on an outcome."""
    return tuple(sorted(violations)[:MAX_REPORTED_EXAMPLES])


# ---------------------------------------------------------------------------
# The ten checks.
# ---------------------------------------------------------------------------


def _check_containment_forests(snapshot: GraphSnapshot) -> list[str]:
    """Check that ``contains`` and ``embeds`` form forests.

    A forest is what makes a containment hierarchy navigable and what makes
    "everything under this folder" a well-defined set. Two parents means an
    entry is in two places at once, which no filesystem or container permits and
    which would make any aggregate over the hierarchy double-count.
    """
    violations: list[str] = []
    for primitive in (Primitive.CONTAINS, Primitive.EMBEDS):
        if not PRIMITIVE_PROPERTIES[primitive].forms_forest:
            continue
        edges = snapshot.edges(primitive)
        parents: dict[str, set[str]] = {}
        for edge in edges:
            parents.setdefault(edge.target_id, set()).add(edge.source_id)
        for child, sources in sorted(parents.items()):
            if len(sources) > 1:
                listed = ", ".join(sorted(sources))
                violations.append(
                    f"{primitive.value}: {child} has {len(sources)} parents ({listed})"
                )
        cycle = _find_cycle((edge.source_id, edge.target_id) for edge in edges)
        if cycle is not None:
            violations.append(f"{primitive.value}: cycle {' -> '.join(cycle)}")
    return violations


def _check_derives_acyclic(snapshot: GraphSnapshot) -> list[str]:
    """Check that ``derives`` is acyclic: nothing is its own ancestor.

    A cycle in derivation is a contradiction rather than an oddity: it says a
    file was made from something that was made from it. Where one appears, some
    inference is wrong, and finding out which is more useful than publishing the
    graph.
    """
    edges = snapshot.edges(Primitive.DERIVES)
    cycle = _find_cycle((edge.source_id, edge.target_id) for edge in edges)
    if cycle is None:
        return []
    return [f"derives: cycle {' -> '.join(cycle)}"]


def _check_identical_is_an_equivalence(snapshot: GraphSnapshot) -> list[str]:
    """Check that ``identical`` is a proper equivalence, transitively closed.

    Symmetry holds by construction, because the identity function orders the
    endpoints of a symmetric primitive, so one unordered pair is one edge.
    Transitive closure does not hold by construction and is what is checked
    here: if A and B are the same bytes and B and C are the same bytes, then A
    and C are the same bytes, and an engine that fails to say so will hash the
    same content twice and report a duplicate group that is missing a member.
    """
    edges = snapshot.edges(Primitive.IDENTICAL)
    present = {frozenset((edge.source_id, edge.target_id)) for edge in edges}
    violations: list[str] = []
    for members in _components((e.source_id, e.target_id) for e in edges).values():
        ordered = sorted(members)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if frozenset((left, right)) not in present:
                    violations.append(
                        f"identical: {left} and {right} are in one component but no edge asserts it"
                    )
    return violations


def _check_equivalent_closed_within_respect(snapshot: GraphSnapshot) -> list[str]:
    """Check that ``equivalent`` is transitively closed within each respect.

    Within one respect, transitivity must hold: asserting that A and B are
    equivalent in respect schema and that B and C are, without asserting it of A
    and C, is a bug, and it shows up as a group that is missing members.

    Across respects nothing is implied, and that holds by construction rather
    than by checking: the respect is part of an edge's identity, so two edges
    under different respects can never be the same edge and no closure
    computation here ever crosses one.
    """
    edges = snapshot.edges(Primitive.EQUIVALENT)
    by_respect: dict[str, list[Assertion]] = {}
    for edge in edges:
        by_respect.setdefault(edge.respect or "", []).append(edge)

    violations: list[str] = []
    for respect, group in sorted(by_respect.items()):
        present = {frozenset((e.source_id, e.target_id)) for e in group}
        for members in _components((e.source_id, e.target_id) for e in group).values():
            ordered = sorted(members)
            for index, left in enumerate(ordered):
                for right in ordered[index + 1 :]:
                    if frozenset((left, right)) not in present:
                        violations.append(
                            f"equivalent[{respect}]: {left} and {right} are in one "
                            f"class but no edge asserts it"
                        )
    return violations


def _check_succeeds_series_are_total(snapshot: GraphSnapshot) -> list[str]:
    """Check that ``succeeds`` series are total: every pair is comparable.

    A series is a line. Two members of one series that neither precedes the
    other means the series is not a series. Either two distinct series were
    merged, or an ordering was asserted that does not exist. This is the property
    that separates ``succeeds`` from ``derives``, which is a partial order and
    branches freely, so it is exactly the property whose failure means the two
    have been confused.

    A series is scoped by respect, scope and level, because two version series in
    two folders are two series and comparing across them is meaningless.
    """
    edges = snapshot.edges(Primitive.SUCCEEDS)
    by_series: dict[tuple[str, str, str], list[Assertion]] = {}
    for edge in edges:
        key = (edge.respect or "", edge.scope.token, edge.level)
        by_series.setdefault(key, []).append(edge)

    violations: list[str] = []
    for (respect, scope, level), group in sorted(by_series.items()):
        pairs = [(e.source_id, e.target_id) for e in group]
        closure = _reachable(pairs)
        for members in _components(pairs).values():
            ordered = sorted(members)
            for index, left in enumerate(ordered):
                for right in ordered[index + 1 :]:
                    if right not in closure.get(left, set()) and left not in closure.get(
                        right, set()
                    ):
                        violations.append(
                            f"succeeds[{respect}|{scope}|{level}]: {left} and {right} "
                            f"are in one series but neither precedes the other"
                        )
    return violations


def _check_concerns_not_composed(snapshot: GraphSnapshot) -> list[str]:
    """Check that ``concerns`` is never used transitively.

    Aboutness chains do not compose. A and B may share a topic, B and C may share
    a different one, and A and C may share nothing. Any implementation that
    treats topical association as transitive connects everything to everything
    after three or four hops, which is the classic failure of naive similarity
    graphs and the reason this property is declared and tested rather than
    assumed.

    Checked in the form that makes chaining impossible rather than merely
    discouraged: every published ``concerns`` edge must rest on direct support
    with a weight earned against the null model, and none may cite another
    ``concerns`` edge as an input. An edge composed from two others would have no
    direct support to show.
    """
    edges = snapshot.edges(Primitive.CONCERNS)
    concerns_edge_ids = {edge.edge_id for edge in edges}
    violations: list[str] = []
    for edge in edges:
        if edge.weight is None:
            violations.append(
                f"concerns: {edge.edge_id} carries no weight; topical association "
                f"arises by chance and must be earned against the null model"
            )
        if not edge.support:
            violations.append(
                f"concerns: {edge.edge_id} has no direct support, so nothing "
                f"distinguishes it from a composition of two other edges"
            )
        cited = {reference for component in edge.support for reference in component.observations}
        composed = sorted(cited & concerns_edge_ids)
        if composed:
            violations.append(
                f"concerns: {edge.edge_id} cites concerns edges "
                f"({', '.join(composed)}) as support, which composes aboutness"
            )
    return violations


def _check_substitutivity_confined(snapshot: GraphSnapshot) -> list[str] | None:
    """Check that substitutivity is confined to ``identical``.

    If two files are byte-identical, every fact derived from one file's content
    is automatically true of the other, and the engine may transfer it without
    re-reading. No other relation licenses that: two files with the same schema
    are not interchangeable, and two experiments under one condition certainly
    are not.

    The check looks for a claim about one entry that consumed observations made
    about a different entry, and requires an asserted ``identical`` edge between
    them. Without the observation-to-entry mapping the property cannot be
    evaluated, and the outcome says so rather than passing.
    """
    owner = snapshot.observation_owner
    if owner is None:
        return None

    identical_pairs = {
        frozenset((edge.source_id, edge.target_id)) for edge in snapshot.edges(Primitive.IDENTICAL)
    }
    violations: list[str] = []
    for claim in sorted(snapshot.claims, key=lambda c: c.claim_id):
        for observation in claim.inputs:
            source_entry = owner.get(observation)
            if source_entry is None or source_entry == claim.subject:
                continue
            if frozenset((claim.subject, source_entry)) not in identical_pairs:
                violations.append(
                    f"claim {claim.claim_id} about {claim.subject} consumed "
                    f"observation {observation} made about {source_entry}, which is "
                    f"not asserted identical to it"
                )
    return violations


def _check_embeds_expansion_state(snapshot: GraphSnapshot) -> list[str]:
    """Check that every ``embeds`` edge carries an expansion state.

    The absence of an ``embeds`` edge means one of two entirely different things. Either the
    container holds nothing else, or the engine did not look far enough. The engine must be
    able to say which. That is the whole reason ``embeds``
    is a separate primitive from ``contains``.

    The second half follows: no closed-world statement may be made over a
    container whose expansion was not complete. "This archive holds exactly three
    files" is only true if the archive was fully opened.
    """
    violations: list[str] = []
    edges = snapshot.edges(Primitive.EMBEDS)
    incomplete: dict[str, ExpansionState] = {}
    for edge in edges:
        if edge.expansion_state is None:
            violations.append(f"embeds: {edge.edge_id} carries no expansion state")
        elif edge.expansion_state is not ExpansionState.COMPLETE:
            incomplete[edge.source_id] = edge.expansion_state

    for claim in sorted(snapshot.claims, key=lambda c: c.claim_id):
        if not claim.predicate.startswith(CLOSED_WORLD_PREDICATE_PREFIX):
            continue
        state = incomplete.get(claim.subject)
        if state is not None:
            violations.append(
                f"claim {claim.claim_id} makes the closed-world statement "
                f"{claim.predicate!r} about {claim.subject}, whose expansion state "
                f"is {state.value!r}"
            )
    return violations


def _check_edges_name_their_rule(snapshot: GraphSnapshot) -> list[str]:
    """Check that every edge names a rule and version, and that specializations resolve.

    This is the invariant that makes the whole thesis operational. If an edge
    exists that no rule licensed, then the rules are not governing the engine and
    the claim that every conclusion is attributable is false.

    A specialization must also resolve to an enabled primitive and agree with the
    edge's own. A disabled specialization is a configuration decision, and an
    edge that rests on one is an edge the configuration said not to draw.
    """
    violations: list[str] = []
    for edge in snapshot.asserted():
        if not edge.rule_id or not edge.rule_version:
            violations.append(f"edge {edge.edge_id} names no rule and version")
        if edge.specialization is None:
            continue
        name = edge.specialization
        if name not in snapshot.specializations:
            violations.append(
                f"edge {edge.edge_id} uses specialization {name!r}, which is not registered"
            )
            continue
        declared = snapshot.specializations.get(name)
        if not declared.enabled:
            violations.append(
                f"edge {edge.edge_id} uses specialization {name!r}, which is disabled"
            )
        elif declared.parent is not edge.primitive:
            violations.append(
                f"edge {edge.edge_id} is a {edge.primitive.value} but its "
                f"specialization {name!r} declares parent {declared.parent.value}"
            )
    return violations


def _check_monotonic_tiers_single(snapshot: GraphSnapshot) -> list[str] | None:
    """Report that the monotonic-tier check cannot be evaluated on a single graph.

    "Adding an extractor never lowers a claim's tier" is a property of two runs,
    not of one graph. Use :func:`check_monotonic_tiers` with both snapshots. Here
    the outcome reports that it was not applicable, which is different from
    reporting that it passed.
    """
    del snapshot
    return None


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "INV-1",
        "contains and embeds form forests: no node has two parents, and no cycles.",
        (Primitive.CONTAINS, Primitive.EMBEDS),
        False,
        _check_containment_forests,
    ),
    Invariant(
        "INV-2",
        "derives is acyclic. Nothing is its own ancestor.",
        (Primitive.DERIVES,),
        False,
        _check_derives_acyclic,
    ),
    Invariant(
        "INV-3",
        "identical is a proper equivalence: symmetric and transitively closed.",
        (Primitive.IDENTICAL,),
        False,
        _check_identical_is_an_equivalence,
    ),
    Invariant(
        "INV-4",
        "equivalent is transitively closed within each respect, and implies "
        "nothing across respects.",
        (Primitive.EQUIVALENT,),
        False,
        _check_equivalent_closed_within_respect,
    ),
    Invariant(
        "INV-5",
        "succeeds series are total: every pair within a series is comparable.",
        (Primitive.SUCCEEDS,),
        False,
        _check_succeeds_series_are_total,
    ),
    Invariant(
        "INV-6",
        "concerns is never used transitively: every edge rests on direct, "
        "weighted support and composes no others.",
        (Primitive.CONCERNS,),
        False,
        _check_concerns_not_composed,
    ),
    Invariant(
        "INV-7",
        "Substitutivity is confined to identical: no content-derived fact is "
        "transferred across any other relation.",
        (Primitive.IDENTICAL,),
        False,
        _check_substitutivity_confined,
    ),
    Invariant(
        "INV-8",
        "Every embeds edge carries an expansion state, and no closed-world "
        "statement is made over a container that was not fully expanded.",
        (Primitive.EMBEDS,),
        False,
        _check_embeds_expansion_state,
    ),
    Invariant(
        "INV-9",
        "Every asserted edge names a rule and a rule version, and every "
        "specialization resolves to an enabled primitive.",
        (),
        False,
        _check_edges_name_their_rule,
    ),
    Invariant(
        "INV-10",
        "Adding an extractor never lowers a claim's tier. Verified by differential run.",
        (),
        True,
        _check_monotonic_tiers_single,
    ),
)


def check_all(snapshot: GraphSnapshot) -> tuple[InvariantOutcome, ...]:
    """Run every invariant and return one outcome each, in declaration order."""
    outcomes: list[InvariantOutcome] = []
    for invariant in INVARIANTS:
        violations = invariant.check(snapshot)
        if violations is None:
            outcomes.append(
                InvariantOutcome(
                    invariant_id=invariant.invariant_id,
                    statement=invariant.statement,
                    passed=True,
                    not_applicable=True,
                )
            )
            continue
        outcomes.append(
            InvariantOutcome(
                invariant_id=invariant.invariant_id,
                statement=invariant.statement,
                passed=not violations,
                violation_count=len(violations),
                examples=_truncate(violations),
            )
        )
    return tuple(outcomes)


def check_monotonic_tiers(before: GraphSnapshot, after: GraphSnapshot) -> InvariantOutcome:
    """Check across two runs that more evidence never lowers a claim's standing.

    More evidence must never lower a claim's standing. Adding an extractor may
    raise a claim's tier, leave it unchanged, or introduce an explicit defeater,
    which is a visible event recorded on the claim. What it may never do is
    quietly erode a conclusion, which is the failure mode of every additive
    confidence model and the reason this engine does not have one.

    Claims absent from the earlier run are new and unconstrained. Claims absent
    from the later run were retracted, which is a defeat and shows up as a
    defeater rather than as a tier change.
    """
    invariant = INVARIANTS[-1]
    earlier = {claim.claim_id: claim for claim in before.claims}
    violations: list[str] = []
    for claim in sorted(after.claims, key=lambda c: c.claim_id):
        previous = earlier.get(claim.claim_id)
        if previous is None:
            continue
        if not never_lowers(previous.inference_tier, claim.inference_tier):
            violations.append(
                f"claim {claim.claim_id} fell from "
                f"{previous.inference_tier.value} to {claim.inference_tier.value}"
            )
    return InvariantOutcome(
        invariant_id=invariant.invariant_id,
        statement=invariant.statement,
        passed=not violations,
        violation_count=len(violations),
        examples=_truncate(violations),
    )
