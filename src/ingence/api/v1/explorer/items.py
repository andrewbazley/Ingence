"""The Graph workspace's pages of items: a folder's children, a folder's files, one entry.

Beside the Wheel, which draws aggregates, these routes list what an aggregate holds, a page at a
time, so a dense sector is opened by paging rather than by fetching it whole:

- ``GET /api/v1/explorer/children?folder=&offset=&limit=``: a folder's child folders, largest
  first, each with its counts per measure. This is what a ``more`` sector gathered.
- ``GET /api/v1/explorer/files?folder=&scope=direct|beneath&offset=&limit=``: a folder's files in
  the Wheel's own order (kind, then examination, then name), so a perimeter bin's ``start`` and
  ``n`` name exactly the files it holds. With ``where=`` and ``not=`` (an arranged Wheel's
  sector), only the files with those values.
- ``GET /api/v1/explorer/entry?id=``: one file or folder, with its exact name and path, its folders
  above it, its category per measure, and every time the run holds for it, each with its source.

Every response names the run it read. Nothing here writes.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

import duckdb

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.explorer.arrange import parse_where, register_people, scoped
from ingence.api.v1.explorer.categories import EXAM, KIND, MEASURES
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.explorer.wheel import (
    Crumb,
    MeasureDefinition,
    SectorExperiment,
    _counts,
    _crumb,
    _decisions,
    _empty_experiment,
    _measure_definitions,
    _rows,
    _run_and_projection,
    _sql_list,
    _unit_status,
)
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.moved import MovedSaid, moved_in_run
from ingence.api.v1.shaping import integer, one, time_from_ns

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["ChildrenResponse", "ClaimCard", "EntryCard", "FilesResponse"]

PAGE_LIMITS = (1, 500)
DEFAULT_PAGE = 100


class FolderRow(ApiModel):
    id: Identifier
    name: str
    path: str
    files: Count
    folders: Count
    bytes: Count
    counts: dict[str, list[Count]]
    unit_status: str


@register("explorer_children")
class ChildrenResponse(ApiModel):
    """``GET /api/v1/explorer/children``: a folder's child folders, a page at a time."""

    schema_version: str
    run_id: Identifier
    folder: Crumb
    total: Count
    offset: Count
    children: list[FolderRow]


class FileRow(ApiModel):
    id: Identifier
    name: str
    path: str
    extension: str
    kind: str
    format: str | None
    exam: str
    stage: str
    prov: str
    bytes: Count
    is_archive_link: bool


@register("explorer_files")
class FilesResponse(ApiModel):
    """``GET /api/v1/explorer/files``: a folder's files in the Wheel's order, a page at a time."""

    schema_version: str
    run_id: Identifier
    folder: Crumb
    scope: Literal["direct", "beneath"]
    total: Count
    offset: Count
    files: list[FileRow]


class EntryTime(ApiModel):
    """One time the run holds for an entry, never merged with another."""

    #: ``modified`` (the filesystem's modification time), ``created`` (its birth time, where
    #: the filesystem records one), ``event`` (a clock read from the content or the name),
    #: ``earliest`` (the earliest credible time a rule concluded), ``name_date`` (a day or a
    #: range of days read from its name or its folder's), each a different source.
    kind: str
    #: ISO 8601 with its zone for an instant; a day (``2026-01-02``) for a name's date.
    at: str | None
    until: str | None
    #: Where it came from, in the engine's words (``mtime``, ``instrument``, ``folder``...).
    source: str | None
    #: ``instant``, ``day`` or ``range``: how precisely it is known.
    precision: Literal["instant", "day", "range"]
    note: str


