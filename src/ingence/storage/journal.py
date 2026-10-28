"""The run journal: exhaustive, structured, queryable activity recording.

The journal logs activity at every stage of the pipeline. When something misbehaves, nobody has to
read the whole pipeline to find where.

Structured rows, not free text. Every question anyone asks of such a log is a query. Which stage
was slow? What happened to this file? Why did the reuse rate drop between these two runs? Which
optional component was active when this result was produced? A text log answers none of these
until someone writes a parser, and the parser is always slightly wrong.

Exhaustive by default, and switchable off. The development build records everything, so that a
decision to remove something can rest on a measurement. The setting ``journal.enabled`` turns the
journal off.

Journalling never fails a run. A journal that could raise would turn a diagnostic into a failure
mode. Every write here is best-effort. A journal that cannot be written is recorded as a warning on
the run, and the error does not propagate.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from ingence.core.ids import IdKind, canonical_json, stable_id
from ingence.core.schema import JournalEvent
from ingence.storage.index_store import FragmentWriter
from ingence.storage.layout import table_for

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Journal", "NullJournal", "open_journal"]


class NullJournal:
    """The journal when recording is switched off.

    A no-op object rather than a ``None`` that every call site must check.
    Scattering ``if journal is not None`` through the scan would be noise, and
    the one place someone forgets is the place that crashes.
    """

    enabled = False

    def record(self, stage: str, event: str, **fields: Any) -> None:
        """Discard an event."""

    def timed(self, stage: str, event: str, **fields: Any) -> Any:
        """Return a context manager that discards a timing."""
        return _NullTimer()

    def close(self) -> int:
        """Return zero events written."""
        return 0


class _NullTimer:
    def __enter__(self) -> _NullTimer:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Timer:
    """Records one event on entry and a second, with a duration, on exit."""

    def __init__(self, journal: Journal, stage: str, event: str, fields: dict[str, Any]) -> None:
        self.journal = journal
        self.stage = stage
        self.event = event
        self.fields = fields
        self.started = 0

    def __enter__(self) -> _Timer:
        self.started = time.monotonic_ns()
        self.journal.record(self.stage, f"{self.event}.started", **self.fields)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        duration = time.monotonic_ns() - self.started
        # The failure path is journalled too, and says what failed. A log that records only
        # successes is of no use when something goes wrong.
        outcome = "finished" if exc_type is None else "failed"
        detail = dict(self.fields)
        if exc is not None:
            detail["error_type"] = type(exc).__name__
            detail["error_message"] = str(exc)
        self.journal.record(self.stage, f"{self.event}.{outcome}", duration_ns=duration, **detail)


class Journal:
    """Writes journal events into a staged run.

    Thread-safe, because a scan emits from several enumeration threads at once
    and a sequence number that raced would destroy the total order the journal
    exists to provide.
    """

    enabled = True

    def __init__(
        self,
        directory: Path,
        run_id: str,
        *,
        started_ns: int | None = None,
        flush_every_records: int = 4096,
    ) -> None:
        self.run_id = run_id
        self.started_ns = started_ns if started_ns is not None else time.monotonic_ns()
        self._writer = FragmentWriter(
            table_for(JournalEvent),
            directory,
            flush_every_records=flush_every_records,
            # Journal events are diagnostics. Flushing them on a timer as well would compete with
            # the scan for disk on a run that produces very few of them.
            flush_every_seconds=float("inf"),
        )
        self._lock = threading.Lock()
        self._sequence = 0
        self.write_failures = 0

    def record(
        self,
        stage: str,
        event: str,
        *,
        entry_id: str | None = None,
        relative_path: str | None = None,
        duration_ns: int | None = None,
        **detail: Any,
    ) -> None:
        """Record one event. Never raises."""
        try:
            now = time.monotonic_ns()
            with self._lock:
                self._sequence += 1
                sequence = self._sequence
                record = JournalEvent(
                    journal_id=stable_id(IdKind.RUN, {"run": self.run_id, "sequence": sequence}),
                    run_id=self.run_id,
                    sequence=sequence,
                    occurred_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    elapsed_ns=now - self.started_ns,
                    stage=stage,
                    event=event,
                    worker=threading.current_thread().name,
                    entry_id=entry_id,
                    relative_path=relative_path,
                    duration_ns=duration_ns,
                    detail=canonical_json(detail),
                )
                self._writer.add(record, now=now / 1e9)
        except Exception:
            self.write_failures += 1

    def timed(self, stage: str, event: str, **fields: Any) -> _Timer:
        """Record the start and end of a unit of work, with its duration."""
        return _Timer(self, stage, event, fields)

    def close(self) -> int:
        """Flush and return how many events were written."""
        try:
            with self._lock:
                return self._writer.close(now=time.monotonic_ns() / 1e9)
        except Exception:
            self.write_failures += 1
            return self._writer.rows_written


def open_journal(
    directory: Path, run_id: str, *, enabled: bool, started_ns: int | None = None
) -> Journal | NullJournal:
    """Return a journal, or the no-op one when recording is switched off."""
    if not enabled:
        return NullJournal()
    return Journal(directory, run_id, started_ns=started_ns)
