"""``GET /api/v1/explorer/wheel``: a folder's shape at a glance, aggregate first.

The Wheel answers three questions. What is in this folder? How many experiment units are there?
What has been examined or joined to analysis? It answers from the run's explorer projection
(:mod:`~ingence.api.v1.explorer.projection`) and never from the run's entries whole:

- Sectors. The chosen folder is at the center and its folders are in rings outward, each sized by
  what it holds beneath (files, or bytes). Detail falls with distance. A ring shows a folder only
  where its share of the whole is wide enough to see (``min_angle``) and the response has room
  (``max_sectors``). The rest of a folder's children are gathered into one ``more`` sector beside
  them, with exact sums. ``max_sectors`` is a cap: a "more" sector takes one of its slots, and
  where none is left a folder's children are counted in it. Folders deeper than the last ring are
  counted in the ring's sector that holds them.
- Bins. Every file beneath the center is counted at the perimeter, in the outermost sector that
  holds it, ordered by kind and then by whether it was examined. The files are gathered into bins
  of neighboring files: at most ``max_bins``, and one more for each sector holding a file, since a
  bin never spans two sectors (``BINS_BOUND``). A bin carries its size and, per measure, the
  category most of its files share and how many do (``purity``), so a mixed bin never reads as
  pure.
- Measures. There are four (:mod:`~ingence.api.v1.explorer.categories`), each with its question,
  its denominator and its categories, counted exactly per sector.
- Experiment units. A folder that a rule reads as an experiment's folder is ``supported``, or
  ``conflicting`` where the claim was published so. A person's confirmation or rejection in the
  Record (read on every request, so it shows at once) makes it ``confirmed`` or ``rejected``. A
  grouping that the engine proposed and nobody approved is a proposal, counted apart and never as a
  unit.
- Provenance. The run's published derivations and mentions, and its proposed pairs, are counted
  apart. Where the run published none, the response says so in words.

Every count is exact, and what the caps left out is counted exactly too. The response names the
one run it read.
"""

from __future__ import annotations

import time
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
    parse_axes,
    parse_where,
    register_people,
    value_label,
)
from ingence.api.v1.explorer.arranged import arranged
from ingence.api.v1.explorer.categories import EXAM, KIND, MEASURES
from ingence.api.v1.explorer.projection import (
    ExplorerProjection,
    projection_for,
    read_projection,
)
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.graph import PinnedRun, _pinned
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, one, run_named
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = [
    "DEFAULT_MAX_BINS",
    "DEFAULT_MAX_SECTORS",
    "DEFAULT_MIN_ANGLE",
    "DEFAULT_RINGS",
    "WheelResponse",
    "explorer_projection",
    "wheel",
]

#: Rings outward from the center a request starts from, and the range it may ask for.
DEFAULT_RINGS = 4
RING_LIMITS = (1, 8)
#: Sectors a response may hold. Above a few thousand a wheel is no longer readable, and the
#: table beside it pages.
DEFAULT_MAX_SECTORS = 720
SECTOR_LIMITS = (8, 4000)
#: Perimeter bins. Thousands of marks, each a run of neighboring files; never one per file.
DEFAULT_MAX_BINS = 2400
#: What ``max_bins`` bounds, in the response's words: a bin never spans two sectors, so a
#: sector holding a file has a bin of its own even where its share of ``max_bins`` is none.
BINS_BOUND = "at most the bins allowed, and one more for each wedge holding a file"
BIN_LIMITS = (0, 8000)
#: The narrowest sector drawn on its own, in degrees; narrower ones are gathered into ``more``.
DEFAULT_MIN_ANGLE = 0.35
ANGLE_LIMITS = (0.0, 20.0)

SizeBy = Literal["files", "bytes"]
UnitStatus = Literal["supported", "conflicting", "confirmed", "rejected", "none"]


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class MeasureCategory(ApiModel):
    key: str
    label: str
    #: How the page draws it: ``yes``, ``partial``, ``problem``, ``elsewhere``, ``no``,
    #: ``unknown``, or ``kind`` for a format family.
    level: str
    meaning: str


class MeasureDefinition(ApiModel):
    key: str
    label: str
    question: str
    denominator: str
    #: The category that stands for "the run could not say".
    unknown: str
    categories: list[MeasureCategory]


class Crumb(ApiModel):
    id: Identifier
    name: str
    #: The folder's path under its root, exactly as the census recorded it.
    path: str


