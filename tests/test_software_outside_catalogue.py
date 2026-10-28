"""Software outside the catalogue, recognised by its structure and asked about.

A program that no layout in the Dictionary declares can be recognised by its folder system and
raised for a question. A folder laid out like a program's (a bin, lib or plugins folder, compiled
files, a licence or a version file: two kinds of that evidence) that no layout the Dictionary
declares matches raises "Is this folder a program?". The question is answered through the per-folder
program answer. "Not a program" stops the question and takes the folder out of the program scope,
and "a program" puts it in. Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.record.targets import program_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

QUESTION = "is this folder a program?"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _asked(project: Project, rule_id: str) -> dict[str, list[str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e "
            "ON f.subject LIKE '%' || e.entry_id || '%' "
            "WHERE f.rule_id = ? AND e.entry_type = 'directory'",
            [rule_id],
        ).fetchall()
    return {path: list(values) for path, values in rows}


def _entry_id(project: Project, path: str) -> str:
    with open_catalog(project.path) as catalog:
        (found,) = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = ?", [path]
        ).fetchone()
    return str(found)


def test_a_folder_laid_out_like_a_program_is_asked_and_answered_per_folder(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    _write(pool / "zibtool" / "bin" / "zibtool.exe")
    _write(pool / "zibtool" / "lib" / "zib.dll")
    _write(pool / "zibtool" / "LICENSE.txt")
    _write(pool / "trellkit" / "plugins" / "trell_reader.jar")
    _write(pool / "trellkit" / "VERSION")
    _write(pool / "brisa scripts" / "brisa_fit.py")
    _write(pool / "brisa scripts" / "brisa_plot.py")
    _write(pool / "brisa scripts" / "LICENSE")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = "run_20260101T000002Z_000000000002"
    run_inference(created, run_id=first, started_at="t")

    asked = _asked(created, "software_outside_the_catalogue")
    assert asked == {  # one question per folder; scripts are not a program
        "zibtool": [f"zibtool: {QUESTION}"],
        "trellkit": [f"trellkit: {QUESTION}"],
    }
    assert "trellkit" not in _asked(created, "installed_software_in_folder")

    for path, kind in (("zibtool", RecordEventKind.REJECT), ("trellkit", RecordEventKind.CONFIRM)):
        append_event(
            created.path,
            kind=kind,
            actor="vorn (an invented person)",
            occurred_at="2026-01-01T00:00:03Z",
            target=program_target(_entry_id(created, path)),
            against_run_id=first,
        )
    run_inference(created, run_id="run_20260101T000004Z_000000000004", started_at="t")
    assert _asked(created, "software_outside_the_catalogue") == {}  # answered: never again
    programs = _asked(created, "installed_software_in_folder")
    assert "zibtool" not in programs  # "this is not a program"
    assert programs.get("trellkit") == ["a program, by a person's answer"]


def test_a_folder_a_person_said_is_a_program_is_deferred_by_the_dive_too(tmp_path: Path) -> None:
    """The dive and inference read one answer: a confirmed program's files are read last."""
    from ingence.infer.deferral import deferred_regions, folders_said_programs
    from ingence.record.log import read_events
    from ingence.rulebook import project_rulebook
    from ingence.rulebook.predicates import load_builtins

    pool = tmp_path / "pool"
    _write(pool / "trellkit" / "plugins" / "trell_reader.jar")
    _write(pool / "trellkit" / "VERSION")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    folder = _entry_id(created, "trellkit")
    append_event(
        created.path,
        kind=RecordEventKind.CONFIRM,
        actor="vorn (an invented person)",
        occurred_at="2026-01-01T00:00:03Z",
        target=program_target(folder),
        against_run_id="run_20260101T000001Z_000000000001",
    )
    assert folders_said_programs(read_events(created.path)) == frozenset({folder})
    load_builtins()
    with open_catalog(created.path) as catalog:
        from ingence.core.schema import Entry

        entries = [
            Entry.model_validate({k: v for k, v in row.items() if k in Entry.model_fields})
            for row in catalog.query("SELECT * FROM entries").to_arrow_table().to_pylist()
        ]
    book = project_rulebook(created.path)
    plain = deferred_regions(entries, book)
    said = deferred_regions(entries, book, programs=frozenset({folder}))
    assert folder not in plain.raised
    assert folder in said.raised
