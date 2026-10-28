"""The columns a dive stored for each table of a published run, and what they relate.

The module reads four stored observations per table and nothing else of the table: the digest of
its column names, order and delimiter (``identity.tabular_schema``), its column count
(``table.column_count``), its column names as the table wrote them (``table.column_names``) and
its delimiter (``table.delimiter``). A table with a digest and a count of at least the floor is a
table of the index. Its class is every table with the same digest.

Where a file has two readings of one key (a dive under a newer engine wrote beside an older one),
the module reads the newest observer's version, as the rule's predicate does. Two readings by that
version that disagree leave the table out, and the file page says so.

Inclusion is over normalised names. A table's names are lower-cased and their white space
collapsed (``extractors.delimited.normalise_column``), and a table's set is the set of them. Table
B's columns contain table A's when A's set is a subset of B's and their digests differ. Equal
sets with different digests (the same names in another order, or another delimiter) are listed
as the same names written another way, with nothing added. The index is an inverted index from
a name to the distinct sets holding it, so a table's containers are found by intersecting the
lists of its own names, the shortest first.

Memory: the index holds one entry per table, with its path, its raw names and its digest. A pool of
a million tables with ten names each takes in the order of a gigabyte. One query builds the index,
and the module keeps the index for the last few runs read.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.core.settings import ColumnSettings
from ingence.extractors.delimited import normalise_column

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = [
    "PROJECTION_VERSION",
    "RULE_ID",
    "ColumnProjection",
    "ColumnSummary",
    "FileColumns",
    "Related",
    "Table",
    "build_projection",
    "column_projection",
    "file_columns",
    "projection_of_catalog",
    "read_catalog",
    "summary_of",
]

#: Moves when the projection's fields or meaning change.
PROJECTION_VERSION = "1"

#: The rule whose floor the page follows.
RULE_ID = "same_column_schema"
DIGEST_KEY = "identity.tabular_schema"
COUNT_KEY = "table.column_count"
NAMES_KEY = "table.column_names"
DELIMITER_KEY = "table.delimiter"
#: The delimited-table extensions the table reader reads (``extractors/tabular.py``).
TABLE_EXTENSIONS = (".csv", ".tsv", ".tab")

_KEYS = (DIGEST_KEY, COUNT_KEY, NAMES_KEY, DELIMITER_KEY)

_MEMORY: dict[str, ColumnProjection] = {}
_MEMORY_KEPT = 4
_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class Table:
    """One table of a published run, as the dive stored it."""

    entry_id: str
    #: Its path under its root.
    path: str
    folder: str | None
    digest: str
    count: int
    #: The names as the table wrote them (stripped), or None where the dive stored none.
    names: tuple[str, ...] | None
    delimiter: str | None


@dataclass(frozen=True, slots=True)
class Related:
    """A table another table's columns are compared to."""

    table: Table
    #: Columns the other table has and this one lacks (a container's), as the other wrote them.
    added: tuple[str, ...] = ()
    #: Columns this table has and the other lacks (a contained table's), as this one wrote them.
    missing: tuple[str, ...] = ()
    #: True where the names are the same and only their order or the delimiter differs.
    same_names: bool = False


@dataclass(frozen=True, slots=True)
class ColumnSummary:
    """The figures for the quality report, each beside what it was counted among."""

    #: Tables whose columns were read and that reach the floor: the denominator of the rest.
    tables_counted: int
    #: Delimited tables by extension in the run, whether or not their columns were read.
    tables_in_run: int
    #: Classes of two or more tables.
    classes: int
    #: The tables in those classes.
    tables_in_classes: int
    #: Classes whose tables lie in more than one folder.
    classes_spanning_folders: int
    #: Tables below the floor or whose reading was not stored, so not counted.
    tables_not_counted: int


