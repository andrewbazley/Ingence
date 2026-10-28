"""``/api/v1/entry`` and ``/api/v1/folder``: one entry, or one folder's entries.

The routes are built from ``api/routes/views.py``'s ``entry`` and ``folder``, which the older
pages read, and shaped field by field. The census's raw record is not sent whole. Its nanosecond
times are sent as ISO 8601 with their zone, its inode and device as strings, and the columns a
page has no use for (mode, owner, fingerprints) are left out.

The quality marks that an entry's name carries are listed beside its readings, each with what it
means in plain words. A mark is a statement about quality. It is never a doubt, and never a reason
to leave the entry out.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from ingence import SCHEMA_VERSION
from ingence.api.routes import views
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, ZonedTime, register
from ingence.api.v1.moved import MovedSaid, moved_said
from ingence.api.v1.shaping import (
    catalog_of,
    integer,
    json_value,
    one,
    text_or_none,
    time_from_ns,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["EntryRecord", "EntryResponse", "FolderResponse", "QualityMarkItem"]


class EntryRecord(ApiModel):
    """What the census recorded about one entry, in values a browser holds exactly."""

    id: Identifier
    name: str
    #: The path under its root.
    path: str
    root_id: Identifier
    parent_id: Identifier | None
    depth: Count
    entry_type: str
    entry_kind: str
    extension: str
    compound_extension: str
    size_bytes: Count | None
    allocated_bytes: Count | None
    #: As strings: an inode or a device number may exceed a browser's exact integers.
    inode: str | None
    device: str | None
    modified_at: ZonedTime | None
    created_at: ZonedTime | None
    created_time_source: str
    changed_at: ZonedTime | None
    event_time: ZonedTime | None
    event_time_source: str
    event_time_tier: str
    symlink_state: str
    symlink_target: str | None
    resolution_status: str
    scan_outcome: str
    is_deleted: bool
    first_seen_run: Identifier
    last_seen_run: Identifier
    extraction_profile: str | None
    moved_from: Identifier | None
    moved_by: str | None
    #: When the move happened, at the latest. It is never the file's clock.
    moved_at: ZonedTime | None = None


class ClaimItem(ApiModel):
    rule_id: str
    rule_version: str
    predicate: str
    object_reference: JsonValue
    inference_tier: str
    resolution_status: str
    inputs: JsonValue
    defeaters: JsonValue


class FailureItem(ApiModel):
    rule_id: str
    rule_version: str
    failure_class: str
    reason: str
    inputs: JsonValue


class FindingItem(ApiModel):
    rule_id: str
    rule_version: str
    raised: JsonValue
    reason: str
    inputs: JsonValue


class ComponentItem(ApiModel):
    component_id: Identifier | None
    component_type: str
    normalized_value: str | None
    raw_value: str | None
    occurrences: JsonValue


class BindingItem(ApiModel):
    component_id: Identifier | None
    term: str | None
    theme_kind: str | None
    quality: str | None
    #: Which slot of the name licensed the lookup's restriction (``constraint`` in the store).
    slot_constraint: str | None
    resolution_status: str | None
    alternative_count: Count | None


class QualityMarkItem(ApiModel):
    """A quality mark the entry's name carries: what the name says, read-only."""

    #: The Dictionary's term, and its mark as the vocabulary declares it (``good`` or ``poor``).
    term: str
    mark: str
    #: The word as the name spells it.
    spelled: str
    #: The word standing directly before it that cancels it (``not``), or None.
    cancelled_by: str | None
    #: What the mark means, in plain words.
    said: str


@register("entry")
class EntryResponse(ApiModel):
    """``GET /api/v1/entry?id=``: one entry, and every rule that said anything about it."""

    schema_version: str
    run_id: Identifier
    entry: EntryRecord
    claims: list[ClaimItem]
    failures: list[FailureItem]
    findings: list[FindingItem]
    components: list[ComponentItem]
    bindings: list[BindingItem]
    marks: list[QualityMarkItem]
    #: Where it came from and how that was known, in words; None if it never moved.
    moved: MovedSaid | None = None


class ClassKey(ApiModel):
    key: JsonValue
    status: str


