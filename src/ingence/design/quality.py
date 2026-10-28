"""The design-grid summary for the quality figures.

Registered in :mod:`ingence.quality.registry`, which reaches this module only when a run's figures
are counted. The figures are the numbers :func:`ingence.design.design_summary` gives for the
published run, taken here from the run staged and not yet published (the same reading, through
:func:`~ingence.design.run_design_of_catalog`), each with its whole beside it.

Counts, wholes and one median. A share is not stored: the crossed share is the crossed pairs
over all pairs. The middle number of factors is read from the grids by factors. The median fill is
the one value that is a fraction. It is stated as that many of 1,000 (a count with its whole, so a
page draws it as a bar) and is not recorded where no grid has a fill. The number of parts merged
(as against the grids where parts were merged) and the findings have no whole in the summary, and
are not stated here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.registry import ComponentNotAvailableError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ingence.core.schema import QualityFigure
    from ingence.design import DesignSummary
    from ingence.quality.context import QualityContext

__all__ = ["MEDIAN_FILL_WHOLE", "figures_of_summary", "summarise"]

PACKAGE = "design"

#: The whole a median fill is stated out of.
MEDIAN_FILL_WHOLE = 1000


def _breakdown(
    context: QualityContext,
    figure: str,
    held: tuple[tuple[str, int], ...],
    *,
    total: int,
    closed: bool,
) -> Iterator[QualityFigure]:
    """A breakdown's rows: every listed value of a closed one (zero where none), and any other
    value found, so a new reason is never dropped."""
    from ingence.quality.catalogue import catalogue

    counts = dict(held)
    listed = list(catalogue().figures[figure].facets) if closed else []
    for facet in [*listed, *sorted(set(counts) - set(listed))]:
        yield context.counted(figure, facet=facet, count=counts.get(facet, 0), total=total)


def figures_of_summary(context: QualityContext, summary: DesignSummary) -> Iterator[QualityFigure]:
    """The figures a design summary gives, each with its whole."""
    yield context.counted("design.with_grid", count=summary.with_grid, total=summary.experiments)
    if not summary.factors_per_grid:
        yield context.not_recorded("design.factors_per_grid", "no experiment has a grid")
    for factors, grids in summary.factors_per_grid:
        yield context.counted(
            "design.factors_per_grid", facet=str(factors), count=grids, total=summary.with_grid
        )
    yield context.counted(
        "design.grids_with_fill", count=summary.fill_grids, total=summary.with_grid
    )
    if summary.median_fill is None:
        yield context.not_recorded(
            "design.median_fill",
            "no grid has a fill: none has two or more factors that are not nested",
        )
    else:
        yield context.counted(
            "design.median_fill",
            count=round(summary.median_fill * MEDIAN_FILL_WHOLE),
            total=MEDIAN_FILL_WHOLE,
        )
    yield context.counted("design.crossed_pairs", count=summary.crossed_pairs, total=summary.pairs)
    yield context.counted("design.files_in_grid", count=summary.files_in_grid, total=summary.files)
    yield from _breakdown(
        context, "design.files_left_out", summary.left_out, total=summary.files, closed=True
    )
    yield from _breakdown(
        context, "design.no_grid", summary.abstentions, total=summary.experiments, closed=True
    )
    yield context.counted(
        "design.grids_with_merge", count=summary.grids_with_merge, total=summary.with_grid
    )
    yield context.counted(
        "design.series_ordered", count=summary.series_ordered, total=summary.series_classes
    )
    yield from _breakdown(
        context,
        "design.series_not_ordered",
        summary.series_not_ordered,
        total=summary.series_classes,
        closed=True,
    )


def summarise(context: QualityContext) -> Iterator[QualityFigure]:
    """The design figures of a run an inference has written, or none for a pass that has not."""
    from ingence.core.project import Project
    from ingence.design import (
        DEFAULT_ENGINE,
        DESIGN_ENGINES,
        run_design_of_catalog,
    )
    from ingence.design import (
        summarise as summed,
    )

    if not context.reaches("design.with_grid"):
        return
    try:
        DESIGN_ENGINES.get(DEFAULT_ENGINE)
    except ComponentNotAvailableError:
        yield from context.withheld(PACKAGE, "the design grid component is switched off")
        return
    settings = Project.open(context.catalog.project).settings.design
    if not settings.enabled:
        yield from context.withheld(PACKAGE, "the design grid is off")
        return
    run = run_design_of_catalog(context.catalog, context.run_id, settings)
    yield from figures_of_summary(context, summed(run))
