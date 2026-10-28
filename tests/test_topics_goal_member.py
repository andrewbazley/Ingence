"""A topic's goal member: a goal added to a topic is followed by its words every run.

It resolves to the experiments in the goal's top tier. A goal since removed resolves to nothing, in
words. Every name is invented, and the pool is the themes' planted one.
"""

from __future__ import annotations

import pytest

from ingence.api.routes.overlay import (
    MEMBER_KINDS,
    OverlayRefusedError,
    goal_key,
    prepare_overlay,
    topics_standing,
    write_prepared,
)
from ingence.api.v1 import context_for, dispatch
from ingence.goals import remove_goal, write_goal
from ingence.topics import resolve


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("goal-member"))


def _carrying(built, *words: str) -> set[str]:
    from fixtures.theme_pool import ENGINE_SURFACES

    wanted = {ENGINE_SURFACES.get(word, word) for word in words}
    return {
        built.ids[unit]
        for unit, written in built.plant.documents.items()
        if unit in built.ids and wanted <= {ENGINE_SURFACES.get(w, w) for w in written}
    }


def test_a_goal_member_resolves_to_the_goal_s_named_experiments_every_run(built) -> None:
    assert "goal" in MEMBER_KINDS
    goal = write_goal(built.project, "keltan vornish", actor="a. person")
    prepared = prepare_overlay(
        built.project,
        actor="a. person",
        occurred_at="2026-01-02T00:00:00Z",
        edits=[
            {"kind": "group", "name": "Kelt line", "members": [{"kind": "goal", "id": goal.id}]}
        ],
    )
    write_prepared(built.project, prepared, actor="a. person", occurred_at="2026-01-02T00:00:00Z")
    (topic,) = [t for t in topics_standing(built.project) if t.name == "Kelt line"]
    (member,) = topic.members
    assert member.kind == "goal" and member.key == goal_key(goal.id) and member.goal_id == goal.id
    found = resolve(built.project, built.infer, suggestions=False)
    (reading,) = [r for r in found.topics if r.topic.topic_id == topic.topic_id]
    (read,) = reading.members
    assert read.state == "resolved" and set(read.experiments) == _carrying(
        built, "keltan", "vornish"
    )
    assert set(reading.experiments) == set(read.experiments)
    # The route says the member as a goal, by its identifier.
    status, body = dispatch(
        context_for(built.project, settled=None, job_running=False), "topics", {}
    )
    assert status == 200, body
    (item,) = [t for t in body["topics"] if t["id"] == topic.topic_id]
    (served,) = item["members"]
    assert served["kind"] == "goal" and served["name"] == goal.id and served["state"] == "resolved"
    # Sent back as the overlay served it, the member carries over; removed by its key, it leaves.
    prepared = prepare_overlay(
        built.project,
        actor="a. person",
        occurred_at="2026-01-02T00:01:00Z",
        edits=[
            {
                "kind": "group",
                "id": topic.topic_id,
                "name": "Kelt line",
                "members": [goal_key(goal.id)],
            }
        ],
    )
    write_prepared(built.project, prepared, actor="a. person", occurred_at="2026-01-02T00:01:00Z")
    (topic,) = [t for t in topics_standing(built.project) if t.name == "Kelt line"]
    assert [m.kind for m in topic.members] == ["goal"]
    # A goal removed resolves to nothing, in words; the member stays on the topic.
    remove_goal(built.project, goal.id)
    found = resolve(built.project, built.infer, suggestions=False)
    (reading,) = [r for r in found.topics if r.topic.topic_id == topic.topic_id]
    (read,) = reading.members
    assert read.state == "nothing" and "no goal" in (read.why or "")


def test_a_goal_member_names_its_goal_by_identifier(built) -> None:
    with pytest.raises(OverlayRefusedError, match="identifier"):
        prepare_overlay(
            built.project,
            actor="a. person",
            occurred_at="2026-01-02T00:02:00Z",
            edits=[{"kind": "group", "name": "Bad", "members": [{"kind": "goal", "id": "nope"}]}],
        )
