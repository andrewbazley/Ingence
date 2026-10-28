"""A document's internal parts are never listed as a folder's files.

The class: a container file (a workbook, a word-processing document, a presentation, any ZIP
whose members are its own parts) is one file of its folder; the parts a dive recovers from inside
it are embedded in it, evidence about it, reachable under it, and never counted or listed among
the folder's files, by any listing a person reads.

One folder holding a workbook with two charts, a document and a presentation, built by hand from
their parts with invented values, then a real ``init``, ``index``, ``dive`` and ``infer``. Every
listing and count of the folder's files is read, and each must say three files. Every name here
is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from fixtures.office_packages import document, presentation, workbook
from ingence.api.routes import search as search_routes
from ingence.api.routes import views
from ingence.api.v1 import context_for, dispatch
from ingence.cli.main import app
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

DOCUMENTS = ("brisa_plan.xlsx", "vorn_notes.docx", "zeph_talk.pptx")


@pytest.fixture(scope="module")
def dived(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("document_parts")
    folder = base / "pool" / "kelt"
    folder.mkdir(parents=True)
    workbook(folder / DOCUMENTS[0], created="2024-04-29T09:00:00Z", modified="2024-05-01T10:00:00Z")
    document(folder / DOCUMENTS[1], created="2024-04-29T09:00:00Z", modified="2024-05-01T10:00:00Z")
    presentation(
        folder / DOCUMENTS[2], created="2024-04-29T09:00:00Z", modified="2024-05-01T10:00:00Z"
    )
    project = base / "project"
    runner = CliRunner()
    for command in (
        ["init", str(project), "--root", str(base / "pool")],
        ["index", "-p", str(project), "--quiet"],
        ["dive", "-p", str(project), "--budget", "60"],
        ["infer", "-p", str(project)],
    ):
        done = runner.invoke(app, command)
        assert done.exit_code == 0, (command, done.output)
    return project


def _one(project: Path, sql: str, parameters: list[Any] | None = None) -> Any:
    with open_catalog(project) as catalog:
        return catalog.query(sql, parameters or []).fetchone()[0]


def _folder_id(project: Path) -> str:
    return str(_one(project, "SELECT entry_id FROM entries WHERE relative_path = 'kelt'"))


def _v1(project: Path, path: str, **query: str) -> dict[str, Any]:
    status, answered = dispatch(
        context_for(project, settled=None, job_running=False),
        path,
        {key: [value] for key, value in query.items()},
    )
    assert int(status) == 200, answered
    return answered


def test_the_dive_recovered_the_parts_as_evidence_about_each_document(dived: Path) -> None:
    """The precondition: the parts are there, under each document, kept as what they are."""
    parts = _one(
        dived,
        "SELECT count(*) FROM entries d JOIN entries c ON c.entry_id = d.parent_entry_id "
        "WHERE d.entry_kind = 'derived' AND c.name IN ('brisa_plan.xlsx', 'vorn_notes.docx', "
        "'zeph_talk.pptx')",
    )
    assert parts > len(DOCUMENTS)


def test_the_folder_lists_one_file_per_document(dived: Path) -> None:
    with open_catalog(dived) as catalog:
        listed = views.folder(catalog, _folder_id(dived))
    assert sorted(entry["name"] for entry in listed["entries"]) == list(DOCUMENTS)
    answered = _v1(dived, "folder", id=_folder_id(dived))
    names = sorted(str(entry["name"]) for entry in answered.get("entries", []))
    assert names == list(DOCUMENTS), answered


def test_the_inventory_counts_three_files_and_no_part(dived: Path) -> None:
    with open_catalog(dived) as catalog:
        here = views.inventory(catalog)["here"]
        counts = views.project(catalog)["counts"]
    kinds = {row["value"]: row["n"] for row in here["by_kind"]}
    assert kinds["file"] == len(DOCUMENTS), kinds
    extensions = {row["value"] for row in here["by_extension"]}
    assert extensions == {"xlsx", "docx", "pptx"}, extensions
    sizes = _one(dived, "SELECT sum(size_bytes) FROM entries WHERE entry_kind = 'filesystem'")
    assert here["bytes"]["total"] == sizes
    assert sum(row["n"] for row in here["by_depth"]) == len(DOCUMENTS) + 2  # the root and kelt
    assert counts["entries"] == len(DOCUMENTS) + 2


def test_a_path_search_finds_the_document_and_none_of_its_parts(dived: Path) -> None:
    found = _v1(dived, "graph/find", q="kelt/brisa_plan.xlsx")
    assert [match["name"] for match in found["matches"]] == ["brisa_plan.xlsx"]
    part = _one(dived, "SELECT entry_id FROM entries WHERE entry_kind = 'derived' LIMIT 1")
    by_identifier = _v1(dived, "graph/find", q=str(part))
    assert [match["id"] for match in by_identifier["matches"]] == [part]


def test_a_folder_does_not_hold_a_part_s_kind_as_a_file(dived: Path) -> None:
    """``ext:xml`` asks for folders holding an XML file; kelt holds none, only documents."""
    with open_catalog(dived) as catalog:
        parts_kind = search_routes.search(catalog, dived, text="kelt ext:xml")
        own_kind = search_routes.search(catalog, dived, text="kelt ext:xlsx")
    assert [row["path"] for row in own_kind["results"]] == ["kelt"], own_kind
    assert [row["path"] for row in parts_kind["results"]] == [], parts_kind


def test_the_parts_are_reachable_under_their_document(dived: Path) -> None:
    container = _one(dived, "SELECT entry_id FROM entries WHERE name = 'brisa_plan.xlsx'")
    with open_catalog(dived) as catalog:
        beneath = views.folder(catalog, str(container))
    names = {entry["name"] for entry in beneath["entries"]}
    assert {"xl/charts/chart1.xml", "xl/charts/chart2.xml", "docProps/core.xml"} <= names


def test_the_explorer_agrees_three_files_in_the_folder(dived: Path) -> None:
    answered = _v1(dived, "explorer/files", folder=_folder_id(dived))
    listed = answered.get("files", answered.get("items", []))
    names = sorted(str(item.get("name")) for item in listed)
    assert names == list(DOCUMENTS), answered


def test_a_part_s_kind_holds_no_file_a_person_can_answer_about(dived: Path) -> None:
    """The coverage view's answer counts the files of an extension, as the page showed them."""
    from ingence.api.routes.decide import AnswerRefusedError, answer_extension
    from ingence.record.verdicts import WORTH_READING

    worth = sorted(WORTH_READING)[0]
    with open_catalog(dived) as catalog, pytest.raises(AnswerRefusedError, match="no file"):
        answer_extension(
            catalog,
            dived,
            actor="kelt",
            occurred_at="2026-01-01T00:00:00Z",
            extension="xml",
            worth_reading=worth,
        )


