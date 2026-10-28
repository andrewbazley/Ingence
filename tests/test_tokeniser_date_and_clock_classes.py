"""Tokeniser 11: three date and clock classes, each over its grammar, never a name.

A date after numbers written with its separator is the date its bare form is: the last three fields
of a run of numbers joined by one separator, or after a letter-led token, where they are a day and a
month (either order) and a four-digit year inside the years. An ISO date is never cut.

A C-library time stamp (what ``ctime`` and ``asctime`` write, spaces and colons replaced as a name
must: ``Tue_Mar__9_14-07-45_2021``) is its own date, read from the weekday, the month's word, the
day and the year after the clock together. Its clock is a time and never a short date.

A clock written with dots after its date (a screenshot's ``at 10.41.27``) is a time, as tokeniser 8
read a clock a calendar could also read.

Every name here is invented, and every stamp is generated from its grammar.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from ingence.core.outcomes import TimeSource
from ingence.dictionary.discover import event_time_from_atoms
from ingence.dictionary.normalize import NORMALIZER_VERSION, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore

STORE = DictionaryStore.builtin()
#: The years a laboratory active since 2013 names, read in 2026.
LAB = Normaliser(STORE, years=(2013, 2026))
#: Years wide enough that a two-digit year at a run's head is a year too.
WIDE = Normaliser(STORE, years=(2000, 2035))
#: The years a normaliser reads when no run names them (1990 to 2035).
DEFAULT = Normaliser(STORE)


def _dates(normaliser: Normaliser, name: str) -> list[str]:
    return [atom.normalised for atom in normaliser.atoms(name) if atom.kind is AtomKind.DATE]


def _read(normaliser: Normaliser, name: str) -> tuple[tuple[str, str, str], ...]:
    return tuple((atom.text, atom.kind.value, atom.normalised) for atom in normaliser.atoms(name))


def test_the_version_names_this_bump() -> None:
    """Tokeniser 11 or a later one (12 reads a stamp's date as its name's clock)."""
    assert int(NORMALIZER_VERSION) >= 11
    assert LAB.version == NORMALIZER_VERSION


# -- 1. a date after numbers written with its separator --------------------------------------------

#: The separators a short date is written with in a name.
SEPARATORS = ("_", "-", ".")
#: Day and month pairs as written: a swap (both twelve or under), a first field only a day can
#: be, a second only a day can be, and one-digit fields.
FIELDS = (("12", "09"), ("17", "04"), ("04", "23"), ("3", "7"), ("1", "11"))
#: Four-digit years inside :data:`LAB`'s years.
FOUR_DIGIT_YEARS = ("2013", "2019", "2026")
#: What leads the date: one number, a longer number, two numbers, each written with the date's
#: separator between them (``{s}``).
NUMBER_LEADS = ("7", "731", "2{s}5")
#: A letter-led token: a label, a glued run, a construct-shaped run ending in digits.
LETTER_LEADS = ("kQ4", "zib12", "B7KL2")
#: What may follow the date: nothing, a letter with the date's separator, a letter after ``_``.
SUFFIXES = ("", "{s}b", "_b")


def _after_numbers() -> list[tuple[str, str]]:
    """Every lead, date, separator and suffix, with the bare form each must read as."""
    cases = []
    for separator in SEPARATORS:
        for first, second in FIELDS:
            for year in FOUR_DIGIT_YEARS:
                date = separator.join((first, second, year))
                for lead in (*NUMBER_LEADS, *LETTER_LEADS):
                    for suffix in SUFFIXES:
                        written = lead.format(s=separator) + separator + date
                        cases.append((f"scan_{written}{suffix.format(s=separator)}", date))
    return sorted(set(cases))


AFTER_NUMBERS = _after_numbers()


def test_the_generated_class_is_large_and_every_bare_form_is_a_date() -> None:
    assert len(AFTER_NUMBERS) > 700
    assert all(_dates(LAB, f"scan_{date}") for _, date in AFTER_NUMBERS)


def test_a_date_after_numbers_with_its_separator_reads_as_its_bare_form() -> None:
    wrong = [
        (name, _dates(LAB, name), _dates(LAB, f"scan_{date}"))
        for name, date in AFTER_NUMBERS
        if _dates(LAB, name) != _dates(LAB, f"scan_{date}")
    ]
    assert wrong == []


#: Letter-led tokens a plate, a position, a channel, a time point or a sample writes before its
#: indices, and the construct-shaped leads above.
INDEX_LEADS = ("A01", "A1", "pos3", "ch1", "t1", "sample1", *LETTER_LEADS)
#: Two-digit years a run of indices can end with, inside the default years.
TWO_DIGIT_YEARS = ("16", "01", "12")


def _after_a_token_with_a_two_digit_year() -> list[str]:
    return sorted(
        {
            f"{lead}{separator}{separator.join((first, second, year))}.tif"
            for lead in INDEX_LEADS
            for separator in SEPARATORS
            for first, second in (*FIELDS, ("16", "2"), ("1", "2"))
            for year in TWO_DIGIT_YEARS
        }
    )


AFTER_A_TOKEN = _after_a_token_with_a_two_digit_year()


def test_after_a_letter_led_token_a_two_digit_year_is_still_a_list() -> None:
    """After a letter-led token, as after numbers, only a four-digit year makes a date.

    A well and its indices (``A01_16_2_01``) stay numbers, and nothing of them is offered to a
    folder's series.
    """
    assert len(AFTER_A_TOKEN) > 300
    dated = [
        (name, reader.years)
        for name in AFTER_A_TOKEN
        for reader in (LAB, DEFAULT)
        if _dates(reader, name) or reader.short_dates(name)
    ]
    assert dated == []


#: Shapes of names, each with tokeniser 10's reading under the default years.
INDEX_PINS = (
    (
        "A01_16_2_01",
        (
            ("A01", "indexed", "a1"),
            ("16", "number", "16"),
            ("2", "number", "2"),
            ("01", "number", "1"),
        ),
    ),
    (
        "A01_1_2_01",
        (
            ("A01", "indexed", "a1"),
            ("1", "number", "1"),
            ("2", "number", "2"),
            ("01", "number", "1"),
        ),
    ),
    (
        "pos3_1_2_12",
        (
            ("pos3", "indexed", "pos3"),
            ("1", "number", "1"),
            ("2", "number", "2"),
            ("12", "number", "12"),
        ),
    ),
    (
        "well_A1_1_2_15",
        (
            ("well", "word", "well"),
            ("A1", "indexed", "a1"),
            ("1", "number", "1"),
            ("2", "number", "2"),
            ("15", "number", "15"),
        ),
    ),
    (
        "ch1_2_3_16",
        (
            ("ch1", "indexed", "ch1"),
            ("2", "number", "2"),
            ("3", "number", "3"),
            ("16", "number", "16"),
        ),
    ),
    (
        "t1_2_3_16",
        (
            ("t1", "indexed", "t1"),
            ("2", "number", "2"),
            ("3", "number", "3"),
            ("16", "number", "16"),
        ),
    ),
    (
        "sample1_2_3_16",
        (
            ("sample", "word", "sample"),
            ("1", "number", "1"),
            ("2", "number", "2"),
            ("3", "number", "3"),
            ("16", "number", "16"),
        ),
    ),
    (
        "scan_pQ731_12_09_16",
        (
            ("scan", "word", "scan"),
            ("pQ731", "indexed", "pq731"),
            ("12", "number", "12"),
            ("09", "number", "9"),
            ("16", "number", "16"),
        ),
    ),
)


@pytest.mark.parametrize(("name", "reading"), INDEX_PINS)
def test_indices_after_a_token_read_as_they_did(
    name: str, reading: tuple[tuple[str, str, str]]
) -> None:
    assert _read(DEFAULT, name) == reading
    assert DEFAULT.short_dates(name) == ()


def test_the_defects_own_shape_after_a_token_still_reads_its_date() -> None:
    assert _dates(LAB, "scan_pQ731_12_09_2016") == ["2016-12-09 or 2016-09-12"]


@pytest.mark.parametrize("separator", SEPARATORS)
def test_after_numbers_a_two_digit_year_is_still_a_list(separator: str) -> None:
    """A run of numbers ending in a two-digit year stays a list (``1-2-15-16``), as version 9 read
    it.

    Only a four-digit year is a date at a run's end.
    """
    for first, second in FIELDS:
        name = "scan_" + separator.join(("7", first, second, "16"))
        assert _dates(LAB, name) == []


def test_the_rightmost_complete_date_wins_where_the_head_could_start_one() -> None:
    """``3_12_09`` is a date under years reaching 2009, and ``12_09_2016`` is one under any.

    The run's last three fields are its date and the head is its numbers.
    """
    assert _dates(WIDE, "scan_3_12_09") == ["2009-03-12 or 2009-12-03"]
    assert _dates(WIDE, "scan_3_12_09_2016") == ["2016-12-09 or 2016-09-12"]
    numbers = [text for text, kind, _ in _read(WIDE, "scan_3_12_09_2016") if kind == "number"]
    assert numbers == ["3"]


def test_two_dates_written_back_to_back_are_both_read() -> None:
    assert _dates(LAB, "kelt_12_09_2016_17_04_2019") == ["2016-12-09 or 2016-09-12", "2019-04-17"]


def test_a_date_after_numbers_joins_its_folders_series() -> None:
    """A folder's series (tokeniser 10) sees the date after numbers as it sees the bare form."""
    assert LAB.short_dates("scan_7_12_09_2016") == LAB.short_dates("scan_12_09_2016")
    assert LAB.short_dates("scan_pQ7_12_09_2016") == LAB.short_dates("scan_12_09_2016")


