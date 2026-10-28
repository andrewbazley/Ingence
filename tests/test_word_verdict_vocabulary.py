"""The vocabulary a laboratory's word verdicts asked for, each as its class.

What an analysis script draws and measures is known as such. The small words joining others are
known and never asked about. A quality word is a mark and never an exclusion. A program named in a
file's name is a program. A word one slip from a known word is proposed as its spelling, never
bound. Every name here is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.spelling import SpellingIndex, one_edit_apart
from ingence.dictionary.store import DictionaryStore

if TYPE_CHECKING:
    from pathlib import Path

STORE = DictionaryStore.builtin()
READER = Normaliser(STORE, years=(2013, 2026))


def _sense(word: str) -> tuple[str, str] | None:
    hit = STORE.look_up(word)
    return (hit.sense.term, hit.sense.theme_kind) if hit is not None else None


@pytest.mark.parametrize(
    ("word", "term"),
    [
        ("heatmaps", "heatmap"),
        ("boxplot", "boxplot"),
        ("violinplot", "violin_plot"),
        ("histogram", "histogram"),
        ("volcano", "volcano_plot"),
        ("gkde", "density_plot"),
        ("kymograph", "kymograph"),
    ],
)
def test_what_an_analysis_draws_is_a_product(word: str, term: str) -> None:
    hit = STORE.look_up(word)
    assert hit is not None
    assert (hit.sense.term, hit.sense.theme_kind) == (term, "file_role")
    assert hit.sense.properties.get("product") is True


@pytest.mark.parametrize(
    ("word", "term"),
    [("step_sizes", "step_size"), ("Deff", "diffusion_coefficient"), ("tlag", "time_lag")],
)
def test_what_a_tracking_analysis_measures_is_a_statistic(word: str, term: str) -> None:
    assert _sense(word) == (term, "statistic")


def test_a_differential_expression_analysis_is_known_by_its_short_name() -> None:
    assert _sense("diffexpr") == ("differential_expression", "technique")


def test_a_counter_after_a_plot_keeps_its_split() -> None:
    """``zib_histogram3``: the plot's kind and its number, since the Dictionary knows the kind."""
    assert [atom.text for atom in READER.atoms("zib_histogram3")] == ["zib", "histogram", "3"]


@pytest.mark.parametrize("word", ["by", "with", "vs", "versus", "and", "of", "per"])
def test_a_connecting_word_is_known_and_so_never_asked_about(word: str) -> None:
    assert _sense(word) is not None
    assert _sense(word)[1] == "connective"  # type: ignore[index]


@pytest.mark.parametrize("word", ["in", "on", "a"])
def test_a_connecting_word_a_unit_or_a_date_spells_keeps_that_reading(word: str) -> None:
    assert (_sense(word) or ("", ""))[1] != "connective"


def test_a_quality_word_is_a_mark_and_never_an_exclusion() -> None:
    hit = STORE.look_up("sick")
    assert hit is not None
    assert (hit.sense.theme_kind, hit.sense.properties.get("mark")) == ("quality", "poor")
    assert hit.sense.section == "quality_marks"  # never the doubt rules' negative lexicon


def test_a_program_named_in_a_file_is_a_program() -> None:
    assert _sense("TrackMate") == ("trackmate", "software")


def test_a_fluorescent_protein_is_known_whole() -> None:
    assert _sense("mNeonGreen") == ("fluorescent_protein", "reagent")


# -- a slip proposed as its spelling ------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "apart"),
    [
        ("kelmar", "kelmer", True),  # changed
        ("kelmar", "kelmaar", True),  # added
        ("kelmar", "kelar", True),  # dropped
        ("kelmar", "klemar", True),  # swapped
        ("kelmar", "kelmar", False),
        ("kelmar", "kelmerz", False),
        ("kelmar", "lekmar", False),  # a swap of letters apart is two edits
    ],
)
def test_one_edit_is_one_letter_added_dropped_changed_or_two_swapped(
    first: str, second: str, apart: bool
) -> None:
    assert one_edit_apart(first, second) is apart


INDEX = SpellingIndex([("vornel", "vornel"), ("brisket", "brisket"), ("kelt", "kelt")])


@pytest.mark.parametrize(
    ("word", "proposed"),
    [
        ("voirnel", "vornel"),
        ("vonrel", "vornel"),
        ("brisqet", "brisket"),
        ("briske", "brisket"),  # a last letter dropped, which is not a plural
        ("vornel", None),
    ],
)
def test_a_slip_of_a_known_word_proposes_it(word: str, proposed: str | None) -> None:
    found = INDEX.propose(word)
    assert (found.spelling if found else None) == proposed


def test_a_short_word_proposes_nothing() -> None:
    assert INDEX.propose("kelb") is None  # a four-letter word is one edit from too many
    assert SpellingIndex([("zibor", "zibor")]).propose("zibo") is None  # nor a four-letter slip


def test_a_plural_is_no_slip() -> None:
    assert SpellingIndex([("vornel", "vornel")]).propose("vornels") is None
    assert SpellingIndex([("vornels", "vornels")]).propose("vornel") is None


def test_a_spelling_two_senses_share_is_proposed_with_both() -> None:
    index = SpellingIndex([("tessel", "brisket"), ("stitch", "brisket")])
    found = index.propose("briskeet")
    assert found is not None
    assert (found.spelling, found.terms) == ("brisket", ("stitch", "tessel"))


def test_two_known_words_one_slip_away_propose_neither() -> None:
    index = SpellingIndex([("vornel", "vornel"), ("vornet", "vornet")])
    assert index.propose("vornez") is None


def test_the_shipped_vocabulary_proposes_a_slip() -> None:
    index = SpellingIndex(
        (sense.term, surface) for sense in STORE.senses() for surface in sense.surfaces
    )
    found = index.propose("heatmpas")
    assert found is not None
    assert found.terms == ("heatmap",)


def test_a_connecting_word_is_never_a_results_chip() -> None:
    """Search's cards show a unit's words by kind; the connectives' kind is hidden, so `with` or
    `by` never stands as a condition chip on every result."""
    from ingence.search.tags import DEFAULT_CHIP_KINDS, DEFAULT_HIDDEN_KINDS

    kinds = {sense.theme_kind for sense in STORE.senses() if sense.section == "connecting_words"}
    assert kinds == {"connective"}
    assert kinds <= set(DEFAULT_HIDDEN_KINDS)
    assert not kinds & set(DEFAULT_CHIP_KINDS)


def test_the_spelling_index_is_built_once_per_run(tmp_path: Path) -> None:
    """The Questions page asks for it on every request; the run's vocabulary does not change."""
    from ingence.core.project import Project
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog
    from ingence.textword.questions import spelling_index

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("a")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    with open_catalog(project.path) as first, open_catalog(project.path) as second:
        assert spelling_index(first) is spelling_index(second)
