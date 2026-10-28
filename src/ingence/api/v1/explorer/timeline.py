"""``GET /api/v1/explorer/timeline``: relationships on a true time axis, every time with its source.

The route answers two questions. What is the dated provenance of this file or group? How did an
experiment or theme change over time? It offers two readings, chosen explicitly (``reading``):

- ``thread``: one file's or folder's lineage (the Lineage route's own walk, bounded the same way),
  each file placed at its time, so a fork and a join stay visible as tracks meeting across time.
- ``activity``: a folder's files counted over time, one track per child folder (or per experiment
  unit beneath it), in bins whose width follows the span shown.

Times from different sources are never merged (the entry card's rule): a rule's earliest credible
time, an instrument's or a document's own clock, the filesystem's modification and birth times,
and a day read from a name or a folder's name. Each mark carries its source and how precisely it
is known. The series ``best`` (earliest credible, else a content clock, else the modification time)
is offered as its own series and says which source each placed mark came from. A date read from a
name is context and never a creation time. It is its own source, drawn as a day or a range, and a
name-derived clock the census read is counted there too. A file with no time from the chosen source
is counted in an ``undated`` lane, never given one.

The route reads one run at a time. Every mark is read from the run named (the pinned one by
default). The project's runs are listed on their own lane by when each began, with the one read
marked. Nothing from two runs is combined.
"""

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

import duckdb
import pyarrow as pa

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.explorer.arrange import (
    Axis,
    Where,
    axis_named,
    parse_where,
    register_people,
    scoped,
    sector_id,
    value_label,
)
from ingence.api.v1.explorer.lineage import LineageEdge, OpenEnd, lineage
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.explorer.wheel import (
    Crumb,
    _crumb,
    _decisions,
    _rows,
    _run_and_projection,
    _unit_status,
)
from ingence.api.v1.graph import PinnedRun, _pinned
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, one, time_from_ns

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.api.v1.explorer.projection import ExplorerProjection

__all__ = ["SOURCES", "TimelineResponse", "timeline"]

Reading = Literal["thread", "activity"]
Precision = Literal["instant", "day", "range"]

#: The sources, in the order the page offers them. ``best`` is a choice among the first three.
SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "best",
        "best available",
        "the earliest credible time where a rule concluded one, else a clock read from the "
        "content, else the modification time; each mark says which",
    ),
    (
        "earliest",
        "earliest credible",
        "the earliest credible time a rule concluded from the times the census read",
    ),
    ("event", "content clock", "a clock read from the content: an instrument's or a document's"),
    (
        "modified",
        "modified",
        "the filesystem's modification time: when the bytes last changed on the disk indexed, "
        "which a copy can reset",
    ),
    ("created", "born", "the filesystem's birth time, where the filesystem records one"),
    (
        "name",
        "named date",
        "a day read from the file's or its folder's name: context, never its creation time",
    ),
)
SOURCE_KEYS = tuple(key for key, _, _ in SOURCES)
#: How many bins an activity reading aims at, and the range a request may ask for.
DEFAULT_BINS = 96
BIN_LIMITS = (8, 400)
#: Tracks an activity reading draws alone; the rest are gathered into one.
DEFAULT_TRACKS = 24
TRACK_LIMITS = (1, 120)
#: The widths a bin may take, narrowest first, in seconds (a month and a year are approximate
#: here only for choosing; the edges themselves fall on calendar boundaries).
_UNITS: tuple[tuple[str, float], ...] = (
    ("hour", 3600.0),
    ("day", 86400.0),
    ("week", 7 * 86400.0),
    ("month", 30.44 * 86400.0),
    ("quarter", 91.3 * 86400.0),
    ("year", 365.25 * 86400.0),
)


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class TimeSourceDefinition(ApiModel):
    key: str
    label: str
    meaning: str


class TimeMark(ApiModel):
    """One time the run holds for a file, from one source, as precisely as it is known."""

    source: str
    #: The engine's own word for where it came from (``instrument``, ``mtime``, ``folder``...).
    detail: str | None
    at: str
    until: str | None
    precision: Precision
    #: The claim it comes from was published as conflicting (an earliest credible time whose
    #: defeater fired): placed where the chosen source puts it, and said, never passed as settled.
    conflicting: bool = False


