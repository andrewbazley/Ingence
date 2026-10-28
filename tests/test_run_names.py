"""A run is named for a person: "Scan 3, September 23, 1:00 PM EDT".

A run identifier such as ``run_20260312T170000Z_...`` is hard for a person to read. Held here: runs
are numbered by when each began, counting from 1. The time is written in a zone with its
abbreviation, converted per instant. The year appears only where it differs from the latest run's. A
supplied ``started_at`` never reorders runs whose identifiers carry their stamp. Every identifier is
synthetic, and every zone fixed, so the figures hold on any machine.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from ingence.api.run_names import began, name_runs, project_run_names, run_label
from ingence.storage.layout import RUNS_DIR

if TYPE_CHECKING:
    from pathlib import Path

#: Four hours behind UTC, called EDT: the zone the labels below are read in.
EDT = dt.timezone(dt.timedelta(hours=-4), "EDT")


def _winter(when: dt.datetime | None) -> bool:
    """The test zone's standard-time months: November to March."""
    return when is not None and (when.month < 4 or when.month > 10)


class _Eastern(dt.tzinfo):
    """A zone with a daylight rule of its own, for converting per instant: EST in winter."""

    def utcoffset(self, when: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(hours=-5 if _winter(when) else -4)

    def dst(self, when: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(hours=0 if _winter(when) else 1)

    def tzname(self, when: dt.datetime | None) -> str:
        return "EST" if _winter(when) else "EDT"


def test_a_run_is_named_by_its_number_and_its_local_time() -> None:
    named = name_runs(
        [
            ("run_20260312T170000Z_aaaaaaaaaaaa", None),
            ("run_20260312T161520Z_bbbbbbbbbbbb", None),
            ("run_20260313T144500Z_cccccccccccc", None),
        ],
        zone=EDT,
    )
    assert named["run_20260312T161520Z_bbbbbbbbbbbb"]["label"] == "Scan 1, March 12, 12:15 PM EDT"
    assert named["run_20260312T170000Z_aaaaaaaaaaaa"]["label"] == "Scan 2, March 12, 1:00 PM EDT"
    assert named["run_20260313T144500Z_cccccccccccc"]["label"] == "Scan 3, March 13, 10:45 AM EDT"
    assert [named[key]["number"] for key in sorted(named)] == [1, 2, 3]
    assert named["run_20260312T170000Z_aaaaaaaaaaaa"]["began"] == "2026-03-12T17:00:00Z"


def test_the_stamp_orders_runs_and_a_supplied_start_does_not() -> None:
    """A caller may supply ``started_at`` (a replay, a test); an inference whose supplied time
    precedes the census it read keeps its place after it, by its identifier's stamp."""
    named = name_runs(
        [
            ("run_20260312T161520Z_bbbbbbbbbbbb", "2026-03-12T16:15:20Z"),
            ("run_20260312T170000Z_aaaaaaaaaaaa", "2026-03-11T00:00:00Z"),
        ],
        zone=EDT,
    )
    assert named["run_20260312T161520Z_bbbbbbbbbbbb"]["number"] == 1
    assert named["run_20260312T170000Z_aaaaaaaaaaaa"]["number"] == 2


def test_without_a_stamp_the_record_says_and_without_either_a_run_is_only_numbered() -> None:
    named = name_runs(
        [
            ("run_t_dddddddddddd", "2026-09-23T03:00:00Z"),
            ("run_20260923T020000Z_eeeeeeeeeeee", None),
            ("run_x_ffffffffffff", "t"),
        ],
        zone=EDT,
    )
    assert (
        named["run_20260923T020000Z_eeeeeeeeeeee"]["label"] == "Scan 1, September 22, 10:00 PM EDT"
    )
    assert named["run_t_dddddddddddd"]["label"] == "Scan 2, September 22, 11:00 PM EDT"
    # Nothing says when it began: numbered after every run that does, and no time invented.
    assert named["run_x_ffffffffffff"]["label"] == "Scan 3"
    assert began("run_x_ffffffffffff", "t") is None


def test_midnight_and_noon_read_as_a_person_says_them() -> None:
    midnight = dt.datetime(2026, 9, 24, 4, 5, tzinfo=dt.UTC)
    noon = dt.datetime(2026, 9, 24, 16, 0, tzinfo=dt.UTC)
    assert run_label(4, midnight, zone=EDT) == "Scan 4, September 24, 12:05 AM EDT"
    assert run_label(5, noon, zone=EDT) == "Scan 5, September 24, 12:00 PM EDT"
    assert run_label(6, None) == "Scan 6"


def test_the_zone_is_read_per_instant_and_the_year_only_where_it_differs() -> None:
    named = name_runs(
        [
            ("run_20251215T150000Z_aaaaaaaaaaaa", None),
            ("run_20260110T150000Z_bbbbbbbbbbbb", None),
            ("run_20260923T150000Z_cccccccccccc", None),
        ],
        zone=_Eastern(),
    )
    assert named["run_20251215T150000Z_aaaaaaaaaaaa"]["label"] == (
        "Scan 1, December 15, 2025, 10:00 AM EST"
    )
    assert named["run_20260110T150000Z_bbbbbbbbbbbb"]["label"] == "Scan 2, January 10, 10:00 AM EST"
    assert named["run_20260923T150000Z_cccccccccccc"]["label"] == (
        "Scan 3, September 23, 11:00 AM EDT"
    )


def test_a_zone_naming_no_abbreviation_is_written_as_its_offset() -> None:
    offset = dt.timezone(dt.timedelta(hours=5, minutes=30))
    instant = dt.datetime(2026, 9, 23, 17, 0, tzinfo=dt.UTC)
    assert run_label(1, instant, zone=offset).endswith("10:30 PM UTC+05:30")


def test_a_project_s_runs_are_named_from_their_directories(tmp_path: Path) -> None:
    for run_id in (
        "run_20260101T000002Z_222222222222",
        "run_20260101T000001Z_111111111111",
    ):
        (tmp_path / RUNS_DIR / run_id).mkdir(parents=True)
    (tmp_path / RUNS_DIR / "not-a-run.txt").write_text("x")
    named = project_run_names(tmp_path, zone=dt.UTC)
    assert set(named) == {"run_20260101T000001Z_111111111111", "run_20260101T000002Z_222222222222"}
    assert named["run_20260101T000001Z_111111111111"]["label"] == "Scan 1, January 1, 12:00 AM UTC"
    assert named["run_20260101T000002Z_222222222222"]["number"] == 2