# -- an ISO date is never cut to make a date after numbers -----------------------------------------

#: ISO dates each normaliser reads, by the year, month and day written.
ISO_DATES = {
    "LAB": (("2014", "03", "17"), ("2021", "11", "30"), ("2026", "03", "04")),
    "DEFAULT": (("1995", "03", "17"), ("2021", "11", "30"), ("2030", "12", "09")),
}
#: Four-digit numbers after the date: years inside either normaliser's span, and counters outside.
FOUR_DIGIT_AFTER = ("2015", "2020", "2000", "1990", "2035", "0001", "1234", "9999")
#: How the name holds the run: bare, after a word, before a word.
ISO_SHAPES = ("{r}", "kelt_{r}", "{r}_zib", "kelt.{r}")


def _iso_then_four_digits() -> list[tuple[str, str, str, tuple[tuple[str, str, str], ...]]]:
    """Every ISO date, separator and four-digit number, with tokeniser 10's reading of the run."""
    cases = []
    for which, dates in ISO_DATES.items():
        for separator in SEPARATORS:
            for year, month, day in dates:
                iso = separator.join((year, month, day))
                for number in FOUR_DIGIT_AFTER:
                    run = (
                        (iso, "date", f"{year}-{month}-{day}"),
                        (number, "number", str(int(number))),
                    )
                    for shape in ISO_SHAPES:
                        cases.append((which, shape.format(r=f"{iso}{separator}{number}"), iso, run))
    return cases


