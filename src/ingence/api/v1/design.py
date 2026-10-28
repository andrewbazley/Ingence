"""``/api/v1/design`` and ``/api/v1/experiments/design``: the design grid of each experiment.

This route is a projection of the pinned run (:mod:`ingence.design`), computed when read and
written nowhere. It comes from the experiment's condition classes, the parts of its files' names
and the run's conclusions about the files.

- ``design``: every experiment of the run, one compact row each (does it have a grid, how many
  factors, how full), and the run's summary;
- ``experiments/design?experiment=<entry id>``: one experiment's grid whole: its factors with their
  levels, its cells, how each pair of factors lies, its findings and its coverage.

Every count is what the engine found in the names it read. A combination "not found" is a
combination that no file of the experiment holds. It may still exist. The engine did not find it,
and the finding asks a person whether it was not run or is somewhere the engine did not read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.design import DesignSummary, RunDesign
    from ingence.design.grid import ExperimentDesign

__all__ = ["DesignResponse", "ExperimentDesignResponse", "design", "experiment_design"]

_FOUND = "found by the engine in the file names it read (not every file that exists)"


class DesignLevel(ApiModel):
    value: str = Field(description="The level as the run normalised it.")
    shown: str = Field(description="The level as most files write it.")
    files: Count = Field(description=f"Files {_FOUND} at this level.")


class DesignLadder(ApiModel):
    kind: Literal["ratio", "difference"] = Field(
        description="Whether each level is a constant multiple, or a constant step, from the last."
    )
    step: float = Field(description="The multiple, or the step in the dimension's base unit.")
    said: str


class DesignMerge(ApiModel):
    position: Count = Field(description="Where the merged part stands in the name (1-based).")
    kind: str
    name: str
    observations: Count = Field(description="Files the pairing was read over.")
    exceptions: Count = Field(description="Files off the pairing.")
    examples: list[str] = Field(description="Names of up to three files off the pairing.")
    said: str = Field(description="For example: one exception in 14.")


class DesignFactor(ApiModel):
    id: str = Field(description="p<position>, the positions joined by + for merged parts.")
    positions: list[Count] = Field(
        description="Where the part, and any part merged into it, stands in the name (1-based)."
    )
    kind: str = Field(description="The component type: word, quantity, number, indexed.")
    part_kind: str = Field(description="In name_pattern's words: variant, quantity, number.")
    name: str = Field(description="What a page calls it: a dimension, a sense, or the kind.")
    order: Literal["numeric", "alphabetical"] = Field(
        description="How the levels are ordered: numerically where they are numbers."
    )
    levels: list[DesignLevel]
    ladder: DesignLadder | None = Field(
        description=(
            "Set where the levels rise by a constant ratio or step: a label on the levels, never "
            "an order in time."
        )
    )
    merged: list[DesignMerge] = Field(description="Parts that always go with this one.")


class DesignStage(ApiModel):
    stage: str
    files: Count


class DesignCell(ApiModel):
    levels: list[Count] = Field(description="One index per factor, into that factor's levels.")
    files: Count
    replicates: Count = Field(description="How many times the combination was repeated.")
    stages: list[DesignStage]
    state: Literal["occupied", "not found", "empty output"] = Field(
        description=(
            "occupied: files found; not found: no file of the experiment holds this combination; "
            "empty output: every file found is an empty output."
        )
    )


class DesignPair(ApiModel):
    first: str
    second: str
    relation: Literal["crossed", "nested", "paired", "partly crossed"]
    nested_in: str | None
    found: Count = Field(description="Combinations of the two found.")
    possible: Count = Field(description="Combinations their levels allow.")
    said: str


class DesignFinding(ApiModel):
    kind: Literal["combination_not_found"]
    combination: list[str]
    question: str = Field(description="A question for a person, never a statement.")


class DesignLeftOut(ApiModel):
    reason: str
    said: str
    files: Count


class DesignAbstention(ApiModel):
    reason: str
    said: str
    experiments: Count = Field(description="Experiments with no grid for this reason.")


class DesignOtherLayout(ApiModel):
    pattern: str = Field(description="The files' names written once.")
    files: Count


class DesignSeriesRefusal(ApiModel):
    reason: str
    said: str
    classes: Count = Field(description="Classes of replicates the series rule would not order.")


class DesignCoverage(ApiModel):
    files: Count = Field(description=f"Files {_FOUND} in the experiment.")
    in_grid: Count
    left_out: list[DesignLeftOut]


class DesignSettings(ApiModel):
    enabled: bool
    merge_parts: bool
    merge_minimum_observations: Count
    merge_exception_share: float
    cells_listed_at_most: Count


class ExperimentDesignRow(ApiModel):
    """One experiment's design, whole."""

    id: Identifier = Field(description="The experiment folder's entry.")
    folder: str
    root_id: Identifier
    has_grid: bool
    reason: str = Field(description="Why there is no grid, in words; empty where there is one.")
    reason_code: str = Field(description="The code of that reason; empty where there is a grid.")
    pattern: str = Field(description="The grid's file names written once.")
    pattern_kinds: list[str]
    factors: list[DesignFactor]
    cells: list[DesignCell]
    pairs: list[DesignPair]
    findings: list[DesignFinding]
    coverage: DesignCoverage
    not_found_listed: bool
    not_found_said: str
    fill: float | None = Field(
        description=(
            "Combinations found over combinations the levels allow; null for one factor or for a "
            "nested design."
        )
    )
    merged_parts: Count
    replicate_positions: list[Count] = Field(
        description="Where the name counts repeats (the replicate axis), 1-based."
    )
    other_layouts: list[DesignOtherLayout] = Field(
        description="Files laid out unlike the grid's, the largest set first: left out of it."
    )
    said: str


