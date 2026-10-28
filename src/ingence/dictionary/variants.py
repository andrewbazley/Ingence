"""The spellings that may be the same word as another: what the Words section offers.

A person who clarifies their own words (what a word means, which spellings are one thing) makes
the engine read their folders better. The engine can offer them the likely pairs so that saying so
is one click. The engine only offers. A variant is never bound, never read by a run, and carries
no probability, only a reason and the files that hold it.

There are six sources, each switched by its own setting (``dictionary.variants``) and each tested
on its own:

1. *Slips*: a spelling one or two letters from a word the Dictionary knows
   (:class:`~ingence.dictionary.spelling.SpellingIndex`, whose limits are ``dictionary.spelling``),
   and, extended here, from another word of the run that no sense holds.
2. *Case and separators*: two unknown spellings alike once capitals and separators are taken out
   (the key the Dictionary's loose match uses, ``store._loose_key``).
3. *Plural and singular*: the rule the themes use (:func:`~ingence.themes.words.plural_folds`).
4. *Glued*: the pieces of a spelling a capital divides, and the spelling a piece is part of,
   with the counts the case reading used.
5. *Initials*: a short spelling written in capitals whose letters are the initials of a known
   multi-part word, where both are in the run.
6. *Same slot*: two spellings that stand where the other could, in names of folders close
   together, never in one name, with the same words beside them at the same distances.
   Two spellings that take each other's place are as often two settings of one condition (a
   control and a treated sample) as one word spelled twice. So the page offers "different things
   of one kind" first for these (:attr:`Variant.offer`).

The order is nearest first (a slip by its edits, the exact relations first), then the files
holding the spelling, then the spelling, so the order is stable. The setting
``dictionary.variants.shown`` gives how many are shown.

A pair that a person called unrelated (``distinct:<a>+<b>``, a rejection in the Record) is never
proposed again. :func:`distinct_pairs` reads these pairs, and every source passes through the same
filter.

Everything is a function of the run's words, the Dictionary and the Record. The module stores
nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from ingence.core.schema import RecordEventKind
from ingence.dictionary.spelling import SpellingIndex
from ingence.dictionary.store import _loose_key
from ingence.record.log import latest_by_target, read_events
from ingence.record.targets import distinct_named_by
from ingence.themes.words import plural_folds

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from ingence.core.settings import SpellingSettings, VariantSettings
    from ingence.dictionary.store import DictionaryStore

__all__ = [
    "REASONS",
    "GluedSpelling",
    "RunWord",
    "Variant",
    "VariantIndex",
    "distinct_pairs",
    "variants_of",
]

#: Each reason's code, in the order a page lists them, with its plain words. A word that depends
#: on the direction of the relation is chosen where the variant is made.
REASONS: dict[str, str] = {
    "case": "the same word in other capitals",
    "separators": "the same word with other separators",
    "plural": "a plural of it",
    "singular": "its singular",
    "glued": "its pieces",
    "glued_in": "it joined to another word",
    "initials": "its initials",
    "spelled_out": "what its initials stand for",
    "slip": "a slip of it",
    "same_slot": "they take each other's place",
}

_LETTERS = re.compile(r"[^a-z]+")

#: Where two spellings that take each other's place stand among the others: after every slip, which
#: is at most the few edits the setting allows.
SAME_SLOT_RANK = 9


@dataclass(frozen=True, slots=True)
class RunWord:
    """One word of the run as the variants read it: its counts, its binding and its company."""

    #: The word as the census stores it.
    word: str
    files: int
    occurrences: int = 0
    #: The spellings the names write it in, as written (capitals kept).
    raw: tuple[str, ...] = ()
    #: The Dictionary term a sense binds it to, or None where none does (or two claim it level).
    term: str | None = None
    #: The names carrying it, and the words beside it in those names (each may be tagged with its
    #: distance, ``+1:dapi``), and the folders they lie in, where the run's names were read for the
    #: comparison of spellings that take each other's place; empty otherwise (the source then
    #: offers nothing).
    names: frozenset[int] = frozenset()
    neighbors: frozenset[str] = frozenset()
    places: frozenset[str] = frozenset()

    @property
    def unknown(self) -> bool:
        return self.term is None


@dataclass(frozen=True, slots=True)
class GluedSpelling:
    """A spelling a capital divides, with its pieces, as the case reading counted them."""

    written: str
    pieces: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Variant:
    """A spelling that may be the same word as another, why, and how many files hold it."""

    spelling: str
    files: int
    occurrences: int
    #: One of :data:`REASONS`' codes, and its plain words.
    reason: str
    reason_said: str
    #: How many edits apart, for a slip. It is None where a distance does not apply.
    distance: int | None = None
    #: The Dictionary term the spelling is bound to, where it is (a slip's target always is).
    known_as: str | None = None
    #: Which choice the page offers first for the pair: ``same`` or ``kind``.
    offer: str = "same"

    def order(self) -> tuple[int, int, str]:
        """Nearest first, then the most files, then the spelling."""
        if self.distance is not None:
            rank = self.distance
        else:
            rank = SAME_SLOT_RANK if self.reason == "same_slot" else 0
        return (rank, -self.files, self.spelling)


def distinct_pairs(project: Path) -> frozenset[tuple[str, str]]:
    """The pairs of spellings a person called unrelated, each sorted: a rejection in force."""
    found: set[tuple[str, str]] = set()
    for target, event in latest_by_target(read_events(project)).items():
        pair = distinct_named_by(target)
        if pair is not None and event.kind is RecordEventKind.REJECT:
            found.add(pair)
    return frozenset(found)


def _pair(first: str, second: str) -> tuple[str, str]:
    one, other = sorted((first.casefold(), second.casefold()))
    return one, other


def _initials(parts: str) -> str:
    """The first letters of a multi-part word's alphabetic parts, joined."""
    return "".join(part[0] for part in parts.split() if part and part[0].isalpha())