class ThreadNode(ApiModel):
    id: Identifier
    kind: Literal["file", "folder", "aggregate"]
    name: str
    path: str
    folder_id: Identifier | None
    folder_name: str | None
    extension: str
    file_kind: str
    hop: int
    is_focus: bool
    members: Count
    #: Every time the run holds for it, one per source.
    marks: list[TimeMark]
    #: The mark it is placed by under the chosen source; None where that source has none.
    placed: TimeMark | None


class TimelineThread(ApiModel):
    nodes: list[ThreadNode]
    edges: list[LineageEdge]
    open_ends: list[OpenEnd]
    #: Files with no time from the chosen source: drawn in the undated lane. A folder or an
    #: aggregate stands for several files and is never counted here.
    undated: list[Identifier]
    lines_not_drawn: Count


class ActivityTrack(ApiModel):
    #: A child folder, ``more:<focus>`` for those gathered, or ``direct:<focus>`` for the files
    #: directly inside the focus; with rows by an axis, ``v:<value>`` for a value, ``v:`` for the
    #: files with none (kind ``none``) and ``more:`` for the values gathered.
    id: Identifier
    kind: Literal["folder", "more", "direct", "value", "none"]
    name: str
    path: str
    files: Count
    unit_status: str
    #: The span a rule read from the folder's own name, where it read one: context.
    name_from: str | None
    name_to: str | None
    #: Days a rule read the folder as copied in bulk.
    copied_on: list[str]


class TimelineBins(ApiModel):
    unit: str
    #: ``len(edges) = bins + 1``, ISO 8601 with zone.
    edges: list[str]


class TimelineActivity(ApiModel):
    tracks: list[ActivityTrack]
    bins: TimelineBins
    #: Per source, per track, per bin: files whose time from that source falls in the bin.
    counts: dict[str, list[list[Count]]]
    #: Per source, per track: files with no time from that source (the undated lane).
    undated: dict[str, list[Count]]
    #: Per source, per track: files whose time from that source falls outside the window.
    outside: dict[str, list[Count]]
    #: Tracks gathered into ``more``.
    tracks_gathered: Count


class RunMark(ApiModel):
    run_id: Identifier
    label: str
    began: str | None
    #: The run every mark here was read from.
    is_read: bool
    #: The run the project's pointer names now.
    is_current: bool


class TimeExtent(ApiModel):
    start: str
    end: str


@register("explorer_timeline")
class TimelineResponse(ApiModel):
    """``GET /api/v1/explorer/timeline``: one run's times for a file's thread or a folder's
    activity, every mark with its source."""

    schema_version: str
    run_id: Identifier
    pinned: PinnedRun
    reading: Reading
    focus: Crumb
    focus_kind: Literal["file", "folder"]
    source: str
    sources: list[TimeSourceDefinition]
    #: Files, by source, that hold a time from it (beneath the focus, or among the thread's).
    dated: dict[str, Count]
    extent: TimeExtent | None
    #: The window a request asked for, or None for the whole extent.
    window: TimeExtent | None
    runs: list[RunMark]
    thread: TimelineThread | None
    activity: TimelineActivity | None
    notes: list[str]


# ---------------------------------------------------------------------------
# Reading.
# ---------------------------------------------------------------------------


def _moment(text: str | None, name: str) -> dt.datetime | None:
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        message = f"{name} is an ISO 8601 time; {text!r} is not one"
        raise ApiError(message, parameter=name) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


#: Each source as a SQL expression over the projection's files: a TIMESTAMP in UTC, or NULL. Naive
#: timestamps in UTC throughout, so no zoned value crosses into Python (which would need a time-zone
#: library the engine does not install). Python marks each as UTC.
_TIME_SQL: dict[str, str] = {
    "earliest": "TRY_CAST(TRY_CAST(earliest_at AS TIMESTAMPTZ) AS TIMESTAMP)",
    "event": (
        "CASE WHEN event_time_ns IS NOT NULL AND coalesce(event_time_source, '') <> 'filename' "
        "THEN make_timestamp(event_time_ns // 1000) END"
    ),
    "modified": "CASE WHEN modified_ns IS NOT NULL THEN make_timestamp(modified_ns // 1000) END",
    "created": "CASE WHEN created_ns IS NOT NULL THEN make_timestamp(created_ns // 1000) END",
    "name": (
        "coalesce(TRY_CAST(TRY_CAST(name_from AS DATE) AS TIMESTAMP), "
        "CASE WHEN event_time_ns IS NOT NULL AND event_time_source = 'filename' "
        "THEN make_timestamp(event_time_ns // 1000) END)"
    ),
}
_TIME_SQL["best"] = (
    f"coalesce({_TIME_SQL['earliest']}, {_TIME_SQL['event']}, {_TIME_SQL['modified']})"
)


