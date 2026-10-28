"""A run named for a person: "Scan 3, September 23, 1:00 PM EDT", computed in one place.

A name such as ``run_20260312T170000Z_0a1b2c3d4e5f`` is hard for a human to read. The engine
numbers the runs chronologically and gives each the local time it ran. Every route that shows a run
gives its name from here. The page shows the name with the identifier beside it in small type and in
the title, because the Record, the logs and the run directories all use the identifier.

A run's number is its place among the project's runs, by when each began, counting from 1. When a
run began is the stamp that its identifier leads with. The engine mints the identifier from the
moment the run starts (``core/ids.run_id``), which is what makes run directories list in
chronological order. The function reads a run record's ``started_at`` only where an identifier
carries no stamp. A caller may supply ``started_at``, as tests and a replay may, and a supplied time
can place an inference before the census that it read. Two runs that began in one second keep the
order of their identifiers.

The time is in the machine's own zone, with its abbreviation. The function converts it per instant,
so a run in January reads in standard time and one in July in daylight time. Tests pass a fixed
zone. The name writes the year only where a run's year differs from the latest run's, so a project
read within one year stays short.

Nothing here reads a root, and nothing here is stored. A name is a rendering of an identifier and a
time, and renumbering after a run is removed changes no record.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING, Any

from ingence.storage.layout import RUNS_DIR

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

__all__ = ["began", "name_runs", "project_run_names", "run_label"]

#: The month names that a label spells. They are in English and never come from the locale, so a
#: label reads the same on every machine and in every test.
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: The stamp a run identifier leads with: ``run_<YYYYMMDD>T<HHMMSS>Z_<digest>``.
_STAMP = re.compile(r"^run_(\d{8})T(\d{6})(?:\d*)Z?_")


def began(run_id: str, started_at: str | None = None) -> dt.datetime | None:
    """Return when a run began, in UTC: its identifier's stamp, else its record's ``started_at``.

    The result is None where neither says. A name then leaves the time out and does not invent
    one.
    """
    stamped = _STAMP.match(run_id)
    if stamped is not None:
        try:
            return dt.datetime.strptime(
                stamped.group(1) + stamped.group(2), "%Y%m%d%H%M%S"
            ).replace(tzinfo=dt.UTC)
        except ValueError:
            pass
    if started_at:
        try:
            parsed = dt.datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)
    return None


def _local(instant: dt.datetime, zone: dt.tzinfo | None) -> dt.datetime:
    """Return the instant in ``zone``, or in the machine's own zone for that instant."""
    return instant.astimezone(zone) if zone is not None else instant.astimezone()


def _zone_name(local: dt.datetime) -> str:
    """Return the zone's abbreviation, or its offset from UTC where the zone names none."""
    named = local.tzname()
    if named:
        return named
    offset = local.utcoffset() or dt.timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def run_label(
    number: int,
    instant: dt.datetime | None,
    *,
    zone: dt.tzinfo | None = None,
    with_year: bool = False,
) -> str:
    """Return one run's name: "Scan 3, September 23, 1:00 PM EDT", or "Scan 3" with no time."""
    if instant is None:
        return f"Scan {number}"
    local = _local(instant, zone)
    hour = local.hour % 12 or 12
    half = "AM" if local.hour < 12 else "PM"
    day = f"{MONTHS[local.month - 1]} {local.day}" + (f", {local.year}" if with_year else "")
    return f"Scan {number}, {day}, {hour}:{local.minute:02d} {half} {_zone_name(local)}"


def name_runs(
    runs: Iterable[tuple[str, str | None]], *, zone: dt.tzinfo | None = None
) -> dict[str, dict[str, Any]]:
    """Number and name every run of one project, from ``(run_id, started_at)`` pairs.

    The result holds, per run identifier, its ``number`` (1 for the earliest), its ``label``, the
    identifier itself, and when it began in UTC as ISO 8601 (or None).
    """
    known = [(run_id, began(run_id, started_at)) for run_id, started_at in runs]
    ordered = sorted(
        known,
        key=lambda item: (
            item[1] is None,
            item[1] or dt.datetime.min.replace(tzinfo=dt.UTC),
            item[0],
        ),
    )
    years = [_local(instant, zone).year for _, instant in ordered if instant is not None]
    latest_year = max(years) if years else None
    named: dict[str, dict[str, Any]] = {}
    for number, (run_id, instant) in enumerate(ordered, start=1):
        with_year = instant is not None and _local(instant, zone).year != latest_year
        named[run_id] = {
            "run_id": run_id,
            "number": number,
            "label": run_label(number, instant, zone=zone, with_year=with_year),
            "began": instant.strftime("%Y-%m-%dT%H:%M:%SZ") if instant is not None else None,
        }
    return named


def project_run_names(
    project: Path,
    *,
    started_at: Mapping[str, str | None] | None = None,
    zone: dt.tzinfo | None = None,
) -> dict[str, dict[str, Any]]:
    """Name every run that a project holds, read from its run directories' names.

    The function opens no run whose identifier carries its stamp, which every run that the engine
    mints does. For a run that does not carry one, the function reads the run record's
    ``started_at``. It takes the value from ``started_at`` where the caller has read it already,
    and from the run otherwise, so every caller numbers alike.
    """
    directory = project / RUNS_DIR
    identifiers = sorted(
        path.name for path in (directory.iterdir() if directory.is_dir() else ()) if path.is_dir()
    )
    supplied = dict(started_at or {})
    for run_id in identifiers:
        if run_id not in supplied and _STAMP.match(run_id) is None:
            supplied[run_id] = _recorded_start(project, run_id)
    return name_runs(((run_id, supplied.get(run_id)) for run_id in identifiers), zone=zone)


def _recorded_start(project: Path, run_id: str) -> str | None:
    """Return a run record's ``started_at``, for an identifier that carries no stamp."""
    from ingence.storage.catalog import open_catalog

    try:
        with open_catalog(project, run_id=run_id) as catalog:
            row = catalog.query("SELECT started_at FROM run LIMIT 1").fetchone()
    except Exception:  # a run that cannot be opened is named without a time and not refused
        return None
    return None if row is None or row[0] is None else str(row[0])
