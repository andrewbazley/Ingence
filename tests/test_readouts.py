"""The readouts step: the views of a run are built once after inference, kept under the run's own
Dictionary fingerprint, and read by the routes. A job builds them in a ``views`` phase before it
finishes. Every name is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingence.api.apply import DefinitionApplier
from ingence.api.v1 import V1Context, dispatch
from ingence.api.v1.assumptions import AssumptionsResponse
from ingence.api.v1.overview import OverviewResponse
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.jobs import JobState
from ingence.jobs.ledger import JobLedger
from ingence.readouts import (
    STEPS,
    build_readouts,
    ranked_key,
    read_readout,
    readout,
    readout_key,
    readouts_path,
)
from ingence.scan.census import run_census

CENSUS_RUN = "run_20260101T000001Z_aaaaaaaaaaaa"
INFER_RUN = "run_20260101T000002Z_bbbbbbbbbbbb"


def _build(base: Path) -> Project:
    pool = base / "pool"
    for folder, names in {
        "Quenby/2021-03-04 drift": ["drift_01.csv", "drift_02.csv", "drift_summary.xlsx"],
        "Quenby/2021-06-10 drift repeat": ["drift_01.csv", "notes.txt"],
        "Quorlin room/2020-02-02 calibration": ["cal_01.tif", "cal_02.tif"],
        "shared/scripts": ["measure.py"],
    }.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    run_inference(Project.open(project.path), run_id=INFER_RUN, started_at="t")
    return Project.open(project.path)


#: Invented instrument spellings in place of the shipped table's, so no vendor is named here.
INSTRUMENTS = frozenset({"quorlin", "brevane"})


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    import ingence.areas as areas_module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(areas_module, "instrument_spellings", lambda: INSTRUMENTS)
        patch.setattr(areas_module, "ambiguous_instrument_spellings", lambda: frozenset())
        yield _build(tmp_path_factory.mktemp("readouts"))


def _context(project: Path, run_id: str) -> V1Context:
    return V1Context(project=project, run_current=run_id, run_pinned=run_id, job_running=False)


def test_the_step_builds_and_stores_every_view(built: Project) -> None:
    made = build_readouts(built.path, INFER_RUN)
    assert set(made["seconds"]) | set(made["skipped"]) == set(STEPS), made
    assert "overview" in made["seconds"] and "assumptions" in made["seconds"], made
    kept = readouts_path(built.path, INFER_RUN)
    key = readout_key(built.path)
    assert (kept / f"overview-{key}.json").is_file()
    assert (kept / f"assumptions-{key}.json").is_file()
    stored = json.loads((kept / f"overview-{key}.json").read_text(encoding="utf-8"))
    assert stored["readouts_version"] == "2" and "payload" in stored and "stubs" in stored


def test_the_overview_is_real_where_the_run_can_fill_it(built: Project) -> None:
    build_readouts(built.path, INFER_RUN)
    held = read_readout(built.path, INFER_RUN, "overview")
    assert held is not None
    payload = OverviewResponse.model_validate(held["payload"])
    assert payload.run_id == INFER_RUN
    assert payload.counts.files == 8
    assert payload.counts.areas == 3
    names = {area.name: area for area in payload.areas}
    assert names["Quorlin room"].kind == "instrument" and names["Quorlin room"].kind_assumed
    assert names["Quenby"].kind == "folder" and names["shared"].kind == "folder"
    assert sum(area.files for area in payload.areas) == 8
    assert payload.file_types[0].share <= 1.0 and sum(t.files for t in payload.file_types) == 8
    assert payload.coverage.read + payload.coverage.not_reached == 8
    assert payload.date_range.basis
    # What is not filled yet is said, not invented.
    assert set(held["stubs"]) >= {"goals", "checks_count"}  # repeats and coverage are real
    assert payload.goals == [] and payload.checks_count == 0
    # Repeats are real: every experiment is once or in a group of repeats.
    assert payload.repeats.seen_once + payload.repeats.repeated == payload.counts.experiments


def test_the_routes_read_the_stored_readouts_and_validate(built: Project) -> None:
    build_readouts(built.path, INFER_RUN)
    status, payload = dispatch(_context(built.path, INFER_RUN), "overview", {})
    assert status == 200, payload
    OverviewResponse.model_validate(payload)
    status, payload = dispatch(_context(built.path, INFER_RUN), "assumptions", {})
    assert status == 200, payload
    answered = AssumptionsResponse.model_validate(payload)
    assert answered.total == len(answered.assumptions)
    counts = [item.count for item in answered.assumptions]
    assert counts == sorted(counts, reverse=True)
    assert all(item.reject.body == {"id": item.id} for item in answered.assumptions)


def test_a_run_with_no_readout_has_it_built_once_on_request(built: Project, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    shutil.rmtree(readouts_path(copy, INFER_RUN), ignore_errors=True)
    assert read_readout(copy, INFER_RUN, "overview") is None
    first = readout(copy, INFER_RUN, "overview")
    assert read_readout(copy, INFER_RUN, "overview") is not None
    assert readout(copy, INFER_RUN, "overview")["payload"] == first["payload"]


def test_the_key_follows_the_dictionary_and_the_area_level(built: Project, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    build_readouts(copy, INFER_RUN)
    before = readout_key(copy)
    assert read_readout(copy, INFER_RUN, "assumptions") is not None
    # The area level is part of the key: a readout made under another level is never read.
    opened = Project.open(copy)
    themes = opened.settings.themes.model_copy(update={"area_level": 2, "person_level": 2})
    opened.save_settings(opened.settings.model_copy(update={"themes": themes}))
    assert readout_key(copy) != before
    assert read_readout(copy, INFER_RUN, "assumptions") is None


def test_the_ranked_questions_are_stored_under_the_questions_settings(
    built: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step stores the whole ranked list. It is keyed by the questions settings, so an answer
    that moves the Dictionary's fingerprint (a word's meaning) does not throw it away, and a
    change to those settings does."""
    import shutil

    import ingence.readouts as module

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    made = build_readouts(copy, INFER_RUN)
    assert "questions_ranked" in made["seconds"], made
    held = read_readout(copy, INFER_RUN, "questions_ranked")
    assert held is not None
    stored = held["payload"]
    assert stored["run_id"] == INFER_RUN and isinstance(stored["questions"], list)
    assert (readouts_path(copy, INFER_RUN) / f"questions_ranked-{ranked_key(copy)}.json").is_file()
    with monkeypatch.context() as patch:
        patch.setattr(module, "readout_key", lambda project: "a-moved-dictionary")
        assert read_readout(copy, INFER_RUN, "overview") is None
        assert read_readout(copy, INFER_RUN, "questions_ranked") is not None
    before = ranked_key(copy)
    opened = Project.open(copy)
    questions = opened.settings.questions.model_copy(update={"sheet_budget": 3})
    opened.save_settings(opened.settings.model_copy(update={"questions": questions}))
    assert ranked_key(copy) != before
    assert read_readout(copy, INFER_RUN, "questions_ranked") is None


