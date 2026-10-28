"""The lattice of group approvals on the Groupings page.

``GET /api/v1/groups/lattice`` shows the run's experiments' shared sets of words as the lattice the
engine asks from: each set, the experiments holding it, the sets it adds to, and its state
(approved, declined, asked now, waiting for a set it adds to, not asked through a declined one),
with the mode the run used, so a person can compare it with the each-deviation way by hand. A person
answers an asked set through the one answer route; the page writes nothing else. Every name is
invented.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.revision_4 import each_deviation, tune, write_revision
from ingence.api.server import WEB_ROOT, DashboardServer
from ingence.api.v1.groups import GroupLattice
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

_FIVE_DAYS = (
    ("20240301", "kelt vorn zib vornzib"),
    ("20240302", "kelt vorn zib vornzib brisa"),
    ("20240303", "kelt vorn zib"),
    ("20240304", "kelt vorn zib"),
    ("20240305", "kelt vorn zib brisa trell"),
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("group_lattice_page")
    pool = base / "pool"
    for day, words in _FIVE_DAYS:
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


def _by_terms(shown: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {" ".join(found["terms"]): found for found in shown["sets"]}


def test_the_lattice_shows_the_core_asked_and_each_derivation_waiting(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, shown = _call(server, "/api/v1/groups/lattice")
    assert status == 200, shown
    GroupLattice.model_validate(shown)
    assert (shown["mode"], shown["bound_reached"], shown["experiments"]) == ("lattice", False, 5)
    sets = _by_terms(shown)
    core = sets["kelt vorn zib"]
    assert core["state"] == "asked"
    assert core["added"] == ["kelt", "vorn", "zib"] and core["base"] == []
    assert [path.split(" ", 1)[0] for path in core["experiments"]] == [d for d, _ in _FIVE_DAYS]
    assert core["target"] == "group:kelt vorn zib"
    assert core["question"] == "question:group_resolution_deviation:a group: kelt vorn zib"
    derivation = sets["brisa kelt vorn zib"]
    assert (derivation["state"], derivation["base"], derivation["added"]) == (
        "waiting",
        ["kelt", "vorn", "zib"],
        ["brisa"],
    )
    assert derivation["parents"] == [["kelt", "vorn", "zib"]]
    assert derivation["question"] is None  # not asked yet


def test_an_answer_through_the_one_route_approves_the_core_and_asks_its_additions(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        status, written = _call(
            server,
            "/api/review/answer",
            {
                "actor": "vorn",
                "target": "question:group_resolution_deviation:a group: kelt vorn zib",
                "word": "correct",
            },
        )
        assert status == 201, written
        assert written["target"] == "group:kelt vorn zib"
    run_inference(Project.open(project), run_id="run_20260101T000003Z_333333333333", started_at="t")
    with DashboardServer(project, port=0) as server:
        status, shown = _call(server, "/api/v1/groups/lattice")
    sets = _by_terms(shown)
    assert (sets["kelt vorn zib"]["state"], sets["kelt vorn zib"]["decided_by"]) == (
        "approved",
        "vorn",
    )
    assert sets["brisa kelt vorn zib"]["state"] == "asked"
    assert sets["kelt vorn vornzib zib"]["state"] == "asked"
    assert sets["brisa kelt trell vorn zib"]["state"] == "waiting"


def test_under_each_deviation_the_page_says_so_and_still_shows_what_the_lattice_would_ask(
    project: Path,
) -> None:
    each_deviation(project / "rulebook" / "01-revision-4.yaml")
    run_inference(Project.open(project), run_id="run_20260101T000003Z_333333333333", started_at="t")
    with DashboardServer(project, port=0) as server:
        status, shown = _call(server, "/api/v1/groups/lattice")
    assert status == 200
    assert shown["mode"] == "each_deviation"
    assert _by_terms(shown)["kelt vorn zib"]["state"] == "asked"
    assert (
        _by_terms(shown)["kelt vorn zib"]["question"] is None
    )  # the run asked in the each_deviation mode


def test_the_section_writes_only_through_the_answer_route_and_its_help_is_catalogued() -> None:
    script = (WEB_ROOT / "groups.js").read_text(encoding="utf-8")
    review = (WEB_ROOT / "review.js").read_text(encoding="utf-8")
    catalog = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))
    keys = set(re.findall(r"'data-help': '([\w.]+)'", script)) | {"groupings.lattice"}
    assert keys <= set(catalog["entries"]), keys - set(catalog["entries"])
    assert set(re.findall(r"write\('([^']+)'", script)) == {"/api/review/answer"}
    # Reads: the lattice, and the lines of work to add to a comparison, the help catalog and the
    # places of the picture marks, and the comparison itself (a template string).
    assert set(re.findall(r"api\('([^']+)'", script)) == {
        "/api/v1/groups/lattice",
        "/api/v1/themes/map?themes_only=true",
        "/help/catalog.json",
        "/help/pictures/positions.json",
    }
    assert "api(`/api/v1/groups/compare?" in script
    assert "fetch(" not in script  # nothing but the page's own helpers reach the server
    assert "window.groupLattice(holder)" in review  # the page's hook, and all of it in review.js


def test_the_bound_on_the_sets_is_said_where_the_run_reached_it(project: Path) -> None:
    """Never a silent cut: a run whose lattice stopped at ``maximum_concepts`` says so."""

    tune(project, "maximum_concepts", 1)
    run_inference(Project.open(project), run_id="run_20260101T000003Z_333333333333", started_at="t")
    with DashboardServer(project, port=0) as server:
        status, shown = _call(server, "/api/v1/groups/lattice")
    assert status == 200
    assert (shown["bound_reached"], shown["maximum_concepts"]) == (True, 1)
    assert len(shown["sets"]) <= 1


def test_a_second_read_of_one_run_reuses_its_experiments_terms(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured on copies of real folders: reading the folders and acquisitions is most of the
    route's time and grows with the pool. A run's experiments and their terms do not change, so a
    second read of the same run recomputes only the states, from the Record as it is now."""
    from ingence.api.v1 import V1Context, groups

    loads: list[int] = []
    real = groups._experiment_records

    def counted(catalog: Any, parameters: Any) -> list[Any]:
        loads.append(1)
        return real(catalog, parameters)

    monkeypatch.setattr(groups, "_experiment_records", counted)
    run = "run_20260101T000002Z_222222222222"
    context = V1Context(project=project, run_current=run, run_pinned=run, job_running=False)
    first = groups.lattice(context, {})
    second = groups.lattice(context, {})
    assert len(loads) == 1
    assert first.sets == second.sets


