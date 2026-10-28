"""Publishing a run's quality figures, and reading them back.

``write_quality`` is called by the census, the dive, the inference and the remote readings after
the run's tables are compacted and before its manifest is built, so the figures describe the
tables a reader will open. It reads the staged run through a catalogue, runs every registered
summary, and writes one small table, ``quality_figures``, beside the others.

A figure is never a reason that a run is not published. A summary that fails is recorded as not
recorded, with the kind of error, and the run publishes.

The figures are counted from the run's own tables, so the run can be checked. ``recompute`` states
the figures of a published run again from its tables, and a test holds the stored rows equal to
them.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any

from ingence.core.schema import QualityFigure, QualityState
from ingence.quality.catalogue import catalogue, load_catalogue, reached, unreadable_catalogues
from ingence.quality.context import QualityContext
from ingence.quality.core import COLLECTOR_FIGURES, QUALITY_TIME_FIGURE, SUMMARIES_FIGURE
from ingence.quality.registry import catalogue_paths, summary_functions
from ingence.storage.catalog import open_catalog, open_staged
from ingence.storage.compact import compact_table
from ingence.storage.index_store import FragmentWriter
from ingence.storage.layout import table_for

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ingence.storage.catalog import Catalog

PEAK_FIGURE = "cost.peak_memory"

__all__ = ["collect", "recompute", "stored", "write_quality"]


def collect(
    catalog: Catalog,
    *,
    run_id: str,
    phase: str,
    extras: Mapping[str, Any] | None = None,
    timed: bool = False,
) -> list[QualityFigure]:
    """Every registered summary's figures for a run, with the ones its pass does not reach as such.

    Sorted by figure and breakdown. A figure a summary should have stated and did not is not
    recorded. One it cannot state because it failed is not recorded with the kind of failure.
    Nothing here can fail a publish. A summary that fails, and a catalogue the cached merge
    does not know (a summary switched on or off after the first read), are recorded as figures not
    recorded or left out, never raised.

    ``timed`` states the seconds each summary took as ``cost.quality_time``, one row for each;
    a run's figures stated again from its tables are not timed, and say so.
    """
    context = QualityContext(catalog, run_id, phase, dict(extras or {}))
    figures: list[QualityFigure] = []
    for name, reason in unreadable_catalogues().items():
        figures.append(context.not_recorded(SUMMARIES_FIGURE, reason, facet=name))
    paths = catalogue_paths()
    spent: dict[str, int] = {}
    for name, summary in summary_functions():
        if name in unreadable_catalogues():
            continue
        try:
            definitions = load_catalogue(paths[name]).figures
        except Exception:
            definitions = {}
        began = time.perf_counter_ns()
        try:
            stated = list(summary(context))
            failure = None
        except Exception as error:
            stated = []
            failure = f"the summary {name} failed: {type(error).__name__}"
        spent[name] = time.perf_counter_ns() - began
        figures.extend(stated)
        seen = {figure.figure for figure in stated}
        for figure, definition in definitions.items():
            if figure in seen or figure in COLLECTOR_FIGURES or figure not in catalogue():
                continue
            if not reached(definition.known_from, phase):
                figures.append(context.not_run(figure))
            else:
                figures.append(
                    context.not_recorded(figure, failure or f"the summary {name} stated nothing")
                )
    if QUALITY_TIME_FIGURE not in catalogue():
        pass
    elif timed:
        figures.extend(
            context.counted(QUALITY_TIME_FIGURE, facet=name, duration_ns=nanoseconds)
            for name, nanoseconds in spent.items()
        )
    else:
        figures.append(
            context.not_recorded(
                QUALITY_TIME_FIGURE, "figures stated again from the tables are not timed"
            )
        )
    return sorted(figures, key=lambda row: (row.figure, row.facet))


def write_quality(
    project: Path,
    staging: Path,
    *,
    run_id: str,
    phase: str,
    extras: Mapping[str, Any] | None = None,
) -> int:
    """Write a staged run's quality figures into its ``quality_figures`` table, and return the rows.

    Called once the staged tables are compacted. The table is written as one compacted file, so
    a run's directory is the same whichever pass wrote it. The engine's peak memory is read
    after the summaries have run, so what they used is in it. The seconds each took are a row of
    their own.
    """
    spec = table_for(QualityFigure)
    held = dict(extras or {})
    with open_staged(project, staging) as catalog:
        figures = collect(catalog, run_id=run_id, phase=phase, extras=held, timed=True)
    # The engine's own peak memory, read as the phase ends (no table holds it). Windows reports
    # current commit, not a peak, so there it is not recorded.
    peak = None if sys.platform == "win32" else _resident_peak()
    if peak is not None:
        figures = [
            row.model_copy(update={"state": QualityState.COUNTED, "peak_bytes": peak, "note": ""})
            if row.figure == PEAK_FIGURE
            else row
            for row in figures
        ]
    directory = staging / spec.directory
    directory.mkdir(parents=True, exist_ok=True)
    writer = FragmentWriter(spec, directory)
    writer.extend(figures, now=time.monotonic())
    writer.close(now=time.monotonic())
    return compact_table(directory, spec)


def recompute(
    project: Path, run_id: str, *, phase: str, extras: Mapping[str, Any] | None = None
) -> list[QualityFigure]:
    """State a published run's figures again, from its tables, as a pass would have."""
    with open_catalog(project, run_id=run_id) as catalog:
        return collect(catalog, run_id=run_id, phase=phase, extras=extras)


def stored(catalog: Catalog) -> list[QualityFigure]:
    """The quality figures a run stored, in their canonical order, or none for a run from an engine
    that kept none."""
    from ingence.storage.index_store import record_from_row

    return [
        record_from_row(row, QualityFigure)
        for rows in catalog.batches("SELECT * FROM quality_figures ORDER BY figure, facet")
        for row in rows
    ]


def _resident_peak() -> int | None:
    """The engine's own peak resident memory so far, by the reading the extraction supervisor
    uses on each platform; ``None`` where it cannot be read."""
    from ingence.executors.supervisor import _resident_bytes

    return _resident_bytes()