ISO_THEN_FOUR_DIGITS = _iso_then_four_digits()


def test_an_iso_date_followed_by_four_digits_keeps_its_date() -> None:
    """``2024-11-30-2020`` is a date and a counter, as tokeniser 10 read it: the run's last three
    fields (``11-30-2020``) are never cut out of the ISO date to make a second, wrong one."""
    readers = {"LAB": LAB, "DEFAULT": DEFAULT}
    assert len(ISO_THEN_FOUR_DIGITS) > 500
    wrong = []
    for which, name, iso, run in ISO_THEN_FOUR_DIGITS:
        reading = _read(readers[which], name)
        start = [text for text, _, _ in reading].index(iso) if iso in name else -1
        if start < 0 or reading[start : start + 2] != run:
            wrong.append((which, name, reading))
    assert wrong == []


def test_an_iso_date_followed_by_four_digits_is_never_offered_to_a_series() -> None:
    assert [
        name
        for which, name, _, _ in ISO_THEN_FOUR_DIGITS
        if (LAB if which == "LAB" else DEFAULT).short_dates(name)
    ] == []


@pytest.mark.parametrize("separator", SEPARATORS)
def test_an_iso_shape_outside_the_years_is_still_not_cut(separator: str) -> None:
    """An ISO shape whose year the years refuse (2009 under 2013-2026) is its numbers, as tokeniser
    10 read it.

    A four-digit counter led by a zero is no year, so what follows it may be a date after numbers.
    """
    name = separator.join(("kelt_2009", "03", "17", "2015"))
    assert _dates(LAB, name) == []
    assert LAB.short_dates(name) == ()
    counted = separator.join(("kelt_0001", "12", "09", "2016"))
    assert _dates(LAB, counted) == _dates(LAB, "kelt_" + separator.join(("12", "09", "2016")))


