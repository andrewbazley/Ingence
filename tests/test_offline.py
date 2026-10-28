"""The offline loop needs no root, and what does need one refuses clearly.

``index --rebind``, ``infer``, ``record import`` and every Dashboard view make no reach for the
root, and ``fix`` checks it once to say it is missing. A walk and a dive refuse before they stage
anything. Otherwise the walk would publish a run stating every file of the root deleted, and the
dive would record every file it tried as a failed reading.

The tests cover the class, not the share: a project whose root does not exist, with a guard that
records every open, list or ``stat`` of a path beneath the old root, in this process and in the
Dashboard's threads.
"""

from __future__ import annotations

import builtins
import csv
import io
import json
import os
import shutil
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fixtures.revision_4 import write_revision
from ingence.api.server import DashboardServer
from ingence.cli.main import app
from ingence.core.project import Project, RootUnreachableError, unreachable_roots
from ingence.infer.run import run_inference
from ingence.record.sheets import import_sheet
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import read_pointer


def _pool(base: Path) -> Path:
    pool = base / "pool"
    (pool / "kelt_ctrl" / "raw").mkdir(parents=True)
    (pool / "kelt_ctrl" / "analysis").mkdir(parents=True)
    (pool / "untitled folder").mkdir(parents=True)
    (pool / "kelt_ctrl" / "raw" / "plate_01_vorn.tif").write_bytes(b"II*\x00" + b"\x00" * 60)
    (pool / "kelt_ctrl" / "analysis" / "vorn_summary.csv").write_text("a,b\n1,2\n")
    (pool / "kelt_ctrl" / "analysis" / "vorn_processed.csv").write_text("a,b\n3,4\n")
    (pool / "untitled folder" / "brisa notes.txt").write_text("hello")
    return pool


@contextmanager
def _guarded(prefix: Path):
    """Record every open, list or ``stat`` of a path beneath ``prefix``, and let it proceed."""
    touched: list[tuple[str, str]] = []
    spelled = str(prefix)
    real = {
        "stat": os.stat,
        "lstat": os.lstat,
        "scandir": os.scandir,
        "listdir": os.listdir,
        "open": os.open,
        "readlink": os.readlink,
        "access": os.access,
        "builtins.open": builtins.open,
    }

    def wrapped(name, function):
        def guarded(*args, **kwargs):
            target = args[0] if args else kwargs.get("path")
            if isinstance(target, (str, bytes, os.PathLike)):
                text = os.fsdecode(target)
                if text == spelled or text.startswith(spelled + os.sep):
                    touched.append((name, text))
            return function(*args, **kwargs)

        return guarded

    for name in ("stat", "lstat", "scandir", "listdir", "open", "readlink", "access"):
        setattr(os, name, wrapped(f"os.{name}", real[name]))
    builtins.open = wrapped("builtins.open", real["builtins.open"])
    io.open = builtins.open
    try:
        yield touched
    finally:
        for name in ("stat", "lstat", "scandir", "listdir", "open", "readlink", "access"):
            setattr(os, name, real[name])
        builtins.open = real["builtins.open"]
        io.open = real["builtins.open"]