def test_a_job_prepares_the_views_before_it_finishes(built: Project, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    shutil.rmtree(copy / "projections", ignore_errors=True)
    applier = DefinitionApplier(copy, executor="thread")
    applier.start()
    try:
        applier.submit_rerun(["inference"])
        assert applier.wait(timeout=180)
    finally:
        applier.stop()
    ledger = JobLedger(copy)
    job = ledger.recent(1)[0]
    assert job.state is JobState.DONE, job
    phases = [event.phase for event in ledger.events(job.job_id) if event.state is JobState.RUNNING]
    assert phases[-1] == "views", phases
    assert job.progress.get("views_run") == job.produced_run
    assert "views_seconds" in job.progress
    key = readout_key(copy)
    assert (readouts_path(copy, str(job.produced_run)) / f"overview-{key}.json").is_file()


def test_the_server_warms_a_missing_readout_in_the_background(
    built: Project, tmp_path: Path
) -> None:
    """A run with no readouts has them built when a server opens the project, and /api/applying
    says so meanwhile."""
    import json
    import shutil
    import time
    import urllib.request

    from ingence.api.server import DashboardServer
    from ingence.readouts import NOT_READY, warm, warming

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    shutil.rmtree(readouts_path(copy, INFER_RUN), ignore_errors=True)
    assert read_readout(copy, INFER_RUN, "overview") is None
    server = DashboardServer(copy, port=0)
    server.start()
    try:
        seen_warming = False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            with urllib.request.urlopen(server.url.rstrip("/") + "/api/applying") as response:
                applying = json.loads(response.read())
            if applying["readouts"]["warming"] == INFER_RUN:
                seen_warming = True
                assert applying["readouts"]["said"] == NOT_READY
            if read_readout(copy, INFER_RUN, "overview") is not None and warming(copy) is None:
                break
            time.sleep(0.2)
        assert read_readout(copy, INFER_RUN, "overview") is not None
        assert applying["readouts"] == {"warming": None, "said": None} or seen_warming
    finally:
        server.stop()
    # Warmed once: a second call finds the readout and builds nothing.
    assert warm(copy, INFER_RUN) is False


def test_a_retry_that_takes_up_a_published_inference_prepares_the_missing_views(
    built: Project, tmp_path: Path
) -> None:
    """An attempt that dies in the views phase leaves the inference current and the readouts
    missing. The retry builds them rather than ending done without them."""
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    shutil.rmtree(copy / "projections", ignore_errors=True)
    ledger = JobLedger(copy)
    job = ledger.enqueue(steps=["inference"], record_sequence=None, base_run=INFER_RUN)
    claimed = ledger.claim_next(pinned=INFER_RUN)
    assert claimed is not None
    # The first attempt published its inference (the run current) and died preparing the views.
    assert ledger.heartbeat(
        job.job_id,
        attempt=1,
        phase="views",
        progress={"inferring_run": INFER_RUN, "inferred_run": INFER_RUN, "views_run": INFER_RUN},
    )
    ledger.interrupt(job.job_id, reason="its worker was gone", retry_limit=2, cause="gone")
    assert ledger.get(job.job_id).state is JobState.QUEUED
    applier = DefinitionApplier(copy, executor="thread")
    applier.start()
    try:
        # A job a close left queued waits for a person's Re-run: this is it.
        applier.resume()
        assert applier.wait(timeout=150)
    finally:
        applier.stop()
    done = JobLedger(copy).get(job.job_id)
    assert done is not None and done.state is JobState.DONE, (done.state, done.error)
    assert done.produced_run == INFER_RUN and done.progress.get("resumed_after") == INFER_RUN
    assert "views_seconds" in done.progress, "the views were prepared on the retry"
    assert read_readout(copy, INFER_RUN, "overview") is not None


def test_an_assumption_is_one_plain_sentence_with_the_full_rule_behind_why() -> None:
    from ingence.readouts import WHY_SEPARATOR, _assumption_text

    statement = "An experiment's files are assumed to follow the workflow it was found to follow."
    text = _assumption_text("analysis", statement, 50, "stage_from_workflow")
    sentence, _, why = text.partition(WHY_SEPARATOR)
    assert sentence.startswith("Read as analysis: 50 files. ")
    assert "\n" not in sentence and sentence.count(". ") == 1
    assert why == statement
    assert _assumption_text("log_file", "", 1, "no_such_rule") == "Read as log file: 1 file."


def test_a_readout_stored_by_an_older_version_is_rebuilt(built: Project, tmp_path: Path) -> None:
    import json
    import shutil

    from ingence.readouts import READOUTS_VERSION

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    build_readouts(copy, INFER_RUN)
    stored = next(readouts_path(copy, INFER_RUN).glob("assumptions-*.json"))
    old = json.loads(stored.read_text(encoding="utf-8"))
    old["readouts_version"] = "1"
    old["payload"]["total"] = -7  # a marker an old file would show
    stored.write_text(json.dumps(old), encoding="utf-8")
    assert read_readout(copy, INFER_RUN, "assumptions") is None
    again = readout(copy, INFER_RUN, "assumptions")
    assert again["payload"]["total"] != -7 and again["readouts_version"] == READOUTS_VERSION
