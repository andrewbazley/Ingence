"""``GET /api/v1/questions/sheet``: the question sheet, as the engine's own output.

``review/question_sheet.py`` builds the sheet from the pinned run, one sheet per project. Each
question has its kind, the class it settles (rule, value, how many), three examples drawn from the
class's key, and how many claims or pairs one answer settles. For a pattern of derivations, it also
has the name pattern with its varying parts marked. A summary says what answering the whole sheet
settles.

The sheet's cuts are parameters that a request may name (``budget``, ``tied``). Each defaults to
the figure in the project's ``questions`` settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, pinned_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.storage.catalog import Catalog

__all__ = [
    "SHEET_MAXIMUM",
    "QuestionSheetResponse",
    "built_sheet",
    "sheet_page_numbers",
    "whole_sheet",
]


class SheetSense(ApiModel):
    """One meaning the Dictionary knows for a tied word, as the question offers it."""

    term: str
    #: The kind of meaning it is (reagent, technique, instrument and so on), where it has one.
    theme_kind: str | None


class SheetAnswer(ApiModel):
    """An answer given since the run, which stays on its question until **Re-run** reads it."""

    word: str
    #: The word as a button says it (``cannot_say`` is "Can't say").
    label: str
    actor: str
    occurred_at: str
    #: Its place in the Record; above the run's watermark, which is why it waits.
    sequence: Count
    note: str | None
    #: Always true here: an answer the run read leaves the sheet.
    waits_for_rerun: bool


class SheetQuestion(ApiModel):
    #: Its place on the sheet, from 1.
    n: Count
    kind: Literal["pack", "tied word", "audit", "derivation pattern", "atypical pair"]
    question: str
    #: What one answer settles, and how many of them.
    settles: str
    settles_n: Count
    #: Three examples of the class, drawn from its key: the same three every time for one run.
    #: Paths relative to the root, as an audit's are, for every kind that has files: a pack's are
    #: files whose names carry a word it would read, a tied word's files whose names hold it.
    examples: list[str]
    #: The class key: the Record target an answer is written on where there is one, else the
    #: pattern's own spelling.
    key: str
    #: Where an answer is written (``pack:``, ``question:``, ``class:``). None for a pattern, whose
    #: pairs are answered one by one.
    target: str | None
    rule_id: str | None
    #: The value the class is about: a pack's domain, a word, a rule's concluded value, a pattern.
    value: str | None
    #: The class's size: files, claims or pairs.
    size: Count
    #: A pattern's made-file name, ``{src}`` for the source's stem and ``#`` for each number.
    template: str | None
    #: What the question rests on, in plain words: the rule's own sentence, and for a pattern
    #: what ``{src}`` and ``#`` stand for.
    meaning: str
    #: For an audit: whether an answer can retract the class's readings; false for a proven
    #: reading, which an answer is recorded about and never changes. None for any other kind.
    retractable: bool | None
    #: The version of the rule the question rests on, in this run; None where it rests on none.
    rule_version: str | None = None
    #: For a tied word: its meanings, in the order they are offered; None for any other kind.
    senses: list[SheetSense] | None = None
    #: The answer given since the run, waiting for Re-run, or None. A question answered stays on
    #: the sheet with it until the next run reads it, then leaves.
    answer: SheetAnswer | None = None
    #: The top-level folders every file of a class of conclusions or a pattern of pairs lies in, not
    #: only the examples: what a folder filter reads. Empty for a question whose files the
    #: Questions page's queue already counts (a pack, a tied word).
    tops: list[str] = Field(default_factory=list)


class SheetSummary(ApiModel):
    questions: Count
    #: How many of them are answered and waiting for Re-run.
    answered_waiting: Count = 0
    packs: Count
    tied: Count
    audits: Count
    patterns: Count
    atypical: Count
    classifying_claims: Count
    claims_settled: Count
    claims_settled_share: float
    audit_classes_total: Count
    classes_for_95pc: Count
    pairs: Count
    pairs_settled: Count
    pairs_settled_share: float
    patterns_total: Count
    patterns_for_90pc: Count


@register("question_sheet")
class QuestionSheetResponse(ApiModel):
    """``GET /api/v1/questions/sheet``: one project's question sheet and what it settles."""

    schema_version: str
    run_id: Identifier
    questions: list[SheetQuestion]
    summary: SheetSummary


