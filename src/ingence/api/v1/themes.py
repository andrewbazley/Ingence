"""``/api/v1/themes...``: what a run's experiments are about, and how that changed.

Eight routes over one projection (:mod:`ingence.themes`), each answering from the run reads are
pinned to (``run=`` names another published run):

- ``themes``: every experiment's theme and broad theme, and every theme and broad theme, with
  the run's summary (how many placed, how many not, seed agreement);
- ``themes/overlap``: the words each pair of lines of work share;
- ``themes/stability``: AMI between seeds (NMI beside it), per theme its stability and core, and
  every resolution tried;
- ``themes/years``: themes over time (first, peak, last, going, direction, by year, specific
  words and their succession), the year profile, and words used in each other's place;
- ``themes/who_else``: per person their experiments and years, the words two or more people
  wrote (by token or by sense), each theme's people and the lines handed on between them; with
  ``word=``, who wrote that word;
- ``themes/undated``: the experiments with no year;
- ``themes/unplaced``: the experiments in no theme, a page at a time, with why;
- ``themes/map``: where each experiment lies in the plane by the words the themes read, with its
  theme and what the engine found of its raw data and analysis.

The routes make no question and no claim. Themes are recomputed from the run and the settings,
and nothing here writes to the run or the Record. The component is optional: where its
dependencies are missing, every route says so in the one error shape.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, AreaKindName, Count, Identifier, register
from ingence.api.v1.names import NamePartItem, name_fields
from ingence.api.v1.shaping import boolean, catalog_of, integer, one, run_named
from ingence.experiment_units import counted_as
from ingence.themes.lines import NamedEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

__all__ = [
    "SplitPartner",
    "ThemesMapResponse",
    "ThemesOverlapResponse",
    "ThemesResponse",
    "ThemesStabilityResponse",
    "ThemesUndatedResponse",
    "ThemesUnplacedResponse",
    "ThemesWhoElseResponse",
    "ThemesYearsResponse",
]

#: Experiments one ``themes`` response carries at most, unless ``limit`` says fewer.
EXPERIMENTS_LIMIT = 20_000
#: Experiments not placed one ``themes/unplaced`` page shows unless told (a short list, never a
#: wall of text).
UNPLACED_PAGE = 25


class CollectionWeightItem(ApiModel):
    collection: str
    experiments: Count
    #: Each of its experiments' weight (mean one over all experiments).
    unit_weight: float


class SeedItem(ApiModel):
    """One seed's final placement (the same steps for every seed)."""

    seed: int
    themes: Count
    placed_experiments: Count
    #: Mean adjusted Rand index of its placement with every other seed's.
    mean_ari: float | None


class SeedPairItem(ApiModel):
    seed_a: int
    seed_b: int
    #: Adjusted Rand index of the two seeds' final placements: 1 the same, near 0 no more alike
    #: than two placements drawn at random.
    ari: float


class ChanceItem(ApiModel):
    """The cutoffs chance gave, and how they were drawn."""

    measured: bool
    null_seed: int
    replicates: int
    edge_neighbors: Count
    placement_neighbors: Count
    shuffled_documents: Count
    quantile_between: float | None
    quantile_within: float | None
    quantile_placement: float | None
    cutoff_between: float | None
    cutoff_within: float | None
    cutoff_placement: float | None


class ThemesSummary(ApiModel):
    experiments: Count
    #: What an experiment is in this run, in sentences a page can show beside the count. The files
    #: route gives its own, since it counts the same folders and places a file under the nearest,
    #: where this counts by the highest. Empty where no run was read.
    experiments_counted_as: str = ""
    placed: Count
    unplaced: Count
    undated: Count
    themes: Count
    broad_themes: Count
    #: AMI between seed 0 and each other seed, averaged; NMI beside it. None where no theme.
    seed_ami: float | None
    seed_nmi: float | None
    resolution: float | None
    broad_resolution: float | None
    vocabulary: Count
    distinct_documents: Count
    #: The folder level below each root read as a person's (a setting), or None.
    person_level: int | None
    collections: list[CollectionWeightItem]
    #: The final placements of the seeds: the mean adjusted Rand index over all seed pairs, the seed
    #: shown (the medoid unless the setting says seed 0), the medoid, and each seed and pair. None
    #: and empty where no theme was drawn.
    seed_ari: float | None = None
    placement_seed: int = 0
    medoid_seed: int | None = None
    final_placement: Literal["medoid", "first"] = "medoid"
    seeds: list[SeedItem] = Field(default_factory=list)
    seed_pairs: list[SeedPairItem] = Field(default_factory=list)
    chance: ChanceItem | None = None
    chance_null: Literal["nearest_ten", "matched"] = "nearest_ten"
    #: Themes and broad themes not connected in their graph; always zero.
    disconnected_themes: Count = 0
    disconnected_broad_themes: Count = 0
    label_method: Literal["tfidf", "log_odds"] = "tfidf"
    label_bootstrap: Count = 0


