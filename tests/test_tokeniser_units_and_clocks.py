"""Tokeniser 14: a folded unit before a lower-case letter, a clock after a date, and a spaced
quantity.

A unit matched by folding and followed by a lower-case letter does not stand. A clock after a date
is a time on the name's event time. A quantity written with a space between its number and its unit
is a quantity. Each is a minimal synthetic case over the grammar, and every name is invented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ingence.dictionary.discover import event_time_from_atoms
from ingence.dictionary.normalize import NORMALIZER_VERSION, Normaliser
from ingence.dictionary.store import DictionaryStore

READER = Normaliser(DictionaryStore.builtin(), years=(2013, 2035))


def _atoms(name: str) -> list[tuple[str, str, str | None]]:
    return [(atom.text, atom.kind.value, atom.unit) for atom in READER.atoms(name)]


def _clock(name: str) -> int | None:
    event_ns, _ = event_time_from_atoms(READER.atoms(name), latest=date(2031, 6, 1))
    return event_ns


def _moment(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp() * 1e9)


def test_the_version_names_this_bump() -> None:
    assert NORMALIZER_VERSION == "14"


# -- a unit matched by folding and followed by a lower-case letter does not stand ----------------


def test_a_folded_unit_before_a_lower_case_letter_is_the_word_s_first_letters() -> None:
    assert _atoms("Plate 2Plate 2_notes") == [
        ("Plate", "word", None),
        ("2", "number", None),
        ("Plate", "word", None),
        ("2", "number", None),
        ("notes", "word", None),
    ]


def test_a_unit_matched_as_written_stands_whatever_follows_a_separator() -> None:
    assert _atoms("x_2pL_y") == [
        ("x", "word", None),
        ("2pL", "quantity", "picolitre"),
        ("y", "word", None),
    ]
    assert _atoms("x_2PL_y")[1] == ("2PL", "quantity", "picolitre")


def test_a_longer_unit_matched_by_folding_still_stands_before_a_separator() -> None:
    assert _atoms("wash_500nm")[1] == ("500nm", "quantity", "nanometre")
    assert _atoms("wash_500NM")[1][1] == "quantity"


# -- a clock after a date is a time on the event time --------------------------------------------


def test_a_six_digit_clock_after_a_date_is_the_name_s_time_of_day() -> None:
    assert _clock("run_2031-03-04_101522") == _moment("2031-03-04 10:15:22")
    assert [atom.clock for atom in READER.atoms("run_2031-03-04_101522") if atom.clock] == [
        "10:15:22"
    ]


def test_a_written_clock_after_a_date_is_the_time_of_day_too() -> None:
    assert _clock("run_2031-03-04_10-15-22_b") == _moment("2031-03-04 10:15:22")
    assert _clock("2031-03-04 at 10.41.27") == _moment("2031-03-04 10:41:27")


def test_a_date_with_no_clock_is_still_midnight_and_a_clock_before_it_is_none() -> None:
    assert _clock("run_2031-03-04") == _moment("2031-03-04 00:00:00")
    # Six digits before the date are not written as a clock after it: a number, and midnight.
    assert _clock("x_101522_2031-03-04") == _moment("2031-03-04 00:00:00")
    assert all(atom.clock is None for atom in READER.atoms("x_101522_2031-03-04"))


def test_six_digits_no_clock_holds_are_no_clock() -> None:
    # 31 is no hour: the run keeps the reading it had (a short date here), and marks no clock.
    atoms = READER.atoms("run_2031-03-04_310305")
    assert all(atom.clock is None for atom in atoms)
    assert _clock("run_2031-03-04_310305") != _moment("2031-03-04 31:03:05".replace("31:", "07:"))


def test_two_different_clocks_leave_the_day_and_not_the_hour() -> None:
    name = "run_2031-03-04_10-15-22_and_2031-03-04_11-00-00"
    assert _clock(name) == _moment("2031-03-04 00:00:00")


# -- spaced quantities ------------------------------------------------------------------------


def test_a_number_a_space_and_a_unit_read_as_one_quantity() -> None:
    (buffer, held, notes) = _atoms("buffer 500 mM notes")
    assert held == ("500 mM", "quantity", "millimolar")
    assert (buffer[1], notes[1]) == ("word", "word")
    assert _atoms("wash 20 min")[1] == ("20 min", "quantity", "minute")


def test_a_spaced_quantity_s_magnitude_is_in_the_base_unit() -> None:
    (held,) = [
        atom for atom in READER.atoms("buffer 500 mM notes") if atom.kind.value == "quantity"
    ]
    assert held.magnitude == 500.0 and held.base_value == 0.5 and held.dimension


def test_a_number_a_space_and_a_word_that_is_no_unit_stay_apart() -> None:
    assert _atoms("plate 2 a") == [
        ("plate", "word", None),
        ("2", "number", None),
        ("a", "word", None),
    ]
    assert [kind for _, kind, _ in _atoms("rep 3 vorn")] == ["word", "number", "word"]


@pytest.mark.parametrize("word", ["pm", "am", "in", "us", "mo", "at"])
def test_a_clock_or_english_word_that_is_also_a_unit_symbol_never_makes_a_spaced_quantity(
    word: str,
) -> None:
    """The text "3 pm" is a time of day and "2 in" a number and a word."""
    assert [kind for _, kind, _ in _atoms(f"meeting 3 {word} notes")] == [
        "word",
        "number",
        "word",
        "word",
    ]


def test_a_one_letter_unit_after_a_space_is_never_a_quantity() -> None:
    assert [kind for _, kind, _ in _atoms("plate 2 h")] == ["word", "number", "word"]


def test_a_spaced_quantity_beside_a_date_keeps_both() -> None:
    assert _atoms("500 mM_2031-03-04") == [
        ("500 mM", "quantity", "millimolar"),
        ("2031-03-04", "date", None),
    ]
