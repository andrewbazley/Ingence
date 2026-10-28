"""Tokeniser 8: four classes read together, each over a grammar and never a name.

A day, a month's word and a year is a date (through the Dictionary's month senses). A time of day
after a date is a time, not a second date. A trailing sign stays with its word, and a trailing minus
stays a divider by default. A name that writes its one date twice has that date's clock. Every name
here is invented, and every date a made-up one.
"""

from __future__ import annotations

import pytest

from ingence.dictionary.discover import event_time_from_atoms
from ingence.dictionary.normalize import NORMALIZER_VERSION, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE)


def _kinds(name: str) -> list[tuple[str, str]]:
    return [(atom.text, atom.kind.value) for atom in READER.atoms(name)]


def _dates(name: str) -> list[str]:
    return [atom.normalised for atom in READER.atoms(name) if atom.kind is AtomKind.DATE]


def test_the_version_names_this_bump_or_a_later_one() -> None:
    """The tokeniser version is 8 or later, and these classes stand in it unchanged."""
    assert int(NORMALIZER_VERSION) >= 8
    assert READER.version == NORMALIZER_VERSION


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("17Jun2031_kelt", "2031-06-17"),  # glued
        ("kelt_3-Mar-2031", "2031-03-03"),  # hyphenated
        ("09Feb2031 vorn", "2031-02-09"),  # at a name's start, with a space after
        ("zib_09 Feb 2031_run", "2031-02-09"),  # spaced
        ("kelt_22_SEPT_2031", "2031-09-22"),  # underscored, the word's spelling and case are free
        ("kelt_1_may_2031", "2031-05-01"),  # a word that is also an ordinary one
    ],
)
def test_a_day_a_months_word_and_a_year_is_a_date(name: str, expected: str) -> None:
    assert _dates(name) == [expected]


@pytest.mark.parametrize(
    "name",
    [
        "kelt_32Feb2031",  # a calendar does not hold it
        "kelt_17Jun4217",  # a year outside the plausible span
        "kelt_17Jun31",  # a two-digit year is a counter as often as a year
        "kelt_17-Jun_2031",  # two different separators
        "kelt_x17Jun2031",  # glued to a letter on its left
        "kelt_17Jun20311",  # glued to a digit on its right
        "kelt_17Junk2031",  # not a month's word
    ],
)
def test_a_run_that_is_not_one_is_no_date(name: str) -> None:
    assert _dates(name) == []


def test_without_a_dictionary_there_is_no_month_word() -> None:
    assert [a for a in Normaliser().atoms("17Jun2031_kelt") if a.kind is AtomKind.DATE] == []


def test_a_month_word_date_is_a_name_clock() -> None:
    event_ns, _ = event_time_from_atoms(READER.atoms("kelt_17Jun2031_vorn"))
    assert event_ns is not None


@pytest.mark.parametrize(
    ("name", "date"),
    [
        ("kelt_10-28-2031_7-11-03_PM", "2031-10-28"),
        ("Screen Shot 2031-03-04 at 12.02.31", "2031-03-04"),
        ("zib_17Jun2031_7-11-03", "2031-06-17"),
    ],
)
def test_a_time_of_day_after_a_date_is_not_a_second_date(name: str, date: str) -> None:
    assert _dates(name) == [date]
    assert event_time_from_atoms(READER.atoms(name))[0] is not None


@pytest.mark.parametrize(
    ("name", "date"),
    [
        ("kelt 2031-03-04 12.02.31", "2031-03-04"),  # directly after, a space between
        ("kelt_2031-03-04-7-11-03", "2031-03-04"),  # directly after, compact
        (
            "20310304_vorn_7-11-03_AM_zib",
            "2031-03-04",
        ),  # further on, followed by the half of the day
    ],
)
def test_a_clock_is_read_where_it_is_written_as_one(name: str, date: str) -> None:
    assert _dates(name) == [date]


def test_a_span_of_two_dates_keeps_both_and_has_no_clock() -> None:
    name = "kelt_1-4-31_to_1-8-31"
    assert len(_dates(name)) == 2
    assert event_time_from_atoms(READER.atoms(name))[0] is None


def test_a_second_date_further_on_with_no_half_of_the_day_is_still_a_date() -> None:
    assert len(_dates("kelt_20310304_rep_7-11-03")) == 2


def test_a_short_run_before_any_date_is_still_a_date() -> None:
    assert _dates("kelt_3-4-31") != []


def test_a_short_run_that_cannot_be_a_time_is_still_read_as_a_date() -> None:
    assert len(_dates("kelt_10-28-2031_28-11-31")) == 2  # 28 is no hour, so it is a day


@pytest.mark.parametrize(
    ("name", "signed"),
    [
        ("kelt_Ca2+", "ca2+"),
        ("kelt_Fe3+_run", "fe3+"),
        ("Xyz+ vorn", "xyz+"),
        ("Mg++_zib", "mg++"),
    ],
)
def test_a_trailing_sign_stays_with_its_word(name: str, signed: str) -> None:
    words = [a.normalised for a in READER.atoms(name) if a.kind is AtomKind.WORD]
    assert signed in words


@pytest.mark.parametrize("name", ["Xy+Zw", "Ca2+Zw", "kelt_2+2", "kelt+3"])
def test_a_plus_between_things_still_divides(name: str) -> None:
    assert not any(a.normalised.endswith("+") for a in READER.atoms(name))


def test_a_trailing_minus_stays_a_divider() -> None:
    assert [a.normalised for a in READER.atoms("Xyz-_zib") if a.kind is AtomKind.WORD] == [
        "xyz",
        "zib",
    ]


def test_a_trailing_sign_is_met_exactly_by_the_dictionary() -> None:
    # Taught, because the shipped Dictionary holds no spelling with a sign (``Ca2+`` alone is the
    # ion, not calcium imaging).
    from ingence.dictionary.store import Section, Sense, load_shipped_sections

    taught = DictionaryStore(
        (
            *load_shipped_sections(),
            Section(
                "taught",
                "",
                (
                    Sense(
                        term="kelt",
                        canonical="kelt",
                        theme_kind="subject",
                        section="taught",
                        surfaces=("kelt2+",),
                    ),
                ),
            ),
        )
    )
    (atom,) = [a for a in Normaliser(taught).atoms("Kelt2+") if a.kind is AtomKind.WORD]
    assert atom.normalised == "kelt2+"
    assert taught.look_up("kelt2+") is not None


def test_a_name_that_writes_its_one_date_twice_has_its_clock() -> None:
    twice = event_time_from_atoms(READER.atoms("kelt_20310617_vorn_6-17-2031"))
    once = event_time_from_atoms(READER.atoms("kelt_20310617_vorn"))
    assert twice[0] is not None and twice[0] == once[0]


def test_a_name_holding_two_different_dates_still_abstains() -> None:
    assert event_time_from_atoms(READER.atoms("kelt_20310617_vorn_20310618"))[0] is None
