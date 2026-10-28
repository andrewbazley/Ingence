"""A project's own folders are trusted by where they are, never by their spelling.

Where a person replaces one of a project's internal folders (``.tmp``, ``projections/search`` and
the rest) with a link into their data, the engine's deletes and replaces would land in that data.
``shutil.rmtree`` refuses only a link given as its own argument, never one higher up the path. The
worst case is Explore's clean-up, which deletes any unlocked ``.tmp/explorer-*`` folder older than
six hours. The tests hold, over invented names and an outside folder ``elsewhere`` whose names and
bytes are compared before and after, that every command refuses a project with such a link, in
words, that each delete refuses one met later, and that Explore's clean-up removes only what a
build marked.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import PROJECT_FOLDERS, ProjectLayoutError

if TYPE_CHECKING:
    from pathlib import Path

RUN = "run_20260101T000001Z_111111111111"
SEVEN_HOURS = 7 * 3600

#: Every folder ``check_layout`` checks, written out so the list is the test's own.
CHECKED = (
    ".tmp",
    "dictionary",
    "imports",
    "jobs",
    "listings",
    "projections",
    "record",
    "rulebook",
    "runs",
    "workspace",
    "projections/search",
    "projections/explorer",
    "projections/themes",
    "jobs/logs",
    "workspace/drafts",
    "workspace/plans",
    "dictionary/packs",
    "dictionary/removed-packs",
    "rulebook/retired",
)


def _project(tmp_path: Path, *, census: bool = True) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "alpha.txt").write_text("alpha words\n", encoding="utf-8")
    (pool / "beta.txt").write_text("beta words\n", encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    if census:
        run_census(project, run_id=RUN, started_at="t")
    return project


def _elsewhere(tmp_path: Path, *files: str) -> Path:
    elsewhere = tmp_path / "elsewhere"
    for name in files:
        path = elsewhere / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"a person's {name}\n", encoding="utf-8")
    elsewhere.mkdir(exist_ok=True)
    return elsewhere


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(folder.rglob("*"))
    }


def _link(link: Path, target: Path) -> None:
    """Replace ``link`` (whatever is there) with a link to the folder ``target``."""
    if link.is_dir() and not link.is_symlink():
        shutil.rmtree(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")


def _aged(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


# -- every command refuses a project with a link among its own folders --------------------------


def test_the_folders_checked_are_each_top_level_folder_and_those_written_beneath() -> None:
    from ingence.storage.layout import OWNED_SUBFOLDERS

    assert set(CHECKED) == set(PROJECT_FOLDERS) | set(OWNED_SUBFOLDERS)
    assert "projections/exports" not in CHECKED


@pytest.mark.parametrize("folder", CHECKED)
def test_a_project_whose_own_folder_is_a_link_is_refused_by_name(
    tmp_path: Path, folder: str
) -> None:
    project = _project(tmp_path, census=False)
    elsewhere = _elsewhere(tmp_path, "keep.txt")
    before = _snapshot(elsewhere)
    _link(project.path / folder, elsewhere)
    with pytest.raises(ProjectLayoutError) as refused:
        Project.open(project.path)
    said = str(refused.value)
    assert f"{folder} in the project" in said
    assert "must be real folders" in said and "move the whole project folder" in said
    assert _snapshot(elsewhere) == before


def test_the_command_line_refuses_in_words(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app

    project = _project(tmp_path, census=False)
    elsewhere = _elsewhere(tmp_path, "keep.txt")
    _link(project.path / "jobs" / "logs", elsewhere)
    done = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert done.exit_code == 1, done.output
    assert "jobs/logs in the project" in done.output and "Traceback" not in done.output


# -- each delete refuses a link met later ---------------------------------------------------------


def test_explore_refuses_a_staging_folder_that_is_a_link_and_leaves_the_data_alone(
    tmp_path: Path,
) -> None:
    from ingence.api.v1.explorer.projection import build_projection

    project = _project(tmp_path)
    elsewhere = _elsewhere(tmp_path, "explorer-old/keep.txt")
    _aged(elsewhere / "explorer-old", SEVEN_HOURS)
    before = _snapshot(elsewhere)
    _link(project.path / ".tmp", elsewhere)
    with pytest.raises(ProjectLayoutError, match=r"\.tmp in the project"):
        Project.open(project.path)
    with open_catalog(project.path) as catalog, pytest.raises(ProjectLayoutError):
        build_projection(catalog)
    assert _snapshot(elsewhere) == before


def test_search_refuses_a_projection_folder_that_is_a_link_and_leaves_the_data_alone(
    tmp_path: Path,
) -> None:
    from ingence.api.routes import search as routes

    project = _project(tmp_path)
    elsewhere = _elsewhere(tmp_path, f"{RUN}/keep.txt")
    before = _snapshot(elsewhere)
    _link(project.path / "projections" / "search", elsewhere)
    with open_catalog(project.path) as catalog, pytest.raises(ProjectLayoutError):
        routes.search(catalog, project.path, text="alpha")
    with open_catalog(project.path) as catalog, pytest.raises(ProjectLayoutError):
        routes.build(catalog, project.path)
    assert _snapshot(elsewhere) == before


def test_pruning_the_logs_refuses_a_logs_folder_that_is_a_link(tmp_path: Path) -> None:
    from ingence.jobs.supervisor import prune_logs

    project = _project(tmp_path, census=False)
    elsewhere = _elsewhere(tmp_path, "job_x-1.log")
    before = _snapshot(elsewhere)
    _link(project.path / "jobs" / "logs", elsewhere)
    with pytest.raises(ProjectLayoutError, match="jobs/logs in the project"):
        prune_logs(project.path, keep=0)
    assert _snapshot(elsewhere) == before


def test_a_draft_is_neither_saved_nor_cleared_through_a_link(tmp_path: Path) -> None:
    from ingence.workspace.drafts import clear_draft, save_draft

    project = _project(tmp_path, census=False)
    elsewhere = _elsewhere(tmp_path, "settings.json")
    before = _snapshot(elsewhere)
    _link(project.path / "workspace" / "drafts", elsewhere)
    with pytest.raises(ProjectLayoutError, match="workspace/drafts in the project"):
        save_draft(project.path, "settings", {"scan": {"workers": 5}})
    with pytest.raises(ProjectLayoutError, match="workspace/drafts in the project"):
        clear_draft(project.path, "settings")
    assert _snapshot(elsewhere) == before


# -- Explore's clean-up removes only what a build marked -----------------------------------------


def test_an_old_staging_folder_no_build_marked_is_left_and_a_marked_one_removed(
    tmp_path: Path,
) -> None:
    from ingence.api.v1.explorer.projection import STAGING_MARKER, build_projection

    project = _project(tmp_path)
    staging = project.path / ".tmp"
    unmarked = staging / "explorer-unmarked"
    (unmarked / "files").mkdir(parents=True)
    (unmarked / "files" / "keep.txt").write_text("a person's file\n", encoding="utf-8")
    marked = staging / "explorer-marked"
    marked.mkdir()
    (marked / STAGING_MARKER).write_text("an earlier build", encoding="utf-8")
    (marked / "files.parquet").write_bytes(b"half written")
    for folder in (unmarked, marked):
        _aged(folder, SEVEN_HOURS)
    before = _snapshot(unmarked)
    with open_catalog(project.path) as catalog:
        built = build_projection(catalog)
    assert _snapshot(unmarked) == before
    assert not marked.exists()
    # The marker is the staging's: the projection published from it holds none.
    assert not (built.path / STAGING_MARKER).exists()
    # Nor does a build leave a staging folder of its own behind.
    assert sorted(path.name for path in staging.glob("explorer-*")) == ["explorer-unmarked"]


def test_a_staging_folder_that_is_a_link_is_never_judged_an_orphan(tmp_path: Path) -> None:
    from ingence.api.v1.explorer.projection import STAGING_MARKER, build_projection

    project = _project(tmp_path)
    elsewhere = _elsewhere(tmp_path, "keep.txt")
    (elsewhere / STAGING_MARKER).write_text("looks like a build's", encoding="utf-8")
    _aged(elsewhere, SEVEN_HOURS)
    before = _snapshot(elsewhere)
    _link(project.path / ".tmp" / "explorer-linked", elsewhere)
    with open_catalog(project.path) as catalog:
        build_projection(catalog)
    assert (project.path / ".tmp" / "explorer-linked").is_symlink()
    assert _snapshot(elsewhere) == before


def test_fix_counts_the_staging_folders_no_build_marked(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app

    project = _project(tmp_path)
    (project.path / ".tmp" / "explorer-unmarked").mkdir()
    done = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert "1 Explore staging folder in .tmp with no build's mark" in done.output, done.output
    assert (project.path / ".tmp" / "explorer-unmarked").is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is made only on Windows")
def test_a_junction_among_the_project_s_folders_is_refused(tmp_path: Path) -> None:
    import _winapi  # type: ignore[import-not-found]

    project = _project(tmp_path, census=False)
    elsewhere = _elsewhere(tmp_path, "keep.txt")
    before = _snapshot(elsewhere)
    shutil.rmtree(project.path / ".tmp")
    _winapi.CreateJunction(str(elsewhere), str(project.path / ".tmp"))
    with pytest.raises(ProjectLayoutError, match="is a junction"):
        Project.open(project.path)
    assert _snapshot(elsewhere) == before
