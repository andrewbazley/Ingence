"""A pool of read workers: files read in parallel, results written in spend order.

One worker is spawned once and reads many files. The supervisor
(:mod:`ingence.executors.supervisor`) starts a fresh interpreter for every isolated extraction,
which costs close to half a second a file. Nothing in the design forbids a pool. The import cost
is paid once per worker. The budget sums work-seconds, with a wall clock beside it. The parent
hands out work in canonical order and is the only writer, so the published tables are the same
for any number of workers. The number of workers caps memory. The process-wide logging swap means
workers are processes and never threads. Politeness on a share is a lower number of workers.

Each worker runs the whole per-file read: stat, detect, identity, the header readers, the body
readers, the members of a container, and stat again (:func:`ingence.scan.deepen.deepen_entry`
with ``isolated=False``, the worker being the boundary). It reports each stage as it starts, so a
file that is killed is recorded with the stage it was in. It returns the plain
:class:`~ingence.scan.deepen.DeepenOutcome` dataclass.

The parent is the only writer. Work is handed out in spend order, at most two tasks per worker in
flight, each with the file's own deadline (``file_seconds`` plus the allowance per gigabyte).
Results arrive in whatever order the workers finish and are held until every earlier file's result
has been yielded, so the caller sees them in the order it handed them out. A worker that passes
its deadline is killed and the file is recorded as a content error at the stage it was in. A
worker that dies is recorded the same way, by how it died, and a replacement is started. Workers
are recycled after about a thousand files or once their resident size passes three quarters of
the ceiling. The pool shrinks after repeated memory deaths, and when the measured per-file latency
rises. The rule is one worker fewer whenever the mean latency of the last thirty-two files is more
than twice the mean of the first thirty-two, re-based after each step, never below one and never
back up. The rule is simple, stated and measurable.

Cancellation reaches every worker through the supervisor's live registry, so a token's discard
kills them all at once. A stop lets the files in hand finish within their deadlines and hands out
nothing more.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from multiprocessing.connection import wait
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.executors.supervisor import (
    _LIVE,
    _LIVE_LOCK,
    _MEMORY_EXIT,
    _MEMORY_SIGNALS,
    _REAP_SECONDS,
    _apply_memory_limit,
    _kill,
    _module_of,
    _resident_bytes,
    _watch_own_memory,
)
from ingence.extractors.base import EXTRACTORS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from multiprocessing.connection import Connection
    from multiprocessing.context import SpawnProcess

    from ingence.core.schema import Entry
    from ingence.core.settings import ExtractionSettings, Settings
    from ingence.scan.deepen import DeepenOutcome
    from ingence.scan.mention_shapes import PoolRoot
    from ingence.scan.rules import ScanRule

__all__ = [
    "ReadContext",
    "ReadPool",
    "ReadResult",
    "ReadTask",
    "SerialReader",
    "auto_workers",
    "extra_modules",
    "worker_count",
]

#: The most workers a pool reads a network share with. A share answers everyone on it, and eight
#: readers is politeness and not a measurement of the share. The share's own behaviour under more
#: readers is unmeasured.
SHARE_WORKER_CAP = 8

#: How many files a worker reads before it is replaced, so that a slow leak in a reader library
#: is bounded by this many files and not by the pass. A thousand is a reason and not a
#: measurement: at a megabyte leaked per file (far more than any reader measured) a worker reaches
#: its ceiling at about this many. The replacement costs about 1.2 s once per thousand files,
#: which is nothing against the reading.
RECYCLE_AFTER_TASKS = 1000

#: The share of the memory ceiling that a worker's resident size may reach before it is replaced:
#: three quarters, so that a worker is replaced on a quiet file and not killed by the self-watch
#: on the next large one, which would cost that file. This is a reason and not a measurement.
RECYCLE_AT_SHARE_OF_CEILING = 0.75

#: Tasks in flight per worker: one being read and one waiting in its pipe, so that the next file
#: starts the moment the last ends. Two and not more, so that a stop or a budget that runs out
#: leaves at most 2N files in hand.
QUEUE_DEPTH = 2

#: Seconds added to a file's own ceiling before the parent kills the worker reading it. The
#: worker's own clock refuses the next stage at the ceiling and records why, and the parent's
#: kill is for a stage that cannot be interrupted. Two seconds is longer than the worker's own
#: check and the pipe between the two (milliseconds), and short enough that a hung stage costs the
#: pass no more than its ceiling and a breath. This is a reason and not a measurement.
KILL_GRACE_SECONDS = 2.0

#: Seconds a freshly spawned worker is given to load the readers and say it is ready. On a
#: development machine a worker is ready in 1.2 s unloaded (the import of the pass and the
#: readers) and in a few seconds at a load of 30. Three minutes is far above both, so only a
#: machine that cannot start a worker at all is called out, and the fallback below is never a
#: load spike.
STARTUP_SECONDS = 180.0

#: The adaptation rule reads a window of per-file latencies and shrinks the pool when the mean
#: of the latest window is more than this many times the first window's. Thirty-two files is
#: enough that one slow file cannot move the mean by itself, and few enough that a share that
#: starts to struggle is noticed within a minute at the rates measured (30 to 45 files per
#: second). Twice: a doubling of the mean time per file is beyond the noise measured between two
#: runs on one pool at one load (a sample collection read twice with one worker took 11.1 s and
#: 15.9 s, a ratio of 1.4, at loads of 21 and 31), and below what a struggling share shows.
#: Neither number is measured on a share. Both are reasons.
LATENCY_WINDOW = 32
LATENCY_RISE = 2.0

#: Memory deaths after which the pool reads with one worker fewer. Two, so that one hostile file
#: does not cost the pool a worker for the rest of the pass (it is replaced), and a second death
#: says the machine is short of memory for this many readers at once. This is a reason and not a
#: measurement.
MEMORY_DEATHS_TO_SHRINK = 2

#: Files per worker below which ``auto`` starts fewer workers than the machine allows. The number
#: comes from a measurement on a sample collection (430 files, the dive examining 502 entries, a
#: 16-core machine at a load of 21 to 31). Sixteen workers took 17.45 s on the wall (28.8 entries
#: per second) against 11.14 s with one (45.1 per second), each worker costing about 1.2 s to
#: start and warm. Capped at seven by this number, the pass took 12.6 s (39.7 per second) against
#: 15.9 s with one (31.6 per second). Sixty-four files is about a second of reading at the rates
#: measured, the cost of one worker's start. A number a person set is never cut by this. The rule
#: for ``auto`` is written beside ``extraction.workers``.
FILES_PER_WORKER = 64

#: Workers that die before they are ready, one after another, after which the pool stops
#: spawning and asks the pass to stop keeping what was read. A machine that cannot start a
#: worker is not helped by starting another, and nothing is ever read in the parent without a
#: boundary, so the honest end is a stop with the reason. Three, so that one worker lost to a
#: transient (a fork refused under memory pressure) is not the pass's end.
STARTUP_FAILURES_TO_FALL_BACK = 3


@dataclass(frozen=True, slots=True)
class ReadContext:
    """What every file's reading shares, sent to each worker once when it starts."""

    settings: Settings
    run_id: str
    observed_at: str
    terms: tuple[str, ...] = ()
    extractors: tuple[str, ...] = ()
    membership: ScanRule | None = None
    roots: tuple[PoolRoot, ...] = ()
    #: Modules a fresh interpreter has to import for every registered extractor to exist there:
    #: the ones outside the engine's own package, which ``load_builtins`` does not reach.
    extra_modules: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadTask:
    """One file to read, with what this reading of it asks for."""

    #: The position in spend order; the parent yields results in this order, lowest first.
    sequence: int
    entry: Entry
    path: str
    identity_methods: tuple[str, ...] = ()
    need_digests: frozenset[str] = frozenset()
    extract: bool = True
    is_remote: bool = False
    #: The file's own clock, in seconds: the parent kills the worker reading it after this plus
    #: a grace, recording the stage the worker said it was in.
    deadline_seconds: float = 60.0


