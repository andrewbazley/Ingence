"""What libraries say while a file is read, kept for the journal rather than printed.

This module is not an extractor. A parser library writes to the terminal whatever it thinks worth
saying about a file, through ``logging`` or ``warnings``: pypdf about a broken cross reference or a
font it cannot decode, Pillow about a palette. Without this module, a dive over many files would
print a flood of such lines into the terminal, where nobody can query them and where they would
bury what the engine itself has to say. They are facts about a file and belong beside the file's
other facts, in the run's journal.

Bounded, because a library can say the same thing a thousand times about one file.
Identical messages are counted once, a few distinct ones are kept per reading, and each
is cut short. The count of everything said is kept whole.

Used around a reading in process and inside the supervised child, so where a reading ran
changes nothing about what is kept.
"""

from __future__ import annotations

import contextlib
import logging
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ingence.core.schema import WarningTally

__all__ = [
    "MESSAGES_KEPT",
    "MESSAGES_TRACKED",
    "MESSAGE_WIDTH",
    "SAMPLE_KEPT",
    "SIGNATURES_KEPT",
    "Said",
    "WarningTallies",
    "captured",
    "signature_of",
    "summarise",
    "tallied",
]

#: Distinct messages kept from one reading.
MESSAGES_KEPT = 5

#: Distinct messages counted during one reading. The first this many are counted one by one, and
#: every later new message only in the total. Counting as messages arrive keeps memory bounded.
#: A library that warns once per page would otherwise hold every page's message in memory until
#: the summary threw all but five away.
MESSAGES_TRACKED = 64

#: Characters kept of each. A font dictionary pypdf prints whole runs to thousands.
MESSAGE_WIDTH = 240

#: Kinds of warning a run tallies one by one. Later kinds are counted together.
SIGNATURES_KEPT = 100

#: Messages a tally keeps between its first and its last.
SAMPLE_KEPT = 3


def _cut(message: str) -> str:
    return message if len(message) <= MESSAGE_WIDTH else message[: MESSAGE_WIDTH - 3] + "..."


@dataclass
class Said:
    """What was said during one reading, counted as it arrived, in bounded memory.

    ``counts`` holds at most :data:`MESSAGES_TRACKED` distinct messages, each cut to
    :data:`MESSAGE_WIDTH`; ``total`` counts every message, kept or not.
    """

    counts: Counter[str] = field(default_factory=Counter)
    total: int = 0

    def add(self, message: str) -> None:
        self.total += 1
        kept = _cut(message)
        if kept in self.counts or len(self.counts) < MESSAGES_TRACKED:
            self.counts[kept] += 1

    def __len__(self) -> int:
        return self.total

    def __bool__(self) -> bool:
        return self.total > 0


class _Collector(logging.Handler):
    """A handler that counts what it is given instead of writing it anywhere."""

    def __init__(self, add: Callable[[str], None]) -> None:
        super().__init__(level=logging.WARNING)
        self.add = add

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.add(f"{record.name}: {record.getMessage()}")
        except Exception:
            self.add(f"{record.name}: (a message that could not be formatted)")


@contextlib.contextmanager
def _counting(add: Callable[[str], None], *, after_outer: bool = False) -> Iterator[None]:
    """Hand ``add`` every message logged at warning or above, and every warning, in the block.

    ``after_outer`` puts the count's filter after every filter already in force. A whole pass then
    counts only what no outer filter decided, so a caller's ``error`` (a test suite's) or
    ``ignore`` still stands, and the engine's own defects cannot go quiet in the count. Without
    it (a dive's reading of one file), every warning in the block is counted.
    """
    root = logging.getLogger()
    handler = _Collector(add)
    root.addHandler(handler)

    def shown(message, category, filename, lineno, file=None, line=None) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        add(f"{category.__name__}: {message}")

    try:
        with warnings.catch_warnings():
            if after_outer:
                warnings.filterwarnings("always", append=True)
            else:
                warnings.simplefilter("always")
            warnings.showwarning = shown
            yield
    finally:
        root.removeHandler(handler)


@contextlib.contextmanager
def captured() -> Iterator[Said]:
    """Count what libraries log at warning or above, and what they warn, inside the block.

    The root logger gains a handler for the duration, so a library's records reach it and
    Python's last-resort handler, which prints them to the terminal, is never reached.
    Warnings are counted rather than shown. Each message leads with where it came from: the
    logger's name, or the warning's category. The block counts messages as they arrive, so
    nothing holds a message that the summary would not keep.
    """
    collected = Said()
    with _counting(collected.add):
        yield collected


