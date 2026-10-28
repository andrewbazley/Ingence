"""A name family read beside its folder's name.

The inference pass does not call this module. ``search/tags.py`` does the same reading in SQL.

The parts of one experiment carry words of their own. The folder's name lists what they share and
what varies, and it can help define what is common and what is individual among the experiment's
members. Only certain class keys apply to certain files.

The module reads grammar over one folder's name and its members' names:

- shared: the folder's words every member restates, which is what the experiment's members share;
- varying: the folder's words only some members restate. These are the keys that apply to some
  files and not others, each naming a subgroup (``ATP`` and ``ADP`` in a folder named
  ``WT kelt ATP ADP``);
- subgroups: the members grouped by which varying words each restates, so that one-thing-or-mixed
  is decided per subgroup and not for the folder at once;
- abbreviations: a one-letter token that is the initial of a folder word no member spells out,
  read as that word (``-C`` in a folder named ``Cropped``);
- context: the folder's words no member restates, which describe every member (a folder named
  ``For <program>`` says what its files are for).

Words are read with the engine's own tokeniser, case folded. Numbers, indexes, dates and versions
vary between replicates and are never a key. Every name in the examples is invented.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.dictionary.normalize import AtomKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.dictionary.normalize import Normaliser

__all__ = ["FolderFamily", "folder_family"]

#: The atoms that say what a file is about rather than which replicate it is.
_KEYED = frozenset({AtomKind.WORD, AtomKind.QUANTITY})

#: Words that introduce a folder's purpose rather than name its members.
_PURPOSE = frozenset({"for"})


@dataclass(frozen=True, slots=True)
class FolderFamily:
    """What a folder's name says about its members."""

    shared: tuple[str, ...]
    varying: tuple[str, ...]
    subgroups: dict[tuple[str, ...], tuple[str, ...]] = field(default_factory=dict)
    abbreviations: dict[str, str] = field(default_factory=dict)
    context: tuple[str, ...] = ()

    @property
    def divides(self) -> bool:
        """Whether the folder's own words split its members into more than one subgroup."""
        return len(self.subgroups) > 1


def _words(name: str, normaliser: Normaliser) -> list[str]:
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    return [atom.text.casefold() for atom in normaliser.atoms(stem) if atom.kind in _KEYED]


def _letters(name: str, normaliser: Normaliser) -> set[str]:
    """One-letter tokens a name holds, case folded, which may abbreviate a folder word."""
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    return {
        atom.text.casefold()
        for atom in normaliser.atoms(stem)
        if len(atom.text) == 1 and atom.text.isalpha()
    }


def folder_family(folder: str, members: Sequence[str], normaliser: Normaliser) -> FolderFamily:
    """Read one folder's name beside its members' names."""
    folder_words = [word for word in _words(folder, normaliser) if word not in _PURPOSE]
    restated: dict[str, set[str]] = defaultdict(set)
    words_of = {member: set(_words(member, normaliser)) for member in members}
    for member, words in words_of.items():
        for word in folder_words:
            if word in words:
                restated[word].add(member)
    everyone = set(members)
    shared = tuple(word for word in dict.fromkeys(folder_words) if restated[word] == everyone)
    varying = tuple(
        word
        for word in dict.fromkeys(folder_words)
        if restated[word] and restated[word] != everyone
    )
    unspoken = [word for word in dict.fromkeys(folder_words) if not restated[word]]
    abbreviations: dict[str, str] = {}
    for word in unspoken:
        initial = word[0]
        if len(word) > 1 and all(initial in _letters(member, normaliser) for member in members):
            abbreviations[initial] = word
    subgroups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for member in members:
        key = tuple(word for word in varying if member in restated[word])
        subgroups[key].append(member)
    context = tuple(word for word in unspoken if word not in abbreviations.values())
    return FolderFamily(
        shared=shared,
        varying=varying,
        subgroups={key: tuple(sorted(names)) for key, names in sorted(subgroups.items())},
        abbreviations=abbreviations,
        context=context,
    )