class ThemeExperiment(ApiModel):
    """One experiment: its folder, its year, its theme. Design names (``DESIGN_FIELDS.md``)."""

    id: Identifier
    folder: str
    #: The folder's own name as written, the name a row shows with its dates left out, its
    #: parts as the tokenizer read them, and whether the run recorded a layout to read them from
    #: (false: no parts, and ``shown_name`` is ``name``). A page never reads a name itself.
    name: str = ""
    shown_name: str = ""
    name_parts: list[NamePartItem] = Field(default_factory=list)
    name_read: bool = False
    root: str
    date: str | None
    last_file_time: str | None
    year: int | None
    files: Count
    bytes: Count
    theme: int | None
    theme_label: str | None
    broad_theme: int | None
    broad_label: str | None
    #: Why it is in no theme, in words; None where it is in one.
    unplaced_reason: str | None
    person: str | None
    #: The area it is in and what the area's folder stands for.
    area: str | None = None
    area_kind: AreaKindName | None = None
    collection: str
    #: Its own distinctive words.
    words: list[str]


class ThemeItem(ApiModel):
    theme: int
    label: str
    words: list[str]
    #: Which of ``words`` are the Dictionary's senses, by canonical name.
    senses: list[str]
    broad_theme: int
    broad: str
    experiments: Count
    stability: float
    core_experiments: Count
    first: int | None
    peak: int | None
    last: int | None
    going: bool
    direction: Literal["rising", "falling", "steady", "new", "stopped", "undated"]
    undated: Count
    by_year: dict[str, Count]
    examples: list[str]
    #: Share of the draws (experiments drawn again with replacement, no refit) in which the label's
    #: words stayed in the label, averaged; and each label word's share. None and empty where the
    #: setting draws none.
    label_stability: float | None = None
    label_support: list[float] = Field(default_factory=list)
    #: The Record target a person's name for this line is written on (``theme:<key>``, the theme's
    #: words sorted), and the name in force where a person gave one: their name, who and when, and
    #: their note. The engine's own words stay in ``label``.
    target: str = ""
    person_label: str | None = None
    person_note: str | None = None
    labelled_by: str | None = None
    labelled_at: str | None = None


class BroadThemeItem(ApiModel):
    broad_theme: int
    label: str
    words: list[str]
    themes: list[int]
    experiments: Count
    label_stability: float | None = None
    label_support: list[float] = Field(default_factory=list)


@register("themes")
class ThemesResponse(ApiModel):
    """``GET /api/v1/themes``: every experiment's theme, every theme, and the run's summary."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    summary: ThemesSummary
    themes: list[ThemeItem]
    broad: list[BroadThemeItem]
    experiments: list[ThemeExperiment]
    experiments_not_shown: Count
    #: One sentence a page can show: how many experiments have no theme.
    unplaced_note: str


class SweepItem(ApiModel):
    resolution: float
    themes: Count
    #: None at a resolution outside the readable window (its other seeds are never drawn).
    seed_ami: float | None
    seed_nmi: float | None


class SplitPartner(ApiModel):
    """Another line of work a line's experiments were split with, and in how many seeds."""

    theme: int
    label: str
    #: In how many of the other seeds (``seeds_compared``) this partner was the one the line's
    #: experiments were split with.
    seeds: Count


class ThemeStabilityItem(ApiModel):
    theme: int
    label: str
    experiments: Count
    #: Mean over the other seeds of the best Jaccard index any of their communities reaches.
    stability: float
    #: Experiments every other seed keeps in this theme's best match.
    core_experiments: Count
    label_stability: float | None = None
    #: Where the other seeds disagree: in how many of the other seeds the best match of this line
    #: overlaps it below ``uncertain_below`` (the seed does not hold the line), and the other lines
    #: those seeds split its experiments with, most seeds first, at most five. A seed that splits
    #: the line among its own experiments names none, so the seeds listed may add up to fewer than
    #: ``split_seeds``. The counts come from the best matches that the stability uses.
    split_seeds: Count = 0
    split_with: list[SplitPartner] = Field(default_factory=list)


@register("themes_stability")
class ThemesStabilityResponse(ApiModel):
    """``GET /api/v1/themes/stability``: how steady the themes are from seed to seed."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    seed_ami: float | None
    seed_nmi: float | None
    resolution: float | None
    seeds: Count
    #: What the two measures are, in words.
    measure: str
    sweep: list[SweepItem]
    themes: list[ThemeStabilityItem]
    #: The final placements: see :class:`ThemesSummary`.
    seed_ari: float | None = None
    placement_seed: int = 0
    medoid_seed: int | None = None
    seed_placements: list[SeedItem] = Field(default_factory=list)
    seed_pairs: list[SeedPairItem] = Field(default_factory=list)
    chance: ChanceItem | None = None
    #: How many other seeds the shown placement was compared with (every seed but the one shown):
    #: the whole of "in k of n seeds".
    seeds_compared: Count = 0
    #: The overlap below which a seed does not hold a line, and a line's boundary is called
    #: uncertain: the tick on the stability axis.
    uncertain_below: float = 0.8


class SharedWordsItem(ApiModel):
    """Two lines of work and the words they have in common."""

    #: The two lines, by theme number, the lower first.
    theme_a: int
    theme_b: int
    #: The words both lines are made of, in the first line's spelling and order.
    words: list[str]
    #: How many words that is, and how many words the line with fewer words has.
    shared: Count
    of_smaller: Count


@register("themes_overlap")
class ThemesOverlapResponse(ApiModel):
    """``GET /api/v1/themes/overlap``: the words each pair of lines of work share."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    #: How many lines of work there are: pairs not listed share no word.
    themes: Count
    #: Only the pairs sharing at least one word, each once.
    pairs: list[SharedWordsItem]