class FolderEntry(ApiModel):
    id: Identifier
    name: str
    entry_type: str
    extension: str
    size_bytes: Count | None
    path: str
    claims: list[ClaimItem]
    #: The class key each grouping rule computed for it, by rule.
    classes: dict[str, ClassKey]
    components: list[ComponentItem]
    bindings: list[BindingItem]


class SetAsideBy(ApiModel):
    rule_id: str
    reason: str
    path: str
    itself: bool


@register("folder")
class FolderResponse(ApiModel):
    """``GET /api/v1/folder?id=``: one folder's entries, each beside its claims and class keys."""

    schema_version: str
    run_id: Identifier
    folder: EntryRecord
    entries: list[FolderEntry]
    #: How many entries the request's ``limit`` allowed.
    limit: Count
    set_aside_by: list[SetAsideBy]


def entry_record(row: Mapping[str, Any]) -> EntryRecord:
    """The census's row for one entry, as the contract sends it."""
    return EntryRecord(
        id=str(row["entry_id"]),
        name=str(row["name"] or ""),
        path=str(row["relative_path"] or ""),
        root_id=str(row["root_id"]),
        parent_id=text_or_none(row.get("parent_entry_id")),
        depth=int(row.get("depth") or 0),
        entry_type=str(row["entry_type"]),
        entry_kind=str(row["entry_kind"]),
        extension=str(row.get("extension") or ""),
        compound_extension=str(row.get("compound_extension") or ""),
        size_bytes=row.get("size_bytes"),
        allocated_bytes=row.get("allocated_bytes"),
        inode=text_or_none(row.get("inode")),
        device=text_or_none(row.get("device")),
        modified_at=time_from_ns(row.get("modified_ns")),
        created_at=time_from_ns(row.get("created_ns")),
        created_time_source=str(row.get("created_time_source") or ""),
        changed_at=time_from_ns(row.get("changed_ns")),
        event_time=time_from_ns(row.get("event_time_ns")),
        event_time_source=str(row.get("event_time_source") or ""),
        event_time_tier=str(row.get("event_time_tier") or ""),
        symlink_state=str(row.get("symlink_state") or ""),
        symlink_target=text_or_none(row.get("symlink_target")),
        resolution_status=str(row["resolution_status"]),
        scan_outcome=str(row["scan_outcome"]),
        is_deleted=bool(row["is_deleted"]),
        first_seen_run=str(row["first_seen_run"]),
        last_seen_run=str(row["last_seen_run"]),
        extraction_profile=text_or_none(row.get("extraction_profile")),
        moved_from=text_or_none(row.get("moved_from")),
        moved_by=text_or_none(row.get("moved_by")),
        moved_at=time_from_ns(row.get("moved_at_ns")),
    )


def _claim(row: Mapping[str, Any]) -> ClaimItem:
    return ClaimItem(
        rule_id=str(row["rule_id"]),
        rule_version=str(row["rule_version"]),
        predicate=str(row["predicate"]),
        # As the claim stores it: a string stays a string, never re-read as JSON.
        object_reference=_as_stored(row.get("object_reference")),
        inference_tier=str(row["inference_tier"]),
        resolution_status=str(row["resolution_status"]),
        inputs=json_value(row.get("inputs")),
        defeaters=json_value(row.get("defeaters_json")),
    )


def _as_stored(value: Any) -> JsonValue:
    return value if value is None or isinstance(value, str) else json_value(value)


def _component(row: Mapping[str, Any]) -> ComponentItem:
    return ComponentItem(
        component_id=text_or_none(row.get("component_id")),
        component_type=str(row["component_type"]),
        normalized_value=text_or_none(row.get("normalized_value")),
        raw_value=text_or_none(row.get("raw_value")),
        occurrences=json_value(row.get("occurrences")),
    )


def _binding(row: Mapping[str, Any]) -> BindingItem:
    return BindingItem(
        component_id=text_or_none(row.get("component_id")),
        term=text_or_none(row.get("term")),
        theme_kind=text_or_none(row.get("theme_kind")),
        quality=text_or_none(row.get("quality")),
        slot_constraint=text_or_none(row.get("slot_constraint")),
        resolution_status=text_or_none(row.get("resolution_status")),
        alternative_count=row.get("alternative_count"),
    )


