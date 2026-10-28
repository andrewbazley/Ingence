"""A person's Stop ends the job ``cancelled``, never retried, with the run current as it was before
the job.

This holds for a stop during any phase. The exception is a Stop asking to keep, read while the
content step reads files: it keeps what was read and lets the job go on to apply the rules. Every
name is invented.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ingence.api.apply import DefinitionApplier
from ingence.api.routes import controls
from ingence.api.server import DashboardServer
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.jobs import JobState
from ingence.jobs import supervisor as supervisor_module
from ingence.jobs.ledger import STOPPED, JobLedger
from ingence.jobs.supervisor import stop_grace
from ingence.jobs.worker import PAUSE_ENV, Steps
from ingence.scan.census import run_census
from ingence.storage.commit import read_pointer

CENSUS_RUN = "run_20260101T000001Z_aaaaaaaaaaaa"
INFER_RUN = "run_20260101T000002Z_bbbbbbbbbbbb"
PATIENCE = 90.0
GUARD = {"Content-Type": "application/json", "X-Ingence-Write": "1"}


@dataclass
class _Report:
    cancelled: bool = False
    published: bool = True


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for folder, names in {
        "Quenby/2021-03-04 drift": ["drift_01.csv", "drift_02.csv"],
        "Quorlin room/2020-02-02 calibration": ["cal_01.tif"],
    }.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a,b\n1,2\n")
    return pool


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("stop")
    project = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    run_inference(Project.open(project.path), run_id=INFER_RUN, started_at="t")
    return project.path


def _waiting(phases: dict[str, threading.Event], stop_at: str) -> Steps:
    """Steps that say when each phase is reached; the one to stop in waits on its token and
    reports its run cancelled, as the engine's passes do; the others return at once, having
    published nothing."""

    def step(name: str):
        def run(*args: Any, token: Any = None, **kwargs: Any) -> _Report:
            phases[name].set()
            if name != stop_at:
                return _Report()
            assert token is not None
            token.wait(PATIENCE)
            return _Report(cancelled=token.cancelled)

        return run

    def views(project: Path, run_id: str, *, reporter: Any = None, token: Any = None) -> dict:
        phases["views"].set()
        if stop_at == "views":
            token.wait(PATIENCE)
        return {"skipped": {}}

    return Steps(
        census=step("census"), inference=step("inference"), deepen=step("dive"), readouts=views
    )


def _stop_in(project: Path, phase: str, *, first_run: bool, keep: bool = False) -> JobLedger:
    phases = {name: threading.Event() for name in ("census", "dive", "inference", "views")}
    DefinitionApplier.default_executor = "thread"
    applier = DefinitionApplier(project, executor="thread")
    applier._supervisor._steps = lambda: _waiting(phases, phase)
    applier.start()
    try:
        if first_run:
            applier.submit_first_run(dive=True)
        else:
            applier.submit_rerun(["rebind", "inference"])
        assert phases[phase].wait(PATIENCE), f"the {phase} phase was never reached"
        ledger = JobLedger(project)
        job = ledger.recent(1)[0]
        asked = ledger.request_stop(job.job_id, keep=keep)
        assert asked is not None and asked.state is JobState.RUNNING
        assert applier.wait(timeout=PATIENCE)
    finally:
        applier.stop()
        DefinitionApplier.default_executor = None
    return JobLedger(project)


@pytest.mark.parametrize("phase", ["census", "dive", "inference", "views"])
def test_a_stop_during_each_phase_ends_the_job_cancelled_with_no_retry(
    built: Path, tmp_path: Path, phase: str
) -> None:
    project = tmp_path / "project"
    pool = _pool(tmp_path)
    if phase in ("census", "dive"):
        Project.create(project, created_at="t", roots=[pool])
        ledger = _stop_in(project, phase, first_run=True)
        assert read_pointer(project) is None, "a stopped first run publishes nothing"
    else:
        shutil.copytree(built, project)
        ledger = _stop_in(project, phase, first_run=False)
        assert read_pointer(project) is not None
        assert read_pointer(project).run_id == INFER_RUN, "the previous run stays current"
    job = ledger.recent(1)[0]
    assert job.state is JobState.CANCELLED, (job.state, job.error)
    assert job.retries == 0 and job.attempts == 1
    assert "stopped by a person" in str(job.error)
    states = [event.state for event in ledger.events(job.job_id)]
    assert JobState.INTERRUPTED not in states and states[-1] is JobState.CANCELLED
    assert not ledger.busy() and ledger.pinned_run() is None


def test_a_stop_in_inference_after_a_rebind_published_restores_the_base_run(
    built: Path, tmp_path: Path
) -> None:
    """The rebind publishes a census (no conclusions) before inference is stopped: the pointer
    goes back to the run current when the job was asked for."""
    project = tmp_path / "project"
    shutil.copytree(built, project)
    phases = {name: threading.Event() for name in ("census", "dive", "inference", "views")}
    waiting = _waiting(phases, "inference")

    def real_census(*args: Any, **kwargs: Any) -> Any:
        return run_census(*args, **kwargs)

    DefinitionApplier.default_executor = "thread"
    applier = DefinitionApplier(project, executor="thread")
    applier._supervisor._steps = lambda: Steps(
        census=real_census, inference=waiting.inference, deepen=None, readouts=waiting.readouts
    )
    applier.start()
    try:
        applier.submit_rerun(["rebind", "inference"])
        assert phases["inference"].wait(PATIENCE)
        rebound = read_pointer(project)
        assert rebound is not None and rebound.run_id != INFER_RUN, "the rebind published"
        ledger = JobLedger(project)
        job = ledger.recent(1)[0]
        ledger.request_stop(job.job_id, keep=False)
        assert applier.wait(timeout=PATIENCE)
    finally:
        applier.stop()
        DefinitionApplier.default_executor = None
    job = JobLedger(project).recent(1)[0]
    assert job.state is JobState.CANCELLED and job.base_run == INFER_RUN
    assert read_pointer(project).run_id == INFER_RUN
    assert "again, as before the job" in str(job.error)


def test_a_queued_job_is_cancelled_at_once_and_a_second_stop_changes_nothing(
    built: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    shutil.copytree(built, project)
    ledger = JobLedger(project)
    job = ledger.enqueue(steps=["inference"], record_sequence=None, base_run=INFER_RUN)
    after = ledger.request_stop(job.job_id, keep=True)
    assert after is not None and after.state is JobState.CANCELLED
    assert ledger.pinned_run() is None and not ledger.busy()
    again = ledger.request_stop(job.job_id, keep=False)
    assert again is not None and again.state is JobState.CANCELLED
    assert ledger.request_stop("job_nobody", keep=False) is None
    # interrupt on a stopped job returns it as it is; a running job stopped by the supervisor
    # ends cancelled too, and is never queued again.
    assert ledger.interrupt(job.job_id, reason="late", retry_limit=2).state is JobState.CANCELLED
    running = ledger.enqueue(steps=["inference"], record_sequence=None, base_run=INFER_RUN)
    assert ledger.claim_next(pinned=INFER_RUN) is not None
    ended = ledger.interrupt(
        running.job_id, reason="a person stopped it", retry_limit=2, cause=STOPPED
    )
    assert ended.state is JobState.CANCELLED and ended.retries == 0
    assert ledger.pinned_run() is None


def test_the_grace_depends_on_the_phase() -> None:
    assert stop_grace("binding") < stop_grace("census") < stop_grace("dive")
    assert stop_grace("inferring") == stop_grace("views") == stop_grace(None)


def test_the_stop_route_writes_the_request_and_the_jobs_route_shows_cancelled(
    built: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    shutil.copytree(built, project)
    ledger = JobLedger(project)
    job = ledger.enqueue(steps=["inference"], record_sequence=None, base_run=INFER_RUN)
    server = DashboardServer(project, port=0)
    server.start()
    try:
        request = urllib.request.Request(
            server.url.rstrip("/") + controls.STOP_ROUTE,
            data=json.dumps({"job_id": job.job_id, "keep": True}).encode(),
            headers=GUARD,
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 201
            stopped = json.loads(response.read())
        assert stopped["requested"] is True and stopped["state"] == "cancelled"
        # Nothing had been read, so nothing is kept: the job had not started.
        assert stopped["stop_keeps"] is False and "will not run" in stopped["said"]
        assert "kept" not in stopped["said"]
        assert stopped["recorded"] is True
        with urllib.request.urlopen(server.url.rstrip("/") + "/api/v1/jobs") as response:
            jobs = json.loads(response.read())
        assert jobs["jobs"][0]["state"] == "cancelled" and jobs["job_running"] is False
        with urllib.request.urlopen(server.url.rstrip("/") + "/api/applying") as response:
            applying = json.loads(response.read())
        assert applying["phase"] == "cancelled" and applying["phase_label"] == "Stopped"
        assert applying["can_stop"] is False
    finally:
        server.stop()


def test_a_stop_read_after_a_step_s_last_token_check_still_ends_the_job_cancelled(
    built: Path, tmp_path: Path
) -> None:
    """A step that returns uncancelled after the Stop was read must not let the views run and the
    job end done.

    Nothing of the job runs on past a Stop.
    """
    project = tmp_path / "project"
    shutil.copytree(built, project)
    phases = {name: threading.Event() for name in ("census", "inference", "views")}

    def deaf_inference(*args: Any, token: Any = None, **kwargs: Any) -> _Report:
        phases["inference"].set()
        assert token is not None
        token.wait(PATIENCE)  # the heartbeat read the Stop and cancelled this token...
        return _Report(cancelled=False)  # ...but the step had already passed its last check

    def census(*args: Any, **kwargs: Any) -> _Report:
        phases["census"].set()
        return _Report()

    def views(project: Path, run_id: str, *, reporter: Any = None, token: Any = None) -> dict:
        phases["views"].set()
        return {"skipped": {}}

    DefinitionApplier.default_executor = "thread"
    applier = DefinitionApplier(project, executor="thread")
    applier._supervisor._steps = lambda: Steps(
        census=census, inference=deaf_inference, deepen=None, readouts=views
    )
    applier.start()
    try:
        applier.submit_rerun(["inference"])
        assert phases["inference"].wait(PATIENCE)
        ledger = JobLedger(project)
        job = ledger.recent(1)[0]
        ledger.request_stop(job.job_id, keep=False)
        assert applier.wait(timeout=PATIENCE)
    finally:
        applier.stop()
        DefinitionApplier.default_executor = None
    job = JobLedger(project).recent(1)[0]
    assert job.state is JobState.CANCELLED, (job.state, job.error)
    assert not phases["views"].is_set(), "the views ran after the Stop"
    assert read_pointer(project).run_id == INFER_RUN
    assert "stopped by a person while applying the rules" in str(job.error)


def test_the_supervisor_s_grace_kill_restores_the_base_run(
    built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that does not end on the Stop is stopped by the supervisor after the phase's grace.

    The run current goes back to the one before the job: the rebind's names, published meanwhile,
    hold no conclusions.
    """
    project = tmp_path / "project"
    shutil.copytree(built, project)
    release = tmp_path / "release"
    # The worker pauses on reaching inference (the tests' own hook) and never looks at its
    # token there, as a step deep in one long call would not.
    monkeypatch.setenv(PAUSE_ENV, f"inferring:{release}")
    monkeypatch.setattr(supervisor_module, "STOP_GRACE_SECONDS", dict.fromkeys(["inferring"], 1.0))
    monkeypatch.setattr(supervisor_module, "STOP_GRACE_DEFAULT_SECONDS", 1.0)
    DefinitionApplier.default_executor = None
    applier = DefinitionApplier(project, executor="subprocess")
    applier.start()
    try:
        applier.submit_rerun(["rebind", "inference"])
        reached = release.with_name(release.name + ".reached")
        deadline = time.monotonic() + PATIENCE
        while not reached.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert reached.exists(), "the worker never reached inference"
        rebound = read_pointer(project)
        assert rebound is not None and rebound.run_id != INFER_RUN, "the rebind published"
        ledger = JobLedger(project)
        job = ledger.recent(1)[0]
        assert ledger.request_stop(job.job_id, keep=False) is not None
        assert applier.wait(timeout=PATIENCE)
    finally:
        applier.stop()
    job = JobLedger(project).recent(1)[0]
    assert job.state is JobState.CANCELLED, (job.state, job.error)
    assert job.retries == 0 and "stopped by a person" in str(job.error)
    assert read_pointer(project).run_id == INFER_RUN, "the base run is current again"
    states = [event.state for event in JobLedger(project).events(job.job_id)]
    assert JobState.INTERRUPTED not in states