@dataclass(slots=True)
class ReadResult:
    """What one task came to, whichever worker read it."""

    task: ReadTask
    outcome: DeepenOutcome
    #: Which worker read it (its number in this pool), or None for the serial reader.
    worker: int | None = None
    #: Why the worker was killed or died with this file in hand, or None where it finished:
    #: ``timeout``, ``memory``, ``crash``.
    killed_for: str | None = None
    #: The stage the worker had reported last where it was killed or died.
    stage: str | None = None
    #: Seconds on the wall clock from the task's start in a worker to its result in the parent.
    seconds: float = 0.0


def extra_modules(names: Iterable[str] | None = None) -> tuple[str, ...]:
    """The modules a worker imports beyond the engine's own, for the extractors it may run.

    Read off the registry the way the supervisor does (``_module_of``): a test fixture or a
    plug-in lives outside the package, and a fresh interpreter has to import its module for the
    extractor to be registered there.
    """
    found: set[str] = set()
    for name in names or EXTRACTORS.names():
        module = _module_of(name)
        if module and not module.startswith("ingence."):
            found.add(module)
    return tuple(sorted(found))


def auto_workers(
    extraction: ExtractionSettings,
    *,
    on_a_share: bool = False,
    cpu_count: int | None = None,
    physical_memory: int | None = None,
) -> int:
    """How many workers ``workers: auto`` means on this machine.

    The least of the cores and half the machine's memory divided by one worker's ceiling, so
    that every worker can reach its ceiling at once and the machine still has its other half;
    at most :data:`SHARE_WORKER_CAP` where a root lies on a network share; never below one.
    """
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if physical_memory is None:
        from ingence.infer.streaming import physical_memory as machine_memory

        physical_memory = machine_memory()
    ceiling = extraction.subprocess_memory_bytes
    by_memory = (
        cores if physical_memory is None or not ceiling else int((0.5 * physical_memory) // ceiling)
    )
    chosen = min(cores, by_memory)
    if on_a_share:
        chosen = min(chosen, SHARE_WORKER_CAP)
    return max(1, chosen)


def worker_count(
    extraction: ExtractionSettings, *, on_a_share: bool = False, files: int | None = None
) -> int:
    """The pool's size as the settings say: a number, or ``auto`` sized from the machine.

    ``files`` is how many the pass will read, where it is known: ``auto`` starts no more
    workers than one per :data:`FILES_PER_WORKER` of them, and always at least one.
    """
    if extraction.workers == "auto":
        chosen = auto_workers(extraction, on_a_share=on_a_share)
        if files is not None:
            chosen = min(chosen, max(1, -(-files // FILES_PER_WORKER)))
        return chosen
    return max(1, int(extraction.workers))


# ---------------------------------------------------------------------------
# The worker.
# ---------------------------------------------------------------------------


def _say(connection: Connection, payload: object) -> None:
    """Send one message and keep the pipe open, tolerating a parent that has already gone.

    Not the supervisor's ``_send``, which closes after one message: a worker speaks many times.
    """
    with contextlib.suppress(BrokenPipeError, EOFError, OSError, ValueError):
        connection.send(payload)


def _warm_readers() -> None:
    """Build every detector, identity method and reader once, before the first file.

    A learned detector loads its model when it is first built, and a registry caches the instance.
    Built here, the cost is the worker's start and not the first file's reading. A component that
    cannot be built is left to the registry to record, as the pass does.
    """
    from ingence.extractors.detect import DETECTORS
    from ingence.identity import IDENTITY_METHODS

    for registry in (DETECTORS, IDENTITY_METHODS, EXTRACTORS):
        for name in registry.names(available_only=True):
            with contextlib.suppress(Exception):
                registry.get(name)


def _worker_main(connection: Connection, context: ReadContext, memory_bytes: int | None) -> None:
    """Load the readers once, then read every file the parent sends until it says stop.

    There are two memory layers, as in the supervisor. The self-watch on resident size starts
    first and runs everywhere. The kernel's address-space limit is asked for once the readers are
    loaded and warmed, at the ceiling plus the baseline the warmed worker then holds. The limit
    counts the engine's own libraries against the worker, and sized for one reading alone it
    would stop a worker before its first file. Whether the kernel took the limit (Linux does,
    macOS refuses, Windows has none) rides in the ready message, so the parent can say which layers
    a worker ran under. The parent's deadline is the layer that always holds.
    """
    # The parent's token is the only authority. A terminal's Ctrl-C reaches every process in the
    # group. Without this, a worker in the middle of a file would report a KeyboardInterrupt as that
    # file's failure, an idle worker would die in ``recv``, and a stop-and-keep run would publish
    # the file in hand as a content error. The parent kills a worker with SIGKILL, which no handler
    # can refuse, so ignoring these loses nothing.
    with contextlib.suppress(OSError, ValueError, AttributeError):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _watch_own_memory(memory_bytes)
    try:
        import importlib

        from ingence.extractors import load_builtins
        from ingence.identity import load_builtins as load_identity_builtins

        load_builtins()
        load_identity_builtins()
        for module in context.extra_modules:
            importlib.import_module(module)
        from ingence.scan.deepen import deepen_entry

        _warm_readers()
    except BaseException as exc:  # said to the parent, never raised in a worker
        _say(connection, ("dead", f"{type(exc).__name__}: {exc}"))
        return
    kernel_limit = False
    if memory_bytes:
        baseline = _resident_bytes() or 0
        kernel_limit = _apply_memory_limit(memory_bytes + baseline)
    _say(connection, ("ready", os.getpid(), kernel_limit))
    while True:
        try:
            message = connection.recv()
        except (EOFError, OSError):
            return
        if message is None:
            return
        task: ReadTask = message
        _say(connection, ("start", task.sequence))

        def progress(stage: str, sequence: int = task.sequence) -> None:
            _say(connection, ("stage", sequence, stage))

        try:
            outcome = deepen_entry(
                task.entry,
                Path(task.path),
                run_id=context.run_id,
                observed_at=context.observed_at,
                settings=context.settings,
                terms=context.terms,
                extractors=context.extractors,
                membership=context.membership,
                roots=context.roots,
                need_digests=task.need_digests,
                extract=task.extract,
                is_remote=task.is_remote,
                identity_methods=task.identity_methods,
                isolated=False,
                progress=progress,
            )
        except MemoryError:
            _say(connection, ("memory", task.sequence))
            return
        except BaseException as exc:  # one file's failure is one file's failure
            _say(connection, ("failed", task.sequence, f"{type(exc).__name__}: {exc}"))
            continue
        _say(connection, ("done", task.sequence, outcome, _resident_bytes()))


# ---------------------------------------------------------------------------
# The parent.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Worker:
    number: int
    process: SpawnProcess
    connection: Connection
    spawned: float
    ready: bool = False
    #: The sequences handed to this worker, in order; the first is the one it is reading.
    queue: deque[int] = field(default_factory=deque)
    tasks_done: int = 0
    resident_bytes: int | None = None
    #: Set once the worker is to be replaced after its queue drains.
    retiring: bool = False
    #: Whether the kernel took the address-space limit; None until ready.
    kernel_limit: bool | None = None
    #: Why the worker is known to be gone, where it said so or its pipe tore; the exit code
    #: says the rest once the process is reaped.
    dead_because: str | None = None
    pipe_closed: bool = False


@dataclass(slots=True)
class _Flight:
    task: ReadTask
    worker: int
    handed_out: float
    started: float | None = None
    stage: str | None = None

    def deadline(self) -> float | None:
        if self.started is None:
            return None
        return self.started + self.task.deadline_seconds + KILL_GRACE_SECONDS


class ReadPool:
    """N read workers, fed in spend order, their results yielded in that order."""

    def __init__(
        self,
        context: ReadContext,
        *,
        workers: int,
        memory_bytes: int | None,
        token: object = None,
        recycle_after: int = RECYCLE_AFTER_TASKS,
        queue_depth: int = QUEUE_DEPTH,
    ) -> None:
        self._context = context
        self._size = max(1, workers)
        self._memory_bytes = memory_bytes
        self._token = token
        self._recycle_after = recycle_after
        self._queue_depth = max(1, queue_depth)
        self._workers: dict[int, _Worker] = {}
        self._retired: list[_Worker] = []
        self._next_number = 0
        self._latencies: list[float] = []
        self._baseline: float | None = None
        #: What the pool did while reading, for the pass's journal: each a dict with ``event``.
        self.notes: list[dict[str, Any]] = []
        self.started_with = self._size
        self.timeouts = 0
        self.deaths = 0
        self.memory_deaths = 0
        self.recycled = 0
        self._startup_failures = 0
        #: Why the pool stopped spawning workers and asked the pass to stop, if it did.
        self.fallen_back: str | None = None
        #: Whether the kernel took the workers' address-space limit, from the first worker ready;
        #: None until one is.
        self.kernel_limit: bool | None = None

    # -- lifecycle --------------------------------------------------------------------------

    @property
    def size(self) -> int:
        """How many workers the pool reads with now."""
        return self._size

    def __enter__(self) -> ReadPool:
        self._fill()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _spawn(self) -> _Worker:
        spawn = multiprocessing.get_context("spawn")
        parent_end, child_end = spawn.Pipe(duplex=True)
        process = spawn.Process(
            target=_worker_main,
            args=(child_end, self._context, self._memory_bytes),
            daemon=True,
        )
        process.start()
        child_end.close()
        with _LIVE_LOCK:
            _LIVE.add(process)
        worker = _Worker(self._next_number, process, parent_end, time.monotonic())
        self._next_number += 1
        self._workers[worker.number] = worker
        return worker

    def _fill(self) -> None:
        while self.fallen_back is None and len(self._workers) < self._size:
            self._spawn()

    def _retire(self, worker: _Worker, *, kill: bool = False) -> None:
        """Take one worker out of the pool: asked to quit, or killed, and reaped."""
        self._workers.pop(worker.number, None)
        if kill:
            _kill(worker.process)
        else:
            with contextlib.suppress(OSError, ValueError, EOFError):
                worker.connection.send(None)
        self._retired.append(worker)

    def _reap_retired(self, *, wait_seconds: float) -> None:
        for worker in list(self._retired):
            worker.process.join(wait_seconds)
            if worker.process.is_alive():
                if wait_seconds:
                    _kill(worker.process)
                    worker.process.join(_REAP_SECONDS)
                else:
                    continue
            with _LIVE_LOCK:
                _LIVE.discard(worker.process)
            with contextlib.suppress(OSError, ValueError):
                worker.connection.close()
            self._retired.remove(worker)

    def close(self) -> None:
        """Ask every worker to quit, and kill the ones that do not."""
        for worker in list(self._workers.values()):
            self._retire(worker)
        self._reap_retired(wait_seconds=_REAP_SECONDS)

    # -- the token --------------------------------------------------------------------------

    def _asked(self) -> str | None:
        token = self._token
        if token is None or not getattr(token, "cancelled", False):
            return None
        return "stop" if getattr(token, "keeps", False) else "discard"

    # -- reading ----------------------------------------------------------------------------

    def read(self, tasks: Iterable[ReadTask]) -> Iterator[ReadResult]:
        """Read the tasks with the pool, yielding each result in the order the tasks came.

        The generator pulls a task from ``tasks`` only when a worker has room for it, so a
        caller's generator can count what it never handed out (a budget that ran out, a stop).
        """
        pending = iter(tasks)
        requeue: deque[ReadTask] = deque()
        exhausted = False
        order: deque[int] = deque()
        flights: dict[int, _Flight] = {}
        done: dict[int, ReadResult] = {}
        self._fill()

        while True:
            halted = self._asked()
            if halted == "discard":
                self._abandon()
                return
            if halted == "stop" and not exhausted:
                # Nothing more is handed out. The caller's generator is run to its end so that it
                # can count what it never handed out, and anything it still yields is dropped.
                exhausted = True
                deque(pending, maxlen=0)

            # Hand out, in order, to workers with room.
            if not exhausted or requeue:
                for worker in sorted(self._workers.values(), key=lambda w: w.number):
                    while (
                        worker.ready
                        and not worker.retiring
                        and worker.dead_because is None
                        and len(worker.queue) < self._queue_depth
                    ):
                        if requeue:
                            task = requeue.popleft()
                            fresh = False
                        elif exhausted:
                            break
                        else:
                            try:
                                task = next(pending)
                            except StopIteration:
                                exhausted = True
                                break
                            fresh = True
                        if fresh:
                            order.append(task.sequence)
                        if not self._dispatch(worker, task, flights):
                            requeue.appendleft(task)
                            break
                    if exhausted and not requeue:
                        break

            # Yield what is ready, in order.
            while order and order[0] in done:
                yield done.pop(order.popleft())

            if exhausted and not flights and not requeue:
                return

            if self.fallen_back is not None and not flights:
                # No worker can be started, and nothing is read here without a boundary. The pass
                # is asked to stop and keep what was read, with the reason, and the caller's
                # generator is run to its end so that it counts the rest.
                stop = getattr(self._token, "stop", None)
                if callable(stop):
                    stop(
                        "no read worker could be started "
                        f"({self.fallen_back}); what was read is kept"
                    )
                while order and order[0] in done:
                    yield done.pop(order.popleft())
                requeue.clear()
                deque(pending, maxlen=0)
                return

            # Wait for a worker to speak, a worker to die, or a deadline.
            timeout = self._next_timeout(flights)
            handles: list[Any] = []
            for worker in self._workers.values():
                handles.append(worker.connection)
                handles.append(worker.process.sentinel)
            if handles:
                with contextlib.suppress(OSError, ValueError):
                    wait(handles, timeout)
            else:
                time.sleep(min(timeout, 0.05))
            for worker in list(self._workers.values()):
                self._receive(worker, flights, done)
            self._reap(flights, done, requeue)
            self._reap_retired(wait_seconds=0.0)
            if self._asked() != "discard":
                self._fill()

    def _abandon(self) -> None:
        """Kill every worker: the token asked to discard, and nothing more is wanted."""
        for worker in list(self._workers.values()):
            self._retire(worker, kill=True)
        self._reap_retired(wait_seconds=_REAP_SECONDS)

    def _dispatch(self, worker: _Worker, task: ReadTask, flights: dict[int, _Flight]) -> bool:
        try:
            worker.connection.send(task)
        except (OSError, ValueError, EOFError):
            worker.pipe_closed = True
            return False
        worker.queue.append(task.sequence)
        flights[task.sequence] = _Flight(task, worker.number, time.monotonic())
        return True

    def _next_timeout(self, flights: dict[int, _Flight]) -> float:
        now = time.monotonic()
        soonest = 0.5
        for flight in flights.values():
            deadline = flight.deadline()
            if deadline is not None:
                soonest = min(soonest, max(0.0, deadline - now))
        for worker in self._workers.values():
            if not worker.ready:
                soonest = min(soonest, max(0.0, worker.spawned + STARTUP_SECONDS - now))
        return soonest

    def _receive(
        self, worker: _Worker, flights: dict[int, _Flight], done: dict[int, ReadResult]
    ) -> None:
        while True:
            try:
                if not worker.connection.poll():
                    return
                message = worker.connection.recv()
            except (EOFError, OSError, ValueError):
                worker.pipe_closed = True
                return
            except Exception as exc:  # an unpicklable or torn message
                worker.dead_because = worker.dead_because or f"crash: {exc}"
                return
            kind = message[0]
            if kind == "ready":
                worker.ready = True
                worker.kernel_limit = bool(message[2]) if len(message) > 2 else False
                if self.kernel_limit is None:
                    self.kernel_limit = worker.kernel_limit
                self._startup_failures = 0
            elif kind == "dead":
                worker.dead_because = f"crash: {message[1]}"
                return
            elif kind == "start":
                flight = flights.get(message[1])
                if flight is not None:
                    flight.started = time.monotonic()
                    flight.stage = "stat"
            elif kind == "stage":
                flight = flights.get(message[1])
                if flight is not None:
                    flight.stage = message[2]
            elif kind == "done":
                self._finished(worker, message[1], message[2], message[3], flights, done)
            elif kind == "failed":
                self._failed_in_worker(worker, message[1], message[2], flights, done)
            elif kind == "memory":
                worker.dead_because = "memory"
                return

    def _finished(
        self,
        worker: _Worker,
        sequence: int,
        outcome: DeepenOutcome,
        resident: int | None,
        flights: dict[int, _Flight],
        done: dict[int, ReadResult],
    ) -> None:
        flight = flights.pop(sequence, None)
        if flight is None:
            return
        with contextlib.suppress(ValueError):
            worker.queue.remove(sequence)
        now = time.monotonic()
        seconds = now - (flight.started or flight.handed_out)
        done[sequence] = ReadResult(flight.task, outcome, worker=worker.number, seconds=seconds)
        worker.tasks_done += 1
        worker.resident_bytes = resident
        self._note_latency(seconds)
        ceiling = self._memory_bytes
        if worker.tasks_done >= self._recycle_after or (
            ceiling and resident is not None and resident > RECYCLE_AT_SHARE_OF_CEILING * ceiling
        ):
            worker.retiring = True
        if worker.retiring and not worker.queue:
            self.recycled += 1
            self.notes.append(
                {
                    "event": "worker.recycled",
                    "worker": worker.number,
                    "tasks": worker.tasks_done,
                    "resident_bytes": resident,
                }
            )
            self._retire(worker)

    def _failed_in_worker(
        self,
        worker: _Worker,
        sequence: int,
        message: str,
        flights: dict[int, _Flight],
        done: dict[int, ReadResult],
    ) -> None:
        flight = flights.pop(sequence, None)
        if flight is None:
            return
        with contextlib.suppress(ValueError):
            worker.queue.remove(sequence)
        seconds = time.monotonic() - (flight.started or flight.handed_out)
        done[sequence] = _killed_result(
            flight,
            worker.number,
            "crash",
            f"the reading raised: {message}",
            seconds,
            run_id=self._context.run_id,
        )
        worker.tasks_done += 1

    def _note_latency(self, seconds: float) -> None:
        """The adaptation rule: one worker fewer when the window's mean latency doubles.

        The rule is a one-way ratchet on mean seconds per file, whatever the cause. It reads the
        ratio of one window's mean to the last base and nothing else. So a share that starts to
        struggle, a run of large files and a machine that other work has loaded look the same to
        it, and a pool that shrank never grows back within the pass. The rule is simple and stated.
        A rule that told the causes apart would need a measurement that this pass does not take.
        """
        self._latencies.append(seconds)
        count = len(self._latencies)
        if count < LATENCY_WINDOW or count % LATENCY_WINDOW:
            return
        window = sum(self._latencies[-LATENCY_WINDOW:]) / LATENCY_WINDOW
        if self._baseline is None:
            self._baseline = window
            return
        if window > LATENCY_RISE * self._baseline and self._size > 1:
            self._size -= 1
            self.notes.append(
                {
                    "event": "pool.shrunk",
                    "because": "latency",
                    "baseline_seconds": round(self._baseline, 4),
                    "window_seconds": round(window, 4),
                    "workers": self._size,
                }
            )
            self._baseline = window
            spare = next(
                (w for w in self._workers.values() if not w.queue and not w.retiring), None
            )
            if spare is not None:
                self._retire(spare)
            elif self._workers:
                max(self._workers.values(), key=lambda w: w.number).retiring = True

    def _reap(
        self, flights: dict[int, _Flight], done: dict[int, ReadResult], requeue: deque[ReadTask]
    ) -> None:
        """Kill a worker past its deadline, record a worker that died, and re-dispatch the rest."""
        now = time.monotonic()
        for worker in list(self._workers.values()):
            reason = worker.dead_because
            if worker.pipe_closed and worker.process.is_alive():
                # The pipe tore first: give the process a moment to finish dying, so its exit
                # code says why, before it is taken for a worker that closed its pipe and lived.
                worker.process.join(0.5)
            alive = worker.process.is_alive()
            running = flights.get(worker.queue[0]) if worker.queue else None
            if reason is None and alive and not worker.pipe_closed:
                deadline = running.deadline() if running is not None else None
                if deadline is not None and now > deadline:
                    reason = "timeout"
                elif not worker.ready and now > worker.spawned + STARTUP_SECONDS:
                    reason = "crash: the worker did not become ready in time"
                else:
                    continue
            elif reason is None and alive:
                reason = "crash: the worker closed its pipe"
            elif reason is None or (not alive and reason.startswith("crash")):
                reason = self._reason_for(worker.process.exitcode)
            if not worker.ready:
                self._startup_failures += 1
                if (
                    self._startup_failures >= STARTUP_FAILURES_TO_FALL_BACK
                    and self.fallen_back is None
                ):
                    self.fallen_back = reason
                    self.notes.append({"event": "pool.fell_back", "because": reason, "workers": 0})
                    for other in list(self._workers.values()):
                        if other is not worker and not other.ready:
                            self._retire(other, kill=True)
            self._retire(worker, kill=True)
            if running is not None:
                seconds = now - (running.started or running.handed_out)
                kind = reason if reason in ("memory", "timeout") else "crash"
                said = {
                    "timeout": (
                        f"the reading passed its ceiling of {running.task.deadline_seconds:.1f} s "
                        f"in stage {running.stage or 'start'} and the worker reading it was killed"
                    ),
                    "memory": (
                        f"the worker reading it outgrew its memory ceiling in stage "
                        f"{running.stage or 'start'} and was stopped"
                    ),
                }.get(
                    kind,
                    f"the worker reading it died in stage {running.stage or 'start'}: {reason}",
                )
                done[running.task.sequence] = _killed_result(
                    running, worker.number, kind, said, seconds, run_id=self._context.run_id
                )
                flights.pop(running.task.sequence, None)
                if kind == "timeout":
                    self.timeouts += 1
                else:
                    self.deaths += 1
                if kind == "memory":
                    self.memory_deaths += 1
                self.notes.append(
                    {
                        "event": "worker.killed" if kind == "timeout" else "worker.died",
                        "worker": worker.number,
                        "because": kind,
                        "entry_id": running.task.entry.entry_id,
                        "in_stage": running.stage,
                    }
                )
            else:
                self.deaths += 1
                self.notes.append(
                    {"event": "worker.died", "worker": worker.number, "because": reason}
                )
            # Everything else the worker held was never started: back to the front of the line.
            waiting = [flights.pop(sequence) for sequence in worker.queue if sequence in flights]
            for flight in reversed(waiting):
                requeue.appendleft(flight.task)
            worker.queue.clear()
            if (
                reason == "memory"
                and self.memory_deaths >= MEMORY_DEATHS_TO_SHRINK
                and self._size > 1
            ):
                self._size -= 1
                self.memory_deaths = 0
                self.notes.append(
                    {"event": "pool.shrunk", "because": "memory", "workers": self._size}
                )

    @staticmethod
    def _reason_for(exitcode: int | None) -> str:
        if exitcode == _MEMORY_EXIT:
            return "memory"
        if exitcode is not None and exitcode < 0 and -exitcode in _MEMORY_SIGNALS:
            return "memory"
        if exitcode is None:
            return "crash: the worker produced no exit code"
        if exitcode < 0:
            return f"crash: killed by signal {-exitcode}"
        return f"crash: exited with status {exitcode}"


def _killed_result(
    flight: _Flight, worker: int, kind: str, said: str, seconds: float, *, run_id: str
) -> ReadResult:
    """The record of a file whose worker was killed or died: a content error at its stage."""
    from ingence.core.schema import FailureClass, ScanError, ScanOutcome
    from ingence.scan.deepen import DeepenOutcome, StageTiming

    stage = flight.stage or "start"
    entry = flight.task.entry
    outcome = DeepenOutcome(
        entry_id=entry.entry_id,
        outcome=ScanOutcome.CONTENT_ERROR,
        seconds=seconds,
        nanoseconds=int(seconds * 1e9),
        stages=[StageTiming(stage, int(seconds * 1e9), None, isolated=True)],
    )
    outcome.errors.append(
        ScanError(
            entry_id=entry.entry_id,
            relative_path=entry.relative_path,
            operation=stage,
            error_type=f"Worker{kind.title()}Error",
            message=said,
            run_id=run_id,
            # The work was attempted and the file, or a reader, did not finish: coverage.
            failure_class=FailureClass.COMPREHENSION,
        )
    )
    return ReadResult(
        flight.task, outcome, worker=worker, killed_for=kind, stage=stage, seconds=seconds
    )


class SerialReader:
    """Reads in this process, one file at a time: the path kept to measure the pool against.

    This is what ``use_subprocess_isolation: false`` means. Every reader runs in the calling
    thread with no boundary at all, so a hostile file can hang the pass, and the measurement says
    what the boundary costs. Here ``file_seconds`` is a refusal between stages and not an
    interruption. The file's clock is read before each stage and a stage is refused once it has
    run out, but a stage that has started runs to its end, however long. Only a worker's deadline
    interrupts.
    """

    size = 1
    started_with = 1
    timeouts = 0
    deaths = 0
    recycled = 0

    def __init__(
        self, context: ReadContext, read: Callable[..., DeepenOutcome] | None = None
    ) -> None:
        self._context = context
        self._read = read
        self.notes: list[dict[str, Any]] = []

    def __enter__(self) -> SerialReader:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self, tasks: Iterable[ReadTask]) -> Iterator[ReadResult]:
        if self._read is None:
            from ingence.scan.deepen import deepen_entry

            self._read = deepen_entry
        context = self._context
        for task in tasks:
            started = time.monotonic()
            outcome = self._read(
                task.entry,
                Path(task.path),
                run_id=context.run_id,
                observed_at=context.observed_at,
                settings=context.settings,
                terms=context.terms,
                extractors=context.extractors,
                membership=context.membership,
                roots=context.roots,
                need_digests=task.need_digests,
                extract=task.extract,
                is_remote=task.is_remote,
                identity_methods=task.identity_methods,
                isolated=False,
            )
            yield ReadResult(task, outcome, worker=None, seconds=time.monotonic() - started)
