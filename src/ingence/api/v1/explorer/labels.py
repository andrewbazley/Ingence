"""``GET /api/v1/explorer/labels``: a person's display labels, and one entry's history of them.

The Graph workspace's human layer. A label is an annotation in the Record on
``label:<entry_id>`` (``api/routes/labels.py`` writes them), read here from the Record as it
stands now, not from a run: a label written after the run published shows at once, and survives
the next run. The latest annotation on an entry is its label; one with no label removes it from
view and keeps what came before in ``history``. A file that moved is read at its new place.

Nothing here reads or writes an entry's name: a label is always shown beside it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence import SCHEMA_VERSION
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one
from ingence.core.schema import RecordEventKind

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["LabelsResponse", "labels"]

#: Labels one response lists at most; a project with more is paged by nobody yet, and says so.
LABELS_LISTED = 20_000


class EntryLabel(ApiModel):
    entry_id: Identifier
    label: str
    actor: str
    at: str
    event_id: Identifier
    #: The entry's name and path when the label was written, as the event recorded them.
    name_then: str | None
    path_then: str | None
    #: Where the label was written, if the file has moved since (the target as written).
    written_at: str | None
    #: Earlier annotations on the same entry, labels and removals.
    earlier: Count


class LabelEvent(ApiModel):
    event_id: Identifier
    #: None where the event removed the label.
    label: str | None
    actor: str
    at: str
    sequence: Count


@register("explorer_labels")
class LabelsResponse(ApiModel):
    """Every entry's current display label; with ``id``, that entry's whole history too."""

    schema_version: str
    labels: list[EntryLabel]
    total: Count
    capped: bool
    history: list[LabelEvent]


def labels(context: V1Context, query: Mapping[str, Sequence[str]]) -> LabelsResponse:
    from ingence.record.log import read_events
    from ingence.record.targets import entry_labelled_by

    wanted = (one(query, "id") or "").strip()
    by_entry: dict[str, list[tuple[int, str, str | None, str, str, dict[str, object]]]] = {}
    for event in read_events(context.project):
        entry = entry_labelled_by(event.target)
        if entry is None or event.kind is not RecordEventKind.ANNOTATE:
            continue
        payload = json.loads(event.payload or "{}")
        label = payload.get("label")
        by_entry.setdefault(entry, []).append(
            (
                event.sequence,
                event.event_id,
                str(label) if isinstance(label, str) and label else None,
                event.actor,
                event.occurred_at,
                payload,
            )
        )
    current: list[EntryLabel] = []
    for entry, events in sorted(by_entry.items()):
        events.sort()
        sequence, event_id, label, actor, at, payload = events[-1]
        del sequence
        if label is None:
            continue
        current.append(
            EntryLabel(
                entry_id=entry,
                label=label,
                actor=actor,
                at=at,
                event_id=event_id,
                name_then=str(payload["name"]) if payload.get("name") else None,
                path_then=str(payload["path"]) if payload.get("path") else None,
                written_at=str(payload["written_at"]) if payload.get("written_at") else None,
                earlier=len(events) - 1,
            )
        )
    history = [
        LabelEvent(event_id=event_id, label=label, actor=actor, at=at, sequence=sequence)
        for sequence, event_id, label, actor, at, _ in sorted(by_entry.get(wanted, []))
    ]
    return LabelsResponse(
        schema_version=SCHEMA_VERSION,
        labels=current[:LABELS_LISTED],
        total=len(current),
        capped=len(current) > LABELS_LISTED,
        history=history,
    )


@route("explorer/labels", model=LabelsResponse)
def labels_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> LabelsResponse:
    return labels(context, query)