# -- a Stop that keeps what was read ------------------------------------------------------


@pytest.mark.parametrize("phase", ["census", "inference"])
def test_a_stop_asking_to_keep_outside_the_content_step_ends_the_job_cancelled(
    built: Path, tmp_path: Path, phase: str
) -> None:
    """Only reading contents can publish part of what it did: in the file list and in the rules
    a Stop that asks to keep still cancels, and says that nothing of the run was kept."""
    project = tmp_path / "project"
    if phase == "census":
        Project.create(project, created_at="t", roots=[_pool(tmp_path)])
        ledger = _stop_in(project, phase, first_run=True, keep=True)
    else:
        shutil.copytree(built, project)
        ledger = _stop_in(project, phase, first_run=False, keep=True)
        assert read_pointer(project).run_id == INFER_RUN
    job = ledger.recent(1)[0]
    assert job.state is JobState.CANCELLED, (job.state, job.error)
    assert "only reading contents keeps what was read" in str(job.error)


class _AsksToStop:
    """The dive's progress reporter, which asks the ledger for a person's Stop once the pass
    has recorded a file, then waits until the worker's heartbeat has read it and acted on the
    pass's token: the stop lands after one file whatever the machine's load."""

    def __init__(self, reporter: Any, project: Path, token: Any, *, keep: bool) -> None:
        self._reporter = reporter
        self._project = project
        self._token = token
        self._keep = keep
        self.asked = False

    def advance(self, **deltas: int) -> None:
        self._reporter.advance(**deltas)
        if self.asked or not deltas.get("entries_examined"):
            return
        self.asked = True
        ledger = JobLedger(self._project)
        assert ledger.request_stop(ledger.recent(1)[0].job_id, keep=self._keep) is not None
        assert self._token.wait(PATIENCE), "the heartbeat never acted on the Stop"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reporter, name)


