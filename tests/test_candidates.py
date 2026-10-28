"""The proposing phase, end to end: candidates drawn within an index, and constrained.

The tests use test revisions, composed after the book's first four (revisions 0 to 3, which name
no index. Revision 4's proposing rules would draw pairs of their own). Each test declares
the smallest Rulebook showing one behaviour: a name family proposing its declared steps, a
member a defeater retracted, one folder whose two files name different conditions, a bound
a project lowered, and a family whose rule may not claim its classes. The generated
laboratory pool proves the shipped rules themselves (``test_revision_4.py``).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.rulebook.load import load_project_revisions, load_revision, load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PARENT_INDEX
from ingence.rulebook.resolve import effective_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import RULEBOOK_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.infer.candidates import ProposalTally
    from ingence.infer.run import InferenceReport
    from ingence.rulebook.load import Revision
    from ingence.rulebook.resolve import EffectiveRulebook

load_builtins()

_INDEXED = "run_20260101T000001Z_111111111111"
_INFERRED = "run_20260101T000002Z_222222222222"
_PROJECTION = "projection_derives_from_acquisition"

#: A defeat condition a census can never fire, so a stage claimed under it stands.
_NEVER_FIRED_BY_A_CENSUS = "extension_contradicts_content"


def _stage_rule() -> dict[str, object]:
    """``stage_from_name`` naming a defeater, so its stages are claimed and a pair can read them."""
    return {
        "id": "stage_from_name",
        "version": "2",
        "predicate": "sense_from_name_tokens",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": [_NEVER_FIRED_BY_A_CENSUS],
        "statement": (
            "A file sits at the stage its name states, reading {minimum_senses} or more senses "
            "of kind {theme_kind}."
        ),
        "rationale": "A test revision, so that stages are claimed.",
        "parameters": [
            {"name": "theme_kind", "type": "string", "default": "stage"},
            {"name": "minimum_senses", "type": "integer", "default": 1, "minimum": 1, "maximum": 5},
        ],
    }


def _family_rule() -> dict[str, object]:
    """``same_name_family`` without stage words, retracted from a link, so members stand or fall."""
    return {
        "id": "same_name_family",
        "version": "2",
        "predicate": "same_component_values",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "name_family",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": ["placeholder_from_entry_kind"],
        "statement": (
            "Two entries share a name family when they agree on every component of kind "
            "{component_kinds}, leaving out a word bound to a sense of kind "
            "{ignored_theme_kinds}, and each carries {minimum_components} or more of them."
        ),
        "rationale": "A test revision, so that families are claimed with stage words left out.",
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
    }


def _projection_rule(within: str) -> dict[str, object]:
    """A proposing rule drawing its candidates within one index: the earlier file of a pair as the
    source, by its earliest credible time, a stage step corroborating."""
    return {
        "id": _PROJECTION,
        "version": "2",
        "predicate": "derives_within_a_name_identity",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": within,
        "statement": (
            "A file is proposed as processed from an earlier one it is drawn with, a step in "
            "{stage_transitions} corroborating it."
        ),
        "rationale": "A test revision, so that the projection draws candidates within an index.",
        "parameters": [
            {
                "name": "stage_transitions",
                "type": "mapping",
                "default": {"raw": "processed", "processed": "analysis"},
            }
        ],
    }


#: 1 March 2020 at noon UTC, as seconds since the epoch, and an hour.
_NOON = 1_583_064_000
_HOUR = 3600


def _in_turn(*paths: Path) -> None:
    """Each file written an hour after the one before it."""
    for turn, path in enumerate(paths):
        os.utime(path, (_NOON + turn * _HOUR, _NOON + turn * _HOUR))


def _chain(tmp_path: Path) -> Path:
    """Raw, processed and analysis files of one name family, in one folder, written in turn."""
    folder = tmp_path / "pool" / "gamma"
    folder.mkdir(parents=True)
    names = ("alpha_beta_raw.tif", "alpha_beta_processed.tif", "alpha_beta_analysis.csv")
    for name in names:
        (folder / name).write_text(name)
    _in_turn(*(folder / name for name in names))
    return folder.parent


def _inferred(
    tmp_path: Path,
    pool: Path,
    *rules: dict[str, object],
    overrides: list[dict[str, object]] | None = None,
) -> tuple[Project, InferenceReport, EffectiveRulebook]:
    """Index a pool, compose the test revision and a project's overrides, and infer once."""
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_INDEXED, started_at="t")
    if overrides:
        change = {"project_revision": 1, "summary": "A lowered bound.", "changes": overrides}
        (project.path / RULEBOOK_DIR / "01-change.yaml").write_text(
            yaml.safe_dump(change, sort_keys=False), encoding="utf-8"
        )
    revision = tmp_path / "revision.yaml"
    document = {"revision": 99, "summary": "A test revision.", "rules": list(rules)}
    revision.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    book = effective_rulebook(
        [*_first_four(), load_revision(revision)],
        project=load_project_revisions(project.path / RULEBOOK_DIR),
    )
    report = run_inference(project, run_id=_INFERRED, started_at="t", rulebook=book)
    return project, report, book


