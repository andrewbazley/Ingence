"""Delimited tables: the columns, the shape, and what the columns hold.

The most common shape of scientific data that is not an image, and the one where
structure is cheapest to recover. A header line and a sample of rows give the
column names, the column count, and a usable guess at each column's type, which
is enough to say that two tables are the same kind of table.

The reads are bounded by design. Column types come from a sample of rows, not the file. The
bytes read are bounded by the extraction limit, and every row inside them is
counted: the rows are in memory already, so this reads no more of the file, and
it takes time in proportion to the bytes. Where the file was read whole, the row
count is exact and says so. Where the
read was cut short, the rows seen are a lower bound and are recorded as one
(``table.row_count_at_least``), never as the count. A count and a lower bound
are different readings, so neither can be mistaken for the other.

Types are declared as guesses. A column of digits might be a measurement, an
identifier, or a barcode that will overflow when somebody parses it as a number.
The type is recorded at the heuristic tier because that is what it is.
"""

from __future__ import annotations

import csv
import io
import re
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json
from ingence.core.outcomes import ScanOutcome, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    declined,
    failed,
)
from ingence.extractors.delimited import detect_delimiter
from ingence.extractors.limits import Limits

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.extractors.base import ExtractionContext

__all__ = ["TabularExtractor", "guess_column_type"]

DELIMITED_EXTENSIONS = {".csv", ".tsv", ".tab"}
DELIMITED_TYPES = {"text/csv", "text/tab-separated-values"}

#: Rows sampled for type inference. Enough to see past a block of blanks at the
#: top of a column, few enough to stay cheap on a large export.
SAMPLE_ROWS = 200

_INTEGER = re.compile(r"^[+-]?\d+$")
_REAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
_BOOLEAN = {"true", "false", "yes", "no", "t", "f", "y", "n", "0", "1"}


def guess_column_type(values: Sequence[str]) -> str:
    """Return a guess at what a column holds, from a sample of its values.

    Blanks are ignored rather than counted as a type, because a column with a
    gap in it is still a column of numbers. A column that is entirely blank is
    reported as empty, which is a finding: it usually means an export wrote a
    header for something it had no data for.
    """
    present = [value.strip() for value in values if value and value.strip()]
    if not present:
        return "empty"
    if all(_INTEGER.match(value) for value in present):
        return "integer"
    if all(_REAL.match(value) for value in present):
        return "real"
    if all(_ISO_DATE.match(value) for value in present):
        return "date"
    if all(value.casefold() in _BOOLEAN for value in present):
        return "boolean"
    return "text"


class TabularExtractor:
    """Reads the header and a sample of rows from a delimited table."""

    name = "tabular"
    #: Version 2 read a semicolon as a delimiter. Version 3 counts every row read, not the 200
    #: sampled for column types, and a read cut short states a lower bound
    #: (``table.row_count_at_least``).
    version = "3"
    #: Splitting a bounded sample on delimiters is arithmetic over bytes
    #: already in hand, with no format library between it and them.
    isolation = Isolation.IN_PROCESS

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in DELIMITED_TYPES:
            return True
        return bool(context.extensions & DELIMITED_EXTENSIONS)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        try:
            payload = context.read(limits.max_bytes_read)
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))
        if not payload:
            return declined(self.name, self.version, "the file is empty")

        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Not text under the one encoding this extractor is willing to
            # assume. Declining is honest; the generic baseline will still
            # harvest whatever readable runs are in there.
            return declined(self.name, self.version, "content is not UTF-8 text")

        lines = text.splitlines()
        delimiter = detect_delimiter(lines[0] if lines else "", context.extensions)

        # Every row in the bytes read is counted, not only the sample typed from. The rest
        # are in memory already, so counting them reads no more of the file, but it takes time in
        # proportion to the bytes. On a laptop, a table of 4 MiB (the default limit) takes
        # 0.4 s in all and one of 64 MiB takes 7 s, nearly all of it this count. An empty
        # line is not a row, in the sample or in the count. A row the parser cannot read after the
        # sample stops the count where it is: what was counted stands as a lower bound.
        rows: list[list[str]] = []
        counted, unreadable = 0, False
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            header = next(reader)
            for row in reader:
                if not row:
                    continue
                counted += 1
                if len(rows) < SAMPLE_ROWS:
                    rows.append(row)
        except StopIteration as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))
        except csv.Error as exc:
            if len(rows) < SAMPLE_ROWS:
                return failed(self.name, self.version, type(exc).__name__, str(exc))
            unreadable = True

        if len(header) < 2:
            return declined(self.name, self.version, "a single column is not a table")

        # Whole only when the read was not cut short and every row was readable. A row count
        # over a truncated read would be a smaller number presented as a total.
        whole = len(payload) < limits.max_bytes_read and not unreadable
        structural = ExtractionTier.FORMALLY_EXTRACTED
        heuristic = ExtractionTier.HEURISTIC

        columns = [name.strip() for name in header]
        types = [
            guess_column_type([row[index] for row in rows if index < len(row)])
            for index in range(len(columns))
        ]
        ragged = sum(1 for row in rows if len(row) != len(columns))

        readings = [
            Reading("table.column_count", len(columns), ValueKind.INTEGER, structural),
            Reading("table.delimiter", delimiter, ValueKind.TEXT, structural),
            Reading(
                "table.column_names",
                canonical_json(columns),
                ValueKind.STRUCTURE,
                structural,
            ),
            Reading(
                "table.column_types",
                canonical_json(dict(zip(columns, types, strict=True))),
                ValueKind.STRUCTURE,
                heuristic,
            ),
            Reading("table.rows_sampled", len(rows), ValueKind.INTEGER, structural),
            Reading("table.row_count_is_exact", whole, ValueKind.BOOLEAN, structural),
        ]
        if whole:
            readings.append(Reading("table.row_count", counted, ValueKind.INTEGER, structural))
        else:
            # A read cut short ends mid-row unless its last byte closes one; that row is not
            # counted, so the figure never exceeds the rows there are.
            complete = counted - (0 if text.endswith(("\n", "\r")) or unreadable else 1)
            if complete > 0:
                readings.append(
                    Reading("table.row_count_at_least", complete, ValueKind.INTEGER, structural)
                )
        if ragged:
            # Rows that do not match the header are worth surfacing: they are
            # usually a delimiter inside an unquoted field, which silently
            # corrupts whatever reads the file next.
            readings.append(Reading("table.ragged_rows", ragged, ValueKind.INTEGER, structural))

        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=len(payload),
        )


@EXTRACTORS.register(
    "tabular",
    version="3",
    summary="delimited tables: columns, shape and guessed column types",
    attributes={"extensions": ",".join(sorted(DELIMITED_EXTENSIONS))},
)
def _build_tabular() -> TabularExtractor:
    return TabularExtractor()
