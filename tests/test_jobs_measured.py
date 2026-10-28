"""Durable jobs, measured and fixed.

These tests pin what measuring the job settings found, and what was built from it: when a
worker is called silent (``silent_seconds``), whether and when a silent worker is stopped
(``stop_silent``, ``stop_silent_seconds``), and how often a job is retried:

- the heartbeat thread can be starved: one foreign call that keeps the interpreter lock stops every
  other thread in the worker, its heartbeat included, for as long as the call lasts; the worker
  records the longest gap its own heartbeat showed, and the supervisor reports it;
- a silent worker is reported at one threshold and stopped at a second, much longer one;
- a followed orphan is judged silent too, from its beats in the ledger;
- a job whose inference published before its worker died is not run again;
- a failure that repeats where it happened is not retried again;
- a hand-run command and a Dashboard job never publish over each other;
- the workers' logs are pruned, never a running or unseen failed job's.

Every name is invented.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ingence.api.server import DashboardServer
from ingence.core.project import Project
from ingence.jobs import JobState
from ingence.jobs.ledger import JobLedger
from ingence.jobs.supervisor import worker_alive
from ingence.jobs.worker import HOLD_ENV, PAUSE_ENV
from ingence.storage.commit import read_pointer
from test_durable_jobs import (  # the recovery tests' builder and helpers, shared
    CENSUS_RUN,
    INFER_RUN,
    PATIENCE,
    _define,
    _freeze,
    _reached,
    _server_process,
    _until,
    _watch_closely,
    built,
    project,
)

__all__ = ["built", "project"]


def _settings(project: Path, **jobs: object) -> None:
    """Append a ``jobs:`` block to the project's settings."""
    lines = "".join(
        f"  {name}: {str(value).lower() if isinstance(value, bool) else value}\n"
        for name, value in jobs.items()
    )
    with (project / "settings.yaml").open("a", encoding="utf-8") as settings:
        settings.write("\njobs:\n" + lines)


# -- the heartbeat, measured ----------------------------------------------------------------------