@register("experiment_design")
class ExperimentDesignResponse(ApiModel):
    """One experiment's design grid, drawn from its files' names."""

    schema_version: str
    run_id: Identifier
    enabled: bool
    settings: DesignSettings
    experiment: ExperimentDesignRow | None = Field(
        description="Null where the grid is switched off by design.enabled."
    )
    notes: list[str]


class DesignSummaryModel(ApiModel):
    experiments: Count
    with_grid: Count
    factors_per_grid: list[list[Count]]
    median_factors: float | None
    fill_grids: Count
    median_fill: float | None
    pairs: Count
    crossed_pairs: Count
    crossed_share: float | None
    files: Count
    files_in_grid: Count
    left_out: list[DesignLeftOut]
    abstentions: list[DesignAbstention]
    merged_parts: Count
    grids_with_merge: Count
    findings: Count
    series_classes: Count = Field(
        description="Classes of two or more replicates the series rule would read."
    )
    series_ordered: Count = Field(description="Of those, the classes it would put in order.")
    series_not_ordered: list[DesignSeriesRefusal] = Field(
        description="The rest, by why not. The rule is shipped off; this is what it would do."
    )


class DesignOverviewRow(ApiModel):
    id: Identifier
    folder: str
    has_grid: bool
    factors: Count
    cells: Count
    fill: float | None
    findings: Count
    in_grid: Count
    files: Count
    reason: str


@register("design")
class DesignResponse(ApiModel):
    """Every experiment's design in the pinned run: one compact row each, and the summary."""

    schema_version: str
    run_id: Identifier
    enabled: bool
    settings: DesignSettings
    summary: DesignSummaryModel
    experiments: list[DesignOverviewRow]
    notes: list[str]


NOTES = (
    "Every count is what the engine found in the file names it read; a combination not found is "
    "one no file holds, which is not a statement that it was never run.",
    "A date, a clock, an extension and a version are never factors; a number that counts "
    "repeats is the replicate axis.",
    "A dose ladder is a label on a factor's levels, never an order in time.",
)


