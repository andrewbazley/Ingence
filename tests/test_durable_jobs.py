"""Durable jobs: an answer's job survives the death of its worker.

The recovery test comes first, and it kills real processes. A person's answer is written to the
Record; a job is started; its worker is paused at a named phase by the tests' own hook
(``INGENCE_JOB_PAUSE``, which the worker honours and nothing else sets) and then killed -- the
server that supervised it with its own subprocess object's ``kill()``, and the worker by its
process identifier (``SIGKILL`` on POSIX, ``TerminateProcess`` on Windows, which is what
``os.kill`` does there with any signal but the two console events). A second server starts on the
project; reconciliation finds the job its dead predecessor left ``running``, says so, and runs it
again; the run it publishes names the job and states a Record watermark at or past the answer.
Throughout, a published run stays readable and the Dashboard's reads are pinned to the settled one.

Every name is invented.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence.api.server import WRITE_HEADER, DashboardServer
from ingence.api.v1.models import RESPONSE_MODELS
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.jobs import JobState
from ingence.jobs.ledger import JobLedger
from ingence.jobs.supervisor import worker_alive
from ingence.jobs.worker import EXIT_SUPERSEDED, PAUSE_ENV, run_job
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import read_pointer

CENSUS_RUN = "run_20260101T000001Z_111111111111"
INFER_RUN = "run_20260101T000002Z_222222222222"

#: Every wait in this file is bounded; a loaded machine spawning interpreters is slow, and a
#: wait that never ends is the failure these tests exist to catch.
PATIENCE = 120.0


def _build(base: Path) -> Project:
    pool = base / "pool"
    for folder, names in {
        "kelt": ["vorn3q_raw.csv", "vorn3q_processed.csv", "brisa notes.txt"],
        "tamen": ["zeph_vorn3q_01.tif", "quolt_02.tif"],
    }.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    run_inference(Project.open(project.path), run_id=INFER_RUN, started_at="t")
    return Project.open(project.path)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("durable_jobs")).path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Project:
    """A copy of one built project per test: every test here writes jobs and runs."""
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return Project.open(target)


# -- helpers -------------------------------------------------------------------------------------


def _request(url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], Any]:
    """One request: its status, its JSON and its headers, an error answered like a success."""
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"} if body else {},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PATIENCE) as response:
            return response.status, json.loads(response.read()), response.headers
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload, error.headers


def _define(url: str, word: str) -> dict[str, Any]:
    """A word defined, then Re-run pressed: a definition waits for Re-run.

    The helper returns the answer's event with the job Re-run asked for (``applying``).
    """
    status, written, _ = _request(
        url.rstrip("/") + "/api/review/answer",
        {"actor": "kelt", "word": word, "answer": f"{word} meant", "theme_kind": "subject"},
    )
    assert status == 201, written
    assert "applying" not in written, "an answer starts nothing by itself"
    status, rerun, _ = _request(url.rstrip("/") + "/api/workspace/rerun", {"actor": "kelt"})
    if status != 201:
        return {**written, "rerun_refused": rerun}
    return {**written, "applying": rerun["applying"]}


def _until(condition: Any, what: str, *, timeout: float = PATIENCE) -> Any:
    """Poll until ``condition()`` is truthy and return it, or fail saying what never happened."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        time.sleep(0.05)
    pytest.fail(f"within {timeout} s: {what}")


def _reached(release: Path) -> dict[str, Any] | None:
    """What the paused worker wrote on reaching its phase, once it has."""
    marker = release.with_name(release.name + ".reached")
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except ValueError:  # being written
        return None
    except PermissionError:  # Windows: the worker is replacing it at this moment
        return None


def _kill(pid: int) -> None:
    """A real kill by process identifier, on every platform CI runs."""
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def _server_process(project: Path, pause: str) -> tuple[subprocess.Popen[str], str]:
    """``serve`` in a process of its own, whose workers pause where ``pause`` says."""
    import ingence

    source = str(Path(ingence.__file__).resolve().parent.parent)
    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; from pathlib import Path; from ingence.api.server import serve; "
            "serve(Path(sys.argv[1]), open_browser=False)",
            str(project),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([source, os.environ.get("PYTHONPATH", "")]),
            PAUSE_ENV: pause,
        },
    )
    assert child.stdout is not None
    first = child.stdout.readline()
    assert first.startswith("Dashboard at "), first
    return child, first.split(" at ", 1)[1].strip()


