"""``/api/v1/shell``, ``/reachability`` and ``/reporting``: the frame's own facts.

The shell route says the project's name (and whether a person gave it), where the project is
written, when its run was published, whether any rule has run in that run and what the census
counted. The reachability route does a ``stat`` of each root's own path, never a read inside it, and
answers within a bound even when a share has gone away. The reporting route says the computer and
Python the engine runs on and, once the repository is public, its new-issue page.

Every root here is a temporary folder this test makes and removes. None is under ``/Volumes``.
"""

from __future__ import annotations

import json
import platform
import shutil
import threading
import time
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from ingence.api import v1
from ingence.api.server import DashboardServer
from ingence.api.v1 import shell as shell_routes
from ingence.api.v1.models import RESPONSE_MODELS
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CENSUS_RUN = "run_20260101T000001Z_111111111111"
INFER_RUN = "run_20260101T000002Z_222222222222"
STARTED = "2026-10-13T20:00:00Z"


def _pool(base: Path) -> Path:
    pool = base / "pool"
    (pool / "HeLa").mkdir(parents=True)
    (pool / "HeLa" / "HeLa_DAPI_actin.csv").write_text("a,b\n1,2\n")
    (pool / "HeLa" / "notes.txt").write_text("hello")
    (pool / "western_blot").mkdir()
    (pool / "western_blot" / "gel_1.png").write_bytes(b"x")
    return pool


def _get(server: DashboardServer, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
        found: dict[str, Any] = json.loads(response.read())
    return found


@pytest.fixture
def census_only(tmp_path: Path) -> Iterator[tuple[DashboardServer, Project, Path]]:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at=STARTED, roots=[pool])
    run_census(project, run_id=CENSUS_RUN, started_at=STARTED)
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project, pool
    server.stop()


@pytest.fixture
def inferred(tmp_path: Path) -> Iterator[tuple[DashboardServer, Project, Path]]:
    pool = _pool(tmp_path)
    project = Project.create(
        tmp_path / "project", created_at=STARTED, roots=[pool], name="Western blot pool"
    )
    run_census(project, run_id=CENSUS_RUN, started_at=STARTED)
    run_inference(project, run_id=INFER_RUN, started_at=STARTED)
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project, pool
    server.stop()


def test_the_shell_names_a_project_a_person_named(inferred: Any) -> None:
    server, project, _ = inferred
    said = _get(server, "/api/v1/shell")
    assert said["project_name"] == "Western blot pool"
    assert said["name_given"] is True
    assert said["project_path"] == str(project.path)
    assert said["run_finished_at"].endswith("Z") or "+" in said["run_finished_at"]


def test_a_project_nobody_named_shows_its_folders_name_and_says_so(census_only: Any) -> None:
    server, project, _ = census_only
    said = _get(server, "/api/v1/shell")
    assert said["project_name"] == project.path.name
    assert said["name_given"] is False


def test_a_census_alone_is_not_yet_inferred_and_counts_its_files_and_folders(
    census_only: Any,
) -> None:
    server, _, pool = census_only
    said = _get(server, "/api/v1/shell")
    assert said["inferred"] is False
    files = sum(1 for each in pool.rglob("*") if each.is_file())
    folders = sum(1 for each in pool.rglob("*") if each.is_dir())
    assert said["census"]["files"] == files
    # The root itself is a folder of the census as well as the two folders below it.
    assert said["census"]["folders"] in (folders, folders + 1)


def test_a_run_with_rules_in_it_is_inferred(inferred: Any) -> None:
    server, _, _ = inferred
    assert _get(server, "/api/v1/shell")["inferred"] is True


def test_every_frame_route_declares_a_registered_model() -> None:
    v1.load_routes()
    for path in ("shell", "reachability", "reporting"):
        assert v1.DECLARED[path] in set(RESPONSE_MODELS.values()), path


def test_a_root_that_is_there_is_reachable(inferred: Any) -> None:
    server, _, pool = inferred
    said = _get(server, "/api/v1/reachability")
    assert said["root_reachable"] is True
    assert [root["path"] for root in said["roots"]] == [str(pool.resolve())] or said["roots"][0][
        "path"
    ].endswith("pool")
    assert said["roots"][0]["why"] is None
    assert said["roots"][0]["kind"] == "folder"
    assert said["last_checked"].endswith("Z")
    assert said["run_finished_at"] is not None


def test_a_root_that_was_removed_is_not_reachable_and_the_last_run_is_still_dated(
    inferred: Any,
) -> None:
    server, _, pool = inferred
    shutil.rmtree(pool)
    said = _get(server, "/api/v1/reachability")
    assert said["root_reachable"] is False
    assert said["roots"][0]["reachable"] is False
    assert "does not exist" in said["roots"][0]["why"]
    assert said["run_finished_at"] is not None
    # Everything else the run says still reads: the unplugged state does not stop a page.
    assert _get(server, "/api/v1/shell")["inferred"] is True
    # Put it back: reachable again, with no restart.
    _pool(pool.parent)
    assert _get(server, "/api/v1/reachability")["root_reachable"] is True


