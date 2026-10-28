"""Tokeniser 9: every class in one reader version, each over a grammar.

The years a person names before a run (default 2013 to the reading run's own year) gate every date a
name spells, the six-digit ``YYMMDD`` form included, and a number outside them is a number. A short
date that two field orders allow is not a date on its own (a neighbour may settle it later). A name
date after the reading is not a clock. A key-value label (``run-02``) is one indexed component. A
compressed format's suffix is that format, compressed. An escaped space before a number is a space.
A list of numbers is not a date. A trailing minus is a sign only after a word the Dictionary marks
as bearing one. A run divided at a capital marks every piece with the whole spelling it came from,
with no cut made inside an acronym. Every name here is invented.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.core.settings import Settings
from ingence.dictionary.discover import event_time_from_atoms, read_name, split_extension
from ingence.dictionary.normalize import NORMALIZER_VERSION, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
#: The years a laboratory active since 2013 names, read in 2026.
LAB = Normaliser(STORE, years=(2013, 2026))
#: Years wide enough that a short date's two field orders both fall inside them.
WIDE = Normaliser(STORE, years=(2000, 2035))


def _dates(normaliser: Normaliser, name: str) -> list[str]:
    return [atom.normalised for atom in normaliser.atoms(name) if atom.kind is AtomKind.DATE]


def _read(normaliser: Normaliser, name: str) -> list[tuple[str, str]]:
    return [(atom.text, atom.kind.value) for atom in normaliser.atoms(name)]


def test_the_version_names_this_bump() -> None:
    assert NORMALIZER_VERSION == "14"  # 14 since 0.4.0
    assert LAB.version == "14"


# -- the years ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "numbers"),
    [
        ("kelt_7-3-2009", ["7", "3", "2009"]),  # a sample's number shaped like a date, before
        ("2009-03-07_kelt", ["2009", "03", "07"]),  # the ISO shape, before
        ("20090307_vorn", ["20090307"]),  # compact, before
        ("zib_2031-03-07", ["2031", "03", "07"]),  # after the last year
        ("brisa_4-6-31", ["4", "6", "31"]),  # a two-digit year after the last
    ],
)
def test_a_number_outside_the_years_is_a_number(name: str, numbers: list[str]) -> None:
    assert _dates(LAB, name) == []
    assert [text for text, kind in _read(LAB, name) if kind == "number"] == numbers


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kelt_2019-03-07", "2019-03-07"),
        ("20130101_vorn", "2013-01-01"),  # the first year is inside
        ("zib_2026-12-31", "2026-12-31"),  # and the last
        ("brisa_17Jun2024", "2024-06-17"),  # a month's word
    ],
)
def test_a_date_inside_the_years_is_a_date(name: str, expected: str) -> None:
    assert _dates(LAB, name) == [expected]


def test_the_years_are_a_first_and_a_last_in_order() -> None:
    with pytest.raises(ValueError, match="years"):
        Normaliser(STORE, years=(2026, 2013))


def test_the_default_years_run_from_2013_to_the_reading_runs_own_year() -> None:
    settings = Settings().dictionary
    assert tuple(settings.years) == (2013, "present")
    assert settings.years_read_on("2026-09-28T01:02:03Z") == (2013, 2026)
    assert settings.years_read_on("2031-01-01T00:00:00Z") == (2013, 2031)


def test_named_years_are_read_as_named() -> None:
    settings = Settings.model_validate({"dictionary": {"years": [2001, 2010]}}).dictionary
    assert settings.years_read_on("2026-09-28T01:02:03Z") == (2001, 2010)
    with pytest.raises(ValueError, match="years"):
        Settings.model_validate({"dictionary": {"years": [2010, 2001]}})


def test_an_old_projects_plausible_years_still_load_and_are_not_the_gate() -> None:
    """A project written before 1.22 dumped ``plausible_years`` into its settings."""
    settings = Settings.model_validate({"dictionary": {"plausible_years": [1990, 2035]}}).dictionary
    assert settings.years_read_on("2026-09-28T01:02:03Z") == (2013, 2026)


# -- six digits: YYMMDD ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("230512_kelt", ["2023-05-12"]),
        ("vorn_170823_zib", ["2017-08-23"]),
        ("brisa170823", ["2017-08-23"]),  # glued to a word
        ("090412_kelt", []),  # 2009: before the years, a number
        ("123456_kelt", []),  # no calendar holds month 34
        ("kelt_20240315_140312", ["2024-03-15"]),  # a time after its date, not a second date
    ],
)
def test_six_digits_are_a_date_only_as_year_month_day_inside_the_years(
    name: str, expected: list[str]
) -> None:
    assert _dates(LAB, name) == expected


# -- a short date two field orders allow ------------------------------------------------------


@pytest.mark.parametrize("name", ["22-06-07_kelt_zib_01.nd2", "vorn_31-12-22.tif", "kelt 19.03.17"])
def test_a_short_date_two_orders_allow_is_no_date_alone(name: str) -> None:
    assert _dates(WIDE, name) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("22-06-07_kelt", "2022-06-07"),  # year first; 2007 is before the years
        ("vorn_31-12-22", "2022-12-31"),  # year last; 2031 is after them
    ],
)
def test_a_short_date_the_years_allow_one_way_is_that_date(name: str, expected: str) -> None:
    assert _dates(LAB, name) == [expected]


@pytest.mark.parametrize("name", ["kelt_22-6-7", "vorn_19-2-4", "zib_17-11-3", "brisa_16-3-12"])
def test_a_year_leads_only_before_a_two_digit_month_and_day(name: str) -> None:
    """``19-2-4`` is a sample's label far more often than 4 February 2019: a year written first is
    followed by a month and a day of two digits each, as a date that sorts is written."""
    assert _dates(LAB, name) == []


@pytest.mark.parametrize("name", ["kelt_2024-11-31", "vorn_20250230", "zib_31-04-2019"])
def test_a_day_no_month_holds_is_no_date(name: str) -> None:
    assert _dates(LAB, name) == []


def test_a_day_and_month_that_could_swap_stay_one_ambiguous_date() -> None:
    (atom,) = [atom for atom in LAB.atoms("kelt_01-02-26") if atom.kind is AtomKind.DATE]
    assert atom.ambiguous
    assert set(atom.alternatives) == {"2026-01-02", "2026-02-01"}
    assert atom.normalised == "2026-01-02 or 2026-02-01"  # both readings, neither chosen (v10)


# -- a name date after the reading is no clock ------------------------------------------------


def test_a_name_date_after_the_reading_is_no_clock() -> None:
    atoms = LAB.atoms("kelt_2026-11-02")
    assert event_time_from_atoms(atoms, latest=date(2026, 9, 28))[0] is None
    assert event_time_from_atoms(atoms, latest=date(2026, 11, 2))[0] is not None


def test_the_reading_day_reaches_the_clock_through_the_census(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_2026-03-04.txt").write_text("a")
    (pool / "vorn_2026-02-01.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260201T000001Z_111111111111", started_at="2026-02-01T00:00:01Z"
    ).published
    with open_catalog(project.path) as catalog:
        clocks = dict(catalog.query("SELECT name, event_time_ns FROM entries").fetchall())
    assert clocks["kelt_2026-03-04.txt"] is None
    assert clocks["vorn_2026-02-01.txt"] is not None


def test_the_run_record_states_the_years_it_read(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_2005-03-04.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["years"] = [2001, 2010]
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000002Z_222222222222",
        started_at="2026-01-01T00:00:02Z",
    ).published
    with open_catalog(project.path) as catalog:
        years = catalog.query("SELECT date_years_json FROM run").fetchall()
        dates = catalog.query(
            "SELECT normalized_value FROM components WHERE component_type = 'date'"
        ).fetchall()
    assert [tuple(json.loads(row[0])) for row in years] == [(2001, 2010)]
    assert dates == [("2005-03-04",)]


# -- a key-value label ------------------------------------------------------------------------


def test_a_key_value_label_is_one_indexed_component() -> None:
    atoms = {atom.text: atom for atom in LAB.atoms("sub-01_task-zib_run-02_bold")}
    assert atoms["run-02"].kind is AtomKind.INDEXED
    assert atoms["run-02"].normalised == "run2"
    assert atoms["sub-01"].kind is AtomKind.INDEXED
    assert atoms["zib"].kind is AtomKind.WORD


@pytest.mark.parametrize("name", ["kelt_WT-1", "kelt_Vorn-2", "kelt_ZIB-3"])
def test_a_key_written_with_a_capital_is_a_word_and_its_number(name: str) -> None:
    """``WT-1``, ``Vorn-2``: a condition or a reagent and its replicate, as tokeniser 8 read it."""
    word, number = name.split("_")[1].split("-")
    assert _read(LAB, name) == [("kelt", "word"), (word, "word"), (number, "number")]


def test_a_lower_case_key_is_one_label_whatever_the_dictionary_knows_it_as() -> None:
    """``run`` is a word the Dictionary knows; written as a key it is the label it labels."""
    assert ("run-3", "indexed") in _read(LAB, "sub-2_run-3_zib")
    assert ("jan-2024", "indexed") not in _read(LAB, "kelt-2_jan-2024")


def test_one_lower_case_pair_alone_is_a_word_and_its_number() -> None:
    """``kelt-2``: as often a reagent and its replicate as a key and its value."""
    assert _read(LAB, "zib_kelt-2") == [("zib", "word"), ("kelt", "word"), ("2", "number")]


# -- a compressed format's suffix --------------------------------------------------------------


def _entry(name: str, extension: str, compound: str):
    from ingence.core.schema import Entry, EntryType

    return Entry.model_construct(
        entry_id="e",
        name=name,
        relative_path=name,
        extension=extension,
        compound_extension=compound,
        entry_type=EntryType.FILE,
    )


def test_a_compressed_formats_suffix_binds_that_format() -> None:
    reading = read_name(
        _entry("zib_kelt.nii.gz", "gz", "nii.gz"),
        run_id="run_20260101T000001Z_111111111111",
        normaliser=LAB,
        store=STORE,
    )
    plain = read_name(
        _entry("zib_kelt.nii", "nii", ""),
        run_id="run_20260101T000001Z_111111111111",
        normaliser=LAB,
        store=STORE,
    )
    (compressed,) = [b for b in reading.bindings if b.constraint == "extension:format"]
    (bare,) = [b for b in plain.bindings if b.constraint == "extension:format"]
    assert compressed.term == bare.term is not None


def test_a_declared_compound_keeps_its_own_format() -> None:
    """``tar.gz`` is declared whole: a compressed archive, not a tar file compressed."""
    stem, extension = split_extension(_entry("kelt.tar.gz", "gz", "tar.gz"), store=STORE)
    assert (stem, extension) == ("kelt", "tar.gz")


# -- an escaped space before a number ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kelt%2045min2", [("kelt", "word"), ("45min", "quantity"), ("2", "number")]),
        ("vorn_2%2010min", [("vorn", "word"), ("2", "number"), ("10min", "quantity")]),
    ],
)
def test_an_escaped_space_before_a_number_is_a_space(
    name: str, expected: list[tuple[str, str]]
) -> None:
    assert [(text.replace("%20", ""), kind) for text, kind in _read(LAB, name)] == expected


# -- a list of numbers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["kelt_1-2-15-16", "vorn_3.4.17.18", "zib_1,2,3", "kelt_1-2-3", "brisa v1.2.13"]
)
def test_a_list_of_numbers_is_no_date(name: str) -> None:
    assert _dates(LAB, name) == []


def test_a_version_after_its_marker_is_a_version() -> None:
    assert ("v1.2.13", "version") in _read(LAB, "brisa v1.2.13")


def test_a_date_alone_is_still_a_date() -> None:
    assert _dates(LAB, "kelt_3-17-15") == ["2015-03-17"]


# -- a trailing minus ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "word"),
    [("kelt_GFP-_zib", "GFP-"), ("zib GFP-", "GFP-"), ("kelt_mCherry-.tif", "mCherry-")],
)
def test_a_trailing_minus_is_a_sign_after_a_word_that_bears_one(name: str, word: str) -> None:
    assert (word, "word") in _read(LAB, name)


@pytest.mark.parametrize("name", ["kelt-_zib", "GFP-kelt", "vorn_GFP-2"])
def test_a_trailing_minus_is_a_divider_anywhere_else(name: str) -> None:
    assert all(not text.endswith("-") for text, _ in _read(LAB, name))


# -- a run divided at a capital ------------------------------------------------------------------


def _pieces(name: str) -> list[tuple[str, str | None]]:
    return [(atom.text, atom.case_whole) for atom in LAB.atoms(name)]


def test_every_piece_of_a_divided_run_carries_the_whole_spelling() -> None:
    assert _pieces("zibKelt_rawFRAP") == [
        ("zib", "zibKelt"),
        ("Kelt", "zibKelt"),
        ("raw", "rawFRAP"),
        ("FRAP", "rawFRAP"),
    ]


def test_a_run_read_as_one_piece_carries_no_whole() -> None:
    assert _pieces("VoRn_mRNA_ZibKT") == [("VoRn", None), ("mRNA", None), ("ZibKT", None)]


@pytest.mark.parametrize(
    ("name", "pieces"),
    [
        ("ZIBdata", ["ZIB", "data"]),  # capitals, then a word the Dictionary knows in lower case
        ("SubXTreated", ["SubX", "Treated"]),  # capitals, then a capitalised word it knows
        ("ZIBylate", ["ZIBylate"]),  # neither: no cut inside the acronym
        ("brisaZIBylate", ["brisa", "ZIBylate"]),
        ("ssZIBkelt", ["ssZIBkelt"]),
    ],
)
def test_no_cut_is_made_inside_an_acronym(name: str, pieces: list[str]) -> None:
    assert [text for text, _ in _pieces(name)] == pieces


@pytest.mark.parametrize(
    ("name", "pieces"),
    [
        ("WTvornKelt", ["WT", "vorn", "Kelt"]),  # a known two-letter tag leads
        ("brisaWT", ["brisa", "WT"]),  # and ends
        ("CaVornKelt", ["CaVorn", "Kelt"]),  # a short piece that is no tag still joins
    ],
)
def test_a_short_known_tag_is_not_glued_onto_its_neighbour(name: str, pieces: list[str]) -> None:
    assert [text for text, _ in _pieces(name)] == pieces


def test_the_slots_carry_the_whole_spelling(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "zibKelt_2.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    with open_catalog(project.path) as catalog:
        (slots,) = catalog.query(
            "SELECT slots_json FROM name_shapes n JOIN entries e ON e.entry_id = n.entry_id "
            "WHERE e.name = 'zibKelt_2.txt'"
        ).fetchone()
    assert [slot.get("case_whole") for slot in json.loads(slots)] == [
        "zibKelt",
        "zibKelt",
        None,
        None,
    ]


# -- a short date two orders allow, read by its folder ------------------------------------------


def _clock(project: Project, name: str) -> int | None:
    with open_catalog(project.path) as catalog:
        (clock,) = catalog.query(
            "SELECT event_time_ns FROM entries WHERE name = ?", [name]
        ).fetchone()
    return clock


def test_a_folders_date_settles_a_short_date_two_orders_allow(tmp_path: Path) -> None:
    """``22-06-17`` is 17 June 2022 or 22 June 2017; a folder dated 2022 says which."""
    pool = tmp_path / "pool"
    (pool / "20220917_kelt" / "zib").mkdir(parents=True)
    (pool / "20220917_kelt" / "zib" / "22-06-17_vorn.txt").write_text("a")
    (pool / "brisa").mkdir()
    (pool / "brisa" / "22-06-17_vorn.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["years"] = [2013, 2026]
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000001Z_111111111111",
        started_at="2026-01-01T00:00:01Z",
    ).published
    with open_catalog(project.path) as catalog:
        rows = dict(catalog.query("SELECT relative_path, event_time_ns FROM entries").fetchall())
    settled = rows["20220917_kelt/zib/22-06-17_vorn.txt"]
    assert settled is not None
    assert datetime.fromtimestamp(settled / 1e9, tz=UTC).date() == date(2022, 6, 17)
    assert rows["brisa/22-06-17_vorn.txt"] is None  # no neighbour: no date


def test_the_two_order_readings_are_the_names_own() -> None:
    assert WIDE.two_order_dates("22-06-07_kelt") == (("2007-06-22", "2022-06-07"),)
    assert LAB.two_order_dates("22-06-07_kelt") == ()


# -- a day, a month's word and a year with a separator on one side only ----------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kelt 14 Feb2025_zib", "2025-02-14"),  # a space before the month's word, none after
        ("vorn_14Feb 2025", "2025-02-14"),  # none before, a space after
        ("brisa_3Mar-2022", "2022-03-03"),
        ("zib 9 Feb 2023", "2023-02-09"),  # the same on both sides, as version 8 read it
    ],
)
def test_a_month_word_date_takes_a_separator_on_either_side_alone(name: str, expected: str) -> None:
    assert _dates(LAB, name) == [expected]


def test_two_different_separators_round_a_month_word_are_still_no_date() -> None:
    """Tokeniser 8's case, unchanged: ``17-Jun_2024`` holds a word between two numbers."""
    assert _dates(LAB, "kelt_17-Jun_2024") == []


