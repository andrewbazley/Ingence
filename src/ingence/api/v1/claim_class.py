"""``GET /api/v1/claims/class``: a claim's class handle and the class's summary.

When a person marks a file's claim as wrong, the page shows the claim's class before the person
commits. A class is every claim that one rule made with one value. Its handle is the Record's class
target (``record/targets.class_target``). The summary gives the rule, the value, how many claims
the class holds and three examples drawn from the handle (``review/claim_classes.py``).

The answer is written on the handle through the one answer route (``api/routes/decide.py``). It is
one Record event on the class, never one per file, and the next inference reads it for every member
(``infer/answered.ClassRefusals``). Nothing here writes.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import one, pinned_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["ClaimClassResponse", "reads_for"]


def reads_for(*, retractable: bool) -> str:
    """What an answer on the handle does, said beside the words a person is offered."""
    if not retractable:
        return (
            "This reading is proven, not supposed: the rule's level does not let an answer retract "
            "it. An answer here is recorded, once, on the class, but the reading is not changed, "
            "and nothing waits for a re-run."
        )
    return (
        "An answer here is written once, on the class: it is about every file this rule read "
        "with this value, outside installed programs. Wrong: at the next re-run each of their "
        "readings is published as conflicting, defeated by your answer, and never removed; a file "
        "that joins the class later is read the same way and says so. Correct: they stand, "
        "answered."
    )


class ClassWord(ApiModel):
    word: str
    label: str


class ClassDecision(ApiModel):
    """The decision in force on the class: its word, who said it and when."""

    word: str
    kind: str
    actor: str
    occurred_at: str
    event_id: Identifier


@register("claim_class")
class ClaimClassResponse(ApiModel):
    """``GET /api/v1/claims/class?claim=``: the class one claim belongs to, before an answer."""

    schema_version: str
    run_id: Identifier
    claim_id: Identifier
    #: The class handle: the Record target an answer on the class is written on.
    handle: str
    rule_id: str
    rule_version: str
    predicate: str
    value: str
    #: How many files the class holds: every claim the rule made with this value in the run.
    size: Count
    #: Three of them, drawn from the handle: the same three for every claim of the class.
    examples: list[str]
    words: list[ClassWord]
    in_force: ClassDecision | None
    #: Whether the claim asked about is itself in the class (the re-read's finding 2): a file
    #: inside an installed program, or one another defeater already marks, is not, and an answer
    #: on the class leaves it as it is.
    member: bool
    #: Why it is not, in plain words; None where it is.
    outside: str | None
    #: Whether an answer can retract the class's readings: false for a proven (entailed) reading,
    #: which an answer is recorded about and never changes.
    retractable: bool
    reads: str


@route("claims/class", model=ClaimClassResponse)
def claim_class_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> ClaimClassResponse:
    from ingence.record.log import latest_by_target, read_events
    from ingence.record.verdicts import CLAIM_WORDS, shown_word
    from ingence.review.claim_classes import claim_class, outside_why

    named = (one(query, "claim") or "").strip()
    if not named:
        message = "name the conclusion by its identifier: claim=<claim id>"
        raise ApiError(message, parameter="claim")
    with pinned_catalog(context) as (catalog, run_id):
        held = claim_class(catalog, named)
        outside = outside_why(catalog, named) if held is not None else None
    if held is None:
        message = f"the run holds no classifying conclusion {named!r} with a value"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="claim")
    event = latest_by_target(read_events(context.project)).get(held.target)
    decision = None
    if event is not None:
        try:
            said = json.loads(event.payload or "{}")
        except (TypeError, ValueError):
            said = {}
        decision = ClassDecision(
            word=str((said if isinstance(said, dict) else {}).get("verdict") or ""),
            kind=event.kind.value,
            actor=event.actor,
            occurred_at=event.occurred_at,
            event_id=event.event_id,
        )
    return ClaimClassResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        claim_id=named,
        handle=held.target,
        rule_id=held.rule_id,
        rule_version=held.rule_version,
        predicate=held.predicate,
        value=held.value,
        size=held.size,
        examples=list(held.examples),
        words=[ClassWord(word=word, label=shown_word(word)) for word in CLAIM_WORDS],
        in_force=decision,
        member=outside is None,
        outside=outside,
        retractable=held.retractable,
        reads=reads_for(retractable=held.retractable),
    )
