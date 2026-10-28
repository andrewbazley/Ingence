"""``GET /api/v1/workflows``: the workflows the experiments follow.

A workflow is an ordered list of stages, each with the file types, folder names and name words
that put a file at it, and the names exempt from it. The engine suggests workflows from the
chains of stages it saw, and a person declares one (``POST /api/workflows``) or withdraws one
(``POST /api/workflows/withdraw``). Each carries its evidence: the experiments following it and
the chains of stages seen, and whether it is in force.

The route reads the run's own statement of the workflows it suggested and the declared ones it
read (``workflows.json`` in its directory, written by the suggester phase of inference), each
with whether the run applied it. A run made before the statement existed, or with
``infer.workflows`` off and nothing declared, answers the workflows the Record holds now, none of
them applied by that run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import pinned_catalog, run_required

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "WorkflowChain",
    "WorkflowEvidence",
    "WorkflowItem",
    "WorkflowOrigin",
    "WorkflowStage",
    "WorkflowsResponse",
]

#: Suggested by the engine from the chains it saw, or declared by a person.
WorkflowOrigin = Literal["suggested", "declared"]


class WorkflowStage(ApiModel):
    """One stage of a workflow and what puts a file at it."""

    stage: str = Field(description="The stage's name.")
    types: list[str] = Field(description="File types at this stage.")
    folders: list[str] = Field(description="Folder names that hold this stage's files.")
    names: list[str] = Field(description="Name words that mark this stage.")


class WorkflowChain(ApiModel):
    """One chain of stages seen, and in how many experiments."""

    chain: list[str] = Field(description="The stages, in the order seen.")
    experiments: Count = Field(description="Experiments whose files follow this chain.")


class WorkflowEvidence(ApiModel):
    """Why the workflow stands."""

    experiments: Count = Field(description="Experiments following it.")
    chains: list[WorkflowChain] = Field(description="The chains of stages seen, most first.")


class WorkflowItem(ApiModel):
    """One workflow."""

    id: Identifier = Field(description="The workflow's identifier.")
    name: str = Field(description="Its name.")
    origin: WorkflowOrigin = Field(description="Suggested by the engine, or declared by a person.")
    stages: list[WorkflowStage] = Field(description="Its stages, in order.")
    exempt: list[str] = Field(description="Name words of files it does not stage.")
    scope: str | None = Field(description="The area it applies to; null for every area.")
    evidence: WorkflowEvidence = Field(description="The experiments and chains behind it.")
    in_force: bool = Field(description="Whether the run applies it.")


@register("workflows")
class WorkflowsResponse(ApiModel):
    """``GET /api/v1/workflows``: every workflow, suggested and declared."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    workflows: list[WorkflowItem] = Field(description="The workflows, most experiments first.")


def _item(payload: Mapping[str, object], *, in_force: bool) -> WorkflowItem:
    """One workflow as the run states it, in the contract's shape."""
    from ingence.record.workflow import Workflow

    workflow = Workflow.from_payload(payload)
    return WorkflowItem(
        id=str(payload.get("id") or workflow.identifier),
        name=workflow.name,
        origin="suggested" if workflow.origin == "suggested" else "declared",
        stages=[
            WorkflowStage(
                stage=stage.stage,
                types=list(stage.types),
                folders=list(stage.folders),
                names=list(stage.names),
            )
            for stage in workflow.stages
        ]
        or [WorkflowStage(stage=rung, types=[], folders=[], names=[]) for rung in workflow.rungs],
        exempt=list(workflow.exempt),
        scope=workflow.scope,
        evidence=WorkflowEvidence(
            experiments=workflow.evidence.experiments,
            chains=[
                WorkflowChain(chain=list(chain), experiments=count)
                for chain, count in workflow.evidence.chains
            ],
        ),
        in_force=in_force,
    )


@route("workflows", model=WorkflowsResponse)
def workflows(context: V1Context, query: Mapping[str, Sequence[str]]) -> WorkflowsResponse:
    """The pinned run's workflows: suggested and declared, most experiments first."""
    del query
    from ingence.infer.workflow_suggest import read_workflows
    from ingence.record.workflow import workflows_in_force

    run_id = run_required(context)
    with pinned_catalog(context) as (catalog, _):
        stated = read_workflows(catalog.run_directory)
    items = [_item(payload, in_force=bool(payload.get("in_force"))) for payload in stated]
    if not stated:
        # A run that stated nothing: the Record's declared workflows, which this run did not read.
        items = [
            _item({**workflow.as_payload(), "id": workflow.identifier}, in_force=False)
            for workflow in workflows_in_force(context.project)
        ]
    items.sort(key=lambda item: (-item.evidence.experiments, item.name, item.id))
    return WorkflowsResponse(schema_version=SCHEMA_VERSION, run_id=run_id, workflows=items)
