"""A group's names written once, with the parts that vary in braces and named.

The Groupings page does not show a group as ``word:atp word:wt word:tif`` above a list of paths. It
shows the members' names written once, with each part that varies in braces, such as
``MAX_kelt_20ms_{2-11}_zib.tif``. It also says what kind of thing each varying part is:

- a counter: a number with no unit after it, which an instrument or a person uses to count movies,
  where its siblings count with it (``001`` to ``005``, and not ``488`` or ``561``);
- a number: a number with no unit after it that does not count, which may name a condition;
- a date: a date that the tokenizer reads (``20260316``, ``2026-03-16``), which is never a counter;
- a clock: a time of day written as one (``14-32-05``);
- a quantity: a number that a unit follows (``10mM``, ``20ms``), which is a condition and keeps its
  place;
- a variant: a word that varies (``filtered``, ``processed``);
- a form: the extension, the same thing written as a movie and as a table;
- a location: the members lie in more than one folder.

The pattern is a rendering and nothing else. It reads names and concludes nothing. A group that it
cannot write as one pattern is listed as its names. Where a varying part is not a word, the one
classifier decides what it is (:mod:`ingence.grouping.identifiers`), and the lattice and the name
families consult that classifier too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from ingence.grouping.identifiers import REPLICATE_LABELS, date_spans, slot_kind

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["DIMENSIONS", "SLOT_VALUES_SHOWN", "NamePattern", "name_pattern"]

#: The kinds of part that a group's names may vary by, in the order that a sentence names them.
DIMENSIONS = ("counter", "number", "date", "clock", "quantity", "variant", "form", "location")

#: The number of a varying part's values that a slot lists before it counts the rest.
SLOT_VALUES_SHOWN = 4

#: A name cut into runs of digits, runs of letters, and runs of anything else.
_TOKEN = re.compile(r"\d+|[^\W\d_]+|[\W_]+")


@dataclass(frozen=True, slots=True)
class NamePattern:
    """A group's names written once, and what its varying parts are."""

    pattern: str
    #: One kind per varying part, in the order the parts stand. ``location`` comes last where the
    #: members lie in more than one folder.
    dimensions: tuple[str, ...]

    @property
    def said(self) -> str:
        """Return the dimensions in words: "a counter and a variant, in two or more folders"."""
        kinds = [kind for kind in self.dimensions if kind != "location"]
        counted = {kind: kinds.count(kind) for kind in DIMENSIONS if kind in kinds}
        parts = [
            f"{'a' if count == 1 else _NUMBER_WORDS.get(count, str(count))} "
            f"{kind if count == 1 else kind + 's'}"
            for kind, count in counted.items()
        ]
        spoken = (
            " and ".join(parts) if len(parts) < 3 else ", ".join(parts[:-1]) + ", and " + parts[-1]
        )
        where = ", in more than one folder" if "location" in self.dimensions else ""
        if not spoken:
            return "the same name, in more than one folder" if where else "the same name"
        return f"varying by {spoken}{where}"


_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _tokens(name: str) -> list[str]:
    """Return a name cut as :data:`_TOKEN` cuts it, with a date or a clock kept whole."""
    found: list[str] = []
    cursor = 0
    for start, end, _kind in date_spans(name):
        if start < cursor:
            continue
        found.extend(_TOKEN.findall(name[cursor:start]))
        found.append(name[start:end])
        cursor = end
    found.extend(_TOKEN.findall(name[cursor:]))
    return found


def _slot(values: Sequence[str], *, shown: int) -> str:
    distinct = sorted(set(values), key=lambda value: (len(value), value))
    if distinct and all(value.isdigit() for value in distinct):
        numbers = sorted(int(value) for value in distinct)
        if len(numbers) > 2 and numbers[-1] - numbers[0] + 1 == len(numbers):
            low = min(distinct, key=int)
            high = max(distinct, key=int)
            return "{" + f"{low}-{high}" + "}"
    listed = distinct if len(distinct) <= shown else [*distinct[:shown], f"...{len(distinct)}"]
    return "{" + ",".join(value if value else "-" for value in listed) + "}"


def _skeleton(tokenised: Sequence[Sequence[str]]) -> list[str]:
    """Return the tokens that every name holds, in order.

    The result is a common subsequence, taken name by name.
    """
    skeleton = list(tokenised[0])
    for tokens in tokenised[1:]:
        matcher = SequenceMatcher(None, skeleton, list(tokens), autojunk=False)
        skeleton = [
            token
            for block in matcher.get_matching_blocks()
            for token in skeleton[block.a : block.a + block.size]
        ]
    return skeleton


def _gaps(skeleton: Sequence[str], tokens: Sequence[str]) -> list[str]:
    """Return what a name holds before, between and after the skeleton's tokens."""
    gaps: list[str] = []
    position = 0
    for anchor in skeleton:
        found = list(tokens).index(anchor, position)
        gaps.append("".join(tokens[position:found]))
        position = found + 1
    gaps.append("".join(tokens[position:]))
    return gaps


def name_pattern(
    names: Sequence[str],
    *,
    folders: int = 1,
    shown: int = SLOT_VALUES_SHOWN,
    replicate_labels: Iterable[str] = (),
) -> NamePattern:
    """Write ``names`` once, each varying part in braces, and say what each varying part is.

    ``folders`` is the number of folders that the members lie in, and more than one adds
    ``location``. A number glued to a label is a counter only where the label counts repeats (``r``,
    or one of ``replicate_labels``, the run's). So ``pQ{1-3}`` and ``v{1-3}`` are numbers.
    """
    labels = {label.casefold() for label in replicate_labels} | REPLICATE_LABELS
    ordered = sorted(names)
    where = ("location",) if folders > 1 else ()
    if not ordered:
        return NamePattern(pattern="", dimensions=where)
    tokenised = [_tokens(name) for name in ordered]
    skeleton = _skeleton(tokenised)
    try:
        columns = list(zip(*(_gaps(skeleton, tokens) for tokens in tokenised), strict=True))
    except ValueError:
        return NamePattern(pattern=_slot(ordered, shown=shown), dimensions=("variant", *where))
    last_dot = max((index for index, piece in enumerate(skeleton) if piece == "."), default=-1)
    parts: list[str] = []
    kinds: list[str] = []
    for position, column in enumerate(columns):
        if len(set(column)) > 1:
            parts.append(_slot(list(column), shown=shown))
            following = skeleton[position] if position < len(skeleton) else ""
            kind = slot_kind(column)
            if kind in ("date", "clock"):
                kinds.append(kind)
            elif all(value.isdigit() for value in column):
                before = skeleton[position - 1] if position > 0 else ""
                glued = before[-1:].isalpha() and before.casefold() not in labels
                if following[:1].isalpha():
                    kinds.append("quantity")
                else:
                    kinds.append("number" if glued else kind or "number")
            elif last_dot >= 0 and position > last_dot:
                kinds.append("form")
            else:
                kinds.append("variant")
        elif column[0]:
            parts.append(column[0])
        if position < len(skeleton):
            parts.append(skeleton[position])
    return NamePattern(pattern="".join(parts), dimensions=(*kinds, *where))
