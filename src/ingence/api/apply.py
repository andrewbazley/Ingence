"""Apply what waits: a job that reads names again and applies the rules.

Only a person's Re-run asks for such a job, and nothing interrupts. A word's definition takes effect
after the re-run. ``meanings_in_force`` and ``changed_spellings`` below compare the meanings in
force at two readings of the Record. Nothing in the engine calls them. Tests use them to find which
spellings an answer changed. What waits is the one list in ``workspace/state.py``, and the Re-run
route asks this applier for one job. The job has two steps:

1. The job reads the names again. Where only words changed, it reads only the names that hold the
   changed words (the partial rebind). It reads every name where anything else changed or the
   partial rebind refuses.
2. The job runs inference in the background, then the readouts, while the page is told which phase
   runs, how far it is and how long is left (``progress_keys``).

Both steps are a durable job (``ingence.jobs``). The engine writes the job to the project's job
ledger before it runs. The job runs in a worker process that the server supervises, and not on a
thread of the server. The engine finds the job again, interrupted, retried, or failed with the
reason, when a worker or the server dies. A job that a close left queued waits for a person at the
next opening. The engine holds it, says so in the opening notice, and starts it on Re-run.

Nothing here touches a root. A rebind replays the stored census, so a re-run takes effect with the
share unmounted. The applier runs one worker and one job at a time. What is asked while a job
waits is gathered into it and applied together, so two jobs never publish runs over each other,
and the page reads which job it is waiting on.

While a job is queued or runs, the pages built on inference read the run that was settled when the
job was asked for (:meth:`DefinitionApplier.settled_run`). The ledger keeps that run, so a server
started after a crash keeps the pin that its predecessor held. The rebind publishes a census before
inference publishes its conclusions, and a page that read the census alone would show no question,
claim or class for the length of the inference. The pages about words read the new bindings at
once.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from typing import TYPE_CHECKING, Any, ClassVar

from ingence.core.project import Project
from ingence.core.settings import JobSettings
from ingence.dictionary.inactive import inactive_meanings
from ingence.infer.run import run_inference
from ingence.jobs import STOP_KEEPS, JobState
from ingence.jobs.ledger import FINAL_STATES, JobLedger, JobRow
from ingence.jobs.provenance import record_watermark
from ingence.jobs.supervisor import JobSupervisor
from ingence.jobs.worker import Steps
from ingence.readouts import build_readouts
from ingence.record.questions import answers_in_force, terms_in_force
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.commit import resolve_current_run

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

__all__ = ["RERUN_STEPS", "DefinitionApplier", "changed_spellings", "meanings_in_force"]

#: The steps :meth:`DefinitionApplier.submit_rerun` can run: those that read only the project.
RERUN_STEPS = frozenset({"rebind", "inference"})

#: What a first run's job says of its census and dive in the ledger (``jobs/worker.py``).
_FIRST_RUN_FIGURES = (
    "census_run",
    "census_seconds",
    "census_entries",
    "census_files",
    "census_folders",
    "census_bytes",
    "census_errors",
    "dive_run",
    "dive_seconds",
    "dive_files_read",
    "dive_bytes_read",
    "dive_left_out",
    "inferring_run",
    "views_run",
    "views_seconds",
    "views_skipped",
)


def meanings_in_force(project: Path) -> dict[str, tuple[str, ...]]:
    """Return each answered spelling and what it is in force as, from the project's own Record."""
    meanings: dict[str, set[str]] = {}
    for answer in (*answers_in_force(project), *terms_in_force(project)):
        meanings.setdefault(answer.raised.casefold(), set()).add(
            f"{answer.term}|{answer.theme_kind}|{answer.scope!r}"
        )
    # An inactivation takes effect at once too. Each spelling that it stops binding is marked, so
    # inactivating a spelling or bringing it back starts a job. The partial rebind refuses a
    # changed sense, and the worker reads every name again.
    for spelling, marks in inactive_meanings(project).items():
        meanings.setdefault(spelling, set()).update(marks)
    return {spelling: tuple(sorted(held)) for spelling, held in meanings.items()}


