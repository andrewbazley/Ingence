"""The reorganisation planner: a plan kept in the workspace, checked, never applied.

On a copy of the explorer fixture (``fixtures/explorer_project.py``, invented names), served as a
person runs the Dashboard. Held here:

- a plan is saved as a file in the workspace, a new version at each save, and nowhere else;
- each move is checked against the run and, read-only, the disk: what goes with it, a name
  already in the destination, a folder moved into itself, mentions that name it by path, the
  decisions that follow it, the source as the census found it, and room and permissions;
- the check says applying is unavailable, and nothing on disk or in the Record changes.
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


@pytest.fixture
def served(explorer, tmp_path) -> Iterator[tuple[Path, str]]:
    from ingence.api.server import DashboardServer

    copy = tmp_path / "project"
    shutil.copytree(explorer.project, copy)
    server = DashboardServer(copy, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        yield copy, server.url.rstrip("/")
    finally:
        server.stop()


def _post(base: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _get(base: str, path: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _snapshot(pool: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(pool)): (path.lstat().st_size, path.lstat().st_mtime_ns)
        for path in sorted(pool.rglob("*"))
    }


def _outcomes(move: dict[str, Any]) -> dict[str, str]:
    return {item["check"]: item["outcome"] for item in move["checks"]}


def test_a_plan_is_kept_checked_and_never_applied(explorer, served) -> None:
    project, base = served
    ids = explorer.ids
    first, second, _ = explorer.experiments
    stem = first.split("_")[1]
    raw_one = f"{first}/raw/{stem}_01.nd2"  # a script mentions it by path
    run_id = _get(base, "/api/v1/explorer/wheel")[1]["run_id"]
    pool_before = _snapshot(explorer.pool)
    from ingence.record.log import read_events

    record_before = len(read_events(project, held=False))
    moves = [
        {"entry_id": ids[raw_one], "to_folder_id": ids[f"{first}/processed"]},
        {"entry_id": ids[f"{first}/analysis"], "to_folder_id": ids[second]},
        {"entry_id": ids[first], "to_folder_id": ids[f"{first}/raw"]},
    ]
    status, saved = _post(
        base,
        "/api/workspace/plan",
        {"name": "tidy the first series", "actor": "a. person", "run_id": run_id, "moves": moves},
    )
    assert status == HTTPStatus.CREATED, saved
    assert saved["version"] == 1
    status, check = _get(base, f"/api/v1/explorer/plan?id={saved['plan_id']}")
    assert status == HTTPStatus.OK, check
    by_entry = {move["entry_id"]: move for move in check["moves"]}

    raw = by_entry[ids[raw_one]]
    assert raw["to_path"] == f"{first}/processed/{stem}_01.nd2"
    assert raw["crosses_roots"] is False
    assert _outcomes(raw)["collision"] == "ok"
    assert _outcomes(raw)["space"] == "ok"
    assert _outcomes(raw)["source"] == "ok"  # the fixture's pool is as its census found it
    assert _outcomes(raw)["links"] == "warn"  # the analysis script names it by path
    assert any(link["why"] == "names it by path" for link in raw["links"])

    analysis = by_entry[ids[f"{first}/analysis"]]
    assert _outcomes(analysis)["collision"] == "refuse"  # the second series has an analysis folder
    assert analysis["outcome"] == "refuse"
    assert analysis["files"] == sum(1 for path in ids if path.startswith(f"{first}/analysis/"))

    into_itself = by_entry[ids[first]]
    assert _outcomes(into_itself)["into_itself"] == "refuse"
    # The first series' folder was confirmed by a person: that decision follows it.
    assert into_itself["decisions"] >= 1

    assert check["apply"]["available"] is False
    assert "not available" in check["apply"]["why"]
    assert check["totals"]["moves"] == 3
    assert check["totals"]["refused"] == 2
    diff = {item["path"]: item for item in check["diff"]}
    assert f"{stem}_01.nd2" in diff[f"{first}/processed"]["added"]
    assert f"{stem}_01.nd2" in diff[f"{first}/raw"]["removed"]

    # Nothing on disk, nothing in the Record; the plan is a file in the workspace.
    assert _snapshot(explorer.pool) == pool_before
    assert len(read_events(project, held=False)) == record_before
    kept = list((project / "workspace" / "plans" / saved["plan_id"]).glob("*.json"))
    assert [path.name for path in kept] == ["1.json"]
    assert json.loads(kept[0].read_text())["applied"] is False


def test_a_plan_saved_again_is_a_new_version_and_the_old_one_is_kept(explorer, served) -> None:
    _, base = served
    ids = explorer.ids
    first = explorer.experiments[0]
    stem = first.split("_")[1]
    run_id = _get(base, "/api/v1/explorer/wheel")[1]["run_id"]
    body = {
        "name": "one move",
        "actor": "a. person",
        "run_id": run_id,
        "moves": [
            {
                "entry_id": ids[f"{first}/raw/{stem}_02.nd2"],
                "to_folder_id": ids[f"{first}/processed"],
            }
        ],
    }
    _, saved = _post(base, "/api/workspace/plan", body)
    status, again = _post(
        base,
        "/api/workspace/plan",
        {**body, "plan_id": saved["plan_id"], "name": "one move, renamed"},
    )
    assert status == HTTPStatus.CREATED, again
    assert again == {"plan_id": saved["plan_id"], "version": 2, "moves": 1}
    _, latest = _get(base, f"/api/v1/explorer/plan?id={saved['plan_id']}")
    assert (latest["name"], latest["version"], latest["versions"]) == (
        "one move, renamed",
        2,
        [1, 2],
    )
    _, first_version = _get(base, f"/api/v1/explorer/plan?id={saved['plan_id']}&version=1")
    assert first_version["name"] == "one move"
    _, listed = _get(base, "/api/v1/explorer/plans")
    assert [item["plan_id"] for item in listed["plans"]] == [saved["plan_id"]]
    assert listed["apply"]["available"] is False


def test_a_plan_is_refused_in_words(explorer, served) -> None:
    _, base = served
    ids = explorer.ids
    first = explorer.experiments[0]
    run_id = _get(base, "/api/v1/explorer/wheel")[1]["run_id"]
    move = {"entry_id": ids[first], "to_folder_id": ids[explorer.experiments[1]]}
    for body in (
        {"name": "x", "actor": "a. person", "run_id": run_id, "moves": []},
        {"name": "", "actor": "a. person", "run_id": run_id, "moves": [move]},
        {"name": "x", "actor": "", "run_id": run_id, "moves": [move]},
        {"name": "x", "actor": "a. person", "run_id": "", "moves": [move]},
        {"name": "x", "actor": "a. person", "run_id": run_id, "moves": [move, move]},
        {"name": "x", "actor": "a. person", "run_id": run_id, "moves": [{"entry_id": "a"}]},
        {"name": "x", "actor": "a. person", "run_id": run_id, "moves": [move], "plan_id": "../x"},
    ):
        status, said = _post(base, "/api/workspace/plan", body)
        assert status == HTTPStatus.BAD_REQUEST, body
        assert said["error"]
    status, said = _get(base, "/api/v1/explorer/plan?id=plan-000000000000")
    assert status == HTTPStatus.NOT_FOUND


def _checked(base: str, name: str, moves: list[dict[str, str]]) -> dict[str, Any]:
    run_id = _get(base, "/api/v1/explorer/wheel")[1]["run_id"]
    status, saved = _post(
        base,
        "/api/workspace/plan",
        {"name": name, "actor": "a. person", "run_id": run_id, "moves": moves},
    )
    assert status == HTTPStatus.CREATED, saved
    status, check = _get(base, f"/api/v1/explorer/plan?id={saved['plan_id']}")
    assert status == HTTPStatus.OK, check
    return check


def test_moves_that_interfere_with_each_other_are_refused(explorer, served) -> None:
    """Each move is checked against the others, not only against the tree as it stands.

    A cycle, a move of what another move carries, and a move into a folder another move takes
    away are refused, and the totals count what moves once."""
    _, base = served
    ids = explorer.ids
    first, second, third = explorer.experiments
    analysis = sorted(path for path in ids if path.startswith(f"{first}/analysis/"))

    cycle = _checked(
        base,
        "a cycle",
        [
            {"entry_id": ids[first], "to_folder_id": ids[second]},
            {"entry_id": ids[second], "to_folder_id": ids[first]},
        ],
    )
    assert [move["outcome"] for move in cycle["moves"]] == ["refuse", "refuse"]
    assert all(_outcomes(move).get("cycle") == "refuse" for move in cycle["moves"]), cycle
    assert cycle["totals"]["refused"] == 2

    overlap = _checked(
        base,
        "an overlap",
        [
            {"entry_id": ids[f"{first}/analysis"], "to_folder_id": ids[f"{third}/raw"]},
            {"entry_id": ids[analysis[0]], "to_folder_id": ids[f"{first}/processed"]},
        ],
    )
    outer, inner = overlap["moves"]
    assert outer["outcome"] != "refuse", outer
    assert _outcomes(inner)["overlap"] == "refuse", inner
    # What moves is counted once: the folder and the files beneath it, the inner file among them.
    assert overlap["totals"]["files"] == len(analysis)
    assert overlap["totals"]["folders"] == 1

    moving = _checked(
        base,
        "into a folder that moves",
        [
            {"entry_id": ids[f"{first}/processed"], "to_folder_id": ids[f"{second}/raw"]},
            {"entry_id": ids[analysis[0]], "to_folder_id": ids[f"{first}/processed"]},
        ],
    )
    carrier, into_moving = moving["moves"]
    assert carrier["outcome"] != "refuse", carrier
    assert _outcomes(into_moving)["destination"] == "refuse", into_moving
    assert "processed" in next(
        item["words"] for item in into_moving["checks"] if item["check"] == "destination"
    )


def test_a_refused_read_of_a_root_is_unknown_never_a_server_error(
    explorer, served, monkeypatch
) -> None:
    """The plan check reads mounted roots, read-only; where the system refuses a read (EPERM,
    EACCES, which ``Path.is_dir`` raises rather than answers), the checks that needed it say
    unknown."""
    import errno
    import pathlib

    _, base = served
    ids = explorer.ids
    first = explorer.experiments[0]
    stem = first.split("_")[1]
    pool = str(explorer.pool)
    for method in ("stat", "lstat"):
        original = getattr(pathlib.Path, method)

        def refusing(self, *args, _original=original, **kwargs):
            if str(self).startswith(pool):
                raise PermissionError(errno.EPERM, "Operation not permitted", str(self))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, method, refusing)
    check = _checked(
        base,
        "a refused read",
        [
            {
                "entry_id": ids[f"{first}/raw/{stem}_02.nd2"],
                "to_folder_id": ids[f"{first}/processed"],
            }
        ],
    )
    outcomes = _outcomes(check["moves"][0])
    assert outcomes["permission"] == "unknown", check["moves"][0]
    assert outcomes["source"] == "unknown", check["moves"][0]


@pytest.fixture
def undetermined_case(tmp_path) -> Iterator[tuple[Path, str, dict[str, str]]]:
    """A small invented pool whose root's case reading is not determined: ``brisa/Data`` and
    ``kelt/data`` differ only in case."""
    from fixtures.revision_4 import write_revision
    from ingence.api.server import DashboardServer
    from ingence.core.project import PROJECT_FILE, Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    for relative in ("brisa/Data/brisa_01.txt", "kelt/data/kelt_01.txt"):
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text("one line\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_aaaaaaaaaaaa", started_at="t")
    run_inference(project, run_id="run_20260101T000003Z_cccccccccccc", started_at="t")
    identity = json.loads((project.path / PROJECT_FILE).read_text())
    for root in identity["roots"]:
        root["case_sensitivity"] = "unknown"
        root["case_established_by"] = None
    (project.path / PROJECT_FILE).write_text(json.dumps(identity))
    with open_catalog(project.path) as catalog:
        ids = dict(
            catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE NOT is_deleted"
            ).fetchall()
        )
    server = DashboardServer(project.path, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        yield project.path, server.url.rstrip("/"), ids
    finally:
        server.stop()


def test_a_root_whose_case_is_not_known_compares_names_without_case(undetermined_case) -> None:
    """Sensitive is the safe guess for identity and the unsafe one for a collision: a root whose
    case was never settled folds names, and says so."""
    _, base, ids = undetermined_case
    check = _checked(
        base, "Data beside data", [{"entry_id": ids["brisa/Data"], "to_folder_id": ids["kelt"]}]
    )
    move = check["moves"][0]
    assert _outcomes(move)["collision"] == "refuse", move
    words = next(item["words"] for item in move["checks"] if item["check"] == "collision")
    assert "case is not known" in words, words


@pytest.fixture
def linked(tmp_path) -> Iterator[tuple[str, dict[str, str]]]:
    """A small invented pool with two links: one relative, into ``brisa/raw``; one absolute,
    into ``brisa/rawer``, a sibling whose name begins with the other's."""
    import os

    from fixtures.revision_4 import write_revision
    from ingence.api.server import DashboardServer
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    for relative in ("brisa/raw/brisa_01.txt", "brisa/rawer/brisa_02.txt", "kelt/kelt_01.txt"):
        (pool / relative).parent.mkdir(parents=True, exist_ok=True)
        (pool / relative).write_text("one line\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    mount = Path(project.identity.roots[0].mount_path)
    try:
        os.symlink("../brisa/raw/brisa_01.txt", pool / "kelt" / "to_raw.txt")
        os.symlink(mount / "brisa" / "rawer" / "brisa_02.txt", pool / "kelt" / "to_rawer.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot make a symbolic link here")
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_aaaaaaaaaaaa", started_at="t")
    run_inference(project, run_id="run_20260101T000003Z_cccccccccccc", started_at="t")
    with open_catalog(project.path) as catalog:
        ids = dict(
            catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE NOT is_deleted"
            ).fetchall()
        )
    server = DashboardServer(project.path, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        yield server.url.rstrip("/"), ids
    finally:
        server.stop()


def test_a_link_into_a_moving_folder_is_found_by_where_it_lands(linked) -> None:
    """A relative target is read against the link's own folder, and a folder is compared with
    its separator, so a sibling sharing its name's beginning is not inside it."""
    base, ids = linked
    check = _checked(
        base, "move the raw folder", [{"entry_id": ids["brisa/raw"], "to_folder_id": ids["kelt"]}]
    )
    move = check["moves"][0]
    pointing = {link["path"] for link in move["links"] if "target lies inside" in link["why"]}
    assert pointing == {"kelt/to_raw.txt"}, move["links"]


def test_two_new_plans_saved_in_one_second_are_two_plans(tmp_path) -> None:
    """A new plan's identifier has a random part, so one name saved twice in one second by one
    person is two plans, never two versions of one."""
    from ingence.api.routes.plans import save_plan

    body = {
        "name": "tidy",
        "actor": "a. person",
        "run_id": "run_20260101T000001Z_aaaaaaaaaaaa",
        "moves": [{"entry_id": "entry_one", "to_folder_id": "entry_two"}],
    }
    first = save_plan(tmp_path, body, saved_at="2026-01-01T00:00:00Z")
    second = save_plan(tmp_path, body, saved_at="2026-01-01T00:00:00Z")
    assert first["plan_id"] != second["plan_id"]
    assert (first["version"], second["version"]) == (1, 1)


def test_a_version_saved_at_once_by_two_writers_is_never_overwritten(tmp_path, monkeypatch) -> None:
    """Each version file is created exclusively: a save that read the versions before another
    writer's landed takes the next number, and the other's version stands."""
    from ingence.api.routes import plans

    body = {
        "name": "tidy",
        "actor": "a. person",
        "run_id": "run_20260101T000001Z_aaaaaaaaaaaa",
        "moves": [{"entry_id": "entry_one", "to_folder_id": "entry_two"}],
    }
    saved = plans.save_plan(tmp_path, body, saved_at="2026-01-01T00:00:00Z")
    folder = tmp_path / "workspace" / "plans" / saved["plan_id"]
    other = folder / "2.json"
    other.write_text('{"written": "by the other writer"}', "utf-8")
    # This save listed the versions before the other writer's landed.
    monkeypatch.setattr(plans, "plan_versions", lambda project, plan_id: [1])
    again = plans.save_plan(
        tmp_path, {**body, "plan_id": saved["plan_id"]}, saved_at="2026-01-01T00:00:01Z"
    )
    assert again["version"] == 3
    assert json.loads(other.read_text("utf-8")) == {"written": "by the other writer"}
    assert not list(folder.glob(".*"))


def test_where_no_hard_link_can_be_made_a_version_is_never_seen_half_written(
    tmp_path, monkeypatch
) -> None:
    """Where the filesystem makes no hard links, a version's number is reserved empty and the
    whole version moved onto it; a reader skips an empty version."""
    import errno

    from ingence.api.routes import plans

    def no_links(source, destination, *args, **kwargs):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(plans.os, "link", no_links)
    body = {
        "name": "tidy",
        "actor": "a. person",
        "run_id": "run_20260101T000001Z_aaaaaaaaaaaa",
        "moves": [{"entry_id": "entry_one", "to_folder_id": "entry_two"}],
    }
    saved = plans.save_plan(tmp_path, body, saved_at="2026-01-01T00:00:00Z")
    folder = tmp_path / "workspace" / "plans" / saved["plan_id"]
    assert json.loads((folder / "1.json").read_text("utf-8"))["name"] == "tidy"
    # Another writer has reserved the next number and not yet moved its version onto it.
    (folder / "2.json").write_text("", "utf-8")
    assert plans.latest_plan(tmp_path, saved["plan_id"])["version"] == 1
    again = plans.save_plan(
        tmp_path, {**body, "plan_id": saved["plan_id"]}, saved_at="2026-01-01T00:00:01Z"
    )
    assert again["version"] == 3
    assert (folder / "2.json").read_text("utf-8") == ""
    assert json.loads((folder / "3.json").read_text("utf-8"))["name"] == "tidy"
    assert not list(folder.glob(".*"))
