"""``POST /api/assumptions/reject``: the Record answer that reverses an assumption.

An assumption the run rests on is a class of claims that one rule made with one value. The
assumptions readout lists them. Rejecting one writes one Record event on the class handle
(``record/targets.class_target``), through the writer of the one answer route
(``api/routes/decide.decide_target``), with the word ``wrong``. The next inference publishes each
member's reading as conflicting, defeated by the answer. A file that joins the class later is read
the same way. The rejection waits for Re-run like every other answer: the event joins the one list
of what waits (``workspace/state.changes_since``) by being a Record answer, and nothing here
applies it.

:func:`class_target_of` finds the class of an assumption's identifier. The identifier is the
handle itself (``class:<rule>:<digest>``), which is what the assumptions readout gives as each
item's ``id``. An identifier that names no class in the pinned run is refused in words.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ingence.record.targets import CLASS_PREFIX

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["REJECT_WORD", "class_target_of", "reject"]

#: The verdict word a rejection writes on the class: the claims are wrong.
REJECT_WORD = "wrong"


def class_target_of(project: Path, assumption_id: str) -> str | None:
    """The class handle an assumption's identifier names, or None.

    A handle given as itself is taken as it is. Otherwise the assumptions readout is asked, where
    it exposes ``class_target_of``.
    """
    if assumption_id.startswith(f"{CLASS_PREFIX}:"):
        return assumption_id
    try:
        from ingence.api.v1 import assumptions as readout
    except ImportError:  # pragma: no cover - the readout is part of this build
        return None
    resolve = getattr(readout, "class_target_of", None)
    if callable(resolve):
        found = resolve(project, assumption_id)
        return str(found) if found else None
    return None


def reject(
    project: Path, assumption_id: str, *, actor: str | None, note: str | None = None
) -> dict[str, Any]:
    """Write the rejection; refused in words (:class:`ValueError`) where nothing can be written."""
    from ingence.api.routes import decide as decide_routes
    from ingence.record import log as record_log
    from ingence.review.claim_classes import class_named_by
    from ingence.storage.catalog import open_catalog

    if not actor:
        message = "rejecting an assumption states actor: an answer nobody gave is not attributable"
        raise ValueError(message)
    with open_catalog(project) as catalog:
        if not catalog.has_run:
            message = "this project has no published run yet, so it rests on no assumption"
            raise ValueError(message)
        target = class_target_of(project, assumption_id)
        if target is None:
            message = (
                f"{assumption_id!r} names no assumption of this run: an assumption is rejected "
                "by its identifier from the assumptions list, or by its class handle"
            )
            raise ValueError(message)
        held = class_named_by(catalog, target)
        if held is None:
            message = f"the run holds no class of conclusions at {target!r}"
            raise ValueError(message)
        if not held.retractable:
            message = (
                f"the readings of {held.rule_id} as {held.value!r} are proven, not supposed: "
                "the rule's level does not let an answer retract them, so there is nothing to "
                "reject; nothing was recorded"
            )
            raise ValueError(message)
        context = decide_routes.DecisionContext(catalog, project)
        decision = decide_routes.Decision(target=target, word=REJECT_WORD, note=note)
        try:
            event = decide_routes.decide_target(
                context, decision, actor=actor, occurred_at=record_log.now()
            )
        except decide_routes.AnswerRefusedError as refused:
            raise ValueError(str(refused)) from refused
    try:
        payload = json.loads(event.payload or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": assumption_id,
        "kind": held.predicate,
        "answer": {
            "target": target,
            "word": REJECT_WORD,
            "event_id": event.event_id,
            "rule_id": held.rule_id,
            "value": held.value,
            "files": held.size,
            **({"verdict": payload.get("verdict")} if isinstance(payload, dict) else {}),
        },
        "waits_for_rerun": True,
        "said": (
            f"Recorded on the class: {held.size:,} readings by {held.rule_id} as "
            f"{held.value!r} are rejected. They change at the next Re-run."
        ),
        "recorded": True,
    }
