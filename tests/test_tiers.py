"""The two tier axes stay apart, and absence stays in three pieces."""

from __future__ import annotations

from ingence.core.tiers import (
    EXTRACTION_TIER_PROPERTIES,
    DecisionTier,
    ExtractionTier,
    InferenceTier,
    ResolutionStatus,
    extraction_distance,
    inference_standing,
    is_absence,
    join_inference_tiers,
    never_lowers,
)


def test_the_two_axes_share_no_member():
    """A value that belonged to both axes would let them be confused."""
    assert not {tier.value for tier in ExtractionTier} & {tier.value for tier in InferenceTier}


def test_the_extraction_ladder_has_six_rungs_and_two_absences():
    graded = [tier for tier in ExtractionTier if not is_absence(tier)]
    absent = [tier for tier in ExtractionTier if is_absence(tier)]
    assert len(graded) == 6
    assert set(absent) == {ExtractionTier.INACCESSIBLE, ExtractionTier.NOT_EXAMINED}


def test_distance_increases_away_from_the_bytes():
    ladder = [
        ExtractionTier.LITERAL,
        ExtractionTier.MECHANICALLY_EXPOSED,
        ExtractionTier.DECODED,
        ExtractionTier.FORMALLY_EXTRACTED,
        ExtractionTier.HEURISTIC,
        ExtractionTier.SEMANTIC,
    ]
    distances = [extraction_distance(tier) for tier in ladder]
    assert distances == sorted(distances)
    assert distances[0] == 0


def test_an_absence_has_no_distance():
    """Substituting a number for "we did not look" makes it a weak positive."""
    assert extraction_distance(ExtractionTier.NOT_EXAMINED) is None
    assert extraction_distance(ExtractionTier.INACCESSIBLE) is None


def test_lettering_covers_the_graded_tiers_only():
    lettered = {
        props.letter for props in EXTRACTION_TIER_PROPERTIES.values() if props.letter is not None
    }
    assert lettered == set("ABCDEF")


def test_testimony_out_of_its_scope_is_not_evidence():
    """A person's answer settles the question asked and no other."""
    assert inference_standing(InferenceTier.TESTIFIED, in_scope=True) is not None
    assert inference_standing(InferenceTier.TESTIFIED, in_scope=False) is None
    assert inference_standing(InferenceTier.ENTAILED, in_scope=False) is not None


def test_machine_tiers_rank_entailed_above_defeasible_above_abductive():
    order = [InferenceTier.ABDUCTIVE, InferenceTier.DEFEASIBLE, InferenceTier.ENTAILED]
    standings = [inference_standing(tier) for tier in order]
    assert standings == sorted(standings)


def test_joining_tiers_takes_the_stronger():
    assert (
        join_inference_tiers(InferenceTier.ABDUCTIVE, InferenceTier.ENTAILED)
        is InferenceTier.ENTAILED
    )
    assert (
        join_inference_tiers(InferenceTier.DEFEASIBLE, InferenceTier.DEFEASIBLE)
        is InferenceTier.DEFEASIBLE
    )


def test_more_evidence_never_lowers_standing():
    assert never_lowers(InferenceTier.ABDUCTIVE, InferenceTier.ENTAILED)
    assert never_lowers(InferenceTier.ENTAILED, InferenceTier.ENTAILED)
    assert not never_lowers(InferenceTier.ENTAILED, InferenceTier.DEFEASIBLE)


def test_absence_is_three_distinct_states():
    absences = set(ResolutionStatus) - {ResolutionStatus.RESOLVED}
    assert len(absences) == 3


def test_only_direct_and_anchored_are_auto_acceptable():
    from ingence.core.tiers import DECISION_TIER_PROPERTIES

    auto = {tier for tier, p in DECISION_TIER_PROPERTIES.items() if p.auto_acceptable}
    assert auto == {DecisionTier.DIRECT, DecisionTier.ANCHORED}
