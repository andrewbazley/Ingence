"""The inference pass: rules evaluated over a published run, and what they publish.

These tests run a rule end to end. Other tests evaluate a predicate on records built by hand.
These index a pool, run the pass and read the published run back, because the defects that
matter here are in what reaches a table: a claim at the wrong tier, a defeater never evaluated,
a failure recorded as a claim, or a second inference adding to the first instead of replacing it.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import inspect
import json
from pathlib import Path

import pytest
import yaml

from fixtures.domain_skew import ask_every_tie
from fixtures.laboratory_pool import Groups, expectations_over, write_pool
from fixtures.revision_4 import write_revision
from ingence.core.ids import PROJECT_SCOPE, Scope, ScopeKind, canonical_json
from ingence.core.project import Project
from ingence.core.schema import Defeater, FailureClass, Rule, RuleEffect, RuleFailure, SourceScope
from ingence.core.settings import ExtractionSettings, Settings
from ingence.core.tiers import InferenceTier, ResolutionStatus
from ingence.infer.claims import claim_for, why_not_claim_ready
from ingence.infer.evaluate import Outcome, key_on_entry, parameters_in_force, raise_on_scope
from ingence.infer.records import assemble_entry_records, assemble_scope_records
from ingence.infer.run import (
    SCAN_RULE_PREFIXES,
    SCOPE_LEVELS,
    _no_subject_for,
    applies_at,
    run_inference,
)
from ingence.infer.subjects import ScopeRecords
from ingence.record.workflow import DEFAULT_WORKFLOW, declare_workflow
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.load import load_revision, load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import ENTRY_SHAPED, PREDICATES, Raised, Reads, reads_of
from ingence.rulebook.resolve import EffectiveRulebook, effective_rulebook
from ingence.scan.cancel import CancellationToken
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import read_pointer

SOURCE = Path(__file__).resolve().parents[1] / "src" / "ingence"


def _at_3() -> EffectiveRulebook:
    """The book at revision 3, for the tests whose expectations name rules that revision 4
    supersedes. Each revision stays readable, which is how a past run replays. Every other test
    runs the pass over the rules that ship."""
    load_builtins()
    return effective_rulebook(load_revisions(), at=3)


def _first_four() -> list[object]:
    return [revision for revision in load_revisions() if revision.number <= 3]


def _project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "exp").mkdir(parents=True)
    (pool / "exp" / "results.csv").write_text("sample,value\n1,2\n")
    # JPEG bytes under a text extension: the misnamed file type detection flags.
    (pool / "exp" / "mislabelled.txt").write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x11" * 2000
    )
    (pool / "notes.md").write_text("# Notes\n")
    # A token the Dictionary holds twice, as a technique and as a statistic.
    (pool / "run_sim_readout.csv").write_text("t,v\n0,1\n")
    return Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )


def _run_id(suffix: str) -> str:
    return f"run_20260101T00000{suffix}Z_{suffix * 12}"


def _index(project: Project) -> object:
    project = ask_every_tie(project.path)  # a tied word in a few names is still asked
    return run_census(project, run_id=_run_id("1"), started_at="t")


def _infer(
    project: Project,
    suffix: str,
    token: CancellationToken | None = None,
    rulebook: EffectiveRulebook | None = None,
) -> object:
    return run_inference(
        project, run_id=_run_id(suffix), started_at="t", token=token, rulebook=rulebook
    )


def _rows(project: Project, sql: str) -> list[tuple[object, ...]]:
    with open_catalog(project.path) as catalog:
        return catalog.query(sql).fetchall()


# ---------------------------------------------------------------------------
# End to end.
# ---------------------------------------------------------------------------


def test_a_census_run_publishes_claims_from_the_one_rule_ready_to_license_them(tmp_path) -> None:
    """Every classifying rule runs; only a rule naming its defeaters publishes."""
    project = _project(tmp_path)
    _index(project)
    census_claims = _rows(project, "SELECT count(*) FROM claims")[0][0]
    report = _infer(project, "2", rulebook=_at_3())

    tallies = {tally.rule_id: tally for tally in report.tallies}
    ready = tallies.pop("format_from_extension")
    assert ready.not_claim_ready is None
    assert ready.claims > 0
    assert all(tally.claims == 0 for tally in tallies.values())
    assert all(tally.not_claim_ready or tally.not_evaluable for tally in tallies.values())

    licensed = _rows(
        project,
        "SELECT DISTINCT rule_id, rule_version, inference_tier FROM claims "
        "WHERE rule_id NOT LIKE 'census.%'",
    )
    assert licensed == [("format_from_extension", "2", "defeasible")]
    carried = _rows(project, "SELECT count(*) FROM claims WHERE rule_id LIKE 'census.%'")
    assert carried[0][0] == census_claims
    assert _rows(project, "SELECT count(*) FROM rules")[0][0] == len(_at_3().rules)


def test_an_inference_over_an_inference_reads_only_the_scans_claims(tmp_path, monkeypatch) -> None:
    """Inferring again from an inference loads the scan's claims, not every claim it published.

    When the current run is already an inference, loading every one of its claims before keeping
    the scan's would cost twenty rows for each claim carried. The conclusions are the same either
    way; only the memory differs. What is carried, and so what is published, is the same either way.
    """
    import ingence.infer.run as inference

    project = _project(tmp_path)
    _index(project)
    _infer(project, "2")
    first = _rows(
        project,
        "SELECT claim_id, subject, predicate, object_reference, rule_id, rule_version, "
        "resolution_status FROM claims ORDER BY claim_id",
    )
    assert any(not str(row[4]).startswith(SCAN_RULE_PREFIXES) for row in first)

    loaded: list[str] = []
    # Read by Arrow batch, through the one reader the pass reads claims with.
    real_rows = inference.batched_rows

    def spying(catalog: object, sql: str, parameters: object = None):
        for row in real_rows(catalog, sql, parameters):
            if "claim_id" in row and "rule_id" in row:
                loaded.append(str(row["rule_id"]))
            yield row

    monkeypatch.setattr(inference, "batched_rows", spying)
    _infer(project, "3")
    assert loaded
    assert all(rule_id.startswith(SCAN_RULE_PREFIXES) for rule_id in loaded)
    again = _rows(
        project,
        "SELECT claim_id, subject, predicate, object_reference, rule_id, rule_version, "
        "resolution_status FROM claims ORDER BY claim_id",
    )
    assert again == first


def test_a_claim_on_a_census_names_its_defeater_and_the_defeater_has_not_fired(tmp_path) -> None:
    """Detection never runs in a census, so nothing could have retracted the claim yet."""
    project = _project(tmp_path)
    _index(project)
    _infer(project, "2")
    for defeaters, status in _rows(
        project,
        "SELECT defeaters_json, resolution_status FROM claims "
        "WHERE rule_id = 'format_from_extension'",
    ):
        parsed = json.loads(str(defeaters))
        assert [defeater["rule_id"] for defeater in parsed] == ["extension_contradicts_content"]
        assert not parsed[0]["fired"]
        assert status == "resolved"


def test_a_content_signature_contradicting_the_extension_retracts_the_format_claim(
    tmp_path,
) -> None:
    """The claim is published as conflicting, naming the reading that retracted it."""
    project = _project(tmp_path)
    _index(project)
    run_deepen(project, run_id=_run_id("2"), started_at="t", budget_seconds=60.0)
    _infer(project, "3", rulebook=_at_3())

    query = (
        "SELECT c.resolution_status, c.defeaters_json FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.rule_id = 'format_from_extension' AND e.name = '{name}'"
    )
    misnamed = _rows(project, query.format(name="mislabelled.txt"))
    assert len(misnamed) == 1
    status, defeaters = misnamed[0]
    assert status == "examined_conflicting"
    fired = json.loads(str(defeaters))[0]
    assert fired["fired"]
    assert fired["observations"]

    ordinary = _rows(project, query.format(name="results.csv"))
    assert [row[0] for row in ordinary] == ["resolved"]


def test_a_rule_unable_to_decide_publishes_a_failure_and_no_claim(tmp_path) -> None:
    """The first thing ever to populate the interpretation class."""
    project = _project(tmp_path)
    _index(project)
    report = _infer(project, "2")

    failures = _rows(
        project,
        "SELECT f.rule_id, f.failure_class, e.name FROM rule_failures f "
        "JOIN entries e ON e.entry_id = f.subject",
    )
    assert ("technique_from_name", "interpretation", "run_sim_readout.csv") in failures
    assert report.failures == len(failures)
    assert not _rows(project, "SELECT 1 FROM claims WHERE rule_id = 'technique_from_name'")


def test_a_second_inference_replaces_the_first_rather_than_adding_to_it(tmp_path) -> None:
    project = _project(tmp_path)
    _index(project)
    _infer(project, "2")
    first = _rows(project, "SELECT count(*), count(DISTINCT claim_id) FROM claims")[0]
    _infer(project, "3")
    second = _rows(project, "SELECT count(*), count(DISTINCT claim_id) FROM claims")[0]
    assert first == second
    assert second[0] == second[1]


def test_a_cancelled_inference_publishes_nothing(tmp_path) -> None:
    project = _project(tmp_path)
    census = _index(project)
    token = CancellationToken()
    token.cancel("interrupted before any rule ran")
    report = _infer(project, "2", token=token)
    assert report.cancelled
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == census.run_id  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Grouping by a key.
# ---------------------------------------------------------------------------


def _family_project(tmp_path: Path) -> Project:
    """Two names agreeing on every word and differing in a number, and one name alone."""
    pool = tmp_path / "pool"
    (pool / "first").mkdir(parents=True)
    (pool / "second").mkdir()
    (pool / "first" / "alpha_beta_01.txt").write_text("a")
    (pool / "second" / "alpha_beta_02.txt").write_text("b")
    (pool / "first" / "gamma_delta_03.txt").write_text("c")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


#: The name family declared again by a later revision that names a defeater, so it
#: may license claims. A test revision: no shipped grouping rule names one.
_CLAIM_READY_FAMILY: dict[str, object] = {
    "revision": 99,
    "summary": "The name family, naming what would defeat its claims.",
    "rules": [
        {
            "id": "same_name_family",
            "version": "2",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "name_family",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["extension_contradicts_content"],
            "statement": (
                "Two entries share a name family when they agree on every component of "
                "kind {component_kinds} and each carries {minimum_components} or more of them."
            ),
            "rationale": "A test revision, so that the family may license claims.",
            "parameters": [
                {"name": "component_kinds", "type": "string_list", "default": ["word"]},
                {
                    "name": "minimum_components",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 20,
                },
            ],
        }
    ],
}


def _rulebook_with(tmp_path: Path, declaration: dict[str, object]) -> EffectiveRulebook:
    load_builtins()
    path = tmp_path / "revision.yaml"
    path.write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")
    return effective_rulebook([*_first_four(), load_revision(path)])  # type: ignore[list-item]


def _identifiers(value: object) -> list[str]:
    """Return a list column as a list, however the catalog hands it back."""
    if isinstance(value, str):
        return [str(item) for item in json.loads(value)]
    return [str(item) for item in value]  # type: ignore[attr-defined]


#: Observed metadata compared on name and size alone, which a test can set, where the
#: shipped rule also compares clocks a test cannot hold still. A test revision.
_NAME_AND_SIZE: dict[str, object] = {
    "revision": 99,
    "summary": "Observed metadata compared on name and size.",
    "rules": [
        {
            "id": "same_observed_metadata",
            "version": "2",
            "predicate": "same_observed_metadata",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "observed_metadata",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "Two entries are equivalent in respect observed metadata when they agree on "
                "all of {compared_facts}."
            ),
            "rationale": "A test revision comparing what a test can set.",
            "parameters": [
                {
                    "name": "compared_facts",
                    "type": "string_list",
                    "default": ["name", "size_bytes"],
                }
            ],
        }
    ],
}


def test_observed_metadata_is_read_beside_each_entry_s_root_and_forms_its_classes(
    tmp_path,
) -> None:
    pool = tmp_path / "pool"
    for folder in ("first", "second"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "plate.tif").write_bytes(b"x" * 64)
    (pool / "first" / "other.tif").write_bytes(b"x" * 64)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _index(project)

    rulebook = _rulebook_with(tmp_path, _NAME_AND_SIZE)
    report = run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

    metadata = {tally.rule_id: tally for tally in report.groupings}["same_observed_metadata"]
    assert metadata.not_evaluable is None
    assert (metadata.classes, metadata.grouped, metadata.largest) == (1, 2, 2)


def test_a_family_forms_a_class_that_is_counted_where_the_rule_may_not_publish(tmp_path) -> None:
    project = _family_project(tmp_path)
    _index(project)
    report = _infer(project, "2", rulebook=_at_3())

    family = {tally.rule_id: tally for tally in report.groupings}["same_name_family"]
    assert (family.classes, family.grouped, family.largest) == (1, 2, 2)
    assert family.claims == 0
    assert "names nothing" in str(family.not_claim_ready)
    assert not _rows(project, "SELECT 1 FROM claims WHERE predicate LIKE 'groups.%'")


def test_each_member_of_a_class_is_claimed_into_it_and_an_entry_alone_is_not(tmp_path) -> None:
    project = _family_project(tmp_path)
    _index(project)
    rulebook = _rulebook_with(tmp_path, _CLAIM_READY_FAMILY)
    report = run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

    claimed = _rows(
        project,
        "SELECT e.name, c.object_reference, c.inference_tier, c.rule_version, c.inputs, "
        "c.subject FROM claims c JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.predicate = 'groups.same_name_family' ORDER BY e.name",
    )
    assert [row[0] for row in claimed] == ["alpha_beta_01.txt", "alpha_beta_02.txt"]
    assert len({row[1] for row in claimed}) == 1
    assert {(row[2], row[3]) for row in claimed} == {("defeasible", "2")}
    family = {tally.rule_id: tally for tally in report.groupings}["same_name_family"]
    assert family.claims == 2
    # Each claim rests on its own entry's components: the INV-7 check in
    # ``core/invariants.py`` allows no fact to pass between entries except across ``identical``.
    owners = dict(_rows(project, "SELECT component_id, entry_id FROM components"))
    for *_, inputs, subject in claimed:
        assert inputs
        assert {owners[identifier] for identifier in _identifiers(inputs)} == {subject}


# ---------------------------------------------------------------------------
# The name rules' filters, on the generated laboratory pool.
# ---------------------------------------------------------------------------

#: The name family and the condition redeclared with a filter each, naming a defeater so that
#: their classes are claimed and can be read back. A test revision: no shipped rule sets either.
_FILTERED_NAME_RULES: dict[str, object] = {
    "revision": 99,
    "summary": "The name family and the condition, counting words by what they mean.",
    "rules": [
        {
            "id": "same_name_family",
            "version": "2",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "name_family",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["extension_contradicts_content"],
            "statement": (
                "Two entries share a name family when they agree on every component of kind "
                "{component_kinds}, leaving out a word bound to a sense of kind "
                "{ignored_theme_kinds}, and each carries {minimum_components} or more of them."
            ),
            "rationale": "A test revision, so that the filtered family is claimed.",
            "parameters": [
                {"name": "component_kinds", "type": "string_list", "default": ["word"]},
                {"name": "ignored_theme_kinds", "type": "string_list", "default": ["stage"]},
                {
                    "name": "minimum_components",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 20,
                },
            ],
        },
        {
            "id": "same_experimental_condition",
            "version": "2",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "specialization": "same_condition_as",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["extension_contradicts_content"],
            "statement": (
                "Two entries are the same condition when they agree on every component of kind "
                "{component_kinds}, counting a word only where it is bound to a sense of kind "
                "{counted_theme_kinds}, and each carries {minimum_components} or more of them."
            ),
            "rationale": "A test revision, so that the filtered condition is claimed.",
            "parameters": [
                {
                    "name": "component_kinds",
                    "type": "string_list",
                    "default": ["quantity", "word"],
                },
                {
                    "name": "counted_theme_kinds",
                    "type": "string_list",
                    "default": ["condition", "reagent"],
                },
                {
                    "name": "minimum_components",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 20,
                },
            ],
        },
    ],
}


def _pool_project(tmp_path: Path) -> tuple[Project, tuple[str, ...]]:
    """The generated laboratory pool, indexed, and the members the platform let it write."""
    pool = write_pool(tmp_path / "pool")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool.root])
    _index(project)
    return project, pool.written


def test_with_no_filter_declared_the_shipped_name_rules_key_the_pool_as_before(tmp_path) -> None:
    """The predicate's keys stay the same until a rule sets a filter, a folder or ordered kinds.

    The version is pinned so that a bump is a deliberate act with a test read behind it. Version
    4 lets ``ignored_theme_kinds`` read an indexed component's label, which changes no
    shipped key, because a label binds only replicate senses and no shipped rule ignores that
    kind. Version 5 keeps the positions of the kinds a rule names, which changes no
    shipped key either, because no shipped rule names one. Version 6 may compare names within an
    experiment, which changes no shipped key, because no shipped rule asks it to.
    """
    project, _ = _pool_project(tmp_path)
    load_builtins()
    registration = PREDICATES.declaration("same_component_values")
    assert (registration.version, reads_of(registration)) == ("6", Reads.RECORDS)
    predicate = PREDICATES.get("same_component_values")
    rules = [rule for rule in _at_3().rules if rule.predicate == "same_component_values"]
    assert len(rules) == 4
    filtered = {
        rule.rule_id
        for rule in rules
        if {"ignored_theme_kinds", "counted_theme_kinds"} & set(parameters_in_force(rule))
    }
    # Revision 3 keeps the condition to the words that name one. The other three
    # declare no filter, and they must key exactly as version 1 did.
    assert filtered == {"same_experimental_condition"}
    with open_catalog(project.path) as catalog:
        records = assemble_entry_records(catalog, reads=ENTRY_SHAPED)

    keyed = 0
    for rule in rules:
        if rule.rule_id in filtered:
            continue
        parameters = parameters_in_force(rule)
        kinds = set(parameters["component_kinds"])
        for entry_records in records:
            if not applies_at(rule.level, entry_records.entry):
                continue
            named = {
                (component.component_type, component.normalized_value)
                for component in entry_records.components
                if component.component_type in kinds and component.source_scope is SourceScope.NAME
            }
            as_before = (
                canonical_json(sorted(named))
                if named and len(named) >= int(parameters["minimum_components"])
                else None
            )
            outcome = key_on_entry(rule, predicate, entry_records, reads=Reads.RECORDS)
            assert (outcome.values[0] if outcome.concluded else None) == as_before
            keyed += outcome.concluded
    assert keyed


def test_the_pool_forms_the_classes_it_was_written_to_show_under_each_filter(tmp_path) -> None:
    """Stage words left out of a family, and a condition kept to its own words."""
    project, written = _pool_project(tmp_path)
    rulebook = _rulebook_with(tmp_path, _FILTERED_NAME_RULES)
    run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

    rows = _rows(
        project,
        "SELECT c.predicate, c.object_reference, e.relative_path FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject WHERE c.predicate IN "
        "('groups.same_name_family', 'groups.same_experimental_condition')",
    )
    formed: dict[tuple[str, str], set[str]] = {}
    for predicate, reference, path in rows:
        members = formed.setdefault((str(predicate), str(reference)), set())
        members.add(str(path).replace("\\", "/"))
    claimed = {
        (predicate.removeprefix("groups."), frozenset(members))
        for (predicate, _), members in formed.items()
    }
    expected = {
        (expectation.rule, expectation.members)
        for expectation in expectations_over(written)
        if isinstance(expectation, Groups) and expectation.parameters
    }
    assert claimed == expected


#: The name family compared within one folder, naming a defeater so that its classes are
#: claimed. A test revision: no shipped rule compares names within a folder.
_FAMILY_WITHIN_A_FOLDER: dict[str, object] = {
    "revision": 99,
    "summary": "The name family, compared within one folder.",
    "rules": [
        {
            "id": "same_name_family",
            "version": "2",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "name_family",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["extension_contradicts_content"],
            "statement": (
                "Two entries share a name family when, compared within one {compared_within}, "
                "they agree on every component of kind {component_kinds} and each carries "
                "{minimum_components} or more of them."
            ),
            "rationale": "A test revision, so that a family kept to its folder is claimed.",
            "parameters": [
                {"name": "component_kinds", "type": "string_list", "default": ["word"]},
                {
                    "name": "compared_within",
                    "type": "enum",
                    "default": "folder",
                    "choices": ["pool", "folder"],
                },
                {
                    "name": "minimum_components",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 20,
                },
            ],
        }
    ],
}


def test_a_family_compared_within_a_folder_claims_only_names_sharing_one(tmp_path) -> None:
    """Names sharing a family in two folders are claimed only where two sit in one folder."""
    pool = tmp_path / "pool"
    for relative in (
        "first/alpha_beta_01.txt",
        "first/alpha_beta_02.txt",
        "second/alpha_beta_03.txt",
    ):
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _index(project)
    rulebook = _rulebook_with(tmp_path, _FAMILY_WITHIN_A_FOLDER)
    report = run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

    family = {tally.rule_id: tally for tally in report.groupings}["same_name_family"]
    assert (family.classes, family.grouped, family.largest) == (1, 2, 2)
    claimed = _rows(
        project,
        "SELECT e.relative_path, c.inputs, c.subject FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.predicate = 'groups.same_name_family' ORDER BY e.relative_path",
    )
    paths = [str(path).replace("\\", "/") for path, _, _ in claimed]
    assert paths == ["first/alpha_beta_01.txt", "first/alpha_beta_02.txt"]
    # Each claim cites its own entry, whose parent is half of why its key is what it is.
    assert all(subject in _identifiers(inputs) for _, inputs, subject in claimed)


#: The acquisition stage concluded from an instrument's own format, naming a defeater so that
#: it is claimed. A test revision: no shipped rule names its conclusion.
_ACQUISITION_STAGE: dict[str, object] = {
    "revision": 99,
    "summary": "The acquisition stage, concluded from an instrument's own format.",
    "rules": [
        {
            "id": "acquisition_stage_from_format",
            "version": "1",
            "predicate": "class_from_format_origin",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["extension_contradicts_content"],
            "statement": (
                "A file whose extension binds to one of {senses}, matched at quality "
                "{minimum_match_quality} or better, in a spelling written by {written_by}, sits "
                "at the {concludes} stage."
            ),
            "rationale": "A test revision, so that a named conclusion is claimed.",
            "parameters": [
                {"name": "senses", "type": "string_list", "default": ["image_format"]},
                {"name": "written_by", "type": "string_list", "default": ["instrument"]},
                {"name": "concludes", "type": "string", "default": "acquisition"},
                {
                    "name": "minimum_match_quality",
                    "type": "enum",
                    "default": "exact",
                    "choices": ["exact", "case_folded", "loose"],
                },
            ],
        }
    ],
}


def test_a_named_conclusion_is_claimed_resting_on_the_binding_that_licensed_it(tmp_path) -> None:
    """The claim names the stage and cites the format binding on its own entry.

    It also reads who writes the spelling: the ND2 and the TIFF bind one image type,
    and only the instrument's spelling begins a workflow.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_01.nd2").write_bytes(b"\x00" * 64)
    (pool / "plate_01_max.tif").write_bytes(b"\x00" * 64)
    (pool / "plate_01.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _index(project)
    rulebook = _rulebook_with(tmp_path, _ACQUISITION_STAGE)
    run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

    claimed = _rows(
        project,
        "SELECT e.name, c.object_reference, c.resolution_status FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.rule_id = 'acquisition_stage_from_format'",
    )
    assert claimed == [("plate_01.nd2", "acquisition", "resolved")]
    cited = _rows(
        project,
        "SELECT b.entry_id = c.subject, b.term FROM "
        "(SELECT subject, unnest(inputs) AS input FROM claims "
        "WHERE rule_id = 'acquisition_stage_from_format') c "
        "JOIN dictionary_bindings b ON b.binding_id = c.input",
    )
    assert cited == [(True, "image_format")]


#: Filesystem noise read from the whole name and either end of it, never from an office
#: suite's lock-file prefix, and naming a defeater so that it is claimed. A test revision.
_NOISE_FROM_SPELLING: dict[str, object] = {
    "revision": 99,
    "summary": "Filesystem noise, read from how a whole name or either end of it is spelled.",
    "rules": [
        {
            "id": "filesystem_noise_from_name",
            "version": "2",
            "predicate": "sense_from_name_spelling",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["extension_contradicts_content"],
            "statement": (
                "An entry whose name, read as {positions}, is spelled as one of {senses}, "
                "never reading {excluded_surfaces}, was made by a tool rather than a person."
            ),
            "rationale": "A test revision, so that noise read from a spelling is claimed.",
            "parameters": [
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": ["windows_metadata", "editor_backup", "archive_helper"],
                },
                {
                    "name": "positions",
                    "type": "string_list",
                    "default": ["whole", "prefix", "suffix"],
                },
                {"name": "excluded_surfaces", "type": "string_list", "default": ["~$"]},
            ],
        }
    ],
}


