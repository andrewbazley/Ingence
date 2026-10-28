"""``GET /api/v1/completeness``: per experiment, what the engine found of raw data and its analysis.

This route is a projection of the pinned run, computed on request by :mod:`ingence.completeness`
and written nowhere. Every count is what the engine found in what was read. Each field's
description says so, because the page and the exported schema are where a person reads it. A lack
of files means the engine did not find them, not that they do not exist. The share is null, never 0,
where no raw acquisition was found.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.names import NamePartItem, name_fields
from ingence.api.v1.shaping import pinned_catalog
from ingence.record.targets import analysed_elsewhere_target

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.completeness import (
        Completeness,
        ExperimentFound,
        GroupCompleteness,
        NoAnalysisFound,
    )
    from ingence.completeness.elsewhere import ElsewhereSaid
    from ingence.core.settings import CompletenessSettings

__all__ = [
    "CompletenessResponse",
    "ExperimentCompleteness",
    "GroupRow",
    "NoAnalysisResponse",
    "NoAnalysisRow",
    "completeness",
    "experiment_said",
    "no_analysis",
]

_FOUND = "found by the engine in what was read (not every file that exists)"


class ElsewhereRow(ApiModel):
    """A person's answer that an experiment's analysis is elsewhere: the page's magenta flag
    (magenta: "a person said so")."""

    actor: str = Field(description="Who said so, under the machine's decider name.")
    occurred_at: str
    event_id: Identifier
    read_by_run: bool = Field(
        description=(
            "Whether this run read the answer; only then is the experiment off the list of no "
            "analysis found."
        )
    )
    said_now: bool = Field(
        description=(
            "Whether the answer is in force in the Record now; differs from read_by_run while "
            "a change waits for the next re-run."
        )
    )
    said: str = Field(description="The words a page shows beside the flag.")


class AnswerWord(ApiModel):
    """A word a page offers for an experiment's analysis being elsewhere, and what it does."""

    word: str
    label: str
    meaning: str


class ExperimentCompleteness(ApiModel):
    """One experiment, as the engine found it: a folder an experiment rule classed."""

    id: Identifier = Field(description="The experiment folder's item.")
    folder: str = Field(description="The experiment's folder, relative to its root.")
    name: str = Field(default="", description="The folder's own name, as written.")
    shown_name: str = Field(
        default="",
        description=(
            "The name a row shows: the folder's name with the dates the tokenizer read in it "
            "left out; the name itself where the run recorded no layout to read."
        ),
    )
    name_parts: list[NamePartItem] = Field(
        default_factory=list,
        description="The folder's name read into parts by the tokenizer; empty where not read.",
    )
    name_read: bool = Field(
        default=False,
        description="Whether the run recorded a layout for the name; false: no parts are given.",
    )
    root_id: Identifier
    person: str | None = Field(
        description=(
            "Whose experiment it is: the first folder level below its root, until a setting "
            "naming a person replaces it; null for a root's own folder."
        )
    )
    files: Count = Field(description=f"Files {_FOUND} in its folder.")
    raw_acquisitions: Count = Field(description=f"Raw acquisition files {_FOUND} in its folder.")
    derived_files: Count = Field(
        description=f"Files {_FOUND} derived from its raw acquisitions, wherever they lie."
    )
    raw_followed: Count = Field(
        description=f"Raw acquisitions {_FOUND} that are followed by an analysis file found."
    )
    share_of_raw_followed_by_analysis: float | None = Field(
        description=(
            "The share of its raw acquisitions found that are followed by an analysis file found; "
            "null where no raw acquisition was found (unknown, never 0)."
        )
    )
    analysis_files: Count = Field(description=f"Files {_FOUND} at an analysis stage.")
    result_tables: Count = Field(description=f"Results tables {_FOUND} in its folder.")
    has_analysis: bool = Field(
        description=(
            "Whether the analysis files or results tables found in it count as an analysis of "
            "its own, under the completeness settings in force."
        )
    )
    theme: str | None = Field(
        description="Its theme, where themes are computed; null where none is given."
    )
    theme_label: str | None = Field(
        default=None,
        description=(
            "The words of its theme (its label), beside the theme's number, for a page to show; "
            "null where it has no theme or themes are not computed."
        ),
    )
    date: str | None = Field(
        description=(
            "Its date, as the themes view dates it: the earliest date a name in its folder gives, "
            "else the earliest believed time of a file in it; null where nothing dates it "
            "(unknown, never a guess). Given whether or not themes are computed."
        )
    )
    said: str = Field(description="One sentence a page shows for it, in plain words.")
    analysed_elsewhere: ElsewhereRow | None = Field(
        description="What a person said of its analysis being elsewhere; null where nobody did."
    )
    answer_target: str = Field(
        description=(
            "The Record target an answer about its analysis being elsewhere is written to, "
            "through POST /api/review/answer with a word of answer_words."
        )
    )


