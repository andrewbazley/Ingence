"""Workflow v2: the payload, the suggester as a phase of inference, the stage it places files
at, the route and the two controls. Every name is invented.

- The Record payload v2 keeps ``rungs`` beside ``stages``, so a v1 reader and a v1 payload both
  still read; a refusal is read by the workflow's identifier.
- The suggester runs inside inference, after the rules and before the flags, deterministic from
  what they concluded: the run states what it suggested in its own directory, the suggestion is in
  force as assumed, and ``stage_from_workflow`` places each file by it.
- ``GET /api/v1/workflows`` reads that statement; ``POST /api/workflows`` counts what a
  declaration would change and writes it; ``/withdraw`` refuses a suggestion, which the next run
  then lists and does not apply.
- With ``infer.workflows`` off, nothing is suggested and the declared workflows alone are read.
"""

from __future__ import annotations

import json
import shutil
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from ingence.core.project import Project
from ingence.core.settings import InferSettings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

sys.path.insert(0, str(Path(__file__).parent))
from test_experiments_route import HEK, HELA, write_pool

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
LATER = "run_20260101T000003Z_333333333333"
PERSON = "mira"
IMAGING = "wf_acquisition_then_processed_then_analysis"


def _pool(base: Path) -> Path:
    pool = write_pool(base / "pool")
    # A processed folder of one experiment and processed images in another, so one chain has
    # three steps and another skips the middle one.
    for number in range(1, 4):
        held = pool / HELA / "processed" / f"hela_dapi_{number:03d}_processed.tif"
        held.parent.mkdir(parents=True, exist_ok=True)
        held.write_text("x")
        (pool / HEK / f"hek293_gfp_{number:03d}_proc.tif").write_text("x")
    return pool


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("e4_workflows")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _stated(project: Project, run_id: str) -> list[dict[str, Any]]:
    from ingence.infer.workflow_suggest import WORKFLOWS_FILE
    from ingence.storage.commit import run_path

    loaded = json.loads((run_path(project.path, run_id) / WORKFLOWS_FILE).read_text("utf-8"))
    return list(loaded["workflows"])


def _placed(project: Project) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'stage_from_workflow' AND c.resolution_status = 'resolved'"
        ).fetchall()
    return {str(path): str(stage) for path, stage in rows}