def _relation_said(relation: str, found: int, possible: int) -> str:
    if relation == "crossed":
        return f"every combination found ({found} of {possible})"
    if relation == "nested":
        return f"each level of one lies under a single level of the other ({found} combinations)"
    if relation == "paired":
        return f"the levels match one to one ({found} combinations)"
    return f"{found} of {possible} combinations found; {possible - found} not found"


def _ladder_said(kind: str, step: float) -> str:
    if kind == "ratio":
        return f"a ladder: each level is {step:g} times the one before"
    return "a ladder: the levels rise in equal steps"


def _said(design: ExperimentDesign) -> str:
    if not design.has_grid:
        return f"No grid: {design.reason}."
    names = " by ".join(factor.name for factor in design.factors)
    cells = [cell for cell in design.cells if cell.state != "not found"]
    return (
        f"{len(design.factors)} {'factor' if len(design.factors) == 1 else 'factors'} ({names}); "
        f"{len(cells)} {'combination' if len(cells) == 1 else 'combinations'} found, "
        f"{design.coverage.in_grid} of {design.coverage.files} files in the grid."
    )


def experiment_row(design: ExperimentDesign) -> ExperimentDesignRow:
    from ingence.design.grid import REASONS

    return ExperimentDesignRow(
        id=design.experiment_id,
        folder=design.folder,
        root_id=design.root_id,
        has_grid=design.has_grid,
        reason=design.reason,
        reason_code=design.reason_code,
        pattern=design.pattern,
        pattern_kinds=list(design.pattern_kinds),
        factors=[
            DesignFactor(
                id=factor.factor_id,
                positions=list(factor.positions),
                kind=factor.kind,
                part_kind=factor.part_kind,
                name=factor.name,
                order="numeric" if factor.order == "numeric" else "alphabetical",
                levels=[
                    DesignLevel(value=level.value, shown=level.shown, files=level.files)
                    for level in factor.levels
                ],
                ladder=(
                    None
                    if factor.ladder is None
                    else DesignLadder(
                        kind="ratio" if factor.ladder.kind == "ratio" else "difference",
                        step=factor.ladder.step,
                        said=_ladder_said(factor.ladder.kind, factor.ladder.step),
                    )
                ),
                merged=[
                    DesignMerge(
                        position=note.position,
                        kind=note.kind,
                        name=note.name,
                        observations=note.observations,
                        exceptions=note.exceptions,
                        examples=list(note.examples),
                        said=note.said,
                    )
                    for note in factor.merged
                ],
            )
            for factor in design.factors
        ],
        cells=[
            DesignCell(
                levels=list(cell.levels),
                files=cell.files,
                replicates=cell.replicates,
                stages=[DesignStage(stage=stage, files=files) for stage, files in cell.stages],
                state=(
                    "not found"
                    if cell.state == "not found"
                    else "empty output"
                    if cell.state == "empty output"
                    else "occupied"
                ),
            )
            for cell in design.cells
        ],
        pairs=[
            DesignPair(
                first=pair.first,
                second=pair.second,
                relation=(
                    "crossed"
                    if pair.relation == "crossed"
                    else "nested"
                    if pair.relation == "nested"
                    else "paired"
                    if pair.relation == "paired"
                    else "partly crossed"
                ),
                nested_in=pair.nested_in,
                found=pair.found,
                possible=pair.possible,
                said=_relation_said(pair.relation, pair.found, pair.possible),
            )
            for pair in design.pairs
        ],
        findings=[
            DesignFinding(
                kind="combination_not_found",
                combination=list(finding.combination),
                question=finding.question,
            )
            for finding in design.findings
        ],
        coverage=DesignCoverage(
            files=design.coverage.files,
            in_grid=design.coverage.in_grid,
            left_out=[
                DesignLeftOut(reason=reason, said=REASONS.get(reason, reason), files=files)
                for reason, files in design.coverage.left_out
            ],
        ),
        not_found_listed=design.not_found_listed,
        not_found_said=design.not_found_said,
        fill=design.fill,
        merged_parts=design.merged_parts,
        replicate_positions=list(design.replicate_positions),
        other_layouts=[
            DesignOtherLayout(pattern=item.pattern, files=item.files)
            for item in design.other_layouts
        ],
        said=_said(design),
    )


