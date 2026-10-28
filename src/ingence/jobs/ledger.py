"""The job ledger: one SQLite file per project, every job and every change of its state.

A row of the table ``jobs`` holds the job's intent (``intent_json``: the spellings a partial
rebind reads again, and the whole-project steps, ``rebind`` and ``inference``, that a re-run of
committed changes asked for), its state (:class:`~ingence.jobs.JobState`), its phase and progress
(the runs it published on the way, seconds per phase), and the worker's heartbeat. It also holds
the error that ended the job, how many times it was retried after an interruption, how many
attempts it took, the run it produced, and the Record sequence it was asked for (the highest in
the Record when it was queued or last added to). It holds the run current when it was queued
(``base_run``), the worker's process identifier and log, and when the job was created, started and
ended. Every change of state is also a row of ``job_events``, with its reason in words, so an
interruption is never overwritten by the retry that follows it.

Writes use write-ahead-log mode, so the server reads while a worker writes. Every write is one
``BEGIN IMMEDIATE`` transaction, so two writers queue and do not interleave. Each call opens its
own connection and closes it at once, so no handle outlives its use (Windows will not remove a
file somebody holds). ``synchronous=FULL`` puts a state change on disk when its call returns.
Every time is ISO 8601 in UTC with its zone.

The pin (table ``meta``, key ``pinned_run``) is the run that every read route serves while any
job is queued or running. It is the run current when the first of them was queued, and after each
job that publishes while more wait, the run that job produced. The ledger clears the pin when
nothing is queued or running. The pin is kept in the ledger and not in the server's memory, so
that a server started after a crash keeps serving the run its predecessor pinned. The latest
published run therefore stays available throughout.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.jobs import JOBS_DIR, LEDGER_FILE, JobState
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

__all__ = [
    "FIRST_RUN_STEPS",
    "LEDGER_VERSION",
    "NOT_ACCIDENTS",
    "STOPPED",
    "JobEvent",
    "JobLedger",
    "JobRow",
    "new_job_id",
    "now",
]

#: The ledger's own layout version, apart from the record schema: it is operational state.
#: Version 2 (schema 1.24): a job may end ``cancelled``, and ``meta`` may hold a person's stop
#: requests (``stop:<job_id>``). The tables are the same, so a ledger at 1 is upgraded by saying
#: so, the first time this engine writes to it. Nothing in it is rewritten.
LEDGER_VERSION = "2"

#: Seconds a writer waits for another writer's transaction before giving up.
BUSY_TIMEOUT_SECONDS = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    intent_json TEXT NOT NULL,
    state TEXT NOT NULL,
    phase TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    heartbeat_at TEXT,
    error TEXT,
    retries INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    record_sequence INTEGER,
    base_run TEXT,
    bound_run TEXT,
    produced_run TEXT,
    worker_pid INTEGER,
    log_path TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_state ON jobs (state, ordinal);
CREATE TABLE IF NOT EXISTS job_events (
    event INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs (job_id),
    at TEXT NOT NULL,
    state TEXT NOT NULL,
    phase TEXT,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_events_by_job ON job_events (job_id, event);
"""

_BUSY = (JobState.QUEUED.value, JobState.RUNNING.value)
#: The states a job ends in: its end, once written, stands.
FINAL_STATES = (JobState.DONE, JobState.FAILED, JobState.CANCELLED)

#: A worker ends in one of five ways, as the supervisor tells the ledger: ``killed`` (a signal
#: from outside: a person, the machine's memory, the Dashboard), ``crashed`` (the process
#: faulted: a segmentation fault, an abort, Windows' exception codes), ``silent`` (stopped for
#: not being heard from), ``exited`` (it ended without finishing its job) and ``gone`` (found
#: dead on taking over, how unknown).
#: The causes that repeat on the same input, which a second time in one phase ends the job.
NOT_ACCIDENTS = frozenset({"crashed", "silent"})
#: A person's Stop is a cause of its own. It ends the job ``cancelled`` and never queues it again
#: (``interrupt``), whether the worker stopped itself on the request or the supervisor stopped it
#: after the phase's grace.
STOPPED = "stopped"
_CAUSE_WORDS = {
    "crashed": "its worker crashed",
    "silent": "its worker fell silent and was stopped",
    STOPPED: "a person stopped it",
}
#: Where a stop request is kept (table ``meta``): ``stop:<job_id>``, holding ``keep`` and when.
_STOP_KEY = "stop:"

