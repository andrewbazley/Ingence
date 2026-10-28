"""The worker: one job, run in a process of its own, through the ordinary write protocol.

Spawned by the supervisor as ``python -m ingence.jobs.worker --project P --job J --attempt N``
(the ``__main__`` guard below), it reads its job from the ledger and does what it asks, in order:

1. binding: the partial rebind of the job's spellings (only the names that could hold them are
   read again, every other reading is carried), or the whole rebind where a re-run asked for one.
2. inferring: inference over the census that the binding step published.
3. views: the readouts of the run inference published (``ingence.readouts``): the themes, their
   map, the search and Explorer projections, the Overview and the assumptions. The worker builds
   them once, before the ledger's ``finish`` moves the pin, so the pages keep the previous run's
   readouts until the new ones exist. This step is always last.

Each step publishes a run through the write protocol (stage, validate, rename, swap the
pointer), so a worker killed anywhere leaves the last published run whole and current, and at
worst a staging directory nobody reads. The run records name the job (``job_id``, through
:func:`~ingence.jobs.provenance.job_scope`) and state the Record watermark each read.

While it runs, a thread writes a heartbeat to the ledger every few seconds. A heartbeat that
finds the job is no longer this attempt's (reconciled away as dead while it was only slow)
cancels the step in hand, which then publishes nothing. A worker never publishes over the attempt
that replaced it. The heartbeat also reads a person's Stop. During the content step, a Stop that
asks to keep makes the pass publish what it has read, and the job goes on to apply the rules. Any
other Stop cancels the step in hand and ends the job ``cancelled``. The worker holds the
project's worker lock throughout. The operating system lets go of it when the process dies,
which is how the supervisor knows it has.

A retry resumes after what was published. Each phase writes the run it is about to make to the
ledger first. Where an earlier attempt's census is current, the retry goes straight to
inference. Where its inference is current, the job is done with that run, even where the worker
died between publishing and telling the ledger. The pause points ``bound`` and ``inferred`` sit
in exactly that window, for the tests.

The heartbeat is measured. The job's progress records the longest gap between two of its beats
on the worker's own monotonic clock (``beat_gap_max_seconds``), and the phase it was in
(``beat_gap_max_phase``). A heartbeat on a thread of its own can be starved: one call into
compiled code that keeps the interpreter lock (a sort of millions of items, a conversion of a
large table to Python objects) stops every other thread in the process for as long as the call
lasts, the heartbeat's included. Measurement confirmed this, and the hold hook below pins it.
The supervisor therefore judges silence with a margin over the longest gap a healthy job was
measured to show.

The tests' pause (:data:`PAUSE_ENV`): ``<phase>:<file>`` makes the worker, on reaching that
phase, write ``<file>.reached`` (its process identifier, job and attempt) and wait until
``<file>`` exists. It is how a test kills a worker at a known point and not at a lucky one.
Nothing in the engine sets it. The tests' hold (:data:`HOLD_ENV`): ``<phase>:<seconds>`` makes
the worker, on reaching that phase, keep the interpreter lock for that many whole seconds in one
foreign call, as a long call into compiled code does. Nothing in the engine sets it either.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import math
import os
import shutil
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.jobs import JOBS_DIR, WORKER_LOCK, JobState, phase_words
from ingence.jobs.ledger import JobLedger, JobRow
from ingence.jobs.locks import FileLock
from ingence.jobs.provenance import job_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ingence.scan.progress import ProgressReporter

__all__ = [
    "CONTENT_PHASE",
    "EXIT_DONE",
    "EXIT_FAILED",
    "EXIT_LOCK_BUSY",
    "EXIT_STOPPED",
    "EXIT_SUPERSEDED",
    "HOLD_ENV",
    "KEPT_REASON",
    "PAUSE_ENV",
    "Steps",
    "main",
    "progress_snapshot_path",
    "run_job",
]

#: ``<phase>:<file>``: pause on reaching ``phase`` until ``file`` exists (tests only).
PAUSE_ENV = "INGENCE_JOB_PAUSE"
#: ``<phase>:<seconds>``: on reaching ``phase``, keep the interpreter lock for that many whole
#: seconds in one call, starving every other thread (tests and measurements only).
HOLD_ENV = "INGENCE_JOB_HOLD"

#: The worker's exit codes, which the supervisor reads when a job is left running.
EXIT_DONE = 0
EXIT_FAILED = 1
#: The job was no longer this attempt's: reconciled away while the worker started or ran.
EXIT_SUPERSEDED = 3
#: Another worker held the project's worker lock for longer than this one would wait.
EXIT_LOCK_BUSY = 4
#: A person stopped the job: it ended ``cancelled``, its run unpublished.
EXIT_STOPPED = 5

#: How long a worker waits for the worker lock. A lock held means a worker from before is still
#: running. The supervisor follows such a worker rather than starting another, so a long wait
#: here is itself a fault worth reporting.
LOCK_WAIT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Steps:
    """The two engine calls a job makes. The subprocess worker uses the engine's own. The
    in-process executor passes the Definition applier's, which a test may have stubbed."""

    census: Callable[..., Any]
    inference: Callable[..., Any]
    #: The dive, which only a first run asks for. None where a caller has none.
    deepen: Callable[..., Any] | None = None
    #: The readouts step after inference (``ingence.readouts.build_readouts``). None where a
    #: caller has none (a test that stubs the engine), and the job ends without views.
    readouts: Callable[..., Any] | None = None


