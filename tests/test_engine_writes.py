"""The engine replaces its own files through new files of names no other writer uses.

To replace ``project.json``, ``settings.yaml`` or ``CURRENT``, the engine writes a new file and
moves it into place. A fixed temporary name (``.project.json.incoming`` and the like) written with
``write_text``, which truncates, would destroy a file a person kept at that name, and a link or a
hard link there would be written through, into the file it led to. Held here, over invented names: a
folder holding such a name is never made a project, every writer leaves a link planted at its old
name and the file the link leads to as they were, a crashed write's leftover is never read as data,
and a tripwire over the package's source for the next fixed name opened with truncation.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ingence
from ingence.core.project import Project, ProjectExistsError
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Callable

RUN = "run_20260101T000001Z_111111111111"
SENTINEL_TEXT = b"a person's own words, kept outside the project\n"


def _pool(base: Path) -> Path:
    pool = base / "pool"
    pool.mkdir(parents=True)
    (pool / "alpha.txt").write_text("alpha words\n", encoding="utf-8")
    (pool / "beta.txt").write_text("beta words\n", encoding="utf-8")
    return pool


def _censused(base: Path) -> Project:
    project = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(project, run_id=RUN, started_at="t")
    return project


# -- a folder holding a temporary name of a project's file is never made a project --------------


@pytest.mark.parametrize(
    "name",
    [
        ".project.json.incoming",
        ".settings.yaml.incoming",
        ".settings.dashboard.yaml.incoming",
        ".CURRENT.incoming",
    ],
)
def test_a_folder_holding_a_project_s_temporary_name_is_not_made_a_project(
    tmp_path: Path, name: str
) -> None:
    folder = tmp_path / "beta"
    folder.mkdir()
    (folder / name).write_bytes(SENTINEL_TEXT)
    with pytest.raises(ProjectExistsError, match=name.replace(".", r"\.")):
        Project.create(folder, created_at="t")
    assert (folder / name).read_bytes() == SENTINEL_TEXT
    assert sorted(path.name for path in folder.iterdir()) == [name]


# -- every writer leaves a link at its old name, and what it leads to, as they were --------------


@dataclass(frozen=True)
class Writer:
    """One of the engine's writers: where its fixed temporary name was, and how to run it."""

    #: Makes what the writer needs and returns the old fixed name and the call that writes.
    prepare: Callable[[Path], tuple[Path, Callable[[], object]]]


