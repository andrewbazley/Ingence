"""Run one extraction in a child process the parent can kill.

A hostile file must not be able to hang a scan, and only a process boundary delivers that. A Python
thread cannot be killed, and cancelling a future does not stop a parser that has entered a blocking
C call: the future is marked cancelled and the thread stays where it was. Containment therefore
requires a process the parent can signal, which is what this module is.

Isolation comes first and throughput second, and they want different shapes. Isolation wants a
*disposable* worker whose lifetime is one file, killable, with its result marshalled back and its
death recorded as a fact about that file rather than a failed run. Throughput wants a *long-lived
pool* amortising startup, and startup here is not cheap: the registry caches instances precisely
because constructing a learned detector per file would cost more than everything else in a scan
combined. Conflating the two produces a design that serves neither. So this module is the disposable
worker, and the long-lived pool is a separate module (:mod:`ingence.executors.pool`).

:class:`Supervisor` keeps a worker count, fixed at one.

The module uses ``spawn`` and not ``fork``. ``fork`` does not exist on Windows and is unsafe next to
threads on macOS, where the child can deadlock in a lock the parent held at the moment of the fork;
the census is threaded, so that is not a theoretical concern here. ``spawn`` costs a fresh
interpreter per child and buys one behaviour on all three supported platforms.

The module does not use ``ProcessPoolExecutor``. A genuinely hung worker is hard to kill through it
and can wedge a pool slot until the interpreter exits, and ``max_tasks_per_child`` addresses leaks
rather than hangs. What is wanted is a parent holding the wall clock and a handle it can
``SIGKILL``, which is ``multiprocessing.Process`` directly.
"""

from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import functools
import multiprocessing
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.extractors.base import EXTRACTORS, ExtractorResult, failed
from ingence.extractors.captured import captured, summarise

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.context import SpawnProcess

    from ingence.extractors.base import ExtractionContext

__all__ = [
    "SupervisionOutcome",
    "Supervisor",
    "live_child_count",
    "memory_signals",
    "terminate_all_children",
]

#: How long a killed child is given to die before the parent stops waiting for it. A ``SIGKILL``ed
#: process is reaped by the kernel promptly; this bound exists so that a child stuck uninterruptibly
#: in the kernel cannot make cancellation itself hang, which would defeat the point of the whole
#: module.
_REAP_SECONDS = 5.0

#: Every child currently running, so cancellation can reach all of them. Held at module scope rather
#: than on the supervisor because the signal handler that services Ctrl-C has no reference to any
#: particular supervisor, and a token that could not reach the children would make the cancellation
#: that ``scan/__init__.py`` describes a claim the code does not keep.
_LIVE: set[SpawnProcess] = set()
_LIVE_LOCK = threading.Lock()


def live_child_count() -> int:
    """Return how many supervised children are running. For tests and the log."""
    with _LIVE_LOCK:
        return len(_LIVE)


def terminate_all_children() -> int:
    """Kill every live child and return how many were killed.

    Called on cancellation. Killing rather than asking is the whole point: a child that is asked
    politely may be inside the blocking call that caused the problem, and a user who pressed Ctrl-C
    is not asking for a negotiation.
    """
    with _LIVE_LOCK:
        children = list(_LIVE)
    for child in children:
        _kill(child)
    for child in children:
        child.join(_REAP_SECONDS)
    # The child is discarded here as well as in the caller's ``finally``. A child is removed from
    # the registry by the thread that started it, on its way out of :meth:`Supervisor.run`. A thread
    # still blocked waiting for the child it just killed never gets there, so the registry would
    # keep naming children that had been dead for some time and ``live_child_count`` would never
    # return to zero. Killing something and leaving it on a list of what is running is a statement
    # that the function has already made false.
    with _LIVE_LOCK:
        for child in children:
            _LIVE.discard(child)
    return len(children)


#: How often the parent looks up while waiting for a child's answer.
#:
#: Small enough that cancellation feels immediate and large enough that the wait costs nothing
#: measurable. It adds no latency to an extraction that succeeds: ``poll`` returns the moment data
#: arrives and only *waits* up to this long, so the happy path is unchanged.
_POLL_SECONDS = 0.05


