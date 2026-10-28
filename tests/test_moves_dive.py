"""A dive confirms a move by its digest, or undoes it.

A walk matches a moved file without reading it, and carries what a dive read of the file it came
from. The next dive checks the carry by the digest it carried. The same digest upgrades the move to
``sampled_digest`` or ``whole_digest``. A different digest undoes the move (``digest_differs``): the
carried readings are dropped, the file is read as the new file it is, a person's decisions about the
file it was taken for stop following it, and the History page's notice and the journal say so.
Every name is invented.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import MovedBy, RecordEventKind
from ingence.core.settings import HashMode, Settings
from ingence.record.log import append_event, read_events
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 8)]
SECOND = 1_000_000_000
#: An invented moment long before the walks, so a birth time is pulled back to it where the
#: filesystem does that, and a copy cannot be told from the file by its birth.
LONG_AGO = 1_700_000_000 * SECOND


def _settings(mode: HashMode = HashMode.QUICK, **scan: object) -> Settings:
    settings = Settings()
    return settings.model_copy(
        update={
            "scan": settings.scan.model_copy(update=scan),
            "hash": settings.hash.model_copy(update={"mode": mode}),
        }
    )


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    (pool / "zib_2024").mkdir(parents=True)
    (pool / "zib_2024" / "kelt_vorn_notes.txt").write_text("three vorn replicates\n" * 20)
    (pool / "zib_2024" / "brisa_01.csv").write_text("a,b\n1,2\n")
    for path in (pool / "zib_2024").iterdir():
        os.utime(path, ns=(LONG_AGO, LONG_AGO))
    return pool


def _row(path: Path, name: str) -> dict:
    with open_catalog(path) as catalog:
        row = catalog.query(
            "SELECT entry_id, moved_from, moved_by, scan_outcome FROM entries "
            "WHERE name = ? AND NOT is_deleted",
            [name],
        ).fetchone()
    return dict(zip(("id", "moved_from", "moved_by", "outcome"), row, strict=True))


def _readings(path: Path, entry_id: str) -> set[tuple[str, str]]:
    with open_catalog(path) as catalog:
        return set(
            catalog.query(
                "SELECT key, coalesce(value_text, CAST(value_integer AS VARCHAR), '') "
                "FROM observations WHERE entry_id = ? AND observer <> 'census'",
                [entry_id],
            ).fetchall()
        )


def _journal(path: Path, event: str) -> list[dict]:
    """The current run's journal events of one kind, each with its entry and its detail."""
    with open_catalog(path) as catalog:
        rows = catalog.query(
            "SELECT entry_id, detail FROM journal_events WHERE event = ? ORDER BY entry_id",
            [event],
        ).fetchall()
    return [{"entry_id": entry, **json.loads(detail or "{}")} for entry, detail in rows]


def _read_and_note(project: Project) -> str:
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    run_deepen(project, run_id=RUNS[1], started_at="2026-01-01T00:00:02Z", budget_seconds=60)
    first = _row(project.path, "kelt_vorn_notes.txt")["id"]
    append_event(
        project.path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-01T00:00:03Z",
        target=f"file:{first}",
        against_run_id=RUNS[1],
        payload={"text": "the good replicates"},
    )
    return first