#: The progress keys that name a run an attempt made or was about to make. The ledger forgets them
#: when the job grows, so a retry never takes up a run that read less than the job now asks.
_PLANNED = (
    "binding_run",
    "bound_run",
    "inferring_run",
    "inferred_run",
    "resumed_after",
    # How its last attempt ended. A crash on what the job asked before is not a crash on what it
    # asks now.
    "last_interruption",
)


def now() -> str:
    """This moment, ISO 8601 in UTC with its zone, to the millisecond."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_job_id() -> str:
    """A job's identifier: when it was made and four random bytes, eight hexadecimal characters."""
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"job_{stamp}_{os.urandom(4).hex()}"


@dataclass(frozen=True, slots=True)
class JobEvent:
    """One change of a job's state, with its reason."""

    job_id: str
    at: str
    state: JobState
    phase: str | None
    message: str


@dataclass(frozen=True, slots=True)
class JobRow:
    """One job as the ledger holds it."""

    ordinal: int
    job_id: str
    kind: str
    spellings: tuple[str, ...]
    steps: tuple[str, ...]
    state: JobState
    phase: str | None
    progress: Mapping[str, Any] = field(default_factory=dict)
    heartbeat_at: str | None = None
    error: str | None = None
    retries: int = 0
    attempts: int = 0
    record_sequence: int | None = None
    base_run: str | None = None
    bound_run: str | None = None
    produced_run: str | None = None
    worker_pid: int | None = None
    log_path: str | None = None
    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None

    @property
    def operations(self) -> tuple[str, ...]:
        """What the job does, in order: a whole or partial rebind, then inference."""
        done: list[str] = []
        # A first run is a census of every root, the dive where asked, then inference. A person
        # starts it with "Read it now" on the landing page.
        if "census" in self.steps:
            done.append("census")
            if "dive" in self.steps:
                done.append("dive")
        elif "rebind" in self.steps:
            done.append("whole_rebind")
        elif self.spellings:
            done.append("partial_rebind")
        if self.spellings or "inference" in self.steps:
            done.append("inference")
        return tuple(done)

    @property
    def busy(self) -> bool:
        return self.state in (JobState.QUEUED, JobState.RUNNING)


#: The steps of a first run. It is the only job that walks a root, which is why only a person's
#: click on the landing page asks for one (``DefinitionApplier.submit_first_run``).
FIRST_RUN_STEPS = frozenset({"census", "dive", "inference"})


def _kind(spellings: Iterable[str], steps: Iterable[str]) -> str:
    """``first_run`` where a census was asked for, ``rerun`` where committed changes asked for
    whole-project steps, else ``definition``."""
    del spellings
    asked = tuple(steps)
    if "census" in asked:
        return "first_run"
    return "rerun" if asked else "definition"


def _row(found: sqlite3.Row) -> JobRow:
    intent = json.loads(found["intent_json"])
    return JobRow(
        ordinal=int(found["ordinal"]),
        job_id=str(found["job_id"]),
        kind=str(found["kind"]),
        spellings=tuple(intent.get("spellings") or ()),
        steps=tuple(intent.get("steps") or ()),
        state=JobState(found["state"]),
        phase=found["phase"],
        progress=json.loads(found["progress_json"] or "{}"),
        heartbeat_at=found["heartbeat_at"],
        error=found["error"],
        retries=int(found["retries"]),
        attempts=int(found["attempts"]),
        record_sequence=found["record_sequence"],
        base_run=found["base_run"],
        bound_run=found["bound_run"],
        produced_run=found["produced_run"],
        worker_pid=found["worker_pid"],
        log_path=found["log_path"],
        created_at=str(found["created_at"]),
        started_at=found["started_at"],
        ended_at=found["ended_at"],
    )


def _upgrade(connection: sqlite3.Connection) -> None:
    """Bring an earlier ledger's version up to :data:`LEDGER_VERSION`, inside a write.

    The upgrade from 1 to 2 (schema 1.24) changes no table: the ``state`` column is text, and a
    stop request is a ``meta`` row. Writing the version says the ledger may now hold what an
    engine before 0.4.0 cannot read, and a later layout change reads it to know what to do.
    """
    connection.execute(
        "INSERT INTO meta (key, value) VALUES ('ledger_version', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value "
        "WHERE CAST(meta.value AS INTEGER) < CAST(excluded.value AS INTEGER)",
        (LEDGER_VERSION,),
    )


