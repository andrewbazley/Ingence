"""The Wheel arranged by axes: rings of values rather than of folders.

What :mod:`~ingence.api.v1.explorer.arrange` defines, drawn: the center is still a folder (the
scope), and each ring outward is one axis of the arrangement, each sector a value of that axis
among the files beneath the center that pass the request's filters. Detail falls with distance
as in the folder Wheel: a value narrower than ``min_angle`` is gathered with its narrow siblings
into one ``more`` sector, with exact sums, and the response has room for ``max_sectors``.

One aggregate query, ``GROUP BY ROLLUP`` over the axes, counts every level at once (files,
bytes, the folders holding them, the experiment units among them, and every measure's
categories); a second places each file in the outermost sector drawn for it and cuts the
perimeter into bins in the Wheel's order. Neither materialises a file in Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from ingence.api.v1.explorer.arrange import (
    Axis,
    Where,
    scoped,
    sector_id,
    value_label,
)
from ingence.api.v1.explorer.categories import MEASURES

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import duckdb

    from ingence.api.v1.explorer.projection import ExplorerProjection

__all__ = ["Arranged", "arranged"]

#: The separator a placement key joins values with (a control character no value holds).
_JOIN = "chr(31)"


@dataclass
class Arranged:
    sectors: list[dict[str, Any]]
    #: The raw perimeter rows: (sector, bin, start, n, bytes, *histograms per measure).
    bin_rows: list[tuple[Any, ...]]
    totals: dict[str, Any]
    #: Values within the rings (every level), drawn alone, and gathered into ``more``.
    values: int = 0
    shown: int = 0
    gathered: int = 0
    names: dict[str, str] = field(default_factory=dict)


def _measure_counts() -> str:
    return ", ".join(
        f"count(*) FILTER (WHERE {measure.key} = '{key}') AS {measure.key}_{key}"
        for measure in MEASURES
        for key in measure.category_keys()
    )


def _counts(row: Mapping[str, Any]) -> dict[str, list[int]]:
    return {
        measure.key: [int(row.get(f"{measure.key}_{key}") or 0) for key in measure.category_keys()]
        for measure in MEASURES
    }


def _summed(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[int]]:
    return {
        measure.key: [
            sum(int(row.get(f"{measure.key}_{key}") or 0) for row in rows)
            for key in measure.category_keys()
        ]
        for measure in MEASURES
    }


def _rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _skipped(prefix: tuple[str, ...], axes: Sequence[Axis]) -> bool:
    """Whether a prefix passes through a "directly in the folder above" that is not drawn.

    A file lying above a folder level has no folder there; its sector is drawn only where an
    axis that is not a folder follows, so that axis's values have somewhere to hang. Otherwise
    the file stays in the folder above, as the folder Wheel keeps a folder's own files at its
    sector ("direct"), and every deeper level of the same chain is not drawn either.
    """
    for place, (value, axis) in enumerate(zip(prefix, axes, strict=False)):
        if axis.key != "folder" or value != "":
            continue
        later = axes[place + 1 :]
        if not any(other.key != "folder" for other in later):
            return True
        if any(prefix[earlier] == "" and axes[earlier].key == "folder" for earlier in range(place)):
            return True
    return False


def _experiment(units: Sequence[str], status_of: Callable[[str], str]) -> dict[str, Any]:
    held = {"units": 0, "conflicting": 0, "confirmed": 0, "rejected": 0}
    for unit in units:
        status = status_of(unit)
        if status == "none":
            continue
        held["units"] += 1
        if status in held:
            held[status] += 1
    return {
        "status": "none",
        "unit_kind": None,
        "rules": [],
        "claim_id": None,
        "decided_by": None,
        "decided_at": None,
        "proposed_groups": 0,
        **held,
    }


def arranged(
    connection: duckdb.DuckDBPyConnection,
    projection: ExplorerProjection,
    *,
    focus: Mapping[str, Any],
    axes: Sequence[Axis],
    where: Sequence[Where],
    size_by: str,
    max_sectors: int,
    min_angle: float,
    max_bins: int,
    status_of: Callable[[str], str],
    binned: Sequence[str],
    orders: Mapping[str, str],
) -> Arranged:
    """Sectors and perimeter rows for one arranged Wheel (see the module)."""
    focus_id = str(focus["entry_id"])
    held = scoped(
        files=projection.files,
        themes=projection.themes,
        focus_id=focus_id,
        focus_depth=int(focus["depth"]),
        axes=axes,
        where=where,
    )
    columns = held.columns
    count = len(columns)
    # The files beneath, with their values, read once: the counts and the perimeter both use it.
    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE arranged_scope AS {held.sql}",
        held.parameters,
    )
    rows = _rows(
        connection.execute(
            f"SELECT {', '.join(columns)}, GROUPING({', '.join(columns)}) AS rolled, "  # noqa: S608
            "count(*) AS files, coalesce(sum(bytes), 0) AS bytes, "
            "count(DISTINCT folder_id) AS folders, "
            "list(DISTINCT unit_id) FILTER (WHERE unit_id IS NOT NULL) AS units, "
            f"{_measure_counts()} FROM arranged_scope GROUP BY ROLLUP ({', '.join(columns)})"
        )
    )
    nodes: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        level = count - int(row["rolled"]).bit_count()
        prefix = tuple(str(row[f"a{index}"]) for index in range(level))
        if level and _skipped(prefix, axes):
            continue
        nodes[prefix] = row
    whole = nodes.get(())
    if whole is None:  # nothing beneath passes the filters
        whole = {"files": 0, "bytes": 0, "folders": 0, "units": []}

    # Names for the folders a folder or unit axis names, in one query.
    folder_ids = sorted(
        {
            prefix[index]
            for prefix in nodes
            for index, axis in enumerate(axes[: len(prefix)])
            if axis.key in ("folder", "unit") and prefix[index]
        }
    )
    names: dict[str, str] = {}
    paths: dict[str, str] = {}
    if folder_ids:
        for identifier, name, path in connection.execute(
            f"SELECT entry_id, name, relative_path FROM {projection.folders} "  # noqa: S608
            "WHERE list_contains(?, entry_id)",
            [folder_ids],
        ).fetchall():
            names[str(identifier)] = str(name or path or identifier)
            paths[str(identifier)] = str(path or "")

    children: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    for prefix in nodes:
        if prefix:
            children.setdefault(prefix[:-1], []).append(prefix)

    def weight(prefix: tuple[str, ...]) -> int:
        return int(nodes[prefix]["files" if size_by == "files" else "bytes"] or 0)

    total_weight = weight(()) if () in nodes else 0
    focus_name = str(focus["name"] or focus["relative_path"] or focus_id)

    def label(prefix: tuple[str, ...], parent_name: str) -> str:
        axis = axes[len(prefix) - 1]
        value = prefix[-1]
        if axis.key == "folder" and value == "":
            return f"files directly in {parent_name}"
        return value_label(axis, value, names=names)

    sectors: list[dict[str, Any]] = [
        {
            "id": focus_id,
            "parent": None,
            "ring": 0,
            "kind": "folder",
            "name": focus_name,
            "path": str(focus["relative_path"] or ""),
            "files": int(whole["files"]),
            "folders": int(whole["folders"]),
            "bytes": int(whole["bytes"] or 0),
            "direct_files": 0,
            "direct_folders": 0,
            "children_shown": 0,
            "gathered": 0,
            "leaf": "all",
            "counts": _counts(whole),
            "experiment": _experiment(whole.get("units") or [], status_of),
            "axis": None,
            "value": None,
            "entry_id": focus_id,
        }
    ]
    anchors: dict[tuple[str, ...], int] = {(): 0}
    prefix_of: list[tuple[str, ...]] = [()]
    gathered = 0
    frontier = [0]
    while frontier:
        following: list[int] = []
        for index in frontier:
            sector = sectors[index]
            prefix = prefix_of[index]
            if sector["kind"] == "more" or len(prefix) >= count:
                continue
            kids = sorted(
                children.get(prefix, []),
                key=lambda kid: (kid[-1] == "", -weight(kid), label(kid, sector["name"])),
            )
            if not kids:
                continue
            # The cap is a hard cap, as in the folder Wheel. The room left is counted once, and
            # where any value is gathered, one slot is kept for its "more".
            room = max_sectors - len(sectors)
            if room <= 0:
                continue
            visible = [
                kid
                for kid in kids
                if weight(kid) > 0
                and (weight(kid) / total_weight * 360.0 if total_weight else 0.0) >= min_angle
            ]
            shown = kids if len(visible) == len(kids) and len(kids) <= room else visible[: room - 1]
            drawn_here = set(shown)
            pooled = [kid for kid in kids if kid not in drawn_here]
            axis = axes[len(prefix)]
            for kid in shown:
                row = nodes[kid]
                value = kid[-1]
                is_folder = axis.key in ("folder", "unit") and value != ""
                sectors.append(
                    {
                        "id": sector_id(kid),
                        "parent": sector["id"],
                        "ring": sector["ring"] + 1,
                        "kind": "value",
                        "name": label(kid, sector["name"]),
                        "path": paths.get(value, "") if is_folder else "",
                        "files": int(row["files"]),
                        "folders": int(row["folders"]),
                        "bytes": int(row["bytes"] or 0),
                        "direct_files": 0,
                        "direct_folders": 0,
                        "children_shown": 0,
                        "gathered": 0,
                        "leaf": "all",
                        "counts": _counts(row),
                        "experiment": _experiment(row.get("units") or [], status_of),
                        "axis": axis.key,
                        "value": value,
                        "entry_id": value if is_folder else None,
                    }
                )
                prefix_of.append(kid)
                anchors[kid] = len(sectors) - 1
                following.append(len(sectors) - 1)
            if pooled:
                rows_pooled = [nodes[kid] for kid in pooled]
                units = sorted({unit for row in rows_pooled for unit in (row.get("units") or [])})
                noun = "folder" if axis.key == "folder" else "value"
                sectors.append(
                    {
                        "id": "more:" + sector_id(prefix),
                        "parent": sector["id"],
                        "ring": sector["ring"] + 1,
                        "kind": "more",
                        "name": f"{len(pooled):,} more {noun}{'' if len(pooled) == 1 else 's'}",
                        "path": "",
                        "files": sum(int(row["files"]) for row in rows_pooled),
                        # Folders holding the gathered values' files; a folder holding two
                        # gathered values is counted once for each.
                        "folders": sum(int(row["folders"]) for row in rows_pooled),
                        "bytes": sum(int(row["bytes"] or 0) for row in rows_pooled),
                        "direct_files": 0,
                        "direct_folders": 0,
                        "children_shown": 0,
                        "gathered": len(pooled),
                        "leaf": "all",
                        "counts": _summed(rows_pooled),
                        "experiment": _experiment(units, status_of),
                        "axis": axis.key,
                        "value": None,
                        "entry_id": None,
                    }
                )
                prefix_of.append((*prefix, "\x00more"))
                for kid in pooled:
                    anchors[kid] = len(sectors) - 1
                gathered += len(pooled)
            sector["children_shown"] = len(shown)
            if shown or pooled:
                ends_here = int(nodes[prefix]["files"]) - sum(
                    int(nodes[kid]["files"]) for kid in (*shown, *pooled)
                )
                sector["direct_files"] = max(0, ends_here)
                sector["leaf"] = "direct" if ends_here > 0 else "none"
        frontier = following

    # The perimeter: each file in the outermost sector drawn for it.
    bin_rows: list[tuple[Any, ...]] = []
    total_files = int(whole["files"])
    if max_bins and total_files:
        levels = range(1, count + 1)
        keys = ", ".join(
            f"concat_ws({_JOIN}, {', '.join(columns[:level])}) AS k{level}" for level in levels
        )
        joins = " ".join(
            f"LEFT JOIN arranged_anchors p{level} ON p{level}.level = {level} "
            f"AND p{level}.key = k.k{level}"
            for level in levels
        )
        placed_sector = (
            "coalesce(" + ", ".join(f"p{level}.sector" for level in reversed(levels)) + ", 0)"
        )
        anchor_rows = [
            (len(prefix), "\x1f".join(prefix), sector)
            for prefix, sector in anchors.items()
            if prefix
        ]
        connection.register(
            "arranged_anchors",
            pa.table(
                {
                    "level": [row[0] for row in anchor_rows],
                    "key": [row[1] for row in anchor_rows],
                    "sector": [row[2] for row in anchor_rows],
                },
                schema=pa.schema(
                    [("level", pa.int64()), ("key", pa.string()), ("sector", pa.int64())]
                ),
            ),
        )
        histograms = ", ".join(f"histogram({key}) AS {key}" for key in binned)
        bin_rows = _binned(
            connection,
            "SELECT * FROM arranged_scope",
            [],
            keys=keys,
            joins=joins,
            placed_sector=placed_sector,
            histograms=histograms,
            orders=orders,
            max_bins=max_bins,
            total_files=total_files,
        )
    values = sum(1 for prefix in nodes if prefix)
    shown_count = sum(1 for sector in sectors[1:] if sector["kind"] == "value")
    totals = {
        "files": total_files,
        "folders": int(whole["folders"]),
        "bytes": int(whole["bytes"] or 0),
        "counts": _counts(whole),
        "experiment": _experiment(whole.get("units") or [], status_of),
    }
    return Arranged(
        sectors=sectors,
        bin_rows=bin_rows,
        totals=totals,
        values=values,
        shown=shown_count,
        gathered=gathered,
        names=names,
    )


def _binned(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: Sequence[Any],
    *,
    keys: str,
    joins: str,
    placed_sector: str,
    histograms: str,
    orders: Mapping[str, str],
    max_bins: int,
    total_files: int,
) -> list[tuple[Any, ...]]:
    """Place each file at its outermost drawn sector and cut the perimeter into bins, in the
    Wheel's order (kind, then examination, then name), in proportion to each sector's files."""
    return connection.execute(
        f"""
        WITH scoped AS ({sql}),
        k AS (SELECT *, {keys} FROM scoped),
        placed AS (SELECT {placed_sector} AS sector, k.* FROM k {joins}),
        ranked AS (
            SELECT *, row_number() OVER (
                       PARTITION BY sector
                       ORDER BY list_position({orders["kind"]}, kind),
                                list_position({orders["exam"]}, exam), name, entry_id
                   ) AS rank,
                   count(*) OVER (PARTITION BY sector) AS members
            FROM placed
        ),
        cut AS (
            SELECT *, CAST(floor((rank - 1) * greatest(1, least(members,
                       round(members * {int(max_bins)} / {int(total_files)}))) / members)
                       AS BIGINT) AS bin
            FROM ranked
        )
        SELECT sector, bin, min(rank) - 1 AS start, count(*) AS n, sum(bytes) AS bytes,
               {histograms}
        FROM cut GROUP BY sector, bin ORDER BY sector, bin
        """,  # noqa: S608
        list(parameters),
    ).fetchall()