class WordSpan(ApiModel):
    word: str
    experiments: Count
    first: int
    last: int
    median: float


class ThemeOverTime(ApiModel):
    theme: int
    label: str
    broad: str
    experiments: Count
    first: int | None
    peak: int | None
    last: int | None
    going: bool
    direction: Literal["rising", "falling", "steady", "new", "stopped", "undated"]
    by_year: dict[str, Count]
    succession: list[WordSpan]
    specific_words: list[WordSpan]


class ThemeShare(ApiModel):
    label: str
    experiments: Count
    #: Its share that year over its usual share.
    lift: float


class WordCount(ApiModel):
    word: str
    experiments: Count


class YearStory(ApiModel):
    experiments: Count
    most: list[ThemeShare]
    under: list[ThemeShare]
    began: list[str]
    last_seen_year_before: list[str]
    new_words: list[WordCount]


class Substitute(ApiModel):
    earlier: str
    later: str
    similarity: float
    together: Count
    earlier_years: str
    later_years: str
    earlier_n: Count
    later_n: Count


@register("themes_years")
class ThemesYearsResponse(ApiModel):
    """``GET /api/v1/themes/years``: themes over time, the year profile, words handed on."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    themes: list[ThemeOverTime]
    story: dict[str, YearStory]
    substitutes: list[Substitute]


class PersonItem(ApiModel):
    person: str
    experiments: Count
    first: int | None
    last: int | None
    example: str


class SharedWord(ApiModel):
    word: str
    #: Matched by a Dictionary sense; else by its spelling.
    sense: bool
    people: dict[str, Count]
    years: dict[str, str]


class PersonSpan(ApiModel):
    experiments: Count
    first: int | None
    last: int | None
    median: float | None


class HandedOn(ApiModel):
    earlier: str
    later: str
    earlier_years: str
    later_years: str


class ThemePeople(ApiModel):
    theme: int
    label: str
    people: dict[str, PersonSpan]
    shared: bool
    handed_on: list[HandedOn]


class WordMatch(ApiModel):
    person: str
    experiments: Count
    years: str
    themes: list[str]
    examples: list[str]


@register("themes_who_else")
class ThemesWhoElseResponse(ApiModel):
    """``GET /api/v1/themes/who_else``: who else wrote the same words, and lines handed on."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    person_level: int | None
    #: What a person is here, in words.
    person_rule: str
    #: True while the person level is the shipped default, never chosen for this project: a
    #: page flags it ("a person is assumed to be the first folder below each root").
    person_rule_is_default: bool
    people: list[PersonItem]
    shared_words: list[SharedWord]
    themes: list[ThemePeople]
    #: The word asked about (``word=``), and who wrote it; empty without one.
    word: str | None
    matches: list[WordMatch]


class UndatedItem(ApiModel):
    id: Identifier
    folder: str
    files: Count
    theme_label: str | None


@register("themes_undated")
class ThemesUndatedResponse(ApiModel):
    """``GET /api/v1/themes/undated``: experiments with no year (design 12)."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    count: Count
    experiments: list[UndatedItem]


class UnplacedItem(ApiModel):
    id: Identifier
    folder: str
    reason: str


@register("themes_unplaced")
class ThemesUnplacedResponse(ApiModel):
    """``GET /api/v1/themes/unplaced``: experiments in no theme, a short page at a time."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    count: Count
    #: How many for each reason.
    reasons: dict[str, Count]
    note: str
    shown: list[UnplacedItem]
    offset: Count
    not_shown: Count


class MapStanding(ApiModel):
    """What the engine found of an experiment's raw data and analysis: the fields of
    ``completeness`` for it, unchanged, so the map invents no score of its own."""

    raw_acquisitions: Count = Field(
        description="Raw acquisition files found by the engine in its folder, in what was read."
    )
    raw_followed: Count = Field(
        description="Of those, the ones followed by an analysis file found."
    )
    share_of_raw_followed_by_analysis: float | None = Field(
        description="Followed over found; null where no raw acquisition was found (never 0)."
    )
    analysis_files: Count
    result_tables: Count
    has_analysis: bool = Field(
        description="Whether the analysis files or results tables found count as an analysis."
    )
    said: str = Field(description="One sentence a page shows for it, in plain words.")


