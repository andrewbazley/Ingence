"""Tokeniser 12: a C-library stamp's date is its name's clock, and a stamp reads its weekday
whatever the Record says of the word.

A name holding a stamp and another date (a sample sheet's, a folder's) has the stamp's date as its
clock, because the stamp is the program's own write time. The other date stays a date in the name,
never its clock. A stamp whose weekday disagrees with its calendar is not a stamp, as under
tokeniser 11, so its name's other date is its clock.

A stamp's words are the C library's. A person inactivating a weekday or a month as a word of their
laboratory's names leaves a stamp reading as one. Every name here is invented, and every stamp is
generated from ``asctime``.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from ingence.api.routes.dictionary import inactivate
from ingence.core.outcomes import TimeSource
from ingence.core.project import Project
from ingence.dictionary.discover import event_time_from_atoms
from ingence.dictionary.normalize import NORMALIZER_VERSION, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
#: The years a laboratory active since 2013 names, read in 2026.
LAB = Normaliser(STORE, years=(2013, 2026))


def _stamp(moment: datetime, space: str = "_", colon: str = "-") -> str:
    """What ``asctime`` writes for a moment, with a name's replacements for spaces and colons."""
    return time.asctime(moment.timetuple()).replace(" ", space).replace(":", colon)


def _midnight(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * 1_000_000_000


def _clock(normaliser: Normaliser, name: str) -> tuple[int | None, TimeSource]:
    return event_time_from_atoms(normaliser.atoms(name))


def _dates(normaliser: Normaliser, name: str) -> list[str]:
    return [atom.normalised for atom in normaliser.atoms(name) if atom.kind is AtomKind.DATE]


# -- 27. a stamp's date wins -----------------------------------------------------------------------

#: How a name writes another date beside its stamp: an ISO date with either separator, eight
#: digits, and a day, a month's word and a year (``{d}`` a ``date``).
OTHER_DATES = (
    lambda d: d.strftime("%Y-%m-%d"),
    lambda d: d.strftime("%Y_%m_%d"),
    lambda d: d.strftime("%Y%m%d"),
    lambda d: f"{d.day}{d.strftime('%b')}{d.year}",
)
#: Where the other date sits: before the stamp or after it, a word between them.
LAYOUTS = ("sheet_{other}_run_{stamp}_log", "run_{stamp}_log_{other}")
#: The stamp's spaces and colons as names write them.
STAMP_WRITINGS = (("_", "-"), ("_", "."), (" ", "-"), ("-", "_"))
#: Clocks, one a calendar could read as a short date.
CLOCKS = ((14, 7, 45), (22, 11, 8), (9, 5, 17))


def _stamped_with_another_date() -> list[tuple[str, date, date]]:
    """Every stamp with another date on another day, and the two days."""
    cases = []
    for year in (2014, 2019, 2025):
        for month, day in ((3, 9), (6, 17), (12, 1)):
            for hour, minute, second in CLOCKS:
                moment = datetime(year, month, day, hour, minute, second)
                for days_before in (1, 40, 400):
                    other = moment.date() - timedelta(days=days_before)
                    for space, colon in STAMP_WRITINGS:
                        stamp = _stamp(moment, space, colon)
                        for written in OTHER_DATES:
                            for layout in LAYOUTS:
                                name = layout.format(other=written(other), stamp=stamp)
                                cases.append((name, moment.date(), other))
    return cases


STAMPED = _stamped_with_another_date()


def test_the_generated_class_is_large_and_holds_two_dates_each() -> None:
    """The premise: every name reads both its stamp's date and its other date as dates."""
    assert len(STAMPED) > 2_000
    wrong = [
        (name, _dates(LAB, name))
        for name, stamped, other in STAMPED
        if sorted(_dates(LAB, name)) != sorted({stamped.isoformat(), other.isoformat()})
    ]
    assert wrong == []


def test_a_stamps_date_is_its_names_clock_over_another_date() -> None:
    wrong = [
        (name, _clock(LAB, name))
        for name, stamped, _ in STAMPED
        if _clock(LAB, name) != (_midnight(stamped), TimeSource.FILENAME)
    ]
    assert wrong == []


def test_the_other_date_stays_a_date_in_the_name() -> None:
    for name, _, other in STAMPED[:: max(1, len(STAMPED) // 200)]:
        assert other.isoformat() in _dates(LAB, name), name


def test_a_stamp_whose_weekday_disagrees_is_no_stamp_and_the_other_date_is_the_clock() -> None:
    """As under tokeniser 11: a weekday the calendar does not give that day leaves the stamp
    undated, so the name holds one date, the other one, and that is its clock."""
    wrong = []
    for name, stamped, other in STAMPED:
        weekday = stamped.strftime("%a")
        wrong_day = (stamped + timedelta(days=1)).strftime("%a")
        disagreeing = name.replace(weekday, wrong_day, 1)
        if _dates(LAB, disagreeing) != [other.isoformat()]:
            wrong.append((disagreeing, "dates", _dates(LAB, disagreeing)))
        elif _clock(LAB, disagreeing) != (_midnight(other), TimeSource.FILENAME):
            wrong.append((disagreeing, "clock", _clock(LAB, disagreeing)))
    assert wrong == []


def test_two_stamps_on_different_days_give_no_clock() -> None:
    """Two write times in one name: the name has not said which is its own."""
    first = _stamp(datetime(2019, 3, 9, 14, 7, 45))
    second = _stamp(datetime(2019, 3, 12, 9, 5, 17))
    assert _clock(LAB, f"run_{first}_to_{second}_{date(2019, 3, 1):%Y-%m-%d}") == (
        None,
        TimeSource.UNAVAILABLE,
    )


def test_one_stamp_written_twice_is_one_clock() -> None:
    stamp = _stamp(datetime(2019, 3, 9, 14, 7, 45))
    assert _clock(LAB, f"run_{stamp}_copy_{stamp}_2019-01-02") == (
        _midnight(date(2019, 3, 9)),
        TimeSource.FILENAME,
    )


def test_a_stamp_no_reading_admits_is_no_stamp_and_the_other_date_is_the_clock() -> None:
    """One behaviour for every stamp the reader cannot take as a write time.

    Its weekday disagrees (above), its year is before the years, or it falls after the reading day.
    Each is not a stamp, so the name's other date is its clock. Alone, such a stamp gives no clock.
    """
    late = _stamp(datetime(2026, 3, 9, 14, 7, 45))
    reading_day = date(2026, 1, 5)
    atoms = LAB.atoms(f"run_{late}_sheet_2025-12-01")
    assert event_time_from_atoms(atoms, latest=reading_day) == (
        _midnight(date(2025, 12, 1)),
        TimeSource.FILENAME,
    )
    assert event_time_from_atoms(LAB.atoms(f"run_{late}_log"), latest=reading_day) == (
        None,
        TimeSource.UNAVAILABLE,
    )
    early = _stamp(datetime(2012, 3, 9, 1, 2, 3))
    assert _clock(LAB, f"run_{early}_sheet_2019-01-02") == (
        _midnight(date(2019, 1, 2)),
        TimeSource.FILENAME,
    )
    assert _clock(LAB, f"run_{early}_log") == (None, TimeSource.UNAVAILABLE)


def test_a_name_without_a_stamp_still_abstains_on_two_dates() -> None:
    assert _clock(LAB, "sheet_2019-01-02_run_2019-03-09") == (None, TimeSource.UNAVAILABLE)


def _census(project: Project, run: int) -> str:
    report = run_census(
        Project.open(project.path),
        run_id=f"run_20260101T00000{run}Z_{str(run) * 12}",
        started_at=f"2026-01-01T00:00:0{run}Z",
        rebind=run > 1,
    )
    assert report.published
    return report.run_id


def _clocks(project: Project) -> dict[str, int | None]:
    with open_catalog(project.path) as catalog:
        return dict(
            catalog.query(
                "SELECT name, event_time_ns FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        )


def test_a_census_takes_the_stamps_date_as_the_entrys_clock(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    stamp = _stamp(datetime(2021, 3, 9, 14, 7, 45))
    names = {
        f"kelt_2021-02-26_{stamp}_log.txt": date(2021, 3, 9),
        f"vorn_20210226_{stamp}_log.txt": date(2021, 3, 9),
        f"zib_2021-02-26_{stamp.replace('Tue', 'Wed')}_log.txt": date(2021, 2, 26),
    }
    for name in names:
        (pool / name).write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _census(project, 1)
    assert _clocks(project) == {name: _midnight(day) for name, day in names.items()}


# -- 28. a stamp reads its weekday regardless ------------------------------------------------------

#: One stamp per weekday, each a Tuesday-to-Monday week of one March.
WEEK = [datetime(2021, 3, day, 14, 7, 45) for day in range(9, 16)]


def _project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for moment in WEEK:
        (pool / f"run_{_stamp(moment)}_log.txt").write_text("a")
    (pool / "kelt_tuesday_brisa.txt").write_text("a")
    (pool / "zib_march_vorn.txt").write_text("a")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def _inactivate(project: Project, term: str, spelling: str | None = None) -> None:
    with open_catalog(project.path) as catalog:
        inactivate(
            catalog,
            project.path,
            actor="wren",
            occurred_at="2026-01-01T00:00:01Z",
            term=term,
            spelling=spelling,
        )


def _bound_terms(project: Project, token: str) -> set[str | None]:
    with open_catalog(project.path) as catalog:
        return {
            row[0]
            for row in catalog.query(
                "SELECT b.term FROM dictionary_bindings b JOIN components c USING (component_id) "
                "WHERE c.normalized_value = ? AND c.component_type = 'word'",
                [token],
            ).fetchall()
        }


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@pytest.mark.parametrize("term", WEEKDAYS)
def test_a_stamp_reads_its_weekday_with_the_weekday_inactive(tmp_path: Path, term: str) -> None:
    """The stamp's reading uses the shipped weekday senses whatever the Record says of the word:
    the project's Dictionary, read with the inactivation in force, has no such sense."""
    moment = next(m for m in WEEK if m.strftime("%A").casefold() == term)
    name = f"run_{_stamp(moment)}_log"
    project = _project(tmp_path)
    _census(project, 1)
    _inactivate(project, term)
    store = DictionaryStore.for_project(project.path)
    assert store.sense_for_term(term) is None
    assert store.look_up(term) is None
    assert _dates(Normaliser(store, years=(2013, 2026)), name) == [moment.date().isoformat()]


def test_a_census_after_inactivating_a_weekday_still_dates_its_stamps(tmp_path: Path) -> None:
    """Through the Record: a person inactivates Tuesday and March as words; the next rebind no
    longer binds either word alone, and every stamp still reads its date and gives its clock."""
    project = _project(tmp_path)
    _census(project, 1)
    before = _clocks(project)
    assert _bound_terms(project, "tuesday") == {"tuesday"}
    _inactivate(project, "tuesday")
    _inactivate(project, "march")
    _census(project, 2)
    assert _bound_terms(project, "tuesday") == {None}
    assert _bound_terms(project, "march") == {None}
    assert _clocks(project) == before
    for moment in WEEK:
        assert before[f"run_{_stamp(moment)}_log.txt"] == _midnight(moment.date())


def test_a_month_spelling_inactivated_leaves_a_stamp_reading(tmp_path: Path) -> None:
    """The spelling a stamp writes (``mar`` of March) inactivated as a word: the stamp still reads.

    A weekday's spelling never stops a stamp, since a stamp reads the abbreviation property, so the
    month's is the spelling that tests this.
    """
    project = _project(tmp_path)
    _census(project, 1)
    before = _clocks(project)
    _inactivate(project, "march", "mar")
    _census(project, 2)
    assert _bound_terms(project, "march") == {"march"}
    assert _clocks(project) == before


def test_a_stamp_reads_a_spelling_a_shipped_extension_adds_after_an_inactivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vocabulary a stamp reads is folded as the one in force is.

    So a spelling an extension adds to a weekday reads in a stamp whether or not anything is
    inactive.
    """
    import ingence.dictionary.store as store_module
    from ingence.dictionary.store import Section, Sense, load_shipped_sections

    tuesday = STORE.sense_for_term("tuesday")
    assert tuesday is not None
    extension = Section(
        name="zz_invented_extension",
        summary="an invented spelling of a weekday",
        senses=(
            Sense(
                term="tuesday",
                canonical=tuesday.canonical,
                theme_kind="date",
                section="zz_invented_extension",
                surfaces=("tuez",),
                properties={"extends": True},
            ),
        ),
    )
    shipped = (*load_shipped_sections(), extension)
    monkeypatch.setattr(store_module, "load_shipped_sections", lambda: shipped)
    name = "run_Tuez_Mar__9_14-07-45_2021_log"
    project = _project(tmp_path)
    reader = Normaliser(DictionaryStore.for_project(project.path), years=(2013, 2026))
    assert _dates(reader, name) == ["2021-03-09"]
    _census(project, 1)
    _inactivate(project, "june")
    reader = Normaliser(DictionaryStore.for_project(project.path), years=(2013, 2026))
    assert _dates(reader, name) == ["2021-03-09"]


def test_a_stamps_words_are_still_the_dictionarys() -> None:
    """No weekday or month is written into the reader: without a Dictionary no stamp is read."""
    assert _dates(Normaliser(None, years=(2013, 2026)), f"run_{_stamp(WEEK[0])}_log") == []


# -- the version: one whole rebind -----------------------------------------------------------------


def test_the_version_names_this_bump() -> None:
    assert NORMALIZER_VERSION == "14"  # 14 since 0.4.0
    assert LAB.version == "14"


def test_a_partial_rebind_is_refused_across_readers_11_and_12(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project read by tokeniser 11 needs one whole rebind, after which a name holding a stamp
    and another date has the stamp's clock."""
    import ingence.dictionary.normalize as normalize

    pool = tmp_path / "pool"
    pool.mkdir()
    stamped = f"kelt_2021-02-26_{_stamp(datetime(2021, 3, 9, 14, 7, 45))}_log.txt"
    for name in (stamped, "zib_vorn.txt"):
        (pool / name).write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    with monkeypatch.context() as patched:
        patched.setattr(normalize, "NORMALIZER_VERSION", "11")
        _census(project, 1)
    with pytest.raises(ValueError, match="tokeniser version 11 and this engine reads them as 14"):
        run_census(
            Project.open(project.path),
            run_id="run_20260101T000002Z_222222222222",
            started_at="2026-01-01T00:00:02Z",
            rebind=True,
            spellings=("zib",),
        )
    _census(project, 3)
    with open_catalog(project.path) as catalog:
        readers = catalog.query("SELECT DISTINCT normalizer_version FROM components").fetchall()
        (stated,) = catalog.query("SELECT normalizer_version FROM run").fetchone()
    assert (readers, stated) == ([("14",)], "14")
    assert _clocks(project)[stamped] == _midnight(date(2021, 3, 9))