def engine_steps() -> Steps:
    from ingence.infer.run import run_inference
    from ingence.readouts import build_readouts
    from ingence.scan.census import run_census
    from ingence.scan.deepen import run_deepen

    return Steps(
        census=run_census, inference=run_inference, deepen=run_deepen, readouts=build_readouts
    )


#: Where a first run's census and dive write what they have seen so far: the snapshot file the
#: census writes beside its staging run (``scan/progress.py``), holding counts and never a
#: percentage. The landing page reads it while the job runs.
def progress_snapshot_path(project: Path, run_id: str) -> Path:
    from ingence.storage.commit import staging_path

    return staging_path(project, run_id).with_suffix(".progress.json")


#: The phase a Stop with ``keep`` keeps in: the content step (``dive``), the one pass that can
#: publish what it has read part way through. The file list and inference are whole or nothing.
CONTENT_PHASE = "dive"
#: Why a content step's token was asked to stop and keep, as the run's journal says it.
KEPT_REASON = "stopped by a person, keeping what was read"


class _SupersededError(Exception):
    """This attempt's job was taken from it. Stop without publishing."""


class _StoppedError(Exception):
    """A person stopped the job: it ends ``cancelled``, publishing nothing more."""

    def __init__(self, phase: str | None, *, keep: bool) -> None:
        super().__init__(f"stopped by a person {phase_words(phase)}")
        self.phase = phase
        self.keep = keep


