"""An exclusive lock the operating system releases when its holder dies, on all three platforms.

Why a lock and not a process identifier: whether a worker is still alive is the one question
reconciliation must answer, and a process identifier answers it badly. Identifiers are reused, a
zombie still has one, and asking Windows about another process's identifier needs its API. The
operating system releases a lock on a file when the process holding it exits, however it exits (a
kill, a crash, a reboot after a power cut). So "can I take it?" means "is nobody holding it?".

POSIX uses ``flock``, which conflicts between two opens of one file even in one process (unlike
``lockf``), so two servers in one test process see each other. Windows uses ``msvcrt.locking``
on the file's first byte, which conflicts between handles. Neither lock is ever waited on in the
kernel. A blocking acquire polls, so it can give up.

After a kill, POSIX lets go of a dead process's ``flock`` as its descriptors close, before its
parent can reap it. Windows lets go of a terminated process's byte locks "when available system
resources" allow (``LockFileEx``'s documentation), so on Windows "held" can outlast a kill for a
moment. This behaviour comes from the documentation and has not been measured here, because the
measurement needs Windows. Nothing here reads a lock once and trusts it. The worker waits up to
thirty seconds for the lock, a supervisor that finds it held follows the holder until it is let
go, and a stopped orphan is waited on until its lock is released.

Who holds it: a holder may describe itself (its process identifier, what it is, the job it runs)
in ``<lock>.holder`` beside the lock. The holder writes the file after it takes the lock and
removes it before it lets go. The file is read only while the lock is held (:func:`holder_of`),
so what it says is about the holder now. A holder that died leaves it stale, and the next holder
writes its own. A holder that says nothing of itself removes it. The file is how the supervisor
stops only the worker it means to, by the identifier that worker wrote, and how a hand-run
command says what it waits for.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

__all__ = [
    "FileLock",
    "ProjectBusyError",
    "busy_words",
    "held_elsewhere",
    "holder_of",
    "publishing",
]

#: The errors that mean "another holds it". Any other error is a filesystem that cannot lock
#: (some NFS, FUSE and SMB mounts). The code raises it and never reads it as "held", because if
#: it read the error as held, a supervisor would never take over and every job would wait with
#: nothing said.
_HELD_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, "EWOULDBLOCK", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EACCES", None),
        getattr(errno, "EDEADLK", None),
    )
    if code is not None
)

if sys.platform == "win32":  # pragma: no cover - exercised by the Windows CI job
    import msvcrt

    def _try(descriptor: int) -> bool:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in _HELD_ERRNOS:
                return False
            raise
        return True

    def _let_go(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try(descriptor: int) -> bool:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in _HELD_ERRNOS:
                return False
            raise
        return True

    def _let_go(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


class FileLock:
    """One exclusive lock on one file, taken without blocking or by polling until a deadline."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    @property
    def held(self) -> bool:
        return self._descriptor is not None

    @property
    def holder_path(self) -> Path:
        return self.path.with_name(self.path.name + ".holder")

    def acquire(
        self,
        *,
        timeout: float = 0.0,
        poll: float = 0.05,
        holder: Mapping[str, Any] | None = None,
    ) -> bool:
        """Take the lock, waiting at most ``timeout`` seconds. Say whether it was taken.

        ``holder`` describes who takes it, written beside the lock with this process's
        identifier once it is held (see the module's docstring).
        """
        if self._descriptor is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            if _try(descriptor):
                self._descriptor = descriptor
                if holder is not None:
                    self._describe(holder)
                else:
                    # A holder that says nothing clears what a dead holder left, so a stale
                    # description can never name an unrelated process.
                    with contextlib.suppress(OSError):
                        self.holder_path.unlink(missing_ok=True)
                return True
            os.close(descriptor)
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll)

    def _describe(self, holder: Mapping[str, Any]) -> None:
        from ingence.core.destinations import create_exclusive

        said = json.dumps({**holder, "pid": os.getpid()}, sort_keys=True)
        # A new file of a name no other writer uses, never a fixed one opened with truncation.
        handle, staged = create_exclusive(self.holder_path, binary=False)
        with handle:
            handle.write(said)
        for _ in range(100):
            try:
                # Windows refuses to replace a file a reader holds open. The reader lets go soon.
                os.replace(staged, self.holder_path)
                return
            except PermissionError:  # pragma: no cover - Windows only
                time.sleep(0.02)
        staged.unlink(missing_ok=True)  # pragma: no cover - said nowhere, held all the same

    def release(self) -> None:
        """Let go of the lock. Letting go of a lock not held does nothing."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        # Before letting go, so a description is never removed from under the next holder. On
        # Windows a reader holding it open refuses the removal. It is stale then, and harmless.
        with contextlib.suppress(OSError):
            self.holder_path.unlink(missing_ok=True)
        try:
            _let_go(descriptor)
        finally:
            os.close(descriptor)


def held_elsewhere(path: Path) -> bool:
    """Whether another holder has the lock on ``path`` now (taking and releasing it if not)."""
    if not path.exists():
        return False
    probe = FileLock(path)
    if probe.acquire():
        probe.release()
        return False
    return True


def holder_of(path: Path) -> dict[str, Any] | None:
    """What the holder of the lock on ``path`` said of itself while somebody holds it, or None."""
    if not held_elsewhere(path):
        return None
    try:
        said = json.loads(path.with_name(path.name + ".holder").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return said if isinstance(said, dict) else None


class ProjectBusyError(RuntimeError):
    """Somebody else is publishing runs in this project, and the caller would not wait."""

    def __init__(self, message: str, *, said: str = "") -> None:
        super().__init__(message)
        #: Who holds the project, in a sentence, without what the caller should do about it.
        self.said = said


#: How long a hand-run command tries for the project before it says who holds it: a supervisor
#: looking whether the lock is held takes and lets go of it in microseconds.
PUBLISH_TRY_SECONDS = 1.0
#: How often a command told to wait looks again.
PUBLISH_WAIT_POLL_SECONDS = 0.5


def busy_words(held: Mapping[str, Any] | None) -> str:
    """Who holds a project's worker lock, in a sentence a person can act on."""
    if held is None:
        return "Another process is publishing runs in this project."
    pid = held.get("pid")
    process = f" (process {pid})" if pid else ""
    if held.get("role") == "worker":
        return (
            f"A Dashboard job ({held.get('job_id')}, attempt {held.get('attempt')}){process} is "
            "publishing runs in this project."
        )
    return (
        f"{held.get('command') or 'Another command'}{process} is publishing runs in this project."
    )


