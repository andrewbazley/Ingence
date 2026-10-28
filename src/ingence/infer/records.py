"""Assembling one entry's records for every entry in a run, and nothing a rule does not read.

The catalog holds a run as tables, and a predicate is handed one entry's records. This module is the
join between the two, pushed into DuckDB. It is bounded by what the rules in force declare they
read. A Rulebook whose predicates read only bindings never loads an observation. This is because
memory is the figure to watch, and the observations table is the largest a dive produces.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from ingence.core.ids import Scope, ScopeKind
from ingence.core.schema import (
    Component,
    DictionaryBinding,
    Entry,
    Observation,
    Root,
    ScanError,
)
from ingence.infer.subjects import EntryRecords, ScopeRecords
from ingence.rulebook.registry import Reads
from ingence.storage.views import view_from_row

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Mapping, Sequence

    from ingence.core.schema import Record
    from ingence.dictionary.programs import ProgramLayout
    from ingence.storage.catalog import Catalog

__all__ = [
    "assemble_entry_records",
    "assemble_scope_records",
    "batched_rows",
    "observations_of",
    "tables_read",
]


def tables_read(reads: Collection[Reads]) -> frozenset[str]:
    """Return the tables one entry's records must be assembled from, for these reads.

    Components come with bindings, because telling a binder that never ran from a binder that found
    nothing needs both.
    """
    wanted = frozenset(reads)
    tables: set[str] = set()
    if wanted & {
        Reads.COMPONENTS,
        Reads.BINDINGS,
        Reads.RECORDS,
        Reads.FORMAT_ORIGIN,
        Reads.FOLDER_CONTENTS,
        Reads.NAME_DATES,
    }:
        tables.add("components")
    if wanted & {Reads.BINDINGS, Reads.RECORDS, Reads.FORMAT_ORIGIN}:
        tables.add("dictionary_bindings")
    if wanted & {Reads.OBSERVATIONS, Reads.RECORDS, Reads.ENTRY_OBSERVATIONS}:
        tables.add("observations")
    if Reads.ENTRY_IN_ROOT in wanted:
        tables.add("roots")
    return frozenset(tables)


def assemble_entry_records(
    catalog: Catalog,
    *,
    reads: Collection[Reads],
    spellings: Mapping[str, tuple[str, ...]] | None = None,
    origins: Mapping[str, Mapping[str, str]] | None = None,
    with_failures: bool = False,
    with_slots: bool = False,
    with_observations: bool = True,
    words_as: str = "terms",
) -> tuple[EntryRecords, ...]:
    """Return one entry's records for every entry the run has not tombstoned, by identifier.

    Every sequence inside is in canonical order, so a predicate sees the same subject however the
    run's fragments were written. ``spellings`` are the Dictionary's, handed to every entry alike
    where a rule reads a name against them. ``origins`` are handed likewise where a rule reads who
    writes a format's spelling.

    ``with_failures`` loads what went wrong reading each entry. It is a pass-level decision and not
    a declared read, on ``spellings``' precedent: which rules are in force decides it, and a
    predicate asks for a scope and not for a failure. ``with_slots`` loads the order a name's
    components were written in, on the same terms, for a rule that keys on positions. ``words_as``
    is the project's ``groups.words_as``, handed to every entry alike.

    An error naming an entry the run does not hold is left where it is. A path that could not be
    stat'd has no row in ``entries``, so there is no entry for it to be a failure of and no folder
    it can be counted into. That is a real limit on what a concentration rule can see. A mount that
    is not mounted at all enumerates nothing, and the rule that would raise it needs entries to
    count.
    """
    tables = tables_read(reads)
    if not with_observations:
        # A streamed pass loads observations a chunk at a time, through :func:`observations_of`,
        # while the rules reading them read them.
        tables = tables - {"observations"}
    # Each row becomes a read-side view of its record. It is validated as the write path left it,
    # copied into a slotted type holding the same values, and the record is dropped. Rules read
    # attributes and narrow by ``isinstance``, and a view answers both. The rows are ordered here
    # and not by DuckDB. A query's ORDER BY would sort the whole table in memory before the first
    # batch arrived, and that would be assembly's high point.
    entries = sorted(
        (
            view_from_row(row, Entry)
            for row in batched_rows(catalog, "SELECT * FROM entries WHERE NOT is_deleted")
        ),
        key=lambda entry: entry.entry_id,
    )
    components = _grouped(catalog, "components", "component_id", Component, tables)
    bindings = _grouped(catalog, "dictionary_bindings", "binding_id", DictionaryBinding, tables)
    observations = _grouped(catalog, "observations", "observation_id", Observation, tables)
    roots = (
        {
            str(row["root_id"]): view_from_row(row, Root)
            for row in batched_rows(catalog, "SELECT * FROM roots ORDER BY root_id")
        }
        if "roots" in tables
        else {}
    )
    failures = (
        _grouped(
            catalog,
            "scan_errors",
            "relative_path, operation",
            ScanError,
            frozenset({"scan_errors"}),
        )
        if with_failures
        else {}
    )
    slots = _slots(catalog, components) if with_slots else {}
    return tuple(
        EntryRecords(
            entry=entry,
            components=tuple(components.get(entry.entry_id, ())),
            bindings=tuple(bindings.get(entry.entry_id, ())),
            observations=tuple(observations.get(entry.entry_id, ())),
            root=roots.get(entry.root_id),
            spellings=spellings or {},
            origins=origins or {},
            failures=tuple(failures.get(entry.entry_id, ())),
            slots=slots.get(entry.entry_id, ()),
            words_as=words_as,
        )
        for entry in entries
    )


def _slots(
    catalog: Catalog, components: Mapping[str, list[Component]]
) -> dict[str, tuple[str, ...]]:
    """Return each entry's components in the order the name wrote them.

    The order is read out of the name shape, which holds one slot per occurrence, because that is
    where the order is. A component is deduplicated by value and says only how often it occurred.
    The identifiers are taken from the components already loaded, so a slot costs a pointer and not
    another copy of a digest. A slot naming a component this entry does not carry is left out and
    not guessed at.
    """
    known = {
        component.component_id: component.component_id
        for held in components.values()
        for component in held
    }
    found: dict[str, tuple[str, ...]] = {}
    for entry_id, ordered in catalog.query(
        "SELECT entry_id, json_extract_string(slots_json, '$[*].component_id') AS ordered "
        "FROM name_shapes WHERE source_scope = 'name'"
    ).fetchall():
        held = tuple(known[item] for item in (ordered or []) if item in known)
        if held:
            found[str(entry_id)] = held
    return found


#: How many rows one batch of a table holds on its way into views. This is a granularity and not a
#: ceiling: every row is still read, and only this many are held as dictionaries at once. The value
#: comes from a measurement on a small real census, with no whole-table sort in DuckDB. Assembly's
#: high point was 415 MiB at 65,536 rows a batch and 325 MiB at 8,192 and at 2,048, in the same
#: time.
ROWS_PER_BATCH = 8_192


def observations_of(catalog: Catalog, entry_ids: Sequence[str]) -> dict[str, list[Observation]]:
    """Return some entries' observations, grouped and ordered as the whole pass groups them.

    A streamed pass loads these for one chunk: the same views, in the same order, as
    :func:`assemble_entry_records` would have attached to these entries.
    """
    grouped: dict[str, list[Observation]] = defaultdict(list)
    if not entry_ids:
        return grouped
    for row in batched_rows(
        catalog,
        "SELECT * FROM observations WHERE entry_id IN (SELECT unnest($ids))",
        {"ids": list(entry_ids)},
    ):
        grouped[str(row["entry_id"])].append(view_from_row(row, Observation))
    return _ordered(grouped, ("observation_id",))


def batched_rows(
    catalog: Catalog, sql: str, parameters: object = None
) -> Iterator[dict[str, object]]:
    """Yield a query's rows one batch at a time, as DuckDB streams them.

    The function never holds the whole table as dictionaries at once. That was 569 MiB of a 4.27 GiB
    peak on its own, held only until each row became a record. The rows stream from the query and
    not from a materialised Arrow table. That table's ``to_batches`` copies while the table stays
    alive, so streaming from it costs more memory than it saves.
    """
    reader = catalog.query(sql, parameters).to_arrow_reader(ROWS_PER_BATCH)
    for batch in reader:
        yield from batch.to_pylist()


def _grouped[RecordType: Record](
    catalog: Catalog,
    table: str,
    key: str,
    record: type[RecordType],
    tables: frozenset[str],
) -> dict[str, list[RecordType]]:
    """Return a table's records grouped by entry, or nothing where no rule reads the table.

    Each entry's records are ordered here, by ``key``, as DuckDB's ``ORDER BY entry_id, key``
    ordered them, without DuckDB sorting the whole table in memory first.
    """
    grouped: dict[str, list[RecordType]] = defaultdict(list)
    if table not in tables:
        return grouped
    for row in batched_rows(catalog, f"SELECT * FROM {table}"):  # noqa: S608
        grouped[str(row["entry_id"])].append(view_from_row(row, record))
    return _ordered(grouped, tuple(column.strip() for column in key.split(",")))


def _ordered[Held](
    grouped: dict[str, list[Held]], columns: tuple[str, ...]
) -> dict[str, list[Held]]:
    """Order each entry's records by the columns, nulls last, as DuckDB's ascending order does."""

    def placed(held: Held) -> tuple[tuple[bool, object], ...]:
        return tuple(
            (value is None, value) for value in (getattr(held, column) for column in columns)
        )

    for held in grouped.values():
        held.sort(key=placed)
    return grouped


def assemble_scope_records(
    records: Sequence[EntryRecords],
    *,
    layouts: tuple[ProgramLayout, ...] = (),
    unloaded_packs: Mapping[str, frozenset[str]] | None = None,
    unloaded_instruments: Mapping[str, frozenset[str]] | None = None,
    idle_packs: Mapping[str, Any] | None = None,
) -> tuple[ScopeRecords, ...]:
    """Return one scope per indexed root and one per folder, each holding what it covers.

    The scopes are a view and never a second load. The entries handed over are the same objects the
    earlier phases were handed, so a scope costs a grouping and not a copy, and memory does not
    move whatever the pool's shape.

    A root holds every entry beneath it, and a folder holds the entries directly in it. The
    asymmetry is the point of having two levels. A root scope exists so a rule can see *across*
    folders: that a label recurs in fourteen of them is a fact no folder carries. A folder scope
    exists to *localise*. A mount that is not mounted, or a vocabulary nobody has taught the
    engine, sits in one folder, and the rules that read one say so in their own rationale: "one
    finding rather than a hundred". Counting every descendant into a folder's rate would raise
    every ancestor of a bad folder as well. That is a hundred findings and not one, and it is the
    opposite of what those rules ask.

    Nothing is lost by holding direct children only, because a nested region is its own scope and
    is raised where it lives.

    A folder with nothing in it is not a scope. A scope is a region with entries, and a region with
    none has no rate to compute and nothing for a rule to cite. Such a folder never appears as a
    parent here, so it is absent by construction and not by a filter.

    Root scopes come first, then folders, each in identifier order, so two runs over an unchanged
    pool hand every rule its scopes in the same sequence.

    A folder scope also sees what lies beside it: its parent's entries, as the parent's own scope
    holds them, so the tuple is shared and not copied. ``layouts`` are the programs and packages
    the Dictionary declares, handed to every scope where a rule in force reads them.
    """
    under_root: dict[str, list[EntryRecords]] = defaultdict(list)
    within_folder: dict[str, list[EntryRecords]] = defaultdict(list)
    own: dict[str, EntryRecords] = {}
    for entry_records in records:
        under_root[entry_records.entry.root_id].append(entry_records)
        own[entry_records.entry.entry_id] = entry_records
        parent = entry_records.entry.parent_entry_id
        if parent is not None:
            within_folder[parent].append(entry_records)
    pool = tuple(records)
    held = {folder_id: tuple(entries) for folder_id, entries in within_folder.items()}

    def beside(folder_id: str) -> tuple[EntryRecords, ...]:
        holder = own.get(folder_id)
        parent = None if holder is None else holder.entry.parent_entry_id
        return () if parent is None else held.get(parent, ())

    return (
        *(
            ScopeRecords(
                scope=Scope(ScopeKind.ROOT, root_id),
                entries=tuple(under_root[root_id]),
                pool=pool,
                layouts=layouts,
                unloaded_packs=unloaded_packs or {},
                unloaded_instruments=unloaded_instruments or {},
                idle_packs=idle_packs or {},
            )
            for root_id in sorted(under_root)
        ),
        *(
            ScopeRecords(
                scope=Scope(ScopeKind.FOLDER, folder_id),
                entries=held[folder_id],
                pool=pool,
                holder=own.get(folder_id),
                siblings=beside(folder_id),
                layouts=layouts,
            )
            for folder_id in sorted(within_folder)
        ),
    )