@pytest.fixture
def offline(tmp_path: Path):
    """A project indexed, dived and inferred under revision 4, then its root taken away."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    shutil.move(pool, tmp_path / "unmounted")
    return Project.open(project.path), pool


def test_the_offline_loop_reaches_for_no_root(offline, tmp_path) -> None:
    """Rebind, infer, a sheet's import and every Dashboard view, with the root gone."""
    project, root = offline
    with open_catalog(project.path) as catalog:
        finding = catalog.query(
            "SELECT finding_id FROM findings ORDER BY finding_id LIMIT 1"
        ).fetchone()
    sheet = tmp_path / "sheet.csv"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["finding_id", "verdict (correct | wrong | cannot_say)", "note"])
        writer.writerow([finding[0] if finding else "", "", "a note"])

    with _guarded(root) as touched:
        rebound = run_census(
            project, run_id="run_20260101T000004Z_444444444444", started_at="t", rebind=True
        )
        assert rebound.published
        inferred = run_inference(
            project, run_id="run_20260101T000005Z_555555555555", started_at="t"
        )
        assert not inferred.cancelled
        with open_catalog(project.path) as catalog:
            import_sheet(catalog, project.path, sheet, actor="kelt", occurred_at="t", check=True)
        with open_catalog(project.path) as catalog:
            folder = catalog.query(
                "SELECT entry_id FROM entries WHERE entry_type = 'directory' AND depth = 1 "
                "ORDER BY relative_path LIMIT 1"
            ).fetchone()[0]
            entry = catalog.query(
                "SELECT entry_id FROM entries WHERE entry_type = 'file' "
                "ORDER BY relative_path LIMIT 1"
            ).fetchone()[0]
        server = DashboardServer(project.path, port=0)
        server.start()
        try:
            for route in (
                "/api/project",
                "/api/inventory",
                "/api/rules",
                "/api/runs",
                "/api/waiting",
                "/api/candidates",
                f"/api/folder/{folder}",
                f"/api/entry/{entry}",
                "/api/logical-context",
                "/api/coverage",
                "/api/record",
                "/api/review",
            ):
                with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
                    assert response.status == 200, route
                    json.loads(response.read())
        finally:
            server.stop()
    assert touched == [], touched


def test_the_guard_sees_a_reach_when_there_is_one(offline) -> None:
    """The positive control: silence from the guard means nothing reached, not a blind guard."""
    project, root = offline
    with _guarded(root) as touched:
        assert unreachable_roots(project.identity.roots)
        assert not (root / "kelt_ctrl").exists()
    assert [name for name, _ in touched] == ["os.stat", "os.stat"]


def test_a_walk_refuses_a_root_it_cannot_reach_and_writes_nothing(offline) -> None:
    """Unmounted is not deleted: no run stating every file of the root gone."""
    project, root = offline
    before = read_pointer(project.path)
    assert unreachable_roots(project.identity.roots)
    with pytest.raises(RootUnreachableError) as refused:
        run_census(project, run_id="run_20260101T000006Z_666666666666", started_at="t")
    assert str(root) in str(refused.value)
    assert "--rebind" in str(refused.value)
    assert read_pointer(project.path) == before
    assert not list((project.path / ".tmp").glob("run_20260101T000006Z*"))


def test_a_dive_refuses_a_root_it_cannot_reach_and_writes_nothing(offline) -> None:
    """Unreachable is not unreadable: no run recording failed readings of files out of reach."""
    project, root = offline
    before = read_pointer(project.path)
    with pytest.raises(RootUnreachableError) as refused:
        run_deepen(project, run_id="run_20260101T000007Z_777777777777", started_at="t")
    assert str(root) in str(refused.value)
    assert read_pointer(project.path) == before
    assert not list((project.path / ".tmp").glob("run_20260101T000007Z*"))


def test_the_command_line_says_so_and_exits_non_zero(offline) -> None:
    """The refusal reaches a person as a sentence naming the root and what still works."""
    project, root = offline
    runner = CliRunner()
    for verb in (["index"], ["dive", "--budget", "1"]):
        result = runner.invoke(app, [*verb, "-p", str(project.path)])
        assert result.exit_code == 1, result.output
        assert "cannot be reached" in " ".join(result.output.split())
    rebound = runner.invoke(app, ["index", "--rebind", "-q", "-p", str(project.path)])
    assert rebound.exit_code == 0, rebound.output
    assert not root.exists()


def test_a_root_that_is_there_is_not_refused(tmp_path) -> None:
    """The check reads the root's own entry and passes a root that is present."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert unreachable_roots(project.identity.roots) == ()
    (tmp_path / "a_file").write_text("x")
    moved = Project.create(tmp_path / "other", created_at="t", roots=[pool])
    shutil.rmtree(pool)
    (tmp_path / "pool").write_text("now a file")
    ((declaration, why),) = unreachable_roots(moved.identity.roots)
    assert declaration.mount_path == str(pool.resolve())
    assert why == "it is not a folder"
