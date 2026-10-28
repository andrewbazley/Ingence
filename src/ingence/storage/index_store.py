"""Append-only Parquet: turning records into fragments, and reading them back.

Two properties govern everything here.

A run's tables are a complete snapshot, not a delta. A re-scan that reuses nine tenths of its
entries still writes all of them. That costs storage and buys something worth more: reading the
Index is a single table read of one run directory, with no history to replay and no window
function to get right. It also makes the current pointer meaningful, because resolving the pointer
gives everything and not the most recent slice of everything. Old runs can be pruned when that
trade stops paying. Nothing depends on them existing.

Fragments are written during a scan and merged before a commit. Small frequent flushes are right
for durability, because a scan interrupted an hour in should not lose an hour. One large sorted
file is right for reading. It also makes the output byte-identical between two identical runs,
because fragment boundaries otherwise depend on thread timing. Compaction reconciles the two. It
happens before the manifest is built, so the manifest describes what is published.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq

from ingence.core.ids import canonical_json
from ingence.storage.layout import JSON_SUFFIX

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ingence.core.schema import Record
    from ingence.storage.layout import TableSpec

__all__ = [
    "COMPRESSION",
    "FragmentWriter",
    "fragment_paths",
    "read_table",
    "record_from_row",
    "row_for",
    "sort_key",
    "write_fragment",
]

#: Zstandard: better ratio than snappy at comparable speed, and supported by
#: every Parquet reader that matters. Fixed rather than configurable, because a
#: codec chosen per run would make two identical runs produce different bytes.
COMPRESSION = "zstd"

#: Rows per row group. Large enough that statistics are useful for predicate
#: pushdown, small enough that a reader need not materialise a whole fragment.
ROW_GROUP_SIZE = 64 * 1024


def row_for(record: Record, spec: TableSpec) -> dict[str, Any]:
    """Convert one record into the column dictionary its table expects.

    The naming rule links the two contracts: a column named ``x`` holds field
    ``x``, and a column named ``x_json`` holds field ``x`` serialised
    canonically. Canonically matters: a nested structure serialised with
    unordered keys would differ between runs while describing the same thing,
    which is one of the two mechanical causes of nondeterminism.
    """
    dumped = record.model_dump(mode="json")
    row: dict[str, Any] = {}
    for column in spec.schema.names:
        if column.endswith(JSON_SUFFIX):
            row[column] = canonical_json(dumped[column[: -len(JSON_SUFFIX)]])
        else:
            row[column] = dumped[column]
    return row


def record_from_row[RecordType: Record](
    row: Mapping[str, Any], record: type[RecordType]
) -> RecordType:
    """Rebuild one record from the column dictionary its table holds.

    The inverse of :func:`row_for`, and the one place the storage naming rule is
    reversed: a column named ``x_json`` holds field ``x`` serialised canonically and
    is parsed back, and every other column is the field of the same name.

    One function serves every record type. A reversal written per type is correct for the columns
    its author remembered, and the one it forgets is the column added next.
    """
    payload: dict[str, Any] = {}
    for column, value in row.items():
        if column.endswith(JSON_SUFFIX):
            payload[column[: -len(JSON_SUFFIX)]] = None if value is None else json.loads(value)
        else:
            payload[column] = value
    return record.model_validate(payload)


def sort_key(row: dict[str, Any], spec: TableSpec) -> tuple[Any, ...]:
    """Return the canonical ordering key for one row.

    ``None`` sorts before every value, rather than raising when compared with a
    string, which it would otherwise do the first time a nullable sort column
    appeared.
    """
    return tuple((value is not None, value) for value in (row[c] for c in spec.sort_by))


def _out_of_range(rows: Sequence[dict[str, Any]], spec: TableSpec) -> str:
    """Name the first integer a batch holds that its column cannot store, with its row's key."""
    for row in rows:
        for column in spec.schema:
            value = row.get(column.name)
            if not pa.types.is_integer(column.type) or not isinstance(value, int):
                continue
            width = column.type.bit_width
            signed = pa.types.is_signed_integer(column.type)
            low, high = (
                (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, (1 << width) - 1)
            )
            if not low <= value <= high:
                key = {name: row.get(name) for name in spec.primary_key}
                return f"{column.name} {value} is outside {column.type} (row {key})"
    return "an integer outside its column's range"


