"""The landing page's one call.

"Make the project" and "Read it now" are one act: ``POST /api/landing/create`` takes the folder, the
name (none: the project's own folder names it), where it is written, the years that count as dates,
whether the first reading opens files, and who decides, and with ``read`` it also queues the census.

- the years land in the project's ``settings.yaml`` (whatever their value, as ``ingence init``
  writes what a person chose), and who decides lands in the machine's decider file, as the decider
  route writes it; opening files is kept nowhere: it rides the request that starts the reading,
  because a settings key an older build does not know is one that build cannot open;
- everything that can be refused is refused before anything is written: a folder that cannot be
  read, a name for who decides that cannot be kept, a project already written at the place;
- the first reading's figures are the engine's own counts, and a count not yet known is None,
  never 0;
- a project nobody named says so (``named_by_folder``), and Rename ends that.

Every name here is invented, every folder is made by the test, and who decides is kept in a folder
of the test's own, never the machine's.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

from ingence.api.apply import DefinitionApplier
from ingence.api.recent import describe_project
from ingence.api.routes import landing
from ingence.api.server import WRITE_HEADER, DashboardServer
from ingence.core.project import Project

PATIENCE = 120.0


def _pool(base: Path) -> Path:
    pool = base / "imaging-drive"
    for folder in ("2024-03-05_HeLa", "2024-03-06_HeLa", "2024-03-07_HEK293"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"DAPI_actin_{index:02d}.csv").write_text(f"a\n{index}\n")
    (pool / "western blot notes.txt").write_text("DMSO control\n")
    return pool


def _get(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return dict(json.loads(response.read()))


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(json.loads(response.read()))
    except urllib.error.HTTPError as error:
        return error.code, dict(json.loads(error.read()))


@pytest.fixture
def landed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A server with no project, a pool, and a configuration folder of the test's own."""
    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    config = tmp_path / "config"
    server = DashboardServer(None, config_home=config, warm_search=False)
    server.start()
    try:
        yield server, _pool(tmp_path), config
    finally:
        server.stop()


# -- the settings the page asks -----


@pytest.mark.parametrize(
    ("given", "read"),
    [
        # Not said is on: a first run reads inside the files.
        (None, True),
        (False, False),
        (True, True),
        ({"enabled": True}, True),
        ({"enabled": False}, False),
        ({"enabled": None}, True),
    ],
)
def test_opening_files_is_a_switch_read_as_one(given: Any, read: bool) -> None:
    assert landing.dive_from(given) is read


@pytest.mark.parametrize("given", ["false", "yes", 1, ["enabled"]])
def test_opening_files_is_never_read_as_truthy(given: Any) -> None:
    with pytest.raises(landing.LandingRefusedError, match="on or off"):
        landing.dive_from(given)


