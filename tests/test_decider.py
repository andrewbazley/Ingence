"""Who is deciding on this machine: asked once, kept outside every project, never guessed.

The name is kept once per person per machine, in their own configuration directory
(``api/decider.py``), so a person does not type it for every decision. The page does not ask
again on every browser origin. These tests hold four things: where the file is, on every
platform, and that a test never reaches the real one; that nothing is guessed; that it is
written outside the project and never into a root; and that the server keeps it behind the same
guard as the Record's route.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ingence.api.decider import (
    CONFIG_HOME_VARIABLE,
    DECIDER_FILE,
    MAX_NAME_LENGTH,
    DeciderRefusedError,
    decider_path,
    machine_decider,
    read_decider,
    refuse_inside_roots,
    user_config_directory,
    write_decider,
)
from ingence.api.server import ANSWER_ROUTE, DECIDER_ROUTE, WRITE_ROUTES, DashboardServer
from ingence.core.project import Project
from ingence.scan.census import run_census

MOMENT = "2026-09-17T14:02:11Z"


# ---------------------------------------------------------------------------
# Where the name is kept.
# ---------------------------------------------------------------------------


def test_the_configuration_directory_follows_each_platform(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert user_config_directory(environ={}, platform="darwin", home=home) == (
        home / "Library" / "Application Support" / "Ingence"
    )
    assert user_config_directory(
        environ={"APPDATA": str(tmp_path / "roaming")}, platform="win32", home=home
    ) == (tmp_path / "roaming" / "Ingence")
    assert user_config_directory(environ={}, platform="win32", home=home) == (
        home / "AppData" / "Roaming" / "Ingence"
    )
    assert user_config_directory(environ={}, platform="linux", home=home) == (
        home / ".config" / "ingence"
    )
    assert user_config_directory(
        environ={"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, platform="linux", home=home
    ) == (tmp_path / "xdg" / "ingence")


def test_the_variable_names_the_directory_outright(tmp_path: Path) -> None:
    named = tmp_path / "elsewhere"
    for platform in ("darwin", "win32", "linux"):
        assert (
            user_config_directory(
                environ={CONFIG_HOME_VARIABLE: str(named)}, platform=platform, home=tmp_path
            )
            == named
        )


def test_no_test_reaches_the_real_configuration_directory() -> None:
    """The suite's own fixture points the variable at a temporary directory for every test."""
    named = os.environ.get(CONFIG_HOME_VARIABLE)
    assert named
    assert decider_path().parent == Path(named)


# ---------------------------------------------------------------------------
# Kept once, read back, never guessed.
# ---------------------------------------------------------------------------


def test_a_name_is_kept_once_and_read_back(tmp_path: Path) -> None:
    assert read_decider(tmp_path) is None
    assert machine_decider(config_home=tmp_path) is None
    kept = write_decider("  Kelt   Vorn ", set_at=MOMENT, config_home=tmp_path)
    assert kept.name == "Kelt Vorn"
    assert kept.stored_in == tmp_path / DECIDER_FILE
    assert machine_decider(config_home=tmp_path) == "Kelt Vorn"
    # A project path changes nothing: the name is the machine's, the same for every project.
    assert machine_decider(tmp_path / "any-project", config_home=tmp_path) == "Kelt Vorn"
    # A later name replaces it; the decisions made under the first keep it in the Record.
    write_decider("zib", set_at=MOMENT, config_home=tmp_path)
    assert machine_decider(config_home=tmp_path) == "zib"
    assert sorted(path.name for path in tmp_path.iterdir()) == [DECIDER_FILE]