def _utc(value: Any) -> dt.datetime:
    """A naive timestamp DuckDB returned, which is UTC here, as a zoned one."""
    moment: dt.datetime = value
    return moment.replace(tzinfo=dt.UTC) if moment.tzinfo is None else moment


def _naive(moment: dt.datetime) -> dt.datetime:
    """A zoned moment as the naive UTC timestamp DuckDB compares against."""
    return moment.astimezone(dt.UTC).replace(tzinfo=None)


def _marks(row: Mapping[str, Any]) -> list[TimeMark]:
    """Every time the run holds for a file, one mark per source, none merged."""
    held: list[TimeMark] = []
    if row.get("earliest_at"):
        held.append(
            TimeMark(
                source="earliest",
                detail=row.get("earliest_source"),
                at=str(row["earliest_at"]),
                until=None,
                precision="instant",
                conflicting=bool(row.get("earliest_conflicting")),
            )
        )
    source = str(row.get("event_time_source") or "")
    if row.get("event_time_ns") is not None and source != "filename":
        held.append(
            TimeMark(
                source="event",
                detail=source or None,
                at=str(time_from_ns(row["event_time_ns"])),
                until=None,
                precision="instant",
            )
        )
    if row.get("modified_ns") is not None:
        held.append(
            TimeMark(
                source="modified",
                detail="mtime",
                at=str(time_from_ns(row["modified_ns"])),
                until=None,
                precision="instant",
            )
        )
    if row.get("created_ns") is not None:
        held.append(
            TimeMark(
                source="created",
                detail="birthtime",
                at=str(time_from_ns(row["created_ns"])),
                until=None,
                precision="instant",
            )
        )
    if row.get("name_from"):
        ranged = row.get("name_to") and row.get("name_to") != row.get("name_from")
        held.append(
            TimeMark(
                source="name",
                detail=row.get("name_source"),
                at=str(row["name_from"]),
                until=str(row["name_to"]) if ranged else None,
                precision="range" if ranged else "day",
            )
        )
    elif row.get("event_time_ns") is not None and source == "filename":
        held.append(
            TimeMark(
                source="name",
                detail="filename",
                at=str(time_from_ns(row["event_time_ns"])),
                until=None,
                precision="instant",
            )
        )
    return held


def _placed(marks: Sequence[TimeMark], source: str) -> TimeMark | None:
    by = {mark.source: mark for mark in marks}
    if source != "best":
        return by.get(source)
    for key in ("earliest", "event", "modified"):
        if key in by:
            return by[key]
    return None


def _runs(context: V1Context, run_id: str) -> list[RunMark]:
    from ingence.api.run_names import project_run_names

    named = project_run_names(context.project)
    return [
        RunMark(
            run_id=held["run_id"],
            label=held["label"],
            began=held["began"],
            is_read=held["run_id"] == run_id,
            is_current=held["run_id"] == context.run_current,
        )
        for held in sorted(named.values(), key=lambda item: item["number"])
    ]


def _unit(width: float) -> tuple[str, float]:
    for name, seconds in _UNITS:
        if seconds >= width:
            return name, seconds
    return _UNITS[-1]


