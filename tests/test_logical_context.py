"""Logical context: composed from claims and a workflow, and what it refuses to assert.

The reading itself is four lines. What is worth testing is the three places where it could quietly
say more than it knows:

Unplaced is never information. A file nothing placed is not thereby a plan.

Two readings outrank the workflow, whatever a person declared. An instrument's own extension is an
observation, and a program is a derivation.

Two workflows disagreeing is reported, not resolved. The checks made when a workflow is declared
refuse a bad declaration. A disagreement is reported instead, because it is a fact about the data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes.classes import (
    OUTRANKS_THE_WORKFLOW,
    STAGE_RULES,
    logical_context,
    place,
)
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.workflow import DEFAULT_WORKFLOW, Workflow, declare_workflow
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
MOMENT = "2026-09-17T14:02:11Z"

THEIRS = Workflow("theirs", ("processed", "analysis"))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A pool whose files sit at four stages, one of which the default workflow does not name."""
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    for name in (
        "vorn_raw.csv",
        "vorn_processed.csv",
        "vorn_analysis.csv",
        "vorn_summary.csv",
        "vorn_pipeline.py",
    ):
        (pool / "kelt" / name).write_text("a\n1\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")
    return project


def placed(project: Project) -> dict[str, dict[str, object]]:
    with open_catalog(project.path) as catalog:
        page = logical_context(catalog, project.path)
    return {str(row["name"]): row for row in page["files"]}


# ---------------------------------------------------------------------------
# The reading itself.
# ---------------------------------------------------------------------------


def test_a_stage_reads_as_where_it_sits_relative_to_where_data_enters() -> None:
    assert place(["raw"], [], [DEFAULT_WORKFLOW])["context"] == "observation"
    assert place(["processed"], [], [DEFAULT_WORKFLOW])["context"] == "derivation"
    assert place(["analysis"], [], [DEFAULT_WORKFLOW])["context"] == "derivation"


def test_the_same_stage_word_reads_differently_in_two_projects() -> None:
    """The reason logical context is not a property of a stage.

    In a laboratory whose instrument emits already-processed data, ``processed`` is where data
    enters. The engine must be able to say that without anybody editing a rule.
    """
    assert place(["processed"], [], [DEFAULT_WORKFLOW])["context"] == "derivation"
    assert place(["processed"], [], [THEIRS])["context"] == "observation"


def test_a_file_at_no_stage_the_workflow_names_is_unplaced_not_information() -> None:
    """Calling it information would force a conclusion onto a file nothing placed."""
    reading = place(["summary"], [], [DEFAULT_WORKFLOW])
    assert reading["context"] == "unplaced"
    assert "not information" in str(reading["because"])


def test_a_file_at_no_stage_at_all_is_unplaced(project: Project) -> None:
    assert place([], [], [DEFAULT_WORKFLOW])["context"] == "unplaced"


def test_with_no_workflow_declared_nothing_is_placed() -> None:
    """ "Never said" and "said the default" are different facts, and the reason says which."""
    reading = place(["raw"], [], [])
    assert reading["context"] == "unplaced"
    assert "no workflow is declared" in str(reading["because"])


# ---------------------------------------------------------------------------
# The two readings that outrank a workflow.
# ---------------------------------------------------------------------------


def test_an_instrument_extension_is_an_observation_whatever_the_workflow_says() -> None:
    """No workflow declaration outranks what the instrument wrote.

    Under ``theirs`` the ``acquisition`` stage is not a rung at all, so without the override an
    instrument file would be unplaced in a laboratory that owns the instrument.
    """
    reading = place(["acquisition"], ["acquisition_stage_from_format"], [THEIRS])
    assert reading["context"] == "observation"
    assert "outranks any workflow" in str(reading["because"])


def test_a_program_is_a_derivation_whatever_the_workflow_says() -> None:
    """A script exists to do something to something else, by construction."""
    assert place(["raw"], ["program_from_extension"], [DEFAULT_WORKFLOW])["context"] == "derivation"


