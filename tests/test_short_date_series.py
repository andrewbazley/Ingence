"""A short date that two field orders allow is settled by its sibling series.

A short date whose year could lead or trail, or whose day and month could swap, is ambiguous and is
not a clock by itself. A folder holding a series of such names is read together. Where the series is
consistent in one field order only (one sibling's first field is above 12, so every sibling is day
first), each ambiguous sibling is read that way, and the reading names the sibling that settled it.
A series consistent in two orders, or in none, settles nothing. Every name here is invented.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import yaml

from ingence.core.project import Project
from ingence.core.schema import Component
from ingence.dictionary.normalize import NORMALIZER_VERSION, Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.infer.subjects import name_dates
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
#: Years wide enough that a short date's year could lead or trail inside them.
WIDE = Normaliser(STORE, years=(2005, 2032))


def test_the_version_names_this_bump() -> None:
    # Tokeniser 14 rereads names.
    assert NORMALIZER_VERSION == "14"
    assert WIDE.version == "14"


# -- the orders one name allows ------------------------------------------------------------------


def test_every_order_a_short_date_allows_is_named() -> None:
    assert WIDE.short_date_orders("kelt_13-04-31") == ((("day_first", "2031-04-13"),),)
    assert WIDE.short_date_orders("kelt_03-04-31") == (
        (("day_first", "2031-04-03"), ("month_first", "2031-03-04")),
    )
    # The year leading or trailing: 22 June 2007, or 7 June 2022.
    assert WIDE.short_date_orders("22-06-07_vorn") == (
        (("day_first", "2007-06-22"), ("year_first", "2022-06-07")),
    )
    # One date either way still allows two orders, and settles nothing between them.
    assert WIDE.short_date_orders("05-05-2031") == (
        (("day_first", "2031-05-05"), ("month_first", "2031-05-05")),
    )
    # A list, a version and a run no calendar holds are no short date.
    assert WIDE.short_date_orders("1-2-15-16") == ()
    assert WIDE.short_date_orders("v1.2.13") == ()
    assert WIDE.short_date_orders("19-2-4_zib") == ()


# -- the series, in a census ---------------------------------------------------------------------


def _census(
    tmp_path: Path,
    files: list[str],
    *,
    rebind: bool = False,
    spellings: tuple[str, ...] | None = None,
    second: str = "",
    years: tuple[int, int] = (2005, 2032),
    day: str = "2032-01-01",
    dictionary: dict[str, object] | None = None,
) -> Project:
    pool = tmp_path / "pool"
    for relative in files:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a")
    project_path = tmp_path / "project"
    if not project_path.exists():
        project = Project.create(project_path, created_at="t", roots=[pool])
        settings = yaml.safe_load((project.path / "settings.yaml").read_text())
        settings.setdefault("dictionary", {})["years"] = list(years)
        settings["dictionary"].update(dictionary or {})
        (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    project = Project.open(project_path)
    second = second or ("2" if rebind else "1")
    assert run_census(
        project,
        run_id=f"run_{day.replace('-', '')}T00000{second}Z_{second * 12}",
        started_at=f"{day}T00:00:0{second}Z",
        rebind=rebind,
        spellings=spellings,
    ).published
    return project


def _clocks(project: Project) -> dict[str, date | None]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT relative_path, event_time_ns FROM entries WHERE entry_type = 'file'"
        ).fetchall()
    return {
        str(path): None if ns is None else datetime.fromtimestamp(ns / 1e9, tz=UTC).date()
        for path, ns in rows
    }


def _settled(project: Project) -> dict[str, dict[str, object]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, o.value_structure FROM observations o "
            "JOIN entries e ON e.entry_id = o.entry_id WHERE o.key = 'name.date_order'"
        ).fetchall()
    return {str(path): json.loads(str(value)) for path, value in rows}


def test_a_sibling_whose_first_field_is_above_twelve_settles_the_series_day_first(
    tmp_path: Path,
) -> None:
    project = _census(
        tmp_path, ["brisa/03-04-31_kelt.txt", "brisa/05-04-31_kelt.txt", "brisa/13-04-31_kelt.txt"]
    )
    clocks = _clocks(project)
    assert clocks["brisa/03-04-31_kelt.txt"] == date(2031, 4, 3)
    assert clocks["brisa/05-04-31_kelt.txt"] == date(2031, 4, 5)
    assert clocks["brisa/13-04-31_kelt.txt"] == date(2031, 4, 13)  # its own reading
    settled = _settled(project)
    assert settled["brisa/03-04-31_kelt.txt"] == {
        "date": "2031-04-03",
        "order": "day_first",
        "settled_by": [{"neighbour": "series", "path": "brisa/13-04-31_kelt.txt"}],
    }
    assert "brisa/13-04-31_kelt.txt" not in settled  # it needed no neighbour


def test_a_sibling_whose_second_field_is_above_twelve_settles_the_series_month_first(
    tmp_path: Path,
) -> None:
    project = _census(tmp_path, ["zib/04-03-31.txt", "zib/04-13-31.txt"])
    assert _clocks(project)["zib/04-03-31.txt"] == date(2031, 4, 3)
    assert _settled(project)["zib/04-03-31.txt"]["order"] == "month_first"


def test_a_series_settles_where_the_year_could_lead_or_trail(tmp_path: Path) -> None:
    # 22-06-31 is only day first (no 31 June, no month 22), so 22-06-07 is 22 June 2007.
    project = _census(tmp_path, ["trell/22-06-07_vorn.txt", "trell/22-06-31_vorn.txt"])
    assert _clocks(project)["trell/22-06-07_vorn.txt"] == date(2007, 6, 22)
    # 31-02-28 is only year first (no 31 February), so 22-06-07 is 7 June 2022.
    project = _census(tmp_path / "second", ["trell/22-06-07_vorn.txt", "trell/31-02-28_vorn.txt"])
    assert _clocks(project)["trell/22-06-07_vorn.txt"] == date(2022, 6, 7)
    assert _settled(project)["trell/22-06-07_vorn.txt"]["order"] == "year_first"


def test_a_series_that_stays_ambiguous_stays_numbers(tmp_path: Path) -> None:
    # Two conventions in one folder (one sibling only day first, one only month first): nothing
    # decides, so the ambiguous sibling keeps no clock. And a series where every sibling allows
    # both orders settles nothing either.
    project = _census(
        tmp_path,
        [
            "kelt/03-04-31.txt",
            "kelt/13-04-31.txt",
            "kelt/04-13-31.txt",
            "vorn/03-04-31.txt",
            "vorn/05-04-31.txt",
        ],
    )
    clocks = _clocks(project)
    assert clocks["kelt/03-04-31.txt"] is None
    assert clocks["vorn/03-04-31.txt"] is None
    assert clocks["vorn/05-04-31.txt"] is None
    assert _settled(project) == {}


def test_a_series_is_one_folder_and_one_separator(tmp_path: Path) -> None:
    # A cousin in another folder, or a sibling written with another separator, settles nothing.
    project = _census(tmp_path, ["kelt/03-04-31.txt", "kelt/13.04.31.txt", "vorn/13-04-31.txt"])
    assert _clocks(project)["kelt/03-04-31.txt"] is None


def test_a_folder_and_a_series_that_disagree_settle_nothing(tmp_path: Path) -> None:
    # The folder dates 22-06-07 to 2007 (22 June), and its sibling 31-02-28 is only year first
    # (7 June 2022): two neighbours saying two things is a conflict, and the name keeps no clock.
    project = _census(
        tmp_path, ["20070917_kelt/22-06-07_vorn.txt", "20070917_kelt/31-02-28_vorn.txt"]
    )
    assert _clocks(project)["20070917_kelt/22-06-07_vorn.txt"] is None
    # Where they agree, both are the evidence.
    project = _census(
        tmp_path / "agree",
        ["20070917_kelt/22-06-07_vorn.txt", "20070917_kelt/22-06-31_vorn.txt"],
    )
    assert _clocks(project)["20070917_kelt/22-06-07_vorn.txt"] == date(2007, 6, 22)
    assert _settled(project)["20070917_kelt/22-06-07_vorn.txt"]["settled_by"] == [
        {"neighbour": "folder", "path": "20070917_kelt"},
        {"neighbour": "series", "path": "20070917_kelt/22-06-31_vorn.txt"},
    ]


def test_a_rebind_reads_the_series_as_the_walk_did(tmp_path: Path) -> None:
    files = ["brisa/03-04-31_kelt.txt", "brisa/13-04-31_kelt.txt", "brisa/zib/22-06-07.txt"]
    walked = _census(tmp_path, files)
    before, evidence = _clocks(walked), _settled(walked)
    rebound = _census(tmp_path, files, rebind=True)
    assert _clocks(rebound) == before
    assert _settled(rebound) == evidence
    # A partial rebind carries the files' readings, and finds the evidence again.
    partial = _census(tmp_path, files, rebind=True, spellings=("trellq",), second="3")
    assert _clocks(partial) == before
    assert _settled(partial) == evidence


def test_a_settled_dates_evidence_is_the_siblings_ruling_out_an_order_it_allows(
    tmp_path: Path,
) -> None:
    """Per sibling, and never itself.

    ``03-04-31`` allows day and month first, so only a sibling ruling out month first is its
    evidence. ``22-06-07`` allows day and year first, and ``03-04-31``, which no year can lead,
    rules year first out for it.
    """
    project = _census(tmp_path, ["kelt/03-04-31.txt", "kelt/13-04-31.txt", "kelt/22-06-07.txt"])
    settled = _settled(project)
    assert settled["kelt/03-04-31.txt"]["settled_by"] == [
        {"neighbour": "series", "path": "kelt/13-04-31.txt"}
    ]
    assert settled["kelt/22-06-07.txt"]["settled_by"] == [
        {"neighbour": "series", "path": "kelt/03-04-31.txt"}
    ]


def test_a_sibling_whose_every_reading_is_after_the_reading_day_rules_nothing(
    tmp_path: Path,
) -> None:
    """``10-25-26``, read in September 2026, is only 25 October 2026: a code or a plan, no clock, so
    it settles nothing for its neighbours.
    """
    project = _census(
        tmp_path,
        ["kelt/03-04-26.txt", "kelt/10-25-26.txt"],
        years=(2013, 2026),
        day="2026-09-29",
    )
    assert _clocks(project)["kelt/03-04-26.txt"] is None
    assert _settled(project) == {}


def test_the_series_reading_is_a_setting_a_project_can_switch_off(tmp_path: Path) -> None:
    """``dictionary.date_series: off``: no series settles a date; a dated folder still does."""
    project = _census(
        tmp_path,
        ["brisa/03-04-31_kelt.txt", "brisa/13-04-31_kelt.txt"],
        dictionary={"date_series": "off"},
    )
    assert _clocks(project)["brisa/03-04-31_kelt.txt"] is None
    # Tokeniser 14.
    assert Normaliser(STORE, date_series="off").version == "14-date_series_off"


# -- a day and a month that could swap: both readings, never the month-first one -----------------

#: The years a laboratory active since 2013 names, read in 2026.
LAB = Normaliser(STORE, years=(2013, 2026))


def test_an_ambiguous_date_names_both_readings_and_chooses_neither() -> None:
    """An ambiguous date names both readings and chooses neither.

    A single month-first value would be a guess, and every reader of the component would take it as
    the day. The two readings are written in the name's field order: the first field read as the
    month, then as the day, so a spelling and its mirror stay two values.
    """
    for name, both in (
        ("kelt 08.05.2022", "2022-08-05 or 2022-05-08"),
        ("kelt 05-09-22", "2022-05-09 or 2022-09-05"),
        ("vorn_02-01-16", "2016-02-01 or 2016-01-02"),
        ("kelt 06.05.22", "2022-06-05 or 2022-05-06"),
        ("kelt 05.06.22", "2022-05-06 or 2022-06-05"),
    ):
        (atom,) = [atom for atom in LAB.atoms(name) if atom.kind.value == "date"]
        assert atom.ambiguous
        assert atom.normalised == both
        assert tuple(sorted(atom.alternatives)) == tuple(sorted(both.split(" or ")))


def test_a_swap_and_its_mirror_are_two_values(tmp_path: Path) -> None:
    """Sorted, ``05.06.22`` and ``06.05.22`` would be one value: one search token, one grouping
    value, and two dates in one name one component.

    In field order they are two.
    """
    two = [atom.normalised for atom in LAB.atoms("kelt 05.06.2022 to 06.05.2022") if atom.ambiguous]
    assert two == ["2022-05-06 or 2022-06-05", "2022-06-05 or 2022-05-06"]
    # Four-digit years: these census years would let a two-digit one lead as well.
    project = _census(tmp_path, ["kelt/05.06.2022 run.txt", "kelt/06.05.2022 run.txt"])
    with open_catalog(project.path) as catalog:
        values = dict(
            catalog.query(
                "SELECT e.name, c.normalized_value FROM components c JOIN entries e "
                "USING (entry_id) WHERE c.component_type = 'date'"
            ).fetchall()
        )
    assert values["05.06.2022 run.txt"] != values["06.05.2022 run.txt"]


def test_an_ambiguous_date_is_no_day_downstream(tmp_path: Path) -> None:
    """Its component is no clock and no name date, so two spellings of one swap are never taken
    for replicates of one day on a guess."""
    project = _census(tmp_path, ["zib/kelt 02-01-16.txt", "trell/kelt 02.01.2016.txt"])
    with open_catalog(project.path) as catalog:
        rows = (
            catalog.query(
                "SELECT c.*, e.relative_path AS path FROM components c "
                "JOIN entries e ON e.entry_id = c.entry_id WHERE c.component_type = 'date'"
            )
            .to_arrow_table()
            .to_pylist()
        )
    assert {row["path"]: row["normalized_value"] for row in rows} == {
        "zib/kelt 02-01-16.txt": "2016-02-01 or 2016-01-02",
        "trell/kelt 02.01.2016.txt": "2016-02-01 or 2016-01-02",
    }
    assert _clocks(project) == {"zib/kelt 02-01-16.txt": None, "trell/kelt 02.01.2016.txt": None}
    for row in rows:
        del row["path"]
    assert name_dates([record_from_row(row, Component) for row in rows]) == ()


def test_a_swap_is_still_a_day_to_a_folder_dated_by_its_name() -> None:
    """Search's fallback reads a folder named with a day as dated; a swap names one of two."""
    from ingence.search.scale import day_precise

    assert day_precise("2016-01-02")
    assert day_precise("2016-01-02 or 2016-02-01")
    assert not day_precise("2016-01")
