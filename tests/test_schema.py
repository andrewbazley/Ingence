"""The record models enforce, at construction, what the primitives declare."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.relations import ExpansionState, Primitive, ReferenceResolution
from ingence.core.schema import (
    CLOCK_TIER_OF,
    CLOCK_TIERS,
    PERSISTED_RECORDS,
    Assertion,
    Claim,
    ClockTier,
    Entry,
    EntryKind,
    FailureClass,
    NameSlot,
    Observation,
    Role,
    Rule,
    RuleEffect,
    ScanError,
    SupportComponent,
    ThemeKind,
    TimeSource,
)
from ingence.core.tiers import ExtractionTier, InferenceTier

EDGE = {
    "edge_id": "e",
    "run_id": "r",
    "source_id": "a",
    "target_id": "b",
    "rule_id": "x",
    "rule_version": "1",
    "scope": PROJECT_SCOPE,
}


def test_every_persisted_record_carries_a_schema_version():
    for record in PERSISTED_RECORDS:
        assert "schema_version" in record.model_fields


def test_records_are_frozen_and_reject_unknown_fields():
    entry = Entry(
        entry_id="e",
        root_id="r",
        relative_path="a",
        name="a",
        first_seen_run="1",
        last_seen_run="1",
    )
    with pytest.raises(ValidationError):
        entry.name = "b"
    with pytest.raises(ValidationError):
        Entry(
            entry_id="e",
            root_id="r",
            relative_path="a",
            name="a",
            first_seen_run="1",
            last_seen_run="1",
            tpyo=1,
        )


def test_observations_and_claims_share_no_field_that_could_confuse_them():
    """A fact and an inference that can occupy one column will be confused."""
    shared = set(Observation.model_fields) & set(Claim.model_fields)
    assert shared == {"schema_version", "run_id"}
    assert "inference_tier" not in Observation.model_fields
    assert "extraction_tier" not in Claim.model_fields


def test_equivalence_without_a_respect_is_refused():
    with pytest.raises(ValidationError, match="requires a respect"):
        Assertion(**EDGE, primitive=Primitive.EQUIVALENT)


def test_embedding_without_an_expansion_state_is_refused():
    with pytest.raises(ValidationError, match="requires an expansion state"):
        Assertion(**EDGE, primitive=Primitive.EMBEDS)


def test_a_reference_must_say_how_it_resolved():
    with pytest.raises(ValidationError, match="how its reference string resolved"):
        Assertion(**EDGE, primitive=Primitive.REFERENCES)


def test_a_well_formed_edge_builds():
    edge = Assertion(**EDGE, primitive=Primitive.EMBEDS, expansion_state=ExpansionState.BOUNDED)
    assert edge.expansion_state is ExpansionState.BOUNDED
    reference = Assertion(
        **EDGE,
        primitive=Primitive.REFERENCES,
        reference_resolution=ReferenceResolution.UNRESOLVED,
    )
    assert reference.reference_resolution is ReferenceResolution.UNRESOLVED


def test_a_retractable_claim_must_name_what_would_retract_it():
    for tier in (InferenceTier.DEFEASIBLE, InferenceTier.ABDUCTIVE):
        with pytest.raises(ValidationError, match="must declare at least one defeater"):
            Claim(
                claim_id="c",
                run_id="r",
                subject="a",
                predicate="p",
                inference_tier=tier,
                rule_id="x",
                rule_version="1",
                scope=PROJECT_SCOPE,
            )


def test_an_entailed_claim_needs_no_defeater():
    claim = Claim(
        claim_id="c",
        run_id="r",
        subject="a",
        predicate="p",
        inference_tier=InferenceTier.ENTAILED,
        rule_id="x",
        rule_version="1",
        scope=PROJECT_SCOPE,
    )
    assert claim.defeaters == ()


def test_support_cannot_be_negative():
    """Impossibility is a decoder constraint, never a large negative weight."""
    with pytest.raises(ValidationError):
        SupportComponent(name="n", family="f", value=-0.1)


def test_a_tier_recording_no_reading_carries_no_reading():
    for tier in (ExtractionTier.NOT_EXAMINED, ExtractionTier.INACCESSIBLE):
        with pytest.raises(ValidationError, match="must carry no value"):
            Observation(
                observation_id="o",
                entry_id="e",
                run_id="r",
                observer="s",
                observer_version="1",
                observed_at="t",
                key="k",
                extraction_tier=tier,
                value_text="something",
            )


def test_a_derived_entry_must_say_what_produced_it():
    with pytest.raises(ValidationError, match="requires a non-empty locator chain"):
        Entry(
            entry_id="e",
            entry_kind=EntryKind.DERIVED,
            root_id="r",
            relative_path="a",
            name="a",
            first_seen_run="1",
            last_seen_run="1",
        )


def test_a_rule_must_be_justifiable_in_one_sentence():
    """A rule that cannot be justified is a heuristic in disguise."""
    with pytest.raises(ValidationError):
        Rule(
            rule_id="r",
            version="1",
            statement="s",
            scope=PROJECT_SCOPE,
            predicate="p",
            effect=RuleEffect.FLAGS,
            rationale="",
        )


def test_a_rule_evaluates_at_file_level_by_default():
    rule = Rule(
        rule_id="r",
        version="1",
        statement="s",
        scope=PROJECT_SCOPE,
        predicate="p",
        effect=RuleEffect.FLAGS,
        rationale="because",
    )
    assert rule.level == "file"


# ---------------------------------------------------------------------------
# The clock ladder, graded by copy-robustness.
# ---------------------------------------------------------------------------


def test_every_time_source_lands_on_exactly_one_clock_tier():
    """A source with no tier is a time nobody can rank against another."""
    assert set(CLOCK_TIER_OF) == set(TimeSource)
    assert set(CLOCK_TIER_OF.values()) <= set(ClockTier)


def test_the_ladder_is_ordered_by_copy_robustness_and_not_by_accuracy():
    """The ordering principle, asserted rather than left in a docstring.

    Every tier that survives a copy outranks every tier that does not. That is
    the whole claim, and it is what makes the ladder principled: a filesystem
    clock can be exact to the nanosecond and still be a fact about when somebody
    moved a folder.
    """
    ranked = [t for t in CLOCK_TIERS if t.rank is not None]
    assert [t.rank for t in ranked] == sorted(t.rank for t in ranked)

    survives = [t.rank for t in ranked if t.survives_copying]
    does_not = [t.rank for t in ranked if not t.survives_copying]
    assert max(survives) < min(does_not)


def test_the_absence_state_is_not_the_worst_rung():
    """``UNAVAILABLE`` has no rank, so it cannot sort as a bad clock.

    The same distinction the extraction tiers draw: "we did not find one" is not
    a weak reading, and giving it a number would let it be compared with one.
    """
    absent = next(t for t in CLOCK_TIERS if t.tier is ClockTier.UNAVAILABLE)
    assert absent.rank is None


def test_an_event_time_defaults_to_having_no_source_and_no_tier():
    """The pair is never absent, so a time can never arrive without provenance."""
    entry = Entry(
        entry_id="e",
        root_id="r",
        relative_path="a.txt",
        name="a.txt",
        entry_type="file",
        first_seen_run="1",
        last_seen_run="1",
    )
    assert entry.event_time_ns is None
    assert entry.event_time_source is TimeSource.UNAVAILABLE
    assert entry.event_time_tier is ClockTier.UNAVAILABLE


# ---------------------------------------------------------------------------
# Occurrences and roles.
# ---------------------------------------------------------------------------


def test_a_slot_that_ends_before_it_starts_is_refused():
    with pytest.raises(ValidationError):
        NameSlot(ordinal=0, component_id="c", component_type="word", start=10, end=4)


def test_a_role_is_declared_and_nothing_ships_one():
    """The vocabulary exists and is empty.

    A role is a relationship inside one interpretation, and there are no interpretations. Shipping a
    role vocabulary would invite a rule to assign one before anything can check whether the
    assignment was right.
    """
    declared = Role(name="control", definition="the condition the others are read against")
    assert declared.enabled
    # Nothing in the shipped Dictionary or the schema module supplies one.
    assert NameSlot(ordinal=0, component_id="c", component_type="word").role is None


def test_a_theme_kind_and_a_role_are_different_records():
    """Not one table with a discriminator, because they are not one kind of thing."""
    assert ThemeKind is not Role
    assert Role in PERSISTED_RECORDS
    assert ThemeKind in PERSISTED_RECORDS


# ---------------------------------------------------------------------------
# Failure class: the partition that routes the fix.
# ---------------------------------------------------------------------------


def test_a_scan_error_refuses_to_be_an_interpretation_failure():
    """Interpretation is a property of the rules, and this layer runs none.

    Refusing it here is what stops the class being populated by the wrong layer
    before the Rulebook exists to populate it correctly, which would make a
    starved rule and a wrong rule look the same in the one report that exists to
    tell them apart.
    """
    with pytest.raises(ValidationError):
        ScanError(
            entry_id=None,
            relative_path="a.txt",
            operation="stat",
            error_type="PermissionError",
            message="denied",
            run_id="r",
            failure_class=FailureClass.INTERPRETATION,
        )


def test_the_three_failure_classes_stay_three():
    """Adding a fourth is a decision, because the partition routes a remedy."""
    assert [f.value for f in FailureClass] == ["access", "comprehension", "interpretation"]