def test_noise_is_read_from_a_whole_name_and_its_ends_and_the_run_records_its_dictionary(
    tmp_path,
) -> None:
    """The census keeps no affix as a component, so the spelling is read off the name."""
    pool = tmp_path / "pool"
    pool.mkdir()
    for name in ("Thumbs.db", "table.csv.part", "~$table.csv", "table.csv"):
        (pool / name).write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _index(project)
    rulebook = _rulebook_with(tmp_path, _NOISE_FROM_SPELLING)
    run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

    claimed = _rows(
        project,
        "SELECT e.name, c.object_reference, c.inputs = [c.subject] FROM claims c "
        "JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.rule_id = 'filesystem_noise_from_name' ORDER BY e.name",
    )
    assert claimed == [
        ("Thumbs.db", "windows_metadata", True),
        ("table.csv.part", "archive_helper", True),
    ]
    (dictionary,) = _rows(project, "SELECT dictionary_hash FROM run")[0]
    assert dictionary


# ---------------------------------------------------------------------------
# What a claim promises about its rule.
# ---------------------------------------------------------------------------


def test_only_the_census_and_the_claim_pass_construct_a_claim() -> None:
    """One place builds a rule's claims, which is what lets the tier ceiling be kept."""
    constructing = sorted(
        {
            path.relative_to(SOURCE).as_posix()
            for path in SOURCE.rglob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Claim"
        }
    )
    assert constructing == ["infer/claims.py", "scan/census.py"]