def _ask(project: Project, path: str, **query: Any) -> tuple[HTTPStatus, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


# ---------------------------------------------------------------------------
# The payload.
# ---------------------------------------------------------------------------


def test_a_v1_payload_reads_and_a_v2_payload_keeps_the_rungs() -> None:
    from ingence.record.workflow import Workflow, WorkflowStage, check_workflow

    v1 = Workflow.from_payload({"name": "plain", "rungs": ["raw", "analysis"], "observation": 0})
    assert v1.rungs == ("raw", "analysis") and v1.stages == () and v1.origin == "declared"
    assert v1.entering_types == ()
    v2 = check_workflow(
        "Imaging then analysis",
        [],
        stages=[
            WorkflowStage("raw", types=("nd2",), folders=("raw",)),
            WorkflowStage("analysis", types=("csv",), names=("*_results.csv",)),
        ],
        scope="imaging",
        exempt=("qc_fail_*",),
        known_stages={"raw", "analysis"},
    )
    payload = v2.as_payload()
    assert payload["rungs"] == ["raw", "analysis"], "v1 readers still read the rungs"
    assert payload["stages"][0] == {
        "stage": "raw",
        "types": ["nd2"],
        "folders": ["raw"],
        "names": [],
    }
    assert payload["exempt"] == ["qc_fail_*"] and payload["scope"] == "imaging"
    again = Workflow.from_payload(payload)
    assert again == v2
    assert again.identifier == "wf_imaging_then_analysis"
    assert again.entering_types == ("nd2",)


def test_a_declared_workflow_displaces_every_suggestion_its_scope_covers() -> None:
    from ingence.infer.workflow_suggest import workflows_in_force_for
    from ingence.record.workflow import Workflow, WorkflowStage

    def suggested(name: str, scope: str | None = None) -> Workflow:
        return Workflow(
            name=name,
            rungs=("acquisition", "analysis"),
            stages=(WorkflowStage("acquisition", types=("nd2",)), WorkflowStage("analysis")),
            scope=scope,
            origin="suggested",
        )

    imaging = suggested("acquisition then analysis")
    plates = suggested("plates then summary", scope="mira/plates")
    everywhere = Workflow(name="Mine", rungs=("acquisition", "analysis"))
    only_plates = Workflow(name="Plates", rungs=("acquisition", "analysis"), scope="mira/plates")
    only_images = Workflow(name="Images", rungs=("acquisition", "analysis"), scope="mira/images")
    # No scope: every suggestion is displaced.
    assert workflows_in_force_for([everywhere], [imaging, plates], ()) == (everywhere,)
    # A scope covers the suggestions inside it, and none outside.
    assert workflows_in_force_for([only_plates], [imaging, plates], ()) == (only_plates, imaging)
    assert workflows_in_force_for([only_images], [imaging, plates], ()) == (
        only_images,
        imaging,
        plates,
    )


def test_a_refusal_is_keyed_to_the_suggested_chain_not_its_name(tmp_path: Path) -> None:
    from ingence.infer.workflow_suggest import workflows_in_force_for
    from ingence.record.workflow import (
        Refusals,
        Workflow,
        withdraw_workflow,
        workflows_refused,
    )

    created = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    run_census(created, run_id=CENSUS, started_at="t")
    withdraw_workflow(
        created.path,
        "acquisition then processed",
        actor=PERSON,
        occurred_at="2026-10-22T01:00:00Z",
        against_run_id=CENSUS,
        chain=["acquisition", "processed"],
    )
    refused = workflows_refused(created.path)
    assert refused.chains == (("acquisition", "processed"),)
    short = Workflow(name="acquisition then processed", rungs=("acquisition", "processed"))
    longer = Workflow(
        name="acquisition then processed then analysis",
        rungs=("acquisition", "processed", "analysis"),
        origin="suggested",
    )
    other = Workflow(name="acquisition then analysis", rungs=("acquisition", "analysis"))
    assert refused.refuses(short) and refused.refuses(longer)
    assert not refused.refuses(other), "a chain that does not begin with the refused one"
    assert workflows_in_force_for([], [longer, other], refused) == (other,)
    # Identifiers alone still read, for a caller holding none of the chains.
    assert Refusals.of(frozenset({longer.identifier})).refuses(longer)


def test_rungs_and_stages_that_disagree_are_refused() -> None:
    from ingence.record.workflow import WorkflowRefusedError, WorkflowStage, check_workflow

    with pytest.raises(WorkflowRefusedError, match="one list"):
        check_workflow(
            "odd",
            ["raw", "analysis"],
            stages=[WorkflowStage("analysis"), WorkflowStage("raw")],
            known_stages={"raw", "analysis"},
        )


# ---------------------------------------------------------------------------
# The suggester, inside inference.
# ---------------------------------------------------------------------------


def test_the_run_states_the_workflows_it_suggested_with_their_evidence(project: Project) -> None:
    stated = _stated(project, INFER)
    by_id = {item["id"]: item for item in stated}
    assert IMAGING in by_id
    imaging = by_id[IMAGING]
    assert imaging["origin"] == "suggested" and imaging["in_force"] is True
    assert imaging["rungs"] == ["acquisition", "processed", "analysis"]
    stages = {stage["stage"]: stage for stage in imaging["stages"]}
    assert stages["acquisition"]["types"] == ["nd2"]
    assert stages["processed"]["types"] == ["tif"] and stages["processed"]["folders"] == [
        "processed"
    ]
    assert stages["analysis"]["types"] == ["csv"]
    chains = {
        tuple(chain["chain"]): chain["experiments"] for chain in imaging["evidence"]["chains"]
    }
    # The chain that skips the middle step is counted under the longer one.
    assert chains == {("acquisition", "processed", "analysis"): 1, ("acquisition", "processed"): 1}
    assert imaging["evidence"]["experiments"] == 2
    # Each experiment the suggestion was counted from, with the chain it reached.
    assert sorted(imaging["evidence"]["followed"].values()) == [
        ["acquisition", "processed"],
        ["acquisition", "processed", "analysis"],
    ]


def test_the_suggestion_is_deterministic(project: Project, tmp_path: Path) -> None:
    again = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    run_census(again, run_id=CENSUS, started_at="t")
    run_inference(again, run_id=INFER, started_at="t")

    def apart_from_entries(stated: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # The experiments followed are named by their entries, which differ between two
        # projects; their chains are what must agree.
        return [
            {
                **item,
                "evidence": {
                    **item["evidence"],
                    "followed": sorted(item["evidence"]["followed"].values()),
                },
            }
            for item in stated
        ]

    assert apart_from_entries(_stated(again, INFER)) == apart_from_entries(_stated(project, INFER))


def test_stage_from_workflow_places_files_by_the_suggested_workflow(project: Project) -> None:
    placed = _placed(project)
    # A file no rule reading the file itself placed: the workflow's type says analysis.
    assert placed[f"{HELA}/analysis/hela_dapi_001.csv"] == "analysis"
    # A file its format or its name already placed is left as placed, never contradicted.
    assert f"{HELA}/analysis/Traj_hela_dapi_002.csv" not in placed, "a derived sense placed it"
    assert f"{HELA}/raw/hela_dapi_001.nd2" not in placed, "the format rule placed it"
    assert f"{HELA}/processed/hela_dapi_001_processed.tif" not in placed, "its name placed it"
    assert f"{HEK}/hek293_gfp_001_proc.tif" not in placed
    assert f"{HELA}/notes.txt" not in placed, "a type no stage lists is placed nowhere"
    with open_catalog(project.path) as catalog:
        own = catalog.query(
            "SELECT count(*) FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE e.name = 'hela_dapi_001_processed.tif' AND c.resolution_status = 'resolved' "
            "AND c.predicate LIKE 'classifies.%stage%'"
        ).fetchone()
    assert own is not None and own[0] >= 1
    with open_catalog(project.path) as catalog:
        tiers = catalog.query(
            "SELECT DISTINCT inference_tier FROM claims WHERE rule_id = 'stage_from_workflow'"
        ).fetchall()
    assert {str(tier[0]) for tier in tiers} == {"abductive"}, "assumed, below the format rule"


def test_the_route_lists_the_runs_workflows_most_experiments_first(project: Project) -> None:
    status, body = _ask(project, "workflows")
    assert status == HTTPStatus.OK, body
    assert body["run_id"] == INFER
    counts = [item["evidence"]["experiments"] for item in body["workflows"]]
    assert counts == sorted(counts, reverse=True)
    imaging = next(item for item in body["workflows"] if item["id"] == IMAGING)
    assert imaging["origin"] == "suggested" and imaging["in_force"] is True
    assert [stage["stage"] for stage in imaging["stages"]] == [
        "acquisition",
        "processed",
        "analysis",
    ]
    assert imaging["scope"] is None and imaging["exempt"] == []


# ---------------------------------------------------------------------------
# The controls, and the next run.
# ---------------------------------------------------------------------------


def test_declaring_withdrawing_and_the_next_run(project: Project) -> None:
    from ingence.api.routes import controls
    from ingence.record.log import read_events
    from ingence.record.workflow import workflows_in_force, workflows_refused

    declared = {
        "workflow": {
            "name": "Plates then tables",
            "scope": None,
            "stages": [
                {"stage": "acquisition", "types": ["nd2"], "folders": ["raw"], "names": []},
                {"stage": "analysis", "types": ["csv", "xlsx"], "folders": [], "names": []},
            ],
            "exempt": ["notes*"],
        },
        "dry_run": True,
    }
    # A dry run counts and records nothing.
    answer = controls.write(
        controls.WORKFLOWS_ROUTE, declared, context=controls.Context(project=project.path)
    )
    assert answer["recorded"] is False and answer["dry_run"] is True
    assert answer["workflow"]["id"] == "wf_plates_then_tables"
    change = answer["what_would_change"]
    assert set(change) == {"stages_changed", "files", "experiments", "said"}
    assert change["files"] > 0 and change["experiments"] > 0
    # In plain words, one clause per transition, never the engine's "x to y".
    assert " to unplaced" not in change["said"] and change["said"].endswith(".")
    assert change["said"].count(";") == change["stages_changed"] - 1
    assert workflows_in_force(project.path) == ()
    # A write without an actor and without a decider is refused in words.
    with pytest.raises(ValueError, match="Set your name first"):
        controls.write(
            controls.WORKFLOWS_ROUTE,
            {**declared, "dry_run": False},
            context=controls.Context(project=project.path),
        )
    # The decider the server knows stands in for an actor.
    answer = controls.write(
        controls.WORKFLOWS_ROUTE,
        {**declared, "dry_run": False},
        context=controls.Context(project=project.path, decider=PERSON),
    )
    assert answer["recorded"] is True
    (held,) = workflows_in_force(project.path)
    assert held.identifier == "wf_plates_then_tables" and held.origin == "declared"
    assert held.entering_types == ("nd2",) and held.exempt == ("notes*",)
    # Refusing the suggestion writes a REJECT on its name, carrying the suggested chain.
    answer = controls.write(
        controls.WORKFLOWS_WITHDRAW_ROUTE,
        {"id": IMAGING, "actor": PERSON},
        context=controls.Context(project=project.path),
    )
    assert answer["withdrawn"] is True and answer["recorded"] is True
    refused = workflows_refused(project.path)
    assert refused.identifiers == frozenset({IMAGING})
    assert refused.chains == (("acquisition", "processed", "analysis"),)
    assert [event.kind.value for event in read_events(project.path)][-2:] == ["define", "reject"]
    # Withdrawing with dry_run counts, echoes it and records nothing.
    answer = controls.write(
        controls.WORKFLOWS_WITHDRAW_ROUTE,
        {"id": "wf_plates_then_tables", "actor": PERSON, "dry_run": True},
        context=controls.Context(project=project.path),
    )
    assert answer["dry_run"] is True and answer["recorded"] is False
    assert answer["withdrawn"] is False
    assert workflows_in_force(project.path)[0].identifier == "wf_plates_then_tables"
    # An identifier nobody has is refused.
    with pytest.raises(ValueError, match="identifier"):
        controls.write(
            controls.WORKFLOWS_WITHDRAW_ROUTE,
            {"id": "wf_nobody", "actor": PERSON},
            context=controls.Context(project=project.path),
        )
    # The next run reads both: the declared workflow applies, the refused suggestion is listed
    # and not applied, and the files are placed by the declared one.
    run_inference(project, run_id=LATER, started_at="t")
    stated = {item["id"]: item for item in _stated(project, LATER)}
    assert stated[IMAGING]["in_force"] is False
    assert stated["wf_plates_then_tables"]["in_force"] is True
    assert stated["wf_plates_then_tables"]["origin"] == "declared"
    # The declared workflow has no scope, so it displaces every suggestion, refused or not.
    assert all(
        item["in_force"] is False for item in stated.values() if item["origin"] == "suggested"
    )
    placed = _placed(project)
    assert placed[f"{HELA}/analysis/hela_dapi_001.csv"] == "analysis"
    assert f"{HELA}/raw/hela_dapi_001.nd2" not in placed, "the format rule placed it"
    assert f"{HELA}/notes.txt" not in placed, "exempt"
    status, body = _ask(project, "workflows")
    assert status == HTTPStatus.OK, body
    assert {item["id"]: item["in_force"] for item in body["workflows"]}[IMAGING] is False


def test_the_controls_without_a_project_answer_their_shape_and_record_nothing() -> None:
    from ingence.api.routes import controls

    answer = controls.write(
        controls.WORKFLOWS_ROUTE,
        {"workflow": {"name": "Plain", "stages": [{"stage": "raw"}, {"stage": "analysis"}]}},
    )
    assert answer["recorded"] is False and answer["what_would_change"]["files"] == 0
    answer = controls.write(controls.WORKFLOWS_WITHDRAW_ROUTE, {"id": "wf_plain"})
    assert answer["withdrawn"] is False and answer["recorded"] is False


# ---------------------------------------------------------------------------
# The switch.
# ---------------------------------------------------------------------------


def test_the_fewest_experiments_a_suggestion_needs_is_a_bounded_setting(tmp_path: Path) -> None:
    """Two experiments follow the imaging chain and three the other: at the default of 2 both
    are suggested, at 3 only the second, and 0 is refused by the setting's bound."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        InferSettings(workflow_minimum_experiments=0)
    created = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    created = created.save_settings(
        created.settings.model_copy(update={"infer": InferSettings(workflow_minimum_experiments=3)})
    )
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    counts = {item["id"]: item["evidence"]["experiments"] for item in _stated(created, INFER)}
    assert IMAGING not in counts, "two experiments are too few at 3"
    assert counts and min(counts.values()) >= 3


def test_with_workflows_off_nothing_is_suggested_and_nothing_placed(tmp_path: Path) -> None:
    created = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    created = created.save_settings(
        created.settings.model_copy(update={"infer": InferSettings(workflows=False)})
    )
    run_census(created, run_id=CENSUS, started_at="t")
    report = run_inference(created, run_id=INFER, started_at="t")
    assert _stated(created, INFER) == []
    assert _placed(created) == {}
    tally = next(t for t in report.tallies if t.rule_id == "stage_from_workflow")
    assert tally.claims == 0
    shutil.rmtree(tmp_path / "project")


def test_what_a_workflow_would_change_is_said_one_clause_per_transition() -> None:
    from ingence.api.routes.controls import _said_moves

    assert _said_moves({"derivation to unplaced": 66, "observation to unplaced": 198}) == (
        "66 files would lose their step (derivation); 198 would lose theirs (observation)."
    )
    assert _said_moves({"observation to derivation": 2, "observation to unplaced": 1}) == (
        "2 files would move from observation to derivation; 1 would lose its step (observation)."
    )
    assert _said_moves({"unplaced to observation": 1}) == "1 file would take a step (observation)."
