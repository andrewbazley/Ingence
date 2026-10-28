"""A walk knows a file that moved: its readings and decisions follow it.

A file at a path the last census did not have, whose name, size and modification time are those
of exactly one file the last census had and this walk did not find, is that file moved: its entry
names the one it came from, a dive's readings of it are carried to the new place, and a person's
decisions about it are read there. The Record on disk is untouched. Every name is invented.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import MovedBy, RecordEventKind
from ingence.core.settings import Settings
from ingence.infer.run import run_inference
from ingence.record.log import append_event, read_events
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 8)]


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    (pool / "zib_2024").mkdir(parents=True)
    (pool / "zib_2024" / "kelt_vorn_notes.txt").write_text("three vorn replicates\n" * 20)
    (pool / "zib_2024" / "brisa_01.csv").write_text("a,b\n1,2\n")
    return pool


def _entry(path: Path, name: str) -> dict:
    with open_catalog(path) as catalog:
        row = catalog.query(
            "SELECT entry_id, relative_path, moved_from, moved_by, scan_outcome FROM entries "
            "WHERE name = ? AND NOT is_deleted",
            [name],
        ).fetchone()
    return dict(zip(("id", "path", "moved_from", "moved_by", "outcome"), row, strict=True))


def _readings(path: Path, entry_id: str) -> set[tuple[str, str]]:
    with open_catalog(path) as catalog:
        return set(
            catalog.query(
                "SELECT key, coalesce(value_text, CAST(value_integer AS VARCHAR), '') "
                "FROM observations WHERE entry_id = ? AND observer <> 'census'",
                [entry_id],
            ).fetchall()
        )


def _moved(pool: Path, name: str, to: str) -> None:
    (pool / to).mkdir(parents=True, exist_ok=True)
    source = pool / "zib_2024" / name
    kept = source.stat()
    shutil.move(source, pool / to / name)
    os.utime(pool / to / name, ns=(kept.st_atime_ns, kept.st_mtime_ns))


def test_a_moved_file_keeps_its_readings_and_its_decisions(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    run_deepen(project, run_id=RUNS[1], started_at="2026-01-01T00:00:02Z", budget_seconds=60)
    before = _entry(project.path, "kelt_vorn_notes.txt")
    readings = _readings(project.path, before["id"])
    assert readings, "the dive read the note"
    append_event(
        project.path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-01T00:00:03Z",
        target=f"file:{before['id']}",
        against_run_id=RUNS[1],
        payload={"text": "the good replicates"},
    )

    _moved(pool, "kelt_vorn_notes.txt", "archive/zib")
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")

    after = _entry(project.path, "kelt_vorn_notes.txt")
    assert after["id"] != before["id"]
    assert after["path"].startswith("archive")
    assert after["moved_from"] == before["id"]
    # Confirmed by its birth time where the filesystem gives one.
    assert after["moved_by"] in (
        MovedBy.NAME_SIZE_TIME.value,
        MovedBy.BIRTH_TIME.value,
        MovedBy.SAME_FILE.value,
    )
    assert after["outcome"] == before["outcome"]
    assert _readings(project.path, after["id"]) == readings
    # The decision is read at the new place, and says where it was written.
    notes = [event for event in read_events(project.path) if event.kind is RecordEventKind.NOTE]
    assert notes[0].target == f"file:{after['id']}"
    assert f"file:{before['id']}" in notes[0].payload
    # The Record on disk still says what was written.
    assert read_events(project.path, moved=False)[0].target == f"file:{before['id']}"
    # And inference over the walk runs.
    run_inference(project, run_id=RUNS[3], started_at="2026-01-01T00:00:05Z")


def test_a_second_move_is_followed_from_the_first_place(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    first = _entry(project.path, "kelt_vorn_notes.txt")["id"]
    append_event(
        project.path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-01T00:00:02Z",
        target=f"file:{first}",
        against_run_id=RUNS[0],
        payload={"text": "x"},
    )
    _moved(pool, "kelt_vorn_notes.txt", "one")
    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:03Z")
    (pool / "two").mkdir()
    shutil.move(pool / "one" / "kelt_vorn_notes.txt", pool / "two" / "kelt_vorn_notes.txt")
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    last = _entry(project.path, "kelt_vorn_notes.txt")
    assert last["path"].startswith("two")
    assert read_events(project.path)[0].target == f"file:{last['id']}"


@pytest.mark.parametrize("numbers", [True, False])
def test_two_files_alike_in_name_size_and_time(tmp_path: Path, numbers: bool) -> None:
    """Two candidates for one key are not a move unless the filesystem tells them apart. On one
    volume each keeps its own number through a rename, and each keeps its birth time.

    What tells them apart is read from the census, per platform. Setting a file's modification
    time earlier than its birth pulls the birth back to it on APFS, so there the two births are
    the one modification time and say nothing. On Windows they stay the two moments the files
    were made, and tell the files apart. On Linux the walk reads them with ``statx`` where the
    filesystem keeps one. Windows' directory listing gives no file number (``DirEntry.stat`` sets
    ``st_ino`` to 0, stored as none).
    """
    pool = tmp_path / "pool"
    for folder in ("a", "b"):
        (pool / folder).mkdir(parents=True)
        (pool / folder / "zib.txt").write_text("same bytes")
        os.utime(pool / folder / "zib.txt", ns=(1_700_000_000_000_000_000,) * 2)
    settings = Settings()
    settings = settings.model_copy(
        update={"scan": settings.scan.model_copy(update={"moves_by_file_number": numbers})}
    )
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT relative_path, entry_id, created_ns, created_time_source, modified_ns, "
            "inode, device FROM entries WHERE name = 'zib.txt' ORDER BY relative_path"
        ).fetchall()
    before = {row[0]: row[1] for row in rows}
    births = [row[2] if row[3] == "birthtime" else None for row in rows]
    # Each birth its own, and neither the modification time over again (moves.BIRTH_GRAIN_NS).
    by_birth = (
        None not in births
        and abs(births[0] - births[1]) >= 1_000
        and all(abs(row[2] - row[4]) >= 1_000 for row in rows)
    )
    numbered = numbers and all(row[5] is not None and row[6] is not None for row in rows)
    for folder in ("a", "b"):
        (pool / f"{folder}2").mkdir()
        shutil.move(pool / folder / "zib.txt", pool / f"{folder}2" / "zib.txt")
    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:02Z")
    with open_catalog(project.path) as catalog:
        moved = {
            path: (source, how)
            for path, source, how in catalog.query(
                "SELECT relative_path, moved_from, moved_by FROM entries "
                "WHERE moved_from IS NOT NULL"
            ).fetchall()
        }
    if numbered or by_birth:
        how = MovedBy.SAME_FILE.value if numbered else MovedBy.BIRTH_TIME.value
        assert moved == {
            "a2/zib.txt": (before["a/zib.txt"], how),
            "b2/zib.txt": (before["b/zib.txt"], how),
        }
    else:
        # Nothing tells them apart: no move.
        assert moved == {}
    if sys.platform == "darwin":
        # APFS pulled both births back to the one modification time: only the numbers can say.
        assert not by_birth
        assert (moved != {}) is numbers


def test_a_copy_is_not_a_move_and_the_setting_turns_moves_off(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    settings = Settings()
    settings = settings.model_copy(
        update={"scan": settings.scan.model_copy(update={"follow_moves": False})}
    )
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    _moved(pool, "brisa_01.csv", "elsewhere")
    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:02Z")
    assert _entry(project.path, "brisa_01.csv")["moved_from"] is None

    copied = tmp_path / "second"
    pool_two = _pool(copied)
    other = Project.create(copied / "project", created_at="t", roots=[pool_two])
    run_census(other, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    (pool_two / "copy").mkdir()
    shutil.copy2(pool_two / "zib_2024" / "brisa_01.csv", pool_two / "copy" / "brisa_01.csv")
    run_census(other, run_id=RUNS[1], started_at="2026-01-01T00:00:02Z")
    with open_catalog(other.path) as catalog:
        assert (
            catalog.query("SELECT count(*) FROM entries WHERE moved_from IS NOT NULL").fetchone()[0]
            == 0
        )


def test_a_file_moved_away_and_back_keeps_its_decisions_where_it_is(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    first = _entry(project.path, "kelt_vorn_notes.txt")["id"]
    append_event(
        project.path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-01T00:00:02Z",
        target=f"file:{first}",
        against_run_id=RUNS[0],
        payload={"text": "x"},
    )
    _moved(pool, "kelt_vorn_notes.txt", "one")
    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:03Z")
    source = pool / "one" / "kelt_vorn_notes.txt"
    kept = source.stat()
    shutil.move(source, pool / "zib_2024" / "kelt_vorn_notes.txt")
    os.utime(pool / "zib_2024" / "kelt_vorn_notes.txt", ns=(kept.st_atime_ns, kept.st_mtime_ns))
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    back = _entry(project.path, "kelt_vorn_notes.txt")
    assert back["id"] == first
    assert read_events(project.path)[0].target == f"file:{first}"
