"""OLE compound storage, which ``.xls`` files use, tested on files built from the specification.

The container is written by ``fixtures.compound_file`` from [MS-CFB] and [MS-OLEPS]
rather than taken from anybody's disk, so the first test checks the fixture against
the reference reader before any test trusts it.
"""

from __future__ import annotations

import datetime as dt
import json
import struct
from pathlib import Path

import pytest

from fixtures.compound_file import (
    SIGNATURE,
    compound_file,
    filetime_of,
    summary_information,
    workbook_stream,
)
from ingence.core.outcomes import CLOCK_TIER_OF, ClockTier, ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.extractors.compound import workbook_globals
from ingence.extractors.limits import Limits

olefile = pytest.importorskip("olefile")

load_builtins()

CREATED = dt.datetime(2026, 3, 4, 9, 30, 0, 250000, tzinfo=dt.UTC)
SAVED = dt.datetime(2026, 3, 5, 17, 0, tzinfo=dt.UTC)
OLE_MEDIA_TYPE = "application/x-ole-storage"


def _write(path: Path, tree: dict[str, object]) -> Path:
    path.write_bytes(compound_file(tree))
    return path


def _context(
    path: Path, extension: str, *, media_type: str | None = None, limits: Limits | None = None
) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({extension}) if extension else frozenset(),
        size_bytes=path.stat().st_size,
        media_type=media_type,
        limits=limits,
    )


def _extract(path: Path, extension: str, **options: object):
    return EXTRACTORS.get("compound").extract(_context(path, extension, **options))  # type: ignore[arg-type]


def _readings(result: object) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}  # type: ignore[attr-defined]


def test_the_fixture_writes_a_compound_file_the_reference_reader_accepts(tmp_path: Path) -> None:
    """A fixture written from a specification is checked before anything trusts it."""
    large = bytes(range(256)) * 20
    path = _write(
        tmp_path / "fixture.bin",
        {"Small": b"tiny stream", "Large": large, "Storage": {"Inner": b"nested"}},
    )
    assert path.read_bytes().startswith(SIGNATURE)
    with olefile.OleFileIO(str(path)) as storage:
        assert sorted(storage.listdir()) == [["Large"], ["Small"], ["Storage", "Inner"]]
        assert storage.openstream("Small").read() == b"tiny stream"
        assert storage.openstream("Large").read() == large
        assert storage.openstream("Storage/Inner").read() == b"nested"


# ---------------------------------------------------------------------------
# What each application stored decides the keys.
# ---------------------------------------------------------------------------


def test_a_legacy_workbook_yields_its_sheets_properties_and_a_tier_two_clock(
    tmp_path: Path,
) -> None:
    """The keys ``spreadsheet.py`` writes, so a table is a table whichever decade saved it."""
    path = _write(
        tmp_path / "plate.xls",
        {
            "Workbook": workbook_stream(["plate_1", "notes"], pad_to=5000),
            "\x05SummaryInformation": summary_information(
                title="Plate layout",
                author="A. Author",
                application="SyntheticSheet",
                created=filetime_of(CREATED),
                saved=filetime_of(SAVED),
            ),
        },
    )
    result = _extract(path, ".xls")

    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["compound.content_kind"] == "table"
    assert readings["table.sheet_count"] == 2
    assert json.loads(str(readings["table.sheet_names"])) == ["plate_1", "notes"]
    assert readings["table.encrypted"] is False
    assert readings["table.title"] == "Plate layout"
    assert readings["table.author"] == "A. Author"
    assert readings["table.creator"] == "SyntheticSheet"
    assert readings["table.modified_ns"] == nanoseconds_since_epoch(SAVED)
    assert result.event_time_source is TimeSource.DOCUMENT
    assert CLOCK_TIER_OF[result.event_time_source] is ClockTier.DOCUMENT
    assert result.event_time_ns == readings["table.created_ns"] == nanoseconds_since_epoch(CREATED)


