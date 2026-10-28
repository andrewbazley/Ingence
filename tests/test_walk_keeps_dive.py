"""A walk keeps what a dive read of a file that has not changed at all.

A census keeps a dive's readings for files that have not changed between walks. The test proves it
on a synthetic pool: indexed, dived, then one file changed, one deleted and one left alone, and
walked again. The unchanged file keeps its readings and its outcome. The changed file's are dropped
with it. The deleted file's go with its tombstone. With the setting off, a walk states only what it
saw. Every name here is invented.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.extractors import load_builtins as load_extractors
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

LATER = 1_767_225_600  # 2026-01-01, so a change moves the modification time for certain


def _dived(tmp_path: Path, *, keep: bool = True) -> tuple[Project, Path]:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn notes.txt").write_text("alpha beta gamma\n" * 40)
    (pool / "kelt" / "tamen.csv").write_text("x,y\n1,2\n3,4\n")
    (pool / "kelt" / "brisa.txt").write_text("delta epsilon\n" * 30)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    if not keep:
        settings = yaml.safe_load((project.path / "settings.yaml").read_text())
        settings.setdefault("scan", {})["keep_dive_readings"] = False
        (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
        project = Project.open(project.path)
    load_extractors()
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project, pool


def _read(project: Project, name: str) -> tuple[int, str, bool]:
    """How many dive readings the file holds now, its outcome, and whether it is deleted."""
    with open_catalog(project.path) as catalog:
        entry_id, outcome, deleted = catalog.query(
            "SELECT entry_id, scan_outcome, is_deleted FROM entries WHERE name = ?", [name]
        ).fetchone()
        readings = catalog.query(
            "SELECT count(*) FROM observations WHERE entry_id = ? "
            "AND observer NOT IN ('census', 'census.organisation')",
            [entry_id],
        ).fetchone()[0]
    return int(readings), str(outcome), bool(deleted)


def _walk_again(project: Project, pool: Path) -> None:
    changed = pool / "kelt" / "tamen.csv"
    changed.write_text("x,y\n1,2\n3,4\n5,6\n")
    os.utime(changed, (LATER, LATER))
    (pool / "kelt" / "brisa.txt").unlink()
    run_census(project, run_id="run_20260101T000003Z_333333333333", started_at="t")


def test_an_unchanged_file_keeps_its_readings_and_a_changed_or_deleted_one_loses_them(
    tmp_path: Path,
) -> None:
    project, pool = _dived(tmp_path)
    kept_before = _read(project, "vorn notes.txt")
    assert kept_before[0] > 0 and kept_before[1] != "metadata_only", "the dive read it"
    assert _read(project, "tamen.csv")[0] > 0
    _walk_again(project, pool)
    assert _read(project, "vorn notes.txt") == kept_before
    readings, outcome, _ = _read(project, "tamen.csv")
    assert (readings, outcome) == (0, "metadata_only"), "a changed file's dive is dropped"
    readings, _, deleted = _read(project, "brisa.txt")
    assert deleted and readings == 0


def test_with_the_setting_off_a_walk_states_only_what_it_saw(tmp_path: Path) -> None:
    project, pool = _dived(tmp_path, keep=False)
    assert _read(project, "vorn notes.txt")[0] > 0
    _walk_again(project, pool)
    assert _read(project, "vorn notes.txt")[:2] == (0, "metadata_only")


@pytest.mark.parametrize("keep", [True, False])
def test_the_run_journal_says_what_was_kept(tmp_path: Path, keep: bool) -> None:
    project, pool = _dived(tmp_path, keep=keep)
    _walk_again(project, pool)
    with open_catalog(project.path) as catalog:
        kept = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'walk.readings_kept'"
        ).fetchall()
    assert bool(kept) is keep
    if keep:
        assert '"files_unchanged":1' in kept[0][0].replace(" ", "")
