"""What waits for a re-run: one list of changes, a count, and nothing started by itself.

Opening a project that holds a change no run has followed signals that a re-run is needed, and
offers it now or later, at the person's choice. Answers and other choices that a person makes in
the Dashboard go on one list of changes. They are not applied until the person starts a rescan or
rebind, and then every new rule and change since the last run takes effect. A badge near the Re-run
button counts the changes and gives a short summary. The summary stays at the level of a count by
kind, and shows the rows only when asked.

- Nothing starts by itself. A session that opens where changes wait says so (one notice on the
  page, with Re-run now and Later, and one line on the command line) and starts nothing.
- One list (:class:`Pending`) holds every change since the last run that changes a reading: a
  committed setting, a person's answer that the names or the rules read, a word defined, turned off
  or back on, and a pack accepted, installed or removed. Each is a :class:`Change` of one of the
  :data:`KINDS`, with the steps it needs. A word's meaning waits too: it takes effect after the
  re-run.
- A count on Re-run and a summary by kind (:meth:`Pending.summary`) give a line per kind, and the
  rows under it only when asked. There is no alert line and no size threshold: a change waits
  whatever its size, and the count says so.
- Re-run applies all of it (:meth:`Pending.request`). The names are read again where any change
  needs it. Only the names holding the changed words are read where only words changed. Every name
  is read where anything else changed, and wherever the partial rebind refuses (the worker's
  fallback). Then the rules apply. A walk or a dive reads the share, so the page gives its
  commands and a person starts it. What reads only the project is applied meanwhile.

Every policy choice is a table in this file or in ``state.py``, so changing one is changing one row.
:data:`SETTING_NEEDS` says which step each settings group needs, :data:`KINDS` holds the words of
each kind of change, and the table in ``state.py`` says which answers the rules or the names read.

A step that a change needs is done only by a run that did that step and began after the change, in
the current run's line of descent. That line is each run's parent, back from the current run.
:data:`STEPS_A_RUN_DOES` says which steps each kind of run does, read from its run record. A dive
must follow the walk it needs, and the rules must be applied after every other step the change
needs (:data:`MUST_FOLLOW`). A partial rebind reads again only the names holding its words, so it
does the rebind for a change to those words and for nothing else. A re-run that began reading
names before a change read the old value, so it does not settle that change. The Record that a
run read places a person's decision (the run's watermark, schema 1.22), whatever the clocks say.
A commit or a pack's page is not in the Record, so the clocks place it. Where a run and such a
change fall in one second, the change waits: a run offered twice costs time, and a run believed
done that is not done is an error. Nothing here is stored. What waits is worked out from the
checkpoints, the Record and the runs every time.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "KINDS",
    "MUST_FOLLOW",
    "SETTING_NEEDS",
    "STEPS_A_RUN_DOES",
    "Change",
    "Pending",
    "RunDone",
    "Step",
    "instant",
    "kind_of_run",
    "needs_of_settings",
    "pending",
    "still_needed",
]


class Step(StrEnum):
    """What a re-run does, in the order the steps must happen."""

    WALK = "walk"
    DIVE = "dive"
    REBIND = "rebind"
    INFERENCE = "inference"

    @property
    def reads_the_share(self) -> bool:
        return self in (Step.WALK, Step.DIVE)

    @property
    def words(self) -> str:
        return {
            Step.WALK: "list the files again (reads your data folders)",
            Step.DIVE: "read file contents again (reads your data folders)",
            Step.REBIND: "read the names again from the saved file list",
            Step.INFERENCE: "apply the rules again",
        }[self]

    @property
    def command(self) -> str:
        return {
            Step.WALK: "ingence index",
            Step.DIVE: "ingence dive",
            Step.REBIND: "ingence index --rebind",
            Step.INFERENCE: "ingence infer",
        }[self]


_ORDER = (Step.WALK, Step.DIVE, Step.REBIND, Step.INFERENCE)

#: The policy table: what each setting needs run when it changes.
#: A key is a settings group (``scan``) or one field of it (``scan.workers``). The longest key
#: that names a changed setting decides. ``None`` says the setting changes how fast the engine
#: gets there and never what it concludes (the test in ``core/settings.py``), so it needs
#: nothing. A test holds every field of ``Settings`` to a row, so a setting added later cannot
#: be committed without somebody saying what it needs.
SETTING_NEEDS: Mapping[str, Step | None] = {
    "schema_version": None,
    "depth": Step.DIVE,
    "scan": Step.WALK,
    "scan.workers": None,
    "scan.flush_every_records": None,
    "scan.flush_every_seconds": None,
    "hash": Step.DIVE,
    "hash.chunk_bytes": None,
    "extraction": Step.DIVE,
    "dictionary": Step.REBIND,
    # The spellings that the Words section offers as variants are a view of a published run.
    # They are computed when the page is read and never bound, so a change needs no run.
    "dictionary.variants": None,
    "journal": None,
    # A listing root's settings: whether declared links are recorded, and whether each file's
    # headers are asked for, change what the census writes. The pause and the user agent change only
    # how politely the archive is asked.
    "listing": Step.WALK,
    "listing.max_listing_bytes": None,
    "listing.max_listing_files": None,
    "listing.pause_seconds": None,
    "listing.user_agent": None,
    # How the Dashboard runs a job: where, how often it is retried, and how often its worker says
    # it is alive. None of it changes what a run concludes.
    "jobs": None,
    # How an inference holds its pool: whole or streamed by folder, the chunk, the projection and
    # the margin. A streamed and a whole pass publish the same rows, so none of it changes what a
    # run concludes.
    "infer": None,
    # Which word the lattice sees for a bound spelling: its Dictionary term, or the spelling. The
    # groups the rules conclude are made of those words, so a change waits for the rules to apply
    # again. No name is read again.
    "groups": Step.INFERENCE,
    # A projection's settings apply when a page is next read, with no run. This holds for
    # completeness and themes alike. The settings change a view and never a conclusion, so nothing
    # here waits. A person's answers are different: each is a Record event, and waits for the next
    # run, which reads it at its watermark.
    "completeness": None,
    # The design grid is a projection of a published run, recomputed when read. A change to how it
    # is drawn needs no run.
    "design": None,
    # The same-columns lines and the inclusion index are read from what a dive stored, when the
    # file page asks: a change to them needs no run.
    "columns": None,
    # Themes are a projection recomputed from the published run when it is next read, keyed by
    # these settings, so a change needs no run.
    "themes": None,
    # The question sheet is read from a published run, so there is nothing to run again.
    "questions": None,
    # What the search box reads beside its results: a search reads a published run and writes
    # nothing into one, so none of it needs a run.
    "search": None,
}


#: Which steps each kind of run does. A census walks the folders and reads every name as it goes,
#: so it also reads the names again. A whole rebind reads every name again from the stored census.
#: A partial rebind (a word question's) reads only the names that hold the answered spellings, so
#: it does no step a commit can need. A dive reads inside files. An inference applies the rules.
STEPS_A_RUN_DOES: Mapping[str, frozenset[Step]] = {
    "census": frozenset({Step.WALK, Step.REBIND}),
    "rebind": frozenset({Step.REBIND}),
    "partial rebind": frozenset(),
    "dive": frozenset({Step.DIVE}),
    "inference": frozenset({Step.INFERENCE}),
}

#: Which steps a step must come after, where the commit needs both: a dive reads the files the
#: walk found, and the rules are applied to what every other step made.
MUST_FOLLOW: Mapping[Step, tuple[Step, ...]] = {
    Step.WALK: (),
    Step.DIVE: (Step.WALK,),
    Step.REBIND: (),
    Step.INFERENCE: (Step.WALK, Step.DIVE, Step.REBIND),
}


def kind_of_run(*, profile: str, whole_rebind: bool, applied_rules: bool) -> str:
    """A run's kind, a key of :data:`STEPS_A_RUN_DOES`, from what its run record holds.

    An inference records the depth profile of the run it read, so it is known by the rules it
    applied rather than by its profile; a census records ``census``, a rebind ``rebind`` (a
    partial one with the spellings it read again), and a dive the depth it read at.
    """
    if applied_rules:
        return "inference"
    if profile == "census":
        return "census"
    if profile == "rebind":
        return "rebind" if whole_rebind else "partial rebind"
    return "dive"


@dataclass(frozen=True, slots=True)
class RunDone:
    """One run in the current run's line of descent: when it began and which steps it did."""

    started_at: str
    steps: frozenset[Step]
    run_id: str = ""
    #: A partial rebind's words, casefolded: it read again only the names holding them. None for
    #: every other run.
    spellings: frozenset[str] | None = None
    #: The highest Record sequence the run read (schema 1.22's ``record_watermark``), or None
    #: where it read none or does not say.
    watermark: int | None = None