def _floor(moment: dt.datetime, unit: str) -> dt.datetime:
    moment = moment.astimezone(dt.UTC)
    if unit == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "day":
        return day
    if unit == "week":
        return day - dt.timedelta(days=day.weekday())
    if unit == "month":
        return day.replace(day=1)
    if unit == "quarter":
        return day.replace(day=1, month=3 * ((day.month - 1) // 3) + 1)
    return day.replace(day=1, month=1)


def _step(moment: dt.datetime, unit: str) -> dt.datetime:
    if unit == "hour":
        return moment + dt.timedelta(hours=1)
    if unit == "day":
        return moment + dt.timedelta(days=1)
    if unit == "week":
        return moment + dt.timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[unit]
    total = moment.month - 1 + months
    return moment.replace(year=moment.year + total // 12, month=total % 12 + 1)


def _edges(start: dt.datetime, end: dt.datetime, bins: int) -> tuple[str, list[dt.datetime]]:
    width = max(1.0, (end - start).total_seconds()) / bins
    unit, _ = _unit(width)
    edges = [_floor(start, unit)]
    while edges[-1] <= end and len(edges) < 2000:
        edges.append(_step(edges[-1], unit))
    return unit, edges


def _safe_id(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        message = f"{identifier!r} is not an identifier"
        raise ApiError(message, parameter="focus")
    return identifier


def _activity(
    context: V1Context,
    connection: duckdb.DuckDBPyConnection,
    projection: ExplorerProjection,
    focus: Mapping[str, Any],
    *,
    bins: int,
    max_tracks: int,
    tracks_by: str,
    window: tuple[dt.datetime, dt.datetime] | None,
    source: str,
    axis: Axis | None = None,
    where: Sequence[Where] = (),
) -> tuple[TimelineActivity | None, dict[str, int], TimeExtent | None]:
    focus_id = _safe_id(str(focus["entry_id"]))
    depth = int(focus["depth"])
    decisions = _decisions(context.project)
    # The files beneath, only those passing the filters where there are any (an arranged
    # Wheel's value seen over time), each with its value on the rows' axis where rows are by one.
    held = scoped(
        files=projection.files,
        themes=projection.themes,
        focus_id=focus_id,
        focus_depth=depth,
        axes=[axis] if axis is not None else [],
        where=where,
        every_column=True,
    )
    beneath, beneath_parameters = held.sql, held.parameters
    if axis is not None:
        return _activity_by_axis(
            connection,
            focus,
            axis=axis,
            beneath=beneath,
            parameters=beneath_parameters,
            bins=bins,
            max_tracks=max_tracks,
            window=window,
            source=source,
        )
    # The tracks: the focus's child folders (or the experiment units beneath it), largest first.
    if tracks_by == "units":
        candidates = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE list_contains(ancestors, ?) "  # noqa: S608
                "AND exp_claim_id IS NOT NULL ORDER BY files DESC, relative_path",
                [focus_id],
            )
        )
    else:
        candidates = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE parent_id = ? "  # noqa: S608
                "ORDER BY files DESC, relative_path",
                [focus_id],
            )
        )
    if tracks_by == "units":
        # The outermost units only: a unit inside another is counted in the one holding it, so
        # no file belongs to two rows and every row counts what it says.
        units = {str(row["entry_id"]) for row in candidates}
        candidates = [
            row for row in candidates if not units.intersection(map(str, row["ancestors"] or ()))
        ]
    candidates = [row for row in candidates if int(row["files"])]
    drawn = candidates[:max_tracks]
    gathered = candidates[max_tracks:]
    # Every file beneath: its track (the drawn folder holding it, else gathered, else direct).
    track_of: dict[str, int] = {str(row["entry_id"]): index for index, row in enumerate(drawn)}
    table = pa.table(
        {
            "folder_id": list(track_of),
            "track": list(track_of.values()),
        }
    )
    connection.register("timeline_tracks", table)
    direct_index = len(drawn)
    more_index = len(drawn) + 1
    rows_sql = (
        f"WITH scoped AS ({beneath}), "  # noqa: S608
        "placed AS (SELECT s.entry_id, coalesce(("
        "SELECT max(t.track) FROM timeline_tracks t WHERE list_contains(s.ancestors, t.folder_id)"
        f"), CASE WHEN len(s.ancestors) > {depth + 1} THEN {more_index} ELSE {direct_index} END)"
        " AS track, s.* EXCLUDE (entry_id) FROM scoped s)"
    )
    times = ", ".join(f"{sql} AS t_{key}" for key, sql in _TIME_SQL.items())
    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE timeline_rows AS {rows_sql} "  # noqa: S608
        f"SELECT track, {times} FROM placed",
        beneath_parameters,
    )
    return _counted(
        connection,
        focus,
        drawn=drawn,
        gathered=gathered,
        tracks_by=tracks_by,
        bins=bins,
        window=window,
        source=source,
        folder_context=lambda ids: _folder_context(context, projection, ids),
        decisions=decisions,
    )


