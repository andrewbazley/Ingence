"""The console script exists, prints its versions, and does not pretend."""

from __future__ import annotations

import json
import subprocess
import sys

import typer

from ingence import SCHEMA_VERSION, __version__
from ingence.cli.main import app


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_prints_both_versions():
    result = run("--version")
    assert result.returncode == 0
    assert __version__ in result.stdout
    assert SCHEMA_VERSION in result.stdout


def test_version_names_the_build_commit_when_one_is_known():
    from ingence.core.provenance import engine_commit

    commit = engine_commit()
    result = run("--version")
    if commit is None:
        assert "build" not in result.stdout
    else:
        assert f"build {commit[:12]}" in result.stdout


def test_the_verb_set_is_the_settled_one():
    assert set(typer.main.get_command(app).commands) == {
        "init",
        "index",
        "dive",
        "infer",
        "map",
        "see",
        "demo",
        "rule",
        "teach",
        "record",
        "log",
        "export",
        "fix",
        "mcp",
    }


def test_an_unimplemented_verb_fails_rather_than_pretending():
    result = run("map")
    assert result.returncode == 2
    assert "not available in this version" in result.stderr


def test_the_full_flow_creates_indexes_and_diagnoses(tmp_path):
    """init, index, index again, and fix --verify, as a person would run them."""
    pool = tmp_path / "pool"
    (pool / "exp").mkdir(parents=True)
    (pool / "exp" / "a.tif").write_bytes(b"x" * 32)
    (pool / "notes.txt").write_text("hello")
    project = tmp_path / "project"

    created = run("init", str(project), "--root", str(pool))
    assert created.returncode == 0, created.stderr
    assert (project / "project.json").is_file()

    # The reuse reasons are the developer's detail, printed with --verbose.
    first = run("index", "--project", str(project), "--quiet", "--verbose")
    assert first.returncode == 0, first.stderr
    assert "4 items" in first.stdout
    assert "no_previous_record 2" in first.stdout

    second = run("index", "--project", str(project), "--quiet", "--verbose")
    assert second.returncode == 0, second.stderr
    assert "reuse 2" in second.stdout

    # A third run, because fix --verify compares the two most recent and a tree
    # written moments earlier has not finished settling its directory
    # timestamps on every filesystem. The first run absorbs that; see the
    # determinism test in test_census.py for the full explanation.
    third = run("index", "--project", str(project), "--quiet")
    assert third.returncode == 0, third.stderr

    diagnosed = run("fix", "--project", str(project), "--verify")
    assert diagnosed.returncode == 0, diagnosed.stderr
    assert "give the same results" in " ".join(diagnosed.stdout.split())


def test_index_and_fix_say_what_the_census_left_out(tmp_path):
    """A person asking why the engine does not see their files gets an answer."""
    pool = tmp_path / "pool"
    (pool / "$RECYCLE.BIN" / "deleted").mkdir(parents=True)
    (pool / "$RECYCLE.BIN" / "deleted" / "big.bin").write_bytes(b"x" * 64)
    (pool / ".DS_Store").write_text("junk")
    (pool / "notes.txt").write_text("hello")
    project = tmp_path / "project"
    assert run("init", str(project), "--root", str(pool)).returncode == 0

    indexed = run("index", "--project", str(project), "--quiet")
    assert indexed.returncode == 0, indexed.stderr
    text = " ".join(indexed.stdout.split())
    assert "left out, $RECYCLE.BIN: 1 folder (folders not opened)" in text
    assert "left out, hidden names: 1 file" in text

    diagnosed = run("fix", "--project", str(project))
    assert diagnosed.returncode == 0, diagnosed.stderr
    text = " ".join(diagnosed.stdout.split())
    assert "left out, on purpose:" in text
    assert "$RECYCLE.BIN: 1 folder" in text


