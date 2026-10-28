"""The supervisor: one worker at a time, watched, and a dead one's job never silently lost.

A thread in the Dashboard's server. One server per project runs its jobs: the one holding
``jobs/supervisor.lock``, which it takes when the project first has a ledger and keeps while it
serves. A second server on the same project writes jobs to the same ledger and reads their state,
and stands by. If the first dies, the operating system lets go of its lock and the second takes
over, reconciling first.

A worker is always started as ``sys.executable -m ingence.jobs.worker``, with the running
engine's own source first on ``PYTHONPATH`` so that the worker runs the engine the server runs (a
worktree's server spawns a worktree's worker). Its input is closed and its output goes to a log
beside the ledger (``jobs/logs/<job>-<attempt>.log``). The start and the stop differ by platform:

- POSIX (macOS, Linux): ``start_new_session=True``. The worker has its own session, so the
  Ctrl-C that closes ``ingence see`` in a terminal does not reach it. The server's close stops it
  on purpose. The supervisor stops it with the subprocess object's ``kill()``, which is
  ``SIGKILL``.
- Windows: ``CREATE_NEW_PROCESS_GROUP``, which keeps a console's Ctrl-C from the worker in the
  same way. The supervisor stops it with the subprocess object's ``kill()``, which is
  ``TerminateProcess``. Windows has no process-group kill of the POSIX kind, and none is needed,
  because the worker starts no children that outlive it (its extraction subprocesses are
  supervised and die with it).

The supervisor draws its conclusions about a worker it started from the subprocess's exit and
from its heartbeat. A worker not heard from for ``jobs.silent_seconds`` of the supervisor's own
time is reported silent (the page says so, and says what will happen). It is stopped and run
again once it has been silent for ``jobs.stop_silent_seconds``, where ``jobs.stop_silent`` says
so. That setting is on by default. Silence is not proof of a freeze: measurement showed that one
long call which keeps the interpreter lock can starve a healthy worker's heartbeat. That is why
the stop waits much longer than the report, so a slow machine is told long before anything is
stopped. The supervisor hears a worker from
before, followed after a restart, the same way, from its beats in the ledger. It stops that
worker by the process identifier the worker wrote beside the worker lock for itself, never by
one the ledger merely remembers, and only once that process's own command line shows it runs
this project's worker for that job. The supervisor does not read Windows' command lines, so there
a silent worker from before is said and never stopped.

A job the worker ended (``done`` or ``failed``) is settled. A job still ``running`` when its
worker has exited was interrupted (killed, crashed, out of memory). The supervisor marks it so
with the exit code and the phase, then queues it again up to the retry limit, then marks it
``failed`` with the reason. On taking over (a server starting), a job the ledger says is
``running`` belongs to a supervisor that is gone. If no process holds the worker lock, its worker
is gone too, and the job is interrupted and queued again the same way. If a worker from before
still holds the lock (the server died and its worker did not), the supervisor follows it until it
lets go, and judges the job then.

How the worker ended decides whether the job runs again. A kill and a worker found gone may be
accidents of the machine and are retried up to ``jobs.retry_limit``. A crash (a segmentation
fault, an abort) and a stopped silence are not accidents, and one that repeats in the same phase
fails the job then (:data:`~ingence.jobs.ledger.NOT_ACCIDENTS`). A worker that raised has already
failed its job itself, with the error, and is never retried, because the same input would raise
again.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import open_appending
from ingence.jobs import JOBS_DIR, PHASE_WORDS, SUPERVISOR_LOCK, WORKER_LOCK, JobState, phase_words
from ingence.jobs.ledger import STOPPED, JobLedger, JobRow
from ingence.jobs.ledger import now as ledger_now
from ingence.jobs.locks import FileLock, held_elsewhere, holder_of
from ingence.jobs.worker import EXIT_LOCK_BUSY, EXIT_SUPERSEDED, Steps, run_job
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "EXECUTORS",
    "WORKER_MODULE",
    "JobSupervisor",
    "command_line",
    "ended_how",
    "prune_logs",
    "runs_worker",
    "spawn_worker",
    "stop_grace",
    "worker_alive",
]

#: The module every worker runs (``spawn_worker``), which a process's command line must name
#: before the supervisor stops it as a worker from before.
WORKER_MODULE = "ingence.jobs.worker"

#: Where a job runs: a process of its own (the default), or a thread of the server's (for a
#: build that cannot start its own interpreter, and for tests that stub the engine).
EXECUTORS = ("subprocess", "thread")

#: How often the supervisor looks at a worker, and at the ledger when idle, in seconds.
POLL_SECONDS = 0.1
#: How long a worker may take to start (interpreter, imports) before its silence counts.
STARTUP_ALLOWANCE_SECONDS = 120.0
#: A jump between two of the supervisor's looks at a worker longer than this is time it was not
#: listening (the machine asleep, on a clock that runs through sleep) and not the worker's
#: silence. The supervisor looks every tenth of a second, and fifty times that is no wait it makes
#: on purpose.
LISTENING_GAP_SECONDS = 5.0
#: How many faults the supervisor keeps to say (the latest).
PROBLEMS_KEPT = 20
#: How long an idle supervisor sleeps before looking at the ledger again for jobs another
#: server wrote (its own server wakes it at once).
IDLE_SECONDS = 0.5
#: How long a worker has to stop itself after a person's Stop, by the phase it is in, before the
#: supervisor stops it. The names, the rules and the views get seconds, because they look at
#: their token between items. The file list and the contents get longer, because one call into a
#: share can hold for a while. Then the supervisor sends ``SIGKILL``, and the job ends
#: ``cancelled``.
STOP_GRACE_SECONDS: dict[str, float] = {
    "census": 20.0,
    "dive": 60.0,
    "binding": 10.0,
    "inferring": 10.0,
    "views": 10.0,
}
STOP_GRACE_DEFAULT_SECONDS = 10.0


def stop_grace(phase: str | None) -> float:
    """Seconds a worker in ``phase`` has to stop itself before it is stopped."""
    return STOP_GRACE_SECONDS.get(phase or "", STOP_GRACE_DEFAULT_SECONDS)


def prune_logs(project: Path, *, keep: int) -> list[Path]:
    """Remove the worker logs of all but the newest ``keep`` jobs. Return what was removed.

    The function never removes a log of a job queued or running (a retry's earlier attempts
    explain it), nor of a failed job that nothing has succeeded since. That log is the latest word
    on the project, and the ledger does not record whether a person has seen it. A log whose job
    the ledger does not hold is one the page cannot show, and goes. Only files named as the
    supervisor names a worker's log are touched.
    """
    # The project's own logs folder, nothing on the way to it a link, before anything is removed.
    logs = owned_path(project, JOBS_DIR, "logs")
    if not logs.is_dir():
        return []
    jobs = JobLedger(project).summaries()  # (ordinal, job_id, state), oldest first
    newest = {job_id for _, job_id, _ in jobs[-keep:]} if keep > 0 else set()
    busy = {job_id for _, job_id, state in jobs if state in (JobState.QUEUED, JobState.RUNNING)}
    last_done = max((ordinal for ordinal, _, state in jobs if state is JobState.DONE), default=0)
    unseen = {
        job_id
        for ordinal, job_id, state in jobs
        if state is JobState.FAILED and ordinal > last_done
    }
    kept = newest | busy | unseen
    removed: list[Path] = []
    for log in sorted(logs.glob("job_*-*.log")):
        job_id, _, attempt = log.stem.rpartition("-")
        if not attempt.isdigit() or job_id in kept:
            continue
        log.unlink(missing_ok=True)
        removed.append(log)
    return removed


def worker_alive(project: Path) -> bool:
    """Whether a worker holds this project's worker lock now: a worker is running a job."""
    return held_elsewhere(project / JOBS_DIR / WORKER_LOCK)