@pytest.mark.parametrize(
    ("mode", "confirmed_by"),
    [(HashMode.QUICK, MovedBy.SAMPLED_DIGEST), (HashMode.FULL, MovedBy.WHOLE_DIGEST)],
)
def test_a_dive_confirms_a_carried_move_by_its_digest(
    tmp_path: Path, mode: HashMode, confirmed_by: MovedBy
) -> None:
    pool = _pool(tmp_path)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(mode)
    )
    first = _read_and_note(project)
    readings = _readings(project.path, first)
    (pool / "archive").mkdir()
    shutil.move(pool / "zib_2024" / "kelt_vorn_notes.txt", pool / "archive" / "kelt_vorn_notes.txt")
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    walked = _row(project.path, "kelt_vorn_notes.txt")
    assert walked["moved_from"] == first
    assert walked["moved_by"] in (
        MovedBy.NAME_SIZE_TIME.value,
        MovedBy.BIRTH_TIME.value,
        MovedBy.SAME_FILE.value,
    )

    run_deepen(project, run_id=RUNS[3], started_at="2026-01-01T00:00:05Z", budget_seconds=60)
    dived = _row(project.path, "kelt_vorn_notes.txt")
    assert (dived["moved_from"], dived["moved_by"]) == (first, confirmed_by.value)
    assert _readings(project.path, dived["id"]) == readings
    assert read_events(project.path)[0].target == f"file:{dived['id']}"
    assert len(_journal(project.path, "move.confirmed")) == 1


def test_a_dive_that_confirms_a_copy_s_bytes_keeps_saying_it_was_a_copy(tmp_path: Path) -> None:
    """A copy whose original is gone is read as a move, and a dive finding the same bytes confirms
    it without forgetting how it was made (``copied_same_bytes``)."""
    pool = _pool(tmp_path)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.QUICK)
    )
    first = _read_and_note(project)
    readings = _readings(project.path, first)
    (pool / "archive").mkdir()
    original = pool / "zib_2024" / "kelt_vorn_notes.txt"
    shutil.copy2(original, pool / "archive" / "kelt_vorn_notes.txt")
    original.unlink()
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    walked = _row(project.path, "kelt_vorn_notes.txt")
    assert (walked["moved_from"], walked["moved_by"]) == (first, MovedBy.COPIED.value)

    run_deepen(project, run_id=RUNS[3], started_at="2026-01-01T00:00:05Z", budget_seconds=60)
    dived = _row(project.path, "kelt_vorn_notes.txt")
    assert (dived["moved_from"], dived["moved_by"]) == (first, MovedBy.COPIED_SAME_BYTES.value)
    assert _readings(project.path, dived["id"]) == readings
    assert read_events(project.path)[0].target == f"file:{dived['id']}"


def test_a_dive_that_finds_other_bytes_undoes_the_carry(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    # Refusing copies off, so the walk takes the impostor below for the file on every platform.
    settings = _settings(moves_refuse_copies=False)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)
    first = _read_and_note(project)
    old_readings = _readings(project.path, first)

    # Another file, alike in name, size and time: the walk cannot tell it from the one gone.
    original = pool / "zib_2024" / "kelt_vorn_notes.txt"
    size = original.stat().st_size
    original.unlink()
    (pool / "archive").mkdir()
    impostor = pool / "archive" / "kelt_vorn_notes.txt"
    impostor.write_text(("other trell words " * 40)[:size])
    os.utime(impostor, ns=(LONG_AGO, LONG_AGO))
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    walked = _row(project.path, "kelt_vorn_notes.txt")
    assert walked["moved_from"] == first
    assert read_events(project.path)[0].target == f"file:{walked['id']}"

    run_deepen(project, run_id=RUNS[3], started_at="2026-01-01T00:00:05Z", budget_seconds=60)
    dived = _row(project.path, "kelt_vorn_notes.txt")
    assert (dived["moved_from"], dived["moved_by"]) == (first, MovedBy.DIGEST_DIFFERS.value)
    # Read afresh in the same dive: its own readings, none of the old file's digests.
    fresh = _readings(project.path, dived["id"])
    assert fresh
    assert fresh != old_readings
    # The decision stays where it was written.
    assert read_events(project.path)[0].target == f"file:{first}"
    undone = _journal(project.path, "move.undone")
    assert [record["entry_id"] for record in undone] == [dived["id"]]

    # And the History page's notice says so.
    from ingence.api.routes.reorganisation import reorganisation

    shown = reorganisation(project.path)
    assert shown["noticed"] is True
    assert shown["moves_undone"] == [
        {"path": "archive/kelt_vorn_notes.txt", "taken_for": "zib_2024/kelt_vorn_notes.txt"}
    ]
    assert "other bytes" in shown["notice"]

    # A later walk and dive keep the decision where it was written.
    run_census(project, run_id=RUNS[4], started_at="2026-01-01T00:00:06Z")
    assert read_events(project.path)[0].target == f"file:{first}"


