"""What a predicate is handed, and what evaluating a rule on an entry came to.

The predicates that ran before this existed are handed exactly what they read then, and the first
half of this file holds that. The second half holds the outcomes, because a rule starved of inputs
and a rule that cannot decide must not look alike in anything the pass records.
"""

from __future__ import annotations

import pytest

from ingence.core.ids import PROJECT_SCOPE, dictionary_term_id
from ingence.core.project import Project
from ingence.core.schema import (
    BindingAlternative,
    Component,
    DictionaryBinding,
    DictionaryOrigin,
    Entry,
    FailureClass,
    MatchQuality,
)
from ingence.core.tiers import ExtractionTier, ResolutionStatus
from ingence.infer.evaluate import (
    evaluate_on_entry,
    key_on_entry,
    parameters_in_force,
    verdict_values,
)
from ingence.infer.records import assemble_entry_records, tables_read
from ingence.infer.subjects import EntryRecords
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES, ClassKey, Reads, reads_of
from ingence.rulebook.resolve import EffectiveRulebook, effective_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

load_builtins()


def shipped_rulebook() -> EffectiveRulebook:
    """The book at revision 3, whose rules these tests run the pass with: several are superseded
    at revision 4, and every one stays readable at the revision that held it."""
    return effective_rulebook(load_revisions(), at=3)


_EXTENSION_CONSTRAINT = "extension:format"


def _entry() -> Entry:
    return Entry(
        entry_id="e",
        root_id="root",
        relative_path="a/protocol.md",
        name="protocol.md",
        depth=1,
        extension="md",
        first_seen_run="r",
        last_seen_run="r",
    )


def _component(kind: str, value: str) -> Component:
    return Component(
        component_id=f"c:{kind}:{value}",
        entry_id="e",
        run_id="r",
        component_type=kind,
        normalized_value=value,
        normalizer="test",
        normalizer_version="1",
    )


def _binding(term: str, theme_kind: str, *, constraint: str | None = None) -> DictionaryBinding:
    return DictionaryBinding(
        binding_id=f"b:{term}",
        component_id=f"c:{term}",
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        constraint=constraint,
        extraction_tier=ExtractionTier.SEMANTIC,
        term_id=dictionary_term_id(
            term=term, scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
        ),
        term=term,
        quality=MatchQuality.EXACT,
        theme_kind=theme_kind,
    )


def _alternative(term: str, theme_kind: str) -> BindingAlternative:
    return BindingAlternative(
        term_id=dictionary_term_id(
            term=term, scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value
        ),
        theme_kind=theme_kind,
        origin=DictionaryOrigin.BUILTIN,
        origin_rank=0,
        scope=PROJECT_SCOPE,
    )


# ---------------------------------------------------------------------------
# What a predicate is handed.
# ---------------------------------------------------------------------------


def test_one_entry_s_records_narrow_to_exactly_what_a_predicate_reads() -> None:
    records = EntryRecords(
        entry=_entry(),
        components=(_component("word", "protocol"),),
        bindings=(_binding("markdown", "format", constraint=_EXTENSION_CONSTRAINT),),
    )
    assert records.narrowed(Reads.ENTRY) is records.entry
    assert records.narrowed(Reads.COMPONENTS) == records.components
    assert records.narrowed(Reads.BINDINGS) == records.bindings
    assert records.narrowed(Reads.OBSERVATIONS) == ()
    assert records.narrowed(Reads.RECORDS) is records
    assert records.narrowed(Reads.ENTRY_IN_ROOT).entry is records.entry
    spelled = records.narrowed(Reads.NAME_SPELLINGS)
    assert (spelled.entry, dict(spelled.spellings)) == (records.entry, {})


def test_one_entry_s_records_refuse_to_stand_in_for_a_pair() -> None:
    """A predicate reading a pair handed one entry would abstain, and look like a verdict."""
    with pytest.raises(ValueError, match="pair"):
        EntryRecords(entry=_entry()).narrowed(Reads.PAIR)