@contextmanager
def publishing(
    project: Path,
    *,
    command: str,
    wait: bool,
    on_wait: Callable[[str], None] | None = None,
) -> Iterator[None]:
    """Hold the project's one publish lock while a hand-run command publishes runs.

    The lock is the Dashboard worker's (``jobs/worker.lock``), so a hand-run ``index``, ``dive``
    or ``infer`` and a Dashboard job never publish over each other. Each run is made from the run
    current when it starts, and two made side by side would leave the pointer on whichever
    finished last, built from a run that is no longer current. A command that finds the lock held
    says who holds it and stops (:class:`ProjectBusyError`), or, told to ``wait``, says so once
    and waits. The supervisor, finding a command holding the lock, starts no job until it is let
    go. Creating the lock creates ``jobs/`` and nothing else in it.
    """
    from ingence.jobs import JOBS_DIR, WORKER_LOCK

    lock = FileLock(project / JOBS_DIR / WORKER_LOCK)
    holder = {"role": "command", "command": command}
    if not lock.acquire(timeout=PUBLISH_TRY_SECONDS, holder=holder):
        said = busy_words(holder_of(lock.path))
        if not wait:
            message = (
                f"{said} This command would publish over it: wait for it to finish, or run "
                "again with --wait."
            )
            raise ProjectBusyError(message, said=said)
        if on_wait is not None:
            on_wait(f"{said} Waiting for it to finish.")
        lock.acquire(timeout=float("inf"), poll=PUBLISH_WAIT_POLL_SECONDS, holder=holder)
    try:
        yield
    finally:
        lock.release()
