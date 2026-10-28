"""Group resolutions on the Groupings page.

A person approves the words a proposed group of experiments shares, through the one answer route, as
a confirm on ``group:<terms>``; ``GET /api/v1/groups/resolutions`` then shows, per approved core,
who approved it and when, the groups it resolved in each experiment and across them, and each
deviation waiting as a question. The resolving rules are separate from this route; on an engine
without them the core is shown with nothing resolved yet, and the response says the run holds no
resolution. Every name is invented.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence.api.server import DashboardServer
from ingence.api.v1.groups import _DEVIATION, GroupResolutions, _core_of
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.scan.census import run_census


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("group_resolutions_page")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt_zib", "2024-03-06_kelt_zib"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "kelt_zib_01.csv").write_text("a\n1\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _call(server: DashboardServer, route: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload


def test_an_approved_core_is_shown_with_who_approved_it_and_what_it_resolved(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        status, shown = _call(server, "/api/v1/groups/resolutions")
        assert status == 200
        assert shown["cores"] == []
        # Written as the target spells a set: once each, lower case, in order.
        refused = _call(
            server,
            "/api/review/answer",
            {"actor": "vorn", "target": "group:zib kelt", "word": "accept"},
        )
        assert refused[0] == 400
        assert "group:kelt zib" in refused[1]["error"]
        status, written = _call(
            server,
            "/api/review/answer",
            {"actor": "vorn", "target": "group:kelt zib", "word": "accept"},
        )
        assert status == 201, written
        assert written["kind"] == "confirm"
        status, shown = _call(server, "/api/v1/groups/resolutions")
    GroupResolutions.model_validate(shown)
    core = shown["cores"][0]
    assert (core["core"], core["approved_by"], core["target"]) == (
        ["kelt", "zib"],
        "vorn",
        "group:kelt zib",
    )
    # Every project's rules resolve groups, but a set is asked, and resolved, only where five
    # experiments share it: this small pool holds no conclusion of theirs, and the run was made
    # before the approval, so nothing is shown as resolved.
    assert shown["resolving_rules_in_run"] is False
    assert core["within"] == core["across"] == core["deviations"] == []
    assert json.loads(read_events(project)[-1].payload)["source"]["page"] == "groupings"


def test_a_resolved_group_belongs_to_the_core_holding_its_words() -> None:
    cores = [("kelt", "trell", "zib"), ("kelt", "zib")]
    # A group of exactly one core's words is that core's; a subset goes to the nearest.
    assert _core_of(frozenset({"kelt", "zib"}), cores) == ("kelt", "zib")
    assert _core_of(frozenset({"kelt", "trell"}), cores) == ("kelt", "trell", "zib")
    assert _core_of(frozenset({"vorn"}), cores) is None
    found = _DEVIATION.match("brisa, added to the group kelt zib")
    assert found is not None
    assert (found.group("word"), found.group("way"), found.group("core")) == (
        "brisa",
        "added to",
        "kelt zib",
    )


def test_a_deviation_waiting_is_shown_under_its_core(tmp_path: Path) -> None:
    """Each deviation waiting is shown under its core.

    A read that failed in DuckDB (a correlated unnest under a left join) and was swallowed would
    show none."""
    from fixtures.revision_4 import each_deviation, write_revision

    pool = tmp_path / "pool"
    for day, words in (
        ("20240301", "kelt zib"),
        ("20240302", "kelt zib"),
        ("20240303", "kelt zib trell"),
    ):
        for number in (1, 2):
            path = pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    each_deviation(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with DashboardServer(created.path, port=0) as server:
        status, written = _call(
            server,
            "/api/review/answer",
            {"actor": "vorn", "target": "group:kelt zib", "word": "accept"},
        )
        assert status == 201, written
    run_inference(created, run_id="run_20260101T000003Z_333333333333", started_at="t")
    with DashboardServer(created.path, port=0) as server:
        status, shown = _call(server, "/api/v1/groups/resolutions")
    assert status == 200
    (core,) = shown["cores"]
    assert [(d["word"], d["way"]) for d in core["deviations"]] == [("trell", "added")]
    assert core["deviations"][0]["experiment"] == "20240303 kelt zib trell"