class _Heartbeat:
    """Tells the ledger this attempt is alive, and learns when the job is no longer its own."""

    def __init__(self, ledger: JobLedger, job_id: str, attempt: int, seconds: float) -> None:
        self._ledger = ledger
        self._job_id = job_id
        self._attempt = attempt
        self._seconds = seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="ingence-heartbeat", daemon=True)
        self.lost = threading.Event()
        #: A person's stop request, read from the ledger each beat. Once found, the heartbeat
        #: sets it with ``keep`` beside it and cancels every step's token, so the step in hand
        #: ends.
        self.stopped = threading.Event()
        self.keep = False
        #: A Stop that kept what was read: read while the content step ran, with ``keep``. The
        #: step's token was asked to stop and keep, so the pass publishes what it has read, and
        #: the job goes on to apply the rules. ``stopped`` stays clear.
        self.kept = threading.Event()
        self.tokens: list[Any] = []
        #: The tokens the content step was given, the ones a kept Stop asks to stop and keep.
        self._content_tokens: list[Any] = []
        #: The phase the main thread is in, for the longest gap's record.
        self.phase: str | None = None
        #: The longest gap between two beats written, in seconds, on this process's own clock.
        self.gap_max = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _beat(self) -> None:
        last = time.monotonic()
        while not self._stop.wait(self._seconds):
            # The gap since the last beat was taken: the wait, and whatever kept this thread
            # from running after it (the interpreter lock held elsewhere, a stopped process, a
            # busy ledger's missed beats). The gap is timed from when the beat was taken, not
            # from when its write came back: a hold that begins while a beat is written keeps
            # this thread inside the write for the whole hold, and the ledger, which has that
            # beat, hears nothing more until it ends. A write the ledger kept waiting counts
            # too, so the gap is the upper bound the silence margin is judged against.
            gap = time.monotonic() - last
            longer = gap > self.gap_max
            progress = (
                {"beat_gap_max_seconds": round(gap, 2), "beat_gap_max_phase": self.phase}
                if longer
                else None
            )
            taken = time.monotonic()
            try:
                ours = self._ledger.heartbeat(
                    self._job_id, attempt=self._attempt, progress=progress
                )
            except Exception:  # a busy ledger misses one beat, never ends the job  # noqa: S112
                continue
            if longer:
                self.gap_max = gap
            last = taken
            if not ours:
                self.lost.set()
                for token in self.tokens:
                    token.cancel("the job was reconciled away from this worker")
                return
            if not self.stopped.is_set():
                self._read_stop()

    def add_token(self, token: Any) -> None:
        """Give a step's token to the heartbeat, which cancels it on a person's Stop. A token
        added after the Stop was read is cancelled at once, so no step runs on past it. A content
        step's token added after a kept Stop is asked to stop and keep at once."""
        self.tokens.append(token)
        if self.phase == CONTENT_PHASE:
            self._content_tokens.append(token)
        if self.stopped.is_set():
            token.cancel("stopped by a person")
        elif self.kept.is_set() and self.phase == CONTENT_PHASE:
            token.stop(KEPT_REASON)

    def _read_stop(self) -> None:
        """Learn of a person's stop request and act on it.

        During the content step, a Stop that asks to keep keeps. The method first marks the
        request answered in the ledger, so the supervisor does not stop the worker after the
        phase's grace and the next beat does not read it again. Then it asks the step's token to
        stop and keep. The pass publishes what it has read, and the job goes on to apply the
        rules. In any other phase, or without keep, a Stop cancels: the step in hand ends and
        publishes nothing, and the job ends ``cancelled``. A request the ledger would not mark is
        read again at the next beat.
        """
        try:
            asked = self._ledger.stop_requested(self._job_id)
        except Exception:  # a busy ledger: asked again at the next beat
            return
        if asked is None:
            return
        keep = bool(asked.get("keep"))
        if keep and self.phase == CONTENT_PHASE:
            try:
                marked = self._ledger.keep_what_was_read(
                    self._job_id, attempt=self._attempt, asked_at=asked.get("at")
                )
            except Exception:  # a busy ledger: asked again at the next beat
                return
            if not marked:
                return
            self.kept.set()
            for token in self._content_tokens:
                token.stop(KEPT_REASON)
            return
        self.keep = keep
        self.stopped.set()
        for token in self.tokens:
            token.cancel("stopped by a person")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _run_id() -> str:
    from ingence.core.ids import run_id as make_run_id

    return make_run_id(started_at=_now(), nonce=os.urandom(8).hex())


def _pause_if_asked(phase: str, job_id: str, attempt: int) -> None:
    asked = os.environ.get(PAUSE_ENV, "")
    named, _, release = asked.partition(":")
    if not release or named != phase:
        return
    from ingence.core.destinations import create_exclusive

    target = Path(release)
    marker = target.with_name(target.name + ".reached")
    # A new file of a name no other writer uses, never a fixed one opened with truncation.
    handle, staged = create_exclusive(marker, binary=False)
    with handle:
        handle.write(
            json.dumps({"pid": os.getpid(), "job_id": job_id, "attempt": attempt, "phase": phase})
        )
    for _ in range(100):
        try:
            # Windows refuses to replace a file a reader holds open. The reader lets go soon.
            os.replace(staged, marker)
            break
        except PermissionError:
            time.sleep(0.05)
    while not target.exists():
        time.sleep(0.05)


def _hold_if_asked(phase: str) -> None:
    asked = os.environ.get(HOLD_ENV, "")
    named, _, seconds = asked.partition(":")
    if not seconds or named != phase:
        return
    hold_interpreter(math.ceil(float(seconds)))


def hold_interpreter(seconds: int) -> None:
    """Keep the interpreter lock for ``seconds`` in one foreign call (tests and measurements).

    ``ctypes.PyDLL`` calls a C function *without* releasing the interpreter lock, which is what
    a long call into compiled code that does not release it looks like to every other thread.
    """
    import ctypes

    if sys.platform == "win32":  # pragma: no cover - the hold's tests are POSIX
        ctypes.PyDLL("kernel32").Sleep(ctypes.c_uint(seconds * 1000))
    else:
        ctypes.PyDLL(None).sleep(ctypes.c_uint(seconds))