def test_dates_after_an_iso_date_and_numbers_are_both_read() -> None:
    assert _dates(LAB, "kelt_2021_03_04_12_09_2016") == ["2021-03-04", "2016-12-09 or 2016-09-12"]


# -- 2. a C-library time stamp ---------------------------------------------------------------------

#: Clocks a calendar could also read under :data:`LAB`'s years in at least one field order, and
#: some it could not: ``22-11-08`` reads year first, ``10-11-15`` as a day and a month swapped.
CLOCKS = ((22, 11, 8), (14, 7, 21), (10, 11, 15), (9, 5, 17), (0, 12, 13), (23, 59, 59), (1, 2, 3))
#: What a name writes for the stamp's spaces, and for its colons.
STAMP_SPACES = (" ", "_")
STAMP_COLONS = ("-", ".", "_", ":")


def _stamp(moment: datetime, space: str, colon: str) -> str:
    """What ``asctime`` writes for a moment, with a name's replacements for spaces and colons."""
    return time.asctime(moment.timetuple()).replace(" ", space).replace(":", colon)


def _stamps() -> list[tuple[str, datetime]]:
    cases = []
    for year in range(2013, 2027):
        for month in (3, 6, 12):
            for day in (9, 17):
                for hour, minute, second in CLOCKS:
                    moment = datetime(year, month, day, hour, minute, second)
                    for space in STAMP_SPACES:
                        for colon in STAMP_COLONS:
                            stamp = _stamp(moment, space, colon)
                            cases.append((f"run{space}{stamp}{space}log", moment))
    return cases


STAMPS = _stamps()


def test_the_stamp_class_is_generated_from_asctime() -> None:
    assert len(STAMPS) > 4_000
    assert _stamp(datetime(2021, 3, 9, 14, 7, 45), "_", "-") == "Tue_Mar__9_14-07-45_2021"


def test_a_time_stamp_is_its_own_date_and_its_clock_is_no_date() -> None:
    wrong = [
        (name, _dates(LAB, name))
        for name, moment in STAMPS
        if _dates(LAB, name) != [moment.date().isoformat()]
    ]
    assert wrong == []


def test_a_time_stamps_clock_and_year_are_read_as_numbers() -> None:
    wrong = []
    for name, moment in STAMPS:
        numbers = [atom.normalised for atom in LAB.atoms(name) if atom.kind is AtomKind.NUMBER]
        expected = [str(moment.hour), str(moment.minute), str(moment.second), str(moment.year)]
        if numbers != expected:
            wrong.append((name, numbers))
    assert wrong == []


def test_a_time_stamps_date_is_its_name_clock() -> None:
    """One date in the name, so the name's clock is the stamp's day (midnight UTC, as ever)."""
    atoms = LAB.atoms("run_Tue_Mar__9_22-11-08_2021_log")
    moment, source = event_time_from_atoms(atoms)
    assert source is TimeSource.FILENAME
    assert moment == int(datetime(2021, 3, 9, tzinfo=UTC).timestamp()) * 1_000_000_000


def test_a_stamp_whose_weekday_disagrees_reads_no_date_and_still_no_date_from_its_clock() -> None:
    """The weekday is read with the date, so a weekday the calendar does not give that day leaves
    the stamp undated.

    Its clock is still a clock.
    """
    wrong_day = _stamp(datetime(2021, 3, 9, 22, 11, 8), "_", "-").replace("Tue", "Mon")
    assert _dates(LAB, f"run_{wrong_day}_log") == []
    assert "22-11-08" not in [atom.text for atom in LAB.atoms(f"run_{wrong_day}_log")]


def test_a_stamp_outside_the_years_reads_no_date() -> None:
    before = _stamp(datetime(2010, 3, 9, 22, 11, 8), "_", "-")
    assert _dates(LAB, f"run_{before}_log") == []


