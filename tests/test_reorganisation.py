"""The comparison run over the engine's own censuses: invented trees on disk, moved the way a
person would, compared through the seam a page calls (``api/routes/reorganisation.py``), with a
decision followed to where its files went and the notice and the folder's one line that say so."""

from __future__ import annotations

import datetime as dt
import os
import shutil
import unicodedata
from pathlib import Path

import pytest

from ingence.api.routes import reorganisation as routes
from ingence.api.routes.reorganisation import census_pair, compare_runs, folder_line, reorganisation
from ingence.api.server import WEB_ROOT
from ingence.compare import carry
from ingence.compare.carry import Carried, Follow, folder_lines
from ingence.compare.match import Side
from ingence.core.ids import Scope, ScopeKind
from ingence.core.project import Project, _write_identity
from ingence.core.schema import CaseSensitivity, RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

# -- the engine's own censuses, over invented trees ------------------------------------------

_FIRST = "run_20260101T000001Z_111111111111"
_SECOND_RUN = "run_20260101T000002Z_222222222222"
_THIRD = "run_20260101T000003Z_333333333333"


def _tree(pool: Path) -> None:
    """Two projects of three experiments of four files, each file of its own size and time."""
    when = 1_700_000_000
    for project in ("projA", "projB"):
        for experiment in ("e1", "e2", "e3"):
            folder = pool / project / experiment
            folder.mkdir(parents=True)
            for n in range(4):
                path = folder / f"{project}_{experiment}_kelt_{n}.csv"
                path.write_text("a,b\n" + "1,2\n" * (when % 97 + n))
                os.utime(path, (when, when))
                when += 100
    for experiment in ("e1", "e2", "e3"):  # a template in three folders: same name, size, time
        path = pool / "projA" / experiment / "template.txt"
        path.write_text("template")
        os.utime(path, (1_600_000_000, 1_600_000_000))


def _project(tmp_path: Path) -> tuple[Project, Path]:
    pool = tmp_path / "pool"
    pool.mkdir()
    _tree(pool)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_FIRST, started_at="t")
    return project, pool


def _folder_id(project: Project, run: str, path: str) -> str:
    with open_catalog(project.path, run_id=run) as catalog:
        row = catalog.query(
            "SELECT entry_id FROM entries WHERE entry_type = 'directory' AND relative_path = ?",
            [path],
        ).fetchone()
    assert row is not None
    return str(row[0])


def _write(project: Project, target: str, place: str, *, kind: RecordEventKind) -> None:
    append_event(
        project.path,
        kind=kind,
        actor="tester",
        occurred_at="2026-01-01T00:00:10Z",
        target=target,
        against_run_id=_FIRST,
        payload={"path": place, "text": "the kelt runs"},
        scope=Scope(ScopeKind.FOLDER, place),
    )


def test_the_same_tree_walked_again_reads_as_the_same_organisation(tmp_path: Path) -> None:
    project, _ = _project(tmp_path)
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    report = compare_runs(Side(project.path, _FIRST), Side(project.path, _SECOND_RUN))
    assert report["verdict"] == "same_organisation"
    assert report["context"] == "same_tree"
    assert report["paths_kept"] == report["matched"] == report["files"]["l"]
    assert report["matched_by_tier"]["place"] == report["matched"]
    assert report["noticed"] is False
    assert report["notice"] == "Nothing was reorganised: the files are where they were."


