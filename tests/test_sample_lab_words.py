"""The words of the sample collection: each is in the shipped Dictionary, or is a named common
biology word, an ordinary English word, or part of a made-up person's name."""

from __future__ import annotations

from collections.abc import Iterable

from ingence.core.schema import Entry, EntryType
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import read_name
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore, load_shipped_sections
from ingence.evaluation.sample_lab_words import words

#: The pack every new project loads whatever else it names (the same list ``init`` uses).
ALWAYS = ("programs",)
SECTIONS = load_shipped_sections()


def store_of(packs: Iterable[str]) -> DictionaryStore:
    """A Dictionary of the core and the named shipped packs, as a new project loads it."""
    chosen = {*ALWAYS, *packs}
    return DictionaryStore(
        tuple(section for section in SECTIONS if not section.domain or section.domain in chosen)
    )


EVERYTHING = DictionaryStore.builtin()
CORE = store_of(())
COLLECTION = store_of(words().pack_names())


def read_by(store: DictionaryStore, word: str) -> bool:
    """Whether ``store`` reads ``word`` as something (even as two things at odds)."""
    entry = Entry(
        entry_id=f"entry_{word}",
        root_id="root_test",
        relative_path=word,
        name=word,
        entry_type=EntryType.FILE,
        extension="",
        compound_extension="",
        first_seen_run="run_test",
        last_seen_run="run_test",
    )
    reading = read_name(entry, run_id="run_test", normaliser=Normaliser(store), store=store)
    return any(
        binding.resolution_status != ResolutionStatus.EXAMINED_NO_EVIDENCE
        for binding in reading.bindings
    )


def dictionary_words() -> list[str]:
    return [word for group in words().dictionary.values() for word in group]


def test_every_dictionary_word_is_read_with_the_packs_the_collection_names() -> None:
    unread = sorted(word for word in dictionary_words() if not read_by(COLLECTION, word))

    assert not unread


def test_the_packs_are_shipped_and_each_pack_word_needs_its_pack() -> None:
    vocabulary = words()
    shipped = {section.domain for section in SECTIONS if section.domain}
    listed = {word.casefold() for names in vocabulary.packs.values() for word in names}

    assert set(vocabulary.packs) <= shipped
    assert listed <= {word.casefold() for word in dictionary_words()}
    for pack, names in vocabulary.packs.items():
        alone = store_of([pack])
        for word in names:
            assert not read_by(CORE, word), f"{word} is read without {pack}"
            assert read_by(alone, word), f"{word} is not read by {pack} alone"


def test_every_other_dictionary_word_is_read_by_the_core_alone() -> None:
    listed = {word.casefold() for names in words().packs.values() for word in names}
    unread = sorted(
        word
        for word in dictionary_words()
        if word.casefold() not in listed and not read_by(CORE, word)
    )

    assert not unread


#: The common biology words of the collection that the shipped packs read. Every other biology word
#: of the collection is unread, so the collection keeps words the Dictionary does not know.
BIOLOGY_WORDS_NOW_READ = frozenset(
    {
        "COS7",
        "HEK293T",
        "HeLa",
        "U2OS",
        "DMSO",
        "cycloheximide",
        "nocodazole",
        "thapsigargin",
        "tunicamycin",
    }
)


def test_no_everyday_word_or_name_is_read_and_only_the_named_public_biology_words_are() -> None:
    vocabulary = words()
    chosen = {
        "biology": [word for group in vocabulary.biology.values() for word in group],
        "everyday": [word for group in vocabulary.everyday.values() for word in group],
        "people": [*vocabulary.first_names, *vocabulary.family_names],
    }
    for section, names in chosen.items():
        read = sorted(word for word in names if read_by(EVERYTHING, word))
        if section == "biology":
            assert set(read) == BIOLOGY_WORDS_NOW_READ, (section, read)
        else:
            assert not read, (section, read)
    # The collection still holds common biology words that nothing reads, for its unread share.
    assert [word for word in chosen["biology"] if word not in BIOLOGY_WORDS_NOW_READ]


def test_no_word_is_in_two_sections() -> None:
    vocabulary = words()
    seen: dict[str, str] = {}
    for kind, groups in (
        ("dictionary", vocabulary.dictionary),
        ("biology", vocabulary.biology),
        ("everyday", vocabulary.everyday),
    ):
        for name, group in groups.items():
            for word in group:
                key = word.casefold()
                if len(key) == 1:
                    continue
                assert seen.get(key, kind).split(".")[0] == kind, (
                    f"{word} in {kind}.{name} and {seen[key]}"
                )
                seen[key] = f"{kind}.{name}"
    people = [*vocabulary.first_names, *vocabulary.family_names]
    assert len({name.casefold() for name in people}) == len(people)
    assert not {name.casefold() for name in people} & set(seen)
