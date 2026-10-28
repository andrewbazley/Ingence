"""Nikon ND2 version 3, tested on files built from the format's structure.

``fixtures.instrument_files`` writes the chunks, the chunk map and Nikon's binary metadata, with
invented values. The structure was read off a real file. On that file, this reader's clock agreed
with the file's own text date and modification time. What these tests pin is that reading, and
the ways a hostile or truncated file must fail without taking the scan with it.
"""

from __future__ import annotations

import datetime as dt
import struct
from pathlib import Path

import pytest

from fixtures.instrument_files import julian_day, lv_double, lv_item, lv_level, lv_string, nd2_file
from ingence.core.outcomes import CLOCK_TIER_OF, ClockTier, ScanOutcome, TimeSource
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS, ExtractionContext, ExtractorResult
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.extractors.limits import Limits
from ingence.extractors.nikon_nd2 import lite_variant

load_builtins()

#: A quarter day, which a Julian day's double holds exactly.
ACQUIRED = dt.datetime(2026, 3, 4, 6, 0, tzinfo=dt.UTC)
ND2_MEDIA_TYPE = "application/x-nikon-nd2"


def _write(tmp_path: Path, data: bytes, name: str = "stack.nd2") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _context(
    path: Path,
    *,
    extension: str = ".nd2",
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
    return EXTRACTORS.get("nikon_nd2").extract(_context(path, **options))  # type: ignore[arg-type]


def _readings(result: ExtractorResult) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}


# ---------------------------------------------------------------------------
# The clock, and what is never mistaken for it.
# ---------------------------------------------------------------------------


def test_an_nd2_offers_its_first_frames_julian_day_as_a_tier_one_clock(tmp_path):
    """The engine's first instrument clock, read from the first frame's Julian day."""
    relative = (1200.0, 1210.0, 1225.0, 1240.0)
    data = nd2_file(julian_day=julian_day(ACQUIRED), frames=4, relative_ms=relative)
    result = _extract(_write(tmp_path, data))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_source is TimeSource.INSTRUMENT
    assert CLOCK_TIER_OF[result.event_time_source] is ClockTier.INSTRUMENT
    assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
    readings = _readings(result)
    assert readings["image.acquired_ns"] == result.event_time_ns
    assert readings["image.frame_count"] == 4
    assert readings["image.format_version"] == "3.0"
    assert readings["image.acquisition_span_ms"] == 40.0
    assert result.bytes_read < len(data)


def test_the_relative_times_are_never_added_to_the_clock(tmp_path):
    """Relative times count from an earlier origin, so adding them to the clock would be wrong."""
    relative = (1_155_000.0, 1_172_000.0)
    data = nd2_file(julian_day=julian_day(ACQUIRED), frames=2, relative_ms=relative)
    result = _extract(_write(tmp_path, data))

    assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
    assert _readings(result)["image.acquisition_span_ms"] == 17_000.0


def test_the_text_date_is_a_reading_with_no_zone_and_never_the_clock(tmp_path):
    """Local wall time in a locale's order: the fourth of March or the third of April."""
    data = nd2_file(julian_day=None, text_date="3/4/2026  1:00:00 AM")
    result = _extract(_write(tmp_path, data))

    readings = _readings(result)
    assert readings["image.text_date"] == "3/4/2026  1:00:00 AM"
    assert readings["image.text_date_zone"] == "unstated"
    assert result.event_time_ns is None
    assert readings["image.acquisition_time_absent"] is True


def test_nothing_but_the_date_leaves_the_text_information(tmp_path):
    """The same chunk names the author and describes the sample, and neither is recorded."""
    data = nd2_file(julian_day=julian_day(ACQUIRED), text_date="3/4/2026  1:00:00 AM")
    values = _readings(_extract(_write(tmp_path, data))).values()
    assert not any("Person" in str(value) for value in values)


@pytest.mark.parametrize(
    "day",
    [0.0, float("nan"), float("inf"), julian_day(dt.datetime(1970, 1, 1, tzinfo=dt.UTC))],
)
def test_an_unset_or_implausible_julian_day_offers_no_clock(tmp_path, day):
    """An instrument whose clock reads 1970 had a flat battery, and tier one outranks everything."""
    result = _extract(_write(tmp_path, nd2_file(julian_day=day)))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_ns is None
    assert _readings(result)["image.acquisition_time_absent"] is True


def test_an_instrument_clock_displaces_every_lower_tier(tmp_path):
    """Tier one outranks a document's own date and every filesystem clock."""
    from ingence.core.schema import Entry, EntryType
    from ingence.scan.deepen import _selected_clock

    result = _extract(_write(tmp_path, nd2_file(julian_day=julian_day(ACQUIRED))))
    for source in (TimeSource.DOCUMENT, TimeSource.FILENAME, TimeSource.MTIME):
        standing = Entry(
            entry_id="entry-1",
            root_id="root-1",
            relative_path="stack.nd2",
            name="stack.nd2",
            entry_type=EntryType.FILE,
            first_seen_run="run-1",
            last_seen_run="run-1",
            event_time_ns=1_000,
            event_time_source=source,
            event_time_tier=CLOCK_TIER_OF[source],
        )
        chosen = _selected_clock([result], standing=standing)
        assert chosen == (nanoseconds_since_epoch(ACQUIRED), TimeSource.INSTRUMENT)


# ---------------------------------------------------------------------------
# Bounded, declined, failed.
# ---------------------------------------------------------------------------


