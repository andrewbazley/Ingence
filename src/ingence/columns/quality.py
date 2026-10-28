"""The same-columns summary for the quality figures.

Registered in :mod:`ingence.quality.registry`, which reaches this module only when a run's figures
are counted. The figures are counted from what a dive stored (the digest of each table's column
names, their order and the delimiter, and its column count). They come through the same projection
the file page reads (:func:`~ingence.columns.stored.projection_of_catalog`), so a figure equals the
value :func:`ingence.columns.same_column_summary` gives for the published run. The module does not
read the rule's claims, so the figures are the same where the rule is switched off.

A pass that reads no content (a census, a listing) does not reach these figures. A component that
is switched off, or a run whose tables cannot be read, gives the figures as not recorded with the
reason, never as zero.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.registry import ComponentNotAvailableError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ingence.core.schema import QualityFigure
    from ingence.quality.context import QualityContext

__all__ = ["summarise"]

PACKAGE = "columns"


def summarise(context: QualityContext) -> Iterator[QualityFigure]:
    """The same-columns figures of a run a dive has read; none for a pass that has not."""
    from ingence.columns import column_engine
    from ingence.columns.stored import projection_of_catalog, summary_of

    if context.phase == "listing":
        yield from context.withheld(PACKAGE, "a listing reads no file's columns")
        return
    if not context.reaches("columns.tables_counted"):
        return
    try:
        column_engine()
    except ComponentNotAvailableError:
        yield from context.withheld(PACKAGE, "the same-columns component is switched off")
        return
    built = projection_of_catalog(context.catalog, context.run_id)
    summary = summary_of(built)
    counted = summary.tables_counted
    if counted > summary.tables_in_run:
        # More tables read than files with a table extension: the whole would be wrong.
        yield from context.withheld(PACKAGE, "more tables were read than the run has table files")
        return
    layouts = len(built.classes)
    yield context.counted("columns.tables_counted", count=counted, total=summary.tables_in_run)
    yield context.counted("columns.layouts", count=layouts, total=counted)
    yield context.counted("columns.classes", count=summary.classes, total=layouts)
    yield context.counted(
        "columns.tables_in_classes", count=summary.tables_in_classes, total=counted
    )
    yield context.counted(
        "columns.classes_spanning_folders",
        count=summary.classes_spanning_folders,
        total=summary.classes,
    )
