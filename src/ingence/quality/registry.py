"""The registry of quality summaries, and how to register one.

A summary is a function that, given a run's tables, states some quality figures of it. The core
registers the first (``core``: what a census found, a dive read, the names, the conclusions, the
cost). Others register theirs the way the engine's other registries are filled: a line here whose
factory imports the module lazily, so the engine starts without it and a removed module is a
deleted line.

To register a summary:

1. Write the function in your package: ``def summarise(context: QualityContext) ->
   Iterable[QualityFigure]``. Count only what the run's tables hold or the pass counted exactly;
   state a figure the pass cannot count as not recorded, never as zero. Build each figure with
   ``context.counted(...)`` or ``context.not_recorded(...)``.
2. Write its catalogue beside the module, in the shape of ``catalogue.yaml`` (label, unit, the
   pass it is known from, what it is out of, a one-line method), and name its figures
   ``yourpackage.name``.
3. Add the registration below, with ``requires`` naming your package, ``attributes`` naming the
   catalogue as a path under the ``ingence`` package, and a factory that imports your function.
4. Add a line to the packaging artifacts for your catalogue if its folder is not already there.

Nothing else changes: the census, the dive and the inference publish every registered summary's
figures with the run, and the manifest, the History page and the export read them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ingence.core.registry import ComponentNotAvailableError, Registry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ingence.core.schema import QualityFigure
    from ingence.quality.context import QualityContext

__all__ = ["SUMMARIES", "Summary", "catalogue_paths", "summary_functions", "unavailable"]

type Summary = Callable[[QualityContext], Iterable[QualityFigure]]

#: Quality summaries, reached through the registry: nothing calls one by name.
SUMMARIES: Registry[Summary] = Registry("quality_summary")

#: The attribute naming a summary's catalogue, as a path under the ``ingence`` package.
CATALOGUE_ATTRIBUTE = "catalogue"


@SUMMARIES.register(
    "core",
    version="1",
    summary="what a run found, read, understood of names and concluded, and what it cost",
    attributes={CATALOGUE_ATTRIBUTE: "quality/catalogue.yaml"},
)
def _core() -> Summary:
    from ingence.quality.core import summarise

    return summarise


@SUMMARIES.register(
    "columns",
    version="1",
    summary="tables with the same columns, counted from the digests a dive stored",
    requires=("ingence.columns",),
    attributes={CATALOGUE_ATTRIBUTE: "columns/quality.yaml"},
)
def _columns() -> Summary:
    from ingence.columns.quality import summarise

    return summarise


@SUMMARIES.register(
    "design",
    version="1",
    summary="the design grid of each experiment: factors, fill, crossing, coverage, abstentions",
    requires=("ingence.design",),
    attributes={CATALOGUE_ATTRIBUTE: "design/quality.yaml"},
)
def _design() -> Summary:
    from ingence.design.quality import summarise

    return summarise


@SUMMARIES.register(
    "themes",
    version="1",
    summary="how far the themes can be trusted: seed agreement, connectivity, placement, cutoffs",
    requires=("ingence.themes",),
    attributes={CATALOGUE_ATTRIBUTE: "themes/quality.yaml"},
)
def _themes() -> Summary:
    from ingence.themes.quality import summarise

    return summarise


def catalogue_paths() -> dict[str, Path]:
    """The catalogue file of every enabled summary, by the summary's name, in canonical order."""
    root = Path(__file__).resolve().parents[1]
    return {
        name: root / SUMMARIES.declaration(name).attributes[CATALOGUE_ATTRIBUTE]
        for name in SUMMARIES.names(enabled_only=True)
    }


def summary_functions() -> tuple[tuple[str, Summary], ...]:
    """Each enabled summary that can be built, by name. One that cannot is left out here.

    A summary that cannot be built (its package absent) is reported by :func:`unavailable`
    rather than failing a run: quality figures are never a reason a run is not published.
    """
    found: list[tuple[str, Summary]] = []
    for name in SUMMARIES.names(enabled_only=True):
        try:
            found.append((name, SUMMARIES.get(name)))
        except ComponentNotAvailableError:
            continue
    return tuple(found)


def unavailable() -> dict[str, str]:
    """Why each enabled summary that cannot be built cannot, by name."""
    reasons: dict[str, str] = {}
    for name in SUMMARIES.names(enabled_only=True):
        try:
            SUMMARIES.get(name)
        except ComponentNotAvailableError as error:
            reasons[name] = str(error)
    return reasons