def test_weekday_words_are_the_dictionarys_not_the_codes() -> None:
    """The weekdays live in the Dictionary's YAML beside the months, each with its ISO number, its
    full name its only surface, and the abbreviation a stamp writes a property the stamp reads."""
    weekdays = {
        sense.term: (
            sense.properties.get("weekday"),
            sense.properties.get("stamp_abbreviation"),
            sense.surfaces,
        )
        for sense in STORE.senses()
        if sense.theme_kind == "date" and "weekday" in sense.properties
    }
    assert weekdays == {
        "monday": (1, "mon", ("monday",)),
        "tuesday": (2, "tue", ("tuesday",)),
        "wednesday": (3, "wed", ("wednesday",)),
        "thursday": (4, "thu", ("thursday",)),
        "friday": (5, "fri", ("friday",)),
        "saturday": (6, "sat", ("saturday",)),
        "sunday": (7, "sun", ("sunday",)),
    }
    assert _dates(Normaliser(None, years=(2013, 2026)), "run_Tue_Mar__9_14-07-45_2021") == []


#: The abbreviations a C-library stamp writes, which bind nowhere else.
ABBREVIATIONS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@pytest.mark.parametrize("abbreviation", ABBREVIATIONS)
def test_a_weekdays_abbreviation_binds_to_nothing_alone(abbreviation: str) -> None:
    """``sat`` is saturated as often as Saturday, and ``mon`` a monomer: alone in a name, each is
    a word the Dictionary does not know, as under tokeniser 10. Glued after a known acronym it is
    that spelling's other half, a word (tokeniser 12 divides a known word in capitals from the word
    glued after it), never a weekday."""
    assert STORE.look_up(abbreviation) is None
    assert STORE.look_up(abbreviation.upper()) is None
    assert not LAB.knows(abbreviation)
    assert _read(LAB, f"GFP{abbreviation}") == (
        ("GFP", "word", "gfp"),
        (abbreviation, "word", abbreviation),
    )


#: Names holding an abbreviation, each with tokeniser 10's reading under :data:`LAB`, except
#: ``GFPsat``, which tokeniser 12 divides after its known acronym.
ABBREVIATION_PINS = (
    ("GFPsat", (("GFP", "word", "gfp"), ("sat", "word", "sat"))),
    ("sat_17_kelt", (("sat", "word", "sat"), ("17", "number", "17"), ("kelt", "word", "kelt"))),
    ("2021-03-04_sat", (("2021-03-04", "date", "2021-03-04"), ("sat", "word", "sat"))),
    ("mon_02", (("mon", "word", "mon"), ("02", "number", "2"))),
    ("sun_exposure", (("sun", "word", "sun"), ("exposure", "word", "exposure"))),
)


@pytest.mark.parametrize(("name", "reading"), ABBREVIATION_PINS)
def test_names_holding_an_abbreviation_read_as_they_did(
    name: str, reading: tuple[tuple[str, str, str]]
) -> None:
    assert _read(LAB, name) == reading


def test_the_full_weekday_names_still_bind() -> None:
    found = STORE.look_up("saturday")
    assert found is not None
    assert found.sense.term == "saturday"


# -- 3. a clock written with dots after its date ---------------------------------------------------

#: Dates inside :data:`LAB`'s years.
SHOT_DATES = ("2024-05-06", "2019-11-30", "2013-01-02")
#: Clocks as a screenshot writes them: the hour unpadded, minutes and seconds in two digits.
SHOT_CLOCKS = ((10, 41, 27), (9, 5, 3), (0, 0, 0), (23, 59, 59), (12, 30, 45), (1, 2, 3))
#: The shapes a screenshot's name takes, with ``{d}`` its date and ``{c}`` its clock.
SHOT_SHAPES = (
    "Screen Shot {d} at {c}",
    "Screenshot {d} at {c} AM",
    "Screenshot {d} at {c} PM",
    "Screen Recording {d} at {c}",
)


def _shots() -> list[tuple[str, str, tuple[int, int, int]]]:
    cases = []
    for shape in SHOT_SHAPES:
        for day in SHOT_DATES:
            for hour, minute, second in SHOT_CLOCKS:
                clock = f"{hour}.{minute:02d}.{second:02d}"
                written = shape.format(d=day, c=clock)
                for spaced in (written, written.replace(" ", "_")):
                    cases.append((spaced, day, (hour, minute, second)))
    return cases


SHOTS = _shots()


