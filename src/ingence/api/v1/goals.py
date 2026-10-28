"""``GET /api/v1/goals``: the goals a person wrote, and what the run says of each.

A goal is a sentence a person wrote (``POST /api/goals``) about what they are looking for. The run
answers it as Ask answers a question. The answer holds how its words were read and widened, the
experiments matching it by tier with why, and a summary: how many at each tier, their first and
last date, how many raw files found are followed by analysis of how many, how many repeated and
how many done once. The route answers during a scan, like ``areas``, with ``phase`` saying where
the job is.

The goals stand in the workspace (:mod:`ingence.goals`). Each is read as Ask reads a question
(:mod:`ingence.api.v1.asking`) against the current run during a scan, else the pinned one.
``phase`` is the running job's phase from the ledger, in the contract's words (listing, reading,
names, rules, views), and null where no job runs. The matches fill in per phase. While the run is
being listed or read there is no run to answer from, and each goal answers its words alone (its
Dictionary expansion). Once the names are read, the matches by name stand. The full card (dates,
repeats, analysis) appears once the rules have run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import V1Context, route
from ingence.api.v1.ask import Expansion, WordsRead
from ingence.api.v1.models import (
    ApiModel,
    Count,
    Identifier,
    JobPhaseName,
    MatchTierName,
    register,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.api.v1.asking import RunExperiments
    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.store import DictionaryStore
    from ingence.goals import Goal
    from ingence.search.ask import AskReading, ExperimentMatch
    from ingence.storage.catalog import Catalog

__all__ = ["PHASE_WORDS", "GoalItem", "GoalMatch", "GoalSummary", "GoalsResponse", "job_phase"]


class GoalMatch(ApiModel):
    """One experiment matching a goal."""

    experiment_id: Identifier = Field(description="The experiment folder's entry.")
    label: str = Field(description="The name a page shows for it.")
    tier: MatchTierName = Field(description="How well it matches.")
    why: str = Field(description="Why it matches, in words.")


class GoalSummary(ApiModel):
    """What the run says of a goal, in counts."""

    experiments: Count = Field(description="Experiments matching, at any tier.")
    named: Count = Field(description="Matching by name.")
    associated: Count = Field(description="Associated with the goal's words.")
    same_theme: Count = Field(description="In the same theme as a match.")
    first: str | None = Field(description="The earliest match's date, YYYY-MM-DD; null where none.")
    last: str | None = Field(description="The latest match's date, YYYY-MM-DD; null where none.")
    raw_with_analysis: Count = Field(
        description="Matches whose raw files are followed by analysis."
    )
    of: Count = Field(description="Matches with raw files: what raw_with_analysis is out of.")
    repeated_groups: Count = Field(description="Groups of repeats among the matches.")
    seen_once: Count = Field(description="Matches done once.")


class GoalItem(ApiModel):
    """One goal and what the run says of it."""

    id: Identifier = Field(description="The goal's identifier.")
    text: str = Field(description="The goal as the person wrote it.")
    read: WordsRead = Field(description="How its words were read.")
    expanded: list[Expansion] = Field(description="The words widened, and how.")
    matches: list[GoalMatch] = Field(description="The experiments matching, best first.")
    summary: GoalSummary = Field(description="The matches in counts.")


@register("goals")
class GoalsResponse(ApiModel):
    """``GET /api/v1/goals``: every goal, each answered from the run."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier | None = Field(
        description="The run answered: the current one during a scan; null before any run, "
        "when each goal answers its words alone."
    )
    phase: JobPhaseName | None = Field(description="Where a running job is; null where none runs.")
    goals: list[GoalItem] = Field(description="The goals, in the order they were written.")


#: The ledger's phase words as the contract says them.
PHASE_WORDS: dict[str, JobPhaseName] = {
    "census": "listing",
    "dive": "reading",
    "binding": "names",
    "inferring": "rules",
    "projections": "views",
}


def job_phase(context: V1Context) -> JobPhaseName | None:
    """The running job's phase, in the contract's words; None where no job runs or the ledger
    names a phase the contract does not."""
    if not context.job_running:
        return None
    from ingence.jobs import JobState
    from ingence.jobs.ledger import JobLedger

    ledger = JobLedger(context.project)
    if not ledger.exists():
        return None
    for row in ledger.in_state(JobState.RUNNING):
        return PHASE_WORDS.get(row.phase or "")
    return None


def _why(match: ExperimentMatch) -> str:
    if match.tier == "same_theme":
        return f"in the same line of work, {match.via_theme}, as named matches"
    if match.tier == "associated":
        words = ", ".join(item.word for item in match.matched)
        return f"holds {words}, which come with the goal's words across the project"
    said = "; ".join(f"{item.word} ({item.how})" for item in match.matched)
    if match.of_words and len(match.matched) < match.of_words:
        return f"matches {len(match.matched)} of {match.of_words} words: {said}"
    return f"its names hold {said}"


#: Kinds of word a repeat's name differs by: a date, a counter, a replicate label, a version.
_REPEAT_NOISE_KINDS = frozenset({"date", "replicate", "count", "version", "identifier", "position"})


