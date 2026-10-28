"""A topic and its drift in plain sentences, each a template filled from the engine's counts.

The page and the run's manifest say these sentences as they are written here, so no number on a
page is computed anywhere the engine did not compute it (a count always with what it is out of).
Nothing here is a model: a sentence is a template and a count.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.topics.reading import CLAUSE_REASONS

if TYPE_CHECKING:
    from ingence.topics.model import Change, TopicReading

__all__ = ["drift_said", "plural", "topic_said"]


def plural(count: int, noun: str) -> str:
    """``1 experiment``, ``2 experiments`` (counts with thousands separators)."""
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def topic_said(reading: TopicReading, run_experiments: int | None) -> str:
    """What a topic holds in the run, out of the run's experiments, or that it was not read."""
    if not reading.read:
        why = next((m.why for m in reading.members if m.why), None)
        return f"Not read: {why or 'none of its members could be read'}."
    held = len(reading.experiments)
    out_of = "" if run_experiments is None else f" of {plural(run_experiments, 'experiment')}"
    parts = [
        (f"{held:,}{out_of}" if out_of else plural(held, "experiment"))
        + f", {plural(reading.files, 'file')}."
    ]
    if reading.nothing:
        count = len(reading.nothing)
        verb = "resolves" if count == 1 else "resolve"
        parts.append(f"{plural(count, 'member')} {verb} to no experiment in this run.")
    if reading.not_computed:
        count = len(reading.not_computed)
        parts.append(
            f"{plural(count, 'member')} could not be read, so the topic may hold more than this."
        )
    return " ".join(parts)


def _because(reasons: tuple[str, ...], subject: str) -> str:
    """Reasons as one clause: a clause stands as it is, a verb phrase follows the subject."""
    clauses = [r if r in CLAUSE_REASONS else f"{subject} {r}" for r in reasons]
    return " and ".join(clauses)


def _grouped(changes: tuple[Change, ...]) -> list[tuple[tuple[str, ...], int]]:
    counted: dict[tuple[str, ...], int] = {}
    for change in changes:
        counted[change.reasons] = counted.get(change.reasons, 0) + 1
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))


def drift_said(reading: TopicReading) -> list[str]:
    """The experiments that joined and left the topic, each group with why, in sentences. Empty
    where nothing changed or there was no earlier run to compare with."""
    drift = reading.drift
    if drift is None:
        return []
    name = f"“{reading.topic.name}”"
    said: list[str] = []
    for verb, changes in (("joined", drift.joined), ("left", drift.left)):
        for reasons, count in _grouped(changes):
            subject = "it" if count == 1 else "each"
            said.append(
                f"{plural(count, 'experiment')} {verb} {name}: {_because(reasons, subject)}."
            )
    if drift.unread:
        count = len(drift.unread)
        said.append(
            f"{plural(count, 'member')} of {name} could not be read in one of the two runs, "
            "so nothing is said of what they gave or took."
        )
    return said
