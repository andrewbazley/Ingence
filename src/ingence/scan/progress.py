"""Progress reporting: a snapshot on disk, and a callback for the terminal.

Two consumers, one source. The terminal wants a live line; a dashboard, a second
terminal, or a scheduler wants to know how a run started hours ago is doing
without attaching to it. Writing a small snapshot file satisfies the second for
almost nothing, and the first reads the same numbers.

The snapshot is replaced atomically for the same reason the run pointer is: a
reader must see the whole previous state or the whole new one, never a half
written file. It lives beside the staging directory and is removed when the run
publishes, so a snapshot lying around is itself a signal that a run did not
finish.

The snapshot holds counts, not percentages, until the total is known. A census
does not know how many entries it will find until it has found them, and a
progress bar that invents a denominator misleads. The census reports what it has
seen. A profile that has a previous run to compare against can offer an estimate,
and says that it is an estimate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from ingence.core.destinations import write_atomically
from ingence.core.ids import canonical_json
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ingence.scan.cancel import CancellationToken

__all__ = ["ProgressReporter", "ProgressSnapshot"]


@dataclass
class ProgressSnapshot:
    """What a run is doing right now.

    Mutable and updated in place under a lock, because it is written to on every
    directory and read from on an interval, and allocating a new one per entry
    would cost more than the scan.
    """

    run_id: str
    stage: str = "starting"
    started_at: float = field(default_factory=time.monotonic)
    directories_seen: int = 0
    entries_seen: int = 0
    entries_reused: int = 0
    entries_examined: int = 0
    #: A census's own counts of what it has found so far: the folders and the files among
    #: the names seen, and the bytes those files hold. ``directories_seen`` counts the
    #: listings finished, and a folder can take more than one. At the census's end these
    #: are the counts its run record carries.
    folders_found: int = 0
    files_found: int = 0
    bytes_seen: int = 0
    errors: int = 0
    #: An inference's counter: the rules it has gone through, of the rules it will go
    #: through. The total is known when the pass begins, so this is a count of a known
    #: whole. ``rules_total`` is None for any other stage.
    rules_run: int = 0
    rules_total: int | None = None
    #: The readouts step's counter: the views prepared, of the views there are
    #: (``ingence.readouts.STEPS``). ``views_total`` is None for any other stage.
    views_done: int = 0
    views_total: int | None = None
    #: The directory currently being enumerated, for a person watching. Relative,
    #: never absolute, so a snapshot can be shown without disclosing where the
    #: data pool sits on this machine.
    current: str = ""
    #: Set once the total is genuinely known, which for a census is at the end.
    total_entries: int | None = None
    finished: bool = False

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def entries_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        return self.entries_seen / elapsed if elapsed > 0 else 0.0


class ProgressReporter:
    """Updates a snapshot, writes it on an interval, and notifies a callback.

    The writing thread is a daemon and waits on the cancellation token rather
    than sleeping, so it stops within milliseconds of a cancellation instead of
    at the end of its interval. That difference is why a user believes Ctrl-C worked.

    ``project`` is the project ``path`` is inside, where it is one: then the snapshot's folder is
    checked to be the project's own, nothing on the way to it a link, before each write and the
    removal (``storage.layout.owned_path``).
    """

    def __init__(
        self,
        snapshot: ProgressSnapshot,
        *,
        path: Path | None = None,
        project: Path | None = None,
        interval_seconds: float = 1.0,
        token: CancellationToken | None = None,
        on_update: Callable[[ProgressSnapshot], None] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.path = path
        self.project = project
        self.interval_seconds = interval_seconds
        self.token = token
        self.on_update = on_update
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- updating ---------------------------------------------------------

    def advance(self, **deltas: int) -> None:
        """Add to counters. Any integer field of the snapshot may be named."""
        with self._lock:
            for name, delta in deltas.items():
                setattr(self.snapshot, name, getattr(self.snapshot, name) + delta)

    def set(self, **values: object) -> None:
        """Replace fields outright, for the ones that are not counters."""
        with self._lock:
            for name, value in values.items():
                setattr(self.snapshot, name, value)

    def read(self) -> ProgressSnapshot:
        """Return a consistent copy, safe to read while the scan runs."""
        with self._lock:
            return ProgressSnapshot(**asdict(self.snapshot))

    # -- publishing -------------------------------------------------------

    def emit(self) -> None:
        """Write the snapshot and notify the callback, once."""
        snapshot = self.read()
        if self.path is not None:
            payload = {
                **asdict(snapshot),
                "elapsed_seconds": round(snapshot.elapsed_seconds, 3),
                "entries_per_second": round(snapshot.entries_per_second, 1),
            }
            # started_at is a monotonic clock reading, meaningful only inside
            # this process. Publishing it would invite a reader to subtract it
            # from a wall clock and get nonsense.
            payload.pop("started_at", None)
            target = self._owned(self.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(target, canonical_json(payload))
        if self.on_update is not None:
            self.on_update(snapshot)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.emit()
            if self.token is not None and self.token.wait(self.interval_seconds):
                break
            if self.token is None:
                self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        """Begin reporting in the background."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="ingence-progress", daemon=True)
        self._thread.start()

    def stop(self, *, finished: bool = True) -> None:
        """Stop reporting and emit one final snapshot."""
        self.set(finished=finished)
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        self.emit()

    def remove_snapshot(self) -> None:
        """Delete the snapshot file, once a run has published.

        A snapshot left behind is a signal that a run did not finish, so removing
        it is part of finishing rather than tidying.
        """
        if self.path is not None:
            self._owned(self.path).unlink(missing_ok=True)

    def _owned(self, path: Path) -> Path:
        return path if self.project is None else owned_path(self.project, path)

    def __enter__(self) -> ProgressReporter:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop(finished=exc_type is None)
