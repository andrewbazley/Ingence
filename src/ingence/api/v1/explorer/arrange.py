"""Arranging a view by what the run knows of each file, not only by where it sits.

A person should be able to orient by themes, by experimental technique and by the words written in
names, and to build how a view is grouped. The measures a view is colored by are not enough for
that. An arrangement is an ordered list of axes. Each ring of the Wheel (or each of the Timeline's
rows) is one axis, and each sector is a value of it among the files beneath the folder at the
center.

The axes, each answering one question about a file:

- ``folder``: which folder beneath holds it? The next level of the tree below the center
  (repeated, the next level again). The plain Wheel is the arrangement of folders alone.
- ``unit``: which experiment unit holds it? The nearest folder that a rule reads as an
  experiment's folder (``classifies.experiment_folder_*``), whatever its depth.
- ``t.<theme kind>``: what do names say about it? The Dictionary's senses of one theme kind
  (``technique``, ``condition``, ``reagent``, ``sample``...) that the file's own name or a
  folder's name above it carries, as the census read and resolved them. A file inherits its
  folders' words, because a folder named for a technique holds that technique's files.
- ``s.<theme kind>``: how is it written? The same words as spelled in the names (lower-cased), so
  ``confocal`` written three ways shows as three sectors. This arrangement finds inconsistent
  naming.
- ``kind``, ``stage``, ``exam``, ``prov``: the four measures' categories
  (:mod:`~ingence.api.v1.explorer.categories`), as rings rather than colors.
- ``year``, ``month``: the file's best available time (the earliest credible time a rule
  concluded, else a content clock, else modified), never a merged or invented one.

Themes overlap freely (``core.schema.Theme``). A file whose names carry two techniques sits in the
sector of the pair, named as the pair, never silently in one of them. So every ring still
partitions the files and every count adds up. A file whose names carry no word of a kind sits in
that kind's ``none`` sector, counted and drawn, never dropped. A theme is read from names only,
never from content, and only where the Dictionary resolved the word. An arrangement shows what the
names say, which is evidence about naming and not about the science.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from ingence.api.v1 import ApiError
from ingence.api.v1.explorer.categories import EXAM, KIND, PROV, STAGE, Measure

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "MAX_AXES",
    "Axis",
    "Where",
    "axis_named",
    "names_an_axis",
    "parse_axes",
    "parse_where",
    "sector_id",
    "value_label",
]

#: Rings an arrangement may hold: beyond four, a wheel of values is no longer readable.
MAX_AXES = 4
#: Where filters one request may carry (a drill-down adds one per ring passed).
MAX_WHERE = 12

Family = Literal["structure", "units", "names", "spelling", "readings", "time", "people"]

#: The separator a combination of values is written with, in the value itself and on screen.
PAIR = " + "

_THEME_KIND = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
#: Theme kinds whose word the run's own readings use too (the Stage reading, the Kind of format):
#: the arrangement says "Stage named" so the two are never read as one.
_READ_TOO = frozenset({"stage", "format", "kind"})

#: The best available time, as the Timeline reads it (earliest credible, else a content clock,
#: else modified), as a naive UTC timestamp.
_BEST = (
    "coalesce(TRY_CAST(TRY_CAST(f.earliest_at AS TIMESTAMPTZ) AS TIMESTAMP), "
    "CASE WHEN f.event_time_ns IS NOT NULL AND coalesce(f.event_time_source, '') <> 'filename' "
    "THEN make_timestamp(f.event_time_ns // 1000) END, "
    "CASE WHEN f.modified_ns IS NOT NULL THEN make_timestamp(f.modified_ns // 1000) END)"
)


@dataclass(frozen=True, slots=True)
class Axis:
    """One question a ring answers about each file."""

    key: str
    label: str
    family: Family
    question: str
    #: The words for a file with no value on this axis.
    none_label: str
    theme_kind: str | None = None
    #: For a measure's axis, the measure whose categories are its values.
    measure: Measure | None = None

    @property
    def spelled(self) -> bool:
        return self.key.startswith("s.")


def _measure_axis(measure: Measure) -> Axis:
    return Axis(
        key=measure.key,
        label=measure.label,
        family="readings",
        question=measure.question,
        none_label=measure.unknown,
        measure=measure,
    )


_FIXED: dict[str, Axis] = {
    "folder": Axis(
        key="folder",
        label="Folders",
        family="structure",
        question="Which folder beneath holds it? (repeated: the next level down)",
        none_label="directly in the folder above",
    ),
    "unit": Axis(
        key="unit",
        label="Experiment units",
        family="units",
        question="Which experiment unit holds it: the nearest folder a rule reads as an "
        "experiment's folder",
        none_label="in no experiment unit",
    ),
    **{measure.key: _measure_axis(measure) for measure in (KIND, STAGE, EXAM, PROV)},
    "year": Axis(
        key="year",
        label="Year",
        family="time",
        question="The year of its best available time (earliest credible, else a content "
        "clock, else modified)",
        none_label="undated",
    ),
    "month": Axis(
        key="month",
        label="Month",
        family="time",
        question="The month of its best available time",
        none_label="undated",
    ),
    # A person's own layer (``api/routes/overlay``): statements, never the engine's readings. The
    # key keeps its name (a topic is a group read under another word). A person reads topics.
    "h.group": Axis(
        key="h.group",
        label="Your collections",
        family="people",
        question="Which of your collections holds it, by its own membership or a folder's above it",
        none_label="in none of your collections",
    ),
    "h.excluded": Axis(
        key="h.excluded",
        label="Your exclusions",
        family="people",
        question="Whether a person set it aside, or a folder above it",
        none_label="not set aside",
    ),
}

#: The words for a file a person set aside, on the ``h.excluded`` axis.
SET_ASIDE = "set aside by you"


def _kind_words(theme_kind: str) -> str:
    return theme_kind.replace("_", " ")


def names_an_axis(theme_kind: str) -> bool:
    """Whether a theme kind is spelled as an axis's key can name it (letters, digits and
    underscores, a letter first): the key is spelled into SQL, so nothing else is."""
    return bool(_THEME_KIND.match(theme_kind))


def axis_named(key: str, *, parameter: str = "arrange") -> Axis:
    """The axis a key names, or an ``ApiError`` (on ``parameter``) saying why none is so named."""
    if key in _FIXED:
        return _FIXED[key]
    prefix, _, theme_kind = key.partition(".")
    if prefix in ("t", "s") and _THEME_KIND.match(theme_kind):
        words = _kind_words(theme_kind)
        # A theme kind sharing a word with a reading of the run is said as what names say.
        named = f"{words.capitalize()} named" if theme_kind in _READ_TOO else words.capitalize()
        if prefix == "t":
            return Axis(
                key=key,
                label=named,
                family="names",
                question=f"What its name, or a folder's name above it, says of {words}, as the "
                "Dictionary reads the words",
                none_label=f"no {words} named",
                theme_kind=theme_kind,
            )
        return Axis(
            key=key,
            label=f"{named}, as spelled",
            family="spelling",
            question=f"The words written for {words} in its name or its folders' names, "
            "as written (lower-cased)",
            none_label=f"no {words} named",
            theme_kind=theme_kind,
        )
    message = (
        f"{key!r} is no axis: folder, unit, kind, stage, exam, prov, year, month, "
        "h.group, h.excluded, t.<theme kind> or s.<theme kind>"
    )
    raise ApiError(message, parameter=parameter)


def parse_axes(query: Mapping[str, Sequence[str]], name: str = "arrange") -> list[Axis]:
    """The arrangement a request names: comma-separated axis keys, outermost ring last."""
    keys = [
        part.strip()
        for text in query.get(name, ())
        for part in str(text).split(",")
        if part.strip()
    ]
    if len(keys) > MAX_AXES:
        message = f"an arrangement holds at most {MAX_AXES} axes; {len(keys)} were named"
        raise ApiError(message, parameter=name)
    return [axis_named(key, parameter=name) for key in keys]


@dataclass(frozen=True, slots=True)
class Where:
    """Only the files whose value on ``axis`` is ``value``; ``negated`` for all but those."""

    axis: Axis
    value: str
    #: The folder-axis level this filter reads (1 for the first folder level), else 0.
    level: int = 0
    negated: bool = False


def parse_where(query: Mapping[str, Sequence[str]]) -> list[Where]:
    """``where=<axis>=<value>`` and ``not=<axis>=<value>``, each repeatable.

    A folder axis names its level with a suffix, ``folder@2=<id>``: the second folder level
    beneath the center. A folder value left empty means "the file lies directly in the folder
    above this level".
    """
    found: list[Where] = []
    for parameter, negated in (("where", False), ("not", True)):
        for text in query.get(parameter, ()):
            key, equals, value = str(text).partition("=")
            if not equals:
                message = f"{parameter} is <axis>=<value>; {text!r} has no '='"
                raise ApiError(message, parameter=parameter)
            key, _, level_text = key.strip().partition("@")
            axis = axis_named(key, parameter=parameter)
            level = 0
            if axis.key == "folder":
                level = int(level_text) if level_text.isdigit() and int(level_text) > 0 else 1
            found.append(Where(axis=axis, value=value, level=level, negated=negated))
    if len(found) > MAX_WHERE:
        message = f"at most {MAX_WHERE} filters; {len(found)} were given"
        raise ApiError(message, parameter="where")
    return found


# ---------------------------------------------------------------------------
# SQL.
# ---------------------------------------------------------------------------


@dataclass
class Scoped:
    """The files beneath a folder with one column per axis, as SQL and its parameters."""

    sql: str
    parameters: list[Any]
    #: The column holding each axis's value, in arrangement order (``a0``, ``a1``...).
    columns: list[str]


class _Joins:
    """The theme tables a query joins, one per theme kind (and read once however many axes), and
    a person's groups and exclusions where an axis or a filter reads them."""

    def __init__(self, themes: str, files: str) -> None:
        self.themes = themes
        self.files = files
        self.aliases: dict[str, str] = {}
        self.people: set[str] = set()

    def alias(self, theme_kind: str) -> str:
        if not _THEME_KIND.match(theme_kind):  # checked again: it is spelled into SQL
            message = f"{theme_kind!r} is not a theme kind"
            raise ApiError(message, parameter="arrange")
        if theme_kind not in self.aliases:
            self.aliases[theme_kind] = f"t{len(self.aliases)}"
        return self.aliases[theme_kind]

    def sql(self) -> str:
        joined = [
            f"LEFT JOIN (SELECT entry_id, terms, spellings FROM {self.themes} "  # noqa: S608
            f"WHERE theme_kind = '{kind}') {alias} ON {alias}.entry_id = f.entry_id"
            for kind, alias in self.aliases.items()
        ]
        # A file is in a person's group, or set aside, by itself or by any folder above it.
        carriers = (
            f"(SELECT entry_id, unnest(list_append(ancestors, entry_id)) AS carrier "  # noqa: S608
            f"FROM {self.files})"
        )
        if "group" in self.people:
            joined.append(
                f"LEFT JOIN (SELECT c.entry_id, list(DISTINCT h.name ORDER BY h.name) AS names "  # noqa: S608
                f"FROM {carriers} c JOIN human_members h ON h.member = c.carrier "
                "GROUP BY c.entry_id) hg ON hg.entry_id = f.entry_id"
            )
        if "excluded" in self.people:
            joined.append(
                f"LEFT JOIN (SELECT DISTINCT c.entry_id FROM {carriers} c "  # noqa: S608
                "JOIN human_excluded x ON x.member = c.carrier) hx ON hx.entry_id = f.entry_id"
            )
        return " ".join(joined)


