"""The design of each experiment: a grid of its conditions, read from its files' names.

The grid is a projection, never a claim. It is recomputed from a published run and the settings
when it is read, and never becomes a Record or Index row. It is an optional component: the engine
reaches it only through :data:`DESIGN_ENGINES`. So removing it means deleting this package, its
routes module's line in ``api/v1/__init__.py`` and the settings group.

Per experiment: factors (a part of the names that is a condition, with its levels in numeric
order where they are numbers, a ladder labelled where they rise by a constant ratio or step),
cells (a combination of levels with its replicate count, its files by stage and its state:
occupied, ``not found`` or ``empty output``), whether each pair of factors is crossed or
nested, parts that go together merged into one factor with the exceptions counted, and
coverage: the experiment's files in the grid and the files left out, with the reason. Where
every other combination was found and one was not, a finding, worded as a question.

Dates, clocks and counters are never factors (groups are made of words). A counter that counts
is the replicate axis. The same run and settings give the same grid byte for byte.

The functions another part of the engine calls: :func:`design_of_run` and :func:`design_summary`
(the numbers the quality figures take).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ingence.core.registry import Registry
from ingence.design.grid import REASONS, ExperimentDesign, GridParameters, build_design
from ingence.rulebook.predicates.series import (
    SeriesRow,
    counting_kinds,
    read_rows,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ingence.core.settings import DesignSettings
    from ingence.storage.catalog import Catalog

__all__ = [
    "DESIGN_ENGINES",
    "DesignEngine",
    "DesignSummary",
    "RunDesign",
    "design_of_run",
    "design_summary",
    "grid_parameters",
    "run_design_of_catalog",
]

#: The engine registered by default.
DEFAULT_ENGINE = "name_grid"


@dataclass(frozen=True, slots=True)
class RunDesign:
    """One run's design: every experiment's, or one's, and whether the grid is switched off."""

    run_id: str
    enabled: bool
    settings: DesignSettings
    experiments: tuple[ExperimentDesign, ...]
    #: Classes of replicates the series rule would read (two files or more), how many it would
    #: put in order, and the rest by why not (a sorted tuple of (reason code, classes)).
    series_classes: int = 0
    series_ordered: int = 0
    series_not_ordered: tuple[tuple[str, int], ...] = ()


class DesignEngine(Protocol):
    """Draws the design grids of a run."""

    version: str

    def projection(
        self,
        project: Path,
        run_id: str,
        settings: DesignSettings | None = None,
        experiment: str | None = None,
    ) -> RunDesign:
        """The run's design: every experiment's, or the named experiment's alone."""
        ...


#: Every design engine, reachable only through here.
DESIGN_ENGINES: Registry[DesignEngine] = Registry("design_engine")


def grid_parameters(settings: DesignSettings) -> GridParameters:
    """The settings ``design.*`` as the grid's arguments."""
    return GridParameters(
        merge_parts=settings.merge_parts,
        merge_minimum_observations=settings.merge_minimum_observations,
        merge_exception_share=settings.merge_exception_share,
        cells_listed_at_most=settings.cells_listed_at_most,
        include_unclassed=settings.include_unclassed,
        factor_levels_at_most=settings.factor_levels_at_most,
    )


class _NameGrid:
    """The grid read from names: condition classes, name layouts, parts of one layout."""

    version = "1"

    def projection(
        self,
        project: Path,
        run_id: str,
        settings: DesignSettings | None = None,
        experiment: str | None = None,
    ) -> RunDesign:
        from ingence.core.project import Project
        from ingence.storage.catalog import open_catalog

        held = settings if settings is not None else Project.open(project).settings.design
        if not held.enabled:
            return RunDesign(run_id=run_id, enabled=False, settings=held, experiments=())
        with open_catalog(project, run_id=run_id) as catalog:
            return run_design_of_catalog(catalog, run_id, held, experiment)


