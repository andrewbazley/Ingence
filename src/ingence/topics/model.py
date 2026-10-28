"""What a reading of the topics gives back: plain values, nothing stored.

A topic is a person's statement in the Record (``api/routes/overlay.Topic``). What its members
come to in a run is read here, from the run, when asked:

- a member resolves to some experiments (``state`` ``resolved``), or to nothing
  (``nothing``: the run holds no experiment it names, said in words), or could not be read
  (``not_computed``: the component that reads it is not available, said in words). Unknown,
  nothing and conflicting are three states. A count of experiments is ``None`` where it could
  not be read, never 0;
- a topic's experiments are the union of its members', its files are those of the experiments
  (an experiment's files are the files whose highest experiment it is);
- drift is what changed between two runs with the topic read the same way in both: the
  experiments that joined or left, each with the member that explains it, in words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.api.routes.overlay import Topic

__all__ = [
    "Change",
    "Drift",
    "ExperimentInfo",
    "MemberReading",
    "Overlap",
    "Suggestion",
    "TopicReading",
    "TopicResolution",
]

State = Literal["resolved", "nothing", "not_computed"]


@dataclass(frozen=True)
class ExperimentInfo:
    """An experiment: its folder (below its root) and how many files it holds in this run.

    An experiment a topic held in the earlier run and the run read no longer has (``in_run``
    false) keeps its earlier folder and has no count of files here: ``files`` is None, never 0.
    """

    experiment: str
    folder: str
    files: int | None
    in_run: bool = True


@dataclass(frozen=True)
class MemberReading:
    """One member of a topic and what it resolves to in one run."""

    key: str
    kind: Literal["entry", "experiment", "core", "line", "goal"]
    state: State
    #: The experiments it resolves to, sorted. Empty unless ``resolved``.
    experiments: tuple[str, ...] = ()
    #: Why it resolves to nothing, or could not be read, in words. It is None where it resolved.
    why: str | None = None
    #: A file, folder or experiment: its name and path in the run, or as it was said where the run
    #: holds it no more (``in_run`` false). Empty for a core and a line.
    name: str | None = None
    path: str | None = None
    in_run: bool = True
    #: A line only: the line of the run found by its experiments (its label, its words), and
    #: whether those words are not the ones it was added with ("the line has drifted").
    line_now: str | None = None
    line_words_now: tuple[str, ...] = ()
    line_drifted: bool = False
    #: A line only: how many of the experiments it was added by the matched line holds, of how
    #: many it was added by.
    line_kept: int | None = None
    line_added_by: int | None = None


@dataclass(frozen=True)
class Change:
    """One experiment that joined or left a topic between two runs, and why, in words."""

    experiment: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Drift:
    """What changed in a topic between the compared run and this one."""

    since_run_id: str
    joined: tuple[Change, ...]
    left: tuple[Change, ...]
    #: Members that could not be read in one of the two runs (so nothing is said of what they
    #: gave or took), by key.
    unread: tuple[str, ...] = ()


@dataclass(frozen=True)
class Overlap:
    """Experiments two topics both hold."""

    topic_id: str
    name: str
    #: Experiments in both, sorted.
    experiments: tuple[str, ...]


@dataclass(frozen=True)
class TopicReading:
    """One topic as one run reads it."""

    topic: Topic
    members: tuple[MemberReading, ...]
    #: The union of the members' experiments, sorted.
    experiments: tuple[str, ...]
    #: Files of those experiments (an experiment's files are those whose highest experiment it
    #: is).
    files: int
    #: False where a member could not be read: the experiments and files are then those of the
    #: members that could, at least.
    complete: bool
    #: Whether any member was read. Where none was (a run that applied no rules, a reading not
    #: available) the topic's experiments and files are unknown, never 0: ``experiments`` and
    #: ``files`` above hold nothing and a page says it was not read.
    read: bool
    #: Members that resolve to nothing, and members that could not be read, by key.
    nothing: tuple[str, ...]
    not_computed: tuple[str, ...]
    overlaps: tuple[Overlap, ...]
    #: Of this topic's experiments, how many another topic holds too.
    shared: int
    #: None where there is no earlier run to compare with (see ``TopicResolution.drift_why``).
    drift: Drift | None


@dataclass(frozen=True)
class Suggestion:
    """A broad line of work offered as a place to start a topic. Never made by itself."""

    broad: int
    label: str
    words: tuple[str, ...]
    experiments: tuple[str, ...]
    #: How many of its experiments a topic already holds.
    in_topics: int


@dataclass(frozen=True)
class TopicResolution:
    """Every topic read against one run."""

    run_id: str
    #: The run drift is read against, and why there is none, in words, where there is none.
    compared_run_id: str | None
    drift_why: str | None
    topics: tuple[TopicReading, ...]
    #: The experiments the topics hold (and, with suggestions, the suggested ones), by identifier.
    experiments: Mapping[str, ExperimentInfo] = field(default_factory=dict)
    #: Every experiment of the run. It is None where the run applied no rules (a census), which
    #: holds no experiments yet: unknown, never 0.
    run_experiments: int | None = 0
    suggestions: tuple[Suggestion, ...] = ()
    #: Why there are no suggestions, in words, where the lines of work could not be read.
    suggestions_why: str | None = None