def _repeats(
    folders: Sequence[str], normaliser: Normaliser, store: DictionaryStore
) -> tuple[int, int]:
    """Groups of repeats among the matches, and matches done once, read from their folders'
    names: two experiments whose folder names hold the same words, dates, counters and replicate
    labels apart, are read as repeats of one another. A name-family reading, the projection's
    own; the run's repeat claims replace it where a repeats reading stands."""
    from ingence.dictionary.normalize import AtomKind

    families: dict[frozenset[str], int] = {}
    for folder in folders:
        name = folder.rsplit("/", 1)[-1]
        words: set[str] = set()
        for atom in normaliser.atoms(name):
            if atom.kind is not AtomKind.WORD:
                continue
            hit = store.look_up(atom.text)
            if hit is not None and hit.sense.theme_kind in _REPEAT_NOISE_KINDS:
                continue
            if (
                hit is not None
                and hit.sense.theme_kind == "process"
                and hit.sense.term in ("repeat", "replicate")
            ):
                continue
            words.add(atom.normalised)
        key = frozenset(words)
        families[key] = families.get(key, 0) + 1
    groups = sum(1 for count in families.values() if count >= 2)
    once = sum(1 for count in families.values() if count == 1)
    return groups, once


def _summary(
    reading: AskReading, known: RunExperiments, catalog: Catalog, project: Path
) -> GoalSummary:
    """The matches in counts: how many at each tier, their first and last date, how many raw
    files are followed by analysis of how many, repeats and experiments done once."""
    from ingence.api.routes import search as search_routes

    matched = [*reading.named, *reading.associated]
    ids = [item.experiment.experiment_id for item in matched]
    held = [known.by_id[eid] for eid in ids if eid in known.by_id]
    firsts = sorted(item.first[:10] for item in held if item.first)
    lasts = sorted((item.last or item.first or "")[:10] for item in held if item.last or item.first)
    with_raw = [item for item in held if item.raw_acquisitions > 0]
    normaliser, store = search_routes._reader(catalog, project)
    groups, once = _repeats([item.folder for item in held], normaliser, store)
    return GoalSummary(
        experiments=len(matched) + len(reading.same_theme),
        named=len(reading.named),
        associated=len(reading.associated),
        same_theme=len(reading.same_theme),
        first=firsts[0] if firsts else None,
        last=lasts[-1] if lasts else None,
        raw_with_analysis=sum(1 for item in with_raw if item.followed > 0 or item.has_analysis),
        of=len(with_raw),
        repeated_groups=groups,
        seen_once=once,
    )


def _words_alone(goal: Goal) -> GoalItem:
    """A goal before a run can be read: its words, widened by the Dictionary, no matches."""
    from ingence.api.v1.asking import words_only

    read, expanded = words_only(goal.text)
    return GoalItem(
        id=goal.id,
        text=goal.text,
        read=read,
        expanded=expanded,
        matches=[],
        summary=GoalSummary(
            experiments=0,
            named=0,
            associated=0,
            same_theme=0,
            first=None,
            last=None,
            raw_with_analysis=0,
            of=0,
            repeated_groups=0,
            seen_once=0,
        ),
    )


@route("goals", model=GoalsResponse)
def goals(context: V1Context, query: Mapping[str, Sequence[str]]) -> GoalsResponse:
    """The goals, each answered from the current run during a scan, else the pinned one."""
    del query
    from ingence.goals import read_goals
    from ingence.storage.catalog import open_catalog

    # Before any run the goals still answer: each gives its words alone, with the running job's
    # phase and never a conflict. So goals typed on the landing page show while the first run is
    # listed and read.
    run_id = context.run_current or context.run_pinned
    standing = read_goals(context.project)
    if run_id is None:
        return GoalsResponse(
            schema_version=SCHEMA_VERSION,
            run_id=None,
            phase=job_phase(context),
            goals=[_words_alone(goal) for goal in standing],
        )
    # The run to answer from is the pinned run, else the current one while the first run is
    # being made. Where that run cannot be opened yet (the project is still being listed or
    # read), each goal answers its words alone, and the matches fill in once a run stands.
    readable = context.run_pinned or run_id
    try:
        with open_catalog(context.project, run_id=readable) as catalog:
            items = _answered(context, catalog, readable, standing)
    except FileNotFoundError:  # no run to read yet: the goals answer their words alone
        items = [_words_alone(goal) for goal in standing]
    return GoalsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        phase=job_phase(context),
        goals=items,
    )


def _answered(
    context: V1Context, catalog: Catalog, run_id: str, standing: Sequence[Goal]
) -> list[GoalItem]:
    """Every goal answered from one run."""
    from ingence.api.v1.asking import (
        ask_in_run,
        expansions_of,
        run_experiments,
        words_read,
    )

    items: list[GoalItem] = []
    known = run_experiments(context, catalog, run_id) if catalog.has_run else None
    for goal in standing:
        reading = ask_in_run(
            context, catalog, goal.text, known.asked if known else (), at_least=goal.at_least
        )
        items.append(
            GoalItem(
                id=goal.id,
                text=goal.text,
                read=words_read(reading),
                expanded=expansions_of(reading),
                matches=[
                    GoalMatch(
                        experiment_id=match.experiment.experiment_id,
                        label=match.experiment.label,
                        tier=match.tier,
                        why=_why(match),
                    )
                    for match in (*reading.named, *reading.associated, *reading.same_theme)
                ],
                summary=(
                    _summary(reading, known, catalog, context.project)
                    if known is not None
                    else GoalSummary(
                        experiments=0,
                        named=0,
                        associated=0,
                        same_theme=0,
                        first=None,
                        last=None,
                        raw_with_analysis=0,
                        of=0,
                        repeated_groups=0,
                        seen_once=0,
                    )
                ),
            )
        )
    return items
