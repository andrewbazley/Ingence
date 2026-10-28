"""Tokenising prose into keywords that a search can match.

The rules for a token:

- Unicode NFKC, curly apostrophes straightened, lower case for matching.
- Internal apostrophes and hyphens stay inside a token (``don't``, ``protein-binding``).
- Scientific identifiers survive: ``p53``, ``Ca2+``, ``5-HT`` are kept (as ``p53``, ``ca2+``,
  ``5-ht``), because a digit is not a reason to drop a word.
- Pure numbers and measurement values are dropped (``500``, ``2.5h``, ``10 mM`` as a value).
- There is no stemming and no lemmatisation. A measurement found that stemming did not help.

One more rule applies to short capitalised words. The token keeps the written case beside the
folded one, because lowercasing merged ``ER`` (the organelle) with dialect ``er``. A token carries
both, and a search or a contrast can choose.

This module reads text it is handed and never a file: an extractor (``extractors/keywords.py``)
hands it a bounded sample, as the text sketch does.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["DEFAULT_STOPWORDS", "ProseToken", "count_terms", "prose_tokens"]

#: A token: letters, digits, internal apostrophes and hyphens, and a trailing charge sign.
_TOKEN = re.compile(
    r"[0-9A-Za-z\u00C0-\u024F\u0370-\u03FF]+(?:['\-][0-9A-Za-z\u00C0-\u024F\u0370-\u03FF]+)*[+\-]?"
)

#: A number, or a number glued to a unit: a value, never a keyword.
_VALUE = re.compile(r"^[0-9]+(?:[.,][0-9]+)*(?:e[+-]?[0-9]+)?[a-z\u00B5\u03BC%/]{0,5}$")

#: A short list of English function words that a keyword list must not lead with. It is not a
#: full standard stop list. It is a parameter.
DEFAULT_STOPWORDS: frozenset[str] = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "fig",
        "figure",
        "table",
        "et",
        "al",
        "using",
        "used",
        "use",
        "were",
        "was",
        "data",
        "sample",
        "samples",
    ]
)


@dataclass(frozen=True, slots=True)
class ProseToken:
    """One token of prose: folded for matching, written for display and for the case channel."""

    folded: str
    written: str

    @property
    def case_key(self) -> str:
        """The written form for a short all-capital word (``ER``, ``GFP``), else the folded one."""
        letters = [c for c in self.written if c.isalpha()]
        if 2 <= len(self.written) <= 6 and letters and all(c.isupper() for c in letters):
            return self.written
        return self.folded


def prose_tokens(text: str, *, stopwords: frozenset[str] = DEFAULT_STOPWORDS) -> list[ProseToken]:
    """Every keyword-eligible token of a text, in order."""
    normalised = unicodedata.normalize("NFKC", text).replace("\u2019", "'").replace("\u2018", "'")
    found: list[ProseToken] = []
    after_a_number = False
    for match in _TOKEN.finditer(normalised):
        written = match.group().strip("-'")
        folded = written.casefold()
        if _VALUE.match(folded):
            after_a_number = True
            continue
        # A short word straight after a number is its unit (``10 mM``, ``5 min``), a value's
        # second half and not a keyword. Three letters, the case-piece minimum, bounds it.
        unit_like = after_a_number and len(folded) <= 3 and folded.isalpha()
        after_a_number = False
        if len(folded) < 2 or folded in stopwords or unit_like:
            continue
        found.append(ProseToken(folded=folded, written=written))
    return found


def count_terms(
    tokens: Iterable[ProseToken], *, limit: int = 200, case_channel: bool = True
) -> list[tuple[str, int]]:
    """The ``limit`` most frequent terms, commonest first, ties by the term: a bounded reading."""
    counts = Counter(token.case_key if case_channel else token.folded for token in tokens)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