@register("explorer_entry")
class EntryCard(ApiModel):
    """``GET /api/v1/explorer/entry``: one file or folder, as every mode's inspector shows it."""

    schema_version: str
    run_id: Identifier
    id: Identifier
    entry_type: Literal["file", "folder"]
    name: str
    path: str
    root_id: Identifier
    #: The folders above it, outermost first.
    ancestors: list[Crumb]
    extension: str
    kind: str | None
    #: The format the run reads, where one; with ``formats``, every format read, and whether
    #: the reading is in conflict (published as conflicting, or two families read): then the
    #: kind is ``conflicting`` and ``format`` is one reading among them, never the settled one.
    format: str | None
    formats: list[str]
    format_conflicting: bool
    #: Per measure, the file's own category; for a folder, None (its counts are in ``counts``).
    categories: dict[str, str] | None
    counts: dict[str, list[Count]] | None
    files: Count
    folders: Count
    bytes: Count
    is_archive_link: bool
    link_target: str | None
    times: list[EntryTime]
    experiment: SectorExperiment | None
    #: Where it came from and how that was known, in the Dashboard's words.
    moved: MovedSaid | None = None


def _folder(connection: duckdb.DuckDBPyConnection, folders: str, folder_id: str) -> dict[str, Any]:
    found = _rows(
        connection.execute(
            f"SELECT * FROM {folders} WHERE entry_id = ? LIMIT 1",  # noqa: S608
            [folder_id],
        )
    )
    if not found:
        message = f"this run holds no live folder {folder_id}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="folder")
    return found[0]


def children(context: V1Context, query: Mapping[str, Sequence[str]]) -> ChildrenResponse:
    folder_id = (one(query, "folder") or "").strip()
    if not folder_id:
        raise ApiError("folder names the folder whose children to list", parameter="folder")
    offset = integer(query, "offset", default=0, low=0, high=10**9)
    limit = integer(query, "limit", default=DEFAULT_PAGE, low=PAGE_LIMITS[0], high=PAGE_LIMITS[1])
    run_id, _, projection, _ = _run_and_projection(context, query)
    decisions = _decisions(context.project)
    connection = explorer_connection()
    try:
        parent = _folder(connection, projection.folders, folder_id)
        total_row = connection.execute(
            f"SELECT count(*) FROM {projection.folders} WHERE parent_id = ?",  # noqa: S608
            [folder_id],
        ).fetchone()
        rows = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE parent_id = ? "  # noqa: S608
                "ORDER BY files DESC, name, entry_id LIMIT ? OFFSET ?",
                [folder_id, limit, offset],
            )
        )
    finally:
        connection.close()
    return ChildrenResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        folder=_crumb(parent),
        total=int(total_row[0]) if total_row else 0,
        offset=offset,
        children=[
            FolderRow(
                id=str(row["entry_id"]),
                name=str(row["name"]),
                path=str(row["relative_path"] or ""),
                files=int(row["files"]),
                folders=int(row["folders"]),
                bytes=int(row["bytes"]),
                counts=_counts(row),
                unit_status=_unit_status(row, decisions)[0],
            )
            for row in rows
        ],
    )