def _enter(
    ledger: JobLedger, job: JobRow, attempt: int, phase: str, beat: _Heartbeat, **progress: Any
) -> None:
    """Record the phase, then honour the tests' pause and hold (after, so the ledger says where
    it is)."""
    beat.phase = phase
    if not ledger.heartbeat(
        job.job_id,
        attempt=attempt,
        phase=phase,
        progress={"phase_started_at": time.time(), **progress},
    ):
        raise _SupersededError
    _pause_if_asked(phase, job.job_id, attempt)
    _hold_if_asked(phase)


def _published(report: object, beat: _Heartbeat | None = None) -> None:
    """A step whose run was cancelled published nothing: the job was taken from this worker,
    or a person stopped it, which ends the job ``cancelled``. A Stop read after the step's last
    look at its token ends the job here too, whether the step published or not: nothing of the
    job runs on past a Stop."""
    if beat is not None and beat.stopped.is_set():
        raise _StoppedError(beat.phase, keep=beat.keep)
    if getattr(report, "cancelled", False):
        raise _SupersededError


def _do(
    project: Path,
    job: JobRow,
    attempt: int,
    ledger: JobLedger,
    steps: Steps,
    beat: _Heartbeat,
    planned: list[str],
) -> str:
    """Run the job's steps in order and return the run it produced last (inference's).

    Each phase writes the run it is about to make to the ledger before it makes it
    (``binding_run``, ``inferring_run`` in the progress). A worker that dies after a run is
    published and before the ledger hears of it leaves the retry enough to know: where the run
    current now is the one a phase planned, that phase is done and is not run again. A job that
    grows while it waits forgets what was planned (the ledger's ``enqueue``).
    """
    from ingence.core.project import Project
    from ingence.scan.cancel import CancellationToken
    from ingence.storage.commit import read_pointer

    current = read_pointer(project)
    current_run = current.run_id if current is not None else None
    if "census" in job.steps:
        # A first run: the census, the dive where asked, then inference below.
        return _first_run(project, job, attempt, ledger, steps, beat, planned, current_run)
    planned_inference = job.progress.get("inferring_run")
    if attempt > 1 and current_run is not None and current_run == planned_inference:
        # Its inference was published and is current: the job is done. Nothing is made again,
        # except the views where the attempt before died before preparing them.
        ledger.heartbeat(
            job.job_id,
            attempt=attempt,
            progress={"resumed_after": current_run, "inferred_run": current_run},
        )
        _views_if_missing(project, job, attempt, ledger, steps, beat, current_run)
        return current_run
    planned_census = job.progress.get("binding_run")
    resumed = (
        attempt > 1
        and current_run is not None
        and current_run in {job.bound_run, planned_census} - {None}
    )
    if resumed:
        ledger.heartbeat(
            job.job_id,
            attempt=attempt,
            bound_run=current_run,
            progress={"resumed_after": current_run, "bound_run": current_run},
        )
    elif job.spellings or "rebind" in job.steps:
        census_id = _run_id()
        planned.append(census_id)
        _enter(ledger, job, attempt, "binding", beat, binding_run=census_id)
        began = time.monotonic()
        token = CancellationToken()
        beat.add_token(token)
        partial = "rebind" not in job.steps
        # The names to read are counted before they are read. A rebind replays the current run's
        # entries, so its total is known, and the page shows "n of total".
        reporter = _reporter_for(project, census_id, "binding", token)
        reporter.set(total_entries=_entries_in(project, current_run))
        try:
            with reporter:
                if not partial:
                    # A committed change to how names are read reads every name again.
                    report = steps.census(
                        Project.open(project),
                        run_id=census_id,
                        started_at=_now(),
                        rebind=True,
                        token=token,
                        reporter=reporter,
                    )
                else:
                    try:
                        report = steps.census(
                            Project.open(project),
                            run_id=census_id,
                            started_at=_now(),
                            rebind=True,
                            spellings=job.spellings,
                            token=token,
                            reporter=reporter,
                        )
                    except ValueError as error:
                        # A project whose names were read by another tokeniser cannot carry
                        # them. The whole rebind reads every name again, and says so.
                        if "re-bind them all" not in str(error):
                            raise
                        census_id = _run_id()
                        planned.append(census_id)
                        ledger.heartbeat(
                            job.job_id, attempt=attempt, progress={"binding_run": census_id}
                        )
                        report = steps.census(
                            Project.open(project),
                            run_id=census_id,
                            started_at=_now(),
                            rebind=True,
                            token=token,
                            reporter=reporter,
                        )
                        partial = False
        finally:
            reporter.remove_snapshot()
        _published(report, beat)
        _pause_if_asked("bound", job.job_id, attempt)
        ledger.heartbeat(
            job.job_id,
            attempt=attempt,
            bound_run=census_id,
            progress={
                "bound_run": census_id,
                "binding_seconds": round(time.monotonic() - began, 1),
                "partial": partial,
            },
        )
    inference_id = _run_id()
    planned.append(inference_id)
    # An attempt that took nothing up says so, over what an attempt before it took up.
    taken = {} if resumed else {"resumed_after": None}
    _enter(ledger, job, attempt, "inferring", beat, inferring_run=inference_id, **taken)
    inferring = time.monotonic()
    token = CancellationToken()
    beat.add_token(token)
    reporter = _reporter_for(project, inference_id, "inferring", token)
    try:
        with reporter:
            report = steps.inference(
                Project.open(project),
                run_id=inference_id,
                started_at=_now(),
                token=token,
                reporter=reporter,
            )
    finally:
        reporter.remove_snapshot()
    _published(report, beat)
    _pause_if_asked("inferred", job.job_id, attempt)
    ledger.heartbeat(
        job.job_id,
        attempt=attempt,
        progress={
            "inferred_run": inference_id,
            "inference_seconds": round(time.monotonic() - inferring, 1),
        },
    )
    _views(project, job, attempt, ledger, steps, beat, inference_id)
    return inference_id


