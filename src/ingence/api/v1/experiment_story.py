"""``GET /api/v1/experiments/story?id=``: one experiment day by day.

What happened in an experiment's folder, day by day: on each date, the stage its files were at,
how many were written and of what types; the workflow it follows, the last stage it reached and
the stages the workflow expects; the experiments read as repeats of it; and what it was made
from. A missing ``id`` is refused.

The route reads the run. The days come from each file's earliest credible time (the day, never
the second: ``grouping/origin_days.py``), with each day's files by the stage they were at. The
workflow and the step reached come from ``completeness/workflow_steps.py``. The repeats come from
``grouping/repeats.py``. The experiments it was made from are those that hold a source of a
derivation that the run proposed or published into one of its files. An ``id`` that is not an
experiment of the run is refused as not found.
"""

from __future__ import annotations

import json
from collections import Counter
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one, run_required
from ingence.infer.workflow_suggest import SAME_RUNG, STAGE_ORDER

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["ExperimentStoryResponse", "StoryDay", "StoryMadeFrom", "StoryRepeat", "StoryWorkflow"]


class StoryDay(ApiModel):
    """One day of the experiment."""

    date: str = Field(description="The day, YYYY-MM-DD.")
    stage: str = Field(description="The stage its files were at that day.")
    files: Count = Field(description="Files written that day.")
    types: list[str] = Field(description="The types of those files.")


class StoryWorkflow(ApiModel):
    """The workflow the experiment follows and how far it got."""

    id: str | None = Field(description="The workflow's identifier; null where none is known.")
    reached: str | None = Field(description="The last of its stages the files reached.")
    expected: list[str] = Field(description="The workflow's stages, in order.")
    raw_files: Count = Field(description="Raw files found in the experiment.")
    raw_reached: Count = Field(
        description="Of those, the ones that reached a later stage (followed by a derived file)."
    )
    reached_type: str | None = Field(
        description="The file type most of the files at the deepest step are; null where none."
    )


class StoryMadeFrom(ApiModel):
    """An experiment this one was made from."""

    experiment_id: Identifier = Field(description="That experiment's folder entry.")
    label: str = Field(description="The name a page shows for it.")


class StoryRepeat(ApiModel):
    """An experiment read as a repeat of this one."""

    experiment_id: Identifier = Field(description="That experiment's folder entry.")
    label: str = Field(description="The name a page shows for it.")


@register("experiment_story")
class ExperimentStoryResponse(ApiModel):
    """``GET /api/v1/experiments/story``: one experiment, day by day."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    experiment_id: Identifier = Field(description="The experiment asked about.")
    days: list[StoryDay] = Field(description="Its days, earliest first.")
    workflow: StoryWorkflow = Field(description="Its workflow and how far it got.")
    repeats: list[StoryRepeat] = Field(description="The experiments read as repeats of it.")
    made_from: list[StoryMadeFrom] = Field(description="What it was made from.")


_UNPLACED = "unplaced"

_UNITS_TABLE = "story_units"

#: A file's day: its earliest credible time, else the date its own name states, else the date
#: its folder's name states (the Years view's order, ``experiment_units.experiment_times``).
_DAYS_SQL = f"""
SELECT e.entry_id,
       lower(coalesce(nullif(e.compound_extension, ''), nullif(e.extension, ''))) AS kind,
       coalesce(
           min(json_extract_string(t.object_reference, '$.at')),
           min(json_extract_string(n.object_reference, '$.from')),
           min(json_extract_string(f.object_reference, '$.from'))
       ) AS at,
       list(DISTINCT s.object_reference) FILTER (WHERE s.object_reference IS NOT NULL) AS stages
FROM entries e
JOIN {_UNITS_TABLE} u ON u.folder_id = e.parent_entry_id
LEFT JOIN claims t ON t.subject = e.entry_id AND t.resolution_status = 'resolved'
    AND t.predicate = 'classifies.earliest_credible_time'
LEFT JOIN claims n ON n.subject = e.entry_id AND n.resolution_status = 'resolved'
    AND n.predicate = 'classifies.date_neighbourhood_of_a_file'
LEFT JOIN claims f ON f.subject = e.parent_entry_id AND f.resolution_status = 'resolved'
    AND f.predicate = 'classifies.date_neighbourhood_of_a_folder'
LEFT JOIN claims s ON s.subject = e.entry_id AND s.resolution_status = 'resolved'
    AND s.predicate LIKE 'classifies.%stage%'
WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
GROUP BY 1, 2
"""  # noqa: S608 -- the one name spelled in is this module's constant

_MADE_FROM_SQL = f"""
SELECT DISTINCT src.parent_entry_id
FROM (
    SELECT source_id, target_id FROM candidates WHERE primitive = 'derives'
    UNION
    SELECT source_id, target_id FROM assertions
    WHERE decision_status = 'asserted' AND primitive = 'derives'
) d
JOIN entries tgt ON tgt.entry_id = d.target_id
JOIN {_UNITS_TABLE} u ON u.folder_id = tgt.parent_entry_id
JOIN entries src ON src.entry_id = d.source_id
"""  # noqa: S608 -- the one name spelled in is this module's constant


def _stage_of(stages: object) -> str:
    """The furthest rung a file's stage claims name, or unplaced."""
    named = [
        SAME_RUNG.get(str(stage), str(stage))
        for stage in (stages if isinstance(stages, list) else [])
    ]
    known = [stage for stage in named if stage in STAGE_ORDER]
    if known:
        return max(known, key=STAGE_ORDER.index)
    return sorted(named)[0] if named else _UNPLACED


@route("experiments/story", model=ExperimentStoryResponse)
def experiment_story(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> ExperimentStoryResponse:
    """One experiment's story: its days, its workflow and the step reached, its repeats, and
    what it was made from."""
    import pyarrow as pa

    from ingence.api.v1.completeness import pinned_completeness
    from ingence.api.v1.names import name_fields
    from ingence.api.v1.shaping import pinned_catalog
    from ingence.completeness.workflow_steps import steps_reached
    from ingence.experiment_units import folders_of_experiment, run_units
    from ingence.grouping.origin_days import day_of
    from ingence.grouping.repeats import repeat_groups

    experiment_id = one(query, "id")
    if not experiment_id:
        message = "id names the experiment folder's entry; none was given"
        raise ApiError(message, parameter="id")
    run_id = run_required(context)
    with pinned_catalog(context) as (catalog, _):
        units = run_units(catalog)
        if units.units.get(experiment_id) != experiment_id:
            message = f"no experiment of this run has the identifier {experiment_id!r}"
            raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
        folders = folders_of_experiment(units, experiment_id)
        catalog.connection.register(
            _UNITS_TABLE, pa.table({"folder_id": pa.array(folders, type=pa.string())})
        )
        try:
            rows = catalog.connection.execute(_DAYS_SQL).fetchall()
            sources = [str(row[0]) for row in catalog.connection.execute(_MADE_FROM_SQL).fetchall()]
        finally:
            catalog.connection.unregister(_UNITS_TABLE)
        steps = steps_reached(catalog, units)
        groups = repeat_groups(catalog, units)
        step = steps.get(experiment_id)
        group = groups.get(experiment_id)
        others = sorted(
            {member for member in (group.members if group else ()) if member != experiment_id}
            | {
                units.units[source]
                for source in sources
                if source in units.units and units.units[source] != experiment_id
            }
        )
        labels = {
            unit: str(held["shown_name"])
            for unit, held in name_fields(
                catalog, [(unit, units.paths.get(unit, "")) for unit in others]
            ).items()
        }
    days: dict[tuple[str, str], tuple[int, set[str]]] = {}
    at_deepest: Counter[str] = Counter()
    for _entry, kind, at, stages in rows:
        stage = _stage_of(stages)
        if step is not None and stage == step.reached and kind:
            at_deepest[str(kind)] += 1
        day = day_of(json.dumps({"at": str(at)})) if at else None
        if day is None:
            continue
        key = (day, stage)
        files, kinds = days.get(key, (0, set()))
        if kind:
            kinds.add(str(kind))
        days[key] = (files + 1, kinds)
    counted = next(
        (
            row
            for row in pinned_completeness(context).experiments
            if row.experiment_id == experiment_id
        ),
        None,
    )
    rank = {stage: position for position, stage in enumerate(STAGE_ORDER)}
    listed = [
        StoryDay(date=day, stage=stage, files=files, types=sorted(kinds))
        for (day, stage), (files, kinds) in sorted(
            days.items(), key=lambda item: (item[0][0], rank.get(item[0][1], len(rank)), item[0][1])
        )
    ]
    made_from = sorted(
        {
            units.units[source]
            for source in sources
            if source in units.units and units.units[source] != experiment_id
        }
    )
    return ExperimentStoryResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        experiment_id=experiment_id,
        days=listed,
        workflow=StoryWorkflow(
            id=step.workflow_id if step else None,
            reached=step.reached if step else None,
            expected=list(step.expected) if step else [],
            raw_files=counted.raw_acquisitions if counted else 0,
            raw_reached=counted.followed if counted else 0,
            reached_type=at_deepest.most_common(1)[0][0] if at_deepest else None,
        ),
        repeats=[
            StoryRepeat(experiment_id=member, label=labels.get(member, member))
            for member in (group.members if group else ())
            if member != experiment_id
        ],
        made_from=[
            StoryMadeFrom(experiment_id=source, label=labels.get(source, source))
            for source in made_from
        ],
    )