@dataclass(frozen=True, slots=True)
class FileColumns:
    """One file's columns page: what it holds, and the tables it relates to."""

    entry_id: str
    #: Whether the file is a table of the index. If it is not, ``why_not`` says why in words.
    read: bool
    why_not: str | None
    path: str
    names: tuple[str, ...]
    count: int | None
    delimiter: str | None
    same_columns: tuple[Related, ...] = ()
    inclusion_on: bool = True
    contained_in: tuple[Related, ...] = ()
    contains: tuple[Related, ...] = ()
    #: How many files each list holds whole, before the page's limit.
    same_columns_total: int = 0
    contained_in_total: int = 0
    contains_total: int = 0
    #: Of the tables with the same columns, how many lie in this table's own folder and how many
    #: elsewhere (the whole counts, whatever the page's limit): "same columns as {n} files in this
    #: folder and {k} others". ``same_columns_here`` names the first of those in the folder.
    same_columns_in_folder: int = 0
    same_columns_elsewhere: int = 0
    same_columns_here: tuple[Related, ...] = ()


@dataclass
class ColumnProjection:
    """A run's tables, their classes and the inverted index of their names."""

    run_id: str
    settings: ColumnSettings
    tables: dict[str, Table] = field(default_factory=dict)
    #: Entries of the run that could not be counted, by entry id, with why in words.
    left_out: dict[str, str] = field(default_factory=dict)
    #: Delimited tables by extension in the run.
    tables_in_run: int = 0
    #: A digest to the sorted entry ids of its tables.
    classes: dict[str, list[str]] = field(default_factory=dict)
    #: The distinct sets of normalised names, and the tables of each (inclusion).
    node_sets: list[frozenset[str]] = field(default_factory=list)
    node_tables: list[list[str]] = field(default_factory=list)
    #: A normalised name to the indexes of the nodes holding it.
    postings: dict[str, list[int]] = field(default_factory=dict)
    #: The node a table with names is in.
    node_of: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Reading the run.
# ---------------------------------------------------------------------------

_SQL = """
SELECT o.entry_id, o.key, o.observer_version, o.value_text, o.value_integer, o.value_structure,
       e.relative_path, e.parent_entry_id
FROM observations o JOIN entries e ON e.entry_id = o.entry_id
WHERE o.key IN (SELECT unnest(?::VARCHAR[])) AND NOT e.is_deleted AND e.entry_type = 'file'
ORDER BY o.entry_id, o.key, o.observation_id
"""

_EXTENSIONS_SQL = """
SELECT count(*) FROM entries
WHERE NOT is_deleted AND entry_type = 'file' AND lower(extension) IN ('csv', 'tsv', 'tab')
"""


def _order(version: str) -> tuple[int, str]:
    return (int(version), "") if version.isdigit() else (-1, version)


def _only(held: _Held | None) -> str | None:
    """The one text a key held, or None where it held none or two."""
    if held is None or len(held.values) != 1:
        return None
    value = next(iter(held.values))
    return value if isinstance(value, str) else None


def _decoded_names(text: str | None) -> tuple[str, ...] | None:
    """The names a table wrote, from the stored JSON list; None where none were stored."""
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except ValueError:
        return None
    if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
        return tuple(decoded)
    return None


@dataclass
class _Held:
    """What the newest version of one key held for one entry."""

    order: tuple[int, str]
    values: set[object] = field(default_factory=set)


def _read_run(
    project: Path, run_id: str
) -> tuple[dict[str, dict[str, _Held]], dict[str, tuple[str, str | None]], int]:
    """Return each table-like entry's newest readings, their places, and the delimited count."""
    from ingence.storage.catalog import open_catalog

    with open_catalog(project, run_id=run_id) as catalog:
        return read_catalog(catalog)