def _as_payload(value: object) -> tuple[str, object] | None:
    """Return the child's message, or None where what arrived is not one.

    A child that sent something the parent cannot read is a child that failed. Unpacking it blindly
    would raise inside the supervising thread, which is the one place in this module that must not
    raise: everything the supervisor stops is supposed to become a recorded content error and let
    the run finish.
    """
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return (value[0], value[1])
    return None


def _await_payload(
    receiver: Connection, process: SpawnProcess, timed_out: threading.Event
) -> tuple[str, object] | None:
    """Wait for the child's answer on the parent's own clock.

    The parent's ceiling must not depend on the child's death reaching it. Without this loop, the
    parent would sit in ``recv`` after the watchdog killed the child. ``recv`` waits forever and
    expects the dying child's end of the pipe to close and break the read. That is an inference
    about the operating system, and this module measures a platform property and does not assume it.

    On Windows, measurement shows that this inference does not hold. With the child confirmed dead,
    ``alive=False`` and exit code -15 from the kill, the parent stayed parked in
    ``WaitForMultipleObjects`` on an overlapped read that never completed. Cancellation reached the
    child perfectly and never reached the thread waiting for it. The watchdog's own test passes
    there, and the only difference is timing: it kills seconds after the spawn where cancellation
    kills milliseconds after it.

    ``poll`` takes a timeout and returns, and ``recv`` does not. The loop uses ``poll``. The three
    ways out are the child answering, the wall clock expiring, and the child dying, and the parent
    sees each of them itself.
    """
    while True:
        try:
            if receiver.poll(_POLL_SECONDS):
                return _as_payload(receiver.recv())
        except (EOFError, OSError):
            return None
        if timed_out.is_set():
            return None
        if not process.is_alive():
            # Dead and silent, or dead having written just before it went. One more look with no
            # wait, because a payload already in the pipe is the child's answer and losing it would
            # turn a successful extraction into a reported crash.
            try:
                return _as_payload(receiver.recv()) if receiver.poll(0) else None
            except (EOFError, OSError):
                return None


def _kill(process: SpawnProcess) -> None:
    """Send the child the signal it cannot catch, tolerating a race with exit."""
    try:
        if process.is_alive():
            process.kill()
    except (OSError, ValueError, AttributeError):
        # Already reaped, or never started. Either way there is nothing to kill and the caller's
        # contract is satisfied.
        pass


@dataclass(frozen=True, slots=True)
class SupervisionOutcome:
    """What running one extraction under supervision produced.

    Carries the result the deepening pass records plus what the supervision itself observed, because
    "this took eleven seconds and was killed" is a fact about the file worth having and is invisible
    in the result alone.
    """

    result: ExtractorResult
    #: Seconds of wall clock the child was alive.
    seconds: float
    #: Whether the parent killed the child rather than the child finishing.
    killed: bool = False
    #: Why it was killed, where it was: ``timeout``, ``memory``, ``crash``.
    reason: str | None = None

    @property
    def contained(self) -> bool:
        """Whether supervision stopped something the calling thread could not."""
        return self.killed or self.reason is not None


#: The exit status a child uses to say it stopped itself for taking too much memory. Distinct from
#: every signal-derived status, so the parent can tell "the engine's own ceiling fired" from "the
#: operating system killed it".
_MEMORY_EXIT = 101

#: How often the child looks at its own resident size. Small enough that a runaway allocation is
#: caught within a few tens of megabytes of the ceiling, large enough that the poll itself is free.
_MEMORY_POLL_SECONDS = 0.05