def test_a_root_that_is_a_file_is_not_a_folder(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at=STARTED, roots=[pool])
    shutil.rmtree(pool)
    pool.write_text("not a folder any more")
    reached = shell_routes.reach_of(project.identity.roots, wait=2.0)
    assert reached[0].reachable is False
    assert reached[0].why == "it is not a folder"


def test_a_root_that_never_answers_is_said_not_to_within_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share that has gone away holds a ``stat`` for minutes. The route answers in the bound,
    and a second look while the first still waits does not start a second wait."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at=STARTED, roots=[pool])
    release = threading.Event()
    started: list[int] = []

    def never(roots: Any) -> tuple[Any, ...]:
        started.append(1)
        release.wait(30)
        return ()

    monkeypatch.setattr(shell_routes, "unreachable_roots", never)
    try:
        first = shell_routes.reach_of(project.identity.roots, wait=0.2)
        assert first[0].reachable is False
        assert first[0].why == "it did not answer within 0.2 seconds"
        second = shell_routes.reach_of(project.identity.roots, wait=0.2)
        assert second[0].reachable is False
        assert "has not answered for some time" in str(second[0].why)
        assert len(started) == 1, "a second look must not start a second wait"
    finally:
        release.set()


def test_the_reporting_route_names_the_computer_and_python_and_no_name(
    inferred: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, project, _ = inferred
    said = _get(server, "/api/v1/reporting")
    assert said["python"] == platform.python_version()
    assert said["computer"].split(" ")[0] in {"macOS", "Windows", "Linux", platform.system()}
    # No folder, project or host name reaches it.
    for private in (str(project.path), project.path.name, platform.node()):
        assert private not in json.dumps(said)


class _Metadata:
    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    def get_all(self, name: str) -> list[str]:
        assert name == "Project-URL"
        return self._urls


def test_the_repository_is_public_only_when_the_package_names_an_issues_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shell_routes.metadata,
        "metadata",
        lambda name: _Metadata(
            ["Homepage, https://ingence.org/", "Documentation, https://ingence.org/"]
        ),
    )
    assert shell_routes.new_issue_address() is None
    monkeypatch.setattr(
        shell_routes.metadata,
        "metadata",
        lambda name: _Metadata(
            [
                "Homepage, https://github.com/example-owner/ingence",
                "Issues, https://github.com/example-owner/ingence/issues",
            ]
        ),
    )
    assert shell_routes.new_issue_address() == "https://github.com/example-owner/ingence/issues/new"
    # An address that is not https is never offered.
    monkeypatch.setattr(
        shell_routes.metadata, "metadata", lambda name: _Metadata(["Issues, javascript:alert(1)"])
    )
    assert shell_routes.new_issue_address() is None


def test_the_date_of_the_last_run_is_the_runs_own_finish_time_not_the_pointers(
    inferred: Any,
) -> None:
    """The pointer's time moves when a person goes back to an earlier run; the run's own record
    does not."""
    from ingence.storage.catalog import open_catalog

    server, project, _ = inferred
    with open_catalog(project.path, run_id=INFER_RUN) as catalog:
        ended, started = catalog.query("SELECT ended_at, started_at FROM run").fetchone()
    expected = ended or started
    assert _get(server, "/api/v1/shell")["run_finished_at"] == expected
    assert _get(server, "/api/v1/reachability")["run_finished_at"] == expected


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ("20.6.0", "macOS 11 (x86_64)"),
        ("21.6.0", "macOS 12 (x86_64)"),
        ("22.6.0", "macOS 13 (x86_64)"),
        ("23.6.0", "macOS 14 (x86_64)"),
        ("24.1.0", "macOS 15 (x86_64)"),
        ("25.0.0", "macOS 26 (x86_64)"),
        ("26.0.0", "macOS (x86_64)"),
    ],
)
def test_macos_is_taken_from_the_darwin_release_where_mac_ver_says_10_16(
    monkeypatch: pytest.MonkeyPatch, release: str, expected: str
) -> None:
    """A Python built against an old SDK says 10.16 for every macOS from 11 on."""
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("10.16", ("", "", ""), "x86_64"))
    monkeypatch.setattr(platform, "release", lambda: release)
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert shell_routes.computer_said() == expected


def test_a_macos_version_the_system_states_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("14.6.1", ("", "", ""), "arm64"))
    monkeypatch.setattr(platform, "release", lambda: "23.6.0")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert shell_routes.computer_said() == "macOS 14.6.1 (arm64)"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "11")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert shell_routes.computer_said() == "Windows 11 (AMD64)"


# ------------------------------------------------------- "Run the rules now", through a server


