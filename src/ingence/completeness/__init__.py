"""Completeness per experiment, and "no analysis found".

The counts say what the engine found and never what exists. A lack of raw data or analysis files
does not mean they do not exist. It means the engine did not find them. Unknown is not absent. So
every count here is *found in what was read*. The share of raw acquisitions followed by an analysis
file is null, never 0, where no raw acquisition was found. The pages never say "never analysed".
They say "no analysis found".

Completeness is a deterministic projection after inference. It is computed on request from one
published run's conclusions (:mod:`~ingence.completeness.counting`, whose classes ``classes.yaml``
declares), the project's completeness settings (``core/settings.CompletenessSettings``) and, for
the reasons, the themes (:func:`engine_themes`). No model runs, nothing is asked, and nothing is
written. Deleting nothing loses nothing, and the same run, settings and themes give the same rows.

:mod:`ingence.experiment_units` defines an experiment and a person, the one definition that themes
share (a person by ``themes.person_level``). The per-theme counts need the themes' assignments,
reached through a small protocol (:data:`ThemeProvider`). Where no provider answers, or the themes
cannot be computed, the themes are said not computed, with the reason, and never guessed.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from ingence.completeness.elsewhere import ElsewhereSaid
    from ingence.core.settings import CompletenessSettings

    #: Themes, as a small protocol: given a project and a run, each experiment's identifier (its
    #: folder's entry) to its theme's, or None where no themes are computed.
    type ThemeProvider = Callable[[Path, str], Mapping[str, str] | None]

__all__ = [
    "FORGOTTEN",
    "STOPPED",
    "Completeness",
    "ExperimentFound",
    "GroupCompleteness",
    "NoAnalysisFound",
    "NoAnalysisList",
    "Outside",
    "aggregate",
    "completeness_of_run",
    "engine_themes",
    "no_analysis_found",
]


def _share(followed: int, raw: int) -> float | None:
    """Followed over found, or None where nothing was found (never 0: unknown is not absent)."""
    return followed / raw if raw else None


@dataclass(frozen=True, slots=True)
class ExperimentFound:
    """One experiment as the engine found it: what its folder holds, counted from the run's claims.

    An experiment is a folder an experiment rule classed (the highest such above a file).
    """

    #: The experiment folder's entry.
    experiment_id: str
    root_id: str
    #: Its folder, relative to its root.
    folder: str
    #: Files found in it.
    files: int
    #: Raw acquisition files found in it.
    raw_acquisitions: int
    #: Of those, the ones found followed by an analysis file (a derivation the engine proposed or
    #: published, or a file named for it, wherever that file lies).
    followed: int
    #: Files found derived from its raw acquisitions, wherever they lie.
    derived_files: int
    #: Files found in it read at an analysis stage.
    analysis_files: int
    #: Results tables found in it.
    result_tables: int
    #: Its theme, where a theme provider gave one.
    theme: str | None = None
    #: The theme's words (its label, as the themes view says them), where the default theme
    #: provider answered; None where a provider gave none, or the experiment is in no theme.
    theme_label: str | None = None
    #: Whose it is (``ingence.experiment_units.person_of`` under ``themes.person_level``); set by
    #: :func:`completeness_of_run`.
    person: str | None = None
    #: Its date, as the themes projection dates it (the earliest date a name gives, else the
    #: earliest believed time), read without the themes; None where nothing dates it.
    date: str | None = None
    #: Whether its own analysis files or results tables are an analysis of its own, under the
    #: settings in force (:meth:`has_analysis_under`); set by :func:`completeness_of_run`.
    has_analysis: bool = False
    #: What a person said of its analysis being elsewhere, as the run read it and as the Record
    #: stands now (``completeness/elsewhere.py``); None where nobody said so.
    elsewhere: ElsewhereSaid | None = None

    @property
    def share(self) -> float | None:
        """The share of its raw acquisitions found followed by an analysis file; None where it
        holds none found."""
        return _share(self.followed, self.raw_acquisitions)

    def has_analysis_under(self, settings: CompletenessSettings) -> bool:
        """Whether the analysis files or results tables found in it count as an analysis of its
        own."""
        return (
            self.analysis_files >= settings.own_analysis_files
            or self.result_tables >= settings.own_result_tables
        )


@dataclass(frozen=True, slots=True)
class Outside:
    """Files found in no experiment the engine found, and the raw data among them."""

    files: int
    raw_acquisitions: int
    followed: int

    @property
    def share(self) -> float | None:
        return _share(self.followed, self.raw_acquisitions)


@dataclass(frozen=True, slots=True)
class GroupCompleteness:
    """Experiments of one person or one theme, summed: the share is over their raw acquisitions
    found, pooled: the sum of each share times its acquisitions over the sum of acquisitions."""

    key: str
    experiments: int
    #: Experiments with at least one raw acquisition found.
    experiments_with_raw: int
    raw_acquisitions: int
    followed: int
    analysis_files: int
    result_tables: int
    #: Experiments listed as having no analysis found (set where the list is made).
    no_analysis_found: int = 0
    #: Experiments a person said are analysed elsewhere, as the run read it: off the list.
    analysed_elsewhere: int = 0

    @property
    def share(self) -> float | None:
        return _share(self.followed, self.raw_acquisitions)


def aggregate(
    experiments: Iterable[ExperimentFound],
    key_of: Callable[[ExperimentFound], str | None],
    *,
    listed: frozenset[str] = frozenset(),
    answered: frozenset[str] = frozenset(),
) -> list[GroupCompleteness]:
    """The experiments summed by ``key_of`` (an experiment with no key is in no group), in key
    order; ``listed`` names the experiments listed as having no analysis found, ``answered``
    those a person said are analysed elsewhere."""
    sums: dict[str, dict[str, int]] = {}
    for row in experiments:
        key = key_of(row)
        if key is None:
            continue
        held = sums.setdefault(
            key,
            {
                "experiments": 0,
                "experiments_with_raw": 0,
                "raw_acquisitions": 0,
                "followed": 0,
                "analysis_files": 0,
                "result_tables": 0,
                "no_analysis_found": 0,
                "analysed_elsewhere": 0,
            },
        )
        held["experiments"] += 1
        held["experiments_with_raw"] += 1 if row.raw_acquisitions else 0
        held["raw_acquisitions"] += row.raw_acquisitions
        held["followed"] += row.followed
        held["analysis_files"] += row.analysis_files
        held["result_tables"] += row.result_tables
        held["no_analysis_found"] += 1 if row.experiment_id in listed else 0
        held["analysed_elsewhere"] += 1 if row.experiment_id in answered else 0
    return [GroupCompleteness(key=key, **counts) for key, counts in sorted(sums.items())]


@dataclass(frozen=True, slots=True)
class Completeness:
    """One run's completeness: per experiment, per person, per theme, and outside every
    experiment."""

    run_id: str
    settings: CompletenessSettings
    experiments: list[ExperimentFound]
    outside: Outside
    people: list[GroupCompleteness]
    themes: list[GroupCompleteness]
    #: Whether a theme provider answered; where none did, ``themes`` is empty, and the per-theme
    #: reasons are not given ("themes not computed"), never guessed.
    themes_computed: bool
    #: Why the themes are not computed, in words; empty where they are.
    themes_said: str
    #: The experiments with no analysis found, and why (:func:`no_analysis_found`).
    no_analysis: NoAnalysisList


# ---------------------------------------------------------------------------
# "No analysis found", with its two reasons.
# ---------------------------------------------------------------------------

#: The two reasons, as stored: the rest of its theme is mostly followed (one experiment
#: forgotten), or mostly not (the line of work stopped). No reason is None.
type Reason = Literal["forgotten", "stopped"]

FORGOTTEN: Reason = "forgotten"
STOPPED: Reason = "stopped"


def _percent(share: float) -> str:
    return f"{round(share * 100)}%"


@dataclass(frozen=True, slots=True)
class NoAnalysisFound:
    """One experiment with raw acquisitions found and no analysis found, and why where a reason
    holds. It says what was found in what was read, and never says "never analysed"."""

    experiment: ExperimentFound
    #: ``forgotten``, ``stopped``, or None where neither holds.
    reason: Reason | None
    #: The share of the rest of its theme's raw acquisitions found followed by analysis: the
    #: other experiments of its theme judged (enough raw acquisitions found), pooled; None where
    #: it has no theme or its theme has no other experiment judged.
    theme_rest: float | None
    #: Words for the reason, or for why none is given.
    reason_said: str

    @property
    def said(self) -> str:
        row = self.experiment
        return (
            f"No analysis found in what was read: {row.followed} of {row.raw_acquisitions} raw "
            f"files found {'is' if row.followed == 1 else 'are'} analyzed, and too few analysis "
            "files or results tables were found in its folder to count as an analysis of its own."
        )


@dataclass(frozen=True, slots=True)
class NoAnalysisList:
    """The experiments listed as having no analysis found, and how many were judged."""

    listed: list[NoAnalysisFound]
    #: Experiments with enough raw acquisitions found to be judged: the list's denominator.
    judged: int
    themes_computed: bool
    #: Experiments that would be listed but a person said are analysed elsewhere, as the run read
    #: the Record: off the list, shown as answered.
    answered: list[NoAnalysisFound] = field(default_factory=list)


def _theme_rest(row: ExperimentFound, judged: list[ExperimentFound]) -> float | None:
    """The rest of the experiment's theme, pooled: followed over found, itself left out."""
    if row.theme is None:
        return None
    others = [
        other
        for other in judged
        if other.theme == row.theme and other.experiment_id != row.experiment_id
    ]
    found = sum(other.raw_acquisitions for other in others)
    return sum(other.followed for other in others) / found if found else None


