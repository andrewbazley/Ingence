"""Merging a run's fragments into one sorted file, before it is published.

A scan flushes small fragments as it goes. That is right for durability and wrong for two other
things.

Reading. A table split across hundreds of small fragments costs a file open and a footer parse
each. It also defeats the row-group statistics that let a reader skip data it does not need.

Determinism. Fragment boundaries depend on when flush thresholds fire, which depends on thread
scheduling and disk speed. Two logically identical runs would produce the same rows in a different
number of files, so their output would not be byte-identical even though nothing about the data
differed. Merging into one canonically sorted file removes the dependency.

Compaction runs before the manifest is built, so the manifest describes what is published and not
an intermediate state.

Memory is bounded on both ends. The results are consumed as a stream of record batches and written
incrementally, so the sorted result is never fully materialised. DuckDB's sort itself is not
bounded by its memory limit. It holds every row it sorts, and a limit below the table's size makes
it refuse the sort and not spill it. So a table of more rows than :data:`PARTITION_ROWS` is sorted
in ranges of its first sort column, one range at a time, and written in order. A scan that can
enumerate a tree can compact it.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ingence.core.ids import canonical_json
from ingence.storage.connection import connect as duckdb_connection
from ingence.storage.index_store import COMPRESSION, ROW_GROUP_SIZE, fragment_paths
from ingence.storage.layout import JSON_SUFFIX, owned_path

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.storage.layout import TableSpec

__all__ = ["COMPACTED_NAME", "compact_run", "compact_table"]

#: The single fragment a compacted table becomes. The name is fixed and not numbered, because a
#: compacted table has exactly one fragment and a name that encoded a count would change when the
#: count did.
COMPACTED_NAME = "part-00000.parquet"

#: Rows pulled from DuckDB at a time. Bounds peak memory during the write.
BATCH_ROWS = 64 * 1024

#: The rows one sort holds at once. DuckDB's sort holds every row of what it sorts, whatever its
#: memory limit (measured: a limit below the table's size refused the sort and did not spill it).
#: A claim's row is wide: its defeaters are written whole, each with its rule's sentence, about
#: 4 KB a row as DuckDB holds it. So a table of more rows than this is split in one pass into
#: buckets by ranges of its first sort column. Each bucket is sorted and written in order. The rows
#: and their order are the same, and the memory is that of one bucket. This is a granularity and
#: not a ceiling: every row is still written.
PARTITION_ROWS = 32 * 1024

#: The most buckets one merge splits a table into. Each bucket is a file open while the table is
#: read, and a bucket holds a table's rows divided by this many.
MAXIMUM_BUCKETS = 64

#: The most ranges that a table may be read in, each by a query of its own. A table split into this
#: many ranges or fewer is read once per range. That costs a few reads of the table and holds no
#: bucket files. Above this number the table is read once, into buckets. Measured on a small real
#: census: its claims (three ranges) merged at +0.14 GiB read range by range and +0.27 GiB bucketed.
MAXIMUM_REREADS = 4

#: The bytes that the bucketing pass holds at once on their way to their buckets. The pass reads as
#: many rows as fit, measured on the table's first rows, and never more than ``BATCH_ROWS``. A
#: claim's row is wide (about 4 KB) and a component's is narrow. A batch too small for a narrow
#: table spends its time writing slivers. Measured on a bench of millions of narrow rows in
#: hundreds of fragments: 16,384 rows a batch took about three times as long as batches sized by
#: bytes, and sorting the table whole took a third longer again. A large claims table took over a
#: third more memory at 65,536 rows a batch than at batches sized by bytes.
BUCKETING_BYTES = 64 * 1024 * 1024

#: The first-column values sampled to place the buckets' bounds. The sample is large enough that
#: each of the most buckets has about sixty-four values, and it is a sample and never the column.
BOUND_SAMPLE = 4096


def _projection(spec: TableSpec, sources: list[str]) -> str:
    """Return the column list to read these fragments under the table's declared schema.

    A column that the declared schema has and no fragment holds is selected as null. The
    projection could name every declared column and rely on ``union_by_name``, which tolerates
    fragments written across a schema addition. That holds for a mixed set, where some fragment has
    the column and DuckDB fills the rest. Where every fragment predates the addition, the column
    exists nowhere and the query is refused. Compaction would then fail, and it runs on every
    publish. A project written before a field was added could not be dived, inferred or published
    by an engine that added the field.

    For example, every fragment of a project's findings written at schema 1.12 lacks
    ``defeaters_json``, which schema 1.17 declares.

    The null is right and not merely convenient. A field added compatibly has a default meaning
    "this run recorded none", which is what a row written before the field existed says. The cast
    to the declared schema gives the null the declared type.
    """
    if not sources:
        return ", ".join(f'"{name}"' for name in spec.schema.names)
    connection = duckdb_connection()
    try:
        present = {
            str(row[0])
            for row in connection.execute(
                "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet($sources, "
                "union_by_name = true))",
                {"sources": sources},
            ).fetchall()
        }
    finally:
        connection.close()
    return ", ".join(
        f'"{name}"' if name in present else _absent(spec, name) for name in spec.schema.names
    )


def _absent(spec: TableSpec, name: str) -> str:
    """Return the value to read for a declared column no fragment holds.

    There are three cases, and the third refuses and does not invent.

    A nullable column reads as null, which is what the absence is.

    A non-nullable column reads as its field's declared default, encoded the way
    :func:`~ingence.storage.index_store.row_for` would encode it. Every compatible addition to this
    schema so far has been a collection defaulting to empty (``excluded``, ``mentions``,
    ``defeaters``). For those the default is the truth and not only a convenience: a run that could
    not record any recorded none, which is what an empty list says.

    A non-nullable column whose field has no default is refused, with the column named. Filling it
    would assert something about rows written before anybody could have asked. This is the
    distinction that unknown is not empty, applied to storage. A person migrating a project needs
    to be told which column it stopped at.
    """
    field = spec.schema.field(name)
    if field.nullable:
        return f'NULL AS "{name}"'
    stem = name[: -len(JSON_SUFFIX)] if name.endswith(JSON_SUFFIX) else name
    declared = spec.model.model_fields.get(stem)
    if declared is None or declared.is_required():
        message = (
            f"the fragments of {spec.name!r} carry no {name!r}, the declared schema requires "
            f"it, and {stem!r} has no default to stand in for what was never recorded. This "
            "project was written before the field existed and needs migrating rather than "
            "reading."
        )
        raise CompactionRefusedError(message)
    default = declared.default_factory() if declared.default_factory else declared.default  # type: ignore[call-arg]
    if name.endswith(JSON_SUFFIX):
        literal = canonical_json(list(default) if isinstance(default, tuple) else default)
        return f"'{literal}' AS \"{name}\""
    if isinstance(default, str):
        escaped = default.replace("'", "''")
        return f"'{escaped}' AS \"{name}\""
    return f'{default!r} AS "{name}"'


class CompactionRefusedError(RuntimeError):
    """Raised where a fragment set cannot be read under the declared schema."""


def _rows_in(connection: duckdb.DuckDBPyConnection, sources: list[str]) -> int:
    """How many rows the fragments hold, read from their footers."""
    rows = connection.execute(
        "SELECT coalesce(sum(num_rows), 0) FROM parquet_file_metadata($sources)",
        {"sources": sources},
    ).fetchone()
    return int(rows[0]) if rows else 0


def _bounds(
    connection: duckdb.DuckDBPyConnection, spec: TableSpec, sources: list[str], buckets: int
) -> list[object]:
    """The first sort column's values that bound the buckets, from a sample of the column.

    The bounds are values, so a value's rows are never split. A bucket holds every row whose first
    column lies between two bounds, and the buckets in order hold the table in order. The bounds
    come from a sample and never the whole column, because a quantile over the column would hold all
    of it. The sample is repeatable. The bounds decide only how the rows are shared out and never
    their order.
    """
    first = f'"{spec.sort_by[0]}"'
    sampled = sorted(
        value
        for (value,) in connection.execute(
            f"SELECT {first} FROM read_parquet($sources, union_by_name = true) "  # noqa: S608
            f"TABLESAMPLE reservoir({BOUND_SAMPLE} ROWS) REPEATABLE (29)",
            {"sources": sources},
        ).fetchall()
        if value is not None
    )
    if not sampled:
        return []
    return sorted({sampled[len(sampled) * index // buckets] for index in range(1, buckets)})


def _ranges(spec: TableSpec, bounds: list[object]) -> list[tuple[str, dict[str, object]]]:
    """The first sort column's ranges between the bounds, as filters and their values, in order.

    Nulls last, as DuckDB orders them. No bounds is one range: the whole table.
    """
    if not bounds:
        return [("", {})]
    first = f'"{spec.sort_by[0]}"'
    ranges: list[tuple[str, dict[str, object]]] = [
        (f"WHERE {first} < $upper", {"upper": bounds[0]})
    ]
    ranges.extend(
        (f"WHERE {first} >= $lower AND {first} < $upper", {"lower": lower, "upper": upper})
        for lower, upper in itertools.pairwise(bounds)
    )
    ranges.append((f"WHERE {first} >= $lower", {"lower": bounds[-1]}))
    ranges.append((f"WHERE {first} IS NULL", {}))
    return ranges


def _bucketed(
    connection: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    sources: list[str],
    columns: str,
    bounds: list[object],
    directory: Path,
) -> list[Path]:
    """Read every row once and write it to its bucket's file; return the files, in order.

    The pass reads the fragments once. A query per range would read every fragment again for each
    range, and a pass's fragments each span the whole key range, so the merge would grow with the
    square of the table. DuckDB computes the bucket, with nulls last.
    """
    first = f'"{spec.sort_by[0]}"'
    cases = " ".join(f"WHEN {first} < $bound{index} THEN {index}" for index in range(len(bounds)))
    null_bucket = len(bounds) + 1
    bucket = f"CASE WHEN {first} IS NULL THEN {null_bucket} {cases} ELSE {len(bounds)} END"
    rows_per_batch = _rows_per_batch(connection, sources, columns)
    result = connection.execute(
        f"SELECT {columns}, {bucket} AS {_BUCKET} "  # noqa: S608 - declared columns and bounds
        "FROM read_parquet($sources, union_by_name = true)",
        {"sources": sources, **{f"bound{index}": value for index, value in enumerate(bounds)}},
    )
    paths = [directory / f".bucket-{number:03d}.incoming" for number in range(null_bucket + 1)]
    writers: dict[int, pq.ParquetWriter] = {}
    try:
        for batch in result.to_arrow_reader(rows_per_batch):
            numbers = batch.column(_BUCKET)
            # The batch's rows grouped by bucket, then each bucket's run written to its file.
            held = pa.Table.from_batches([batch.take(pc.sort_indices(numbers))]).drop_columns(
                [_BUCKET]
            )
            counted = pc.value_counts(numbers)
            start = 0
            for number, count in sorted(
                zip(
                    counted.field("values").to_pylist(),
                    counted.field("counts").to_pylist(),
                    strict=True,
                )
            ):
                writer = writers.get(number)
                if writer is None:
                    # A bucket is read once, soon, by this process: written uncompressed.
                    writer = pq.ParquetWriter(paths[number], spec.schema, compression="none")
                    writers[number] = writer
                writer.write_table(held.slice(start, count).cast(spec.schema))
                start += count
    finally:
        for writer in writers.values():
            writer.close()
    return [path for number, path in enumerate(paths) if number in writers]


def _rows_per_batch(connection: duckdb.DuckDBPyConnection, sources: list[str], columns: str) -> int:
    """As many rows as :data:`BUCKETING_BYTES` holds, measured on the table's first rows."""
    first_rows = connection.execute(
        f"SELECT {columns} FROM read_parquet($sources, union_by_name = true) LIMIT 1024",  # noqa: S608
        {"sources": sources},
    ).to_arrow_table()
    if not first_rows.num_rows:
        return BATCH_ROWS
    width = max(1, int(first_rows.nbytes) // int(first_rows.num_rows))
    return max(1024, min(BATCH_ROWS, BUCKETING_BYTES // width))


#: The column the bucketing pass adds to each row, and drops before writing it.
_BUCKET = "__ingence_bucket"


def _write_sorted(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    parameters: dict[str, object],
    spec: TableSpec,
    writer: pq.ParquetWriter,
) -> int:
    """Write one sorted query's rows in batches; return how many."""
    written = 0
    for batch in connection.execute(sql, parameters).to_arrow_reader(BATCH_ROWS):
        # Cast and do not trust. DuckDB applies its own type mapping on the way out, and the
        # declared Arrow schema is the contract. A column that comes back as int32 must be stored
        # as int64, or the fragment will not concatenate with one written by pyarrow.
        table = pa.Table.from_batches([batch]).cast(spec.schema)
        writer.write_table(table, row_group_size=ROW_GROUP_SIZE)
        written += batch.num_rows
    return written


def compact_table(directory: Path, spec: TableSpec, *, partition_rows: int | None = None) -> int:
    """Merge one table's fragments into a single canonically sorted file.

    Returns the number of rows written. Does nothing and returns zero for a table with no
    fragments, which is a legitimate state and not an error: a census run has no claims and a clean
    project has no scan errors.

    A table that already consists of exactly one correctly named fragment is still rewritten. That
    fragment was sorted within itself when it was written, but nothing has yet sorted it against the
    table's declared order.
    """
    paths = fragment_paths(directory)
    if not paths:
        return 0

    order = ", ".join(f'"{column}"' for column in spec.sort_by)
    sources = [str(path) for path in paths]
    columns = _projection(spec, sources)
    destination = directory / f".{COMPACTED_NAME}.incoming"

    connection = duckdb_connection()
    written = 0
    try:
        budget = PARTITION_ROWS if partition_rows is None else partition_rows
        buckets = min(MAXIMUM_BUCKETS, -(-_rows_in(connection, sources) // max(1, budget)))
        bounds = _bounds(connection, spec, sources, buckets) if buckets > 1 else []
        writer = pq.ParquetWriter(
            destination,
            spec.schema,
            compression=COMPRESSION,
            store_schema=True,
        )
        try:
            if len(bounds) < MAXIMUM_REREADS:
                # A few ranges, or one: each is read by a query of its own. The projection names
                # what the fragments hold and fills what they lack, because ``union_by_name``
                # does not reach a column that no fragment has at all.
                for where, values in _ranges(spec, bounds):
                    written += _write_sorted(
                        connection,
                        f"SELECT {columns} FROM read_parquet($sources, union_by_name = true) "  # noqa: S608
                        f"{where} ORDER BY {order}",
                        {"sources": sources, **values},
                        spec,
                        writer,
                    )
            else:
                for bucket in _bucketed(connection, spec, sources, columns, bounds, directory):
                    written += _write_sorted(
                        connection,
                        f"SELECT * FROM read_parquet($bucket) ORDER BY {order}",  # noqa: S608
                        {"bucket": [str(bucket)]},
                        spec,
                        writer,
                    )
                    bucket.unlink()
        finally:
            writer.close()
    finally:
        connection.close()
        for leftover in directory.glob(".bucket-*.incoming"):
            leftover.unlink()

    for path in paths:
        path.unlink()
    destination.replace(directory / COMPACTED_NAME)
    return written


def compact_run(run_directory: Path, specs: tuple[TableSpec, ...]) -> dict[str, int]:
    """Compact every table of a staged run. Returns rows per table.

    The scan calls this before the commit, so that what the manifest lists and what the invariants
    ran against are the same files a reader will open. Each table's folder, whose fragments are
    deleted and replaced, is checked to be the staged run's own and no link
    (``storage.layout.owned_path``). The staging itself is checked where it is made and published.
    """
    return {
        spec.name: compact_table(owned_path(run_directory, spec.directory), spec)
        for spec in specs
        if not spec.spans_runs
    }
