"""Find the day each group of files began, and its last day.

The day matters, not the second. The rules already read a copy by its modification time and set
impossible times aside, such as a creation date in 1969. So a copy-paste birth date never dates a
group. For example, an analysis that was copied a day after it was made is dated by the day it
was made.

A group's origin day is the earliest day among its members' earliest credible times. A group's
last day is the latest such day. A timeline lists groups by origin day, and within one day by
stage, where data enters first. A group at a later stage whose origin day comes before the origin
day of every group at an entering stage is out of order. This is the one case where a time is
worth a person's attention. Nothing here reads a clock. The module reads the days the rules
concluded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = ["GroupDays", "day_of", "days_by_stage", "out_of_order", "timeline"]


def day_of(earliest_credible_time: str) -> str | None:
    """The day an ``earliest_credible_time`` value names (``YYYY-MM-DD``), or None if unreadable."""
    try:
        at = json.loads(earliest_credible_time)["at"]
    except (ValueError, KeyError, TypeError):
        return None
    return str(at)[:10] if isinstance(at, str) and len(at) >= 10 else None


@dataclass(frozen=True, slots=True)
class GroupDays:
    """One group of files and the days it spans: its name, its stage, and its first and last day."""

    group: str
    stage: str
    first: str
    last: str
    files: int


def timeline(
    members: Iterable[tuple[str, str, str]], *, stage_order: Sequence[str]
) -> list[GroupDays]:
    """Each group's origin and last day, listed by origin day and then by stage.

    ``members`` gives, for each file, its group, its stage, and its day. A stage not named in
    ``stage_order`` sorts after every named one, and groups tie-break by name.
    """
    spans: dict[tuple[str, str], list[str]] = {}
    for group, stage, day in members:
        spans.setdefault((group, stage), []).append(day)
    rank = {stage: position for position, stage in enumerate(stage_order)}
    found = [
        GroupDays(group=group, stage=stage, first=min(days), last=max(days), files=len(days))
        for (group, stage), days in spans.items()
    ]
    return sorted(found, key=lambda g: (g.first, rank.get(g.stage, len(rank)), g.group))


def out_of_order(groups: Sequence[GroupDays], *, entry_stages: Iterable[str]) -> list[GroupDays]:
    """The groups at a later stage whose origin day precedes every entering group's origin day."""
    entering = set(entry_stages)
    starts = [g.first for g in groups if g.stage in entering]
    if not starts:
        return []
    earliest = min(starts)
    return [g for g in groups if g.stage not in entering and g.first < earliest]


def days_by_stage(groups: Sequence[GroupDays]) -> Mapping[str, str]:
    """The earliest origin day of each stage, for a sentence that says when each began."""
    first: dict[str, str] = {}
    for group in groups:
        first[group.stage] = min(first.get(group.stage, group.first), group.first)
    return first