def _reason(
    rest: float | None, settings: CompletenessSettings, *, themed: bool, has_theme: bool
) -> tuple[Reason | None, str]:
    if not themed:
        return (
            None,
            "No reason given: themes not computed, so its theme's other experiments are unknown.",
        )
    if not has_theme:
        return None, "No reason given: it has no theme."
    if rest is None:
        return None, (
            "No reason given: no other experiment of its theme has enough raw files found "
            "to compare."
        )
    if rest >= settings.forgotten_at_least:
        return FORGOTTEN, (
            f"Perhaps forgotten: the rest of its theme's raw data found is mostly analyzed "
            f"({_percent(rest)})."
        )
    if rest < settings.stopped_below:
        return STOPPED, (
            f"A line that stopped: the rest of its theme's raw data found is mostly not analyzed "
            f"either ({_percent(rest)})."
        )
    return None, (
        f"No reason given: the rest of its theme's raw data found is partly analyzed "
        f"({_percent(rest)})."
    )


def no_analysis_found(
    experiments: Iterable[ExperimentFound],
    settings: CompletenessSettings,
    *,
    themes_computed: bool,
    answered: frozenset[str] = frozenset(),
) -> NoAnalysisList:
    """The experiments with raw acquisitions found and no analysis found, each with its reason.

    Listed: at least ``judged_from_acquisitions`` raw acquisitions found, under
    ``listed_below_share`` of them followed by an analysis file found, and no analysis of its own
    (``has_analysis_under``). The reasons read the rest of its theme (its other experiments judged,
    pooled): forgotten at or above ``forgotten_at_least``, a line that stopped under
    ``stopped_below``, none between, and none where themes are not computed (never guessed). Ordered
    by share, the most raw data first, then by folder. An experiment in ``answered`` (a person said
    its analysis is elsewhere, and the run read it) is moved to the answered list.
    """
    rows = list(experiments)
    judged = [row for row in rows if row.raw_acquisitions >= settings.judged_from_acquisitions]
    listed: list[NoAnalysisFound] = []
    shown_answered: list[NoAnalysisFound] = []
    for row in judged:
        share = row.share
        if share is None or share >= settings.listed_below_share:
            continue
        if row.has_analysis_under(settings):
            continue
        rest = _theme_rest(row, judged) if themes_computed else None
        reason, said = _reason(
            rest, settings, themed=themes_computed, has_theme=row.theme is not None
        )
        item = NoAnalysisFound(experiment=row, reason=reason, theme_rest=rest, reason_said=said)
        (shown_answered if row.experiment_id in answered else listed).append(item)

    def order(item: NoAnalysisFound) -> tuple[float, int, str, str]:
        found = item.experiment
        return (found.share or 0.0, -found.raw_acquisitions, found.root_id, found.folder)

    return NoAnalysisList(
        listed=sorted(listed, key=order),
        judged=len(judged),
        themes_computed=themes_computed,
        answered=sorted(shown_answered, key=order),
    )


