"""What a view can be arranged by, and what one value of an axis holds.

- ``GET /api/v1/explorer/axes?focus=``: every axis an arrangement may use beneath a folder, each
  with how many of the files beneath it have a value, how many values there are, and the largest
  few. The page offers the axes that would say something, and says why the others would not ("no
  technique named in any name beneath this folder").
- ``GET /api/v1/explorer/value?focus=&axis=&value=&where=``: one value's card. It gives the
  value's files and bytes, the folders holding them (largest first), each measure's categories,
  and the experiment units among them. For a theme, it also gives how the value is spelled
  (``confocal`` as ``lsm`` in some names) and whether the files' own names say it or only their
  folders' do.

Both read the run's explorer projection (version 2's ``themes.parquet``) and write nothing.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import duckdb

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.explorer.arrange import (
    PAIR,
    Axis,
    Where,
    axis_named,
    best_time_sql,
    names_an_axis,
    parse_where,
    register_people,
    scoped,
    value_label,
)
from ingence.api.v1.explorer.categories import MEASURES
from ingence.api.v1.explorer.projection import connect as explorer_connection
from ingence.api.v1.explorer.wheel import (
    AxisInfo,
    Crumb,
    SectorExperiment,
    WhereInfo,
    _crumb,
    _decisions,
    _rows,
    _run_and_projection,
    _unit_status,
)
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.api.v1.explorer.projection import ExplorerProjection

__all__ = ["AxesResponse", "ValueCard"]

#: The largest values an axis summary names.
EXAMPLES = 4
#: The folders a value's card names, largest first.
HOLDERS = 12


class AxisExample(ApiModel):
    value: str
    label: str
    files: Count


class AxisSummary(ApiModel):
    axis: AxisInfo
    #: Files beneath the folder with a value on this axis (not in its none value).
    files_with_value: Count
    #: Distinct values among them (a pair of techniques is one value).
    values: Count
    #: For a theme: the Dictionary senses and the spellings its values are made of.
    senses: Count | None
    spellings: Count | None
    examples: list[AxisExample]


class UnarrangedKind(ApiModel):
    """A theme kind beneath the folder that no axis can be named by, set aside and counted."""

    theme_kind: str
    #: Files beneath the folder carrying a word of it.
    files: Count
    words: str


@register("explorer_axes")
class AxesResponse(ApiModel):
    """``GET /api/v1/explorer/axes``: what a view beneath one folder can be arranged by."""

    schema_version: str
    run_id: Identifier
    focus: Crumb
    files: Count
    axes: list[AxisSummary]
    #: Theme kinds spelled so no axis key can name them (a space, a hyphen: a person's own
    #: definition, a laboratory's pack), counted here rather than breaking every other axis.
    unarranged: list[UnarrangedKind]


class ValueHolder(ApiModel):
    """A folder holding some of a value's files directly."""

    id: Identifier
    name: str
    path: str
    files: Count


class ValueSpelling(ApiModel):
    #: A spelling as written (lower-cased), or for a spelling's card a sense it binds to.
    text: str
    files: Count


@register("explorer_value")
class ValueCard(ApiModel):
    """``GET /api/v1/explorer/value``: one value of an axis beneath a folder."""

    schema_version: str
    run_id: Identifier
    focus: Crumb
    axis: AxisInfo
    value: str
    label: str
    where: list[WhereInfo]
    files: Count
    bytes: Count
    #: Folders holding its files directly, and the largest of them.
    folders: Count
    holders: list[ValueHolder]
    counts: dict[str, list[Count]]
    experiment: SectorExperiment
    #: For a theme: the spellings written for it (by sense), or the senses a spelling binds to.
    spellings: list[ValueSpelling]
    #: For a theme: files whose own name says it, and files that only inherit it from a folder.
    own_name: Count | None
    inherited: Count | None


def _focus(
    connection: duckdb.DuckDBPyConnection, projection: ExplorerProjection, focus_id: str
) -> dict[str, Any]:
    if focus_id:
        found = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE entry_id = ? LIMIT 1",  # noqa: S608
                [focus_id],
            )
        )
    else:
        found = _rows(
            connection.execute(
                f"SELECT * FROM {projection.folders} WHERE len(ancestors) = 0 "  # noqa: S608
                "ORDER BY root_id, relative_path, entry_id LIMIT 1"
            )
        )
    if not found:
        message = f"this run holds no live folder {focus_id}" if focus_id else "no folder"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="focus")
    return found[0]