def _pointer(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.storage.commit import read_pointer, write_pointer

    project = _censused(base)
    pointer = read_pointer(project.path)
    assert pointer is not None
    return project.path / ".CURRENT.incoming", lambda: write_pointer(project.path, pointer)


def _identity(base: Path) -> tuple[Path, Callable[[], object]]:
    project = Project.create(base / "project", created_at="t")
    return project.path / ".project.json.incoming", lambda: project.rename("beta")


def _upgrade(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.core.settings import upgrade_settings_file

    base.mkdir(parents=True, exist_ok=True)
    path = base / "settings.yaml"
    path.write_text("schema_version: '1.10'\nhash:\n  mode: none\n", encoding="utf-8")

    def run() -> None:
        assert upgrade_settings_file(path)

    return base / ".settings.yaml.incoming", run


def _overlay(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.workspace.settings_overlay import write_dashboard_settings

    project = Project.create(base / "project", created_at="t")
    return project.path / ".settings.dashboard.yaml.incoming", lambda: write_dashboard_settings(
        project.path, {"scan": {"workers": 12}}, provenance={"scan.workers": "set by a person"}
    )


def _draft(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.workspace.drafts import save_draft

    project = Project.create(base / "project", created_at="t")
    drafts = project.path / "workspace" / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    return drafts / "settings.json.incoming", lambda: save_draft(
        project.path, "settings", {"scan": {"workers": 5}}
    )


def _progress(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.scan.progress import ProgressReporter, ProgressSnapshot

    folder = base / ".tmp"
    folder.mkdir(parents=True)
    path = folder / f"{RUN}.progress.json"
    reporter = ProgressReporter(ProgressSnapshot(run_id=RUN, stage="census"), path=path)
    return folder / f".{path.name}.incoming", reporter.emit


def _decider(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.api.decider import DECIDER_FILE, write_decider

    base.mkdir(parents=True, exist_ok=True)
    return base / f".{DECIDER_FILE}.{os.getpid()}.incoming", lambda: write_decider(
        "a person", set_at="t", config_home=base
    )


def _recent(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.api.recent import RECENT_FILE, remember_project

    project = Project.create(base / "project", created_at="t")
    home = base / "configuration"
    home.mkdir()
    return home / f".{RECENT_FILE}.{os.getpid()}.incoming", lambda: remember_project(
        project.path, opened_at="t", name=None, config_home=home
    )


def _holder(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.jobs.locks import FileLock

    base.mkdir(parents=True, exist_ok=True)
    lock = FileLock(base / "alpha.lock")

    def run() -> None:
        assert lock.acquire(holder={"command": "a test"})
        lock.release()

    return base / f"alpha.lock.holder.{os.getpid()}", run


def _themes(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.themes import projection as themes

    project = _censused(base)
    themes.themes_projection(project.path, RUN)
    cached = sorted(themes.projection_path(project.path, RUN).glob("*.json"))
    assert len(cached) == 1
    cached[0].unlink()

    def run() -> None:
        with themes._LOCK:
            themes._MEMORY.clear()
        themes.themes_projection(project.path, RUN)
        assert cached[0].is_file()

    return cached[0].with_name(f".{cached[0].name}.{os.getpid()}.incoming"), run


def _record(base: Path) -> tuple[Path, Callable[[], object]]:
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event, fragment_name, next_sequence

    project = _censused(base)
    old = project.path / ".tmp" / f"{fragment_name(next_sequence(project.path))}.incoming"
    return old, lambda: append_event(
        project.path,
        kind=RecordEventKind.CONFIRM,
        actor="a person",
        occurred_at="2026-01-02T03:04:05Z",
        target="entry_alpha",
        against_run_id=RUN,
    )


WRITERS = {
    "the run pointer": Writer(_pointer),
    "the project's identity": Writer(_identity),
    "the settings upgrade": Writer(_upgrade),
    "the Dashboard settings overlay": Writer(_overlay),
    "a draft": Writer(_draft),
    "the progress snapshot": Writer(_progress),
    "who decides": Writer(_decider),
    "the recent projects": Writer(_recent),
    "a lock's holder": Writer(_holder),
    "the themes cache": Writer(_themes),
    "the Record": Writer(_record),
}


def _plant(kind: str, at: Path, sentinel: Path) -> None:
    try:
        if kind == "symbolic link":
            at.symlink_to(sentinel)
        else:
            os.link(sentinel, at)
    except (OSError, NotImplementedError):
        pytest.skip(f"this system does not let a test make a {kind}")


@pytest.mark.parametrize("kind", ["symbolic link", "hard link"])
@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_link_at_a_writer_s_old_temporary_name_is_never_written_through(
    tmp_path: Path, writer: str, kind: str
) -> None:
    sentinel = tmp_path / "outside" / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_bytes(SENTINEL_TEXT)
    old, run = WRITERS[writer].prepare(tmp_path / "inside")
    _plant(kind, old, sentinel)
    run()
    assert sentinel.read_bytes() == SENTINEL_TEXT
    if kind == "symbolic link":
        # The link still leads to the sentinel. Compared as files, not as text: on Windows a link's
        # target reads back with the long-path prefix (\\?\C:\...), so its spelling differs.
        assert old.is_symlink() and os.path.samefile(old, sentinel)
    else:
        assert not old.is_symlink() and os.path.samefile(old, sentinel)
        assert old.read_bytes() == SENTINEL_TEXT


# -- a crashed write's leftover is the project's, never a person's file -------------------------


@pytest.mark.parametrize("where", ["ingence", "."])
def test_a_crashed_write_s_leftover_at_a_project_s_top_is_never_indexed(
    tmp_path: Path, where: str
) -> None:
    """The project lives inside the folder it reads, or is that folder; hidden files are read."""
    from ingence.core.settings import ScanSettings, Settings

    pool = _pool(tmp_path)
    home = pool / where if where != "." else pool
    project = Project.create(
        home,
        created_at="t",
        roots=[pool],
        settings=Settings(scan=ScanSettings(include_hidden=True)),
    )
    leftovers = [
        f".project.json.{os.getpid()}.0a1b2c3d.incoming",
        f".settings.dashboard.yaml.{os.getpid()}.4e5f6a7b.incoming",
    ]
    for name in leftovers:
        (project.path / name).write_text("{}", encoding="utf-8")
    run_census(project, run_id=RUN, started_at="t")
    with open_catalog(project.path) as catalog:
        names = {str(row[0]) for row in catalog.query("SELECT name FROM entries").fetchall()}
    assert "alpha.txt" in names
    assert not names & set(leftovers)


# -- the tripwire ---------------------------------------------------------------------------------

#: The one module that may build a temporary name and open it: every writer goes through it.
WRITER_MODULE = "core/destinations.py"
_TRUNCATING = {"w", "wb", "w+", "wb+", "w+b", "wt", "x", "xb"}


def _fixed_temporary_names_opened(tree: ast.Module, source: str) -> list[int]:
    """The lines where a name holding ``incoming`` is opened with ``write_text``, ``write_bytes``
    or ``open`` to write, in any function (or at the module's top)."""
    found: list[int] = []
    scopes: list[ast.AST] = [tree]
    scopes += [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    for scope in scopes:
        body = list(ast.walk(scope))
        held = {
            target.id
            for node in body
            if isinstance(node, ast.Assign)
            and "incoming" in (ast.get_source_segment(source, node.value) or "")
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        def names_one(node: ast.AST, held: set[str] = held) -> bool:
            spelled = ast.get_source_segment(source, node) or ""
            return "incoming" in spelled or (isinstance(node, ast.Name) and node.id in held)

        for node in body:
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            mode = next(
                (
                    argument.value
                    for argument in [
                        *node.args,
                        *(k.value for k in node.keywords if k.arg == "mode"),
                    ]
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                ),
                None,
            )
            if isinstance(called, ast.Attribute) and called.attr in ("write_text", "write_bytes"):
                opened = names_one(called.value)
            elif isinstance(called, ast.Attribute) and called.attr == "open":
                opened = mode in _TRUNCATING and names_one(called.value)
            elif isinstance(called, ast.Name) and called.id == "open" and node.args:
                opened = mode in _TRUNCATING and names_one(node.args[0])
            else:
                opened = False
            if opened:
                found.append(node.lineno)
    return sorted(set(found))


def test_no_fixed_temporary_name_is_opened_with_truncation_outside_the_writer() -> None:
    package = Path(ingence.__file__).parent
    found: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        if relative == WRITER_MODULE:
            continue
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        found += [
            f"{relative}:{number}: {lines[number - 1].strip()}"
            for number in _fixed_temporary_names_opened(ast.parse(source), source)
        ]
    assert not found, (
        "a temporary name is opened with truncation; write with "
        "core.destinations.write_atomically instead:\n" + "\n".join(found)
    )


def test_the_tripwire_finds_a_fixed_temporary_name_opened_with_truncation() -> None:
    source = (
        "def f(path):\n"
        "    staging = path.with_name(f'.{path.name}.incoming')\n"
        "    staging.write_text('x')\n"
        "    with open(staging, 'wb') as handle:\n"
        "        handle.write(b'x')\n"
        "    (path / 'a.incoming').write_bytes(b'x')\n"
        "    with staging.open('r+b') as handle:\n"
        "        pass\n"
        "    path.write_text('x')\n"
    )
    assert _fixed_temporary_names_opened(ast.parse(source), source) == [3, 4, 6]


def test_the_project_s_temporary_names_are_its_own() -> None:
    from ingence.storage.layout import is_project_name

    assert is_project_name(".project.json.incoming")
    assert is_project_name(f".CURRENT.{os.getpid()}.0a1b2c3d.incoming")
    assert is_project_name(".settings.dashboard.yaml.7.0a1b2c3d.incoming")
    assert not is_project_name(".alpha.txt.7.0a1b2c3d.incoming")
    assert not is_project_name(".project.json.seven.0a1b2c3d.incoming")
