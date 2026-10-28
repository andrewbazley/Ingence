"""``GET /api/v1/overlap?mode=connections|by_year``: how experiments relate.

Two readings of one run, one model with the mode and two optional sections:

- ``connections``: every experiment as a node (its area and the area's kind, its theme, its
  first and last date, how complete it is, how many repeats it has) and an edge for each pair
  that overlaps, with why (shared words, the same workflow, the same instrument, a repeat, a
  copy, made from) and the evidence for each why;
- ``by_year``: the themes over time, the year profile and the words handed on, as
  ``themes/years`` answers them (its item models, imported, never copied), and each area with
  its first and last year.

The mode is ``connections`` unless told, and any other word is refused. The route reads the pinned
run. The connections come from the completeness rows and the projection in
``grouping/connections.py``. The by-year section comes from the stored readouts. The invented
example in :mod:`ingence.api.v1.examples` stays for the tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import (
    ApiModel,
    AreaKindName,
    Count,
    Identifier,
    OverlapEdgeKindName,
    register,
)
from ingence.api.v1.shaping import one, run_required
from ingence.api.v1.themes import Substitute, ThemeOverTime, YearStory

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = [
    "OverlapArea",
    "OverlapByYear",
    "OverlapConnections",
    "OverlapEdge",
    "OverlapEvidence",
    "OverlapExperiment",
    "OverlapMode",
    "OverlapNode",
    "OverlapResponse",
]

OverlapMode = Literal["connections", "by_year"]
#: The modes, for the refusal's words.
MODES: tuple[OverlapMode, ...] = ("connections", "by_year")


class OverlapNode(ApiModel):
    """One experiment on the connections map."""

    experiment_id: Identifier = Field(description="The experiment folder's entry.")
    label: str = Field(description="The name a page shows for it.")
    area: str | None = Field(description="The area it is in; null where none.")
    area_kind: AreaKindName | None = Field(description="What the area's folder stands for.")
    theme: str | None = Field(description="The words of its theme; null where it is in none.")
    start: str | None = Field(description="Its first date, YYYY-MM-DD; null where undated.")
    end: str | None = Field(description="Its last date, YYYY-MM-DD; null where undated.")
    completeness: float | None = Field(
        description="Its raw files found followed over its raw files, 0 to 1; null where no raw."
    )
    repeats: Count = Field(description="Experiments read as repeats of it, itself included.")


#: What an evidence item's count counts.
OverlapEvidenceUnit = Literal["files", "words", "experiments"]


class OverlapEvidence(ApiModel):
    """Why an edge is drawn, for one kind."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    kind: OverlapEdgeKindName = Field(description="The kind of overlap this evidence is for.")
    words: list[str] = Field(description="The words behind it, where words are the evidence.")
    count: Count | None = Field(description="How many things back it; null where uncounted.")
    unit: OverlapEvidenceUnit | None = Field(
        default=None, description="What the count counts: files, words or experiments."
    )
    from_: Identifier | None = Field(
        default=None,
        alias="from",
        description="For made_from: the experiment the other was made from.",
    )
    to: Identifier | None = Field(
        default=None, description="For made_from: the experiment made from the other."
    )


class OverlapEdge(ApiModel):
    """Two experiments that overlap, and why."""

    a: Identifier = Field(description="One experiment.")
    b: Identifier = Field(description="The other.")
    kinds: list[OverlapEdgeKindName] = Field(description="Every way they overlap.")
    weight: float = Field(description="How strongly, 0 to 1, over every kind.")
    evidence: list[OverlapEvidence] = Field(description="The evidence, one item per kind.")


class OverlapConnections(ApiModel):
    """The connections map: nodes and edges."""

    nodes: list[OverlapNode] = Field(description="Every experiment.")
    edges: list[OverlapEdge] = Field(description="Every pair that overlaps, each once.")


class OverlapArea(ApiModel):
    """One area over the years."""

    id: Identifier = Field(description="The area's identifier.")
    name: str = Field(description="The folder's name, as written.")
    kind: AreaKindName = Field(description="What the folder stands for.")
    kind_assumed: bool = Field(description="True while the kind is the engine's own reading.")
    experiments: Count = Field(description="Experiments found in it.")
    first: int | None = Field(description="Its first year; null where undated.")
    last: int | None = Field(description="Its last year; null where undated.")


class OverlapExperiment(ApiModel):
    """One experiment, as the by-year view lists it."""

    experiment_id: Identifier = Field(description="The experiment folder's entry.")
    label: str = Field(description="The name a page shows for it.")
    area: str | None = Field(description="The area it is in; null where none.")
    area_kind: AreaKindName | None = Field(description="What the area's folder stands for.")
    theme: str | None = Field(description="The words of its theme; null where it is in none.")
    start: str | None = Field(description="Its first date, YYYY-MM-DD; null where undated.")
    end: str | None = Field(description="Its last date, YYYY-MM-DD; null where undated.")
    completeness: float | None = Field(
        description="Its raw files found followed over its raw files, 0 to 1; null where no raw."
    )


class OverlapByYear(ApiModel):
    """The run by year: themes over time, the year profile, words handed on, and the areas."""

    themes: list[ThemeOverTime] = Field(description="Each theme over time (themes/years).")
    story: dict[str, YearStory] = Field(description="Each year's profile (themes/years).")
    substitutes: list[Substitute] = Field(description="Words used in each other's place.")
    areas: list[OverlapArea] = Field(description="Each area, with its first and last year.")
    experiments: list[OverlapExperiment] = Field(
        default_factory=list,
        description="Every experiment with its area, theme, dates and completeness (0.4.0).",
    )