def _expression(axis: Axis, *, focus_depth: int, folder_level: int, joins: _Joins) -> str:
    """The SQL for a file's value on one axis: text, '' for none, NULL where a folder chain
    ends above the level asked (the file lies directly in the folder above)."""
    if axis.key == "folder":
        # A file's ancestors list its folders from the root (index 1, depth 0) down to its own. The
        # center sits at index depth + 1, and folder level k beneath it at depth + 1 + k. Empty
        # where the file lies above that level: "directly in the folder above".
        return f"coalesce(f.ancestors[{focus_depth + 1 + folder_level}], '')"
    if axis.key == "unit":
        return "coalesce(f.unit_id, '')"
    if axis.measure is not None:
        return f"f.{axis.measure.key}"
    if axis.key == "year":
        return f"coalesce(strftime({_BEST}, '%Y'), '')"
    if axis.key == "month":
        return f"coalesce(strftime({_BEST}, '%Y-%m'), '')"
    if axis.key == "h.group":
        joins.people.add("group")
        return f"coalesce(array_to_string(hg.names, '{PAIR}'), '')"
    if axis.key == "h.excluded":
        joins.people.add("excluded")
        return f"CASE WHEN hx.entry_id IS NOT NULL THEN '{SET_ASIDE}' ELSE '' END"
    assert axis.theme_kind is not None
    alias = joins.alias(axis.theme_kind)
    column = "spellings" if axis.spelled else "terms"
    return f"coalesce(array_to_string({alias}.{column}, '{PAIR}'), '')"


