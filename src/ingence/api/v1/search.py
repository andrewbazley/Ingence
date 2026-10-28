"""``/api/v1/search``: one page of search results, typed.

Built from ``api/routes/search.py``'s ``search``, which the search box reads, and shaped field by
field. A result's tags and its ranking are sent as the search engine made them (plain JSON): they
are presentation the search layer owns and changes, and typing them here would freeze it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from ingence import SCHEMA_VERSION
from ingence.api.routes import search as search_routes
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import boolean, integer, json_value, one, pinned_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["ByMonth", "CoOccurringWord", "SearchResponse", "SearchResult"]

#: The most results one request may ask for.
MOST = 200


class WordFoundBy(ApiModel):
    """How one typed word was found in a result."""

    word: str
    #: ``spelling``, ``sense``, ``starts_with``, ``plain_name`` or ``near_spelling``.
    found_by: str


class SearchResult(ApiModel):
    """One result: a unit (an experiment, an analysis folder, a figure or document), not a file."""

    position: Count
    #: The unit's folder (or, for a figure or document, the file) as an entry identifier.
    folder_id: Identifier
    unit_kind: str
    path: str
    basis: str
    made_as: JsonValue
    doubted: bool
    marked_good: bool
    hidden_from_similar: bool
    contents: str
    tags: JsonValue
    ranked_by: JsonValue
    covered: list[str]
    missing: list[str]
    why: JsonValue
    inside_count: Count
    folded_count: Count
    #: How it was found, strongest first: ``spelling`` (a name holds the word as typed), ``sense``
    #: (another spelling of the sense it names), ``starts_with`` (a word in a name begins with it),
    #: ``plain_name`` (it stands inside a name), ``near_spelling``. For several words, the weakest
    #: of each word's strongest way, as the ranking reads it.
    found_by: str | None
    #: The same, in a sentence.
    found_by_said: str
    #: Every other way any of its matches took, strongest first.
    also_found_by: list[str]
    #: Each covered word and how it was found.
    found_by_word: list[WordFoundBy]


class CoOccurringWord(ApiModel):
    """A word that comes with the query across the matched units.

    ``pmi`` is pointwise mutual information over unit documents, in bits. Zero is what chance
    gives. It is not a probability, and it only orders the words.
    """

    word: str
    #: Matched units holding the word.
    units: Count
    #: Units in the whole pool holding it.
    units_in_pool: Count
    pmi: float


class MonthCount(ApiModel):
    #: ``YYYY-MM``.
    month: str
    units: Count


class ByMonth(ApiModel):
    """The matched units by the month of their earliest believed time, the undated apart."""

    #: ``earliest_credible_time`` where the rule reading time concluded in the run, else
    #: ``own_time`` (a clock a file's content states, or its modification).
    by: str
    units: Count
    dated: Count
    undated: Count
    #: Every month from the first to the last, an empty one as zero.
    months: list[MonthCount]


class HiddenSummary(ApiModel):
    count: Count
    shown: bool


@register("search")
class SearchResponse(ApiModel):
    """``GET /api/v1/search?q=``: results ranked by a tuple whose parts are shown, never a score."""

    schema_version: str
    run_id: Identifier
    query: str
    #: How each word was read (the search layer's own rows).
    reading: JsonValue
    warnings: list[str]
    refused: list[str]
    concept_keys: list[str]
    hidden: HiddenSummary
    results: list[SearchResult]
    total_units: Count
    projection_built_now: bool
    limit: Count
    offset: Count
    #: The words that come with the query across every matched unit, strongest first.
    co_occurring_words: list[CoOccurringWord]
    #: Every matched unit counted by month.
    by_month: ByMonth


def _texts(values: Any) -> list[str]:
    return [str(value) for value in values or ()]


def _result(row: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        position=int(row.get("position") or 0),
        folder_id=str(row["folder_id"]),
        unit_kind=str(row.get("unit_kind") or ""),
        path=str(row.get("path") or ""),
        basis=str(row.get("basis") or ""),
        made_as=json_value(row.get("made_as")),
        doubted=bool(row.get("doubted")),
        marked_good=bool(row.get("marked_good")),
        hidden_from_similar=bool(row.get("hidden_from_similar")),
        contents=str(row.get("contents") or ""),
        tags=json_value(row.get("tags") or {}),
        ranked_by=json_value(row.get("ranked_by") or {}),
        covered=_texts(row.get("covered")),
        missing=_texts(row.get("missing")),
        why=json_value(row.get("why") or []),
        inside_count=int(row.get("inside_count") or 0),
        folded_count=int(row.get("folded_count") or 0),
        found_by=str(row["found_by"]) if row.get("found_by") else None,
        found_by_said=str(row.get("found_by_said") or ""),
        also_found_by=_texts(row.get("also_found_by")),
        found_by_word=[
            WordFoundBy(word=str(held.get("word") or ""), found_by=str(held.get("found_by") or ""))
            for held in row.get("found_by_word") or ()
        ],
    )


def _by_month(held: Mapping[str, Any] | None) -> ByMonth:
    held = held or {}
    return ByMonth(
        by=str(held.get("by") or "own_time"),
        units=int(held.get("units") or 0),
        dated=int(held.get("dated") or 0),
        undated=int(held.get("undated") or 0),
        months=[
            MonthCount(month=str(month["month"]), units=int(month["units"]))
            for month in held.get("months") or ()
        ],
    )


@route("search", model=SearchResponse)
def search_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> SearchResponse:
    text = one(query, "q") or ""
    limit = integer(query, "limit", default=search_routes.LIMIT, low=1, high=MOST)
    offset = integer(query, "offset", default=0, low=0, high=10_000)
    show_hidden = boolean(query, "hidden", true_word="show")
    with pinned_catalog(context) as (catalog, run_id):
        found = search_routes.search(
            catalog,
            context.project,
            text=text,
            limit=limit,
            offset=offset,
            show_hidden=show_hidden,
        )
    hidden = found.get("hidden") or {}
    return SearchResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        query=str(found.get("query") or text),
        reading=json_value(found.get("reading") or []),
        warnings=_texts(found.get("warnings")),
        refused=_texts(found.get("refused")),
        concept_keys=_texts(found.get("concept_keys")),
        hidden=HiddenSummary(count=int(hidden.get("count") or 0), shown=bool(hidden.get("shown"))),
        results=[_result(row) for row in found.get("results", [])],
        total_units=int(found.get("total_units") or 0),
        projection_built_now=bool(found.get("projection_built_now")),
        limit=limit,
        offset=offset,
        co_occurring_words=[
            CoOccurringWord(
                word=str(word["word"]),
                units=int(word["units"]),
                units_in_pool=int(word["units_in_pool"]),
                pmi=float(word["pmi"]),
            )
            for word in found.get("co_occurring_words") or ()
        ],
        by_month=_by_month(found.get("by_month")),
    )