def read_catalog(
    catalog: Catalog,
) -> tuple[dict[str, dict[str, _Held]], dict[str, tuple[str, str | None]], int]:
    """The same reading from a catalog already open, which may be a run staged and not yet
    published (the quality figures read it so)."""
    from ingence.infer.records import batched_rows

    held: dict[str, dict[str, _Held]] = defaultdict(dict)
    places: dict[str, tuple[str, str | None]] = {}
    for row in batched_rows(catalog, _SQL, [list(_KEYS)]):
        entry = str(row["entry_id"])
        key = str(row["key"])
        order = _order(str(row["observer_version"]))
        if key == COUNT_KEY:
            value: object = row["value_integer"]
        elif key == NAMES_KEY:
            value = row["value_structure"]
        else:
            value = row["value_text"]
        places[entry] = (
            str(row["relative_path"] or ""),
            str(row["parent_entry_id"]) if row["parent_entry_id"] else None,
        )
        seen = held[entry].get(key)
        if seen is None or order > seen.order:
            held[entry][key] = _Held(order, {value})
        elif order == seen.order:
            seen.values.add(value)
    in_run = catalog.query(_EXTENSIONS_SQL).fetchone()
    return held, places, int(in_run[0]) if in_run else 0


def build_projection(
    held: dict[str, dict[str, _Held]],
    places: dict[str, tuple[str, str | None]],
    *,
    run_id: str,
    settings: ColumnSettings,
    tables_in_run: int,
) -> ColumnProjection:
    """Assemble a projection from the readings of a run (pure: the tests build it directly)."""
    built = ColumnProjection(run_id=run_id, settings=settings, tables_in_run=tables_in_run)
    for entry in sorted(held):
        readings = held[entry]
        digest = readings.get(DIGEST_KEY)
        count = readings.get(COUNT_KEY)
        path, folder = places[entry]
        if digest is None:
            built.left_out[entry] = "the dive stored no column digest for it"
            continue
        if len(digest.values) != 1:
            built.left_out[entry] = "two readings of its columns disagree"
            continue
        if count is None:
            built.left_out[entry] = "the dive stored its columns but not how many there are"
            continue
        if len(count.values) != 1:
            built.left_out[entry] = "two readings of its number of columns disagree"
            continue
        number = next(iter(count.values))
        value = next(iter(digest.values))
        if not isinstance(number, int) or not isinstance(value, str) or not value:
            built.left_out[entry] = "its stored columns could not be read"
            continue
        if number < settings.minimum_columns:
            built.left_out[entry] = (
                f"it has fewer than {settings.minimum_columns} columns, the least this page lists"
            )
            continue
        built.tables[entry] = Table(
            entry_id=entry,
            path=path,
            folder=folder,
            digest=value,
            count=number,
            names=_decoded_names(_only(readings.get(NAMES_KEY))),
            delimiter=_only(readings.get(DELIMITER_KEY)),
        )
    grouped: dict[str, list[str]] = defaultdict(list)
    for entry, table in built.tables.items():
        grouped[table.digest].append(entry)
    built.classes = {digest: sorted(members) for digest, members in sorted(grouped.items())}
    if settings.inclusion:
        _index_names(built)
    return built


def _index_names(built: ColumnProjection) -> None:
    """Index the distinct sets of normalised names the tables of the floor hold."""
    nodes: dict[frozenset[str], int] = {}
    for entry in sorted(built.tables):
        table = built.tables[entry]
        if table.names is None:
            continue
        names = frozenset(normalise_column(name) for name in table.names)
        if len(names) < built.settings.inclusion_minimum_columns:
            continue
        at = nodes.get(names)
        if at is None:
            at = len(built.node_sets)
            nodes[names] = at
            built.node_sets.append(names)
            built.node_tables.append([])
            for name in sorted(names):
                built.postings.setdefault(name, []).append(at)
        built.node_tables[at].append(entry)
        built.node_of[entry] = at


#: The floor found for a project, by the stamp of its rulebook folder, so a page does not load
#: the whole Rulebook for each request. A rule change writes a file there, which moves the stamp.
_FLOORS: dict[tuple[str, tuple[tuple[str, int, int], ...]], int | None] = {}


def _rulebook_stamp(project: Path) -> tuple[tuple[str, int, int], ...]:
    from ingence.storage.layout import RULEBOOK_DIR

    folder = project / RULEBOOK_DIR
    if not folder.is_dir():
        return ()
    stamp = []
    for path in sorted(folder.iterdir()):
        try:
            seen = path.stat()
        except OSError:
            continue
        stamp.append((path.name, seen.st_mtime_ns, seen.st_size))
    return tuple(stamp)