def write_fragment(
    rows: Sequence[dict[str, Any]], spec: TableSpec, path: Path | IO[bytes]
) -> Path | IO[bytes]:
    """Write one Parquet fragment, sorted, under the table's declared schema.

    ``path`` is the fragment's file, or a file already open to write it into (the Record's append
    creates its fragment exclusively, so nothing standing at a name is written through).

    The schema is passed explicitly rather than inferred. Inference on an empty
    batch produces a table with no columns, and inference on a batch where every
    value of a nullable column happens to be absent produces a null-typed column,
    both of which then fail to concatenate with a fragment written from
    different data.
    """
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        table = pa.Table.from_pylist(list(rows), schema=spec.schema)
    except OverflowError as exc:
        # Say what could not be stored. Arrow's own message names no table, column or value, so an
        # overflow on a machine nobody can reach would say only "int too big to convert". The range
        # checks before a flush stop that error. This message makes the next gap in them findable.
        raise OverflowError(f"{spec.name}: {_out_of_range(rows, spec)}") from exc
    pq.write_table(
        table,
        path,
        compression=COMPRESSION,
        row_group_size=ROW_GROUP_SIZE,
        # Statistics let a reader skip row groups. Deterministic, since they are
        # computed from the data rather than from the writing.
        write_statistics=True,
        # The Arrow schema is embedded so a reader reconstructs exact types
        # rather than Parquet's lossier physical ones.
        store_schema=True,
    )
    return path


class FragmentWriter:
    """Buffers records for one table and flushes them as fragments.

    Flushing is driven by record count or elapsed time, whichever comes first.
    Counting alone stalls durability on a slow share where records arrive
    rarely. Timing alone flushes constantly on a fast local disk. The two
    together avoid both.

    The class is not thread-safe, by design. Use one writer per table per thread, and let the
    caller own any fan-in. Locking here would put a mutex on the hot path of a scan whose threads
    otherwise share nothing.
    """

    def __init__(
        self,
        spec: TableSpec,
        directory: Path,
        *,
        flush_every_records: int = 4096,
        flush_every_seconds: float = 30.0,
    ) -> None:
        self.spec = spec
        self.directory = directory
        self.flush_every_records = flush_every_records
        self.flush_every_seconds = flush_every_seconds
        self._buffer: list[dict[str, Any]] = []
        self._fragments = 0
        self._rows_written = 0
        self._last_flush: float | None = None

    @property
    def rows_written(self) -> int:
        """How many rows have reached disk."""
        return self._rows_written

    @property
    def pending(self) -> int:
        """How many rows are buffered and not yet on disk."""
        return len(self._buffer)

    def add(self, record: Record, *, now: float) -> None:
        """Buffer one record, flushing if a threshold has been reached."""
        self._buffer.append(row_for(record, self.spec))
        # The "never flushed" sentinel is None and not zero. A clock reading of exactly zero is a
        # legitimate value, and using zero as the sentinel would stop time-driven flushing from
        # ever firing, without any error.
        if self._last_flush is None:
            self._last_flush = now
        if (
            len(self._buffer) >= self.flush_every_records
            or now - self._last_flush >= self.flush_every_seconds
        ):
            self.flush(now=now)

    def extend(self, records: Iterable[Record], *, now: float) -> None:
        """Buffer several records."""
        for record in records:
            self.add(record, now=now)

    def flush(self, *, now: float) -> Path | None:
        """Write the buffer as a fragment. Returns the path, or None if empty."""
        self._last_flush = now
        if not self._buffer:
            return None
        rows = self._buffer
        rows.sort(key=lambda row: sort_key(row, self.spec))
        path = self.directory / f"part-{self._fragments:05d}.parquet"
        write_fragment(rows, self.spec, path)
        self._fragments += 1
        self._rows_written += len(rows)
        self._buffer = []
        return path

    def close(self, *, now: float) -> int:
        """Flush anything remaining and return the total rows written."""
        self.flush(now=now)
        return self._rows_written


def fragment_paths(directory: Path) -> list[Path]:
    """Return a table's fragments in canonical order."""
    return sorted(directory.glob("*.parquet"))


def read_table(directory: Path, spec: TableSpec) -> pa.Table:
    """Read every fragment of one table as a single Arrow table.

    Returns an empty table under the declared schema when the directory holds
    nothing, rather than raising. A table with no rows is a legitimate state: a
    census run has no claims, and a project with no errors has no error rows.
    """
    paths = fragment_paths(directory)
    if not paths:
        return spec.schema.empty_table()
    return pa.concat_tables([pq.read_table(path, schema=spec.schema) for path in paths])
