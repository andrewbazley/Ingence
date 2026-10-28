"""A definition takes effect after the re-run, and nothing interrupts.

A word defined on the Dashboard is written to the Record and waits; Re-run's partial
rebind binds it in a published census, and inference runs in the background; the page reads what
is waiting at ``/api/applying``. A write naming no meaning applies nothing. Every name is invented.
"""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING

import pytest

from ingence.api.server import WRITE_HEADER, DashboardServer
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def served(tmp_path: Path):
    pool = tmp_path / "pool"
    for folder, names in {
        "kelt": ["vorn3q_raw.csv", "vorn3q_processed.csv", "brisa notes.txt"],
        "tamen": ["zeph_vorn3q_01.tif", "quolt_02.tif"],
    }.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project
    server.stop()


def _post(server: DashboardServer, body: dict, route: str = "/api/review/answer") -> dict:
    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def _get(server: DashboardServer, path: str) -> dict:
    with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
        return json.loads(response.read())


def _rerun(server: DashboardServer) -> dict:
    return _post(server, {"actor": "kelt"}, route="/api/workspace/rerun")


def test_a_defined_word_is_bound_by_a_partial_rebind_and_then_inferred(served) -> None:
    server, project = served
    written = _post(
        server,
        {"actor": "kelt", "word": "vorn3q", "answer": "vorn three q", "theme_kind": "subject"},
    )
    assert written["target"].startswith("term:"), "the Record event is written first"
    assert "applying" not in written, "it waits for Re-run"
    assert _get(server, "/api/applying")["phase"] == "idle"
    applying = _rerun(server)["applying"]
    assert applying["queued"] == ["vorn3q"] or applying["phase"] in {"names", "rules", "views"}
    assert server.applier.wait(timeout=120)
    status = _get(server, "/api/applying")
    assert status["phase"] == "done" and status["partial"] is True
    assert status["spellings"] == ["vorn3q"] and "waiting_for" not in status
    with open_catalog(project.path, run_id=status["bound_run"]) as catalog:
        assert catalog.query("SELECT rebind_spellings_json FROM run").fetchone()[0] == '["vorn3q"]'
        bound = catalog.query(
            "SELECT count(DISTINCT b.entry_id) FROM dictionary_bindings b "
            "JOIN components c USING (component_id) "
            "WHERE c.normalized_value = 'vorn3q' AND b.resolution_status = 'resolved'"
        ).fetchone()[0]
    assert bound == 3
    with open_catalog(project.path) as catalog:
        current, parent = catalog.query("SELECT run_id, parent_run_id FROM run").fetchone()
    assert current == status["inferred_run"] and parent == status["bound_run"]
    # The partial rebind and the inference settle it: nothing waits.
    from ingence.workspace.state import pending_rerun

    assert pending_rerun(project.path) is None


def test_a_write_naming_no_meaning_applies_nothing(served) -> None:
    """Whether an extension is worth reading changes no spelling's meaning."""
    server, _ = served
    written = _post(server, {"actor": "kelt", "extension": "csv", "worth_reading": "yes"})
    assert "applying" not in written
    assert _get(server, "/api/applying")["phase"] == "idle"


def test_while_inference_runs_the_pages_read_the_run_settled_before_it(
    served, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebind's census holds only the census's own claims; a page keeps inference's.

    The job runs in a worker process, so the worker's own pause (``INGENCE_JOB_PAUSE``) holds it
    at the start of inference, rather than a stub in this process.
    """
    import time

    from ingence.jobs.worker import PAUSE_ENV

    server, project = served
    release = tmp_path / "release"
    reached = tmp_path / "release.reached"
    monkeypatch.setenv(PAUSE_ENV, f"inferring:{release}")
    before = _get(server, "/api/project")["counts"]
    _post(
        server,
        {"actor": "kelt", "word": "vorn3q", "answer": "vorn three q", "theme_kind": "subject"},
    )
    _rerun(server)
    deadline = time.monotonic() + 120
    while not reached.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert reached.is_file(), "the rebind published and inference began"
    with open_catalog(project.path) as catalog:
        census_claims = catalog.query("SELECT count(*) FROM claims").fetchone()[0]
    during = _get(server, "/api/project")["counts"]
    assert census_claims < before["claims"], "the current run is the census"
    assert during == before, "the page reads the run settled before the answer"
    release.write_text("go", encoding="utf-8")
    assert server.applier.wait(timeout=120)
    assert server.applier.settled_run() is None
