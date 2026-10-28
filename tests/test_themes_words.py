"""The theme layer's words from free text, and plurals of unknown words.

A free text (a title, a description, keywords) is read into the theme layer's words exactly as a
run's names are: the engine's name reader line by line, the projection's own word rule, and the
prose stop list for words the Dictionary does not bind. Within a corpus a word the Dictionary does
not know and its regular plural are one word. Every word is invented.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from ingence.core.settings import ThemeSettings
from ingence.dictionary.store import DictionaryStore
from ingence.textword.prose import DEFAULT_STOPWORDS
from ingence.themes import projection, words
from ingence.themes.core import compute_themes
from ingence.themes.words import SENSE_MARK, read_text, read_texts


@pytest.fixture(scope="module")
def store() -> DictionaryStore:
    return DictionaryStore.builtin()


def test_a_free_text_is_read_into_theme_words_line_by_line(store: DictionaryStore) -> None:
    text = "The zephyl brisas were imaged by widefield at 500nm\nkeywords: vornak | kelt12"
    read = read_text(text, store=store)
    # Function words of prose are left out (the prose stop list), a unit is a "how" word, a
    # word the Dictionary binds is its sense by its term, the rest their stems, in order.
    assert "the" not in read.words and "were" not in read.words and "by" not in read.words
    assert "nm" not in read.words and "500" not in read.words
    sense = f"{SENSE_MARK}epifluorescence"
    assert sense in read.words
    assert read.senses[sense] == "epifluorescence"
    for word in ("zephyl", "brisas", "imaged", "vornak", "kelt12"):
        assert word in read.words, word
    assert read.words.index("zephyl") < read.words.index("vornak")
    # The same text, read again, gives the same words.
    assert read_text(text, store=store) == read
    # Each line is its own name: a word never joins across a line break.
    assert read_text("zephyl\nbrisas", store=store).words == ("zephyl", "brisas")


def test_a_word_counts_once_per_field_whatever_its_line_breaks(store: DictionaryStore) -> None:
    """A word is counted once per field, so the counts do not depend on where a text was broken.

    A field is a title, a description, a keywords field (each element of the list ``read_text`` is
    given).
    """
    one_line = read_text("zephyl brisa zephyl vornak zephyl", store=store)
    broken = read_text("zephyl brisa\nzephyl vornak\nzephyl", store=store)
    assert one_line.words == broken.words == ("zephyl", "brisa", "vornak")
    fields = read_text(["zephyl brisa", "vornak\nzephyl"], store=store)
    assert fields.words == ("zephyl", "brisa", "vornak", "zephyl")
    corpus = read_texts({"s1": ["zephyl\nzephyl brisa", "zephyl"]}, store=store)
    assert corpus.documents["s1"] == ["zephyl", "brisa", "zephyl"]


def test_a_missing_or_empty_field_is_skipped(store: DictionaryStore) -> None:
    """A field given as None is not read as the word "none"."""
    fields = ["zephyl brisa", None, "", "vornak"]
    assert read_text(fields, store=store).words == ("zephyl", "brisa", "vornak")  # type: ignore[arg-type]
    corpus = read_texts({"s1": fields}, store=store)  # type: ignore[dict-item]
    assert corpus.documents["s1"] == ["zephyl", "brisa", "vornak"]


def test_the_free_text_route_shares_the_projections_word_rule(store: DictionaryStore) -> None:
    # One rule, never re-implemented: the projection reads a component's words with the very
    # function the free-text route uses.
    assert projection.word_tokens is words.word_tokens
    how = frozenset(ThemeSettings().how_kinds)
    is_how = words.how_piece(store, how)
    assert words.word_tokens("zeph500nm", None, None, None, how=how, is_how=is_how) == ["zeph"]
    assert words.word_tokens("x", "resolved", "nanometre", "unit", how=how, is_how=is_how) == []
    # The stop list is the prose module's, applied to unbound words only.
    assert "which" in DEFAULT_STOPWORDS
    assert "which" not in read_text("which zephyl", store=store).words


def test_a_corpus_of_texts_folds_series_and_gives_the_dictionarys_phrases(
    store: DictionaryStore,
) -> None:
    texts = {
        "s1": "vorn1 zephyl",
        "s2": ["vorn2 zephyl", "brisa"],
        "s3": "vorn7 brisa",
    }
    corpus = read_texts(texts, store=store)
    assert sorted(corpus.documents) == ["s1", "s2", "s3"]
    # Three numbers of one prefix read as one series, as the projection folds a run's names.
    assert corpus.documents["s1"] == ["vorn", "zephyl"]
    assert corpus.documents["s2"] == ["vorn", "zephyl", "brisa"]
    # The Dictionary's spellings written in parts, ready for compute_themes's phrases.
    assert corpus.phrases and all(len(key) > 1 for key in corpus.phrases)
    assert all(value.startswith(SENSE_MARK) for value in corpus.phrases.values())
    assert read_texts(texts, store=store) == corpus


def _plural_pool() -> dict[str, list[str]]:
    """Three planted groups of twelve; half of two groups write two of their words in the
    plural. Each document also draws two of six shared words, so no two are one text."""
    shared = ("alpok", "nirkel", "sebbit", "tullor", "gormin", "wixet")
    groups = {
        "a": (
            ("nabrisc", "nabriscs"),
            ("pellox", "pellox"),
            ("dorvane", "dorvane"),
            ("quiddy", "quiddies"),
        ),
        "b": (
            ("toskel", "toskels"),
            ("fenwick", "fenwick"),
            ("lumbray", "lumbray"),
            ("glabbox", "glabboxes"),
        ),
        "c": (
            ("hobrin", "hobrin"),
            ("celtax", "celtax"),
            ("murrow", "murrow"),
            ("vintle", "vintle"),
        ),
    }
    documents: dict[str, list[str]] = {}
    for name, pairs in groups.items():
        for number in range(12):
            own = [plural if number % 2 else singular for singular, plural in pairs]
            documents[f"{name}{number:02d}"] = [
                *own,
                shared[number % 6],
                shared[(number + 1 + number // 6) % 6],
            ]
    return documents


def test_an_unknown_words_plural_is_read_as_its_singular_within_a_corpus() -> None:
    result = compute_themes(_plural_pool())
    shown = {word for summary in result.themes for word in summary.words}
    for singular, plural in (
        ("nabrisc", "nabriscs"),
        ("quiddy", "quiddies"),
        ("toskel", "toskels"),
        ("glabbox", "glabboxes"),
    ):
        assert singular in shown, singular
        assert plural not in shown, plural
        assert plural not in result.display
    # A setting turns it off: then both spellings are words of their own.
    apart = compute_themes(_plural_pool(), settings=ThemeSettings(fold_unknown_plurals=False))
    assert "nabriscs" in apart.display and "nabrisc" in apart.display


def test_a_plural_is_never_folded_onto_a_known_sense_or_without_its_singular() -> None:
    documents = _plural_pool()
    # "pellox" is present but a sense the caller knows: its plural stays a word of its own.
    for unit in sorted(documents):
        if unit.startswith("a"):
            documents[unit].append("pelloxes")
    senses = {"pellox": "pellox method"}
    folded = words.plural_folds({w for d in documents.values() for w in d}, senses)
    assert "pelloxes" not in folded
    assert folded["nabriscs"] == "nabrisc"
    assert folded["quiddies"] == "quiddy"
    assert folded["glabboxes"] == "glabbox"
    # No singular among the words: nothing to fold onto.
    assert words.plural_folds({"wembles"}, {}) == {}
    # Words ending in "ss", "us" or "is" are not regular plurals.
    assert words.plural_folds({"glass", "glas", "brumus", "brumu"}, {}) == {}
    # A sense token never folds and is never folded onto.
    sense = f"{SENSE_MARK}kelt"
    assert words.plural_folds({sense, f"{sense}s"}, {}) == {}


def test_es_is_a_plural_ending_only_after_a_hissing_sound_or_o() -> None:
    """A word ending in e plus s is not folded onto a different, shorter word (as "spines" onto
    "spin").

    The engine strips "es" only after a hissing sound or o.
    """
    assert words.plural_folds({"spirnes", "spirn"}, {}) == {}
    assert words.plural_folds({"spirnes", "spirne", "spirn"}, {}) == {"spirnes": "spirne"}
    for singular in ("brusch", "glabbox", "kelsh", "vorz", "drovo", "plimas"):
        assert words.plural_folds({singular, f"{singular}es"}, {}) == {f"{singular}es": singular}, (
            singular
        )


def test_a_word_whose_own_plural_is_present_is_a_singular_and_folds_never_chain() -> None:
    """With "kelnses", "kelns" and "keln", "kelns" is not folded onto "keln", and "kelnses" does not
    follow it there.

    "kelns" has its own plural present, so it is a singular.
    """
    assert words.plural_folds({"kelnses", "kelns", "keln"}, {}) == {"kelnses": "kelns"}
    pool = {"kelnses", "kelns", "keln", "quiddies", "quiddy", "toskels", "toskel"}
    folded = words.plural_folds(pool, {})
    assert not set(folded.values()) & set(folded)