def rule_floor(project: Path, settings: ColumnSettings) -> int:
    """The fewest columns a table needs: the rule's own ``minimum_columns`` where the rule is in
    force in the project, else the setting's.

    The page and the rule share one floor. Otherwise they would disagree silently once a person
    tuned one and not the other, and the page's "same columns" would list tables that the rule
    does not group.
    """
    key = (str(project.resolve()), _rulebook_stamp(project))
    with _LOCK:
        known = _FLOORS.get(key, -1)
    if known == -1:
        from ingence.infer.evaluate import parameters_in_force
        from ingence.rulebook import project_rulebook

        rule = project_rulebook(project).by_id(RULE_ID)
        known = (
            int(str(parameters_in_force(rule)["minimum_columns"]))
            if rule is not None and any(item.name == "minimum_columns" for item in rule.parameters)
            else None
        )
        with _LOCK:
            _FLOORS[key] = known
            while len(_FLOORS) > _MEMORY_KEPT * 4:
                _FLOORS.pop(next(iter(_FLOORS)))
    return settings.minimum_columns if known is None else int(known)


def column_projection(
    project: Path, run_id: str, settings: ColumnSettings | None = None
) -> ColumnProjection:
    """The run's projection: from memory, or built from the run and kept."""
    if settings is None:
        from ingence.core.project import Project

        settings = Project.open(project).settings.columns
    settings = settings.model_copy(update={"minimum_columns": rule_floor(project, settings)})
    key = f"{project.resolve()}::{run_id}::{PROJECTION_VERSION}::{settings.model_dump_json()}"
    with _LOCK:
        found = _MEMORY.get(key)
    if found is not None:
        return found
    held, places, in_run = _read_run(project, run_id)
    built = build_projection(held, places, run_id=run_id, settings=settings, tables_in_run=in_run)
    with _LOCK:
        _MEMORY[key] = built
        while len(_MEMORY) > _MEMORY_KEPT:
            _MEMORY.pop(next(iter(_MEMORY)))
    return built


def projection_of_catalog(
    catalog: Catalog, run_id: str, settings: ColumnSettings | None = None
) -> ColumnProjection:
    """The projection of a run read through a catalog already open, never kept in memory.

    For a run staged and not yet published (the quality figures are counted from the tables just
    written), where the published projection cannot be asked for. The floor and the settings are
    the ones :func:`column_projection` uses, so the two give one answer for one run.
    """
    project = catalog.project
    if settings is None:
        from ingence.core.project import Project

        settings = Project.open(project).settings.columns
    settings = settings.model_copy(update={"minimum_columns": rule_floor(project, settings)})
    held, places, in_run = read_catalog(catalog)
    return build_projection(held, places, run_id=run_id, settings=settings, tables_in_run=in_run)


# ---------------------------------------------------------------------------
# Questions put to a projection.
# ---------------------------------------------------------------------------


def _by_path(related: Iterable[Related]) -> list[Related]:
    return sorted(
        related,
        key=lambda item: (
            len(item.added) + len(item.missing),
            item.table.path,
            item.table.entry_id,
        ),
    )


def _raw_by_name(table: Table) -> dict[str, str]:
    """A table's names as it wrote them, by their normalised spelling (the first writing wins)."""
    found: dict[str, str] = {}
    for name in table.names or ():
        found.setdefault(normalise_column(name), name)
    return found


def same_columns(built: ColumnProjection, entry_id: str) -> list[Related]:
    """The other tables with this table's digest, in path order."""
    table = built.tables.get(entry_id)
    if table is None:
        return []
    members = built.classes.get(table.digest, [])
    return _by_path(Related(built.tables[member]) for member in members if member != entry_id)


