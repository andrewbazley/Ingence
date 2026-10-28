"""A run's, or every run's, quality figures as a table for a paper.

``ingence export quality`` writes three files into a folder:

- ``quality.csv``: one row per figure and breakdown value, with the project and the run, the
  pass that wrote the run, the figure, its label and unit, its state, the count, what the count is
  out of, the seconds for a timed figure, the peak memory in bytes, the one-line method, and
  a note;
- ``quality.json``: the same rows, with the runs' versions beside them;
- ``quality_runs.csv``: one row per run, with the versions that wrote it, read from the run record
  (the engine, the schema, the name reader, the Rulebook and Dictionary digests, the packs), and
  three counts a paper's table of runs wants beside them: the answers in the answer log that the
  run had read (``record_answers``), the claims the run settled (``settled_claims``) and the
  seconds it took (``wall_seconds``). A count the run does not state is a blank cell, never zero.

Two projects' tables concatenate into one. The columns are the same, and the first is the
project, named by ``--project-name`` where given and otherwise the neutral word ``project``, so
a table that is shared carries no name a person did not choose to put there. No share is written:
a share is the count over its whole, and a reader divides.
A figure that the run's pass does not reach or the engine does not record keeps its row, with its
state and no count, so a column never goes missing from a paper's table and is never zero.
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION
from ingence.core.destinations import write_replacing
from ingence.core.schema import QualityFigure, QualityState
from ingence.quality.catalogue import catalogue
from ingence.quality.compare import NS, figures_of, versions_of
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import MANIFEST_FILE, RUNS_DIR

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "COLUMNS",
    "NEUTRAL_PROJECT_NAME",
    "RUN_COLUMNS",
    "published_runs",
    "quality_rows",
    "run_rows",
    "table_paths",
    "write_tables",
]

#: What the first column holds unless the person names the project (``--project-name``).
NEUTRAL_PROJECT_NAME = "project"

#: The figure table's columns, in order. The first is the project, so tables concatenate.
COLUMNS: tuple[str, ...] = (
    "project",
    "run",
    "phase",
    "section",
    "figure",
    "facet",
    "label",
    "unit",
    "state",
    "count",
    "total",
    "seconds",
    "peak_bytes",
    "method",
    "note",
)

#: The run table's columns: the versions that wrote each run, from its record.
RUN_COLUMNS: tuple[str, ...] = (
    "project",
    "run",
    "phase",
    "started_at",
    "engine_version",
    "engine_commit",
    "schema_version",
    "normalizer_version",
    "rulebook_hash",
    "dictionary_hash",
    "packs",
    "record_answers",
    "settled_claims",
    "wall_seconds",
)


def published_runs(project: Path) -> list[str]:
    """The identifiers of the project's published runs, oldest first."""
    runs = project / RUNS_DIR
    if not runs.is_dir():
        return []
    return sorted(found.name for found in runs.iterdir() if (found / MANIFEST_FILE).is_file())


def quality_rows(project: Path, run_ids: Sequence[str], *, name: str) -> list[dict[str, Any]]:
    """The figure table's rows for the runs, in run order and the catalogue's figure order."""
    held = catalogue()
    order = {figure: at for at, figure in enumerate(held.figures)}
    rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        for figure in sorted(
            figures_of(project, run_id),
            key=lambda item: (order.get(item.figure, len(order)), item.facet != "", item.facet),
        ):
            definition = held.figures.get(figure.figure)
            if definition is None:
                continue
            rows.append(
                {
                    "project": name,
                    "run": run_id,
                    "phase": figure.phase,
                    "section": definition.section,
                    "figure": figure.figure,
                    "facet": figure.facet,
                    "label": definition.label,
                    "unit": definition.unit,
                    "state": figure.state.value,
                    "count": figure.count,
                    "total": figure.total,
                    "seconds": None if figure.duration_ns is None else figure.duration_ns / NS,
                    "peak_bytes": figure.peak_bytes,
                    "method": definition.method,
                    "note": figure.note,
                }
            )
    return rows


def run_rows(project: Path, run_ids: Sequence[str], *, name: str) -> list[dict[str, Any]]:
    """One row per run with the versions its record states, referred to and never copied into
    the figures."""
    rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        with open_catalog(project, run_id=run_id) as catalog:
            started = catalog.query("SELECT started_at FROM run").fetchone()
        figures = figures_of(project, run_id)
        rows.append(
            {
                "project": name,
                "run": run_id,
                "phase": figures[0].phase if figures else None,
                "started_at": started[0] if started else None,
                **versions_of(project, run_id),
                "record_answers": _answers_in_force(project, run_id),
                "settled_claims": _settled_claims(figures),
                "wall_seconds": _wall_seconds(figures),
            }
        )
    return rows


def _answers_in_force(project: Path, run_id: str) -> int | None:
    """The answers in the answer log that the run had read: the decisions in force at the
    sequence its record states, withdrawn ones and notes (which decide nothing) left out. None for
    a run that read no answer log, which is not an empty one."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.answers import events_at

    with open_catalog(project, run_id=run_id) as catalog:
        row = catalog.query("SELECT record_watermark FROM run").fetchone()
    if row is None or row[0] is None:
        return None
    return sum(
        1 for event in events_at(project, int(row[0])) if event.kind is not RecordEventKind.NOTE
    )


def _settled_claims(figures: Sequence[QualityFigure]) -> int | None:
    """The claims the run concluded and no defeater put in conflict: the ``resolved`` count of
    its claims-by-standing figure. None where the run's pass did not conclude claims."""
    for figure in figures:
        if (
            figure.figure == "conclusions.claims_standing"
            and figure.facet == "resolved"
            and figure.state is QualityState.COUNTED
        ):
            return figure.count
    return None


def _wall_seconds(figures: Sequence[QualityFigure]) -> float | None:
    """The run's wall time in seconds, from its stored figure, or None where it is not recorded."""
    for figure in figures:
        if (
            figure.figure == "cost.wall_time"
            and figure.state is QualityState.COUNTED
            and figure.duration_ns is not None
        ):
            return figure.duration_ns / NS
    return None


def _csv(columns: Sequence[str], rows: Sequence[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row[name] is None else row[name] for name in columns])
    return buffer.getvalue()


def table_paths(directory: Path) -> tuple[Path, Path, Path]:
    """The three files an export writes into ``directory``: the figures as CSV and as JSON, and
    the runs. Named here once, so a command asks about every one before it writes any."""
    return directory / "quality.csv", directory / "quality.json", directory / "quality_runs.csv"


def write_tables(
    directory: Path, rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]]
) -> tuple[Path, Path, Path]:
    """Write the three files into ``directory``, each whole or not there, and return their paths.

    The caller has asked ``core.destinations`` whether the folder may be written, and whether
    each file may be (:func:`table_paths`). Nothing here decides that.
    """
    directory.mkdir(parents=True, exist_ok=True)
    figures_file, json_file, runs_file = table_paths(directory)
    write_replacing(figures_file, _csv(COLUMNS, rows))
    write_replacing(
        json_file,
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "columns": list(COLUMNS),
                "runs": list(runs),
                "figures": list(rows),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    write_replacing(runs_file, _csv(RUN_COLUMNS, runs))
    return figures_file, json_file, runs_file