def _first_four() -> list[Revision]:
    """The book's revisions 0 to 3, which name no index."""
    return [revision for revision in load_revisions() if revision.number <= 3]


def _candidates(project: Project) -> list[tuple[str, str, str, list[str]]]:
    """Each candidate: its source's name, its target's, its tier, and the constraints that fired."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT s.name, t.name, c.decision_tier, c.defeaters_json FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id ORDER BY s.name, t.name"
        ).fetchall()
    return [
        (
            str(source),
            str(target),
            str(tier),
            sorted(
                defeater["rule_id"] for defeater in json.loads(str(defeaters)) if defeater["fired"]
            ),
        )
        for source, target, tier, defeaters in rows
    ]


def _tally(report: InferenceReport) -> ProposalTally:
    return {tally.rule_id: tally for tally in report.proposals}[_PROJECTION]


def test_a_family_proposes_each_member_from_the_one_made_before_it_and_nothing_else(
    tmp_path,
) -> None:
    """The earlier file is the source, link by link.

    The raw file is never proposed for the analysis its processed file was made between, and the
    steps that the stage words agree with corroborate. A declared stage step between two members
    does not draw a second pair."""
    project, report, _ = _inferred(
        tmp_path,
        _chain(tmp_path),
        _stage_rule(),
        _family_rule(),
        _projection_rule("same_name_family"),
    )

    assert _candidates(project) == [
        ("alpha_beta_processed.tif", "alpha_beta_analysis.csv", "circumstantial", []),
        ("alpha_beta_raw.tif", "alpha_beta_processed.tif", "circumstantial", []),
    ]
    tally = _tally(report)
    assert (tally.within, tally.pairs, tally.supported, tally.blocked, tally.truncated) == (
        "same_name_family",
        6,
        2,
        0,
        0,
    )
    assert report.candidates == 2
    with open_catalog(project.path) as catalog:
        support, defeaters = catalog.query(
            "SELECT c.support_json, c.defeaters_json FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id WHERE s.name = 'alpha_beta_raw.tif' "
            "ORDER BY c.candidate_id LIMIT 1"
        ).fetchone()
    # The clock named is the platform's: a birth where the filesystem keeps one.
    names = [component["name"] for component in json.loads(str(support))]
    assert names[0] == "name_identity" and names[2] == "stage_step"
    assert names[1].startswith("earlier_by_")
    assert {defeater["rule_id"] for defeater in json.loads(str(defeaters))} == {
        "derivation_endpoints_hold_eligible_roles",
        "derivation_follows_the_clock",
        "identity_conflict_blocks_a_derivation",
    }
    others = [tally for tally in report.proposals if tally.rule_id != _PROJECTION]
    assert others
    assert all("names no index" in str(tally.not_ready) for tally in others)


def test_a_member_a_defeater_retracted_from_its_family_is_never_drawn(tmp_path) -> None:
    """A link sharing the family's words and stage stands in for content kept elsewhere."""
    pool = _chain(tmp_path)
    try:
        os.symlink(os.path.join("gamma", "alpha_beta_raw.tif"), pool / "alpha_beta_raw.tif")
    except OSError:
        pytest.skip("this platform refuses to create a symbolic link here")

    project, report, _ = _inferred(
        tmp_path, pool, _stage_rule(), _family_rule(), _projection_rule("same_name_family")
    )

    assert [(source, target) for source, target, _, _ in _candidates(project)] == [
        ("alpha_beta_processed.tif", "alpha_beta_analysis.csv"),
        ("alpha_beta_raw.tif", "alpha_beta_processed.tif"),
    ]
    assert _tally(report).pairs == 6