def test_the_years_land_in_the_settings_file_as_init_writes_them_and_opening_files_does_not(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    made = landing.create_project(
        {
            "root": str(pool),
            "name": "HeLa imaging",
            "project": str(tmp_path / "hela"),
            "years": [2000, 2020],
            "dive": {"enabled": True},
        },
        created_at="2026-10-13T00:00:00Z",
    )
    written = yaml.safe_load((made / "settings.yaml").read_text(encoding="utf-8"))
    assert written["dictionary"]["years"] == [2000, 2020]
    # Nothing about the switch is kept, so any build that can open the project today still can.
    assert "dive" not in written
    assert set(written) <= {"dictionary", "schema_version"}
    opened = Project.open(made)
    assert tuple(opened.settings.dictionary.years) == (2000, 2020)
    assert not hasattr(opened.settings, "dive")


def test_the_years_default_from_2013_and_nothing_else_is_written(tmp_path: Path) -> None:
    made = landing.create_project(
        {"root": str(_pool(tmp_path)), "name": "HEK293", "project": str(tmp_path / "hek")},
        created_at="2026-10-13T00:00:00Z",
    )
    written = yaml.safe_load((made / "settings.yaml").read_text(encoding="utf-8"))
    # The landing page proposes the engine's own default, 2013 to the present.
    assert written["dictionary"]["years"] == [2013, "present"]
    assert "dive" not in written


def test_the_years_a_person_gives_before_a_first_run_are_saved_and_the_switch_is_not(
    tmp_path: Path,
) -> None:
    made = landing.create_project(
        {"root": str(_pool(tmp_path)), "name": "HEK293", "project": str(tmp_path / "hek")},
        created_at="2026-10-13T00:00:00Z",
    )
    landing.save_first_run_settings(made, {"years": [2001, 2015], "dive": True})
    written = yaml.safe_load((made / "settings.yaml").read_text(encoding="utf-8"))
    assert written["dictionary"]["years"] == [2001, 2015]
    assert "dive" not in written


def test_a_project_given_no_name_is_named_by_its_folder_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The folder proposed is under the home folder, which is the test's own.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    pool = _pool(tmp_path)
    made = landing.create_project({"root": str(pool)}, created_at="2026-10-13T00:00:00Z")
    # The folder proposed follows the folder it reads, and the project holds no name of its own.
    assert made == (tmp_path / "home" / landing.PROJECTS_PARENT / "imaging-drive").resolve()
    assert Project.open(made).identity.name is None
    said = describe_project(made)
    assert (said["name"], said["named_by_folder"]) == ("imaging-drive", True)
    named = landing.rename_project(made, "Western blots")
    after = describe_project(named)
    assert (after["name"], after["named_by_folder"]) == ("Western blots", False)
    assert named.name == "imaging-drive"


# -- the one call, over the server -----


def test_read_it_now_makes_the_project_keeps_who_decides_and_starts_the_census_in_one_call(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path
) -> None:
    server, pool, config = landed
    base = server.url.rstrip("/")
    where = tmp_path / "projects" / "hela"
    status, made = _post(
        base + "/api/landing/create",
        {
            "root": str(pool),
            "name": "HeLa imaging",
            "project": str(where),
            "years": [2000, 2020],
            "dive": {"enabled": True},
            "who": "Dana Whitfield",
            "read": True,
        },
    )
    assert status == 201, made
    assert made["url"] == "/"
    # What landed: the years in the project, the name on the machine, and nothing of the switch.
    written = yaml.safe_load((where / "settings.yaml").read_text(encoding="utf-8"))
    assert written["dictionary"]["years"] == [2000, 2020]
    assert "dive" not in written
    assert _get(base + "/api/decider")["decider"] == "Dana Whitfield"
    assert (config / "decider.json").is_file()
    # The census was started by the same call, and the switch rode that request: the job has a dive.
    assert server.applier is not None
    assert server.applier.wait(PATIENCE)
    progress = _get(base + "/api/landing/progress")
    assert progress["state"] == "done", progress
    assert progress["operations"] == ["census", "dive", "inference"]
    # What the page draws: the engine's own counts, the census's after it has ended.
    counters = progress["counters"]
    # The census counts the folder it reads along with the three in it.
    assert (counters["folders_found"], counters["files_found"]) == (4, 7)
    assert counters["bytes_seen"] > 0
    # Nothing in the answer is a share of anything.
    assert "percent" not in json.dumps(progress).casefold()


def test_a_call_without_read_makes_the_project_and_starts_nothing(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path
) -> None:
    server, pool, config = landed
    base = server.url.rstrip("/")
    status, made = _post(
        base + "/api/landing/create",
        {"root": str(pool), "name": "HEK293", "project": str(tmp_path / "hek")},
    )
    assert status == 201, made
    assert server.applier is not None
    assert _get(base + "/api/landing/progress")["phase"] == "idle"
    # Who decides was not said, so nothing was kept.
    assert not (config / "decider.json").exists()
    assert not (tmp_path / "hek" / "CURRENT").exists()


def test_who_decides_already_kept_is_not_written_again(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path
) -> None:
    server, pool, config = landed
    base = server.url.rstrip("/")
    assert _post(base + "/api/decider", {"decider": "Sam Okafor"})[0] == 201
    first = (config / "decider.json").read_text(encoding="utf-8")
    status, made = _post(
        base + "/api/landing/create",
        {"root": str(pool), "project": str(tmp_path / "p"), "who": "Sam  Okafor"},
    )
    assert status == 201, made
    assert (config / "decider.json").read_text(encoding="utf-8") == first


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"root": "nowhere"}, "Not readable: nothing is at this path"),
        ({"who": "x" * 200}, "a name is at most 120 characters"),
        ({"project": "{existing}"}, "A project is already saved here"),
        ({"years": [2020, 2000]}, "is after the last"),
        ({"dive": "false"}, "on or off"),
    ],
)
def test_what_cannot_be_made_is_said_and_nothing_is_written(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path, body: dict[str, Any], words: str
) -> None:
    server, pool, config = landed
    base = server.url.rstrip("/")
    existing = Project.create(tmp_path / "already", created_at="t", roots=[pool]).path
    sent: dict[str, Any] = {
        "root": str(pool),
        "name": "HeLa imaging",
        "project": str(tmp_path / "new"),
        "who": "Dana Whitfield",
        "read": True,
        **{key: (str(existing) if value == "{existing}" else value) for key, value in body.items()},
    }
    if sent["root"] == "nowhere":
        sent["root"] = str(tmp_path / "nowhere")
    before = sorted(path.name for path in tmp_path.iterdir())
    status, said = _post(base + "/api/landing/create", sent)
    assert status == 400, said
    assert words in said["error"]
    # Nothing was written: no project, no name for who decides, no job, nothing bound.
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert not (config / "decider.json").exists()
    assert server.project is None
    assert sorted(path.name for path in pool.iterdir()) == [
        "2024-03-05_HeLa",
        "2024-03-06_HeLa",
        "2024-03-07_HEK293",
        "western blot notes.txt",
    ]