@register("overlap")
class OverlapResponse(ApiModel):
    """``GET /api/v1/overlap``: the connections map, or the run by year."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    mode: OverlapMode = Field(description="Which section is filled.")
    connections: OverlapConnections | None = Field(
        description="The connections map; null in the other mode."
    )
    by_year: OverlapByYear | None = Field(description="The run by year; null in the other mode.")


def _mode(query: Mapping[str, Sequence[str]]) -> OverlapMode:
    given = one(query, "mode")
    if given is None or given == "":
        return "connections"
    if given == "connections":
        return "connections"
    if given == "by_year":
        return "by_year"
    message = f"mode is one of {', '.join(MODES)}, not {given!r}"
    raise ApiError(message, parameter="mode")


def _connections(context: V1Context) -> OverlapConnections:
    """The connections map from the run: nodes from the completeness rows, edges from
    the projection in ``grouping/connections.py``."""
    from ingence.api.v1.completeness import pinned_completeness
    from ingence.api.v1.names import name_fields
    from ingence.api.v1.shaping import pinned_catalog
    from ingence.grouping.connections import connections

    found = pinned_completeness(context).experiments
    with pinned_catalog(context) as (catalog, _):
        names = name_fields(catalog, [(row.experiment_id, row.folder) for row in found])
        labels = {unit: str(held["shown_name"]) for unit, held in names.items()}
        nodes, edges = connections(catalog, found, labels)
    return OverlapConnections(
        nodes=[
            OverlapNode(
                experiment_id=node.experiment_id,
                label=node.label,
                area=node.area,
                area_kind="person" if node.area_kind == "person" else None,
                theme=node.theme,
                start=node.start,
                end=node.end,
                completeness=node.completeness,
                repeats=node.repeats,
            )
            for node in nodes
        ],
        edges=[
            OverlapEdge(
                a=edge.a,
                b=edge.b,
                kinds=list(edge.kinds),
                weight=edge.weight,
                evidence=[
                    OverlapEvidence(
                        kind=item.kind,
                        words=list(item.words),
                        count=item.count,
                        unit=item.unit,
                        from_=item.from_id,
                        to=item.to_id,
                    )
                    for item in edge.evidence
                ],
            )
            for edge in edges
        ],
    )


@route("overlap", model=OverlapResponse)
def overlap(context: V1Context, query: Mapping[str, Sequence[str]]) -> OverlapResponse:
    """The pinned run's overlap, in the mode asked.

    The connections map comes from the run, and the by-year view from the stored readouts.
    """
    mode = _mode(query)
    run_id = run_required(context)
    if mode == "connections":
        return OverlapResponse(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            mode=mode,
            connections=_connections(context),
            by_year=None,
        )
    return OverlapResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        mode="by_year",
        connections=None,
        by_year=by_year_of_run(context.project, run_id),
    )


def by_year_of_run(project: Path, run_id: str) -> OverlapByYear:
    """The by-year section from the run's readouts: the themes projection (kept by the readouts
    step), the Overview's areas, and completeness for each experiment's dates and share."""
    from ingence.api.v1.themes import years_of_projection
    from ingence.areas import area_id, area_of
    from ingence.completeness import completeness_of_run
    from ingence.core.project import Project
    from ingence.readouts import readout, with_area_answers
    from ingence.themes.projection import themes_projection

    built = themes_projection(project, run_id)
    themes, story, substitutes = years_of_projection(built)
    # The areas' kinds with a person's answers read over the stored ones.
    overview = with_area_answers(project, readout(project, run_id, "overview"))
    kinds = {row["id"]: (row["kind"], row["kind_assumed"]) for row in overview["areas"]}
    level = Project.open(project).settings.themes.area_level
    theme_of = {str(row["id"]): row.get("theme_label") for row in built.get("experiments", [])}
    years_of: dict[str, list[int]] = {}
    experiments: list[OverlapExperiment] = []
    for item in completeness_of_run(project, run_id).experiments:
        name = area_of(item.folder, level)
        area = area_id(item.root_id, name) if name is not None else None
        kind = kinds.get(area, ("folder", True))[0] if area is not None else None
        year = int(item.date[:4]) if item.date and item.date[:4].isdigit() else None
        if area is not None and year is not None:
            years_of.setdefault(area, []).append(year)
        experiments.append(
            OverlapExperiment(
                experiment_id=item.experiment_id,
                label=item.folder.rsplit("/", 1)[-1] or item.folder,
                area=name,
                area_kind=kind,
                theme=theme_of.get(item.experiment_id),
                start=item.date,
                end=item.date,
                completeness=(
                    round(item.followed / item.raw_acquisitions, 4)
                    if item.raw_acquisitions
                    else None
                ),
            )
        )
    areas = [
        OverlapArea(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            kind_assumed=row["kind_assumed"],
            experiments=row["experiments"],
            first=min(years_of[row["id"]]) if row["id"] in years_of else None,
            last=max(years_of[row["id"]]) if row["id"] in years_of else None,
        )
        for row in overview["areas"]
    ]
    return OverlapByYear(
        themes=themes, story=story, substitutes=substitutes, areas=areas, experiments=experiments
    )