def test_a_chunk_map_past_the_byte_ceiling_is_bounded_and_offers_no_clock(tmp_path):
    """The map a clock depends on was not read, so no clock is claimed."""
    data = nd2_file(julian_day=julian_day(ACQUIRED), frames=200)
    result = _extract(_write(tmp_path, data), limits=Limits(max_bytes_read=2048))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert _readings(result)["image.metadata_bounded"] is True
    assert result.event_time_ns is None
    assert result.bytes_read <= 2048


def test_a_clock_reached_before_the_ceiling_is_kept(tmp_path):
    """A later chunk stopping the read does not unsay what an earlier one said."""
    path = _write(tmp_path, nd2_file(julian_day=julian_day(ACQUIRED), relative_ms=(0.0, 5.0)))
    everything = _extract(path)
    stopped = _extract(path, limits=Limits(max_bytes_read=everything.bytes_read - 1))

    assert _readings(stopped)["image.metadata_bounded"] is True
    assert stopped.event_time_ns == everything.event_time_ns == nanoseconds_since_epoch(ACQUIRED)


def test_a_legacy_jpeg_2000_nd2_is_declined(tmp_path):
    path = _write(tmp_path, b"\x00\x00\x00\x0cjP  \r\n\x87\n" + bytes(64))
    assert _extract(path).outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_a_file_named_nd2_holding_something_else_is_declined(tmp_path):
    path = _write(tmp_path, b"plainly not an ND2 file " * 8)
    assert _extract(path).outcome is ScanOutcome.SKIPPED_BY_POLICY


def test_an_nd2_with_no_chunk_map_fails_that_file_and_nothing_else(tmp_path):
    """An acquisition stopped mid-write leaves no map, and a failure is retried next run."""
    data = nd2_file(julian_day=julian_day(ACQUIRED), with_map=False)
    assert _extract(_write(tmp_path, data)).outcome is ScanOutcome.CONTENT_ERROR


def test_a_map_pointing_outside_the_file_fails_rather_than_reading_anywhere(tmp_path):
    data = bytearray(nd2_file(julian_day=julian_day(ACQUIRED)))
    struct.pack_into("<Q", data, len(data) - 8, len(data) * 4)
    result = _extract(_write(tmp_path, bytes(data)))

    assert result.outcome is ScanOutcome.CONTENT_ERROR
    assert result.error_type == "StructureBoundsError"


def test_a_level_claiming_billions_of_items_costs_nothing_and_still_reads(tmp_path):
    """A level's count bounds a loop, never an allocation."""
    metadata = lv_level(
        "SLxPictureMetadata",
        [lv_double("dTimeAbsolute", julian_day(ACQUIRED))],
        claimed_items=4_000_000_000,
    )
    result = _extract(_write(tmp_path, nd2_file(julian_day=None, metadata=metadata)))

    assert result.outcome is ScanOutcome.EXTRACTED
    assert result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)


def test_a_level_claiming_more_bytes_than_it_holds_fails_the_file(tmp_path):
    metadata = bytearray(lv_level("SLxPictureMetadata", [lv_double("dTimeAbsolute", 1.0)]))
    # The level's length follows its type, its name's length, the name, and its item count.
    name_bytes = len("SLxPictureMetadata\x00".encode("utf-16-le"))
    struct.pack_into("<Q", metadata, 2 + name_bytes + 4, 1 << 40)
    result = _extract(_write(tmp_path, nd2_file(julian_day=None, metadata=bytes(metadata))))

    assert result.outcome is ScanOutcome.CONTENT_ERROR


# ---------------------------------------------------------------------------
# The binary variant on its own.
# ---------------------------------------------------------------------------


def test_the_lite_variant_nests_levels_and_keeps_a_repeated_names_first_value():
    buffer = lv_level(
        "top",
        [
            lv_string("name", "first"),
            lv_string("name", "second"),
            lv_level("inner", [lv_double("x", 1.5)]),
        ],
    )
    assert lite_variant(buffer) == {"top": {"name": "first", "inner": {"x": 1.5}}}


def test_metadata_nested_past_the_depth_limit_is_refused():
    buffer = lv_double("x", 1.0)
    for _ in range(40):
        buffer = lv_level("level", [buffer])
    with pytest.raises(ValueError, match="nest deeper"):
        lite_variant(buffer)


def test_an_unknown_value_type_is_refused_rather_than_guessed():
    """A wrong guess at one width misreads every value after it."""
    with pytest.raises(ValueError, match="not one this decodes"):
        lite_variant(lv_item(76, "", bytes(16)))


# ---------------------------------------------------------------------------
# Routing and the process boundary.
# ---------------------------------------------------------------------------


def test_a_misnamed_nd2_is_supported_once_detection_recognised_its_content(tmp_path):
    path = _write(tmp_path, nd2_file(julian_day=julian_day(ACQUIRED)), name="stack.bin")
    extractor = EXTRACTORS.get("nikon_nd2")
    assert extractor.supports(_context(path, extension=".bin", media_type=ND2_MEDIA_TYPE))
    assert not extractor.supports(_context(path, extension=".bin"))


def test_an_isolated_extraction_answers_across_the_process_boundary(tmp_path):
    from ingence.executors.supervisor import Supervisor

    path = _write(tmp_path, nd2_file(julian_day=julian_day(ACQUIRED)))
    supervised = Supervisor(timeout_seconds=60.0).run("nikon_nd2", _context(path))
    assert not supervised.contained
    assert supervised.result.event_time_ns == nanoseconds_since_epoch(ACQUIRED)