def _resident_bytes() -> int | None:
    """Return how much memory this process has taken, or ``None`` where unknown.

    There are three platforms and three measures, and the differences are written down here and not
    inferred at the call site. Darwin's is ``ru_maxrss``, the one figure POSIX offers a process
    about itself, in bytes there. Linux's is ``VmHWM`` from the process's own status, in kilobytes.
    Getting a unit wrong by a factor of 1,024 makes the ceiling either never fire or fire
    immediately.

    Never use ``ru_maxrss`` on Linux. Linux carries a process's peak resident size across the exec
    that starts a spawned child, so the child would read its parent's peak as its own, and a child
    of a parent that once held more than the child's ceiling would stop itself before doing
    anything. Measured on an Ubuntu machine: under a parent that had held 600 MB, each child read
    619 MB through ``ru_maxrss`` and 18 MB through ``VmHWM``, which belongs to the memory map the
    exec replaced. Darwin does not carry it: measured on a Mac, the same child read 12 MB either
    way.

    Windows has no ``resource`` module and reports through ``GetProcessMemoryInfo`` instead. It
    reports private usage and not working set, because Windows trims a working set under pressure,
    so a runaway allocator's working set can *fall* while it is still taking memory. Private commit
    does not fall, which is the property a ceiling needs.

    Any failure returns ``None``. The watcher then does not start, and the parent's wall clock is
    the only layer.
    """
    if sys.platform == "win32":  # pragma: no cover - Windows
        return _windows_private_bytes()
    elif sys.platform.startswith("linux"):  # pragma: no cover - Linux
        return _linux_peak_bytes()
    else:
        return _rusage_peak_bytes()