@pytest.mark.parametrize(
    ("name", "reads"),
    [
        ("class_from_bound_sense", Reads.BINDINGS),
        ("format_from_extension", Reads.BINDINGS),
        ("placeholder_from_entry_kind", Reads.ENTRY),
        ("sense_from_name_tokens", Reads.BINDINGS),
        ("structure_from_component_kind", Reads.COMPONENTS),
    ],
)
def test_the_predicates_that_already_ran_declare_what_they_already_read(
    name: str, reads: Reads
) -> None:
    assert reads_of(PREDICATES.declaration(name)) is reads


def test_a_rule_through_the_pass_concludes_what_its_predicate_concludes() -> None:
    """The pass adds attribution and an outcome; it must not change the verdict."""
    rule = shipped_rulebook().by_id("format_from_extension")
    assert rule is not None
    predicate = PREDICATES.get(rule.predicate)
    records = EntryRecords(
        entry=_entry(),
        components=(_component("extension", "md"),),
        bindings=(_binding("markdown", "format", constraint=_EXTENSION_CONSTRAINT),),
    )
    outcome = evaluate_on_entry(rule, predicate, records, reads=Reads.BINDINGS)
    direct = predicate.evaluate(records.bindings, parameters_in_force(rule))
    assert outcome.concluded
    assert outcome.values == (direct,)
    assert outcome.inputs == ("b:markdown",)


def test_a_verdict_is_a_value_several_values_or_nothing() -> None:
    assert verdict_values(None, rule_id="r") == ()
    assert verdict_values("", rule_id="r") == ()
    assert verdict_values("markdown", rule_id="r") == ("markdown",)
    assert verdict_values(("protocol", "figure", "protocol"), rule_id="r") == (
        "figure",
        "protocol",
    )


def test_a_verdict_that_is_not_a_value_is_refused_rather_than_published() -> None:
    """``True`` read as the value ``"True"`` would be a claim nobody made."""
    with pytest.raises(TypeError, match="bool"):
        verdict_values(True, rule_id="r")


# ---------------------------------------------------------------------------
# What evaluating it came to.
# ---------------------------------------------------------------------------


def test_a_binder_that_never_ran_leaves_a_rule_unexamined_rather_than_without_evidence() -> None:
    """The binder writes a binding for every component whenever it runs, even to decline."""
    rule = shipped_rulebook().by_id("file_role_from_name")
    assert rule is not None
    predicate = PREDICATES.get(rule.predicate)
    records = EntryRecords(entry=_entry(), components=(_component("word", "protocol"),))
    outcome = evaluate_on_entry(rule, predicate, records, reads=Reads.BINDINGS)
    assert outcome.status is ResolutionStatus.UNEXAMINED
    assert outcome.failure_class is None


def test_a_conflict_on_the_rule_s_axis_reaches_the_outcome_as_an_interpretation_failure() -> None:
    """``ms`` is a technique and a unit, and a technique rule cannot say which it was."""
    rule = shipped_rulebook().by_id("technique_from_name")
    assert rule is not None
    predicate = PREDICATES.get(rule.predicate)
    conflict = DictionaryBinding(
        binding_id="b:ms",
        component_id="c:ms",
        entry_id="e",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        resolution_status=ResolutionStatus.EXAMINED_CONFLICTING,
        alternative_count=2,
        alternatives=(
            _alternative("mass_spectrometry", "technique"),
            _alternative("millisecond", "unit"),
        ),
    )
    records = EntryRecords(
        entry=_entry(), components=(_component("word", "ms"),), bindings=(conflict,)
    )
    outcome = evaluate_on_entry(rule, predicate, records, reads=Reads.BINDINGS)
    assert outcome.status is ResolutionStatus.EXAMINED_CONFLICTING
    assert outcome.failure_class is FailureClass.INTERPRETATION
    assert outcome.inputs == ("b:ms",)


