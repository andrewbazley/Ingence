"""An ND2 read for its clock alone: a setting, off by default.

Of an ND2's turn, the instrument extractor takes a third of the time, and the generic string
harvest, reading every byte, takes more. Detection and the instrument's own extractor alone would
cost about half the time and a quarter of the bytes. So ``extraction.nd2_clock_only`` reads an ND2
with detection, the digest the hash mode names, and the readers that declare a header read (the
instrument extractor's clock), and no reader of the body. It is off by default, so nothing changes
until a person turns it on.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from fixtures.instrument_files import julian_day, nd2_file
from ingence.core.schema import Entry, EntryType
from ingence.core.settings import ExtractionSettings, Settings
from ingence.scan.deepen import deepen_entry

if TYPE_CHECKING:
    from pathlib import Path

ACQUIRED = dt.datetime(2021, 5, 6, 7, 8, 9, tzinfo=dt.UTC)


def _entry(path: Path) -> Entry:
    return Entry(
        entry_id="entry_kelt",
        root_id="root_x",
        relative_path=path.name,
        name=path.name,
        entry_type=EntryType.FILE,
        extension="nd2",
        size_bytes=path.stat().st_size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


def _dive(path: Path, **extraction: object) -> tuple[set[str], set[str], object]:
    outcome = deepen_entry(
        _entry(path),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(extraction=ExtractionSettings(enabled=True, **extraction)),
    )
    stages = {stage.stage for stage in outcome.stages}
    keys = {observation.key for observation in outcome.observations}
    return stages, keys, outcome


def test_an_nd2_read_for_its_clock_alone_skips_the_body_and_keeps_the_clock(tmp_path) -> None:
    path = tmp_path / "kelt_vorn_01.nd2"
    path.write_bytes(nd2_file(julian_day=julian_day(ACQUIRED)))
    stages, keys, outcome = _dive(path)
    assert "extract.generic" in stages  # the default reads the body
    assert any(key.startswith("strings.") for key in keys)

    stages, keys, clocked = _dive(path, nd2_clock_only=True)
    assert "extract.generic" not in stages
    assert not any(key.startswith("strings.") for key in keys)
    assert "type.media_type" in keys  # detection still runs
    assert clocked.event_time_ns == outcome.event_time_ns is not None  # the clock is kept
    assert ("clock_only", "an ND2 read for its clock alone (extraction.nd2_clock_only)") in (
        clocked.warnings
    )


def test_the_setting_is_off_by_default_and_names_the_extensions_it_reads() -> None:
    settings = ExtractionSettings()
    assert settings.nd2_clock_only is False
    assert settings.clock_only_extensions == ("nd2",)


def test_a_file_read_for_its_clock_alone_is_never_reused_as_one_read_whole() -> None:
    from ingence.scan.reuse import processing_policy

    def fingerprint(extension: str, **extraction: object) -> str:
        return processing_policy(
            extensions=frozenset({extension}),
            size_bytes=100,
            settings=Settings(extraction=ExtractionSettings(enabled=True, **extraction)),
        ).fingerprint

    assert fingerprint(".nd2") != fingerprint(".nd2", nd2_clock_only=True)
    assert fingerprint(".tif") == fingerprint(".tif", nd2_clock_only=True)  # others unchanged