def _activity_by_axis(
    connection: duckdb.DuckDBPyConnection,
    focus: Mapping[str, Any],
    *,
    axis: Axis,
    beneath: str,
    parameters: Sequence[Any],
    bins: int,
    max_tracks: int,
    window: tuple[dt.datetime, dt.datetime] | None,
    source: str,
) -> tuple[TimelineActivity | None, dict[str, int], TimeExtent | None]:
    """Rows by an axis's values (an arrangement's axis): the largest ``max_tracks`` values, the
    files with none, and the rest gathered, each counted over time like a folder's row."""
    values = connection.execute(
        f"SELECT a0, count(*) AS n FROM ({beneath}) GROUP BY a0 ORDER BY n DESC, a0",  # noqa: S608
        list(parameters),
    ).fetchall()
    named = [(str(value), int(count)) for value, count in values if value != ""]
    drawn = named[:max_tracks]
    gathered = named[max_tracks:]
    none_files = sum(int(count) for value, count in values if value == "")
    track_of = {value: index for index, (value, _) in enumerate(drawn)}
    connection.register(
        "timeline_values",
        pa.table(
            {"value": pa.array(list(track_of), pa.string()), "track": list(track_of.values())},
            schema=pa.schema([("value", pa.string()), ("track", pa.int64())]),
        ),
    )
    none_index, more_index = len(drawn), len(drawn) + 1
    times = ", ".join(f"{sql} AS t_{key}" for key, sql in _TIME_SQL.items())
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE timeline_rows AS "  # noqa: S608
        f"WITH scoped AS ({beneath}), placed AS (SELECT coalesce(v.track, CASE WHEN s.a0 = '' "
        f"THEN {none_index} ELSE {more_index} END) AS track, s.* FROM scoped s "
        "LEFT JOIN timeline_values v ON v.value = s.a0) "
        f"SELECT track, {times} FROM placed",
        list(parameters),
    )
    rows = [
        {"entry_id": sector_id([value]), "name": value_label(axis, value), "value": value}
        for value, _ in drawn
    ]
    return _counted(
        connection,
        focus,
        drawn=rows,
        gathered=[{"files": count} for _, count in gathered],
        tracks_by=axis.key,
        bins=bins,
        window=window,
        source=source,
        folder_context=lambda ids: {},
        decisions={},
        axis=axis,
        none_files=none_files,
    )