def _fixed_summaries(
    connection: duckdb.DuckDBPyConnection,
    projection: ExplorerProjection,
    focus: Mapping[str, Any],
) -> list[AxisSummary]:
    depth = int(focus["depth"])
    child = f"f.ancestors[{depth + 2}]"
    best = best_time_sql()
    expressions = {
        "folder": child,
        "unit": "f.unit_id",
        "kind": "CASE WHEN f.kind <> 'unknown' THEN f.kind END",
        "stage": "CASE WHEN f.stage <> 'none' THEN f.stage END",
        "exam": "CASE WHEN f.exam <> 'unknown' THEN f.exam END",
        "prov": "CASE WHEN f.prov <> 'none' THEN f.prov END",
        "year": f"strftime({best}, '%Y')",
        "month": f"strftime({best}, '%Y-%m')",
    }
    selected = ", ".join(f"{sql} AS {key}" for key, sql in expressions.items())
    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE axes_scope AS SELECT {selected} "  # noqa: S608
        f"FROM {projection.files} f WHERE list_contains(f.ancestors, ?)",
        [str(focus["entry_id"])],
    )
    tallies = ", ".join(f"count({key}), count(DISTINCT {key})" for key in expressions)
    counted = connection.execute(f"SELECT {tallies} FROM axes_scope").fetchone()  # noqa: S608
    assert counted is not None
    tops: dict[str, list[tuple[str, int]]] = {}
    union = " UNION ALL ".join(
        f"SELECT '{key}' AS axis, CAST({key} AS VARCHAR) AS v, count(*) AS n FROM axes_scope "  # noqa: S608
        f"WHERE {key} IS NOT NULL GROUP BY 2"
        for key in expressions
    )
    for axis_key, value, held in connection.execute(
        f"SELECT axis, v, n FROM ({union}) "  # noqa: S608
        f"QUALIFY row_number() OVER (PARTITION BY axis ORDER BY n DESC, v) <= {EXAMPLES}"
    ).fetchall():
        tops.setdefault(str(axis_key), []).append((str(value), int(held)))
    folder_ids = [value for key in ("folder", "unit") for value, _ in tops.get(key, [])]
    names = {
        str(identifier): str(name)
        for identifier, name in connection.execute(
            f"SELECT entry_id, name FROM {projection.folders} WHERE list_contains(?, entry_id)",  # noqa: S608
            [folder_ids],
        ).fetchall()
    }
    summaries: list[AxisSummary] = []
    for place, key in enumerate(expressions):
        axis = axis_named(key)
        with_value, distinct = counted[2 * place], counted[2 * place + 1]
        summaries.append(
            AxisSummary(
                axis=_info(axis),
                files_with_value=int(with_value),
                values=int(distinct),
                senses=None,
                spellings=None,
                examples=[
                    AxisExample(
                        value=value, label=value_label(axis, value, names=names), files=held
                    )
                    for value, held in tops.get(axis.key, [])
                ],
            )
        )
    return summaries


def _people_summaries(
    connection: duckdb.DuckDBPyConnection,
    projection: ExplorerProjection,
    focus: Mapping[str, Any],
    project: Any,
) -> list[AxisSummary]:
    """A person's groups and exclusions as axes, where the person has made any."""
    from ingence.api.routes.overlay import standing_layer

    layer = standing_layer(project)
    summaries: list[AxisSummary] = []
    for key, present in (("h.group", bool(layer.groups)), ("h.excluded", bool(layer.excluded))):
        if not present:
            continue
        axis = axis_named(key)
        held = scoped(
            files=projection.files,
            themes=projection.themes,
            focus_id=str(focus["entry_id"]),
            focus_depth=int(focus["depth"]),
            axes=[axis],
            where=[],
        )
        rows = connection.execute(
            f"SELECT a0, count(*) AS n FROM ({held.sql}) WHERE a0 <> '' "  # noqa: S608
            "GROUP BY a0 ORDER BY n DESC, a0",
            held.parameters,
        ).fetchall()
        summaries.append(
            AxisSummary(
                axis=_info(axis),
                files_with_value=sum(int(n) for _, n in rows),
                values=len(rows),
                senses=None,
                spellings=None,
                examples=[
                    AxisExample(value=str(v), label=value_label(axis, str(v)), files=int(n))
                    for v, n in rows[:EXAMPLES]
                ],
            )
        )
    return summaries