def _entries_in(project: Path, run_id: str | None) -> int | None:
    """How many live entries the run holds (a rebind's total), or None where it cannot be read."""
    if run_id is None:
        return None
    try:
        from ingence.storage.catalog import open_catalog

        with open_catalog(project, run_id=run_id) as catalog:
            found = catalog.query("SELECT count(*) FROM entries WHERE NOT is_deleted").fetchone()
    except Exception:
        return None
    return int(found[0]) if found else None


def _reporter_for(project: Path, run_id: str, stage: str, token: Any) -> ProgressReporter:
    """A phase's progress reporter: the snapshot file beside the run's staging, which
    ``/api/applying`` reads for the phase's counts."""
    from ingence.scan.progress import ProgressReporter, ProgressSnapshot

    return ProgressReporter(
        ProgressSnapshot(run_id=run_id, stage=stage),
        path=progress_snapshot_path(project, run_id),
        project=project,
        token=token,
    )


def _views_if_missing(
    project: Path,
    job: JobRow,
    attempt: int,
    ledger: JobLedger,
    steps: Steps,
    beat: _Heartbeat,
    run_id: str,
) -> None:
    """A retry that takes up a published inference prepares its views where the attempt before
    died in the views phase, or before it. The readouts are looked for, never assumed."""
    from ingence.readouts import read_readout

    if steps.readouts is None or read_readout(project, run_id, "overview") is not None:
        return
    _views(project, job, attempt, ledger, steps, beat, run_id)


def _views(
    project: Path,
    job: JobRow,
    attempt: int,
    ledger: JobLedger,
    steps: Steps,
    beat: _Heartbeat,
    run_id: str,
) -> None:
    """The readouts step: the views of the run inference published, built and kept before the
    job finishes and the pin moves. A caller with no readouts step skips it."""
    from ingence.scan.cancel import CancellationToken

    if steps.readouts is None:
        return
    _enter(ledger, job, attempt, "views", beat, views_run=run_id)
    began = time.monotonic()
    token = CancellationToken()
    beat.add_token(token)
    reporter = _reporter_for(project, run_id, "views", token)
    try:
        with reporter:
            built = steps.readouts(project, run_id, reporter=reporter, token=token)
    finally:
        reporter.remove_snapshot()
    if beat.stopped.is_set():
        raise _StoppedError(beat.phase, keep=beat.keep)
    if token.cancelled:
        raise _SupersededError
    ledger.heartbeat(
        job.job_id,
        attempt=attempt,
        progress={
            "views_seconds": round(time.monotonic() - began, 1),
            "views_skipped": dict(built.get("skipped") or {}) if isinstance(built, dict) else {},
        },
    )


