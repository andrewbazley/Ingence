"""One answer for several questions, through the Record's one way in.

``POST /api/review/answer`` takes ``{"actor", "targets": [...], "word", "note"}``. It is the same
code path that a list of ``decisions`` takes, with the answer's fields written once. The tests
check five things:

1. One event per target, each with the same word and note, under the actor named. The answer is
   one request.
2. Each target is refused on its own terms. A target the run does not hold is refused and the
   rest are written, as a list of decisions is.
3. A batch that names nothing, or names too much, is refused in words and writes nothing.
4. A target named twice is answered once.
5. The guard is the answer route's: no ``X-Ingence-Write`` header, no write.

Every name here is invented (``test_review_pages``' pool).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.server import ANSWER_ROUTE, BATCH_LIMIT, DashboardServer
from ingence.record.log import read_events

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[DashboardServer, Path]]:
    from fixtures.one_folder_pairs import allow_pairs_in_one_folder
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from test_review_pages import _pool

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    server = DashboardServer(project.path, port=0)
    server.start()
    try:
        yield server, project.path
    finally:
        server.stop()


def _post(
    server: DashboardServer, body: dict[str, Any], *, guarded: bool = True
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        server.url.rstrip("/") + ANSWER_ROUTE, method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    if guarded:
        request.add_header("X-Ingence-Write", "1")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.load(error)
        finally:
            error.close()


def _claim_targets(server: DashboardServer, count: int) -> list[str]:
    query = "api/pages/claims?limit=200&answered=show"
    with urllib.request.urlopen(server.url + query) as response:
        rows = json.load(response)["rows"]
    targets = [str(row["target"]) for row in rows if not row.get("grouped_by")]
    assert len(targets) >= count
    return targets[:count]


def test_one_request_answers_every_target_with_the_one_word_and_note(
    served: tuple[DashboardServer, Path],
) -> None:
    server, project = served
    targets = _claim_targets(server, 5)
    status, body = _post(
        server,
        {"actor": "Dana Whitfield", "targets": targets, "word": "correct", "note": "checked"},
    )
    assert status == 201, body
    assert body["refused"] == []
    assert [item["target"] for item in body["written"]] == targets
    events = [event for event in read_events(project) if event.target in targets]
    assert len(events) == 5
    assert {event.actor for event in events} == {"Dana Whitfield"}
    notes = {json.loads(event.payload)["note"] for event in events}
    assert notes == {"checked"}
    assert {json.loads(event.payload)["verdict"] for event in events} == {"correct"}


def test_a_target_the_run_does_not_hold_is_refused_and_the_rest_are_written(
    served: tuple[DashboardServer, Path],
) -> None:
    server, project = served
    targets = [*_claim_targets(server, 2), "claim:claim_not_in_this_run"]
    status, body = _post(
        server, {"actor": "Sam Okafor", "targets": targets, "word": "correct", "note": "n"}
    )
    assert status == 201, body
    assert len(body["written"]) == 2
    assert [item["target"] for item in body["refused"]] == ["claim:claim_not_in_this_run"]
    assert {event.target for event in read_events(project)} >= set(targets[:2])


def test_a_batch_that_names_nothing_or_too_much_is_refused_and_writes_nothing(
    served: tuple[DashboardServer, Path],
) -> None:
    server, project = served
    before = len(list(read_events(project)))
    for targets in ([], "claim:x", [""], [3], [f"claim:t{n}" for n in range(BATCH_LIMIT + 1)]):
        status, body = _post(server, {"actor": "Sam Okafor", "targets": targets, "word": "correct"})
        assert status == 400, (targets if len(str(targets)) < 40 else "too many", body)
        assert "target" in body["error"] or "batch" in body["error"]
    assert len(list(read_events(project))) == before


def test_a_target_named_twice_is_answered_once(served: tuple[DashboardServer, Path]) -> None:
    server, project = served
    (target,) = _claim_targets(server, 1)
    status, body = _post(
        server, {"actor": "Dana Whitfield", "targets": [target, target], "word": "correct"}
    )
    assert status == 201, body
    assert len(body["written"]) == 1
    assert len([event for event in read_events(project) if event.target == target]) == 1


def test_a_batch_answer_without_the_guard_header_is_refused(
    served: tuple[DashboardServer, Path],
) -> None:
    server, project = served
    (target,) = _claim_targets(server, 1)
    before = len(list(read_events(project)))
    status, _ = _post(
        server, {"actor": "Dana Whitfield", "targets": [target], "word": "correct"}, guarded=False
    )
    assert status == 403
    assert len(list(read_events(project))) == before


def test_a_batch_names_who_made_it(served: tuple[DashboardServer, Path]) -> None:
    server, _ = served
    (target,) = _claim_targets(server, 1)
    status, body = _post(server, {"targets": [target], "word": "correct"})
    assert status == 400
    assert "actor" in body["error"]


def test_a_batch_refused_whole_says_why_in_a_sentence(
    served: tuple[DashboardServer, Path],
) -> None:
    """Nothing was written, so the page has only a sentence to show: one reason for one row, the
    first of several otherwise, in words and never "Bad Request"."""
    server, project = served
    before = len(list(read_events(project)))
    status, body = _post(
        server,
        {"actor": "Sam Okafor", "targets": ["claim:claim_one_gone"], "word": "correct"},
    )
    assert status == 409 and body["written"] == []
    assert body["error"] == body["refused"][0]["error"] and body["error"]
    status, body = _post(
        server,
        {
            "actor": "Sam Okafor",
            "targets": ["claim:claim_one_gone", "claim:claim_two_gone"],
            "word": "correct",
        },
    )
    assert status == 409
    assert body["error"].startswith("None of the 2 rows was answered. The first was refused: ")
    assert len(list(read_events(project))) == before