def _counted(
    connection: duckdb.DuckDBPyConnection,
    focus: Mapping[str, Any],
    *,
    drawn: Sequence[Mapping[str, Any]],
    gathered: Sequence[Mapping[str, Any]],
    tracks_by: str,
    bins: int,
    window: tuple[dt.datetime, dt.datetime] | None,
    source: str,
    folder_context: Any,
    decisions: Mapping[str, tuple[str, str, str]],
    axis: Axis | None = None,
    none_files: int = 0,
) -> tuple[TimelineActivity | None, dict[str, int], TimeExtent | None]:
    """``timeline_rows`` (a track and every source's time per file) counted into bins.

    Every count comes from one placement. Each file's track is decided once, in SQL, and a row's
    files, its bins, its undated and its outside are all counted from that. None is taken from the
    folder's own total or found by subtraction.
    """
    focus_id = str(focus["entry_id"])
    # Files per track, counted from the rows themselves: what a row says is what it drew.
    per_track = dict(
        connection.execute("SELECT track, count(*) FROM timeline_rows GROUP BY track").fetchall()
    )
    dated = {
        key: int(
            connection.execute(
                f"SELECT count(t_{key}) FROM timeline_rows"  # noqa: S608
            ).fetchone()[0]  # type: ignore[index]
        )
        for key in SOURCE_KEYS
    }
    # The span shown is the chosen source's; where it holds no time, every source's.
    extent_row = connection.execute(
        f"SELECT min(t_{source}), max(t_{source}) FROM timeline_rows"  # noqa: S608
    ).fetchone()
    if extent_row is None or extent_row[0] is None:
        extent_row = connection.execute(
            "SELECT min(t), max(t) FROM (SELECT unnest([t_best, t_earliest, t_event, t_modified, "
            "t_created, t_name]) AS t FROM timeline_rows) WHERE t IS NOT NULL"
        ).fetchone()
    if extent_row is None or extent_row[0] is None:
        return None, dated, None
    start, end = _utc(extent_row[0]), _utc(extent_row[1])
    extent = TimeExtent(start=_iso(start), end=_iso(end))
    if window is not None:
        start, end = window
    unit, edges = _edges(start, end, bins)
    tracks_total = len(drawn) + 2
    counts: dict[str, list[list[int]]] = {}
    undated: dict[str, list[int]] = {}
    outside: dict[str, list[int]] = {}
    bin_count = len(edges) - 1
    counts = {key: [[0] * bin_count for _ in range(tracks_total)] for key in SOURCE_KEYS}
    undated = {key: [0] * tracks_total for key in SOURCE_KEYS}
    outside = {key: [0] * tracks_total for key in SOURCE_KEYS}
    # Every source at once: the rows unpivoted to (track, source, time), then counted. A bin is
    # the count of calendar boundaries of its unit since the first edge, which falls on one:
    # arithmetic, never a lookup per file.
    columns = ", ".join(f"t_{key}" for key in SOURCE_KEYS)
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE timeline_long AS "  # noqa: S608
        f"SELECT track, replace(source, 't_', '') AS source, t FROM (UNPIVOT timeline_rows "
        f"ON {columns} INTO NAME source VALUE t)"
    )
    first, last = _naive(edges[0]), _naive(edges[-1])
    for key, track, index, held in connection.execute(
        f"SELECT source, track, date_diff('{unit}', ?::TIMESTAMP, t) AS bin, count(*) "  # noqa: S608
        "FROM timeline_long WHERE t >= ?::TIMESTAMP AND t < ?::TIMESTAMP "
        "GROUP BY source, track, bin",
        [first, first, last],
    ).fetchall():
        if 0 <= int(index) < bin_count:
            counts[str(key)][int(track)][int(index)] = int(held)
    for key, track, held in connection.execute(
        "SELECT source, track, count(*) FROM timeline_long "
        "WHERE t < ?::TIMESTAMP OR t >= ?::TIMESTAMP GROUP BY source, track",
        [first, last],
    ).fetchall():
        outside[str(key)][int(track)] = int(held)
    for track, *missing in connection.execute(
        "SELECT track, "  # noqa: S608
        + ", ".join(f"count(*) FILTER (WHERE t_{key} IS NULL)" for key in SOURCE_KEYS)
        + " FROM timeline_rows GROUP BY track"
    ).fetchall():
        for key, held in zip(SOURCE_KEYS, missing, strict=True):
            undated[key][int(track)] = int(held)
    # The tracks' own context: a folder's name date and the days it was copied in bulk.
    if axis is not None:
        tracks = [
            ActivityTrack(
                id=str(row["entry_id"]),
                kind="value",
                name=str(row["name"]),
                path="",
                files=int(per_track.get(index, 0)),
                unit_status="none",
                name_from=None,
                name_to=None,
                copied_on=[],
            )
            for index, row in enumerate(drawn)
        ]
        tracks.append(
            ActivityTrack(
                id="v:",
                kind="none",
                name=axis.none_label,
                path="",
                files=int(per_track.get(len(drawn), none_files)),
                unit_status="none",
                name_from=None,
                name_to=None,
                copied_on=[],
            )
        )
        more_words = f"{len(gathered):,} more value{'' if len(gathered) == 1 else 's'}"
    else:
        folder_ids = [str(row["entry_id"]) for row in drawn]
        context_rows = folder_context(folder_ids)
        tracks = [
            ActivityTrack(
                id=str(row["entry_id"]),
                kind="folder",
                name=str(row["name"]),
                path=str(row["relative_path"] or ""),
                files=int(per_track.get(index, 0)),
                unit_status=_unit_status({**row}, decisions)[0],
                name_from=context_rows.get(str(row["entry_id"]), {}).get("from"),
                name_to=context_rows.get(str(row["entry_id"]), {}).get("to"),
                copied_on=context_rows.get(str(row["entry_id"]), {}).get("copied", []),
            )
            for index, row in enumerate(drawn)
        ]
        tracks.append(
            ActivityTrack(
                id=f"direct:{focus_id}",
                kind="direct",
                name=f"directly in {focus['name']}",
                path=str(focus["relative_path"] or ""),
                files=int(per_track.get(len(drawn), 0)),
                unit_status="none",
                name_from=None,
                name_to=None,
                copied_on=[],
            )
        )
        more_words = (
            f"{len(gathered):,} more folder{'' if len(gathered) == 1 else 's'}"
            if gathered or tracks_by == "folders"
            else "files in no unit"
        )
    tracks.append(
        ActivityTrack(
            id=f"more:{focus_id}",
            kind="more",
            name=more_words,
            path=str(focus["relative_path"] or "") if axis is None else "",
            files=int(per_track.get(len(drawn) + 1, 0)),
            unit_status="none",
            name_from=None,
            name_to=None,
            copied_on=[],
        )
    )
    return (
        TimelineActivity(
            tracks=tracks,
            bins=TimelineBins(unit=unit, edges=[_iso(edge) for edge in edges]),
            counts=counts,
            undated=undated,
            outside=outside,
            tracks_gathered=len(gathered),
        ),
        dated,
        extent,
    )


