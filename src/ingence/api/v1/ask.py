"""``GET /api/v1/ask?q=``: a question in a person's words, answered from the run.

The response holds these parts:

- How the question was read: the words kept, dropped and unknown.
- The words widened to their Dictionary variants.
- The filters read from the question (years, types, areas).
- The experiments matching the question, by tier: named by its words, associated with them, or in
  the same theme as one that is. Each lists the words it matched and how.
- The experiments in the same theme.

A missing or blank ``q`` is refused.

The reading is :mod:`ingence.search.ask`, over the experiments the Experiments page knows
(:mod:`ingence.api.v1.asking`). The ``filters`` are the years, file types and areas the question
names. A year is a date or a four-digit number. A file type is ``images``, ``tables`` or
``scripts``, where the run holds such files. An area is a top-level folder's name. Each filter is
null where the question names none. The filters narrow the experiments answered with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, MatchTierName, register
from ingence.api.v1.shaping import one, run_required

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "AskDates",
    "AskExperiment",
    "AskFilters",
    "AskResponse",
    "AskSameTheme",
    "Expansion",
    "Followed",
    "MatchedWord",
    "WordsRead",
]


class WordsRead(ApiModel):
    """How a question's or a goal's words were read."""

    kept: list[str] = Field(description="The words the question is answered with.")
    dropped: list[str] = Field(description="The words left out (connectives, filler).")
    unknown: list[str] = Field(description="The words nothing in the run or the Dictionary knows.")


class Expansion(ApiModel):
    """One word widened to the spellings it is also answered with."""

    word: str = Field(description="The word as written.")
    to: list[str] = Field(description="The spellings it is widened to.")
    how: str = Field(description="How, in words (a Dictionary variant, a plural, a slip).")


class AskFilters(ApiModel):
    """The filters read from the question."""

    years: list[int] | None = Field(description="Years the question names; null for any.")
    types: list[str] | None = Field(description="File types it names; null for any.")
    areas: list[str] | None = Field(description="Areas it names; null for any.")


class MatchedWord(ApiModel):
    """One word an experiment matched, and how."""

    word: str = Field(description="The word matched.")
    how: str = Field(description="How it matched, in words (in its name, in a file's name).")
    kind: str | None = Field(
        default=None,
        alias="class",
        description="The word's Dictionary class or theme kind (nucleotide, reagent); null "
        "where the Dictionary binds it to nothing.",
    )


class AskDates(ApiModel):
    """An experiment's first and last date."""

    first: str | None = Field(description="Its first date, YYYY-MM-DD; null where undated.")
    last: str | None = Field(description="Its last date, YYYY-MM-DD; null where undated.")


class Followed(ApiModel):
    """Raw files found followed by analysis, over raw files (the Experiments page's numbers)."""

    n: Count = Field(description="Raw files found followed by analysis.")
    of: Count = Field(description="Raw files found.")


class AskExperiment(ApiModel):
    """One experiment matching the question."""

    experiment_id: Identifier = Field(description="The experiment folder's entry.")
    label: str = Field(description="The name a page shows for it.")
    score_tier: MatchTierName = Field(description="How well it matches.")
    matched: list[MatchedWord] = Field(description="The words it matched, and how.")
    dates: AskDates = Field(description="Its first and last date.")
    completeness: float | None = Field(
        description="Its raw files found followed over its raw files; null where no raw."
    )
    followed: Followed = Field(
        default=Followed(n=0, of=0),
        description="The same two numbers as counts: raw files followed, of raw files.",
    )


class AskSameTheme(ApiModel):
    """An experiment in the same theme as one that matched."""

    experiment_id: Identifier = Field(description="The experiment folder's entry.")
    via_theme: str = Field(description="The words of the theme it shares.")
    label: str = Field(default="", description="The name a page shows for it.")
    dates: AskDates = Field(
        default=AskDates(first=None, last=None), description="Its first and last date."
    )
    completeness: float | None = Field(
        default=None,
        description="Its raw files found followed over its raw files; null where no raw.",
    )


@register("ask")
class AskResponse(ApiModel):
    """``GET /api/v1/ask``: a question answered from the run."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    q: str = Field(description="The question as asked.")
    read: WordsRead = Field(description="How its words were read.")
    expanded: list[Expansion] = Field(description="The words widened, and how.")
    filters: AskFilters = Field(description="The filters read from it.")
    experiments: list[AskExperiment] = Field(description="The experiments matching, best first.")
    same_theme: list[AskSameTheme] = Field(description="Experiments in the same theme as a match.")


#: The most characters a question may run to: a sentence or two, never a pasted page.
QUESTION_MAXIMUM = 500


@route("ask", model=AskResponse)
def ask(context: V1Context, query: Mapping[str, Sequence[str]]) -> AskResponse:
    """A question answered from the pinned run, in tiers, each experiment with why."""
    from ingence.api.v1.asking import (
        ask_in_run,
        dates_of,
        expansions_of,
        followed_of,
        matched_of,
        run_experiments,
        words_read,
    )
    from ingence.api.v1.shaping import pinned_catalog

    q = (one(query, "q") or "").strip()
    if not q:
        message = "q is the question, in words; none was given"
        raise ApiError(message, parameter="q")
    if len(q) > QUESTION_MAXIMUM:
        message = f"q is at most {QUESTION_MAXIMUM} characters; {len(q)} were given"
        raise ApiError(message, parameter="q")
    run_required(context)
    with pinned_catalog(context) as (catalog, run_id):
        known = run_experiments(context, catalog, run_id)
        reading = ask_in_run(context, catalog, q, known.asked)
    return AskResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        q=q,
        read=words_read(reading),
        expanded=expansions_of(reading),
        filters=AskFilters(
            years=list(reading.filters.years) if reading.filters.years is not None else None,
            types=list(reading.filters.types) if reading.filters.types is not None else None,
            areas=list(reading.filters.areas) if reading.filters.areas is not None else None,
        ),
        experiments=[
            AskExperiment(
                experiment_id=item.experiment.experiment_id,
                label=item.experiment.label,
                score_tier=item.tier,
                matched=matched_of(item),
                dates=dates_of(known.by_id.get(item.experiment.experiment_id)),
                completeness=(
                    held.completeness
                    if (held := known.by_id.get(item.experiment.experiment_id)) is not None
                    else None
                ),
                followed=followed_of(known.by_id.get(item.experiment.experiment_id)),
            )
            for item in reading.experiments
        ],
        same_theme=[
            AskSameTheme(
                experiment_id=item.experiment.experiment_id,
                via_theme=item.via_theme or "",
                label=item.experiment.label,
                dates=dates_of(known.by_id.get(item.experiment.experiment_id)),
                completeness=(
                    held.completeness
                    if (held := known.by_id.get(item.experiment.experiment_id)) is not None
                    else None
                ),
            )
            for item in reading.same_theme
        ],
    )
