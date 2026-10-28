"""XLSX: sheets, their shape, header rows, and the workbook's own clock.

Tabular data is where the dates are. A laboratory's spreadsheet holds a column of measurement dates,
a plate layout with condition names, and a header row whose words the Dictionary already knows.

A workbook is a ZIP and the cascade already opens it, so this is not the difference between
something and nothing. What it adds is structure: the cascade sees ``xl/worksheets/sheet1.xml`` and
a pile of shared strings, and this sees three sheets, their dimensions, and what their first row is
called.

The workbook is opened read-only and values-only, and both halves matter. ``read_only`` streams
rather than building the whole workbook in memory, which is the difference
between a bounded read and an unbounded one on a file somebody left a million
rows in. ``data_only`` returns the cached value of a formula rather than the
formula: the engine records what the sheet says, and a formula is what the sheet
would say if something recalculated it, which nothing here will.

The extractor is isolated, like the other parsers of ZIP and XML formats. A spreadsheet is a
ZIP of XML, and both
layers are input the extractor did not write. Declaring the boundary is one line.

It fills clock tier two, from the workbook's own core part. ``dcterms:created``
is the application's own metadata, it travels with the bytes, and a copy does not
move it. ``modified`` is recorded and is not offered as the event, for the reason a
PDF's is not: it moves whenever a tool rewrites the file.

openpyxl's properties are never read, because they are not the file's. Tests with
openpyxl 3.1.5 on workbooks built for the purpose show two problems:

- Where the core part is missing or states no date, ``created`` and ``modified`` come
  back as the moment the workbook was loaded, and wherever the part names no creator,
  the creator comes back as ``openpyxl``. Offering the first as the clock would give every
  workbook without a creation date the scan's own time as its tier-two clock.
- Where the core part states a date W3CDTF allows and openpyxl does not, a year, a
  year and a month, or a day without a time, openpyxl refuses the whole workbook, and
  the result would be a content error with no sheet read.

So the core part is read by the reader that the office extractor uses, and the two cannot
disagree about a package's date. A property that the part does not state is not recorded.
openpyxl reads the sheets with its own reading of the property parts left out.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING, Any

from ingence.core.outcomes import ScanOutcome, TimeSource, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    declined,
    failed,
)
from ingence.extractors.limits import Limits
from ingence.extractors.ooxml import (
    CORE_PROPERTIES,
    CORE_RELATIONSHIP,
    Package,
    PartRefusedError,
    Properties,
    core_readings,
)

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["SPREADSHEET_EXTENSIONS", "SpreadsheetExtractor"]

#: ``.xls`` is deliberately absent. It is OLE compound storage rather than OOXML, a different
#: container entirely, and the compound extractor (``compound.py``) reads it. Claiming it here
#: would make this extractor run on files it cannot read and report a content error.
SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm", ".xltx"}

#: How many sheets are described. A workbook with four hundred sheets is real and
#: describing all of them would make one file dominate the observation table. The
#: count is still recorded, so the truncation is visible rather than silent.
MAX_SHEETS_DESCRIBED = 32

#: How many header cells are read from a sheet's first row. A header is the row a
#: Dictionary can recognise words in, and a sheet 5,000 columns wide is a matrix
#: rather than a table.
MAX_HEADER_CELLS = 64

#: The core properties recorded as text, as ``table.title`` and ``table.author``.
_TABLE_TEXT = ("title", "author")


class SpreadsheetExtractor:
    """Reads an OOXML workbook's sheets, their shape and their header rows."""

    name = "spreadsheet"
    #: Two, because the readings changed for unchanged input: the properties come
    #: from the workbook's core part rather than from openpyxl's defaults.
    version = "2"
    #: A ZIP of XML, parsed by a library, on input the engine did not write.
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return bool(SPREADSHEET_EXTENSIONS & context.extensions)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        try:
            from openpyxl.reader.excel import ExcelReader
        except ImportError:
            return declined(self.name, self.version, "openpyxl is not installed")

        limits = context.limits or Limits()
        readings: list[Reading] = []
        event: tuple[int, TimeSource] | None = None

        try:
            with context.open() as handle:
                with zipfile.ZipFile(handle) as archive:
                    package = Package(archive, remaining=limits.max_bytes_read)
                    try:
                        event = _core_properties(package, readings)
                    except PartRefusedError as exc:
                        # Recorded as the office extractor records it, and nothing more
                        # is read: a package carrying a forbidden part is not handed to
                        # a second parser.
                        readings.append(Reading("table.part_refused", True, ValueKind.NEGATIVE))
                        return self._result(context, readings, None, (str(exc),))

                workbook = _open_workbook(ExcelReader, handle)
                try:
                    names = list(workbook.sheetnames)
                    readings.append(Reading("table.sheet_count", len(names), ValueKind.INTEGER))
                    readings.append(
                        Reading(
                            "table.sheet_names",
                            _joined(names[:MAX_SHEETS_DESCRIBED]),
                            ValueKind.STRUCTURE,
                        )
                    )
                    readings.extend(_describe_sheets(workbook, names, limits))
                finally:
                    # Read-only workbooks hold the ZIP open. Closed in a finally
                    # because a parse failure halfway through must not leave a
                    # handle on a file the engine promised only to read.
                    workbook.close()
        except Exception as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])

        return self._result(context, readings, event, ())

    def _result(
        self,
        context: ExtractionContext,
        readings: list[Reading],
        event: tuple[int, TimeSource] | None,
        warnings: tuple[str, ...],
    ) -> ExtractorResult:
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            warnings=warnings,
            bytes_read=context.size_bytes,
            event_time_ns=event[0] if event else None,
            event_time_source=event[1] if event else None,
        )


