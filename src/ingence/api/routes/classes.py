"""Logical context: which of three worlds each file is in, composed at display time.

A class is a composition that the API reads at display time, built from claims that the
classification phase already makes. This module is that composition for the per-file axis,
``logical_context``, with the values ``observation``, ``derivation`` and ``information``. The
values are read relative to the workflow.

The context is a composition and not a fourth classifying rule. A rule that concluded a logical
context would have to read the file's stage, and a stage is itself a classifying claim.
``EntryRecords.claims`` holds the claims that the phases before this one made. It is empty for
classification, which is the first phase. So such a rule could not see the stage. The only way to
write it as a rule would be to read the stage senses off the bindings again. That repeats the
comparison that the stage rules already make, so the same reading would be computed in two
places.

Composing the context instead loses nothing. Every element is a claim with a rule, a version and
inputs behind it, so the reading is as attributable as a claim would be. The API recomputes it
from the same four inputs every time. The design would change if classification ran in rounds, so
that a rule could read what an earlier round concluded. That change to the pass deserves its own
justification and would not be a side effect of this module.

``unplaced`` is an absence and never a conclusion. A file whose stage no workflow names is
unplaced. The view says so and claims nothing. Concluding "this file is unplaced" would assert an
absence, and the engine keeps absence apart from the other states of a claim to prevent that.

Two workflows that disagree are reported and never resolved. Where a project has declared several
workflows and they place one stage differently, the view names both and places the file in
neither. That is a fact about the data and not about a declaration, so the view reports it,
where the four guards in ``record/workflow.py`` refuse a declaration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ingence.record.workflow import (
    DEFAULT_WORKFLOW,
    LogicalContext,
    logical_context_of,
    workflows_in_force,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ingence.record.workflow import Workflow
    from ingence.storage.catalog import Catalog

__all__ = [
    "LIMIT",
    "OUTRANKS_THE_WORKFLOW",
    "STAGE_RULES",
    "logical_context",
    "place",
]

#: How many files one page places. The tally is over the whole run. The rows are a page.
LIMIT = 500

#: The rules whose conclusion fixes a logical context whatever the workflow says, because each
#: rests on stronger evidence than a stage word.
#:
#: An instrument's own extension is an observation: ``acquisition_stage_from_format`` reads the
#: extension slot at exact quality, and no workflow declaration outranks what the instrument
#: wrote. A program is a derivation by construction: a script exists to do something to
#: something else.
#:
#: The tuple names rule identifiers and not concluded values, because the program rule concludes
#: the format sense that matched (``python_source``, ``shell_script``), so there is no single
#: value to match on. A project revision that renames either rule passes its own names.
OUTRANKS_THE_WORKFLOW: dict[str, LogicalContext] = {
    "acquisition_stage_from_format": LogicalContext.OBSERVATION,
    "program_from_extension": LogicalContext.DERIVATION,
}

#: The rules whose conclusion is read as the file's stage. Revision 4's three, which are the
#: three that place a file on a pipeline.
STAGE_RULES: tuple[str, ...] = (
    "pipeline_stage_from_name",
    "acquisition_stage_from_format",
    "analysis_stage_from_derived_sense",
    # Revision 5 (0.4.0): the stage a file's own tags give it, and an export read from a name
    # ending in two extensions.
    "stage_from_writer_tags",
    "export_stage_from_inner_format",
)


def place(
    stages: Sequence[str],
    outranked_by: Sequence[str],
    workflows: Sequence[Workflow],
) -> dict[str, Any]:
    """Return one file's logical context, and the reason it has that one.

    ``stages`` are the stages claimed about the file, ``outranked_by`` the rules that concluded
    about it whose reading holds whatever the workflow says, and ``workflows`` the ones in
    force. Pure, so the whole policy is testable without a project.
    """
    for rule_id in outranked_by:
        fixed = OUTRANKS_THE_WORKFLOW.get(rule_id)
        if fixed is not None:
            return {
                "context": fixed.value,
                "because": (
                    f"{rule_id} concluded it, and that outranks any workflow: an instrument's "
                    "own extension is an observation and a program is a derivation, whatever "
                    "a person declared about their stages"
                ),
                "stages": list(stages),
                "workflows": [],
            }

    if not workflows:
        return {
            "context": LogicalContext.UNPLACED.value,
            "because": "no workflow is declared, so nothing says where this project's data enters",
            "stages": list(stages),
            "workflows": [],
        }

    placed: dict[str, list[str]] = {}
    for workflow in workflows:
        for stage in stages or [None]:  # type: ignore[list-item]
            context = logical_context_of(stage, workflow)
            if context is LogicalContext.UNPLACED:
                continue
            placed.setdefault(context.value, []).append(workflow.name)

    if not placed:
        return {
            "context": LogicalContext.UNPLACED.value,
            "because": (
                "no workflow names a stage this file was claimed at. Unplaced, and deliberately "
                "not information: a file nothing placed is not thereby a plan"
            ),
            "stages": list(stages),
            "workflows": [workflow.name for workflow in workflows],
        }

    if len(placed) > 1:
        return {
            "context": "conflicting",
            "because": (
                "the workflows in force place this file differently, and picking one would "
                "invent an answer: "
                + "; ".join(
                    f"{context} by {', '.join(sorted(set(names)))}"
                    for context, names in sorted(placed.items())
                )
            ),
            "stages": list(stages),
            "workflows": [workflow.name for workflow in workflows],
        }

    agreed, naming = next(iter(placed.items()))
    return {
        "context": agreed,
        "because": f"its stage sits there in {', '.join(sorted(set(naming)))}",
        "stages": list(stages),
        "workflows": sorted(set(naming)),
    }


def _rows(catalog: Catalog, sql: str, parameters: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = catalog.query(sql, parameters or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def logical_context(
    catalog: Catalog,
    project: Path,
    *,
    limit: int = LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Place every file in this run, against the workflows the Record declares.

    Where a project has declared none, the view reports that and shows what the shipped default
    would give, marked as a preview. A person who decides whether to declare a workflow should
    see its effect first. Showing the default unmarked would make a laboratory that never
    declared a workflow look as though it had agreed.
    """
    declared = workflows_in_force(project)
    workflows = declared or (DEFAULT_WORKFLOW,)
    if not catalog.has_run:
        return {
            "declared": bool(declared),
            "workflows": [workflow.as_payload() for workflow in workflows],
            "tally": [],
            "files": [],
        }

    claims = _rows(
        catalog,
        """
        SELECT c.subject AS subject, c.rule_id AS rule_id, c.object_reference AS value
        FROM claims c
        WHERE c.predicate LIKE 'classifies.%' AND c.resolution_status = 'resolved'
        ORDER BY c.subject, c.rule_id
        """,
    )
    named = {
        row["entry_id"]: row["name"]
        for row in _rows(catalog, "SELECT entry_id, name FROM entries WHERE entry_type = 'file'")
    }

    stages: dict[str, list[str]] = {}
    outranked: dict[str, list[str]] = {}
    for row in claims:
        subject = str(row["subject"])
        rule_id = str(row["rule_id"])
        if rule_id in STAGE_RULES and row["value"]:
            stages.setdefault(subject, []).append(str(row["value"]))
        if rule_id in OUTRANKS_THE_WORKFLOW:
            outranked.setdefault(subject, []).append(rule_id)

    placed: list[dict[str, Any]] = []
    tally: dict[str, int] = {}
    for entry_id, name in sorted(named.items(), key=lambda item: str(item[1])):
        reading = place(
            sorted(set(stages.get(entry_id, ()))),
            sorted(set(outranked.get(entry_id, ()))),
            workflows,
        )
        tally[reading["context"]] = tally.get(reading["context"], 0) + 1
        placed.append({"entry_id": entry_id, "name": name, **reading})

    return {
        "declared": bool(declared),
        "workflows": [workflow.as_payload() for workflow in workflows],
        "tally": [
            {"value": context, "n": count}
            for context, count in sorted(tally.items(), key=lambda item: (-item[1], item[0]))
        ],
        "files": placed[offset : offset + limit],
    }
