"""The landing page: somewhere to land before a project, or before its first run.

- The projects opened on this machine are a small list in the person's configuration
  directory, newest first, each described from the project's own small files; a project that is
  gone says so.
- A folder is checked by its own listing only: whether it is there and a folder, its top-level
  folders and a count of its direct children, never of anything beneath. A folder that is a
  project, or lies inside one, is refused as a root in words.
- A project is made as ``ingence init`` makes one, with the years the person gave written.
- A first run is one durable job: the census, the dive where asked, then inference, started
  only by a person's request and only for a project with no run.
- The server lands somewhere always: with no project, ``/`` is the landing page and every read
  that needs a project says so; a project opened there binds the server once, and another project
  opened from a bound server gets a server of its own beside it.

Every name here is invented; every folder is made by the test.
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
from ingence.api.recent import (
    RECENT_KEPT,
    describe_recent,
    name_of,
    read_recent,
    recent_path,
    remember_project,
)
from ingence.api.routes import landing
from ingence.api.server import LANDING_ROUTES, WRITE_HEADER, WRITE_ROUTES, DashboardServer
from ingence.core.project import Project
from ingence.jobs import JobState
from ingence.jobs.ledger import JobLedger
from ingence.storage.commit import read_pointer

PATIENCE = 120.0


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt", "2024-03-07_vorn"):
        (pool / folder / "deeper").mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"zib_glorbex_{index:02d}.csv").write_text(f"a\n{index}\n")
        (pool / folder / "deeper" / "quolt.txt").write_text("brisa\n")
    (pool / "tamen notes.txt").write_text("vorn\n")
    (pool / ".hidden_zeph").write_text("")
    return pool


# -- the projects opened on this machine ---------------------------------------------------------


def test_the_list_keeps_the_newest_first_once_each(tmp_path: Path) -> None:
    home = tmp_path / "config"
    first, second = tmp_path / "kelt", tmp_path / "vorn"
    remember_project(first, opened_at="2026-10-01T00:00:00Z", name="Kelt study", config_home=home)
    remember_project(second, opened_at="2026-10-02T00:00:00Z", config_home=home)
    remember_project(first, opened_at="2026-10-03T00:00:00Z", config_home=home)
    listed = read_recent(home)
    assert [item["path"] for item in listed] == [str(first.resolve()), str(second.resolve())]
    assert listed[0]["opened_at"] == "2026-10-03T00:00:00Z"
    # The name given it is kept when it is opened again without one.
    assert listed[0]["name"] == "Kelt study"
    assert name_of(first, home) == "Kelt study"
    assert name_of(second, home) == "vorn"
    assert recent_path(home).parent == home


def test_the_list_is_bounded_and_survives_a_broken_file(tmp_path: Path) -> None:
    home = tmp_path / "config"
    for index in range(RECENT_KEPT + 5):
        remember_project(tmp_path / f"glorbex{index}", opened_at=f"t{index}", config_home=home)
    assert len(read_recent(home)) == RECENT_KEPT
    recent_path(home).write_text("{not json", encoding="utf-8")
    assert read_recent(home) == []
    # And it is written again whole at the next opening.
    remember_project(tmp_path / "kelt", opened_at="t", config_home=home)
    assert len(read_recent(home)) == 1


def test_a_project_is_described_from_its_own_files_and_a_gone_one_says_so(tmp_path: Path) -> None:
    home = tmp_path / "config"
    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool])
    remember_project(made.path, opened_at="t", config_home=home)
    remember_project(tmp_path / "gone-project", opened_at="t", config_home=home)
    gone, there = describe_recent(home)
    assert gone["exists"] is False
    assert "no longer an Ingence project" in gone["why"]
    assert there["exists"] is True
    assert there["roots"] == [{"label": None, "path": str(pool.resolve()), "listing": False}]
    assert there["runs"] == 0
    assert there["has_run"] is False
    assert there["last_run_at"] is None


# -- a folder checked -----------------------------------------------------------------------------


def test_a_folder_is_checked_by_its_own_listing_only(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    said = landing.check_folder(str(pool))
    assert said["refusal"] is None
    assert said["folders"] == ["2024-03-05_kelt", "2024-03-06_kelt", "2024-03-07_vorn"]
    # Direct children only: the files two levels down are not counted.
    assert (said["folders_count"], said["files_count"], said["hidden_count"]) == (3, 1, 1)


@pytest.mark.parametrize(
    ("typed", "words"),
    [
        ("", "Type or paste"),
        ("missing-folder", "Not readable: nothing is at this path"),
        ("pool/tamen notes.txt", "Not readable: this is a file"),
    ],
)
def test_what_cannot_be_read_is_refused_in_words(tmp_path: Path, typed: str, words: str) -> None:
    _pool(tmp_path)
    said = landing.check_folder(str(tmp_path / typed) if typed else "")
    assert words in said["refusal"]


def test_a_project_is_never_a_root_nor_anything_inside_one(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool])
    assert "is an Ingence project" in landing.check_folder(str(made.path))["refusal"]
    inside = landing.check_folder(str(made.path / "runs"))["refusal"]
    assert "inside the Ingence project" in inside
    # And no new project is written inside it.
    assert "inside the Ingence project" in landing.check_project_folder(str(made.path / "zeph"))


def test_a_project_folder_is_proposed_under_the_home_folder(tmp_path: Path) -> None:
    proposed = landing.proposed_project_folder("Kelt: vorn/quolt", home=tmp_path)
    assert proposed == str(tmp_path / landing.PROJECTS_PARENT / "Kelt vorn quolt")


@pytest.mark.parametrize(
    ("given", "read"),
    [([2015, "present"], (2015, "present")), (["2014", "2020"], (2014, 2020))],
)
def test_the_years_are_read_as_given(given: list[Any], read: tuple[Any, Any]) -> None:
    assert landing.years_from(given) == read


@pytest.mark.parametrize("given", [[2020, 2015], ["twenty", "present"], [2015], [1700, 2020]])
def test_years_that_cannot_be_are_refused(given: list[Any]) -> None:
    with pytest.raises(landing.LandingRefusedError):
        landing.years_from(given)


# -- a project made -------------------------------------------------------------------------------


def test_a_project_is_made_with_the_years_the_person_gave(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    made = landing.create_project(
        {
            "root": str(pool),
            "name": "Kelt study",
            "project": str(tmp_path / "projects" / "kelt"),
            "years": [2016, "present"],
        },
        created_at="2026-10-09T00:00:00Z",
    )
    opened = Project.open(made)
    assert [root.mount_path for root in opened.identity.roots] == [str(pool.resolve())]
    assert tuple(opened.settings.dictionary.years) == (2016, "present")
    written = yaml.safe_load((made / "settings.yaml").read_text(encoding="utf-8"))
    assert written["dictionary"]["years"] == [2016, "present"]
    # Nothing was written into the folder it reads.
    assert sorted(path.name for path in pool.iterdir()) == [
        ".hidden_zeph",
        "2024-03-05_kelt",
        "2024-03-06_kelt",
        "2024-03-07_vorn",
        "tamen notes.txt",
    ]


def test_a_project_that_cannot_be_made_is_refused_in_words(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    with pytest.raises(landing.LandingRefusedError, match="Give the project a name"):
        landing.create_project({"root": str(pool), "name": "///"}, created_at="t")
    with pytest.raises(landing.LandingRefusedError, match="nothing is at this path"):
        landing.create_project({"root": str(tmp_path / "nowhere"), "name": "kelt"}, created_at="t")
    first = landing.create_project(
        {"root": str(pool), "name": "kelt", "project": str(tmp_path / "kelt")}, created_at="t"
    )
    with pytest.raises(landing.LandingRefusedError, match="A project is already saved here"):
        landing.create_project(
            {"root": str(pool), "name": "kelt", "project": str(first)}, created_at="t"
        )


# -- the first run --------------------------------------------------------------------------------


@pytest.mark.parametrize("dive", [False, True])
def test_a_first_run_is_one_job_census_dive_and_inference(tmp_path: Path, dive: bool) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool]).path
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        queued = applier.submit_first_run(dive=dive)
        assert queued["kind"] == "first_run"
        assert applier.first_run_busy() or applier.status()["state"] == "done"
        assert applier.wait(PATIENCE)
        said = applier.status()
    finally:
        applier.stop()
    assert said["state"] == "done", said
    assert said["operations"] == ["census", *(["dive"] if dive else []), "inference"]
    # What the census saw, as its own counts: the files at every depth, not the check's.
    assert said["census_files"] == 10
    assert said["census_errors"] == 0
    assert ("dive_files_read" in said) is dive
    # Each step published a run, and the current one is the inference's.
    pointer = read_pointer(project)
    assert pointer is not None
    assert pointer.run_id == said["inferred_run"]
    runs = sorted(path.name for path in (project / "runs").iterdir() if path.is_dir())
    assert len(runs) == (3 if dive else 2)
    # The snapshot files the page read while it ran are gone with the run published.
    assert not list((project / ".tmp").glob("*.progress.json"))
    events = JobLedger(project).events(said["job_id"])
    assert "read every name and folder in the roots" in events[0].message
    assert not applier.first_run_busy()


def test_a_first_run_is_refused_for_a_project_already_read(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool]).path
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        applier.submit_first_run(dive=False)
        assert applier.wait(PATIENCE)
        with pytest.raises(ValueError, match="already has a scan"):
            applier.submit_first_run(dive=False)
    finally:
        applier.stop()
    assert [job.state for job in JobLedger(project).recent()] == [JobState.DONE]


def test_a_first_run_a_close_stopped_goes_on_at_the_next_click(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool]).path
    ledger = JobLedger(project)
    # As a Dashboard closed while the job waited leaves it: queued, and held for a person.
    held = ledger.enqueue(steps=["census", "inference"], record_sequence=None, base_run=None)
    ledger.hold([held.job_id])
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        assert applier.first_run_busy()
        assert applier.status()["phase"] == "stopped"
        applier.submit_first_run(dive=False)
        assert applier.wait(PATIENCE)
    finally:
        applier.stop()
    # The held job ran; no second job was asked for.
    assert [(job.job_id, job.state) for job in ledger.recent()] == [(held.job_id, JobState.DONE)]


def test_a_root_that_is_gone_fails_the_first_run_in_words(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "kelt-project", created_at="t", roots=[pool]).path
    pool.rename(tmp_path / "moved-pool")
    applier = DefinitionApplier(project, executor="thread")
    try:
        applier.start()
        applier.submit_first_run(dive=False)
        assert applier.wait(PATIENCE)
        said = landing.first_run_progress(project, applier)
    finally:
        applier.stop()
    assert said["state"] == "failed"
    # Said in words, the error's kind kept apart for the page's "Why".
    assert said["error_kind"] == "RootUnreachableError"
    assert said["error_words"].startswith("A walk reads every declared root")
    assert read_pointer(project) is None


# -- the server lands somewhere always ------------------------------------------------------------


def _get(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def _post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", WRITE_HEADER: "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_landing_writes_are_named_and_guarded() -> None:
    assert set(LANDING_ROUTES) <= set(WRITE_ROUTES)
    assert all(route.startswith("/api/landing/") for route in LANDING_ROUTES)


def test_a_server_with_no_project_lands_and_binds_the_first_one_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    pool = _pool(tmp_path)
    config = tmp_path / "config"
    server = DashboardServer(None, config_home=config, warm_search=False)
    server.start()
    try:
        base = server.url.rstrip("/")
        status, kind, body = _get(base + "/")
        assert status == 200
        assert "text/html" in kind
        assert b"/landing.js" in body
        # A read that needs a project says there is none, never a server error.
        status, _, body = _get(base + "/api/project")
        assert status == 404
        assert "no project is open here yet" in json.loads(body)["error"]
        status, _, body = _get(base + "/api/v1/project")
        assert status == 404
        assert json.loads(body)["error"]["code"] == "no_project"
        state = json.loads(_get(base + "/api/landing/state")[2])
        assert state["project"] is None
        assert state["recent"] == []
        # A write that needs a project is refused in words.
        status, said = _post(base + "/api/landing/first-run", {"dive": False})
        assert status == 400
        assert "no project is open here yet" in said["error"]

        status, made = _post(
            base + "/api/landing/create",
            {"root": str(pool), "name": "Kelt study", "project": str(tmp_path / "kelt")},
        )
        assert status == 201, made
        assert made["url"] == "/"
        assert server.project == (tmp_path / "kelt").resolve()
        # Until it is read, / is still the landing page, which now shows the project.
        assert b"/landing.js" in _get(base + "/")[2]
        state = json.loads(_get(base + "/api/landing/state")[2])
        assert state["project"]["name"] == "Kelt study"
        assert state["project"]["has_run"] is False
        # The dive a default project would make is said before it is asked for: fifteen minutes.
        assert state["project"]["dive_opens_files"] is True
        assert state["project"]["dive_budget_seconds"] == 900.0
        assert [item["name"] for item in state["recent"]] == ["Kelt study"]

        status, started = _post(
            base + "/api/landing/first-run", {"dive": False, "years": [2014, "present"]}
        )
        assert status == 201, started
        assert server.applier is not None
        assert server.applier.wait(PATIENCE)
        progress = json.loads(_get(base + "/api/landing/progress")[2])
        assert progress["state"] == "done", progress
        assert tuple(Project.open(server.project).settings.dictionary.years) == (2014, "present")
        # Read: / is the Dashboard now, and /landing is still the landing page.
        assert b"/landing.js" not in _get(base + "/")[2]
        assert b"/landing.js" in _get(base + "/landing")[2]
        status, _, body = _get(base + "/api/project")
        assert status == 200
        # The versioned API names the job for what it was.
        jobs = json.loads(_get(base + "/api/v1/jobs")[2])["jobs"]
        assert [(job["kind"], job["operations"]) for job in jobs] == [
            ("first_run", ["census", "inference"])
        ]
    finally:
        server.stop()


def test_another_project_opened_from_a_bound_server_gets_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    pool = _pool(tmp_path)
    first = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    second = Project.create(tmp_path / "vorn", created_at="t", roots=[pool]).path
    server = DashboardServer(first, config_home=tmp_path / "config", warm_search=False)
    server.start()
    try:
        base = server.url.rstrip("/")
        status, opened = _post(base + "/api/landing/open", {"project": str(first)})
        assert (status, opened["url"]) == (201, "/")
        status, opened = _post(base + "/api/landing/open", {"project": str(second)})
        assert status == 201
        assert opened["url"].startswith("http://127.0.0.1:")
        assert opened["url"] != server.url
        [beside] = server.beside
        assert beside.project == second.resolve()
        assert server.project == first.resolve()
        state = json.loads(_get(opened["url"].rstrip("/") + "/api/landing/state")[2])
        assert state["project"]["path"] == str(second.resolve())
        # Opening it again goes to the same server.
        assert _post(base + "/api/landing/open", {"project": str(second)})[1]["url"] == beside.url
        status, said = _post(base + "/api/landing/open", {"project": str(tmp_path / "zeph")})
        assert status == 400
        assert "not an Ingence project" in said["error"]
    finally:
        server.stop()
    # It stops with the server that started it.
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(beside.url + "api/landing/state", timeout=5)


def test_see_lands_with_no_project_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from ingence.cli import main

    served: list[Path | None] = []
    monkeypatch.setattr(main, "serve", lambda project, **_: served.append(project))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main.app, ["see", "--no-open"])
    assert result.exit_code == 0, result.output
    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    # A project with no run is served too: its landing page reads it on a person's click.
    result = CliRunner().invoke(main.app, ["see", "-p", str(made), "--no-open"])
    assert result.exit_code == 0, result.output
    assert served == [None, made]
    # A path typed with -p that is no project is still refused by name.
    result = CliRunner().invoke(main.app, ["see", "-p", str(tmp_path / "zeph"), "--no-open"])
    assert result.exit_code == 1


# -- the list is never written inside a folder the project reads ---------------------------------


def _tree_of(folder: Path) -> list[tuple[str, int]]:
    return sorted(
        (path.relative_to(folder).as_posix(), path.stat().st_size) for path in folder.rglob("*")
    )


def test_the_list_is_not_written_inside_a_folder_the_project_reads(tmp_path: Path) -> None:
    """A person may read a folder that holds the configuration folder (their whole home folder).
    The project is then not remembered, the refusal says why in words, and nothing is made there."""
    from ingence.api.recent import RecentNotKeptError

    home_folder = tmp_path / "homefolder"
    pool = _pool(home_folder)
    made = Project.create(tmp_path / "kelt", created_at="t", roots=[home_folder]).path
    before = _tree_of(home_folder)
    for config in (
        home_folder / "Library" / "Application Support" / "Ingence",
        home_folder / "config",
        home_folder,
    ):
        with pytest.raises(RecentNotKeptError) as refused:
            remember_project(made, opened_at="t", config_home=config)
        said = str(refused.value)
        assert str(home_folder) in said and "a folder this project reads" in said
        assert "writes nothing into the folders it reads" in said
        assert "INGENCE_CONFIG_HOME" in said
        assert not recent_path(config).exists()
    assert not (home_folder / "Library").exists()
    assert _tree_of(home_folder) == before
    assert pool.is_dir()


def test_the_list_is_not_written_through_a_link_into_a_folder_the_project_reads(
    tmp_path: Path,
) -> None:
    from ingence.api.recent import RecentNotKeptError

    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    link = tmp_path / "settings_link"
    try:
        link.symlink_to(pool, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")
    before = _tree_of(pool)
    with pytest.raises(RecentNotKeptError):
        remember_project(made, opened_at="t", config_home=link / "config")
    assert _tree_of(pool) == before


def test_the_list_is_not_written_inside_a_root_spelled_in_another_letter_case(
    tmp_path: Path,
) -> None:
    """A root typed in another letter case is the same folder on a disk that ignores case; the
    guard compared spellings and let the list into it."""
    from ingence.api.recent import RecentNotKeptError

    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    shouted = tmp_path / pool.name.upper()
    if not shouted.exists():
        pytest.skip("this disk tells names apart by case")
    before = _tree_of(pool)
    with pytest.raises(RecentNotKeptError, match="a folder this project reads"):
        remember_project(made, opened_at="t", config_home=shouted / "config")
    with pytest.raises(RecentNotKeptError):
        remember_project(made, opened_at="t", config_home=pool / "sub" / ".." / "config")
    assert _tree_of(pool) == before


def test_the_list_is_still_kept_beside_a_project_and_outside_every_root(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    home = tmp_path / "config"
    remember_project(made, opened_at="t", config_home=home)
    assert [item["path"] for item in read_recent(home)] == [str(made.resolve())]
    # A path that is no project has no roots to ask, as before.
    remember_project(tmp_path / "gone-project", opened_at="t", config_home=home)
    assert len(read_recent(home)) == 2


def test_a_project_opens_from_the_landing_page_though_the_list_cannot_be_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The project opens and is only not listed: the page is told why, the server says it once,
    and nothing is written into the folder the project reads."""
    from ingence.api import server as server_module

    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    monkeypatch.setattr(server_module, "_SAID", set())
    pool = _pool(tmp_path)
    first = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    second = Project.create(tmp_path / "vorn", created_at="t", roots=[pool]).path
    before = _tree_of(pool)
    server = DashboardServer(first, config_home=pool / "config", warm_search=False)
    server.start()
    try:
        base = server.url.rstrip("/")
        for opening in (first, second, first):
            status, opened = _post(base + "/api/landing/open", {"project": str(opening)})
            assert status == 201, opened
            assert "a folder this project reads" in opened["note"]
            assert opened["project"]["exists"] is True
    finally:
        server.stop()
    assert _tree_of(pool) == before
    assert not (pool / "config").exists()
    said = capsys.readouterr().out
    assert said.count("is not added to the list of recent projects") == 1  # once per reason


def test_see_says_once_why_a_project_is_not_listed_and_still_serves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.api import server as server_module

    class StoppedBeforeStartingError(Exception):
        pass

    def stop(held: DashboardServer) -> None:
        held._http.server_close()  # the port it bound, which nothing will serve
        raise StoppedBeforeStartingError

    monkeypatch.setattr(server_module, "_SAID", set())
    monkeypatch.setattr(DashboardServer, "start", stop)
    pool = _pool(tmp_path)
    made = Project.create(tmp_path / "kelt", created_at="t", roots=[pool]).path
    monkeypatch.setenv("INGENCE_CONFIG_HOME", str(pool / "settings"))
    before = _tree_of(pool)
    for _ in range(2):
        with pytest.raises(StoppedBeforeStartingError):  # about to start: nothing refused it
            server_module.serve(made, open_browser=False)
    assert _tree_of(pool) == before
    assert not (pool / "settings").exists()
    said = capsys.readouterr().out
    assert said.count("is not added to the list of recent projects") == 1
    assert "INGENCE_CONFIG_HOME" in said