def test_a_clock_written_with_dots_after_its_date_is_a_time() -> None:
    wrong = []
    for name, day, (hour, minute, second) in SHOTS:
        atoms = LAB.atoms(name)
        dates = [atom.normalised for atom in atoms if atom.kind is AtomKind.DATE]
        numbers = [atom.normalised for atom in atoms if atom.kind is AtomKind.NUMBER]
        if dates != [day] or numbers != [str(hour), str(minute), str(second)]:
            wrong.append((name, dates, numbers))
    assert wrong == []


#: Dotted runs a screenshot never writes: a minute or a second of one digit, or a letter glued
#: after the seconds (a quantity: ``10x``, ``03h``). None of them is a date under :data:`LAB`.
NOT_CLOCKS = (
    "1.5.10",
    "10.4.27",
    "10.41.7",
    "9.5.3",
    "10.41.27x",
    "10.41.27b",
    "9.05.03h",
    "1.5.10x",
)


def test_a_dotted_run_a_clock_never_writes_is_read_as_without_a_date() -> None:
    """After its date, a dotted run is a clock only with two-digit minutes and seconds and nothing
    glued after it.

    Anything else reads as it would with no date before it (``2021-03-04 1.5.10x`` keeps its
    ``10x``).
    """
    wrong = []
    for day in SHOT_DATES:
        for joiner in (" at ", "_at_", "_", " "):
            for run in NOT_CLOCKS:
                name, alone = f"{day}{joiner}{run}", f"kelt_{run}"
                after = [
                    (atom.text, atom.kind.value, atom.normalised)
                    for atom in LAB.atoms(name)
                    if atom.start >= len(day) + len(joiner)
                ]
                without = [
                    (atom.text, atom.kind.value, atom.normalised)
                    for atom in LAB.atoms(alone)
                    if atom.start >= len("kelt_")
                ]
                if after != without:
                    wrong.append((name, after, without))
    assert wrong == []


def test_a_quantity_after_a_date_keeps_its_unit() -> None:
    assert _read(LAB, "2021-03-04 1.5.10x") == (
        ("2021-03-04", "date", "2021-03-04"),
        ("1.5", "number", "1.5"),
        ("10x", "quantity", "10 fold"),
    )
    assert _read(LAB, "2021-03-04_2.5.10") == (
        ("2021-03-04", "date", "2021-03-04"),
        ("2.5", "number", "2.5"),
        ("10", "number", "10"),
    )


# -- the neighbours: what the three classes must not move (a tripwire, never a target) -------------

