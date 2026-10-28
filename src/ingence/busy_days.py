"""The busiest days of an experiment's files.

An experiment's files are often written in a few sittings: most of them on one day or a run of
days, a few more over months. The Years view opens an experiment's files on those days. The
engine decides which, so the page draws a window and computes none of it.

The rule takes the day with the most files. It extends the run of days around that day while the
next day holds at least ``floor`` of the dated files (5 percent). The run is the busiest days if
it holds at least ``min_share`` of the dated files (half). Where the files are too few
(``min_files``), or all fall within ``min_span_days`` of each other, there is nothing to open on
and the answer is none: the whole span is the window.

A file's day is the UTC day of its believed time (the earliest credible time a rule concluded),
else its modification time. A file with neither is not dated and is not counted in either
number. Every cut is a parameter with its default named here.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "FLOOR_SHARE",
    "MIN_FILES",
    "MIN_SHARE_PERCENT",
    "MIN_SPAN_DAYS",
    "BusyDays",
    "busiest_days",
    "day_of",
    "days_words",
]

#: Fewer dated files than this and there is no busiest run: a handful of files has no sittings.
MIN_FILES = 20
#: Files all written within this many days of each other are one sitting already.
MIN_SPAN_DAYS = 30
#: A day joins the run around the peak if it holds at least this share of the dated files.
FLOOR_SHARE = 0.05
#: The run must hold at least this percent of the dated files (rounded half up) to be the answer.
MIN_SHARE_PERCENT = 50

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_EPOCH = dt.date(1970, 1, 1)


@dataclass(frozen=True, slots=True)
class BusyDays:
    """A run of days that holds most of an experiment's dated files."""

    first_day: dt.date
    last_day: dt.date
    #: Dated files written on those days, and all dated files: the share's two numbers.
    files: int
    dated_files: int

    @property
    def percent(self) -> int:
        """The share as a whole percent, rounded half up."""
        return (200 * self.files + self.dated_files) // (2 * self.dated_files)


def day_of(moment: str | None) -> int | None:
    """The UTC day number (days since 1970-01-01) of an ISO 8601 time or day, or None.

    A time stating a zone is moved to UTC. A day alone, or a time stating none, is read as UTC,
    which is how the page draws a believed time.
    """
    if not moment:
        return None
    text = str(moment).strip().replace(" ", "T", 1)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.UTC)
    return (parsed.date() - _EPOCH).days


def busiest_days(
    days: Iterable[int | None],
    *,
    min_files: int = MIN_FILES,
    min_span_days: int = MIN_SPAN_DAYS,
    floor: float = FLOOR_SHARE,
    min_share_percent: int = MIN_SHARE_PERCENT,
) -> BusyDays | None:
    """The busiest run of days among the files' day numbers, or None where there is none.

    ``days`` holds one day number per file, None for a file with no time.
    """
    counted = Counter(day for day in days if day is not None)
    dated = sum(counted.values())
    if dated < min_files:
        return None
    ordered = sorted(counted)
    if ordered[-1] - ordered[0] < min_span_days:
        return None
    # The earliest day holding the most files, so the same files always give the same run.
    peak = min(ordered, key=lambda day: (-counted[day], day))
    least = dated * floor
    first = last = peak
    while counted.get(first - 1, 0) >= least:
        first -= 1
    while counted.get(last + 1, 0) >= least:
        last += 1
    held = sum(counted.get(day, 0) for day in range(first, last + 1))
    found = BusyDays(
        first_day=_EPOCH + dt.timedelta(days=first),
        last_day=_EPOCH + dt.timedelta(days=last),
        files=held,
        dated_files=dated,
    )
    return found if found.percent >= min_share_percent else None


def days_words(first: dt.date, last: dt.date) -> str:
    """The run in words: ``2 Mar 2026``, ``2 to 4 Mar 2026``, ``28 Feb to 2 Mar 2026``."""
    if first == last:
        return f"{first.day} {_MONTHS[first.month - 1]} {first.year}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} to {last.day} {_MONTHS[last.month - 1]} {last.year}"
    if first.year == last.year:
        return (
            f"{first.day} {_MONTHS[first.month - 1]} to {last.day} {_MONTHS[last.month - 1]} "
            f"{last.year}"
        )
    return (
        f"{first.day} {_MONTHS[first.month - 1]} {first.year} to "
        f"{last.day} {_MONTHS[last.month - 1]} {last.year}"
    )
