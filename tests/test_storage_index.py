"""Fragments are written durably, then merged into one sorted file."""

from __future__ import annotations

import hashlib

from ingence.core.schema import Entry, EntryType
from ingence.storage.compact import COMPACTED_NAME, compact_table
from ingence.storage.index_store import (
    FragmentWriter,
    fragment_paths,
    read_table,
    record_from_row,
    row_for,
)
from ingence.storage.layout import table_for

SPEC = table_for(Entry)


def entry(identifier: str, *, size: int = 0, path: str | None = None) -> Entry:
    return Entry(
        entry_id=identifier,
        root_id="root_x",
        relative_path=path or identifier,
        name=identifier,
        entry_type=EntryType.FILE,
        size_bytes=size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


def test_a_record_maps_onto_its_columns(tmp_path):
    row = row_for(entry("entry_a"), SPEC)
    assert set(row) == set(SPEC.schema.names)
    assert row["entry_id"] == "entry_a"
    # Enumerations are stored as their values, not their repr.
    assert row["entry_type"] == "file"
    # Nested structures are canonically serialised into the _json column.
    assert row["locator_chain_json"] == "[]"


def test_a_value_no_column_can_store_is_named_when_the_write_refuses_it(tmp_path):
    """Arrow's own message names nothing ("int too big to convert"), and the engine's names the
    table, the column and the value.

    So a failure on a machine nobody can reach says where.
    """
    import pytest

    writer = FragmentWriter(SPEC, tmp_path)
    writer.add(entry("entry_0").model_copy(update={"device": 1 << 64}), now=0.0)
    with pytest.raises(OverflowError, match=r"entries: device \d+ is outside int64"):
        writer.close(now=1.0)


def test_an_inode_above_the_signed_range_is_stored_and_read_back_exactly(tmp_path):
    """POSIX makes an inode unsigned, and a real one may lie at or above 2**63.

    macOS's ExFAT driver gives every empty file an inode whose top 32 bits are all set.
    Stored in a signed column, the first such entry refused the whole fragment and the
    census could not publish at all. The largest unsigned value is the edge of the class.
    """
    values = (2**63, 0xFFFFFFFF_00000001, 2**64 - 1)
    records = [
        entry(f"entry_{index}").model_copy(update={"inode": value})
        for index, value in enumerate(values)
    ]
    writer = FragmentWriter(SPEC, tmp_path)
    for record in records:
        writer.add(record, now=0.0)
    writer.close(now=1.0)
    rows = read_table(tmp_path, SPEC).to_pylist()
    assert [row["inode"] for row in rows] == list(values)
    assert [record_from_row(row, Entry).inode for row in rows] == list(values)
    # The catalog reads every table through DuckDB's `read_parquet`, so read it that way too.
    import duckdb

    sources = [path.as_posix() for path in fragment_paths(tmp_path)]
    found = duckdb.connect(":memory:").execute(
        "SELECT inode FROM read_parquet(?) ORDER BY entry_id", [sources]
    )
    assert [row[0] for row in found.fetchall()] == list(values)


def test_flushing_is_driven_by_count(tmp_path):
    writer = FragmentWriter(SPEC, tmp_path, flush_every_records=2)
    for index in range(5):
        writer.add(entry(f"entry_{index}"), now=float(index))
    assert writer.close(now=9.0) == 5
    assert len(fragment_paths(tmp_path)) == 3


def test_flushing_is_also_driven_by_time(tmp_path):
    """Counting alone stalls durability where records arrive rarely."""
    writer = FragmentWriter(SPEC, tmp_path, flush_every_records=1000, flush_every_seconds=1.0)
    writer.add(entry("entry_0"), now=0.0)
    writer.add(entry("entry_1"), now=5.0)
    assert fragment_paths(tmp_path)


def test_an_empty_table_reads_back_under_its_declared_schema(tmp_path):
    table = read_table(tmp_path, SPEC)
    assert table.num_rows == 0
    assert table.schema.equals(SPEC.schema)


def test_compaction_merges_and_sorts(tmp_path):
    writer = FragmentWriter(SPEC, tmp_path, flush_every_records=2)
    for identifier in ["entry_e", "entry_a", "entry_d", "entry_b", "entry_c"]:
        writer.add(entry(identifier), now=0.0)
    writer.close(now=1.0)
    assert len(fragment_paths(tmp_path)) > 1

    assert compact_table(tmp_path, SPEC) == 5
    assert [path.name for path in fragment_paths(tmp_path)] == [COMPACTED_NAME]
    table = read_table(tmp_path, SPEC)
    assert table.column("entry_id").to_pylist() == [
        "entry_a",
        "entry_b",
        "entry_c",
        "entry_d",
        "entry_e",
    ]
    assert table.schema.equals(SPEC.schema)


def test_compaction_removes_the_dependency_on_flush_timing(tmp_path):
    """Fragment boundaries depend on thread scheduling. Compacted output must not."""

    def build(directory, every):
        writer = FragmentWriter(SPEC, directory, flush_every_records=every)
        for identifier in ["entry_c", "entry_a", "entry_b", "entry_e", "entry_d"]:
            writer.add(entry(identifier, size=7), now=0.0)
        writer.close(now=1.0)
        compact_table(directory, SPEC)
        return hashlib.sha256((directory / COMPACTED_NAME).read_bytes()).hexdigest()

    assert build(tmp_path / "a", 1) == build(tmp_path / "b", 4)


def test_compacting_nothing_is_not_an_error(tmp_path):
    assert compact_table(tmp_path, SPEC) == 0


def test_a_stored_row_reads_back_as_the_record_it_was_written_from(tmp_path):
    """The naming rule reversed once, for every record type, through a real table.

    A claim rather than an entry because it carries the most serialised fields: a
    scope, a support vector and its defeaters all live in ``_json`` columns, and a
    rebuilder written per type is correct only for the columns its author
    remembered.
    """
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import Claim, Defeater, SupportComponent

    claim = Claim(
        claim_id="claim_x",
        run_id="run_1",
        subject="entry_a",
        predicate="classifies.format_from_extension",
        object_reference="markdown",
        rule_id="format_from_extension",
        rule_version="2",
        scope=PROJECT_SCOPE,
        inputs=("binding_b", "binding_a"),
        support=(SupportComponent(name="extension", family="format_binding", value=1.0),),
        defeaters=(
            Defeater(reason="the content names another format", rule_id="r", rule_version="1"),
        ),
    )
    spec = table_for(Claim)
    writer = FragmentWriter(spec, tmp_path)
    writer.add(claim, now=0.0)
    writer.close(now=1.0)
    rows = read_table(tmp_path, spec).to_pylist()

    assert record_from_row(rows[0], Claim) == claim
    assert record_from_row(row_for(entry("entry_a"), SPEC), Entry) == entry("entry_a")