#: Each name with its whole reading under :data:`LAB`, as tokeniser 10 read it and tokeniser 11
#: still reads it: the bare short date and its swap, lists of numbers, a year outside the years, six
#: digits read year first, a version triple, dotted numbers that are no clock, a clock a calendar
#: could read (tokeniser 8), a span of two dates, and a weekday and a month with no clock.
NEIGHBOURS = (
    (
        "kelt_12_09_2016",
        (("kelt", "word", "kelt"), ("12_09_2016", "date", "2016-12-09 or 2016-09-12")),
    ),
    ("kelt_3-17-15", (("kelt", "word", "kelt"), ("3-17-15", "date", "2015-03-17"))),
    (
        "kelt_1_2_3_4",
        (
            ("kelt", "word", "kelt"),
            ("1", "number", "1"),
            ("2", "number", "2"),
            ("3", "number", "3"),
            ("4", "number", "4"),
        ),
    ),
    (
        "kelt_1-2-15-16",
        (
            ("kelt", "word", "kelt"),
            ("1", "number", "1"),
            ("2", "number", "2"),
            ("15", "number", "15"),
            ("16", "number", "16"),
        ),
    ),
    (
        "vorn_3.4.17.18",
        (("vorn", "word", "vorn"), ("3.4", "number", "3.4"), ("17.18", "number", "17.18")),
    ),
    (
        "kelt_7-3-2009",
        (
            ("kelt", "word", "kelt"),
            ("7", "number", "7"),
            ("3", "number", "3"),
            ("2009", "number", "2009"),
        ),
    ),
    (
        "zib_7_3_2009_vorn",
        (
            ("zib", "word", "zib"),
            ("7", "number", "7"),
            ("3", "number", "3"),
            ("2009", "number", "2009"),
            ("vorn", "word", "vorn"),
        ),
    ),
    ("vorn_230512", (("vorn", "word", "vorn"), ("230512", "date", "2023-05-12"))),
    ("vorn_22-06-07", (("vorn", "word", "vorn"), ("22-06-07", "date", "2022-06-07"))),
    ("zib_v1.2.3", (("zib", "word", "zib"), ("v1.2.3", "version", "v1.2.3"))),
    ("brisa v1.2.13", (("brisa", "word", "brisa"), ("v1.2.13", "version", "v1.2.13"))),
    (
        "brisa_12.5.2",
        (("brisa", "word", "brisa"), ("12.5", "number", "12.5"), ("2", "number", "2")),
    ),
    ("kelt pH 7.4", (("kelt", "word", "kelt"), ("pH", "word", "ph"), ("7.4", "number", "7.4"))),
    (
        "kelt_10.41.27",
        (("kelt", "word", "kelt"), ("10.41", "number", "10.41"), ("27", "number", "27")),
    ),
    (
        "kelt_2024-05-06_zib_10.41.27",
        (
            ("kelt", "word", "kelt"),
            ("2024-05-06", "date", "2024-05-06"),
            ("zib", "word", "zib"),
            ("10.41", "number", "10.41"),
            ("27", "number", "27"),
        ),
    ),
    (
        "zib_2024-05-06_104127",
        (
            ("zib", "word", "zib"),
            ("2024-05-06", "date", "2024-05-06"),
            ("104127", "number", "104127"),
        ),
    ),
    (
        "kelt_2024-03-04_7-11-03",
        (
            ("kelt", "word", "kelt"),
            ("2024-03-04", "date", "2024-03-04"),
            ("7", "number", "7"),
            ("11", "number", "11"),
            ("03", "number", "3"),
        ),
    ),
    (
        "kelt_2016-12-09_3",
        (("kelt", "word", "kelt"), ("2016-12-09", "date", "2016-12-09"), ("3", "number", "3")),
    ),
    (
        "kelt_12_09_2016_3",
        (
            ("kelt", "word", "kelt"),
            ("12", "number", "12"),
            ("09", "number", "9"),
            ("2016", "number", "2016"),
            ("3", "number", "3"),
        ),
    ),
    (
        "kelt_12_09_2016b",
        (
            ("kelt", "word", "kelt"),
            ("12_09_2016", "date", "2016-12-09 or 2016-09-12"),
            ("b", "word", "b"),
        ),
    ),
    ("kelt_17Jun2024", (("kelt", "word", "kelt"), ("17Jun2024", "date", "2024-06-17"))),
    ("kelt_Tue_zib", (("kelt", "word", "kelt"), ("Tue", "word", "tue"), ("zib", "word", "zib"))),
    (
        "kelt_Tue_Mar_9_2021",
        (
            ("kelt", "word", "kelt"),
            ("Tue", "word", "tue"),
            ("Mar", "word", "mar"),
            ("9", "number", "9"),
            ("2021", "number", "2021"),
        ),
    ),
    (
        "kelt_1-4-24_to_1-8-24",
        (
            ("kelt", "word", "kelt"),
            ("1-4-24", "date", "2024-01-04 or 2024-04-01"),
            ("to", "word", "to"),
            ("1-8-24", "date", "2024-01-08 or 2024-08-01"),
        ),
    ),
)


@pytest.mark.parametrize(("name", "reading"), NEIGHBOURS)
def test_the_neighbours_read_as_they_did(name: str, reading: tuple[tuple[str, str, str]]) -> None:
    assert _read(LAB, name) == reading


def test_a_folders_series_still_settles_a_short_date() -> None:
    """Tokeniser 10's orders, unchanged: a first field above twelve is a day."""
    assert WIDE.short_date_orders("kelt_13-04-31") == ((("day_first", "2031-04-13"),),)
    assert WIDE.short_date_orders("kelt_03-04-31") == (
        (("day_first", "2031-04-03"), ("month_first", "2031-03-04")),
    )


def test_a_clock_is_never_offered_to_a_folders_series() -> None:
    """A folder's series (tokeniser 10) reads each name's one short date; a stamp's clock and a
    clock after its date are times, so a series of stamped or clocked names never dates them."""
    assert LAB.short_dates("run_Tue_Mar__9_22-11-08_2021_log") == ()
    assert WIDE.short_dates("kelt_2031-03-04_10-11-15") == ()
    assert WIDE.short_dates("kelt_10-11-15") != ()
