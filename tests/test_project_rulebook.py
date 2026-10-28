"""A project's own revisions, composed after the shipped Rulebook.

What is versioned is a project's deltas, and never a copy of the Rulebook. A project revision
switches a rule on or off, overrides its parameters, supersedes it, or adds a rule of a new name,
and it numbers itself in a sequence of its own so the next shipped revision cannot collide with it.
An override takes effect as a version of its own, ``1+p3``, so that a claim's rule version names
the settings it was licensed at. A change that names nothing real, or sets a value its parameter
refuses, stops the Rulebook composing rather than being skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.infer.claims import why_not_claim_ready
from ingence.infer.run import run_inference
from ingence.rulebook import project_rulebook, shipped_rulebook
from ingence.rulebook.load import (
    RuleDeclarationError,
    load_project_revision,
    load_project_revisions,
    load_revisions,
)
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.render import render_rule
from ingence.rulebook.resolve import effective_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.schema import Rule
    from ingence.rulebook.resolve import EffectiveRulebook

load_builtins()


def _revision(directory: Path, number: int, **document: object) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{number:02d}-change.yaml"
    body = {"project_revision": number, "summary": "A change.", **document}
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


#: The shipped book these tests compose after: revisions 0 to 3. Their rules are the ones the
#: deltas below name, several superseded at revision 4, and every one readable at the revision that
#: held it. Composing at a revision is how a past run replays.
_AT = 3


def _composed(directory: Path) -> EffectiveRulebook:
    return effective_rulebook(load_revisions(), at=_AT, project=load_project_revisions(directory))


def _book() -> EffectiveRulebook:
    return effective_rulebook(load_revisions(), at=_AT)


def _shipped(rule_id: str) -> Rule:
    rule = _book().by_id(rule_id)
    assert rule is not None
    return rule


def _declaration_of(rule: Rule, **changes: object) -> dict[str, object]:
    """Return a rule as the declaration a revision file would hold, with some fields changed."""
    declaration: dict[str, object] = {
        "id": rule.rule_id,
        "version": rule.version,
        "predicate": rule.predicate,
        "effect": rule.effect.value,
        "tier": rule.tier.value,
        "level": rule.level,
        "statement": rule.statement,
        "rationale": rule.rationale,
        "parameters": [
            {
                key: value
                for key, value in {
                    "name": specification.name,
                    "type": specification.type.value,
                    "default": json.loads(specification.default),
                    "choices": list(specification.choices) or None,
                    "minimum": specification.minimum,
                    "maximum": specification.maximum,
                }.items()
                if value is not None
            }
            for specification in rule.parameters
        ],
    }
    if rule.primitive is not None:
        declaration["primitive"] = rule.primitive.value
    if rule.specialization is not None:
        declaration["specialization"] = rule.specialization
    elif rule.respect is not None:
        declaration["respect"] = rule.respect
    if rule.defeated_by:
        declaration["defeated_by"] = list(rule.defeated_by)
    declaration.update(changes)
    return declaration


# ---------------------------------------------------------------------------
# Composing.
# ---------------------------------------------------------------------------


def test_a_project_with_no_revisions_of_its_own_has_exactly_the_shipped_rulebook(tmp_path) -> None:
    book = _composed(tmp_path / "rulebook")
    assert book.digest == _book().digest
    assert book.project_revision == 0
    assert project_rulebook(tmp_path).digest == shipped_rulebook().digest


def test_an_override_takes_effect_as_a_version_of_its_own(tmp_path) -> None:
    """A claim's rule version then names the settings it was licensed at."""
    directory = tmp_path / "rulebook"
    _revision(
        directory,
        1,
        changes=[{"override": "same_composition", "parameters": {"minimum_children": 8}}],
    )
    book = _composed(directory)
    rule = book.by_id("same_composition")
    assert rule is not None
    assert rule.version == f"{_shipped('same_composition').version}+p1"
    assert "at least 8 children" in render_rule(rule)
    assert book.digest != _book().digest
    assert book.project_revision == 1