def _post(
    server: DashboardServer, route: str, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    import urllib.error

    request = urllib.request.Request(
        server.url.rstrip("/") + route,
        data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return answer.status, json.loads(answer.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def census_server(tmp_path: Path) -> Iterator[tuple[DashboardServer, Project]]:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at=STARTED, roots=[pool])
    run_census(project, run_id=CENSUS_RUN, started_at=STARTED)
    home = tmp_path / "home"
    home.mkdir()
    server = DashboardServer(project.path, port=0, config_home=home)
    server.start()
    yield server, project
    server.stop()


def test_a_second_ask_while_the_rules_are_being_applied_queues_nothing(
    census_server: Any,
) -> None:
    """The ledger joins a job only while it is still queued, so a second ask once the first ran
    would be a second inference. The route answers the applier's status and queues nothing."""
    server, _ = census_server
    status, first = _post(server, "/api/workspace/infer")
    assert status == 201 and first["started"] == ["inference"]
    # At once (the job is queued or just started), and again once it is running.
    status, again = _post(server, "/api/workspace/infer")
    assert status == 201 and again["started"] == [] and "already" in again["note"]
    # The contract's phase words since 0.4.0: names, rules, views.
    assert again["applying"]["phase"] in {"queued", "names", "rules", "views", "done"}
    for _ in range(1200):
        if server.applier.status().get("phase") in {"names", "rules", "views", "done"}:
            break
        time.sleep(0.1)
    if server.applier.job_busy():
        status, running = _post(server, "/api/workspace/infer")
        assert status == 201 and running["started"] == []
    assert server.applier.wait(timeout=240)
    assert len(server.applier.jobs(20)) == 1, "the rules were applied once"
    assert _get(server, "/api/v1/shell")["inferred"] is True
    # Once they have run, the route refuses.
    status, refused = _post(server, "/api/workspace/infer")
    assert status == 400 and "rules have run in this project already" in refused["error"]
    assert len(server.applier.jobs(20)) == 1


def test_where_changes_already_wait_the_button_does_re_runs_work_in_one_job(
    census_server: Any,
) -> None:
    """A committed change to the years that count as dates waits for Re-run (the names are read
    again, then the rules): the button then asks what Re-run asks, as one job, not inference
    alone."""
    server, _ = census_server
    status, _draft = _post(
        server,
        "/api/workspace/draft",
        {"kind": "settings", "values": {"dictionary": {"years": [1995, 2026]}}},
    )
    assert status == 201
    status, _committed = _post(
        server, "/api/workspace/commit", {"actor": "dana", "message": "The years"}
    )
    assert status == 201
    status, answer = _post(server, "/api/workspace/infer")
    assert status == 201
    assert "rebind" in answer["started"] and "inference" in answer["started"], answer
    assert server.applier.wait(timeout=240)
    jobs = server.applier.jobs(20)
    assert len(jobs) == 1 and {"rebind", "inference"} <= set(jobs[0].steps)
    assert _get(server, "/api/v1/shell")["inferred"] is True


def test_a_job_a_close_left_stopped_is_started_by_the_ask_and_runs_once(tmp_path: Path) -> None:
    """A job still queued when the Dashboard closes is held until a person asks, as Re-run does. The
    button is such an ask: it must start the held job, not answer "already" for ever."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at=STARTED, roots=[pool])
    run_census(project, run_id=CENSUS_RUN, started_at=STARTED)
    home = tmp_path / "home"
    home.mkdir()
    first = DashboardServer(project.path, port=0, config_home=home)
    first.start()
    status, started = _post(first, "/api/workspace/infer")
    assert status == 201 and started["started"] == ["inference"]
    first.stop()  # a close: the job still queued or running is stopped and held
    second = DashboardServer(project.path, port=0, config_home=home)
    second.start()
    try:
        assert second.applier.stopped(), "the close left the job held"
        assert _get(second, "/api/applying")["phase"] == "stopped"
        status, answer = _post(second, "/api/workspace/infer")
        assert status == 201 and answer["started"] == []
        assert answer["applying"]["phase"] != "stopped", "the ask started the held job"
        assert second.applier.wait(timeout=240)
        assert _get(second, "/api/v1/shell")["inferred"] is True
        assert len(second.applier.jobs(20)) == 1, "the held job ran, and no second one was made"
    finally:
        second.stop()


def test_where_only_a_dive_waits_the_rules_run_over_the_census_and_the_commands_are_said(
    census_server: Any,
) -> None:
    """A committed ``depth`` needs the folders read again, which only a person starts: Re-run would
    start nothing. The button queues the rules over the census the project already has, and still
    passes on the commands for the rest."""
    server, _ = census_server
    status, _draft = _post(
        server, "/api/workspace/draft", {"kind": "settings", "values": {"depth": "deep"}}
    )
    assert status == 201
    status, _committed = _post(
        server, "/api/workspace/commit", {"actor": "dana", "message": "Deeper"}
    )
    assert status == 201
    status, answer = _post(server, "/api/workspace/infer")
    assert status == 201
    assert answer["started"] == ["inference"], answer
    assert answer["needs_the_share"] == ["dive"]
    assert answer["commands"] == ["ingence dive", "ingence infer"]
    assert server.applier.wait(timeout=240)
    jobs = server.applier.jobs(20)
    assert len(jobs) == 1 and tuple(jobs[0].steps) == ("inference",)
    assert _get(server, "/api/v1/shell")["inferred"] is True