def run_design_of_catalog(
    catalog: Catalog, run_id: str, held: DesignSettings, experiment: str | None = None
) -> RunDesign:
    """The design of a run read through a catalog already open, which may be a run staged and
    not yet published (the quality figures read it so). ``held`` is the settings in force."""
    from ingence.design.read import experiment_files_of

    if not held.enabled:
        return RunDesign(run_id=run_id, enabled=False, settings=held, experiments=())
    parameters = grid_parameters(held)
    read = experiment_files_of(catalog, only=None if experiment is None else [experiment])
    classes, ordered, refused = _series_counts(read)
    return RunDesign(
        run_id=run_id,
        enabled=True,
        settings=held,
        series_classes=classes,
        series_ordered=ordered,
        series_not_ordered=refused,
        experiments=tuple(
            build_design(
                item.experiment_id,
                item.root_id,
                item.folder,
                item.files,
                programs=item.programs,
                parameters=parameters,
            )
            for item in read
        ),
    )


def _series_counts(read: Sequence[Any]) -> tuple[int, int, tuple[tuple[str, int], ...]]:
    """What the series rule would make of each class of replicates, from the same reading the
    rule uses (:func:`~ingence.rulebook.predicates.series.read_rows`): classes read, classes put
    in order, and the others by reason. The rule is shipped off. This says what it would do."""
    kinds = counting_kinds("indexed")
    held: dict[str, list[SeriesRow]] = {}
    for item in read:
        for file in item.files:
            if file.class_key is None:
                continue
            parts = tuple((part.kind, part.value, part.shown) for part in file.parts)
            held.setdefault(file.class_key, []).append(
                SeriesRow(file.entry_id, parts, file.clock_ns)
            )
    ordered = 0
    refused: dict[str, int] = {}
    classes = 0
    for key in sorted(held):
        rows = held[key]
        if len(rows) < 2:
            continue
        classes += 1
        reading = read_rows(rows, kinds) if all(row.parts for row in rows) else None
        if reading is not None and reading.orders:
            ordered += 1
        else:
            code = reading.code if reading is not None else "unrecorded"
            refused[code] = refused.get(code, 0) + 1
    return classes, ordered, tuple(sorted(refused.items()))


@DESIGN_ENGINES.register(
    DEFAULT_ENGINE,
    version="1",
    summary="A design grid per experiment from the condition classes and the parts of its names",
)
def _build_name_grid() -> DesignEngine:
    return _NameGrid()


def design_of_run(
    project: Path,
    run_id: str,
    settings: DesignSettings | None = None,
    experiment: str | None = None,
) -> RunDesign:
    """Each experiment's design in a published run, or the named experiment's alone.

    Reached through the registry. Raises ``ComponentNotAvailableError`` where the component is
    disabled, ``FileNotFoundError`` where the run is not a published run of the project.
    ``settings`` defaults to the project's own. Where ``design.enabled`` is off the result says
    so and holds no experiment.
    """
    return DESIGN_ENGINES.get(DEFAULT_ENGINE).projection(project, run_id, settings, experiment)


