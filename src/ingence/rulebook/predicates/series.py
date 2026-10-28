"""Reading a class of replicates as a series: which order its members' counting part gives.

:func:`read_series` is the one function for this. The proposing predicate
(:class:`~ingence.rulebook.predicates.propose.SucceedsByOrderedToken`), the design summary's count
of classes not ordered, and the tests share it. It is given the members of one class of names that
agree on every condition (the class a grouping rule drew). It says either the members in order, or
why the class has no order. A class that has no order abstains whole. An order proposed for some of
its members and not the rest would be a series with holes, and a wrong order looks exactly like a
right one. Each refusal has a code (:data:`REASONS`) so a run can count them.

The class's members agree on everything but the parts the grouping ignores: a bare number, a
replicate label (``rep1``, ``rep2``), a word bound to a stage. So an order is read off exactly
one varying part, and the class abstains when:

- ``layout`` / ``unrecorded``: the members' names are not laid out alike, or the run recorded no
  layout for them;
- ``no_variation``: nothing tells one member from another by its name;
- ``many_parts``: more than one part differs, or the one that differs is not a counting part (a
  stage word beside a number is two parts, and a number would then order a raw file before a
  processed one);
- ``labels`` / ``parse``: the labels of an indexed part differ (``rep1`` with ``run2`` counts
  nothing), or a value is not a number; a value is never compared as text;
- ``not_counting``: the numbers do not count. The replicate grouping leaves every bare number
  out of its key, so a class can hold ``488``, ``561`` and ``640`` (wavelengths), ``1``, ``10`` and
  ``100`` (a dose written without its unit) or ``0``, ``30`` and ``60`` (minutes). Those are
  conditions, and ordering them would put a condition in order as if it were somebody counting. A
  number is a count only where :func:`~ingence.grouping.identifiers.slot_kind` says its siblings
  count with it: written padded to a width (``001``), or no larger than twice how many there are;
- ``tie``: two members carry the same number (``1`` and ``001``), because a tie would break the
  total order a series has;
- ``clocks``: two members carry a clock from the file's own content (an instrument's or a
  document's) and those clocks run against the order the numbers give. The number then names
  something other than the order the files were made in.

A dose ladder is not a series, with a unit or without one. ``1uM``, ``10uM`` and ``100uM`` differ
in a quantity, which a class of replicates holds fixed. ``1``, ``10`` and ``100`` do not count.
The design grid labels the ladder on its factor's levels (:mod:`ingence.design`).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.dictionary.normalize import split_indexed
from ingence.grouping.identifiers import slot_kind

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from ingence.infer.subjects import EntryRecords

__all__ = [
    "CLOCK_TOLERANCE_SECONDS",
    "COUNTING_KINDS",
    "REASONS",
    "SeriesReading",
    "SeriesRow",
    "counting_kinds",
    "counts_by_reason",
    "read_rows",
    "read_series",
    "rows_of",
]

#: What ``ordering_kind`` ``indexed`` counts by: a running number written alone, and an indexed
#: label (``rep3``). A rule naming another kind counts by that kind alone.
COUNTING_KINDS: dict[str, frozenset[str]] = {"indexed": frozenset({"number", "indexed"})}

#: How far two content clocks may run against the order and still agree with it, in seconds: the
#: default of the rule's ``tolerance_seconds``.
CLOCK_TOLERANCE_SECONDS = 60

#: The reasons a class is not put in order, by code, in words.
REASONS: dict[str, str] = {
    "unrecorded": "the run recorded no layout for the names",
    "layout": "the names are not laid out alike",
    "no_variation": "nothing tells the members apart by name",
    "many_parts": "more than one part varies, or the one that varies is no counting part",
    "labels": "the labels of the counting part differ",
    "parse": "a value of the counting part is no number",
    "not_counting": "the numbers do not count, so they may name a condition",
    "tie": "two members carry the same number",
    "clocks": "the files' own clocks run against the order of the numbers",
    "too_few": "the class holds fewer than two members",
}

_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")
_CONTENT_TIERS = frozenset({"instrument", "document"})


def counting_kinds(ordering_kind: str) -> frozenset[str]:
    """The component types a rule's ``ordering_kind`` counts by."""
    return COUNTING_KINDS.get(ordering_kind, frozenset({ordering_kind}))


@dataclass(frozen=True, slots=True)
class SeriesRow:
    """One member of a class as the reading needs it: its name's parts and its content clock."""

    member_id: str
    #: Each part of the name in order: its kind, its normalised value, and as written.
    parts: tuple[tuple[str, str, str], ...]
    #: The file's own clock (an instrument's or a document's), in nanoseconds, or None.
    clock_ns: int | None = None


@dataclass(frozen=True, slots=True)
class SeriesReading:
    """A class read as a series: its members in order, or why it has none."""

    #: Member identifiers from the earliest to the latest; empty where the class has no order.
    order: tuple[str, ...]
    #: Why there is no order, in words; empty where there is one.
    reason: str = ""
    #: The code of the reason (a key of :data:`REASONS`); empty where there is an order.
    code: str = ""
    #: The value each member counted by, by identifier (empty where there is no order).
    values: tuple[tuple[str, int], ...] = ()
    #: True where the abstention is a layout the run did not record (unexamined, not refused).
    unexamined: bool = False
    #: Each member's place in ``order``, so a pair's two ends are found without a search.
    places: dict[str, int] = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def orders(self) -> bool:
        return bool(self.order)


