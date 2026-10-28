"""Lambert Instruments LI-FLIM, tested on files built from the format's structure.

``fixtures.instrument_files`` writes the text header, with its blocks, sections and
``{END}`` marker, then zeroed pixels, with every value invented. The structure was read
off a real file, where its FILETIME fell seconds after the local time in that file's name and
seconds before its modification time.
"""

from __future__ import annotations

import datetime as dt
import struct
from pathlib import Path

from fixtures.instrument_files import filetime_ticks, li_flim_file
from ingence.core.outcomes import ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext, ExtractorResult
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.extractors.limits import Limits

load_builtins()

ACQUIRED = dt.datetime(2026, 3, 4, 9, 30, 0, 500000, tzinfo=dt.UTC)


def _write(tmp_path: Path, data: bytes, name: str = "lifetime.fli") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _context(path: Path, *, limits: Limits | None = None) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({".fli"}),
        size_bytes=path.stat().st_size,
        limits=limits,
    )


def _extract(path: Path, **options: object) -> ExtractorResult:
    return EXTRACTORS.get("lambert_fli").extract(_context(path, **options))  # type: ignore[arg-type]


def _readings(result: ExtractorResult) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}


def test_an_li_flim_file_offers_its_earliest_time_point_as_a_tier_one_clock(tmp_path):
    later = ACQUIRED + dt.timedelta(seconds=2)
    data = li_flim_file(timestamps=[filetime_ticks(later), filetime_ticks(ACQUIRED)], phases=3)
    result = _extract(_write(tmp_path, data))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_source is TimeSource.INSTRUMENT
    assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
    readings = _readings(result)
    assert readings["image.timestamp_count"] == 2
    assert readings["image.acquired_last_ns"] == nanoseconds_since_epoch(later)
    assert readings["image.width"] == 4
    assert readings["image.height"] == 3
    assert readings["image.phases"] == 3
    assert readings["image.sample_type"] == "UINT16"
    assert readings["image.dark_image"] is True
    assert readings["image.layout_accounts_for_file"] is True
    assert readings["image.format_version"] == "1.0"


def test_a_file_that_does_not_match_its_own_layout_says_so(tmp_path):
    """Truncated, or not what it says. The clock in its header still stands."""
    data = li_flim_file(timestamps=[filetime_ticks(ACQUIRED)])[:-10]
    result = _extract(_write(tmp_path, data))

    assert _readings(result)["image.layout_accounts_for_file"] is False
    assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)


def test_an_autodesk_animation_named_fli_is_declined(tmp_path):
    """FLIC shares the extension, which is why a name alone is never enough here."""
    animation = struct.pack("<IH", 1024, 0xAF12) + bytes(200)
    assert _extract(_write(tmp_path, animation)).outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_the_comments_block_is_never_recorded(tmp_path):
    result = _extract(_write(tmp_path, li_flim_file(timestamps=[filetime_ticks(ACQUIRED)])))
    readings = _readings(result)
    assert not any("somebody typed" in str(value) for value in readings.values())
    assert not any("comment" in key for key in readings)


def test_malformed_and_unset_time_points_are_counted_and_offer_no_clock(tmp_path):
    """A high half past 32 bits and a zero FILETIME are not times, and neither is prose."""
    values = ["not a time", "4294967296 1", "0 0"]
    data = li_flim_file(timestamps=[filetime_ticks(ACQUIRED)], stamp_values=values)
    result = _extract(_write(tmp_path, data))

    readings = _readings(result)
    assert result.event_time_ns is None
    assert readings["image.timestamps_unparsed"] == 3
    assert readings["image.acquisition_time_absent"] is True


def test_a_header_that_does_not_end_within_the_ceiling_is_bounded(tmp_path):
    data = b"{FLIMIMAGE}\n" + b"x = 1\n" * 2000
    result = _extract(_write(tmp_path, data), limits=Limits(max_bytes_read=4096))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert _readings(result)["image.header_bounded"] is True
    assert result.event_time_ns is None


def test_a_header_that_never_ends_at_all_fails_the_file(tmp_path):
    data = b"{FLIMIMAGE}\n[LAYOUT]\nx = 1\n"
    assert _extract(_write(tmp_path, data)).outcome is ScanOutcome.CONTENT_ERROR


def test_no_more_than_one_block_past_the_header_is_read(tmp_path):
    data = li_flim_file(timestamps=[filetime_ticks(ACQUIRED)], width=64, height=64, phases=8)
    header = data.index(b"{END}") + len(b"{END}")
    result = _extract(_write(tmp_path, data))
    assert result.bytes_read <= header + 4096 < len(data)


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path):
    from ingence.executors.supervisor import Supervisor

    path = _write(tmp_path, li_flim_file(timestamps=[filetime_ticks(ACQUIRED)]))
    supervised = Supervisor(timeout_seconds=60.0).run("lambert_fli", _context(path))
    assert not supervised.contained
    assert supervised.result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
