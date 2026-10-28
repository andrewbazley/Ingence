"""``/api/v1/questions``, ``/api/v1/runs``, ``/api/v1/workspace`` and ``/api/v1/rules``, typed.

Each is built from the function that the older page reads (``api/routes/pages.py``'s questions
page, ``changes.py``'s runs, ``workspace.py``'s state and ``rules.py``'s rules in force) and
shaped field by field into its declared model. A review row's own fields vary by the kind of
question and are sent as the page layer made them (plain JSON). The parts a page branches on
(progress, filters, paging) are typed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from ingence import SCHEMA_VERSION
from ingence.api.routes import changes as change_routes
from ingence.api.routes import pages as page_routes
from ingence.api.routes import rules as rule_routes
from ingence.api.routes import workspace as workspace_routes
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, ZonedTime, register
from ingence.api.v1.shaping import (
    boolean,
    integer,
    json_value,
    one,
    pinned_catalog,
    text_or_none,
    zoned_or_none,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["QuestionsResponse", "RulesResponse", "RunsResponse", "WorkspaceResponse"]


# ---------------------------------------------------------------------------
# Questions.
# ---------------------------------------------------------------------------


class Progress(ApiModel):
    rows: Count
    answered: Count
    remaining: Count


class QuestionFilters(ApiModel):
    top: str | None
    rule: str | None
    tops: list[str]
    rules: list[str]


class Dimension(ApiModel):
    name: str
    direction: str
    meaning: str


@register("questions")
class QuestionsResponse(ApiModel):
    """``GET /api/v1/questions``: the Questions page, its progress, filters and one page of rows."""

    schema_version: str
    run_id: Identifier
    progress: Progress
    #: The same counts over the rows the filters keep.
    shown: Progress
    answered_shown: bool
    hidden_answered: Count
    filters: QuestionFilters
    #: Each question as the review layer made it; its fields vary by the kind of question.
    rows: list[dict[str, JsonValue]]
    offset: Count
    limit: Count
    dimensions: list[Dimension]
    order: list[str]
    theme_kinds: list[str]


def _progress(value: Any) -> Progress:
    held = value or {}
    return Progress(
        rows=int(held.get("rows") or 0),
        answered=int(held.get("answered") or 0),
        remaining=int(held.get("remaining") or 0),
    )


def _texts(values: Any) -> list[str]:
    return [str(value) for value in values or ()]


@route("questions", model=QuestionsResponse)
def questions_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> QuestionsResponse:
    limit = integer(query, "limit", default=page_routes.LIMIT, low=1, high=page_routes.LIMIT)
    offset = integer(query, "offset", default=0, low=0, high=10_000_000)
    answered = boolean(query, "answered", true_word="show")
    order_text = one(query, "order")
    order = [part for part in (order_text or "").split(",") if part] or None
    with pinned_catalog(context) as (catalog, run_id):
        found = page_routes.page(
            catalog,
            context.project,
            "questions",
            top=one(query, "top"),
            rule=one(query, "rule"),
            order=order,
            limit=limit,
            offset=offset,
            answered=answered,
        )
    filters = found.get("filters") or {}
    rows = json_value(found.get("rows") or [])
    return QuestionsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        progress=_progress(found.get("progress")),
        shown=_progress(found.get("shown")),
        answered_shown=bool(found.get("answered_shown")),
        hidden_answered=int(found.get("hidden_answered") or 0),
        filters=QuestionFilters(
            top=text_or_none(filters.get("top")),
            rule=text_or_none(filters.get("rule")),
            tops=_texts(filters.get("tops")),
            rules=_texts(filters.get("rules")),
        ),
        rows=[row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else [],
        offset=offset,
        limit=limit,
        dimensions=[
            Dimension(
                name=str(item.get("name") or ""),
                direction=str(item.get("direction") or ""),
                meaning=str(item.get("meaning") or ""),
            )
            for item in found.get("dimensions") or []
        ],
        order=_texts(found.get("order")),
        theme_kinds=_texts(found.get("theme_kinds")),
    )


# ---------------------------------------------------------------------------
# Runs.
# ---------------------------------------------------------------------------


class RunCounts(ApiModel):
    entries: Count
    claims: Count
    findings: Count
    candidates: Count


class RunItem(ApiModel):
    run_id: Identifier
    #: The run's number and name as a person reads them ("Run 3, September 23, 1:00 PM EDT").
    number: Count
    label: str
    #: When the run began, UTC, from the stamp its identifier leads with.
    began: ZonedTime | None
    parent_run_id: Identifier | None
    parent_label: str | None
    profile: str
    #: When the run began, where the run recorded a time with its zone.
    started_at: ZonedTime | None
    #: The start as the run recorded it, whatever it is.
    started_at_as_recorded: str | None
    inference: bool
    described: str
    current: bool
    counts: RunCounts


class RunPair(ApiModel):
    before: Identifier | None
    after: Identifier | None


@register("runs")
class RunsResponse(ApiModel):
    """``GET /api/v1/runs``: every published run, oldest first, and the pair a page compares."""

    schema_version: str
    runs: list[RunItem]
    current: Identifier | None
    #: The run every read is pinned to (the settled run while a job runs).
    pinned: Identifier | None
    pair: RunPair


@route("runs", model=RunsResponse)
def runs_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> RunsResponse:
    del query
    found = change_routes.runs(context.project)
    pair = found.get("pair") or {}
    return RunsResponse(
        schema_version=SCHEMA_VERSION,
        runs=[
            RunItem(
                run_id=str(item["run_id"]),
                number=int(item["number"]),
                label=str(item.get("label") or item["run_id"]),
                began=zoned_or_none(item.get("began")),
                parent_run_id=text_or_none(item.get("parent_run_id")),
                parent_label=text_or_none(item.get("parent_label")),
                profile=str(item.get("profile") or ""),
                started_at=zoned_or_none(item.get("started_at")),
                started_at_as_recorded=text_or_none(item.get("started_at")),
                inference=bool(item.get("inference")),
                described=str(item.get("described") or ""),
                current=bool(item.get("current")),
                counts=RunCounts(
                    entries=int((item.get("counts") or {}).get("entries") or 0),
                    claims=int((item.get("counts") or {}).get("claims") or 0),
                    findings=int((item.get("counts") or {}).get("findings") or 0),
                    candidates=int((item.get("counts") or {}).get("candidates") or 0),
                ),
            )
            for item in found.get("runs", [])
        ],
        current=text_or_none(found.get("current")),
        pinned=context.run_pinned,
        pair=RunPair(
            before=text_or_none(pair.get("before")), after=text_or_none(pair.get("after"))
        ),
    )


# ---------------------------------------------------------------------------
# The workspace.
# ---------------------------------------------------------------------------


@register("workspace")
class WorkspaceResponse(ApiModel):
    """``GET /api/v1/workspace``: what is edited and not committed, and what waits for a re-run.

    Whether a job is running is the handshake's (``/api/v1/version``), which every page reads.
    """

    schema_version: str
    #: Uncommitted edits, by kind (settings, rule parameters).
    uncommitted: dict[str, Count]
    pending_rerun: JsonValue
    unreadable_checkpoints: list[JsonValue]


@route("workspace", model=WorkspaceResponse)
def workspace_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> WorkspaceResponse:
    del query
    found = workspace_routes.read(context.project, ["state"])
    return WorkspaceResponse(
        schema_version=SCHEMA_VERSION,
        uncommitted={
            str(kind): int(count or 0)
            for kind, count in sorted((found.get("uncommitted") or {}).items())
        },
        pending_rerun=json_value(found.get("pending_rerun")),
        unreadable_checkpoints=[
            json_value(item) for item in found.get("unreadable_checkpoints") or []
        ],
    )


# ---------------------------------------------------------------------------
# The rules in force.
# ---------------------------------------------------------------------------


class RuleItem(ApiModel):
    rule_id: str
    version: str
    effect: str
    enabled: bool
    #: The rule in plain words, or its statement where no plain sentence is written.
    plain: str
    plain_from: str
    statement: str
    claims: Count
    findings: Count
    candidates: Count
    rests_on: Count


class RuleSection(ApiModel):
    effect: str
    heading: str
    rules: list[RuleItem]


@register("rules")
class RulesResponse(ApiModel):
    """``GET /api/v1/rules``: the rules the run carries, each in plain words, grouped by effect."""

    schema_version: str
    run_id: Identifier
    rules: Count
    sections: list[RuleSection]


@route("rules", model=RulesResponse)
def rules_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> RulesResponse:
    del query
    with pinned_catalog(context) as (catalog, run_id):
        found = rule_routes.rules(catalog)
    return RulesResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        rules=int(found.get("rules") or 0),
        sections=[
            RuleSection(
                effect=str(section["effect"]),
                heading=str(section["heading"]),
                rules=[
                    RuleItem(
                        rule_id=str(rule["rule_id"]),
                        version=str(rule["version"]),
                        effect=str(rule["effect"]),
                        enabled=bool(rule.get("enabled", True)),
                        plain=str(rule.get("plain") or ""),
                        plain_from=str(rule.get("plain_from") or ""),
                        statement=str(rule.get("statement") or ""),
                        claims=int(rule.get("claims") or 0),
                        findings=int(rule.get("findings") or 0),
                        candidates=int(rule.get("candidates") or 0),
                        rests_on=int(rule.get("rests_on") or 0),
                    )
                    for rule in section.get("rules", [])
                ],
            )
            for section in found.get("sections", [])
        ],
    )
