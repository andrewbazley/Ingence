"""A word one or two slips from a word the Dictionary knows, proposed as its spelling.

The engine sometimes asks about a word that is a known word misspelt. A person types a letter
twice, drops one or swaps two, and the word is then one that nobody taught the engine. The engine
proposes the known spellings beside the word's question and never binds them. A person takes or
leaves a proposal, because a word a slip from another is as often a different word (a construct's
name, an abbreviation) as a slip.

A proposal covers one or two edits, on words of six letters or more (``unknwon`` for unknown,
``filamemt`` for filament, ``CyroEM`` for cryo-EM). A letter added, dropped or changed, or two
neighbouring letters swapped, is one edit each (the optimal string alignment distance). Shorter
words are two edits from too many others to propose anything. The engine offers every candidate,
nearest first, up to a number. Where two candidates are equally near, the engine shows both and the
person says which. All three limits are settings (``dictionary.spelling``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "CANDIDATES",
    "CORRECTION_ONE_EDIT_LETTERS",
    "CORRECTION_TWO_EDIT_LETTERS",
    "EDITS",
    "MINIMUM_LETTERS",
    "Proposal",
    "SpellingIndex",
    "edits_apart",
    "one_edit_apart",
]

#: The shortest known word a slip is proposed for, and the shortest word proposed from. With two
#: edits allowed, a five-letter word is two edits from dozens of others.
MINIMUM_LETTERS = 6

#: How many edits a slip may be from the word it is proposed for.
EDITS = 2

#: The fewest letters a word has to be bound as a slip of a Dictionary spelling: five letters at one
#: edit, and seven at two edits, and then only with the same letters in another order (``hoescht``
#: for ``hoechst``). A variant that kept the first two and last letters admitted too many English
#: words, so the engine does not use it. Shorter words are a slip of too many others. A binding,
#: unlike a proposal, is a reading, so these limits are stricter than :data:`MINIMUM_LETTERS` and
#: :data:`EDITS` and are not settings.
CORRECTION_ONE_EDIT_LETTERS = 5
CORRECTION_TWO_EDIT_LETTERS = 7

#: How many candidates a word's question shows, nearest first: three, as many as a person reads at
#: a glance beside the question.
CANDIDATES = 3

#: What a spelling is compared without: the separators a name writes between a compound's parts,
#: so ``cryo-em`` is offered for ``CyroEM``, a word written whole.
_SEPARATORS = str.maketrans("", "", " _-.")


def edits_apart(first: str, second: str, *, limit: int = EDITS) -> int:
    """Return how many edits apart two words are, or ``limit + 1`` where more than ``limit``.

    This is the optimal string alignment distance: a letter added, dropped or changed, or two
    neighbouring letters swapped, is one edit each, and no substring is edited twice. The function
    stops as soon as every way on is past the limit, which keeps a whole Dictionary's comparison
    cheap.
    """
    if abs(len(first) - len(second)) > limit:
        return limit + 1
    previous_row: list[int] | None = None
    row = list(range(len(second) + 1))
    for i in range(1, len(first) + 1):
        current = [i] + [0] * len(second)
        for j in range(1, len(second) + 1):
            cost = 0 if first[i - 1] == second[j - 1] else 1
            current[j] = min(row[j] + 1, current[j - 1] + 1, row[j - 1] + cost)
            if (
                previous_row is not None
                and i > 1
                and j > 1
                and first[i - 1] == second[j - 2]
                and first[i - 2] == second[j - 1]
            ):
                current[j] = min(current[j], previous_row[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous_row, row = row, current
    return min(row[-1], limit + 1)


def one_edit_apart(first: str, second: str) -> bool:
    """Whether two words differ by one letter added, dropped or changed, or two letters swapped."""
    return first != second and edits_apart(first, second, limit=1) == 1


def _deletions(word: str, depth: int) -> set[str]:
    """Every word ``word`` becomes with up to ``depth`` letters dropped, itself included."""
    found = {word}
    frontier = {word}
    for _ in range(depth):
        frontier = {
            item[:index] + item[index + 1 :] for item in frontier for index in range(len(item))
        }
        found |= frontier
    return found


@dataclass(frozen=True, slots=True)
class Proposal:
    """A known spelling a word is a slip from, the terms it spells (one, or a few), and how near."""

    spelling: str
    terms: tuple[str, ...]
    #: How many edits the word is from the spelling: one or two by default.
    edits: int = 1


class SpellingIndex:
    """The known spellings of letters alone, indexed so a word's near neighbours are found fast.

    Built from ``(term, spelling)`` pairs; a spelling is compared casefolded and without the
    separators a name writes between a compound's parts (``cryo-em`` as ``cryoem``), and left out
    where what remains holds anything but letters (``tar.gz`` is kept as ``targz``; ``2p`` is not).
    Where several spellings compare alike, the first in sorted order is the one shown.
    """

    def __init__(
        self,
        spellings: Iterable[tuple[str, str]],
        *,
        minimum: int = MINIMUM_LETTERS,
        edits: int = EDITS,
        candidates: int = CANDIDATES,
    ) -> None:
        self.minimum = minimum
        self.edits = edits
        self.candidates_shown = candidates
        self._term: dict[str, set[str]] = defaultdict(set)
        self._shown: dict[str, str] = {}
        self._by_deletion: dict[str, set[str]] = defaultdict(set)
        for term, spelling in spellings:
            compared = spelling.casefold().translate(_SEPARATORS)
            if len(compared) < minimum or not compared.isalpha():
                continue
            self._term[compared].add(term)
            shown = self._shown.get(compared)
            self._shown[compared] = (
                spelling.casefold() if shown is None else min(shown, spelling.casefold())
            )
            for deleted in _deletions(compared, edits):
                self._by_deletion[deleted].add(compared)

    def candidates(self, word: str) -> tuple[Proposal, ...]:
        """Return every known spelling that ``word`` is a slip of, nearest first.

        The result holds up to the number set. The word and each spelling are at least
        :attr:`minimum` letters. A plural is a spelling of its word, never a slip (a trailing ``s``
        added or dropped is not proposed). A word that the Dictionary spells is not a slip at all.
        A spelling that several senses share is proposed with every one of them. Ties in nearness
        are in alphabetical order.
        """
        folded = word.casefold().translate(_SEPARATORS)
        if folded in self._term or not folded.isalpha() or len(folded) < self.minimum:
            return ()
        near: set[str] = set()
        for key in _deletions(folded, self.edits):
            near.update(self._by_deletion.get(key, ()))
        found = sorted(
            (distance, compared)
            for compared in near
            if compared + "s" != folded
            and folded + "s" != compared
            and 0 < (distance := edits_apart(folded, compared, limit=self.edits)) <= self.edits
        )
        return tuple(
            Proposal(
                spelling=self._shown[compared],
                terms=tuple(sorted(self._term[compared])),
                edits=distance,
            )
            for distance, compared in found[: self.candidates_shown]
        )

    def near(self, word: str) -> tuple[Proposal, ...]:
        """Return every spelling in this index within the edits of ``word``, nearest first.

        This differs from :meth:`candidates` in three ways. It does not stop at a word the index
        spells (a word is never its own neighbor, but its neighbors are found). It is not cut at
        the number of candidates. A plural is a neighbor like any other, and the caller decides
        what a plural is. Built on a set of the run's own unknown words, it compares them with each
        other, where :meth:`candidates` compares a word only with what the Dictionary spells. Both
        words are at least :attr:`minimum` letters, of letters alone. Ties go in alphabetical
        order.
        """
        folded = word.casefold().translate(_SEPARATORS)
        if not folded.isalpha() or len(folded) < self.minimum:
            return ()
        near: set[str] = set()
        for key in _deletions(folded, self.edits):
            near.update(self._by_deletion.get(key, ()))
        found = sorted(
            (distance, compared)
            for compared in near
            if compared != folded
            and 0 < (distance := edits_apart(folded, compared, limit=self.edits)) <= self.edits
        )
        return tuple(
            Proposal(
                spelling=self._shown[compared],
                terms=tuple(sorted(self._term[compared])),
                edits=distance,
            )
            for distance, compared in found
        )

    def propose(self, word: str) -> Proposal | None:
        """The one known spelling nearest ``word``, or None where there is none or a tie for it.

        This is the single proposal, kept for the readers that show one: the nearest of
        :meth:`candidates`, where no other candidate is as near.
        """
        found = self.candidates(word)
        if not found or (len(found) > 1 and found[1].edits == found[0].edits):
            return None
        return found[0]