def test_a_word_document_keeps_the_keys_every_document_uses(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "protocol.doc",
        {
            "WordDocument": struct.pack("<H", 0xA5EC) + b"\x00" * 900,
            "\x05SummaryInformation": summary_information(
                title="Protocol", created=filetime_of(CREATED)
            ),
        },
    )
    readings = _readings(_extract(path, ".doc"))
    assert readings["compound.content_kind"] == "document"
    assert readings["document.title"] == "Protocol"
    assert readings["document.created_ns"] == nanoseconds_since_epoch(CREATED)
    assert not any(key.startswith("table.") for key in readings)


def test_a_container_naming_no_application_keeps_compound_keys(tmp_path: Path) -> None:
    """Read by content under any name, and saying no more than the container says.

    The formats built on this container are not named here, and none needs to be:
    detection recognises the signature and the structure belongs to the container.
    """
    path = _write(
        tmp_path / "acquisition.bin",
        {
            "Image": {"Contents": b"\x00" * 128, "Frame 1": b"\x01" * 5000},
            "\x05SummaryInformation": summary_information(
                title="Run 3", created=filetime_of(CREATED)
            ),
        },
    )
    extractor = EXTRACTORS.get("compound")
    context = _context(path, ".bin", media_type=OLE_MEDIA_TYPE)
    assert extractor.supports(context)
    result = extractor.extract(context)

    readings = _readings(result)
    assert readings["compound.content_kind"] == "unknown"
    assert readings["compound.storage_count"] == 1
    assert readings["compound.stream_count"] == 3
    # Sorted by the names themselves, so the summary stream's leading \x05 comes first.
    assert json.loads(str(readings["compound.top_level_names"])) == [
        "\\x05SummaryInformation",
        "Image",
    ]
    assert readings["compound.title"] == "Run 3"
    assert result.event_time_ns == nanoseconds_since_epoch(CREATED)
    assert not any(key.startswith(("document.", "table.")) for key in readings)


def test_an_encrypted_ooxml_package_is_recorded_here(tmp_path: Path) -> None:
    """The OOXML extractor declines these bytes, and this is where the encryption is said."""
    path = _write(
        tmp_path / "locked.docx",
        {"EncryptionInfo": b"\x04\x00\x04\x00" + b"\x00" * 200, "EncryptedPackage": b"\x9f" * 6000},
    )
    readings = _readings(_extract(path, ".docx", media_type=OLE_MEDIA_TYPE))
    assert readings["compound.content_kind"] == "encrypted_package"
    assert readings["compound.encrypted_package"] is True


# ---------------------------------------------------------------------------
# Sheets, encryption and code pages.
# ---------------------------------------------------------------------------


def test_an_encrypted_workbook_says_so_rather_than_failing(tmp_path: Path) -> None:
    """Sheet names are encrypted with everything else, and that is a fact about the file."""
    path = _write(
        tmp_path / "locked.xls", {"Workbook": workbook_stream(["secret"], encrypted=True)}
    )
    result = _extract(path, ".xls")
    assert result.outcome is ScanOutcome.EXTRACTED
    readings = _readings(result)
    assert readings["table.encrypted"] is True
    assert "table.sheet_names" not in readings


def test_sheet_names_decode_in_both_character_widths_and_the_older_format() -> None:
    names = ["plate_é", "板_1"]
    parsed = workbook_globals(workbook_stream(names))
    assert parsed == (tuple(names), False)
    assert workbook_globals(workbook_stream(["plaque_é"], biff8=False)) == (("plaque_é",), False)


def test_a_record_claiming_more_than_the_stream_holds_ends_the_walk() -> None:
    stream = workbook_stream(["first"])[:-4] + struct.pack("<HH", 0x0085, 60000) + b"\x00" * 10
    parsed = workbook_globals(stream)
    assert parsed is not None
    assert parsed[0] == ("first",)


def test_a_stream_that_is_not_a_workbook_is_unreadable_rather_than_sheetless() -> None:
    assert workbook_globals(b"\x00" * 64) is None
    assert workbook_globals(b"") is None