class SectorExperiment(ApiModel):
    """Whether a sector's folder is an experiment unit, and how many lie beneath it."""

    #: The folder's own standing: ``none`` where no rule reads it as an experiment's folder.
    status: UnitStatus
    #: What the rule read it as holding: ``experiment``, ``acquisitions`` or ``analysis``.
    unit_kind: str | None
    rules: list[str]
    claim_id: Identifier | None
    #: Who confirmed or rejected it in the Record, and when, where somebody did.
    decided_by: str | None
    decided_at: str | None
    #: Units at or beneath the sector (itself included), by standing.
    units: Count
    conflicting: Count
    confirmed: Count
    rejected: Count
    #: Groupings of experiments the engine proposed at or beneath the sector: proposals, never
    #: units.
    proposed_groups: Count


class WheelSector(ApiModel):
    #: The folder's entry, or ``more:<parent>`` for the folders a ring gathered together; in an
    #: arranged Wheel, ``v:<value>/<value>...`` for a value (its values from the center out).
    id: Identifier
    parent: Identifier | None
    ring: Count
    kind: Literal["folder", "more", "value"]
    name: str
    path: str
    files: Count
    #: Folders beneath it (itself not counted). For ``more``, the gathered folders and theirs.
    folders: Count
    bytes: Count
    direct_files: Count
    direct_folders: Count
    #: Of its child folders, how many have sectors of their own in the next ring.
    children_shown: Count
    #: For ``more``: how many folders it gathers.
    gathered: Count
    #: Which of its files the perimeter counts at it: ``all`` (it is outermost), ``direct``
    #: (only those directly inside; the rest lie in its children's sectors) or ``none``.
    leaf: Literal["all", "direct", "none"]
    #: Per measure, the count in each category, in the measure's category order.
    counts: dict[str, list[Count]]
    #: None where no unit or proposal lies at or beneath it.
    experiment: SectorExperiment | None
    #: In an arranged Wheel: the axis the sector's ring answers and its value ('' for none, and for
    #: ``folder`` '' is "directly in the folder above"), and the folder a ``folder`` or ``unit``
    #: value names.
    axis: str | None = None
    value: str | None = None
    entry_id: Identifier | None = None


class AxisInfo(ApiModel):
    """One axis an arrangement can use: the question its ring answers about each file."""

    key: str
    label: str
    #: ``structure``, ``units``, ``names``, ``spelling``, ``readings`` or ``time``.
    family: str
    question: str
    #: The words for a file with no value on this axis.
    none_label: str


class WhereInfo(ApiModel):
    """A filter the Wheel applied: only files whose value on ``axis`` is ``value``."""

    axis: str
    #: The folder level a ``folder`` filter reads (1 for the first beneath the center), else 0.
    level: Count
    value: str
    label: str
    negated: bool


class WheelArrangement(ApiModel):
    """How an arranged Wheel was built: its rings' axes, center outward, and its filters."""

    axes: list[AxisInfo]
    where: list[WhereInfo]
    #: Files beneath the center before the filters, so a filtered Wheel says what it left out.
    files_beneath: Count


class WheelBins(ApiModel):
    """The perimeter, column by column: bin ``i`` is ``sector[i]``, ``start[i]``, ``n[i]``..."""

    #: The index in ``sectors`` of the sector whose files the bin holds.
    sector: list[Count]
    #: The rank, within that sector's files in the Wheel's order, of the bin's first file.
    start: list[Count]
    n: list[Count]
    bytes: list[Count]
    #: Per measure, the index of the category most of the bin's files share.
    dominant: dict[str, list[Count]]
    #: Per measure, the percentage of the bin's files in that category (100 for a pure bin).
    purity: dict[str, list[Count]]


class WheelTotals(ApiModel):
    files: Count
    folders: Count
    bytes: Count
    counts: dict[str, list[Count]]
    experiment: SectorExperiment
    #: The run's lines of provenance, whole (not only beneath the center).
    published_derivations: Count
    published_mentions: Count
    proposed_pairs: Count
    #: What the run holds of provenance, in words: "none published" says so.
    provenance: str


class WheelCaps(ApiModel):
    rings: Count
    max_sectors: Count
    max_bins: Count
    min_angle: float
    size_by: SizeBy
    sectors_shown: Count
    #: Folders within the rings, beneath the center.
    folders_in_rings: Count
    #: Of those, how many are not drawn alone: gathered into ``more`` sectors, or, where the cap
    #: left no room even for one, counted in the folder holding them.
    folders_gathered: Count
    #: Folders deeper than the last ring, counted in the sector that holds them.
    folders_deeper: Count
    bins_shown: Count
    message: str


class ProjectionState(ApiModel):
    #: Whether this request built the run's projection (the first request for a run does).
    built_now: bool
    build_seconds: float
    #: How long the server took to answer, in milliseconds.
    answered_ms: float