def _current_run(project: Path) -> str | None:
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(project)
    return pointer.run_id if pointer is not None else None


def spawn_worker(
    project: Path, job: JobRow, *, heartbeat_seconds: float
) -> tuple[subprocess.Popen[bytes], Path]:
    """Start the worker process for one attempt of one job, and say where its log is."""
    import ingence

    source = str(Path(ingence.__file__).resolve().parent.parent)
    environment = dict(os.environ)
    already = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(part for part in (source, already) if part)
    # The project's own logs folder, nothing on the way a link, and never a link at the log itself.
    logs = owned_path(project, JOBS_DIR, "logs")
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{job.job_id}-{job.attempts}.log"
    # Its own process group (Windows) or session (POSIX), so a console's Ctrl-C meant for the
    # server does not reach the worker. The server stops it on purpose when it closes.
    # On Windows it is also detached from the console: a new process group keeps
    # Ctrl-C away, but closing the console window reaches every process attached to it, and the
    # worker's output goes to its log file anyway.
    platform: dict[str, Any] = (
        {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        }
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    with open_appending(log_path, binary=True) as log:
        process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
            [
                sys.executable,
                "-m",
                WORKER_MODULE,
                "--project",
                str(project),
                "--job",
                job.job_id,
                "--attempt",
                str(job.attempts),
                "--heartbeat",
                str(heartbeat_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            **platform,
        )
    return process, log_path


class _Hearing:
    """When a worker's heartbeat last changed, on the supervisor's own monotonic clock.

    A hearing is measured on the supervisor's own monotonic clock and never from the heartbeat's
    wall-clock time. A laptop that slept for an hour wakes with every heartbeat an hour old, and a
    supervisor judging by the wall clock would stop a worker that is fine. Silence is only time
    the supervisor spent listening. The monotonic clock stands still while the machine sleeps on
    macOS (``mach_absolute_time``, measured) and Linux (``CLOCK_MONOTONIC``). On Windows before
    Python 3.13 it runs on through a sleep, so a laptop asleep for a quarter of an hour mid-job
    would wake to a quarter of an hour of "silence" and stop a healthy worker. So a jump between
    two looks longer than :data:`LISTENING_GAP_SECONDS` (the supervisor looks every tenth of a
    second) is not counted. The reference moves forward by it, on every platform.

    A failed read of the ledger counts as not heard. The hearing hands the fault to
    ``on_fault`` to be said, and the silence goes on being counted from the last beat heard.
    The ledger is read at most twice a second.
    """

    def __init__(
        self,
        ledger: JobLedger,
        job_id: str,
        *,
        allowance: float = STARTUP_ALLOWANCE_SECONDS,
        on_fault: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._ledger = ledger
        self.job_id = job_id
        self._on_fault = on_fault
        #: The heartbeat the claim wrote. The worker has spoken once it differs.
        self._last: str | None = self._read()
        self._began = time.monotonic()
        self._changed: float | None = None
        self._looked = 0.0
        self._previous = self._began
        #: How long a start may take before its silence counts: none for a worker from before,
        #: which started long ago, and whose silence counts from when it was first listened to.
        self._allowance = allowance

    def _read(self) -> str | None:
        try:
            held = self._ledger.get(self.job_id)
        except (sqlite3.Error, OSError) as error:
            if self._on_fault is not None:
                self._on_fault(error)
            return self._last if hasattr(self, "_last") else None
        return held.heartbeat_at if held is not None else None

    def silent_for(self) -> float:
        """Seconds since the worker was last heard, or 0 while it starts, up to the allowance."""
        now = time.monotonic()
        away = now - self._previous
        self._previous = now
        if away > LISTENING_GAP_SECONDS:
            # Not listening (asleep, or this thread held off): that time is nobody's silence.
            self._began += away
            if self._changed is not None:
                self._changed += away
        if now - self._looked >= 0.5:
            self._looked = now
            beat = self._read()
            if beat != self._last:
                self._last = beat
                self._changed = now
        if self._changed is None:
            # Starting: an interpreter and the engine's imports come before the first beat, and
            # take seconds on a loaded machine. Silence is judged from the first beat on, or once
            # the start itself has taken longer than any start should.
            started = now - self._began
            return started if started >= self._allowance else 0.0
        return now - self._changed


#: What a job a dead worker left was doing, in a person's words (the one list of
#: ``ingence.jobs``).
_PHASE_WORDS = PHASE_WORDS


class JobSupervisor:
    """Runs a project's queued jobs one at a time, in workers it watches."""

    def __init__(
        self,
        project: Path,
        *,
        executor: str = "subprocess",
        retry_limit: int = 2,
        heartbeat_seconds: float = 2.0,
        silent_seconds: float = 60.0,
        stop_silent: bool = True,
        stop_silent_seconds: float = 900.0,
        logs_kept: int = 100,
        steps: Callable[[], Steps] | None = None,
        hold_what_waited: bool = True,
    ) -> None:
        if executor not in EXECUTORS:
            message = f"a job runs in one of {EXECUTORS}, not {executor!r}"
            raise ValueError(message)
        self.project = project
        self.ledger = JobLedger(project)
        self.executor = executor
        self.retry_limit = retry_limit
        self.heartbeat_seconds = heartbeat_seconds
        self.silent_seconds = silent_seconds
        self.stop_silent = stop_silent
        #: Silence long enough to stop a worker, where ``stop_silent`` says to. Never shorter
        #: than the silence that is reported.
        self.stop_silent_seconds = max(stop_silent_seconds, silent_seconds)
        self.logs_kept = logs_kept
        #: Seconds the running worker has not been heard from, where that passed
        #: ``silent_seconds``, and None while it is heard. The page says it.
        self.silent_for: float | None = None
        #: Who holds the project's worker lock where it is not a worker of this supervisor's,
        #: such as a hand-run command, while jobs wait for it. None otherwise. The page says it.
        self.held_by: dict[str, Any] | None = None
        self._hearing: _Hearing | None = None
        self._steps = steps
        self._lock = FileLock(project / JOBS_DIR / SUPERVISOR_LOCK)
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._changed = threading.Condition()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        #: What reconciliation found, in words, oldest first: a server prints these.
        self.reconciled: list[str] = []
        #: Whether a job left queued by a Dashboard that closed waits for a person. True, the
        #: default, keeps the rule that no run starts by itself: the job is held, said, and
        #: started by the next Re-run (:meth:`release_held`). False: the supervisor resumes the job
        #: at take-over.
        self.hold_what_waited = hold_what_waited
        #: When this supervisor was made, by the ledger's clock: only a job asked for before it
        #: is one a close left (a job a person asks for here is never held, whenever the
        #: supervisor takes over).
        self._made_at = ledger_now()
        #: A fault the loop met and survived, in words, for the page and the log.
        self.problems: list[str] = []
        self._following = False

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> list[str]:
        """Take over the project's jobs if nobody runs them, reconcile, and start watching."""
        with self._guard:
            if self._thread is not None:
                return list(self.reconciled)
            try:
                self._take_over()
            except Exception as error:  # the Dashboard opens whatever the ledger holds
                self._fault(error)
                self.reconciled.append(
                    f"The job ledger at {self.ledger.path} could not be read ({error}); the "
                    "Dashboard serves the published runs, and applies no answer until it can."
                )
            self._thread = threading.Thread(target=self._loop, name="ingence-jobs", daemon=True)
            self._thread.start()
        return list(self.reconciled)

    def stop(self) -> None:
        """Stop watching. A worker still running is stopped and its job queued again."""
        self._stopped.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=60)
        self._lock.release()

    def release_held(self) -> None:
        """A person's Re-run, on this Dashboard or another serving the project: held jobs run."""
        self.ledger.release_held()
        self._wake.set()

    def holding(self) -> set[str]:
        """The queued jobs a close stopped, waiting for a person (kept in the ledger)."""
        return self.ledger.held()

    def worker_process(self) -> subprocess.Popen[bytes] | None:
        """The worker process running now, where this supervisor started one."""
        return self._process

    def wait_for_change(self, timeout: float) -> None:
        """Block until a job ends here, or ``timeout`` passes."""
        with self._changed:
            self._changed.wait(timeout)

    # -- the loop ----------------------------------------------------------------------------

    def _fault(self, error: BaseException) -> None:
        """Keep a fault the loop survived, once per kind of fault, the latest few."""
        said = f"{type(error).__name__}: {error}"
        if not self.problems or self.problems[-1] != said:
            self.problems.append(said)
            del self.problems[:-PROBLEMS_KEPT]

    def _notify(self) -> None:
        with self._changed:
            self._changed.notify_all()

    def _idle(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _take_over(self) -> bool:
        """Hold the supervisor lock, reconciling on the moment it is taken. Say if it is held."""
        if self._lock.held:
            return True
        if not self.ledger.exists() or not self._lock.acquire():
            return False
        self.reconciled.extend(self._reconcile())
        return True

    def _reconcile(self) -> list[str]:
        """Settle what a supervisor that is gone left running, and hold what it left queued."""
        said: list[str] = []
        #: What each job a dead worker left was doing, for the one line said of it.
        interrupted: dict[str, str] = {}
        for job in self.ledger.in_state(JobState.RUNNING):
            if worker_alive(self.project) and self._runs_it(job):
                self._following = True
                said.append(
                    f"Job {job.job_id} is still running in a worker started before this "
                    "Dashboard; following it."
                )
                continue
            phase = job.phase or "its start"
            stopped = self._stop_asked(job.job_id)
            after = self.ledger.interrupt(
                job.job_id,
                reason=(
                    f"a person had stopped it, and it was gone when the Dashboard started "
                    f"({phase_words(job.phase)}, attempt {job.attempts})"
                    if stopped
                    else f"its worker was gone when the Dashboard started (it was in {phase}, "
                    f"attempt {job.attempts})"
                ),
                retry_limit=self.retry_limit,
                cause=STOPPED if stopped else "gone",
            )
            if stopped:
                self._point_back(job)
                said.append(f"Job {job.job_id} was stopped by a person and will not run again.")
                continue
            if after.state is JobState.QUEUED and self.hold_what_waited:
                interrupted[job.job_id] = _PHASE_WORDS.get(job.phase or "", "starting")
                continue
            outcome = (
                f"it runs again (retry {after.retries} of {self.retry_limit})"
                if after.state is JobState.QUEUED
                else f"it will not run again: {after.error}"
            )
            said.append(
                f"Job {job.job_id} was interrupted during {phase}: its worker was gone when "
                f"the Dashboard started; {outcome}."
            )
        # No answer queues a job, and the server's one line says in words what waits for Re-run.
        # What was asked and never started, or was interrupted (the Dashboard closed first, or
        # died), waits for a person. The ledger holds it where every server reads it, and the
        # server says so in one line of words.
        queued = [
            job
            for job in self.ledger.in_state(JobState.QUEUED)
            if not job.created_at or job.created_at < self._made_at
        ]
        if self.hold_what_waited and queued:
            self.ledger.hold(job.job_id for job in queued)
            doing = sorted(set(interrupted.values()))
            how = f" (it was interrupted while {' and '.join(doing)})" if doing else ""
            said.append(
                f"A re-run stopped when the Dashboard last closed{how}; Re-run starts it again."
            )
        elif queued:
            waiting = [job for job in queued if not any(job.job_id in line for line in said)]
            if waiting:
                names = ", ".join(job.job_id for job in waiting)
                said.append(
                    f"{len(waiting)} job{'s' if len(waiting) != 1 else ''} waited for this "
                    f"Dashboard and {'run' if len(waiting) != 1 else 'runs'} now: {names}."
                )
        return said

    def _worker_lock(self) -> Path:
        return self.project / JOBS_DIR / WORKER_LOCK

    def _runs_it(self, job: JobRow) -> bool:
        """Whether the lock's holder is this job's worker (or says nothing of itself: a worker
        from an older engine, which wrote no description, is taken at its word)."""
        held = holder_of(self._worker_lock())
        if held is None:
            return True
        return held.get("role") == "worker" and held.get("job_id") == job.job_id

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                if not self._take_over():
                    self._idle(IDLE_SECONDS)
                    continue
                if self._following:
                    self._follow()
                    continue
                self._settle_left_running()
                if worker_alive(self.project):
                    # Somebody else holds the project, such as a hand-run ``index`` or
                    # ``infer``. A job started now would publish over it, so jobs wait, and the
                    # page says for whom.
                    self.held_by = holder_of(self._worker_lock()) or {"role": "unknown"}
                    self._notify()
                    self._idle(IDLE_SECONDS)
                    continue
                self.held_by = None
                if self.holding():
                    # A job a close stopped waits for a person's Re-run.
                    self._notify()
                    self._idle(IDLE_SECONDS)
                    continue
                job = self.ledger.claim_next(pinned=_current_run(self.project))
                if job is None:
                    self._notify()
                    self._idle(IDLE_SECONDS)
                    continue
                self._run(job)
                self._notify()
                try:
                    prune_logs(self.project, keep=self.logs_kept)
                except OSError as error:  # a log left is never worth a job's outcome
                    self._fault(error)
            except Exception as error:  # the loop outlives any one fault, and says so
                self._fault(error)
                self._notify()
                self._idle(1.0)
        self._notify()

    def _settle_left_running(self) -> None:
        """Settle a job the ledger says runs while no worker does and none of ours is starting.

        Called only between jobs, from the loop that starts them, so no worker of this
        supervisor's is between its claim and its lock. A worker's end that the supervisor could
        not write down (the ledger refused the write, as a full disk makes it) would otherwise
        leave its job running for ever with nothing running it.
        """
        for job in self.ledger.in_state(JobState.RUNNING):
            if worker_alive(self.project):
                return
            stopped = self._stop_asked(job.job_id)
            self.ledger.interrupt(
                job.job_id,
                reason=(
                    f"a person had stopped it, and its end was not written then "
                    f"({phase_words(job.phase)}, attempt {job.attempts})"
                    if stopped
                    else f"its worker was gone and its end could not be written then (it was in "
                    f"{job.phase or 'its start'}, attempt {job.attempts})"
                ),
                retry_limit=self.retry_limit,
                cause=STOPPED if stopped else "gone",
            )
            if stopped:
                self._point_back(job)
            self._notify()

    def _follow(self) -> None:
        """Watch a worker from before until it lets go of its lock, then judge its job.

        The supervisor hears the worker as it hears a worker of its own: by its beats in the
        ledger, on this supervisor's clock from when it began listening. Silence past
        ``silent_seconds`` is said, and past ``stop_silent_seconds`` the worker is stopped where
        the settings say.
        """
        running = self.ledger.in_state(JobState.RUNNING)
        if worker_alive(self.project) and not self._stopped.is_set():
            if running:
                job = running[0]
                if self._hearing is None or self._hearing.job_id != job.job_id:
                    self._hearing = _Hearing(
                        self.ledger, job.job_id, allowance=0.0, on_fault=self._fault
                    )
                silent = self._hearing.silent_for()
                self.silent_for = silent if silent > self.silent_seconds else None
                if (
                    self.silent_for is not None
                    and self.stop_silent
                    and silent > self.stop_silent_seconds
                    and self._stop_orphan(job)
                ):
                    self._following = False
                    self._hearing = None
                    self.silent_for = None
                    self.ledger.interrupt(
                        job.job_id,
                        reason=(
                            f"its worker, started before this Dashboard, was not heard from for "
                            f"{self.stop_silent_seconds:.0f} s during {job.phase or 'its start'} "
                            "and was stopped"
                        ),
                        retry_limit=self.retry_limit,
                        cause="silent",
                    )
                    self._notify()
                    return
            self._idle(POLL_SECONDS)
            return
        self._following = False
        self._hearing = None
        self.silent_for = None
        for job in running:
            self.ledger.interrupt(
                job.job_id,
                reason=(
                    f"its worker, started before this Dashboard, stopped during "
                    f"{job.phase or 'its start'} without finishing"
                ),
                retry_limit=self.retry_limit,
                cause="exited",
            )
        self._notify()

    def _stop_orphan(self, job: JobRow) -> bool:
        """Stop a worker from before, by the identifier it wrote for itself. Say if it stopped.

        The function stops only a process shown to be this job's worker, by three things that
        must agree. The worker described itself as this job's beside the lock, with the
        identifier the ledger holds for the attempt. The process with that identifier now runs
        ``ingence.jobs.worker`` for this project and this job, read from its own command line. A
        description can be stale (left by a worker that died, under a holder that clears
        nothing) and an identifier can have been reused by any process, but the command line is
        the process's own. Where it cannot be read (Windows, here) nothing is stopped, and the
        silence is said.
        """
        held = holder_of(self._worker_lock())
        pid = job.worker_pid
        if (
            held is None
            or pid is None
            or held.get("role") != "worker"
            or held.get("job_id") != job.job_id
            or held.get("pid") != pid
        ):
            self._fault(
                RuntimeError(
                    f"job {job.job_id}'s worker is silent and was not stopped: it did not say "
                    "who it is beside the worker lock"
                )
            )
            return False
        command = command_line(pid)
        if command is None or not runs_worker(command, self.project, job.job_id):
            why = (
                "its command line could not be read"
                if command is None
                else "that process is not this project's worker for it"
            )
            self._fault(
                RuntimeError(
                    f"job {job.job_id}'s worker is silent and was not stopped: process {pid} is "
                    f"named for it, and {why}"
                )
            )
            return False
        # SIGKILL on POSIX. On Windows ``os.kill`` with any other signal is TerminateProcess.
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        deadline = time.monotonic() + 60
        while worker_alive(self.project) and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
        return not worker_alive(self.project)

    def _run(self, job: JobRow) -> None:
        if self.executor == "thread":
            self._run_in_thread(job)
            return
        process, log_path = spawn_worker(
            self.project, job, heartbeat_seconds=self.heartbeat_seconds
        )
        self._process = process
        heard = _Hearing(self.ledger, job.job_id, on_fault=self._fault)
        try:
            try:
                self.ledger.attach(
                    job.job_id, attempt=job.attempts, pid=process.pid, log_path=str(log_path)
                )
            except (sqlite3.Error, OSError) as error:
                # The worker writes its own identifier when it starts. The log path waits. A
                # ledger that refused one write never makes the supervisor let go of its worker.
                self._fault(error)
            stop_asked_at: float | None = None
            stop_looked = 0.0
            while True:
                try:
                    code = process.wait(timeout=POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    if self._stopped.is_set():
                        process.kill()
                        code = process.wait(timeout=60)
                        self._settle(job, code, closing=True)
                        return
                    # A person's Stop: the worker reads the request at its next beat and ends the
                    # step in hand. A worker that has not stopped after the phase's grace is
                    # stopped here, and its job ends cancelled. A Stop that the worker answered by
                    # keeping what was read waits no longer. The supervisor looks at the request
                    # again and forgets the grace once the request is answered, so a job applying
                    # the rules to what was kept is not stopped for it.
                    looked = time.monotonic()
                    if looked - stop_looked >= 0.5:
                        stop_looked = looked
                        try:
                            if self.ledger.stop_requested(job.job_id) is None:
                                stop_asked_at = None
                            elif stop_asked_at is None:
                                stop_asked_at = looked
                        except (sqlite3.Error, OSError) as error:
                            self._fault(error)
                    if stop_asked_at is not None:
                        phase = (self.ledger.get(job.job_id) or job).phase
                        if looked - stop_asked_at > stop_grace(phase):
                            process.kill()
                            process.wait(timeout=60)
                            self._settle(
                                job,
                                None,
                                closing=False,
                                said="stopped by a person",
                                cause=STOPPED,
                            )
                            return
                    try:
                        silent = heard.silent_for()
                    except Exception as error:  # The waiting goes on. The fault is said.
                        self._fault(error)
                        continue
                    self.silent_for = silent if silent > self.silent_seconds else None
                    if (
                        self.silent_for is not None
                        and self.stop_silent
                        and silent > self.stop_silent_seconds
                    ):
                        process.kill()
                        process.wait(timeout=60)
                        self._settle(
                            job,
                            None,
                            closing=False,
                            said=(
                                f"was not heard from for {self.stop_silent_seconds:.0f} s and "
                                "was stopped"
                            ),
                            cause="silent",
                        )
                        return
            self._settle(job, code, closing=False)
        finally:
            self._process = None
            self.silent_for = None

    def _run_in_thread(self, job: JobRow) -> None:
        outcome: list[int] = []

        def work() -> None:
            outcome.append(
                run_job(
                    self.project,
                    job.job_id,
                    job.attempts,
                    steps=self._steps() if self._steps is not None else None,
                    heartbeat_seconds=self.heartbeat_seconds,
                )
            )

        thread = threading.Thread(target=work, name="ingence-job", daemon=True)
        thread.start()
        thread.join()
        self._settle(job, outcome[0] if outcome else 1, closing=False)

    def _settle(
        self,
        job: JobRow,
        code: int | None,
        *,
        closing: bool,
        said: str | None = None,
        cause: str | None = None,
    ) -> None:
        """Judge a worker that has exited by what the ledger says of its job."""
        now = self.ledger.get(job.job_id)
        if now is None or now.state is not JobState.RUNNING or now.attempts != job.attempts:
            return  # the worker ended it, or it is another attempt's now
        phase = now.phase or "its start"
        if closing:
            self.ledger.interrupt(
                job.job_id,
                reason=f"the Dashboard closed while it ran (during {phase})",
                retry_limit=self.retry_limit,
                counts=False,
            )
            return
        if code == EXIT_LOCK_BUSY:
            reason = f"another worker held the project's worker lock (exit code {code})"
            self.ledger.interrupt(
                job.job_id, reason=reason, retry_limit=self.retry_limit, counts=False
            )
            return
        if said is None:
            cause = ended_how(code)
            said = (
                "was superseded"
                if code == EXIT_SUPERSEDED
                else f"{_ENDED_WORDS[cause]} ({_code_words(code)})"
            )
        if cause != STOPPED and self._stop_asked(job.job_id):
            # A worker that died on a person's Stop before writing its own end: cancelled, not
            # interrupted, so it is never run again.
            cause = STOPPED
            said = "stopped by a person"
        if cause == STOPPED:
            # The run current goes back to the one before the job. A rebind's names may have
            # been published since, and they hold no conclusions.
            self.ledger.interrupt(
                job.job_id,
                reason=f"{said} {phase_words(now.phase)}",
                retry_limit=self.retry_limit,
                cause=STOPPED,
            )
            self._point_back(now)
            return
        self.ledger.interrupt(
            job.job_id,
            reason=f"its worker {said} during {phase} without finishing",
            retry_limit=self.retry_limit,
            cause=cause,
        )

    def _point_back(self, job: JobRow) -> None:
        """After a Stop: the run current when the job was asked for is current again, where a
        step of the job had published one since (``storage.commit.point_back_to``)."""
        from ingence.storage.commit import point_back_to

        if job.base_run is None:
            return
        try:
            point_back_to(self.project, job.base_run, updated_at=ledger_now())
        except Exception as error:  # the base run gone, or the pointer unwritable: said
            self._fault(error)

    def _stop_asked(self, job_id: str) -> bool:
        try:
            return self.ledger.stop_requested(job_id) is not None
        except (sqlite3.Error, OSError):
            return False


def command_line(pid: int) -> str | None:
    """A process's command line as one string, or None where it cannot be read here.

    Linux: ``/proc/<pid>/cmdline``. macOS: ``ps -ww -p <pid> -o command=`` (``/bin/ps``, part of
    the system). Windows: not read, because its command line needs the process's own memory or a
    management query, and neither is cheap. The result is None, and the caller stops nothing.
    """
    platform = sys.platform  # read at call time, so either branch can be exercised anywhere
    if platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        return " ".join(parts) or None
    if platform == "darwin":
        try:
            shown = subprocess.run(  # noqa: S603 - the system's ps, a number as its argument
                ["/bin/ps", "-ww", "-p", str(int(pid)), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        said = shown.stdout.strip()
        return said if shown.returncode == 0 and said else None
    return None


def runs_worker(command: str, project: Path, job_id: str) -> bool:
    """Whether a command line runs this project's worker for this job, as ``spawn_worker``
    writes it: ``-m ingence.jobs.worker --project <project> --job <job> ``, in that order, so a
    project whose path begins with this one's, spaces and all, is never taken for it."""
    if f"-m {WORKER_MODULE} --project " not in command:
        return False
    forms = {str(project)}
    with contextlib.suppress(OSError):
        forms.add(str(project.resolve()))
    return any(f"--project {form} --job {job_id} " in f"{command} " for form in forms)


#: The signals by which a process faults on its own, rather than being stopped from outside.
_CRASH_SIGNALS = frozenset(
    int(number)
    for name in ("SIGSEGV", "SIGBUS", "SIGILL", "SIGFPE", "SIGABRT", "SIGSYS")
    if (number := getattr(signal, name, None)) is not None
)
#: Windows' exception codes (``NTSTATUS`` errors: an access violation is 0xC0000005) start here.
_WINDOWS_EXCEPTION = 0xC0000000
_ENDED_WORDS = {"crashed": "crashed", "killed": "was killed", "exited": "exited"}


def ended_how(code: int | None) -> str:
    """``crashed``, ``killed`` or ``exited``: how a worker's exit code says it ended.

    On POSIX a negative code is the signal that ended it: a fault of its own (a segmentation
    fault, an abort) is a crash, anything else came from outside. On Windows a process ended by
    an unhandled exception exits with that exception's code, and ``TerminateProcess`` (a kill)
    with the code it was given, which is 1 from ``Popen.kill``.
    """
    if code is None:
        return "killed"
    if code < 0:
        return "crashed" if -code in _CRASH_SIGNALS else "killed"
    if sys.platform == "win32" and code >= _WINDOWS_EXCEPTION:  # pragma: no cover - Windows
        return "crashed"
    return "exited"


def _code_words(code: int | None) -> str:
    if code is not None and code < 0:
        try:
            return f"signal {signal.Signals(-code).name}"
        except ValueError:
            return f"signal {-code}"
    if code is not None and code >= _WINDOWS_EXCEPTION:
        return f"exit code {code:#x}"
    return f"exit code {code}"
