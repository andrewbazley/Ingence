"""``GET /api/v1/restore/preview``: what restoring a past run would change.

Computed from the project and written nowhere. The page shows it and sends its ``digest`` back
with ``POST /api/workspace/restore``, which writes only that exact preview.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.workspace.restore import RestoreRefusedError, restore_plan

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["RestorePreview", "preview"]


@register("restore_preview")
class RestorePreview(ApiModel):
    """What restoring one run would change, and the digest a person confirms."""

    run_id: Identifier
    current_run_id: Identifier | None
    #: The highest decision the run read, and how that was known.
    watermark: Count
    watermark_from: str
    #: How many earlier events the one withdrawal would name.
    withdraw_count: Count
    #: Decisions set aside and brought back, by kind.
    set_aside: dict[str, Count]
    brought_back: dict[str, Count]
    #: Dashboard settings that change back: name -> [value now, value then].
    settings: dict[str, list[Any]]
    checkpoint_id: Identifier | None
    #: Rules whose parameters differ from the run's and that an override cannot return (the
    #: rule is on one side only, or its parameter refuses the earlier value): left as they are.
    rules_kept: list[str]
    digest: str
    #: Rule parameters the restore returns, by an appended revision: rule -> parameter -> [value
    #: now, value then].
    rules_reverted: dict[str, dict[str, list[Any]]] = Field(default_factory=dict)


@route("restore/preview", model=RestorePreview)
def preview(context: V1Context, query: Mapping[str, Sequence[str]]) -> RestorePreview:
    """The preview of restoring ``run``; nothing is written."""
    run_id = (query.get("run") or [""])[0]
    if not run_id:
        message = "name the run to restore (run=...)"
        raise ApiError(message, parameter="run")
    try:
        plan = restore_plan(context.project, run_id)
    except RestoreRefusedError as error:
        raise ApiError(str(error), parameter="run") from error
    return RestorePreview(
        run_id=plan.run_id,
        current_run_id=plan.current_run_id,
        watermark=plan.watermark,
        watermark_from=plan.watermark_from,
        withdraw_count=len(plan.withdraw),
        set_aside=plan.set_aside,
        brought_back=plan.brought_back,
        settings=plan.settings,
        checkpoint_id=plan.checkpoint_id,
        rules_kept=list(plan.rules_kept),
        digest=plan.digest,
        rules_reverted=plan.rules_reverted,
    )
