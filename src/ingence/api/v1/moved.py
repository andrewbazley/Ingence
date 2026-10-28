"""Where a file came from, and the one sentence every page shows for it.

A walk that finds a file at a new path records the entry it came from (``moved_from``), how that
was known (``moved_by``) and when, at the latest (``moved_at_ns``). The Dashboard's inspector
(``/api/v1/entry``) and the Graph workspace's (``/api/v1/explorer/entry``) show the same words,
made here, beside the label "Moved here".

A copy keeping its original's name, size and time, with the original removed, is read as a move.
What was read and decided about the original follows the copy like any move. The words say it was
a copy, so a person can see it. A folder whose move rests on copies alone says so too
(``copied_contents``).

The original's place is kept after later walks. The run that found a move holds the original as
deleted, and a later walk carrying the move does not. So the place is read from the project's
earlier runs (an entry's identifier names one path), newest first. Where none holds it, the words
say so once, never "a copy of a file no longer there, the original removed".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import duckdb

from ingence.api.v1.models import ApiModel, ZonedTime
from ingence.api.v1.shaping import time_from_ns
from ingence.core.schema import MovedBy

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = ["MovedSaid", "move_words", "moved_in_run", "moved_said"]


class MovedSaid(ApiModel):
    """Where an entry came from, how that was known, and the words a page shows for it."""

    #: The path it had, read from the run or the project's earlier runs; None where none holds it.
    from_path: str | None
    #: The stored word (``copied``, ``same_file``, ``digest_differs``...).
    by: str
    #: When it moved, at the latest: never the file's own clock.
    at: ZonedTime | None
    #: What a page shows beside "Moved here".
    said: str


#: The ways a dive found the same contents: its whole digest, or a sample's (a spot check).
_CHECKED = frozenset({MovedBy.WHOLE_DIGEST.value})
_SPOT_CHECKED = frozenset({MovedBy.SAMPLED_DIGEST.value, MovedBy.COPIED_SAME_BYTES.value})
#: The ways that say it was a copy, its original removed.
_COPIES = frozenset(
    {MovedBy.COPIED.value, MovedBy.COPIED_SAME_BYTES.value, MovedBy.COPIED_CONTENTS.value}
)


def move_words(by: str, from_path: str | None) -> str:
    """The words for one move, beside the label "Moved here", in plain words.

    The words say "contents", never "bytes", and a sample's match is a spot check.
    """
    if by == MovedBy.DIGEST_DIFFERS.value:
        other = f"those of {from_path}" if from_path else "the file it was taken for"
        return f"not after all; its contents differ from {other}"
    if by in _COPIES:
        said = (
            f"as a copy of {from_path}, the original removed"
            if from_path
            else ("as a copy, the original removed")
        )
    else:
        said = f"from {from_path}" if from_path else "from an earlier place"
    if by in _CHECKED:
        return f"{said}, its contents checked"
    if by in _SPOT_CHECKED:
        return f"{said}, its contents spot-checked"
    return said


#: The one query both readers ask, over a run's ``entries``.
_MOVED_SQL = (
    "SELECT moved.moved_from, moved.moved_by, moved.moved_at_ns, taken.relative_path "
    "FROM entries moved LEFT JOIN entries taken ON taken.entry_id = moved.moved_from "
    "WHERE moved.entry_id = ? AND moved.moved_from IS NOT NULL LIMIT 1"
)


def _earlier_path(
    connection: duckdb.DuckDBPyConnection, project: Path, run_id: str, entry_id: str
) -> str | None:
    """Where an entry was, read from the project's runs before ``run_id``, newest first."""
    from ingence.storage.layout import RUNS_DIR

    runs = project / RUNS_DIR
    earlier = (
        sorted(
            (path for path in runs.iterdir() if path.is_dir() and path.name < run_id),
            reverse=True,
        )
        if runs.is_dir()
        else []
    )
    for run in earlier:
        fragments = sorted((run / "entries").glob("*.parquet"))
        if not fragments:
            continue
        listed = ", ".join("'" + path.as_posix().replace("'", "''") + "'" for path in fragments)
        try:
            row = connection.execute(
                f"SELECT relative_path FROM read_parquet([{listed}], union_by_name = true) "  # noqa: S608
                "WHERE entry_id = ? LIMIT 1",
                [entry_id],
            ).fetchone()
        except duckdb.Error:
            continue
        if row is not None and row[0] is not None:
            return str(row[0])
    return None


def _said(
    row: tuple[Any, ...] | None,
    *,
    connection: duckdb.DuckDBPyConnection,
    project: Path,
    run_id: str,
) -> MovedSaid | None:
    if row is None or row[1] is None:
        return None
    by = str(row[1])
    from_path = (
        str(row[3])
        if row[3] is not None
        else _earlier_path(connection, project, run_id, str(row[0]))
    )
    return MovedSaid(
        from_path=from_path, by=by, at=time_from_ns(row[2]), said=move_words(by, from_path)
    )


def moved_said(catalog: Catalog, entry_id: str) -> MovedSaid | None:
    """The move one entry of a run records, in words, or None where it records none (a run
    written before moves were recorded reads them as none)."""
    if catalog.run_directory is None:
        return None
    try:
        row = catalog.query(_MOVED_SQL, [entry_id]).fetchone()
        return _said(
            row,
            connection=catalog.connection,
            project=catalog.project,
            run_id=catalog.run_directory.name,
        )
    except duckdb.Error:
        return None


def moved_in_run(
    project: Path,
    run_id: str,
    entry_id: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> MovedSaid | None:
    """The same, read from the run's entries alone, on ``connection`` where the caller has one
    open: a card that reads a projection and no catalog asks it without opening one (a catalog
    defines a view per table, and a connection costs more than this read on a small project)."""
    from ingence.storage.catalog import parquet_source
    from ingence.storage.connection import connect
    from ingence.storage.layout import RUNS_DIR, TABLES

    fragments = sorted((project / RUNS_DIR / run_id / "entries").glob("*.parquet"))
    if not fragments:
        return None
    spec = next(table for table in TABLES if table.name == "entries")
    reading = connection if connection is not None else connect()
    try:
        source = parquet_source(reading, spec, fragments)
        row = reading.execute(f"WITH entries AS ({source}) {_MOVED_SQL}", [entry_id]).fetchone()
        return _said(row, connection=reading, project=project, run_id=run_id)
    except duckdb.Error:
        return None
    finally:
        if connection is None:
            reading.close()
