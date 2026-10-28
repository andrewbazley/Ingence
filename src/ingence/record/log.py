"""Append to the Record: one decision, attributed, and never rewritten.

The Record is one of the four inputs that make an interpretation reproducible. Every interpretation
is reproducible from (Index, Rulebook, Dictionary, Record), so a person's decision is part of a
reproducible interpretation and not an exception to one. This module has one narrow job: put a
decision in, in a form that nothing can quietly change afterwards.

The log is append-only by construction. Each event is one Parquet fragment, and the fragment's name
is the event's sequence number. Nothing is rewritten, merged or deleted, so no code path here can
lose a decision. The directory listing is the log's index, so the next sequence costs a listing and
not a read of every event ever made.

A sequence is claimed under an exclusive lock, because two appenders are real. The Dashboard's
Review view writes while a command-line session may also be running, both on one machine. Reading
the last sequence and then writing it is not atomic on its own. Two appenders that both read 7 would
both write 7. That gives two decisions under one identifier, in a table whose primary key is the
event identifier. The lock is a file created with ``O_CREAT | O_EXCL``, which is atomic on all three
platforms. :func:`exclusive_creation_refuses` measures that and does not assume it. A platform
property is measured and never assumed.

A fragment is complete before it is visible. The bytes are written into the project's staging
directory, flushed, and only then moved into ``record/``. An interruption therefore leaves either
nothing or one whole event. It never leaves a truncated Parquet file in the one table that has no
second copy anywhere.

The clock is passed in and never read here. A decision's moment is part of what the decision is, so
it is an argument. Both inputs to a run identifier are passed in the same way, so that a test can
reproduce a run exactly. :func:`now` exists for a caller at the edge that has nowhere else to get a
moment.

An event cannot be born orphaned. ``RecordEvent.orphaned`` says that a decision's basis has
vanished, for example an endpoint was deleted or a rule was withdrawn. An append-only log cannot
flip a flag on a row it has already written, so orphanhood is what a reader concludes when the
Record is replayed against a run. A writer never asserts it. :func:`append_event` has no argument
through which a caller could ask for it. ``claim_for`` likewise has no argument through which a
caller could ask for a tier. Both ceilings hold by construction.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq

from ingence.core.destinations import create_exclusive
from ingence.core.ids import PROJECT_SCOPE, canonical_json, digest_text, record_event_id
from ingence.core.schema import MovedBy, RecordEvent, RecordEventKind
from ingence.core.versions import refuse_newer
from ingence.storage.commit import fsync_directory, fsync_file, run_path
from ingence.storage.index_store import record_from_row, row_for, write_fragment
from ingence.storage.layout import RECORD_DIR, STAGING_DIR, owned_path, table_for

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from ingence.core.ids import Scope

__all__ = [
    "HELD_PROJECTS",
    "LOCK_FILE",
    "WITHDRAWABLE_PREFIXES",
    "AppendError",
    "RecordLockedError",
    "WithdrawRefusedError",
    "answer_digest",
    "answer_withdrawals",
    "append_event",
    "at_new_places",
    "exclusive_creation_refuses",
    "fragment_name",
    "in_force",
    "latest_by_target",
    "moved_entries",
    "next_sequence",
    "notes_by_target",
    "now",
    "read_at",
    "read_bound",
    "read_events",
    "record_directory",
    "sequences_present",
    "withdrawable",
    "withdrawn_ids",
]

#: Held for the whole of a claim: read the sequences present, write the fragment, move it
#: in. Named with a leading dot so it never looks like a fragment, and it is the only file
#: in ``record/`` that is not one.
LOCK_FILE = ".append.lock"

#: How long an appender waits for another to finish before giving up. An append writes one
#: small fragment, so a wait longer than this means a holder died rather than a holder is
#: slow, and saying so is more useful than waiting.
LOCK_TIMEOUT_SECONDS = 10.0

#: How often the wait re-tries the claim. Short enough that two sequential appends in a
#: test do not visibly pause, long enough not to spin.
LOCK_POLL_SECONDS = 0.02

#: Width of the sequence in a fragment's name, so a listing sorts in sequence order as
#: text. Ten digits is more decisions than a person will ever make and the width costs
#: nothing; a name that sorted differently from its number would make the listing lie.
_SEQUENCE_WIDTH = 10


class AppendError(Exception):
    """An event was refused. The Record is unchanged."""


class RecordLockedError(AppendError):
    """Another appender holds the lock, or one died holding it."""


def record_directory(project: Path) -> Path:
    """Return the project's Record directory, which spans runs."""
    return project / RECORD_DIR


