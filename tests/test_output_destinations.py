"""A file the engine writes where a person says is refused inside the folders it reads.

Source data is read-only. ``teach shapes --out``, ``teach export-answers --out`` and
``index --dry-run --report`` ask the one check that ``ingence export`` asks
(``core/destinations.py``). The check refuses a place inside any root of the project, and anywhere
inside the project but its ``projections/exports`` folder, the nearest of those on the way up
deciding. A place outside both is the person's own. A name where something already is gets refused
unless ``--overwrite`` is given (a file under the project's exports folder apart), and a command
writing several files asks about each before it writes any. Every name is invented.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.destinations import (
    DestinationRefusedError,
    check_output_file,
    check_output_folder,
    declared_roots,
    where_refused,
)
from ingence.core.project import Project

if TYPE_CHECKING:
    from collections.abc import Callable

    from typer.testing import Result

_FILES = {
    "kelt_2024-03-04/kelt_readings.csv": "sample,value\nkelt_a,1\nkelt_b,2\n",
    "kelt_2024-03-04/kelt_notes.txt": "notes on the kelt run\n",
    "vorn_2024-05-06/vorn_fit.py": 'print("vorn")\n',
    "vorn_2024-05-06/vorn_readings.csv": "sample,value\nvorn_a,3\n",
}


def _shapes(project: Path, out: Path, *more: str) -> Result:
    return CliRunner().invoke(
        app, ["teach", "shapes", "-p", str(project), "--out", str(out), *more]
    )


def _answers(project: Path, out: Path, *more: str) -> Result:
    return CliRunner().invoke(
        app, ["teach", "export-answers", "-p", str(project), "--out", str(out), *more]
    )


def _report(project: Path, out: Path, *more: str) -> Result:
    return CliRunner().invoke(
        app, ["index", "--dry-run", "-p", str(project), "--report", str(out), *more]
    )


#: The three options that write a file a person names, each as a call taking (project, file).
ALL_OPTIONS = (
    pytest.param(_shapes, id="teach-shapes"),
    pytest.param(_answers, id="export-answers"),
    pytest.param(_report, id="dry-run-report"),
)


class World:
    """An invented pool, a project that reads it, and the means to see that nothing changed."""

    def __init__(self, base: Path, *, project: Path | None = None) -> None:
        self.base = base
        self.pool = base / "pool"
        for relative, text in _FILES.items():
            path = self.pool / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        where = project if project is not None else base / "project"
        made = CliRunner().invoke(app, ["init", str(where), "--root", str(self.pool)])
        assert made.exit_code == 0, made.output
        self.project = where
        self.exports = where / "projections" / "exports"

    def ready_for(self, write: Callable[[Path, Path], Result]) -> None:
        """Index the pool where the command reads the Index (a refusal never gets that far, so a
        test of refusals leaves it unindexed and is quick)."""
        if write is _shapes:
            indexed = CliRunner().invoke(app, ["index", "-p", str(self.project), "--quiet"])
            assert indexed.exit_code == 0, indexed.output

    def snapshot(self, folder: Path | None = None) -> list[tuple[str, str, str, int]]:
        """Every name under the folder, with the bytes and the time of each file: a rewrite of
        the same bytes still shows."""
        found: list[tuple[str, str, str, int]] = []
        top = folder or self.pool
        for path in sorted(top.rglob("*")):
            relative = path.relative_to(top).as_posix()
            if path.is_file() and not path.is_symlink():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                found.append((relative, "file", digest, path.stat().st_mtime_ns))
            else:
                found.append((relative, "other", "", 0))
        return found


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _refused(result: Result) -> None:
    assert result.exit_code == 2, result.output
    assert "refused" in result.output
    said = " ".join(result.output.split())
    # Plain words, nothing internal: it names the folder and says where the file may go.
    assert "never writes into the folders it reads" in said or "keeps for itself" in said
    assert "or one under" in said
    for internal in ("invariant", "Traceback"):
        assert internal not in said, internal


# -- Inside a root: refused, and the root is byte-identical afterwards --------------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_inside_the_root_is_refused_and_the_root_is_untouched(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    before = world.snapshot()
    for out in (
        world.pool / "kelt_2024-03-04" / "table.csv",  # a new file
        world.pool / "table.csv",  # a new file at the top of the root
        world.pool / "a_new_folder" / "deeper" / "table.csv",  # in folders that do not exist
    ):
        result = write(world.project, out)
        _refused(result)
        assert "a folder this project reads" in " ".join(result.output.split())
        assert not out.exists(), out
    assert not (world.pool / "a_new_folder").exists()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_naming_an_indexed_file_replaces_nothing(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    """``--out`` names a text file inside the root. The command refuses it and every file keeps
    its bytes. Writing it would exit 0 and turn the indexed file into a table."""
    before = world.snapshot()
    for relative in _FILES:
        result = write(world.project, world.pool / relative)
        _refused(result)
    assert world.snapshot() == before
    assert (world.pool / "kelt_2024-03-04" / "kelt_notes.txt").read_text() == (
        "notes on the kelt run\n"
    )


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_the_root_itself_and_a_relative_path_into_it_are_refused(
    world: World, write: Callable[[Path, Path], Result], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = world.snapshot()
    _refused(write(world.project, world.pool))
    # A relative path is judged where it lands: the working directory is the root here.
    monkeypatch.chdir(world.pool / "kelt_2024-03-04")
    _refused(write(world.project, Path("table.out")))
    _refused(write(world.project, Path("..") / "table.out"))
    assert world.snapshot() == before


# -- Inside the project: refused, except its exports folder ------------------------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_inside_the_project_outside_its_exports_is_refused(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    kept = {name: (world.project / name).read_bytes() for name in ("settings.yaml", "project.json")}
    for out in (
        world.project / "settings.yaml",
        world.project / "project.json",
        world.project / "record" / "x.csv",
        world.project / "runs" / "x.csv",
        world.project / "projections" / "x.csv",
        world.project / "a_new_top_folder" / "x.csv",
        world.project / "x.csv",
    ):
        result = write(world.project, out)
        _refused(result)
        assert f"inside the project at {world.project}" in " ".join(result.output.split())
    assert {name: (world.project / name).read_bytes() for name in kept} == kept
    assert not (world.project / "a_new_top_folder").exists()
    # The check made the exports folder to ask, and took it away again: nothing was made.
    assert not world.exports.exists()


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_in_the_exports_folder_is_allowed(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    world.ready_for(write)
    before = world.snapshot()
    assert not world.exports.exists()
    world.exports.mkdir(parents=True)
    out = world.exports / "table.out"
    result = write(world.project, out)
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8")
    assert world.snapshot() == before


def test_a_new_folder_inside_the_exports_folder_is_made_by_the_command_that_makes_folders(
    world: World,
) -> None:
    world.ready_for(_shapes)
    out = world.exports / "mine" / "deeper" / "table.out"
    result = _shapes(world.project, out)
    assert result.exit_code == 0, result.output
    assert out.is_file()


# -- Outside both: the person's choice ---------------------------------------------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_outside_the_root_and_the_project_is_allowed(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    world.ready_for(write)
    out = tmp_path_factory.mktemp("elsewhere") / "table.out"
    before = world.snapshot()
    result = write(world.project, out)
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8")
    # Nothing was left made inside the project by asking, and the root is as it was.
    assert not world.exports.exists()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_already_outside_both_is_refused_in_words_and_left_as_it_was(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A name a person gives where a file already is is refused, and the refusal says so."""
    world.ready_for(write)
    out = tmp_path_factory.mktemp("elsewhere") / "table.out"
    out.write_text("old\n", encoding="utf-8")
    kept = out.stat().st_mtime_ns
    before = world.snapshot()
    result = write(world.project, out)
    assert result.exit_code == 2, result.output
    said = " ".join(result.output.split())
    assert f"There is already a file at {out}" in said
    assert "nothing was written" in said and "--overwrite" in said
    assert "Traceback" not in said
    assert out.read_text(encoding="utf-8") == "old\n" and out.stat().st_mtime_ns == kept
    assert sorted(path.name for path in out.parent.iterdir()) == ["table.out"]
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_with_overwrite_a_file_already_outside_both_is_replaced(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    world.ready_for(write)
    out = tmp_path_factory.mktemp("elsewhere") / "table.out"
    out.write_text("old\n", encoding="utf-8")
    before = world.snapshot()
    result = write(world.project, out, "--overwrite")
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") != "old\n"
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_link_that_leads_nowhere_is_refused_and_nothing_is_made_where_it_leads(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    out = elsewhere / "table.out"
    _link(out, elsewhere / "nowhere.out")
    result = write(world.project, out)
    assert result.exit_code == 2, result.output
    assert f"There is already a link that leads nowhere at {out}" in " ".join(result.output.split())
    assert out.is_symlink() and not (elsewhere / "nowhere.out").exists()


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_already_in_the_exports_folder_is_replaced_without_being_asked(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    world.ready_for(write)
    world.exports.mkdir(parents=True)
    out = world.exports / "table.out"
    out.write_text("old\n", encoding="utf-8")
    result = write(world.project, out)
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") != "old\n"


def test_a_dry_run_report_never_says_nothing_was_written_once_it_is_written(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    out = tmp_path_factory.mktemp("elsewhere") / "report.json"
    result = _report(world.project, out)
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert "Nothing was written" not in said
    assert "Ingence wrote nothing in your data folders" in said
    assert said.endswith(f"The report is at {out}.")
    assert out.is_file()
    alone = CliRunner().invoke(app, ["index", "--dry-run", "-p", str(world.project)])
    assert alone.exit_code == 0, alone.output
    assert "Ingence wrote nothing and opened no file." in alone.output


def _export(project: Path, kind: str, out: Path, *more: str) -> Result:
    return CliRunner().invoke(
        app, ["export", kind, "--project", str(project), "--out", str(out), *more]
    )


def test_an_export_of_three_files_with_one_name_taken_writes_none_of_the_three(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    world.ready_for(_shapes)
    out = tmp_path_factory.mktemp("elsewhere") / "quality"
    first = _export(world.project, "quality", out)
    assert first.exit_code == 0, first.output
    names = ("quality.csv", "quality.json", "quality_runs.csv")
    assert sorted(path.name for path in out.iterdir()) == sorted(names)
    (out / "quality.csv").unlink()
    (out / "quality_runs.csv").unlink()
    kept = (out / "quality.json").read_bytes()
    again = _export(world.project, "quality", out)
    assert again.exit_code == 2, again.output
    said = " ".join(again.output.split())
    assert f"There is already a file at {out / 'quality.json'}" in said
    assert sorted(path.name for path in out.iterdir()) == ["quality.json"]
    assert (out / "quality.json").read_bytes() == kept
    replaced = _export(world.project, "quality", out, "--overwrite")
    assert replaced.exit_code == 0, replaced.output
    assert sorted(path.name for path in out.iterdir()) == sorted(names)


def test_a_themes_export_into_a_folder_it_wrote_before_is_refused_without_overwrite(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    pytest.importorskip("sklearn")
    world.ready_for(_shapes)
    out = tmp_path_factory.mktemp("elsewhere") / "themes"
    first = _export(world.project, "themes", out)
    assert first.exit_code == 0, first.output
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    (out / "themes.csv").unlink()
    again = _export(world.project, "themes", out)
    assert again.exit_code == 2, again.output
    assert "--overwrite" in " ".join(again.output.split())
    assert not (out / "themes.csv").exists()
    assert {path.name: path.read_bytes() for path in out.iterdir()} == {
        name: data for name, data in before.items() if name != "themes.csv"
    }
    replaced = _export(world.project, "themes", out, "--overwrite")
    assert replaced.exit_code == 0, replaced.output
    assert (out / "themes.csv").is_file()


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_folder_where_a_file_is_wanted_is_said_so(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("a_folder")
    result = write(world.project, elsewhere)
    assert result.exit_code == 2, result.output
    assert "is a folder, and a file is wanted" in " ".join(result.output.split())
    assert list(elsewhere.iterdir()) == []


# -- Links ---------------------------------------------------------------------------------------


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a symbolic link")


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_link_leading_into_the_root_is_refused(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("links")
    # A link to an indexed file: writing through it would replace the file.
    to_file = elsewhere / "to_a_file.csv"
    _link(to_file, world.pool / "kelt_2024-03-04" / "kelt_readings.csv")
    # A link to a folder in the root, and a file beneath it.
    to_folder = elsewhere / "to_a_folder"
    _link(to_folder, world.pool / "vorn_2024-05-06")
    # A link to a file that is not made yet: writing through it would make it in the root.
    to_new = elsewhere / "to_a_new_file.csv"
    _link(to_new, world.pool / "vorn_2024-05-06" / "not_yet.csv")
    # A link to the root itself, with a folder beneath it that is not made yet.
    to_root = elsewhere / "to_the_root"
    _link(to_root, world.pool)
    before = world.snapshot()
    for out in (to_file, to_folder / "new.csv", to_new, to_root / "fresh" / "new.csv"):
        _refused(write(world.project, out))
    assert not (world.pool / "vorn_2024-05-06" / "not_yet.csv").exists()
    assert not (world.pool / "fresh").exists()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_link_leading_into_the_project_is_refused_but_one_into_exports_is_not(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("links")
    to_settings = elsewhere / "to_settings.out"
    _link(to_settings, world.project / "settings.yaml")
    kept = (world.project / "settings.yaml").read_bytes()
    _refused(write(world.project, to_settings))
    assert (world.project / "settings.yaml").read_bytes() == kept
    world.ready_for(write)
    world.exports.mkdir(parents=True)
    to_exports = elsewhere / "to_exports"
    _link(to_exports, world.exports)
    result = write(world.project, to_exports / "table.out")
    assert result.exit_code == 0, result.output
    assert (world.exports / "table.out").is_file()


# -- A project inside its own root -------------------------------------------------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_project_inside_its_own_root_still_writes_into_its_exports_folder(
    tmp_path: Path, write: Callable[[Path, Path], Result]
) -> None:
    """The nearest folder wins: a file under the exports folder meets it before the project and
    before the root that holds both, so it is allowed. Every other place in the root, or in the
    project, is refused."""
    inside = World(tmp_path, project=tmp_path / "pool" / "ingence_project")
    inside.ready_for(write)
    before = {
        name: (inside.project / name).read_bytes() for name in ("settings.yaml", "project.json")
    }
    inside.exports.mkdir(parents=True)
    out = inside.exports / "table.out"
    result = write(inside.project, out)
    assert result.exit_code == 0, result.output
    assert out.is_file()
    for refused in (
        inside.pool / "table.out",  # the root, beside the project
        inside.pool / "kelt_2024-03-04" / "table.out",
        inside.project / "table.out",  # the project, outside exports
        inside.project / "record" / "table.out",
        inside.project / "projections" / "table.out",
    ):
        _refused(write(inside.project, refused))
        assert not refused.exists(), refused
    assert {name: (inside.project / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_project_inside_its_own_root_makes_its_first_export_folder(
    tmp_path: Path, write: Callable[[Path, Path], Result]
) -> None:
    """With no exports folder yet, the first file is still taken (the check makes the folder to
    compare it by identity)."""
    inside = World(tmp_path, project=tmp_path / "pool" / "ingence_project")
    inside.ready_for(write)
    assert not inside.exports.exists()
    if write is _shapes:
        out = inside.exports / "first" / "table.out"
    else:
        inside.exports.mkdir(parents=True)
        out = inside.exports / "table.out"
    result = write(inside.project, out)
    assert result.exit_code == 0, result.output
    assert out.is_file()


# -- The check itself --------------------------------------------------------------------------


def test_nothing_is_made_by_asking(world: World, tmp_path_factory: pytest.TempPathFactory) -> None:
    project = Project.open(world.project)
    roots = declared_roots(project)
    assert roots == [world.pool]
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    assert not world.exports.exists()
    # A place outside the project leaves the project untouched, its folders' times included.
    times = {
        folder: folder.stat().st_mtime_ns
        for folder in (world.project, world.project / "projections")
    }
    assert where_refused(elsewhere / "a.csv", project=world.project, roots=roots) is None
    assert {folder: folder.stat().st_mtime_ns for folder in times} == times
    assert where_refused(world.project / "x.csv", project=world.project, roots=roots) is not None
    assert where_refused(world.pool / "x.csv", project=world.project, roots=roots) is not None
    assert not world.exports.exists()
    # Inside the exports folder it is allowed and, being the folder a person is about to write,
    # the question leaves it made.
    assert where_refused(world.exports / "a.csv", project=world.project, roots=roots) is None
    assert world.exports.is_dir()


def test_the_checks_raise_in_words_and_name_where_to_go(world: World) -> None:
    roots = declared_roots(Project.open(world.project))
    with pytest.raises(DestinationRefusedError, match="a folder this project reads") as caught:
        check_output_file(world.pool / "x.csv", project=world.project, roots=roots)
    assert str(world.exports) in str(caught.value)
    with pytest.raises(DestinationRefusedError, match="inside the project at"):
        check_output_folder(world.project / "record", project=world.project, roots=roots)
    a_file = world.pool / "vorn_2024-05-06" / "vorn_fit.py"
    with pytest.raises(DestinationRefusedError, match="is a file, and a folder is wanted"):
        check_output_folder(a_file, project=world.project, roots=[])
    check_output_folder(world.exports / "any", project=world.project, roots=roots)


def test_a_root_that_cannot_be_reached_is_compared_by_its_spelling(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A share that is not mounted cannot be compared by identity, and a folder made at the place
    it will appear would sit under the data: refused by where it is written."""
    gone = world.base / "pool_gone"
    world.pool.rename(gone)
    roots = declared_roots(Project.open(world.project))
    assert roots == [world.pool]
    refusal = where_refused(world.pool / "new" / "x.csv", project=world.project, roots=roots)
    assert refusal is not None and refusal.kind == "root"
    assert not world.pool.exists()
    result = _answers(world.project, world.pool / "new" / "x.csv")
    _refused(result)
    assert not world.pool.exists()
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    assert _answers(world.project, elsewhere / "x.csv").exit_code == 0


def test_a_disk_that_ignores_case_does_not_let_another_spelling_through(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    shouted = world.base / "POOL"
    if not shouted.exists():
        pytest.skip("this disk tells names apart by case")
    for write in (_shapes, _answers, _report):
        _refused(write(world.project, shouted / "table.out"))
        _refused(write(world.project, world.base / "PROJECT" / "table.out"))
    assert not (world.pool / "table.out").exists()


def test_a_write_that_fails_is_said_in_words(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A folder that cannot be made (its parent is a file) is no traceback."""
    world.ready_for(_shapes)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    blocker = elsewhere / "afile"
    blocker.write_text("x", encoding="utf-8")
    for write in (_shapes, _answers, _report):
        result = write(world.project, blocker / "table.out")
        assert result.exit_code == 1, result.output
        assert "could not write" in result.output
        assert "Traceback" not in result.output
    assert blocker.read_text(encoding="utf-8") == "x"


# -- A ``..`` after a link is the system's to resolve, not a spelling's -------------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_dotdot_after_a_link_is_followed_as_the_system_follows_it(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """With ``<elsewhere>/lnk`` leading to a folder inside the pool, ``<elsewhere>/lnk/..`` is the
    pool, whatever the spelling says. The check follows the link and does not remove the ``..`` by
    spelling. Judging a place beside the link would let the file replace an indexed one."""
    elsewhere = tmp_path_factory.mktemp("dotdot")
    link = elsewhere / "lnk"
    _link(link, world.pool / "vorn_2024-05-06")
    before = world.snapshot()
    for out in (
        link / ".." / "kelt_2024-03-04" / "kelt_readings.csv",  # an indexed file
        link / ".." / "kelt_2024-03-04" / "new.csv",
        link / ".." / "a_new_folder" / "new.csv",  # a folder that does not exist yet
        link / ".." / "table.out",
    ):
        _refused(write(world.project, out))
    assert not (world.pool / "a_new_folder").exists()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_relative_dotdot_from_inside_a_linked_folder_is_followed_too(
    world: World,
    write: Callable[[Path, Path], Result],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cd`` into a link and name ``../x``: the system leaves the folder the link leads to."""
    elsewhere = tmp_path_factory.mktemp("dotdot")
    link = elsewhere / "lnk"
    _link(link, world.pool / "vorn_2024-05-06")
    monkeypatch.chdir(link)
    before = world.snapshot()
    _refused(write(world.project, Path("..") / "kelt_2024-03-04" / "kelt_readings.csv"))
    _refused(write(world.project, Path("..") / "table.out"))
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_dotdot_that_stays_outside_is_still_allowed(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    world.ready_for(write)
    elsewhere = tmp_path_factory.mktemp("dotdot")
    (elsewhere / "inner").mkdir()
    result = write(world.project, elsewhere / "inner" / ".." / "table.out")
    assert result.exit_code == 0, result.output
    assert (elsewhere / "table.out").is_file()


def test_the_export_walk_follows_a_dotdot_after_a_link_as_well(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from ingence.themes.export import ExportRefusedError, check_destination

    elsewhere = tmp_path_factory.mktemp("dotdot")
    link = elsewhere / "lnk"
    _link(link, world.pool / "vorn_2024-05-06")
    roots = declared_roots(Project.open(world.project))
    for folder in (link / ".." / "exported_here", link / ".." / "kelt_2024-03-04"):
        with pytest.raises(ExportRefusedError, match="inside the data root"):
            check_destination(folder, project=world.project, roots=roots)
    (elsewhere / "inner").mkdir()
    check_destination(elsewhere / "inner" / ".." / "exported", project=world.project, roots=roots)


# -- ``~`` is made the home folder once, so the check and the write are given the same path ----


def _home(monkeypatch: pytest.MonkeyPatch, where: Path) -> None:
    where.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(where))
    monkeypatch.setenv("USERPROFILE", str(where))


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_tilde_names_the_home_folder_for_the_check_and_for_the_write(
    world: World,
    write: Callable[[Path, Path], Result],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--out=~/table.out`` reaches a program unexpanded. The check and the write both read
    ``~`` as the home folder, so the file is written there and no folder named ``~`` is made in the
    working directory, here inside the pool."""
    world.ready_for(write)
    home = tmp_path_factory.mktemp("home")
    _home(monkeypatch, home)
    monkeypatch.chdir(world.pool / "kelt_2024-03-04")
    before = world.snapshot()
    result = write(world.project, Path("~") / "table.out")
    assert result.exit_code == 0, result.output
    assert (home / "table.out").is_file()
    assert not (world.pool / "kelt_2024-03-04" / "~").exists()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_tilde_whose_home_is_inside_the_root_is_refused_and_nothing_is_made(
    world: World, write: Callable[[Path, Path], Result], monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(monkeypatch, world.pool / "kelt_2024-03-04")
    monkeypatch.chdir(world.base)
    before = world.snapshot()
    _refused(write(world.project, Path("~") / "table.out"))
    _refused(write(world.project, Path("~") / "kelt_readings.csv"))
    assert not (world.base / "~").exists()
    assert world.snapshot() == before


def test_a_path_is_made_absolute_without_expanding_or_normalizing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.core.destinations import joined, typed_path

    _home(monkeypatch, tmp_path / "home")
    assert typed_path(Path("~") / "a.csv") == tmp_path / "home" / "a.csv"
    assert typed_path(Path("a") / "~") == Path("a") / "~"
    monkeypatch.chdir(tmp_path)
    assert joined(Path("~") / "a.csv") == Path.cwd() / "~" / "a.csv"  # joined expands nothing
    assert joined(Path("x") / ".." / "y") == Path.cwd() / "x" / ".." / "y"  # and normalizes nothing


# -- The exports folder is the project's own, or it is not the exports folder -------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_an_exports_folder_that_is_a_link_into_the_root_is_not_the_exports_folder(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    """A link at ``projections/exports`` leading into the pool is not the exports folder. The
    nearest-wins rule must not take the pool's subfolder for it and treat every file under it as
    "inside the exports folder"."""
    assert not world.exports.exists()
    _link(world.exports, world.pool / "vorn_2024-05-06")
    before = world.snapshot()
    for out in (
        world.exports / "table.out",
        world.exports / "deeper" / "table.out",
        world.exports / "vorn_fit.py",  # an indexed file, reached through the link
    ):
        _refused(write(world.project, out))
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_projections_folder_that_is_a_link_into_the_root_gets_no_folder_made_in_the_root(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    """With ``projections`` linked into the pool, the check must not make ``exports`` inside the
    pool to compare it by identity. A project one of whose own folders is a link is refused when
    it is opened, before anything is checked or made (``check_layout``)."""
    (world.project / "projections").rmdir()
    _link(world.project / "projections", world.pool / "vorn_2024-05-06")
    before = world.snapshot()
    for out in (world.exports / "table.out", world.project / "table.out"):
        result = write(world.project, out)
        assert result.exit_code == 1, result.output
        said = " ".join(result.output.split())
        assert "projections in the project" in said and "must be real folders" in said
        assert "Traceback" not in said
    assert not (world.pool / "vorn_2024-05-06" / "exports").exists()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_an_exports_folder_linked_elsewhere_is_judged_by_where_the_file_lands(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A link at ``projections/exports`` is not the exports folder, so a file written through it is
    judged by the folder it really lands in: refused in the project, allowed outside both."""
    world.ready_for(write)
    elsewhere = tmp_path_factory.mktemp("exports_target")
    _link(world.exports, elsewhere)
    result = write(world.project, world.exports / "table.out")
    assert result.exit_code == 0, result.output
    assert (elsewhere / "table.out").is_file()
    world.exports.unlink()
    _link(world.exports, world.project / "record")
    kept = sorted(path.name for path in (world.project / "record").iterdir())
    _refused(write(world.project, world.exports / "table.out"))
    assert sorted(path.name for path in (world.project / "record").iterdir()) == kept


def test_the_exports_folder_reached_through_a_linked_project_path_still_counts(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The project named through a link is the same project: its own exports folder is allowed."""
    elsewhere = tmp_path_factory.mktemp("project_link")
    link = elsewhere / "project_link"
    _link(link, world.project)
    roots = declared_roots(Project.open(world.project))
    assert (
        where_refused(link / "projections" / "exports" / "a.csv", project=link, roots=roots) is None
    )
    assert where_refused(link / "x.csv", project=link, roots=roots) is not None


# -- A file is written beside itself and moved into place ---------------------------------------


def _hard_link(link: Path, target: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("this system does not let a test make a hard link")


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_second_hard_linked_name_of_an_indexed_file_is_not_written_through(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A backup snapshot that hard-links files holds a second name for an indexed file. Writing
    through it would change the indexed file's bytes. A name already there is refused, and with
    ``--overwrite`` the file is written beside itself and moved into place, so the second name
    leads to the new file and the indexed file is as it was."""
    world.ready_for(write)
    elsewhere = tmp_path_factory.mktemp("snapshot")
    second = elsewhere / "second_name.txt"
    indexed = world.pool / "kelt_2024-03-04" / "kelt_notes.txt"
    _hard_link(second, indexed)
    before = world.snapshot()
    assert write(world.project, second).exit_code == 2
    assert world.snapshot() == before
    result = write(world.project, second, "--overwrite")
    assert result.exit_code == 0, result.output
    assert second.read_text(encoding="utf-8") != "notes on the kelt run\n"
    assert indexed.read_text(encoding="utf-8") == "notes on the kelt run\n"
    assert world.snapshot() == before
    assert [path.name for path in elsewhere.iterdir()] == ["second_name.txt"]  # no leftover


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_link_standing_at_the_destination_is_replaced_and_never_written_through(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    world.ready_for(write)
    elsewhere = tmp_path_factory.mktemp("links")
    theirs = elsewhere / "theirs.txt"
    theirs.write_text("a person's own file\n", encoding="utf-8")
    link = elsewhere / "standing.out"
    _link(link, theirs)
    refused = write(world.project, link)
    assert refused.exit_code == 2, refused.output
    assert f"There is already a link at {link}" in " ".join(refused.output.split())
    assert link.is_symlink() and theirs.read_text(encoding="utf-8") == "a person's own file\n"
    result = write(world.project, link, "--overwrite")
    assert result.exit_code == 0, result.output
    assert theirs.read_text(encoding="utf-8") == "a person's own file\n"
    assert link.is_file() and not link.is_symlink()


def test_a_write_that_fails_leaves_the_file_as_it_was_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.core import destinations

    target = tmp_path / "table.out"
    target.write_text("old\n", encoding="utf-8")

    def refuse(*_: object, **__: object) -> None:
        message = "the move failed"
        raise OSError(message)

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="the move failed"):
        destinations.write_replacing(target, "new\n")
    with pytest.raises(OSError, match="the move failed"), destinations.replacing(target) as handle:
        handle.write("new\n")
    with pytest.raises(RuntimeError, match="half-way"), destinations.replacing(target) as handle:
        handle.write("new\n")
        message = "half-way"
        raise RuntimeError(message)
    assert [path.name for path in tmp_path.iterdir()] == ["table.out"]
    assert target.read_text(encoding="utf-8") == "old\n"


# -- The new file has the permissions of the one it replaces -----------------------------------

_no_modes = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="a Windows file has no owner, group and other bits"
)


@_no_modes
@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_file_written_again_keeps_the_permissions_it_had(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Replacing a file makes a new one with the replaced file's permissions, so a private file
    (0600) does not come out readable by others (0644)."""
    world.ready_for(write)
    elsewhere = tmp_path_factory.mktemp("private")
    for mode in (0o600, 0o640, 0o755):
        out = elsewhere / f"mine_{mode:o}.out"
        out.write_text("old\n", encoding="utf-8")
        out.chmod(mode)
        result = write(world.project, out, "--overwrite")
        assert result.exit_code == 0, result.output
        assert out.read_text(encoding="utf-8") != "old\n"
        assert out.stat().st_mode & 0o777 == mode, f"{mode:o}"


@_no_modes
def test_the_helpers_keep_a_files_permissions_and_give_a_new_file_the_systems_default(
    tmp_path: Path,
) -> None:
    from ingence.core.destinations import replacing, write_replacing

    private = tmp_path / "private.txt"
    private.write_text("old\n", encoding="utf-8")
    private.chmod(0o600)
    write_replacing(private, "new\n")
    assert private.stat().st_mode & 0o777 == 0o600
    write_replacing(private, b"newer\n")
    assert private.stat().st_mode & 0o777 == 0o600
    with replacing(private) as handle:
        handle.write("newest\n")
    assert private.stat().st_mode & 0o777 == 0o600
    assert private.read_text(encoding="utf-8") == "newest\n"

    for umask, mode in ((0o077, 0o600), (0o022, 0o644)):
        before = os.umask(umask)
        try:
            write_replacing(tmp_path / f"new_{mode:o}.txt", "x\n")
        finally:
            os.umask(before)
        assert (tmp_path / f"new_{mode:o}.txt").stat().st_mode & 0o777 == mode


# -- The temporary file is a new file that follows no link --------------------------------------


def test_the_temporary_name_is_not_guessable_and_a_planted_link_at_it_is_never_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link planted at the temporary name must not lead the write into the file it points at
    (an indexed file, say). The name has a random part, and the file is created exclusively and
    without following a link: a taken name, a file or a link, stops the write and is left as it
    is."""
    from ingence.core import destinations

    target = tmp_path / "table.out"
    indexed = tmp_path / "indexed.txt"
    indexed.write_text("an indexed file\n", encoding="utf-8")
    planted = tmp_path / ".table.out.fixed.incoming"
    monkeypatch.setattr(destinations, "_incoming", lambda _path: planted)
    _link(planted, indexed)
    attempts: tuple[Callable[[], None], ...] = (
        lambda: destinations.write_replacing(target, "new\n"),
        lambda: destinations.write_replacing(target, b"new\n"),
        lambda: _replace_with(target),
    )
    for attempt in attempts:
        with pytest.raises(FileExistsError):
            attempt()
        assert indexed.read_text(encoding="utf-8") == "an indexed file\n"
        assert not target.exists()
        assert planted.is_symlink(), "what the engine did not make, it does not remove"
    # A plain file at the name is taken the same way.
    planted.unlink()
    planted.write_text("somebody else's\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        destinations.write_replacing(target, "new\n")
    assert planted.read_text(encoding="utf-8") == "somebody else's\n"
    monkeypatch.undo()
    assert destinations._incoming(target) != destinations._incoming(target)


def _replace_with(target: Path) -> None:
    from ingence.core.destinations import replacing

    with replacing(target) as handle:
        handle.write("new\n")


# -- Where a file lands is the real folder its name is in ---------------------------------------


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_path_that_passes_through_the_pool_and_lands_outside_is_allowed(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    """``<outside>/rel_link/../../outside/fine.csv`` goes down a link into the pool, up out of it,
    and lands in ``<outside>``. A walk along the spelling would meet the link's folder and refuse a
    file that lands where nobody reads."""
    world.ready_for(write)
    outside = world.base / "outside"
    outside.mkdir()
    _link(outside / "rel_link", Path("..") / "pool" / "vorn_2024-05-06")
    before = world.snapshot()
    through = outside / "rel_link" / ".." / ".." / "outside" / "fine.out"
    result = write(world.project, through)
    assert result.exit_code == 0, result.output
    assert (outside / "fine.out").is_file()
    assert world.snapshot() == before


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_a_link_standing_in_the_pool_and_leading_out_is_still_refused(
    world: World, write: Callable[[Path, Path], Result]
) -> None:
    """A link in the data that leads outside lands, once replaced, in the data."""
    outside = world.base / "outside"
    outside.mkdir()
    standing = world.pool / "vorn_2024-05-06" / "out_link.csv"
    _link(standing, outside / "target.csv")
    before = world.snapshot()
    _refused(write(world.project, standing))
    assert not (outside / "target.csv").exists()
    assert world.snapshot() == before


def test_where_a_file_lands_is_judged_by_its_real_folder_and_its_own_name(world: World) -> None:
    roots = declared_roots(Project.open(world.project))
    outside = world.base / "outside"
    outside.mkdir()
    _link(outside / "rel_link", Path("..") / "pool" / "vorn_2024-05-06")
    through = outside / "rel_link" / ".." / ".." / "outside" / "fine.csv"
    assert where_refused(through, project=world.project, roots=roots) is None
    inside = outside / "rel_link" / ".." / "kelt_2024-03-04" / "new.csv"
    refusal = where_refused(inside, project=world.project, roots=roots)
    assert refusal is not None and refusal.kind == "root"
    (outside / "inner").mkdir()
    plain = outside / "inner" / ".." / "fine.csv"
    assert where_refused(plain, project=world.project, roots=roots) is None


# -- An existing file that is not writable is not replaced --------------------------------------


def _made_read_only(path: Path) -> None:
    path.chmod(0o444)
    if os.access(path, os.W_OK):
        pytest.skip("this account can write a read-only file (the super-user can)")


@pytest.mark.parametrize("write", ALL_OPTIONS)
def test_an_existing_file_that_is_not_writable_is_not_replaced(
    world: World, write: Callable[[Path, Path], Result], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A shell redirect to a read-only file fails. Replacing it by a new file would succeed, so the
    engine does not replace it, even with ``--overwrite`` (without it, a file there is refused
    before this is asked)."""
    world.ready_for(write)
    elsewhere = tmp_path_factory.mktemp("locked")
    out = elsewhere / "locked.out"
    out.write_text("theirs\n", encoding="utf-8")
    _made_read_only(out)
    before = world.snapshot()
    result = write(world.project, out, "--overwrite")
    assert result.exit_code == 1, result.output
    assert f"could not write {out} (read-only)" in " ".join(result.output.split())
    assert "Traceback" not in result.output
    assert out.read_text(encoding="utf-8") == "theirs\n"
    assert out.stat().st_mode & 0o777 == 0o444
    assert [path.name for path in elsewhere.iterdir()] == ["locked.out"]  # nothing left beside it
    assert world.snapshot() == before


def test_the_helpers_refuse_a_file_that_is_not_writable_and_make_nothing(tmp_path: Path) -> None:
    from ingence.core.destinations import ReadOnlyFileError, replacing, write_replacing

    locked = tmp_path / "locked.txt"
    locked.write_text("theirs\n", encoding="utf-8")
    _made_read_only(locked)
    with pytest.raises(ReadOnlyFileError, match="read-only"):
        write_replacing(locked, "new\n")
    with pytest.raises(ReadOnlyFileError, match="read-only"), replacing(locked) as handle:
        handle.write("new\n")
    assert issubclass(ReadOnlyFileError, PermissionError)
    assert locked.read_text(encoding="utf-8") == "theirs\n"
    assert [path.name for path in tmp_path.iterdir()] == ["locked.txt"]
    locked.chmod(0o644)
    write_replacing(locked, "new\n")  # writable again: replaced
    assert locked.read_text(encoding="utf-8") == "new\n"