def _theme_summaries(
    connection: duckdb.DuckDBPyConnection,
    projection: ExplorerProjection,
    focus_id: str,
) -> tuple[list[AxisSummary], list[UnarrangedKind]]:
    """One axis per theme kind beneath the folder, by sense and by spelling; and the kinds no
    axis key can name, set aside and counted, so one unusual kind never takes the whole Arrange
    panel with it."""
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE axes_themes AS SELECT t.* "  # noqa: S608
        f"FROM {projection.themes} t JOIN {projection.files} f ON f.entry_id = t.entry_id "
        "WHERE list_contains(f.ancestors, ?)",
        [focus_id],
    )
    kinds = connection.execute(
        f"SELECT theme_kind, count(*) AS files, count(DISTINCT array_to_string(terms, '{PAIR}')), "
        "count(DISTINCT array_to_string(spellings, '" + PAIR + "')) "
        "FROM axes_themes GROUP BY theme_kind ORDER BY files DESC, theme_kind"
    ).fetchall()
    senses: dict[str, int] = {}
    spellings: dict[str, int] = {}
    for kind, sense_count, spelling_count in connection.execute(
        "SELECT theme_kind, count(DISTINCT term), count(DISTINCT spelling) FROM ("
        "SELECT theme_kind, unnest(terms) AS term, NULL AS spelling FROM axes_themes "
        "UNION ALL SELECT theme_kind, NULL, unnest(spellings) FROM axes_themes) GROUP BY 1"
    ).fetchall():
        senses[str(kind)] = int(sense_count)
        spellings[str(kind)] = int(spelling_count)
    tops: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for column, prefix in (("terms", "t"), ("spellings", "s")):
        for kind, value, held in connection.execute(
            f"SELECT theme_kind, array_to_string({column}, '{PAIR}') AS v, count(*) AS n "  # noqa: S608
            "FROM axes_themes GROUP BY 1, 2 "
            f"QUALIFY row_number() OVER (PARTITION BY theme_kind ORDER BY n DESC, v) <= {EXAMPLES}"
        ).fetchall():
            tops.setdefault((prefix, str(kind)), []).append((str(value), int(held)))
    summaries: list[AxisSummary] = []
    unarranged: list[UnarrangedKind] = []
    for kind, held, sense_combos, spelling_combos in kinds:
        if not names_an_axis(str(kind)):
            unarranged.append(
                UnarrangedKind(
                    theme_kind=str(kind),
                    files=int(held),
                    words=(
                        f"{int(held)} {'file carries' if int(held) == 1 else 'files carry'} a "
                        f"word of the kind "
                        f"{str(kind)!r}, which cannot be arranged by: an axis is named by "
                        "letters, digits and underscores"
                    ),
                )
            )
            continue
        for prefix, distinct in (("t", sense_combos), ("s", spelling_combos)):
            axis = axis_named(f"{prefix}.{kind}")
            summaries.append(
                AxisSummary(
                    axis=_info(axis),
                    files_with_value=int(held),
                    values=int(distinct),
                    senses=senses.get(str(kind), 0),
                    spellings=spellings.get(str(kind), 0),
                    examples=[
                        AxisExample(value=value, label=value_label(axis, value), files=count)
                        for value, count in tops.get((prefix, str(kind)), [])
                    ],
                )
            )
    return summaries, sorted(unarranged, key=lambda item: item.theme_kind)


def _info(axis: Axis) -> AxisInfo:
    return AxisInfo(
        key=axis.key,
        label=axis.label,
        family=axis.family,
        question=axis.question,
        none_label=axis.none_label,
    )


def axes(context: V1Context, query: Mapping[str, Sequence[str]]) -> AxesResponse:
    run_id, _, projection, _ = _run_and_projection(context, query)
    connection = explorer_connection()
    # A person's groups and exclusions, for the arrangement's ``h.`` axes.
    register_people(connection, context.project, run_id)
    try:
        focus = _focus(connection, projection, (one(query, "focus") or "").strip())
        themes, unarranged = _theme_summaries(connection, projection, str(focus["entry_id"]))
        summaries = [
            *_fixed_summaries(connection, projection, focus),
            *_people_summaries(connection, projection, focus, context.project),
            *themes,
        ]
    finally:
        connection.close()
    return AxesResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        focus=_crumb(focus),
        files=int(focus["files"]),
        axes=summaries,
        unarranged=unarranged,
    )