def test_a_healthy_job_records_the_longest_gap_its_heartbeat_showed(project: Project) -> None:
    _settings(project.path, heartbeat_seconds=0.2)
    with DashboardServer(project.path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(project.path).get(job_id)
    assert done is not None and done.state is JobState.DONE
    gap = done.progress.get("beat_gap_max_seconds")
    assert isinstance(gap, float) and 0.0 < gap < 30.0


@pytest.mark.skipif(sys.platform == "win32", reason="the hold hook calls the C library's sleep")
def test_a_worker_holding_the_interpreter_lock_starves_its_heartbeat_and_is_said_silent(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measurement's first finding, pinned: a heartbeat on a thread of its own does not beat
    while another thread holds the interpreter lock in one call; silence is said, and the job
    (only slow) finishes."""
    path = project.path
    _watch_closely(path, stop_silent=False)
    monkeypatch.setenv(HOLD_ENV, "binding:4")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        silent = _until(
            lambda: server.applier.status().get("silent_seconds"), "the silence is said"
        )
        assert silent >= 1.5
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 0
    assert done.progress["beat_gap_max_seconds"] >= 3.5


def test_a_beat_that_takes_long_to_come_back_counts_toward_the_gap_after_it() -> None:
    """A beat is timed from when it was taken, not from when its write came back.

    On Python 3.13, a hold that begins while a beat is being written keeps the beat's thread
    inside the write, waiting for the interpreter lock to return to it. The ledger has the beat,
    and no other for as long as the hold lasts, so the silence is said. Measured from the moment
    the write came back, which is after the hold, the longest gap recorded would be an ordinary
    one. A beat whose write came back two seconds late shows a gap of at least two seconds."""
    from ingence.jobs.worker import _Heartbeat

    class SlowOnce:
        calls = 0

        def heartbeat(self, job_id: str, *, attempt: int, progress: object = None) -> bool:
            self.calls += 1
            if self.calls == 3:
                time.sleep(2.0)  # the write landed; the thread came back two seconds later
            return True

    ledger = SlowOnce()
    beat = _Heartbeat(ledger, "job-kelt", 1, 0.05)  # type: ignore[arg-type]
    beat.start()
    try:
        _until(lambda: ledger.calls >= 6, "beats after the slow one", timeout=20.0)
    finally:
        beat.stop()
    assert beat.gap_max >= 2.0, beat.gap_max


@pytest.mark.skipif(sys.platform == "win32", reason="the hold hook calls the C library's sleep")
def test_a_starved_heartbeat_never_holds_the_ledger_against_other_writers(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A beat is one statement, so a starved beat never holds the ledger's write lock.

    A beat written as a transaction of several statements, starved between two of them, would hold
    the ledger's write lock for as long as the interpreter lock was held elsewhere, and every other
    writer -- the answer route queueing a job -- would wait on it. One statement takes and lets go
    of the write lock inside the database call, where the interpreter lock is not needed. Beats
    every millisecond make a beat in flight when the hold begins all but certain."""
    path = project.path
    _settings(path, heartbeat_seconds=0.001, silent_seconds=60)
    monkeypatch.setenv(HOLD_ENV, "binding:4")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        ledger = JobLedger(path)
        _until(
            lambda: (row := ledger.get(job_id)) is not None and row.phase == "binding",
            "the worker entered binding, where it holds the interpreter lock",
        )
        waits = []
        for _ in range(5):
            writer = sqlite3.connect(ledger.path, timeout=30, isolation_level=None)
            try:
                began = time.monotonic()
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("COMMIT")
                waits.append(time.monotonic() - began)
            finally:
                writer.close()
            time.sleep(0.2)
        assert server.applier.wait(timeout=PATIENCE)
    assert max(waits) < 1.0, waits
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE


# -- a retry resumes from what its predecessor published ------------------------------------------


def _runs(project: Path) -> list[str]:
    return sorted(path.name for path in (project / "runs").iterdir())


@pytest.mark.parametrize("after", ["bound", "inferred"])
def test_a_retry_skips_every_phase_its_dead_predecessor_published(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: str
) -> None:
    """The worker dies after a phase's run is published and before the ledger hears of it: the
    retry reads what it planned from the ledger, finds that run current, and does not make it
    again. After inference, the job is done with the run already published."""
    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"{after}:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        paused = _until(lambda: _reached(release), f"the worker reached '{after}'")
        published = read_pointer(path)
        assert published is not None
        before = _runs(path)
        row = JobLedger(path).get(job_id)
        assert row is not None
        assert row.bound_run is None or after == "inferred", "the ledger has not heard"
        process = server.applier.worker_process()
        assert process is not None and process.pid == int(paused["pid"])
        process.kill()
        process.wait(timeout=PATIENCE)
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE
    assert (done.attempts, done.retries) == (2, 1)
    made = [name for name in _runs(path) if name not in before]
    if after == "inferred":
        assert made == [], "nothing is made again"
        assert done.produced_run == published.run_id
    else:
        assert len(made) == 1, "inference only: the census is not made again"
        assert done.progress.get("resumed_after") == published.run_id
        assert done.produced_run == made[0]
    said = " ".join(event.message for event in JobLedger(path).events(job_id))
    assert "published before" in said, said


def test_a_job_that_grew_after_its_census_was_published_binds_again(project: Project) -> None:
    """What a dead attempt planned is forgotten when the job grows: a new word is read."""
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn",), record_sequence=1, base_run=INFER_RUN)
    assert ledger.claim_next(pinned=INFER_RUN) is not None
    assert ledger.heartbeat(
        job.job_id,
        attempt=1,
        phase="inferring",
        progress={"binding_run": CENSUS_RUN, "inferring_run": INFER_RUN},
    )
    ledger.interrupt(job.job_id, reason="the worker was gone", retry_limit=2)
    kept = ledger.get(job.job_id)
    assert kept is not None and kept.progress["inferring_run"] == INFER_RUN
    ledger.enqueue(spellings=("kelt",), record_sequence=2, base_run=INFER_RUN)
    grown = ledger.get(job.job_id)
    assert grown is not None
    assert "binding_run" not in grown.progress and "inferring_run" not in grown.progress


# -- silence: said at one threshold, acted on at a second -----------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="a process is frozen here with SIGSTOP")
def test_a_silent_worker_is_told_first_and_stopped_only_after_the_longer_silence(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    _settings(
        path, heartbeat_seconds=0.2, silent_seconds=1.5, stop_silent=True, stop_silent_seconds=5
    )
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        pid = int(_until(lambda: _reached(release), "the worker reached 'binding'")["pid"])
        _freeze(path, job_id, pid)
        said = _until(
            lambda: server.applier.status().get("silent_said"), "the silence is said in words"
        )
        status = server.applier.status()
        assert status["silent_seconds"] < 5, "told before it is stopped"
        assert status["silent_stops_at_seconds"] == 5
        assert "stops the job and starts it again" in said and "5 s" in said, said
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 1
    (stopped,) = [e for e in JobLedger(path).events(job_id) if e.state is JobState.INTERRUPTED]
    assert "not heard from for 5 s" in stopped.message, stopped.message


@pytest.mark.skipif(sys.platform == "win32", reason="a process is frozen here with SIGSTOP")
def test_a_silent_worker_left_to_finish_is_said_to_be(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    _settings(path, heartbeat_seconds=0.2, silent_seconds=1.5, stop_silent=False)
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        pid = int(_until(lambda: _reached(release), "the worker reached 'binding'")["pid"])
        _freeze(path, job_id, pid)
        try:
            said = _until(lambda: server.applier.status().get("silent_said"), "the words")
            assert "lets it finish" in said and "stop_silent" in said, said
            assert server.applier.status().get("silent_stops_at_seconds") is None
        finally:
            os.kill(pid, signal.SIGCONT)
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)


def test_the_defaults_tell_before_they_stop_and_stop_well_after(project: Project) -> None:
    settings = Project.open(project.path).settings.jobs
    assert settings.stop_silent is True
    assert settings.stop_silent_seconds >= 5 * settings.silent_seconds


# -- a followed orphan is heard too ---------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="a process is frozen here with SIGSTOP")
def test_a_followed_orphan_is_judged_silent_and_stopped_where_the_settings_say(
    project: Project, tmp_path: Path
) -> None:
    """The server dies, its worker lives on frozen: the next server follows it, hears nothing,
    says so, stops it by the identifier the worker wrote for itself, and runs the job again."""
    path = project.path
    _settings(
        path, heartbeat_seconds=0.2, silent_seconds=1.5, stop_silent=True, stop_silent_seconds=4
    )
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
    orphan = int(paused["pid"])
    _freeze(path, job_id, orphan)
    release.write_text("go", encoding="utf-8")
    server = DashboardServer(path, port=0)
    try:
        server.start()
        assert any("following it" in line for line in server.applier.reconciled), (
            server.applier.reconciled
        )
        said = _until(lambda: server.applier.status().get("silent_said"), "the orphan's silence")
        assert "stops the job" in said, said
        assert server.applier.wait(timeout=PATIENCE)
    finally:
        server.stop()
        if worker_alive(path):
            os.kill(orphan, signal.SIGKILL)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE
    assert (done.attempts, done.retries) == (2, 1)
    stopped = [e for e in JobLedger(path).events(job_id) if e.state is JobState.INTERRUPTED]
    assert len(stopped) == 1 and "not heard from" in stopped[0].message, stopped


# -- retries: an accident is retried, the same failure twice is not -------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="a crash is sent here as a POSIX signal")
def test_a_worker_that_crashes_twice_in_one_phase_is_not_run_a_third_time(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        for attempt in (1, 2):
            reached = _until(
                lambda attempt=attempt: (
                    (_reached(release) or {}).get("attempt") == attempt and _reached(release)
                ),
                f"attempt {attempt} reached 'binding'",
            )
            os.kill(int(reached["pid"]), signal.SIGSEGV)
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.FAILED
    assert done.attempts == 2, "not a third time"
    assert done.error is not None and "the same way twice" in done.error, done.error
    assert "crashed" in done.error


def test_a_step_that_raises_on_its_input_fails_at_once_and_is_not_retried(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api import apply as apply_module

    def refuse(*args: object, **kwargs: object) -> object:
        message = "a name the tokeniser cannot read"
        raise ValueError(message)

    monkeypatch.setattr(apply_module.DefinitionApplier, "default_executor", "thread")
    monkeypatch.setattr(apply_module, "run_census", refuse)
    with DashboardServer(project.path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(project.path).get(job_id)
    assert done is not None and done.state is JobState.FAILED
    assert (done.attempts, done.retries) == (1, 0)
    assert done.error is not None and "cannot read" in done.error


def test_a_full_disk_fails_the_job_in_words_and_frees_what_its_run_had_staged(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api import apply as apply_module
    from ingence.storage.commit import staging_path

    staged: list[Path] = []

    def fill(project_arg: Project, *, run_id: str, **kwargs: object) -> object:
        staging = staging_path(project_arg.path, run_id)
        (staging / "entries").mkdir(parents=True)
        (staging / "entries" / "part-0.parquet").write_bytes(b"x" * 4096)
        staged.append(staging)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(apply_module.DefinitionApplier, "default_executor", "thread")
    monkeypatch.setattr(apply_module, "run_census", fill)
    with DashboardServer(project.path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(project.path).get(job_id)
    assert done is not None and done.state is JobState.FAILED and done.retries == 0
    assert done.error is not None and "disk holding the project is full" in done.error
    assert "removed" in done.error, done.error
    assert staged and not staged[0].exists()


def test_a_job_whose_end_could_not_be_written_is_settled_once_it_can_be(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker dies and the supervisor cannot write that down (the ledger refused: a full
    disk). The job must not stay running for ever: once the ledger takes writes, it is settled."""
    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    real = JobLedger.interrupt
    refused: list[str] = []

    def refuse_once(self: JobLedger, job_id: str, **kwargs: object) -> object:
        if not refused:
            refused.append(job_id)
            message = "database or disk is full"
            raise sqlite3.OperationalError(message)
        return real(self, job_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(JobLedger, "interrupt", refuse_once)
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        _until(lambda: _reached(release), "the worker reached 'binding'")
        process = server.applier.worker_process()
        assert process is not None
        release.write_text("go", encoding="utf-8")
        process.kill()
        assert server.applier.wait(timeout=PATIENCE)
    assert refused == [job_id]
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 1


# -- one publisher at a time: a hand-run command beside a Dashboard job ---------------------------


def _command(project: Path, *words: str) -> subprocess.Popen[str]:
    """``ingence <words> -p <project>`` in a process of its own, running this engine."""
    import ingence

    source = str(Path(ingence.__file__).resolve().parent.parent)
    return subprocess.Popen(
        [sys.executable, "-m", "ingence.cli.main", *words, "-p", str(project)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={
            **{key: value for key, value in os.environ.items() if key != PAUSE_ENV},
            "PYTHONPATH": os.pathsep.join([source, os.environ.get("PYTHONPATH", "")]),
            "COLUMNS": "200",
            # Windows writes a pipe in its ANSI code page, which has no box-drawing characters.
            "PYTHONIOENCODING": "utf-8",
        },
    )


def test_a_hand_run_command_refuses_in_words_while_a_dashboard_job_publishes(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        _until(lambda: _reached(release), "the worker reached 'binding'")
        before = _runs(path)
        refused = _command(path, "infer")
        out, _ = refused.communicate(timeout=PATIENCE)
        assert refused.returncode == 1, out
        said = " ".join(out.split())
        assert "Dashboard job" in said and job_id in said and "--wait" in said, said
        assert _runs(path) == before, "the refused command published nothing"

        waiting = _command(path, "infer", "--wait")
        assert waiting.stdout is not None
        heard: list[str] = []
        reader = threading.Thread(
            target=lambda: heard.extend(iter(waiting.stdout.readline, "")), daemon=True
        )
        reader.start()
        # Released only once the command has said it waits, so it cannot start after the job.
        _until(lambda: any("Waiting for it" in line for line in heard), "the command waits")
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
        assert waiting.wait(timeout=PATIENCE) == 0, "".join(heard)
        reader.join(timeout=PATIENCE)
        waiting.stdout.close()
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE
    current = read_pointer(path)
    assert current is not None and current.run_id not in (done.produced_run, *before)


def test_a_dashboard_job_waits_for_a_hand_run_command_and_says_for_what(
    project: Project,
) -> None:
    from ingence.jobs import JOBS_DIR, WORKER_LOCK
    from ingence.jobs.locks import FileLock

    path = project.path
    held = FileLock(path / JOBS_DIR / WORKER_LOCK)
    assert held.acquire(holder={"role": "command", "command": "ingence index --rebind"})
    try:
        with DashboardServer(path, port=0) as server:
            job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
            said = _until(lambda: server.applier.status().get("held_said"), "why it waits")
            assert "ingence index --rebind" in said and str(os.getpid()) in said, said
            held.release()
            assert server.applier.wait(timeout=PATIENCE)
    finally:
        held.release()
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE
    assert (done.attempts, done.retries) == (1, 0), "it waited; it was never started and refused"


# -- the workers' logs are pruned -----------------------------------------------------------------


def _ended_job(ledger: JobLedger, state: JobState, word: str) -> str:
    """A job written, claimed and ended in the ledger, with an empty log for its attempt."""
    job = ledger.enqueue(spellings=(word,), record_sequence=0, base_run=INFER_RUN)
    claimed = ledger.claim_next(pinned=INFER_RUN)
    assert claimed is not None and claimed.job_id == job.job_id
    ledger.finish(
        job.job_id,
        attempt=1,
        state=state,
        produced_run=INFER_RUN if state is JobState.DONE else None,
        error=None if state is JobState.DONE else "it raised",
    )
    logs = ledger.project / "jobs" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{job.job_id}-1.log").write_text("", encoding="utf-8")
    return job.job_id


def test_the_logs_kept_are_the_newest_and_never_a_busy_or_unseen_failed_job(
    project: Project,
) -> None:
    from ingence.jobs.supervisor import prune_logs

    ledger = JobLedger(project.path)
    old_failed = _ended_job(ledger, JobState.FAILED, "kelt")  # seen: a job ended done after it
    done = [_ended_job(ledger, JobState.DONE, f"vorn{number}") for number in range(5)]
    unseen = _ended_job(ledger, JobState.FAILED, "zib")  # the latest word: nothing done since
    waiting = ledger.enqueue(spellings=("brisa",), record_sequence=0, base_run=INFER_RUN)
    logs = project.path / "jobs" / "logs"
    (logs / f"{waiting.job_id}-1.log").write_text("its first attempt", encoding="utf-8")
    (logs / "job_20200101T000000Z_00000000-1.log").write_text("", encoding="utf-8")  # unknown
    (logs / "notes.txt").write_text("not a worker's log", encoding="utf-8")

    removed = prune_logs(project.path, keep=3)  # the newest three: the last done, zib, brisa

    left = sorted(path.name for path in logs.iterdir())
    kept_jobs = {name.rsplit("-", 1)[0] for name in left if name.endswith(".log")}
    assert kept_jobs == {done[-1], unseen, waiting.job_id}, left
    assert prune_logs(project.path, keep=1) == [logs / f"{done[-1]}-1.log"], "never zib or brisa"
    assert old_failed not in kept_jobs and "notes.txt" in left
    assert len(removed) == 6  # four done, the seen failure, the unknown


def test_a_job_s_end_prunes_the_logs_to_the_setting(project: Project) -> None:
    _settings(project.path, logs_kept=1)
    ledger = JobLedger(project.path)
    earlier = [_ended_job(ledger, JobState.DONE, f"trell{number}") for number in range(3)]
    with DashboardServer(project.path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        assert server.applier.wait(timeout=PATIENCE)
    left = sorted(path.name for path in (project.path / "jobs" / "logs").iterdir())
    assert left == [f"{job_id}-1.log"], (left, earlier)
    assert Project.open(project.path).settings.jobs.logs_kept == 1


def test_how_a_worker_ended_is_read_from_its_exit_code_on_each_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry rule's input, read without a platform to hand: POSIX's negative codes are the
    signal that ended the process, and Windows' exception codes start at 0xC0000000."""
    from types import SimpleNamespace

    from ingence.jobs import supervisor as supervisor_module
    from ingence.jobs.supervisor import ended_how

    monkeypatch.setattr(supervisor_module, "sys", SimpleNamespace(platform="linux"))
    assert ended_how(-int(signal.SIGSEGV)) == "crashed"
    assert ended_how(-int(signal.SIGABRT)) == "crashed"
    assert ended_how(-9) == "killed"  # SIGKILL: a person, the machine's memory, the Dashboard
    assert ended_how(-15) == "killed"
    assert ended_how(None) == "killed"
    assert ended_how(1) == "exited"
    monkeypatch.setattr(supervisor_module, "sys", SimpleNamespace(platform="win32"))
    assert ended_how(0xC0000005) == "crashed"  # an access violation
    assert ended_how(1) == "exited"  # Popen.kill is TerminateProcess with 1


def test_an_attempt_that_took_nothing_up_does_not_say_it_did(project: Project) -> None:
    """An attempt before it took a published run up and then died; this one ran in full, and its
    end must not repeat the earlier attempt's words."""
    from types import SimpleNamespace

    from ingence.jobs.worker import EXIT_DONE, Steps, run_job

    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn",), record_sequence=0, base_run=INFER_RUN)
    assert ledger.claim_next(pinned=INFER_RUN) is not None
    assert ledger.heartbeat(
        job.job_id, attempt=1, phase="inferring", progress={"resumed_after": "run_taken_up"}
    )
    ledger.interrupt(job.job_id, reason="the worker was gone", retry_limit=2)
    claimed = ledger.claim_next(pinned=INFER_RUN)
    assert claimed is not None and claimed.attempts == 2
    published = SimpleNamespace(cancelled=False)
    steps = Steps(census=lambda *a, **k: published, inference=lambda *a, **k: published)
    assert run_job(project.path, job.job_id, 2, steps=steps, heartbeat_seconds=0.2) == EXIT_DONE
    (ended,) = [event for event in ledger.events(job.job_id) if event.state is JobState.DONE]
    assert "published before" not in ended.message, ended.message


def test_a_ledger_that_will_not_open_is_said_with_the_room_left_on_its_disk(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured on a full disk: the ledger, a WAL database, could not be opened even to read
    ("unable to open database file"), and the status said only that. It says the room left too."""
    from ingence.api import apply as apply_module

    def refuse(ledger: JobLedger) -> object:
        message = "unable to open database file"
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(apply_module, "status_from_ledger", refuse)
    applier = apply_module.DefinitionApplier(project.path, executor="thread")
    with pytest.raises(sqlite3.OperationalError) as raised:
        applier.status()
    said = str(raised.value)
    assert "unable to open database file" in said and "free on the project's disk" in said, said


def _suspend_on_windows(pid: int) -> None:  # pragma: no cover - Windows only
    """Windows' own freeze of a whole process (``NtSuspendProcess``), as ``SIGSTOP`` is POSIX's."""
    import ctypes

    process_suspend_resume = 0x0800
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(process_suspend_resume, False, pid)
    assert handle, ctypes.get_last_error()
    try:
        assert ntdll.NtSuspendProcess(handle) == 0
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' freeze; POSIX's tests use SIGSTOP")
def test_on_windows_a_suspended_worker_is_told_silent_then_stopped_and_run_again(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # pragma: no cover - Windows only
    """The silence path on Windows: a suspended worker is said silent, stopped with
    ``TerminateProcess`` at the longer threshold, and its job run again once Windows lets go of
    the killed worker's lock."""
    path = project.path
    _settings(
        path, heartbeat_seconds=0.2, silent_seconds=1.5, stop_silent=True, stop_silent_seconds=5
    )
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        pid = int(_until(lambda: _reached(release), "the worker reached 'binding'")["pid"])
        ledger = JobLedger(path)
        held = ledger.get(job_id)
        first = held.heartbeat_at if held is not None else None
        _until(
            lambda: (row := ledger.get(job_id)) is not None and row.heartbeat_at != first,
            "a fresh heartbeat",
        )
        _suspend_on_windows(pid)
        said = _until(lambda: server.applier.status().get("silent_said"), "the silence in words")
        assert "stops the job and starts it again" in said, said
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)
    done = JobLedger(path).get(job_id)
    assert done is not None and done.state is JobState.DONE and done.retries == 1


# -- further cases --------------------------------------------------------------------------------


def test_a_later_answer_joining_a_waiting_retry_is_applied_not_resumed_past(
    project: Project,
) -> None:
    """Attempt 1 published its inference and died; while the retry waited, a later answer about the
    same word joined it (no new spelling, a higher Record sequence). The retry must not end done
    with a run that read the Record before that answer."""
    from types import SimpleNamespace

    from ingence.jobs.worker import EXIT_DONE, Steps, run_job

    current = read_pointer(project.path)
    assert current is not None
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn",), record_sequence=5, base_run=current.run_id)
    assert ledger.claim_next(pinned=current.run_id) is not None
    assert ledger.heartbeat(
        job.job_id,
        attempt=1,
        phase="inferring",
        bound_run=current.run_id,
        progress={"binding_run": current.run_id, "inferring_run": current.run_id},
    )
    ledger.interrupt(job.job_id, reason="killed", retry_limit=2, cause="killed")
    ledger.enqueue(spellings=("vorn",), record_sequence=9, base_run=current.run_id)
    joined = ledger.get(job.job_id)
    assert joined is not None and joined.record_sequence == 9
    assert "inferring_run" not in joined.progress and joined.bound_run is None
    assert ledger.claim_next(pinned=current.run_id) is not None
    calls: list[str] = []
    published = SimpleNamespace(cancelled=False)

    def step(name: str) -> object:
        def run(*args: object, **kwargs: object) -> object:
            calls.append(name)
            return published

        return run

    steps = Steps(census=step("census"), inference=step("inference"))
    assert run_job(project.path, job.job_id, 2, steps=steps, heartbeat_seconds=0.2) == EXIT_DONE
    assert calls == ["census", "inference"], "the later answer is read, then applied"


@pytest.mark.skipif(sys.platform == "win32", reason="a process is frozen here with SIGSTOP")
def test_one_failed_ledger_read_never_makes_the_supervisor_let_go_of_its_worker(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed read of the ledger never makes the supervisor let go of its worker.

    If the error left the loop that waits on the worker, the supervisor would forget its own
    process, take it for another holder of the project, and never stop the frozen worker. A
    failed read is a fault said, and counts as not heard; the waiting goes on."""
    from ingence.jobs import supervisor as supervisor_module
    from ingence.jobs.supervisor import JobSupervisor

    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    current = read_pointer(path)
    assert current is not None
    ledger = JobLedger(path)
    job = ledger.enqueue(spellings=("vorn3q",), record_sequence=None, base_run=current.run_id)
    supervisor = JobSupervisor(
        path, heartbeat_seconds=0.2, silent_seconds=1.0, stop_silent=True, stop_silent_seconds=3.0
    )
    supervisor.start()
    # A job queued before this supervisor waits for a person's Re-run.
    supervisor.release_held()
    try:
        pid = int(_until(lambda: _reached(release), "the worker reached 'binding'")["pid"])
        real = supervisor_module._Hearing.silent_for
        failed: list[int] = []

        def once(hearing: object) -> float:
            if not failed:
                failed.append(1)
                message = "database is locked"
                raise sqlite3.OperationalError(message)
            return real(hearing)  # type: ignore[arg-type]

        _freeze(path, job.job_id, pid)
        monkeypatch.setattr(supervisor_module._Hearing, "silent_for", once)
        _until(
            lambda: any(
                event.state is JobState.INTERRUPTED for event in JobLedger(path).events(job.job_id)
            ),
            "the frozen worker stopped for its silence",
            timeout=30,
        )
        assert failed == [1]
        assert any("database is locked" in problem for problem in supervisor.problems)
        release.write_text("go", encoding="utf-8")
        _until(
            lambda: (
                (row := JobLedger(path).get(job.job_id)) is not None and row.state is JobState.DONE
            ),
            "the job run again to its end",
        )
    finally:
        supervisor.stop()
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    done = JobLedger(path).get(job.job_id)
    assert done is not None and done.retries == 1
    (stopped,) = [e for e in JobLedger(path).events(job.job_id) if e.state is JobState.INTERRUPTED]
    assert "not heard from" in stopped.message, stopped.message


def test_silence_counts_only_the_time_the_supervisor_was_listening(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before Python 3.13 on Windows the monotonic clock runs on through the machine's sleep, so a
    laptop asleep a quarter of an hour mid-job would wake to a quarter of an hour of "silence" and
    stop a healthy worker. Time the supervisor did not spend listening -- a jump between two looks
    far longer than it ever waits -- is not silence."""
    from ingence.jobs import supervisor as supervisor_module

    clock = [1000.0]
    monkeypatch.setattr(supervisor_module.time, "monotonic", lambda: clock[0])
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("vorn",), record_sequence=0, base_run=INFER_RUN)
    assert ledger.claim_next(pinned=INFER_RUN) is not None
    hearing = supervisor_module._Hearing(ledger, job.job_id, allowance=0.0)
    assert ledger.heartbeat(job.job_id, attempt=1)
    clock[0] += 0.6
    assert hearing.silent_for() == 0.0, "heard"
    for _ in range(10):  # listening, a look every half second, nothing heard
        clock[0] += 0.5
        hearing.silent_for()
    assert hearing.silent_for() == pytest.approx(5.0)
    clock[0] += 1800.0  # asleep: the clock ran on, the supervisor was not listening
    assert hearing.silent_for() < 10.0, "the sleep is not silence"
    clock[0] += 0.5
    assert hearing.silent_for() == pytest.approx(5.5)


def _holding(lock: Path, holder: dict[str, object] | None) -> subprocess.Popen[str]:
    """A process of its own that takes ``lock`` (describing itself as ``holder``) and keeps it."""
    import ingence

    source = str(Path(ingence.__file__).resolve().parent.parent)
    code = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from ingence.jobs.locks import FileLock\n"
        "held = json.loads(sys.argv[2])\n"
        "assert FileLock(Path(sys.argv[1])).acquire(holder=held)\n"
        "print('held', flush=True)\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(lock), json.dumps(holder)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([source, os.environ.get("PYTHONPATH", "")]),
        },
    )
    assert child.stdout is not None and child.stdout.readline().strip() == "held"
    return child


@pytest.mark.parametrize(
    ("holder", "stale"),
    [
        ({"role": "command", "command": "ingence index"}, None),
        ({"role": "worker", "job_id": "job_20200101T000000Z_00000000", "attempt": 1}, None),
        (None, "before"),
        (None, "after"),
    ],
    ids=[
        "a command holds it",
        "another job's worker holds it",
        "a stale description",
        "a stale description an older holder left in place",
    ],
)
def test_a_silent_orphan_is_never_stopped_by_an_identifier_that_is_not_its_worker(
    project: Project, holder: dict[str, object] | None, stale: str | None
) -> None:
    """The ledger remembers a worker's identifier, and after that worker died the identifier can be
    another process's. The stop kills only a holder that says it is this job's worker with that
    identifier; a holder that says nothing clears any old description first, so a stale one cannot
    name an unrelated process."""
    from ingence.jobs import JOBS_DIR, WORKER_LOCK
    from ingence.jobs.supervisor import JobSupervisor

    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL
    )
    lock = project.path / JOBS_DIR / WORKER_LOCK
    ledger = JobLedger(project.path)
    job = ledger.enqueue(spellings=("kelt",), record_sequence=0, base_run=INFER_RUN)
    assert ledger.claim_next(pinned=INFER_RUN) is not None
    ledger.attach(job.job_id, attempt=1, pid=unrelated.pid)
    row = ledger.get(job.job_id)
    assert row is not None
    # A description a dead worker of this very job left behind, naming what is now its identifier
    # reused by an unrelated process.
    described = json.dumps(
        {"role": "worker", "job_id": row.job_id, "attempt": 1, "pid": unrelated.pid}
    )
    lock.parent.mkdir(parents=True, exist_ok=True)
    if stale == "before":
        lock.with_name(lock.name + ".holder").write_text(described, encoding="utf-8")
    keeper = _holding(lock, holder)
    if stale == "after":
        # A holder from an older version clears nothing, so the description is still there while it
        # holds the lock; only the process's own command line can tell.
        lock.with_name(lock.name + ".holder").write_text(described, encoding="utf-8")
    try:
        supervisor = JobSupervisor(project.path)
        assert supervisor._stop_orphan(row) is False
        time.sleep(0.3)
        assert unrelated.poll() is None, "the unrelated process lives"
        assert any("was not stopped" in problem for problem in supervisor.problems)
    finally:
        for child in (unrelated, keeper):
            if child.poll() is None:
                child.kill()
            child.wait(timeout=PATIENCE)
        if keeper.stdout is not None:
            keeper.stdout.close()


def test_pruning_reads_only_what_it_decides_by(project: Project) -> None:
    """Pruning needs each job's identifier, state and order. It must not read every column of
    every job, because a row it did not need -- here, progress that is not JSON -- would stop
    it."""
    from ingence.jobs.supervisor import prune_logs

    ledger = JobLedger(project.path)
    first = _ended_job(ledger, JobState.DONE, "kelt")
    last = _ended_job(ledger, JobState.DONE, "vorn")
    writer = sqlite3.connect(ledger.path, isolation_level=None)
    try:
        writer.execute("UPDATE jobs SET progress_json = 'not json' WHERE job_id = ?", (first,))
    finally:
        writer.close()
    removed = prune_logs(project.path, keep=1)
    assert [path.name for path in removed] == [f"{first}-1.log"]
    assert (project.path / "jobs" / "logs" / f"{last}-1.log").is_file()


def test_a_restore_is_refused_in_words_while_a_dashboard_job_publishes(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Going back to an earlier run moves the pointer, and a job running meanwhile would publish
    over the restore. The restore route takes the publish lock."""
    from test_durable_jobs import _request

    path = project.path
    release = tmp_path / "release"
    monkeypatch.setenv(PAUSE_ENV, f"binding:{release}")
    with DashboardServer(path, port=0) as server:
        base = server.url.rstrip("/")
        job_id = _define(server.url, "vorn3q")["applying"]["job_id"]
        _until(lambda: _reached(release), "the worker reached 'binding'")
        before = read_pointer(path)
        assert before is not None
        status, shown, _ = _request(f"{base}/api/v1/restore/preview?run={CENSUS_RUN}")
        assert status == 200, shown
        status, said, _ = _request(
            f"{base}/api/workspace/restore",
            {"run": CENSUS_RUN, "actor": "a person", "previewed": shown["digest"]},
        )
        assert status == 400, said
        assert "Dashboard job" in said["error"] and job_id in said["error"], said
        assert "restore" in said["error"]
        after = read_pointer(path)
        assert after is not None and after.run_id == before.run_id, "the pointer did not move"
        release.write_text("go", encoding="utf-8")
        assert server.applier.wait(timeout=PATIENCE)


def test_a_process_is_taken_for_a_worker_only_by_its_own_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What ``_stop_orphan`` reads before it stops anything: the module the supervisor spawns,
    this project and this job, as ``spawn_worker`` writes them; and on a platform whose command
    lines are not read here, nothing at all, so nothing is stopped."""
    from types import SimpleNamespace

    from ingence.jobs import supervisor as supervisor_module
    from ingence.jobs.supervisor import command_line, runs_worker

    project = tmp_path / "kelt project"
    job = "job_20200101T000000Z_00000000"
    spawned = f"/usr/bin/python3 -m ingence.jobs.worker --project {project} --job {job} --attempt 2"
    assert runs_worker(spawned, project, job)
    assert not runs_worker(spawned.replace(job, job[:-1] + "1"), project, job), "another job"
    assert not runs_worker(spawned.replace("kelt project", "vorn project"), project, job)
    assert not runs_worker(spawned.replace(str(project), f"{project}-copy"), project, job), (
        "a project whose name only begins with this one's"
    )
    assert not runs_worker(f"/bin/sleep 60 --project {project} --job {job}", project, job)
    shorter = tmp_path / "kelt"  # a project whose path this one's begins with, up to a space
    assert not runs_worker(spawned, shorter, job)
    assert not runs_worker(f"python -c 'import ingence.jobs.worker' --job {job}", project, job)

    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL
    )
    try:
        seen = command_line(unrelated.pid)
        if sys.platform in ("darwin", "linux"):
            assert seen is not None and "time.sleep(60)" in seen, seen
            assert not runs_worker(seen, project, job)
        monkeypatch.setattr(supervisor_module, "sys", SimpleNamespace(platform="win32"))
        assert command_line(unrelated.pid) is None, "not read on Windows: nothing is stopped"
    finally:
        unrelated.kill()
        unrelated.wait(timeout=PATIENCE)


def test_a_job_its_worker_finished_is_never_interrupted_by_a_late_judgement(
    project: Project,
) -> None:
    """A server following a worker it did not start can read the job as running, then find the
    worker's lock let go, though the worker wrote its end between the two reads. A job whose end
    is written is never interrupted after it: the late judgement is refused, the job's end
    stands, and the job does not run a second time."""
    ledger = JobLedger(project.path)
    job = ledger.enqueue(steps=("inference",), record_sequence=0, base_run=INFER_RUN)
    claimed = ledger.claim_next(pinned=INFER_RUN)
    assert claimed is not None and claimed.job_id == job.job_id
    ended = ledger.finish(
        job.job_id, attempt=claimed.attempts, state=JobState.DONE, produced_run=INFER_RUN
    )
    assert ended is not None and ended.state is JobState.DONE
    late = ledger.interrupt(
        job.job_id, reason="its worker let go of its lock", retry_limit=2, cause="gone"
    )
    assert late.state is JobState.DONE
    held = ledger.get(job.job_id)
    assert held is not None
    assert (held.state, held.attempts, held.retries) == (JobState.DONE, 1, 0)
