"""A build names its commit.

A package built from a checkout carries the commit it was built from, so a wheel's
``/api/v1/version`` and its run records say which build they came from. A folder that is not a
checkout of its own gives none. ``commit_of`` is the build hook's one decision. The build itself
is checked by hand where a network can fetch the build backend.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
HOOK = REPOSITORY / "tools" / "release" / "hatch_build.py"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ingence_hatch_build", HOOK)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(folder: Path, *arguments: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(folder),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@needs_git
def test_a_checkout_gives_its_commit_and_marks_a_changed_file(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    head = _git(tmp_path, "rev-parse", "HEAD")
    hook = _hook()
    assert hook.commit_of(tmp_path) == head
    (tmp_path / "a.txt").write_text("two\n")
    assert hook.commit_of(tmp_path) == f"{head}+dirty"
    (tmp_path / "new.txt").write_text("untracked\n")
    _git(tmp_path, "checkout", "--", "a.txt")
    assert hook.commit_of(tmp_path) == head, "an untracked file does not make a build dirty"


@needs_git
def test_a_folder_inside_another_checkout_takes_none_of_its_commit(tmp_path: Path) -> None:
    """A source distribution unpacked inside some repository must not stamp that repository."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    unpacked = tmp_path / "ingence-0.2.0.dev3"
    unpacked.mkdir()
    assert _hook().commit_of(unpacked) is None


def test_a_folder_that_is_no_checkout_gives_none(tmp_path: Path) -> None:
    assert _hook().commit_of(tmp_path) is None


def test_the_hook_is_wired_into_the_build_and_the_source_distribution() -> None:
    held = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    build = held["tool"]["hatch"]["build"]
    assert build["hooks"]["custom"]["path"] == "tools/release/hatch_build.py"
    assert "tools/release/hatch_build.py" in build["targets"]["sdist"]["include"], (
        "a wheel built from the source distribution runs the hook too, so it must be there"
    )
    assert HOOK.is_file()