def _rusage_peak_bytes() -> int | None:
    """Return this process's peak resident size from ``ru_maxrss``, where that peak is its own.

    Bytes on Darwin, and kilobytes on the other POSIX systems that report it. Never read on Linux,
    which carries a parent's peak into it.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - a POSIX build without it
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def _linux_peak_bytes() -> int | None:  # pragma: no cover - Linux
    """Return this process's own peak resident size on Linux, from ``/proc/self/status``.

    ``VmHWM`` belongs to the current memory map, which an exec replaces, so it never
    holds a parent's peak. None where it cannot be read, which leaves the watcher unstarted, as on a
    platform with no measure at all.
    """
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _windows_private_bytes() -> int | None:  # pragma: no cover - Windows
    """Return this process's private commit on Windows, through ``ctypes``.

    Windows has no kernel limit on a process's address space. Without this function, a child on
    Windows would have only the parent's wall clock. The wall clock is not a memory ceiling: an
    extractor that allocates without bound allocates for the whole wall clock, and that can drive a
    machine into paging and leave it slow long afterwards. This function gives the self-watch a
    measure on Windows.

    ``PROCESS_MEMORY_COUNTERS_EX``'s ``PrivateUsage`` is the field, because
    Windows trims a working set under pressure so a runaway allocator's working set can *fall* while
    it is still taking memory. Private commit does not fall, and not falling is the property a
    ceiling needs.

    The library is resolved once and never inside the poll. This runs on a thread that starts before
    the child imports anything, so loading a DLL here would take the Windows loader lock twenty
    times a second while the main thread is importing modules, which is how a deadlock is written by
    accident. It is also plain waste: a handle resolved per poll is a handle resolved forty thousand
    times a minute for one integer.
    """
    reader = _windows_memory_reader()
    if reader is None:
        return None
    function, counters = reader
    counters.cb = ctypes.sizeof(counters)
    if not function(_windows_current_process(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PrivateUsage)


@functools.cache
def _windows_memory_reader() -> tuple[Any, Any] | None:  # pragma: no cover - Windows
    """Resolve ``GetProcessMemoryInfo`` and a counters buffer, once per process.

    Returns ``None`` where it cannot be resolved. The child then has no self-watch, and the parent's
    wall clock is the only layer.
    """

    class _Counters(ctypes.Structure):
        """``PROCESS_MEMORY_COUNTERS_EX``: two counters then nine sizes.

        Declared here rather than taken from a package, because this runs inside a spawned child
        whose whole cost is what it imports.
        """

        _fields_ = (
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        )

    # Reached by name rather than as an attribute, because ``WinDLL`` exists only on Windows. Naming
    # a platform-conditional attribute directly would break importing the engine on Windows, and it
    # would break the type check here.
    load_library = getattr(ctypes, "WinDLL", None)
    if load_library is None:
        return None

    # ``K32GetProcessMemoryInfo`` is in kernel32 on Windows 7 and later and needs
    # no second library loaded; psapi is the older home and the fallback.
    for library, symbol in (
        ("kernel32", "K32GetProcessMemoryInfo"),
        ("psapi", "GetProcessMemoryInfo"),
    ):
        try:
            function = getattr(load_library(library), symbol)
        except (OSError, AttributeError):
            continue
        function.restype = ctypes.c_int
        function.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
        return function, _Counters()
    return None


@functools.cache
def _windows_current_process() -> int | None:  # pragma: no cover - Windows
    """Return the current-process pseudo-handle, resolved once.

    ``GetCurrentProcess`` returns ``(HANDLE)-1``, which must reach the callee as a
    pointer-width value rather than as a 32-bit int, so the signature is declared rather than left
    to ctypes to guess.
    """
    load_library = getattr(ctypes, "WinDLL", None)
    if load_library is None:
        return None
    try:
        function = load_library("kernel32").GetCurrentProcess
    except (OSError, AttributeError):
        return None
    function.restype = ctypes.c_void_p
    function.argtypes = ()
    handle = function()
    return int(handle) if handle is not None else None


def _apply_memory_limit(limit_bytes: int | None) -> bool:
    """Ask the kernel to cap the child's address space. Returns whether it took.

    Linux enforces this limit and macOS refuses it. Darwin returns ``EINVAL`` for ``RLIMIT_AS``
    however the call is phrased, even setting soft equal to an infinite hard limit, and CPython
    surfaces that as a :class:`ValueError`. This is measured and not taken from documentation.
    Windows has no ``setrlimit`` at all.

    So this is one of two layers and never the only one. Reporting the *presence* of ``RLIMIT_AS``
    as though it were enforcement is a false safety claim, which is why the return value says what
    actually happened and :func:`describe_platform` measures rather than asks.
    """
    if limit_bytes is None or limit_bytes <= 0:
        return False
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return False
    for name in ("RLIMIT_AS", "RLIMIT_DATA"):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            _, hard = resource.getrlimit(which)
            ceiling = limit_bytes if hard == resource.RLIM_INFINITY else min(limit_bytes, hard)
            resource.setrlimit(which, (ceiling, hard))
        except (ValueError, OSError):
            continue
        return True
    return False


def _watch_own_memory(limit_bytes: int | None) -> None:
    """Have the child stop itself if it outgrows its ceiling.

    The second layer, and the one that works where the kernel will not help. A daemon thread polling
    this process's own resident size, exiting hard when it is exceeded. Hard rather than raising,
    because raising into an allocating loop needs an allocation to build the exception and that is
    precisely what is failing.

    There are two layers because each covers the other's gap. A Python allocation loop releases the
    interpreter lock often enough for this thread to run and be caught within milliseconds; a C
    library allocating with the lock held never lets it run, and the parent's wall clock catches
    that instead. Neither alone is sufficient and neither is redundant.
    """
    if limit_bytes is None or limit_bytes <= 0 or _resident_bytes() is None:
        return

    def poll() -> None:
        while True:
            resident = _resident_bytes()
            if resident is not None and resident > limit_bytes:
                os._exit(_MEMORY_EXIT)
            time.sleep(_MEMORY_POLL_SECONDS)

    watcher = threading.Thread(target=poll, name="ingence-memory-watch", daemon=True)
    watcher.start()


def _child_main(
    connection: Connection,
    name: str,
    module: str | None,
    context: ExtractionContext,
    memory_bytes: int | None,
) -> None:
    """Run one extractor and send back its result. The whole of the child.

    Deliberately tiny, and deliberately reaching the extractor through the registry rather than
    receiving it: a registered component is addressed by name everywhere else in the engine and a
    child process is not the place to make an exception, and an extractor instance is not reliably
    picklable while its name always is.

    ``spawn`` starts a fresh interpreter with an empty registry, so the child has to repopulate it.
    Registration is an import side effect everywhere in this engine, so the child imports the
    builtins and then the module that declared this extractor. That second import is what lets an
    extractor living outside the package be isolated at all, and it costs nothing when the extractor
    is a builtin, because the module is already in ``sys.modules``.
    """
    _apply_memory_limit(memory_bytes)
    _watch_own_memory(memory_bytes)
    try:
        import importlib

        from ingence.extractors import load_builtins

        load_builtins()
        if module:
            importlib.import_module(module)
        extractor = EXTRACTORS.get(name)
        # What a parser library says about the file comes back with the result for the journal,
        # rather than being printed to the terminal the child inherited.
        with captured() as said:
            result = extractor.extract(context)
        if said:
            result = dataclasses.replace(result, warnings=result.warnings + summarise(said))
    except MemoryError:
        # Sent before anything else is attempted. Allocating a message here can itself fail under a
        # tight ceiling, so the payload is a constant.
        _send(connection, ("memory", None))
        return
    except BaseException as exc:
        _send(connection, ("crash", f"{type(exc).__name__}: {exc}"))
        return
    _send(connection, ("ok", result))


def _send(connection: Connection, payload: object) -> None:
    """Send one message and close, tolerating a parent that has already gone."""
    try:
        connection.send(payload)
    except (BrokenPipeError, EOFError, OSError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            connection.close()


@dataclass(frozen=True, slots=True)
class Supervisor:
    """Runs isolated extractions, one child per extraction.

    ``workers`` is always one. The read pool is a separate class
    (:class:`ingence.executors.pool.ReadPool`).
    """

    timeout_seconds: float = 60.0
    memory_bytes: int | None = None
    workers: int = 1

    def run(
        self, name: str, context: ExtractionContext, *, ceiling_seconds: float | None = None
    ) -> SupervisionOutcome:
        """Run one extractor in a child, and contain whatever it does.

        Never raises for anything the child does. A hang, a crash, an exhausted address space and a
        clean decline all come back as an outcome, because one unreadable file must never fail a
        scan and the difference between them is a fact worth recording rather than an exception
        worth throwing.

        ``ceiling_seconds`` is this file's own wall clock, which the caller computes
        from its size (:func:`~ingence.extractors.limits.ceiling_for`). Per call rather than per
        supervisor, because the ceiling is a fact about the file being read and one supervisor reads
        every file in a run.
        """
        started = time.monotonic()
        ceiling = self.timeout_seconds if ceiling_seconds is None else ceiling_seconds
        spawn = multiprocessing.get_context("spawn")
        receiver, sender = spawn.Pipe(duplex=False)
        process = spawn.Process(
            target=_child_main,
            args=(sender, name, _module_of(name), context, self.memory_bytes),
            daemon=True,
        )
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            _kill(process)

        watchdog = threading.Timer(ceiling, expire)
        watchdog.daemon = True

        try:
            process.start()
        except OSError as exc:
            receiver.close()
            sender.close()
            return self._outcome(name, started, "crash", f"could not spawn: {exc}")

        with _LIVE_LOCK:
            _LIVE.add(process)
        # The parent's copy of the writing end must go, or the pipe never reports end-of-file when
        # the child dies and a crashed child looks like a hung one. This is the single easiest thing
        # to get wrong here.
        sender.close()
        watchdog.start()

        try:
            payload = _await_payload(receiver, process, timed_out)
        finally:
            watchdog.cancel()
            _kill(process)
            process.join(_REAP_SECONDS)
            with _LIVE_LOCK:
                _LIVE.discard(process)
            receiver.close()

        seconds = time.monotonic() - started

        if timed_out.is_set():
            return self._outcome(
                name,
                started,
                "timeout",
                f"exceeded its {ceiling:g}s ceiling and was killed",
                killed=True,
                seconds=seconds,
            )
        if payload is None:
            return self._outcome(
                name,
                started,
                self._reason_for(process.exitcode),
                self._message_for(process.exitcode),
                killed=True,
                seconds=seconds,
            )

        kind, body = payload
        if kind == "ok" and isinstance(body, ExtractorResult):
            return SupervisionOutcome(result=body, seconds=seconds)
        if kind == "memory":
            return self._outcome(
                name,
                started,
                "memory",
                "ran out of memory: the address-space limit refused an allocation",
                killed=True,
                seconds=seconds,
            )
        return self._outcome(name, started, "crash", str(body), seconds=seconds)

    @staticmethod
    def _reason_for(exitcode: int | None) -> str:
        """Read a reason out of how the child died.

        The engine's own ceiling has a status of its own, so "we stopped it" and "the operating
        system stopped it" stay distinguishable. They imply different remedies: the first is a limit
        a user can raise, the second is a machine that ran out.
        """
        if exitcode == _MEMORY_EXIT:
            return "memory"
        if exitcode is not None and exitcode < 0 and -exitcode in _MEMORY_SIGNALS:
            return "memory"
        return "crash"

    @staticmethod
    def _message_for(exitcode: int | None) -> str:
        """Say how the child died in terms a person can act on.

        Every route to a memory reason says "memory" and says which layer stopped it. There are
        three routes, and they fire on different platforms. The two layers are not redundant, so
        which one acted is what a person needs. A reason column that reads ``memory`` beside a
        message that reads ``killed by signal 9`` makes the reader do the translation.
        """
        if exitcode == _MEMORY_EXIT:
            return "exceeded its memory ceiling and stopped itself"
        if exitcode is None:
            return "child produced no result and no exit code"
        if exitcode is not None and exitcode < 0 and -exitcode in _MEMORY_SIGNALS:
            return f"ran out of memory: the operating system killed it with signal {-exitcode}"
        if exitcode < 0:
            return f"child killed by signal {-exitcode}"
        return f"child exited with status {exitcode} before sending a result"

    def _outcome(
        self,
        name: str,
        started: float,
        reason: str,
        message: str,
        *,
        killed: bool = False,
        seconds: float | None = None,
    ) -> SupervisionOutcome:
        """Turn a supervision failure into a recorded content error.

        This is a content error and not a deferral. ``DEFERRED`` says content work was not
        attempted, and everything here was attempted: the child started, ran, and was stopped or
        died. Something is known to be wrong with the file or with the parser, which is what
        ``CONTENT_ERROR`` means and is what makes the result correctly non-reusable, so the next run
        tries again rather than serving the failure forever.
        """
        version = _version_of(name)
        return SupervisionOutcome(
            result=failed(name, version, f"Isolation{reason.title()}Error", message),
            seconds=time.monotonic() - started if seconds is None else seconds,
            killed=killed,
            reason=reason,
        )


#: Names of the signals an operating system uses when it, rather than the engine, decides a process
#: has taken too much. Linux's out-of-memory killer sends ``SIGKILL``, and macOS answers a breached
#: address-space limit with ``SIGSEGV`` or ``SIGBUS`` as the allocator falls over rather than with a
#: clean :class:`MemoryError`.
_MEMORY_SIGNAL_NAMES = ("SIGSEGV", "SIGBUS", "SIGKILL")


#: The subset of those this platform actually defines.
#:
#: Resolved by name and not by attribute access, because ``SIGBUS`` and ``SIGKILL`` do not exist on
#: Windows. Naming them directly made importing this module an ``AttributeError`` there, and because
#: the deepening pass imports the supervisor and the CLI imports the deepening pass,
#: ``ingence --version`` failed on Windows before it printed anything. An engine that cannot start
#: is a worse failure than any it was built to contain.
#:
#: An attribute present on the machine where a line was written is not thereby a property of every
#: machine that runs it. The set is empty on Windows and nothing consults it there, because
#: ``multiprocessing`` reports a Windows exit code rather than a negative signal number, so the
#: empty set is the correct answer rather than a degraded one.
def memory_signals(source: object = signal) -> frozenset[int]:
    """Return the memory-kill signals one signal module defines.

    A function taking the module rather than a comprehension over the imported one, so the defensive
    lookup is testable without reloading anything. A test that reloaded this module to simulate a
    narrower platform would leave every other module holding the previous class objects and the
    previous ``_LIVE_CHILDREN`` registry, so a later cancellation would wait on children registered
    somewhere nothing was looking. That is a hang built out of a test, and it is avoided by having
    something pure to call.
    """
    return frozenset(
        int(member)
        for member in (getattr(source, name, None) for name in _MEMORY_SIGNAL_NAMES)
        if member is not None
    )


_MEMORY_SIGNALS = memory_signals()


def _module_of(name: str) -> str | None:
    """Return the module whose import registered this extractor.

    The registry is filled by import side effects, so the module that declared a component is the
    one a fresh interpreter has to import to find it. Read off the factory rather than configured,
    because a second place to state it is a second place for it to be wrong.
    """
    try:
        factory = EXTRACTORS.declaration(name).factory
    except (KeyError, AttributeError):
        return None
    return getattr(factory, "__module__", None)


def _version_of(name: str) -> str:
    """Return a registered extractor's version without constructing it.

    Through the declaration rather than the instance, because the instance may be exactly the thing
    that could not be built.
    """
    try:
        return EXTRACTORS.declaration(name).version
    except (KeyError, AttributeError):
        return "unknown"


def spawn_start_seconds() -> float:
    """Return what one empty spawned child costs on this machine, measured.

    Called by the throughput measurement rather than by the engine. ``spawn`` re-imports the
    interpreter and the package in every child, so this is the per-file floor for isolated
    extraction and the number the pooling decision turns on. Measuring it is cheaper than arguing
    about it.
    """
    spawn = multiprocessing.get_context("spawn")
    started = time.monotonic()
    process = spawn.Process(target=_noop, daemon=True)
    process.start()
    process.join()
    return time.monotonic() - started


def _noop() -> None:
    """Do nothing at all, in a fresh interpreter. The startup cost, isolated."""
    return None


def describe_platform() -> dict[str, object]:
    """Return what this platform actually offers, for the run record.

    The result is measured and not asked. ``hasattr(resource, "RLIMIT_AS")`` is true on macOS and
    the call it advertises fails there, so reporting presence would state a safety property that the
    platform does not have. The probe below tries the call and reports what happened.

    A safety property that holds on two platforms and silently does not on the third is what this
    whole module exists to avoid asserting.
    """
    return {
        "start_method": "spawn",
        # Whether the kernel will cap a child's address space for us.
        "address_space_limit_enforced": address_space_limit_enforced(),
        # Whether a child can watch its own resident size. The layer that works where the kernel
        # will not help.
        "self_memory_watch": _resident_bytes() is not None,
        "platform": sys.platform,
        "cpu_count": os.cpu_count() or 1,
    }


_ENFORCED: bool | None = None


def address_space_limit_enforced() -> bool:
    """Return whether this platform honours an address-space ceiling. Measured.

    The probe runs in a child, and the child is the point. A no-op ``setrlimit`` succeeds
    everywhere, so probing without changing anything cannot tell the platforms apart; and probing
    for real in the parent would lower the parent's own ceiling for as long as the probe takes,
    which could refuse an unrelated allocation in another thread. A throwaway child has neither
    problem: it lowers only its own limit and then exits.

    Costs one spawn, once per process, cached. Called for the run record, which happens once a run,
    so the cost is paid where it is invisible and the answer is right on every platform rather than
    assumed from the platform's name.
    """
    global _ENFORCED
    if _ENFORCED is not None:
        return _ENFORCED
    try:
        spawn = multiprocessing.get_context("spawn")
        receiver, sender = spawn.Pipe(duplex=False)
        process = spawn.Process(target=_probe_address_space, args=(sender,), daemon=True)
        process.start()
        sender.close()
        answer = receiver.recv() if receiver.poll(30.0) else False
        process.join(_REAP_SECONDS)
        receiver.close()
        _ENFORCED = bool(answer)
    except (OSError, EOFError, ValueError):  # pragma: no cover - platform dependent
        _ENFORCED = False
    return _ENFORCED


def _probe_address_space(connection: Connection) -> None:
    """Try to lower this child's own address-space ceiling and report the answer."""
    _send(connection, _apply_memory_limit(1 << 30))
