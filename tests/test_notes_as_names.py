"""A note written as a file name: an empty file whose name is a sentence.

An empty text file, or an empty file with no extension, whose name is several words written with
spaces and no data-like piece (no digit), is claimed a note and its name is its content. It is
never raised as an empty output, empty, or no data point. Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType
from ingence.infer.run import run_inference
from ingence.rulebook.predicates.notes import reads_as_a_note
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

NOTE = "the kelt culture was over-saturated, data can not be compared.txt"
BARE_NOTE = "brisa said to ask vorn first"


def _entry(name: str, size: int = 0) -> Entry:
    extension = name.rsplit(".", 1)[1] if "." in name else ""
    return Entry(
        entry_id="entry_" + "1" * 32,
        root_id="root_" + "2" * 32,
        relative_path=name,
        name=name,
        extension=extension,
        entry_type=EntryType.FILE,
        size_bytes=size,
        depth=1,
        first_seen_run="run_20260101T000001Z_111111111111",
        last_seen_run="run_20260101T000001Z_111111111111",
    )


def test_an_empty_file_whose_name_is_a_sentence_reads_as_a_note() -> None:
    assert reads_as_a_note(_entry(NOTE))
    assert reads_as_a_note(_entry(BARE_NOTE))


def test_a_name_with_a_number_bytes_or_a_data_extension_is_no_note() -> None:
    assert not reads_as_a_note(_entry("ask about kelt sample 12.txt"))  # a data-like piece
    assert not reads_as_a_note(_entry(NOTE, size=40))  # it holds bytes: its content is there
    assert not reads_as_a_note(_entry("the kelt culture was over-saturated.csv"))  # a table
    assert not reads_as_a_note(_entry("kelt_vorn_zib_brisa.txt"))  # a template, not a sentence
    assert not reads_as_a_note(_entry("kelt vorn.txt"))  # two words are a name


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_a_note_is_claimed_as_one_and_never_raised_as_an_empty_output(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    folder = pool / "kelt run"
    _write(folder / "kelt_vorn_01.txt", "tracks")
    _write(folder / "kelt_vorn_02.txt")  # an empty output beside a replicate holding bytes
    _write(folder / NOTE)
    _write(folder / BARE_NOTE)
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")
    with open_catalog(created.path) as catalog:
        notes = catalog.query(
            "SELECT e.name, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'note_written_as_a_file_name' "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
        raised = catalog.query(
            "SELECT e.name, f.raised FROM findings f JOIN entries e ON e.entry_id = f.subject "
            "WHERE f.rule_id = 'content_unreadable'"
        ).fetchall()
    assert dict(notes) == {NOTE: "note", BARE_NOTE: "note"}
    assert {name for name, _ in raised} == {"kelt_vorn_02.txt"}
