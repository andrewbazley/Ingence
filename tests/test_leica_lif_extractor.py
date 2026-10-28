"""Leica LIF, tested on headers built from the format's structure.

``fixtures.instrument_files`` writes the memory block and the project's XML header in
UTF-16, with each image's acquisition times as hexadecimal Windows FILETIMEs and every
value invented. The structure was read off a real file, where every stamp preceded that file's
modification time by minutes.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fixtures.instrument_files import filetime_ticks, lif_file, lif_header
from ingence.core.outcomes import ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext, ExtractorResult
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.extractors.limits import Limits

load_builtins()

ACQUIRED = dt.datetime(2026, 3, 4, 9, 30, 0, 123456, tzinfo=dt.UTC)


def _stamp(seconds_later: float) -> int:
    return filetime_ticks(ACQUIRED + dt.timedelta(seconds=seconds_later))


def _write(tmp_path: Path, data: bytes, name: str = "project.lif") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _context(path: Path, *, limits: Limits | None = None) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({".lif"}),
        size_bytes=path.stat().st_size,
        limits=limits,
    )


def _extract(path: Path, **options: object) -> ExtractorResult:
    return EXTRACTORS.get("leica_lif").extract(_context(path, **options))  # type: ignore[arg-type]


def _readings(result: ExtractorResult) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}


def test_a_lif_offers_its_earliest_acquisition_time_as_a_tier_one_clock(tmp_path):
    """One project file holds many acquisitions, and the earliest is when the project began.

    The earliest sits in the second image's list here, because nothing says a header lists
    its images in the order they were acquired.
    """
    stamps = [[_stamp(60), _stamp(65)], [_stamp(0)], [_stamp(30)]]
    data = lif_file(timestamps=stamps, images_without_times=2)
    result = _extract(_write(tmp_path, data))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_source is TimeSource.INSTRUMENT
    assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
    readings = _readings(result)
    assert readings["image.image_count"] == 5
    assert readings["image.images_with_timestamps"] == 3
    assert readings["image.timestamp_count"] == 4
    latest = ACQUIRED + dt.timedelta(seconds=65)
    assert readings["image.acquired_last_ns"] == nanoseconds_since_epoch(latest)
    assert readings["image.format_version"] == "2"


def test_a_lif_with_no_acquisition_times_offers_no_clock_and_says_so(tmp_path):
    result = _extract(_write(tmp_path, lif_file(timestamps=[], images_without_times=3)))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_ns is None
    readings = _readings(result)
    assert readings["image.acquisition_time_absent"] is True
    assert readings["image.image_count"] == 3


def test_malformed_and_unset_stamps_are_counted_and_never_become_a_clock(tmp_path):
    data = lif_file(timestamps=[[_stamp(0)]], stamp_text="zz 0 1dcab9c22b7f000g")
    result = _extract(_write(tmp_path, data))

    assert result.event_time_ns is None
    assert _readings(result)["image.timestamps_unparsed"] == 3


def test_a_header_past_the_byte_ceiling_is_bounded_and_offers_no_clock(tmp_path):
    """The earliest time might sit in the part not read, so none is claimed."""
    data = lif_file(timestamps=[[_stamp(0)]], images_without_times=50)
    result = _extract(_write(tmp_path, data), limits=Limits(max_bytes_read=1024))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert _readings(result)["image.metadata_bounded"] is True
    assert result.event_time_ns is None
    assert result.bytes_read <= 1024


def test_a_header_claiming_more_than_the_file_holds_fails_the_file(tmp_path):
    data = lif_header("<LMSDataContainerHeader/>", claimed_characters=1 << 30)
    result = _extract(_write(tmp_path, data))

    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert result.error_type == "StructureBoundsError"


def test_a_file_named_lif_holding_something_else_is_declined(tmp_path):
    path = _write(tmp_path, b"plainly not a LIF file " * 8)
    assert _extract(path).outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_the_memory_blocks_after_the_header_are_never_read(tmp_path):
    header = lif_file(timestamps=[[_stamp(0)]])
    result = _extract(_write(tmp_path, header + bytes(100_000)))
    assert result.bytes_read == len(header)


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path):
    from ingence.executors.supervisor import Supervisor

    path = _write(tmp_path, lif_file(timestamps=[[_stamp(0)]]))
    supervised = Supervisor(timeout_seconds=60.0).run("leica_lif", _context(path))
    assert not supervised.contained
    assert supervised.result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)


def test_a_project_level_timestamp_is_neither_the_clock_nor_the_latest(tmp_path):
    """The attribute-pair shape seen on a real file sat on the Experiment, not on an image.

    What it dates is not established, so an earlier one must not become the clock and a later
    one must not become the latest acquisition.
    """
    image = (
        '<Element Name="image"><Data><Image><ImageDescription/>'
        f'<TimeStampList NumberOfTimeStamps="1">{_stamp(0):x}</TimeStampList>'
        "</Image></Data></Element>"
    )
    for project_stamp in (_stamp(-3600), _stamp(3600)):
        high, low = project_stamp >> 32, project_stamp & 0xFFFFFFFF
        stamp = f'<TimeStamp HighInteger="{high}" LowInteger="{low}"/>'
        xml = (
            '<LMSDataContainerHeader Version="2"><Element Name="project"><Data><Experiment>'
            f"{stamp}</Experiment></Data><Children>{image}</Children></Element>"
            "</LMSDataContainerHeader>"
        )
        result = _extract(_write(tmp_path, lif_header(xml)))
        readings = _readings(result)
        assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
        assert readings["image.acquired_last_ns"] == nanoseconds_since_epoch(ACQUIRED)
        assert readings["image.timestamp_count"] == 1