def test_nothing_is_guessed_from_the_login_or_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every decision is attributable, never to a name the person did not give."""
    for variable in ("USER", "LOGNAME", "USERNAME"):
        monkeypatch.setenv(variable, "brisa")
    assert machine_decider(config_home=tmp_path) is None
    assert machine_decider() is None


def test_an_unreadable_file_is_nobody_rather_than_a_guess(tmp_path: Path) -> None:
    (tmp_path / DECIDER_FILE).write_text("{not json", encoding="utf-8")
    assert machine_decider(config_home=tmp_path) is None
    (tmp_path / DECIDER_FILE).write_text(json.dumps({"decider": "   "}), encoding="utf-8")
    assert machine_decider(config_home=tmp_path) is None
    (tmp_path / DECIDER_FILE).write_text(json.dumps(["kelt"]), encoding="utf-8")
    assert machine_decider(config_home=tmp_path) is None


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_NAME_LENGTH + 1), "kelt\x00vorn"])
def test_a_name_that_says_nobody_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(DeciderRefusedError):
        write_decider(name, set_at=MOMENT, config_home=tmp_path)
    assert not (tmp_path / DECIDER_FILE).exists()


def test_a_file_inside_a_root_is_refused_without_reaching_the_root(tmp_path: Path) -> None:
    """Compared as spelled: a root that is not mounted is never asked about."""
    root = tmp_path / "pool"
    with pytest.raises(DeciderRefusedError, match="writes nothing into the data it reads"):
        refuse_inside_roots(root / "kelt" / DECIDER_FILE, [str(root)])
    refuse_inside_roots(tmp_path / "config" / DECIDER_FILE, [str(root)])
    refuse_inside_roots(tmp_path / "pool-other" / DECIDER_FILE, [str(root)])
    assert not root.exists()


# ---------------------------------------------------------------------------
# The server keeps it, behind the Record route's guard.
# ---------------------------------------------------------------------------


@pytest.fixture
def censused(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "kelt_vorn_raw.csv").write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project


def _post(server: DashboardServer, route: str, body: dict[str, object], **headers: str) -> int:
    request = urllib.request.Request(
        server.url.rstrip("/") + route, method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        request.add_header(name.replace("_", "-"), value)
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        error.close()
        return int(error.code)


def _get(server: DashboardServer, route: str) -> dict[str, object]:
    with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
        return dict(json.loads(response.read()))


def test_the_server_asks_once_and_keeps_the_name_outside_the_project(
    censused: Project, tmp_path: Path
) -> None:
    config = tmp_path / "config"

    def project_files() -> list[str]:
        return sorted(str(path.relative_to(censused.path)) for path in censused.path.rglob("*"))

    # A server warms a run that has no readouts as it starts (derived views under
    # projections/); warmed first here, so what is compared is what the decider writes.
    from ingence.readouts import warm

    warm(censused.path, "run_20260101T000001Z_111111111111")
    before = project_files()
    with DashboardServer(censused.path, port=0, config_home=config) as server:
        shown = _get(server, DECIDER_ROUTE)
        assert shown["decider"] is None
        assert shown["stored_in"] == str(config / DECIDER_FILE)
        # The same guard as the Record's route: the header, and this server's own origin.
        assert _post(server, DECIDER_ROUTE, {"decider": "kelt"}) == 403
        assert (
            _post(
                server,
                DECIDER_ROUTE,
                {"decider": "kelt"},
                X_Ingence_Write="1",
                Origin="http://elsewhere.example",
            )
            == 403
        )
        assert _post(server, DECIDER_ROUTE, {"decider": " "}, X_Ingence_Write="1") == 400
        assert _post(server, DECIDER_ROUTE, {"decider": "kelt"}, X_Ingence_Write="1") == 201
        assert _get(server, DECIDER_ROUTE)["decider"] == "kelt"
        # A decision still states its actor: the page sends the name; the route requires it.
        assert _post(server, ANSWER_ROUTE, {"word": "kelt"}, X_Ingence_Write="1") == 400
    assert machine_decider(config_home=config) == "kelt"
    assert project_files() == before


def test_a_name_ending_as_an_agent_s_answers_do_is_refused(
    censused: Project, tmp_path: Path
) -> None:
    """``<name>_agent`` marks the answers of a file a person imported as an agent's,
    so a person's own decisions are never recorded under a name that reads as one."""
    config = tmp_path / "config"
    with DashboardServer(censused.path, port=0, config_home=config) as server:
        assert _post(server, DECIDER_ROUTE, {"decider": " kelt_Agent "}, X_Ingence_Write="1") == 400
        assert _post(server, DECIDER_ROUTE, {"decider": "kelt"}, X_Ingence_Write="1") == 201
    assert machine_decider(config_home=config) == "kelt"


def test_the_server_refuses_to_keep_the_name_inside_a_root(censused: Project) -> None:
    pool = Path(censused.identity.roots[0].mount_path)
    with DashboardServer(censused.path, port=0, config_home=pool / "config") as server:
        assert _post(server, DECIDER_ROUTE, {"decider": "kelt"}, X_Ingence_Write="1") == 400
    assert not (pool / "config").exists()


def test_the_routes_that_write_are_named() -> None:
    assert WRITE_ROUTES[:2] == (ANSWER_ROUTE, DECIDER_ROUTE)