# -- a query is read with the years its run read the names with ------------------------------------


def test_a_query_reads_dates_with_the_years_and_day_of_the_run_that_read_the_names(
    tmp_path: Path,
) -> None:
    """Search tokenises a query as the census tokenised the names, years and reading day included,
    through an inference run that walks nothing, so a date the census did not read is no date in
    a query either."""
    from ingence.api.routes.search import _reader
    from ingence.infer.run import run_inference

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_2005-03-04.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["years"] = [2001, 2010]
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000001Z_111111111111",
        started_at="2026-01-01T00:00:01Z",
    ).published
    run_inference(
        Project.open(project.path),
        run_id="run_20260101T000002Z_222222222222",
        started_at="2026-01-01T00:00:02Z",
    )
    with open_catalog(project.path) as catalog:
        normaliser, _ = _reader(catalog, project.path)
    assert normaliser.years == (2001, 2010)
    assert normaliser.read_on == date(2026, 1, 1)
    assert _dates(normaliser, "zib_2012-03-04") == []


# -- a partial rebind across years --------------------------------------------------------------


def test_a_partial_rebind_is_refused_where_the_years_moved(tmp_path: Path) -> None:
    """A partial rebind carries names read with the stored run's years; after New Year's Day, or
    a person editing ``dictionary.years``, those differ from this run's, and the carried and the
    re-read names would be read two ways in one run."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt_zib.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20261231T000001Z_111111111111", started_at="2026-12-31T00:00:01Z"
    ).published
    with pytest.raises(ValueError, match="years"):
        run_census(
            Project.open(project.path),
            run_id="run_20270101T000002Z_222222222222",
            started_at="2027-01-01T00:00:02Z",
            rebind=True,
            spellings=("zib",),
        )
    same_year = run_census(
        Project.open(project.path),
        run_id="run_20261231T000003Z_333333333333",
        started_at="2026-12-31T00:00:03Z",
        rebind=True,
        spellings=("zib",),
    )
    assert same_year.published


# -- six digits: a named setting ------------------------------------------------------------------


def test_six_digits_read_year_first_is_a_setting_a_project_can_turn_off() -> None:
    """A six-digit code is read year first only (a day-first code would read as another real date,
    unmarked): the reading is ``dictionary.six_digit_dates``, ``year_first`` or ``none``."""
    assert Settings().dictionary.six_digit_dates == "year_first"
    off = Normaliser(STORE, years=(2013, 2026), six_digit_dates="none")
    assert _dates(off, "240807_kelt") == []
    assert _dates(off, "20240807_kelt") == ["2024-08-07"]  # eight digits are unaffected
    assert off.version == "14-six_digit_none"
    with pytest.raises(ValueError, match="six_digit_dates"):
        Normaliser(STORE, six_digit_dates="day_first")


def test_the_six_digit_setting_reaches_the_census(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "240807_kelt.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["six_digit_dates"] = "none"
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    assert run_census(
        Project.open(project.path),
        run_id="run_20260101T000001Z_111111111111",
        started_at="2026-01-01T00:00:01Z",
    ).published
    with open_catalog(project.path) as catalog:
        kinds = catalog.query(
            "SELECT component_type FROM components WHERE raw_value = '240807'"
        ).fetchall()
    assert kinds == [("number",)]


# -- February's length, and a lab a day ahead of UTC ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"), [("kelt_2024-02-29", ["2024-02-29"]), ("kelt_2025-02-29", [])]
)
def test_the_twenty_ninth_of_february_is_a_date_only_in_a_leap_year(
    name: str, expected: list[str]
) -> None:
    assert _dates(LAB, name) == expected


def test_a_name_date_one_day_after_the_reading_day_is_still_a_clock() -> None:
    """The reading day is the run's UTC day; a laboratory east of UTC may name a file tomorrow."""
    assert event_time_from_atoms(LAB.atoms("kelt_2026-09-29"), latest=date(2026, 9, 28))[0]
    assert event_time_from_atoms(LAB.atoms("kelt_2026-09-30"), latest=date(2026, 9, 28))[0] is None


def test_how_many_key_value_pairs_make_a_convention_is_a_setting() -> None:
    assert Settings().dictionary.key_value_pairs == 2
    one = Normaliser(STORE, years=(2013, 2026), key_value_pairs=1)
    assert ("kelt-2", "indexed") in _read(one, "zib_kelt-2")
    assert one.version == "14-key_value_pairs_1"
    with pytest.raises(ValueError, match="key_value_pairs"):
        Normaliser(STORE, key_value_pairs=0)