class GroupRow(ApiModel):
    """One person's or one theme's experiments, summed over their raw acquisitions found."""

    key: str
    experiments: Count = Field(description="Experiments the engine found.")
    experiments_with_raw: Count = Field(
        description=f"Experiments with at least one raw acquisition {_FOUND}."
    )
    raw_acquisitions: Count = Field(description=f"Raw acquisitions {_FOUND}.")
    raw_followed: Count = Field(
        description=f"Raw acquisitions {_FOUND} followed by an analysis file found."
    )
    share_of_raw_followed_by_analysis: float | None = Field(
        description="Followed over found, pooled; null where no raw acquisition was found."
    )
    analysis_files: Count = Field(description=f"Analysis files {_FOUND}.")
    result_tables: Count = Field(description=f"Results tables {_FOUND}.")
    no_analysis_found: Count = Field(
        description="Experiments listed as having no analysis found in what was read."
    )
    analysed_elsewhere: Count = Field(
        description="Experiments a person said are analyzed elsewhere, as this run read them."
    )


class OutsideRow(ApiModel):
    """Files the engine found in no experiment it found, and the raw data among them."""

    files: Count = Field(description=f"Files {_FOUND} in no experiment found.")
    raw_acquisitions: Count = Field(
        description=f"Raw acquisitions {_FOUND} in no experiment found."
    )
    raw_followed: Count = Field(
        description="Of those, the ones followed by an analysis file found."
    )
    share_of_raw_followed_by_analysis: float | None
    said: str


class Thresholds(ApiModel):
    """The completeness settings the response was computed under (``completeness.*``)."""

    judged_from_acquisitions: Count
    listed_below_share: float
    own_analysis_files: Count
    own_result_tables: Count
    forgotten_at_least: float
    stopped_below: float


@register("completeness")
class CompletenessResponse(ApiModel):
    """Per experiment, per person and per theme: raw data and analysis, as the engine found them."""

    schema_version: str
    run_id: Identifier
    experiments: list[ExperimentCompleteness]
    people: list[GroupRow]
    themes: list[GroupRow]
    themes_computed: bool = Field(
        description="Whether themes were computed; where not, per-theme sums are empty."
    )
    themes_said: str
    outside: OutsideRow
    settings: Thresholds
    answer_words: list[AnswerWord]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def experiment_said(row: ExperimentFound) -> str:
    """The sentence a page shows for one experiment: what was found, never what exists."""
    if not row.raw_acquisitions:
        return (
            f"No raw acquisition found in what was read; {_plural(row.files, 'file')} found, "
            f"{_plural(row.analysis_files, 'analysis file')} and "
            f"{_plural(row.result_tables, 'results table')} among them."
        )
    return (
        f"{row.followed} of {_plural(row.raw_acquisitions, 'raw acquisition')} found "
        f"{'is' if row.followed == 1 else 'are'} analyzed; "
        f"{_plural(row.analysis_files, 'analysis file')} and "
        f"{_plural(row.result_tables, 'results table')} found in its folder."
    )


def elsewhere_row(said: ElsewhereSaid | None) -> ElsewhereRow | None:
    if said is None:
        return None
    return ElsewhereRow(
        actor=said.actor,
        occurred_at=said.occurred_at,
        event_id=said.event_id,
        read_by_run=said.read_by_run,
        said_now=said.said_now,
        said=said.said,
    )


