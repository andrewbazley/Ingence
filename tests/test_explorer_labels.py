"""A person's display labels: annotations in the Record, never a name, never evidence.

On a copy of the explorer fixture (``fixtures/explorer_project.py``, invented names), served as a
person runs the Dashboard, so the one write route and its guard are what is exercised.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


@pytest.fixture
def served(explorer, tmp_path) -> Iterator[tuple[Path, str]]:
    from ingence.api.server import DashboardServer

    copy = tmp_path / "project"
    shutil.copytree(explorer.project, copy)
    server = DashboardServer(copy, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        yield copy, server.url.rstrip("/")
    finally:
        server.stop()


def _post(base: str, body: dict[str, Any], *, guarded: bool = True) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if guarded:
        headers["X-Ingence-Write"] = "1"
    request = urllib.request.Request(
        f"{base}/api/review/answer", data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _get(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}{path}", timeout=30) as response:
        return json.loads(response.read())


def _label_events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return [
        event for event in read_events(project, held=False) if event.target.startswith("label:")
    ]


def test_a_label_is_an_attributed_annotation_and_the_name_is_unchanged(explorer, served) -> None:
    project, base = served
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    entry = explorer.ids[summary]
    status, body = _post(
        base, {"actor": "a. person", "labels": [{"entry_id": entry, "label": "the kelt summary"}]}
    )
    assert status == HTTPStatus.CREATED, body
    assert body["written"] == 1
    events = _label_events(project)
    assert len(events) == 1
    assert events[0].kind.value == "annotate"
    assert events[0].actor == "a. person"
    assert json.loads(events[0].payload)["label"] == "the kelt summary"
    listed = _get(base, "/api/v1/explorer/labels")
    assert [(item["entry_id"], item["label"]) for item in listed["labels"]] == [
        (entry, "the kelt summary")
    ]
    assert listed["labels"][0]["actor"] == "a. person"
    # The entry's own name, everywhere, is the name on disk.
    card = _get(base, f"/api/v1/explorer/entry?id={entry}")
    assert card["name"] == summary.rsplit("/", 1)[-1]
    lineage = _get(base, f"/api/v1/explorer/lineage?focus={entry}")
    assert next(node for node in lineage["nodes"] if node["id"] == entry)["name"] == card["name"]


def test_a_write_without_the_guard_is_refused_and_nothing_is_written(explorer, served) -> None:
    project, base = served
    entry = next(iter(explorer.ids.values()))
    status, _ = _post(
        base, {"actor": "a. person", "labels": [{"entry_id": entry, "label": "x"}]}, guarded=False
    )
    assert status == HTTPStatus.FORBIDDEN
    assert _label_events(project) == []


def test_a_batch_is_checked_whole_before_anything_is_written(explorer, served) -> None:
    project, base = served
    good = explorer.ids[explorer.experiments[0]]
    for batch in (
        [{"entry_id": good, "label": "fine"}, {"entry_id": "entry_nothing", "label": "no such"}],
        [{"entry_id": good, "label": "fine"}, {"entry_id": good, "label": "twice"}],
        [{"entry_id": good, "label": "   "}],
        [{"entry_id": good, "label": "x" * 201}],
        [{"entry_id": good, "label": "bell\u0007"}],
        # A label's note is checked as the layer's notes are.
        [{"entry_id": good, "label": "fine", "note": "x" * 401}],
        [{"entry_id": good, "label": "fine", "note": "a line\u0000and another"}],
        [{"entry_id": good, "label": "fine", "note": ["not", "text"]}],
    ):
        status, body = _post(base, {"actor": "a. person", "labels": batch})
        assert status == HTTPStatus.BAD_REQUEST, (batch, body)
        assert body["error"]
    status, body = _post(base, {"labels": [{"entry_id": good, "label": "no actor"}]})
    assert status == HTTPStatus.BAD_REQUEST
    assert _label_events(project) == []


def test_a_label_s_author_is_one_person_however_the_name_is_padded(explorer, served) -> None:
    """The label write reads its author as every other write does."""
    project, base = served
    first, second = (explorer.ids[folder] for folder in explorer.experiments[:2])
    for actor, entry in (("a. person", first), ("  a. person ", second)):
        status, body = _post(
            base,
            {"actor": actor, "labels": [{"entry_id": entry, "label": "x", "note": " a note "}]},
        )
        assert status == HTTPStatus.CREATED, body
    events = _label_events(project)
    assert {event.actor for event in events} == {"a. person"}
    assert {json.loads(event.payload)["note"] for event in events} == {"a note"}


def test_removing_a_label_keeps_its_history(explorer, served) -> None:
    project, base = served
    entry = explorer.ids[explorer.experiments[1]]
    for label in ("first words", "better words", None):
        status, _ = _post(
            base, {"actor": "a. person", "labels": [{"entry_id": entry, "label": label}]}
        )
        assert status == HTTPStatus.CREATED
    listed = _get(base, "/api/v1/explorer/labels")
    assert entry not in {item["entry_id"] for item in listed["labels"]}
    history = _get(base, f"/api/v1/explorer/labels?id={entry}")["history"]
    assert [item["label"] for item in history] == ["first words", "better words", None]
    assert len(_label_events(project)) == 3


def test_a_person_s_layer_is_never_read_by_inference_and_survives_a_new_run(
    explorer, served, tmp_path
) -> None:
    """Labels, a group, an exclusion and a proposed relation, then an inference: its claims,
    assertions and findings are exactly those of the same inference over a copy that holds none
    of them."""
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.storage.catalog import open_catalog

    project, base = served
    control = tmp_path / "control"
    shutil.copytree(explorer.project, control)
    labelled = [explorer.ids[path] for path in sorted(explorer.ids)[:5]]
    first, second, _ = explorer.experiments
    raw, mask = explorer.derivations[0]
    scratch = next(path for path in explorer.ids if path.startswith("scratch/"))
    status, body = _post(
        base,
        {
            "actor": "a. person",
            "labels": [{"entry_id": e, "label": f"label {n}"} for n, e in enumerate(labelled)],
            "overlay": [
                {
                    "kind": "group",
                    "name": "the pair",
                    "members": [explorer.ids[first], explorer.ids[second]],
                },
                {"kind": "exclude", "entry": explorer.ids[scratch], "reason": "scratch"},
                {
                    "kind": "relate",
                    "source": explorer.ids[mask],
                    "target": explorer.ids[raw],
                    "relation": "derives",
                },
            ],
        },
    )
    assert status == HTTPStatus.CREATED, body
    assert body["written"] == len(labelled) + 3

    def published(where: Path) -> dict[str, list[tuple[Any, ...]]]:
        with open_catalog(where) as catalog:
            return {
                "claims": catalog.query(
                    "SELECT subject, predicate, object_reference, rule_id, rule_version, "
                    "inference_tier, resolution_status FROM claims ORDER BY ALL"
                ).fetchall(),
                "assertions": catalog.query(
                    "SELECT source_id, target_id, primitive, rule_id, rule_version, "
                    "decision_status FROM assertions ORDER BY ALL"
                ).fetchall(),
                "findings": catalog.query(
                    "SELECT rule_id, rule_version, subject, raised, inputs, reason "
                    "FROM findings ORDER BY ALL"
                ).fetchall(),
            }

    again = "run_20260101T000009Z_eeeeeeeeeeee"
    run_inference(Project.open(project), run_id=again, started_at="t")
    run_inference(Project.open(control), run_id=again, started_at="t")
    with_layer, without = published(project), published(control)
    assert all(without[table] for table in ("claims", "findings")), without.keys()
    for table in ("claims", "assertions", "findings"):
        assert with_layer[table] == without[table], table
    listed = _get(base, "/api/v1/explorer/labels")
    assert {item["entry_id"] for item in listed["labels"]} == set(labelled)


def test_the_tabs_counts_are_kept_until_the_run_or_the_record_changes(
    explorer, served, monkeypatch
) -> None:
    """Every page asks for the tabs' counts on every load; they are counted once per run and Record
    state, and again as soon as either changes."""
    from ingence.api.routes import pages as page_routes

    _, base = served
    calls: list[int] = []
    original = page_routes.waiting

    def counting(catalog: Any, project: Any) -> dict[str, int]:
        calls.append(1)
        return original(catalog, project)

    monkeypatch.setattr(page_routes, "waiting", counting)
    # The server began counting as it started: let that count finish, and count from here on what
    # the requests cause.
    from ingence.api import server as server_module

    assert server_module.waiting_settled(60)
    server_module._WAITING.clear()
    calls.clear()
    first = _counted(base)
    second = _get(base, "/api/waiting")
    assert first == second
    assert len(calls) == 1
    entry = explorer.ids[explorer.experiments[0]]
    status, _ = _post(
        base, {"actor": "a. person", "labels": [{"entry_id": entry, "label": "day one"}]}
    )
    assert status == HTTPStatus.CREATED
    _counted(base)
    assert len(calls) == 2


def _counted(base: str) -> dict[str, Any]:
    """The tabs' counts once they have been made.

    A request waits a moment for a count being made and then answers "counting" (the page asks
    again). On a machine under load the count can outlast that moment, so the test waits for the
    count to be made, by the server's own word that none is, and asks again: it never waits for a
    time.
    """
    from ingence.api import server as server_module

    answer = _get(base, "/api/waiting")
    for _ in range(20):
        if not answer.get("counting"):
            break
        assert server_module.waiting_settled(120)
        answer = _get(base, "/api/waiting")
    return answer