def test_a_word_inside_a_document_names_the_document_not_its_part(dived: Path) -> None:
    """A search finds kelt by a word the document's text holds; the file it names is the
    document, since the part the text was read from is the document's own content."""
    with open_catalog(dived) as catalog:
        found = search_routes.search(catalog, dived, text="wells")
    (kelt,) = [row for row in found["results"] if row["path"] == "kelt"]
    lines = [line for line in kelt["why"] if line["where"] == "content"]
    assert lines, kelt
    assert {name for line in lines for name in line["examples"]} == {"vorn_notes.docx"}
    assert all(line["entries"] == 1 for line in lines)


def _said_counts(project: Path) -> tuple[int, int, int]:
    with open_catalog(project) as catalog:
        total, own = catalog.query(
            "SELECT count(*), count(*) FILTER (WHERE entry_kind = 'filesystem') FROM entries"
        ).fetchone()
    return int(total), int(own), int(total) - int(own)


def test_fix_states_the_parts_apart_from_the_files_and_folders(dived: Path) -> None:
    """``fix`` states the parts inside documents apart from the files and folders."""
    total, own, parts = _said_counts(dived)
    done = CliRunner().invoke(app, ["fix", "-p", str(dived)])
    said = " ".join(done.output.split())
    assert done.exit_code == 0, done.output
    assert f"{total} items: {own} files and folders, {parts} parts inside documents" in said
    assert f"items: {total}" not in said


def test_infer_states_the_parts_apart_from_the_files_and_folders(dived: Path) -> None:
    total, own, parts = _said_counts(dived)
    done = CliRunner().invoke(app, ["infer", "-p", str(dived)])
    said = " ".join(done.output.split())
    assert done.exit_code == 0, done.output
    assert (
        f"Applied the rules to {total} items ({own} files and folders, {parts} parts inside "
        "documents)" in said
    ), said