def test_under_each_deviation_the_section_asks_nothing_and_says_what_that_mode_does() -> None:
    """Under the each-deviation way the lattice is shown to compare, never answered from; and a
    set not asked says why in words that fit both reasons."""
    script = (WEB_ROOT / "groups.js").read_text(encoding="utf-8")
    assert "const answerable = found.mode === 'lattice';" in script
    assert "set.state === 'asked' && answerable" in script
    assert "fewer experiments than the scan asks about" in script


def test_the_kept_terms_are_bounded_and_guarded(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-run terms are computed into a local and kept under a lock, the oldest dropped first,
    so two requests never evict each other's half-made entry."""
    from ingence.api.v1 import groups

    assert isinstance(groups._TERMS_LOCK, type(__import__("threading").Lock()))
    monkeypatch.setattr(groups, "_experiment_records", lambda catalog, parameters: [])
    groups._TERMS_KEPT.clear()
    for number in range(groups._RUNS_KEPT + 2):
        groups._term_sets(None, project, f"run_{number}", {"experiment_rules": ["a", "b"]})
    assert len(groups._TERMS_KEPT) == groups._RUNS_KEPT
    assert [key[1] for key in groups._TERMS_KEPT] == [
        f"run_{number}" for number in range(2, groups._RUNS_KEPT + 2)
    ]