def _describe(spellings: Iterable[str], steps: Iterable[str]) -> str:
    words = sorted(spellings)
    parts = []
    if "census" in steps:
        parts.append("read every name and folder in the roots")
        if "dive" in steps:
            parts.append("open files for their headers and clocks")
    elif "rebind" in steps:
        parts.append("read every name again")
    elif words:
        parts.append("read again the names holding " + ", ".join(f'"{word}"' for word in words))
    if words or "inference" in steps:
        parts.append("apply the rules")
    return ", then ".join(parts) or "nothing"


class JobLedger:
    """The ledger of one project's jobs. Nothing is written until the first job is."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.path = project / JOBS_DIR / LEDGER_FILE
        #: Whether this ledger's version was brought up to :data:`LEDGER_VERSION` (once per
        #: instance, inside its first write).
        self._upgraded = False

    # -- connections ---------------------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def _open(self, path: Path | None = None) -> sqlite3.Connection:
        connection = sqlite3.connect(
            path or self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
        # A file that is not a database fails here, at the first statement: the connection is
        # closed before the error goes on, or it is left for the collector (Python 3.13 warns).
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def _create(self) -> None:
        """Make the ledger whole beside itself and move it into place, under a lock.

        ``sqlite3.connect`` makes an empty file before any table exists, and a reader opening
        that file would find no ``jobs`` table. So the ledger is built under another name and
        renamed, and a reader sees no file or a whole one.
        """
        from ingence.jobs.locks import FileLock

        self.path.parent.mkdir(parents=True, exist_ok=True)
        creating = FileLock(self.path.with_name(self.path.name + ".create.lock"))
        if not creating.acquire(timeout=BUSY_TIMEOUT_SECONDS):
            message = f"could not create the job ledger at {self.path}: another process holds it"
            raise TimeoutError(message)
        try:
            if self.path.is_file():
                return
            # The project's own jobs folder, nothing on the way to it a link, before anything in
            # it is removed or replaced.
            staged = owned_path(
                self.project, self.path.with_name(f"{self.path.name}.{os.getpid()}.incoming")
            )
            staged.unlink(missing_ok=True)
            connection = self._open(staged)
            try:
                # Outside a transaction, as SQLite requires. Persistent in the file once set.
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("BEGIN IMMEDIATE")
                # Statement by statement: ``executescript`` commits first, outside it.
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES ('ledger_version', ?)",
                    (LEDGER_VERSION,),
                )
                connection.execute("COMMIT")
            finally:
                connection.close()
            os.replace(staged, owned_path(self.project, self.path))
        finally:
            creating.release()

    @contextmanager
    def _writing(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, taken at once so two writers queue rather than interleave."""
        if not self.exists():
            self._create()
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not self._upgraded:
                    _upgrade(connection)
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            self._upgraded = True
        finally:
            connection.close()

    @contextmanager
    def _reading(self) -> Iterator[sqlite3.Connection | None]:
        """A read, or None where no ledger was ever written (reading creates nothing)."""
        if not self.exists():
            yield None
            return
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    def version(self) -> str | None:
        """The ledger's layout version as written in it, or None where no ledger was written."""
        with self._reading() as connection:
            if connection is None:
                return None
            found = connection.execute(
                "SELECT value FROM meta WHERE key = 'ledger_version'"
            ).fetchone()
        return None if found is None else str(found[0])

    def journal_mode(self) -> str:
        with self._reading() as connection:
            if connection is None:
                return ""
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0])

    # -- reads -----------------------------------------------------------------------------------

    def get(self, job_id: str) -> JobRow | None:
        with self._reading() as connection:
            if connection is None:
                return None
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return None if found is None else _row(found)

    def recent(self, limit: int = 20) -> list[JobRow]:
        """The most recent jobs, newest first."""
        with self._reading() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY ordinal DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [_row(found) for found in rows]

    def in_state(self, *states: JobState) -> list[JobRow]:
        """Every job in one of these states, oldest first."""
        with self._reading() as connection:
            if connection is None:
                return []
            marks = ", ".join("?" for _ in states)
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY ordinal",  # noqa: S608
                tuple(state.value for state in states),
            ).fetchall()
        return [_row(found) for found in rows]

    def summaries(self) -> list[tuple[int, str, JobState]]:
        """Every job's order, identifier and state, oldest first, and nothing else of it."""
        with self._reading() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT ordinal, job_id, state FROM jobs ORDER BY ordinal"
            ).fetchall()
        return [(int(found[0]), str(found[1]), JobState(found[2])) for found in rows]

    def events(self, job_id: str) -> list[JobEvent]:
        """A job's history, oldest first."""
        with self._reading() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY event", (job_id,)
            ).fetchall()
        return [
            JobEvent(
                job_id=str(found["job_id"]),
                at=str(found["at"]),
                state=JobState(found["state"]),
                phase=found["phase"],
                message=str(found["message"]),
            )
            for found in rows
        ]

    def busy(self) -> bool:
        """Whether any job is queued or running."""
        with self._reading() as connection:
            if connection is None:
                return False
            return _busy(connection)

    def pinned_run(self) -> str | None:
        """The run that reads are pinned to while a job is queued or running, else None."""
        with self._reading() as connection:
            if connection is None:
                return None
            return _pin(connection) if _busy(connection) else None

    def reading(self) -> tuple[str | None, bool]:
        """The pin and whether a job is busy, read in one transaction so the two agree."""
        with self._reading() as connection:
            if connection is None:
                return None, False
            connection.execute("BEGIN")
            try:
                busy = _busy(connection)
                return (_pin(connection) if busy else None), busy
            finally:
                connection.execute("COMMIT")

    def held(self) -> set[str]:
        """The queued jobs a close stopped that wait for a person's Re-run.

        The ledger keeps the hold, where every server reads it. A job is held while the latest of
        its events is the hold. A person's Re-run on any Dashboard writes a later event (joining
        it, or releasing it), and the hold is gone for all of them.
        """
        with self._reading() as connection:
            if connection is None:
                return set()
            rows = connection.execute(
                "SELECT j.job_id, (SELECT e.message FROM job_events e WHERE e.job_id = j.job_id "
                "ORDER BY e.event DESC LIMIT 1) FROM jobs j WHERE j.state = ?",
                (JobState.QUEUED.value,),
            ).fetchall()
        return {str(job_id) for job_id, message in rows if str(message or "").startswith(HELD)}

    def hold(self, job_ids: Iterable[str]) -> None:
        """Hold these queued jobs for a person's Re-run: one event each, the hold's words."""
        at = now()
        with self._writing() as connection:
            for job_id in sorted(set(job_ids)):
                _event(connection, job_id, JobState.QUEUED, None, HELD, at)

    def release_held(self) -> list[str]:
        """A person's Re-run: every held job may run now. Returns the jobs released."""
        at = now()
        released = sorted(self.held())
        if released:
            with self._writing() as connection:
                for job_id in released:
                    _event(connection, job_id, JobState.QUEUED, None, RELEASED, at)
        return released

    def stop_requested(self, job_id: str) -> dict[str, Any] | None:
        """A person's stop request on this job (``keep`` and when), or None where none waits.

        The worker's heartbeat reads it each beat (one statement, so a starved thread never holds
        the write lock), and the supervisor reads it while it waits on the worker. A request that
        the worker has honoured by keeping what was read (:meth:`keep_what_was_read`) waits no
        longer: the job goes on to apply the rules, and nothing is to be stopped. A later Stop is
        a request of its own.
        """
        with self._reading() as connection:
            if connection is None:
                return None
            found = connection.execute(
                "SELECT value FROM meta WHERE key = ?", (f"{_STOP_KEY}{job_id}",)
            ).fetchone()
        if found is None or found[0] is None:
            return None
        try:
            held = json.loads(str(found[0]))
        except ValueError:
            return None
        if not isinstance(held, dict) or held.get("kept_at"):
            return None
        return held

    # -- writes ----------------------------------------------------------------------------------

    def request_stop(self, job_id: str, *, keep: bool) -> JobRow | None:
        """A person's Stop: write the request the worker reads, and end a job that has not
        started ``cancelled`` at once. None where there is no such job. A job that has ended is
        returned as it is, its end standing.

        ``keep`` asks to keep what the content step has read so far. It is written for the
        worker to honour where the pass can, and said in the job's history either way.
        """
        at = now()
        with self._writing() as connection:
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if found is None:
                return None
            held = _row(found)
            if held.state in FINAL_STATES:
                return held
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (f"{_STOP_KEY}{job_id}", json.dumps({"keep": keep, "at": at}, sort_keys=True)),
            )
            said = "a person asked to stop it" + (
                ", keeping what was read so far" if keep else ", keeping nothing of this run"
            )
            if held.state is JobState.QUEUED:
                # Nothing runs it: its end is written here.
                connection.execute(
                    "UPDATE jobs SET state = ?, error = ?, ended_at = ? WHERE job_id = ?",
                    (JobState.CANCELLED.value, said, at, job_id),
                )
                _event(connection, job_id, JobState.CANCELLED, held.phase, f"cancelled: {said}", at)
                _settle_pin(connection, None)
            else:
                _event(connection, job_id, JobState.RUNNING, held.phase, said, at)
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row(found)

    def keep_what_was_read(self, job_id: str, *, attempt: int, asked_at: object) -> bool:
        """Honour a person's Stop by keeping what the content step has read. The method marks the
        request answered, so neither the worker nor the supervisor reads it again, and says so in
        the job's history. False where the job is no longer this attempt's, or the request was
        replaced (a later Stop, read at the next beat) or is gone.
        """
        at = now()
        key = f"{_STOP_KEY}{job_id}"
        with self._writing() as connection:
            found = connection.execute(
                "SELECT state, attempts, phase FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if (
                found is None
                or found["state"] != JobState.RUNNING.value
                or int(found["attempts"]) != attempt
            ):
                return False
            value = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            try:
                held = json.loads(str(value[0])) if value is not None else None
            except ValueError:
                held = None
            if not isinstance(held, dict) or held.get("at") != asked_at or held.get("kept_at"):
                return False
            connection.execute(
                "UPDATE meta SET value = ? WHERE key = ?",
                (json.dumps({**held, "kept_at": at}, sort_keys=True), key),
            )
            _event(
                connection,
                job_id,
                JobState.RUNNING,
                found["phase"],
                "stopped reading contents and kept what was read; the rules are applied to it next",
                at,
            )
        return True

    def enqueue(
        self,
        *,
        spellings: Iterable[str] = (),
        steps: Iterable[str] = (),
        record_sequence: int | None,
        base_run: str | None,
    ) -> JobRow:
        """Write down what is asked, joining a job already queued and not yet started.

        Answers given while a job waits ride along with it: two jobs never publish runs over each
        other, and one job applying two answers costs one rebind and one inference. The Record
        sequence becomes the higher of the two.
        """
        wanted = sorted({spelling for spelling in spellings if spelling})
        asked = sorted(set(steps))
        at = now()
        with self._writing() as connection:
            waiting = connection.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY ordinal LIMIT 1",
                (JobState.QUEUED.value,),
            ).fetchone()
            if waiting is not None:
                held = _row(waiting)
                spellings_now = sorted({*held.spellings, *wanted})
                steps_now = sorted({*held.steps, *asked})
                given = (held.record_sequence, record_sequence)
                sequence = max((value for value in given if value is not None), default=None)
                # A job that grows forgets the census an earlier attempt bound. A retry resumes at
                # inference only where that census read every spelling the job now holds, so a
                # word joined after an interruption is read again and not claimed as applied by a
                # watermark that covers it.
                grew = spellings_now != sorted(held.spellings) or steps_now != sorted(held.steps)
                # So does a job asked for a later Record. A later answer about a word it already
                # holds joins it as no new spelling, and a census or an inference an earlier
                # attempt made read the Record before that answer.
                later = sequence is not None and (
                    held.record_sequence is None or sequence > held.record_sequence
                )
                forget = grew or later
                # The same holds for the runs its attempts planned: a retry takes up a planned
                # run only where the job asks what it asked when that run was planned.
                progress = {
                    key: value
                    for key, value in held.progress.items()
                    if not forget or key not in _PLANNED
                }
                connection.execute(
                    "UPDATE jobs SET intent_json = ?, kind = ?, record_sequence = ?, "
                    "bound_run = CASE WHEN ? THEN NULL ELSE bound_run END, progress_json = ? "
                    "WHERE job_id = ?",
                    (
                        json.dumps({"spellings": spellings_now, "steps": steps_now}),
                        _kind(spellings_now, steps_now),
                        sequence,
                        forget,
                        json.dumps(progress, sort_keys=True),
                        held.job_id,
                    ),
                )
                _event(
                    connection,
                    held.job_id,
                    JobState.QUEUED,
                    None,
                    f"more asked of it while it waited: {_describe(spellings_now, steps_now)}",
                    at,
                )
                job_id = held.job_id
            else:
                job_id = new_job_id()
                connection.execute(
                    "INSERT INTO jobs (job_id, kind, intent_json, state, record_sequence, "
                    "base_run, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        _kind(wanted, asked),
                        json.dumps({"spellings": wanted, "steps": asked}),
                        JobState.QUEUED.value,
                        record_sequence,
                        base_run,
                        at,
                    ),
                )
                _event(connection, job_id, JobState.QUEUED, None, _describe(wanted, asked), at)
            if _pin(connection) is None and base_run is not None:
                _set_pin(connection, base_run)
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row(found)

    def claim_next(self, *, pinned: str | None) -> JobRow | None:
        """Take the oldest queued job for a worker: running, one attempt more. None if none waits.

        ``pinned`` is the run current now, which reads are pinned to if nothing pins them yet.
        """
        at = now()
        with self._writing() as connection:
            waiting = connection.execute(
                "SELECT job_id, attempts FROM jobs WHERE state = ? ORDER BY ordinal LIMIT 1",
                (JobState.QUEUED.value,),
            ).fetchone()
            if waiting is None:
                return None
            attempt = int(waiting["attempts"]) + 1
            connection.execute(
                "UPDATE jobs SET state = ?, attempts = ?, started_at = ?, heartbeat_at = ?, "
                "phase = NULL, worker_pid = NULL, error = NULL WHERE job_id = ?",
                (JobState.RUNNING.value, attempt, at, at, waiting["job_id"]),
            )
            _event(connection, waiting["job_id"], JobState.RUNNING, None, f"attempt {attempt}", at)
            if _pin(connection) is None and pinned is not None:
                _set_pin(connection, pinned)
            found = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (waiting["job_id"],)
            ).fetchone()
        return _row(found)

    def attach(self, job_id: str, *, attempt: int, pid: int, log_path: str | None = None) -> None:
        """Record which process runs this attempt, and where its log is."""
        with self._writing() as connection:
            connection.execute(
                "UPDATE jobs SET worker_pid = ?, log_path = COALESCE(?, log_path) "
                "WHERE job_id = ? AND attempts = ?",
                (pid, log_path, job_id, attempt),
            )

    def heartbeat(
        self,
        job_id: str,
        *,
        attempt: int,
        phase: str | None = None,
        progress: Mapping[str, Any] | None = None,
        bound_run: str | None = None,
    ) -> bool:
        """Say this attempt is alive, and where it is. False where the job is no longer its own.

        A worker whose job was reconciled away (marked interrupted and queued again while it was
        thought dead) learns so here, and stops instead of publishing over its successor.

        A beat that names no phase and no run is written one statement at a time
        (:meth:`_beat`). These are the heartbeat thread's writes, which another thread holding the
        interpreter lock can starve at any moment.
        """
        if phase is None and bound_run is None:
            return self._beat(job_id, attempt=attempt, progress=progress)
        at = now()
        with self._writing() as connection:
            held = connection.execute(
                "SELECT state, attempts, phase, progress_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if (
                held is None
                or held["state"] != JobState.RUNNING.value
                or int(held["attempts"]) != attempt
            ):
                return False
            merged = json.loads(held["progress_json"] or "{}")
            if progress:
                merged.update(progress)
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, phase = COALESCE(?, phase), "
                "progress_json = ?, bound_run = COALESCE(?, bound_run) WHERE job_id = ?",
                (at, phase, json.dumps(merged, sort_keys=True), bound_run, job_id),
            )
            if phase is not None and phase != held["phase"]:
                _event(connection, job_id, JobState.RUNNING, phase, f"now {phase}", at)
        return True

    def _beat(
        self, job_id: str, *, attempt: int, progress: Mapping[str, Any] | None = None
    ) -> bool:
        """A heartbeat as single statements, each its own transaction.

        A beat written as ``BEGIN IMMEDIATE``, a read, an update and ``COMMIT`` holds the ledger's
        write lock between its statements, which is where Python code runs. Measurement showed
        the consequence. A thread starved of the interpreter lock there (another thread in one
        long call into compiled code) kept the write lock for the length of that call: 4 s in the
        test, 16 s for a sort. Every other writer, the answer route queueing a job among them,
        waited on it. A single statement takes and lets go of the write lock inside the database
        call, which runs without the interpreter lock. The method merges progress by comparing
        and swapping: the update applies only where the progress is still what was read.
        """
        if not self.exists():
            self._create()
        connection = self._open()
        try:
            for _ in range(20):
                at = now()
                if not progress:
                    cursor = connection.execute(
                        "UPDATE jobs SET heartbeat_at = ? WHERE job_id = ? AND state = ? "
                        "AND attempts = ?",
                        (at, job_id, JobState.RUNNING.value, attempt),
                    )
                    return cursor.rowcount == 1
                held = connection.execute(
                    "SELECT state, attempts, progress_json FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if (
                    held is None
                    or held["state"] != JobState.RUNNING.value
                    or int(held["attempts"]) != attempt
                ):
                    return False
                merged = json.loads(held["progress_json"] or "{}")
                merged.update(progress)
                cursor = connection.execute(
                    "UPDATE jobs SET heartbeat_at = ?, progress_json = ? WHERE job_id = ? "
                    "AND state = ? AND attempts = ? AND progress_json IS ?",
                    (
                        at,
                        json.dumps(merged, sort_keys=True),
                        job_id,
                        JobState.RUNNING.value,
                        attempt,
                        held["progress_json"],
                    ),
                )
                if cursor.rowcount == 1:
                    return True
                # The progress changed between the read and the write: read it again.
            return False
        finally:
            connection.close()

    def finish(
        self,
        job_id: str,
        *,
        attempt: int,
        state: JobState,
        error: str | None = None,
        produced_run: str | None = None,
        message: str | None = None,
    ) -> JobRow | None:
        """End this attempt ``done``, ``failed`` or ``cancelled``. None where the job is no longer
        its own."""
        if state not in FINAL_STATES:
            message_text = f"a job finishes done, failed or cancelled, not {state.value}"
            raise ValueError(message_text)
        at = now()
        with self._writing() as connection:
            held = connection.execute(
                "SELECT state, attempts, phase FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if (
                held is None
                or held["state"] != JobState.RUNNING.value
                or int(held["attempts"]) != attempt
            ):
                return None
            connection.execute(
                "UPDATE jobs SET state = ?, error = ?, produced_run = COALESCE(?, produced_run), "
                "ended_at = ?, heartbeat_at = ? WHERE job_id = ?",
                (state.value, error, produced_run, at, at, job_id),
            )
            said = message or (
                f"published {produced_run}"
                if state is JobState.DONE
                else f"cancelled: {error}"
                if state is JobState.CANCELLED
                else f"failed: {error}"
            )
            _event(connection, job_id, state, held["phase"], said, at)
            _settle_pin(connection, produced_run if state is JobState.DONE else None)
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row(found)

    def interrupt(
        self,
        job_id: str,
        *,
        reason: str,
        retry_limit: int,
        counts: bool = True,
        cause: str | None = None,
    ) -> JobRow:
        """Mark a running job's worker dead, and queue it again or fail it at the limit.

        A job that has ended, done or failed, is returned as it is: its end stands.

        ``counts`` is False for an interruption nobody should hold against the job, such
        as the Dashboard closing while it ran. It queues the job again without spending a retry.

        ``cause`` is how the worker ended, where the supervisor knows (``killed``, ``crashed``,
        ``silent``, ``exited``, ``gone`` or ``stopped``). A person's Stop ends the job
        ``cancelled`` and never queues it again: a job stopped on purpose is not an interruption to
        recover from. A failure that is not an accident and repeats where it happened is not
        retried again. A worker that crashed, or fell silent and was stopped, in the same phase as
        its last attempt did will do so again on the same input, so the job fails then with both
        said, and does not spend the retries left on it. A kill, a worker found gone and a worker
        that exited are retried up to the limit, as they may be accidents of the machine.
        """
        at = now()
        with self._writing() as connection:
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if found is None:
                message = f"no job {job_id} in the ledger at {self.path}"
                raise LookupError(message)
            held = _row(found)
            if held.state in FINAL_STATES:
                # Its end, once written, stands. A judgement that the worker is gone, read before
                # the worker wrote its end and acted on after, would run a finished job again.
                # Nothing is written.
                return held
            if cause == STOPPED:
                connection.execute(
                    "UPDATE jobs SET state = ?, error = ?, ended_at = ?, worker_pid = NULL "
                    "WHERE job_id = ?",
                    (JobState.CANCELLED.value, reason, at, job_id),
                )
                _event(
                    connection, job_id, JobState.CANCELLED, held.phase, f"cancelled: {reason}", at
                )
                _settle_pin(connection, None)
                found = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                return _row(found)
            _event(connection, job_id, JobState.INTERRUPTED, held.phase, reason, at)
            this = {"cause": cause, "phase": held.phase}
            again = (
                counts and cause in NOT_ACCIDENTS and held.progress.get("last_interruption") == this
            )
            if counts and (held.retries >= retry_limit or again):
                error = (
                    f"{_CAUSE_WORDS.get(cause or '', 'stopped')} the same way twice, in "
                    f"{held.phase or 'its start'}: it would do so again on the same input, so "
                    f"it is not run a third time; the last: {reason}"
                    if again
                    else f"interrupted {held.attempts} times and stopped after {retry_limit} "
                    f"{'retry' if retry_limit == 1 else 'retries'}; the last: {reason}"
                )
                connection.execute(
                    "UPDATE jobs SET state = ?, error = ?, ended_at = ? WHERE job_id = ?",
                    (JobState.FAILED.value, error, at, job_id),
                )
                _event(connection, job_id, JobState.FAILED, held.phase, error, at)
                _settle_pin(connection, None)
            else:
                retries = held.retries + (1 if counts else 0)
                progress = dict(held.progress)
                if counts:
                    progress["last_interruption"] = this
                connection.execute(
                    "UPDATE jobs SET state = ?, retries = ?, worker_pid = NULL, progress_json = ? "
                    "WHERE job_id = ?",
                    (JobState.QUEUED.value, retries, json.dumps(progress, sort_keys=True), job_id),
                )
                said = (
                    f"queued again: retry {retries} of {retry_limit}"
                    if counts
                    else "queued again (not counted as a retry)"
                )
                _event(connection, job_id, JobState.QUEUED, held.phase, said, at)
            found = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row(found)


