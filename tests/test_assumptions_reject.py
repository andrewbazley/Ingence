"""Rejecting an assumption: one Record answer on the class handle, through the answer route's
writer, that waits for Re-run on the one list of what waits. Every name is invented. The pool is
the themes' planted one."""

from __future__ import annotations

import json
import urllib.request

import pytest

from ingence.api.routes import controls
from ingence.api.routes.assumptions_reject import REJECT_WORD, class_target_of, reject
from ingence.api.server import DashboardServer
from ingence.record.log import read_events
from ingence.record.targets import class_target
from ingence.storage.catalog import open_catalog
from ingence.workspace.state import changes_since

GUARD = {"Content-Type": "application/json", "X-Ingence-Write": "1"}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("reject"))


def _largest_class(project) -> tuple[str, str, int]:
    with open_catalog(project) as catalog:
        rule, value, count = catalog.query(
            "SELECT rule_id, object_reference, count(*) FROM claims "
            "WHERE predicate LIKE 'classifies.%' AND object_reference IS NOT NULL "
            "AND resolution_status = 'resolved' GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT 1"
        ).fetchone()
    return str(rule), str(value), int(count)


def test_a_handle_names_its_class_and_an_unknown_identifier_names_none(built) -> None:
    rule, value, _ = _largest_class(built.project)
    target = class_target(rule, value)
    assert class_target_of(built.project, target) == target
    assert class_target_of(built.project, "asm_nothing_here") is None


def test_rejecting_writes_one_answer_on_the_class_that_waits_for_rerun(built) -> None:
    rule, value, _ = _largest_class(built.project)
    target = class_target(rule, value)
    before = len(read_events(built.project))
    answered = reject(built.project, target, actor="a. person", note="not this")
    assert answered["recorded"] is True and answered["waits_for_rerun"] is True
    assert answered["answer"]["word"] == REJECT_WORD and answered["answer"]["target"] == target
    assert answered["answer"]["rule_id"] == rule and answered["answer"]["value"] == value
    assert answered["kind"].startswith("classifies.")
    events = read_events(built.project)
    assert len(events) == before + 1 and events[-1].target == target
    assert json.loads(events[-1].payload or "{}").get("verdict") == REJECT_WORD
    waiting = [change for change in changes_since(built.project) if change.kind == "answer"]
    assert waiting and any("Wrong" in row for change in waiting for row in change.rows)


def test_a_rejection_by_nobody_or_of_nothing_is_refused_in_words(built) -> None:
    rule, value, _ = _largest_class(built.project)
    with pytest.raises(ValueError, match="actor"):
        reject(built.project, class_target(rule, value), actor=None)
    with pytest.raises(ValueError, match="names no assumption"):
        reject(built.project, "asm_nothing_here", actor="a. person")
    with pytest.raises(ValueError, match="no class"):
        reject(built.project, class_target(rule, "no_such_value"), actor="a. person")


def test_a_proven_class_is_refused_in_words_and_nothing_is_recorded(built, monkeypatch) -> None:
    """A class on a tier an answer cannot retract (entailed) is not rejected: the route says so
    in words and writes nothing."""
    from dataclasses import replace

    from ingence.review import claim_classes

    rule, value, _ = _largest_class(built.project)
    target = class_target(rule, value)
    proven = claim_classes.class_named_by
    with open_catalog(built.project) as catalog:
        held = proven(catalog, target)
    assert held is not None
    monkeypatch.setattr(
        claim_classes, "class_named_by", lambda catalog, target: replace(held, retractable=False)
    )
    before = len(read_events(built.project))
    with pytest.raises(ValueError, match="proven"):
        reject(built.project, target, actor="a. person")
    assert len(read_events(built.project)) == before


def test_the_control_route_answers_the_contract_s_shape(built) -> None:
    rule, value, _ = _largest_class(built.project)
    server = DashboardServer(built.project, port=0)
    server.start()
    try:
        request = urllib.request.Request(
            server.url.rstrip("/") + controls.ASSUMPTION_REJECT_ROUTE,
            data=json.dumps({"id": class_target(rule, value), "actor": "a. person"}).encode(),
            headers=GUARD,
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 201
            payload = json.loads(response.read())
    finally:
        server.stop()
    assert set(payload) == {"id", "kind", "answer", "waits_for_rerun", "said", "recorded"}
    assert payload["recorded"] is True and payload["said"].endswith(".")


def test_a_rejection_whose_body_names_only_the_assumption_is_the_decider_s(built) -> None:
    """The assumptions route's ``reject.body`` is ``{id}`` alone: posted as a page sends it, the
    answer is the server's decider's, as ``/api/workflows`` takes it; with no decider either,
    it is refused in the one sentence every control says."""
    rule, value, _ = _largest_class(built.project)
    target = class_target(rule, value)
    before = len(read_events(built.project))
    known = controls.Context(project=built.project, decider="a. decider")
    answered = controls.write(controls.ASSUMPTION_REJECT_ROUTE, {"id": target}, context=known)
    assert answered["recorded"] is True
    events = read_events(built.project)
    assert len(events) == before + 1 and events[-1].actor == "a. decider"
    nobody = controls.Context(project=built.project)
    with pytest.raises(ValueError, match="Set your name first"):
        controls.write(controls.ASSUMPTION_REJECT_ROUTE, {"id": target}, context=nobody)
    assert len(read_events(built.project)) == before + 1
    assert controls.ACTOR_REFUSAL == "Ingence needs your name to save this. Set your name first."