@register("explorer_wheel")
class WheelResponse(ApiModel):
    """``GET /api/v1/explorer/wheel``: one folder's shape, in sectors and perimeter bins."""

    schema_version: str
    run_id: Identifier
    pinned: PinnedRun
    focus: Crumb
    #: The folders above the center, outermost first, ending with the center's parent.
    breadcrumb: list[Crumb]
    #: Every root's folder, so the page can start from another.
    roots: list[Crumb]
    measures: list[MeasureDefinition]
    #: Ring by ring, each ring's sectors in drawing order around the circle.
    sectors: list[WheelSector]
    bins: WheelBins
    totals: WheelTotals
    caps: WheelCaps
    projection: ProjectionState
    #: None for the folder Wheel; the axes and filters of an arranged one.
    arrangement: WheelArrangement | None = None


# ---------------------------------------------------------------------------
# Reading the request, and the projection.
# ---------------------------------------------------------------------------


def _decimal(
    query: Mapping[str, Sequence[str]], name: str, *, default: float, low: float, high: float
) -> float:
    text = one(query, name)
    if text is None or text == "":
        return default
    try:
        value = float(text)
    except ValueError:
        message = f"{name} must be a number from {low} to {high}; {text!r} is not one"
        raise ApiError(message, parameter=name) from None
    if not low <= value <= high:
        message = f"{name} must be from {low} to {high}; {value} is outside that"
        raise ApiError(message, parameter=name)
    return value


def explorer_projection(project: Path, run_id: str) -> tuple[ExplorerProjection, bool]:
    """The run's projection, and whether this call built it."""
    found = read_projection(project, run_id)
    if found is not None:
        return found, False
    with open_catalog(project, run_id=run_id) as catalog:
        built = projection_for(catalog)
    return built, True