def answer_words() -> list[AnswerWord]:
    from ingence.completeness.elsewhere import ELSEWHERE_WORDS

    return [
        AnswerWord(word=item["word"], label=item["label"], meaning=item["meaning"])
        for item in ELSEWHERE_WORDS
    ]


def group_row(group: GroupCompleteness) -> GroupRow:
    return GroupRow(
        key=group.key,
        experiments=group.experiments,
        experiments_with_raw=group.experiments_with_raw,
        raw_acquisitions=group.raw_acquisitions,
        raw_followed=group.followed,
        share_of_raw_followed_by_analysis=group.share,
        analysis_files=group.analysis_files,
        result_tables=group.result_tables,
        no_analysis_found=group.no_analysis_found,
        analysed_elsewhere=group.analysed_elsewhere,
    )


def experiment_row(
    row: ExperimentFound, names: Mapping[str, Any] | None = None
) -> ExperimentCompleteness:
    return ExperimentCompleteness(
        id=row.experiment_id,
        folder=row.folder,
        **(names or {}),
        root_id=row.root_id,
        person=row.person,
        files=row.files,
        raw_acquisitions=row.raw_acquisitions,
        derived_files=row.derived_files,
        raw_followed=row.followed,
        share_of_raw_followed_by_analysis=row.share,
        analysis_files=row.analysis_files,
        result_tables=row.result_tables,
        has_analysis=row.has_analysis,
        theme=row.theme,
        theme_label=row.theme_label,
        date=row.date,
        said=experiment_said(row),
        analysed_elsewhere=elsewhere_row(row.elsewhere),
        answer_target=analysed_elsewhere_target(row.experiment_id),
    )


def pinned_completeness(context: V1Context) -> Completeness:
    """The pinned run's completeness, or the refusal of a project with no published run."""
    from http import HTTPStatus

    from ingence.completeness import completeness_of_run

    if context.run_pinned is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    return completeness_of_run(context.project, context.run_pinned)


def themes_not_computed(why: str) -> str:
    """The words for themes not computed, with why."""
    return f"Themes not computed: {why}; so nothing is summed per theme and no reason is given."


@route("completeness", model=CompletenessResponse)
def completeness(context: V1Context, query: Mapping[str, Sequence[str]]) -> CompletenessResponse:
    """Every experiment of the pinned run, and the sums per person and per theme."""
    del query
    found = pinned_completeness(context)
    outside = found.outside
    with pinned_catalog(context) as (catalog, _):
        names = name_fields(catalog, [(row.experiment_id, row.folder) for row in found.experiments])
    return CompletenessResponse(
        schema_version=SCHEMA_VERSION,
        run_id=found.run_id,
        experiments=[experiment_row(row, names[row.experiment_id]) for row in found.experiments],
        people=[group_row(group) for group in found.people],
        themes=[group_row(group) for group in found.themes],
        themes_computed=found.themes_computed,
        themes_said="" if found.themes_computed else themes_not_computed(found.themes_said),
        outside=OutsideRow(
            files=outside.files,
            raw_acquisitions=outside.raw_acquisitions,
            raw_followed=outside.followed,
            share_of_raw_followed_by_analysis=outside.share,
            said=(
                f"{_plural(outside.raw_acquisitions, 'raw acquisition')} found in no experiment "
                f"Ingence found; {outside.followed} of them analyzed."
            ),
        ),
        settings=thresholds(found.settings),
        answer_words=answer_words(),
    )


