"""A project may live anywhere, inside the folder it reads included, and never reads itself.

A person keeps a project wherever they like: beside the data, inside it, as the data folder
itself, or on another drive. The census leaves out the project it is writing by the folder's
device and file number (and its path), wherever it lies, from the very first run, and every other
Ingence project by its layout, whether or not it has run. Nothing the engine writes while it runs
(the publish lock, the jobs ledger, the workspace) ever enters a census. Every name here is
invented.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project, ProjectExistsError
from ingence.core.schema import RecognisedAs
from ingence.scan.census import run_census, walk_root
from ingence.scan.dry_run import dry_run
from ingence.scan.recognise import OwnProject
from ingence.storage.catalog import open_catalog
from ingence.storage.layout import is_project_name

if TYPE_CHECKING:
    from pathlib import Path

FIRST = "run_20260101T000001Z_111111111111"
SECOND = "run_20260101T000002Z_222222222222"
THIRD = "run_20260101T000003Z_333333333333"


def _pool(base: Path) -> Path:
    """A small invented data folder."""
    pool = base / "zeph"
    (pool / "kelt").mkdir(parents=True)
    (pool / "vorn").mkdir()
    (pool / "kelt" / "brisa_01.tif").write_bytes(b"a" * 40)
    (pool / "vorn" / "quolt.csv").write_bytes(b"x,y\n1,2\n")
    (pool / "tamen.txt").write_bytes(b"notes")
    return pool


DATA = {".", "kelt", "vorn", "kelt/brisa_01.tif", "vorn/quolt.csv", "tamen.txt"}


def _paths(project: Project, run_id: str | None = None) -> set[str]:
    """Every path a run's Index holds, the gone included."""
    with open_catalog(project.path, run_id=run_id) as catalog:
        rows = catalog.query("SELECT relative_path FROM entries").fetchall()
    return {str(row[0]) for row in rows}


def _left_out(report: object) -> list[tuple[str, RecognisedAs, bool]]:
    return [
        (path.relative_path, path.recognised_as, path.indexed)
        for path in report.recognised  # type: ignore[attr-defined]
    ]


def _engine_writes_while_running(project: Path) -> None:
    """What a Dashboard and its jobs write into a project, beside what a run writes."""
    (project / "jobs").mkdir(exist_ok=True)
    (project / "jobs" / "worker.lock").write_text("")
    (project / "jobs" / "ledger.sqlite3").write_bytes(b"\x00" * 16)
    (project / "workspace" / "drafts").mkdir(parents=True, exist_ok=True)
    (project / "workspace" / "drafts" / "rules.json").write_text("{}")
    (project / "projections").mkdir(exist_ok=True)
    (project / "advice").mkdir(exist_ok=True)
    (project / "listings").mkdir(exist_ok=True)
    (project / "settings.dashboard.yaml").write_text("scan: {}\n")
    (project / ".CURRENT.incoming").write_text("")


# ---------------------------------------------------------------------------
# Where a project may be made.
# ---------------------------------------------------------------------------