def _first_run_stopped_in_the_dive(tmp_path: Path, *, keep: bool, files: int) -> Path:
    from ingence.core.settings import ExtractionSettings, Settings
    from ingence.jobs.worker import engine_steps
    from ingence.scan.deepen import run_deepen

    pool = tmp_path / "pool" / "2024-05-06 brisa plates"
    pool.mkdir(parents=True)
    for index in range(files):
        (pool / f"brisa_plate_{index:02d}.csv").write_text(f"well,value\nB{index},{index}\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[tmp_path / "pool"],
        # One worker, so one file is in hand when the stop lands and the rest are not reached.
        settings=Settings(extraction=ExtractionSettings(enabled=True, workers=1)),
    ).path

    def deepen(*args: Any, token: Any, reporter: Any, **kwargs: Any) -> Any:
        asking = _AsksToStop(reporter, project, token, keep=keep)
        return run_deepen(*args, token=token, reporter=asking, **kwargs)

    real = engine_steps()
    DefinitionApplier.default_executor = "thread"
    applier = DefinitionApplier(project, executor="thread")
    applier._supervisor._steps = lambda: Steps(
        census=real.census, inference=real.inference, deepen=deepen, readouts=real.readouts
    )
    applier.start()
    try:
        applier.submit_first_run(dive=True)
        assert applier.wait(timeout=4 * PATIENCE)
    finally:
        applier.stop()
        DefinitionApplier.default_executor = None
    return project