class NoAnalysisRow(ApiModel):
    """One experiment with raw acquisitions found and no analysis found in what was read."""

    id: Identifier = Field(description="The experiment folder's item.")
    folder: str
    root_id: Identifier
    person: str | None
    theme: str | None
    theme_label: str | None = Field(
        default=None,
        description="The words of its theme, beside the theme's number; null where it has none.",
    )
    raw_acquisitions: Count = Field(description=f"Raw acquisition files {_FOUND} in its folder.")
    raw_followed: Count = Field(
        description=f"Raw acquisitions {_FOUND} followed by an analysis file found."
    )
    share_of_raw_followed_by_analysis: float = Field(
        description="The share of its raw acquisitions found followed by an analysis file found."
    )
    analysis_files: Count = Field(description=f"Analysis files {_FOUND} in its folder.")
    result_tables: Count = Field(description=f"Results tables {_FOUND} in its folder.")
    reason: Literal["forgotten", "stopped"] | None = Field(
        description=(
            "forgotten: the rest of its theme's raw data found is mostly followed by analysis; "
            "stopped: mostly not (a line of work that ended); null: neither, or themes not "
            "computed."
        )
    )
    theme_rest_share: float | None = Field(
        description=(
            "The share of the rest of its theme's raw acquisitions found followed by analysis; "
            "null where it has no theme or no other experiment of its theme is judged."
        )
    )
    reason_said: str = Field(description="The reason, or why none is given, in plain words.")
    said: str = Field(description="One sentence a page shows for it: no analysis found.")
    analysed_elsewhere: ElsewhereRow | None = Field(
        description="What a person said of its analysis being elsewhere; null where nobody did."
    )
    answer_target: str = Field(
        description="The Record target an answer about its analysis being elsewhere is written to."
    )


@register("no_analysis_found")
class NoAnalysisResponse(ApiModel):
    """The experiments with raw acquisitions found and no analysis found in what was read.

    The list never says "never analyzed", because what the engine did not find may exist elsewhere.
    """

    schema_version: str
    run_id: Identifier
    listed: list[NoAnalysisRow]
    answered: list[NoAnalysisRow] = Field(
        description=(
            "Experiments that would be listed, but a person said their analysis is elsewhere "
            "and this run read the answer: shown as answered."
        )
    )
    judged: Count = Field(
        description=(
            "Experiments with enough raw acquisitions found to be judged "
            "(completeness.judged_from_acquisitions): the list's denominator."
        )
    )
    themes_computed: bool
    themes_said: str
    settings: Thresholds
    answer_words: list[AnswerWord]


def no_analysis_row(item: NoAnalysisFound) -> NoAnalysisRow:
    row = item.experiment
    return NoAnalysisRow(
        id=row.experiment_id,
        folder=row.folder,
        root_id=row.root_id,
        person=row.person,
        theme=row.theme,
        theme_label=row.theme_label,
        raw_acquisitions=row.raw_acquisitions,
        raw_followed=row.followed,
        share_of_raw_followed_by_analysis=row.share or 0.0,
        analysis_files=row.analysis_files,
        result_tables=row.result_tables,
        reason=item.reason,
        theme_rest_share=item.theme_rest,
        reason_said=item.reason_said,
        said=item.said,
        analysed_elsewhere=elsewhere_row(row.elsewhere),
        answer_target=analysed_elsewhere_target(row.experiment_id),
    )


def thresholds(settings: CompletenessSettings) -> Thresholds:
    return Thresholds(
        judged_from_acquisitions=settings.judged_from_acquisitions,
        listed_below_share=settings.listed_below_share,
        own_analysis_files=settings.own_analysis_files,
        own_result_tables=settings.own_result_tables,
        forgotten_at_least=settings.forgotten_at_least,
        stopped_below=settings.stopped_below,
    )


@route("completeness/no_analysis_found", model=NoAnalysisResponse)
def no_analysis(context: V1Context, query: Mapping[str, Sequence[str]]) -> NoAnalysisResponse:
    """The pinned run's experiments with no analysis found, each with its reason."""
    del query
    found = pinned_completeness(context)
    return NoAnalysisResponse(
        schema_version=SCHEMA_VERSION,
        run_id=found.run_id,
        listed=[no_analysis_row(item) for item in found.no_analysis.listed],
        answered=[no_analysis_row(item) for item in found.no_analysis.answered],
        judged=found.no_analysis.judged,
        themes_computed=found.themes_computed,
        themes_said="" if found.themes_computed else themes_not_computed(found.themes_said),
        settings=thresholds(found.settings),
        answer_words=answer_words(),
    )
