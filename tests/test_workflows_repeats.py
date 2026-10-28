"""Repeats per experiment and the deepest step reached.

Every name is invented. Two experiments of one person share a signature (the same words, the
same file types, the same instrument type) and began on different days: they repeat each other.
A third shares the words but holds another file type, and two folders of another person share a
signature and one day: a part of one experiment, not a repeat. The step each reached is read
against the workflow the run suggested, and the story route lists the days.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
FIRST = "mira/2024-02-05_kelt_dapi"
AGAIN = "mira/2024-02-19_kelt_dapi"
OTHER = "mira/2024-03-11_kelt_dapi_plates"
ONE_DAY = "nami/2024-03-01_zeph_gfp"
SAME_DAY = "nami/2024-03-01_zeph_gfp_part2"
ALL = (FIRST, AGAIN, OTHER, ONE_DAY, SAME_DAY)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("e4_repeats")
    pool = base / "pool"
    for folder in (FIRST, AGAIN):
        for number in range(1, 4):
            _write(pool / folder / "raw" / f"kelt_dapi_{number:03d}.nd2")
            _write(pool / folder / "processed" / f"kelt_dapi_{number:03d}_processed.tif")
        _write(pool / folder / "analysis" / "kelt_dapi_001_results.csv", "a,b\n1,2\n")
    for folder in (ONE_DAY, SAME_DAY):
        for number in range(1, 4):
            _write(pool / folder / "raw" / f"zeph_gfp_{number:03d}.nd2")
        _write(pool / folder / "analysis" / "zeph_gfp_001_results.csv", "a,b\n1,2\n")
    for number in range(1, 4):
        _write(pool / OTHER / "raw" / f"kelt_dapi_{number:03d}.nd2")
        _write(pool / OTHER / "plates" / f"kelt_dapi_{number:03d}.xlsx")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _ids(project: Project) -> dict[str, str]:
    from ingence.experiment_units import policy_experiments

    with open_catalog(project.path) as catalog:
        return {held["path"]: unit for unit, held in policy_experiments(catalog).items()}


def _ask(project: Project, path: str, **query: Any) -> tuple[HTTPStatus, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def test_the_same_experiment_on_another_day_is_a_repeat(project: Project) -> None:
    from ingence.grouping.repeats import experiment_signatures, repeat_groups

    ids = _ids(project)
    assert set(ids) == set(ALL)
    with open_catalog(project.path) as catalog:
        signatures = experiment_signatures(catalog)
        groups = repeat_groups(catalog)
    assert signatures[ids[FIRST]] == signatures[ids[AGAIN]]
    # The folder's own words, a bound word as its Dictionary term (the stain's), the dates out.
    words = signatures[ids[FIRST]].words
    assert "kelt" in words and len(words) == 2 and not any(word.isdigit() for word in words)
    assert signatures[ids[FIRST]].instruments == ("nd2",)
    assert "processed" not in signatures[ids[FIRST]].words, "a stage word is left out"
    # The folder's own words tell it apart (the plural folded onto its Dictionary term).
    assert "plate" in signatures[ids[OTHER]].words
    assert signatures[ids[OTHER]] != signatures[ids[FIRST]]
    assert set(groups) == {ids[FIRST], ids[AGAIN]}
    assert groups[ids[FIRST]].types == ("csv", "nd2", "tif"), "reported on the group"
    assert groups[ids[FIRST]].group == groups[ids[AGAIN]].group
    assert groups[ids[FIRST]].members == (ids[FIRST], ids[AGAIN]), "earliest first"
    assert signatures[ids[ONE_DAY]] == signatures[ids[SAME_DAY]]
    assert ids[SAME_DAY] not in groups, "one day only: a part of one experiment, not a repeat"


def test_the_experiments_route_and_the_overview_carry_the_repeats(project: Project) -> None:
    ids = _ids(project)
    status, body = _ask(project, "experiments", limit=50)
    assert status == HTTPStatus.OK, body
    rows = {row["folder"]: row for row in body["rows"]}
    assert rows[FIRST]["repeats"]["n"] == 2
    assert rows[FIRST]["repeats"]["group"] == rows[AGAIN]["repeats"]["group"]
    assert rows[FIRST]["repeats"]["group"].startswith("rep_")
    assert rows[OTHER]["repeats"] == {"n": 0, "group": None}
    assert rows[SAME_DAY]["repeats"] == {"n": 0, "group": None}
    status, overview = _ask(project, "overview")
    assert status == HTTPStatus.OK, overview
    assert overview["repeats"] == {"seen_once": 3, "repeated": 2, "groups": 1}
    assert {row["id"] for row in body["rows"]} == set(ids.values())


def test_the_deepest_step_reached_is_read_against_the_workflow(project: Project) -> None:
    from ingence.completeness.workflow_steps import steps_reached, workflows_applied

    with open_catalog(project.path) as catalog:
        applied = workflows_applied(catalog)
        steps = steps_reached(catalog)
    assert applied, "the run suggested a workflow"
    ids = _ids(project)
    imaging = next(w for w in applied if w.rungs == ("acquisition", "processed", "analysis"))
    assert steps[ids[FIRST]].workflow_id == imaging.identifier
    assert steps[ids[FIRST]].reached == "analysis"
    assert steps[ids[FIRST]].expected == ("acquisition", "processed", "analysis")
    _status, body = _ask(project, "experiments", limit=50)
    rows = {row["folder"]: row for row in body["rows"]}
    assert rows[FIRST]["workflow"] == {
        "id": imaging.identifier,
        "reached": "analysis",
        "expected": ["acquisition", "processed", "analysis"],
    }
    other = rows[OTHER]["workflow"]
    assert other is None or other["reached"] == "acquisition"


def test_the_story_lists_the_days_the_workflow_and_the_repeats(project: Project) -> None:
    ids = _ids(project)
    status, story = _ask(project, "experiments/story", id=ids[FIRST])
    assert status == HTTPStatus.OK, story
    assert story["experiment_id"] == ids[FIRST]
    assert [item["experiment_id"] for item in story["repeats"]] == [ids[AGAIN]]
    assert story["repeats"][0]["label"], "a label beside the id"
    assert story["workflow"]["reached"] == "analysis"
    assert story["workflow"]["expected"] == ["acquisition", "processed", "analysis"]
    assert story["workflow"]["raw_files"] == 3 and 0 <= story["workflow"]["raw_reached"] <= 3
    assert story["workflow"]["reached_type"] == "csv"
    assert story["made_from"] == []
    assert story["days"], "every file has a day"
    assert all(len(day["date"]) == 10 for day in story["days"])
    stages = {day["stage"] for day in story["days"]}
    assert {"acquisition", "processed", "analysis"} <= stages
    assert sum(day["files"] for day in story["days"]) == 7
    assert all(day["types"] and "" not in day["types"] for day in story["days"])
    status, missing = _ask(project, "experiments/story", id="entry_nobody")
    assert status == HTTPStatus.NOT_FOUND, missing
    status, refused = _ask(project, "experiments/story")
    assert status == HTTPStatus.BAD_REQUEST, refused