def contained_in(built: ColumnProjection, entry_id: str) -> list[Related]:
    """Tables whose columns contain this table's, each with the columns it adds."""
    at = built.node_of.get(entry_id)
    if at is None:
        return []
    table = built.tables[entry_id]
    mine = built.node_sets[at]
    lists = sorted((built.postings[name] for name in mine), key=len)
    candidates = set(lists[0])
    for posting in lists[1:]:
        candidates.intersection_update(posting)
        if not candidates:
            break
    found: list[Related] = []
    for node in candidates:
        theirs = built.node_sets[node]
        same = node == at
        for member in built.node_tables[node]:
            other = built.tables[member]
            if other.digest == table.digest:
                continue
            raw = _raw_by_name(other)
            added = tuple(
                raw[name] for name in sorted(theirs - mine, key=lambda n: _position(other, n))
            )
            found.append(Related(other, added=added, same_names=same))
    return _by_path(found)


def contains(built: ColumnProjection, entry_id: str) -> list[Related]:
    """Tables whose columns this table's contain, each with the columns it is missing."""
    at = built.node_of.get(entry_id)
    if at is None:
        return []
    table = built.tables[entry_id]
    mine = built.node_sets[at]
    hits: dict[int, int] = defaultdict(int)
    for name in mine:
        for node in built.postings[name]:
            hits[node] += 1
    raw = _raw_by_name(table)
    found: list[Related] = []
    for node, shared in hits.items():
        theirs = built.node_sets[node]
        if shared != len(theirs) or node == at:
            continue
        missing = tuple(
            raw[name] for name in sorted(mine - theirs, key=lambda n: _position(table, n))
        )
        for member in built.node_tables[node]:
            other = built.tables[member]
            if other.digest != table.digest:
                found.append(Related(other, missing=missing))
    return _by_path(found)


def _position(table: Table, name: str) -> int:
    """Where a normalised name first stands among a table's columns."""
    for index, written in enumerate(table.names or ()):
        if normalise_column(written) == name:
            return index
    return 0


def file_columns(built: ColumnProjection, entry_id: str, limit: int | None = None) -> FileColumns:
    """One file's page, whole or the reason it has none (the lists cut at the limit).

    ``limit`` is how many files each list names (the setting's ``files_listed`` where None);
    every count beside a list is the whole.
    """
    settings = built.settings
    table = built.tables.get(entry_id)
    if table is None:
        reason = built.left_out.get(entry_id)
        if reason is None:
            reason = "no column names were stored for it, so it is not read as a table"
        return FileColumns(
            entry_id=entry_id,
            read=False,
            why_not=reason,
            path="",
            names=(),
            count=None,
            delimiter=None,
            inclusion_on=settings.inclusion,
        )
    limit = settings.files_listed if limit is None else limit
    same = same_columns(built, entry_id)
    here = [item for item in same if item.table.folder == table.folder]
    inside = contained_in(built, entry_id) if settings.inclusion else []
    holds = contains(built, entry_id) if settings.inclusion else []
    return FileColumns(
        entry_id=entry_id,
        read=True,
        why_not=None,
        path=table.path,
        names=table.names or (),
        count=table.count,
        delimiter=table.delimiter,
        same_columns=tuple(same[:limit]),
        inclusion_on=settings.inclusion,
        contained_in=tuple(inside[:limit]),
        contains=tuple(holds[:limit]),
        same_columns_total=len(same),
        contained_in_total=len(inside),
        contains_total=len(holds),
        same_columns_in_folder=len(here),
        same_columns_elsewhere=len(same) - len(here),
        same_columns_here=tuple(here[:limit]),
    )


def summary_of(built: ColumnProjection) -> ColumnSummary:
    """The classes of two or more tables, the tables in them and the classes across folders."""
    many = {digest: members for digest, members in built.classes.items() if len(members) >= 2}
    spanning = sum(
        1
        for members in many.values()
        if len({built.tables[member].folder for member in members}) >= 2
    )
    return ColumnSummary(
        tables_counted=len(built.tables),
        tables_in_run=built.tables_in_run,
        classes=len(many),
        tables_in_classes=sum(len(members) for members in many.values()),
        classes_spanning_folders=spanning,
        tables_not_counted=len(built.left_out),
    )
