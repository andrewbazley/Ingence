"""The reorganization notice and a folder's one line, read from two censuses and written nowhere.

``compare/`` compares any two runs. This module is the seam that a page calls.

What the pages show, and when. The notice is a flag that says what changed, and never a roadblock:

- The History page calls :func:`reorganisation` for the project. When ``noticed`` is true, it
  shows the ``notice`` sentence while the difference stands. The notice stops nothing and waits
  for nothing.
- A folder's own page calls :func:`folder_line` with the folder's path as read. When the answer
  is not None, the page shows it as one quiet line (``compare.carry.SAY_ON_FOLDER_PAGE`` controls
  this). It is never a queue.
- Two named runs or two projects (the same files on another drive, or a backup beside its
  source) go through :func:`compare_runs`.

Which two censuses. The current run, and the latest earlier run that is not built from the same
census. A rebind or an inference reads a census over again and leaves every path where it was. So
a run whose ancestry (its parent, and that run's parent) reaches the current run's own census is
the same census and is passed over. Where a run of the chain was moved to the Trash, the census is
the latest walk at or before it (:func:`_census_of`). A project with no earlier census has nothing
to say (``available`` is false).

Cost, and what is held. Matching two runs' files is SQL over their Parquet and takes seconds on a
large census. Reading the Record's decisions against the result takes milliseconds. The two runs
never change, so :func:`reorganisation` holds the measured comparison per (project, earlier run,
later run), for the last ``HELD`` it was asked, and when the Record grows it reads only the
decisions again. One comparison is measured at a time. A second page that asks for the same one
waits for it and is given the one held.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from ingence.api.run_names import project_run_names
from ingence.compare import carry
from ingence.compare.match import Side
from ingence.compare.report import Measured, compare_organisation, measure, report_of
from ingence.record.log import read_events
from ingence.storage.catalog import sql_path_list
from ingence.storage.commit import read_pointer
from ingence.storage.connection import connect as duckdb_connection
from ingence.storage.layout import RUNS_DIR

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["census_pair", "compare_runs", "folder_line", "moves_undone", "reorganisation"]

#: How many measured comparisons are held between calls (each holds a few counts, each census's
#: folders and the matched files beneath the folders that moved).
HELD = 4

_held: OrderedDict[tuple[str, str, str], Measured] = OrderedDict()
#: The report last read against each held comparison, with the Record it was read from.
_reported: dict[tuple[str, str, str], tuple[tuple[int, str, bool], dict[str, Any]]] = {}
_holding = threading.Lock()
_measuring = threading.Lock()


def _parents(project: Path) -> dict[str, str | None]:
    """Every run of a project and the run it was built from, read from the run records."""
    directory = project / RUNS_DIR
    found: dict[str, str | None] = {}
    connection = duckdb_connection()
    try:
        for path in sorted(directory.iterdir()) if directory.is_dir() else []:
            files = sorted((path / "run").glob("*.parquet"))
            if not path.is_dir() or not files:
                continue
            row = connection.execute(
                f"SELECT parent_run_id FROM read_parquet({sql_path_list(files)}) LIMIT 1"  # noqa: S608
            ).fetchone()
            found[path.name] = str(row[0]) if row and row[0] else None
    finally:
        connection.close()
    return found


def _census_of(run: str, parents: dict[str, str | None]) -> str:
    """The walk whose paths a run carries: its parent, and that run's parent, back to a run built
    from no other. A walk is such a run. A rebind, a dive and an inference each name the run they
    read.

    Where a run in the chain is gone (moved to the Trash), the chain cannot be followed past it.
    Stopping there would make the current census look like a census of its own, and a re-read of it
    would be compared with itself ("Nothing was reorganised"). The census is then the latest walk at
    or before the missing run, the one it can only have been built from. With no such walk, the
    missing run stands for its census.
    """
    seen = {run}
    while (parent := parents.get(run)) is not None:
        if parent not in parents:
            walks = [each for each, above in parents.items() if above is None and each <= parent]
            return max(walks) if walks else parent
        if parent in seen:
            break
        run = parent
        seen.add(run)
    return run


def census_pair(project: Path) -> tuple[str, str] | None:
    """The current run and the latest earlier run built from a different census, or None.

    Run identifiers begin with the moment the run began, so they order the runs (as
    ``run_names`` does); "earlier" is an identifier that sorts before.
    """
    pointer = read_pointer(project)
    if pointer is None:
        return None
    parents = _parents(project)
    after = pointer.run_id
    if after not in parents:
        return None
    own = _census_of(after, parents)
    earlier = [run for run in sorted(parents) if run < after and _census_of(run, parents) != own]
    return (earlier[-1], after) if earlier else None


def compare_runs(left: Side, right: Side, *, project: Path | None = None) -> dict[str, Any]:
    """Compare any two sides. ``project`` names whose Record to read decisions from (none: none)."""
    events = read_events(project) if project is not None else ()
    return compare_organisation(left, right, events=events)


def _measured(project: Path, first: str, second: str) -> tuple[tuple[str, str, str], Measured]:
    """The held comparison of two runs, measured now if it is not held."""
    key = (str(project), first, second)
    with _holding:
        if key in _held:
            _held.move_to_end(key)
            return key, _held[key]
    with _measuring:
        with _holding:
            if key in _held:
                return key, _held[key]
        names = project_run_names(project)
        measured = measure(
            Side(project, first, names.get(first, {}).get("label", first)),
            Side(project, second, names.get(second, {}).get("label", second)),
        )
        with _holding:
            _held[key] = measured
            while len(_held) > HELD:
                gone, _ = _held.popitem(last=False)
                _reported.pop(gone, None)
    return key, measured


def reorganisation(
    project: Path, *, before: str | None = None, after: str | None = None
) -> dict[str, Any]:
    """What the History page shows: the notice for a run against the census before it.

    Keys: ``available`` (False where there is no earlier census, and then nothing else but
    ``notice`` None), ``noticed`` (a folder moved, a decision's folder is gone and its files were
    found elsewhere, or files were matched without their sizes: the page shows ``notice``),
    ``notice`` (the plain sentences), ``compared`` (the two runs' names), ``matched_without_size``
    and ``states_no_sizes`` (per run, ``before`` and ``after``), and the whole report under
    ``report``.
    """
    chosen = (before, after) if before and after else census_pair(project)
    if chosen is None:
        return {"available": False, "noticed": False, "notice": None, "folder_lines": {}}
    first, second = chosen
    events = read_events(project)
    key, measured = _measured(project, first, second)
    # The Record as read, and the folder-page veto as set now: either changing reads again.
    state = (len(events), events[-1].event_id if events else "", carry.SAY_ON_FOLDER_PAGE)
    with _holding:
        found = _reported.get(key)
    if found is not None and found[0] == state:
        report = found[1]
    else:
        report = report_of(measured, events)
        with _holding:
            if key in _held:
                _reported[key] = (state, report)
    undone = moves_undone(project)
    said = report["notice"]
    if undone:
        said = f"{said} {_undone_sentence(len(undone))}" if said else _undone_sentence(len(undone))
    return {
        "available": True,
        "noticed": report["noticed"] or bool(undone),
        "notice": said,
        "compared": {"before": report["left"]["label"], "after": report["right"]["label"]},
        # How many files were matched without their sizes, and which reading states none. The
        # notice says both.
        "matched_without_size": report["matched_without_size"],
        "states_no_sizes": {
            "before": report["states_no_sizes"].get("l", False),
            "after": report["states_no_sizes"].get("r", False),
        },
        "folder_lines": report["folder_lines"],
        "report": report,
        "moves_undone": undone,
    }


def _undone_sentence(files: int) -> str:
    """What the notice adds where a dive undid moves."""
    if files == 1:
        return (
            "A content read found that one file the file list took for moved holds other bytes. "
            "So what was read and answered about the file it was taken for no longer follows it. "
            "Its own results come from the content read."
        )
    return (
        f"A content read found that {files:,} files the file list took for moved hold other "
        "bytes. So what was read and answered about the files they were taken for no longer "
        "follows them. Their own results come from the content read."
    )


def moves_undone(project: Path) -> list[dict[str, str]]:
    """The current run's files that a dive found were not the files a walk took them for.

    Each comes with its path and the path of the file it was taken for. The function reads them
    from the current run's entries (``moved_by`` ``digest_differs``) and the entry named by
    ``moved_from``, which the run holds as a tombstone. The result is empty where the run holds
    none, or was written before runs recorded any.
    """
    from ingence.core.schema import MovedBy
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        if not catalog.has_run:
            return []
        rows = catalog.query(
            "SELECT moved.relative_path, coalesce(taken.relative_path, moved.moved_from) "
            "FROM entries moved LEFT JOIN entries taken ON taken.entry_id = moved.moved_from "
            "WHERE moved.moved_by = ? AND NOT moved.is_deleted "
            "ORDER BY moved.relative_path",
            [MovedBy.DIGEST_DIFFERS.value],
        ).fetchall()
    return [{"path": str(path), "taken_for": str(taken)} for path, taken in rows]


def folder_line(
    project: Path, place: str, *, before: str | None = None, after: str | None = None
) -> str | None:
    """The one line a folder's own page shows, or None: the folder's path as read."""
    lines: dict[str, str] = reorganisation(project, before=before, after=after)["folder_lines"]
    return lines.get(place)