@contextlib.contextmanager
def tallied(source: str) -> Iterator[WarningTallies]:
    """What :func:`captured` counts, tallied by kind for a whole run as it arrives.

    The tally serves a pass that reads no file one at a time (the census, inference), whose run
    record states what libraries said during it. It counts by signature rather than by message,
    so a warning raised once for each of a thousand rows is one kind counted a thousand times.
    A reading's :class:`Said` would keep the first few messages and count the rest together.
    A filter already in force wins (``_counting``'s ``after_outer``). The count is of what
    libraries say, and must not become a place where the engine's own defects, or a caller's
    ``error``, go quiet.
    """
    tallies = WarningTallies()
    with _counting(lambda message: tallies.add(source, message), after_outer=True):
        yield tallies


def summarise(messages: Said | list[str]) -> tuple[str, ...]:
    """Return a bounded account of what was said: distinct messages, cut short, counted."""
    said = messages if isinstance(messages, Said) else _said(messages)
    if not said:
        return ()
    chosen = sorted(said.counts.items(), key=lambda pair: (-pair[1], pair[0]))[:MESSAGES_KEPT]
    kept = [message + (f" (x{number})" if number > 1 else "") for message, number in chosen]
    rest = said.total - sum(number for _, number in chosen)
    if rest > 0:
        kept.append(f"and {rest} more")
    return tuple(kept)


def _said(messages: list[str]) -> Said:
    said = Said()
    for message in messages:
        said.add(message)
    return said


# ---------------------------------------------------------------------------
# A run's warnings, tallied by kind.
# ---------------------------------------------------------------------------

#: A quoted run that reads as a path: a separator in it, or a dotted suffix at its end.
_QUOTED_PATH = re.compile(r"""(['"])((?:(?!\1).)*?(?:[/\\]|\.[A-Za-z0-9]{1,6})(?:(?!\1).)*?)\1""")
_NUMBER = re.compile(r"\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)\b")
#: How :func:`summarise` says a message came more than once, and how it counts the rest.
_REPEATED = re.compile(r"^(?P<message>.*) \(x(?P<count>\d+)\)$", re.DOTALL)
_THE_REST = re.compile(r"^and (?P<count>\d+) more$")
#: The signature the summarised remainder of one reading is tallied under.
NOT_KEPT_BY_KIND = "messages not kept by kind"
#: The signature every kind beyond :data:`SIGNATURES_KEPT` is tallied under.
OTHER_KINDS = "other kinds"


def signature_of(message: str) -> str:
    """The kind of a message: quoted paths replaced by ``<path>`` and numbers by ``<n>``."""
    shaped = _QUOTED_PATH.sub("<path>", message)
    return _cut(_NUMBER.sub("<n>", shaped))


@dataclass
class _Tally:
    source: str
    count: int
    first: str
    last: str
    sample: list[str]


class WarningTallies:
    """A run's warnings counted by signature, each with its first, its last and a small sample.

    Bounded whatever a run says: :data:`SIGNATURES_KEPT` kinds, :data:`SAMPLE_KEPT` messages a
    kind, each cut to :data:`MESSAGE_WIDTH`. The counts are exact. A line :func:`summarise`
    wrote counts as many as it says (``... (x100)`` is a hundred), and its closing ``and 7
    more`` is tallied as :data:`NOT_KEPT_BY_KIND`.
    """

    def __init__(self) -> None:
        self._kinds: dict[tuple[str, str], _Tally] = {}

    def add(self, source: str, message: str, count: int = 1) -> None:
        repeated = _REPEATED.match(message)
        if repeated:
            message, count = repeated.group("message"), count * int(repeated.group("count"))
        rest = _THE_REST.match(message)
        signature = NOT_KEPT_BY_KIND if rest else signature_of(message)
        if rest:
            count = int(rest.group("count"))
        key = (source, signature)
        if key not in self._kinds and len(self._kinds) >= SIGNATURES_KEPT:
            key = ("", OTHER_KINDS)
        cut = _cut(message)
        tally = self._kinds.get(key)
        if tally is None:
            self._kinds[key] = _Tally(key[0] or source, count, cut, cut, [])
            return
        tally.count += count
        if (
            tally.last != tally.first
            and len(tally.sample) < SAMPLE_KEPT
            and tally.last not in tally.sample
        ):
            tally.sample.append(tally.last)
        tally.last = cut

    def __len__(self) -> int:
        return sum(tally.count for tally in self._kinds.values())

    def tallies(self) -> tuple[WarningTally, ...]:
        """The run record's tallies, most frequent first, then by source and signature."""
        from ingence.core.schema import WarningTally

        ordered = sorted(self._kinds.items(), key=lambda item: (-item[1].count, item[0]))
        return tuple(
            WarningTally(
                source=tally.source,
                signature=signature,
                count=tally.count,
                first_message=tally.first,
                last_message=tally.last,
                sample=tuple(tally.sample),
            )
            for (_, signature), tally in ordered
        )