def _run_record(project: Path, run_id: str) -> dict[str, Any]:
    with open_catalog(project, run_id=run_id) as catalog:
        watermark, job, parent = catalog.query(
            "SELECT record_watermark, job_id, parent_run_id FROM run"
        ).fetchone()
    return {"record_watermark": watermark, "job_id": job, "parent_run_id": parent}


def _counts(project: Path, run_id: str | None = None) -> tuple[int, int]:
    with open_catalog(project, run_id=run_id) as catalog:
        claims = catalog.query("SELECT count(*) FROM claims").fetchone()[0]
        entries = catalog.query("SELECT count(*) FROM entries").fetchone()[0]
    return claims, entries


# -- the recovery test ---------------------------------------------------------------------------


def test_a_worker_killed_mid_job_is_found_on_restart_retried_and_completes(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    settled_counts = _counts(path)
    first_release = tmp_path / "release-first"
    child, url = _server_process(path, f"inferring:{first_release}")
    worker_pid: int | None = None
    try:
        # 1. A person's answer is recorded, and a job is started for it.
        written = _define(url, "vorn3q")
        answered = int(written["sequence"])
        job_id = written["applying"]["job_id"]
        assert job_id.startswith("job_")

        # 2. The worker reaches the named phase: the partial rebind has published its census.
        paused = _until(lambda: _reached(first_release), "the worker reached 'inferring'")
        assert paused["job_id"] == job_id and paused["phase"] == "inferring"
        worker_pid = int(paused["pid"])
        row = JobLedger(path).get(job_id)
        assert row is not None and row.state is JobState.RUNNING and row.phase == "inferring"
        assert row.worker_pid == worker_pid and row.record_sequence == answered
        assert worker_pid != child.pid, "the job runs out of the server's process"

        # 3. The settled run is pinned for reading while the job runs, and says so.
        current = read_pointer(path)
        assert current is not None and current.run_id != INFER_RUN, "the census is current"
        status, version, _ = _request(url.rstrip("/") + "/api/v1/version")
        assert status == 200 and version["job_running"] is True
        assert (version["run_pinned"], version["run_current"]) == (INFER_RUN, current.run_id)
        status, _, headers = _request(url.rstrip("/") + "/api/project")
        assert status == 200 and headers["X-Ingence-Run-Pinned"] == INFER_RUN

        # 4. The server dies (its own subprocess object's kill), then its worker (by pid).
        child.kill()
        child.wait(timeout=PATIENCE)
        _kill(worker_pid)
        _until(lambda: not worker_alive(path), "the killed worker let go of its lock")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=PATIENCE)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()

    # 5. Nothing is running it now, and the ledger still says it runs; a published run reads.
    left = JobLedger(path).get(job_id)
    assert left is not None and left.state is JobState.RUNNING
    assert _counts(path, INFER_RUN) == settled_counts
    assert _counts(path)[1] == settled_counts[1], "the census the dead job published reads"

    # 6. The server restarts. Reconciliation finds the interrupted job and says so. The job waits
    #    for a person's Re-run, which runs it again; this time its worker pauses too, so the pin is
    #    seen across the restart.
    second_release = tmp_path / "release-second"
    monkeypatch.setenv(PAUSE_ENV, f"inferring:{second_release}")
    server = DashboardServer(path, port=0)
    server.start()
    try:
        said = " ".join(server.applier.reconciled)
        # One line of words: no job's identifier.
        assert "A re-run stopped when the Dashboard last closed" in said, said
        assert "interrupted while applying the rules" in said, said
        assert _request(server.url.rstrip("/") + "/api/applying")[1]["phase"] == "stopped"
        status, rerun, _ = _request(server.url.rstrip("/") + "/api/workspace/rerun", {"by": "kelt"})
        assert status == 201, rerun
        events = [(event.state, event.message) for event in JobLedger(path).events(job_id)]
        states = [state for state, _ in events]
        at = states.index(JobState.INTERRUPTED)
        assert "was gone" in events[at][1] and "inferring" in events[at][1], events
        assert events[at + 1][0] is JobState.QUEUED, "queued again at once"

        again = _until(lambda: _reached(second_release), "the retried worker reached its phase")
        assert again["job_id"] == job_id and int(again["pid"]) != worker_pid
        status, version, _ = _request(server.url.rstrip("/") + "/api/v1/version")
        assert version["job_running"] is True and version["run_pinned"] == INFER_RUN
        status, listed, _ = _request(server.url.rstrip("/") + "/api/v1/jobs")
        assert status == 200
        (shown,) = [job for job in listed["jobs"] if job["job_id"] == job_id]
        assert (shown["state"], shown["retries"], shown["attempts"]) == ("running", 1, 2)
        assert [event["state"] for event in shown["events"]][:1] == ["queued"]
        assert "interrupted" in [event["state"] for event in shown["events"]]
        applying = _request(server.url.rstrip("/") + "/api/applying")[1]
        # /api/applying says the contract's phase word: "rules".
        assert (applying["phase"], applying["job_id"], applying["retries"]) == (
            "rules",
            job_id,
            1,
        )

        # 7. It completes.
        second_release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
        done = JobLedger(path).get(job_id)
        assert done is not None and done.state is JobState.DONE and done.error is None
        assert done.produced_run is not None
        status, version, _ = _request(server.url.rstrip("/") + "/api/v1/version")
        assert version["job_running"] is False
        assert version["run_pinned"] == version["run_current"] == done.produced_run
    finally:
        server.stop()

    # 8. The published run incorporates the answer, and names the job that produced it.
    produced = _run_record(path, done.produced_run)
    assert produced["record_watermark"] is not None and produced["record_watermark"] >= answered
    assert produced["job_id"] == job_id
    with open_catalog(path) as catalog:
        bound = catalog.query(
            "SELECT count(DISTINCT b.entry_id) FROM dictionary_bindings b "
            "JOIN components c USING (component_id) "
            "WHERE c.normalized_value = 'vorn3q' AND b.resolution_status = 'resolved'"
        ).fetchone()[0]
    assert bound == 3