def test_only_the_claim_pass_constructs_a_finding() -> None:
    """One place builds what a flagging rule raises, beside what else a rule publishes."""
    constructing = sorted(
        {
            path.relative_to(SOURCE).as_posix()
            for path in SOURCE.rglob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Finding"
        }
    )
    assert constructing == ["infer/claims.py"]


# ---------------------------------------------------------------------------
# Flags: a finding raised, and never a claim.
# ---------------------------------------------------------------------------


def test_a_token_the_dictionary_holds_twice_is_raised_as_a_finding_and_never_a_claim(
    tmp_path,
) -> None:
    """The finding names the token and its entry, and cites the binding that could not choose."""
    project = _project(tmp_path)
    _index(project)
    report = _infer(project, "2", rulebook=_at_3())
    tally = next(tally for tally in report.flags if tally.rule_id == "binding_is_ambiguous")
    assert tally.findings >= 1
    rows = _rows(
        project,
        "SELECT e.name, f.raised, f.subject_kind, f.inputs FROM findings f "
        "JOIN entries e ON e.entry_id = f.subject WHERE f.rule_id = 'binding_is_ambiguous'",
    )
    raised = {name: (list(values), kind, list(inputs)) for name, values, kind, inputs in rows}
    values, kind, inputs = raised["run_sim_readout.csv"]
    assert (values, kind, len(inputs)) == (["sim"], "entry", 1)
    cited = _rows(
        project,
        "SELECT DISTINCT b.entry_id = f.subject, b.resolution_status FROM "
        "(SELECT subject, unnest(inputs) AS input FROM findings "
        "WHERE rule_id = 'binding_is_ambiguous') f "
        "JOIN dictionary_bindings b ON b.binding_id = f.input",
    )
    assert cited == [(True, "examined_conflicting")]
    assert _rows(project, "SELECT count(*) FROM claims WHERE predicate LIKE 'flags.%'") == [(0,)]