def _refused(code: str, detail: str = "", *, unexamined: bool = False) -> SeriesReading:
    reason = REASONS[code] + (f" ({detail})" if detail else "")
    return SeriesReading(order=(), reason=reason, code=code, unexamined=unexamined)


def _content_clock(entry: object) -> int | None:
    """The file's own clock where it is an instrument's or a document's, else None."""
    nanoseconds = getattr(entry, "event_time_ns", None)
    if nanoseconds is None or str(getattr(entry, "event_time_tier", "")) not in _CONTENT_TIERS:
        return None
    return int(nanoseconds)


def rows_of(members: Sequence[EntryRecords]) -> list[SeriesRow] | None:
    """Each member's parts in the order the name wrote them, or None where a name has no layout."""
    rows: list[SeriesRow] = []
    for member in members:
        by_id = {component.component_id: component for component in member.components}
        row = tuple(
            (
                by_id[identifier].component_type,
                by_id[identifier].normalized_value,
                by_id[identifier].raw_value or by_id[identifier].normalized_value,
            )
            for identifier in member.slots
            if identifier in by_id
        )
        if not row:
            return None
        rows.append(SeriesRow(member.entry.entry_id, row, _content_clock(member.entry)))
    return rows


def _digits(kind: str, value: str, raw: str) -> str | None:
    """The digits a counting part is written with, or None where it holds none."""
    if kind == "indexed":
        split = split_indexed(value)
        if split is None:
            return None
        found = _TRAILING_DIGITS.search(raw)
        return found.group(1) if found else str(split[1])
    found = _TRAILING_DIGITS.fullmatch(raw.strip()) if raw else None
    return found.group(1) if found else (value if value.isdigit() else None)


def read_series(
    members: Sequence[EntryRecords],
    kinds: Collection[str],
    tolerance_seconds: int = CLOCK_TOLERANCE_SECONDS,
) -> SeriesReading:
    """Read ``members`` (one class, in any order) as a series counted by components of ``kinds``."""
    if len(members) < 2:
        return _refused("too_few")
    rows = rows_of(members)
    if rows is None:
        return _refused("unrecorded", unexamined=True)
    return read_rows(rows, kinds, tolerance_seconds)


def read_rows(
    rows: Sequence[SeriesRow],
    kinds: Collection[str],
    tolerance_seconds: int = CLOCK_TOLERANCE_SECONDS,
) -> SeriesReading:
    """:func:`read_series` over rows already made (the design summary makes its own)."""
    if len(rows) < 2:
        return _refused("too_few")
    width = len(rows[0].parts)
    if any(len(row.parts) != width for row in rows) or any(
        [part[0] for part in row.parts] != [part[0] for part in rows[0].parts] for row in rows
    ):
        return _refused("layout")

    def written(row: SeriesRow, place: int) -> tuple[str, str]:
        """What a place holds: its value, and for a counting part also how it was written, so
        ``1`` and ``001`` are two members that tie rather than two that do not differ."""
        kind, value, raw = row.parts[place]
        return value, (raw if kind in kinds else "")

    varying = [place for place in range(width) if len({written(row, place) for row in rows}) > 1]
    if not varying:
        return _refused("no_variation")
    counting = [place for place in varying if rows[0].parts[place][0] in kinds]
    if len(varying) > 1 or not counting:
        shown = ", ".join(rows[0].parts[place][0] for place in varying)
        return _refused("many_parts", shown)
    place = counting[0]
    kind = rows[0].parts[place][0]
    labels = {
        split[0]
        for row in rows
        if kind == "indexed" and (split := split_indexed(row.parts[place][1])) is not None
    }
    if len(labels) > 1:
        return _refused("labels", ", ".join(sorted(labels)))
    counted: list[tuple[int, str, str]] = []
    for row in rows:
        part = row.parts[place]
        digits = _digits(part[0], part[1], part[2])
        if digits is None:
            return _refused("parse", repr(part[2] or part[1]))
        counted.append((int(digits), row.member_id, digits))
    numbers = [value for value, _, _ in counted]
    ties = sorted({value for value in numbers if numbers.count(value) > 1})
    if ties:
        return _refused("tie", str(ties[0]))
    if slot_kind(sorted({digits for _, _, digits in counted})) != "counter":
        shown = ", ".join(str(value) for value in sorted(numbers)[:6])
        return _refused("not_counting", shown)
    ordered = sorted(counted)
    clocks = {row.member_id: row.clock_ns for row in rows}
    tolerance = tolerance_seconds * 1_000_000_000
    held = [clock for _, identifier, _ in ordered if (clock := clocks[identifier]) is not None]
    if any(earlier - later > tolerance for earlier, later in itertools.pairwise(held)):
        return _refused("clocks")
    order = tuple(identifier for _, identifier, _ in ordered)
    return SeriesReading(
        order=order,
        values=tuple((identifier, value) for value, identifier, _ in ordered),
        places={identifier: place for place, identifier in enumerate(order)},
    )


def counts_by_reason(readings: Iterable[SeriesReading]) -> dict[str, int]:
    """How many readings were not ordered, by reason code, in code order."""
    found: dict[str, int] = {}
    for reading in readings:
        if not reading.orders:
            found[reading.code] = found.get(reading.code, 0) + 1
    return dict(sorted(found.items()))
