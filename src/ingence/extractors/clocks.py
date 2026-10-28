"""Content clocks: the date forms formats write, parsed strictly or not at all.

This module is not an extractor, and two extractors share it on purpose. OOXML core properties
and a notebook's execution metadata both write W3C-DTF, the profile of ISO 8601 the web
settled on. Two parsers of one grammar would be two places for the partial cases to be decided
differently, and the partial cases are the whole difficulty. Deleting either extractor leaves
this module in use by the other.

A date short of a day abstains: reading ``2026-03`` as the first of March asserts a day nobody
wrote, and a wrong day is worse than no day because it looks like an answer. A
missing *time* is not an abstention, because a date with no time is a real
statement about a day and midnight UTC is how the engine already stores one. A
missing *offset* is read as UTC: OOXML specifies its core properties as UTC and
producers omit the designator, and attaching the local zone would move every
document's clock by wherever the scan happened to run.

Instrument clocks arrive as numbers, and convert exactly. A Windows FILETIME, which Leica and
Lambert Instruments write, counts tenths of a microsecond from 1601 and is UTC by definition. A
Julian day number, which Nikon writes, is a double counting days from noon on January 1 of 4713
BC. Nikon does not say which zone it means. Comparing a real file's own text date and
modification time with the decoded value shows that the number is UTC. Both abstain outside 1980
to 2200. An instrument whose clock reads 1601 or 1970 has an unset field or a flat battery, and
a tier-one clock outranks every other clock a file has, so a wrong one does more damage than
none.

Integer arithmetic, never a float timestamp. A notebook records execution to
the microsecond, and ``datetime.timestamp() * 1e9`` is exact only for whole
seconds. A float near 1.8e18 has a spacing of 256, so a microsecond clock would
come back tens of nanoseconds away from what the file says. A float result is the same on
every run, and still wrong. The exact integer result costs no more. A Julian day's double
becomes a fraction before any arithmetic for the same reason, so the result is
exactly the value the file stored.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from fractions import Fraction

__all__ = ["filetime_ns", "julian_day_ns", "nanoseconds_since_epoch", "parse_w3cdtf"]

#: ``YYYY[-MM[-DD[Thh:mm[:ss[.f]][TZD]]]]``. One pattern rather than a field-by-field
#: parse, because the optionality is the specification and writing it out is what
#: makes the partial forms visible. The offset accepts ``+hh:mm`` as W3C-DTF writes
#: it and ``+hhmm`` as ISO 8601's basic form writes it. Neither is a guess.
_W3CDTF = re.compile(
    r"""
    ^(?P<year>\d{4})
    (?:-(?P<month>\d{2})
        (?:-(?P<day>\d{2})
            (?:T(?P<hour>\d{2}):(?P<minute>\d{2})
                (?::(?P<second>\d{2})(?:[.,](?P<fraction>\d{1,9}))?)?
                (?P<zone>[Zz]|[+\-]\d{2}:?\d{2})?
            )?
        )?
    )?$
    """,
    re.VERBOSE,
)

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

#: FILETIME ticks, tenths of a microsecond, from 1601-01-01 to 1970-01-01 at UTC.
_FILETIME_TICKS_BEFORE_EPOCH = 116_444_736_000_000_000
#: The Julian day number of 1970-01-01T00:00:00 UTC, 2440587.5, as an exact fraction.
_JULIAN_DAY_AT_EPOCH = Fraction(4_881_175, 2)
_NANOSECONDS_PER_DAY = 86_400_000_000_000
#: The span an instrument's clock is believed inside: 1980-01-01 up to 2200-01-01, UTC.
_INSTRUMENT_SPAN_NS = (315_532_800_000_000_000, 7_258_118_400_000_000_000)


def nanoseconds_since_epoch(moment: dt.datetime) -> int:
    """Return a datetime as exact integer nanoseconds since the epoch at UTC.

    A naive datetime is read as UTC, on the reasoning in the module docstring.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)
    delta = aware - _EPOCH
    seconds = delta.days * 86_400 + delta.seconds
    return seconds * 1_000_000_000 + delta.microseconds * 1_000


def filetime_ns(ticks: int) -> int | None:
    """Return a Windows FILETIME as nanoseconds since the epoch at UTC, or None.

    Exact, because a FILETIME counts whole tenths of a microsecond. Zero is a field
    nobody set, and a time outside the instrument span abstains.
    """
    if ticks <= 0:
        return None
    return _within_instrument_span((ticks - _FILETIME_TICKS_BEFORE_EPOCH) * 100)


def julian_day_ns(day: float) -> int | None:
    """Return a Julian day number as nanoseconds since the epoch at UTC, or None.

    As exact as the double it arrives in, which at today's dates resolves about forty
    microseconds. A day that is not a finite positive number, or that falls outside
    the instrument span, abstains.
    """
    if not math.isfinite(day) or day <= 0:
        return None
    nanoseconds = round((Fraction(day) - _JULIAN_DAY_AT_EPOCH) * _NANOSECONDS_PER_DAY)
    return _within_instrument_span(nanoseconds)


def parse_w3cdtf(raw: str) -> tuple[int, str] | None:
    """Return nanoseconds since the epoch and the resolution written, or None.

    The resolution is ``day``, ``minute``, ``second`` or ``subsecond``, which is
    what a reader needs to know how much of the value was stated rather than
    filled in.
    """
    match = _W3CDTF.match(raw.strip())
    if match is None:
        return None
    fields = match.groupdict()
    if not fields["month"] or not fields["day"]:
        return None

    try:
        moment = dt.datetime(
            year=int(fields["year"]),
            month=int(fields["month"]),
            day=int(fields["day"]),
            hour=int(fields["hour"] or 0),
            minute=int(fields["minute"] or 0),
            second=int(fields["second"] or 0),
            tzinfo=_zone_of(fields["zone"]),
        )
    except ValueError:
        # Month 13, hour 24, a leap second, an offset of 99 hours. A malformed
        # field is not an event and not an error either: the file is intact and
        # this value is not.
        return None

    fraction = fields["fraction"] or ""
    nanoseconds = nanoseconds_since_epoch(moment) + (int(fraction.ljust(9, "0")) if fraction else 0)
    if fraction:
        resolution = "subsecond"
    elif fields["second"]:
        resolution = "second"
    elif fields["hour"]:
        resolution = "minute"
    else:
        resolution = "day"
    return nanoseconds, resolution


def _zone_of(designator: str | None) -> dt.timezone:
    """Return the stated UTC offset, or UTC where none is written."""
    if not designator or designator in {"Z", "z"}:
        return dt.UTC
    digits = designator[1:].replace(":", "")
    delta = dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return dt.timezone(-delta if designator[0] == "-" else delta)


def _within_instrument_span(nanoseconds: int) -> int | None:
    """Return a time inside the span an instrument's clock is believed in, or None."""
    earliest, latest = _INSTRUMENT_SPAN_NS
    return nanoseconds if earliest <= nanoseconds < latest else None
