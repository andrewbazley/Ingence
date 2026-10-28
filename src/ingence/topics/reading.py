"""A topic's members resolved against a run, and what drifted between two runs.

Pure: it reads :class:`~ingence.topics.runview.RunView` values and the topics' members and
returns plain values (``model``). Nothing is read from a run or the Record here, and nothing is
stored.

How each kind of member resolves (each in words when it resolves to nothing):

- an experiment resolves to itself where the run counts its folder as an experiment, to the
  larger experiment it lies in where the experiment rules now place it inside one, and to
  nothing otherwise;
- a file or folder resolves to the experiment it lies in; a folder above experiments, to
  the experiments beneath it. One in no experiment resolves to nothing (the item stays in the
  topic and on the Wheel);
- a core is followed by its words: it resolves to the experiments whose terms hold every
  word, re-read every run, so an experiment that comes to carry the words joins;
- a line is found again by its experiments: it resolves to the experiments of the run's line
  holding most of the experiments it was added by (``themes.lines.match_line``), so a label that
  changed does not lose it. Its stored words say when the line has drifted.

Drift reads the topic's members the same way in two runs: what differs is the runs'. A
member that could not be read in either is left out of the comparison and named.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ingence.topics.model import (
    Change,
    Drift,
    MemberReading,
    Overlap,
    TopicReading,
)
from ingence.topics.runview import key_of_word

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.api.routes.overlay import Topic, TopicMember
    from ingence.themes.lines import LineMatch
    from ingence.topics.runview import EntryInfo, RunView

__all__ = [
    "CLAUSE_REASONS",
    "LINE_CHANGED",
    "NO_RULES_APPLIED",
    "compare",
    "overlaps",
    "read_member",
    "read_topic",
]

#: Why an experiment joined or left a topic through a line of work: said as a clause of its own,
#: where every other reason is a phrase that follows "it" or "each".
LINE_CHANGED = "the line's experiments changed"
#: Why nothing is said of a run that applied no rules (a census): it holds no experiments yet.
NO_RULES_APPLIED = "this run applied no rules, so it holds no experiments yet"
CLAUSE_REASONS = frozenset({LINE_CHANGED})


def _nothing(
    member: TopicMember, why: str, *, said: tuple[str, str] | None = None
) -> MemberReading:
    """A member resolving to nothing, with the name and path it was said with where the run does
    not hold it (``said``)."""
    name, path = said if said is not None else (None, None)
    return MemberReading(
        key=member.key,
        kind=member.kind,
        state="nothing",
        why=why,
        name=name,
        path=path,
        in_run=said is None,
    )


def _not_read(member: TopicMember, why: str) -> MemberReading:
    return MemberReading(key=member.key, kind=member.kind, state="not_computed", why=why)


def _resolved(
    member: TopicMember,
    experiments: Sequence[str],
    *,
    held: EntryInfo | None = None,
    line: LineMatch | None = None,
    drifted: bool = False,
) -> MemberReading:
    return MemberReading(
        key=member.key,
        kind=member.kind,
        state="resolved",
        experiments=tuple(sorted(set(experiments))),
        name=None if held is None else held.name,
        path=None if held is None else held.path,
        line_now=None if line is None else line.line.label,
        line_words_now=() if line is None else tuple(line.line.label_words),
        line_drifted=drifted,
        line_kept=None if line is None else line.held,
        line_added_by=None if line is None else line.of,
    )


def _held(entry: str, view: RunView) -> EntryInfo | None:
    """The entry in the run a view reads: at its current place, else at an earlier one (a run
    made before the entry moved holds it under the identifier it had then)."""
    found = view.entries.get(entry)
    for earlier in view.aliases.get(entry, ()):
        found = found or view.entries.get(earlier)
    return found


def _said_as(member: TopicMember, topic: Topic) -> tuple[str, str]:
    """The name and path an entry had when it was said."""
    entry = str(member.entry)
    return topic.names.get(entry, ""), topic.paths.get(entry, "")


def _read_entry(member: TopicMember, topic: Topic, view: RunView) -> MemberReading:
    held = _held(str(member.entry), view)
    if held is None:
        return _nothing(
            member,
            "this run holds no such file or folder (moved away or gone)",
            said=_said_as(member, topic),
        )
    base = view.base
    if held.kind == "file":
        owner = base.units.get(held.parent or "")
        if owner is None:
            return _nothing(member, "the file lies in no experiment", said=None)
        return _resolved(member, [owner], held=held)
    inside = base.units.get(held.entry_id)
    if inside is not None:
        return _resolved(member, [inside], held=held)
    beneath = [
        experiment
        for experiment in base.experiments
        if _lies_beneath(experiment, held.entry_id, base.parents)
    ]
    if not beneath:
        return _nothing(member, "the folder lies in no experiment and holds none")
    return _resolved(member, beneath, held=held)


def _lies_beneath(folder: str, above: str, parents: Mapping[str, str | None]) -> bool:
    seen: set[str] = set()
    current: str | None = parents.get(folder)
    while current is not None and current not in seen:
        if current == above:
            return True
        seen.add(current)
        current = parents.get(current)
    return False


def _read_experiment(member: TopicMember, topic: Topic, view: RunView) -> MemberReading:
    base = view.base
    held = _held(str(member.entry), view)
    if held is None:
        return _nothing(
            member,
            "this run holds no such folder (moved away or gone)",
            said=_said_as(member, topic),
        )
    if held.entry_id in base.experiments:
        return _resolved(member, [held.entry_id], held=held)
    owner = base.units.get(held.entry_id)
    if owner is not None:
        return _resolved(member, [owner], held=held)
    return _nothing(member, "this run does not count the folder as an experiment")


def _read_core(member: TopicMember, view: RunView) -> MemberReading:
    if view.core_terms is None:
        return _not_read(member, view.cores_why or "the words experiments carry were not read")
    # The ways the lattice reads the core's words in this run (one under ``terms``; the spellings
    # the names write under ``spellings``): an experiment holding every word of any way carries it.
    ways = (
        [frozenset(way) for way in view.core_ways(member.terms)]
        if view.core_ways is not None
        else [frozenset(key_of_word(term) for term in member.terms)]
    )
    carrying = [
        experiment
        for experiment, terms in view.core_terms.items()
        if any(way <= terms for way in ways)
    ]
    if not carrying:
        return _nothing(member, "no experiment in this run carries all of its words")
    return _resolved(member, carrying)


def _read_line(member: TopicMember, view: RunView) -> MemberReading:
    if view.lines is None:
        return _not_read(member, view.lines_why or "the lines of work were not read")
    from ingence.themes.lines import match_line

    found = match_line(member.anchors, member.words, view.lines.lines)
    if found is None:
        # No line holds more than half of what it was added by: it is followed by none.
        closest = match_line(member.anchors, member.words, view.lines.lines, minimum_share=0.0)
        if closest is None:
            return _nothing(
                member, "none of the experiments the line was added by is in a line of work now"
            )
        return _nothing(
            member,
            f"the line has split: no line of work holds more than half of the experiments it was "
            f"added by (the closest holds {closest.held} of {closest.of})",
        )
    said = {word.casefold() for word in member.words}
    now = tuple(found.line.label_words)
    # The words it was added with against the words it has now: a different label reads as a
    # line that has drifted, though the line is the same one by its experiments.
    drifted = bool(said) and said != {word.casefold() for word in now}
    return _resolved(member, sorted(found.line.experiments), line=found, drifted=drifted)


def _read_goal(member: TopicMember, view: RunView) -> MemberReading:
    """What a goal member resolves to: the experiments in the goal's top tier in this run.

    A goal is followed by its words. The tier is read as the Goals page reads it. A goal since
    removed resolves to nothing.
    """
    if view.goals is None:
        return _not_read(member, view.goals_why or "the goals were not read against this run")
    goal_id = member.goal_id or member.key.partition(":")[2]
    found = view.goals.get(goal_id)
    if found is None:
        return _nothing(member, "no goal of this identifier stands any more")
    if not found:
        return _nothing(member, "no experiment in this run matches every word of the goal")
    return _resolved(member, found)


def read_member(member: TopicMember, topic: Topic, view: RunView) -> MemberReading:
    """What one member of ``topic`` resolves to in the run ``view`` reads."""
    if not view.base.rules_applied:
        return _not_read(member, NO_RULES_APPLIED)
    if member.kind == "experiment":
        return _read_experiment(member, topic, view)
    if member.kind == "core":
        return _read_core(member, view)
    if member.kind == "line":
        return _read_line(member, view)
    if member.kind == "goal":
        return _read_goal(member, view)
    return _read_entry(member, topic, view)


def read_members(topic: Topic, view: RunView) -> tuple[MemberReading, ...]:
    """Every member of the topic read in the run ``view`` reads, its experiments named as they
    are now (a view of an earlier run maps an experiment that has since moved to its new place)."""
    readings = [read_member(member, topic, view) for member in topic.members]
    if not view.moved:
        return tuple(readings)
    return tuple(
        replace(
            reading,
            experiments=tuple(sorted({view.moved.get(e, e) for e in reading.experiments})),
        )
        for reading in readings
    )


def union(members: Sequence[MemberReading]) -> tuple[str, ...]:
    return tuple(sorted({e for member in members for e in member.experiments}))


def overlaps(
    held: Mapping[str, tuple[str, ...]], names: Mapping[str, str]
) -> dict[str, list[Overlap]]:
    """For each topic, the topics sharing experiments with it and the experiments shared,
    ordered by most shared, then name, then identifier."""
    found: dict[str, list[Overlap]] = {}
    for topic_id, mine in held.items():
        own = set(mine)
        row = [
            Overlap(topic_id=other, name=names[other], experiments=tuple(sorted(own & set(theirs))))
            for other, theirs in held.items()
            if other != topic_id and own & set(theirs)
        ]
        found[topic_id] = sorted(
            row, key=lambda item: (-len(item.experiments), item.name, item.topic_id)
        )
    return found


def _why_joined(member: TopicMember) -> str:
    if member.kind == "core":
        return f"now carries the words {', '.join(member.terms)}"
    if member.kind == "goal":
        return "now matches the goal's words"
    if member.kind == "line":
        return LINE_CHANGED
    if member.kind == "experiment":
        return "is an experiment in this run"
    return "now holds an item the topic names"


def _why_left(member: TopicMember) -> str:
    if member.kind == "core":
        return f"no longer carries the words {', '.join(member.terms)}"
    if member.kind == "goal":
        return "no longer matches the goal's words"
    if member.kind == "line":
        return LINE_CHANGED
    if member.kind == "experiment":
        return "is no longer an experiment in this run"
    return "no longer holds an item the topic names"


def compare(
    topic: Topic, now: Sequence[MemberReading], before: Sequence[MemberReading], since_run_id: str
) -> Drift:
    """Experiments that joined and left ``topic`` between two runs, each with the members that
    explain it. A member not read in one of the runs is left out of both sides and named."""
    earlier = {reading.key: reading for reading in before}
    by_key = {member.key: member for member in topic.members}
    joined: dict[str, list[str]] = {}
    left: dict[str, list[str]] = {}
    unread: list[str] = []
    for reading in now:
        was = earlier.get(reading.key)
        if reading.state == "not_computed" or was is None or was.state == "not_computed":
            unread.append(reading.key)
            continue
        member = by_key[reading.key]
        for experiment in sorted(set(reading.experiments) - set(was.experiments)):
            joined.setdefault(experiment, []).append(_why_joined(member))
        for experiment in sorted(set(was.experiments) - set(reading.experiments)):
            left.setdefault(experiment, []).append(_why_left(member))
    # An experiment another member still holds in both runs neither joined nor left.
    still = {
        e
        for reading in now
        if reading.state != "not_computed" and reading.key not in unread
        for e in reading.experiments
    }
    held_before = {
        e
        for reading in before
        if reading.state != "not_computed" and reading.key not in unread
        for e in reading.experiments
    }
    return Drift(
        since_run_id=since_run_id,
        joined=tuple(
            Change(experiment=e, reasons=tuple(dict.fromkeys(why)))
            for e, why in sorted(joined.items())
            if e not in held_before
        ),
        left=tuple(
            Change(experiment=e, reasons=tuple(dict.fromkeys(why)))
            for e, why in sorted(left.items())
            if e not in still
        ),
        unread=tuple(sorted(unread)),
    )


def read_topic(
    topic: Topic,
    view: RunView,
    *,
    earlier: tuple[RunView, str] | None,
) -> TopicReading:
    """One topic against the run ``view`` reads, and against ``earlier`` (a view and its run
    identifier) for drift. Overlaps are filled in by the caller, who holds every topic."""
    members = read_members(topic, view)
    experiments = union(members)
    nothing = tuple(m.key for m in members if m.state == "nothing")
    not_read = tuple(m.key for m in members if m.state == "not_computed")
    files = sum(view.base.files.get(e, 0) for e in experiments)
    drift = None
    if earlier is not None:
        before_view, before_run = earlier
        drift = compare(topic, members, read_members(topic, before_view), before_run)
    return TopicReading(
        topic=topic,
        members=members,
        experiments=experiments,
        files=files,
        complete=not not_read,
        read=any(m.state != "not_computed" for m in members),
        nothing=nothing,
        not_computed=not_read,
        overlaps=(),
        shared=0,
        drift=drift,
    )