#: Where the themes are looked for, and the function asked.
THEMES_MODULE = "ingence.themes"
THEMES_FUNCTION = "theme_assignments"


def _engine_assignments(project: Path, run_id: str) -> Mapping[str, Any] | None:
    """The themes package's assignments for the run, or None where this engine holds none."""
    try:
        module = importlib.import_module(THEMES_MODULE)
    except ImportError:
        return None
    function = getattr(module, THEMES_FUNCTION, None)
    if function is None:
        return None
    answered = function(project, run_id)
    return None if answered is None else dict(answered)


def _field_of(assignment: Any, name: str) -> Any:
    return assignment.get(name) if isinstance(assignment, dict) else getattr(assignment, name, None)


def engine_themes(project: Path, run_id: str) -> dict[str, str] | None:
    """Each experiment's theme from ``theme_assignments(project, run_id)``, imported lazily; None
    where this engine holds no such function (the themes are then not computed).

    An assignment carries ``theme_id`` (an attribute, or a key of a mapping); an experiment it
    names none for is in no theme.
    """
    answered = _engine_assignments(project, run_id)
    if answered is None:
        return None
    themes: dict[str, str] = {}
    for experiment, assignment in answered.items():
        theme = _field_of(assignment, "theme_id")
        if theme is not None:
            themes[str(experiment)] = str(theme)
    return themes