def scoped(
    *,
    files: str,
    themes: str,
    focus_id: str,
    focus_depth: int,
    axes: Sequence[Axis],
    where: Sequence[Where],
    extra_columns: str = "",
    every_column: bool = False,
) -> Scoped:
    """Every file beneath ``focus_id`` passing ``where``, with its value on each axis.

    ``every_column`` selects every column of the files table (a page of files), else the few
    an aggregate reads."""
    joins = _Joins(themes, files)
    columns: list[str] = []
    selected: list[str] = []
    level = 0
    for index, axis in enumerate(axes):
        if axis.key == "folder":
            level += 1
        expression = _expression(axis, focus_depth=focus_depth, folder_level=level, joins=joins)
        columns.append(f"a{index}")
        selected.append(f"{expression} AS a{index}")
    conditions = ["list_contains(f.ancestors, ?)"]
    parameters: list[Any] = [focus_id]
    for item in where:
        expression = _expression(
            item.axis, focus_depth=focus_depth, folder_level=item.level or 1, joins=joins
        )
        condition = f"{expression} = ?"
        parameters.append(item.value)
        conditions.append(f"NOT ({condition})" if item.negated else condition)
    chosen = ", ".join(selected)
    base = (
        "f.*"
        if every_column
        else "f.entry_id, f.name, f.kind, f.exam, f.stage, f.prov, f.bytes, f.folder_id, f.unit_id"
    )
    sql = (
        f"SELECT {base}{', ' + chosen if chosen else ''}"  # noqa: S608
        f"{', ' + extra_columns if extra_columns else ''} "
        f"FROM {files} f {joins.sql()} WHERE {' AND '.join(conditions)}"
    )
    return Scoped(sql=sql, parameters=parameters, columns=columns)