#: The kinds whose question rests on a rule's reading, said beside it in the rule's own sentence.
_RULE_READINGS = frozenset({"audit", "derivation pattern", "atypical pair"})


def _worded(row: dict[str, object]) -> dict[str, object]:
    """A sheet row with the rule's plain sentence beside it.

    The words a person reads come from the one table (``api/plain_words.yaml``), never a rule's
    identifier.
    """
    from ingence.api.plain_words import rule_sentence

    if row.get("kind") not in _RULE_READINGS or not row.get("rule_id"):
        return row
    said = rule_sentence(str(row["rule_id"])) or ""
    return {**row, "meaning": " ".join(part for part in (str(row["meaning"]), said) if part)}


def built_sheet(
    context: V1Context, query: Mapping[str, Sequence[str]], catalog: Catalog
) -> dict[str, Any]:
    """The sheet of the run ``catalog`` reads, with the project's own cuts (and a request's).

    The cuts are the project's own, as the project reads them: its settings file and what the
    Dashboard committed beside it. Each default comes from those settings, and a request may narrow
    the budget and the tied words. ``questions/ranked`` shares this function and ranks the sheet's
    questions among the others.
    """
    from ingence.core.project import Project
    from ingence.review.question_sheet import question_sheet

    held = Project.open(context.project).settings.questions
    parameters = held.model_copy(
        update={
            "sheet_budget": integer(query, "budget", default=held.sheet_budget, low=1, high=10_000),
            "sheet_tied_words": integer(
                query, "tied", default=held.sheet_tied_words, low=0, high=10_000
            ),
        }
    )
    return question_sheet(catalog, context.project, parameters)


#: The most the settings let the sheet hold: its budget, its tied words and its atypical pairs each
#: take up to this many (``QuestionSettings``' own upper bound).
SHEET_MAXIMUM = 10_000


def whole_sheet(context: V1Context, catalog: Catalog) -> dict[str, Any]:
    """Every question the sheet can ask, with no budget: the budget, the tied words and the atypical
    pairs each at the settings' maximum (:data:`SHEET_MAXIMUM`).

    For ``questions/ranked``, which ranks every question and so cannot take the Sheet page's cut
    of the first ``sheet_budget``. The order is the sheet's own, so what the Sheet page shows is
    this order with its tied words and atypical pairs cut to the project's own numbers, then
    cut to its budget.
    """
    from ingence.core.project import Project
    from ingence.review.question_sheet import question_sheet

    held = Project.open(context.project).settings.questions
    largest = held.model_copy(
        update={
            "sheet_budget": SHEET_MAXIMUM,
            "sheet_tied_words": SHEET_MAXIMUM,
            "atypical_most": SHEET_MAXIMUM,
        }
    )
    return question_sheet(catalog, context.project, largest)


def sheet_page_numbers(context: V1Context, whole: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Each question of the whole sheet that the Sheet page shows, by its key, with its number
    there: the project's own tied words, atypical pairs and budget applied to the whole order."""
    from ingence.core.project import Project

    held = Project.open(context.project).settings.questions
    kept: list[Mapping[str, Any]] = []
    tied = atypical = 0
    for row in whole:
        if row["kind"] == "tied word":
            tied += 1
            if tied > held.sheet_tied_words:
                continue
        elif row["kind"] == "atypical pair":
            atypical += 1
            if atypical > held.atypical_most:
                continue
        kept.append(row)
    return {str(row["key"]): number for number, row in enumerate(kept[: held.sheet_budget], 1)}


@route("questions/sheet", model=QuestionSheetResponse)
def question_sheet_route(
    context: V1Context, query: Mapping[str, Sequence[str]]
) -> QuestionSheetResponse:
    with pinned_catalog(context) as (catalog, run_id):
        built = built_sheet(context, query, catalog)
    return QuestionSheetResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        questions=[SheetQuestion(**_worded(row)) for row in built["questions"]],
        summary=SheetSummary(**built["summary"]),
    )
