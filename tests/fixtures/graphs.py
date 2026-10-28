"""Two fixture graphs: one that satisfies every invariant, one that violates each.

The malformed graph proves that the invariant checks find what they claim to find. An invariant
suite that has only ever been run against valid data proves nothing: a check that always returns
"no violations" passes such a suite perfectly. Each violation below is built deliberately, is the
smallest thing that breaks its invariant, and is annotated with which one it breaks.

Several malformations that would be natural to write here are impossible to construct, because the
record models reject them at build time: an ``equivalent`` edge with no respect, an ``embeds`` edge
with no expansion state, a ``references`` edge that does not say how it resolved, a defeasible claim
naming no defeater. Those are enforced twice on purpose: once where the error is cheapest to
understand, and once over the whole graph before publication. The tests assert the construction-time
half separately.
"""

from __future__ import annotations

from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind, edge_id
from ingence.core.invariants import CLOSED_WORLD_PREDICATE_PREFIX, GraphSnapshot
from ingence.core.relations import (
    ExpansionState,
    Primitive,
    ReferenceResolution,
    SpecializationRegistry,
)
from ingence.core.schema import (
    Assertion,
    Claim,
    Defeater,
    SupportComponent,
)
from ingence.core.tiers import InferenceTier

RUN = "run_fixture"
RULE = "fixture.rule"
RULE_VERSION = "1"


def _edge(
    source: str,
    target: str,
    primitive: Primitive,
    *,
    respect: str | None = None,
    scope: Scope = PROJECT_SCOPE,
    rule_id: str = RULE,
    rule_version: str = RULE_VERSION,
    **extra: object,
) -> Assertion:
    """Build one assertion with a genuine identifier.

    The identifier comes from the real identity function rather than a literal,
    so the fixtures exercise endpoint ordering for symmetric primitives as a side
    effect of existing.
    """
    return Assertion(
        edge_id=edge_id(
            source=source,
            target=target,
            primitive=primitive,
            rule_id=rule_id or "unnamed",
            rule_version=rule_version or "0",
            scope=scope,
            respect=respect,
        ),
        run_id=RUN,
        source_id=source,
        target_id=target,
        primitive=primitive,
        respect=respect,
        rule_id=rule_id,
        rule_version=rule_version,
        scope=scope,
        inference_tier=InferenceTier.ENTAILED,
        **extra,  # type: ignore[arg-type]
    )


def _support(*observations: str) -> tuple[SupportComponent, ...]:
    return (
        SupportComponent(
            name="fixture_support",
            family="fixture",
            value=1.0,
            observations=tuple(observations),
        ),
    )


def _defeater() -> tuple[Defeater, ...]:
    return (
        Defeater(
            reason="a conflicting acquisition index would retract this",
            rule_id=RULE,
            rule_version=RULE_VERSION,
        ),
    )


# ---------------------------------------------------------------------------
# The clean graph.
# ---------------------------------------------------------------------------


