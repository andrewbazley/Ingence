"""Cancellation that actually cancels.

A cancelled Python future does not stop a blocking operating-system read. If a
scan is waiting on a stalled network share, cancelling the future marks it
cancelled and the thread stays blocked in the kernel until the share answers or
the mount times out, which can be minutes. Anything built on futures alone will
therefore appear to hang after a user presses Ctrl-C, which is the single most
damaging thing a long-running tool can do to a person's trust in it.

Two mechanisms, matched to two kinds of work.

Cooperative cancellation serves the census. Enumeration is a great many short
operations, so a token checked between directories bounds the delay at roughly
one directory. That is fast enough to feel immediate, and it needs no process
machinery.

Termination serves extraction. Opening a file can block for an unbounded time
inside a single call, so an extractor declaring ``Isolation.SUBPROCESS`` runs in
a supervised child that is killed rather than asked. Cancelling the token kills
every live child at once, through
:func:`ingence.executors.supervisor.terminate_all_children`. Extractors that
declare nothing still run in the calling thread, where a token cannot interrupt
them. This is a deliberate trade: bounded work cannot hang, so it does not pay
for a process.

A cancelled run publishes nothing. The transactional write protocol makes this
free. A run is assembled in a staging directory that no reader ever resolves
through the pointer, so abandoning it leaves the previous run current and the
project valid. Cancellation is therefore safe at any moment, which is why the
engine offers it.

A stopped run keeps what it has. A token asked to :meth:`stop` says stop and
keep. A content pass finishes the file in hand, counts the rest as not reached
and publishes as an exhausted budget does: a complete snapshot with honest gaps.
The census and inference are atomic, so for them a stop is a discard. A stop can
be escalated to a discard by :meth:`cancel`, and never the other way. On a
terminal the first Ctrl-C stops, the second discards, and the third exits.

Running out of a deepening budget is not cancellation and does not come through
here. A budget that runs out completes the run and marks what it did not reach as
unexamined. A budget can never cause a run to fail.
"""

from __future__ import annotations

import signal
import threading
from contextlib import contextmanager, suppress
from types import FrameType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["CancellationToken", "CancelledError", "cancel_on_signal"]


class CancelledError(Exception):
    """Raised at a checkpoint after cancellation was requested."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CancellationToken:
    """A thread-safe flag saying that work should stop, and why.

    Deliberately one-way. A token that could be un-cancelled would let a race
    resurrect work a user asked to stop, and there is no situation where that is
    what anyone wants.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._keep = False
        self._lock = threading.Lock()

    def cancel(self, reason: str = "cancelled") -> None:
        """Request that work stop and discard, and kill every supervised child immediately.

        The first reason given is the one kept. The first is the cause and
        anything after it is a consequence. A Ctrl-C followed by a cascade of
        worker failures should report the Ctrl-C. A discard that follows a stop
        takes the discard's reason: the stop asked to keep, and what it kept was
        then thrown away for the reason given here.

        Children are killed here and not at the next checkpoint, because a child
        inside a blocking call never reaches the checkpoint. Setting the flag and
        waiting would leave a user who pressed Ctrl-C watching a parser finish.
        This is the behaviour the mechanism exists to prevent.
        """
        with self._lock:
            if self._reason is None or self._keep:
                self._reason = reason
            self._keep = False
            self._event.set()
        self._terminate_children()

    def stop(self, reason: str = "stopped") -> None:
        """Request that work stop and keep what it has.

        A content pass finishes the file in hand within its own ceiling, counts the rest as
        not reached and publishes, as an exhausted budget does. Nothing is killed, because
        a killed reading would be recorded as a content error against a file that was only
        interrupted. A token already cancelled is left as it is. A stop never softens a
        discard, and a second stop changes nothing.
        """
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._keep = True
            self._event.set()

    @staticmethod
    def _terminate_children() -> int:
        """Kill supervised children, tolerating a build with no supervisor.

        Imported here rather than at module scope: this module is low-level and
        is imported by things that have no business pulling in the extraction
        stack, and the import is paid once, on a path a user takes at most twice.
        """
        try:
            from ingence.executors.supervisor import terminate_all_children
        except ImportError:  # pragma: no cover - the supervisor is not optional
            return 0
        return terminate_all_children()

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested, to keep or to discard."""
        return self._event.is_set()

    @property
    def keeps(self) -> bool:
        """Whether the request is to stop and keep what was read, rather than to discard."""
        with self._lock:
            return self._event.is_set() and self._keep

    @property
    def reason(self) -> str | None:
        """Why work was cancelled, or None if it was not."""
        with self._lock:
            return self._reason

    def raise_if_cancelled(self) -> None:
        """Stop here if cancellation was requested.

        Called between units of work rather than inside them, so that a unit
        either completes and is recorded or never started. A unit abandoned
        halfway would have to be distinguished from one that failed, and there
        is no need to create that problem.
        """
        if self._event.is_set():
            raise CancelledError(self._reason or "cancelled")

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or the timeout elapses.

        Used instead of sleeping in background threads, so a progress reporter
        stops within milliseconds of a cancellation rather than at the end of its
        interval.
        """
        return self._event.wait(timeout)


@contextmanager
def cancel_on_signal(
    token: CancellationToken,
    signals: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM),
    *,
    keep_first: bool = False,
) -> Iterator[CancellationToken]:
    """Route interrupt signals into a token for the duration of a block.

    The first signal cancels. A second signal restores the default handler and
    re-raises, so a user who presses Ctrl-C twice gets an immediate exit. A program
    that has stopped listening should not tell the user to be patient.

    With ``keep_first`` the ladder has three rungs. The first signal stops and
    keeps what was read, the second discards it, and the third exits. A pass that
    can publish what it has should not throw an hour's reading away on one Ctrl-C.
    The third rung leaves the staging directory behind. The default handler ends the
    process where it stands, so the run being assembled stays under the project's
    staging as an abandoned directory that no reader resolves through the pointer.
    Nothing in the engine removes it. ``ingence fix`` names it as safe to delete, and a person
    removes it.

    Previous handlers are restored on exit, including on the exception path,
    because a library that permanently rewires signal handling is a library that
    breaks the program embedding it.

    Signal handlers can only be installed from the main thread. Called from
    anywhere else this yields the token unchanged rather than failing, since
    losing signal handling is a smaller problem than refusing to run.
    """
    if threading.current_thread() is not threading.main_thread():
        yield token
        return

    previous: dict[signal.Signals, object] = {}

    def handle(signum: int, frame: FrameType | None) -> None:
        del frame
        name = signal.Signals(signum).name
        if token.cancelled and not token.keeps:
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)
            return
        if token.cancelled:
            token.cancel(f"interrupted twice by {name}: what was read is discarded")
            return
        if keep_first:
            token.stop(f"interrupted by {name}")
        else:
            token.cancel(f"interrupted by {name}")

    try:
        for number in signals:
            try:
                previous[number] = signal.getsignal(number)
                signal.signal(number, handle)
            except (OSError, ValueError):  # pragma: no cover - platform dependent
                previous.pop(number, None)
        yield token
    finally:
        for number, handler in previous.items():
            # Restoring must not raise on the exception path, or the original
            # failure is replaced by a signal-handling one and the cause is lost.
            with suppress(OSError, ValueError):  # pragma: no cover
                signal.signal(number, handler)  # type: ignore[arg-type]
