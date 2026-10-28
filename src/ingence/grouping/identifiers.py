"""Find what a name varies by that is not a word: a date, a clock, a number, a replicate identifier.

The engine does not group by dates or replicate identifier numbers. Grouping relies on words or
terms that a person can read, except for scientific nouns such as names of genes, strains and
reagents. A date is one that the tokeniser reads as a date. A replicate identifier is known by its
shape (``001``, ``01``, ``-1``, ``_1``, ``_001``) and by its siblings: files of one type holding
the same shape, in the same place, with other values that count.

Every path that forms or shows a group consults this module:

- the lattice's terms and the experiments' clustering leave out a word bound to a
  sense of the kinds :data:`NOT_GROUP_WORD_KINDS` (a month or a weekday; ``rep``, ``trial``),
  whatever a rule declares;
- ``name_pattern`` names a varying date and a varying clock, and a varying number is a counter only
  where its siblings count with it (:func:`slot_kind`);
- the Groupings page shows a class a name rule formed in two or more experiments as a series
  inside each, never asked across them, where what tells the experiments' files apart is only
  what they count (:func:`series_across`).

The module judges a class where the whole class is seen. A key reads one name at a time and cannot
tell a series from a family. Keying every name that holds a number, within its experiment, split a
processed file holding none from the replicates it was made from, in one folder (measured on a
generated test pool). So the rules keep their keys and the page reads the class. An unknown word
stays. A strain's or a construct's name that the Dictionary lacks is exactly the scientific noun
that the rule keeps. Nothing here reads a file or writes anything.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from ingence.core.schema import DictionaryBinding
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.normalize import AtomKind, Normaliser

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ingence.core.schema import Component

__all__ = [
    "IDENTIFIER_DIMENSIONS",
    "NOT_GROUP_WORD_KINDS",
    "REPLICATE_LABELS",
    "date_spans",
    "masked",
    "replicate_labels_of",
    "series_across",
    "slot_kind",
]

#: The kinds of sense whose words never make a group: a month's or a weekday's word is a date, and
#: a replicate's word (``rep``, ``trial``, ``round``) labels which repeat a file is.
NOT_GROUP_WORD_KINDS = frozenset({"date", "replicate"})

#: The varying parts, in ``name_pattern``'s words, that a series is counted, dated or timed by. A
#: ``number`` whose siblings do not count with it may name a condition and is not one.
IDENTIFIER_DIMENSIONS = frozenset({"counter", "date", "clock"})


# ---------------------------------------------------------------------------
# A class's names, read together: what each varying part is.
# ---------------------------------------------------------------------------

#: A clock written as one: hours, then minutes and seconds of two digits, one separator between,
#: with no letter or digit glued to it (``v1.23.45`` is a version's, never a time).
_CLOCK = re.compile(r"(?<![0-9A-Za-z.])([01]?\d|2[0-3])([-:.])([0-5]\d)\2([0-5]\d)(?![0-9A-Za-z])")
_DIGITS = re.compile(r"^\d+$")
#: Where a date could stand: six to eight digits (``20260316``, ``260316``), or numbers joined by
#: a separator (``2026-03-16``, ``2026-03``, ``16.3.26``).
_DATE_SHAPED = re.compile(r"\d{6,8}|\d{1,4}[-_./ ]\d{1,2}(?:[-_./ ]\d{1,4})?")
#: The most digits a counter is written with unpadded (``1024``), and padded to a width
#: (``000017``): beyond them, no replicate's number.
_COUNTER_DIGITS = 4
_PADDED_DIGITS = 6


@lru_cache(maxsize=1)
def _reader() -> Normaliser:
    return Normaliser()


@lru_cache(maxsize=65536)
def date_spans(name: str, dates: tuple[str, ...] | None = None) -> tuple[tuple[int, int, str], ...]:
    """Where a name writes a date or a clock: each span, and ``date`` or ``clock``.

    A date is the tokeniser's. The function takes the spellings that the run's own tokeniser
    stored as the name's dates, where the caller has them (``dates``, the reproducible source).
    Otherwise it reads the name again, without its extension, with the tokeniser and no
    Dictionary (numeric dates). A clock is written as one (``14-32-05``, ``9.41.27``) outside
    any date.
    """
    stem, dot, extension = name.rpartition(".")
    text = stem if dot and stem and extension and not extension.isdigit() else name
    if dates is not None:
        return _spans_of(text, dates)
    spans: list[tuple[int, int, str]] = []
    # The tokeniser runs only where a date could stand (about 0.3 ms a name, so ten
    # thousand counted names cost three seconds on the Groupings page): a run of six to eight
    # digits, or numbers joined by a separator; ``kelt_vorn_001`` holds neither.
    if _DATE_SHAPED.search(text):
        spans.extend(
            (atom.start, atom.start + len(atom.text), "date")
            for atom in _reader().atoms(text)
            if atom.kind is AtomKind.DATE and not atom.ambiguous
        )
        for match in _CLOCK.finditer(text):
            if not any(start < match.end() and match.start() < end for start, end, _ in spans):
                spans.append((match.start(), match.end(), "clock"))
    return tuple(sorted(spans))


def _spans_of(text: str, dates: Sequence[str]) -> tuple[tuple[int, int, str], ...]:
    """The spans of the date spellings given, longest first, and the clocks outside them."""
    spans: list[tuple[int, int, str]] = []
    for spelling in sorted({d for d in dates if d}, key=lambda d: (-len(d), d)):
        start = text.find(spelling)
        while start >= 0:
            end = start + len(spelling)
            if not any(s < end and start < e for s, e, _ in spans):
                spans.append((start, end, "date"))
            start = text.find(spelling, end)
    for match in _CLOCK.finditer(text):
        if not any(start < match.end() and match.start() < end for start, end, _ in spans):
            spans.append((match.start(), match.end(), "clock"))
    return tuple(sorted(spans))


def _counts(values: Sequence[str]) -> bool:
    """Whether numbers are a counter's: written as one is, and counting with their siblings.

    Its siblings count with it where some value is written wider than its number (``01``,
    ``007``: a count padded to a width) or the largest is no more than twice how many values
    there are (``1``, ``2``, ``3``, ``5``); ``488``, ``561`` and ``640`` hold the shape and do not
    count, and may name a condition.
    """
    if not values or not all(_DIGITS.match(value) for value in values):
        return False
    padded = any(len(value) > 1 and value.startswith("0") for value in values)
    # A count padded to a width is a counter up to six digits; an unpadded one up to four.
    if any(len(value) > (_PADDED_DIGITS if padded else _COUNTER_DIGITS) for value in values):
        return False
    distinct = {int(value) for value in values}
    return padded or max(distinct) <= 2 * len(distinct)


def slot_kind(values: Sequence[str]) -> str | None:
    """What a varying part of a class's names is, where it is not a word, or None.

    ``date`` where every value is a date the tokeniser reads, ``clock`` where every value is a
    clock, ``counter`` where every value is a number of a counter's shape and the values count
    (:func:`_counts`), ``number`` for other numbers. A value holding a letter is a word's part,
    and None.
    """
    distinct = [value for value in values if value]
    if not distinct or len(distinct) < len(values):
        return None
    if all(date_spans(value) == ((0, len(value), "date"),) for value in distinct):
        return "date"
    if all(_CLOCK.fullmatch(value) for value in distinct):
        return "clock"
    if all(_DIGITS.match(value) for value in distinct):
        return "counter" if _counts(distinct) else "number"
    return None


#: A run of digits with the letters glued before it and the character after it.
_NUMBER_IN_PLACE = re.compile(r"(?P<label>[^\W\d_]*)(?P<number>\d+)(?P<after>[^\W\d_]?)")

#: Labels whose number counts repeats whatever a Dictionary says: ``r2``.
REPLICATE_LABELS = frozenset({"r"})


def _stem(name: str) -> tuple[str, str]:
    """A name's stem and its extension (with the dot), as :func:`date_spans` reads them."""
    stem, dot, extension = name.rpartition(".")
    if dot and stem and extension and not extension.isdigit():
        return stem, dot + extension
    return name, ""