def value(context: V1Context, query: Mapping[str, Sequence[str]]) -> ValueCard:
    axis_key = (one(query, "axis") or "").strip()
    if not axis_key:
        raise ApiError("axis names the axis the value is of", parameter="axis")
    axis = axis_named(axis_key.partition("@")[0], parameter="axis")
    level_text = axis_key.partition("@")[2]
    level = int(level_text) if axis.key == "folder" and level_text.isdigit() else 1
    wanted = one(query, "value")
    if wanted is None:
        raise ApiError("value names the value to describe ('' for none)", parameter="value")
    where = parse_where(query)
    run_id, _, projection, _ = _run_and_projection(context, query)
    connection = explorer_connection()
    # A person's groups and exclusions, for the arrangement's ``h.`` axes.
    register_people(connection, context.project, run_id)
    try:
        focus = _focus(connection, projection, (one(query, "focus") or "").strip())
        held = scoped(
            files=projection.files,
            themes=projection.themes,
            focus_id=str(focus["entry_id"]),
            focus_depth=int(focus["depth"]),
            axes=[],
            where=[
                *where,
                Where(axis=axis, value=wanted, level=level if axis.key == "folder" else 0),
            ],
        )
        connection.execute(
            f"CREATE OR REPLACE TEMP TABLE value_scope AS {held.sql}", held.parameters
        )
        measure_counts = ", ".join(
            f"count(*) FILTER (WHERE {measure.key} = '{key}')"
            for measure in MEASURES
            for key in measure.category_keys()
        )
        totals = connection.execute(
            "SELECT count(*), coalesce(sum(bytes), 0), count(DISTINCT folder_id), "  # noqa: S608
            f"list(DISTINCT unit_id) FILTER (WHERE unit_id IS NOT NULL), {measure_counts} "
            "FROM value_scope"
        ).fetchone()
        assert totals is not None
        files, size, folders, units, *flat = totals
        counts: dict[str, list[int]] = {}
        place = 0
        for measure in MEASURES:
            counts[measure.key] = [
                int(held_count) for held_count in flat[place : place + len(measure.categories)]
            ]
            place += len(measure.categories)
        holders = [
            ValueHolder(
                id=str(identifier), name=str(name), path=str(path or ""), files=int(held_count)
            )
            for identifier, name, path, held_count in connection.execute(
                f"SELECT v.folder_id, d.name, d.relative_path, count(*) AS n FROM value_scope v "  # noqa: S608
                f"JOIN {projection.folders} d ON d.entry_id = v.folder_id "
                f"GROUP BY 1, 2, 3 ORDER BY n DESC, d.relative_path LIMIT {HOLDERS}"
            ).fetchall()
        ]
        spellings: list[ValueSpelling] = []
        own_name: int | None = None
        inherited: int | None = None
        if axis.theme_kind is not None:
            column = "terms" if axis.spelled else "spellings"
            spellings = [
                ValueSpelling(
                    text=str(text).replace("_", " ") if axis.spelled else str(text),
                    files=int(held_count),
                )
                for text, held_count in connection.execute(
                    f"SELECT x, count(*) AS n FROM (SELECT unnest(t.{column}) AS x "  # noqa: S608
                    f"FROM value_scope v JOIN {projection.themes} t ON t.entry_id = v.entry_id "
                    "AND t.theme_kind = ?) GROUP BY x ORDER BY n DESC, x LIMIT 24",
                    [axis.theme_kind],
                ).fetchall()
            ]
            said = connection.execute(
                f"SELECT count(*) FILTER (WHERE t.own), count(*) FILTER (WHERE NOT t.own) "  # noqa: S608
                f"FROM value_scope v JOIN {projection.themes} t ON t.entry_id = v.entry_id "
                "AND t.theme_kind = ?",
                [axis.theme_kind],
            ).fetchone()
            if said is not None:
                own_name, inherited = int(said[0]), int(said[1])
        decisions = _decisions(context.project)
        statuses = {
            str(row["entry_id"]): _unit_status(row, decisions)[0]
            for row in _rows(
                connection.execute(
                    "SELECT entry_id, exp_claim_id, exp_claim_ids, exp_conflicting "  # noqa: S608
                    f"FROM {projection.folders} WHERE list_contains(?, entry_id)",
                    [list(units or [])],
                )
            )
        }
        names = {
            str(identifier): str(name)
            for identifier, name in connection.execute(
                f"SELECT entry_id, name FROM {projection.folders} WHERE list_contains(?, entry_id)",  # noqa: S608
                [[wanted, *(item.value for item in where)]],
            ).fetchall()
        }
    finally:
        connection.close()
    experiment = {"units": 0, "conflicting": 0, "confirmed": 0, "rejected": 0}
    for status in statuses.values():
        if status == "none":
            continue
        experiment["units"] += 1
        if status in experiment:
            experiment[status] += 1
    return ValueCard(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        focus=_crumb(focus),
        axis=_info(axis),
        value=wanted,
        label=value_label(axis, wanted, names=names),
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
        files=int(files),
        bytes=int(size),
        folders=int(folders),
        holders=holders,
        counts=counts,
        experiment=SectorExperiment(
            status="none",
            unit_kind=None,
            rules=[],
            claim_id=None,
            decided_by=None,
            decided_at=None,
            proposed_groups=0,
            **experiment,
        ),
        spellings=spellings,
        own_name=own_name,
        inherited=inherited,
    )


@route("explorer/axes", model=AxesResponse)
def axes_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> AxesResponse:
    return axes(context, query)


@route("explorer/value", model=ValueCard)
def value_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> ValueCard:
    return value(context, query)