def files(context: V1Context, query: Mapping[str, Sequence[str]]) -> FilesResponse:
    folder_id = (one(query, "folder") or "").strip()
    if not folder_id:
        raise ApiError("folder names the folder whose files to list", parameter="folder")
    scope_text = (one(query, "scope") or "direct").strip()
    if scope_text not in ("direct", "beneath"):
        message = f"scope is direct or beneath; {scope_text!r} is neither"
        raise ApiError(message, parameter="scope")
    scope: Literal["direct", "beneath"] = "direct" if scope_text == "direct" else "beneath"
    offset = integer(query, "offset", default=0, low=0, high=10**9)
    limit = integer(query, "limit", default=DEFAULT_PAGE, low=PAGE_LIMITS[0], high=PAGE_LIMITS[1])
    run_id, _, projection, _ = _run_and_projection(context, query)
    where = "folder_id = ?" if scope == "direct" else "list_contains(ancestors, ?)"
    parameters: list[Any] = [folder_id]
    # A Timeline bin's files: those whose time from one source falls in a window.
    source = (one(query, "source") or "").strip()
    window = (one(query, "from"), one(query, "to"))
    if source or any(window):
        from ingence.api.v1.explorer.timeline import _TIME_SQL, _moment, _naive

        if source not in _TIME_SQL or not all(window):
            message = "a window names a source and both from and to"
            raise ApiError(message, parameter="source")
        start, end = _moment(window[0], "from"), _moment(window[1], "to")
        assert start is not None and end is not None
        where += f" AND {_TIME_SQL[source]} >= ?::TIMESTAMP AND {_TIME_SQL[source]} < ?::TIMESTAMP"
        parameters += [_naive(start), _naive(end)]
    # An arranged Wheel's sector: only the files whose values match (``where``), and for a ``more``
    # sector all but the values drawn beside it (``not``). See ``explorer/arrange``.
    filters = parse_where(query)
    connection = explorer_connection()
    # A person's groups and exclusions, for the arrangement's ``h.`` axes.
    register_people(connection, context.project, run_id)
    try:
        folder = _folder(connection, projection.folders, folder_id)
        source_sql = projection.files
        if filters:
            if scope != "beneath":
                message = "a filtered page of files reads beneath the folder (scope=beneath)"
                raise ApiError(message, parameter="scope")
            held = scoped(
                files=projection.files,
                themes=projection.themes,
                focus_id=folder_id,
                focus_depth=int(folder["depth"]),
                axes=[],
                where=filters,
                every_column=True,
            )
            source_sql = f"({held.sql})"
            where = "true" + where.removeprefix("list_contains(ancestors, ?)")
            parameters = [*held.parameters, *parameters[1:]]
        total_row = connection.execute(
            f"SELECT count(*) FROM {source_sql} WHERE {where}",  # noqa: S608
            parameters,
        ).fetchone()
        rows = _rows(
            connection.execute(
                f"SELECT * FROM {source_sql} WHERE {where} "  # noqa: S608
                f"ORDER BY list_position({_sql_list(KIND.category_keys())}, kind), "
                f"list_position({_sql_list(EXAM.category_keys())}, exam), name, entry_id "
                "LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            )
        )
    finally:
        connection.close()
    return FilesResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        folder=_crumb(folder),
        scope=scope,
        total=int(total_row[0]) if total_row else 0,
        offset=offset,
        files=[_file_row(row) for row in rows],
    )


def _file_row(row: Mapping[str, Any]) -> FileRow:
    return FileRow(
        id=str(row["entry_id"]),
        name=str(row["name"]),
        path=str(row["relative_path"] or ""),
        extension=str(row["extension"] or ""),
        kind=str(row["kind"]),
        format=row["format"],
        exam=str(row["exam"]),
        stage=str(row["stage"]),
        prov=str(row["prov"]),
        bytes=int(row["bytes"] or 0),
        is_archive_link=bool(row["is_archive_link"]),
    )


def _times(row: Mapping[str, Any]) -> list[EntryTime]:
    """Every time the run holds for a file, each from its own source, none merged."""
    held: list[EntryTime] = []
    if row.get("modified_ns") is not None:
        held.append(
            EntryTime(
                kind="modified",
                at=time_from_ns(row["modified_ns"]),
                until=None,
                source="mtime",
                precision="instant",
                note="the filesystem's last modification time: when the bytes last changed "
                "on the disk that was indexed, which a copy can reset",
            )
        )
    if row.get("created_ns") is not None:
        held.append(
            EntryTime(
                kind="created",
                at=time_from_ns(row["created_ns"]),
                until=None,
                source="birthtime",
                precision="instant",
                note="the filesystem's birth time, where it records one",
            )
        )
    if row.get("event_time_ns") is not None:
        source = str(row.get("event_time_source") or "")
        held.append(
            EntryTime(
                kind="event",
                at=time_from_ns(row["event_time_ns"]),
                until=None,
                source=source or None,
                precision="instant",
                note=(
                    "a clock the engine read for the event the file records"
                    + (
                        " (from its name: a date written in a name is context, never proof of "
                        "when it was made)"
                        if source == "filename"
                        else ""
                    )
                ),
            )
        )
    if row.get("earliest_at"):
        held.append(
            EntryTime(
                kind="earliest",
                at=str(row["earliest_at"]),
                until=None,
                source=row.get("earliest_source"),
                precision="instant",
                note=(
                    "the earliest credible time a rule concluded from the times above"
                    + (" (published as conflicting)" if row.get("earliest_conflicting") else "")
                    # Why a time above was passed over, as the rule said it: the date inside a
                    # document that is its blank template's.
                    + (
                        f"; {row['earliest_not_believed']}"
                        if row.get("earliest_not_believed")
                        else ""
                    )
                ),
            )
        )
    if row.get("name_from"):
        ranged = row.get("name_to") and row.get("name_to") != row.get("name_from")
        held.append(
            EntryTime(
                kind="name_date",
                at=str(row["name_from"]),
                until=str(row["name_to"]) if ranged else None,
                source=row.get("name_source"),
                precision="range" if ranged else "day",
                note="a day read from its name or its folder's name: context, never its "
                "creation time"
                + (
                    f" (one of {int(row['name_dates']):,} dates its names hold, the earliest; "
                    "never merged into one span)"
                    if int(row.get("name_dates") or 0) > 1
                    else ""
                ),
            )
        )
    return held