def changed_spellings(
    before: dict[str, tuple[str, ...]], after: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Return the spellings whose meaning in force differs between two readings of the Record."""
    return tuple(
        sorted(
            spelling
            for spelling in set(before) | set(after)
            if before.get(spelling) != after.get(spelling)
        )
    )


class DefinitionApplier:
    """Applies what a person defined: the partial rebind, then inference, one job at a time.

    The job ledger backs the applier (``ingence.jobs``). :meth:`submit` and :meth:`submit_rerun`
    write a job to the project's ledger. The supervisor runs it in a worker process of its own.
    :meth:`status`, :meth:`settled_run` and :meth:`wait` read the ledger. Its callers are the server
    and ``workspace/``. A job outlives the process that asked for it. When a worker dies, the
    applier finds the job, says so and retries it, so the job is not lost with the server.
    """

    #: The executor that every applier in this process uses where none is given and the project's
    #: settings are not to decide (``subprocess`` or ``thread``). None lets the settings decide. A
    #: test that stubs the engine sets ``thread``, because a stub does not cross a process.
    default_executor: ClassVar[str | None] = None

    def __init__(self, project: Path, *, executor: str | None = None) -> None:
        self._project = project
        self._ledger = JobLedger(project)
        settings = _job_settings(project)
        self._supervisor = JobSupervisor(
            project,
            executor=executor or self.default_executor or settings.executor,
            retry_limit=settings.retry_limit,
            heartbeat_seconds=settings.heartbeat_seconds,
            silent_seconds=settings.silent_seconds,
            stop_silent=settings.stop_silent,
            stop_silent_seconds=settings.stop_silent_seconds,
            logs_kept=settings.logs_kept,
            steps=self._engine_steps,
        )

    @staticmethod
    def _engine_steps() -> Steps:
        # The step functions are read at call time from this module, so a test's stub of either
        # reaches a job run in the server's own process (the ``thread`` executor).
        return Steps(
            census=lambda *args, **kwargs: run_census(*args, **kwargs),
            inference=lambda *args, **kwargs: run_inference(*args, **kwargs),
            deepen=lambda *args, **kwargs: run_deepen(*args, **kwargs),
            readouts=lambda *args, **kwargs: build_readouts(*args, **kwargs),
        )

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> list[str]:
        """Start supervising: reconcile what a dead predecessor left running, and say what."""
        return self._supervisor.start()

    def stop(self) -> None:
        """Stop supervising. A job still running is stopped and queued for the next start."""
        self._supervisor.stop()

    @property
    def reconciled(self) -> list[str]:
        """What reconciliation found when this server took over the project's jobs, in words."""
        return list(self._supervisor.reconciled)

    def worker_process(self) -> Any:
        """Return the worker process that is running a job now, where this server started one."""
        return self._supervisor.worker_process()

    # -- asking ------------------------------------------------------------------------------

    def submit(self, spellings: Iterable[str]) -> dict[str, Any]:
        """Queue these spellings for the next job."""
        wanted = {spelling for spelling in spellings if spelling}
        if not wanted:
            return self.status()
        self._enqueue(spellings=sorted(wanted))
        return self.status()

    def submit_rerun(
        self, steps: Iterable[str], *, spellings: Iterable[str] = ()
    ) -> dict[str, Any]:
        """Queue a re-run of what waits: read names again, apply the rules again.

        Only these two steps read the project alone. A walk or a dive reads the share and is never
        queued here. Words whose meaning changed ride along as spellings, because a definition
        waits for Re-run. With ``rebind`` every name is read again anyway. Without it, only the
        names that hold the changed words are read again (the partial rebind, which reads every
        name where it refuses).
        """
        wanted = set(steps)
        unknown = wanted - RERUN_STEPS
        if unknown or not wanted:
            message = f"the applier re-runs {sorted(RERUN_STEPS)}, not {sorted(unknown)}"
            raise ValueError(message)
        self._enqueue(spellings=sorted({word for word in spellings if word}), steps=sorted(wanted))
        return self.status()

    def submit_first_run(self, *, dive: bool) -> dict[str, Any]:
        """Queue a project's first run: a census of every root, the dive where ``dive`` says, then
        inference, as one durable job.

        Only a person's click asks for it ("Read it now" on the landing page), and only for a
        project with no run. This is the one job that walks a root. A project that has been read
        is read again by ``ingence index`` or by Re-run, and never by this. A first run that a
        close stopped is started again and not asked for twice.
        """
        if resolve_current_run(self._project) is not None:
            message = (
                "This project already has a scan. To apply changes, use Re-run in the Dashboard. "
                "To list the files again, run ingence index."
            )
            raise ValueError(message)
        busy = self._ledger.in_state(JobState.QUEUED, JobState.RUNNING)
        if busy:
            if any("census" in job.steps for job in busy):
                return self.resume()
            message = (
                "Another job is waiting or running in this project. Start again when it finishes."
            )
            raise ValueError(message)
        steps = ["census", "inference", *(["dive"] if dive else [])]
        self._enqueue(steps=sorted(steps))
        return self.status()

    def first_run_busy(self) -> bool:
        """Return whether a first run is waiting or running.

        The landing page follows the first run, and ``/`` stays the landing page until it ends, even
        once its census has published.
        """
        try:
            busy = self._ledger.in_state(JobState.QUEUED, JobState.RUNNING)
        except (sqlite3.Error, OSError):
            return False
        return any("census" in job.steps for job in busy)

    def job_busy(self) -> bool:
        """Return whether any job is queued or running.

        "Run the rules now" asks for a second job only where none is busy. The ledger joins a job
        only while it is still queued, and a second job asked while the first runs would be a second
        inference.
        """
        try:
            return bool(self._ledger.in_state(JobState.QUEUED, JobState.RUNNING))
        except (sqlite3.Error, OSError):
            return False

    def _enqueue(self, *, spellings: Iterable[str] = (), steps: Iterable[str] = ()) -> None:
        current = resolve_current_run(self._project)
        self._ledger.enqueue(
            spellings=spellings,
            steps=steps,
            # The answer is in the Record before its job is written. The job is asked for the
            # Record as it stands now, which is what the watermarks of its runs are checked against.
            record_sequence=record_watermark(self._project),
            base_run=current.name if current is not None else None,
        )
        self._supervisor.start()
        # A person asked. A job that a close stopped, which this one joins, may run now.
        self._supervisor.release_held()

    def stopped(self) -> bool:
        """Return whether a job that a close left queued waits for a person's Re-run."""
        return bool(self._supervisor.holding())

    def resume(self) -> dict[str, Any]:
        """Start the job a close stopped, as asked by a person's Re-run."""
        self._supervisor.start()
        self._supervisor.release_held()
        return self.status()

    # -- reading -----------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return what the worker is doing, what it has done, and what is waiting for it.

        The ledger supplies the status. It holds the job, its state, its retries, its heartbeat,
        and the reason for the last interruption where there was one.
        """
        try:
            state = status_from_ledger(self._ledger)
        except sqlite3.OperationalError as error:
            # On a full disk, the ledger (a WAL database) cannot be opened even to read, as a
            # measurement showed. The route says the room left beside SQLite's words, so a person
            # can tell.
            raise sqlite3.OperationalError(f"{error} ({_room_left(self._project)})") from error
        silent = self._supervisor.silent_for
        if silent is not None and state.get("state") == "running":
            # The worker's heartbeat stopped. The page says so, and says what will happen.
            state["silent_seconds"] = round(silent, 1)
            state.update(silence_words(self._supervisor, silent))
        held = self._supervisor.held_by
        if held is not None and state.get("state") == "queued":
            state["held_by"] = dict(held)
            state["held_said"] = held_words(held)
        if state.get("state") == "queued" and state.get("job_id") in self._supervisor.holding():
            # A close stopped the job, and it waits for a person. The page's opening notice says so.
            state["phase"] = "stopped"
            state["stopped"] = True
            state.update(progress_keys("stopped", list(state.get("operations") or [])))
        return state

    def settled_run(self) -> str | None:
        """Return the run that the pages built on inference read while a job runs, or None."""
        return self.reading()[0]

    def reading(self) -> tuple[str | None, bool]:
        """Return the run that reads are pinned to (None when unpinned), and whether a job is busy.

        A ledger that cannot be read never stops a page from reading a run. The page then reads the
        current run, unpinned, and ``/api/applying`` (which does not catch) says what is wrong.
        """
        try:
            pinned, busy = self._ledger.reading()
            if busy and self._only_held_busy():
                # A job that a close stopped pins no page. Until a person starts it, nothing will
                # publish. A run made since, in a terminal, is the one to read. The exception is
                # where the current run is the census that the held job published, which holds no
                # conclusions yet, so its base is still read.
                return self._held_pin(pinned)
            return pinned, busy
        except (sqlite3.Error, OSError):
            return None, False

    def _only_held_busy(self) -> bool:
        held = self._supervisor.holding()
        if not held:
            return False
        busy = self._ledger.in_state(JobState.QUEUED, JobState.RUNNING)
        return all(job.job_id in held for job in busy)

    def _held_pin(self, pinned: str | None) -> tuple[str | None, bool]:
        current = resolve_current_run(self._project)
        name = current.name if current is not None else None
        for job in self._ledger.in_state(JobState.QUEUED):
            if name is not None and name in {job.bound_run, job.progress.get("binding_run")}:
                return pinned, True
        return None, False

    def jobs(self, limit: int = 20) -> list[JobRow]:
        return self._ledger.recent(limit)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until no job is queued or running (for tests and the command line)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        if self._ledger.busy():
            # Somebody must run what waits: this server's supervisor, if none runs yet.
            self._supervisor.start()
        while self._ledger.busy():
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                return False
            self._supervisor.wait_for_change(min(0.2, left) if left is not None else 0.2)
        return True


def _room_left(project: Path) -> str:
    """Return how much room the project's disk has, in words, or why that is unknown."""
    try:
        free = shutil.disk_usage(project).free
    except OSError as error:
        return f"Ingence could not read the free space on the project's disk: {error}"
    return f"{free / 1e6:.1f} MB free on the project's disk"


def silence_words(supervisor: JobSupervisor, silent: float) -> dict[str, Any]:
    """Return what the page says of a silent worker: for how long, and what will happen and when.

    ``silent_stops_at_seconds`` is the silence at which the engine stops the worker and runs its
    job again. It is None where ``jobs.stop_silent`` is off and the worker is left to finish.
    """
    seconds = f"{silent:.0f} s"
    if supervisor.stop_silent:
        stop = supervisor.stop_silent_seconds
        return {
            "silent_stops_at_seconds": round(stop),
            "silent_said": (
                f"Ingence has had no message from this job for {seconds}. Some long steps send "
                f"none. After {stop:.0f} s with no message, Ingence stops the job and starts it "
                "again (setting jobs.stop_silent_seconds)."
            ),
        }
    return {
        "silent_stops_at_seconds": None,
        "silent_said": (
            f"Ingence has had no message from this job for {seconds}. The setting "
            "jobs.stop_silent is off, so Ingence lets it finish. Closing the Dashboard stops it."
        ),
    }


def held_words(held: dict[str, Any]) -> str:
    """Return what the page says while a job waits for somebody else who holds the project."""
    what = held.get("command") or "another program"
    pid = held.get("pid")
    return (
        f"Waiting: {what}{f' (process {pid})' if pid else ''} is saving results in this project. "
        "This job starts when it finishes."
    )


def _job_settings(project: Path) -> JobSettings:
    """Return the project's job settings, or the defaults where the project cannot be opened."""
    try:
        return Project.open(project).settings.jobs
    except Exception:  # a server still serves a project whose settings do not read
        return JobSettings()


#: The contract's phase words, by the phase that the ledger holds. A job lists files, reads
#: contents, reads names, applies rules and prepares views. The ledger keeps its own words, which
#: the worker's pause and hold name, as do its tests. ``/api/applying`` sends the contract's words
#: in ``phase`` and the ledger's in ``phase_legacy``.
PHASE_OF: dict[str, str] = {
    "census": "listing",
    "dive": "reading",
    "binding": "names",
    "inferring": "rules",
    "views": "views",
}
#: The ledger's word for each contract phase.
LEGACY_OF: dict[str, str] = {contract: ledger for ledger, contract in PHASE_OF.items()}
#: The present phase in words. A page shows them.
_PHASE_WORDS: dict[str, str] = {
    "idle": "Nothing is running",
    "off": "Nothing is running",
    "queued": "Waiting to start",
    "stopped": "Stopped",
    "listing": "Listing files",
    "reading": "Reading contents",
    "names": "Reading names",
    "rules": "Applying rules",
    "views": "Preparing views",
    "running": "Running",
    "done": "Done",
    "failed": "Did not finish",
    "interrupted": "Interrupted",
    "cancelled": "Stopped",
}
#: Which of a job's operations each phase runs. This is the step that the page counts.
_OPERATION_OF_PHASE: dict[str, tuple[str, ...]] = {
    "listing": ("census",),
    "reading": ("dive",),
    "names": ("partial_rebind", "whole_rebind"),
    "rules": ("inference",),
    "views": ("views",),
}
#: What each phase counts, and the progress key that names the run whose snapshot holds the count.
_UNIT_OF_PHASE: dict[str, str] = {
    "listing": "files",
    "reading": "files",
    "names": "names",
    "rules": "rules",
    "views": "views",
}
_RUN_OF_PHASE: dict[str, str] = {
    "listing": "census_run",
    "reading": "dive_run",
    "names": "binding_run",
    "rules": "inferring_run",
    "views": "views_run",
}
#: The snapshot's counters for each phase: what is done, and the total where one is known.
_COUNTS_OF_PHASE: dict[str, tuple[str, str | None]] = {
    "listing": ("files_found", None),
    "reading": ("entries_examined", "total_entries"),
    "names": ("entries_seen", "total_entries"),
    "rules": ("rules_run", "rules_total"),
    "views": ("views_done", "views_total"),
}
#: How far along a phase must be before the function estimates its time left from its rate.
_ESTIMATE_FROM = 0.05


def progress_keys(
    phase: str,
    operations: tuple[str, ...] | list[str] = (),
    *,
    ended: bool = False,
    running: bool = False,
    counts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the progress keys of ``/api/applying``, beside the keys that it had.

    ``phase`` is the contract's word. ``phase_label`` is the phase in words. ``step`` and ``steps``
    are read from the job's operations: the one that the phase runs, and how many there are, with
    the views counted as one more. Every step counts once the job has ended. ``unit``, ``done``,
    ``total``, ``rate_per_s`` and ``seconds_left`` come from the phase's snapshot (``counts``, the
    file that the worker's reporter writes). The time left is estimated as elapsed times what
    remains over what is done, and only once :data:`_ESTIMATE_FROM` of a known total is done. It is
    null before then. ``can_stop`` is true while a job runs in any phase. ``stop_keeps`` is true
    while the job reads contents and ``ingence.jobs.STOP_KEEPS`` says that a Stop then keeps what
    was read. This is the one constant that the Stop route reads too.
    """
    listed = [*operations]
    if listed and "inference" in listed and "views" not in listed:
        listed.append("views")
    steps = len(listed)
    step = 0
    runs = _OPERATION_OF_PHASE.get(phase, ())
    for index, operation in enumerate(listed, start=1):
        if operation in runs:
            step = index
            break
    if ended:
        step = steps
    done: int | None = None
    total: int | None = None
    rate: float | None = None
    left: float | None = None
    unit = _UNIT_OF_PHASE.get(phase)
    named = _COUNTS_OF_PHASE.get(phase)
    if counts and named is not None:
        done_key, total_key = named
        held = counts.get(done_key)
        done = int(held) if isinstance(held, int | float) else None
        held = counts.get(total_key) if total_key else None
        total = int(held) if isinstance(held, int | float) else None
        elapsed = counts.get("elapsed_seconds")
        if done is not None and isinstance(elapsed, int | float) and elapsed > 0:
            rate = round(done / elapsed, 2)
            if total and done / total >= _ESTIMATE_FROM:
                left = round(elapsed * max(0, total - done) / done, 1)
    return {
        "phase_label": _PHASE_WORDS.get(phase, phase.replace("_", " ")),
        "phase_legacy": LEGACY_OF.get(phase, phase),
        "step": step,
        "steps": steps,
        "unit": unit,
        "done": done,
        "total": total,
        "rate_per_s": rate,
        "seconds_left": left,
        "can_stop": running,
        "stop_keeps": running and phase == "reading" and STOP_KEEPS,
    }


def phase_counts(project: Path, phase: str, progress: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the running phase's counts from its snapshot file.

    The snapshot file is ``jobs/worker.progress_snapshot_path``. The result is None where the phase
    writes none or the file is not there yet.
    """
    from ingence.jobs.worker import progress_snapshot_path

    key = _RUN_OF_PHASE.get(phase)
    run_id = progress.get(key) if key else None
    if not isinstance(run_id, str) or not run_id:
        return None
    try:
        held = json.loads(progress_snapshot_path(project, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return held if isinstance(held, dict) else None


def status_from_ledger(ledger: JobLedger) -> dict[str, Any]:
    """Return the applier's status from the ledger: the job running, else the latest to end."""
    recent = ledger.recent(50)
    if not recent:
        return {"phase": "idle", "jobs_done": 0, "queued": [], **progress_keys("idle")}
    running = next((job for job in recent if job.state is JobState.RUNNING), None)
    ended = next((job for job in recent if job.state in FINAL_STATES), None)
    waiting = [job for job in reversed(recent) if job.state is JobState.QUEUED]
    # The job running, else the next to run, else the latest to end.
    shown = running or (waiting[0] if waiting else ended) or recent[0]
    queued = sorted({word for job in waiting if job is not shown for word in job.spellings})
    progress = dict(shown.progress)
    state: dict[str, Any] = {
        "job_id": shown.job_id,
        "state": shown.state.value,
        "spellings": list(shown.spellings),
        "rerun": list(shown.steps),
        "operations": list(shown.operations),
        "retries": shown.retries,
        "attempts": shown.attempts,
        "heartbeat_at": shown.heartbeat_at,
        "error": shown.error,
        "queued": queued,
        "jobs_done": sum(1 for job in recent if job.state is JobState.DONE),
    }
    for name in ("bound_run", "binding_seconds", "partial", "inferred_run", "inference_seconds"):
        if name in progress:
            state[name] = progress[name]
    # A first run: what its census and dive saw, for the landing page.
    state["kind"] = shown.kind
    for name in _FIRST_RUN_FIGURES:
        if name in progress:
            state[name] = progress[name]
    interruptions = [
        event.message
        for event in ledger.events(shown.job_id)
        if event.state is JobState.INTERRUPTED
    ]
    if interruptions:
        state["interrupted"] = interruptions[-1]
    counts: dict[str, Any] | None = None
    if shown.state is JobState.RUNNING:
        binds = bool(shown.spellings) or "rebind" in shown.steps
        first = "census" in shown.steps
        held = shown.phase or ("census" if first else "binding" if binds else "inferring")
        phase = PHASE_OF.get(held, held)
        state["phase"] = phase
        began = progress.get("phase_started_at")
        if isinstance(began, int | float):
            state["elapsed_seconds"] = round(max(0.0, time.time() - began), 1)
        counts = phase_counts(ledger.project, phase, progress)
    elif shown.state is JobState.QUEUED:
        state["phase"] = "queued"
        state["queued"] = sorted({*queued, *shown.spellings})
    else:
        state["phase"] = shown.state.value
        state["finished_at"] = shown.ended_at
    state.update(
        progress_keys(
            str(state["phase"]),
            shown.operations,
            ended=shown.state in FINAL_STATES,
            running=shown.state is JobState.RUNNING,
            counts=counts,
        )
    )
    return state