def _first_run(
    project: Path,
    job: JobRow,
    attempt: int,
    ledger: JobLedger,
    steps: Steps,
    beat: _Heartbeat,
    planned: list[str],
    current_run: str | None,
) -> str:
    """A first run: the census of every root, the dive where asked, then inference.

    This is the only job that walks a root, so it is asked for only by a person's click on the
    landing page, and only for a project with no run. Each step publishes a run as
    ``ingence index``, ``ingence dive`` and ``ingence infer`` do, and writes the run it is about to
    make to the ledger first, so a retry takes up what an earlier attempt published (as :func:`_do`
    does). The census, the dive and inference write their counts to a snapshot file while they work
    (:func:`progress_snapshot_path`), which the landing page reads. Inference counts the rules it
    has gone through of the rules it will go through.
    """
    from ingence.core.project import Project
    from ingence.extractors import load_builtins
    from ingence.extractors.base import EXTRACTORS
    from ingence.scan.cancel import CancellationToken
    from ingence.scan.profiles import PROFILES, depth_named, depth_word

    progress = job.progress
    taken_up = {
        name: progress.get(key)
        for name, key in (
            ("census", "census_run"),
            ("dive", "dive_run"),
            ("inference", "inferring_run"),
        )
    }
    if attempt > 1 and current_run is not None and current_run == taken_up["inference"]:
        ledger.heartbeat(
            job.job_id,
            attempt=attempt,
            progress={"resumed_after": current_run, "inferred_run": current_run},
        )
        _views_if_missing(project, job, attempt, ledger, steps, beat, current_run)
        return current_run
    resumed_at = None
    if attempt > 1 and current_run is not None:
        if current_run == taken_up["dive"]:
            resumed_at = "dive"
        elif current_run == taken_up["census"]:
            resumed_at = "census"

    def reporter_for(run_id: str, stage: str, token: CancellationToken) -> ProgressReporter:
        return _reporter_for(project, run_id, stage, token)

    if resumed_at is None:
        census_id = _run_id()
        planned.append(census_id)
        _enter(ledger, job, attempt, "census", beat, census_run=census_id)
        began = time.monotonic()
        token = CancellationToken()
        beat.add_token(token)
        # The extractors in force enter each file's fingerprint, as ``ingence index`` loads them.
        load_builtins()
        reporter = reporter_for(census_id, "census", token)
        try:
            with reporter:
                report = steps.census(
                    Project.open(project),
                    run_id=census_id,
                    started_at=_now(),
                    token=token,
                    reporter=reporter,
                    extractor_registry=EXTRACTORS,
                )
        finally:
            reporter.remove_snapshot()
        _published(report, beat)
        if getattr(report, "published", True) is False:
            failed = [
                getattr(outcome, "statement", str(outcome))
                for outcome in getattr(report, "invariants", ())
                if not getattr(outcome, "passed", True)
            ]
            message = "the census was not published" + (
                f": {'; '.join(failed[:3])}" if failed else ""
            )
            raise RuntimeError(message)
        counts = getattr(report, "counts", None)
        ledger.heartbeat(
            job.job_id,
            attempt=attempt,
            bound_run=census_id,
            progress={
                "census_seconds": round(time.monotonic() - began, 1),
                "census_entries": getattr(counts, "entries", None),
                "census_files": getattr(counts, "files", None),
                "census_folders": getattr(counts, "directories", None),
                "census_bytes": getattr(counts, "bytes_seen", None),
                "census_errors": getattr(counts, "errors", None),
            },
        )
        resumed_at = "census"
    if resumed_at == "census" and "dive" in job.steps:
        opened = Project.open(project)
        depth = depth_named(opened.settings.depth)
        if steps.deepen is None or not PROFILES[depth].opens_files:
            # A project whose depth opens no file (``init --depth metadata``) is never dived.
            ledger.heartbeat(
                job.job_id,
                attempt=attempt,
                progress={
                    "dive_left_out": (
                        f"this project's depth is {depth_word(depth)}, which opens no file"
                    )
                },
            )
        else:
            dive_id = _run_id()
            planned.append(dive_id)
            _enter(ledger, job, attempt, "dive", beat, dive_run=dive_id)
            began = time.monotonic()
            token = CancellationToken()
            beat.add_token(token)
            reporter = reporter_for(dive_id, "dive", token)
            # A first run's content step uses the standard depth, whatever the project's own (a
            # deeper reading is asked for by hand afterwards). It is bounded by the standard
            # budget's minutes on the wall clock and not in work-seconds, so a pool of workers
            # reads as much as the minutes allow. It reads one file of every series before any
            # series gets a second.
            from ingence.core.settings import SpendOrder
            from ingence.scan.profiles import ProfileName

            standard = PROFILES[ProfileName.STANDARD]
            try:
                with reporter:
                    report = steps.deepen(
                        opened,
                        run_id=dive_id,
                        started_at=_now(),
                        profile=ProfileName.STANDARD.value,
                        token=token,
                        reporter=reporter,
                        total_seconds=standard.budget_seconds,
                        spend_order_override=SpendOrder.SERIES_FIRST,
                    )
            finally:
                reporter.remove_snapshot()
            _published(report, beat)
            ledger.heartbeat(
                job.job_id,
                attempt=attempt,
                progress={
                    "dive_seconds": round(time.monotonic() - began, 1),
                    "dive_files_read": getattr(report, "extracted", None),
                    "dive_bytes_read": getattr(report, "bytes_read", None),
                    # A person's Stop that kept what was read: the pass ended early.
                    "dive_stopped": bool(getattr(report, "stopped", False)),
                },
            )
    inference_id = _run_id()
    planned.append(inference_id)
    _enter(ledger, job, attempt, "inferring", beat, inferring_run=inference_id)
    inferring = time.monotonic()
    token = CancellationToken()
    beat.add_token(token)
    reporter = reporter_for(inference_id, "inferring", token)
    try:
        with reporter:
            report = steps.inference(
                Project.open(project),
                run_id=inference_id,
                started_at=_now(),
                token=token,
                reporter=reporter,
            )
    finally:
        reporter.remove_snapshot()
    _published(report, beat)
    ledger.heartbeat(
        job.job_id,
        attempt=attempt,
        progress={
            "inferred_run": inference_id,
            "inference_seconds": round(time.monotonic() - inferring, 1),
        },
    )
    _views(project, job, attempt, ledger, steps, beat, inference_id)
    return inference_id


