"""What a quality summary is handed, and how it builds a figure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.schema import QualityFigure, QualityState
from ingence.quality.catalogue import catalogue, reached

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from ingence.storage.catalog import Catalog

__all__ = ["QualityContext"]


@dataclass(frozen=True, slots=True)
class QualityContext:
    """A run's tables, read, and what the pass that wrote it can add.

    ``catalog`` reads the run exactly as a reader does (the staged run before its pointer is
    swapped, or a published one). ``phase`` is the pass that wrote the run. ``extras`` holds
    what only the pass counted and no table keeps (an inference's tallies of rule outcomes),
    under names the summary that reads them documents. A summary treats a name it does not find
    as not recorded.
    """

    catalog: Catalog
    run_id: str
    phase: str
    extras: Mapping[str, Any] = field(default_factory=dict)

    def reaches(self, figure: str) -> bool:
        """Whether this run's pass is far enough along to state the figure."""
        return reached(catalogue().figures[figure].known_from, self.phase)

    def counted(
        self,
        figure: str,
        *,
        count: int | None = None,
        total: int | None = None,
        facet: str = "",
        duration_ns: int | None = None,
        peak_bytes: int | None = None,
    ) -> QualityFigure:
        """A counted figure: a count out of its whole, a duration or a peak, exactly as taken."""
        self._known(figure)
        return QualityFigure(
            run_id=self.run_id,
            phase=self.phase,
            figure=figure,
            facet=facet,
            state=QualityState.COUNTED,
            count=count,
            total=total,
            duration_ns=duration_ns,
            peak_bytes=peak_bytes,
        )

    def not_recorded(self, figure: str, note: str, *, facet: str = "") -> QualityFigure:
        """A figure the engine does not record, or this pass could not: no count, and the words."""
        self._known(figure)
        return QualityFigure(
            run_id=self.run_id,
            phase=self.phase,
            figure=figure,
            facet=facet,
            state=QualityState.NOT_RECORDED,
            note=note,
        )

    def not_run(self, figure: str) -> QualityFigure:
        """A figure this run's pass does not reach: nothing is said, in particular not zero."""
        self._known(figure)
        return QualityFigure(
            run_id=self.run_id, phase=self.phase, figure=figure, state=QualityState.NOT_RUN
        )

    def withheld(self, package: str, note: str) -> Iterator[QualityFigure]:
        """Every figure of a package (its ids begin ``package.``) as not recorded, with one reason.

        For a summary whose component is switched off, or whose input this pass cannot read: the
        figures keep their rows and say why, and none is counted as zero. A figure this run's
        pass does not reach is not run, as everywhere.
        """
        for figure in catalogue().figures:
            if not figure.startswith(f"{package}."):
                continue
            yield self.not_recorded(figure, note) if self.reaches(figure) else self.not_run(figure)

    @staticmethod
    def _known(figure: str) -> None:
        if figure not in catalogue():
            message = f"quality figure {figure!r} is in no catalogue, so it has no method"
            raise KeyError(message)