def clean_graph() -> GraphSnapshot:
    """A small graph that satisfies all ten invariants.

    Deliberately exercises every primitive, so a check that accidentally passes
    by finding nothing of its primitive to look at is not mistaken for a check
    that passes.
    """
    series_scope = Scope(ScopeKind.FOLDER, "sessions")
    assertions = (
        # A containment forest: one root, two children, one grandchild.
        _edge("dir_root", "dir_a", Primitive.CONTAINS),
        _edge("dir_root", "file_1", Primitive.CONTAINS),
        _edge("dir_a", "file_2", Primitive.CONTAINS),
        # An archive fully expanded, so a closed-world statement about it is
        # legitimate.
        _edge(
            "file_archive",
            "file_member",
            Primitive.EMBEDS,
            expansion_state=ExpansionState.COMPLETE,
        ),
        # A derivation chain with no cycle.
        _edge("file_1", "file_2", Primitive.DERIVES),
        _edge("file_2", "file_3", Primitive.DERIVES),
        # A transitively closed identity class of three.
        _edge("file_1", "file_copy_a", Primitive.IDENTICAL),
        _edge("file_copy_a", "file_copy_b", Primitive.IDENTICAL),
        _edge("file_1", "file_copy_b", Primitive.IDENTICAL),
        # A closed equivalence class in one respect.
        _edge("table_1", "table_2", Primitive.EQUIVALENT, respect="schema"),
        _edge("table_2", "table_3", Primitive.EQUIVALENT, respect="schema"),
        _edge("table_1", "table_3", Primitive.EQUIVALENT, respect="schema"),
        # A total series: v3 after v2 after v1, every pair comparable.
        _edge("v2", "v1", Primitive.SUCCEEDS, respect="version", scope=series_scope),
        _edge("v3", "v2", Primitive.SUCCEEDS, respect="version", scope=series_scope),
        # A reference that resolved, and one that did not. Both are findings.
        _edge(
            "script_1",
            "file_1",
            Primitive.REFERENCES,
            reference_resolution=ReferenceResolution.EXACT,
        ),
        _edge(
            "script_1",
            "file_missing",
            Primitive.REFERENCES,
            reference_resolution=ReferenceResolution.UNRESOLVED,
        ),
        # An aboutness edge with a weight and direct support.
        _edge(
            "file_1",
            "file_2",
            Primitive.CONCERNS,
            respect="technique",
            weight=0.7,
            support=_support("obs_direct"),
        ),
        # An edge carrying a specialization that resolves to its own primitive.
        _edge("dir_root", "dir_b", Primitive.CONTAINS, specialization="directory_of"),
    )
    claims = (
        Claim(
            claim_id="claim_closed_world",
            run_id=RUN,
            subject="file_archive",
            predicate=f"{CLOSED_WORLD_PREDICATE_PREFIX}holds_exactly",
            inference_tier=InferenceTier.ENTAILED,
            rule_id=RULE,
            rule_version=RULE_VERSION,
            scope=PROJECT_SCOPE,
            inputs=("obs_archive",),
        ),
        # A fact transferred from a byte-identical twin, which is the one
        # relation that licenses transfer.
        Claim(
            claim_id="claim_transferred",
            run_id=RUN,
            subject="file_1",
            predicate="has_schema",
            inference_tier=InferenceTier.ENTAILED,
            rule_id=RULE,
            rule_version=RULE_VERSION,
            scope=PROJECT_SCOPE,
            inputs=("obs_from_copy",),
        ),
        Claim(
            claim_id="claim_defeasible",
            run_id=RUN,
            subject="file_2",
            predicate="derived_from",
            object_reference="file_1",
            inference_tier=InferenceTier.DEFEASIBLE,
            rule_id=RULE,
            rule_version=RULE_VERSION,
            scope=PROJECT_SCOPE,
            inputs=("obs_own",),
            defeaters=_defeater(),
        ),
    )
    return GraphSnapshot(
        assertions=assertions,
        claims=claims,
        specializations=SpecializationRegistry(),
        observation_owner={
            "obs_archive": "file_archive",
            # Owned by a byte-identical twin of file_1, so the transfer is legal.
            "obs_from_copy": "file_copy_a",
            "obs_own": "file_2",
            "obs_direct": "file_1",
        },
    )


# ---------------------------------------------------------------------------
# The malformed graph.
# ---------------------------------------------------------------------------