def run_job(
    project: Path,
    job_id: str,
    attempt: int,
    *,
    steps: Steps | None = None,
    heartbeat_seconds: float = 2.0,
    lock_wait_seconds: float = LOCK_WAIT_SECONDS,
) -> int:
    """Run one attempt of one job and end it in the ledger. Return the worker's exit code."""
    ledger = JobLedger(project)
    lock = FileLock(project / JOBS_DIR / WORKER_LOCK)
    if not lock.acquire(
        timeout=lock_wait_seconds,
        holder={"role": "worker", "job_id": job_id, "attempt": attempt},
    ):
        return EXIT_LOCK_BUSY
    try:
        job = ledger.get(job_id)
        if job is None or job.state is not JobState.RUNNING or job.attempts != attempt:
            return EXIT_SUPERSEDED
        ledger.attach(job_id, attempt=attempt, pid=os.getpid())
        beat = _Heartbeat(ledger, job_id, attempt, heartbeat_seconds)
        beat.start()
        planned: list[str] = []
        try:
            with job_scope(job_id):
                produced = _do(
                    project, job, attempt, ledger, steps or engine_steps(), beat, planned
                )
        except _SupersededError:
            return EXIT_SUPERSEDED
        except _StoppedError as stopped:
            # A person's Stop: the job ends ``cancelled`` and is never queued again. The run
            # current stays the one current when the job was asked for, where a step before the
            # stop had published one of this job's (a rebind's names).
            restored = _restore_base_run(project, job)
            said = str(stopped) + (
                f"; the run current is {restored} again, as before the job" if restored else ""
            )
            if stopped.keep:
                said += (
                    "; only reading contents keeps what was read, so nothing of this run was kept"
                )
            ended = ledger.finish(job_id, attempt=attempt, state=JobState.CANCELLED, error=said)
            return EXIT_STOPPED if ended is not None else EXIT_SUPERSEDED
        except Exception as error:
            if beat.stopped.is_set():
                # The step raised on the cancelled token's account: a stop, not a failure.
                restored = _restore_base_run(project, job)
                said = f"stopped by a person {phase_words(beat.phase)}" + (
                    f"; the run current is {restored} again, as before the job" if restored else ""
                )
                ended = ledger.finish(job_id, attempt=attempt, state=JobState.CANCELLED, error=said)
                return EXIT_STOPPED if ended is not None else EXIT_SUPERSEDED
            traceback.print_exc()
            said = f"{type(error).__name__}: {error}"
            if _disk_full(error):
                said = f"{_free_what_it_staged(project, planned)} ({said})"
            ended = ledger.finish(job_id, attempt=attempt, state=JobState.FAILED, error=said)
            return EXIT_FAILED if ended is not None else EXIT_SUPERSEDED
        finally:
            beat.stop()
        if beat.lost.is_set():
            return EXIT_SUPERSEDED
        message = _resumed_words(ledger.get(job_id), produced)
        if beat.kept.is_set():
            message = (message or f"published {produced}") + (
                "; a person stopped reading contents part way, and what was read was kept"
            )
        ended = ledger.finish(
            job_id,
            attempt=attempt,
            state=JobState.DONE,
            produced_run=produced,
            message=message,
        )
        return EXIT_DONE if ended is not None else EXIT_SUPERSEDED
    finally:
        lock.release()