# ---------------------------------------------------------------------------
# The summary for the quality figures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DesignSummary:
    """What a run's grids came to: for the quality figures, counts with their denominators."""

    run_id: str
    enabled: bool
    #: Experiments the run holds (the denominator of everything below).
    experiments: int
    #: Experiments with a grid: at least one factor.
    with_grid: int
    #: Grids by their number of factors, sorted by that number.
    factors_per_grid: tuple[tuple[int, int], ...]
    median_factors: float | None
    #: Grids of two or more factors whose fill is defined (not nested), and their median fill:
    #: combinations found over combinations the levels allow.
    fill_grids: int
    median_fill: float | None
    #: Pairs of factors, and how many are crossed. ``crossed_share`` is None with no pair.
    pairs: int
    crossed_pairs: int
    crossed_share: float | None
    #: Files of all the experiments, those in a grid, and those left out by reason (a sorted
    #: tuple of (reason, files)).
    files: int
    files_in_grid: int
    left_out: tuple[tuple[str, int], ...]
    #: Experiments with no grid, by why (a sorted tuple of (reason, experiments)).
    abstentions: tuple[tuple[str, int], ...]
    #: Parts merged into another, and the grids where one was.
    merged_parts: int
    grids_with_merge: int
    #: Findings raised ("combination not found").
    findings: int
    #: Classes of replicates (two files or more) the series rule would read, how many it would
    #: put in order, and the rest by why not (a sorted tuple of (reason code, classes)). The rule
    #: is shipped off: this is what it would do, counted from the same reading it uses.
    series_classes: int = 0
    series_ordered: int = 0
    series_not_ordered: tuple[tuple[str, int], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The summary as plain data, in a fixed order."""
        return {
            "run_id": self.run_id,
            "enabled": self.enabled,
            "experiments": self.experiments,
            "with_grid": self.with_grid,
            "factors_per_grid": [list(item) for item in self.factors_per_grid],
            "median_factors": self.median_factors,
            "fill_grids": self.fill_grids,
            "median_fill": self.median_fill,
            "pairs": self.pairs,
            "crossed_pairs": self.crossed_pairs,
            "crossed_share": self.crossed_share,
            "files": self.files,
            "files_in_grid": self.files_in_grid,
            "left_out": [list(item) for item in self.left_out],
            "abstentions": [list(item) for item in self.abstentions],
            "merged_parts": self.merged_parts,
            "grids_with_merge": self.grids_with_merge,
            "findings": self.findings,
            "series_classes": self.series_classes,
            "series_ordered": self.series_ordered,
            "series_not_ordered": [list(item) for item in self.series_not_ordered],
        }


def summarise(run: RunDesign) -> DesignSummary:
    """The summary of a run's designs (:func:`design_summary` without the reading)."""
    grids = [design for design in run.experiments if design.has_grid]
    sizes: dict[int, int] = {}
    for design in grids:
        sizes[len(design.factors)] = sizes.get(len(design.factors), 0) + 1
    fills = [design.fill for design in grids if design.fill is not None]
    pairs = [pair for design in grids for pair in design.pairs]
    left: dict[str, int] = {}
    for design in run.experiments:
        for reason, files in design.coverage.left_out:
            left[reason] = left.get(reason, 0) + files
    absent: dict[str, int] = {}
    for design in run.experiments:
        if not design.has_grid:
            code = design.reason_code or "no_files"
            absent[code] = absent.get(code, 0) + 1
    crossed = sum(1 for pair in pairs if pair.relation == "crossed")
    return DesignSummary(
        run_id=run.run_id,
        enabled=run.enabled,
        experiments=len(run.experiments),
        with_grid=len(grids),
        factors_per_grid=tuple(sorted(sizes.items())),
        median_factors=(
            float(statistics.median(len(design.factors) for design in grids)) if grids else None
        ),
        fill_grids=len(fills),
        median_fill=float(statistics.median(fills)) if fills else None,
        pairs=len(pairs),
        crossed_pairs=crossed,
        crossed_share=crossed / len(pairs) if pairs else None,
        files=sum(design.coverage.files for design in run.experiments),
        files_in_grid=sum(design.coverage.in_grid for design in run.experiments),
        left_out=tuple(sorted(left.items())),
        abstentions=tuple(sorted(absent.items())),
        merged_parts=sum(design.merged_parts for design in grids),
        grids_with_merge=sum(1 for design in grids if design.merged_parts),
        findings=sum(len(design.findings) for design in grids),
        series_classes=run.series_classes,
        series_ordered=run.series_ordered,
        series_not_ordered=run.series_not_ordered,
    )


def design_summary(
    project: Path, run_id: str, settings: DesignSettings | None = None
) -> DesignSummary:
    """The numbers for the quality figures: experiments with a grid, factors per grid, the
    median fill, the share of factor pairs that are crossed, coverage, abstentions (experiments
    with no grid, by why) and how often parts were merged.

    Every count has its denominator beside it (``experiments``, ``pairs``, ``files``), and a
    share or median is None where nothing was counted, never 0.
    """
    return summarise(design_of_run(project, run_id, settings))


#: The reasons an experiment has no grid, in words (a key of :attr:`DesignSummary.abstentions`).
NO_GRID_REASONS: dict[str, str] = dict(REASONS)


def as_json(run: RunDesign) -> str:
    """A run's design as canonical JSON (sorted keys): what two inferences are compared by."""
    from dataclasses import asdict

    return json.dumps(
        {
            "enabled": run.enabled,
            "experiments": [asdict(design) for design in run.experiments],
        },
        sort_keys=True,
        default=str,
    )
