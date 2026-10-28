"""TIFF, tested on files built from the format's structure: readings, and never a clock.

``fixtures.instrument_files`` writes classic little-endian TIFFs with the tags an ImageJ or
OME writer adds, and every value is invented. The first test checks the fixture against
tifffile, the reference reader, before any other test trusts it.
"""

from __future__ import annotations

import datetime as dt
import struct
from pathlib import Path

import pytest

from fixtures.instrument_files import julian_day, tiff_file
from ingence.core.outcomes import ScanOutcome
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext, ExtractorResult
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.extractors.limits import Limits

load_builtins()

#: When the Nikon source a crop was cut from was acquired, as ImageJ's ``Info`` copies it.
SOURCE_ACQUIRED = dt.datetime(2026, 3, 4, 18, 0, tzinfo=dt.UTC)
IMAGEJ_INFO = f"dTimeAbsolute = {julian_day(SOURCE_ACQUIRED)}\nsLensName = invented\n"


def _ome(acquired: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        f'<Image ID="Image:0"><AcquisitionDate>{acquired}</AcquisitionDate></Image></OME>'
    )


def _write(tmp_path: Path, data: bytes, name: str = "crop.tif") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _context(
    path: Path,
    *,
    extension: str = ".tif",
    media_type: str | None = None,
    limits: Limits | None = None,
) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({extension}),
        size_bytes=path.stat().st_size,
        media_type=media_type,
        limits=limits,
    )


def _extract(path: Path, **options: object) -> ExtractorResult:
    return EXTRACTORS.get("tiff").extract(_context(path, **options))  # type: ignore[arg-type]


def _readings(result: ExtractorResult) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}


def test_the_fixture_is_a_tiff_the_reference_reader_accepts(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    path = _write(
        tmp_path, tiff_file(pages=3, date_time="2026:03:04 06:00:00", imagej_info=IMAGEJ_INFO)
    )
    with tifffile.TiffFile(path) as tif:
        assert len(tif.pages) == 3
        assert tif.is_imagej
        assert tif.imagej_metadata["Info"] == IMAGEJ_INFO
        assert tif.pages[0].tags["DateTime"].value == "2026:03:04 06:00:00"


def test_a_tiff_records_its_times_as_readings_and_offers_no_clock(tmp_path):
    """A write time, and a time about the source a crop was cut from: neither is its event."""
    data = tiff_file(pages=3, date_time="2026:03:04 06:00:00", imagej_info=IMAGEJ_INFO)
    result = _extract(_write(tmp_path, data))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_ns is None
    assert result.event_time_source is None
    readings = _readings(result)
    assert readings["image.tiff_datetime"] == "2026:03:04 06:00:00"
    assert readings["image.tiff_datetime_zone"] == "unstated"
    assert readings["image.source_acquired_ns"] == nanoseconds_since_epoch(SOURCE_ACQUIRED)
    assert readings["image.source_acquired_field"] == "ImageJ Info, dTimeAbsolute"
    assert readings["image.description_kind"] == "imagej"
    assert readings["image.page_count"] == 3
    assert readings["image.width"] == 2
    assert readings["image.height"] == 2
    assert readings["image.tiff_variant"] == "classic"


@pytest.mark.parametrize(
    ("acquired", "zone"),
    [("2026-03-04T09:30:00", "unstated"), ("2026-03-04T09:30:00Z", "stated")],
)
def test_an_ome_acquisition_date_is_a_reading_with_its_zone_class(tmp_path, acquired, zone):
    result = _extract(_write(tmp_path, tiff_file(description=_ome(acquired)), "plane.ome.tif"))

    readings = _readings(result)
    assert readings["image.description_kind"] == "ome-xml"
    assert readings["image.ome_acquisition_date"] == acquired
    assert readings["image.ome_acquisition_date_zone"] == zone
    assert result.event_time_ns is None


def test_a_page_chain_pointing_back_on_itself_ends(tmp_path):
    result = _extract(_write(tmp_path, tiff_file(pages=2, looping=True)))

    readings = _readings(result)
    assert readings["image.page_count"] == 2
    assert readings["image.page_chain_stopped"] == "a page pointing back into the chain"


def test_a_page_chain_past_the_byte_ceiling_is_counted_as_far_as_it_was_read(tmp_path):
    result = _extract(_write(tmp_path, tiff_file(pages=200)), limits=Limits(max_bytes_read=600))

    readings = _readings(result)
    assert result.outcome is ScanOutcome.EXTRACTED
    assert readings["image.page_count_bounded"] is True
    assert 1 < readings["image.page_count"] < 200
    assert result.bytes_read <= 600


def test_a_page_pointer_outside_the_file_stops_the_chain_rather_than_the_scan(tmp_path):
    whole = tiff_file(pages=3)
    (first,) = struct.unpack_from("<I", whole, 4)
    truncated = whole[: first + 2 + 9 * 12 + 4]
    result = _extract(_write(tmp_path, truncated))

    readings = _readings(result)
    assert result.outcome is ScanOutcome.EXTRACTED
    assert readings["image.page_count"] == 1
    assert readings["image.page_chain_stopped"] == "a pointer outside the file"


def test_a_directory_claiming_thousands_of_entries_fails_the_file(tmp_path):
    data = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 65535) + bytes(64)
    result = _extract(_write(tmp_path, data))
    assert result.outcome is ScanOutcome.CONTENT_ERROR


def test_a_big_endian_bigtiff_reads_the_same_way(tmp_path):
    """Written by hand here, because the fixture writes the classic form."""
    header = b"MM\x00\x2b" + struct.pack(">HH", 8, 0) + struct.pack(">Q", 16)
    width = struct.pack(">HHQ", 256, 3, 1) + struct.pack(">H", 640) + bytes(6)
    height = struct.pack(">HHQ", 257, 3, 1) + struct.pack(">H", 480) + bytes(6)
    directory = struct.pack(">Q", 2) + width + height + struct.pack(">Q", 0)
    result = _extract(_write(tmp_path, header + directory))

    readings = _readings(result)
    assert readings["image.tiff_variant"] == "bigtiff"
    assert readings["image.width"] == 640
    assert readings["image.height"] == 480
    assert readings["image.page_count"] == 1


def test_a_file_named_tif_holding_something_else_is_declined(tmp_path):
    path = _write(tmp_path, b"plainly not a TIFF file " * 8)
    assert _extract(path).outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_no_pixel_is_read(tmp_path):
    data = tiff_file(width=256, height=256)
    result = _extract(_write(tmp_path, data))
    assert result.bytes_read < 256 * 256


def test_every_tiff_based_file_is_supported_once_detection_named_its_type(tmp_path):
    path = _write(tmp_path, tiff_file(), "stack.lsm")
    extractor = EXTRACTORS.get("tiff")
    assert extractor.supports(_context(path, extension=".lsm", media_type="image/tiff"))
    assert not extractor.supports(_context(path, extension=".lsm"))


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path):
    from ingence.executors.supervisor import Supervisor

    path = _write(tmp_path, tiff_file(pages=2, imagej_info=IMAGEJ_INFO))
    supervised = Supervisor(timeout_seconds=60.0).run("tiff", _context(path))
    assert not supervised.contained
    readings = {reading.key: reading.value for reading in supervised.result.readings}
    assert readings["image.source_acquired_ns"] == nanoseconds_since_epoch(SOURCE_ACQUIRED)
