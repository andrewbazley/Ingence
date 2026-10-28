"""``/api/v1``: the version handshake and the response-model convention.

Every ``/api/v1`` response is a declared model: identifiers are strings, counts are integers a
browser holds exactly, times carry their zone, and a model refuses a field it does not declare.
The routes before ``/api/v1`` keep working.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from pydantic import ValidationError

from ingence import SCHEMA_VERSION, __version__
from ingence.api import v1
from ingence.api.server import DashboardServer
from ingence.api.v1.models import (
    MAX_SAFE_INTEGER,
    RESPONSE_MODELS,
    ApiModel,
    Count,
    ErrorResponse,
    VersionResponse,
    ZonedTime,
)
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

INFER_RUN = "run_20260101T000002Z_222222222222"


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("api_v1")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "brisa_notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id=INFER_RUN, started_at="t")
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project
    server.stop()


def _get(server: DashboardServer, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_handshake_names_the_api_the_schema_the_engine_and_the_run(served) -> None:
    server, _ = served
    status, payload = _get(server, "/api/v1/version")
    assert status == 200
    answered = VersionResponse.model_validate(payload)
    assert answered.api_version == "1.0"
    assert answered.schema_version == SCHEMA_VERSION
    assert answered.engine_version == __version__
    assert answered.run_pinned == answered.run_current == INFER_RUN
    assert answered.job_running is False
    assert answered.answered_at.endswith("Z")


def test_an_unknown_route_is_refused_in_the_one_error_shape(served) -> None:
    server, _ = served
    status, payload = _get(server, "/api/v1/no/such/route")
    assert status == 404
    assert ErrorResponse.model_validate(payload).error.code == "not_found"


def test_the_routes_before_v1_keep_working(served) -> None:
    server, _ = served
    status, payload = _get(server, "/api/runs")
    assert status == 200
    assert payload


def test_a_route_answering_another_model_is_a_server_error(served, monkeypatch) -> None:
    _, project = served
    monkeypatch.setitem(
        v1.ROUTES,
        "version",
        lambda context, query: ErrorResponse.model_validate(
            {"error": {"code": "x", "message": "y"}}
        ),
    )
    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "version", {})
    assert status == 500
    assert "declares VersionResponse" in payload["error"]["message"]


def test_every_route_declares_a_registered_model() -> None:
    v1.load_routes()
    registered = set(RESPONSE_MODELS.values())
    assert v1.ROUTES
    for path in v1.ROUTES:
        assert v1.DECLARED[path] in registered, path


def test_a_time_without_its_zone_is_refused() -> None:
    class Moment(ApiModel):
        at: ZonedTime

    assert Moment(at="2026-09-28T21:30:00Z").at.endswith("Z")
    assert Moment(at="2026-09-28T21:30:00-04:00")
    with pytest.raises(ValidationError, match="states no zone"):
        Moment(at="2026-09-28T21:30:00")


def test_a_count_a_browser_cannot_hold_exactly_is_refused() -> None:
    class Counted(ApiModel):
        n: Count

    assert Counted(n=MAX_SAFE_INTEGER).n == MAX_SAFE_INTEGER
    with pytest.raises(ValidationError):
        Counted(n=MAX_SAFE_INTEGER + 1)
    with pytest.raises(ValidationError):
        Counted(n=-1)


def test_a_model_refuses_a_field_it_does_not_declare() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"error": {"code": "x", "message": "y"}, "extra": 1})