def test_index_hands_the_census_the_extractors_whose_versions_fingerprint_each_file(tmp_path):
    """``index`` gives the census the extractor registry."""
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "notes.txt").write_text("hello")
    project = tmp_path / "project"
    assert run("init", str(project), "--root", str(pool)).returncode == 0

    indexed = run("index", "--project", str(project), "--quiet")
    assert indexed.returncode == 0, indexed.stderr

    with open_catalog(project) as catalog:
        (components,) = catalog.query('SELECT components_json FROM "run"').fetchone()
    recorded = json.dumps(json.loads(str(components)))
    assert '"text"' in recorded
    assert '"compound"' in recorded


def test_indexing_outside_a_project_fails_clearly(tmp_path):
    result = run("index", "--project", str(tmp_path))
    assert result.returncode == 1
    assert "not an Ingence project" in " ".join(result.stdout.split())


def test_a_project_with_no_roots_says_so(tmp_path):
    project = tmp_path / "project"
    run("init", str(project))
    result = run("index", "--project", str(project))
    assert result.returncode == 1
    assert "This project has no data folder" in result.stdout


def test_teach_reads_the_projects_own_vocabulary(tmp_path):
    """Two read-only subcommands. Revision and promotion stay with the Rulebook.

    ``teach unknown`` is the discovery surface: it computes the unbound tokens
    from the Index on demand rather than reading proposals somebody wrote, so
    nothing accumulates unreviewed and the ranking sharpens as a pool grows.
    """
    pool = tmp_path / "pool"
    (pool / "exp1").mkdir(parents=True)
    (pool / "exp1" / "confocal_stack_2026-01-04.tif").write_bytes(b"x")
    (pool / "exp1" / "zzqqxx_notes.txt").write_bytes(b"y")
    project = tmp_path / "project"

    # A project loads only the domain packs it names. Confocal is microscopy's.
    assert run("init", str(project), "-r", str(pool), "--domain", "microscopy").returncode == 0
    assert run("index", "-p", str(project), "--quiet").returncode == 0

    recognised = run("teach", "vocabulary", "-p", str(project))
    assert recognised.returncode == 0
    assert "confocal" in recognised.stdout

    unknown = run("teach", "unknown", "-p", str(project))
    assert unknown.returncode == 0
    assert "zzqqxx" in unknown.stdout


def test_teach_collisions_says_which_contested_spellings_are_reported_and_which_decided():
    """``teach collisions`` says which collisions are decided and which are reported.

    Precedence makes a winner rather than a tiebreak, so the command shows which collisions
    are won and which are reported."""
    result = run("teach", "collisions", "-n", "5")
    assert result.returncode == 0
    assert "contested spellings" in result.stdout
    assert "reported as a tie" in result.stdout


def test_teach_collisions_answers_without_an_index_because_it_reads_the_vocabulary():
    """Over what the Dictionary holds rather than over what a pool used, so it answers at
    install time, which is where a pack's disagreements have to be visible."""
    result = run("teach", "collisions", "--decided")
    assert result.returncode == 0
    said = " ".join(result.stdout.split())
    # Every shipped section stands level, so no collision is won outright: each is a tie.
    assert "No collision is decided: every contested spelling is reported as a tie." in said


def test_ingence_alone_opens_the_dashboard_and_help_stays_on_help(monkeypatch, tmp_path):
    """``ingence`` with no arguments does what ``ingence see`` does.

    The help is ``ingence --help``."""
    from typer.testing import CliRunner

    from ingence.cli import main as cli

    opened: list[tuple] = []
    monkeypatch.setattr(cli, "serve", lambda found, **kwargs: opened.append((found, kwargs)))
    monkeypatch.chdir(tmp_path)  # no project here: the landing page, never an error
    result = CliRunner().invoke(cli.app, [])
    assert result.exit_code == 0, result.output
    assert opened == [(None, {"port": 0, "open_browser": True})]
    shown = CliRunner().invoke(cli.app, ["--help"])
    assert shown.exit_code == 0 and "see" in shown.output and not opened[1:]


def test_teach_without_a_subcommand_shows_its_help():
    result = run("teach")
    assert result.returncode != 0
    assert "vocabulary" in result.stdout or "vocabulary" in result.stderr