class MapPoint(ApiModel):
    """One experiment: where it lies, its theme and its standing."""

    id: Identifier
    #: The experiment folder's own name (the last part of ``folder``).
    label: str
    folder: str
    #: Its place, 0 to 1 on both axes with one scale for both (shapes and ratios are kept). Null,
    #: never 0, where it has none (see ``no_place_reason``).
    x: float | None
    y: float | None
    #: Why it has no place, in words; null where it has one.
    no_place_reason: str | None
    #: Experiments at exactly its place, itself included: those reading the same words share a
    #: point. 0 where it has no place.
    at_this_place: Count
    theme: int | None
    theme_label: str | None
    #: The Record target a person's name for its line of work is written on (``theme:<key>``, the
    #: same as ``themes``). Null where it is in no theme.
    theme_key: str | None
    broad_theme: int | None
    broad_label: str | None
    #: Why it is in no theme, in words; null where it is in one. An experiment in no theme is
    #: still placed if it has words the themes read.
    no_theme_reason: str | None
    year: int | None
    files: Count
    #: Null where the completeness reading is not available (see ``standing_said``).
    standing: MapStanding | None


class MapTheme(ApiModel):
    """One theme on the map: its name, and the middle of its experiments' places."""

    theme: int
    label: str
    person_label: str | None
    target: str
    experiments: Count
    #: How many of its experiments have a place (the middle is of these). Null in a themes-only
    #: answer made before the layout was drawn (``layout_ready`` false), never 0.
    placed: Count | None
    #: The middle of its placed experiments' places; null where it has none, and in a themes-only
    #: answer made before the layout was drawn.
    x: float | None
    y: float | None
    #: Its stability between seeds, as ``themes`` gives it (below 0.80 a page marks its edges
    #: uncertain).
    stability: float


@register("themes_map")
class ThemesMapResponse(ApiModel):
    """``GET /api/v1/themes/map?themes_only=``: experiments placed in the plane by their themes.

    Each is placed by the words the themes read. With ``themes_only=true`` the answer
    carries the themes alone and is made without drawing the layout.
    """

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    #: Whether this answer carries the themes alone (``?themes_only=true``): it is made from the
    #: themes projection without drawing the layout, so it answers at once, and ``experiments`` is
    #: empty because they were not asked for, not because the run has none.
    themes_only: bool
    #: Whether the layout is held, so the places in this answer are real and a full read is
    #: immediate. Always true in a full answer; in a themes-only answer false until the first full
    #: read has drawn the layout (then the themes carry no centers, ``placed`` and the counts of
    #: places are null).
    layout_ready: bool
    #: ``tsne`` or ``pca``. Null where no plane was drawn (or, in a themes-only answer, not yet).
    method: Literal["tsne", "pca"] | None
    #: What the places mean and do not mean, in words.
    method_said: str
    #: The seed of the themes' placement the map lies in the space of.
    seed: int
    #: Distinct documents the plane was drawn for (experiments reading the same words are one). Null
    #: before the layout was drawn.
    distinct_documents: Count | None
    #: Every experiment of the run, with a place or without one.
    count: Count
    #: How many have a place and how many have none; null before the layout was drawn.
    placed: Count | None
    unplaced: Count | None
    #: Whether ``standing`` is given on the points, and if not, why in words; null where it is.
    standing_computed: bool
    standing_said: str | None
    themes: list[MapTheme]
    experiments: list[MapPoint]


# ---------------------------------------------------------------------------


