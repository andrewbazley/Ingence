"""The Rules page's input boxes.

Every rule parameter is shown by its declared ``ParameterSpec`` (type, choices, bounds) with the
value the run used, the value the project's revisions give now and the shipped default. A typed
value is a draft of kind ``rule_parameters``, saved over the guarded write route and committed as a
project Rulebook revision. ``settings.yaml`` and the Record are never written by it.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ingence.api.server import WEB_ROOT, DashboardServer
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

BOUNDED = "candidate_generation_is_bounded"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("rules_inputs")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "brisa_notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(
        project, run_id="run_20200101T000001Z_111111111111", started_at="2020-01-01T00:00:01Z"
    )
    run_inference(
        Project.open(project.path),
        run_id="run_20200101T000002Z_222222222222",
        started_at="2020-01-01T00:00:02Z",
    )
    return project.path


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _send(
    server: DashboardServer, route: str, body: dict[str, Any], *, guarded: bool = True
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if guarded:
        headers["X-Ingence-Write"] = "1"
    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        method="POST",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read())
        error.close()
        return error.code, payload


def _read(server: DashboardServer, route: str) -> dict[str, Any]:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return json.loads(response.read())


def _parameter(rules: dict[str, Any], rule_id: str, name: str) -> dict[str, Any]:
    for section in rules["sections"]:
        for rule in section["rules"]:
            if rule["rule_id"] == rule_id:
                return next(item for item in rule["parameters"] if item["name"] == name)
    message = f"no rule {rule_id}"
    raise AssertionError(message)


def test_every_parameter_is_shown_with_its_declared_type_bounds_and_values(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        rules = _read(server, "/api/rules")
    shown = [
        parameter
        for section in rules["sections"]
        for rule in section["rules"]
        for parameter in rule["parameters"]
    ]
    assert shown, "the fixture's rules declare parameters"
    for parameter in shown:
        assert parameter["type"] in {
            "boolean",
            "integer",
            "real",
            "string",
            "enum",
            "string_list",
            "mapping",
            "duration",
        }
        assert set(parameter) >= {"value", "committed", "default", "drafted", "error"}
        if parameter["type"] == "enum":
            assert parameter["value"] in parameter["choices"]
    bounded = _parameter(rules, BOUNDED, "maximum_candidates")
    assert bounded["type"] == "integer"
    assert bounded["value"] == bounded["committed"] == bounded["default"]
    assert bounded["drafted"] is False
    assert rules["revisions_unreadable"] is None


def test_a_typed_value_is_a_draft_then_a_revision_and_never_settings_or_the_record(
    project: Path,
) -> None:
    (project / "settings.yaml").write_text("# mine\n")
    settings_before = (project / "settings.yaml").read_bytes()
    record = project / "record"
    record_before = sorted(path.name for path in record.rglob("*")) if record.exists() else []
    with DashboardServer(project, port=0) as server:
        was = _parameter(_read(server, "/api/rules"), BOUNDED, "maximum_candidates")["value"]
        status, saved = _send(
            server,
            "/api/workspace/draft",
            {"kind": "rule_parameters", "values": {BOUNDED: {"maximum_candidates": 7}}},
        )
        assert (status, saved["errors"]) == (201, {})
        drafted = _parameter(_read(server, "/api/rules"), BOUNDED, "maximum_candidates")
        assert (drafted["drafted"], drafted["draft"], drafted["value"]) == (True, 7, was)
        assert _read(server, "/api/workspace/state")["uncommitted"]["rule_parameters"] == 1
        status, _ = _send(server, "/api/workspace/commit", {"actor": "kelt", "message": "fewer"})
        assert status == 201
        committed = _parameter(_read(server, "/api/rules"), BOUNDED, "maximum_candidates")
        # The run is unchanged until a re-run, and the project's revisions say 7.
        assert (committed["value"], committed["committed"], committed["drafted"]) == (was, 7, False)
    assert (project / "settings.yaml").read_bytes() == settings_before
    record_after = sorted(path.name for path in record.rglob("*")) if record.exists() else []
    assert record_after == record_before


def test_a_value_its_bounds_refuse_is_kept_with_the_rulebook_s_words(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, saved = _send(
            server,
            "/api/workspace/draft",
            {"kind": "rule_parameters", "values": {BOUNDED: {"maximum_candidates": -4}}},
        )
        assert status == 201
        assert "maximum_candidates" in saved["errors"][BOUNDED]
        shown = _parameter(_read(server, "/api/rules"), BOUNDED, "maximum_candidates")
        assert shown["draft"] == -4
        assert "minimum" in shown["error"] or "below" in shown["error"]
        status, refused = _send(server, "/api/workspace/commit", {"actor": "kelt", "message": "m"})
        assert status == 400
        assert refused["error"]


def test_a_draft_without_the_write_header_is_refused(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, refused = _send(
            server,
            "/api/workspace/draft",
            {"kind": "rule_parameters", "values": {BOUNDED: {"maximum_candidates": 7}}},
            guarded=False,
        )
    assert status == 403
    assert "header" in refused["error"]


def test_the_page_renders_an_input_by_each_declared_type() -> None:
    script = (WEB_ROOT / "rules.js").read_text(encoding="utf-8")
    block = script[script.index("function parameterInput") :]
    block = block[: block.index("\n}\n")]
    for kind in ("boolean", "integer", "real", "duration", "enum", "string_list", "mapping"):
        assert f"'{kind}'" in block, kind
    assert "data-draft" in block
    assert "rule_parameters:" in block