def _impostor(pool: Path) -> None:
    """The note's file gone, and another alike in name, size and time in a new folder."""
    original = pool / "zib_2024" / "kelt_vorn_notes.txt"
    size = original.stat().st_size
    original.unlink()
    (pool / "archive").mkdir()
    impostor = pool / "archive" / "kelt_vorn_notes.txt"
    impostor.write_text(("other trell words " * 40)[:size])
    os.utime(impostor, ns=(LONG_AGO, LONG_AGO))


@pytest.mark.parametrize("between", ["walk", "rebind"])
def test_a_walk_or_a_rebind_between_keeps_an_unchecked_move_for_the_dive(
    tmp_path: Path, between: str
) -> None:
    """Walk, walk (or rebind), dive. The second census keeps the unchecked move on the entry, so
    the dive finds the wrong match by its digest, and the note stays with the file it was
    written about."""
    pool = _pool(tmp_path)
    settings = _settings(moves_refuse_copies=False)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)
    first = _read_and_note(project)
    _impostor(pool)
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    run_census(
        project, run_id=RUNS[3], started_at="2026-01-01T00:00:05Z", rebind=between == "rebind"
    )
    kept = _row(project.path, "kelt_vorn_notes.txt")
    assert kept["moved_from"] == first
    # Another file, made after the one gone: a copy by its file number or its birth.
    assert kept["moved_by"] == MovedBy.COPIED.value

    run_deepen(project, run_id=RUNS[4], started_at="2026-01-01T00:00:06Z", budget_seconds=60)
    dived = _row(project.path, "kelt_vorn_notes.txt")
    assert (dived["moved_from"], dived["moved_by"]) == (first, MovedBy.DIGEST_DIFFERS.value)
    assert read_events(project.path)[0].target == f"file:{first}"


def test_confirming_by_digest_is_a_setting(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    settings = _settings(moves_confirmed_by_dive=False)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)
    _read_and_note(project)
    (pool / "archive").mkdir()
    shutil.move(pool / "zib_2024" / "kelt_vorn_notes.txt", pool / "archive" / "kelt_vorn_notes.txt")
    run_census(project, run_id=RUNS[2], started_at="2026-01-01T00:00:04Z")
    walked = _row(project.path, "kelt_vorn_notes.txt")["moved_by"]
    run_deepen(project, run_id=RUNS[3], started_at="2026-01-01T00:00:05Z", budget_seconds=60)
    assert _row(project.path, "kelt_vorn_notes.txt")["moved_by"] == walked


def test_a_digest_of_another_method_version_neither_confirms_nor_undoes() -> None:
    """A digest's algorithm can change with its method's version: the same bytes then give another
    value, and a check across versions would undo a real move. It says nothing instead."""
    from ingence.core.schema import Entry
    from ingence.scan.deepen import Budget, _check_moves

    moved = Entry(
        entry_id="new",
        root_id="root_0",
        relative_path="vorn/zib.txt",
        name="zib.txt",
        size_bytes=40,
        first_seen_run=RUNS[0],
        last_seen_run=RUNS[0],
        moved_from="old",
        moved_by=MovedBy.NAME_SIZE_TIME,
    )
    held = {"new": {"sampled": "aaaa"}}
    versions = {("new", "sampled"): "1"}

    def checked(answer: tuple[str, str]) -> dict:
        return _check_moves(
            [moved],
            held,
            versions=versions,
            enabled=True,
            check=lambda entry, kind: answer,
            budget=Budget(granted_seconds=60),
            token=None,
        )

    assert checked(("aaaa", "1"))["new"].same is True
    assert checked(("bbbb", "1"))["new"].same is False
    assert checked(("bbbb", "2")) == {}