def test_a_worker_killed_while_its_server_runs_is_retried_by_the_supervisor(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server lives; the supervisor sees its worker die, says so, and runs the job again."""
    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        written = _define(server.url, "vorn3q")
        job_id = written["applying"]["job_id"]
        paused = _until(lambda: _reached(release), "the worker reached 'binding'")
        process = server.applier.worker_process()
        assert process is not None and process.pid == int(paused["pid"])
        process.kill()  # the subprocess object's own kill, as on Windows
        # The retry must not pause again: the release is there before it can look.
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
        done = JobLedger(path).get(job_id)
        assert done is not None and done.state is JobState.DONE
        assert (done.retries, done.attempts) == (1, 2)
        states = [event.state for event in JobLedger(path).events(job_id)]
        assert JobState.INTERRUPTED in states
        interrupted = next(
            event for event in JobLedger(path).events(job_id) if event.state is JobState.INTERRUPTED
        )
        # A kill by signal on POSIX (the supervisor says how it ended); an exit code on Windows.
        assert "binding" in interrupted.message
        assert "was killed" in interrupted.message or "exited" in interrupted.message
    assert _run_record(path, done.produced_run or "")["job_id"] == job_id


def test_a_server_whose_worker_outlived_it_follows_that_worker_rather_than_start_another(
    project: Project, tmp_path: Path
) -> None:
    """The server dies and its worker does not: the next server follows the worker to its end."""
    path = project.path
    release = tmp_path / "release"
    child, url = _server_process(path, f"inferring:{release}")
    try:
        job_id = _define(url, "vorn3q")["applying"]["job_id"]
        paused = _until(lambda: _reached(release), "the worker reached 'inferring'")
        child.kill()
        child.wait(timeout=PATIENCE)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=PATIENCE)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()
    assert worker_alive(path), "its own session kept the worker alive past its server"
    server = DashboardServer(path, port=0)
    try:
        server.start()
        said = " ".join(server.applier.reconciled)
        assert job_id in said and "following it" in said, said
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
    finally:
        server.stop()
        if worker_alive(path):
            _kill(int(paused["pid"]))
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE
    assert (done.attempts, done.retries) == (1, 0), "the one worker finished; none was started"
    assert done.worker_pid == int(paused["pid"])


def test_closing_the_dashboard_mid_job_queues_it_again_without_spending_a_retry(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        _until(lambda: _reached(release), "the worker reached 'binding'")
    closed = JobLedger(path).get(job_id)
    assert closed is not None and closed.state is JobState.QUEUED and closed.retries == 0
    _until(lambda: not worker_alive(path), "the close stopped its worker")
    assert any("closed while it ran" in event.message for event in JobLedger(path).events(job_id))
    release.write_text("go", encoding="utf-8")
    with DashboardServer(path, port=0) as server:
        # It waits for a person: Re-run starts it.
        assert any("A re-run stopped" in line for line in server.applier.reconciled)
        assert server.applier.stopped()
        server.applier.resume()
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 0


def test_a_worker_given_an_attempt_that_is_not_its_own_does_nothing(project: Project) -> None:
    ledger = JobLedger(project.path)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFER_RUN)
    claimed = ledger.claim_next(pinned=INFER_RUN)
    assert claimed is not None
    assert run_job(project.path, job.job_id, claimed.attempts + 1) == EXIT_SUPERSEDED
    ledger.interrupt(job.job_id, reason="the worker was gone", retry_limit=2)
    assert run_job(project.path, job.job_id, claimed.attempts) == EXIT_SUPERSEDED
    held = ledger.get(job.job_id)
    assert held is not None and held.state is JobState.QUEUED and held.produced_run is None


def _freeze(project: Path, job_id: str, pid: int) -> None:
    """Stop a worker with ``SIGSTOP`` just after a heartbeat, never inside one.

    A worker frozen while its heartbeat is writing holds the ledger's write lock until it is
    killed or continued, and every other writer waits the ledger's busy timeout and fails: a
    test that froze it at a random moment failed so about one run in twenty. Frozen as soon as a
    fresh beat is seen, the heartbeat thread is in its wait, a whole interval from its next write.
    """
    ledger = JobLedger(project)
    held = ledger.get(job_id)
    first = held.heartbeat_at if held is not None else None
    _until(
        lambda: (row := ledger.get(job_id)) is not None and row.heartbeat_at != first,
        "a fresh heartbeat",
    )
    os.kill(pid, signal.SIGSTOP)


def _watch_closely(project: Path, *, stop_silent: bool) -> None:
    """Settings that hear a worker five times a second, call it silent after 1.5 s, and stop it
    (where they stop it) after 3 s."""
    with (project / "settings.yaml").open("a", encoding="utf-8") as settings:
        settings.write(
            "\njobs:\n  heartbeat_seconds: 0.2\n  silent_seconds: 1.5\n"
            f"  stop_silent: {'true' if stop_silent else 'false'}\n  stop_silent_seconds: 3\n"
        )


@pytest.mark.skipif(sys.platform == "win32", reason="a process is frozen here with SIGSTOP")
def test_a_frozen_worker_is_reported_silent_and_left_to_finish_by_default(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    _watch_closely(path, stop_silent=False)
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        pid = int(_until(lambda: _reached(release), "the worker reached 'binding'")["pid"])
        _freeze(path, job_id, pid)
        try:
            silent = _until(
                lambda: server.applier.status().get("silent_seconds"), "the silence is said"
            )
            assert silent >= 1.5
        finally:
            os.kill(pid, signal.SIGCONT)
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 0


@pytest.mark.skipif(sys.platform == "win32", reason="a process is frozen here with SIGSTOP")
def test_a_frozen_worker_is_stopped_and_its_job_run_again_where_the_settings_say(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    _watch_closely(path, stop_silent=True)
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        pid = int(_until(lambda: _reached(release), "the worker reached 'binding'")["pid"])
        _freeze(path, job_id, pid)
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 1
    stopped = [e for e in JobLedger(path).events(job_id) if e.state is JobState.INTERRUPTED]
    assert len(stopped) == 1 and "not heard from" in stopped[0].message


# -- the ledger ----------------------------------------------------------------------------------


def test_the_ledger_is_sqlite_in_wal_mode_beside_the_workspace_and_times_carry_a_zone(
    project: Project,
) -> None:
    ledger = JobLedger(project.path)
    assert not ledger.exists(), "nothing is written before a job is"
    job = ledger.enqueue(spellings=("vorn3q",), record_sequence=4, base_run=INFER_RUN)
    assert ledger.path == project.path / "jobs" / "ledger.sqlite3"
    assert ledger.journal_mode() == "wal"
    assert job.state is JobState.QUEUED and job.created_at.endswith("Z")
    assert job.record_sequence == 4 and job.base_run == INFER_RUN
    # A second answer before the job starts rides along with the first.
    merged = ledger.enqueue(spellings=("brisa",), record_sequence=5, base_run=INFER_RUN)
    assert merged.job_id == job.job_id
    assert merged.spellings == ("brisa", "vorn3q") and merged.record_sequence == 5


def test_the_ledger_reads_while_a_writer_holds_its_transaction(project: Project) -> None:
    ledger = JobLedger(project.path)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFER_RUN)
    writer = sqlite3.connect(ledger.path, timeout=5, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE jobs SET phase = 'held' WHERE job_id = ?", (job.job_id,))
        seen = ledger.get(job.job_id)
        assert seen is not None and seen.phase is None, "a reader sees the last commit"
        writer.execute("COMMIT")
    finally:
        writer.close()
    seen = ledger.get(job.job_id)
    assert seen is not None and seen.phase == "held"


def test_an_interrupted_job_is_queued_again_up_to_the_limit_and_then_fails(
    project: Project,
) -> None:
    ledger = JobLedger(project.path)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFER_RUN)
    for attempt in (1, 2, 3):
        claimed = ledger.claim_next(pinned=INFER_RUN)
        assert claimed is not None and claimed.job_id == job.job_id
        assert claimed.attempts == attempt
        after = ledger.interrupt(job.job_id, reason="the worker was gone", retry_limit=2)
        assert after.state is (JobState.QUEUED if attempt <= 2 else JobState.FAILED)
    assert after.retries == 2 and after.error is not None
    assert "2 retries" in after.error and "the worker was gone" in after.error
    assert ledger.claim_next(pinned=INFER_RUN) is None
    assert not ledger.busy() and ledger.pinned_run() is None


def test_a_running_job_with_no_worker_is_reconciled_when_a_server_starts(
    project: Project,
) -> None:
    """No process to kill: the ledger says running, and nobody holds the worker's lock."""
    ledger = JobLedger(project.path)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFER_RUN)
    assert ledger.claim_next(pinned=INFER_RUN) is not None
    with DashboardServer(project.path, port=0) as server:
        assert any("interrupted while" in line for line in server.applier.reconciled)
        server.applier.resume()  # a person's Re-run
        assert server.applier.wait(timeout=PATIENCE)
    done = ledger.get(job.job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 1
    assert _run_record(project.path, done.produced_run or "")["job_id"] == job.job_id


# -- the page ------------------------------------------------------------------------------------


def test_the_jobs_route_lists_the_ledger_in_its_declared_model(project: Project) -> None:
    assert "jobs" in RESPONSE_MODELS
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn3q",), record_sequence=0, base_run=INFER_RUN)
    ledger.claim_next(pinned=INFER_RUN)
    ledger.interrupt(job.job_id, reason="the worker was gone", retry_limit=0)
    with DashboardServer(project.path, port=0) as server:
        status, listed, _ = _request(server.url.rstrip("/") + "/api/v1/jobs")
        status_again, applying, _ = _request(server.url.rstrip("/") + "/api/applying")
        _, _, headers = _request(server.url.rstrip("/") + "/api/project")
    assert status == 200 and status_again == 200
    assert headers["X-Ingence-Job-Running"] == "false" and "X-Ingence-Run-Pinned" not in headers
    assert RESPONSE_MODELS["jobs"].model_validate(listed)
    (shown,) = listed["jobs"]
    assert (shown["job_id"], shown["state"]) == (job.job_id, "failed")
    assert shown["operations"] == ["partial_rebind", "inference"]
    assert shown["created_at"].endswith("Z")
    assert applying["phase"] == "failed" and "the worker was gone" in applying["error"]


def test_a_ledger_that_cannot_be_read_never_stops_a_page_reading_a_run(project: Project) -> None:
    ledger = JobLedger(project.path)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)  # init makes jobs/
    ledger.path.write_bytes(b"not a database at all, and long enough to be read as one" * 4)
    with DashboardServer(project.path, port=0) as server:
        status, _, headers = _request(server.url.rstrip("/") + "/api/project")
        assert status == 200 and headers["X-Ingence-Job-Running"] == "false"
        status, version, _ = _request(server.url.rstrip("/") + "/api/v1/version")
        assert status == 200 and version["run_pinned"] == version["run_current"] == INFER_RUN
        status, said, _ = _request(server.url.rstrip("/") + "/api/applying")
        assert status == 500 and "DatabaseError" in said["error"]
        assert any("could not be read" in line for line in server.applier.reconciled)
        # An answer is still written, and Re-run is told in words why its job could not be.
        written = _define(server.url, "vorn3q")
        assert written["target"] == "term:vorn3q"
        assert "DatabaseError" in written["rerun_refused"]["error"]