def test_a_folder_whose_own_name_repudiates_its_contents_is_raised(tmp_path) -> None:
    """repudiated_by_name names its terms, so it runs, and it reads the folder's own name."""
    pool = tmp_path / "pool"
    (pool / "broken_exports").mkdir(parents=True)
    (pool / "broken_exports" / "table.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _index(project)
    _infer(project, "2")
    rows = _rows(
        project,
        "SELECT e.name, f.raised FROM findings f JOIN entries e ON e.entry_id = f.subject "
        "WHERE f.rule_id = 'repudiated_by_name'",
    )
    assert [(name, list(raised)) for name, raised in rows] == [("broken_exports", ["broken"])]


def test_a_flag_reading_a_group_a_scope_or_a_clock_says_why_it_is_not_evaluable(tmp_path) -> None:
    """Every flag that cannot run says which input it lacks, and the three that read a folder
    lack none: the scope, its failure rate and its unbound rate all exist."""
    project = _project(tmp_path)
    _index(project)
    report = _infer(project, "2", rulebook=_at_3())
    reasons = {tally.rule_id: tally.not_evaluable for tally in report.flags}
    assert "a group" in (reasons["replication_below_expected"] or "")
    assert reasons["access_failure_is_concentrated"] is None
    assert reasons["comprehension_failure_is_concentrated"] is None
    assert reasons["vocabulary_unread_in_folder"] is None
    assert "event_time" in (reasons["clocks_disagree"] or "")
    assert "names none" in (reasons["authored_name_doubt"] or "")
    assert reasons["binding_is_ambiguous"] is None
    assert reasons["repudiated_by_name"] is None


def test_findings_are_computed_again_by_every_inference_and_never_accumulate(tmp_path) -> None:
    project = _project(tmp_path)
    _index(project)
    _infer(project, "2")
    first = _rows(project, "SELECT finding_id, run_id FROM findings ORDER BY finding_id")
    _infer(project, "3")
    second = _rows(project, "SELECT finding_id, run_id FROM findings ORDER BY finding_id")
    assert first
    assert [finding for finding, _ in first] == [finding for finding, _ in second]
    assert {run for _, run in first} != {run for _, run in second}


def test_a_claim_takes_exactly_the_tier_its_rule_declares() -> None:
    """There is no argument through which a caller could ask for another."""
    rule = _at_3().by_id("format_from_extension")
    assert rule is not None
    outcome = Outcome(
        rule_id=rule.rule_id,
        subject="entry_x",
        status=ResolutionStatus.RESOLVED,
        values=("markdown",),
        inputs=("binding_x",),
    )
    defeater = Defeater(reason="x", rule_id="extension_contradicts_content", rule_version="1")
    claim = claim_for(rule, outcome, "markdown", run_id="run_x", defeaters=(defeater,))
    assert claim.inference_tier is rule.tier
    assert not [name for name in inspect.signature(claim_for).parameters if "tier" in name]
    with pytest.raises(ValueError, match="did not conclude"):
        claim_for(rule, outcome, "not_concluded", run_id="run_x", defeaters=(defeater,))


def test_a_retractable_rule_licenses_a_claim_only_while_its_defeaters_are_in_force() -> None:
    book = _at_3()
    in_force = {rule.rule_id for rule in book.rules}
    fmt = book.by_id("format_from_extension")
    stage = book.by_id("stage_from_name")
    entailed = book.by_id("identity_requires_a_content_digest")
    assert fmt is not None and stage is not None and entailed is not None
    assert why_not_claim_ready(fmt, in_force) is None
    assert "names nothing" in str(why_not_claim_ready(stage, in_force))
    switched_off = in_force - {"extension_contradicts_content"}
    assert "not in force" in str(why_not_claim_ready(fmt, switched_off))
    assert why_not_claim_ready(entailed, in_force) is None


@pytest.mark.parametrize("failure_class", [FailureClass.ACCESS, FailureClass.COMPREHENSION])
def test_a_rule_failure_refuses_the_classes_the_scan_layer_records(
    failure_class: FailureClass,
) -> None:
    with pytest.raises(ValueError, match="scan layer"):
        RuleFailure(
            failure_id="failure_x",
            run_id="run_x",
            rule_id="rule_x",
            rule_version="1",
            scope=PROJECT_SCOPE,
            subject="entry_x",
            failure_class=failure_class,
        )


def test_a_rule_neither_defeats_itself_nor_names_defeaters_where_nothing_can_be_retracted() -> None:
    base: dict[str, object] = {
        "rule_id": "example",
        "version": "1",
        "statement": "A thing.",
        "scope": PROJECT_SCOPE,
        "predicate": "format_from_extension",
        "effect": RuleEffect.CLASSIFIES,
        "rationale": "Because.",
    }
    with pytest.raises(ValueError, match="itself"):
        Rule(**base, defeated_by=("example",))
    with pytest.raises(ValueError, match="twice"):
        Rule(**base, defeated_by=("other", "other"))
    with pytest.raises(ValueError, match="cannot be retracted"):
        Rule(**base, tier=InferenceTier.ENTAILED, defeated_by=("other",))
    assert Rule(**base, defeated_by=("other",)).defeated_by == ("other",)


# ---------------------------------------------------------------------------
# A scope: one region of the pool, for a fact no single file carries.
# ---------------------------------------------------------------------------


def _unplaced_label_pool(tmp_path: Path) -> Project:
    """Two folders, each holding one file per index of a label the Dictionary places nowhere.

    ``qq`` binds nothing whole and nothing as a label, which is checked rather than assumed by
    the test that reads the bindings back.
    """
    pool = tmp_path / "pool"
    for folder in ("first", "second"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"{folder}_qq{index}.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    return project


def test_a_scope_holds_every_entry_under_its_root_and_never_a_second_copy(tmp_path) -> None:
    """A scope is a view over the records the pass already assembled, not a copy of them."""
    project = _project(tmp_path)
    _index(project)
    with open_catalog(project.path) as catalog:
        records = assemble_entry_records(catalog, reads={Reads.BINDINGS})
    scopes = assemble_scope_records(records)

    roots = [scope for scope in scopes if scope.scope.kind is ScopeKind.ROOT]
    assert len(roots) == 1
    scope = roots[0]
    assert scope.token == scope.scope.token
    assert len(scope.entries) == len(records)
    assert all(held is assembled for held, assembled in zip(scope.entries, records, strict=True))
    assert scope.owns() >= {records[0].entry.entry_id}


def test_a_folder_scope_holds_what_sits_in_it_and_not_what_sits_below_it(tmp_path) -> None:
    """A root scope exists to see across folders and a folder scope to localise. Counting
    every descendant into a folder's rate would raise every ancestor of a bad folder too,
    which is a hundred findings rather than one."""
    project = _project(tmp_path)
    _index(project)
    with open_catalog(project.path) as catalog:
        records = assemble_entry_records(catalog, reads={Reads.BINDINGS})
    scopes = assemble_scope_records(records)
    folders = {scope.scope.token: scope for scope in scopes if scope.scope.kind is ScopeKind.FOLDER}
    by_id = {held.entry.entry_id: held.entry for held in records}

    # The pool's root folder holds "exp", "notes.md" and one more file; "exp" holds two.
    assert len(folders) == 2
    held = {
        tuple(sorted(by_id[records.entry.entry_id].name for records in scope.entries))
        for scope in folders.values()
    }
    assert ("mislabelled.txt", "results.csv") in held
    assert ("exp", "notes.md", "run_sim_readout.csv") in held

    # Every entry a folder scope holds names that folder as its parent, and the entries are
    # the same objects the root scope and the earlier phases were handed.
    assembled = {id(held) for held in records}
    for token, scope in folders.items():
        parent = token.split(":", 1)[1]
        assert all(held.entry.parent_entry_id == parent for held in scope.entries)
        assert all(id(held) in assembled for held in scope.entries)


def test_a_label_the_dictionary_misses_is_raised_about_the_root_and_not_about_a_file(
    tmp_path,
) -> None:
    """The finding names the label, the region it recurs in, and the components carrying it."""
    project = _unplaced_label_pool(tmp_path)
    _index(project)
    report = run_inference(project, run_id=_run_id("2"), started_at="t")

    tally = next(
        tally for tally in report.flags if tally.rule_id == "indexed_label_may_mark_replicates"
    )
    assert tally.not_evaluable is None
    assert tally.findings == 1

    rows = _rows(
        project,
        "SELECT subject, subject_kind, raised, reason, inputs FROM findings "
        "WHERE rule_id = 'indexed_label_may_mark_replicates'",
    )
    (subject, kind, raised, reason, inputs) = rows[0]
    assert (list(raised), str(kind)) == (["qq"], "scope")
    assert str(subject).startswith("root:")
    assert reason == "qq runs 1 to 2 across 2 folders and 4 files"

    # The subject is a region and never one of the files, which is what makes it one question.
    assert not _rows(project, f"SELECT 1 FROM entries WHERE entry_id = '{subject}'")
    carried = _rows(
        project,
        "SELECT DISTINCT c.normalized_value FROM components c WHERE c.component_id IN "
        "(SELECT unnest(inputs) FROM findings WHERE rule_id = 'indexed_label_may_mark_replicates')",
    )
    assert sorted(str(value) for (value,) in carried) == ["qq1", "qq2"]
    assert len(_identifiers(inputs)) == 4


def test_a_label_the_dictionary_places_somewhere_is_never_raised(tmp_path) -> None:
    """``nd2`` binds a vendor format on its whole text, so its label is a settled question."""
    pool = tmp_path / "pool"
    for folder in ("first", "second"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"{folder}_nd{index}.nd2").write_bytes(b"x" * 64)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    _index(project)
    report = run_inference(project, run_id=_run_id("2"), started_at="t")

    tally = next(
        tally for tally in report.flags if tally.rule_id == "indexed_label_may_mark_replicates"
    )
    assert (tally.not_evaluable, tally.findings) == (None, 0)


def test_a_flag_reading_a_scope_at_a_level_nothing_assembles_says_which_are_assembled() -> None:
    """A rule ahead of the pass and a rule asking for the wrong region must not read alike.

    The pass assembles both levels the shipped flags declare, so the refusal is exercised
    against a rule declaring one that is not: ``project`` is a real scope kind and the pass
    builds no subject for it.
    """
    rule = Rule(
        rule_id="a_whole_project_is_raised",
        version="1",
        predicate="vocabulary_unread_in_scope",
        effect=RuleEffect.FLAGS,
        tier=InferenceTier.DEFEASIBLE,
        level="project",
        statement="A project is raised.",
        rationale="A level the pass assembles no scope for.",
        scope=PROJECT_SCOPE,
    )
    reason = str(_no_subject_for(rule, Reads.SCOPE))
    assert "a scope at level 'project'" in reason
    assert "'folder', 'root'" in reason


def test_every_level_the_pass_assembles_a_scope_for_is_one_a_rule_may_declare() -> None:
    """The levels and the kinds are one mapping, so a level admitted at load and a level the
    pass builds nothing for cannot drift apart."""
    assert set(SCOPE_LEVELS) == {"root", "folder"}
    assert set(SCOPE_LEVELS.values()) == {ScopeKind.ROOT, ScopeKind.FOLDER}


def test_a_finding_about_a_scope_may_not_cite_a_record_from_outside_it() -> None:
    """The check one entry away, widened: a region's finding rests on that region's records."""

    class _Foreign:
        name = "foreign"
        version = "1"

        def evaluate(self, subject: object, parameters: object) -> object:
            return Raised(values=("qq",), inputs=("component_from_another_root",))

    rule = shipped_rulebook().by_id("repudiated_by_name")
    assert rule is not None
    scope = assemble_scope_records(())
    empty = ScopeRecords(scope=Scope(ScopeKind.ROOT, "root_x"))
    assert scope == ()
    with pytest.raises(ValueError, match="no entry in that scope carries"):
        raise_on_scope(rule, _Foreign(), empty)


# ---------------------------------------------------------------------------
# A name that states a count, read against the workflow in force.
# ---------------------------------------------------------------------------


def _stated_count_pool(tmp_path: Path, *, declared: bool = True) -> Project:
    """One derivation and one observation, each stating a count of three in its name.

    ``analysis_N=3.csv`` is placed at the analysis stage by its name and ``raw_N=3.csv`` at
    the raw stage, so one file sits past the rung where data enters and one sits at it. The
    workflow is declared after the census, because a decision is made against a run a person
    was looking at and there is no run to look at before one.
    """
    pool = tmp_path / "pool"
    pool.mkdir(parents=True)
    (pool / "kelt_analysis_N=3.csv").write_text("a,b\n1,2\n")
    (pool / "kelt_raw_N=3.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    _index(project)
    if declared:
        declare_workflow(
            project.path,
            DEFAULT_WORKFLOW,
            actor="robin",
            occurred_at="2026-09-20T00:00:00Z",
            against_run_id=_run_id("1"),
        )
    return project


def _stated_counts(project: Project) -> dict[str, list[str]]:
    """What the rule raised about each file, by the file's own name."""
    rows = _rows(
        project,
        "SELECT e.name, f.raised FROM findings f JOIN entries e ON e.entry_id = f.subject "
        "WHERE f.rule_id = 'stated_count_may_mark_replicates'",
    )
    return {str(name): _identifiers(raised) for name, raised in rows}


def test_a_stated_count_is_read_as_a_cardinal_on_a_derivation_and_an_ordinal_on_raw_data(
    tmp_path,
) -> None:
    """The whole of tier 1, end to end: the equals sign joins, the label binds a count, and
    the flag reads the stage claim the classifying phase published."""
    project = _stated_count_pool(tmp_path)
    report = run_inference(project, run_id=_run_id("2"), started_at="t")

    tally = next(
        tally for tally in report.flags if tally.rule_id == "stated_count_may_mark_replicates"
    )
    assert tally.not_evaluable is None
    assert tally.findings == 2
    assert _stated_counts(project) == {
        "kelt_analysis_N=3.csv": ['{"count":3,"reading":"cardinal"}'],
        "kelt_raw_N=3.csv": ['{"count":3,"reading":"ordinal"}'],
    }


def test_a_stated_count_is_raised_about_the_file_and_cites_the_binding_that_said_so(
    tmp_path,
) -> None:
    """A finding may cite only records its own subject carries, and the record here is
    the binding that made the label a count."""
    project = _stated_count_pool(tmp_path)
    run_inference(project, run_id=_run_id("2"), started_at="t")

    rows = _rows(
        project,
        "SELECT f.subject_kind, b.term, b.constraint FROM findings f "
        "JOIN dictionary_bindings b ON b.binding_id IN (SELECT unnest(f.inputs)) "
        "WHERE f.rule_id = 'stated_count_may_mark_replicates'",
    )
    assert len(rows) == 2
    assert {(str(kind), str(term), str(constraint)) for kind, term, constraint in rows} == {
        ("entry", "stated_replicate_count", "name:count")
    }


def test_a_project_with_no_declared_workflow_gets_the_uncertain_reading_everywhere(
    tmp_path,
) -> None:
    """ "This laboratory never said" and "this laboratory said the default" are different
    facts, and nothing here turns the first into the second."""
    project = _stated_count_pool(tmp_path, declared=False)
    run_inference(project, run_id=_run_id("2"), started_at="t")
    assert _stated_counts(project) == {
        "kelt_analysis_N=3.csv": ['{"count":3,"reading":"ordinal"}'],
        "kelt_raw_N=3.csv": ['{"count":3,"reading":"ordinal"}'],
    }


def test_a_script_stating_a_count_is_a_cardinal_with_no_workflow_declared(tmp_path) -> None:
    """End to end: ``program_from_extension`` outranks the missing workflow."""
    pool = tmp_path / "pool"
    pool.mkdir(parents=True)
    (pool / "kelt_vorn_N=3.py").write_text("print(1)\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    _index(project)
    run_inference(project, run_id=_run_id("2"), started_at="t")
    assert _stated_counts(project) == {
        "kelt_vorn_N=3.py": ['{"count":3,"reading":"cardinal"}'],
    }


# ---------------------------------------------------------------------------
# A folder: the region that the three folder flags read.
# ---------------------------------------------------------------------------


#: The unread-vocabulary rule as revision 4 declares it, naming the program scope.
_UNREAD = "vocabulary_unread_in_folder_outside_programs"


def _legibility_pool(tmp_path: Path) -> Project:
    """Two folders, one whose words the Dictionary holds and one whose words nobody has taught it.

    The illegible half is spelled from invented strings, which is what a fixture is built
    from: a corpus generated over the grammar is exhaustive where a harvested one is sampled.
    """
    pool = tmp_path / "pool"
    (pool / "legible").mkdir(parents=True)
    (pool / "illegible").mkdir(parents=True)
    for index in range(1, 9):
        (pool / "legible" / f"raw_processed_replicate_{index}.csv").write_text("a,b\n1,2\n")
        (pool / "illegible" / f"zorbex_quillan_vramp_{index}.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    return project


def _raised_folders(project: Project, rule_id: str) -> dict[str, str]:
    """What a folder-level flag raised, by the name of the folder it was raised about."""
    rows = _rows(
        project,
        "SELECT e.name, f.reason FROM findings f "
        "JOIN entries e ON 'folder:' || e.entry_id = f.subject "
        f"WHERE f.rule_id = '{rule_id}' AND f.subject_kind = 'scope'",
    )
    return {str(name): str(reason) for name, reason in rows}


def test_a_folder_whose_words_nobody_taught_the_engine_is_raised_and_its_neighbour_is_not(
    tmp_path,
) -> None:
    """The rule needs a per-scope unbound rate, and a folder scope supplies one.
    A pool-wide figure is a number nobody can act on; this one names a folder."""
    project = _legibility_pool(tmp_path)
    _index(project)
    report = run_inference(project, run_id=_run_id("2"), started_at="t")

    tally = next(
        tally
        for tally in report.flags
        if tally.rule_id == "vocabulary_unread_in_folder_outside_programs"
    )
    assert tally.not_evaluable is None
    raised = _raised_folders(project, _UNREAD)
    assert list(raised) == ["illegible"]
    assert "reached no sense" in raised["illegible"]


def test_a_folder_finding_is_about_the_folder_and_cites_the_bindings_that_found_nothing(
    tmp_path,
) -> None:
    """A finding about a region may cite only records the entries in that region carry."""
    project = _legibility_pool(tmp_path)
    _index(project)
    run_inference(project, run_id=_run_id("2"), started_at="t")

    rows = _rows(
        project,
        f"SELECT f.subject, f.raised, f.inputs FROM findings f WHERE f.rule_id = '{_UNREAD}'",
    )
    assert len(rows) == 1
    (subject, raised, inputs) = rows[0]
    assert str(subject).startswith("folder:")
    assert list(raised) == ["word_occurrences"]
    held = _rows(
        project,
        "SELECT count(DISTINCT c.entry_id) FROM dictionary_bindings b "
        "JOIN components c ON c.component_id = b.component_id "
        "WHERE b.binding_id IN (SELECT unnest(inputs) FROM findings "
        f"WHERE rule_id = '{_UNREAD}')",
    )
    assert held[0][0] == 8
    assert len(_identifiers(inputs)) == 24


def test_a_folder_too_small_to_judge_is_not_raised_however_unreadable_it_is(tmp_path) -> None:
    """Four unknown tokens out of five is not a finding, which is what the floor is for."""
    pool = tmp_path / "pool"
    (pool / "illegible").mkdir(parents=True)
    (pool / "illegible" / "zorbex_quillan_vramp.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    _index(project)
    report = run_inference(project, run_id=_run_id("2"), started_at="t")

    tally = next(
        tally
        for tally in report.flags
        if tally.rule_id == "vocabulary_unread_in_folder_outside_programs"
    )
    assert (tally.not_evaluable, tally.findings) == (None, 0)


#: The shipped concentration rule at thresholds a test can reach. The rule counts entries and
#: compares a folder against the pool, so the shipped floor of ten failing files and a factor
#: of five need a pool of sixty; the reading is what is under test and not the numbers.
_CONCENTRATION: dict[str, object] = {
    "revision": 98,
    "summary": "Comprehension failure concentrated, at thresholds a test can reach.",
    "rules": [
        {
            "id": "comprehension_failure_is_concentrated_here",
            "version": "1",
            "predicate": "failure_concentrated_in_scope",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised when its entries failing with class {failure_class} "
                "number {minimum_failures} or more and its rate exceeds the pool's by a "
                "factor of {concentration_factor}."
            ),
            "rationale": "A test revision, at thresholds a small pool can reach.",
            "parameters": [
                {
                    "name": "failure_class",
                    "type": "enum",
                    "default": "comprehension",
                    "choices": ["access", "comprehension", "interpretation"],
                },
                {"name": "minimum_failures", "type": "integer", "default": 3},
                {"name": "concentration_factor", "type": "real", "default": 2.0},
            ],
        }
    ],
}


def _unreadable_pool(tmp_path: Path) -> tuple[Project, tuple[str, ...]]:
    """A folder whose files kill the parser that opens them, beside a folder of plain tables.

    ``hostile_crash`` dies without returning, the way a segfaulting C parser does, and the
    supervisor records that as comprehension: it reached the bytes and could not make sense
    of them, which sends somebody to an extractor and never to a rule. A synthetic extractor
    whose whole body is the failure mode, rather than a real file that happens to break, so
    the test cannot pass for the wrong reason.
    """
    from fixtures.hostile import HOSTILE_SUFFIX, register_all

    names = register_all()
    pool = tmp_path / "pool"
    (pool / "unreadable").mkdir(parents=True)
    (pool / "readable").mkdir(parents=True)
    for index in range(1, 7):
        (pool / "unreadable" / f"book{index}{HOSTILE_SUFFIX}").write_text("x")
    for index in range(1, 13):
        (pool / "readable" / f"table{index}.csv").write_text("a,b\n1,2\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            extraction=ExtractionSettings(
                enabled=True, adapters=("hostile_crash",), subprocess_timeout_seconds=20.0
            )
        ),
    )
    return project, names


@contextlib.contextmanager
def _with_failures(tmp_path: Path):
    """Index and deepen a pool whose files break the parser, unregistering the fixture after."""
    from ingence.extractors.base import EXTRACTORS

    project, names = _unreadable_pool(tmp_path)
    try:
        _index(project)
        run_deepen(project, run_id=_run_id("3"), started_at="t", budget_seconds=120.0)
        yield project
    finally:
        for name in names:
            EXTRACTORS.unregister(name)


def _concentrating(factor: float, revision: int) -> dict[str, object]:
    """The test revision with one threshold moved, deep-copied so neither shares the other's."""
    declaration = copy.deepcopy(_CONCENTRATION)
    declaration["revision"] = revision
    for parameter in declaration["rules"][0]["parameters"]:  # type: ignore[index]
        if parameter["name"] == "concentration_factor":
            parameter["default"] = factor
    return declaration


def test_a_folder_failing_far_more_often_than_the_pool_is_raised_and_names_the_remedy(
    tmp_path,
) -> None:
    """The rule needs a per-scope failure rate, and a folder scope supplies one. The
    class names the remedy: comprehension sends somebody to an extractor, never to a rule."""
    with _with_failures(tmp_path) as project:
        rulebook = _rulebook_with(tmp_path, _CONCENTRATION)
        report = run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)

        rule = "comprehension_failure_is_concentrated_here"
        tally = next(tally for tally in report.flags if tally.rule_id == rule)
        assert tally.not_evaluable is None
        assert tally.findings == 1

        rows = _rows(
            project,
            "SELECT e.name, f.raised, f.reason, f.inputs FROM findings f "
            f"JOIN entries e ON 'folder:' || e.entry_id = f.subject WHERE f.rule_id = '{rule}'",
        )
        (name, raised, reason, inputs) = rows[0]
        assert str(name) == "unreadable"
        assert list(raised) == ["comprehension"]
        assert "6 of 6 entries failed on comprehension" in str(reason)
        assert "against the pool's" in str(reason)
        assert len(_identifiers(inputs)) == 6


def test_a_failure_reaches_the_entry_it_happened_to_and_the_region_that_holds_it(
    tmp_path,
) -> None:
    """The plumbing, which is where the defects live: a scan error becomes a record on one
    entry, and a region is the entries it holds."""
    with _with_failures(tmp_path) as project:
        with open_catalog(project.path) as catalog:
            records = assemble_entry_records(catalog, reads={Reads.BINDINGS}, with_failures=True)
        scopes = assemble_scope_records(records)

        failing = [held for held in records if held.failures]
        assert len(failing) == 6
        assert {failure.failure_class for held in failing for failure in held.failures} == {
            FailureClass.COMPREHENSION
        }

        # Every scope is handed the pool, and it is one object rather than one copy per scope.
        assert all(scope.pool is scopes[0].pool for scope in scopes)
        assert len(scopes[0].pool) == len(records)
        # And the pool is never what a finding may cite.
        folders = [scope for scope in scopes if scope.scope.kind is ScopeKind.FOLDER]
        smallest = min(folders, key=lambda scope: len(scope.entries))
        assert len(smallest.owns()) < len(records)


def test_a_folder_that_failed_no_more_often_than_the_pool_is_not_raised(tmp_path) -> None:
    """Concentration and not a count. A hundred failures in a pool of a hundred thousand is
    background; comparing a region against the pool is what turns a long list into a short
    one."""
    with _with_failures(tmp_path) as project:
        rulebook = _rulebook_with(tmp_path, _concentrating(1000.0, 97))
        report = run_inference(project, run_id=_run_id("2"), started_at="t", rulebook=rulebook)
        rule = "comprehension_failure_is_concentrated_here"
        tally = next(tally for tally in report.flags if tally.rule_id == rule)
        assert (tally.not_evaluable, tally.findings) == (None, 0)