def test_who_decides_may_not_land_inside_the_folder_being_read(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path
) -> None:
    """The decider's file is the machine's, and the engine writes nothing into a root: refused
    before the project is made, as the decider route refuses it."""
    _, pool, _ = landed
    inside = DashboardServer(None, config_home=pool / "config", warm_search=False)
    inside.start()
    try:
        status, said = _post(
            inside.url.rstrip("/") + "/api/landing/create",
            {"root": str(pool), "project": str(tmp_path / "p"), "who": "Dana Whitfield"},
        )
        assert status == 400, said
        assert "inside" in said["error"]
        assert not (tmp_path / "p").exists()
        assert not (pool / "config").exists()
    finally:
        inside.stop()


def test_a_project_already_written_at_the_place_is_offered_to_be_opened(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path
) -> None:
    server, pool, _ = landed
    base = server.url.rstrip("/")
    existing = Project.create(tmp_path / "already", created_at="t", roots=[pool]).path
    said = _get(base + "/api/landing/project-folder?path=" + urllib.request.quote(str(existing)))
    assert said["refusal"] == "A project is already saved here."
    assert said["existing"] == str(existing)
    free = _get(
        base + "/api/landing/project-folder?path=" + urllib.request.quote(str(tmp_path / "fresh"))
    )
    assert free["refusal"] is None
    assert free["existing"] is None


