"""The Graph workspace's projection, built where a project is awkward.

On copies of the explorer fixture (``fixtures/explorer_project.py``, invented names): a project
under a folder whose name holds an apostrophe, a build that fails, staging folders left by builds
that are gone, a project that cannot be written, and a build that finds another process has
published the same projection first.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _copy(explorer: ExplorerProject, base: Path) -> Path:
    """A copy of the fixture with no projection built and nothing staged."""
    copy = base / "project"
    shutil.copytree(explorer.project, copy)
    shutil.rmtree(copy / "projections", ignore_errors=True)
    shutil.rmtree(copy / ".tmp", ignore_errors=True)
    return copy


def _ask(project: Path, path: str, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def _staged(project: Path) -> list[str]:
    staging = project / ".tmp"
    return sorted(path.name for path in staging.glob("explorer-*")) if staging.is_dir() else []


def test_a_project_under_a_name_with_an_apostrophe_is_explored_like_any_other(
    explorer, tmp_path
) -> None:
    """Every path spelled into SQL is quoted: a quote in a folder's name ended the string, and
    every explorer route answered 500."""
    project = _copy(explorer, tmp_path / "a person's runs")
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    status, wheel = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK, wheel
    root = wheel["focus"]["id"]
    for path, query in (
        ("explorer/timeline", {"reading": "activity"}),
        ("explorer/timeline", {"reading": "thread", "focus": explorer.ids[summary]}),
        ("explorer/lineage", {"focus": explorer.ids[summary]}),
        ("explorer/files", {"folder": root, "scope": "beneath"}),
        ("explorer/children", {"folder": root}),
        ("explorer/entry", {"id": explorer.ids[summary]}),
    ):
        status, body = _ask(project, path, **query)
        assert status == HTTPStatus.OK, (path, body)


def test_a_failed_build_leaves_no_staging_and_publishes_nothing(
    explorer, tmp_path, monkeypatch
) -> None:
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer import projection

    project = _copy(explorer, tmp_path)
    monkeypatch.setattr(projection, "_themes_sql", lambda files: "SELECT no_such_column")
    status, body = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.INTERNAL_SERVER_ERROR, body
    assert _staged(project) == []
    assert not projection.projection_path(project, PUBLISHED_RUN).exists()
    monkeypatch.undo()
    status, body = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK, body


def test_a_later_build_removes_what_a_builder_that_is_gone_left_and_nothing_else(
    explorer, tmp_path
) -> None:
    """A staging folder whose builder's lock is free, or with no lock and old, is an orphan; one
    whose lock is held, or too young to judge, is a build in progress and is left alone. Each is
    marked as a build marks its own (one no build marked is never removed: test_owned_paths)."""
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer.projection import ORPHAN_SECONDS, STAGING_MARKER
    from ingence.jobs.locks import FileLock

    project = _copy(explorer, tmp_path)
    staging = project / ".tmp"
    made = {}
    for name in ("explorer-dead-1", "explorer-old-2", "explorer-young-3", "explorer-busy-4"):
        folder = staging / name
        folder.mkdir(parents=True)
        (folder / STAGING_MARKER).write_text("an earlier build", encoding="utf-8")
        (folder / "files.parquet").write_bytes(b"half written")
        made[name] = folder
    # The dead builder's lock is left behind, free; the busy builder's is held now.
    (staging / "explorer-dead-1.lock").write_text("")
    busy = FileLock(staging / "explorer-busy-4.lock")
    assert busy.acquire()
    old = time.time() - 2 * ORPHAN_SECONDS
    os.utime(made["explorer-old-2"], (old, old))
    # A run's own staging is another writer's, never the projection's to judge.
    (staging / f"{PUBLISHED_RUN}.partial").mkdir()
    try:
        status, body = _ask(project, "explorer/wheel")
        assert status == HTTPStatus.OK, body
        assert _staged(project) == ["explorer-busy-4", "explorer-busy-4.lock", "explorer-young-3"]
        assert (staging / f"{PUBLISHED_RUN}.partial").is_dir()
    finally:
        busy.release()


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a folder's mode stops its owner writing only on POSIX, and never stops root",
)
def test_a_project_that_cannot_be_written_is_explored_and_left_as_it_was(
    explorer, tmp_path
) -> None:
    """A project folder set to 555 answered 500 from the Wheel and the Timeline. Its projection
    is built in a temporary folder held for the process, and nothing is written to it."""
    project = _copy(explorer, tmp_path)
    before = sorted(str(path.relative_to(project)) for path in project.rglob("*"))
    project.chmod(0o555)
    try:
        for path, query in (
            ("explorer/wheel", {}),
            ("explorer/timeline", {"reading": "activity"}),
        ):
            status, body = _ask(project, path, **query)
            assert status == HTTPStatus.OK, (path, body)
    finally:
        project.chmod(0o755)
    assert sorted(str(path.relative_to(project)) for path in project.rglob("*")) == before


def test_a_build_that_finds_its_projection_published_keeps_that_one(explorer, tmp_path) -> None:
    """Two processes building one run: the second to finish keeps the first's projection and
    discards its own, rather than removing a folder the first may be reading."""
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer.projection import build_projection, projection_path
    from ingence.storage.catalog import open_catalog

    project = _copy(explorer, tmp_path)
    status, _ = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK
    meta_file = projection_path(project, PUBLISHED_RUN) / "meta.json"
    meta = json.loads(meta_file.read_text("utf-8"))
    meta["published_by"] = "the first process"
    meta_file.write_text(json.dumps(meta), "utf-8")
    with open_catalog(project, run_id=PUBLISHED_RUN) as catalog:
        second = build_projection(catalog)
    assert second.meta["published_by"] == "the first process"
    assert json.loads(meta_file.read_text("utf-8"))["published_by"] == "the first process"
    assert _staged(project) == []


def _edited_families(tmp_path: Path, monkeypatch: Any) -> Path:
    """A copy of ``explorer_kinds.yaml`` the families are read from, for a test to edit."""
    from importlib import resources

    from ingence.api.v1.explorer import categories

    tmp_path.mkdir(parents=True, exist_ok=True)
    copy = tmp_path / "explorer_kinds.yaml"
    copy.write_text(
        resources.files("ingence.api").joinpath("explorer_kinds.yaml").read_text("utf-8"), "utf-8"
    )
    monkeypatch.setattr(categories, "_kinds_file", lambda: copy)
    return copy


def _edit(families_file: Path) -> None:
    """Move one format out of the image family, as a person editing the file would."""
    text = families_file.read_text("utf-8")
    edited = text.replace("formats: [image_format, video_format,", "formats: [image_format,", 1)
    assert edited != text
    families_file.write_text(edited, "utf-8")
    later = families_file.stat().st_mtime + 5
    os.utime(families_file, (later, later))


def test_an_edit_to_the_families_file_shows_on_the_next_request(
    explorer, tmp_path, monkeypatch
) -> None:
    """``meta.json`` records the families a projection was built with; they are compared on
    reading, and the file is read again when it changes (not once per process), so an edit shows
    on the next request with no restart. The test edits the file itself, never the function that
    reads it."""
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer import projection
    from ingence.api.v1.explorer.categories import kind_families

    families_file = _edited_families(tmp_path / "families", monkeypatch)
    project = _copy(explorer, tmp_path)
    status, _ = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK
    assert projection.read_projection(project, PUBLISHED_RUN) is not None
    assert "video_format" in kind_families()["image"]
    _edit(families_file)
    assert "video_format" not in kind_families()["image"]
    assert projection.read_projection(project, PUBLISHED_RUN) is None
    status, body = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK
    assert body["projection"]["built_now"] is True
    assert projection.read_projection(project, PUBLISHED_RUN) is not None


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="a folder's mode stops its owner writing only on POSIX, and never stops root",
)
def test_a_projection_held_for_a_read_only_project_is_checked_too(
    explorer, tmp_path, monkeypatch
) -> None:
    """The projection held in a temporary folder for a project that cannot be written gets the
    families check; it is read like any other."""
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer import projection

    families_file = _edited_families(tmp_path / "families", monkeypatch)
    project = _copy(explorer, tmp_path)
    project.chmod(0o555)
    try:
        status, _ = _ask(project, "explorer/wheel")
        assert status == HTTPStatus.OK
        assert projection.read_projection(project, PUBLISHED_RUN) is not None
        _edit(families_file)
        assert projection.read_projection(project, PUBLISHED_RUN) is None
    finally:
        project.chmod(0o755)


def test_a_build_goes_on_where_the_filesystem_cannot_lock(explorer, tmp_path, monkeypatch) -> None:
    """Some network mounts refuse a lock: the build's own lock is a courtesy to later builds,
    never a condition of answering, and it leaves nothing behind."""
    import errno

    from ingence.jobs.locks import FileLock

    def refuse(self: FileLock, **_: object) -> bool:
        raise OSError(errno.ENOLCK, "no locks available")

    project = _copy(explorer, tmp_path)
    monkeypatch.setattr(FileLock, "acquire", refuse)
    status, body = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK, body
    assert _staged(project) == []


def test_an_older_projection_rebuilt_by_two_processes_at_once_is_no_error(
    explorer, tmp_path, monkeypatch
) -> None:
    """Two processes rebuilding one outdated projection both answer without an error.

    Each moves the older version aside in one step and removes it there, so a removal that
    another process has finished is nobody's error. Without that, the second process answered
    500 (2 of 4 processes in one round)."""
    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer import projection

    project = _copy(explorer, tmp_path)
    status, _ = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK
    final = projection.projection_path(project, PUBLISHED_RUN)
    meta = json.loads((final / "meta.json").read_text(encoding="utf-8"))
    meta["projection_version"] = "an older one"
    (final / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    removing = shutil.rmtree

    def raced(path: Any, ignore_errors: bool = False, **rest: Any) -> None:
        # Another process's removal of the same folder finished first.
        removing(path, ignore_errors=True)
        if not ignore_errors:
            raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(projection.shutil, "rmtree", raced)
    status, body = _ask(project, "explorer/wheel")
    assert status == HTTPStatus.OK, body
    assert body["projection"]["built_now"] is True
    assert projection.read_projection(project, PUBLISHED_RUN) is not None
    assert not [path.name for path in final.parent.iterdir() if ".old-" in path.name]


def test_a_build_never_takes_a_staging_folder_another_holds(explorer, tmp_path) -> None:
    """A build must not ignore that its staging's lock is held (another build under the same
    process identifier) and remove that build's work. It stages under another name instead, and
    leaves the held one alone."""
    import os as os_module

    from fixtures.explorer_project import PUBLISHED_RUN
    from ingence.api.v1.explorer.projection import build_projection
    from ingence.jobs.locks import FileLock
    from ingence.storage.catalog import open_catalog

    project = _copy(explorer, tmp_path)
    name = f"explorer-{PUBLISHED_RUN}-{os_module.getpid()}"
    theirs = project / ".tmp" / name
    theirs.mkdir(parents=True)
    (theirs / "files.parquet").write_bytes(b"another build's work")
    held = FileLock(theirs.with_name(name + ".lock"))
    assert held.acquire()
    try:
        with open_catalog(project, run_id=PUBLISHED_RUN) as catalog:
            built = build_projection(catalog)
        assert (built.path / "meta.json").is_file()
        assert (theirs / "files.parquet").read_bytes() == b"another build's work"
        assert _staged(project) == [name, f"{name}.lock"]
    finally:
        held.release()
