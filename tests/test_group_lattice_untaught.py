"""``groups/lattice?untaught=1``: the lattice with no person's answer given to it.

The class: a person can see the lattice as the run's words alone make it, and is told which
sets exist only because of an answer. Built on the pool of ``test_group_lattice_page.py``.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.server import DashboardServer
from ingence.api.v1.groups import GroupLattice
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

DAYS = (
    ("20240301", "kelt vorn zib vornzib"),
    ("20240302", "kelt vorn zib vornzib brisa"),
    ("20240303", "kelt vorn zib"),
    ("20240304", "kelt vorn zib"),
    ("20240305", "kelt vorn zib brisa trell"),
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("lattice_untaught")
    pool = base / "pool"
    for day, words in DAYS:
        for number in (1, 2):
            path = pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _get(server: DashboardServer, route: str) -> Any:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return json.loads(response.read())


def _post(server: DashboardServer, route: str, body: dict[str, Any]) -> int:
    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    with urllib.request.urlopen(request) as response:
        return int(response.status)


def _states(shown: dict[str, Any]) -> dict[str, str]:
    return {" ".join(found["terms"]): found["state"] for found in shown["sets"]}


def test_untaught_passes_no_answer_and_says_so(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status = _post(
            server,
            "/api/review/answer",
            {
                "actor": "vorn",
                "target": "question:group_resolution_deviation:a group: kelt vorn zib",
                "word": "correct",
            },
        )
        assert status == 201
    run_inference(Project.open(project), run_id="run_20260101T000003Z_333333333333", started_at="t")
    with DashboardServer(project, port=0) as server:
        taught = _get(server, "/api/v1/groups/lattice")
        untaught = _get(server, "/api/v1/groups/lattice?untaught=1")
    GroupLattice.model_validate(untaught)
    assert taught["untaught"] is False and taught["sets_changed_by_answers"] == []
    assert _states(taught)["kelt vorn zib"] == "approved"
    assert untaught["untaught"] is True
    assert _states(untaught)["kelt vorn zib"] != "approved"
    core = next(s for s in untaught["sets"] if s["terms"] == ["kelt", "vorn", "zib"])
    assert core["decided_by"] is None and core["decided_at"] is None
    # The sets are the same with and without answers (they come from the words); their states
    # differ, and the response names them: the core is approved when answered, and a set it adds
    # to is asked only because the core was approved.
    changed = {" ".join(s["terms"]): s for s in untaught["sets_changed_by_answers"]}
    assert changed["kelt vorn zib"]["state_with_answers"] == "approved"
    assert changed["kelt vorn zib"]["state_without_answers"] == "asked"
    derived = changed["brisa kelt vorn zib"]
    assert (derived["state_without_answers"], derived["state_with_answers"]) == ("waiting", "asked")
    assert _states(untaught)["brisa kelt vorn zib"] == "waiting"
    assert {" ".join(s["terms"]) for s in taught["sets"]} == {
        " ".join(s["terms"]) for s in untaught["sets"]
    }


def test_untaught_with_no_answer_names_no_set(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        untaught = _get(server, "/api/v1/groups/lattice?untaught=1")
    assert untaught["untaught"] is True and untaught["sets_changed_by_answers"] == []