def _core_properties(package: Package, readings: list[Reading]) -> tuple[int, TimeSource] | None:
    """Record what the workbook's own core part states, and return the clock it offers.

    The core part is the one the package's own relationships name, read exactly as the
    office extractor reads a document's. A workbook naming none records no property and
    offers no clock.
    """
    event: tuple[int, TimeSource] | None = None
    for _, kind, part, external in package.relationships(""):
        if external or kind != CORE_RELATIONSHIP:
            continue
        core = Properties(dict(CORE_PROPERTIES))
        package.stream(part, core)
        event = core_readings(core.values, readings, namespace="table", text_keys=_TABLE_TEXT)
    return event


def _open_workbook(reader_type: Any, handle: Any) -> Any:
    """Open a workbook for its sheets, with openpyxl's reading of the property parts left out.

    Built exactly as ``load_workbook`` builds it, read-only and values-only, with the two
    steps that parse the core and custom property parts replaced by ones that read
    nothing. The engine reads the core part itself, and openpyxl's reading of it refuses
    whole workbooks over dates the format allows. A test holds each of those dates, so
    an openpyxl that renamed the two steps would fail it rather than quietly read the
    parts again.
    """
    reader = reader_type(handle, read_only=True, data_only=True)
    reader.read_properties = _read_nothing
    reader.read_custom = _read_nothing
    reader.read()
    return reader.wb


def _read_nothing() -> None:
    """Stand in for one of openpyxl's property readers, whose result nothing here uses."""


def _describe_sheets(workbook: Any, names: list[str], limits: Limits) -> list[Reading]:
    """Return one set of readings per described sheet.

    Dimensions come from the sheet's declared extent rather than by counting
    rows, which on a read-only workbook would stream the whole file to learn its
    size. A declared extent can be wrong, and it is recorded as formally
    extracted rather than literal because of that.
    """
    del limits
    readings: list[Reading] = []
    for index, name in enumerate(names[:MAX_SHEETS_DESCRIBED]):
        sheet = workbook[name]
        rows = getattr(sheet, "max_row", None)
        columns = getattr(sheet, "max_column", None)
        if rows is not None:
            readings.append(Reading(f"table.sheet.{index}.row_count", int(rows), ValueKind.INTEGER))
        if columns is not None:
            readings.append(
                Reading(f"table.sheet.{index}.column_count", int(columns), ValueKind.INTEGER)
            )

        header = _header_of(sheet)
        if header:
            readings.append(
                Reading(
                    f"table.sheet.{index}.column_names",
                    _joined(header),
                    ValueKind.STRUCTURE,
                    # A first row is a header by convention and not by
                    # declaration. The cells are literal; calling them column
                    # names is a reading of what the sheet means.
                    ExtractionTier.HEURISTIC,
                )
            )
        else:
            readings.append(Reading(f"table.sheet.{index}.header_absent", True, ValueKind.NEGATIVE))
    return readings


def _header_of(sheet: Any) -> list[str]:
    """Return the first row's non-empty cells as strings, bounded."""
    try:
        rows = sheet.iter_rows(min_row=1, max_row=1, max_col=MAX_HEADER_CELLS, values_only=True)
        first = next(iter(rows), ())
    except Exception:
        return []
    return [str(cell).strip() for cell in first if cell is not None and str(cell).strip()]


def _joined(values: list[str]) -> str:
    """Return a canonical JSON serialisation of a list."""
    from ingence.core.ids import canonical_json

    return canonical_json(values)


@EXTRACTORS.register(
    "spreadsheet",
    version="2",
    summary="OOXML workbooks: sheets, shape, header rows, and a tier-two clock",
    requires=("openpyxl",),
    attributes={"extensions": ",".join(sorted(SPREADSHEET_EXTENSIONS))},
)
def _build_spreadsheet() -> SpreadsheetExtractor:
    return SpreadsheetExtractor()
