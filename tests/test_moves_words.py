"""The words every page shows for a move, and that a copy says it was one.

A copy that keeps its original's name, size and time, with the original removed, is read as a
move. The move says it was made by a copy, so a person can see it: in the stored move (``moved_by``
``copied``) and in what the Dashboard's inspector and the Graph workspace's inspector show, which
are one sentence made in one place. Every name is invented.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from ingence.core.schema import MovedBy

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("by", "said", "unplaced"),
    [
        (MovedBy.SAME_FILE, "from kelt/zib.txt", "from an earlier place"),
        (MovedBy.BIRTH_TIME, "from kelt/zib.txt", "from an earlier place"),
        (MovedBy.NAME_SIZE_TIME, "from kelt/zib.txt", "from an earlier place"),
        (MovedBy.NEIGHBOURS, "from kelt/zib.txt", "from an earlier place"),
        (MovedBy.CONTENTS, "from kelt/zib.txt", "from an earlier place"),
        (
            MovedBy.WHOLE_DIGEST,
            "from kelt/zib.txt, its contents checked",
            "from an earlier place, its contents checked",
        ),
        (
            MovedBy.SAMPLED_DIGEST,
            "from kelt/zib.txt, its contents spot-checked",
            "from an earlier place, its contents spot-checked",
        ),
        (
            MovedBy.COPIED,
            "as a copy of kelt/zib.txt, the original removed",
            "as a copy, the original removed",
        ),
        (
            MovedBy.COPIED_SAME_BYTES,
            "as a copy of kelt/zib.txt, the original removed, its contents spot-checked",
            "as a copy, the original removed, its contents spot-checked",
        ),
        (
            MovedBy.COPIED_CONTENTS,
            "as a copy of kelt/zib.txt, the original removed",
            "as a copy, the original removed",
        ),
        (
            MovedBy.DIGEST_DIFFERS,
            "not after all; its contents differ from those of kelt/zib.txt",
            "not after all; its contents differ from the file it was taken for",
        ),
    ],
)
def test_every_way_a_move_was_known_has_its_words(by: MovedBy, said: str, unplaced: str) -> None:
    """In plain words ("contents", never "bytes"; a sampled check is a spot check), and said once
    where the earlier place is not known."""
    from ingence.api.v1.moved import move_words

    assert move_words(by.value, "kelt/zib.txt") == said
    assert move_words(by.value, None) == unplaced


def test_every_word_the_stored_move_holds_has_words() -> None:
    from ingence.api.v1.moved import move_words

    for by in MovedBy:
        assert move_words(by.value, "kelt/zib.txt")
        assert move_words(by.value, None)


def test_a_copy_says_it_was_a_copy_on_both_inspectors(tmp_path: Path) -> None:
    """A copy made and its original removed, walked: the Dashboard's entry and the Graph
    workspace's card both say where it came from and that it was a copy, in the same words."""
    from ingence.api.v1 import context_for, dispatch
    from ingence.core.project import Project
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "vorn").mkdir()
    (pool / "kelt" / "zib_trell.csv").write_text("a,b\n1,2\n" * 8, encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    shutil.copy2(pool / "kelt" / "zib_trell.csv", pool / "vorn" / "zib_trell.csv")
    (pool / "kelt" / "zib_trell.csv").unlink()
    run_census(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT entry_id, moved_by FROM entries WHERE relative_path = 'vorn/zib_trell.csv'"
        ).fetchone()
    assert row is not None
    copy_id, how = row
    assert how == MovedBy.COPIED.value

    context = context_for(project.path, settled=None, job_running=False)
    status, entry = dispatch(context, "entry", {"id": [copy_id]})
    assert int(status) == 200, entry
    expected = "as a copy of kelt/zib_trell.csv, the original removed"
    assert entry["moved"]["by"] == "copied"
    assert entry["moved"]["from_path"] == "kelt/zib_trell.csv"
    assert entry["moved"]["said"] == expected
    status, card = dispatch(context, "explorer/entry", {"id": [copy_id]})
    assert int(status) == 200, card
    assert card["moved"] == entry["moved"]


def test_a_file_that_never_moved_says_nothing_about_moving(tmp_path: Path) -> None:
    from ingence.api.v1 import context_for, dispatch
    from ingence.core.project import Project
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "zib_trell.csv").write_text("a,b\n", encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'kelt/zib_trell.csv'"
        ).fetchone()
    assert row is not None
    context = context_for(project.path, settled=None, job_running=False)
    _, entry = dispatch(context, "entry", {"id": [row[0]]})
    assert entry["moved"] is None
    _, card = dispatch(context, "explorer/entry", {"id": [row[0]]})
    assert card["moved"] is None


def test_the_row_keeps_the_original_s_place_after_later_walks(tmp_path: Path) -> None:
    """The run that found the move holds the original as deleted, and the next walk does not. The
    row still names where it was, read from the project's earlier runs."""
    from ingence.api.v1 import context_for, dispatch
    from ingence.core.project import Project
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "vorn").mkdir()
    (pool / "kelt" / "zib_trell.csv").write_text("a,b\n1,2\n" * 8, encoding="utf-8")
    (pool / "kelt" / "brisa_notes.txt").write_text("one\n", encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    shutil.copy2(pool / "kelt" / "zib_trell.csv", pool / "vorn" / "zib_trell.csv")
    (pool / "kelt" / "zib_trell.csv").unlink()
    run_census(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    (pool / "kelt" / "brisa_notes.txt").write_text("one and two\n", encoding="utf-8")
    run_census(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    run_census(project, run_id="run_20260101T000004Z_444444444444", started_at="t")
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT entry_id, moved_by FROM entries WHERE relative_path = 'vorn/zib_trell.csv'"
        ).fetchone()
    assert row is not None and row[1] == MovedBy.COPIED.value
    context = context_for(project.path, settled=None, job_running=False)
    _, entry = dispatch(context, "entry", {"id": [row[0]]})
    _, card = dispatch(context, "explorer/entry", {"id": [row[0]]})
    expected = "as a copy of kelt/zib_trell.csv, the original removed"
    assert entry["moved"]["from_path"] == "kelt/zib_trell.csv"
    assert entry["moved"]["said"] == expected
    assert card["moved"] == entry["moved"]
