"""What a run records about the Record it read and the job that produced it (schema 1.22).

The Record watermark (``RunRecord.record_watermark``) is the highest Record sequence a run
incorporates. The run reads it before it first reads the Record, and it is exact: the run reads
the Record inside ``record.log.read_at`` at it, so every decision at or below it was read and
none above it was, however long the run takes. So the run can be made again from the Index, the
Rulebook, the Dictionary and the Record. A decision above it is the next run's, and a
page treats it as waiting. The watermark is ``0`` for an empty Record and ``None`` for a run that
read no Record at all.

The job (``RunRecord.job_id``) is the durable job that produced the run, where one did. A worker
runs a job's steps inside :func:`job_scope`, and the code that builds each run record reads
:func:`current_job_id`. A context variable carries the job, and no parameter is threaded through
every command, so the commands' signatures stay as they are.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ingence.record.log import read_bound, sequences_present

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["current_job_id", "job_scope", "record_watermark"]

_JOB: ContextVar[str | None] = ContextVar("ingence_job_id", default=None)


def record_watermark(project: Path) -> int:
    """The highest sequence in the project's Record now, or 0 where it holds none.

    The function reads the Record's listing (a fragment is named for its sequence), which costs a
    directory listing and opens no fragment. Inside a bound (``record.log.read_at``) it returns
    the bound where that is lower, so a run started inside one states what it reads.
    """
    highest = max(sequences_present(project), default=0)
    bound = read_bound(project)
    return highest if bound is None else min(highest, bound)


def current_job_id() -> str | None:
    """The job whose steps are running here, or None outside a job."""
    return _JOB.get()


@contextmanager
def job_scope(job_id: str) -> Iterator[None]:
    """Run the enclosed steps as ``job_id``'s: every run record built inside names it."""
    token = _JOB.set(job_id)
    try:
        yield
    finally:
        _JOB.reset(token)