def settings_model(run: RunDesign) -> DesignSettings:
    held = run.settings
    return DesignSettings(
        enabled=held.enabled,
        merge_parts=held.merge_parts,
        merge_minimum_observations=held.merge_minimum_observations,
        merge_exception_share=held.merge_exception_share,
        cells_listed_at_most=held.cells_listed_at_most,
    )


def summary_model(summary: DesignSummary) -> DesignSummaryModel:
    from ingence.design import NO_GRID_REASONS
    from ingence.design.grid import REASONS
    from ingence.rulebook.predicates.series import REASONS as SERIES_REASONS

    return DesignSummaryModel(
        experiments=summary.experiments,
        with_grid=summary.with_grid,
        factors_per_grid=[list(item) for item in summary.factors_per_grid],
        median_factors=summary.median_factors,
        fill_grids=summary.fill_grids,
        median_fill=summary.median_fill,
        pairs=summary.pairs,
        crossed_pairs=summary.crossed_pairs,
        crossed_share=summary.crossed_share,
        files=summary.files,
        files_in_grid=summary.files_in_grid,
        left_out=[
            DesignLeftOut(reason=reason, said=REASONS.get(reason, reason), files=files)
            for reason, files in summary.left_out
        ],
        abstentions=[
            DesignAbstention(
                reason=reason, said=NO_GRID_REASONS.get(reason, reason), experiments=count
            )
            for reason, count in summary.abstentions
        ],
        merged_parts=summary.merged_parts,
        grids_with_merge=summary.grids_with_merge,
        findings=summary.findings,
        series_classes=summary.series_classes,
        series_ordered=summary.series_ordered,
        series_not_ordered=[
            DesignSeriesRefusal(reason=code, said=SERIES_REASONS.get(code, code), classes=count)
            for code, count in summary.series_not_ordered
        ],
    )


def _run(context: V1Context) -> str:
    from http import HTTPStatus

    if context.run_pinned is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    return context.run_pinned


def design_overview(run: RunDesign) -> DesignResponse:
    from ingence import design as engine

    summary = engine.summarise(run)
    return DesignResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run.run_id,
        enabled=run.enabled,
        settings=settings_model(run),
        summary=summary_model(summary),
        experiments=[
            DesignOverviewRow(
                id=item.experiment_id,
                folder=item.folder,
                has_grid=item.has_grid,
                factors=len(item.factors),
                cells=sum(1 for cell in item.cells if cell.state != "not found"),
                fill=item.fill,
                findings=len(item.findings),
                in_grid=item.coverage.in_grid,
                files=item.coverage.files,
                reason=item.reason,
            )
            for item in run.experiments
        ],
        notes=list(NOTES),
    )


@route("design", model=DesignResponse)
def design(context: V1Context, query: Mapping[str, Sequence[str]]) -> DesignResponse:
    """Every experiment of the pinned run: whether it has a grid, and the run's summary."""
    del query
    from ingence.design import design_of_run

    return design_overview(design_of_run(context.project, _run(context)))


@route("experiments/design", model=ExperimentDesignResponse)
def experiment_design(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> ExperimentDesignResponse:
    """One experiment's design grid, whole."""
    from http import HTTPStatus

    from ingence.design import design_of_run

    named = (one(query, "experiment") or "").strip()
    if not named:
        message = "name the experiment by its folder's identifier: experiment=<entry id>"
        raise ApiError(message, parameter="experiment")
    run = design_of_run(context.project, _run(context), experiment=named)
    if run.enabled and not run.experiments:
        message = f"no experiment {named} in this run"
        raise ApiError(
            message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="experiment"
        )
    return ExperimentDesignResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run.run_id,
        enabled=run.enabled,
        settings=settings_model(run),
        experiment=experiment_row(run.experiments[0]) if run.experiments else None,
        notes=list(NOTES),
    )
