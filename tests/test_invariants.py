"""The invariant suite must fail on a malformed graph, not only pass on a clean one.

A suite that has only been run against valid data proves nothing, because a check that always
answers "no violations" passes it perfectly.
"""

from __future__ import annotations

import pytest

from fixtures.graphs import clean_graph, malformed_graph, tier_regression_pair
from ingence.core.invariants import (
    INVARIANTS,
    GraphSnapshot,
    check_all,
    check_monotonic_tiers,
)

SINGLE_SNAPSHOT_INVARIANTS = [inv.invariant_id for inv in INVARIANTS if not inv.differential]


def test_there_are_ten_invariants():
    assert len(INVARIANTS) == 10
    assert [inv.invariant_id for inv in INVARIANTS] == [f"INV-{n}" for n in range(1, 11)]


def test_the_clean_graph_passes_every_invariant():
    outcomes = check_all(clean_graph())
    failures = [outcome.invariant_id for outcome in outcomes if not outcome.passed]
    assert failures == []


@pytest.mark.parametrize("invariant_id", SINGLE_SNAPSHOT_INVARIANTS)
def test_each_invariant_fails_on_the_malformed_graph(invariant_id):
    """Every check must be shown to catch the thing it exists to catch."""
    outcomes = {outcome.invariant_id: outcome for outcome in check_all(malformed_graph())}
    outcome = outcomes[invariant_id]
    assert not outcome.passed, f"{invariant_id} did not catch its violation"
    assert outcome.violation_count >= 1
    assert outcome.examples, f"{invariant_id} failed without explaining why"


def test_a_differential_invariant_reports_not_applicable_rather_than_passing():
    """Reporting success on evidence never gathered is worse than no check."""
    outcome = next(o for o in check_all(clean_graph()) if o.invariant_id == "INV-10")
    assert outcome.not_applicable


def test_an_unevaluable_check_reports_not_applicable():
    """The INV-7 check needs to know which entry each observation belongs to."""
    snapshot = GraphSnapshot(
        assertions=clean_graph().assertions,
        claims=clean_graph().claims,
        observation_owner=None,
    )
    outcome = next(o for o in check_all(snapshot) if o.invariant_id == "INV-7")
    assert outcome.not_applicable


def test_a_tier_that_fell_between_runs_is_caught():
    before, after = tier_regression_pair()
    outcome = check_monotonic_tiers(before, after)
    assert not outcome.passed
    assert "fell from entailed to abductive" in outcome.examples[0]


def test_a_tier_that_rose_between_runs_is_permitted():
    before, after = tier_regression_pair()
    assert check_monotonic_tiers(after, before).passed


def test_a_new_claim_in_the_later_run_is_unconstrained():
    _, after = tier_regression_pair()
    assert check_monotonic_tiers(GraphSnapshot(), after).passed
