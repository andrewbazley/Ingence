"""A glued word is one word, and the whole word is what is asked.

A string without spaces or any characters separating letters and numbers, in a field that was
likely labelled by a person, is read as one word. The tail of a strain's name is never read apart
from it. A tail cut off a strain's name was bound as a duration and asked about on thousands of
files; a construct's name raised a lone letter.

The class, written over the grammar of a name and never over the names that revealed it: a run of
letters and digits with no separator, which the tokeniser would cut into several atoms, is one word
where it begins with a letter. A run beginning with a number is still a quantity and what it
quantifies; a counter after a word the Dictionary knows keeps its split; a run the tokeniser
already reads as one thing (a quantity, an indexed label, a date, a version, an identifier) is
unchanged; and a run of letters alone keeps the case rule. Every name here is invented.
"""

from __future__ import annotations

import pytest

from ingence.core.settings import Settings
from ingence.dictionary.normalize import GLUED_RUNS, AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore

STORE = DictionaryStore.builtin()
DEFAULT = Normaliser(STORE)
WHOLE = Normaliser(STORE, glued_runs="whole")
SPLIT = Normaliser(STORE, glued_runs="split")


def _read(normaliser: Normaliser, name: str) -> list[tuple[str, str]]:
    return [(atom.text, atom.kind.value) for atom in normaliser.atoms(name)]


@pytest.mark.parametrize(
    "run",
    [
        "vorn3d",  # a strain written with d for a deletion: never three days
        "kelt15d",
        "pQ7XT",  # a construct: never a lone letter and a number
        "Q3XY",
        "B7KL2",  # a gene with digits in it
        "N2O4",  # a formula
        "R412W",  # a mutation: never 412 watts
        "Tq500nM",  # a treatment glued to its dose: read whole, and the person says which
    ],
)
def test_a_glued_run_beginning_with_a_letter_is_one_word(run: str) -> None:
    (atom,) = DEFAULT.atoms(run)
    assert (atom.text, atom.kind, atom.normalised) == (run, AtomKind.WORD, run.casefold())
    # The split reading, kept under its setting, cut it.
    assert len(SPLIT.atoms(run)) > 1


def test_the_whole_word_is_what_a_name_holds_between_its_separators() -> None:
    """``24h`` beside a glued strain is still a duration; the strain is one word."""
    assert _read(DEFAULT, "24h brisa vorn3d in kelt--6") == [
        ("24h", "quantity"),
        ("brisa", "word"),
        ("vorn3d", "word"),
        ("in", "word"),
        ("kelt", "word"),
        ("6", "number"),
    ]


@pytest.mark.parametrize(
    ("run", "read"),
    [
        ("3mMvorn", [("3mM", "quantity"), ("vorn", "word")]),
        ("20min003", [("20min", "quantity"), ("003", "number")]),
        ("10mM", [("10mM", "quantity")]),
        ("2.5h", [("2.5h", "quantity")]),
    ],
)
def test_a_run_beginning_with_a_number_is_a_quantity_and_what_it_quantifies(
    run: str, read: list[tuple[str, str]]
) -> None:
    assert _read(DEFAULT, run) == read


def test_a_counter_after_a_word_the_dictionary_knows_keeps_its_split() -> None:
    """``sample1`` is the first sample; ``vornx1`` is nobody's word and is asked about whole."""
    assert _read(DEFAULT, "sample1") == [("sample", "word"), ("1", "number")]
    assert _read(DEFAULT, "vornx1") == [("vornx1", "word")]


@pytest.mark.parametrize(
    ("run", "kind"),
    [
        ("FRAP009", "indexed"),
        ("kelt488", "indexed"),
        ("rep2", "indexed"),
        ("v12", "version"),
        ("20260104", "date"),
    ],
)
def test_a_run_already_read_as_one_thing_is_unchanged(run: str, kind: str) -> None:
    ((text, read),) = _read(DEFAULT, run)
    assert (text, read) == (run, kind)


def test_a_run_of_letters_alone_keeps_the_case_rule() -> None:
    """A case boundary divides two words, and a piece shorter than three belongs whole."""
    assert _read(DEFAULT, "rawVORN") == [("raw", "word"), ("VORN", "word")]
    assert _read(DEFAULT, "HeLa") == [("HeLa", "word")]


def test_a_dot_between_letters_divides_a_run_and_a_decimal_point_joins() -> None:
    """A name carrying another file's name with its extension is read as parts."""
    assert _read(DEFAULT, "Q3XY.tif")[0] == ("Q3XY", "word")
    assert _read(DEFAULT, "2.5h") == [("2.5h", "quantity")]


def test_the_literal_reading_takes_every_glued_run_whole() -> None:
    """The rule taken to the letter, kept as the setting's other value and measured."""
    assert _read(WHOLE, "3mMvorn") == [("3mMvorn", "word")]
    assert _read(WHOLE, "20min003") == [("20min003", "word")]
    assert _read(WHOLE, "sample1") == [("sample1", "word")]
    assert _read(WHOLE, "10mM") == [("10mM", "quantity")]


def test_every_reading_records_itself_in_the_version_components_carry() -> None:
    """Two projects reading names differently never share a component identity."""
    versions = {Normaliser(STORE, glued_runs=mode).version for mode in GLUED_RUNS}
    assert len(versions) == len(GLUED_RUNS)
    assert DEFAULT.version == "14"  # Tokeniser 14 rereads names.


def test_the_setting_defaults_to_reading_a_letter_led_run_whole() -> None:
    assert Settings().dictionary.glued_runs == "letter_led"
    with pytest.raises(ValueError, match="glued_runs"):
        Normaliser(STORE, glued_runs="sometimes")
