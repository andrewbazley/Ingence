"""``ingence dive`` after a Ctrl-C.

The class: the command line never reports a run it did not publish as read. A dive whose token is
cancelled before it publishes prints no "Read N files ... as run X" for a run it abandoned and does
not end with a status of 0, so a script cannot take an interrupted dive for a finished one. Every
name here is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ingence.cli import main as cli
from ingence.cli.main import app
from ingence.scan.cancel import CancellationToken
from ingence.storage.commit import read_pointer

_TREE = (
    "2026-03-04 plate reads/plate_A_well_01.txt",
    "2026-03-04 plate reads/plate_A_well_02.txt",
    "2026-03-04 plate reads/plate_B_well_01.txt",
    "2026-03-05 plate reads/notes.txt",
)


def _said(output: str) -> str:
    return " ".join(output.split())


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    """A project indexed and not yet dived, so a dive has files to try."""
    pool = tmp_path / "pool"
    for relative in _TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("some invented notes about a plate\n" * 4)
    made = tmp_path / "project"
    runner = CliRunner()
    for arguments in (
        ["init", str(made), "--root", str(pool)],
        ["index", "-p", str(made), "--quiet"],
    ):
        done = runner.invoke(app, arguments)
        assert done.exit_code == 0, done.output
    return made


class _AlreadyCancelled(CancellationToken):
    """A token cancelled before the dive opens a file, as a Ctrl-C at its start is."""

    def __init__(self) -> None:
        super().__init__()
        self.cancel("interrupted by SIGINT")


def test_a_cancelled_dive_says_so_publishes_nothing_and_ends_with_1(
    indexed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "CancellationToken", _AlreadyCancelled)
    before = read_pointer(indexed)
    assert before is not None

    done = CliRunner().invoke(app, ["dive", "-p", str(indexed), "--budget", "60"])

    assert done.exit_code == 1, done.output
    said = _said(done.output)
    assert "Stopped: interrupted by SIGINT" in said
    assert "Ingence saved nothing" in said
    assert "as run" not in said, "an abandoned run is never reported as read"
    assert "Next:" not in said, "no next step is offered for a run that was not published"
    after = read_pointer(indexed)
    assert after is not None
    assert after.run_id == before.run_id, "the previous run is still current"