def needs_of_settings(changed: Sequence[str]) -> tuple[Step, ...]:
    """The steps the changed settings (dotted names) need, in order, inference last."""
    found: set[Step] = set()
    for dotted in changed:
        parts = dotted.split(".")
        for length in range(len(parts), 0, -1):
            key = ".".join(parts[:length])
            if key in SETTING_NEEDS:
                need = SETTING_NEEDS[key]
                if need is not None:
                    found.add(need)
                break
        else:
            # A setting no row names is assumed to need the most, never the least.
            found.add(Step.WALK)
    if found:
        found.add(Step.INFERENCE)
    return tuple(step for step in _ORDER if step in found)


#: The kinds of change a re-run applies, in the order the summary lists them, each with its words
#: for one and for more. An answer is one a person gave to a question, whether the names or the
#: rules read it. A word defined, turned off or back on belongs to the Dictionary. A pack is
#: accepted or refused, or installed or removed on its page. A setting is each value a commit
#: changed, a rule's settings included.
KINDS: Mapping[str, tuple[str, str]] = {
    "answer": ("answer", "answers"),
    "word defined": ("word defined", "words defined"),
    "word off": ("word turned off", "words turned off"),
    "word on": ("word turned back on", "words turned back on"),
    "pack": ("Dictionary change", "Dictionary changes"),
    "setting": ("setting", "settings"),
}


