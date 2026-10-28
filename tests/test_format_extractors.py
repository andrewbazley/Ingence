"""The long-tail format extractors, and the clock tier they were declared to fill.

Each of these declares a process boundary and inherits everything the supervisor was tested
against: a hang, a crash and an exhausted address space are already recorded content errors with
the run completing. The declaration is one line and it is the entire safety story, which is what
makes writing a format parser cheap rather than dangerous.

They fill clock tier two, which was declared with no source. An extractor that reaches a content
clock has an obligation to emit it rather than a decision to take, and these are the first to
carry it.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

import pytest

from ingence.core.outcomes import CLOCK_TIER_OF, ClockTier, ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext, Isolation, isolation_of
from ingence.extractors.document import parse_pdf_date

load_builtins()

pypdf = pytest.importorskip("pypdf")
openpyxl = pytest.importorskip("openpyxl")


def _context(path: Path, extension: str) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({extension}),
        size_bytes=path.stat().st_size,
    )


def _readings(result: object) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The declaration that makes them safe.
# ---------------------------------------------------------------------------


def test_every_format_parser_declares_a_process_boundary() -> None:
    """One line each, and it is the whole safety story.

    A format parser reads input it did not write, which is precisely the case that isolation per
    component exists for. An extractor here that forgot the line would run in the calling thread,
    where a hang cannot be stopped at all.
    """
    formats = (
        "document",
        "spreadsheet",
        "archive",
        "notebook",
        "office",
        "compound",
        "nikon_nd2",
        "leica_lif",
        "lambert_fli",
        "tiff",
    )
    for name in formats:
        assert isolation_of(EXTRACTORS.get(name)) is Isolation.SUBPROCESS


def test_the_cheap_extractors_still_pay_nothing() -> None:
    """Isolating everything makes a cheap pass expensive, which is why it is declared.

    Reading eight kilobytes and hashing a stream cannot hang, so these must not
    have acquired a boundary by being near ones that did.
    """
    for name in ("generic", "text", "tabular"):
        assert isolation_of(EXTRACTORS.get(name)) is Isolation.IN_PROCESS


def test_a_child_imports_no_parser_library_until_its_format_is_read() -> None:
    """Every isolated child imports every builtin module, so none may import a parser at the top.

    ``load_builtins`` runs in each spawned child before the one extractor it was started for, so a
    library imported at module scope by any extractor is paid by every isolated extraction of every
    other format. The child is cut to its floor by moving four enums; one parser imported an
    indentation level too high would put a tenth of a second back on every file, and nothing would
    break.

    Measured in a fresh interpreter, because this process has imported them all.
    """
    import subprocess
    import sys

    libraries = (
        "pypdf",
        "openpyxl",
        "nbformat",
        "olefile",
        # The instrument readers use the standard library. The libraries that read the
        # same formats must stay out of every child, where each would cost a tenth of a
        # second or more.
        "nd2",
        "readlif",
        "tifffile",
        "numpy",
        "PIL",
        "imagecodecs",
        "requests",
        "jsonschema",
        "fastjsonschema",
        # The standard library's XML parser counts: measured at 1.8 ms to import,
        # which every isolated child of every format would otherwise pay.
        "xml.parsers.expat",
    )
    probe = (
        "import sys\n"
        "import ingence.executors.supervisor\n"
        "from ingence.extractors import load_builtins\n"
        "load_builtins()\n"
        f"print(','.join(sorted(name for name in {libraries!r} if name in sys.modules)))\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert finished.stdout.strip() == "", (
        f"importing the builtin extractors imports {finished.stdout.strip()}; a parser "
        f"library belongs inside the extract method of the extractor that needs it"
    )


# ---------------------------------------------------------------------------
# PDF dates: what is a date, and what only looks like one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_resolution"),
    [
        ("D:20260115103000Z", "second"),
        ("D:20260115103000+01'00'", "second"),
        ("D:202601151030", "minute"),
        ("D:20260115", "day"),
        ("20260115103000", "second"),
    ],
)
def test_a_pdf_date_carrying_a_day_is_an_event(raw: str, expected_resolution: str) -> None:
    """A date with no time is a real statement about a day.

    Midnight UTC is how the engine already stores one, and discarding a good day
    for want of an hour would throw away the commonest form a producer writes.
    """
    parsed = parse_pdf_date(raw)
    assert parsed is not None
    _, resolution = parsed
    assert resolution == expected_resolution


@pytest.mark.parametrize(
    "raw",
    [
        "D:2026",
        "D:202601",
        "D:20261315",
        "D:20260132",
        "not a date at all",
        "",
    ],
)
def test_a_pdf_date_short_of_a_day_abstains(raw: str) -> None:
    """Reading a bare year as the first of January asserts a day nobody wrote.

    The same abstention the engine already makes for a year-month in a filename, and for
    the same reason: a wrong day is worse than no day because it looks like an
    answer. A month of 13 is a producer's bug, and a malformed field is not an
    event and not an error either.
    """
    assert parse_pdf_date(raw) is None


def test_an_offset_is_applied_rather_than_ignored() -> None:
    """One o'clock in Berlin is midday UTC, and storing the first would be wrong by an hour."""
    berlin = parse_pdf_date("D:20260115130000+01'00'")
    utc = parse_pdf_date("D:20260115120000Z")
    assert berlin is not None
    assert utc is not None
    assert berlin[0] == utc[0]


