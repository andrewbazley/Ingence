"""The words two lines of work are made of in common.

The Overlap view of the Years page shades a table of every line against every other by how many of
their words they share, and names the words on hover. The engine finds them here, so the page
draws and counts nothing. A pair is listed only if it shares at least one word. It is listed once,
with the lower theme number first. A pair that is not listed shares none.

A word is compared without regard to case (``HeLa`` and ``hela`` are one word), and each pair
gives the words in the spelling and order of the first line's own list, which ranks its words by
how well they set it apart.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["shared_words"]


def shared_words(themes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every pair of themes with a word in common: ``theme_a`` < ``theme_b``, their shared
    ``words``, how many (``shared``), and the size of the shorter word list (``of_smaller``)."""
    by_id = {int(theme["theme"]): theme for theme in themes}
    holders: dict[str, list[int]] = defaultdict(list)
    for theme_id, theme in sorted(by_id.items()):
        for word in {str(word).casefold() for word in theme.get("words", ())}:
            holders[word].append(theme_id)
    together: dict[tuple[int, int], set[str]] = defaultdict(set)
    for word, ids in holders.items():
        for first, second in combinations(ids, 2):
            together[first, second].add(word)
    pairs: list[dict[str, Any]] = []
    for (first, second), common in sorted(together.items()):
        own = [
            str(word) for word in by_id[first].get("words", ()) if str(word).casefold() in common
        ]
        listed = list(dict.fromkeys(own))
        pairs.append(
            {
                "theme_a": first,
                "theme_b": second,
                "words": listed,
                "shared": len(listed),
                "of_smaller": min(
                    len(by_id[first].get("words", ())), len(by_id[second].get("words", ()))
                ),
            }
        )
    return pairs