def test_a_ledger_that_cannot_be_read_leaves_no_connection_open(project: Project) -> None:
    """A connection opened on a file that is not a database is closed before the error is raised.

    Python 3.13 warns about a connection left for the collector, and the suite treats a warning as
    a failure. A run on 3.13 found five, one for every read of such a ledger. The connection is
    reached through the error's own traceback, so the check holds on any version.
    """
    ledger = JobLedger(project.path)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)  # init makes jobs/
    ledger.path.write_bytes(b"not a database at all, and long enough to be read as one" * 4)
    for attempt in (
        lambda: ledger.get("job_absent"),
        lambda: ledger.enqueue(spellings=("vorn",), record_sequence=1, base_run=INFER_RUN),
    ):
        with pytest.raises(sqlite3.DatabaseError) as raised:
            attempt()
        opened = [
            value
            for frame, _ in traceback.walk_tb(raised.tb)
            for value in frame.f_locals.values()
            if isinstance(value, sqlite3.Connection)
        ]
        assert opened, "the connection the failed open made is in its traceback"
        for connection in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")


def test_a_word_joining_a_job_queued_again_is_read_again_not_resumed_past(
    project: Project,
) -> None:
    """Answer one word, its census binds, the worker is interrupted, a second word joins the
    re-queued job. A retry must bind again: resuming at inference would end the job done, with a
    watermark covering the second word, and that word never read."""
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn",), record_sequence=1, base_run=INFER_RUN)
    claimed = ledger.claim_next(pinned=INFER_RUN)
    assert claimed is not None
    assert ledger.heartbeat(job.job_id, attempt=1, phase="inferring", bound_run=INFER_RUN)
    ledger.interrupt(job.job_id, reason="the worker was gone", retry_limit=2)
    unchanged = ledger.get(job.job_id)
    assert unchanged is not None and unchanged.bound_run == INFER_RUN
    ledger.enqueue(spellings=("vorn",), record_sequence=1, base_run=INFER_RUN)
    same = ledger.get(job.job_id)
    assert same is not None and same.bound_run == INFER_RUN, "nothing new: the census still serves"
    ledger.enqueue(spellings=("kelt",), record_sequence=2, base_run=INFER_RUN)
    grown = ledger.get(job.job_id)
    assert grown is not None and grown.spellings == ("kelt", "vorn")
    assert grown.bound_run is None
    assert grown.record_sequence == 2


def test_a_decision_no_run_read_is_said_once_in_words_not_by_its_sequence(
    project: Project,
) -> None:
    """No answer queues a job, and the server's one line says in words what waits."""
    from ingence.api import server as server_module
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    ledger = JobLedger(project.path)
    ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFER_RUN)
    with DashboardServer(project.path, port=0) as server:
        server.applier.resume()
        assert server.applier.wait(timeout=PATIENCE)
    current = read_pointer(project.path)
    assert current is not None
    append_event(
        project.path,
        kind=RecordEventKind.DEFINE,
        actor="a person",
        occurred_at="2026-01-01T00:00:09Z",
        target="term:vorn",
        against_run_id=current.run_id,
        payload={"answer": "vorn meant", "theme_kind": "subject", "raised": "vorn"},
    )
    said: list[str] = []
    with DashboardServer(project.path, port=0) as server:
        assert not any("did not read" in line for line in server.applier.reconciled)
        server_module.say_what_waits(project.path, say=said.append)
    assert said == ["Waiting for Re-run: 1 word defined."]