def test_a_workbook_past_the_byte_ceiling_is_not_opened_and_says_so(tmp_path: Path) -> None:
    path = _write(tmp_path / "large.xls", {"Workbook": workbook_stream(["a"], pad_to=9000)})
    readings = _readings(_extract(path, ".xls", limits=Limits(max_bytes_read=4096)))
    assert readings["table.sheet_names_bounded"] is True
    assert "table.sheet_names" not in readings


def test_a_utf8_code_page_arrives_signed_and_is_still_decoded(tmp_path: Path) -> None:
    """65001 is -535 as the signed 16-bit integer the property set stores."""
    path = _write(
        tmp_path / "utf8.doc",
        {
            "WordDocument": b"\x00" * 600,
            "\x05SummaryInformation": summary_information(codepage=65001, title="Plaque é"),
        },
    )
    assert _readings(_extract(path, ".doc"))["document.title"] == "Plaque é"


def test_a_zero_creation_time_is_an_unset_field_and_offers_no_clock(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "unset.doc",
        {"WordDocument": b"\x00" * 600, "\x05SummaryInformation": summary_information(created=0)},
    )
    result = _extract(path, ".doc")
    assert result.event_time_ns is None
    assert "document.created_ns" not in _readings(result)


# ---------------------------------------------------------------------------
# What is claimed, and what is not this container.
# ---------------------------------------------------------------------------


def test_the_legacy_names_are_claimed_and_the_ooxml_ones_are_not() -> None:
    extractor = EXTRACTORS.get("compound")
    for extension, claimed in ((".xls", True), (".doc", True), (".ppt", True), (".xlsx", False)):
        context = ExtractionContext(
            entry_id="entry-2",
            path=Path(f"file{extension}"),
            relative_path=f"file{extension}",
            extensions=frozenset({extension}),
            size_bytes=0,
        )
        assert extractor.supports(context) is claimed


def test_two_vendor_formats_built_on_the_container_are_claimed_and_read_as_it(
    tmp_path: Path,
) -> None:
    """``.oib`` and ``.zvi`` are compound storage, claimed by name.

    What either yields is what the container holds, and no vendor stream gives a clock.
    """
    extractor = EXTRACTORS.get("compound")
    declaration = EXTRACTORS.declaration("compound")
    assert {".oib", ".zvi"} <= set(declaration.attributes["extensions"].split(","))
    assert declaration.attributes["media_types"] == OLE_MEDIA_TYPE
    for extension in (".oib", ".zvi"):
        path = _write(tmp_path / f"stack{extension}", {"Stream": b"\x00" * 600})
        assert extractor.supports(_context(path, extension))
        result = _extract(path, extension)
        assert result.outcome is ScanOutcome.EXTRACTED
        assert result.event_time_ns is None


def test_a_legacy_name_holding_something_else_is_declined(tmp_path: Path) -> None:
    """Rich text saved as ``.doc`` is common, intact, and not this container."""
    path = tmp_path / "letter.doc"
    path.write_bytes(b"{\\rtf1\\ansi plain rich text}")
    result = _extract(path, ".doc")
    assert result.outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_a_truncated_container_fails_that_file_and_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "truncated.xls"
    path.write_bytes(SIGNATURE + b"\x00" * 600)
    result = _extract(path, ".xls")
    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert result.error_type


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path: Path) -> None:
    from ingence.executors.supervisor import Supervisor

    path = _write(
        tmp_path / "boundary.xls",
        {
            "Workbook": workbook_stream(["a"]),
            "\x05SummaryInformation": summary_information(created=filetime_of(CREATED)),
        },
    )
    supervised = Supervisor(timeout_seconds=60.0).run("compound", _context(path, ".xls"))
    assert not supervised.contained
    assert supervised.result.outcome is ScanOutcome.EXTRACTED
    assert supervised.result.event_time_ns == nanoseconds_since_epoch(CREATED)