def test_the_whole_tree_moved_under_a_new_folder_is_relocated_whole(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    (pool / "Archive").mkdir()
    for name in ("projA", "projB"):
        shutil.move(str(pool / name), str(pool / "Archive" / name))
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    report = compare_runs(Side(project.path, _FIRST), Side(project.path, _SECOND_RUN))
    assert report["verdict"] == "relocated_whole"
    assert report["noticed"] is True
    assert "everything now sits inside 'Archive'" in report["notice"]
    assert report["matched_by_tier"]["place"] == 0


def test_a_note_follows_its_folder_and_the_folders_own_page_says_so_once(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    identifier = _folder_id(project, _FIRST, "projA/e1")
    _write(project, f"folder:{identifier}", "projA/e1", kind=RecordEventKind.NOTE)
    shutil.move(str(pool / "projA" / "e1"), str(pool / "projA" / "2026-01 kelt"))
    run_census(project, run_id=_SECOND_RUN, started_at="t")

    answer = reorganisation(project.path)
    assert answer["available"] is True and answer["noticed"] is True
    report = answer["report"]
    assert report["decisions_followed"] == 1 and report["decisions_scattered"] == 0
    (decision,) = report["decisions"]
    assert decision["new_place"] == "projA/2026-01 kelt"
    assert (
        decision["new_target"] == f"folder:{_folder_id(project, _SECOND_RUN, 'projA/2026-01 kelt')}"
    )
    assert "1 of your notes and answers was written about a folder" in answer["notice"]
    line = folder_line(project.path, "projA/2026-01 kelt")
    assert line == ("A note was written about 'projA/e1', whose files (5 of 5) are now here.")
    assert folder_line(project.path, "projA/e2") is None


def test_dropping_the_folder_line_is_the_veto_and_the_notice_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, pool = _project(tmp_path)
    identifier = _folder_id(project, _FIRST, "projB/e2")
    _write(project, f"folder:{identifier}", "projB/e2", kind=RecordEventKind.ANNOTATE)
    shutil.move(str(pool / "projB" / "e2"), str(pool / "projB" / "renamed"))
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    report = reorganisation(project.path)["report"]
    assert report["folder_lines"] == {
        "projB/renamed": (
            "An answer was written about 'projB/e2', whose files (4 of 4) are now here."
        )
    }
    assert "your notes and answers" in report["notice"]
    carried = [
        Carried(
            d["event_id"],
            d["kind"],
            d["old_target"],
            d["old_place"],
            d["new_target"],
            d["new_place"],
            d["files"],
            d["went"],
            Follow(d["follow"]),
        )
        for d in report["decisions"]
    ]
    assert folder_lines(carried, enabled=False) == {}
    # The veto set after the module was imported, as a person would set it, is read when called.
    monkeypatch.setattr(carry, "SAY_ON_FOLDER_PAGE", False)
    assert folder_lines(carried) == {}
    assert folder_line(project.path, "projB/renamed") is None
    assert "your notes and answers" in reorganisation(project.path)["notice"]


def test_a_folder_whose_files_scattered_is_said_once_and_asks_nothing(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    identifier = _folder_id(project, _FIRST, "projA/e3")
    _write(project, f"folder:{identifier}", "projA/e3", kind=RecordEventKind.NOTE)
    for n, target in enumerate(("e1", "e2", "e1")):
        shutil.move(
            str(pool / "projA" / "e3" / f"projA_e3_kelt_{n}.csv"),
            str(pool / "projA" / target / f"from_e3_{n}.csv"),
        )
    shutil.move(str(pool / "projA" / "e3"), str(pool / "projA" / "gone_e3"))
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    report = reorganisation(project.path)["report"]
    assert report["decisions_scattered"] == 1 and report["decisions_followed"] == 0
    assert report["folder_lines"] == {}
    assert "nothing waits on that" in report["notice"]


def test_a_note_on_a_folder_that_did_not_move_is_not_listed(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    identifier = _folder_id(project, _FIRST, "projB/e1")
    _write(project, f"folder:{identifier}", "projB/e1", kind=RecordEventKind.NOTE)
    shutil.move(str(pool / "projA" / "e1"), str(pool / "projA" / "moved"))
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    assert reorganisation(project.path)["report"]["decisions"] == []


def test_a_rebind_of_a_census_is_not_an_earlier_census(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    assert census_pair(project.path) is None
    run_census(project, run_id=_SECOND_RUN, started_at="t", rebind=True)
    assert census_pair(project.path) is None
    shutil.move(str(pool / "projA"), str(pool / "projC"))
    run_census(project, run_id=_THIRD, started_at="t")
    pair = census_pair(project.path)
    assert pair is not None and pair[1] == _THIRD and pair[0] in (_FIRST, _SECOND_RUN)


def test_a_run_trashed_in_the_middle_of_a_chain_does_not_make_a_census_of_its_own(
    tmp_path: Path,
) -> None:
    """A rebind between a walk and its inference is moved to the Trash. The inference's chain
    still reaches the census before it, so the page notices that a folder was renamed since the
    walk before, and does not read "Nothing was reorganised"."""
    project, pool, first = _walked(tmp_path)
    shutil.move(str(pool / "projA" / "e1"), str(pool / "projA" / "e1 renamed"))
    second, rebound, inferred = _now_run(2), _now_run(3), _now_run(4)
    run_census(project, run_id=second, started_at="t")
    run_census(project, run_id=rebound, started_at="t", rebind=True)
    run_inference(project, run_id=inferred, started_at="t")
    assert census_pair(project.path) == (first, inferred)
    trash = tmp_path / "trash"
    trash.mkdir()
    shutil.move(str(project.path / "runs" / rebound), str(trash / rebound))
    assert census_pair(project.path) == (first, inferred)
    answer = reorganisation(project.path)
    assert answer["noticed"] is True
    assert answer["notice"].startswith("Some folders were renamed or moved")


def test_a_project_with_one_census_has_nothing_to_notice(tmp_path: Path) -> None:
    project, _ = _project(tmp_path)
    assert reorganisation(project.path) == {
        "available": False,
        "noticed": False,
        "notice": None,
        "folder_lines": {},
    }


def test_a_backup_folder_beside_its_source_reads_as_the_same_layout(tmp_path: Path) -> None:
    """Two folders of one census are the two sides. Each is read by the path beneath it."""
    project, pool = _project(tmp_path)
    shutil.copytree(pool / "projA", pool / "Backup projA", copy_function=shutil.copy2)
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    left = Side(project.path, _SECOND_RUN, under="projA")
    right = Side(project.path, _SECOND_RUN, under="Backup projA")
    report = compare_runs(left, right)
    assert report["verdict"] == "same_organisation"
    assert report["context"] != "same_tree"
    assert report["files"] == {"l": 15, "r": 15}
    assert report["matched"] == 15


def test_a_folder_pooled_with_others_is_not_followed_but_said(tmp_path: Path) -> None:
    """Every experiment's files gathered into one folder: the note's files did go together, to a
    place that now mixes them with many others, so it is not read there."""
    project, pool = _project(tmp_path)
    identifier = _folder_id(project, _FIRST, "projB/e1")
    _write(project, f"folder:{identifier}", "projB/e1", kind=RecordEventKind.NOTE)
    (pool / "everything").mkdir()
    for path in sorted(pool.glob("proj*/e*/*.csv")):
        os.rename(path, pool / "everything" / path.name)
    for path in sorted(pool.glob("proj*/e*/template.txt")):
        os.remove(path)
    for folder in sorted(pool.glob("proj*/e*")):
        folder.rmdir()
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    report = reorganisation(project.path)["report"]
    assert report["decisions_mixed"] == 1 and report["decisions_followed"] == 0
    (decision,) = report["decisions"]
    assert decision["follow"] == "mixed" and decision["among"] == 20
    assert report["folder_lines"] == {}
    assert "now sit among other files" in report["notice"]


def test_a_moved_tree_is_the_same_drive(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    (pool / "Archive").mkdir()
    shutil.move(str(pool / "projA"), str(pool / "Archive" / "projA"))
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    report = compare_runs(Side(project.path, _FIRST), Side(project.path, _SECOND_RUN))
    assert report["context"] == "same_tree"
    assert "another computer" not in report["notice"]


def _now_run(tail: int) -> str:
    """A run stamped when it runs (a file born today is never "born after" the run)."""
    return f"run_{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}Z_{tail:012x}"


def _wide(pool: Path, folders: int, files: int) -> None:
    """Many folders of a few files, each file of its own size and time."""
    n = 0
    for g in range(folders):
        for i in range(files):
            path = pool / f"g{g:02d}" / f"zib_{g:02d}_{i}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("k" * (n + 1))
            os.utime(path, (1_700_000_000 + 100 * n, 1_700_000_000 + 100 * n))
            n += 1


def test_one_folder_renamed_among_hundreds_of_files_is_noticed_with_its_note(
    tmp_path: Path,
) -> None:
    """With a high share of paths kept (98 percent), a note's folder that moved is still noticed.
    The notice does not say "Nothing was reorganised", and the History page shows the move."""
    pool = tmp_path / "pool"
    _wide(pool, 50, 10)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    first = _now_run(1)
    run_census(project, run_id=first, started_at="t")
    _write_at(project, first, "g07")
    shutil.move(str(pool / "g07"), str(pool / "g07 done"))
    run_census(project, run_id=_now_run(2), started_at="t")
    answer = reorganisation(project.path)
    assert answer["report"]["verdict"] == "folders_renamed"
    assert answer["noticed"] is True
    assert not answer["notice"].startswith("Nothing was reorganised")
    assert answer["report"]["decisions_followed"] == 1


def _write_at(project: Project, run: str, place: str) -> None:
    append_event(
        project.path,
        kind=RecordEventKind.NOTE,
        actor="tester",
        occurred_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        target=f"folder:{_folder_id(project, run, place)}",
        against_run_id=run,
        payload={"path": place, "text": "the zib runs"},
        scope=Scope(ScopeKind.FOLDER, place),
    )


def _walked(tmp_path: Path) -> tuple[Project, Path, str]:
    """The invented tree, walked once by a run stamped when it runs."""
    pool = tmp_path / "pool"
    pool.mkdir()
    _tree(pool)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    first = _now_run(1)
    run_census(project, run_id=first, started_at="t")
    return project, pool, first


def test_files_renamed_inside_their_folders_are_said_so_and_not_noticed(tmp_path: Path) -> None:
    project, pool, first = _walked(tmp_path)
    for path in sorted(pool.glob("proj*/e*/proj*.csv")):
        os.rename(path, path.with_name("renamed " + path.name))
    second = _now_run(2)
    run_census(project, run_id=second, started_at="t")
    report = compare_runs(Side(project.path, first), Side(project.path, second))
    assert report["verdict"] == "files_renamed"
    assert report["noticed"] is False
    assert report["notice"] == "Some files were renamed inside their folders; no folder moved."


def test_one_folder_read_with_its_root_recorded_two_ways_is_the_same_organisation(
    tmp_path: Path,
) -> None:
    """Case is merged on each side on its own. The same census with one root recorded as telling
    case apart does not read "The whole tree moved as one piece ('(top)' is now '(top)')"."""
    pool = tmp_path / "pool"
    _tree(pool)
    one = Project.create(tmp_path / "one", created_at="t", roots=[pool])
    two = Project.create(tmp_path / "two", created_at="t", roots=[pool])
    told_apart = tuple(
        root.model_copy(update={"case_sensitivity": CaseSensitivity.SENSITIVE})
        for root in two.identity.roots
    )
    _write_identity(two.path, two.identity.model_copy(update={"roots": told_apart}))
    first, second = _now_run(1), _now_run(2)
    run_census(one, run_id=first, started_at="t")
    run_census(Project.open(two.path), run_id=second, started_at="t")
    report = compare_runs(Side(one.path, first), Side(two.path, second))
    assert report["verdict"] == "same_organisation"
    assert report["paths_kept"] == report["matched"] == report["files"]["l"]
    assert report["notice"].startswith("Nothing was reorganised")


def test_two_roots_that_mirror_each_other_walked_twice_are_the_same_files(tmp_path: Path) -> None:
    """A path alone names two files on each side, yet the files match as the same files and are
    not read as "different files"."""
    first_root, second_root = tmp_path / "kelt", tmp_path / "vorn"
    _tree(first_root)
    shutil.copytree(first_root, second_root, copy_function=shutil.copy2)
    project = Project.create(tmp_path / "project", created_at="t", roots=[first_root, second_root])
    first, second = _now_run(1), _now_run(2)
    run_census(project, run_id=first, started_at="t")
    run_census(project, run_id=second, started_at="t")
    report = compare_runs(Side(project.path, first), Side(project.path, second))
    assert report["verdict"] == "same_organisation"
    assert report["matched"] == report["files"]["l"] == 2 * 27
    assert report["matched_by_tier"]["place"] == report["matched"]
    assert report["paths_compared"]["root_in_path"] is True


def test_a_name_stored_in_another_composed_form_is_the_same_path(tmp_path: Path) -> None:
    """An accented letter stored as one character, or as a letter and its accent, is one name."""
    project, pool, _ = _walked(tmp_path)
    apart = unicodedata.normalize("NFD", "zib café.csv")
    (pool / "projB" / "e1" / apart).write_text("x" * 7)
    before = _now_run(2)
    run_census(project, run_id=before, started_at="t")
    folder = pool / "projB" / "e1"
    os.rename(folder / apart, folder / unicodedata.normalize("NFC", apart))
    after = _now_run(3)
    run_census(project, run_id=after, started_at="t")
    report = compare_runs(Side(project.path, before), Side(project.path, after))
    assert report["verdict"] == "same_organisation"
    assert report["matched_by_tier"]["place"] == report["matched"] == report["files"]["l"]


def test_the_history_notice_says_new_changed_and_gone_and_never_counts_copies_as_missing(
    tmp_path: Path,
) -> None:
    """Two folders renamed, each holding a copy of one template (there, but not told apart), two
    files added, one edited where it sits and one removed."""
    project, pool, first = _walked(tmp_path)
    for name in ("e1", "e2"):
        shutil.move(str(pool / "projA" / name), str(pool / "projA" / f"{name} moved"))
    for n in range(2):
        (pool / "projB" / "e1" / f"new_vorn_{n}.csv").write_text("n" * (n + 3))
    (pool / "projB" / "e3" / "projB_e3_kelt_0.csv").write_text("edited, and longer than it was")
    (pool / "projB" / "e2" / "projB_e2_kelt_1.csv").unlink()
    second = _now_run(2)
    run_census(project, run_id=second, started_at="t")
    answer = reorganisation(project.path, before=first, after=second)
    report = answer["report"]
    assert report["not_told_apart"] == {"l": 2, "r": 2}
    assert report["absent"] == {"l": 1, "r": 2}
    assert report["changed"] == {"l": 1, "r": 1}
    assert answer["noticed"] is True
    assert answer["notice"] == (
        "Some folders were renamed or moved; each kept all of its files. Since the earlier "
        "reading, 2 files are new, 1 was changed and 1 is gone."
    )


def test_one_more_answer_reads_only_the_decisions_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One more answer reads only the decisions again. The comparison is not keyed by the Record's
    length, so an answer does not make it run again, which takes seconds on a large census while
    the History list waits."""
    project, pool, first = _walked(tmp_path)
    shutil.move(str(pool / "projA" / "e2"), str(pool / "projA" / "e2 done"))
    run_census(project, run_id=_now_run(2), started_at="t")
    measured: list[str] = []
    real = routes.measure

    def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        measured.append("once")
        return real(*args, **kwargs)

    monkeypatch.setattr(routes, "measure", counted)
    assert reorganisation(project.path)["report"]["decisions_followed"] == 0
    _write_at(project, first, "projA/e2")
    answer = reorganisation(project.path)
    assert answer["report"]["decisions_followed"] == 1
    assert "1 of your notes and answers was written about a folder" in answer["notice"]
    assert folder_line(project.path, "projA/e2 done") is not None
    assert measured == ["once"]


def test_the_history_page_shows_the_runs_before_the_notice_arrives() -> None:
    """The History page shows the list before the whole comparison finishes."""
    script = (WEB_ROOT / "review.js").read_text(encoding="utf-8")
    body = script[script.index("async function showChanges()") :]
    body = body[: body.index("\n}\n")]
    assert "await api('/api/reorganisation')" not in body
    assert body.index("api('/api/reorganisation')") < body.index("fill(listPane,\n")


def test_a_decision_about_a_file_that_moved_is_counted_not_listed(tmp_path: Path) -> None:
    project, pool = _project(tmp_path)
    run_inference(project, run_id=_SECOND_RUN, started_at="t")
    with open_catalog(project.path, run_id=_SECOND_RUN) as catalog:
        rows = catalog.query(
            "SELECT c.claim_id FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE e.entry_type = 'file' AND starts_with(e.relative_path, 'projA/e1/')"
        ).fetchall()
    assert rows, "the invented tree should draw at least one claim about a file"
    append_event(
        project.path,
        kind=RecordEventKind.CONFIRM,
        actor="tester",
        occurred_at="2026-01-01T00:00:10Z",
        target=f"claim:{rows[0][0]}",
        against_run_id=_SECOND_RUN,
    )
    shutil.move(str(pool / "projA" / "e1"), str(pool / "projA" / "2026-01 kelt"))
    run_census(project, run_id=_THIRD, started_at="t")
    report = reorganisation(project.path)["report"]
    assert report["decisions_about_moved_files"] == 1
    assert report["decisions"] == []
