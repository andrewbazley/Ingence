"""A line of work found again in another run, by the experiments it holds.

A theme (a line of work) is named in the Record by the three words of its label
(``theme:<key>``, ``record/targets.theme_key``), and its label is the engine's reading of the
words its experiments carry. A re-run that reads other words for the same experiments gives the
line another label, so anything keyed by the label (a person's name for the line, a topic that
holds it) would lose it. What stays is the experiments: an experiment's identifier is its folder's
entry identifier, the same in every run that holds the folder.

:func:`match_line` finds the line of a run that holds most of a set of experiments (ties: the most
words in common, then the key's order), so a name or a topic follows the line. It follows a line
only where the line holds more than half of the experiments it was named or added by
(:data:`FOLLOW_SHARE`, a parameter). Below that the line has split or been taken apart, and
following whichever line kept one of them would put a name or a topic on a line it was never
about. The rest of this module reads a projection's lines as plain values. Nothing here reads the
Record or a run, and nothing is written: a projection is never a claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.record.targets import theme_key

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

__all__ = [
    "FOLLOW_SHARE",
    "LABEL_JOINER",
    "BroadLine",
    "Line",
    "LineMatch",
    "NamedEvent",
    "broad_lines_of",
    "experiments_of_key",
    "follow_names",
    "lines_of",
    "match_line",
]

#: What a label's words are joined with, as the projection writes it.
LABEL_JOINER = " · "

#: The share of the experiments that a name or a topic's line was given to that a line must hold.
#: A name or a topic follows a line only when the line holds more than this share (exclusive:
#: exactly half follows nothing). This is a parameter and not buried: ``match_line`` and
#: ``follow_names`` take it as ``minimum_share``. Half is where a line can be the only one that
#: holds more than the rest. The value is not measured beyond that.
FOLLOW_SHARE = 0.5


@dataclass(frozen=True)
class Line:
    """One line of work of a run, as a projection holds it."""

    theme: int
    label: str
    #: ``theme:`` target's key: the label's words, lower-cased, sorted, joined with ``+``.
    key: str
    label_words: tuple[str, ...]
    #: The label's words and the line's other distinctive words, casefolded: what a match counts
    #: as in common.
    words: frozenset[str]
    experiments: frozenset[str]


@dataclass(frozen=True)
class BroadLine:
    """A broad line of work: the lines drawn together at a coarser resolution."""

    broad: int
    label: str
    words: tuple[str, ...]
    themes: tuple[int, ...]
    experiments: frozenset[str]


@dataclass(frozen=True)
class LineMatch:
    line: Line
    #: How many of the experiments looked for the line holds, and how many were looked for.
    held: int
    of: int
    #: How many of the words looked for the line also has.
    shared_words: int


def lines_of(built: Mapping[str, Any]) -> tuple[Line, ...]:
    """Every line of work of a themes projection, with the experiments placed in it, ordered by
    theme number."""
    members: dict[int, set[str]] = {}
    for row in built.get("experiments", ()):
        if row.get("theme") is not None:
            members.setdefault(int(row["theme"]), set()).add(str(row["id"]))
    found: list[Line] = []
    for theme in built.get("themes", ()):
        label = str(theme.get("label") or "")
        label_words = tuple(part for part in label.split(LABEL_JOINER) if part)
        found.append(
            Line(
                theme=int(theme["theme"]),
                label=label,
                key=theme_key(label_words),
                label_words=label_words,
                words=frozenset(
                    str(word).casefold() for word in (*label_words, *(theme.get("words") or ()))
                ),
                experiments=frozenset(members.get(int(theme["theme"]), ())),
            )
        )
    return tuple(sorted(found, key=lambda line: line.theme))


def broad_lines_of(built: Mapping[str, Any]) -> tuple[BroadLine, ...]:
    """Every broad line of a themes projection, with the experiments placed in it."""
    members: dict[int, set[str]] = {}
    for row in built.get("experiments", ()):
        if row.get("broad_theme") is not None:
            members.setdefault(int(row["broad_theme"]), set()).add(str(row["id"]))
    return tuple(
        sorted(
            (
                BroadLine(
                    broad=int(broad["broad_theme"]),
                    label=str(broad.get("label") or ""),
                    words=tuple(str(word) for word in broad.get("words") or ()),
                    themes=tuple(int(theme) for theme in broad.get("themes") or ()),
                    experiments=frozenset(members.get(int(broad["broad_theme"]), ())),
                )
                for broad in built.get("broad", ())
            ),
            key=lambda broad: broad.broad,
        )
    )


def match_line(
    experiments: Collection[str],
    words: Collection[str],
    lines: Sequence[Line],
    *,
    minimum_share: float = FOLLOW_SHARE,
) -> LineMatch | None:
    """The line holding most of ``experiments``; ties go to the line sharing most of ``words``,
    then to the lower key. None where no line holds more than ``minimum_share`` of them (the
    share is of the experiments looked for; 0 asks for any one, exclusive)."""
    wanted = frozenset(str(experiment) for experiment in experiments)
    said = frozenset(str(word).casefold() for word in words)
    best: tuple[tuple[int, int, str], Line] | None = None
    for line in lines:
        held = len(wanted & line.experiments)
        if not held or held <= minimum_share * len(wanted):
            continue
        rank = (-held, -len(said & line.words), line.key)
        if best is None or rank < best[0]:
            best = (rank, line)
    if best is None:
        return None
    line = best[1]
    return LineMatch(
        line=line,
        held=len(wanted & line.experiments),
        of=len(wanted),
        shared_words=len(said & line.words),
    )


def experiments_of_key(built: Mapping[str, Any], key: str) -> list[str]:
    """The experiments of the line of a projection whose key is ``key``, sorted; none where no
    line has it."""
    found = next((line for line in lines_of(built) if line.key == key), None)
    return [] if found is None else sorted(found.experiments)


@dataclass(frozen=True)
class NamedEvent:
    """One statement naming (or un-naming) a line of work, as the Record holds it."""

    #: The statement's position in the Record: the latest statement about a line wins.
    sequence: int
    #: The line's key as it was when the statement was written (``theme:<key>``).
    key: str
    #: The run it was written against, where one is recorded.
    run_id: str | None
    #: The experiments of the line when the statement was written, where it recorded them.
    experiments: frozenset[str] | None


@dataclass(frozen=True)
class _Read[T: NamedEvent]:
    """One statement and what it came to in a run: the experiments it is about, the line they
    point to (if any), and the share of its experiments that line holds."""

    event: T
    anchors: frozenset[str] | None
    line: Line | None
    share: float


def _replaces(later: _Read[Any], earlier: _Read[Any], minimum_share: float) -> bool:
    """Whether a later statement is the person's last word on what an earlier one was about.

    A later statement replaces an earlier one when it holds more than ``minimum_share`` of the
    earlier one's experiments (a rename, or a name taken away, of the same experiments, however many
    have come or gone since). It also replaces it when the earlier one kept no experiments and both
    name the same line (a name written before experiments were kept, matched by its key). Dates
    decide here and shares do not: the share of a line shrinks and grows with the runs, and the
    newer word must not lose to the older for it.
    """
    if earlier.anchors and later.anchors:
        held = len(later.anchors & earlier.anchors)
        if held > minimum_share * len(earlier.anchors):
            return True
    return (
        earlier.event.experiments is None
        and earlier.line is not None
        and later.line is not None
        and earlier.line.key == later.line.key
    )


def follow_names[T: NamedEvent](
    lines: Sequence[Line],
    events: Sequence[T],
    *,
    run_id: str,
    earlier: Callable[[str], Mapping[str, Any] | None],
    minimum_share: float = FOLLOW_SHARE,
) -> dict[str, T]:
    """Which statement names each line of this run, by the line's key: a name follows its line.

    A statement is about a line by the experiments it held when the statement was written. So a
    run that reads other words for the same line (another label, another key) does not lose it.
    The line that holds most of those experiments is the one named, if it holds more than
    ``minimum_share`` of them (ties: the most words in common, then the key's order,
    ``match_line``). Below that the statement names no line: a line that has been split or taken
    apart is not named after whichever line kept one of its experiments. Where the statement
    recorded its experiments, the function uses them. Where it did not (a statement written
    before experiments were kept), the function reads them from the line of that key in the run
    it was written against (``earlier(run_id)`` gives that run's projection, or None where it
    cannot be read). Where neither is known, the statement names the line with its key in this
    run, if there is one. A statement recorded with experiments names the line they point to,
    even when another line now carries its key (a label coincidence), and it names no line if
    none holds enough of them.

    A later statement about the same experiments replaces an earlier one first (``_replaces``: it
    holds more than ``minimum_share`` of the earlier one's experiments, or the earlier one kept
    none and names the same line). So a rename, or a name taken away, stands over the older name
    however the line has changed since. Then, among the statements left that land on one line
    (two lines merged, an experiment moved from one to the other), the winner is the one holding
    the larger share of its own experiments in the line. Next comes the one whose key is the
    line's key, and then the latest in the Record (a removal included).
    """
    by_key = {line.key: line for line in lines}
    reads: list[_Read[T]] = []
    for event in events:
        anchors = event.experiments
        if anchors is None and event.key not in by_key:
            built = earlier(event.run_id) if event.run_id and event.run_id != run_id else None
            if built is not None:
                found = experiments_of_key(built, event.key)
                anchors = frozenset(found) if found else None
        line: Line | None
        share = 1.0
        if anchors:
            matched = match_line(anchors, event.key.split("+"), lines, minimum_share=minimum_share)
            line = matched.line if matched is not None else None
            share = matched.held / matched.of if matched is not None else 0.0
        else:
            # Nothing is known of the experiments: the line carrying its key is the line.
            line = by_key.get(event.key)
        reads.append(_Read(event=event, anchors=anchors, line=line, share=share))
    standing: list[_Read[T]] = []
    for later in sorted(reads, key=lambda read: read.event.sequence):
        standing = [held for held in standing if not _replaces(later, held, minimum_share)]
        standing.append(later)
    best: dict[str, tuple[tuple[float, bool, int], T]] = {}
    for read in standing:
        if read.line is None:
            continue
        rank = (read.share, read.event.key == read.line.key, read.event.sequence)
        if read.line.key not in best or rank > best[read.line.key][0]:
            best[read.line.key] = (rank, read.event)
    return {key: held[1] for key, held in best.items()}