def test_a_stop_with_keep_during_a_first_run_s_dive_keeps_what_was_read_and_applies_the_rules(
    tmp_path: Path,
) -> None:
    from ingence.storage.catalog import open_catalog

    files = 6
    project = _first_run_stopped_in_the_dive(tmp_path, keep=True, files=files)
    ledger = JobLedger(project)
    job = ledger.recent(1)[0]
    assert job.state is JobState.DONE, (job.state, job.error)
    assert job.retries == 0 and job.attempts == 1
    assert job.progress.get("dive_stopped") is True
    dive_run = job.progress["dive_run"]
    pointer = read_pointer(project)
    assert pointer is not None and pointer.run_id == job.produced_run != dive_run
    # The content run was published with what was read, the rest left unexamined.
    with open_catalog(project, run_id=dive_run) as catalog:
        outcomes = dict(
            catalog.query(
                "SELECT scan_outcome, count(*) FROM entries WHERE entry_type = 'file' "
                "GROUP BY scan_outcome"
            ).fetchall()
        )
        stopped = catalog.query(
            "SELECT count(*) FROM journal_events WHERE event = 'run.stopped'"
        ).fetchone()[0]
    assert outcomes.get("extracted", 0) >= 1, outcomes
    assert outcomes.get("metadata_only", 0) >= 1, outcomes
    assert sum(outcomes.values()) == files
    assert stopped == 1
    # Inference ran over it, and the rules' run is current.
    with open_catalog(project) as catalog:
        parent, claims = catalog.query(
            "SELECT parent_run_id, (SELECT count(*) FROM claims) FROM run"
        ).fetchone()
    assert parent == dive_run and claims > 0
    said = [event.message for event in ledger.events(job.job_id)]
    assert any("kept what was read" in message for message in said), said
    assert JobState.CANCELLED not in [event.state for event in ledger.events(job.job_id)]
    assert ledger.stop_requested(job.job_id) is None, "the request was answered"
    assert not ledger.busy()


