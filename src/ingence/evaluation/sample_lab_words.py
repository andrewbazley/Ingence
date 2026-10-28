"""The words of the sample collection, read from ``sample_lab_words.yaml``.

The file is the one list of every word a person can read in a name of the collection. Each word is
in the shipped Dictionary, is a common biology word (the Dictionary reads some of them and not the
rest), is an ordinary English word, or belongs to a made-up person. A test holds each section to
what it says.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["WORDS_FILE", "Words", "words"]

WORDS_FILE = Path(__file__).with_name("sample_lab_words.yaml")


def _list(section: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(str(word) for word in section[key])


@dataclass(frozen=True)
class Words:
    """The sections of the vocabulary, as tuples in the file's order."""

    first_names: tuple[str, ...]
    family_names: tuple[str, ...]
    dictionary: dict[str, tuple[str, ...]]
    biology: dict[str, tuple[str, ...]]
    everyday: dict[str, tuple[str, ...]]
    files: dict[str, tuple[str, ...]]
    #: pack name -> the dictionary words only that pack reads.
    packs: dict[str, tuple[str, ...]]

    def dictionary_words(self) -> frozenset[str]:
        return frozenset(word.casefold() for group in self.dictionary.values() for word in group)

    def biology_words(self) -> frozenset[str]:
        return frozenset(word.casefold() for group in self.biology.values() for word in group)

    def everyday_words(self) -> frozenset[str]:
        return frozenset(word.casefold() for group in self.everyday.values() for word in group)

    def file_words(self) -> frozenset[str]:
        return frozenset(word.casefold() for group in self.files.values() for word in group)

    def pack_names(self) -> tuple[str, ...]:
        """The packs the collection's words need, beside the core."""
        return tuple(self.packs)

    def people_words(self) -> frozenset[str]:
        return frozenset(word.casefold() for word in (*self.first_names, *self.family_names))

    def everything(self) -> frozenset[str]:
        """Every word a name of the collection may hold."""
        return (
            self.dictionary_words()
            | self.biology_words()
            | self.everyday_words()
            | self.file_words()
            | self.people_words()
        )


def _read() -> Words:
    loaded = yaml.safe_load(WORDS_FILE.read_text(encoding="utf-8"))
    people = loaded["people"]
    return Words(
        first_names=_list(people, "first"),
        family_names=_list(people, "family"),
        dictionary={key: _list(loaded["dictionary"], key) for key in loaded["dictionary"]},
        biology={key: _list(loaded["biology"], key) for key in loaded["biology"]},
        everyday={key: _list(loaded["everyday"], key) for key in loaded["everyday"]},
        files={key: _list(loaded["files"], key) for key in loaded["files"]},
        packs={key: _list(loaded["packs"], key) for key in loaded["packs"]},
    )


_CACHE: list[Words] = []


def words() -> Words:
    """The vocabulary (read once)."""
    if not _CACHE:
        _CACHE.append(_read())
    return _CACHE[0]