def test_two_rules_outrank_the_workflow() -> None:
    assert set(OUTRANKS_THE_WORKFLOW) == {
        "acquisition_stage_from_format",
        "program_from_extension",
    }
    assert "pipeline_stage_from_name" in STAGE_RULES


# ---------------------------------------------------------------------------
# Two workflows.
# ---------------------------------------------------------------------------


def test_two_workflows_disagreeing_is_reported_rather_than_resolved() -> None:
    """Two workflows that disagree are reported, neither refused nor resolved.

    Whether two workflows in scope for one file disagree is a fact about the data rather than
    about a declaration, so there is nothing to refuse at declaration time. Picking one would
    invent an answer, and the reading names both instead.
    """
    reading = place(["processed"], [], [DEFAULT_WORKFLOW, THEIRS])
    assert reading["context"] == "conflicting"
    assert "default" in str(reading["because"])
    assert "theirs" in str(reading["because"])


def test_two_workflows_agreeing_is_not_a_conflict() -> None:
    reading = place(["analysis"], [], [DEFAULT_WORKFLOW, THEIRS])
    assert reading["context"] == "derivation"
    assert sorted(reading["workflows"]) == ["default", "theirs"]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Over a real project.
# ---------------------------------------------------------------------------


def test_a_project_is_placed_against_the_workflow_its_record_declares(
    project: Project,
) -> None:
    declare_workflow(
        project.path,
        DEFAULT_WORKFLOW,
        actor="robin",
        occurred_at=MOMENT,
        against_run_id=INFERENCE,
    )
    with open_catalog(project.path) as catalog:
        page = logical_context(catalog, project.path)
    assert page["declared"] is True
    rows = {str(row["name"]): row for row in page["files"]}
    assert rows["vorn_raw.csv"]["context"] == "observation"
    assert rows["vorn_processed.csv"]["context"] == "derivation"
    assert rows["vorn_summary.csv"]["context"] == "unplaced"


def test_a_program_in_a_real_project_is_a_derivation(project: Project) -> None:
    """The override reaching a real claim rather than a hand-built one.

    ``vorn_pipeline.py`` is claimed a program from its extension by revision 4, and that is
    what places it, not its name.
    """
    declare_workflow(
        project.path,
        DEFAULT_WORKFLOW,
        actor="robin",
        occurred_at=MOMENT,
        against_run_id=INFERENCE,
    )
    row = placed(project)["vorn_pipeline.py"]
    assert row["context"] == "derivation"
    assert "outranks any workflow" in str(row["because"])


def test_an_undeclared_project_is_shown_the_default_marked_as_one(project: Project) -> None:
    """A person should see what the default would give before agreeing to it.

    Showing it unmarked would make a laboratory that never said look as though it had agreed.
    That is the distinction the whole of logical context turns on.
    """
    with open_catalog(project.path) as catalog:
        page = logical_context(catalog, project.path)
    assert page["declared"] is False
    assert [workflow["name"] for workflow in page["workflows"]] == ["default"]
    assert any(row["context"] == "observation" for row in page["files"])


def test_changing_the_workflow_re_interprets_without_a_rescan(project: Project) -> None:
    """A different workflow in the Record re-interprets the same Index, with no rescan.

    The Record is one of the four inputs, so a person declaring a different workflow gets a
    different interpretation of the same Index -- reproducibly, and without anything being
    re-scanned or re-inferred.
    """
    declare_workflow(
        project.path, DEFAULT_WORKFLOW, actor="a", occurred_at=MOMENT, against_run_id=INFERENCE
    )
    before = placed(project)
    assert before["vorn_processed.csv"]["context"] == "derivation"

    declare_workflow(project.path, THEIRS, actor="a", occurred_at=MOMENT, against_run_id=INFERENCE)
    after = placed(project)
    assert after["vorn_processed.csv"]["context"] == "conflicting"
    assert after["vorn_raw.csv"]["context"] == "observation"


def test_the_tally_counts_every_file_and_the_page_is_bounded(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        page = logical_context(catalog, project.path, limit=2)
        whole = logical_context(catalog, project.path)
    assert len(page["files"]) == 2
    assert sum(int(row["n"]) for row in page["tally"]) == len(whole["files"])