def test_a_stop_without_keep_during_a_first_run_s_dive_ends_the_job_cancelled(
    tmp_path: Path,
) -> None:
    project = _first_run_stopped_in_the_dive(tmp_path, keep=False, files=4)
    ledger = JobLedger(project)
    job = ledger.recent(1)[0]
    assert job.state is JobState.CANCELLED, (job.state, job.error)
    assert "stopped by a person while reading contents" in str(job.error)
    # The file list the job published before the stop stays; no content run and no rules.
    pointer = read_pointer(project)
    assert pointer is not None and pointer.run_id == job.progress["census_run"]
    assert "inferring_run" not in job.progress
    assert not ledger.busy()


def test_a_kept_stop_is_not_stopped_by_the_supervisor_after_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker of its own process, paused on reaching the content step, reads a Stop asking to
    keep: the request is answered, so the supervisor, whose grace for the step is five seconds
    here (more than a heartbeat's two), never stops the worker; the content step's token, made
    after the answer, is asked to stop and keep at once, and the job goes on to apply the rules
    and ends done."""
    from ingence.core.settings import ExtractionSettings, Settings

    pool = tmp_path / "pool" / "2024-05-07 vorn plates"
    pool.mkdir(parents=True)
    for index in range(3):
        (pool / f"vorn_plate_{index:02d}.csv").write_text(f"well,value\nC{index},{index}\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[tmp_path / "pool"],
        settings=Settings(extraction=ExtractionSettings(enabled=True, workers=1)),
    ).path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"dive:{release}")
    grace = 5.0
    monkeypatch.setattr(supervisor_module, "STOP_GRACE_SECONDS", dict.fromkeys(["dive"], grace))
    monkeypatch.setattr(supervisor_module, "STOP_GRACE_DEFAULT_SECONDS", grace)
    DefinitionApplier.default_executor = None
    applier = DefinitionApplier(project, executor="subprocess")
    applier.start()
    try:
        applier.submit_first_run(dive=True)
        reached = release.with_name(release.name + ".reached")
        deadline = time.monotonic() + PATIENCE
        while not reached.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert reached.exists(), "the worker never reached the content step"
        ledger = JobLedger(project)
        job = ledger.recent(1)[0]
        assert ledger.request_stop(job.job_id, keep=True) is not None
        deadline = time.monotonic() + PATIENCE
        while ledger.stop_requested(job.job_id) is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ledger.stop_requested(job.job_id) is None, "the heartbeat never answered the Stop"
        # Well past the grace: a supervisor still counting it would have stopped the worker.
        time.sleep(grace + 1.0)
        assert ledger.get(job.job_id).state is JobState.RUNNING
        release.write_text("go")
        assert applier.wait(timeout=4 * PATIENCE)
    finally:
        applier.stop()
    job = JobLedger(project).recent(1)[0]
    assert job.state is JobState.DONE, (job.state, job.error)
    assert job.progress.get("dive_stopped") is True
    assert read_pointer(project).run_id == job.produced_run