#: The event holding a job that a close stopped, and the one a person's Re-run writes to release
#: it. A job is held while the hold is its latest event.
HELD = "held: stopped when the Dashboard last closed, waiting for a person's Re-run"
RELEASED = "released by a person's Re-run"


def _event(
    connection: sqlite3.Connection,
    job_id: str,
    state: JobState,
    phase: str | None,
    message: str,
    at: str,
) -> None:
    connection.execute(
        "INSERT INTO job_events (job_id, at, state, phase, message) VALUES (?, ?, ?, ?, ?)",
        (job_id, at, state.value, phase, message),
    )


def _busy(connection: sqlite3.Connection) -> bool:
    marks = ", ".join("?" for _ in _BUSY)
    return (
        connection.execute(
            f"SELECT 1 FROM jobs WHERE state IN ({marks}) LIMIT 1",  # noqa: S608
            _BUSY,
        ).fetchone()
        is not None
    )


def _pin(connection: sqlite3.Connection) -> str | None:
    found = connection.execute("SELECT value FROM meta WHERE key = 'pinned_run'").fetchone()
    return None if found is None or found[0] is None else str(found[0])


def _set_pin(connection: sqlite3.Connection, run_id: str | None) -> None:
    connection.execute(
        "INSERT INTO meta (key, value) VALUES ('pinned_run', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (run_id,),
    )


def _settle_pin(connection: sqlite3.Connection, produced: str | None) -> None:
    """After a job ends: pinned to what it produced while more wait, and unpinned when none do."""
    if not _busy(connection):
        _set_pin(connection, None)
    elif produced is not None:
        _set_pin(connection, produced)
