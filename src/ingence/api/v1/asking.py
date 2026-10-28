"""What ``ask`` and ``goals`` share: the run's experiments as a question is answered with, and
one question read against them.

The reading is the search package's (:mod:`ingence.search.ask`). This module only gathers what a
route knows of the experiments (their labels, themes, dates and how complete each is) from the
same readings the Experiments page uses. It also reads the settings in force: the search box's
settings, and the themes' kinds that say how a question is read rather than what it asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.api.v1.ask import AskDates, Expansion, Followed, MatchedWord, WordsRead
from ingence.search.ask import ExperimentAsked, ask

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.api.v1 import V1Context
    from ingence.search.ask import AskReading, ExperimentMatch
    from ingence.storage.catalog import Catalog

__all__ = [
    "ExperimentKnown",
    "RunExperiments",
    "ask_in_run",
    "dates_of",
    "expansions_of",
    "followed_of",
    "matched_of",
    "run_experiments",
    "words_only",
    "words_read",
]


@dataclass(frozen=True, slots=True)
class ExperimentKnown:
    """One experiment as the routes know it before a question is asked."""

    asked: ExperimentAsked
    folder: str
    first: str | None
    last: str | None
    raw_acquisitions: int
    followed: int
    #: Whether the run read its raw files as followed by analysis of its own (the Experiments
    #: page's reading), for a goal's summary.
    has_analysis: bool

    @property
    def completeness(self) -> float | None:
        if self.raw_acquisitions <= 0:
            return None
        return round(self.followed / self.raw_acquisitions, 3)


@dataclass(frozen=True, slots=True)
class RunExperiments:
    """The pinned run's experiments, by identifier, with the readers a question needs."""

    run_id: str
    by_id: dict[str, ExperimentKnown]

    @property
    def asked(self) -> list[ExperimentAsked]:
        return [known.asked for known in self.by_id.values()]


def run_experiments(context: V1Context, catalog: Catalog, run_id: str) -> RunExperiments:
    """The run's experiments with their labels, themes, dates and raw-and-followed counts."""
    from ingence.api.v1.experiments import _names
    from ingence.completeness import completeness_of_run
    from ingence.experiment_units import experiment_times, run_units

    found = completeness_of_run(context.project, run_id)
    names = _names(catalog, found.experiments)
    times = experiment_times(catalog, run_units(catalog))
    by_id: dict[str, ExperimentKnown] = {}
    for row in found.experiments:
        shown = str(names.get(row.experiment_id, {}).get("shown_name") or row.folder)
        first, last = times.get(row.experiment_id, (row.date, None))
        by_id[row.experiment_id] = ExperimentKnown(
            asked=ExperimentAsked(
                experiment_id=row.experiment_id,
                label=shown,
                theme=row.theme,
                theme_label=row.theme_label,
                folder=row.folder,
                first=_day(first),
            ),
            folder=row.folder,
            first=first,
            last=last,
            raw_acquisitions=int(row.raw_acquisitions),
            followed=int(row.followed),
            has_analysis=bool(row.has_analysis),
        )
    return RunExperiments(run_id=run_id, by_id=by_id)


def ask_in_run(
    context: V1Context,
    catalog: Catalog,
    text: str,
    experiments: Sequence[ExperimentAsked],
    *,
    at_least: int | None = None,
) -> AskReading:
    """One question read against the run, under the settings in force."""
    from ingence.api.routes import search as search_routes
    from ingence.core.project import Project
    from ingence.search.ask import DEFAULT_DROPPED_KINDS
    from ingence.search.query import DROPPED_KINDS

    settings = Project.open(context.project).settings
    normaliser, store = search_routes._reader(catalog, context.project)
    # Only one search projection is built at a time. A question may build the run's search
    # projection, as a search and the readouts' search view do. Two builds share one staging
    # folder, so one build could read a staging file that the other has just removed. The
    # question waits for the same lock that the builds hold.
    with search_routes._one_at_a_time(context.project):
        return ask(
            catalog,
            context.project,
            text,
            normaliser=normaliser,
            store=store,
            experiments=experiments,
            dictionary_hash=store.fingerprint(),
            inside_min_length=settings.search.inside_min_length,
            associated_tier=settings.search.associated_tier,
            dropped_kinds=(
                frozenset(settings.themes.how_kinds) | DROPPED_KINDS | DEFAULT_DROPPED_KINDS
            ),
            at_least=at_least,
        )


def words_read(reading: AskReading) -> WordsRead:
    return WordsRead(
        kept=list(reading.kept), dropped=list(reading.dropped), unknown=list(reading.unknown)
    )


def expansions_of(reading: AskReading) -> list[Expansion]:
    return [Expansion(word=item.word, to=list(item.to), how=item.how) for item in reading.expanded]


def matched_of(match: ExperimentMatch) -> list[MatchedWord]:
    return [
        MatchedWord.model_validate({"word": item.word, "how": item.how, "class": item.kind})
        for item in match.matched
    ]


def followed_of(known: ExperimentKnown | None) -> Followed:
    if known is None:
        return Followed(n=0, of=0)
    return Followed(n=known.followed, of=known.raw_acquisitions)


def _day(value: str | None) -> str | None:
    """A date as YYYY-MM-DD: the first ten characters of a day or a moment."""
    return None if not value else str(value)[:10]


def dates_of(known: ExperimentKnown | None) -> AskDates:
    if known is None:
        return AskDates(first=None, last=None)
    return AskDates(first=_day(known.first), last=_day(known.last))


class _NoPool:
    """A vocabulary that spells nothing.

    Before a run can be read, only the Dictionary's own widening (senses, class words) is known.
    """

    def entries_with(self, token: str) -> int:
        return 0

    def near(self, token: str, *, distance: int) -> tuple[str, ...]:
        return ()

    def containing(self, piece: str) -> tuple[str, ...]:
        return ()

    def holding(self, text: str) -> bool:
        return False

    def beginning(self, text: str) -> bool:
        return False


def words_only(text: str) -> tuple[WordsRead, list[Expansion]]:
    """A goal's words read with the shipped Dictionary alone.

    This is what a goal answers while the run is being listed or read and there is no run to match
    against yet. Every word that is not a question word is kept. No word is unknown, because nothing
    has been looked at.
    """
    from ingence.dictionary.normalize import Normaliser
    from ingence.dictionary.store import DictionaryStore
    from ingence.search.ask import DEFAULT_DROPPED_KINDS, unpunctuated
    from ingence.search.expand import expand
    from ingence.search.query import parse_query

    store = DictionaryStore.builtin()
    parsed = parse_query(
        unpunctuated(text),
        normaliser=Normaliser(store),
        store=store,
        question=True,
        dropped_kinds=DEFAULT_DROPPED_KINDS,
    )
    concepts = expand(parsed, store=store, vocabulary=_NoPool())
    read = WordsRead(
        kept=[concept.typed for concept in concepts if not concept.excluded],
        dropped=list(parsed.dropped),
        unknown=[],
    )
    return read, [
        Expansion(word=item.word, to=list(item.to), how=item.how)
        for concept in concepts
        for item in concept.expansions
    ]