def entry(context: V1Context, query: Mapping[str, Sequence[str]]) -> EntryCard:
    identifier = (one(query, "id") or "").strip()
    if not identifier:
        raise ApiError("id names the file or folder", parameter="id")
    run_id, _, projection, _ = _run_and_projection(context, query)
    connection = explorer_connection()
    try:
        file_rows = _rows(
            connection.execute(
                f"SELECT * FROM {projection.files} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                [identifier],
            )
        )
        folder_rows = (
            []
            if file_rows
            else _rows(
                connection.execute(
                    f"SELECT * FROM {projection.folders} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                    [identifier],
                )
            )
        )
        if not file_rows and not folder_rows:
            message = f"run {run_id} holds no live file or folder {identifier}"
            raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
        row = file_rows[0] if file_rows else folder_rows[0]
        chain = [str(value) for value in row["ancestors"] or ()]
        above = {
            str(found["entry_id"]): found
            for found in _rows(
                connection.execute(
                    f"SELECT entry_id, name, relative_path FROM {projection.folders} "  # noqa: S608
                    "WHERE list_contains(?, entry_id)",
                    [chain],
                )
            )
        }
        # Where it came from, on the connection already open.
        moved = moved_in_run(context.project, run_id, identifier, connection=connection)
    finally:
        connection.close()
    ancestors = [_crumb(above[key]) for key in chain if key in above]
    if file_rows:
        return EntryCard(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            id=identifier,
            entry_type="file",
            name=str(row["name"]),
            path=str(row["relative_path"] or ""),
            root_id=str(row["root_id"]),
            ancestors=ancestors,
            extension=str(row["extension"] or ""),
            kind=str(row["kind"]),
            format=row["format"],
            formats=[str(value) for value in row["formats"] or ()],
            format_conflicting=bool(row["format_conflicting"]),
            categories={measure.key: str(row[measure.key]) for measure in MEASURES},
            counts=None,
            files=1,
            folders=0,
            bytes=int(row["bytes"] or 0),
            is_archive_link=bool(row["is_archive_link"]),
            link_target=row["symlink_target"] if row["is_archive_link"] else None,
            times=_times(row),
            experiment=None,
            moved=moved,
        )
    status, decided = _unit_status(row, _decisions(context.project))
    experiment = None
    if status != "none" or row["proposed_groups"]:
        experiment = SectorExperiment(
            **{
                **_empty_experiment(),
                "status": status,
                "unit_kind": row["exp_kind"],
                "rules": sorted(r for r in str(row["exp_rules"] or "").split(",") if r),
                "claim_id": str(row["exp_claim_id"]) if row["exp_claim_id"] else None,
                "decided_by": decided[1] if decided else None,
                "decided_at": decided[2] if decided else None,
                "units": 1 if status != "none" else 0,
                "proposed_groups": int(row["proposed_groups"] or 0),
            }
        )
    return EntryCard(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        id=identifier,
        entry_type="folder",
        name=str(row["name"] or row["relative_path"] or identifier),
        path=str(row["relative_path"] or ""),
        root_id=str(row["root_id"]),
        ancestors=ancestors,
        extension="",
        kind=None,
        format=None,
        formats=[],
        format_conflicting=False,
        categories=None,
        counts=_counts(row),
        files=int(row["files"]),
        folders=int(row["folders"]),
        bytes=int(row["bytes"]),
        is_archive_link=False,
        link_target=None,
        times=[],
        experiment=experiment,
        moved=moved,
    )