def _counted(
    name: str,
    replicate_labels: frozenset[str] = REPLICATE_LABELS,
    dates: tuple[str, ...] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """A name masked, and what each mark stood for, in order: its dates, clocks and counters.

    Only what a name counts is masked. That is a date and a clock wherever written, a number
    standing apart (``_001``, ``-1``, ``03``), and a number glued to a label only where the
    label counts repeats (``r2``, or a label the Dictionary binds to the replicate kind, given as
    ``replicate_labels``). A number a unit follows (``1mM``, ``2h``) is a condition, a construct's
    or a version's number (``pQ1``, ``v2``) names which one: both are kept.
    """
    stem, extension = _stem(name)
    labels = {label.casefold() for label in replicate_labels} | REPLICATE_LABELS
    parts: list[str] = []
    values: list[str] = []
    cursor = 0

    def numbers(text: str) -> str:
        def mark(found: re.Match[str]) -> str:
            label, number, after = found.group("label", "number", "after")
            if after or (label and label.casefold() not in labels):
                return found.group()
            values.append(number)
            return f"{label}#"

        return _NUMBER_IN_PLACE.sub(mark, text)

    for start, end, kind in date_spans(name, dates):
        if start < cursor:
            continue
        parts.extend((numbers(stem[cursor:start]), f"<{kind}>"))
        values.append(stem[start:end])
        cursor = end
    parts.append(numbers(stem[cursor:]))
    return ("".join(parts) + extension).casefold(), tuple(values)


def masked(
    name: str,
    *,
    marks: bool = True,
    dates: Sequence[str] | None = None,
    replicate_labels: Iterable[str] = (),
) -> str:
    """A name with what it counts masked (:func:`_counted`): each date, clock and counter a mark.

    ``kelt_vorn_003.nd2`` and ``kelt_vorn_017.nd2`` are both ``kelt_vorn_#.nd2`` (the extension is
    kept), and ``zib_2026-03-02_kelt.tif`` is ``zib_<date>_kelt.tif``; case is folded. With
    ``marks`` false the marks are dropped: ``kelt_vorn_.nd2``. ``replicate_labels`` are the
    labels the run's Dictionary binds to the replicate kind (:func:`replicate_labels_of`).
    """
    labels = frozenset(label.casefold() for label in replicate_labels) | REPLICATE_LABELS
    shape = _counted(name, labels, dates=None if dates is None else tuple(dates))[0]
    return shape if marks else _MARK.sub("", shape)


_MARK = re.compile(r"#|<date>|<clock>")


def replicate_labels_of(components: Iterable[Component], bindings: Iterable[object]) -> set[str]:
    """The labels a name holds that the Dictionary bound to the replicate kind (``rep``, ``trial``):
    a word so bound, or an indexed atom's label (``rep2``), each casefolded."""
    settled = {
        binding.component_id
        for binding in bindings
        if isinstance(binding, DictionaryBinding)
        and binding.resolution_status is ResolutionStatus.RESOLVED
        and binding.theme_kind == "replicate"
    }
    found: set[str] = set()
    for component in components:
        if component.component_id not in settled:
            continue
        value = str(component.normalized_value).casefold()
        if component.component_type == "word":
            found.add(value)
        elif component.component_type == "indexed":
            label = _LABEL_OF_INDEX.match(value)
            if label:
                found.add(label.group(1))
    return found


_LABEL_OF_INDEX = re.compile(r"^([^\W\d_]+)\d+$")


def series_across(
    members: Iterable[tuple[str | None, str]],
    *,
    replicate_labels: Iterable[str] = (),
) -> bool:
    """Whether a class lying in two or more experiments is one series, counted in each.

    ``members`` are each member's experiment (None outside every experiment) and name, and
    ``replicate_labels`` the labels the Dictionary binds to the replicate kind. A series across
    experiments holds some name, masked (:func:`masked`), in every experiment and wherever else
    a member lies, and somewhere names masked alike differ by a date, a clock or a counter. What
    links the experiments' files is a name counted differently in each, never a word. A counter
    is judged among its siblings: the values one experiment holds count with one another, or,
    where each experiment holds one, the values together do (:func:`slot_kind`). Files held in
    one experiment only (an analysis beside its movies) may vary by words too. A class whose
    places share no masked name is linked by more than what it counts, and is asked, as is one
    whose numbers do not count (``488``, ``561``).
    """
    labels = frozenset(replicate_labels) | REPLICATE_LABELS
    by_place: dict[str | None, set[str]] = {}
    counted: dict[str, dict[str | None, set[tuple[str, ...]]]] = {}
    for place, name in members:
        shape, values = _counted(name, labels)
        by_place.setdefault(place, set()).add(shape)
        counted.setdefault(shape, {}).setdefault(place, set()).add(values)
    if len([place for place in by_place if place is not None]) < 2:
        return False
    if not set.intersection(*by_place.values()):
        return False
    for held in counted.values():
        every = set().union(*held.values())
        if len(every) < 2 or not every or not next(iter(every)):
            continue
        for position in range(len(next(iter(every)))):
            column = [values[position] for values in every]
            if len(set(column)) < 2:
                continue
            siblings = [
                [values[position] for values in found]
                for found in held.values()
                if len({values[position] for values in found}) > 1
            ]
            judged = siblings or [column]
            if any(slot_kind(values) in IDENTIFIER_DIMENSIONS for values in judged):
                return True
    return False
