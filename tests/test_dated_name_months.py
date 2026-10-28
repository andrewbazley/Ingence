"""Search's dated folder reads a month word by the Dictionary's month senses, not by the theme kind.

A dated folder's name holds a day's date and another word (search's fallback when a run holds no
experiment rule's conclusion). **A month word** is the one date-kind word that may stand for the
date's month (``17_Mar_kelt``) and is no "other word" (``2021-03-04_Mar`` is a calendar folder).
Every other date-kind word (``today``, ``overnight``, ``am``, ``pm``, the weekdays) is an ordinary
word here: ``2021-03-04_saturday`` is dated and ``saturday_17_kelt`` is not, as for ``kelt``. The
month senses are the ones tokeniser 8 reads month-word dates by: a date-kind sense whose canonical
value is a month's number. Every name here is invented; the words are the shipped Dictionary's.
"""

from __future__ import annotations

import pytest

from ingence.dictionary.normalize import AtomKind, Normaliser, is_month_sense
from ingence.dictionary.store import DictionaryStore
from ingence.search.scale import dated_name, word_reading

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE, years=(2013, 2026))


def _components(name: str) -> list[tuple[str, str | None, float | None]]:
    """A folder name's components as search's fallback reads them, from the run's Dictionary."""
    found: list[tuple[str, str | None, float | None]] = []
    for atom in READER.atoms(name):
        if atom.kind is AtomKind.DATE:
            found.append(("date", None, None))
            continue
        hit = STORE.look_up(atom.text) if atom.kind is AtomKind.WORD else None
        reading = word_reading(hit.sense.theme_kind, hit.sense.canonical) if hit else None
        found.append((atom.kind.value, reading, atom.magnitude))
    return found


#: Every surface of a date-kind sense that is not a month, generated from the shipped Dictionary.
NOT_MONTHS = sorted(
    surface
    for sense in STORE.senses()
    if sense.theme_kind == "date" and not is_month_sense(sense.theme_kind, sense.canonical)
    for surface in sense.surfaces
    if surface.isalpha() and STORE.look_up(surface) is not None
)
#: Every surface of a month sense.
MONTHS = sorted(
    surface
    for sense in STORE.senses()
    if is_month_sense(sense.theme_kind, sense.canonical)
    for surface in sense.surfaces
    if surface.isalpha()
)


def test_the_generated_words_are_the_dictionarys() -> None:
    assert {"today", "overnight", "am", "pm", "saturday", "monday"} <= set(NOT_MONTHS)
    assert {"mar", "march", "jun"} <= set(MONTHS)
    assert not set(NOT_MONTHS) & set(MONTHS)


@pytest.mark.parametrize("word", NOT_MONTHS)
def test_a_date_kind_word_that_is_no_month_is_another_word(word: str) -> None:
    """As an ordinary word does: a day's date and it is dated; a day number beside it is not."""
    assert dated_name(_components(f"2021-03-04_{word}")) is dated_name(
        _components("2021-03-04_kelt")
    )
    assert dated_name(_components(f"2021-03-04_{word}")) is True
    assert dated_name(_components(f"{word}_17_kelt")) is dated_name(_components("zib_17_kelt"))
    assert dated_name(_components(f"{word}_17_kelt")) is False


@pytest.mark.parametrize("word", MONTHS)
def test_a_month_word_still_dates_a_day_and_is_no_other_word(word: str) -> None:
    """Pinned: a month word beside a day number and a word is a dated name; after a date alone it
    is a calendar folder."""
    assert dated_name(_components(f"17_{word}_kelt")) is True
    assert dated_name(_components(f"2021-03-04_{word}")) is False
    assert dated_name(_components(f"{word}_2021")) is False