def _run_and_projection(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> tuple[str, bool, ExplorerProjection, bool]:
    run_id, is_pinned = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet: index it, and the Wheel reads that"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    projection, built_now = explorer_projection(context.project, run_id)
    return run_id, is_pinned, projection, built_now


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _measure_definitions() -> list[MeasureDefinition]:
    return [
        MeasureDefinition(
            key=measure.key,
            label=measure.label,
            question=measure.question,
            denominator=measure.denominator,
            unknown=measure.unknown,
            categories=[
                MeasureCategory(
                    key=category.key,
                    label=category.label,
                    level=category.level,
                    meaning=category.meaning,
                )
                for category in measure.categories
            ],
        )
        for measure in MEASURES
    ]


def _counts(row: Mapping[str, Any]) -> dict[str, list[int]]:
    return {
        measure.key: [int(row.get(f"{measure.key}_{key}") or 0) for key in measure.category_keys()]
        for measure in MEASURES
    }


def _crumb(row: Mapping[str, Any]) -> Crumb:
    return Crumb(
        id=str(row["entry_id"]),
        name=str(row["name"] or row["relative_path"] or row["entry_id"]),
        path=str(row["relative_path"] or ""),
    )


# ---------------------------------------------------------------------------
# Experiment units, with the Record's decisions read now.
# ---------------------------------------------------------------------------


def _decisions(project: Path) -> dict[str, tuple[str, str, str]]:
    """The Record's latest confirm or reject on each claim: (kind, actor, when), by claim id."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import latest_by_target, read_events

    found: dict[str, tuple[str, str, str]] = {}
    for target, event in latest_by_target(read_events(project)).items():
        if not target.startswith("claim:"):
            continue
        if event.kind in (RecordEventKind.CONFIRM, RecordEventKind.REJECT):
            found[target.split(":", 1)[1]] = (event.kind.value, event.actor, event.occurred_at)
    return found


def _unit_status(
    row: Mapping[str, Any], decisions: Mapping[str, tuple[str, str, str]]
) -> tuple[UnitStatus, tuple[str, str, str] | None]:
    if not row["exp_claim_id"]:
        return "none", None
    for claim_id in row["exp_claim_ids"] or ():
        decided = decisions.get(str(claim_id))
        if decided is not None:
            return ("confirmed" if decided[0] == "confirm" else "rejected"), decided
    return ("conflicting" if row["exp_conflicting"] else "supported"), None


def _empty_experiment() -> dict[str, Any]:
    return {
        "status": "none",
        "unit_kind": None,
        "rules": [],
        "claim_id": None,
        "decided_by": None,
        "decided_at": None,
        "units": 0,
        "conflicting": 0,
        "confirmed": 0,
        "rejected": 0,
        "proposed_groups": 0,
    }


# ---------------------------------------------------------------------------
# The Wheel.
# ---------------------------------------------------------------------------


def _sql_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(f"'{value}'" for value in values) + "]"


#: The measures a bin's histograms are read for, in the order the query returns them.
_BINNED = ("kind", "exam", "stage", "prov")
#: Each measure's categories by their place in its order, for a bin's dominant category.
_POSITIONS: dict[str, dict[str, int]] = {
    measure.key: {category: place for place, category in enumerate(measure.category_keys())}
    for measure in MEASURES
}

#: Every file beneath the center placed in the outermost sector holding it (the anchor of greatest
#: depth among its folders), ordered within that sector by kind, then examination, then name, and
#: cut into bins in proportion to the sector's share of the files. One query over the projection.
_BINS_SQL = """
WITH scoped AS (
    SELECT entry_id, name, kind, exam, stage, prov, bytes, ancestors[{first}:] AS chain
    FROM {files} WHERE list_contains(ancestors, $focus)
),
placed AS (
    SELECT s.entry_id, arg_max(m.sector, m.depth) AS sector
    FROM (SELECT entry_id, unnest(chain) AS folder_id FROM scoped) s
    JOIN wheel_anchors m USING (folder_id)
    GROUP BY s.entry_id
),
ranked AS (
    SELECT p.sector, s.*,
           row_number() OVER (
               PARTITION BY p.sector
               ORDER BY list_position({kinds}, s.kind), list_position({exams}, s.exam),
                        s.name, s.entry_id
           ) AS rank,
           count(*) OVER (PARTITION BY p.sector) AS members
    FROM placed p JOIN scoped s USING (entry_id)
),
binned AS (
    SELECT *, CAST(floor((rank - 1) * greatest(1, least(members,
               round(members * $bins / $total))) / members) AS BIGINT) AS bin
    FROM ranked
)
SELECT sector, bin, min(rank) - 1 AS start, count(*) AS n, sum(bytes) AS bytes,
       histogram(kind) AS kind, histogram(exam) AS exam, histogram(stage) AS stage,
       histogram(prov) AS prov
FROM binned GROUP BY sector, bin ORDER BY sector, bin
"""


def _dominant(held: Mapping[str, int], positions: Mapping[str, int], count: int) -> tuple[int, int]:
    """The category most of a bin's files share (the earlier in its order on a tie), and the
    percentage of the bin's files it holds."""
    best = min(
        held, key=lambda category: (-held[category], positions.get(category, len(positions)))
    )
    return positions.get(best, 0), round(100 * held[best] / count) if count else 0


def _provenance(projection: ExplorerProjection) -> tuple[int, int, int, str]:
    """The run's lines of provenance, whole, and the sentence saying so."""
    provenance = projection.meta.get("provenance", {})
    derivations = int(provenance.get("published_derivations", 0))
    mentions = int(provenance.get("published_mentions", 0))
    proposals = int(provenance.get("proposed_pairs", 0))
    if derivations or mentions:
        said = (
            f"This run published {derivations:,} derivation{'' if derivations == 1 else 's'} "
            f"and {mentions:,} mention{'' if mentions == 1 else 's'}"
        )
    else:
        said = "This run published no provenance: no derivation and no mention"
    said += (
        f"; {proposals:,} pair{'' if proposals == 1 else 's'} proposed."
        if proposals
        else "; none proposed."
    )
    return derivations, mentions, proposals, said


def _bins(rows: Sequence[Sequence[Any]]) -> WheelBins:
    """The perimeter's columns from its rows: (sector, bin, start, n, bytes, *histograms)."""
    bin_sector: list[int] = []
    bin_start: list[int] = []
    bin_n: list[int] = []
    bin_bytes: list[int] = []
    dominant: dict[str, list[int]] = {measure.key: [] for measure in MEASURES}
    purity: dict[str, list[int]] = {measure.key: [] for measure in MEASURES}
    for sector_index, _, start, count, size, *histograms in rows:
        bin_sector.append(int(sector_index))
        bin_start.append(int(start))
        bin_n.append(int(count))
        bin_bytes.append(int(size or 0))
        for measure_key, histogram in zip(_BINNED, histograms, strict=True):
            place, share = _dominant(dict(histogram), _POSITIONS[measure_key], int(count))
            dominant[measure_key].append(place)
            purity[measure_key].append(share)
    return WheelBins(
        sector=bin_sector,
        start=bin_start,
        n=bin_n,
        bytes=bin_bytes,
        dominant={measure.key: dominant[measure.key] for measure in MEASURES},
        purity={measure.key: purity[measure.key] for measure in MEASURES},
    )


def _arranged_wheel(
    context: V1Context,
    connection: duckdb.DuckDBPyConnection,
    projection: ExplorerProjection,
    *,
    run_id: str,
    is_pinned: bool,
    built_now: bool,
    started: float,
    focus: Mapping[str, Any],
    breadcrumb: list[Crumb],
    roots: list[Crumb],
    axes: Sequence[Axis],
    where: Sequence[Where],
    size_by: SizeBy,
    max_sectors: int,
    max_bins: int,
    min_angle: float,
    topics_notice: str | None = None,
) -> WheelResponse:
    """The Wheel arranged by axes (:mod:`~ingence.api.v1.explorer.arranged`)."""
    decisions = _decisions(context.project)
    statuses = {
        str(row["entry_id"]): _unit_status(row, decisions)[0]
        for row in _rows(
            connection.execute(
                "SELECT entry_id, exp_claim_id, exp_claim_ids, exp_conflicting "  # noqa: S608
                f"FROM {projection.folders} WHERE exp_claim_id IS NOT NULL"
            )
        )
    }
    built = arranged(
        connection,
        projection,
        focus=focus,
        axes=axes,
        where=where,
        size_by=size_by,
        max_sectors=max_sectors,
        min_angle=min_angle,
        max_bins=max_bins,
        status_of=lambda unit: statuses.get(unit, "none"),
        binned=_BINNED,
        orders={"kind": _sql_list(KIND.category_keys()), "exam": _sql_list(EXAM.category_keys())},
    )
    derivations, mentions, proposals, said = _provenance(projection)
    names = dict(built.names)
    where_folders = sorted(
        {item.value for item in where if item.axis.key in ("folder", "unit") and item.value}
        - set(names)
    )
    if where_folders:
        for identifier, name in connection.execute(
            f"SELECT entry_id, name FROM {projection.folders} "  # noqa: S608
            "WHERE list_contains(?, entry_id)",
            [where_folders],
        ).fetchall():
            names[str(identifier)] = str(name)
    labels = _arrangement_words(axes)
    message = (
        f"Arranged by {labels}: {built.shown:,} of {built.values:,} values drawn; "
        f"{built.gathered:,} gathered. Every file is counted at the edge, in "
        f"{len(built.bin_rows):,} bins."
    )
    if where:
        message += f" Filtered to {built.totals['files']:,} of {int(focus['files']):,} files."
    if topics_notice and any(
        item.key == "h.group" for item in [*axes, *(clause.axis for clause in where)]
    ):
        # The arrangement reads a person's topics, and some could not be read: say so.
        message += f" {topics_notice}"

    def experiment(held: Mapping[str, Any]) -> SectorExperiment | None:
        if not held["units"]:
            return None
        return SectorExperiment(**held)

    return WheelResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        pinned=_pinned(context, run_id, is_pinned=is_pinned),
        focus=_crumb(focus),
        breadcrumb=breadcrumb,
        roots=roots,
        measures=_measure_definitions(),
        sectors=[
            WheelSector(
                **{key: value for key, value in sector.items() if key != "experiment"},
                experiment=experiment(sector["experiment"]),
            )
            for sector in built.sectors
        ],
        bins=_bins(built.bin_rows),
        totals=WheelTotals(
            files=int(built.totals["files"]),
            folders=int(built.totals["folders"]),
            bytes=int(built.totals["bytes"]),
            counts=built.totals["counts"],
            experiment=SectorExperiment(**built.totals["experiment"]),
            published_derivations=derivations,
            published_mentions=mentions,
            proposed_pairs=proposals,
            provenance=said,
        ),
        caps=WheelCaps(
            rings=len(axes),
            max_sectors=max_sectors,
            max_bins=max_bins,
            min_angle=min_angle,
            size_by=size_by,
            sectors_shown=len(built.sectors),
            folders_in_rings=built.values,
            folders_gathered=built.gathered,
            folders_deeper=0,
            bins_shown=len(built.bin_rows),
            message=message,
        ),
        projection=ProjectionState(
            built_now=built_now,
            build_seconds=float(projection.meta.get("build_seconds", 0.0)),
            answered_ms=round(1000 * (time.perf_counter() - started), 1),
        ),
        arrangement=WheelArrangement(
            axes=[_axis_info(axis) for axis in axes],
            where=[
                WhereInfo(
                    axis=item.axis.key,
                    level=item.level,
                    value=item.value,
                    label=value_label(item.axis, item.value, names=names),
                    negated=item.negated,
                )
                for item in where
            ],
            files_beneath=int(focus["files"]),
        ),
    )


