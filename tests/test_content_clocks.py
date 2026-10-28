"""The date forms content clocks arrive in, parsed strictly or not at all.

Shared by the OOXML and notebook extractors, so one grammar decides the partial cases once, and by
the instrument readers, whose clocks arrive as numbers. The rules: short of a day abstains, a
missing time is a day at midnight UTC, and a missing offset is read as UTC. An instrument's number
converts exactly, and abstains when it is unset or falls outside 1980 to 2200.
"""

from __future__ import annotations

import datetime as dt
import math
from fractions import Fraction

import pytest

from fixtures.instrument_files import filetime_ticks, julian_day
from ingence.extractors.clocks import (
    filetime_ns,
    julian_day_ns,
    nanoseconds_since_epoch,
    parse_w3cdtf,
)


@pytest.mark.parametrize(
    ("raw", "resolution"),
    [
        ("2026-03-04T09:30:00Z", "second"),
        ("2026-03-04T09:30:00.123456Z", "subsecond"),
        ("2026-03-04T09:30Z", "minute"),
        ("2026-03-04", "day"),
        ("2026-03-04T09:30:00+01:00", "second"),
        ("2026-03-04T09:30:00+0100", "second"),
        ("2026-03-04T09:30:00", "second"),
    ],
)
def test_a_date_carrying_a_day_is_an_event(raw: str, resolution: str) -> None:
    """A date with no time is a real statement about a day, and the resolution says so."""
    parsed = parse_w3cdtf(raw)
    assert parsed is not None
    assert parsed[1] == resolution


@pytest.mark.parametrize(
    "raw",
    [
        "2026",
        "2026-03",
        "2026-13-01",
        "2026-02-30",
        "2026-03-04T24:00:00Z",
        "2026-03-04 09:30:00",
        "2026-03-04T09:30:00.1234567890Z",
        "yesterday",
        "",
    ],
)
def test_anything_short_of_a_day_or_malformed_abstains(raw: str) -> None:
    """Reading ``2026-03`` as the first of March asserts a day nobody wrote.

    A wrong day is worse than no day because it looks like an answer, and a space
    where the grammar puts a ``T`` is a different grammar rather than a slip to
    forgive.
    """
    assert parse_w3cdtf(raw) is None


def test_an_offset_is_applied_and_a_missing_one_is_utc() -> None:
    """Ten thirty an hour east is nine thirty UTC, and no designator means UTC."""
    utc = parse_w3cdtf("2026-03-04T09:30:00Z")
    assert utc is not None
    assert parse_w3cdtf("2026-03-04T10:30:00+01:00") == utc
    assert parse_w3cdtf("2026-03-04T09:30:00") == utc


def test_a_microsecond_clock_is_exact_where_a_float_timestamp_is_not() -> None:
    """Why the arithmetic is integer: a float near 1.8e18 has a spacing of 256.

    A notebook records execution to the microsecond, and multiplying a float
    timestamp by a billion lands tens of nanoseconds from what the file says.
    """
    moment = dt.datetime(2026, 3, 4, 9, 30, 0, 123457, tzinfo=dt.UTC)
    parsed = parse_w3cdtf("2026-03-04T09:30:00.123457Z")
    assert parsed is not None
    exact = (int(moment.replace(microsecond=0).timestamp())) * 1_000_000_000 + 123_457_000
    assert parsed[0] == exact == nanoseconds_since_epoch(moment)
    assert int(moment.timestamp() * 1_000_000_000) != exact


def test_nine_fractional_digits_reach_the_nanosecond() -> None:
    parsed = parse_w3cdtf("2026-03-04T09:30:00.000000001Z")
    whole = parse_w3cdtf("2026-03-04T09:30:00Z")
    assert parsed is not None
    assert whole is not None
    assert parsed[0] - whole[0] == 1


# ---------------------------------------------------------------------------
# Instrument clocks, which arrive as numbers.
# ---------------------------------------------------------------------------


def test_a_filetime_converts_exactly_to_the_tenth_of_a_microsecond() -> None:
    """A FILETIME counts whole tenths of a microsecond, so nothing may be rounded."""
    moment = dt.datetime(2026, 3, 4, 9, 30, 0, 123456, tzinfo=dt.UTC)
    assert filetime_ns(filetime_ticks(moment) + 7) == nanoseconds_since_epoch(moment) + 700


def test_a_zero_filetime_is_a_field_nobody_set() -> None:
    assert filetime_ns(0) is None


@pytest.mark.parametrize(
    "moment",
    [
        dt.datetime(1601, 1, 1, 0, 0, 1, tzinfo=dt.UTC),
        dt.datetime(1970, 1, 1, tzinfo=dt.UTC),
        dt.datetime(1979, 12, 31, 23, 59, 59, tzinfo=dt.UTC),
        dt.datetime(2200, 1, 1, tzinfo=dt.UTC),
    ],
)
def test_an_instrument_time_outside_1980_to_2200_abstains(moment: dt.datetime) -> None:
    """An instrument clock reading 1970 has a flat battery, and tier one outranks every clock."""
    assert filetime_ns(filetime_ticks(moment)) is None


def test_the_instrument_span_includes_its_first_instant() -> None:
    moment = dt.datetime(1980, 1, 1, tzinfo=dt.UTC)
    assert filetime_ns(filetime_ticks(moment)) == nanoseconds_since_epoch(moment)


@pytest.mark.parametrize("hour", [0, 6, 12, 18])
def test_a_julian_day_converts_exactly_where_its_double_is_exact(hour: int) -> None:
    """Midnight, noon and the quarters between are exact in binary, so the result must be."""
    moment = dt.datetime(2026, 3, 4, hour, tzinfo=dt.UTC)
    assert julian_day_ns(julian_day(moment)) == nanoseconds_since_epoch(moment)


def test_a_julian_day_keeps_every_bit_of_precision_its_double_carries() -> None:
    """Converted through a fraction, so the result is the stored value and not a float's guess."""
    day = 2_461_103.600_000_000_2
    expected = round((Fraction(day) - Fraction(4_881_175, 2)) * 86_400_000_000_000)
    assert julian_day_ns(day) == expected


@pytest.mark.parametrize("day", [0.0, -1.0, math.nan, math.inf, 2_415_020.5, 2_524_593.5])
def test_a_julian_day_unset_impossible_or_outside_the_span_abstains(day: float) -> None:
    """Zero, a negative or non-finite day, 1900, and the first instant of 2200 all abstain."""
    assert julian_day_ns(day) is None