def _marks(context: V1Context, found: Mapping[str, Any]) -> list[QualityMarkItem]:
    """The quality marks the entry's own name carries, each worded and checked for a cancel.

    The Dictionary is read only where a binding of the quality kind is there to explain.
    """
    bindings = [row for row in found.get("bindings", []) if row.get("theme_kind") == "quality"]
    if not bindings:
        return []
    from ingence.dictionary.quality_marks import cancelled_by, quality_marks
    from ingence.dictionary.store import DictionaryStore

    marks = quality_marks(DictionaryStore.for_project(context.project))
    spelled_as = {
        str(row.get("component_id")): str(row.get("raw_value") or row.get("normalized_value") or "")
        for row in found.get("components", [])
    }
    name = str(found["entry"].get("name") or "")
    shown: list[QualityMarkItem] = []
    for row in bindings:
        held = marks.get(str(row.get("term")))
        if held is None or row.get("resolution_status") != "resolved":
            continue
        spelled = spelled_as.get(str(row.get("component_id")), "")
        shown.append(
            QualityMarkItem(
                term=held.term,
                mark=held.mark,
                spelled=spelled,
                cancelled_by=cancelled_by(name, spelled, held.negated_by),
                said=held.said,
            )
        )
    return sorted(shown, key=lambda item: (item.term, item.spelled))


def _identifier(query: Mapping[str, Sequence[str]]) -> str:
    identifier = (one(query, "id") or "").strip()
    if not identifier:
        raise ApiError("id names the item", parameter="id")
    return identifier


@route("entry", model=EntryResponse)
def entry_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> EntryResponse:
    identifier = _identifier(query)
    # The run the page names, where it names one: an item beside a graph is read from the
    # graph's run, never from a newer one published meanwhile.
    with catalog_of(context, query) as (catalog, run_id):
        found = views.entry(catalog, identifier)
        moved = moved_said(catalog, identifier) if found.get("entry") is not None else None
    if found.get("entry") is None:
        message = f"run {run_id} holds no item {identifier}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
    return EntryResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        entry=entry_record(found["entry"]),
        claims=[_claim(row) for row in found.get("claims", [])],
        failures=[
            FailureItem(
                rule_id=str(row["rule_id"]),
                rule_version=str(row["rule_version"]),
                failure_class=str(row["failure_class"]),
                reason=str(row["reason"]),
                inputs=json_value(row.get("inputs")),
            )
            for row in found.get("failures", [])
        ],
        findings=[
            FindingItem(
                rule_id=str(row["rule_id"]),
                rule_version=str(row["rule_version"]),
                raised=json_value(row.get("raised")),
                reason=str(row["reason"]),
                inputs=json_value(row.get("inputs")),
            )
            for row in found.get("findings", [])
        ],
        components=[_component(row) for row in found.get("components", [])],
        bindings=[_binding(row) for row in found.get("bindings", [])],
        marks=_marks(context, found),
        moved=moved,
    )


@route("folder", model=FolderResponse)
def folder_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> FolderResponse:
    identifier = _identifier(query)
    limit = integer(query, "limit", default=views.LIMIT, low=1, high=views.LIMIT)
    with catalog_of(context, query) as (catalog, run_id):
        found = views.folder(catalog, identifier, limit=limit)
    if found.get("folder") is None:
        message = f"run {run_id} holds no folder {identifier}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
    return FolderResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        folder=entry_record(found["folder"]),
        entries=[
            FolderEntry(
                id=str(row["entry_id"]),
                name=str(row["name"] or ""),
                entry_type=str(row["entry_type"]),
                extension=str(row.get("extension") or ""),
                size_bytes=row.get("size_bytes"),
                path=str(row.get("relative_path") or ""),
                claims=[_claim(claim) for claim in row.get("claims", [])],
                classes={
                    str(rule): ClassKey(key=json_value(held.get("key")), status=str(held["status"]))
                    for rule, held in sorted(row.get("classes", {}).items())
                },
                components=[_component(item) for item in row.get("components", [])],
                bindings=[_binding(item) for item in row.get("bindings", [])],
            )
            for row in found.get("entries", [])
        ],
        limit=limit,
        set_aside_by=[
            SetAsideBy(
                rule_id=str(item["rule_id"]),
                reason=str(item["reason"]),
                path=str(item["path"]),
                itself=bool(item["itself"]),
            )
            for item in found.get("set_aside_by", [])
        ],
    )
