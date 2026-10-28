"""Every run that reads the Record states its watermark. A run made inside a job names it.

``RunRecord.record_watermark`` is the highest Record sequence a run incorporates, read before the
run first reads the Record: a census and a rebind read answers into senses, inference reads
decisions, a dive reads the worth-reading answers. ``RunRecord.job_id`` names the durable job
that produced the run. Every name is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.api.routes import decide as decide_routes
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.jobs.provenance import current_job_id, job_scope, record_watermark
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

CENSUS_RUN = "run_20260101T000001Z_111111111111"
INFER_RUN = "run_20260101T000002Z_222222222222"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_quolt_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "brisa notes.txt").write_text("hello")
    made = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(made, run_id=CENSUS_RUN, started_at="t")
    run_inference(Project.open(made.path), run_id=INFER_RUN, started_at="t")
    return Project.open(made.path)


def _run_record(project: Path, run_id: str) -> dict[str, object]:
    with open_catalog(project, run_id=run_id) as catalog:
        watermark, job = catalog.query("SELECT record_watermark, job_id FROM run").fetchone()
    return {"record_watermark": watermark, "job_id": job}


def test_every_run_that_reads_the_record_states_its_watermark(project: Project) -> None:
    path = project.path
    assert _run_record(path, CENSUS_RUN)["record_watermark"] == 0, "an empty Record read"
    assert _run_record(path, INFER_RUN)["record_watermark"] == 0
    assert record_watermark(path) == 0
    with open_catalog(path) as catalog:
        event = decide_routes.define_word(
            catalog,
            path,
            actor="kelt",
            occurred_at="2026-09-28T21:00:00Z",
            word="quolt",
            answer="quolt meant",
            theme_kind="subject",
        )
    assert record_watermark(path) == event.sequence
    rebind, inference, dive = (
        "run_20260101T000003Z_333333333333",
        "run_20260101T000004Z_444444444444",
        "run_20260101T000005Z_555555555555",
    )
    run_census(Project.open(path), run_id=rebind, started_at="t", rebind=True)
    run_inference(Project.open(path), run_id=inference, started_at="t")
    run_deepen(Project.open(path), run_id=dive, started_at="t", budget_seconds=5)
    for run_id in (rebind, inference, dive):
        stated = _run_record(path, run_id)
        assert stated["record_watermark"] == event.sequence, run_id
        assert stated["job_id"] is None, "no job ran it"


def test_a_run_made_inside_a_job_names_it(project: Project) -> None:
    job = "job_20260928T210000Z_abcdef01"
    assert current_job_id() is None
    with job_scope(job):
        assert current_job_id() == job
        run_inference(
            Project.open(project.path), run_id="run_20260101T000006Z_666666666666", started_at="t"
        )
    assert current_job_id() is None
    assert _run_record(project.path, "run_20260101T000006Z_666666666666")["job_id"] == job