def malformed_graph() -> GraphSnapshot:
    """A graph violating every invariant that a single snapshot can express.

    Nine of the ten checks. INV-10 (adding an extractor never lowers a claim's tier) compares two
    runs, so :func:`tier_regression_pair` exercises it.
    """
    registry = SpecializationRegistry()
    registry.set_enabled("archive_member", enabled=False)
    series_scope = Scope(ScopeKind.FOLDER, "sessions")

    assertions = (
        # INV-1: one child with two parents under contains.
        _edge("dir_left", "file_shared", Primitive.CONTAINS),
        _edge("dir_right", "file_shared", Primitive.CONTAINS),
        # INV-1: a cycle under embeds.
        _edge(
            "arc_a",
            "arc_b",
            Primitive.EMBEDS,
            expansion_state=ExpansionState.BOUNDED,
        ),
        _edge(
            "arc_b",
            "arc_a",
            Primitive.EMBEDS,
            expansion_state=ExpansionState.BOUNDED,
        ),
        # INV-2: a derivation cycle. Something is its own ancestor.
        _edge("cyc_1", "cyc_2", Primitive.DERIVES),
        _edge("cyc_2", "cyc_3", Primitive.DERIVES),
        _edge("cyc_3", "cyc_1", Primitive.DERIVES),
        # INV-3: an identity class of three, missing the closing edge.
        _edge("iden_a", "iden_b", Primitive.IDENTICAL),
        _edge("iden_b", "iden_c", Primitive.IDENTICAL),
        # INV-4: an equivalence class in one respect, missing its closing edge.
        _edge("eq_a", "eq_b", Primitive.EQUIVALENT, respect="schema"),
        _edge("eq_b", "eq_c", Primitive.EQUIVALENT, respect="schema"),
        # INV-5: two members of one series that neither precedes the other.
        _edge("ser_b", "ser_a", Primitive.SUCCEEDS, respect="version", scope=series_scope),
        _edge("ser_c", "ser_a", Primitive.SUCCEEDS, respect="version", scope=series_scope),
        # INV-6: an aboutness edge with no weight and no direct support, and one
        # composed from two other aboutness edges.
        _edge("top_a", "top_b", Primitive.CONCERNS, respect="technique"),
        _edge(
            "top_b",
            "top_c",
            Primitive.CONCERNS,
            respect="technique",
            weight=0.4,
            support=_support("obs_topic"),
        ),
        # INV-8: an embedding that stopped short, over which a closed-world
        # statement is nonetheless made below.
        _edge(
            "arc_partial",
            "arc_member",
            Primitive.EMBEDS,
            expansion_state=ExpansionState.BOUNDED,
        ),
        # INV-9: an edge that names no rule, and one resting on a specialization
        # the configuration has switched off.
        _edge("orphan_a", "orphan_b", Primitive.DERIVES, rule_id="", rule_version=""),
        _edge(
            "arc_partial",
            "arc_member_2",
            Primitive.EMBEDS,
            expansion_state=ExpansionState.COMPLETE,
            specialization="archive_member",
        ),
    )

    # INV-6, second form: an edge whose support cites other concerns edges,
    # which is composing aboutness by another route.
    chained = _edge(
        "top_a",
        "top_c",
        Primitive.CONCERNS,
        respect="technique",
        weight=0.3,
        support=_support(assertions[13].edge_id, assertions[14].edge_id),
    )

    claims = (
        # INV-8: a closed-world statement over a container that was not fully
        # expanded.
        Claim(
            claim_id="claim_overreach",
            run_id=RUN,
            subject="arc_partial",
            predicate=f"{CLOSED_WORLD_PREDICATE_PREFIX}holds_exactly",
            inference_tier=InferenceTier.ENTAILED,
            rule_id=RULE,
            rule_version=RULE_VERSION,
            scope=PROJECT_SCOPE,
            inputs=("obs_partial",),
        ),
        # INV-7: a fact about one entry transferred to another with no identity
        # edge licensing the transfer.
        Claim(
            claim_id="claim_illegal_transfer",
            run_id=RUN,
            subject="file_target",
            predicate="has_schema",
            inference_tier=InferenceTier.ENTAILED,
            rule_id=RULE,
            rule_version=RULE_VERSION,
            scope=PROJECT_SCOPE,
            inputs=("obs_elsewhere",),
        ),
    )

    return GraphSnapshot(
        assertions=(*assertions, chained),
        claims=claims,
        specializations=registry,
        observation_owner={
            "obs_partial": "arc_partial",
            "obs_topic": "top_b",
            # Owned by a file that is merely similar, not byte-identical.
            "obs_elsewhere": "file_lookalike",
        },
    )


def tier_regression_pair() -> tuple[GraphSnapshot, GraphSnapshot]:
    """Two snapshots where a claim's tier fell between runs.

    This is what INV-10 forbids. Adding an extractor may raise a tier, leave it unchanged, or
    introduce a defeater. It may never quietly move a conclusion down.
    """

    def claim(tier: InferenceTier) -> Claim:
        return Claim(
            claim_id="claim_regressing",
            run_id=RUN,
            subject="file_1",
            predicate="derived_from",
            object_reference="file_0",
            inference_tier=tier,
            rule_id=RULE,
            rule_version=RULE_VERSION,
            scope=PROJECT_SCOPE,
            defeaters=_defeater() if tier is not InferenceTier.ENTAILED else (),
        )

    before = GraphSnapshot(claims=(claim(InferenceTier.ENTAILED),))
    after = GraphSnapshot(claims=(claim(InferenceTier.ABDUCTIVE),))
    return before, after