def counted(kind: str, count: int) -> str:
    """``3 answers``, ``1 word turned off``: a kind's count in words."""
    one, more = KINDS[kind]
    return f"{count} {one if count == 1 else more}"


@dataclass(frozen=True, slots=True)
class Change:
    """One change a re-run applies: its kind, its rows in words, when it was made, its needs."""

    key: str
    kind: str
    rows: tuple[str, ...]
    made: object
    needs: tuple[Step, ...]
    #: Where the change is to what some words mean: those words, casefolded, so only the names
    #: holding them need reading again. Empty where every name does.
    spellings: frozenset[str] = field(default=frozenset())
    #: The change's Record sequence, where it is a person's decision: a run that read the Record
    #: to it followed it, whatever the clocks say.
    sequence: int | None = None

    @property
    def count(self) -> int:
        return max(1, len(self.rows))


@dataclass(frozen=True, slots=True)
class Pending:
    """Every change waiting for a re-run, each with the steps it still needs."""

    changes: tuple[Change, ...]
    steps: tuple[Step, ...]

    @property
    def count(self) -> int:
        """What the badge on Re-run says: every row, a commit counted by the settings it changed."""
        return sum(change.count for change in self.changes)

    @property
    def commits(self) -> tuple[str, ...]:
        """The waiting commits' checkpoints."""
        return tuple(change.key for change in self.changes if change.kind == "setting")

    @property
    def answers(self) -> tuple[str, ...]:
        """Every waiting change that is not a commit, in words (a pack accepted, a group)."""
        return tuple(
            row for change in self.changes if change.kind != "setting" for row in change.rows
        )

    def summary(self) -> list[dict[str, object]]:
        """A line per kind, in :data:`KINDS` order: its count in words and its rows."""
        lines: list[dict[str, object]] = []
        for kind in KINDS:
            held = [change for change in self.changes if change.kind == kind]
            if not held:
                continue
            count = sum(change.count for change in held)
            lines.append(
                {
                    "kind": kind,
                    "count": count,
                    "words": counted(kind, count),
                    "rows": [row for change in held for row in change.rows],
                }
            )
        return lines

    def banner(self) -> str:
        """The one line a session opens with: ``3 answers and 1 setting since the last scan.``"""
        said = [str(line["words"]) for line in self.summary()]
        joined = said[0] if len(said) == 1 else f"{', '.join(said[:-1])} and {said[-1]}"
        return f"{joined} since the last scan."

    def _of_the_project(self) -> tuple[Change, ...]:
        """The changes a re-run can apply from the project alone: none of their steps reads the
        share."""
        return tuple(
            change
            for change in self.changes
            if not any(step.reads_the_share for step in change.needs)
        )

    def applies(self) -> tuple[Step, ...]:
        """The steps Re-run runs now: those of every change that reads only the project."""
        found = {step for change in self._of_the_project() for step in change.needs}
        return tuple(step for step in _ORDER if step in found)

    def needing_the_share(self) -> tuple[Step, ...]:
        """The steps of the changes a person starts in a terminal, a walk or a dive among them."""
        found = {
            step
            for change in self.changes
            if any(need.reads_the_share for need in change.needs)
            for step in change.needs
        }
        return tuple(step for step in _ORDER if step in found)

    def request(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """What Re-run asks the applier for: the steps, and the words whose names to read again.

        Re-run applies everything that reads only the project, whatever else waits. A change that
        needs a walk or a dive waits for a person's terminal and holds back no other change.
        ``MUST_FOLLOW`` keeps it waiting for its own run afterwards. Where only words changed,
        Re-run reads only the names holding the changed words: ``rebind`` is asked for only where a
        change needs every name read again, and otherwise the words go alone, which the worker
        reads as a partial rebind (and reads every name where that refuses). The rules are applied
        last, whatever changed.
        """
        applied = self._of_the_project()
        rebinding = [change for change in applied if Step.REBIND in change.needs]
        every_name = any(not change.spellings for change in rebinding)
        words = sorted({word for change in rebinding for word in change.spellings})
        steps = [Step.REBIND.value] if every_name else []
        if any(Step.INFERENCE in change.needs for change in applied):
            steps.append(Step.INFERENCE.value)
        return tuple(steps), tuple(words)

    def as_payload(self) -> dict[str, object]:
        """The page's reading: the count, the summary by kind, the line, and the steps."""
        return {
            "count": self.count,
            "summary": self.summary(),
            "banner": self.banner(),
            "commits": list(self.commits),
            "answers": list(self.answers),
            "steps": [
                {
                    "step": step.value,
                    "words": step.words,
                    "reads_the_share": step.reads_the_share,
                    "command": step.command,
                }
                for step in self.steps
            ],
        }


def instant(text: object) -> dt.datetime | None:
    """An ISO 8601 time as a checkpoint or a run record writes it, in UTC; None if unreadable."""
    try:
        parsed = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def _does(run: RunDone, step: Step, spellings: frozenset[str]) -> bool:
    """Whether a run did ``step`` for a change to ``spellings`` (empty: to every name)."""
    if step in run.steps:
        return True
    return (
        step is Step.REBIND
        and bool(spellings)
        and run.spellings is not None
        and spellings <= run.spellings
    )


def _followed(run: RunDone, when: dt.datetime | None, sequence: int | None) -> bool:
    """Whether a run began after a change: by the Record it read where both say, else by clocks.

    A run's watermark is read before it reads the Record (``jobs/provenance.py``), so a run whose
    watermark reaches a decision's sequence read that decision, even in the same second. A run
    whose watermark falls short began before the decision was written. Where either is unknown (a
    commit is not a Record event, and a run before schema 1.22 states no watermark), the clocks
    decide, and a run in the change's own second has not followed it.
    """
    if sequence is not None and run.watermark is not None:
        return run.watermark >= sequence
    began = instant(run.started_at)
    return when is not None and began is not None and began > when


def still_needed(
    needs: Iterable[object],
    made: object,
    runs: Sequence[RunDone],
    *,
    spellings: frozenset[str] = frozenset(),
    sequence: int | None = None,
) -> tuple[Step, ...]:
    """The steps a change made at ``made`` still waits for, given the runs since it, oldest first.

    ``runs`` is the current run's line of descent, oldest first. A step is done by the first run
    that began after the change, did the step, and came after the runs that did every step it
    must follow (:data:`MUST_FOLLOW`); a step that must follow one not done is not done either.
    A change to what some words mean (``spellings``) has its rebind done by a partial rebind that
    read those words, too. A person's decision (``sequence``) is followed by a run that read the
    Record to it (:func:`_followed`). A change whose time cannot be read, and that no run's
    watermark places, waits for everything it needs.
    """
    named = {Step(str(need)) for need in needs}
    wanted = [step for step in _ORDER if step in named]
    when = instant(made)
    if when is None and sequence is None:
        return tuple(wanted)
    since = [run for run in runs if _followed(run, when, sequence)]
    done_at: dict[Step, int] = {}
    # In the order the steps happen, so each step a step must follow is settled before it.
    for step in wanted:
        earlier = [done_at.get(first) for first in MUST_FOLLOW[step] if first in named]
        if None in earlier:
            continue
        start = max((index + 1 for index in earlier if index is not None), default=0)
        found = next(
            (index for index in range(start, len(since)) if _does(since[index], step, spellings)),
            None,
        )
        if found is not None:
            done_at[step] = found
    return tuple(step for step in wanted if step not in done_at)


def pending(changes: Sequence[Change], runs: Sequence[RunDone]) -> Pending | None:
    """What waits for a re-run: each change with the steps no run since it has done, or None.

    ``runs`` is the current run's line of descent, oldest first (:func:`still_needed`).
    """
    waiting: list[Change] = []
    for change in changes:
        left = still_needed(
            change.needs,
            change.made,
            runs,
            spellings=change.spellings,
            sequence=change.sequence,
        )
        if left:
            waiting.append(replace(change, needs=left))
    if not waiting:
        return None
    found = {step for change in waiting for step in change.needs}
    return Pending(changes=tuple(waiting), steps=tuple(step for step in _ORDER if step in found))