def _capitals(word: RunWord, minimum: int, maximum: int) -> bool:
    """Whether the run writes the word in capitals, letters alone, within the size initials take."""
    return any(
        raw.isalpha() and raw.isupper() and minimum <= len(raw) <= maximum for raw in word.raw
    )


class VariantIndex:
    """The run's words, indexed once, so each word's variants are found without a scan.

    ``words`` are the run's words by the word as stored; ``store`` is the Dictionary (read for the
    known words' spellings, unless ``known`` is handed in already built); ``glued`` the spellings
    a capital divided, by the casefolded whole; ``distinct`` the pairs a person called unrelated.
    """

    def __init__(
        self,
        words: Mapping[str, RunWord],
        *,
        settings: VariantSettings,
        spelling: SpellingSettings,
        store: DictionaryStore | None = None,
        known: SpellingIndex | None = None,
        glued: Mapping[str, GluedSpelling] | None = None,
        distinct: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self.words = dict(words)
        self.settings = settings
        self.spelling = spelling
        self.distinct = distinct
        self.glued = dict(glued or {})
        self._unknown = {word: found for word, found in self.words.items() if found.unknown}
        # The switch decides whether the Dictionary's spellings are compared, whoever built them.
        self._known = (
            None
            if not settings.slips
            else (known if known is not None else self._known_index(store))
        )
        self._among = (
            SpellingIndex(
                ((word, word) for word in self._unknown),
                minimum=spelling.minimum_letters,
                edits=spelling.edits,
                candidates=max(1, len(self._unknown)),
            )
            if settings.slips_among_unknown
            else None
        )
        self._loose: dict[str, list[str]] = {}
        if settings.case_and_separators:
            for word in self._unknown:
                key = _loose_key(word)
                if key:
                    self._loose.setdefault(key, []).append(word)
        self._folds = plural_folds(self._unknown, {}) if settings.plurals and self._unknown else {}
        self._plurals: dict[str, list[str]] = {}
        for plural, singular in self._folds.items():
            self._plurals.setdefault(singular, []).append(plural)
        self._spelled_out: dict[str, list[str]] = {}
        if settings.initials:
            for word, found in self.words.items():
                if " " in word and not found.unknown:
                    letters = _initials(word)
                    if len(letters) >= settings.initials_minimum_letters:
                        self._spelled_out.setdefault(letters, []).append(word)
        self._places = self._same_slot() if settings.same_slot else {}

    # -- construction ------------------------------------------------------------------------

    def _known_index(self, store: DictionaryStore | None) -> SpellingIndex | None:
        if store is None or not self.settings.slips:
            return None
        return SpellingIndex(
            ((sense.term, surface) for sense in store.senses() for surface in sense.surfaces),
            minimum=self.spelling.minimum_letters,
            edits=self.spelling.edits,
            candidates=self.spelling.candidates,
        )

    def _same_slot(self) -> dict[str, list[str]]:
        """Pairs of unknown spellings standing in each other's place, by the word.

        Words are taken most files first, up to ``same_slot_words``. Two are candidates when they
        share at least ``same_slot_neighbors`` words beside them (a word in more than
        ``same_slot_hub_names`` names is in every name and says nothing), are never in one name,
        and lie in the same folder or in folders with one parent.
        """
        limit = self.settings.same_slot_words
        ranked = sorted(self._unknown.values(), key=lambda found: (-found.files, found.word))[
            :limit
        ]
        pool = {found.word: found for found in ranked if found.names}
        shared_by: dict[str, list[str]] = {}
        for word, found in pool.items():
            for other in found.neighbors:
                shared_by.setdefault(other, []).append(word)
        # A neighbor is a word, possibly tagged with its distance (``+1:dapi``); it is a hub where
        # more names carry it than the setting allows, whoever else it stands beside.
        hubs = {
            other
            for other in shared_by
            if self.words.get(other.rpartition(":")[2], RunWord(other, 0)).files
            > self.settings.same_slot_hub_names
        }
        counted: dict[tuple[str, str], int] = {}
        for other, holders in shared_by.items():
            if other in hubs or len(holders) < 2:
                continue
            for first, second in combinations(sorted(holders), 2):
                counted[(first, second)] = counted.get((first, second), 0) + 1
        found_pairs: dict[str, list[str]] = {}
        for (first, second), shared in counted.items():
            if shared < self.settings.same_slot_neighbors:
                continue
            held_first, held_second = pool[first], pool[second]
            if held_first.names & held_second.names:
                continue  # in one name together: two words of the name, not one for the other
            if not _near(held_first.places, held_second.places):
                continue
            found_pairs.setdefault(first, []).append(second)
            found_pairs.setdefault(second, []).append(first)
        return found_pairs

    # -- the variants of a word --------------------------------------------------------------

    def of(self, word: str) -> tuple[Variant, ...]:
        """The variants of ``word``, nearest first, at most ``shown``; none that was ruled out."""
        key = word.casefold()
        found: dict[str, Variant] = {}

        def add(
            spelling: str,
            reason: str,
            *,
            said: str | None = None,
            distance: int | None = None,
            known_as: str | None = None,
        ) -> None:
            folded = spelling.casefold()
            if spelling == word or _pair(key, folded) in self.distinct:
                return
            held = self.words.get(spelling) or self.words.get(folded)
            variant = Variant(
                spelling=spelling,
                files=held.files if held is not None else 0,
                occurrences=held.occurrences if held is not None else 0,
                reason=reason,
                reason_said=said or REASONS[reason],
                distance=distance,
                known_as=known_as,
                offer="kind" if reason == "same_slot" else "same",
            )
            kept = found.get(spelling)
            if kept is None or variant.order() < kept.order():
                found[spelling] = variant

        own = self.words.get(word) or self.words.get(key)
        # 1. Slips: toward a word the Dictionary knows, and among the run's unknown words.
        if self._known is not None and (own is None or own.unknown):
            for near in self._known.candidates(key):
                add(
                    near.spelling,
                    "slip",
                    said=_edits_said(near.edits),
                    distance=near.edits,
                    known_as=near.terms[0] if near.terms else None,
                )
        if self._among is not None and key in self._unknown:
            for near in self._among.near(key):
                if near.spelling in self._unknown and not _plural_pair(key, near.spelling):
                    add(near.spelling, "slip", said=_edits_said(near.edits), distance=near.edits)
        # 2. Case and separators among the unknown.
        if key in self._unknown:
            for other in self._loose.get(_loose_key(key), ()):
                if other != key:
                    reason = "case" if other.casefold() == key.casefold() else "separators"
                    add(other, reason)
        # 3. Plural and singular among the unknown.
        if key in self._unknown:
            if key in self._folds:
                add(self._folds[key], "singular")
            for plural in self._plurals.get(key, ()):
                add(plural, "plural")
        # 4. Glued: the pieces of a spelling a capital divides, and the spellings holding a piece.
        if self.settings.glued:
            whole = self.glued.get(key)
            if whole is not None:
                for piece in whole.pieces:
                    add(piece.casefold(), "glued")
            for held_whole in self.glued.values():
                if key in (piece.casefold() for piece in held_whole.pieces):
                    add(held_whole.written.casefold(), "glued_in")
        # 5. Initials, both ways, where both are in the run.
        if self.settings.initials and own is not None:
            if own.unknown and _capitals(
                own, self.settings.initials_minimum_letters, self.settings.initials_maximum_letters
            ):
                for spelled in self._spelled_out.get(_LETTERS.sub("", key), ()):
                    add(spelled, "spelled_out")
            if " " in key and not own.unknown:
                letters = _initials(key)
                for word_, found_ in self._unknown.items():
                    if _LETTERS.sub("", word_) == letters and _capitals(
                        found_,
                        self.settings.initials_minimum_letters,
                        self.settings.initials_maximum_letters,
                    ):
                        add(word_, "initials")
        # 6. Same slot, among the unknown.
        for other in self._places.get(key, ()):
            add(other, "same_slot")
        return tuple(sorted(found.values(), key=Variant.order)[: self.settings.shown])


def _edits_said(edits: int) -> str:
    return "one letter apart" if edits == 1 else f"{_NUMBERS.get(edits, edits)} letters apart"


_NUMBERS = {2: "two", 3: "three"}


def _plural_pair(first: str, second: str) -> bool:
    """Whether one word is the other with a plural ending: the plural source says it, not a slip."""
    one, other = sorted((first, second), key=len)
    return other in {f"{one}s", f"{one}es"} or (one.endswith("y") and other == f"{one[:-1]}ies")


def _near(first: Iterable[str], second: Iterable[str]) -> bool:
    """Whether two sets of folders hold a pair in one folder, or in folders with one parent."""
    places = tuple(second)
    for one in first:
        parent = one.rpartition("/")[0]
        for other in places:
            if one == other or parent == other.rpartition("/")[0]:
                return True
    return False


def variants_of(
    word: str,
    *,
    run_words: Mapping[str, RunWord],
    store: DictionaryStore | None,
    settings: VariantSettings,
    spelling: SpellingSettings | None = None,
    glued: Mapping[str, GluedSpelling] | None = None,
    distinct: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[Variant, ...]:
    """The spellings that may be the same word as ``word``, each with why, nearest first.

    Builds the index each call; a caller asking for many words builds a :class:`VariantIndex`
    once and calls :meth:`VariantIndex.of`.
    """
    from ingence.core.settings import SpellingSettings as Defaults

    return VariantIndex(
        run_words,
        settings=settings,
        spelling=spelling if spelling is not None else Defaults(),
        store=store,
        glued=glued,
        distinct=distinct,
    ).of(word)
