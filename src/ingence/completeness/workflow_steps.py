"""The deepest step each experiment reached against its workflow.

An experiment follows the workflow in force (declared or suggested, as the run applied it) whose
stages its files reached the most of; the deepest step is the last of that workflow's stages, in
its order, that any of its files sits at, read from the run's resolved stage claims (the format
rule's, the name rules' and ``stage_from_workflow``'s alike: a file's stage is a file's stage,
whichever rule read it, and ``raw`` and ``acquisition`` are one rung). What the workflow expects
is its stages in order. An experiment no workflow's stage reaches follows none.

This is a projection over the run, on ``completeness/counting.py``'s terms: one query joined to
the map of folders to experiments. Nothing is read from a file or the Record, and nothing is
written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyarrow as pa

from ingence.experiment_units import run_units
from ingence.infer.workflow_suggest import SAME_RUNG, read_workflows
from ingence.record.workflow import Workflow

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.experiment_units import RunUnits
    from ingence.storage.catalog import Catalog

__all__ = ["StepReached", "stages_reached", "steps_reached", "workflows_applied"]

_UNITS_TABLE = "workflow_steps_units"

_STAGES_SQL = f"""
SELECT u.experiment_id, c.object_reference AS stage, count(DISTINCT e.entry_id) AS files
FROM entries e
JOIN {_UNITS_TABLE} u ON u.folder_id = e.parent_entry_id
JOIN claims c ON c.subject = e.entry_id AND c.resolution_status = 'resolved'
    AND c.predicate LIKE 'classifies.%stage%' AND c.object_reference IS NOT NULL
WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
GROUP BY 1, 2 ORDER BY 1, 2
"""  # noqa: S608 -- the one name spelled in is this module's constant


@dataclass(frozen=True, slots=True)
class StepReached:
    """One experiment's workflow, the deepest of its stages reached, and what it expects."""

    workflow_id: str
    reached: str | None
    expected: tuple[str, ...]


def workflows_applied(catalog: Catalog) -> tuple[Workflow, ...]:
    """The workflows the catalog's run applied, as it stated them, most experiments first."""
    found = [
        Workflow.from_payload(payload)
        for payload in read_workflows(getattr(catalog, "run_directory", None))
        if payload.get("in_force")
    ]
    return tuple(sorted(found, key=lambda w: (-w.evidence.experiments, w.identifier)))


def stages_reached(catalog: Catalog, run: RunUnits | None = None) -> dict[str, dict[str, int]]:
    """Each experiment's stages, folded to rungs, with how many of its files sit at each."""
    held = run if run is not None else run_units(catalog)
    if not held.units:
        return {}
    folders = sorted(held.units)
    catalog.connection.register(
        _UNITS_TABLE,
        pa.table(
            {
                "folder_id": pa.array(folders, type=pa.string()),
                "experiment_id": pa.array([held.units[f] for f in folders], type=pa.string()),
            }
        ),
    )
    try:
        rows = catalog.connection.execute(_STAGES_SQL).fetchall()
    finally:
        catalog.connection.unregister(_UNITS_TABLE)
    found: dict[str, dict[str, int]] = {}
    for unit, stage, files in rows:
        rung = SAME_RUNG.get(str(stage), str(stage))
        counted = found.setdefault(str(unit), {})
        counted[rung] = max(counted.get(rung, 0), int(files))
    return found


def steps_reached(
    catalog: Catalog,
    run: RunUnits | None = None,
    *,
    workflows: Mapping[str, Workflow] | None = None,
) -> dict[str, StepReached]:
    """Each experiment that follows a workflow in force, to the step it reached.

    ``workflows`` is the workflows applied, by identifier, where the caller holds them.
    """
    held = run if run is not None else run_units(catalog)
    applied = tuple(workflows.values()) if workflows is not None else workflows_applied(catalog)
    if not applied:
        return {}
    reached = stages_reached(catalog, held)
    found: dict[str, StepReached] = {}
    for unit in sorted(set(held.units.values())):
        stages = reached.get(unit, {})
        if not stages:
            continue
        best: tuple[int, int, str, Workflow] | None = None
        for workflow in applied:
            hits = [rung for rung in workflow.rungs if rung in stages]
            if not hits:
                continue
            candidate = (len(hits), workflow.evidence.experiments, workflow.identifier, workflow)
            if best is None or (candidate[0], candidate[1], -len(candidate[2])) > (
                best[0],
                best[1],
                -len(best[2]),
            ):
                best = candidate
        if best is None:
            continue
        workflow = best[3]
        deepest = [rung for rung in workflow.rungs if rung in stages][-1]
        found[unit] = StepReached(
            workflow_id=workflow.identifier, reached=deepest, expected=tuple(workflow.rungs)
        )
    return found
