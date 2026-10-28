"""Durable jobs: what a person asked the engine to apply survives the process that applies it.

An answer is written to the Record first, as every decision is. Applying it (reading names again,
applying the rules again) is a job, and a job is written down before it runs:

- The ledger (:mod:`ingence.jobs.ledger`): one SQLite file per project holding each job's
  intent, state, phase, progress, heartbeat, error, retries, the run it produced and the Record
  sequence it was asked for. The ledger is written in transactions and can be read while it is
  written.
- The worker (:mod:`ingence.jobs.worker`): a process of its own, ``python -m ingence.jobs.worker``,
  that reads its job from the ledger, runs it through the ordinary write protocol, heart-beats while
  it runs and records what it published.
- The supervisor (:mod:`ingence.jobs.supervisor`): a thread in the Dashboard's server that
  starts one worker at a time and watches it. It marks a job whose worker died ``interrupted``,
  so the job is never silently lost. On start it reconciles what a dead predecessor left
  ``running``.

The ledger lives in ``<project>/jobs/``, beside ``workspace/``. It is operational state like the
workspace: mutable, and never a source of scientific truth. Losing it loses the history of jobs
and any job not yet run. It never loses a decision (the Record holds those) or a run (``runs/``
holds those). It is not inside ``runs/``, whose directories are immutable once published, nor
inside ``record/``, which is append-only and whose listing is its index. The project layout's
rule that "only the pointer is mutable" is about what readers resolve interpretations through,
and nothing resolves an interpretation through the ledger. Nothing is written there until the
first job is, except the worker lock. That lock is the project's one publish lock: a hand-run
``index``, ``dive`` or ``infer`` takes it too (creating ``jobs/worker.lock`` and, while it holds
it, ``worker.lock.holder``), so that a command and a Dashboard job never publish over each other
(:func:`ingence.jobs.locks.publishing`).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "JOBS_DIR",
    "LEDGER_FILE",
    "PHASE_WORDS",
    "STOP_KEEPS",
    "SUPERVISOR_LOCK",
    "WORKER_LOCK",
    "JobState",
    "phase_words",
]

#: Beside ``runs/``, ``record/`` and ``workspace/`` in a project (see the module's docstring).
JOBS_DIR = "jobs"
#: What a job is doing in each phase, in a person's words. This is the one list that the worker,
#: the supervisor and the Stop route use to name a phase, by the ledger's phase word.
PHASE_WORDS: dict[str, str] = {
    "census": "listing the files",
    "dive": "reading contents",
    "binding": "reading the names",
    "inferring": "applying the rules",
    "views": "preparing the views",
}
#: Whether a Stop while reading contents keeps what was read. ``/api/applying``'s ``stop_keeps``
#: and the Stop route's answer both read this constant. It is True because the worker's
#: heartbeat asks the content step's token to stop and keep: the pass publishes what it has read,
#: and the job goes on to apply the rules.
STOP_KEEPS = True


def phase_words(phase: str | None) -> str:
    """A phase in a person's words, such as ``while reading the names``.

    The result is ``before it started`` for none.
    """
    if not phase:
        return "before it started"
    return f"while {PHASE_WORDS.get(phase, phase.replace('_', ' '))}"


#: The ledger, a SQLite database in write-ahead-log mode (its ``-wal`` and ``-shm`` beside it).
LEDGER_FILE = "ledger.sqlite3"
#: Held by the one server whose supervisor runs this project's jobs, for as long as it serves.
SUPERVISOR_LOCK = "supervisor.lock"
#: Held by a worker for as long as it runs a job, and by a hand-run command that publishes runs.
#: The operating system lets go of both when the process holding one dies, however it dies,
#: which is what makes "the worker is gone" knowable.
WORKER_LOCK = "worker.lock"


class JobState(StrEnum):
    """Where a job stands. A job ends ``done``, ``failed`` or ``cancelled``. ``interrupted`` is
    on the way back."""

    #: Written down and waiting for the worker.
    QUEUED = "queued"
    #: A worker has it.
    RUNNING = "running"
    #: It will not run again: its worker raised, or it was interrupted more than the limit.
    FAILED = "failed"
    #: It published what it was asked for.
    DONE = "done"
    #: Its worker died while it ran. Queued again at once where retries remain, so a job rests in
    #: this state only in its history. The history keeps every interruption and its reason.
    INTERRUPTED = "interrupted"
    #: A person's Stop: final, never retried.
    CANCELLED = "cancelled"
