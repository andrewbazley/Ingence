"""``ingence fix --verify`` prints the same sample rows in the same order, run after run.

``--verify`` exists to check that two runs agree, and its own report has to be the one thing that
does not change when nothing else does. Without an ``ORDER BY``, a query with a ``LIMIT`` lets two
calls on one project choose different rows, or the same rows in a different order. The rows are
taken in the canonical order of each table's primary key.

The project is built from invented file names: forty new files between the two runs, so that
there are many more differing rows than the report shows.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.settings import Settings
from ingence.scan.census import run_census
from ingence.storage.catalog import describe_differences

if TYPE_CHECKING:
    from pathlib import Path

EARLIER = "run_20320101T000001Z_aaaaaaaaaaaa"
LATER = "run_20320101T000002Z_bbbbbbbbbbbb"


@pytest.fixture(scope="module")
def project_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project whose two runs differ in a great many rows."""
    base = tmp_path_factory.mktemp("verify_order")
    root = base / "Example_Lab"
    root.mkdir()
    for number in range(20):
        (root / f"HeLa_plate_{number:02d}.tif").write_bytes(bytes([number]) * 16)
    project = Project.create(
        base / "project", created_at="2032-01-01T00:00:00Z", roots=[root], settings=Settings()
    )
    run_census(Project.open(project.path), run_id=EARLIER, started_at="2032-01-01T00:00:01Z")
    for number in range(20, 60):
        (root / f"HEK293_plate_{number:02d}.tif").write_bytes(bytes([number]) * 16)
    run_census(Project.open(project.path), run_id=LATER, started_at="2032-01-01T00:00:02Z")
    return project.path


@pytest.fixture(autouse=True)
def _a_console_wide_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """A console of its own with a width set, so a long path never wraps."""
    monkeypatch.setattr("ingence.cli.main.console", Console(width=500))


def _verify(path: Path) -> str:
    result = CliRunner().invoke(app, ["fix", "-p", str(path), "--verify"])
    assert result.exit_code == 1, result.output
    return result.output


def test_two_verify_runs_on_one_project_print_identical_text(project_path: Path) -> None:
    first = _verify(project_path)
    second = _verify(project_path)
    assert "differing rows" in first
    assert " has '" in first
    assert first == second


def test_the_rows_of_the_description_come_back_the_same_every_time(project_path: Path) -> None:
    seen = {repr(describe_differences(project_path, EARLIER, LATER, limit=8)) for _ in range(6)}
    assert len(seen) == 1


def test_the_sample_rows_are_in_the_canonical_order_of_their_keys(project_path: Path) -> None:
    found = describe_differences(project_path, EARLIER, LATER, limit=20)
    assert found
    only_in = re.compile(r"^only in the (earlier|later) run: (\S+)$")
    changed = re.compile(r"^(\w+): (\S+) has ")
    checked = 0
    for table, lines in found.items():
        # The rows that exist on one side only: the earlier run's first, then the later run's,
        # each in the order of the key.
        absent = [only_in.match(line) for line in lines if only_in.match(line)]
        keys = [(m.group(1) != "earlier", m.group(2)) for m in absent if m]
        assert keys == sorted(keys), (table, keys)
        # The rows that exist on both: column by column in the table's own order, each column's
        # rows in the order of the key.
        by_column: dict[str, list[str]] = {}
        for line in lines:
            match = changed.match(line)
            if match:
                by_column.setdefault(match.group(1), []).append(match.group(2))
        for column, ids in by_column.items():
            assert ids == sorted(ids), (table, column, ids)
        checked += len(keys) + sum(len(ids) for ids in by_column.values())
    assert checked > 20