class ClaimDecision(ApiModel):
    kind: str
    actor: str
    at: str
    note: str | None


@register("explorer_claim")
class ClaimCard(ApiModel):
    """``GET /api/v1/explorer/claim?id=``: one published conclusion, with its rule and evidence.

    What a group's membership line in Lineage rests on: the claim that put a file in the group,
    the rule and version that concluded it, its tier, its support and what would retract it,
    and the Record's latest decision on it, where a person made one.
    """

    schema_version: str
    run_id: Identifier
    id: Identifier
    subject: Identifier
    predicate: str
    #: The claim's object as the rule wrote it: a sense, a key, or structured JSON as text.
    object_reference: str | None
    rule_id: str
    rule_version: str
    rule_statement: str | None
    tier: str
    resolution_status: str
    support: list[str]
    defeaters: list[str]
    decision: ClaimDecision | None


def claim(context: V1Context, query: Mapping[str, Sequence[str]]) -> ClaimCard:
    import json

    from ingence.api.v1.graph import _statement
    from ingence.record.log import latest_by_target, read_events
    from ingence.storage.catalog import open_catalog

    identifier = (one(query, "id") or "").strip().removeprefix("member:").removeprefix("claim:")
    if not identifier:
        raise ApiError("id names the conclusion", parameter="id")
    run_id, _, _, _ = _run_and_projection(context, query)
    with open_catalog(context.project, run_id=run_id) as catalog:
        rows = _rows(catalog.query("SELECT * FROM claims WHERE claim_id = ? LIMIT 1", [identifier]))
        if not rows:
            message = f"run {run_id} holds no conclusion {identifier}"
            raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
        row = rows[0]
        statement = _statement(catalog, str(row["rule_id"]), str(row["rule_version"]))

    def said(text: Any, key: str) -> list[str]:
        held = json.loads(text) if isinstance(text, str) and text else []
        return [str(item.get(key) or item) for item in held or ()]

    event = latest_by_target(read_events(context.project)).get(f"claim:{identifier}")
    decision = None
    if event is not None:
        payload = json.loads(event.payload) if isinstance(event.payload, str) else {}
        decision = ClaimDecision(
            kind=event.kind.value,
            actor=event.actor,
            at=event.occurred_at,
            note=payload.get("note") if isinstance(payload, dict) else None,
        )
    return ClaimCard(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        id=identifier,
        subject=str(row["subject"]),
        predicate=str(row["predicate"]),
        object_reference=row["object_reference"],
        rule_id=str(row["rule_id"]),
        rule_version=str(row["rule_version"]),
        rule_statement=statement,
        tier=str(row["inference_tier"]),
        resolution_status=str(row["resolution_status"]),
        support=said(row.get("support_json"), "name"),
        defeaters=said(row.get("defeaters_json"), "reason"),
        decision=decision,
    )


@register("explorer_measures")
class MeasuresResponse(ApiModel):
    """``GET /api/v1/explorer/measures``: what each measure means, category by category.

    The same definitions the Wheel sends, for a mode that draws no Wheel: every inspector names
    a file's categories in these words.
    """

    schema_version: str
    measures: list[MeasureDefinition]


@route("explorer/measures", model=MeasuresResponse)
def measures_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> MeasuresResponse:
    del context, query
    return MeasuresResponse(schema_version=SCHEMA_VERSION, measures=_measure_definitions())


@route("explorer/claim", model=ClaimCard)
def claim_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> ClaimCard:
    return claim(context, query)


@route("explorer/children", model=ChildrenResponse)
def children_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> ChildrenResponse:
    return children(context, query)


@route("explorer/files", model=FilesResponse)
def files_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> FilesResponse:
    return files(context, query)


@route("explorer/entry", model=EntryCard)
def entry_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> EntryCard:
    return entry(context, query)