def _folder_context(
    context: V1Context, projection: ExplorerProjection, folder_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Each drawn folder's name date and bulk-copy days, from the run's claims."""
    import json

    from ingence.storage.catalog import open_catalog

    if not folder_ids:
        return {}
    found: dict[str, dict[str, Any]] = {}
    with open_catalog(context.project, run_id=projection.run_id) as catalog:
        catalog.connection.register(
            "timeline_folder_ids", pa.table({"id": pa.array(list(folder_ids), pa.string())})
        )
        for subject, predicate, value in catalog.query(
            "SELECT subject, predicate, object_reference FROM claims WHERE subject IN "
            "(SELECT id FROM timeline_folder_ids) AND predicate IN "
            "('classifies.date_neighbourhood_of_a_folder', 'classifies.folder_copied_in_bulk') "
            "ORDER BY subject, predicate, object_reference"
        ).fetchall():
            try:
                said = json.loads(value) if value else {}
            except ValueError:
                continue
            held = found.setdefault(str(subject), {"copied": []})
            if predicate.endswith("date_neighbourhood_of_a_folder"):
                held["from"] = said.get("from")
                held["to"] = said.get("to")
            elif said.get("copied_on"):
                held["copied"].append(str(said["copied_on"]))
    return found


def timeline(context: V1Context, query: Mapping[str, Sequence[str]]) -> TimelineResponse:
    reading_text = (one(query, "reading") or "activity").strip()
    if reading_text not in ("thread", "activity"):
        message = f"reading is thread or activity; {reading_text!r} is neither"
        raise ApiError(message, parameter="reading")
    reading: Reading = "thread" if reading_text == "thread" else "activity"
    source = (one(query, "source") or "best").strip()
    if source not in SOURCE_KEYS:
        message = f"source is one of {', '.join(SOURCE_KEYS)}; {source!r} is none of them"
        raise ApiError(message, parameter="source")
    bins = integer(query, "bins", default=DEFAULT_BINS, low=BIN_LIMITS[0], high=BIN_LIMITS[1])
    max_tracks = integer(
        query, "tracks", default=DEFAULT_TRACKS, low=TRACK_LIMITS[0], high=TRACK_LIMITS[1]
    )
    tracks_by = (one(query, "by") or "folders").strip()
    # Rows by folders, by experiment units, or by any axis an arrangement uses.
    rows_axis = None if tracks_by in ("folders", "units") else axis_named(tracks_by, parameter="by")
    where = parse_where(query)
    t0 = _moment(one(query, "from"), "from")
    t1 = _moment(one(query, "to"), "to")
    if (t0 is None) != (t1 is None):
        raise ApiError("a window names both from and to", parameter="from" if t0 is None else "to")
    if t0 is not None and t1 is not None and t1 <= t0:
        raise ApiError("a window's end comes after its start", parameter="to")
    window = (t0, t1) if t0 is not None and t1 is not None else None
    run_id, is_pinned, projection, _ = _run_and_projection(context, query)
    focus_id = (one(query, "focus") or "").strip()

    connection = explorer_connection()
    # A person's groups and exclusions, for the arrangement's ``h.`` axes.
    register_people(connection, context.project, run_id)
    # Every time is read and binned in UTC: the connection's own zone is UTC (``connect``).
    notes: list[str] = []
    try:
        roots = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE len(ancestors) = 0 "  # noqa: S608
                "ORDER BY root_id, relative_path LIMIT 1"
            )
        )
        if not focus_id and roots:
            focus_id = str(roots[0]["entry_id"])
        folder = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                [focus_id],
            )
        )
        file_row = (
            []
            if folder
            else _rows(
                connection.execute(
                    f"SELECT * FROM {projection.files} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                    [focus_id],
                )
            )
        )
        if not folder and not file_row:
            message = f"run {run_id} holds no live file or folder {focus_id or '(none named)'}"
            raise ApiError(
                message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="focus"
            )
        focus_kind: Literal["file", "folder"] = "folder" if folder else "file"
        focus_row = (folder or file_row)[0]
        thread: TimelineThread | None = None
        activity: TimelineActivity | None = None
        extent: TimeExtent | None = None
        dated: dict[str, int] = {}
        if reading == "activity":
            if focus_kind == "file":
                message = (
                    "the activity reading counts a folder's files over time; for one file, "
                    "choose the thread reading"
                )
                raise ApiError(message, code="not_a_folder", parameter="focus")
            activity, dated, extent = _activity(
                context,
                connection,
                projection,
                focus_row,
                bins=bins,
                max_tracks=max_tracks,
                tracks_by=tracks_by,
                window=window,
                source=source,
                axis=rows_axis,
                where=where,
            )
            if activity is None:
                notes.append("No file beneath this folder holds a time from any source.")
        else:
            walked = lineage(
                context,
                {
                    **{name: list(values) for name, values in query.items()},
                    "focus": [focus_id],
                },
            )
            ids = [node.id for node in walked.nodes if node.kind == "file"]
            connection.register("timeline_ids", pa.table({"id": pa.array(ids, pa.string())}))
            rows = {
                str(row["entry_id"]): row
                for row in _rows(
                    connection.execute(
                        f"SELECT * FROM {projection.files} "  # noqa: S608
                        "WHERE entry_id IN (SELECT id FROM timeline_ids)"
                    )
                )
            }
            nodes: list[ThreadNode] = []
            undated: list[str] = []
            moments: list[str] = []
            for node in walked.nodes:
                marks = _marks(rows[node.id]) if node.id in rows else []
                placed = _placed(marks, source)
                # Only a file is undated: an aggregate or a folder stands for several files, whose
                # times differ, and is never counted as one file with none.
                if placed is None and node.kind == "file":
                    undated.append(node.id)
                for mark in marks:
                    dated[mark.source] = dated.get(mark.source, 0) + 1
                    moments.extend(item for item in (mark.at, mark.until) if item)
                nodes.append(
                    ThreadNode(
                        id=node.id,
                        kind=node.kind,
                        name=node.name,
                        path=node.path,
                        folder_id=node.folder_id,
                        folder_name=node.folder_name,
                        extension=node.extension,
                        file_kind=node.file_kind,
                        hop=node.hop,
                        is_focus=node.is_focus,
                        members=node.members,
                        marks=marks,
                        placed=placed,
                    )
                )
            dated["best"] = sum(1 for node in nodes if _placed(node.marks, "best") is not None)
            parsed = [
                _moment(text if "T" in text else f"{text}T00:00:00Z", "at") for text in moments
            ]
            known = [moment for moment in parsed if moment is not None]
            if known:
                extent = TimeExtent(start=_iso(min(known)), end=_iso(max(known)))
            thread = TimelineThread(
                nodes=nodes,
                edges=[edge for edge in walked.edges if edge.kind != "group_member"],
                open_ends=walked.open_ends,
                undated=undated,
                lines_not_drawn=walked.truncation.lines_not_drawn,
            )
            files_drawn = sum(1 for node in nodes if node.kind == "file")
            if undated:
                notes.append(
                    f"{len(undated):,} of {files_drawn:,} files drawn have no time from this "
                    "source; they are in the undated lane, and no time is made up for them."
                )
            if files_drawn < len(nodes):
                notes.append(
                    "A folder, or files gathered together, stands for several files whose times "
                    "differ: it is not placed; open it to place each file."
                )
    finally:
        connection.close()
    if source == "name":
        notes.append("A named date is read from a name: context for when, never a creation time.")
    return TimelineResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        pinned=_pinned(context, run_id, is_pinned=is_pinned),
        reading=reading,
        focus=_crumb(focus_row),
        focus_kind=focus_kind,
        source=source,
        sources=[
            TimeSourceDefinition(key=key, label=label, meaning=meaning)
            for key, label, meaning in SOURCES
        ],
        dated={key: int(value) for key, value in dated.items()},
        extent=extent,
        window=TimeExtent(start=_iso(window[0]), end=_iso(window[1])) if window else None,
        runs=_runs(context, run_id),
        thread=thread,
        activity=activity,
        notes=notes,
    )


@route("explorer/timeline", model=TimelineResponse)
def timeline_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> TimelineResponse:
    return timeline(context, query)
