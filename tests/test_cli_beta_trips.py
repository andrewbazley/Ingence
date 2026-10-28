"""The command line says what it is doing and counts what it says it counts.

One test for each trip that a fresh install can meet: ``init`` with a root that cannot
be reached still makes the project and says so; a dive on a terminal says it has started and how
far it has got, and says nothing where output is piped; ``index`` and ``infer`` and ``fix`` give
figures that agree or say what each counts; a count of one kind is grammatical; ``see`` prints its
address when its output is a pipe. Every name is invented.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ingence.cli import main as cli
from ingence.cli.main import app
from ingence.scan.progress import ProgressSnapshot

REPOSITORY = Path(__file__).resolve().parents[1]

_TREE = (
    "2026-01-12 HeLa confocal/HeLa_GFP_stack_01.txt",
    "2026-01-12 HeLa confocal/HeLa_GFP_stack_02.txt",
    "2026-01-12 HeLa confocal/HeLa_DAPI_stack_03.txt",
    "2026-02-03 U2OS widefield/U2OS_mCherry_timelapse_01.txt",
    "2026-02-03 U2OS widefield/notes.txt",
)


def _said(output: str) -> str:
    return " ".join(output.split())


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for relative in _TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some invented notes about the stack\n" * 4)
    return pool


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("beta_trips")
    made = base / "project"
    runner = CliRunner()
    for arguments in (
        ["init", str(made), "--root", str(_pool(base))],
        ["index", "-p", str(made), "--quiet"],
        ["dive", "-p", str(made), "--budget", "60"],
        ["infer", "-p", str(made)],
    ):
        done = runner.invoke(app, arguments)
        assert done.exit_code == 0, done.output
    return made


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    """A project indexed and not yet dived, so a dive has files to try."""
    made = tmp_path / "project"
    runner = CliRunner()
    for arguments in (
        ["init", str(made), "--root", str(_pool(tmp_path))],
        ["index", "-p", str(made), "--quiet"],
    ):
        done = runner.invoke(app, arguments)
        assert done.exit_code == 0, done.output
    return made


# -- init with a root that cannot be reached -----------------------------------------------------


def test_init_with_a_root_that_cannot_be_reached_still_makes_the_project(tmp_path: Path) -> None:
    missing = tmp_path / "share not mounted"
    done = CliRunner().invoke(app, ["init", str(tmp_path / "project"), "--root", str(missing)])
    assert done.exit_code == 0, done.output
    assert (tmp_path / "project" / "project.json").is_file()
    said = _said(done.output)
    warnings = [line for line in done.output.splitlines() if "warning" in line]
    assert len(warnings) == 1, "one plain line"
    assert "share not mounted" in said, "it names the root"
    assert "ingence index" in said, "it says index will need the folder"
    assert "Next:" in said, "the next command is still given"


def test_init_with_a_root_that_is_there_says_nothing_of_warnings(tmp_path: Path) -> None:
    done = CliRunner().invoke(
        app, ["init", str(tmp_path / "project"), "--root", str(_pool(tmp_path))]
    )
    assert done.exit_code == 0, done.output
    assert "warning" not in done.output


def test_init_warns_of_each_unreachable_root_apart_from_a_reachable_one(tmp_path: Path) -> None:
    done = CliRunner().invoke(
        app,
        [
            "init",
            str(tmp_path / "project"),
            "--root",
            str(_pool(tmp_path)),
            "--root",
            str(tmp_path / "offline one"),
            "--root",
            str(tmp_path / "offline two"),
        ],
    )
    assert done.exit_code == 0, done.output
    lines = [line for line in done.output.splitlines() if "warning" in line]
    assert len(lines) == 2
    first, second = (_said(line) for line in lines)
    assert "offline one" in first
    assert "offline two" not in first
    assert "offline two" in second
    assert "offline one" not in second
    assert "pool" not in first + second


# -- dive says it is working ---------------------------------------------------------------------


def test_a_dive_on_a_terminal_says_how_many_files_and_how_long(
    indexed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "console", Console(force_terminal=True, color_system=None, width=200))
    monkeypatch.setattr(cli, "_output_is_a_terminal", lambda: True)
    done = CliRunner().invoke(
        app, ["dive", "-p", str(indexed), "--budget", "90", "--depth", "deep"]
    )
    assert done.exit_code == 0, done.output
    said = _said(done.output)
    assert "Reading up to" in said
    assert "a time limit of 90 seconds" in said
    assert "Ingence prints a line every 30 seconds" in said


def test_a_dive_where_output_is_piped_says_nothing_of_its_progress(indexed: Path) -> None:
    done = CliRunner().invoke(
        app, ["dive", "-p", str(indexed), "--budget", "90", "--depth", "deep"]
    )
    assert done.exit_code == 0, done.output
    assert "Reading up to" not in done.output
    assert "so far" not in done.output


def test_a_dive_in_a_pipe_says_nothing_though_colour_is_forced(
    indexed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FORCE_COLOR`` and ``TTY_COMPATIBLE`` make rich call a pipe a terminal; a dive does not."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TTY_COMPATIBLE", "1")
    monkeypatch.setattr(cli, "console", Console(color_system=None, width=200))
    assert cli.console.is_terminal, "rich takes the forced pipe for a terminal"
    done = CliRunner().invoke(
        app, ["dive", "-p", str(indexed), "--budget", "90", "--depth", "deep"]
    )
    assert done.exit_code == 0, done.output
    assert "Reading up to" not in done.output
    assert "so far" not in done.output


def test_a_dive_says_how_far_it_has_got_every_interval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "console", Console(force_terminal=True, color_system=None, width=200))
    monkeypatch.setattr(cli, "DIVE_NOTE_SECONDS", 0.0)
    notes = cli._DiveNotes()
    snapshot = ProgressSnapshot(run_id="r", stage="dive", entries_examined=3)
    notes.update(snapshot)
    assert capsys.readouterr().out == "", "nothing before the dive knows how many files it has"
    notes.start(1500, None)
    assert "no time limit" in capsys.readouterr().out
    notes.update(snapshot)
    assert "3 of 1,500 files tried so far" in _said(capsys.readouterr().out)
    notes.start(0, 60.0)
    assert capsys.readouterr().out == "", "a dive with nothing to read says nothing"


def test_a_dive_says_nothing_more_inside_the_interval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "console", Console(force_terminal=True, color_system=None, width=200))
    notes = cli._DiveNotes()
    notes.start(10, 5.0)
    capsys.readouterr()
    notes.update(ProgressSnapshot(run_id="r", stage="dive", entries_examined=2))
    assert capsys.readouterr().out == ""


# -- counts that agree ---------------------------------------------------------------------------


def test_infer_counts_the_conclusions_the_dashboard_counts_and_says_what_each_part_is() -> None:
    assert cli._conclusions_said(36, 20) == "56 results (36 from the rules, 20 from the file list)"
    assert cli._conclusions_said(0, 20) == "20 results (0 from the rules, 20 from the file list)"
    assert cli._conclusions_said(1, 0) == "1 result"
    assert cli._conclusions_said(2_286, 0) == "2,286 results"


def test_infer_fix_and_the_run_agree_on_how_many_conclusions(project: Path) -> None:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        stored = catalog.count("claims")
    runner = CliRunner()
    inferred = _said(runner.invoke(app, ["infer", "-p", str(project)]).output)
    assert f"{stored:,} results (" in inferred, inferred
    fixed = _said(runner.invoke(app, ["fix", "-p", str(project)]).output)
    assert f"results: {stored}" in fixed, fixed
    assert "claims:" not in fixed


def test_the_index_progress_line_says_what_it_counts(capsys: pytest.CaptureFixture[str]) -> None:
    cli._render(ProgressSnapshot(run_id="r", stage="census", entries_seen=12, directories_seen=7))
    printed = _said(capsys.readouterr().out)
    assert "12 items" in printed
    assert "7 folders opened" in printed
    assert "directories" not in printed


def test_a_left_out_count_is_grammatical(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    for number in range(15):
        (pool / f".hidden-{number}").write_text("x")
    (pool / "notes.txt").write_text("hello")
    runner = CliRunner()
    assert (
        runner.invoke(app, ["init", str(tmp_path / "project"), "--root", str(pool)]).exit_code == 0
    )
    indexed = runner.invoke(app, ["index", "-p", str(tmp_path / "project"), "--quiet"])
    assert indexed.exit_code == 0, indexed.output
    said = _said(indexed.output)
    assert "left out, hidden names: 15 files" in said
    assert "15 file," not in said
    assert "15 file " not in said


# -- see prints its address where output is a pipe -----------------------------------------------


def _free_port() -> int:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return int(held.getsockname()[1])


def test_see_prints_its_address_when_its_output_is_a_pipe(project: Path, tmp_path: Path) -> None:
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    port = _free_port()
    environment = {
        **os.environ,
        "INGENCE_CONFIG_HOME": str(config_home),
        "PYTHONPATH": os.pathsep.join([str(REPOSITORY / "src"), os.environ.get("PYTHONPATH", "")]),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "see",
            "-p",
            str(project),
            "--port",
            str(port),
            "--no-open",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    first: list[str] = []
    reader = threading.Thread(target=lambda: first.append(process.stdout.readline()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    try:
        reader.join(timeout=60)
        assert first, "nothing was printed to the pipe within a minute"
        assert f"http://127.0.0.1:{port}" in first[0]
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        if process.stdout is not None:
            process.stdout.close()