def _arrangement_words(axes: Sequence[Axis]) -> str:
    """The axes in words, a run of folder levels said once: "Technique, then Folders (2 levels)"."""
    words: list[str] = []
    run = 0
    for index, axis in enumerate(axes):
        if axis.key == "folder":
            run += 1
            if index + 1 < len(axes) and axes[index + 1].key == "folder":
                continue
            words.append("Folders" if run == 1 else f"Folders ({run} levels)")
            run = 0
        else:
            words.append(axis.label)
    return ", then ".join(words)


def _axis_info(axis: Axis) -> AxisInfo:
    return AxisInfo(
        key=axis.key,
        label=axis.label,
        family=axis.family,
        question=axis.question,
        none_label=axis.none_label,
    )


def wheel(context: V1Context, query: Mapping[str, Sequence[str]]) -> WheelResponse:
    """One folder's shape: sectors, perimeter bins, measures and units (see the module)."""
    started = time.perf_counter()
    rings = integer(query, "rings", default=DEFAULT_RINGS, low=RING_LIMITS[0], high=RING_LIMITS[1])
    max_sectors = integer(
        query,
        "max_sectors",
        default=DEFAULT_MAX_SECTORS,
        low=SECTOR_LIMITS[0],
        high=SECTOR_LIMITS[1],
    )
    max_bins = integer(
        query, "max_bins", default=DEFAULT_MAX_BINS, low=BIN_LIMITS[0], high=BIN_LIMITS[1]
    )
    min_angle = _decimal(
        query, "min_angle", default=DEFAULT_MIN_ANGLE, low=ANGLE_LIMITS[0], high=ANGLE_LIMITS[1]
    )
    size_text = (one(query, "size") or "files").strip()
    if size_text not in ("files", "bytes"):
        message = f"size is files or bytes; {size_text!r} is neither"
        raise ApiError(message, parameter="size")
    size_by: SizeBy = "files" if size_text == "files" else "bytes"
    axes = parse_axes(query)
    where = parse_where(query)
    if where and not axes:
        axes = [axis_named("folder")] * rings
    run_id, is_pinned, projection, built_now = _run_and_projection(context, query)

    connection = explorer_connection()
    # A person's groups and exclusions, for the arrangement's ``h.`` axes.
    topics_notice = register_people(connection, context.project, run_id)
    try:
        folders, files = projection.folders, projection.files
        roots = [
            _crumb(row)
            for row in _rows(
                connection.execute(
                    f"SELECT entry_id, name, relative_path FROM {folders} "  # noqa: S608
                    "WHERE len(ancestors) = 0 ORDER BY root_id, relative_path, entry_id"
                )
            )
        ]
        focus_id = (one(query, "focus") or "").strip() or (roots[0].id if roots else "")
        if not focus_id:
            message = "this run holds no folder to start from"
            raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND)
        found = _rows(
            connection.execute(
                f"SELECT * FROM {folders} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                [focus_id],
            )
        )
        if not found:
            is_file = connection.execute(
                f"SELECT name FROM {files} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                [focus_id],
            ).fetchone()
            if is_file is not None:
                message = (
                    f"{is_file[0]} is a file; the Wheel starts from a folder "
                    "(open a file in Lineage or Timeline)"
                )
                raise ApiError(message, code="not_a_folder", parameter="focus")
            message = f"run {run_id} holds no live folder {focus_id}"
            raise ApiError(
                message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="focus"
            )
        focus = found[0]
        focus_depth = int(focus["depth"])
        ancestors = [str(value) for value in focus["ancestors"] or ()]
        above = {
            str(row["entry_id"]): row
            for row in _rows(
                connection.execute(
                    f"SELECT entry_id, name, relative_path FROM {folders} "  # noqa: S608
                    "WHERE list_contains(?, entry_id)",
                    [ancestors],
                )
            )
        }
        breadcrumb = [_crumb(above[identifier]) for identifier in ancestors if identifier in above]
        if axes:
            return _arranged_wheel(
                context,
                connection,
                projection,
                run_id=run_id,
                is_pinned=is_pinned,
                built_now=built_now,
                started=started,
                focus=focus,
                breadcrumb=breadcrumb,
                roots=roots,
                axes=axes,
                where=where,
                size_by=size_by,
                max_sectors=max_sectors,
                max_bins=max_bins,
                min_angle=min_angle,
                topics_notice=topics_notice,
            )

        # Every folder within the rings beneath the center, every column. At eight rings from a
        # root, that is most of the tree, so the query is bounded by the rings asked for.
        within = _rows(
            connection.execute(
                f"SELECT * FROM {folders} WHERE list_contains(ancestors, ?) "  # noqa: S608
                "AND depth <= ? ORDER BY depth, relative_path, entry_id",
                [focus_id, focus_depth + rings],
            )
        )
        deeper_row = connection.execute(
            f"SELECT count(*) FROM {folders} WHERE list_contains(ancestors, ?) "  # noqa: S608
            "AND depth > ?",
            [focus_id, focus_depth + rings],
        ).fetchone()
        folders_deeper = int(deeper_row[0]) if deeper_row else 0

        def weight(row: Mapping[str, Any]) -> int:
            return int(row["files"] if size_by == "files" else row["bytes"])

        children: dict[str, list[dict[str, Any]]] = {}
        for row in within:
            children.setdefault(str(row["parent_id"]), []).append(row)
        whole = weight(focus)

        sectors: list[dict[str, Any]] = []
        #: Each folder a sector stands for, with that sector's index and the folder's depth.
        anchors: dict[str, tuple[int, int]] = {}

        def add(row: Mapping[str, Any], *, parent: str | None, ring: int) -> int:
            sectors.append(
                {
                    "id": str(row["entry_id"]),
                    "parent": parent,
                    "ring": ring,
                    "kind": "folder",
                    "name": str(row["name"] or row["relative_path"] or row["entry_id"]),
                    "path": str(row["relative_path"] or ""),
                    "files": int(row["files"]),
                    "folders": int(row["folders"]),
                    "bytes": int(row["bytes"]),
                    "direct_files": int(row["direct_files"]),
                    "direct_folders": int(row["direct_folders"]),
                    "children_shown": 0,
                    "gathered": 0,
                    "leaf": "all",
                    "counts": _counts(row),
                    "experiment": _empty_experiment(),
                }
            )
            anchors[str(row["entry_id"])] = (len(sectors) - 1, int(row["depth"]))
            return len(sectors) - 1

        add(focus, parent=None, ring=0)
        gathered_total = 0
        frontier = [0]
        while frontier:
            following: list[int] = []
            for index in frontier:
                sector = sectors[index]
                if sector["ring"] >= rings:
                    continue
                kids = sorted(
                    children.get(sector["id"], []),
                    key=lambda row: (-weight(row), str(row["name"]), str(row["entry_id"])),
                )
                if not kids:
                    continue
                # The cap is a hard cap: the room left is counted once (every sector added is in
                # ``sectors`` already), and where any child is gathered, one slot is kept for its
                # "more" sector before a child is shown. With no room for even that, the children
                # are counted in this sector, as deeper folders are.
                room = max_sectors - len(sectors)
                if room <= 0:
                    continue
                visible = [
                    kid
                    for kid in kids
                    if weight(kid) > 0
                    and (weight(kid) / whole * 360.0 if whole else 0.0) >= min_angle
                ]
                if len(visible) == len(kids) and len(kids) <= room:
                    shown = kids
                else:
                    shown = visible[: room - 1]
                held = {id(kid) for kid in shown}
                pooled = [kid for kid in kids if id(kid) not in held]
                for kid in shown:
                    following.append(add(kid, parent=sector["id"], ring=sector["ring"] + 1))
                if pooled:
                    counts = {
                        measure.key: [
                            sum(int(row.get(f"{measure.key}_{key}") or 0) for row in pooled)
                            for key in measure.category_keys()
                        ]
                        for measure in MEASURES
                    }
                    sectors.append(
                        {
                            "id": f"more:{sector['id']}",
                            "parent": sector["id"],
                            "ring": sector["ring"] + 1,
                            "kind": "more",
                            "name": f"{len(pooled):,} more folder{'' if len(pooled) == 1 else 's'}",
                            "path": sector["path"],
                            "files": sum(int(row["files"]) for row in pooled),
                            "folders": sum(int(row["folders"]) + 1 for row in pooled),
                            "bytes": sum(int(row["bytes"]) for row in pooled),
                            "direct_files": 0,
                            "direct_folders": 0,
                            "children_shown": 0,
                            "gathered": len(pooled),
                            "leaf": "all",
                            "counts": counts,
                            "experiment": _empty_experiment(),
                        }
                    )
                    more_index = len(sectors) - 1
                    for row in pooled:
                        anchors[str(row["entry_id"])] = (more_index, int(row["depth"]))
                    gathered_total += len(pooled)
                sector["children_shown"] = len(shown)
                if shown or pooled:
                    sector["leaf"] = "direct" if sector["direct_files"] else "none"
            frontier = following
        # A gathered folder's own folders within the rings are gathered with it.
        folders_gathered = len(within) - sum(
            1 for sector in sectors[1:] if sector["kind"] == "folder"
        )

        # Experiment units and proposals beneath the center, placed in the sector holding them.
        decisions = _decisions(context.project)
        parents = {index: sector["parent"] for index, sector in enumerate(sectors)}
        by_id = {sector["id"]: index for index, sector in enumerate(sectors)}
        units = _rows(
            connection.execute(
                "SELECT entry_id, depth, ancestors, exp_claim_id, exp_claim_ids, exp_kind, "  # noqa: S608
                f"exp_rules, exp_conflicting, proposed_groups FROM {folders} "
                "WHERE (entry_id = ? OR list_contains(ancestors, ?)) "
                "AND (exp_claim_id IS NOT NULL OR proposed_groups > 0)",
                [focus_id, focus_id],
            )
        )
        totals_experiment = _empty_experiment()
        for unit in units:
            status, decided = _unit_status(unit, decisions)
            chain = [*(str(value) for value in unit["ancestors"] or ()), str(unit["entry_id"])]
            placed = max(
                (anchors[identifier] for identifier in chain if identifier in anchors),
                key=lambda held: held[1],
                default=None,
            )
            if placed is None:
                continue
            own = sectors[placed[0]]
            if own["id"] == str(unit["entry_id"]) and status != "none":
                own["experiment"].update(
                    status=status,
                    unit_kind=unit["exp_kind"],
                    rules=sorted(
                        str(rule) for rule in str(unit["exp_rules"] or "").split(",") if rule
                    ),
                    claim_id=str(unit["exp_claim_id"]),
                    decided_by=decided[1] if decided else None,
                    decided_at=decided[2] if decided else None,
                )
            step: int | None = placed[0]
            targets = [totals_experiment]
            while step is not None:
                targets.append(sectors[step]["experiment"])
                parent = parents[step]
                step = by_id[parent] if parent is not None else None
            for target in targets:
                if status != "none":
                    target["units"] += 1
                if status in ("conflicting", "confirmed", "rejected"):
                    target[status] += 1
                target["proposed_groups"] += int(unit["proposed_groups"] or 0)

        # The perimeter: every file beneath the center, in the outermost sector holding it.
        rows: list[Any] = []
        total_files = int(focus["files"])
        if max_bins and total_files:
            anchor_table = pa.table(
                {
                    "folder_id": list(anchors),
                    "sector": [held[0] for held in anchors.values()],
                    "depth": [held[1] for held in anchors.values()],
                }
            )
            connection.register("wheel_anchors", anchor_table)
            rows = connection.execute(
                _BINS_SQL.format(
                    files=files,
                    first=focus_depth + 1,
                    kinds=_sql_list(KIND.category_keys()),
                    exams=_sql_list(EXAM.category_keys()),
                ),
                {"focus": focus_id, "bins": max_bins, "total": total_files},
            ).fetchall()
        bins = _bins(rows)
    finally:
        connection.close()

    derivations, mentions, proposals, said = _provenance(projection)
    shown_folders = sum(1 for sector in sectors[1:] if sector["kind"] == "folder")
    message = (
        f"{shown_folders:,} of {len(within):,} folders within {rings} ring"
        f"{'' if rings == 1 else 's'} drawn; {folders_gathered:,} gathered or counted in their "
        f"parent; {folders_deeper:,} deeper counted in the outer ring. Every file is counted at "
        f"the edge, in {len(bins.n):,} bins: {BINS_BOUND}."
    )
    return WheelResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        pinned=_pinned(context, run_id, is_pinned=is_pinned),
        focus=_crumb(focus),
        breadcrumb=breadcrumb,
        roots=roots,
        measures=_measure_definitions(),
        sectors=[
            WheelSector(
                **{key: value for key, value in sector.items() if key != "experiment"},
                experiment=(
                    SectorExperiment(**sector["experiment"])
                    if sector["experiment"]["units"]
                    or sector["experiment"]["proposed_groups"]
                    or sector["experiment"]["status"] != "none"
                    else None
                ),
            )
            for sector in sectors
        ],
        bins=bins,
        totals=WheelTotals(
            files=int(focus["files"]),
            folders=int(focus["folders"]),
            bytes=int(focus["bytes"]),
            counts=_counts(focus),
            experiment=SectorExperiment(**totals_experiment),
            published_derivations=derivations,
            published_mentions=mentions,
            proposed_pairs=proposals,
            provenance=said,
        ),
        caps=WheelCaps(
            rings=rings,
            max_sectors=max_sectors,
            max_bins=max_bins,
            min_angle=min_angle,
            size_by=size_by,
            sectors_shown=len(sectors),
            folders_in_rings=len(within),
            folders_gathered=folders_gathered,
            folders_deeper=folders_deeper,
            bins_shown=len(bins.n),
            message=message,
        ),
        projection=ProjectionState(
            built_now=built_now,
            build_seconds=float(projection.meta.get("build_seconds", 0.0)),
            answered_ms=round(1000 * (time.perf_counter() - started), 1),
        ),
    )


@route("explorer/wheel", model=WheelResponse)
def wheel_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> WheelResponse:
    return wheel(context, query)