def test_a_project_may_be_made_inside_the_folder_it_reads(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(pool / "kelt" / "ingence", created_at="t", roots=[pool])
    assert project.identity.roots[0].mount_path == str(pool.resolve())


def test_a_project_may_be_the_folder_it_reads(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(pool, created_at="t", roots=[pool])
    assert (pool / "project.json").is_file()
    assert (pool / "tamen.txt").read_bytes() == b"notes"
    assert project.path == pool.resolve()


def test_a_project_is_never_made_over_a_persons_own_file_or_folder(tmp_path: Path) -> None:
    """A folder of the person's named as a project's is theirs: nothing is written into it."""
    pool = _pool(tmp_path)
    (pool / "runs").mkdir()
    (pool / "runs" / "brisa_run_1.csv").write_bytes(b"1")
    with pytest.raises(ProjectExistsError, match="runs"):
        Project.create(pool, created_at="t", roots=[pool])
    assert not (pool / "project.json").exists()
    assert sorted(path.name for path in pool.iterdir()) == ["kelt", "runs", "tamen.txt", "vorn"]

    (pool / "record").mkdir()
    (pool / "record" / "brisa.csv").write_bytes(b"2")
    with pytest.raises(ProjectExistsError, match="record, runs, names a project writes"):
        Project.create(pool, created_at="t", roots=[pool])

    elsewhere = tmp_path / "vorn_notes"
    elsewhere.mkdir()
    (elsewhere / "settings.yaml").write_text("mine: 1\n")
    with pytest.raises(ProjectExistsError, match=r"settings\.yaml"):
        Project.create(elsewhere, created_at="t")
    assert (elsewhere / "settings.yaml").read_text() == "mine: 1\n"


def test_an_empty_folder_of_a_projects_name_is_used_for_it(tmp_path: Path) -> None:
    """An interrupted create, or a folder made ready for it, holds nothing to protect."""
    place = tmp_path / "kelt_project"
    (place / "runs").mkdir(parents=True)
    Project.create(place, created_at="t")
    assert (place / "project.json").is_file()


def _folds_case(folder: Path) -> bool:
    """Whether a folder's disk takes a name spelled in another case for the same file."""
    probe = folder / "Brisa_Case_Probe"
    probe.mkdir()
    try:
        return (folder / "brisa_case_probe").exists()
    finally:
        probe.rmdir()


def test_an_empty_folder_named_like_a_projects_in_another_case_is_taken(tmp_path: Path) -> None:
    """On a disk that ignores case, a person's empty Record would become the project's Record."""
    if not _folds_case(tmp_path):
        pytest.skip("this disk tells Record and record apart, so the project writes beside it")
    pool = _pool(tmp_path)
    (pool / "Record").mkdir()
    with pytest.raises(ProjectExistsError, match="Record, a name a project writes"):
        Project.create(pool, created_at="t", roots=[pool])
    assert not (pool / "project.json").exists()
    assert sorted(path.name for path in pool.iterdir()) == ["Record", "kelt", "tamen.txt", "vorn"]


def test_a_persons_own_project_json_is_not_called_a_project(tmp_path: Path) -> None:
    """A web project's project.json is a person's file, refused in the words for one."""
    pool = _pool(tmp_path)
    (pool / "project.json").write_text("{}")
    with pytest.raises(ProjectExistsError, match=r"project\.json, a name a project writes"):
        Project.create(pool, created_at="t", roots=[pool])
    made = Project.create(tmp_path / "ingence", created_at="t")
    with pytest.raises(ProjectExistsError, match="a project already exists"):
        Project.create(made.path, created_at="t")


def test_every_folder_a_project_writes_is_made_with_it(tmp_path: Path) -> None:
    """Each is the project's before a person can make one of its name in a shared folder."""
    from ingence.storage.layout import PROJECT_FOLDERS

    project = Project.create(tmp_path / "ingence", created_at="t")
    assert {"jobs", "workspace", "projections", "listings", "imports"} <= PROJECT_FOLDERS
    assert "advice" not in PROJECT_FOLDERS  # nothing writes there since the suggestion layer went
    for name in PROJECT_FOLDERS:
        assert (project.path / name).is_dir(), name


def test_a_folder_with_the_names_a_project_writes_is_not_called_a_project(tmp_path: Path) -> None:
    """project.json, settings.yaml and runs together are a person's: refused as names."""
    pool = _pool(tmp_path)
    (pool / "project.json").write_text("{}")
    (pool / "settings.yaml").write_text("a: 1\n")
    (pool / "runs").mkdir()
    (pool / "runs" / "brisa_run_1.csv").write_bytes(b"1")
    with pytest.raises(ProjectExistsError, match="names a project writes"):
        Project.create(pool, created_at="t", roots=[pool])


# ---------------------------------------------------------------------------
# What a census leaves out.
# ---------------------------------------------------------------------------


def test_a_project_inside_its_root_is_left_out_from_its_first_run(tmp_path: Path) -> None:
    """Before a first run the project holds no pointer, and it was indexed. It is left out."""
    pool = _pool(tmp_path)
    project = Project.create(pool / "vorn" / "ingence", created_at="t", roots=[pool])
    _engine_writes_while_running(project.path)

    first = run_census(project, run_id=FIRST, started_at="t")
    second = run_census(project, run_id=SECOND, started_at="t")

    assert _paths(project, FIRST) == DATA
    assert _paths(project, SECOND) == DATA, "nothing reads as gone on the second run"
    assert second.counts.tombstones == 0
    expected = [("vorn/ingence", RecognisedAs.THIS_PROJECT, False)]
    assert _left_out(first) == _left_out(second) == expected


def test_a_project_that_is_its_root_leaves_out_its_own_names_and_nothing_else(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    project = Project.create(pool, created_at="t", roots=[pool])
    _engine_writes_while_running(project.path)

    first = run_census(project, run_id=FIRST, started_at="t")
    second = run_census(project, run_id=SECOND, started_at="t")

    assert _paths(project, FIRST) == DATA
    assert _paths(project, SECOND) == DATA
    names = {path for path, _, _ in _left_out(second)}
    assert {"project.json", "settings.yaml", "runs", "record", "jobs", "workspace"} <= names
    assert {kind for _, kind, _ in _left_out(second)} == {RecognisedAs.THIS_PROJECT}
    assert "CURRENT" in names, "the pointer the first run published"
    assert "project.json" in {path for path, _, _ in _left_out(first)}


def test_a_projects_own_names_deeper_in_the_root_are_data(tmp_path: Path) -> None:
    """Only the project's own folder holds its names. A folder called runs elsewhere is data."""
    pool = _pool(tmp_path)
    (pool / "kelt" / "runs").mkdir()
    (pool / "kelt" / "runs" / "brisa_02.tif").write_bytes(b"b")
    project = Project.create(pool, created_at="t", roots=[pool])
    run_census(project, run_id=FIRST, started_at="t")
    assert {"kelt/runs", "kelt/runs/brisa_02.tif"} <= _paths(project)


def test_a_root_inside_a_project_is_indexed_whole(tmp_path: Path) -> None:
    home = tmp_path / "kelt_work"
    home.mkdir()
    pool = _pool(home)
    project = Project.create(home, created_at="t", roots=[pool])
    _engine_writes_while_running(project.path)

    report = run_census(project, run_id=FIRST, started_at="t")

    assert _paths(project) == DATA
    assert report.recognised == ()


def test_a_projects_folder_holding_a_persons_files_keeps_them_as_data(tmp_path: Path) -> None:
    """Made in a folder that already held files, the project leaves out its own names only."""
    pool = _pool(tmp_path)
    project = Project.create(pool / "kelt", created_at="t", roots=[pool])

    report = run_census(project, run_id=FIRST, started_at="t")

    assert _paths(project) == DATA
    left = {path for path, _, _ in _left_out(report)}
    assert {"kelt/project.json", "kelt/runs"} <= left
    assert "kelt/brisa_01.tif" not in left


def test_this_project_is_left_out_even_where_projects_are_indexed_as_data(
    tmp_path: Path,
) -> None:
    """``scan.index_recognised_outputs`` reads other projects as data, never the one writing."""
    from ingence.core.settings import ScanSettings, Settings

    pool = _pool(tmp_path)
    settings = Settings(scan=ScanSettings(index_recognised_outputs=True))
    project = Project.create(pool / "ingence", created_at="t", roots=[pool], settings=settings)
    report = run_census(project, run_id=FIRST, started_at="t")
    assert _paths(project) == DATA
    assert _left_out(report) == [("ingence", RecognisedAs.THIS_PROJECT, False)]


def test_another_project_is_recognised_before_its_first_run(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    Project.create(pool / "vorn" / "other", created_at="t", roots=[tmp_path / "elsewhere"])
    project = Project.create(tmp_path / "ingence", created_at="t", roots=[pool])

    report = run_census(project, run_id=FIRST, started_at="t")

    assert _paths(project) == DATA
    assert _left_out(report) == [("vorn/other", RecognisedAs.PROJECT, False)]


def test_a_rebind_and_a_later_walk_keep_the_project_out(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(pool / "vorn" / "ingence", created_at="t", roots=[pool])
    run_census(project, run_id=FIRST, started_at="t")
    rebound = run_census(project, run_id=SECOND, started_at="t", rebind=True)
    _engine_writes_while_running(project.path)
    walked = run_census(project, run_id=THIRD, started_at="t")

    assert _paths(project, SECOND) == DATA
    assert _paths(project, THIRD) == DATA
    assert walked.counts.tombstones == 0
    assert _left_out(rebound) == [("vorn/ingence", RecognisedAs.THIS_PROJECT, False)]


def test_a_projects_folder_in_a_persons_spelling_is_left_out_where_the_disk_ignores_case(
    tmp_path: Path,
) -> None:
    """A project made before its folders were made at once can hold one in a person's spelling."""
    if not _folds_case(tmp_path):
        pytest.skip("this disk tells Projections and projections apart")
    pool = _pool(tmp_path)
    project = Project.create(pool, created_at="t", roots=[pool])
    (project.path / "projections").rmdir() if (project.path / "projections").is_dir() else None
    (pool / "Projections" / "explorer").mkdir(parents=True)
    (pool / "Projections" / "explorer" / "files.parquet").write_bytes(b"x")

    report = run_census(project, run_id=FIRST, started_at="t")
    walked = dry_run(pool, project=project.path)

    assert _paths(project) == DATA
    assert "Projections" in {path for path, _, _ in _left_out(report)}
    assert {"path": "Projections", "as": "this_project"} in walked["left_out"]


def test_the_persons_exclusions_and_hidden_names_hold_for_the_projects_folder(
    tmp_path: Path,
) -> None:
    """A folder the settings leave out is left out, the project in it or not. The dry run agrees."""
    from ingence.core.settings import ScanSettings, Settings

    for name, settings in (
        ("proj", Settings(scan=ScanSettings(exclude_globs=("proj",)))),
        (".ingence", Settings()),
    ):
        pool = _pool(tmp_path / name.strip("."))
        project = Project.create(pool / name, created_at="t", roots=[pool], settings=settings)
        (project.path / "vorn_notes.txt").write_text("mine")

        report = run_census(project, run_id=FIRST, started_at="t")
        walked = dry_run(
            pool,
            project=project.path,
            exclude_globs=settings.scan.exclude_globs,
            include_hidden=settings.scan.include_hidden,
        )

        assert _paths(project) == DATA, name
        assert _left_out(report) == [(name, RecognisedAs.THIS_PROJECT, False)], name
        assert walked["left_out"] == [{"path": name, "as": "this_project"}], name


def test_a_hidden_file_in_the_projects_folder_is_no_ones_with_hidden_names_read(
    tmp_path: Path,
) -> None:
    """With hidden names read, the census and the dry run still agree on a .DS_Store."""
    from ingence.core.settings import ScanSettings, Settings

    pool = _pool(tmp_path)
    settings = Settings(scan=ScanSettings(include_hidden=True))
    project = Project.create(
        pool / "vorn" / "ingence", created_at="t", roots=[pool], settings=settings
    )
    (project.path / ".DS_Store").write_bytes(b"\x00")

    report = run_census(project, run_id=FIRST, started_at="t")
    walked = dry_run(pool, project=project.path, include_hidden=True)

    assert _paths(project) == DATA
    assert _left_out(report) == [("vorn/ingence", RecognisedAs.THIS_PROJECT, False)]
    assert walked["left_out"] == [{"path": "vorn/ingence", "as": "this_project"}]


# ---------------------------------------------------------------------------
# How the project is known: its folder's device and file number, or its path.
# ---------------------------------------------------------------------------


def _stand_in(pool: Path) -> Path:
    """A folder holding a project's names and no layout another project is recognised by."""
    folder = pool / "vorn" / "lamp"
    (folder / "runs").mkdir(parents=True)
    (folder / "project.json").write_text("{}")
    return folder


def _walked(pool: Path, own: OwnProject) -> tuple[set[str], list[tuple[str, RecognisedAs]]]:
    holder = Project.create(pool.parent / "holder", created_at="t", roots=[pool])
    entries: set[str] = set()
    left: list[tuple[str, RecognisedAs]] = []
    for chunk in walk_root(
        holder.identity.roots[0], settings=holder.settings, run_id=FIRST, own_project=own
    ):
        entries |= {entry.relative_path for entry in chunk.entries}
        left += [(path.relative_path, path.recognised_as) for path in chunk.recognised]
    return entries, left


def test_the_project_is_known_by_its_folders_number_whatever_its_path_is_written_as(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path)
    folder = os.stat(_stand_in(pool))
    own = OwnProject(
        path=tmp_path / "written" / "otherwise", identity=(folder.st_dev, folder.st_ino)
    )
    entries, left = _walked(pool, own)
    assert {"vorn", "vorn/quolt.csv"} <= entries
    assert not [path for path in entries if path.startswith("vorn/lamp")]
    assert left == [("vorn/lamp", RecognisedAs.THIS_PROJECT)]


def test_a_project_on_another_volume_is_not_a_folder_here_with_the_same_number(
    tmp_path: Path,
) -> None:
    """A file number is unique on one volume only: another device's folder is another folder."""
    pool = _pool(tmp_path)
    folder = os.stat(_stand_in(pool))
    own = OwnProject(
        path=tmp_path / "on_another_drive", identity=(folder.st_dev + 1, folder.st_ino)
    )
    entries, left = _walked(pool, own)
    assert {"vorn/lamp", "vorn/lamp/project.json", "vorn/lamp/runs"} <= entries
    assert left == []


def test_a_project_on_another_volume_is_read_from_and_leaves_the_root_whole(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path / "drive_a")
    project = Project.create(tmp_path / "drive_b" / "ingence", created_at="t", roots=[pool])
    report = run_census(project, run_id=FIRST, started_at="t")
    assert _paths(project) == DATA
    assert report.recognised == ()


# ---------------------------------------------------------------------------
# Ingence's own environment: a venv, a uv tool's or a conda environment, wherever it is kept.
# ---------------------------------------------------------------------------


def test_the_environment_ingence_runs_in_is_its_own(tmp_path: Path) -> None:
    """A venv (uv's tool folder too) and a conda environment are its own. A system Python is not."""
    from ingence.scan.recognise import environment_directory

    venv, base = tmp_path / "ingence-env", tmp_path / "python"
    conda = tmp_path / "envs" / "ingence"
    (conda / "conda-meta").mkdir(parents=True)
    system = tmp_path / "usr-local"
    system.mkdir()

    assert environment_directory(str(venv), str(base)) == venv
    assert environment_directory(str(conda), str(conda)) == conda
    assert environment_directory(str(system), str(system)) is None


def test_an_environment_kept_inside_the_root_is_left_out_whole(tmp_path: Path) -> None:
    """Installed into a folder the census reads, the engine's libraries are not data."""
    pool = _pool(tmp_path)
    environment = pool / "ingence-env"
    (environment / "lib" / "site-packages" / "numpy").mkdir(parents=True)
    (environment / "lib" / "site-packages" / "numpy" / "core.py").write_text("x")
    identity = os.stat(environment)
    project = Project.create(tmp_path / "ingence", created_at="t", roots=[pool])

    report = run_census(
        project,
        run_id=FIRST,
        started_at="t",
        installation=frozenset({(identity.st_dev, identity.st_ino)}),
    )

    assert _paths(project) == DATA
    assert _left_out(report) == [("ingence-env", RecognisedAs.INSTALLATION, False)]


def test_the_running_engines_environment_is_among_its_installation() -> None:
    from ingence.scan.recognise import environment_directory, installation_directories

    found = environment_directory(sys.prefix, sys.base_prefix)
    if found is None:
        pytest.skip("this interpreter is a system Python, not an environment of its own")
    folder = os.stat(found)
    assert (folder.st_dev, folder.st_ino) in installation_directories()


# ---------------------------------------------------------------------------
# The dry run, and every name a project writes.
# ---------------------------------------------------------------------------


def test_a_dry_run_leaves_out_what_a_census_leaves_out(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    Project.create(pool / "kelt" / "other", created_at="t")
    project = Project.create(pool / "vorn" / "ingence", created_at="t", roots=[pool])
    _engine_writes_while_running(project.path)

    report = dry_run(pool, project=project.path)

    assert report["kinds"] == {"directory": 2, "file": 3}
    assert report["left_out"] == [
        {"path": "kelt/other", "as": "project"},
        {"path": "vorn/ingence", "as": "this_project"},
    ]


def test_a_dry_run_of_a_root_that_is_gone_says_so_with_a_project_named(tmp_path: Path) -> None:
    """Naming the project adds no way to fail: an unreadable root is reported."""
    project = Project.create(tmp_path / "ingence", created_at="t")
    report = dry_run(tmp_path / "gone", project=project.path)
    assert report["walk"]["entries"] == 0
    assert report["errors"]
    assert report["left_out"] == []


def test_a_file_browsers_own_file_in_the_project_keeps_it_left_out_whole(
    tmp_path: Path,
) -> None:
    """Opening the project's folder in a file browser writes a hidden file nobody owns."""
    pool = _pool(tmp_path)
    project = Project.create(pool / "vorn" / "ingence", created_at="t", roots=[pool])
    (project.path / ".DS_Store").write_bytes(b"\x00")

    report = run_census(project, run_id=FIRST, started_at="t")
    walked = dry_run(pool, project=project.path)

    assert _paths(project) == DATA
    assert _left_out(report) == [("vorn/ingence", RecognisedAs.THIS_PROJECT, False)]
    assert walked["left_out"] == [{"path": "vorn/ingence", "as": "this_project"}]


#: The names the engine's code joins onto a project's folder: ``project / X``, ``project.path / X``,
#: ``self.project / X``, ``home / X`` and the like, wherever ``X`` is a string or a constant.
_PROJECT_HOLDERS = frozenset(
    {"project", "project_path", "project_dir", "project_directory", "home"}
)
#: Modules where one of those names is not a project's folder, each with what it is there.
_NOT_A_PROJECT = {"ingence.api.decider": "home is the person's home folder, for Ingence's settings"}


def _imported_names(tree: object, module_name: str, is_package: bool) -> dict[str, tuple[str, str]]:
    """Every name a module imports with ``from ... import``, wherever the statement sits.

    The Dashboard's routes import their constants inside the function that writes, so a module's
    attributes alone would miss them.
    """
    import ast
    import importlib.util

    package = module_name if is_package else module_name.rpartition(".")[0]
    names: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):  # type: ignore[arg-type]
        if not isinstance(node, ast.ImportFrom):
            continue
        source = (
            importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            if node.level
            else node.module or ""
        )
        for alias in node.names:
            names[alias.asname or alias.name] = (source, alias.name)
    return names


def _names_joined_onto_a_project() -> tuple[dict[str, set[str]], set[tuple[str, str]]]:
    """Each name the engine's source joins onto a project's folder, and the joins left unread.

    Returns the names, each with the modules joining it, and every join whose name could not be
    read from the code, as (module, the expression).
    """
    import ast
    import importlib
    from pathlib import Path as SourcePath

    import ingence

    package_folder = SourcePath(ingence.__file__).parent
    found: dict[str, set[str]] = {}
    unread: set[tuple[str, str]] = set()
    for source in sorted(package_folder.rglob("*.py")):
        parts = source.relative_to(package_folder.parent).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module_name = ".".join(parts[:-1] if is_package else parts)
        if module_name in _NOT_A_PROJECT or source.name == "__main__.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported = _imported_names(tree, module_name, is_package)
        loaded: list[object] = []

        def module_of(name: str = module_name, cache: list[object] = loaded) -> object:
            if not cache:
                cache.append(importlib.import_module(name))
            return cache[0]

        def read(node: ast.expr, imported: dict[str, tuple[str, str]] = imported) -> object:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                if node.id in imported:
                    origin, attribute = imported[node.id]
                    held = importlib.import_module(origin)
                    return getattr(held, attribute, None) or _submodule(origin, attribute)
                return getattr(module_of(), node.id, None)
            if isinstance(node, ast.Attribute):
                base = read(node.value)
                return getattr(base, node.attr, None) if base is not None else None
            if isinstance(node, ast.JoinedStr):
                pieces = [
                    read(value.value if isinstance(value, ast.FormattedValue) else value)
                    for value in node.values
                ]
                if all(isinstance(piece, str) for piece in pieces):
                    return "".join(str(piece) for piece in pieces)
            return None

        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            left = node.left
            holder = (
                left.id
                if isinstance(left, ast.Name)
                else left.attr
                if isinstance(left, ast.Attribute)
                else None
            )
            if holder == "path" and isinstance(left, ast.Attribute):
                inner = left.value
                holder = inner.id if isinstance(inner, ast.Name) else getattr(inner, "attr", None)
            if holder not in _PROJECT_HOLDERS:
                continue
            value = read(node.right)
            if isinstance(value, str) and value:
                found.setdefault(value.split("/")[0], set()).add(module_name)
            else:
                unread.add((module_name, ast.unparse(node.right)))
    return found, unread


def _submodule(origin: str, attribute: str) -> object:
    """A module imported by name from its package (``from ingence import jobs``), or None."""
    import importlib

    try:
        return importlib.import_module(f"{origin}.{attribute}")
    except ImportError:
        return None


#: Joins onto a project's folder whose name is computed where it is used, each with why it is safe.
_UNREAD_AND_WHY: dict[tuple[str, str], str] = {
    ("ingence.api.routes.workspace", "parts[0]"): (
        "reads one of the two settings files, by a name checked against them, and writes nothing"
    ),
}


def test_every_name_a_project_writes_at_its_top_is_one_the_census_knows() -> None:
    """Every name the source joins onto a project's folder is in the layout's list.

    Found from the code, not typed here, so a writer added tomorrow (an ``imports`` folder, say)
    fails this test until the census knows its name.
    """
    joined, unread = _names_joined_onto_a_project()
    assert {"runs", "record", "jobs", "workspace", "projections"} <= set(joined)
    unknown = {name: sorted(where) for name, where in joined.items() if not is_project_name(name)}
    assert unknown == {}
    assert sorted(unread - set(_UNREAD_AND_WHY)) == []
    for name in (".CURRENT.incoming", ".settings.yaml.incoming", ".ingence-case-probe", "imports"):
        assert is_project_name(name), name
    assert not is_project_name("brisa_01.tif")
    assert not is_project_name(".brisa.incoming")


@pytest.mark.parametrize("where", ["kelt/ingence", "."])
def test_nothing_the_commands_write_enters_a_census(tmp_path: Path, where: str) -> None:
    """init, index, dive, infer and index again, as a person runs them, the project inside its
    root or the root itself. Afterwards its top holds the person's names and a project's alone."""
    import subprocess

    pool = _pool(tmp_path)
    place = pool / where if where != "." else pool
    persons = {path.name for path in pool.iterdir()}

    def run(*args: str) -> str:
        finished = subprocess.run(
            [sys.executable, "-m", "ingence.cli.main", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        assert finished.returncode == 0, finished.stdout + finished.stderr
        return " ".join(finished.stdout.split())

    said = f"left out, this project: {where if where != '.' else ''}"
    run("init", str(place), "--root", str(pool), "--rules", "shipped")
    assert said in run("index", "-p", str(place), "--dry-run")
    assert said in run("index", "-p", str(place), "--quiet")
    run("dive", "-p", str(place), "--budget", "30")
    run("infer", "-p", str(place))
    run("index", "-p", str(place), "--quiet")

    project = Project.open(place)
    runs = sorted(path.name for path in (place / "runs").iterdir())
    assert len(runs) == 4
    for run_id in runs:
        assert _paths(project, run_id) == DATA, run_id
    on_top = {path.name for path in place.iterdir()}
    assert {name for name in on_top if not is_project_name(name)} <= persons


def test_a_left_out_path_is_printed_as_written() -> None:
    """A folder named like the console's markup prints as itself, and stops nothing."""
    from rich.text import Text

    from ingence.cli.main import _left_out_lines

    (line,) = _left_out_lines([("project", "kelt/[/old]/proj", False)])
    assert Text.from_markup(line).plain == "  left out, another Ingence project: kelt/[/old]/proj"
    (line,) = _left_out_lines([("project", "kelt/[old]/proj", False)])
    assert Text.from_markup(line).plain.endswith("kelt/[old]/proj")
