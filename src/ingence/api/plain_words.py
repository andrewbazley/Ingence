"""Plain words for the Dashboard: what a rule does, and what a shared value means.

Each plain sentence says what a rule means, the way a friend would explain it. A rule's own
statement is a template that the loader holds to its parameters, and the Rulebook's digest covers it
(``rulebook/resolve.py``: a rule whose sentence changed is a different thing to have agreed to).
Rewriting a shipped statement would change every run record's ``rulebook_hash`` without changing a
conclusion. So the plain sentences live here, in a table that only the API reads, keyed by rule
identifier. Nothing else in the engine opens ``plain_words.yaml``. The table enters no rule's
identity, no Rulebook digest, no Dictionary fingerprint and no exported schema, and editing a
sentence changes no record.

The table has these parts:

- ``rules``: one sentence per rule, saying what it concludes and from what. The sentence never has
  the rule's name after it, because the page shows the name and version as the handle that opens
  the rule. A rule with no sentence here shows its own statement.
- ``values``: what one part of a conclusion's value means, by the part's name and its value, such
  as ``source: modified_before_born``. The claim samples that are grouped by what their values
  share use it (``routes/pages.py``). The table is keyed by the value's shape and never by the
  rule's name.
- ``never_shown``: the words that a sentence never carries. A rule whose name holds one is cited in
  a sentence by its plain sentence and version alone.
- ``levels`` and ``levels_plural``: the word for each level of a root's ladder, by the root's kind
  (``lab`` or ``archive``). ``/api/v1/ladder`` serves them.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "PLAIN_WORDS",
    "PlainWords",
    "name_shown_in_a_sentence",
    "plain_words",
    "rule_sentence",
    "value_sentence",
]

#: The table, beside this module and declared in the wheel's artifacts.
PLAIN_WORDS = Path(__file__).with_name("plain_words.yaml")


@dataclass(frozen=True)
class PlainWords:
    """The tables, as read."""

    rules: Mapping[str, str] = field(default_factory=dict)
    values: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    #: The words a sentence never carries, case folded (:func:`name_shown_in_a_sentence`).
    never_shown: frozenset[str] = frozenset()
    #: The word for each level of a ladder, by the kind of root and then the level. ``levels`` holds
    #: the singular and ``level_plurals`` holds the plural
    #: (``levels["archive"]["experiment"] == "study"``).
    levels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    level_plurals: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def _words_by_kind(table: Mapping[str, Mapping[str, object]] | None) -> dict[str, dict[str, str]]:
    """Return a table of ``{kind: {level: words}}`` as plain strings."""
    return {
        str(kind): {
            str(level): " ".join(str(text).split()) for level, text in (words or {}).items()
        }
        for kind, words in (table or {}).items()
    }


def _load(path: Path) -> PlainWords:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        message = f"{path.name}: the plain words are a mapping of tables"
        raise TypeError(message)
    rules = {
        str(key): " ".join(str(text).split()) for key, text in (document.get("rules") or {}).items()
    }
    values = {
        str(part): {str(value): " ".join(str(text).split()) for value, text in (said or {}).items()}
        for part, said in (document.get("values") or {}).items()
    }
    never = frozenset(str(word).casefold() for word in (document.get("never_shown") or ()))
    return PlainWords(
        rules=rules,
        values=values,
        never_shown=never,
        levels=_words_by_kind(document.get("levels")),
        level_plurals=_words_by_kind(document.get("levels_plural")),
    )


@functools.cache
def plain_words(path: Path = PLAIN_WORDS) -> PlainWords:
    """Return the plain words, read once per process."""
    return _load(path)


def rule_sentence(rule_id: str) -> str | None:
    """Return a rule's plain sentence, or None where the table has none.

    Where the table has none, the page shows the rule's statement.
    """
    return plain_words().rules.get(rule_id)


def name_shown_in_a_sentence(identifier: str) -> bool:
    """Return whether an identifier read as words may stand in a sentence.

    An example is ``group_resolution_deviation``. The result is False where any of its words is
    one that the Dashboard's sentences never carry (``never_shown`` in the table). A sentence then
    names the thing by its plain words alone.
    """
    never = plain_words().never_shown
    return not any(word in never for word in identifier.replace("_", " ").casefold().split())


def value_sentence(shared: Mapping[str, str]) -> str | None:
    """Return what the parts shared by a group of values mean, in plain words.

    The result is None where any part is unknown. ``shared`` maps each part's name to its value, as
    the claim samples group them. Every part must have a sentence. One part that is unexplained
    leaves the whole unexplained, and the function does not return a sentence that explains half of
    what the files share.
    """
    if not shared:
        return None
    said = [plain_words().values.get(part, {}).get(str(value)) for part, value in shared.items()]
    if any(sentence is None for sentence in said):
        return None
    return " ".join(sentence for sentence in said if sentence)