def _restore_base_run(project: Path, job: JobRow) -> str | None:
    """Make the run current when the job was asked for current again, where a step of this job
    published one since (a rebind's census, which holds no conclusions): the pointer moves
    back through ``storage.commit.point_back_to``, which the supervisor calls too when it is the
    one that stopped the worker. Returns the run restored, or None where nothing moved."""
    from ingence.storage.commit import point_back_to

    if job.base_run is None:
        return None
    try:
        moved = point_back_to(project, job.base_run, updated_at=_now())
    except Exception:  # the base run is gone, or the pointer cannot be written: said by its absence
        return None
    return job.base_run if moved else None


#: The errors that mean the disk holding the project has no room (``EDQUOT``: none for this user).
_NO_ROOM = frozenset(
    code for code in (errno.ENOSPC, getattr(errno, "EDQUOT", None)) if code is not None
)


def _disk_full(error: BaseException) -> bool:
    """Whether an error, or one it was raised from, says the disk has no room."""
    seen: BaseException | None = error
    for _ in range(10):  # A chain of causes is short. A cycle is not followed for ever.
        if seen is None:
            return False
        if isinstance(seen, OSError) and seen.errno in _NO_ROOM:
            return True
        if "No space left on device" in str(seen) or "disk is full" in str(seen):
            return True  # pyarrow and SQLite say it in words
        seen = seen.__cause__ or seen.__context__
    return False


def _free_what_it_staged(project: Path, planned: Sequence[str]) -> str:
    """Remove this attempt's unfinished runs' staging, and say so with what it freed.

    The function runs only on a full disk. Measurement showed that a census that ran out of room
    left its staging behind, which kept the disk full and then kept the ledger itself from
    opening. Everywhere else an unfinished run's staging is left for ``ingence fix`` to report, as
    the write protocol intends. Nothing but this worker's own staging is touched: its run
    identifiers are ones it made, and no reader ever resolves a staging directory.
    """
    from ingence.storage.commit import staging_path
    from ingence.storage.layout import owned_path

    freed = 0
    removed = 0
    for run_id in planned:
        # The project's own staging, nothing on the way to it a link, before anything is removed.
        staging = owned_path(project, staging_path(project, run_id))
        if not staging.exists():
            continue
        # A file gone while counted leaves the count short. The removal still frees what is there.
        with contextlib.suppress(OSError):
            freed += sum(item.stat().st_size for item in staging.rglob("*") if item.is_file())
        shutil.rmtree(staging, ignore_errors=True)
        removed += 1
    kept = (
        f"; its unfinished run's staging was removed, freeing {freed / 1e6:.1f} MB"
        if removed
        else ""
    )
    return f"The disk holding the project is full: free space and ask again{kept}"


def _resumed_words(row: JobRow | None, produced: str) -> str | None:
    """What the finishing event says where an earlier attempt's run was taken up, else None."""
    if row is None or not row.progress.get("resumed_after"):
        return None
    resumed = row.progress["resumed_after"]
    if resumed == produced:
        return f"published {produced}: its inference was published before its last worker died"
    return f"published {produced}, inferring from {resumed}, published before its last worker died"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingence.jobs.worker",
        description="Run one job from a project's job ledger (started by the Dashboard).",
    )
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--job", required=True)
    parser.add_argument("--attempt", required=True, type=int)
    parser.add_argument("--heartbeat", type=float, default=2.0)
    arguments = parser.parse_args(argv)
    return run_job(
        arguments.project,
        arguments.job,
        arguments.attempt,
        heartbeat_seconds=arguments.heartbeat,
    )


if __name__ == "__main__":
    sys.exit(main())