def test_the_read_goes_to_the_server_that_reads_the_new_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server already reading a project starts another beside it for a new one, and the census is
    queued there, not in the project this server reads."""
    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    pool = _pool(tmp_path)
    first = Project.create(tmp_path / "first", created_at="t", roots=[pool]).path
    server = DashboardServer(first, config_home=tmp_path / "config", warm_search=False)
    server.start()
    try:
        status, made = _post(
            server.url.rstrip("/") + "/api/landing/create",
            {"root": str(pool), "project": str(tmp_path / "second"), "read": True},
        )
        assert status == 201, made
        assert made["url"].startswith("http://127.0.0.1:")
        [beside] = server.beside
        assert beside.project == (tmp_path / "second").resolve()
        assert beside.applier is not None
        assert beside.applier.wait(PATIENCE)
        assert _get(made["url"].rstrip("/") + "/api/landing/progress")["state"] == "done"
        # The project this server reads was not touched.
        assert not (first / "CURRENT").exists()
    finally:
        server.stop()


# -- the figures -----


def test_a_count_not_yet_known_is_none_and_never_zero() -> None:
    nothing = landing._counters({"phase": "census", "seen": None})
    assert nothing == {
        "folders_found": None,
        "files_found": None,
        "bytes_seen": None,
        "rules_run": None,
        "rules_total": None,
    }
    during = landing._counters(
        {"phase": "census", "seen": {"folders_found": 17, "files_found": 0, "bytes_seen": 0}}
    )
    # A census that has seen no file says 0 (a count), where one that has said nothing says None.
    assert (during["folders_found"], during["files_found"], during["bytes_seen"]) == (17, 0, 0)
    assert during["rules_run"] is None


def test_after_the_census_its_figures_stay_and_the_rules_are_counted_of_their_whole() -> None:
    inferring = landing._counters(
        {
            "phase": "inferring",
            "census_folders": 3,
            "census_files": 7,
            "census_bytes": 2048,
            "seen": {"rules_run": 21, "rules_total": 48},
        }
    )
    assert inferring == {
        "folders_found": 3,
        "files_found": 7,
        "bytes_seen": 2048,
        "rules_run": 21,
        "rules_total": 48,
    }
    dive = landing._counters(
        {"phase": "dive", "census_folders": 3, "census_files": 7, "census_bytes": 2048, "seen": {}}
    )
    assert (dive["rules_run"], dive["rules_total"]) == (None, None)
    assert dive["bytes_seen"] == 2048


# -- where the project is written: another folder's project is not offered ------------------------


def _two_folders_of_one_name(base: Path) -> tuple[Path, Path]:
    """Two folders called ``imaging-drive``, in two places, each holding a file."""
    roots = []
    for place in ("lab-a", "lab-b"):
        root = base / place / "imaging-drive"
        root.mkdir(parents=True)
        (root / "DAPI_actin_01.csv").write_text("a\n1\n")
        roots.append(root)
    return roots[0], roots[1]


def test_a_project_reads_the_root_it_declares_and_no_other(tmp_path: Path) -> None:
    first, second = _two_folders_of_one_name(tmp_path)
    made = landing.create_project(
        {"root": str(first), "name": "Whitfield imaging", "project": str(tmp_path / "w")},
        created_at="2026-10-13T00:00:00Z",
    )
    assert landing.project_reads_root(str(made), str(first)) is True
    assert landing.project_reads_root(str(made), str(second)) is False
    # A folder that is no project reads nothing.
    assert landing.project_reads_root(str(tmp_path / "nowhere"), str(first)) is False


def test_the_place_says_whether_the_project_there_reads_this_folder(tmp_path: Path) -> None:
    first, second = _two_folders_of_one_name(tmp_path)
    made = landing.create_project(
        {"root": str(first), "name": "Whitfield imaging", "project": str(tmp_path / "w")},
        created_at="2026-10-13T00:00:00Z",
    )
    assert landing.check_project_folder(str(made), str(first)) == landing.ALREADY_HERE
    assert landing.check_project_folder(str(made), str(second)) == landing.ANOTHER_HERE
    # Said without a root, it is the plain sentence.
    assert landing.check_project_folder(str(made)) == landing.ALREADY_HERE
    # A project of another folder is never made over, nor opened in place of the one asked for.
    with pytest.raises(landing.LandingRefusedError, match="for another data folder"):
        landing.create_project(
            {"root": str(second), "name": "Okafor imaging", "project": str(made)},
            created_at="2026-10-13T00:00:00Z",
        )


def test_a_free_place_is_proposed_where_another_folders_project_holds_the_name(
    tmp_path: Path,
) -> None:
    first, second = _two_folders_of_one_name(tmp_path)
    taken = landing.create_project(
        {"root": str(first), "name": "Whitfield imaging", "project": str(tmp_path / "home" / "x")},
        created_at="2026-10-13T00:00:00Z",
    )
    proposed = str(taken)
    # For the folder it reads, the place is kept (the page offers to open the project).
    assert landing.free_project_folder(proposed, str(first)) == proposed
    # For the other folder, the next free place is proposed.
    assert landing.free_project_folder(proposed, str(second)) == f"{proposed} 2"
    landing.create_project(
        {"root": str(second), "name": "Okafor imaging", "project": f"{proposed} 2"},
        created_at="2026-10-13T00:00:00Z",
    )
    assert landing.free_project_folder(proposed, str(first)) == proposed
    third = tmp_path / "lab-c" / "imaging-drive"
    third.mkdir(parents=True)
    assert landing.free_project_folder(proposed, str(third)) == f"{proposed} 3"


def test_the_route_offers_to_open_only_a_project_that_reads_the_folder(
    landed: tuple[DashboardServer, Path, Path], tmp_path: Path
) -> None:
    server, _, _ = landed
    base = server.url.rstrip("/")
    first, second = _two_folders_of_one_name(tmp_path)
    made = landing.create_project(
        {"root": str(first), "name": "Whitfield imaging", "project": str(tmp_path / "w")},
        created_at="2026-10-13T00:00:00Z",
    )
    quoted = urllib.request.quote
    same = _get(
        f"{base}/api/landing/project-folder?path={quoted(str(made))}&root={quoted(str(first))}"
    )
    assert (same["refusal"], same["existing"], same["existing_reads_root"]) == (
        landing.ALREADY_HERE,
        str(made),
        True,
    )
    assert same["alternative"] is None
    other = _get(
        f"{base}/api/landing/project-folder?path={quoted(str(made))}&root={quoted(str(second))}"
    )
    assert (other["refusal"], other["existing_reads_root"]) == (landing.ANOTHER_HERE, False)
    assert other["alternative"] == f"{made} 2"
    # Where no place was typed, the one proposed is free of the other folder's project.
    home = tmp_path / "home"
    parent = home / landing.PROJECTS_PARENT
    parent.mkdir(parents=True)
    landing.create_project(
        {"root": str(first), "project": str(parent / "imaging-drive")},
        created_at="2026-10-13T00:00:00Z",
    )
    # (The route proposes under the process's own home, so only the helper is asked here.)
    assert landing.free_project_folder(str(parent / "imaging-drive"), str(second)) == str(
        parent / "imaging-drive 2"
    )
