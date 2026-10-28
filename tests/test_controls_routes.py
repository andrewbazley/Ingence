"""The 0.4.0 controls: seven workspace writes, each named in ``WORKSPACE_ROUTES`` behind the same
guard as every other write, each answering its final shape, each refusing a malformed body in words,
and none pinned to a run. A project with no run at all answers them. Every name is invented.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api.routes import controls
from ingence.api.server import WORKSPACE_ROUTES, WRITE_ROUTES, DashboardServer
from ingence.core.project import Project

if TYPE_CHECKING:
    pass

GUARD = {"Content-Type": "application/json", "X-Ingence-Write": "1"}

#: A well-formed body for each control, and the keys its answer carries.
WELL_FORMED: dict[str, tuple[dict[str, Any], set[str]]] = {
    controls.STOP_ROUTE: (
        {"job_id": "job_0001", "keep": True},
        {"job_id", "keep", "requested", "state", "stop_keeps", "said", "recorded"},
    ),
    controls.ASSUMPTION_REJECT_ROUTE: (
        {"id": "asm_file_type_nd2"},
        {"id", "kind", "answer", "waits_for_rerun", "said", "recorded"},
    ),
    controls.WORKFLOWS_ROUTE: (
        {
            "workflow": {
                "name": "Imaging then analysis",
                "scope": "Confocal",
                "stages": [
                    {"stage": "acquisition", "types": ["nd2"], "folders": ["raw"], "names": []},
                    {"stage": "analysis", "types": ["csv"]},
                ],
                "exempt": ["notes"],
            },
            "dry_run": True,
        },
        {"workflow", "what_would_change", "dry_run", "said", "recorded"},
    ),
    controls.WORKFLOWS_WITHDRAW_ROUTE: (
        {"id": "wf_imaging_then_analysis"},
        {"id", "withdrawn", "what_would_change", "said", "recorded"},
    ),
    controls.AREAS_KIND_ROUTE: (
        {"area_id": "area_plate_reader", "kind": "instrument", "actor": "Quenby"},
        {"area_id", "kind", "answer_target", "said", "recorded"},
    ),
    controls.GOALS_ROUTE: (
        {"text": "HeLa timecourses with analysis"},
        {"goal", "said", "recorded"},
    ),
    controls.GOALS_REMOVE_ROUTE: ({"id": "goal_1"}, {"id", "removed", "said", "recorded"}),
    controls.GOALS_LOWER_ROUTE: (
        {"id": "goal_1", "at_least": 2},
        {"id", "at_least", "said", "recorded"},
    ),
}

#: A malformed body for each control: the wrong type, a word outside the kinds, empty or too
#: long text, stages that are no list.
MALFORMED: dict[str, list[dict[str, Any]]] = {
    controls.STOP_ROUTE: [{"job_id": "job_0001", "keep": "yes"}, {"keep": True}],
    controls.ASSUMPTION_REJECT_ROUTE: [{}, {"id": 3}],
    controls.WORKFLOWS_ROUTE: [
        {"workflow": {"name": "x", "stages": "acquisition"}},
        {"workflow": {"name": "", "stages": []}},
        {"workflow": {"name": "x", "stages": [{"types": ["nd2"]}]}},
        {"workflow": {"name": "x", "stages": []}, "dry_run": "yes"},
        {"workflow": "x"},
    ],
    controls.WORKFLOWS_WITHDRAW_ROUTE: [{"id": ""}],
    controls.AREAS_KIND_ROUTE: [
        {"area_id": "area_plate_reader", "kind": "robot", "actor": "Quenby"},
        {"area_id": "", "kind": "instrument", "actor": "Quenby"},
        # An answer names who gives it: no actor and no decider on this server is refused.
        {"area_id": "area_plate_reader", "kind": "instrument"},
    ],
    controls.GOALS_ROUTE: [{"text": ""}, {"text": "x" * 501}, {"text": 5}, {}],
    controls.GOALS_REMOVE_ROUTE: [{}, {"id": None}],
    controls.GOALS_LOWER_ROUTE: [
        {"id": "goal_1", "at_least": 0},
        {"id": "goal_1", "at_least": "two"},
        {"id": "goal_1", "at_least": True},
        {"at_least": 2},
    ],
}


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    """A project with a root and **no run at all**: the controls never pin to one."""
    base = tmp_path_factory.mktemp("controls")
    pool = base / "pool"
    (pool / "Confocal").mkdir(parents=True)
    (pool / "Confocal" / "notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server
    server.stop()


def _post(
    server: DashboardServer, route: str, body: dict[str, Any], headers: dict[str, str]
) -> tuple[int, Any]:
    request = urllib.request.Request(
        server.url.rstrip("/") + route, data=json.dumps(body).encode(), headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_every_control_is_a_guarded_workspace_write() -> None:
    assert set(controls.ROUTES) <= set(WORKSPACE_ROUTES)
    assert set(controls.ROUTES) <= set(WRITE_ROUTES)
    assert set(WELL_FORMED) == set(controls.ROUTES) == set(MALFORMED)


#: The controls still answering their shape and recording nothing (their tracks are later).
STUBS = tuple(
    route
    for route in controls.ROUTES
    if route
    not in (
        controls.GOALS_ROUTE,
        controls.GOALS_REMOVE_ROUTE,
        controls.GOALS_LOWER_ROUTE,
        controls.ASSUMPTION_REJECT_ROUTE,
    )
)


@pytest.mark.parametrize("route", STUBS)
def test_a_well_formed_body_answers_201_with_the_final_shape_recording_nothing(
    served, route
) -> None:
    """On a project with no run and no job, every control answers its shape and records
    nothing: the stubs by design, the Stop because there is no such job, the area's kind
    because an answer is written against a run."""
    body, keys = WELL_FORMED[route]
    status, payload = _post(served, route, body, GUARD)
    if route == controls.WORKFLOWS_WITHDRAW_ROUTE:
        # An identifier no run and no Record holds is refused in words.
        assert status == 400 and "identifier" in payload["error"], payload
        return
    assert status == 201, payload
    assert set(payload) == keys, (route, sorted(payload))
    assert payload["recorded"] is False
    assert isinstance(payload["said"], str) and payload["said"].endswith(".")


def test_a_goal_is_written_to_the_workspace_even_with_no_run(served) -> None:
    """Goals are a projection, allowed during a scan and before a run."""
    body, keys = WELL_FORMED[controls.GOALS_ROUTE]
    status, payload = _post(served, controls.GOALS_ROUTE, body, GUARD)
    assert status == 201 and set(payload) == keys and payload["recorded"] is True
    assert payload["goal"]["text"] == body["text"] and payload["goal"]["id"].startswith("goal_")
    status, removed = _post(
        served, controls.GOALS_REMOVE_ROUTE, {"id": payload["goal"]["id"]}, GUARD
    )
    assert status == 201 and removed["removed"] is True and removed["recorded"] is True
    status, again = _post(served, controls.GOALS_REMOVE_ROUTE, {"id": payload["goal"]["id"]}, GUARD)
    assert status == 201 and again["removed"] is False and again["recorded"] is False


def test_a_goal_is_lowered_to_at_least_n_of_m_and_raised_back(served) -> None:
    """POST /api/goals/lower reaches lower_goal; a goal that does not stand is refused in words."""
    _, written = _post(served, controls.GOALS_ROUTE, {"text": "kelt vorn brisa"}, GUARD)
    goal_id = written["goal"]["id"]
    status, lowered = _post(
        served, controls.GOALS_LOWER_ROUTE, {"id": goal_id, "at_least": 2}, GUARD
    )
    assert status == 201 and lowered == {
        "id": goal_id,
        "at_least": 2,
        "said": "The goal now takes an experiment matching at least 2 of its words.",
        "recorded": True,
    }
    status, raised = _post(
        served, controls.GOALS_LOWER_ROUTE, {"id": goal_id, "at_least": None}, GUARD
    )
    assert status == 201 and raised["at_least"] is None and raised["recorded"] is True
    status, missing = _post(
        served, controls.GOALS_LOWER_ROUTE, {"id": "goal_999", "at_least": 1}, GUARD
    )
    assert status == 400 and "goal_999" in missing["error"]
    _post(served, controls.GOALS_REMOVE_ROUTE, {"id": goal_id}, GUARD)


def test_rejecting_an_assumption_with_no_run_is_refused_in_words(served) -> None:
    status, payload = _post(
        served,
        controls.ASSUMPTION_REJECT_ROUTE,
        {"id": "asm_file_type_nd2", "actor": "a. person"},
        GUARD,
    )
    assert status == 400 and "no published run" in payload["error"]
    status, payload = _post(
        served, controls.ASSUMPTION_REJECT_ROUTE, {"id": "asm_file_type_nd2"}, GUARD
    )
    assert status == 400 and payload["error"] == controls.ACTOR_REFUSAL


@pytest.mark.parametrize("route", controls.ROUTES)
def test_without_the_guard_header_a_control_is_refused(served, route) -> None:
    body, _ = WELL_FORMED[route]
    status, payload = _post(served, route, body, {"Content-Type": "application/json"})
    assert status == 403 and "X-Ingence-Write" in payload["error"]


@pytest.mark.parametrize("route", controls.ROUTES)
def test_a_malformed_body_is_refused_in_words(served, route) -> None:
    for body in MALFORMED[route]:
        status, payload = _post(served, route, body, GUARD)
        assert status == 400, (route, body, payload)
        assert isinstance(payload["error"], str) and payload["error"], (route, body)


def test_the_shapes_say_what_the_brief_fixes(served) -> None:
    status, stopped = _post(served, controls.STOP_ROUTE, WELL_FORMED[controls.STOP_ROUTE][0], GUARD)
    assert status == 201 and stopped["keep"] is True and stopped["requested"] is False
    assert isinstance(stopped["state"], str) and stopped["stop_keeps"] is False

    _, declared = _post(
        served, controls.WORKFLOWS_ROUTE, WELL_FORMED[controls.WORKFLOWS_ROUTE][0], GUARD
    )
    workflow = declared["workflow"]
    assert workflow["origin"] == "declared" and workflow["id"] == "wf_imaging_then_analysis"
    assert workflow["name"] == "Imaging then analysis" and workflow["scope"] == "Confocal"
    assert [stage["stage"] for stage in workflow["stages"]] == ["acquisition", "analysis"]
    assert workflow["stages"][1] == {
        "stage": "analysis",
        "types": ["csv"],
        "folders": [],
        "names": [],
    }
    assert workflow["exempt"] == ["notes"] and declared["dry_run"] is True
    assert set(declared["what_would_change"]) == {"stages_changed", "files", "experiments", "said"}

    _, kind = _post(
        served,
        controls.AREAS_KIND_ROUTE,
        {"area_id": "area_plate_reader", "kind": "date", "actor": "Quenby"},
        GUARD,
    )
    assert kind["answer_target"] == "area:area_plate_reader" and kind["kind"] == "date"
    assert stopped["state"] == controls.NO_SUCH_JOB

    _, goal = _post(served, controls.GOALS_ROUTE, {"text": "  U2OS titrations  "}, GUARD)
    assert goal["goal"]["text"] == "U2OS titrations" and goal["goal"]["id"]
    _, removed = _post(served, controls.GOALS_REMOVE_ROUTE, {"id": goal["goal"]["id"]}, GUARD)
    assert removed["removed"] is True


def test_write_refuses_a_route_it_does_not_own() -> None:
    with pytest.raises(LookupError):
        controls.write("/api/elsewhere", {})
    with pytest.raises(ValueError, match="mapping"):
        controls.write(controls.GOALS_ROUTE, ["not", "a", "mapping"])


def test_applying_carries_the_0_4_0_progress_keys_beside_the_old_ones(served) -> None:
    """``/api/applying`` keeps every key the page read and adds the phase in words.

    It also adds the step of how many, and the counts (null and false when no job runs)."""
    with urllib.request.urlopen(served.url.rstrip("/") + "/api/applying") as response:
        applying = json.loads(response.read())
    assert applying["phase"] == "idle" and applying["jobs_done"] == 0 and applying["queued"] == []
    assert applying["phase_label"] == "Nothing is running"
    assert (applying["step"], applying["steps"]) == (0, 0)
    for name in ("done", "total", "rate_per_s", "seconds_left"):
        assert applying[name] is None, name
    assert applying["can_stop"] is False and applying["stop_keeps"] is False


def test_the_progress_keys_count_the_step_from_a_job_s_operations() -> None:
    """The phases are the contract's words, and the views are one step more after inference."""
    from ingence.api.apply import progress_keys

    first_run = ("census", "dive", "inference")
    assert progress_keys("listing", first_run)["step"] == 1
    assert progress_keys("reading", first_run)["step"] == 2
    assert progress_keys("rules", first_run)["step"] == 3
    assert progress_keys("views", first_run)["step"] == 4
    assert progress_keys("queued", first_run)["step"] == 0
    assert progress_keys("done", first_run, ended=True)["step"] == 4
    assert progress_keys("names", ("partial_rebind", "inference"))["step"] == 1
    assert progress_keys("rules", ("partial_rebind", "inference"))["steps"] == 3
    assert progress_keys("stopped", [])["phase_label"] == "Stopped"
    assert progress_keys("something_else")["phase_label"] == "something else"
    assert progress_keys("rules", first_run)["phase_legacy"] == "inferring"
    assert progress_keys("listing", first_run)["phase_legacy"] == "census"
