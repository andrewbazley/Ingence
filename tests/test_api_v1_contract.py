"""The contract: every ``/api/v1`` route's live response is its declared model.

On the graph fixture project (``fixtures/graph_project.py``), served over the loopback server,
every registered route is asked a question and its JSON is validated against the model it
declares -- the model its exported schema and the page's TypeScript declarations come from. A
route added later is held to the same: validated as its model when it answers, and as the one
error shape when it refuses the question it was given. The high-use routes must be there,
every integer a response carries must fit a browser's exact integers, and every time must carry
its zone. The routes before ``/api/v1`` answer as they did.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pytest

from ingence.api import v1
from ingence.api.server import DashboardServer
from ingence.api.v1.models import MAX_SAFE_INTEGER, RESPONSE_MODELS, ErrorResponse

v1.load_routes()

#: The routes the dashboard guide names as the high-use ones, beside the graph's.
HIGH_USE = (
    "search",
    "folder",
    "entry",
    "questions",
    "runs",
    "workspace",
    "rules",
    "graph/neighborhood",
    "graph/edge",
    "graph/find",
    "version",
)


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.graph_project import build_graph_project

    graph = build_graph_project(tmp_path_factory.mktemp("contract"))
    server = DashboardServer(graph.project, port=0)
    server.start()
    yield server, graph
    server.stop()


def _examples(graph: Any) -> dict[str, dict[str, str]]:
    """A question that each route answers on the fixture."""
    return {
        "version": {},
        "graph/neighborhood": {"center": graph.ids["trell"], "include_candidates": "true"},
        "graph/edge": {"id": graph.derivation_edge},
        "graph/find": {"q": "kelt"},
        "entry": {"id": graph.ids["run_kelt.py"]},
        "folder": {"id": graph.ids["trell"]},
        "search": {"q": "kelt"},
        "questions": {},
        # The Words queue, asked with the understood words shown as well.
        "words/queue": {"understood": "show", "answered": "show"},
        "runs": {},
        "workspace": {},
        "rules": {},
        # The experiments the engine found, a page of them.
        "experiments": {"limit": "5", "sort": "files", "order": "desc"},
        # A person's topics (none made here), read against the run.
        "topics": {},
        # The 0.4.0 contract's stubs: each answers its invented example.
        "overview": {},
        "assumptions": {},
        "checks": {"limit": "3"},
        "overlap": {"mode": "by_year"},
        # The story route is not asked here, because it needs a real experiment id.
        "workflows": {},
        "ask": {"q": "hela timecourse with analysis"},
        "areas": {},
        "goals": {},
    }


def _get(server: DashboardServer, path: str, query: dict[str, str]) -> tuple[int, Any]:
    url = server.url.rstrip("/") + v1.PREFIX + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _integers(value: Any) -> list[int]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return [number for item in value.values() for number in _integers(item)]
    if isinstance(value, list):
        return [number for item in value for number in _integers(item)]
    return []


def test_every_high_use_route_is_served_and_declares_a_registered_model() -> None:
    for path in HIGH_USE:
        assert path in v1.ROUTES, path
        assert v1.DECLARED[path] in set(RESPONSE_MODELS.values()), path


@pytest.mark.parametrize("path", sorted(v1.ROUTES))
def test_every_route_answers_its_declared_model_live(served, path) -> None:
    server, graph = served
    examples = _examples(graph)
    status, payload = _get(server, path, examples.get(path, {}))
    if path in examples:
        assert status == 200, payload
    if status == 200:
        v1.DECLARED[path].model_validate(payload)
        too_large = [number for number in _integers(payload) if abs(number) > MAX_SAFE_INTEGER]
        assert not too_large, f"{path} sends integers a browser rounds: {too_large[:3]}"
    else:
        ErrorResponse.model_validate(payload)


def test_an_entry_s_times_carry_their_zone_and_its_inode_is_a_string(served) -> None:
    server, graph = served
    status, payload = _get(server, "entry", {"id": graph.ids["kelt_raw.csv"]})
    assert status == 200
    record = payload["entry"]
    assert record["modified_at"].endswith("Z")
    assert record["created_at"] is None or record["created_at"].endswith("Z")
    assert record["inode"] is None or isinstance(record["inode"], str)
    assert "modified_ns" not in record


def test_a_missing_entry_folder_or_parameter_is_refused_in_the_one_shape(served) -> None:
    server, _ = served
    for path, query in (
        ("entry", {"id": "entry_nothing"}),
        ("folder", {"id": "entry_nothing"}),
        ("entry", {}),
        ("search", {"limit": "0"}),
        ("questions", {"limit": "zero"}),
    ):
        status, payload = _get(server, path, query)
        assert status in (400, 404), (path, query)
        ErrorResponse.model_validate(payload)


def test_the_routes_before_v1_answer_as_they_did(served) -> None:
    server, graph = served
    with urllib.request.urlopen(
        server.url.rstrip("/") + f"/api/entry/{graph.ids['kelt_raw.csv']}"
    ) as response:
        legacy = json.loads(response.read())
    assert legacy["entry"]["entry_id"] == graph.ids["kelt_raw.csv"]
    assert "modified_ns" in legacy["entry"]


def test_the_topic_export_is_declared_and_refuses_what_names_no_topic(served) -> None:
    """The CSV of a topic's files is a declared route; with no topic named, or one that does not
    stand, it answers in the one error shape (the fixture holds no topic)."""
    server, _ = served
    assert "topics/export" in v1.ROUTES
    assert v1.DECLARED["topics/export"] in set(RESPONSE_MODELS.values())
    for query, expected in (({}, 400), ({"topic": "hgnosuchtopic"}, 404)):
        status, payload = _get(server, "topics/export", query)
        assert status == expected, payload
        ErrorResponse.model_validate(payload)


def test_the_group_comparison_answers_its_declared_model_on_a_project_with_groups(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """``groups/compare``: two groups of the invented theme pool, a line of work and a set of words,
    answer the model the schema is exported from, with no integer a browser rounds."""
    pytest.importorskip("sklearn", reason="a line of work needs the themes extra")
    from fixtures.theme_pool import build_theme_project

    project, _ = build_theme_project(tmp_path_factory.mktemp("contract-compare") / "built")
    with DashboardServer(project, port=0) as server:
        status, lattice = _get(server, "groups/lattice", {})
        assert status == 200
        words = "|".join(lattice["sets"][0]["terms"])
        url = (
            server.url.rstrip("/")
            + v1.PREFIX
            + "groups/compare?"
            + urllib.parse.urlencode([("group", f"words:{words}"), ("group", "line:0")])
        )
        with urllib.request.urlopen(url) as response:
            assert response.status == 200
            payload = json.loads(response.read())
    v1.DECLARED["groups/compare"].model_validate(payload)
    assert not [n for n in _integers(payload) if abs(n) > MAX_SAFE_INTEGER]
    assert "groups/compare" in v1.ROUTES and "groups/compare" in v1.DECLARED


def test_the_contract_s_literals_spell_the_engine_s_enums() -> None:
    """Each closed vocabulary of the 0.4.0 contract is an enum of the engine, spelled once more as a
    ``Literal`` so the exported schema closes over it; the two never drift."""
    from typing import get_args

    from ingence.api.v1.jobs import JobStateName
    from ingence.api.v1.models import AreaKindName, AssumptionKindName
    from ingence.core.schema import MATCH_QUALITY_ORDER, AreaKind, AssumptionKind, MatchQuality
    from ingence.jobs import JobState

    assert list(get_args(AreaKindName)) == [kind.value for kind in AreaKind]
    assert list(get_args(AssumptionKindName)) == [kind.value for kind in AssumptionKind]
    assert set(get_args(JobStateName)) == {state.value for state in JobState}
    assert "cancelled" in get_args(JobStateName)
    assert MATCH_QUALITY_ORDER[-1] is MatchQuality.CORRECTED
    assert MATCH_QUALITY_ORDER[:3] == (
        MatchQuality.EXACT,
        MatchQuality.CASE_FOLDED,
        MatchQuality.LOOSE,
    )


def test_the_0_4_0_stubs_refuse_what_they_cannot_answer_in_the_one_shape(served) -> None:
    """A missing question, a missing experiment, a limit outside 1 to 50 and a mode no overlap has
    are refused in words; the defaults answer."""
    server, _ = served
    for path, query in (
        ("ask", {}),
        ("ask", {"q": "   "}),
        ("experiments/story", {}),
        ("checks", {"limit": "0"}),
        ("checks", {"limit": "51"}),
        ("checks", {"limit": "five"}),
        ("overlap", {"mode": "sideways"}),
    ):
        status, payload = _get(server, path, query)
        assert status == 400, (path, query, payload)
        ErrorResponse.model_validate(payload)
    status, payload = _get(server, "overlap", {})
    assert status == 200 and payload["mode"] == "connections" and payload["by_year"] is None
    assert isinstance(payload["connections"]["edges"], list)  # the fixture may draw none
    status, payload = _get(server, "checks", {"limit": "2"})
    assert status == 200 and len(payload["checks"]) == 2 and payload["total"] >= 2
    status, payload = _get(server, "assumptions", {})
    assert status == 200
    counts = [item["count"] for item in payload["assumptions"]]
    assert counts == sorted(counts, reverse=True) and payload["total"] == len(counts)
    assert all(item["reject"]["body"] == {"id": item["id"]} for item in payload["assumptions"])


def test_areas_and_goals_answer_during_a_scan_and_every_stub_refuses_with_a_conflict(
    served,
) -> None:
    """With no run at all every 0.4.0 route refuses ``no_published_run`` as a conflict; while a
    first run is scanning (a current run, no pin yet) ``areas`` and ``goals`` answer for the current
    run and the rest still refuse. ``areas`` reads the run, so the scanning context names the served
    project's own run."""
    from pathlib import Path

    from ingence.storage.commit import read_pointer

    _, graph = served
    none = v1.V1Context(
        project=Path("nowhere"), run_current=None, run_pinned=None, job_running=False
    )
    for path, query in (
        ("overview", {}),
        ("assumptions", {}),
        ("checks", {}),
        ("overlap", {}),
        ("experiments/story", {"id": ["entry_x"]}),
        ("workflows", {}),
        ("ask", {"q": ["hela"]}),
        ("areas", {}),
    ):
        status, payload = v1.dispatch(none, path, query)
        assert status == 409 and payload["error"]["code"] == "no_published_run", path
    # Goals answer before any run: their words alone, no run named, no conflict.
    status, payload = v1.dispatch(none, "goals", {})
    assert status == 200 and payload["run_id"] is None and payload["goals"] == []
    pointer = read_pointer(graph.project)
    assert pointer is not None
    scanning = v1.V1Context(
        project=graph.project, run_current=pointer.run_id, run_pinned=None, job_running=True
    )
    for path in ("areas", "goals"):
        status, payload = v1.dispatch(scanning, path, {})
        assert status == 200 and payload["run_id"] == pointer.run_id, (path, payload)
    status, _ = v1.dispatch(scanning, "overview", {})
    assert status == 409