# ---------------------------------------------------------------------------
# Words and identifiers.
# ---------------------------------------------------------------------------


def sector_id(values: Sequence[str]) -> str:
    """A value sector's identifier: its values from the center outward, each quoted."""
    return "v:" + "/".join(quote(value, safe="") for value in values)


def register_people(connection: Any, project: Any, run_id: str | None = None) -> str | None:
    """Register a person's standing groups and exclusions as the tables the ``h.`` axes join.

    ``run_id`` is the run the view reads (the one the workspace pinned): the experiments a topic's
    cores and lines resolve to are those of that run. The function returns a sentence saying why a
    topic's cores or lines could not be resolved, or None. A view that draws the arrangement says
    it, so the files of experiments such a member would add are never quietly counted under "in
    none of your topics".
    """
    import pyarrow as pa

    from ingence.api.routes.overlay import standing_layer

    layer = standing_layer(project)
    members = [(member, name) for _, name, held in layer.groups for member in held]
    followed, notice = _followed_members(project, run_id)
    members += followed
    connection.register(
        "human_members",
        pa.table(
            {"member": [m for m, _ in members], "name": [n for _, n in members]},
            schema=pa.schema([("member", pa.string()), ("name", pa.string())]),
        ),
    )
    connection.register(
        "human_excluded",
        pa.table({"member": list(layer.excluded)}, schema=pa.schema([("member", pa.string())])),
    )
    return notice


def _followed_members(project: Any, run_id: str | None) -> tuple[list[tuple[str, str]], str | None]:
    """The experiments a topic's cores and lines resolve to in the run read, as members of that
    topic: ``([(experiment folder, topic name)], notice)``.

    A core is followed by its words and a line is found again by its experiments
    (``ingence.topics``), so they are read from the run, where an entry, a folder or an experiment
    a topic names is a member as it was said. Nothing is read where no topic holds a core or a
    line, so a person's groups of files and folders cost nothing more. The function never fails the
    view, and never hides that it could not. Where a core or a line cannot be read (a run that
    applied no rules, a component not available, a reading that failed), the notice says so in
    words.
    """
    try:
        from ingence.api.routes.overlay import topics_standing
        from ingence.storage.commit import read_pointer
        from ingence.topics import topics_engine

        topics = topics_standing(project)
        if not any(member.kind in ("core", "line") for topic in topics for member in topic.members):
            return [], None
        if run_id is None:
            pointer = read_pointer(project)
            if pointer is None:
                return [], None
            run_id = pointer.run_id
        found = topics_engine().resolve(project, run_id, suggestions=False)
    except Exception as error:  # an optional reading: the view is drawn, and says what is missing
        return [], (
            "Your collections could not be read for this view, so the experiments their cores and "
            "lines would add are counted under \u201cin none of your collections\u201d "
            f"({type(error).__name__})."
        )
    followed = sorted(
        {
            (experiment, reading.topic.name)
            for reading in found.topics
            for member in reading.members
            if member.kind in ("core", "line")
            for experiment in member.experiments
        }
    )
    whys = sorted(
        {
            member.why
            for reading in found.topics
            for member in reading.members
            if member.kind in ("core", "line") and member.state == "not_computed" and member.why
        }
    )
    notice = (
        "Some of your collections were not read for this view ("
        + "; ".join(whys)
        + "): the experiments their cores and lines would add are counted under "
        "\u201cin none of your collections\u201d."
        if whys
        else None
    )
    return followed, notice


def best_time_sql() -> str:
    """A file's best available time as SQL over the files table aliased ``f``."""
    return _BEST


def value_label(axis: Axis, value: str | None, *, names: Mapping[str, str] | None = None) -> str:
    """A value in words: a term's underscores as spaces, a folder by its name, none as none."""
    if value is None or value == "":
        return axis.none_label
    if axis.key in ("folder", "unit"):
        return (names or {}).get(value, value)
    if axis.measure is not None:
        for category in axis.measure.categories:
            if category.key == value:
                return category.label
        return value
    if axis.family == "names":
        return PAIR.join(part.replace("_", " ") for part in value.split(PAIR))
    return value
