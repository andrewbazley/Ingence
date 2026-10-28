"""``GET /api/v1/assumptions``: what the run assumed without asking.

Every reading the run rests on that nobody confirmed, most files first: what a file type is,
which program wrote a file, which domain a word belongs to in a folder, which spelling a slip
meant, what an area's folder stands for, which workflow an experiment follows, which stage a
file is at, and which files are repeats of one another. Each carries the claim tier the engine
gave it, examples of what rests on it, and the write that rejects it
(``POST /api/assumptions/reject``), which waits for Re-run like every other answer.

The route reads the stored readout and computes nothing. The readout holds every class of claims
that a rule at the heuristic tier made and whose statement says it assumed. It groups them by rule
and value (``ingence.readouts.assumptions_readout``, on ``GET /api/v1/claims/class``'s machinery).
It is built once after inference and kept. Each item's ``id`` is the class handle that the Record
answer reversing it is written on. The invented example in :mod:`ingence.api.v1.examples` stays
for the tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, AssumptionKindName, Count, Identifier, register
from ingence.api.v1.shaping import run_required

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["AssumptionExample", "AssumptionItem", "AssumptionsResponse", "RejectAction"]

#: Where an assumption is rejected, and what the body names.
REJECT_ROUTE = "/api/assumptions/reject"


class AssumptionExample(ApiModel):
    """One file or folder that rests on an assumption."""

    entry_id: Identifier = Field(description="The entry's identifier.")
    label: str = Field(description="Its path relative to its root, for a page to show.")


class RejectAction(ApiModel):
    """The write that rejects an assumption: the route and the body to post."""

    route: str = Field(description="The route to post to: /api/assumptions/reject.")
    body: dict[str, str] = Field(description='The body: {"id": the assumption\'s id}.')


class AssumptionItem(ApiModel):
    """One assumption the run rests on."""

    id: Identifier = Field(description="The assumption's identifier.")
    kind: AssumptionKindName = Field(description="What kind of thing was assumed.")
    text: str = Field(description="The assumption in one sentence.")
    count: Count = Field(description="Files resting on it.")
    examples: list[AssumptionExample] = Field(description="A few of those files.")
    reject: RejectAction = Field(description="How to reject it.")
    tier: str = Field(description="The claim tier the engine gave it (its tier word).")


@register("assumptions")
class AssumptionsResponse(ApiModel):
    """``GET /api/v1/assumptions``: every assumption, most files first."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    total: Count = Field(description="How many assumptions there are.")
    assumptions: list[AssumptionItem] = Field(description="The assumptions, most files first.")


@route("assumptions", model=AssumptionsResponse)
def assumptions(context: V1Context, query: Mapping[str, Sequence[str]]) -> AssumptionsResponse:
    """The pinned run's assumptions, most files first, from the stored readout."""
    del query
    from ingence.readouts import readout

    run_id = run_required(context)
    return AssumptionsResponse.model_validate(
        readout(context.project, run_id, "assumptions")["payload"]
    )