def test_a_later_override_composes_on_top_of_an_earlier_one(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _revision(
        directory,
        1,
        changes=[{"override": "same_name_family", "parameters": {"minimum_components": 3}}],
    )
    _revision(
        directory,
        2,
        changes=[
            {"override": "same_name_family", "parameters": {"component_kinds": ["word", "number"]}}
        ],
    )
    rule = _composed(directory).by_id("same_name_family")
    assert rule is not None
    assert rule.version == f"{_shipped('same_name_family').version}+p2"
    rendered = render_rule(rule)
    assert "word or number" in rendered
    assert "3 or more" in rendered


def test_a_disabled_rule_leaves_force_and_says_which_revision_switched_it_off(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _revision(directory, 1, changes=[{"disable": "stage_from_name"}])
    book = _composed(directory)
    assert book.by_id("stage_from_name") is None
    assert ("stage_from_name", "disabled at project revision 1") in book.inactive

    _revision(directory, 2, changes=[{"enable": "stage_from_name"}])
    assert _composed(directory).by_id("stage_from_name") is not None


def test_a_superseded_rule_stays_readable_as_not_in_force(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _revision(directory, 1, changes=[{"supersede": "stage_from_name"}])
    book = _composed(directory)
    assert book.by_id("stage_from_name") is None
    assert ("stage_from_name", "superseded at project revision 1") in book.inactive


def test_a_project_adds_a_rule_of_its_own_and_never_reuses_a_name(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    template = _shipped("stage_from_name")
    _revision(directory, 1, rules=[_declaration_of(template, id="stage_from_name_here")])
    added = _composed(directory).by_id("stage_from_name_here")
    assert added is not None
    assert added.origin == "project"

    _revision(directory, 2, rules=[_declaration_of(template)])
    with pytest.raises(RuleDeclarationError, match="already declares"):
        _composed(directory)


def test_a_project_can_give_a_rule_a_defeat_condition_by_superseding_it(tmp_path) -> None:
    """The route by which a rule the shipped Rulebook cannot publish from comes to publish."""
    directory = tmp_path / "rulebook"
    replacement = _declaration_of(
        _shipped("stage_from_name"),
        id="stage_from_name_with_a_defeater",
        defeated_by=["extension_contradicts_content"],
    )
    _revision(directory, 1, changes=[{"supersede": "stage_from_name"}], rules=[replacement])
    book = _composed(directory)
    rule = book.by_id("stage_from_name_with_a_defeater")
    assert rule is not None
    assert why_not_claim_ready(rule, {rule.rule_id for rule in book.rules}) is None


# ---------------------------------------------------------------------------
# Refusing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"override": "no_such_rule", "parameters": {"x": 1}}, "not declared by any revision"),
        ({"disable": "no_such_rule"}, "not declared by any revision"),
        (
            {"override": "same_composition", "parameters": {"no_such_parameter": 1}},
            "does not declare",
        ),
        (
            {"override": "same_composition", "parameters": {"minimum_children": 0}},
            "below its minimum",
        ),
        (
            {"override": "same_composition", "parameters": {"minimum_children": "eight"}},
            "is integer",
        ),
        ({"override": "same_composition"}, "at least one parameter"),
        (
            {"disable": "same_composition", "parameters": {"minimum_children": 8}},
            "only an override",
        ),
        ({"disable": "same_composition", "enable": "same_composition"}, "exactly one"),
    ],
)
def test_a_change_naming_nothing_real_or_setting_what_its_parameter_refuses_is_refused(
    tmp_path, change: dict[str, object], match: str
) -> None:
    directory = tmp_path / "rulebook"
    _revision(directory, 1, changes=[change])
    with pytest.raises(RuleDeclarationError, match=match):
        _composed(directory)


def test_a_change_to_a_rule_no_longer_in_force_says_why_it_is_not(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _revision(directory, 1, changes=[{"supersede": "same_composition"}])
    _revision(
        directory,
        2,
        changes=[{"override": "same_composition", "parameters": {"minimum_children": 8}}],
    )
    # In words a person reads, never the book's bookkeeping.
    with pytest.raises(RuleDeclarationError, match="in force: replaced in your project's rules"):
        _composed(directory)


def test_a_project_revision_numbers_itself_in_a_sequence_of_its_own(tmp_path) -> None:
    path = tmp_path / "01-change.yaml"
    path.write_text(yaml.safe_dump({"revision": 3, "summary": "A change."}), encoding="utf-8")
    with pytest.raises(RuleDeclarationError, match="sequence of its own"):
        load_project_revision(path)


def test_two_project_revisions_sharing_a_number_are_refused(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _revision(directory, 1, changes=[{"disable": "stage_from_name"}])
    other = {
        "project_revision": 1,
        "summary": "B.",
        "changes": [{"disable": "file_role_from_name"}],
    }
    (directory / "01-other.yaml").write_text(yaml.safe_dump(other), encoding="utf-8")
    with pytest.raises(RuleDeclarationError, match="declared twice"):
        load_project_revisions(directory)


def test_one_revision_says_one_thing_about_a_rule(tmp_path) -> None:
    directory = tmp_path / "rulebook"
    _revision(
        directory,
        1,
        changes=[{"disable": "same_composition"}, {"enable": "same_composition"}],
    )
    with pytest.raises(RuleDeclarationError, match="more than once"):
        load_project_revisions(directory)


# ---------------------------------------------------------------------------
# Running on a project's rules.
# ---------------------------------------------------------------------------


def _project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "notes.md").write_text("x")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def test_an_inference_run_evaluates_and_records_the_project_s_own_rules(tmp_path) -> None:
    project = _project(tmp_path)
    _revision(
        project.path / RULEBOOK_DIR,
        1,
        changes=[{"override": "same_composition", "parameters": {"minimum_children": 8}}],
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    report = run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")

    with open_catalog(project.path) as catalog:
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())
        digest = catalog.query("SELECT rulebook_hash FROM run").fetchone()[0]
    assert versions["same_composition"] == "1+p1"
    assert digest == project_rulebook(project.path).digest
    assert digest != shipped_rulebook().digest
    tallies = {tally.rule_id: tally for tally in report.groupings}
    assert tallies["same_composition"].rule_version == "1+p1"


def test_rule_show_composes_a_project_s_revisions_when_asked(tmp_path) -> None:
    project = _project(tmp_path)
    _revision(
        project.path / RULEBOOK_DIR,
        1,
        changes=[{"override": "same_composition", "parameters": {"minimum_children": 8}}],
    )
    result = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "rule", "show", "-p", str(project.path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "COLUMNS": "400"},
    )
    assert result.returncode == 0, result.stderr
    assert "Rules in force, with your project's rules" in result.stdout
    assert "same_composition (v1+p1" in result.stdout
    assert "at least 8 children" in result.stdout
