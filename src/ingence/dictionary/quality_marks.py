"""The quality marks the Dictionary declares, and what each says in plain words.

A quality mark is a sense of the ``quality`` kind carrying ``mark`` (``quality_marks.yaml``). The
marks are ``good`` and the poor mark that a person writes when the work looks unwell. A mark
records what a name says and no more. It is never a doubt (the doubt rules count only the terms
they name), never a conclusion about the work, and never a reason to leave anything out. This
module reads the marks from the vocabulary in force, so a pack or a person's answer that adds a
mark is read the same way. The module also words each mark for a person reviewing. The entry's
readings and a search card's chip show it, read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from ingence.dictionary.store import DictionaryStore

__all__ = ["QualityMark", "cancel_pattern", "cancelled_by", "negated_throughout", "quality_marks"]

_WORDS = re.compile(r"[^0-9a-z]+")

#: What each mark means, in plain words, after the sense's own canonical words.
_SAID: dict[str, str] = {
    "good": "by its name: what the name says, never a conclusion that the work is good",
    "poor": "by its name: a mark on quality, never a doubt, and nothing is left out for it",
}


@dataclass(frozen=True, slots=True)
class QualityMark:
    """One quality mark a sense declares."""

    term: str
    #: ``good`` or ``poor``, as the sense declares it.
    mark: str
    #: The plain words a person reads beside the mark.
    said: str
    #: The words that cancel the mark where one stands directly before it (``not sick``).
    negated_by: tuple[str, ...]
    #: The spellings a name writes it with.
    surfaces: tuple[str, ...]


def quality_marks(store: DictionaryStore) -> dict[str, QualityMark]:
    """Every sense of the quality kind carrying a mark, by its term."""
    found: dict[str, QualityMark] = {}
    for sense in store.senses():
        mark = sense.properties.get("mark")
        if sense.theme_kind != "quality" or not isinstance(mark, str) or not mark:
            continue
        tail = _SAID.get(mark, "by its name")
        found[sense.term] = QualityMark(
            term=sense.term,
            mark=mark,
            said=f"{sense.canonical} {tail}",
            # Folded, as both readers compare them.
            negated_by=tuple(
                sorted(str(word).casefold() for word in (sense.properties.get("negated_by") or ()))
            ),
            surfaces=tuple(sorted(str(surface) for surface in sense.surfaces)),
        )
    return dict(sorted(found.items()))


def cancelled_by(name: str, spelled: str, negations: Collection[str]) -> str | None:
    """The word that cancels a mark where it stands directly before it in a name (``not sick``).

    Words are cut at anything but letters and digits, case folded; None where nothing cancels.
    """
    words = [word for word in _WORDS.split(name.casefold()) if word]
    wanted = spelled.casefold()
    folded = {word.casefold() for word in negations}
    for index, word in enumerate(words):
        if index and word == wanted and words[index - 1] in folded:
            return words[index - 1]
    return None


def negated_throughout(name: str, spelled: str, negations: Collection[str]) -> str | None:
    """The cancelling word where every occurrence of a spelling in a name stands directly
    after one (``not_failed_kelt``), or None where the name says it at least once unnegated
    (``not_failed_then_failed``) or not at all. Cut and folded as :func:`cancelled_by`."""
    words = [word for word in _WORDS.split(name.casefold()) if word]
    wanted = spelled.casefold()
    before = [
        words[index - 1] if index else "" for index, word in enumerate(words) if word == wanted
    ]
    folded = {word.casefold() for word in negations}
    if not before or any(word not in folded for word in before):
        return None
    return before[0]


def cancel_pattern(marks: Iterable[QualityMark]) -> str | None:
    """A pattern finding, in a case-folded name, a cancelling word directly before a mark's word.

    The same reading as :func:`cancelled_by`, for SQL (``regexp_matches``, which reads no
    lookaround); None where no mark declares a word that cancels it, and the reader reads none.
    """
    held = list(marks)
    negations = sorted({word.casefold() for mark in held for word in mark.negated_by})
    surfaces = sorted({word.casefold() for mark in held for word in mark.surfaces})
    if not negations or not surfaces:
        return None
    cancelling = "|".join(re.escape(word) for word in negations)
    spelled = "|".join(re.escape(word) for word in surfaces)
    return rf"(^|[^a-z0-9])({cancelling})[^a-z0-9]+({spelled})([^a-z0-9]|$)"