# ---------------------------------------------------------------------------
# PDF, end to end.
# ---------------------------------------------------------------------------


def _write_pdf(path: Path, *, created: str | None = "D:20260115103000Z") -> Path:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if created is not None:
        writer.add_metadata({"/CreationDate": created, "/Title": "A synthetic page"})
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_a_pdf_yields_its_structure_and_a_tier_two_clock(tmp_path: Path) -> None:
    """The first source tier two has ever had.

    A PDF's creation date is the document's own metadata and it travels with the
    bytes, so it outranks any filesystem clock however precise: the ladder grades
    by copy-robustness rather than by accuracy.
    """
    extractor = EXTRACTORS.get("document")
    result = extractor.extract(_context(_write_pdf(tmp_path / "paper.pdf"), ".pdf"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["document.page_count"] == 1
    assert readings["document.title"] == "A synthetic page"
    assert result.event_time_source is TimeSource.DOCUMENT
    assert CLOCK_TIER_OF[result.event_time_source] is ClockTier.DOCUMENT
    assert result.event_time_ns is not None


def test_a_pdf_with_no_creation_date_offers_no_clock(tmp_path: Path) -> None:
    """Abstention is a valid output, and here it is the common one.

    Plenty of PDFs carry no creation date. Offering the filesystem's instead
    would assert that a copy's mtime is when the document was made, which is the
    confusion the two columns exist to prevent.
    """
    extractor = EXTRACTORS.get("document")
    result = extractor.extract(_context(_write_pdf(tmp_path / "bare.pdf", created=None), ".pdf"))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_ns is None
    assert result.event_time_source is None


def test_a_pdf_with_no_extractable_text_records_that_as_a_finding(tmp_path: Path) -> None:
    """A blank page is very often a scan, and that permanently narrows what it can be.

    Negative results are returned rather than discarded, per the extractor
    contract: an engine that throws this away asks the same question on every run.
    """
    extractor = EXTRACTORS.get("document")
    result = extractor.extract(_context(_write_pdf(tmp_path / "scan.pdf"), ".pdf"))
    assert _readings(result)["document.text_absent"] is True


def test_a_file_that_is_not_a_pdf_becomes_a_content_error_not_a_crash(tmp_path: Path) -> None:
    """One unreadable file must never fail a scan.

    An extractor may not raise to signal an ordinary outcome, and a file whose
    extension lies is exactly an ordinary outcome.
    """
    path = tmp_path / "liar.pdf"
    path.write_bytes(b"this is not a PDF at all, whatever the name says\n")
    result = EXTRACTORS.get("document").extract(_context(path, ".pdf"))

    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert not result.succeeded
    assert result.error_type


# ---------------------------------------------------------------------------
# XLSX, end to end.
# ---------------------------------------------------------------------------


def _write_workbook(path: Path, *, created: dt.datetime) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "plate_1"
    sheet.append(["sample_id", "concentration_nM", "incubation_min"])
    sheet.append(["a1", 500, 40])
    sheet.append(["a2", 250, 40])
    workbook.create_sheet("notes")
    workbook.properties.created = created
    workbook.save(path)
    return path


def test_a_workbook_yields_its_sheets_their_shape_and_a_tier_two_clock(tmp_path: Path) -> None:
    """Tabular data is where the dates are, which is why this came second.

    The header row is where a Dictionary can recognise words, and it is recorded
    at the heuristic tier because a first row is a header by convention and not
    by declaration.
    """
    moment = dt.datetime(2026, 3, 4, 9, 30, tzinfo=dt.UTC)
    path = _write_workbook(tmp_path / "plate.xlsx", created=moment)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["table.sheet_count"] == 2
    assert "plate_1" in str(readings["table.sheet_names"])
    assert readings["table.sheet.0.row_count"] == 3
    assert readings["table.sheet.0.column_count"] == 3
    assert "concentration_nM" in str(readings["table.sheet.0.column_names"])
    assert result.event_time_source is TimeSource.DOCUMENT
    assert result.event_time_ns == int(moment.timestamp() * 1_000_000_000)


def test_a_workbook_reports_a_missing_header_rather_than_nothing(tmp_path: Path) -> None:
    """An empty sheet has no header, and saying so is a finding."""
    workbook = openpyxl.Workbook()
    workbook.active.title = "empty"
    path = tmp_path / "blank.xlsx"
    workbook.save(path)

    readings = _readings(EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx")))
    assert readings["table.sheet.0.header_absent"] is True


def test_a_zip_that_is_not_a_workbook_becomes_a_content_error(tmp_path: Path) -> None:
    """The extension claimed OOXML and the bytes are an ordinary archive.

    Recorded rather than raised, and never reusable: a later run retries it.
    """
    path = tmp_path / "impostor.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "not a workbook\n")

    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))
    assert result.outcome is ScanOutcome.CONTENT_ERROR


# ---------------------------------------------------------------------------
# XLSX properties: the file's own, never a library's defaults.
# ---------------------------------------------------------------------------

_PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SPREADSHEET_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_CORE_NAMESPACES = (
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:dcterms="http://purl.org/dc/terms/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


def _core_part(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<cp:coreProperties{_CORE_NAMESPACES}>{body}</cp:coreProperties>"
    )


def _workbook_package(path: Path, *, core_part: str | None) -> Path:
    """Write the smallest workbook OOXML allows, holding the given core part or none at all.

    Built from the format's structure with invented values rather than saved by a
    library, because a library saving a workbook writes the properties it defaults to,
    and those defaults are exactly what these tests must be able to leave out.
    """
    content_types = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels"'
        ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>',
    ]
    relationships = [
        f'<Relationship Id="rId1" Type="{_OFFICE_RELATIONSHIPS}/officeDocument"'
        ' Target="xl/workbook.xml"/>'
    ]
    if core_part is not None:
        content_types.append(
            '<Override PartName="/docProps/core.xml"'
            ' ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        )
        relationships.append(
            f'<Relationship Id="rId2" Type="{_PACKAGE_RELATIONSHIPS}/metadata/core-properties"'
            ' Target="docProps/core.xml"/>'
        )
    content_types.append("</Types>")
    workbook = (
        f'<workbook xmlns="{_SPREADSHEET_MAIN}" xmlns:r="{_OFFICE_RELATIONSHIPS}">'
        '<sheets><sheet name="invented" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_relationships = (
        f'<Relationships xmlns="{_PACKAGE_RELATIONSHIPS}">'
        f'<Relationship Id="rId1" Type="{_OFFICE_RELATIONSHIPS}/worksheet"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sheet = (
        f'<worksheet xmlns="{_SPREADSHEET_MAIN}"><sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>invented</t></is></c></row></sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{_PACKAGE_RELATIONSHIPS}">'
            f"{''.join(relationships)}</Relationships>",
        )
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        if core_part is not None:
            archive.writestr("docProps/core.xml", core_part)
    return path


def test_a_workbook_without_a_core_part_offers_no_clock_and_invents_no_property(
    tmp_path: Path,
) -> None:
    """A library's default is not the file's value, and the scan's own time is not a clock.

    openpyxl fills a missing ``created`` and ``modified`` with the moment it loads the
    workbook, and a missing creator with its own name. Offering those as readings gave a
    workbook with no creation date a tier-two clock equal to when it was scanned.
    """
    path = _workbook_package(tmp_path / "bare.xlsx", core_part=None)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["table.sheet_count"] == 1
    assert result.event_time_ns is None
    assert result.event_time_source is None
    for key in ("table.created_ns", "table.modified_ns", "table.author", "table.title"):
        assert key not in readings


def test_a_core_part_stating_no_date_and_no_creator_records_neither(tmp_path: Path) -> None:
    """What the part states is recorded, and nothing it leaves out is supplied."""
    core_part = _core_part("<dc:title>An invented title</dc:title>")
    path = _workbook_package(tmp_path / "titled.xlsx", core_part=core_part)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["table.title"] == "An invented title"
    assert result.event_time_ns is None
    for key in ("table.created_ns", "table.modified_ns", "table.author"):
        assert key not in readings


def test_a_core_part_gives_exactly_the_dates_and_creator_it_states(tmp_path: Path) -> None:
    """The creation date is the clock; the modification date is recorded and not offered."""
    core_part = _core_part(
        "<dc:creator>An Invented Person</dc:creator>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">2001-02-03T04:05:06Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2002-03-04T05:06:07Z</dcterms:modified>'
    )
    path = _workbook_package(tmp_path / "dated.xlsx", core_part=core_part)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    created = int(dt.datetime(2001, 2, 3, 4, 5, 6, tzinfo=dt.UTC).timestamp()) * 1_000_000_000
    modified = int(dt.datetime(2002, 3, 4, 5, 6, 7, tzinfo=dt.UTC).timestamp()) * 1_000_000_000
    assert readings["table.author"] == "An Invented Person"
    assert readings["table.created_ns"] == created
    assert readings["table.modified_ns"] == modified
    assert result.event_time_ns == created
    assert result.event_time_source is TimeSource.DOCUMENT
    assert CLOCK_TIER_OF[result.event_time_source] is ClockTier.DOCUMENT


@pytest.mark.parametrize("written", ["2001", "2001-02"])
def test_a_workbook_date_short_of_a_day_is_recorded_and_not_a_clock(
    tmp_path: Path, written: str
) -> None:
    """A year, or a year and a month, is not a day, as for every other clock.

    W3CDTF allows both, and openpyxl 3.1.5 refused the whole workbook over either, so a
    sheet nobody's date could be parsed from was a sheet nobody read. The workbook is
    read, and the date is recorded as written.
    """
    core_part = _core_part(
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{written}</dcterms:created>'
    )
    path = _workbook_package(tmp_path / "coarse.xlsx", core_part=core_part)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["table.sheet_count"] == 1
    assert result.event_time_ns is None
    assert "table.created_ns" not in readings
    assert readings["table.created_unparsed"] == written


def test_a_workbook_dated_with_a_day_alone_is_read_and_dated_at_midnight(tmp_path: Path) -> None:
    """A day without a time is a real statement about a day, and midnight UTC is how it is kept.

    W3CDTF allows it, and openpyxl 3.1.5 refused the whole workbook over it.
    """
    core_part = _core_part(
        '<dcterms:created xsi:type="dcterms:W3CDTF">2001-02-03</dcterms:created>'
    )
    path = _workbook_package(tmp_path / "day.xlsx", core_part=core_part)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    midnight = int(dt.datetime(2001, 2, 3, tzinfo=dt.UTC).timestamp()) * 1_000_000_000
    assert readings["table.sheet_count"] == 1
    assert readings["table.created_ns"] == midnight
    assert readings["table.created_resolution"] == "day"
    assert result.event_time_ns == midnight


def test_a_workbook_core_part_declaring_a_document_type_is_refused_and_nothing_more_is_read(
    tmp_path: Path,
) -> None:
    """OPC forbids a document type in every part, so one is a finding rather than input."""
    refused = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE cp:coreProperties [<!ENTITY invented "invented">]>'
        f"<cp:coreProperties{_CORE_NAMESPACES}>"
        "<dc:title>&invented;</dc:title></cp:coreProperties>"
    )
    path = _workbook_package(tmp_path / "refused.xlsx", core_part=refused)
    result = EXTRACTORS.get("spreadsheet").extract(_context(path, ".xlsx"))

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["table.part_refused"] is True
    assert "table.sheet_count" not in readings
    assert "table.title" not in readings
    assert result.event_time_ns is None
    assert result.warnings


def test_xls_is_not_claimed_by_the_ooxml_extractor() -> None:
    """Legacy .xls is OLE compound storage, a different container entirely.

    Claiming it would make this extractor run on files it cannot read and report a content error.
    Comprehension failure and absent coverage route differently. The compound extractor reads the
    format, and the OOXML document extractor does not take it either.
    """
    extractor = EXTRACTORS.get("spreadsheet")
    context = ExtractionContext(
        entry_id="entry-2",
        path=Path("legacy.xls"),
        relative_path="legacy.xls",
        extensions=frozenset({".xls"}),
        size_bytes=0,
    )
    assert not extractor.supports(context)
    assert not EXTRACTORS.get("office").supports(context)
    assert EXTRACTORS.get("compound").supports(context)


# ---------------------------------------------------------------------------
# A time and its source travel together.
# ---------------------------------------------------------------------------


def test_a_time_without_a_source_is_refused() -> None:
    """A clock the engine cannot rank is worse than no clock.

    The pair is the whole point: a bare number cannot be compared against any
    other clock, and a source with no time says one was found and withholds it.
    """
    from ingence.extractors.base import ExtractorResult

    with pytest.raises(ValueError, match="travel together"):
        ExtractorResult(extractor="x", extractor_version="1", event_time_ns=1)
    with pytest.raises(ValueError, match="travel together"):
        ExtractorResult(extractor="x", extractor_version="1", event_time_source=TimeSource.DOCUMENT)


# ---------------------------------------------------------------------------
# Promoting a content clock onto the entry.
# ---------------------------------------------------------------------------


def _entry(source: TimeSource, value: int | None) -> object:
    """One entry carrying whatever clock the census left on it."""
    from ingence.core.schema import Entry, EntryType

    return Entry(
        entry_id="entry-1",
        root_id="root-1",
        relative_path="paper.pdf",
        name="paper.pdf",
        entry_type=EntryType.FILE,
        first_seen_run="run-1",
        last_seen_run="run-1",
        event_time_ns=value,
        event_time_source=source,
        event_time_tier=CLOCK_TIER_OF[source],
    )


def _offering(source: TimeSource | None, value: int | None) -> object:
    from ingence.extractors.base import ExtractorResult

    return ExtractorResult(
        extractor="synthetic",
        extractor_version="1",
        event_time_ns=value,
        event_time_source=source,
    )


def test_a_document_clock_displaces_a_filename_clock() -> None:
    """The ladder grades by copy-robustness, not by accuracy.

    A date parsed from a filename is tier three and a PDF's creation date is tier
    two, and both survive a copy. The document one is closer to the event, so it
    wins, and the filename reading stays in the observation table either way.
    """
    from ingence.scan.deepen import _selected_clock

    standing = _entry(TimeSource.FILENAME, 1_000)
    chosen = _selected_clock([_offering(TimeSource.DOCUMENT, 2_000)], standing=standing)
    assert chosen == (2_000, TimeSource.DOCUMENT)


def test_nothing_displaces_a_clock_at_a_better_tier() -> None:
    """More evidence never lowers a claim's standing.

    An instrument clock is tier one, and a document clock arriving later must not
    quietly replace it with something further from the event.
    """
    from ingence.scan.deepen import _selected_clock

    standing = _entry(TimeSource.INSTRUMENT, 1_000)
    assert _selected_clock([_offering(TimeSource.DOCUMENT, 2_000)], standing=standing) is None


def test_two_extractors_disagreeing_at_one_tier_produce_nothing() -> None:
    """Abstention rather than arbitration, because nothing distinguishes them.

    Picking the first would make the answer depend on registry order, so the conclusion could not be
    made again from the index. Conflicting is a state of its own rather than a value. Both readings
    stay recorded, so the conflict stays visible.
    """
    from ingence.scan.deepen import _selected_clock

    standing = _entry(TimeSource.FILENAME, 1_000)
    offered = [_offering(TimeSource.DOCUMENT, 2_000), _offering(TimeSource.DOCUMENT, 3_000)]
    assert _selected_clock(offered, standing=standing) is None


def test_two_extractors_agreeing_at_one_tier_are_not_a_conflict() -> None:
    """Two readings of one value is one fact seen twice, which settles rather than conflicts."""
    from ingence.scan.deepen import _selected_clock

    standing = _entry(TimeSource.FILENAME, 1_000)
    offered = [_offering(TimeSource.DOCUMENT, 2_000), _offering(TimeSource.DOCUMENT, 2_000)]
    assert _selected_clock(offered, standing=standing) == (2_000, TimeSource.DOCUMENT)


def test_an_entry_with_no_clock_takes_whatever_content_offers() -> None:
    """The unavailable state is not a rung and must not sort as the worst one.

    Ranking it as five would make a document clock lose to nothing at all, which
    is the bug a declared absence state exists to prevent.
    """
    from ingence.scan.deepen import _selected_clock

    standing = _entry(TimeSource.UNAVAILABLE, None)
    chosen = _selected_clock([_offering(TimeSource.DOCUMENT, 2_000)], standing=standing)
    assert chosen == (2_000, TimeSource.DOCUMENT)


def test_an_extractor_offering_nothing_leaves_the_entry_alone() -> None:
    """Most files carry no content clock, and that must change nothing."""
    from ingence.scan.deepen import _selected_clock

    standing = _entry(TimeSource.FILENAME, 1_000)
    assert _selected_clock([_offering(None, None)], standing=standing) is None
