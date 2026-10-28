"""Goals: what a person is looking for, in their own words, kept in the workspace.

A goal is a sentence a person wrote ("U2OS titrations with analysis"). The run answers it as Ask
answers a question (:mod:`ingence.search.ask`): the experiments matching it by tier, with why,
and a summary in counts. A goal is a projection, and inference never reads it. It is written to
the Dashboard's workspace (``workspace/goals.jsonl``, beside the search's hides), never to the
Record and never into a run. So it may be written while a scan runs, and nothing the engine
concludes depends on it. A goal names who wrote it where a page says so. A missing actor is not
refused, because a goal decides nothing.

A goal resolves every run to the experiments in the top tier (named: every known word matched).
A person can lower it to "at least n of m": the named tier then takes every experiment
matching at least ``n`` of the goal's ``m`` known words. Experiments in the same line of work
are offered as a labelled suggestion (the same-theme tier), never added.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import re
import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import open_appending
from ingence.storage.layout import owned_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = [
    "GOALS_FILE",
    "TEXT_MAXIMUM",
    "Goal",
    "goal_experiments",
    "goal_id_of",
    "lower_goal",
    "read_goals",
    "remove_goal",
    "write_goal",
]

#: Beside the search's hides: the workspace, never the Record, never a run.
GOALS_FILE = "workspace/goals.jsonl"
#: The most characters a goal may run to (the controls' own limit).
TEXT_MAXIMUM = 500
#: How a goal's identifier is spelled.
_GOAL_ID = re.compile(r"^goal_[0-9a-z]{1,40}$")
#: The lock file beside the goals: a write counts the goals and appends under it, so two
#: writes at once (two pages, two servers) never take one identifier.
_LOCK_FILE = "workspace/goals.lock"
_WRITING = threading.Lock()


@contextlib.contextmanager
def _locked(project: Path) -> Iterator[None]:
    """Hold the goals' lock: this process's, then the file's (shared by every process)."""
    path = owned_path(project, _LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITING, path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class Goal:
    """One goal as it stands."""

    id: str
    text: str
    actor: str | None
    written_at: str
    #: Lowered to "at least n of m": the fewest of its known words an experiment must match to be
    #: in the top tier. It is None for every word.
    at_least: int | None = None


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(project: Path, line: dict[str, Any]) -> None:
    path = owned_path(project, GOALS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_appending(path) as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def _text(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        message = "a goal is a sentence; none was given"
        raise ValueError(message)
    cleaned = " ".join(text.split())
    if len(cleaned) > TEXT_MAXIMUM:
        message = f"a goal is at most {TEXT_MAXIMUM} characters; {len(cleaned)} were given"
        raise ValueError(message)
    return cleaned


def goal_id_of(project: Path) -> str:
    """The next goal's identifier: numbered in the order goals were written, never reused."""
    path = project / GOALS_FILE
    count = 0
    if path.is_file():
        for raw in path.read_text("utf-8").splitlines():
            try:
                if json.loads(raw).get("act") == "write":
                    count += 1
            except (ValueError, AttributeError):
                continue
    return f"goal_{count + 1}"


def write_goal(project: Path, text: object, *, actor: object = None) -> Goal:
    """Write one goal, or refuse an empty or overlong one with :class:`ValueError`."""
    cleaned = _text(text)
    who = " ".join(str(actor).split()) if isinstance(actor, str) and actor.strip() else None
    with _locked(project):
        goal = Goal(id=goal_id_of(project), text=cleaned, actor=who, written_at=_now())
        _append(
            project,
            {
                "act": "write",
                "id": goal.id,
                "text": goal.text,
                "actor": goal.actor,
                "at": goal.written_at,
            },
        )
    return goal


def remove_goal(project: Path, goal_id: object, *, actor: object = None) -> bool:
    """Remove a goal, and return False where no goal of that identifier stands."""
    if not isinstance(goal_id, str) or not _GOAL_ID.match(goal_id):
        message = "a goal is named by its identifier (goal_<n>)"
        raise ValueError(message)
    who = " ".join(str(actor).split()) if isinstance(actor, str) and actor.strip() else None
    with _locked(project):
        if goal_id not in {goal.id for goal in read_goals(project)}:
            return False
        _append(project, {"act": "remove", "id": goal_id, "actor": who, "at": _now()})
    return True


def lower_goal(project: Path, goal_id: str, at_least: int | None, *, actor: object = None) -> Goal:
    """Lower a goal to "at least n of m" (or raise it back to every word with None)."""
    if at_least is not None and (isinstance(at_least, bool) or at_least < 1):
        message = "at_least is a whole number of words, one or more"
        raise ValueError(message)
    who = " ".join(str(actor).split()) if isinstance(actor, str) and actor.strip() else None
    with _locked(project):
        standing = {goal.id: goal for goal in read_goals(project)}
        if goal_id not in standing:
            message = f"no goal {goal_id!r} stands"
            raise LookupError(message)
        _append(
            project,
            {"act": "lower", "id": goal_id, "at_least": at_least, "actor": who, "at": _now()},
        )
    return replace(standing[goal_id], at_least=at_least)


def goal_experiments(project: Path, run_id: str) -> tuple[dict[str, tuple[str, ...]], str | None]:
    """Each standing goal's experiments in one run: those in its top tier (named), read as the
    Goals route reads them, by goal identifier. The second value is the reason, where the run could
    not be read (the mapping is then empty and the reason says so). It is what a topic's goal member
    resolves to."""
    from ingence.api.v1 import V1Context
    from ingence.api.v1.asking import ask_in_run, run_experiments
    from ingence.storage.catalog import open_catalog

    standing = read_goals(project)
    if not standing:
        return {}, None
    context = V1Context(project=project, run_current=run_id, run_pinned=run_id, job_running=False)
    found: dict[str, tuple[str, ...]] = {}
    try:
        with open_catalog(project, run_id=run_id) as catalog:
            if not catalog.has_run:
                return {}, "this run cannot be asked: it holds no index"
            known = run_experiments(context, catalog, run_id)
            for goal in standing:
                reading = ask_in_run(
                    context, catalog, goal.text, known.asked, at_least=goal.at_least
                )
                found[goal.id] = tuple(
                    sorted(item.experiment.experiment_id for item in reading.named)
                )
    except FileNotFoundError:
        return {}, "the run the goals would be read against cannot be opened"
    return found, None


def read_goals(project: Path) -> list[Goal]:
    """Every goal standing, in the order written. A line that cannot be read is skipped."""
    path = project / GOALS_FILE
    if not path.is_file():
        return []
    standing: dict[str, Goal] = {}
    for raw in path.read_text("utf-8").splitlines():
        try:
            line = json.loads(raw)
            act = str(line["act"])
            goal_id = str(line["id"])
        except (ValueError, KeyError, TypeError):
            continue
        if act == "write":
            standing[goal_id] = Goal(
                id=goal_id,
                text=str(line.get("text") or ""),
                actor=line.get("actor") if isinstance(line.get("actor"), str) else None,
                written_at=str(line.get("at") or ""),
            )
        elif act == "remove":
            standing.pop(goal_id, None)
        elif act == "lower" and goal_id in standing:
            lowered = line.get("at_least")
            standing[goal_id] = replace(
                standing[goal_id],
                at_least=int(lowered) if isinstance(lowered, int) and lowered >= 1 else None,
            )
    return list(standing.values())
