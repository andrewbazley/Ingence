"""A date a name spells is a date only where a calendar and the plausible span hold it.

A name spelling a date no nanosecond count can hold gives no clock. The date reader must also not
read a sample's number (``4217-09-13``), a counter (``6-2-013``, the year 13) or a time of day
(``7-35-26``, the thirty-fifth of July) as a date, because a date is a component that the rest of
the engine groups, orders and dates by. The plausible span is a setting, and its default was
measured on real censuses. A run shaped like a date that the span or a calendar does not hold is
read as the numbers it is written with.

Three tests of one reading, each over the grammar of a written date and never over a name that
revealed it. The year lies in the span. The month and the day are ones a calendar holds, whichever
way round they are written (the ISO form and the day-and-month form alike). One separator runs
between day, month and year (``2-17-2026``, never ``6_2-17``). Every name here is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.core.settings import Settings
from ingence.dictionary.discover import event_time_from_atoms
from ingence.dictionary.normalize import UNNAMED_YEARS, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
DEFAULT = Normaliser(STORE)
WIDE = Normaliser(STORE, years=(1000, 5000))


def _dates(normaliser: Normaliser, name: str) -> list[str]:
    return [atom.normalised for atom in normaliser.atoms(name) if atom.kind is AtomKind.DATE]


def _read(name: str) -> list[tuple[str, str]]:
    return [(atom.text, atom.kind.value) for atom in DEFAULT.atoms(name)]


@pytest.mark.parametrize(
    ("name", "numbers"),
    [
        ("kelt_4217-09-13", ["4217", "09", "13"]),  # a sample's number, in the ISO shape
        ("kelt_1113-05-20", ["1113", "05", "20"]),
        ("28830912_vorn", ["28830912"]),  # compact, eight digits
        ("vorn_2977.03", ["2977.03"]),  # a year and a month
        ("zib_6-2-013", ["6", "2", "013"]),  # a counter, not the year 13
        ("kelt_4-6-47", ["4", "6", "47"]),  # a two-digit year, read in this century, past the span
    ],
)
def test_a_year_outside_the_plausible_span_is_no_date(name: str, numbers: list[str]) -> None:
    """The run is read as the numbers it is written with, as its grammar otherwise allows."""
    atoms = DEFAULT.atoms(name)
    assert not [atom for atom in atoms if atom.kind is AtomKind.DATE]
    assert [atom.text for atom in atoms if atom.kind is AtomKind.NUMBER] == numbers


@pytest.mark.parametrize(
    ("name", "date"),
    [
        ("20030407_vorn", "2003-04-07"),
        ("kelt_2031-07-16", "2031-07-16"),
        ("zib_9-14-2024", "2024-09-14"),
        ("zib_3.8.16", "2016-03-08 or 2016-08-03"),  # a swap names both
    ],
)
def test_a_date_inside_the_span_is_still_a_date(name: str, date: str) -> None:
    assert _dates(DEFAULT, name) == [date]


def test_a_year_leads_only_before_a_two_digit_month_and_day() -> None:
    """The tokeniser reads a leading year only before a month and a day of two digits each. So
    ``12.9.17`` is a day of 2017, and ``12.09.17`` is also 17 September 2012: two field orders,
    no date on its own."""
    assert _dates(DEFAULT, "zib_12.9.17") == ["2017-12-09 or 2017-09-12"]  # both
    assert _dates(DEFAULT, "zib_12.09.17") == []


@pytest.mark.parametrize(
    "name",
    [
        "kelt_7-35-26",  # a time of day: the thirty-fifth of July
        "vorn_23-23-26",  # neither way round is a month
        "kelt_10-0-2020",  # a day or a month of nought
    ],
)
def test_a_day_and_month_no_calendar_holds_is_no_date(name: str) -> None:
    """Both the ISO form and the day-and-month form ask whether the month and day exist."""
    assert _dates(DEFAULT, name) == []


def test_one_separator_runs_between_day_month_and_year() -> None:
    """A label's number beside a date is not the date's day, and the date itself is found."""
    assert _dates(DEFAULT, "zib6_2-17-2026_log") == ["2026-02-17"]
    assert _dates(DEFAULT, "vorn_6.5-40min") == []
    assert _read("vorn_6.5-40min") == [("vorn", "word"), ("6.5", "number"), ("40min", "quantity")]


def test_a_day_and_month_that_could_swap_stay_ambiguous() -> None:
    """Both readings a calendar holds are kept, and neither is chosen."""
    (atom,) = [atom for atom in DEFAULT.atoms("zib_01-02-2026") if atom.kind is AtomKind.DATE]
    assert atom.ambiguous
    assert (atom.normalised, atom.alternatives) == (
        "2026-01-02 or 2026-02-01",
        ("2026-01-02", "2026-02-01"),
    )


def test_a_time_of_day_after_a_date_no_longer_hides_the_names_clock() -> None:
    """Read as a second date, a time of day would make the name carry two dates, and the clock
    would abstain."""
    atoms = DEFAULT.atoms("2011_03_05_15_43_29_zib")
    assert [atom.normalised for atom in atoms if atom.kind is AtomKind.DATE] == ["2011-03-05"]
    moment, _ = event_time_from_atoms(atoms)
    assert moment is not None


def test_the_span_is_a_setting_and_no_longer_in_the_version() -> None:
    """A project may move the years. The run record states them, and a run of digits that two
    spans read differently is a different component by its kind and value."""
    assert _dates(WIDE, "kelt_4217-09-13") == ["4217-09-13"]
    # The tokeniser's version is 14.
    assert WIDE.version == "14"
    assert Normaliser(STORE, glued_runs="whole", years=(1000, 5000)).version == "14-whole"
    assert DEFAULT.version == "14"


def test_the_default_span_is_the_measured_one() -> None:
    """The unnamed years are the measured default span. A run names its own years."""
    assert UNNAMED_YEARS == (1990, 2035)
    assert tuple(Settings().dictionary.years) == (2013, "present")
    with pytest.raises(ValueError, match="years"):
        Normaliser(STORE, years=(2035, 1990))


def test_the_projects_span_reaches_the_census(tmp_path: Path) -> None:
    """The setting is read where the census builds its normaliser, as ``glued_runs`` is."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_4217-09-13.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["years"] = [1000, 5000]
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    report = run_census(
        Project.open(project.path),
        run_id="run_20260101T000001Z_111111111111",
        started_at="2026-01-01T00:00:00Z",
    )
    assert report.published
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT normalized_value, normalizer_version FROM components "
            "WHERE component_type = 'date'"
        ).fetchall()
    assert rows == [("4217-09-13", "14")]  # the tokeniser's version is 14