def engine_theme_labels(project: Path, run_id: str) -> dict[str, str]:
    """Each experiment's theme's words from the same assignments, for the experiments in a theme.

    Empty where the themes are not computed. A number alone says nothing to a person, so the
    words are served beside it.
    """
    try:
        answered = _engine_assignments(project, run_id)
    except Exception:  # said as "themes not computed" by the provider read, never here
        return {}
    labels: dict[str, str] = {}
    for experiment, assignment in (answered or {}).items():
        label = _field_of(assignment, "label")
        if label and _field_of(assignment, "theme_id") is not None:
            labels[str(experiment)] = str(label)
    return labels


def confirmed_derivations(project: Path, watermark: int | None) -> list[tuple[str, str]]:
    """Every derivation a person confirmed, as the run read the Record: (acquisition, follower).

    ``derived`` confirms the pair as proposed, ``reversed`` the other way round; a run recording
    no watermark read no answer. Read with the answers inference reads (``record/answers.py``).
    """
    from ingence.record.answers import answers_read
    from ingence.record.log import read_at, read_events

    if watermark is None:
        return []
    with read_at(project, watermark):
        events = read_events(project)
    found: list[tuple[str, str]] = []
    for answers in answers_read(events, watermark=watermark).pairs.values():
        for answer in answers:
            if not answer.source or not answer.target:
                continue
            if answer.word == "derived":
                found.append((answer.source, answer.target))
            elif answer.word == "reversed":
                found.append((answer.target, answer.source))
    return sorted(set(found))


#: Why themes are not computed where this engine holds no theme assignments at all.
NO_THEMES_HERE = "this engine holds no theme assignments"