def _projection(context: V1Context, query: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    from ingence.core.project import Project
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.themes import themes_engine

    run_id, _ = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    try:
        engine = themes_engine()
    except ComponentNotAvailableError as error:
        from ingence.theme_words import unavailable_in_words

        message = unavailable_in_words(error)
        raise ApiError(
            message, code="component_unavailable", status=HTTPStatus.SERVICE_UNAVAILABLE
        ) from error
    settings = Project.open(context.project).settings.themes
    return engine.projection(context.project, run_id, settings)


def _note(count: int) -> str:
    if count == 0:
        return "every experiment has a theme"
    noun = "experiment has" if count == 1 else "experiments have"
    return f"{count} {noun} no theme"


@dataclass(frozen=True)
class _Said(NamedEvent):
    """A statement naming a line, with what it says (the name, its note, who and when)."""

    said: Mapping[str, str | None]


def person_labels(
    project: Path,
    built: Mapping[str, Any] | None = None,
    *,
    earlier: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> dict[str, dict[str, str | None]]:
    """The name a person gave each line of work, by its target, as the Record says now.

    Read on every request (as the Graph workspace's labels are), never at a run's watermark: a
    name is a display annotation, not a decision a run reads.

    A name follows its line. A line's target is built from its three-word label, and a re-run that
    reads other words for the same experiments gives it another label. With the run's projection
    (``built``) each statement is matched to the line of the run holding most of the experiments the
    line held when it was named (``themes.lines.follow_names``), and the name is served on that
    line's current target. Without it (or without a line to match) a name is served on the target it
    was written on. ``earlier`` reads the projection of the run a statement was written against, for
    a statement that kept no experiments.
    """
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.targets import THEME_PREFIX, theme_named_by, theme_target

    said: list[_Said] = []
    for target, event in latest_by_target(read_events(project)).items():
        if not target.startswith(f"{THEME_PREFIX}:"):
            continue
        raw = event.payload
        payload: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        anchors = payload.get("experiments")
        said.append(
            _Said(
                sequence=int(event.sequence),
                key=theme_named_by(target) or "",
                run_id=str(event.against_run_id) if event.against_run_id else None,
                experiments=frozenset(str(e) for e in anchors) if anchors else None,
                said={
                    "person_label": payload.get("described") or None,
                    "person_note": payload.get("note") or None,
                    "labelled_by": event.actor,
                    "labelled_at": event.occurred_at,
                },
            )
        )
    if built is None:
        return {theme_target(item.key): dict(item.said) for item in said}
    from ingence.themes.lines import follow_names, lines_of

    followed = follow_names(
        lines_of(built),
        said,
        run_id=str(built.get("run_id") or ""),
        earlier=earlier or (lambda _run: None),
    )
    return {theme_target(key): dict(item.said) for key, item in followed.items()}


def _labels_for(context: V1Context, built: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    """The names of the lines of the run ``built`` is the projection of, following each line."""
    from ingence.core.project import Project
    from ingence.themes import themes_engine

    settings = Project.open(context.project).settings.themes

    def earlier(run_id: str) -> Mapping[str, Any] | None:
        try:
            return themes_engine().projection(context.project, run_id, settings)
        except Exception:  # a run removed, or a component not available: the key stands alone
            return None

    return person_labels(context.project, built, earlier=earlier)


def _named(theme: dict[str, Any], labels: dict[str, dict[str, str | None]]) -> dict[str, Any]:
    from ingence.record.targets import theme_key, theme_target

    target = theme_target(theme_key(theme.get("label", "").split(" \u00b7 ")))
    return {**theme, "target": target, **labels.get(target, {})}


@route("themes", model=ThemesResponse)
def themes(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesResponse:
    """Every experiment's theme, every theme and broad theme, and the summary."""
    built = _projection(context, query)
    labels = _labels_for(context, built)
    limit = integer(query, "limit", default=EXPERIMENTS_LIMIT, low=0, high=EXPERIMENTS_LIMIT)
    keys = ThemeExperiment.model_fields
    shown_rows = built["experiments"][:limit]
    with catalog_of(context, query) as (catalog, _):
        names = name_fields(catalog, [(row["id"], row["folder"]) for row in shown_rows])
        counted = counted_as(catalog)
    # The area and its kind: the folder at the area level, with the kind the run's areas give
    # it. ``person`` is set only where the kind is a person.
    from ingence.areas import area_id, area_kinds_of_run, area_of

    areas_known = area_kinds_of_run(context.project, built["run_id"])
    level = built["summary"].get("person_level")
    experiments = []
    for row in shown_rows:
        area = area_of(str(row.get("folder") or ""), level)
        kind = (
            areas_known.get(area_id(str(row.get("root")), area), ("folder", True))[0]
            if area is not None
            else None
        )
        experiments.append(
            ThemeExperiment(
                **{
                    k: v
                    for k, v in row.items()
                    if k in keys and k not in ("area", "area_kind", "person")
                },
                **names[row["id"]],
                person=row.get("person") if kind == "person" else None,
                area=area,
                area_kind=kind,
            )
        )
    theme_keys = ThemeItem.model_fields
    summary = dict(built["summary"])
    summary.pop("sweep", None)
    summary["experiments_counted_as"] = counted
    return ThemesResponse(
        schema_version=SCHEMA_VERSION,
        run_id=built["run_id"],
        summary=ThemesSummary(**summary),
        themes=[
            ThemeItem(**{k: v for k, v in _named(t, labels).items() if k in theme_keys})
            for t in built["themes"]
        ],
        broad=[BroadThemeItem(**b) for b in built["broad"]],
        experiments=experiments,
        experiments_not_shown=max(0, len(built["experiments"]) - limit),
        unplaced_note=_note(len(built["unplaced"])),
    )


@route("themes/stability", model=ThemesStabilityResponse)
def stability(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesStabilityResponse:
    """AMI between seeds overall, NMI beside it, and each theme's stability and core."""
    from ingence.core.project import Project
    from ingence.themes.core import UNCERTAIN_BELOW

    built = _projection(context, query)
    summary = built["summary"]
    label_of = {t["theme"]: t["label"] for t in built["themes"]}
    return ThemesStabilityResponse(
        schema_version=SCHEMA_VERSION,
        run_id=built["run_id"],
        seed_ami=summary["seed_ami"],
        seed_nmi=summary["seed_nmi"],
        resolution=summary["resolution"],
        seeds=Project.open(context.project).settings.themes.seeds,
        measure=(
            "AMI (adjusted mutual information) between the themes drawn with seed 0 and with "
            "each other seed, averaged; NMI beside it. The adjusted Rand index (ARI) compares the "
            "final placements of every pair of seeds, and the seed most like the others is "
            "shown. A theme's stability is the mean, over the other seeds, of the best overlap "
            "(Jaccard) any of their groups reaches with it; its core is the experiments every "
            "seed keeps together"
        ),
        sweep=[SweepItem(**row) for row in summary["sweep"]],
        themes=[
            ThemeStabilityItem(
                theme=t["theme"],
                label=t["label"],
                experiments=t["experiments"],
                stability=t["stability"],
                core_experiments=t["core_experiments"],
                label_stability=t.get("label_stability"),
                split_seeds=t.get("split_seeds", 0),
                split_with=[
                    SplitPartner(
                        theme=p["theme"], label=label_of.get(p["theme"], ""), seeds=p["seeds"]
                    )
                    for p in t.get("split_with", [])
                ],
            )
            for t in built["themes"]
        ],
        seeds_compared=max(0, len(summary.get("seeds", [])) - 1),
        uncertain_below=UNCERTAIN_BELOW,
        seed_ari=summary.get("seed_ari"),
        placement_seed=summary.get("placement_seed", 0),
        medoid_seed=summary.get("medoid_seed"),
        seed_placements=[SeedItem(**row) for row in summary.get("seeds", [])],
        seed_pairs=[SeedPairItem(**row) for row in summary.get("seed_pairs", [])],
        chance=ChanceItem(**summary["chance"]) if summary.get("chance") else None,
    )


@route("themes/overlap", model=ThemesOverlapResponse)
def overlap(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesOverlapResponse:
    """The words each pair of lines of work have in common (the Overlap view's table)."""
    from ingence.themes.overlap import shared_words

    built = _projection(context, query)
    return ThemesOverlapResponse(
        schema_version=SCHEMA_VERSION,
        run_id=built["run_id"],
        themes=len(built["themes"]),
        pairs=[SharedWordsItem(**row) for row in shared_words(built["themes"])],
    )


def _spans(rows: list[dict[str, Any]]) -> list[WordSpan]:
    return [WordSpan(**row) for row in rows]


def years_of_projection(
    built: Mapping[str, Any],
) -> tuple[list[ThemeOverTime], dict[str, YearStory], list[Substitute]]:
    """Themes over time, the year profile and the substitutes, from a themes projection.

    ``themes/years`` answers with them, and ``overlap?mode=by_year`` carries them too.
    """
    over_time = [
        ThemeOverTime(
            theme=t["theme"],
            label=t["label"],
            broad=t["broad"],
            experiments=t["experiments"],
            first=t["first"],
            peak=t["peak"],
            last=t["last"],
            going=t["going"],
            direction=t["direction"],
            by_year=t["by_year"],
            succession=_spans(t["succession"]),
            specific_words=_spans(t["specific_words"]),
        )
        for t in built["themes"]
    ]
    story = {
        year: YearStory(
            experiments=row["experiments"],
            most=[ThemeShare(label=a, experiments=b, lift=c) for a, b, c in row["most"]],
            under=[ThemeShare(label=a, experiments=b, lift=c) for a, b, c in row["under"]],
            began=row["began"],
            last_seen_year_before=row["last_seen_year_before"],
            new_words=[WordCount(word=w, experiments=n) for w, n in row["new_words"]],
        )
        for year, row in built["story"].items()
    }
    return over_time, story, [Substitute(**row) for row in built["substitutes"]]


@route("themes/years", model=ThemesYearsResponse)
def years(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesYearsResponse:
    """Themes over time, the year profile, and words used in each other's place."""
    built = _projection(context, query)
    over_time, story, substitutes = years_of_projection(built)
    return ThemesYearsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=built["run_id"],
        themes=over_time,
        story=story,
        substitutes=substitutes,
    )


def _matches(built: dict[str, Any], word: str, project: Any) -> list[WordMatch]:
    """Who wrote ``word``: its spelling or sense exactly, the sense the Dictionary binds the
    spelling to, or any word it begins (never a substring)."""
    from ingence.dictionary.store import DictionaryStore

    typed = word.strip().casefold()
    wanted = {typed, typed.replace("_", " ")}
    hit = DictionaryStore.for_project(project).look_up(typed)
    if hit is not None:
        # Experiments carry a sense's canonical name (``TrackMate``), read without case.
        wanted.add(str(hit.sense.term).replace("_", " ").casefold())
        wanted.add(str(hit.sense.canonical or hit.sense.term).replace("_", " ").casefold())
    found: dict[str, list[dict[str, Any]]] = {}
    for row in built["experiments"]:
        index = [w.casefold() for w in row.get("index_words", [])]
        if any(w in wanted or w.startswith(typed) for w in index):
            found.setdefault(row["person"] or "", []).append(row)
    out: list[WordMatch] = []
    for person in sorted(found):
        rows = found[person]
        dated = sorted(r["year"] for r in rows if r["year"] is not None)
        labels = [r["theme_label"] for r in rows if r["theme_label"]]
        top = sorted(set(labels), key=lambda label: (-labels.count(label), label))[:3]
        out.append(
            WordMatch(
                person=person,
                experiments=len(rows),
                years=f"{dated[0]}-{dated[-1]}" if dated else "",
                themes=top,
                examples=[r["folder"] for r in rows[:3]],
            )
        )
    return out


def _person_level_is_default(context: V1Context) -> bool:
    """Whether no settings file of the project names a person level (the default stands)."""
    from ingence.core.project import read_settings_file
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE

    for path in (context.project / SETTINGS_FILE, context.project / DASHBOARD_SETTINGS_FILE):
        themes = read_settings_file(path).get("themes")
        if isinstance(themes, dict) and "person_level" in themes:
            return False
    return True


@route("themes/who_else", model=ThemesWhoElseResponse)
def who_else(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesWhoElseResponse:
    """Per person, the shared words, each theme's people and lines handed on; who wrote a word."""
    built = _projection(context, query)
    level = built["summary"]["person_level"]
    rule = (
        "nobody: no folder level is set as a person's"
        if level is None
        else (
            f"the folder {level} level{'s' if level != 1 else ''} below each root "
            "(a setting, not read from names)"
        )
    )
    word = (one(query, "word") or "").strip() or None
    found = built["who_else"]
    return ThemesWhoElseResponse(
        schema_version=SCHEMA_VERSION,
        run_id=built["run_id"],
        person_level=level,
        person_rule=rule,
        person_rule_is_default=_person_level_is_default(context),
        people=[PersonItem(**row) for row in found["people"]],
        shared_words=[SharedWord(**row) for row in found["shared_words"]],
        themes=[
            ThemePeople(
                theme=t["theme"],
                label=t["label"],
                people={p: PersonSpan(**s) for p, s in t["people"].items()},
                shared=t["shared"],
                handed_on=[HandedOn(**h) for h in t["handed_on"]],
            )
            for t in built["themes"]
        ],
        word=word,
        matches=_matches(built, word, context.project) if word else [],
    )


@route("themes/undated", model=ThemesUndatedResponse)
def undated(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesUndatedResponse:
    """The experiments with no year: no date in a name and no believed time of a file."""
    built = _projection(context, query)
    rows = {row["id"]: row for row in built["experiments"]}
    listed = [
        UndatedItem(
            id=unit,
            folder=rows[unit]["folder"],
            files=rows[unit]["files"],
            theme_label=rows[unit]["theme_label"],
        )
        for unit in built["undated"]
    ]
    return ThemesUndatedResponse(
        schema_version=SCHEMA_VERSION, run_id=built["run_id"], count=len(listed), experiments=listed
    )


@route("themes/unplaced", model=ThemesUnplacedResponse)
def unplaced(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesUnplacedResponse:
    """The experiments in no theme, a page at a time (``limit``, ``offset``), with why."""
    built = _projection(context, query)
    limit = integer(query, "limit", default=UNPLACED_PAGE, low=1, high=1000)
    offset = integer(query, "offset", default=0, low=0, high=10**9)
    rows = {row["id"]: row for row in built["experiments"]}
    every = [
        UnplacedItem(
            id=unit, folder=rows[unit]["folder"], reason=rows[unit]["unplaced_reason"] or ""
        )
        for unit in built["unplaced"]
    ]
    reasons: dict[str, int] = {}
    for item in every:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1
    shown = every[offset : offset + limit]
    return ThemesUnplacedResponse(
        schema_version=SCHEMA_VERSION,
        run_id=built["run_id"],
        count=len(every),
        reasons=dict(sorted(reasons.items())),
        note=_note(len(every)),
        shown=shown,
        offset=offset,
        not_shown=max(0, len(every) - offset - len(shown)),
    )


#: Each run's standing of its experiments, held in memory: a published run's claims never change,
#: so the counts are the same on every read, and counting them again takes seconds on a large run.
#: Keyed by the project, the run and the completeness settings (a setting changes ``has_analysis``).
_STANDING_HELD: dict[tuple[str, str, str], dict[str, MapStanding]] = {}
_STANDING_KEPT = 4
#: Held while a run's standing is looked up and, where it is not held, counted, so two first reads
#: of one run count it once.
_STANDING_LOCK = threading.Lock()
#: What a person is told where the standing could not be counted; the cause is not shown (it may
#: hold a path).
STANDING_NOT_COUNTED = (
    "What was found of raw data and analysis could not be counted for this run, so no standing "
    "is shown."
)


def _standing(project: Path, run_id: str) -> tuple[dict[str, MapStanding] | None, str | None]:
    """Each experiment's completeness fields and None, or None and why not in plain words (the map
    is answered without it)."""
    from ingence.api.v1.completeness import experiment_said
    from ingence.completeness import completeness_of_run
    from ingence.core.project import Project

    settings = Project.open(project).settings.completeness
    key = (str(project.resolve()), run_id, settings.model_dump_json())
    with _STANDING_LOCK:
        held = _STANDING_HELD.get(key)
        if held is not None:
            return held, None
        try:
            found = completeness_of_run(project, run_id, settings=settings)
        except Exception:  # an optional part: the map is answered without it, and says so
            return None, STANDING_NOT_COUNTED
        counted = {
            row.experiment_id: MapStanding(
                raw_acquisitions=row.raw_acquisitions,
                raw_followed=row.followed,
                share_of_raw_followed_by_analysis=row.share,
                analysis_files=row.analysis_files,
                result_tables=row.result_tables,
                has_analysis=row.has_analysis,
                said=experiment_said(row),
            )
            for row in found.experiments
        }
        _STANDING_HELD[key] = counted
        while len(_STANDING_HELD) > _STANDING_KEPT:
            _STANDING_HELD.pop(next(iter(_STANDING_HELD)))
        return counted, None


def _method_said(method: str | None, experiments: int) -> str:
    from ingence.themes.mapxy import METHOD_WORDS, NO_PLACE_FEW

    if method is None:
        return f"No map was drawn: {NO_PLACE_FEW if experiments else 'the run has no experiments'}."
    return METHOD_WORDS[method]


#: What ``method_said`` says in a themes-only answer made before the layout was drawn.
NOT_PLACED_YET = (
    "The places are not drawn yet: the first full read of the map draws them once and keeps them."
)
#: What ``standing_said`` says in a themes-only answer (no experiment is carried).
STANDING_NOT_ASKED = "The standing is given with the experiments, in the full answer."


@route("themes/map", model=ThemesMapResponse)
def themes_map(context: V1Context, query: Mapping[str, Sequence[str]]) -> ThemesMapResponse:
    """Where each experiment lies in the plane, with its theme and what was found of it.

    With ``themes_only=true`` the themes alone, answered at once from the themes projection: the
    layout is not drawn (the first full read draws it, taking about a minute on a large project),
    so each theme's center is given only where the layout is already held, and ``experiments`` is
    empty. A page draws the themes' names first, then asks for the whole answer.
    """
    from ingence.core.project import Project
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.themes import themes_engine

    only = boolean(query, "themes_only")
    built = _projection(context, query)
    run_id = built["run_id"]
    settings = Project.open(context.project).settings.themes
    try:
        engine = themes_engine()
        placed = (
            engine.held_map_projection(context.project, run_id, settings)
            if only
            else engine.map_projection(context.project, run_id, settings)
        )
    except ComponentNotAvailableError as error:
        from ingence.theme_words import unavailable_in_words

        raise ApiError(
            unavailable_in_words(error),
            code="component_unavailable",
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        ) from error
    standing: dict[str, MapStanding] | None = None
    standing_said: str | None = STANDING_NOT_ASKED
    if not only:
        standing, standing_said = _standing(context.project, run_id)
    labels = _labels_for(context, built)
    named = {t["theme"]: _named(t, labels) for t in built["themes"]}
    places: dict[str, Any] = {} if placed is None else placed["places"]
    reasons: dict[str, str] = {} if placed is None else placed["reasons"]
    shared = Counter(tuple(xy) for xy in places.values() if xy is not None)
    points: list[MapPoint] = []
    members: dict[int, list[tuple[float, float]]] = {}
    for row in built["experiments"]:
        xy = places.get(row["id"])
        theme = row["theme"]
        if xy is not None and theme is not None:
            members.setdefault(theme, []).append((xy[0], xy[1]))
        if only:
            continue
        points.append(
            MapPoint(
                id=row["id"],
                label=row["folder"].rstrip("/").rsplit("/", 1)[-1],
                folder=row["folder"],
                x=None if xy is None else xy[0],
                y=None if xy is None else xy[1],
                no_place_reason=None if xy is not None else reasons.get(row["id"]),
                at_this_place=0 if xy is None else shared[tuple(xy)],
                theme=theme,
                theme_label=row["theme_label"],
                theme_key=None if theme is None else named[theme]["target"],
                broad_theme=row["broad_theme"],
                broad_label=row["broad_label"],
                no_theme_reason=row["unplaced_reason"],
                year=row["year"],
                files=row["files"],
                standing=None if standing is None else standing.get(row["id"]),
            )
        )
    themes: list[MapTheme] = []
    for t in built["themes"]:
        found = members.get(t["theme"], [])
        themes.append(
            MapTheme(
                theme=t["theme"],
                label=t["label"],
                person_label=named[t["theme"]].get("person_label"),
                target=named[t["theme"]]["target"],
                experiments=t["experiments"],
                placed=None if placed is None else len(found),
                x=round(sum(x for x, _ in found) / len(found), 6) if found else None,
                y=round(sum(y for _, y in found) / len(found), 6) if found else None,
                stability=t["stability"],
            )
        )
    count = len(built["experiments"])
    drawn = sum(1 for row in built["experiments"] if places.get(row["id"]) is not None)
    return ThemesMapResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        themes_only=only,
        layout_ready=placed is not None,
        method=None if placed is None else placed["method"],
        method_said=(NOT_PLACED_YET if placed is None else _method_said(placed["method"], count)),
        seed=int(built["summary"]["placement_seed"]) if placed is None else placed["seed"],
        distinct_documents=None if placed is None else placed["points"],
        count=count,
        placed=None if placed is None else drawn,
        unplaced=None if placed is None else count - drawn,
        standing_computed=standing is not None,
        standing_said=standing_said,
        themes=themes,
        experiments=points,
    )
