"""The questions as one file to fill in, and whom Import offers as a file's author.

``GET /api/v1/questions/pack`` writes the Questions page's rows as one self-describing file
(``review/question_pack.py``) that whoever fills it in returns; Import writes each answer as its
author's through the answer route (``api/routes/imports.py``). ``GET /api/v1/authors`` lists the
people who have answered in this project, whom Import offers beside "Agent-generated".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, register
from ingence.review.question_pack import question_pack

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["ImportAuthors", "QuestionPack"]

#: The most questions one file holds unless the page asks for fewer.
PACK_LIMIT = 200


def _questions(context: V1Context, limit: int) -> list[dict[str, Any]]:
    from ingence.api.routes import pages
    from ingence.storage.catalog import open_catalog

    with open_catalog(context.project, run_id=context.run_pinned) as catalog:
        page = pages.page(catalog, context.project, "questions", limit=limit)
    rows = list(page.get("rows", []))
    for row in rows:
        row.setdefault("names", row.get("examples") or row.get("files") or [])
        # A proposal is asked on its own target, with its own words. A vocabulary pack, a person's
        # miscellaneous files and a group's words are answered on pack:, miscellaneous: and group:
        # targets, which the engine reads. An answer on the question's target would be written
        # where nothing reads it.
        proposed = row.get("proposal")
        if isinstance(proposed, dict) and proposed.get("target"):
            row["target"] = str(proposed["target"])
            row["words"] = list(proposed.get("words") or [])
    return rows


def _limit(query: Mapping[str, Sequence[str]]) -> int:
    raw = (query.get("limit") or [str(PACK_LIMIT)])[0]
    try:
        value = int(raw)
    except ValueError as error:
        message = f"limit is a whole number of questions, not {raw!r}"
        raise ApiError(message, parameter="limit") from error
    if not 1 <= value <= 1000:
        message = "limit is between 1 and 1000 questions"
        raise ApiError(message, parameter="limit")
    return value


@register("question_pack")
class QuestionPack(ApiModel):
    """The questions as one file: how to answer, the questions with their evidence, the answers."""

    format: str
    instructions: str
    project: str
    run: dict[str, str]
    questions: list[dict[str, Any]]
    answers: dict[str, Any]
    #: Said before exporting: what the file carries off this machine if the person sends it on.
    carries: str


@route("questions/pack", model=QuestionPack)
def pack(context: V1Context, query: Mapping[str, Sequence[str]]) -> QuestionPack:
    if context.run_pinned is None:
        raise ApiError("no published run to ask about", code="no_published_run")
    built = question_pack(
        _questions(context, _limit(query)),
        project_name=context.project.name,
        run_label=context.run_pinned,
        run_id=context.run_pinned,
    )
    return QuestionPack(
        **built,
        carries=(
            "It holds this project's file and folder names and nothing else: no file contents, "
            "no path above the project's roots. Whoever you send it to reads those names."
        ),
    )


@register("import_authors")
class ImportAuthors(ApiModel):
    """Whom Import offers as a file's author: the people who have answered in this project."""

    authors: list[str]


@route("authors", model=ImportAuthors)
def authors(context: V1Context, query: Mapping[str, Sequence[str]]) -> ImportAuthors:
    from ingence.record.imported import record_authors

    del query
    return ImportAuthors(authors=record_authors(context.project))