def _themes_read(
    provider: ThemeProvider, project: Path, run_id: str
) -> tuple[Mapping[str, str] | None, str]:
    """The provider's themes and, where there are none, why.

    Any failure of the themes says "themes not computed" with its reason, and never fails a page.
    ``theme_assignments`` raises ``ComponentNotAvailableError`` where its engine cannot run."""
    from ingence.core.registry import ComponentNotAvailableError

    try:
        themed = provider(project, run_id)
    except ComponentNotAvailableError as error:
        from ingence.theme_words import unavailable_in_words

        return None, unavailable_in_words(error)
    except Exception as error:
        return None, f"the themes could not be computed: {error}"
    return themed, "" if themed is not None else NO_THEMES_HERE


def completeness_of_run(
    project: Path,
    run_id: str,
    *,
    themes: ThemeProvider | None = None,
    settings: CompletenessSettings | None = None,
) -> Completeness:
    """Count one published run's experiments and sum them per person and per theme.

    ``themes`` is the theme provider (default :func:`engine_themes`); ``settings`` the
    completeness settings (default the project's, the Dashboard's laid over ``settings.yaml``).
    """
    from ingence.completeness.counting import count_run
    from ingence.completeness.elsewhere import elsewhere_answers, run_watermark
    from ingence.core.project import Project
    from ingence.experiment_units import experiment_dates, person_of, run_units
    from ingence.storage.catalog import open_catalog

    held = Project.open(project).settings
    in_force = settings if settings is not None else held.completeness
    provider = themes if themes is not None else engine_themes
    with open_catalog(project, run_id=run_id) as catalog:
        watermark = run_watermark(catalog)
        units = run_units(catalog)
        counted = count_run(catalog, confirmed_derivations(project, watermark), run=units)
        dated = experiment_dates(catalog, units)
    themed, themes_said = _themes_read(provider, project, run_id)
    # The words of each theme come from the default provider's assignments; a provider handed in
    # (a test's) gives numbers alone.
    theme_words = engine_theme_labels(project, run_id) if themes is None and themed else {}
    # What a person said of an experiment's analysis being elsewhere: as the run read the Record
    # (it moves the experiment off the list) and as it stands now (a change waits for re-run).
    said = elsewhere_answers(project, watermark)
    experiments = [
        ExperimentFound(
            experiment_id=item.unit.entry_id,
            root_id=item.unit.root_id,
            folder=item.unit.relative_path,
            files=item.files,
            raw_acquisitions=item.raw_acquisitions,
            followed=item.followed,
            derived_files=item.derived_files,
            analysis_files=item.analysis_files,
            result_tables=item.result_tables,
            theme=(themed or {}).get(item.unit.entry_id),
            theme_label=theme_words.get(item.unit.entry_id),
            person=person_of(item.unit.relative_path, held.themes.person_level),
            date=dated.get(item.unit.entry_id),
            elsewhere=said.get(item.unit.entry_id),
        )
        for item in counted
        if item.unit is not None
    ]
    experiments = [
        replace(row, has_analysis=row.has_analysis_under(in_force)) for row in experiments
    ]
    rest = next(item for item in counted if item.unit is None)
    answered = frozenset(experiment for experiment, held in said.items() if held.read_by_run)
    unanalysed = no_analysis_found(
        experiments, in_force, themes_computed=themed is not None, answered=answered
    )
    listed = frozenset(item.experiment.experiment_id for item in unanalysed.listed)
    return Completeness(
        run_id=run_id,
        settings=in_force,
        experiments=experiments,
        outside=Outside(
            files=rest.files, raw_acquisitions=rest.raw_acquisitions, followed=rest.followed
        ),
        people=aggregate(experiments, lambda row: row.person, listed=listed, answered=answered),
        themes=(
            aggregate(experiments, lambda row: row.theme, listed=listed, answered=answered)
            if themed is not None
            else []
        ),
        themes_computed=themed is not None,
        themes_said=themes_said,
        no_analysis=unanalysed,
    )