def now() -> str:
    """Return the current instant in the form the engine writes elsewhere.

    The function is for a caller at the edge, such as a command or a request handler, that has
    nowhere else to get a moment from. Nothing inside this module calls it, so the write path stays
    a pure function of its arguments.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fragment_name(sequence: int) -> str:
    """Return the fragment name that holds one sequence."""
    return f"{sequence:0{_SEQUENCE_WIDTH}d}.parquet"


def sequences_present(project: Path) -> tuple[int, ...]:
    """Return the sequences already in the Record, ascending.

    Read from the fragment names rather than from the events, because the name is the
    sequence and a listing is cheaper than opening every fragment ever written. A test
    holds the two in agreement, since a name that disagreed with the row inside it would
    make this function quietly wrong.
    """
    directory = record_directory(project)
    if not directory.is_dir():
        return ()
    found: list[int] = []
    for path in directory.glob("*.parquet"):
        try:
            found.append(int(path.stem))
        except ValueError:
            message = f"{path.name} is in the Record and is not named for a sequence"
            raise AppendError(message) from None
    return tuple(sorted(found))


def next_sequence(project: Path) -> int:
    """Return the sequence the next event takes. One for an empty Record.

    The highest present plus one, rather than the count plus one. They are the same
    while nothing is missing, and if a fragment ever went astray the count would hand
    out a number already used, which is the one failure this function must not have.
    """
    present = sequences_present(project)
    return present[-1] + 1 if present else 1


def exclusive_creation_refuses(directory: Path) -> bool:
    """Measure whether this filesystem refuses to create a file that exists.

    The lock rests on ``O_CREAT | O_EXCL`` being atomic and failing when the target is there. POSIX
    and Windows both specify that. This function checks it on the machine in use instead of taking
    the specification's word for it, because platform assumptions made without measuring have turned
    out to be wrong somewhere.

    Returns True when the second creation was refused. The probe leaves nothing behind.
    """
    probe = directory / ".exclusive-probe"
    descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        second = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return True
    else:
        os.close(second)
        return False
    finally:
        probe.unlink(missing_ok=True)


#: Windows refuses to create a file whose deletion is still completing. A lock that another appender
#: has just removed is, for a moment, "delete pending", and creating it then fails with "permission
#: denied" and not "exists". On Windows that refusal is waited through like a lock that is held.
#: Elsewhere it means what it says.
_DELETE_PENDING_REFUSES = os.name == "nt"


def _claim_lock(directory: Path, *, timeout: float) -> int:
    """Create the lock file, waiting for whoever holds it. Returns its descriptor."""
    path = directory / LOCK_FILE
    deadline = time.monotonic() + timeout
    while True:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except PermissionError as exc:
            if not _DELETE_PENDING_REFUSES or time.monotonic() >= deadline:
                message = f"the Record at {directory} cannot be written: {exc.strerror}"
                raise AppendError(message) from exc
            time.sleep(LOCK_POLL_SECONDS)
        except FileExistsError:
            if time.monotonic() >= deadline:
                message = (
                    f"the Record's lock at {path} was held for more than {timeout:g} s. "
                    "An append writes one small fragment, so this is a holder that died "
                    "rather than one that is slow: remove the file to release it."
                )
                raise RecordLockedError(message) from None
            time.sleep(LOCK_POLL_SECONDS)
        except OSError as exc:  # pragma: no cover - a read-only or full project directory
            if exc.errno in {errno.EROFS, errno.EACCES, errno.ENOSPC}:
                message = f"the Record at {directory} cannot be written: {exc.strerror}"
                raise AppendError(message) from exc
            raise


def _release_lock(directory: Path, descriptor: int) -> None:
    os.close(descriptor)
    (directory / LOCK_FILE).unlink(missing_ok=True)


def _checked_instant(occurred_at: str) -> str:
    """Refuse a moment that is not an instant with a zone.

    A decision's moment is read by a person reading the log and compared with a run's
    own times, so a wall time with no zone is two hours wrong somewhere and looks
    right. Parsed rather than pattern-matched, and returned as given, because the bytes
    a person wrote are what the log should hold.
    """
    text = occurred_at.strip()
    if not text:
        message = "an event states no moment; a decision without a when is not attributable"
        raise AppendError(message)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        message = (
            f"{occurred_at!r} is not an instant the engine writes, such as 2026-09-17T14:02:11Z"
        )
        raise AppendError(message) from exc
    if parsed.tzinfo is None:
        message = (
            f"{occurred_at!r} states no zone. A decision's moment is compared with a run's "
            "own times, and a wall time with no zone cannot be."
        )
        raise AppendError(message)
    return text


def append_event(
    project: Path,
    *,
    kind: RecordEventKind,
    actor: str,
    occurred_at: str,
    target: str,
    against_run_id: str,
    payload: Mapping[str, Any] | None = None,
    scope: Scope = PROJECT_SCOPE,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> RecordEvent:
    """Append one decision to the Record and return it as written.

    Every argument but the scope and the payload is required. Each is one of the four things that an
    event must carry: who decided (``actor``), when (``occurred_at``), against which run
    (``against_run_id``), and what it decides (``kind`` and ``target``, with ``payload`` for the
    detail). None of them has a default, because a default here would be the engine inventing an
    attribution.

    The run must be one that this project has published. A decision is read in the light of what the
    person was looking at when they made it. A run identifier that names nothing makes that
    impossible, while looking exactly like a decision that can be read.
    """
    # The project's own Record folder, nothing on the way to it a link (``owned_path``).
    directory = owned_path(project, record_directory(project))
    directory.mkdir(parents=True, exist_ok=True)

    if not actor.strip():
        message = "an event names no actor; a decision nobody made is not attributable"
        raise AppendError(message)
    if not target.strip():
        message = "an event names no target; a decision about nothing cannot be replayed"
        raise AppendError(message)
    moment = _checked_instant(occurred_at)
    if kind is RecordEventKind.WITHDRAW:
        _check_withdrawal(project, payload)
    if not run_path(project, against_run_id).is_dir():
        message = (
            f"no run {against_run_id!r} is published in this project. An event is made "
            "against what a person was looking at, and a run that is not there cannot be "
            "looked at."
        )
        raise AppendError(message)

    detail = canonical_json(dict(payload) if payload else {})
    spec = table_for(RecordEvent)
    # The project's own staging, nothing on the way to it a link, before a fragment is moved out.
    staging = owned_path(project, STAGING_DIR)
    staging.mkdir(parents=True, exist_ok=True)

    descriptor = _claim_lock(directory, timeout=timeout)
    try:
        if kind is RecordEventKind.WITHDRAW and payload and "answer" in payload:
            # Checked with the lock held. Two clicks on one button reach this one after the other,
            # and the second finds the first's withdrawal already written.
            _check_answer_withdrawal(project, dict(payload))
        sequence = next_sequence(project)
        event = RecordEvent(
            event_id=record_event_id(
                actor=actor,
                occurred_at=moment,
                kind=kind.value,
                target=target,
                sequence=sequence,
            ),
            sequence=sequence,
            kind=kind,
            actor=actor,
            occurred_at=moment,
            scope=scope,
            target=target,
            against_run_id=against_run_id,
            payload=detail,
        )
        # Into a new file of a name no other writer uses, never a fixed name opened with
        # truncation: a file or a link standing at one is never written through.
        handle, incoming = create_exclusive(staging / fragment_name(sequence))
        try:
            with handle:
                write_fragment([row_for(event, spec)], spec, handle)
            fsync_file(incoming)
        except BaseException:
            incoming.unlink(missing_ok=True)
            raise
        destination = directory / fragment_name(sequence)
        if destination.exists():  # pragma: no cover - the lock is what prevents this
            incoming.unlink(missing_ok=True)
            message = f"sequence {sequence} is already in the Record at {destination}"
            raise AppendError(message)
        os.replace(incoming, destination)
        fsync_directory(directory)
    finally:
        _release_lock(directory, descriptor)
    return event


#: The targets whose answers a person may withdraw one at a time. They are what a word means
#: (``term:``), how a spelling that a capital divides reads (``split:``), two spellings that are
#: unrelated (``distinct:``), and a sense or one spelling of one turned off (``sense:``,
#: ``surface:``). The answer on every other target is taken back by answering again, or by going
#: back to an earlier run.
WITHDRAWABLE_PREFIXES: tuple[str, ...] = ("term", "split", "distinct", "sense", "surface")


class WithdrawRefusedError(AppendError):
    """A withdrawal of one answer was refused, in words.

    ``stale`` says the page read an answer the Record does not hold so (no event has that
    sequence and digest): the page is out of date, which is a conflict and not a bad request.
    """

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


def answer_digest(event: RecordEvent) -> str:
    """The digest a page names an answer by: everything the event says, so that an answer
    changed or replaced since the page read it cannot be withdrawn under the old name."""
    return digest_text(
        canonical_json(
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "kind": event.kind.value,
                "target": event.target,
                "actor": event.actor,
                "occurred_at": event.occurred_at,
                "payload": event.payload,
            }
        )
    )


def withdrawable(event: RecordEvent) -> bool:
    """Whether a person may withdraw this event one at a time: an answer about a word.

    A note decides nothing and a withdrawal is not an answer; any other target is not a word's.
    """
    prefix, _, rest = event.target.partition(":")
    return (
        bool(rest)
        and prefix in WITHDRAWABLE_PREFIXES
        and event.kind not in (RecordEventKind.NOTE, RecordEventKind.WITHDRAW)
    )


def _check_answer_withdrawal(project: Path, detail: Mapping[str, Any]) -> None:
    """Refuse a withdrawal of one answer unless it names, as the page read it, one answer about a
    word that is in the Record and not yet withdrawn.

    The check is made here, in the one writer, so no caller can build a withdrawal that does not
    hold to it. The answer is named by its sequence and its digest (:func:`answer_digest`). The
    withdrawal names that answer's event and no other. The answer is about a word
    (:data:`WITHDRAWABLE_PREFIXES`), is an answer (not a note, not a withdrawal), is not already
    withdrawn, and is still the answer in force on its word.

    :func:`append_event` calls this function with the Record's lock held, so what it reads is what
    the append will follow.
    """
    named = detail.get("answer")
    sequence = named.get("sequence") if isinstance(named, dict) else None
    digest = str(named.get("digest") or "") if isinstance(named, dict) else ""
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not digest:
        message = "a withdrawal of one answer names it by its sequence and its digest"
        raise WithdrawRefusedError(message)
    everything = _events_from(sorted(record_directory(project).glob("*.parquet")))
    found = next((event for event in everything if event.sequence == sequence), None)
    if found is None or answer_digest(found) != digest:
        message = (
            "the Record holds no answer with that sequence and digest: the page is out of date, "
            "so look at it again before withdrawing"
        )
        raise WithdrawRefusedError(message, stale=True)
    if list(detail.get("withdrawn") or ()) != [found.event_id]:
        message = "a withdrawal of one answer names that answer's event and no other"
        raise WithdrawRefusedError(message)
    if found.kind in (RecordEventKind.NOTE, RecordEventKind.WITHDRAW):
        message = "that is not an answer: a note decides nothing, and a withdrawal is not one"
        raise WithdrawRefusedError(message)
    if not withdrawable(found):
        message = (
            "only an answer about a word is withdrawn one at a time (what it means, how a "
            "spelling reads, spellings called unrelated, a word or spelling turned off); "
            "answer again to change any other"
        )
        raise WithdrawRefusedError(message)
    if found.event_id in withdrawn_ids(everything):
        message = "that answer is already withdrawn"
        raise WithdrawRefusedError(message)
    # Only the answer in force on its word can be withdrawn. A page that read an answer that a later
    # one has replaced is out of date. Withdrawing the replaced answer would change no reading,
    # while the badge counted it as taken back.
    standing = latest_by_target(in_force(everything)).get(found.target)
    if standing is None or standing.event_id != found.event_id:
        message = (
            "a later answer has replaced that one, so withdrawing it would change nothing: the "
            "page is out of date, so look at it again before withdrawing"
        )
        raise WithdrawRefusedError(message, stale=True)


def answer_withdrawals(project: Path) -> tuple[tuple[RecordEvent, RecordEvent], ...]:
    """Each withdrawal of one answer in force, with the answer it withdrew, in the order made.

    A withdrawal that a later withdrawal has itself withdrawn brings its answer back and is not
    listed. Read at the Record as it stands (or as a run's watermark bounds it).
    """
    everything = read_events(project, withdrawn=True)
    gone = withdrawn_ids(everything)
    by_id = {event.event_id: event for event in everything}
    found: list[tuple[RecordEvent, RecordEvent]] = []
    for event in everything:
        if event.kind is not RecordEventKind.WITHDRAW or event.event_id in gone:
            continue
        try:
            detail = json.loads(event.payload or "{}")
        except ValueError:
            continue
        if not isinstance(detail, dict) or "answer" not in detail:
            continue
        found.extend(
            (event, by_id[str(identifier)])
            for identifier in detail.get("withdrawn", ())
            if str(identifier) in by_id
        )
    return tuple(found)


def _check_withdrawal(project: Path, payload: Mapping[str, Any] | None) -> None:
    """Refuse a withdrawal that does not say what it withdraws, why, and what was previewed.

    There are two ways in, and both are a person's. One is going back to an earlier run (below). The
    other is withdrawing one answer about a word (:func:`_check_answer_withdrawal`).

    The generic writer is not a back door. Withdrawing is not something the engine does on its own.
    A withdrawal names the earlier events it withdraws, the run a person restored to, and the digest
    of the preview they confirmed. Every event it names must already be in the Record.
    ``workspace/restore.restore`` is the one caller that builds a restore's withdrawal, and a test
    holds that.
    """
    detail = dict(payload or {})
    if "answer" in detail:
        return  # checked by the writer with the lock held (:func:`_check_answer_withdrawal`)
    withdrawn = detail.get("withdrawn")
    if not isinstance(withdrawn, list | tuple) or not withdrawn:
        message = "a withdrawal names the earlier events it withdraws"
        raise AppendError(message)
    for name in ("restored_to", "previewed"):
        if not str(detail.get(name) or "").strip():
            message = f"a withdrawal states {name}: it is written only by a person going back"
            raise AppendError(message)
    # The whole Record, never a run's bound (``read_at``): a writer checks against what is there.
    known = {
        event.event_id
        for event in _events_from(sorted(record_directory(project).glob("*.parquet")))
    }
    unknown = sorted(str(identifier) for identifier in withdrawn if str(identifier) not in known)
    if unknown:
        message = f"a withdrawal names events the Record does not hold: {', '.join(unknown[:5])}"
        raise AppendError(message)


def withdrawn_ids(events: Sequence[RecordEvent]) -> frozenset[str]:
    """Every event a withdrawal in force has withdrawn (schema 1.22).

    Read newest first: a withdrawal is in force unless a later withdrawal in force names it, and
    only a later event can name an earlier one (a restore withdraws what came after the run it
    returns to). So one pass from the newest event backwards settles every chain of restores, and
    withdrawing a withdrawal brings back exactly what it withdrew.
    """
    gone: set[str] = set()
    for event in sorted(events, key=lambda event: (event.sequence, event.event_id), reverse=True):
        if event.kind is not RecordEventKind.WITHDRAW or event.event_id in gone:
            continue
        try:
            detail = json.loads(event.payload or "{}")
        except ValueError:
            continue
        gone.update(str(identifier) for identifier in detail.get("withdrawn", ()))
    return frozenset(gone)


def in_force(events: Sequence[RecordEvent]) -> tuple[RecordEvent, ...]:
    """The events replay honours: none withdrawn, and no withdrawal itself, in order."""
    gone = withdrawn_ids(events)
    return tuple(
        event
        for event in sorted(events, key=lambda event: (event.sequence, event.event_id))
        if event.event_id not in gone and event.kind is not RecordEventKind.WITHDRAW
    )


def moved_entries(project: Path) -> dict[str, str]:
    """Every entry a file moved away from, mapped to the entry it is in the current run (1.22).

    The function reads in run order, from the runs up to the current one. A move is written once, by
    the walk that first found the file at its new path (``moved_from`` on the new entry). The
    project's history of moves is those rows, applied oldest run first. Each move carries every
    decision already following the file along with it, so a file moved away and back again is
    followed back. A move that a dive refuted by its digest is taken back, so nothing follows the
    file to that entry. A decision resolves only to an entry that the current run holds. One whose
    file has since gone stays where it was written. A run later than the current one (after a
    restore to an earlier run) is not read. The result is held per current run, since a run is
    immutable.
    """
    from ingence.storage.commit import read_pointer
    from ingence.storage.layout import RUNS_DIR

    pointer = read_pointer(project)
    if pointer is None:
        return {}
    key = (project.resolve(), pointer.run_id)
    with _MOVED_LOCK:
        known = _MOVED.get(key)
    if known is not None:
        return known
    runs = (
        sorted(
            path.name
            for path in (project / RUNS_DIR).iterdir()
            if path.is_dir() and path.name <= pointer.run_id
        )
        if (project / RUNS_DIR).is_dir()
        else []
    )
    where: dict[str, str] = {}
    for run in runs:
        found = [
            move
            for fragment in sorted((project / RUNS_DIR / run / "entries").glob("*.parquet"))
            for move in _moves_in(fragment)
            if move[1] and move[0] != move[1]
        ]
        # A move that a dive refuted is taken back. Whatever followed the file to the entry that the
        # walk took for it stays where it was written.
        refuted = {entry for entry, _, how in found if how == _REFUTED}
        if refuted:
            where = {original: now for original, now in where.items() if now not in refuted}
        moves = {source: entry for entry, source, how in found if how != _REFUTED}
        if not moves:
            continue
        for original, now in list(where.items()):
            if now in moves:
                where[original] = moves[now]
        for source, entry in moves.items():
            where.setdefault(source, entry)
    live = _live_entries(project, pointer.run_id) if where else frozenset()
    resolved = {
        original: now
        for original, now in where.items()
        if now != original and now in live and original not in live
    }
    with _MOVED_LOCK:
        _MOVED[key] = resolved
        while len(_MOVED) > HELD_PROJECTS:
            _MOVED.pop(next(iter(_MOVED)))
    return resolved


def _live_entries(project: Path, run_id: str) -> frozenset[str]:
    """The entries a run holds that are not tombstones."""
    from ingence.storage.layout import RUNS_DIR

    found: set[str] = set()
    for fragment in sorted((project / RUNS_DIR / run_id / "entries").glob("*.parquet")):
        try:
            table = pq.ParquetFile(fragment).read(columns=["entry_id", "is_deleted"])
        except (OSError, pa.ArrowException):
            continue
        found.update(
            str(entry)
            for entry, deleted in zip(
                table.column("entry_id").to_pylist(),
                table.column("is_deleted").to_pylist(),
                strict=True,
            )
            if not deleted
        )
    return frozenset(found)


#: The ``moved_by`` of a move that a dive's digest refuted.
_REFUTED = MovedBy.DIGEST_DIFFERS.value


def _moves_in(fragment: Path) -> list[tuple[str, str, str | None]]:
    """The (entry, moved_from, moved_by) rows one entries fragment holds, skipping row groups with
    none."""
    try:
        parquet = pq.ParquetFile(fragment)
    except (OSError, pa.ArrowException):
        return []
    names = parquet.schema_arrow.names
    if "moved_from" not in names:
        return []
    column = names.index("moved_from")
    columns = ["entry_id", "moved_from", *(["moved_by"] if "moved_by" in names else [])]
    found: list[tuple[str, str, str | None]] = []
    for group in range(parquet.num_row_groups):
        described = parquet.metadata.row_group(group)
        statistics = described.column(column).statistics
        if statistics is not None and statistics.null_count == described.num_rows:
            continue
        rows = parquet.read_row_group(group, columns=columns)
        how = (
            rows.column("moved_by").to_pylist() if "moved_by" in columns else [None] * rows.num_rows
        )
        found.extend(
            (str(entry), str(source), None if said is None else str(said))
            for entry, source, said in zip(
                rows.column("entry_id").to_pylist(),
                rows.column("moved_from").to_pylist(),
                how,
                strict=True,
            )
            if source
        )
    return found


def at_new_places(
    events: Sequence[RecordEvent], moved: Mapping[str, str]
) -> tuple[RecordEvent, ...]:
    """Each decision about a file that moved, read at the file's new place (schema 1.22).

    This is a reading and never an event, because the engine writes no decision on anybody's behalf.
    A target that names an entry that moved, as any one of its ``:``-separated parts, is read with
    that part replaced by the entry the file is now. The event says where it was written
    (``payload``'s ``written_at``). The Record on disk is untouched.
    """
    if not moved:
        return tuple(events)
    read: list[RecordEvent] = []
    for event in events:
        parts = event.target.split(":")
        placed = [moved.get(part, part) for part in parts]
        if placed == parts:
            read.append(event)
            continue
        detail = json.loads(event.payload or "{}")
        detail["written_at"] = event.target
        read.append(
            event.model_copy(update={"target": ":".join(placed), "payload": canonical_json(detail)})
        )
    return tuple(read)


_MOVED: dict[tuple[Path, str], dict[str, str]] = {}
_MOVED_LOCK = threading.Lock()


#: The watermark each project's Record is read at inside :func:`read_at`, by resolved path.
_READ_AT: ContextVar[tuple[tuple[Path, int], ...]] = ContextVar(
    "ingence_record_read_at", default=()
)


@contextmanager
def read_at(project: Path, watermark: int | None) -> Iterator[None]:
    """Every read of a project's Record inside reads it as it stood at a run's watermark.

    A run is reproducible from Index, Rulebook, Dictionary and Record, and its run record states the
    watermark at which it read the Record. A run enters this context once, right after capturing its
    watermark. Every reader it calls reads through :func:`read_events`, which honours the context.
    The readers are inference's, the Dictionary's, the binder's and the dive's. A decision written
    after the watermark, while the run is under way, is not read. A withdrawal after the watermark
    does not reach back. The next run, at its own watermark, reads both.

    The watermark is a context variable and not a parameter threaded through every reader, as with
    ``jobs.provenance.job_scope``. It is held per project, by its resolved path. Another project's
    Record is not bounded by this one, and a bound inside another project never reaches past it. A
    watermark captured inside a bound is the bound (``jobs.provenance.record_watermark``), so a run
    started inside one states what it reads. ``None`` bounds nothing: a run that reads no Record
    states no watermark.

    What is bounded, exactly:

    - Inference's pass, whole, from its watermark's capture through its run record. A reader added
      anywhere in it is bounded without being told.
    - The census's block that reads the vocabulary and the divided spellings, and the dive's three
      blocks that read the worth-reading answers, the vocabulary and the program answers. A reader
      added elsewhere in either is not bounded.
    - A page that reads what a run read (the Graph page's prefix mappings).

    What is not bounded:

    - A thread or a process that a run starts. Each starts with an empty context, and none reads the
      Record today.
    - The catalog's ``record_events`` view, which SQL reads over every fragment. No run queries it.
    - :func:`append_event`'s own check of what a withdrawal names, which reads the fragments whole.
    - Every other writer's read (an import, a restore, the answer route). It is unbounded because no
      run calls it.
    """
    if watermark is None:
        yield
        return
    key = project.resolve()
    bounds = dict(_READ_AT.get())
    bounds[key] = min(watermark, bounds.get(key, watermark))
    token = _READ_AT.set(tuple(bounds.items()))
    try:
        yield
    finally:
        _READ_AT.reset(token)


def read_bound(project: Path) -> int | None:
    """The watermark a read of this project's Record is bounded at here, or None."""
    bounds = _READ_AT.get()
    return dict(bounds).get(project.resolve()) if bounds else None


def _within_bound(project: Path, events: tuple[RecordEvent, ...]) -> tuple[RecordEvent, ...]:
    """The events a read here may return: those at or below the bound, or all of them."""
    bound = read_bound(project)
    return events if bound is None else tuple(event for event in events if event.sequence <= bound)


def read_events(
    project: Path, *, held: bool = True, withdrawn: bool = False, moved: bool = True
) -> tuple[RecordEvent, ...]:
    """Return every decision in force in the Record, in the order they were made.

    Withdrawn events and the withdrawals themselves are left out unless ``withdrawn=True`` (schema
    1.22). A person who goes back to an earlier run withdraws the decisions made since. Those
    decisions stay in the Record, in every export and on the History page (which reads with
    ``withdrawn=True``). Every replay skips them, as reading the Record means everywhere else. The
    default is the safe direction: a reader that forgets the flag replays what is in force.

    A decision about a file that moved is read at its new place unless ``moved=False`` (schema 1.22,
    :func:`at_new_places`). The Record on disk is untouched.

    The function reads the whole Record, because the Record is a log that a person reads and an
    inference replays, and both want all of it. It is the one table with no bound on a read for that
    reason. A decision that fell outside a page would silently stop being replayed.

    A process holds its reads between calls and checks them against the fragments on every read.
    Opening one Parquet file per decision is nearly all of what a read costs. Measured on a person's
    whole review, it takes seconds, where the rows themselves take milliseconds. The Dashboard read
    the Record once per page. So the events that a process has read are kept, with each fragment's
    name, size, modification time and file number as it was read. Every later read lists the
    directory and compares. A fragment that is new is read and added. Any fragment that changed, was
    replaced or is gone discards what was held, and the whole Record is read again. Nothing is
    written anywhere, the fragments stay the only copy, and a held read returns exactly what reading
    the fragments returns (a test holds the two equal). ``held=False`` reads the fragments and
    nothing else.

    Inside :func:`read_at`, the function reads the Record as it stood at that watermark. Nothing
    after the watermark is read, so a withdrawal after it does not reach back.
    """
    directory = record_directory(project)
    if not directory.is_dir():
        return ()
    if not held:
        everything = _events_from(sorted(directory.glob("*.parquet")))
    else:
        everything = _HELD.read(directory)
    # Inside a run, the Record as it stood at the run's watermark (:func:`read_at`).
    everything = _within_bound(project, everything)
    chosen = everything if withdrawn else in_force(everything)
    return at_new_places(chosen, moved_entries(project)) if moved else chosen


def _events_from(paths: Sequence[Path]) -> tuple[RecordEvent, ...]:
    """The events in these fragments, in the order they were made."""
    if not paths:
        return ()
    spec = table_for(RecordEvent)
    table = pa.concat_tables([pq.read_table(path, schema=spec.schema) for path in paths])
    # A decision that a newer engine wrote is refused before it is validated (schema 1.22), so the
    # words say which decision and which version, and do not name an unknown kind.
    for sequence, stamped in zip(
        table.column("sequence").to_pylist(),
        table.column("schema_version").to_pylist(),
        strict=True,
    ):
        refuse_newer(str(stamped), what=f"the Record at {paths[0].parent} (decision {sequence})")
    events = [record_from_row(row, RecordEvent) for row in table.to_pylist()]
    return tuple(sorted(events, key=lambda event: (event.sequence, event.event_id)))


#: A fragment as a held read last saw it: size, modification time in nanoseconds, and the
#: file's number on its volume. A fragment is never rewritten, so any of the three changing
#: says the Record is not the one that was read, and what was held is discarded.
type _Seen = tuple[int, int, int]

#: How many projects' Records one process holds at once. A Dashboard serves one project and a
#: command reads one; the bound is for a process that opens many, such as the test suite.
HELD_PROJECTS = 8


class _HeldRecords:
    """The Records this process has read, each with the fragments it was read from."""

    def __init__(self, capacity: int = HELD_PROJECTS) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._held: dict[Path, tuple[dict[str, _Seen], tuple[RecordEvent, ...]]] = {}

    def read(self, directory: Path) -> tuple[RecordEvent, ...]:
        key = directory.resolve()
        present = _fragments_seen(directory)
        with self._lock:
            was = self._held.get(key)
        if was is not None and all(present.get(name) == seen for name, seen in was[0].items()):
            added = sorted(name for name in present if name not in was[0])
            events = was[1]
            if added:
                events = tuple(
                    sorted(
                        (*events, *_events_from([directory / name for name in added])),
                        key=lambda event: (event.sequence, event.event_id),
                    )
                )
        else:
            events = _events_from([directory / name for name in sorted(present)])
        with self._lock:
            self._held.pop(key, None)
            self._held[key] = (present, events)
            while len(self._held) > self._capacity:
                self._held.pop(next(iter(self._held)))
        return events

    def forget(self) -> None:
        """Drop every held Record, so the next read of each reads its fragments."""
        with self._lock:
            self._held.clear()


def _fragments_seen(directory: Path) -> dict[str, _Seen]:
    """Every fragment in the Record directory, by name, as it stands now."""
    found: dict[str, _Seen] = {}
    with os.scandir(directory) as listing:
        for item in listing:
            if not item.name.endswith(".parquet") or not item.is_file():
                continue
            stat = item.stat()
            found[item.name] = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    return found


_HELD = _HeldRecords()


def latest_by_target(events: Sequence[RecordEvent]) -> dict[str, RecordEvent]:
    """Return the last decision made about each target.

    Replay is in order and later decisions win, so this is what "what does the Record
    currently say about this thing" means. Every earlier decision stays in the log and
    stays readable, which is the whole point of the log being append-only: a person can
    see that they changed their mind, and when.
    """
    latest: dict[str, RecordEvent] = {}
    for event in sorted(events, key=lambda event: (event.sequence, event.event_id)):
        # A note decides nothing, so it is never what the Record currently says about a target. A
        # note written after a verdict leaves the verdict in force.
        if event.kind in (RecordEventKind.NOTE, RecordEventKind.WITHDRAW):
            continue
        latest[event.target] = event
    return latest


def notes_by_target(events: Sequence[RecordEvent]) -> dict[str, list[RecordEvent]]:
    """Return every note written about each target, in the order they were written.

    A note is kept beside the decisions rather than among them (``RecordEventKind.NOTE``), so a
    page showing a target shows what was said about it as well as what was decided.
    """
    found: dict[str, list[RecordEvent]] = {}
    for event in sorted(events, key=lambda event: (event.sequence, event.event_id)):
        if event.kind is RecordEventKind.NOTE:
            found.setdefault(event.target, []).append(event)
    return found