def test_two_files_of_one_folder_naming_different_conditions_are_blocked(tmp_path) -> None:
    """No name family holds both, so only the folder pairs them, and the conflict refuses it."""
    folder = tmp_path / "pool" / "gamma"
    folder.mkdir(parents=True)
    (folder / "alpha_sham_raw.tif").write_text("a")
    (folder / "beta_dosed_processed.tif").write_text("b")
    _in_turn(folder / "alpha_sham_raw.tif", folder / "beta_dosed_processed.tif")

    project, report, _ = _inferred(
        tmp_path, folder.parent, _stage_rule(), _projection_rule(PARENT_INDEX)
    )

    assert _candidates(project) == [
        (
            "alpha_sham_raw.tif",
            "beta_dosed_processed.tif",
            "blocked",
            ["identity_conflict_blocks_a_derivation"],
        )
    ]
    tally = _tally(report)
    assert (tally.within, tally.blocked, dict(tally.blocked_by)) == (
        PARENT_INDEX,
        1,
        {"identity_conflict_blocks_a_derivation": 1},
    )


def test_a_bound_a_project_lowered_cuts_counterparts_and_claims_each_cut_at_its_version(
    tmp_path,
) -> None:
    project, report, _ = _inferred(
        tmp_path,
        _chain(tmp_path),
        _stage_rule(),
        _family_rule(),
        _projection_rule("same_name_family"),
        overrides=[
            {"override": "candidate_generation_is_bounded", "parameters": {"maximum_candidates": 1}}
        ],
    )

    tally = _tally(report)
    assert (tally.pairs, tally.truncated) == (3, 3)
    with open_catalog(project.path) as catalog:
        cuts = catalog.query(
            "SELECT rule_version, object_reference, inputs FROM claims "
            "WHERE rule_id = 'candidate_generation_is_bounded'"
        ).fetchall()
    assert [(version, json.loads(str(reference))) for version, reference, _ in cuts] == [
        ("1+p1", {"cut": 1, "within": "same_name_family"})
    ] * 3
    assert all(inputs for _, _, inputs in cuts)


def test_candidates_are_never_drawn_within_classes_their_rule_may_not_claim(tmp_path) -> None:
    """The shipped name family names nothing that would defeat its claims, so claims no class."""
    project, report, _ = _inferred(
        tmp_path, _chain(tmp_path), _stage_rule(), _projection_rule("same_name_family")
    )

    tally = _tally(report)
    assert tally.pairs == 0
    assert "whose classes are not claimed" in str(tally.not_ready)
    assert _candidates(project) == []


def test_candidates_are_rewritten_by_every_inference_rather_than_added_to(tmp_path) -> None:
    project, _, book = _inferred(
        tmp_path,
        _chain(tmp_path),
        _stage_rule(),
        _family_rule(),
        _projection_rule("same_name_family"),
    )
    run_inference(
        project, run_id="run_20260101T000003Z_333333333333", started_at="t", rulebook=book
    )

    assert len(_candidates(project)) == 2


def test_a_book_naming_no_index_proposes_nothing_and_says_why(tmp_path) -> None:
    """The book's first four revisions name no index: each proposing rule says so."""
    project = Project.create(tmp_path / "project", created_at="t", roots=[_chain(tmp_path)])
    run_census(project, run_id=_INDEXED, started_at="t")

    report = run_inference(
        project, run_id=_INFERRED, started_at="t", rulebook=effective_rulebook(_first_four())
    )

    assert report.proposals
    assert all(tally.within is None for tally in report.proposals)
    assert all("names no index" in str(tally.not_ready) for tally in report.proposals)
    assert report.candidates == 0


def test_two_outputs_on_one_rung_are_siblings_and_no_derivation(tmp_path) -> None:
    """A tool's processed and filtered results of one source are siblings.

    A tool's results written from the same raw data are different analysis outputs, not
    derivations of each other. Two files of one folder that share a name identity are siblings,
    and nothing is proposed between them. This holds for the raw file and the processed file
    written an hour after it too. The identity's evidence is across an experiment's folders
    (``tests/test_derivations_before_stages.py``).
    """
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    for name in (
        "vorn_cell3_results_processed.csv",
        "vorn_cell3_results_filtered.csv",
        "tamen_well2_raw.csv",
        "tamen_well2_processed.csv",
        "brisa_well5_raw.csv",
        "brisa_well5_raw_processed.csv",
    ):
        (pool / "kelt" / name).write_text("x,y\n1,2\n")
    for raw, processed in (
        ("tamen_well2_raw.csv", "tamen_well2_processed.csv"),
        ("brisa_well5_raw.csv", "brisa_well5_raw_processed.csv"),
    ):
        _in_turn(pool / "kelt" / raw, pool / "kelt" / processed)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        pairs = {
            (str(source), str(target))
            for source, target in catalog.query(
                "SELECT s.name, t.name FROM candidates c "
                "JOIN entries s ON s.entry_id = c.source_id "
                "JOIN entries t ON t.entry_id = c.target_id "
                "WHERE c.rule_id = 'derivation_within_name_identity'"
            ).fetchall()
        }
    assert pairs == set()