def test_records_are_assembled_per_entry_and_only_from_the_tables_rules_read(tmp_path) -> None:
    pool = tmp_path / "pool"
    (pool / "exp").mkdir(parents=True)
    (pool / "exp" / "protocol_v2.txt").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")

    with open_catalog(project.path) as catalog:
        reading_bindings = assemble_entry_records(catalog, reads={Reads.BINDINGS})
        reading_entries = assemble_entry_records(catalog, reads={Reads.ENTRY})
        reading_roots = assemble_entry_records(catalog, reads={Reads.ENTRY_IN_ROOT})

    by_name = {records.entry.name: records for records in reading_bindings}
    protocol = by_name["protocol_v2.txt"]
    assert protocol.components
    assert protocol.bindings
    assert {binding.entry_id for binding in protocol.bindings} == {protocol.entry.entry_id}
    assert [records.entry.entry_id for records in reading_bindings] == sorted(
        records.entry.entry_id for records in reading_bindings
    )
    assert all(not records.observations for records in reading_bindings)
    assert all(not records.components and not records.bindings for records in reading_entries)
    assert tables_read({Reads.BINDINGS}) == {"components", "dictionary_bindings"}
    assert tables_read({Reads.ENTRY}) == set()
    assert tables_read({Reads.ENTRY_IN_ROOT}) == {"roots"}
    assert all(records.root is None for records in reading_entries)
    assert all(
        records.root is not None and records.root.root_id == records.entry.root_id
        for records in reading_roots
    )


def test_a_container_member_s_name_was_never_read_so_a_name_rule_is_unexamined() -> None:
    """Members are promoted with a name and never tokenised; no evidence would be a lie."""
    from ingence.core.ids import LocatorStep

    rule = shipped_rulebook().by_id("format_from_extension")
    assert rule is not None
    predicate = PREDICATES.get(rule.predicate)
    member = Entry(
        entry_id="member",
        entry_kind="derived",
        root_id="root",
        relative_path="a/bundle.zip",
        name="inner.csv",
        parent_entry_id="e",
        locator_chain=(LocatorStep(scheme="zip", target="inner.csv"),),
        transformation="zip",
        extension="csv",
        first_seen_run="r",
        last_seen_run="r",
    )
    outcome = evaluate_on_entry(rule, predicate, EntryRecords(entry=member), reads=Reads.BINDINGS)
    assert outcome.status is ResolutionStatus.UNEXAMINED


# ---------------------------------------------------------------------------
# What a grouping rule comes to.
# ---------------------------------------------------------------------------


def test_a_grouping_rule_through_the_pass_keys_an_entry_on_the_records_it_read() -> None:
    rule = shipped_rulebook().by_id("same_name_family")
    assert rule is not None
    predicate = PREDICATES.get(rule.predicate)
    records = EntryRecords(
        entry=_entry(),
        components=(
            _component("word", "alpha"),
            _component("word", "beta"),
            _component("extension", "md"),
        ),
    )
    outcome = key_on_entry(rule, predicate, records, reads=Reads.COMPONENTS)
    direct = predicate.evaluate(records.components, parameters_in_force(rule))
    assert outcome.concluded
    assert outcome.values == (direct.key,)
    assert outcome.inputs == ("c:word:alpha", "c:word:beta")


def test_a_grouping_predicate_answering_anything_but_a_key_is_refused() -> None:
    """A value about one subject is not a class, and reading it as one would invent a key."""
    rule = shipped_rulebook().by_id("same_name_family")
    assert rule is not None

    class Answering:
        name = "answering"
        version = "1"

        def evaluate(self, subject: object, parameters: object) -> object:
            return "yes"

    with pytest.raises(TypeError, match="class key"):
        key_on_entry(rule, Answering(), EntryRecords(entry=_entry()), reads=Reads.COMPONENTS)


def test_an_empty_class_key_is_refused() -> None:
    """Every subject returning one would share one class."""
    with pytest.raises(ValueError, match="non-empty"):
        ClassKey(key="")
