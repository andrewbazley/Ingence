"""``GET /api/v1/jobs``: the project's recent jobs and their states, from the job ledger.

What the page shows as queued, running, failed and done, and the history that says why: every
interruption with its reason, every retry, the run each job produced and the Record sequence it
was asked for. Read-only; a job is written by the answer and re-run routes, never here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ingence.api.v1 import ApiError, now, route
from ingence.api.v1.models import ApiModel, Count, Identifier, ZonedTime, register
from ingence.jobs.ledger import JobEvent, JobLedger, JobRow

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.api.v1 import V1Context

__all__ = ["JOBS_LIMIT", "JobEventItem", "JobItem", "JobsResponse", "jobs"]

#: How many jobs a request lists where it does not say, and the most it may ask for.
JOBS_LIMIT = 20
JOBS_LIMIT_MAX = 200

JobStateName = Literal["queued", "running", "failed", "done", "interrupted", "cancelled"]


class JobEventItem(ApiModel):
    """One change of a job's state, with its reason in words."""

    at: ZonedTime
    state: JobStateName
    phase: str | None
    message: str


class JobItem(ApiModel):
    """One job as the ledger holds it."""

    job_id: Identifier
    #: ``definition`` (an answer named a meaning), ``rerun`` (committed changes) or ``first_run``
    #: (a project's first reading, asked for on the landing page).
    kind: Literal["definition", "rerun", "first_run"]
    #: What it does, in order: ``partial_rebind`` or ``whole_rebind``, then ``inference``. For a
    #: first run, ``census``, then ``dive`` where asked, then ``inference``.
    operations: tuple[str, ...]
    #: The spellings a partial rebind reads again.
    spellings: tuple[str, ...]
    state: JobStateName
    #: Where a running job is, in the ledger's words: ``census``, ``dive``, ``binding``,
    #: ``inferring`` or ``views`` (``/api/applying`` says the contract's words).
    phase: str | None
    #: Times it was queued again after an interruption, and attempts made in all.
    retries: Count
    attempts: Count
    #: The highest Record sequence when it was asked for; its runs' watermarks reach it.
    record_sequence: Count | None
    #: The run current when it was asked for, the census its rebind published, and the run it
    #: produced last.
    base_run: Identifier | None
    bound_run: Identifier | None
    produced_run: Identifier | None
    error: str | None
    heartbeat_at: ZonedTime | None
    created_at: ZonedTime
    started_at: ZonedTime | None
    ended_at: ZonedTime | None
    events: tuple[JobEventItem, ...]


@register("jobs")
class JobsResponse(ApiModel):
    """``GET /api/v1/jobs``: the recent jobs, newest first, and which run reads are served."""

    api_version: Literal["1.0"] = "1.0"
    run_pinned: Identifier | None
    run_current: Identifier | None
    job_running: bool
    jobs: tuple[JobItem, ...]
    answered_at: ZonedTime


def _event(event: JobEvent) -> JobEventItem:
    return JobEventItem(
        at=event.at, state=event.state.value, phase=event.phase, message=event.message
    )


def _item(row: JobRow, events: Sequence[JobEvent]) -> JobItem:
    return JobItem(
        job_id=row.job_id,
        kind=row.kind if row.kind in ("rerun", "first_run") else "definition",
        operations=row.operations,
        spellings=row.spellings,
        state=row.state.value,
        phase=row.phase,
        retries=row.retries,
        attempts=row.attempts,
        record_sequence=row.record_sequence,
        base_run=row.base_run,
        bound_run=row.bound_run,
        produced_run=row.produced_run,
        error=row.error,
        heartbeat_at=row.heartbeat_at,
        created_at=row.created_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        events=tuple(_event(event) for event in events),
    )


def _limit(query: Mapping[str, Sequence[str]]) -> int:
    given = query.get("limit")
    if not given:
        return JOBS_LIMIT
    try:
        wanted = int(given[0])
    except ValueError:
        wanted = 0
    if not 1 <= wanted <= JOBS_LIMIT_MAX:
        message = f"limit is a whole number from 1 to {JOBS_LIMIT_MAX}, not {given[0]!r}"
        raise ApiError(message, parameter="limit")
    return wanted


@route("jobs", model=JobsResponse)
def jobs(context: V1Context, query: Mapping[str, Sequence[str]]) -> JobsResponse:
    """The project's recent jobs, newest first, each with its history."""
    ledger = JobLedger(context.project)
    rows = ledger.recent(_limit(query))
    return JobsResponse(
        run_pinned=context.run_pinned,
        run_current=context.run_current,
        job_running=context.job_running,
        jobs=tuple(_item(row, ledger.events(row.job_id)) for row in rows),
        answered_at=now(),
    )
