"""A person's "this folder is not a program" takes it out of the program scope.

The answer is read from the Record the way an approved group is. Inference does not raise the
folder as installed software, the dive does not defer beneath it, and the Programs page lets it go
with the answered rows. A later "it is a program after all" puts it back. Every name is invented;
the program's layout is a packaging convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import write_revision
from ingence.api.routes import decide as decide_routes
from ingence.api.routes import pages
from ingence.core.project import Project
from ingence.infer.deferral import deferred_regions
from ingence.infer.run import run_inference
from ingence.record.log import now, read_events
from ingence.record.targets import PROGRAM_PREFIX, program_target, target_kind
from ingence.record.verdicts import folders_not_programs
from ingence.rulebook import project_rulebook
from ingence.scan.census import run_census
from ingence.scan.deepen import _entry_from_row
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

FIRST = "run_20260101T000001Z_111111111111"
SECOND = "run_20260101T000002Z_222222222222"
THIRD = "run_20260101T000003Z_333333333333"
FOURTH = "run_20260101T000004Z_444444444444"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _project(base: Path) -> Project:
    pool = base / "pool"
    app = pool / "tools" / "Fiji.app"
    _write(app / "ImageJ-linux64", "launcher")
    for number in range(4):
        _write(app / "jars" / f"vorn-library-{number}.jar")
    for number in range(3):
        _write(app / "plugins" / f"brisa_plugin_{number}.jar")
    _write(app / "luts" / "fire.lut")
    _write(pool / "data" / "kelt_raw_1.csv", "a,b\n1,2\n")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id=FIRST, started_at="t")
    run_inference(created, run_id=SECOND, started_at="t")
    return created


def _program_folders(project: Project) -> list[str]:
    with open_catalog(project.path) as catalog:
        return [row["path"] for row in pages._program_folders(catalog)]


def _say(project: Project, *, is_program: bool) -> None:
    with open_catalog(project.path) as catalog:
        folder = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE relative_path = 'tools/Fiji.app'"
            ).fetchone()[0]
        )
        decide_routes.say_folder_is_program(
            catalog,
            project.path,
            actor="a person",
            occurred_at=now(),
            entry_id=folder,
            is_program=is_program,
        )


def test_the_program_target_is_its_own_kind() -> None:
    assert program_target("entry_x") == f"{PROGRAM_PREFIX}:entry_x"
    assert target_kind(program_target("entry_x")) == PROGRAM_PREFIX


def test_a_folder_said_not_to_be_a_program_leaves_the_program_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert _program_folders(project) == ["tools/Fiji.app"]

    _say(project, is_program=False)
    events = read_events(project.path)
    assert len(folders_not_programs(events)) == 1

    run_inference(project, run_id=THIRD, started_at="t")
    assert _program_folders(project) == []
    with open_catalog(project.path) as catalog:
        page = pages.programs(catalog, project.path)
        shown = pages.programs(catalog, project.path, answered=True)
    assert page["programs"] == []
    assert page["hidden_answered"] == 0
    assert shown["programs"] == []


def test_the_dive_stops_deferring_beneath_a_folder_said_not_to_be_a_program(tmp_path: Path) -> None:
    project = _project(tmp_path)
    book = project_rulebook(project.path)
    with open_catalog(project.path) as catalog:
        entries = [
            _entry_from_row(row)
            for row in catalog.query("SELECT * FROM entries").to_arrow_table().to_pylist()
        ]
    before = deferred_regions(entries, book)
    assert before.raised, "the layout must be raised as a program"
    said = frozenset(before.raised)
    after = deferred_regions(entries, book, not_programs=said)
    assert not after.raised
    assert not after.beneath


def test_saying_it_is_a_program_after_all_puts_it_back(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _say(project, is_program=False)
    _say(project, is_program=True)
    assert folders_not_programs(read_events(project.path)) == frozenset()
    run_inference(project, run_id=THIRD, started_at="t")
    assert _program_folders(project) == ["tools/Fiji.app"]


def test_a_note_on_the_folder_does_not_displace_the_answer(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _say(project, is_program=False)
    with open_catalog(project.path) as catalog:
        folder = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE relative_path = 'tools/Fiji.app'"
            ).fetchone()[0]
        )
        decide_routes.annotate_folder(
            catalog,
            project.path,
            actor="a person",
            occurred_at=now(),
            entry_id=folder,
            described="a folder of things the laboratory made",
        )
    assert len(folders_not_programs(read_events(project.path))) == 1


def test_a_file_is_not_a_folder_to_say_this_of(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        entry = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE relative_path = 'data/kelt_raw_1.csv'"
            ).fetchone()[0]
        )
        with pytest.raises(decide_routes.AnswerRefusedError):
            decide_routes.say_folder_is_program(
                catalog,
                project.path,
                actor="a person",
                occurred_at=now(),
                entry_id=entry,
                is_program=False,
            )


def test_a_folder_said_not_to_be_a_program_is_answered_before_the_next_inference(
    tmp_path: Path,
) -> None:
    """Between the answer and the next inference the folder is still raised: its row leaves the
    page and counts as answered, so the tab never counts a row nobody can see."""
    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        before = pages.programs(catalog, project.path)
    assert before["progress"]["remaining"] == 1
    _say(project, is_program=False)
    with open_catalog(project.path) as catalog:
        after = pages.programs(catalog, project.path)
        shown = pages.programs(catalog, project.path, answered=True)
        waiting = pages.waiting(catalog, project.path)
    assert after["programs"] == []
    assert after["progress"]["remaining"] == 0
    assert after["progress"]["answered"] == 1
    assert waiting["programs"] == 0
    assert [row["not_a_program"] for row in shown["programs"]] == [True]
