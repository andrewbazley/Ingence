"""A table says how many tables with its columns lie in its own folder.

"Same columns as {n} files in this folder and {k} others" is said from the engine's own whole
counts, never from a list cut at its limit. The counts in the table's own folder and elsewhere are
served beside the total, whatever the page's limit. The files in its own folder are named, so the
count and what opens are one answer. A page may ask for more files to be named, up to a ceiling,
and the counts do not move. Names and values are invented.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest

from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen

HEADER = "well,cell_line,treatment,cell_count"
#: Where each table lies: three with one header in one folder, two in another, two in a third
#: (one of them in a folder below it), and one with a header of its own.
TABLES = {
    "plate_a/HeLa_DMSO.csv": HEADER,
    "plate_a/HeLa_taxol.csv": HEADER,
    "plate_a/HeLa_nocodazole.csv": HEADER,
    "plate_b/HEK293_DMSO.csv": HEADER,
    "plate_b/HEK293_taxol.csv": HEADER,
    "plate_c/HeLa_etoposide.csv": HEADER,
    "plate_c/replicates/HeLa_etoposide_two.csv": HEADER,
    "plate_a/viability.csv": "well,viability",
}
#: How many tables share the one header.
SHARING = 7


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("columns_in_folder")
    pool = base / "pool"
    for relative, header in TABLES.items():
        target = pool / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{header}\nA1,HeLa,DMSO,12\n", encoding="utf-8")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    made = made.save_settings(
        made.settings.model_copy(update={"extraction": ExtractionSettings(enabled=True)})
    )
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(made, run_id="run_20260101T000002Z_222222222222", started_at="t", profile="standard")
    run_inference(made, run_id="run_20260101T000003Z_333333333333", started_at="t")
    return made


def _ask(project: Project, identifier: str, **query: Any) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    status, body = dispatch(
        context,
        "columns",
        {"id": [identifier], **{name: [str(value)] for name, value in query.items()}},
    )
    assert status == HTTPStatus.OK, body
    return body


def _identifier(project: Project, relative: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = ?", [relative]
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_the_tables_in_the_tables_own_folder_and_elsewhere_are_counted_whole(
    project: Project,
) -> None:
    body = _ask(project, _identifier(project, "plate_a/HeLa_DMSO.csv"))
    assert body["read"] is True
    assert body["same_columns_total"] == SHARING - 1
    assert body["same_columns_in_folder"] == 2
    assert body["same_columns_elsewhere"] == SHARING - 1 - 2
    other = _ask(project, _identifier(project, "plate_b/HEK293_DMSO.csv"))
    assert other["same_columns_in_folder"] == 1
    assert other["same_columns_elsewhere"] == SHARING - 1 - 1


def test_a_table_in_a_folder_below_another_counts_only_its_own_folder(project: Project) -> None:
    """Tables of a folder above or below are elsewhere: "in this folder" is the folder itself."""
    above = _ask(project, _identifier(project, "plate_c/HeLa_etoposide.csv"))
    below = _ask(project, _identifier(project, "plate_c/replicates/HeLa_etoposide_two.csv"))
    for body in (above, below):
        assert body["same_columns_total"] == SHARING - 1
        assert body["same_columns_in_folder"] == 0
        assert body["same_columns_elsewhere"] == SHARING - 1
        assert body["same_columns_here"] == []


def test_the_files_in_the_tables_own_folder_are_named_and_agree_with_the_count(
    project: Project,
) -> None:
    body = _ask(project, _identifier(project, "plate_a/HeLa_DMSO.csv"))
    named = {item["path"] for item in body["same_columns_here"]}
    assert named == {"plate_a/HeLa_taxol.csv", "plate_a/HeLa_nocodazole.csv"}
    assert len(named) == body["same_columns_in_folder"]


def test_a_table_with_no_twin_has_none_anywhere(project: Project) -> None:
    body = _ask(project, _identifier(project, "plate_a/viability.csv"))
    assert body["read"] is True
    assert body["same_columns_total"] == 0
    assert body["same_columns_in_folder"] == 0
    assert body["same_columns_elsewhere"] == 0
    assert body["same_columns_here"] == []


def test_a_page_may_ask_for_more_names_and_the_counts_do_not_move(project: Project) -> None:
    identifier = _identifier(project, "plate_a/HeLa_DMSO.csv")
    short = _ask(project, identifier, limit=1)
    whole = _ask(project, identifier, limit=500)
    assert len(short["same_columns"]) == 1 and len(short["same_columns_here"]) == 1
    assert len(whole["same_columns"]) == SHARING - 1 and len(whole["same_columns_here"]) == 2
    for body in (short, whole):
        assert body["same_columns_total"] == SHARING - 1
        assert body["same_columns_in_folder"] == 2


def test_the_limit_is_refused_in_words_beyond_its_ceiling(project: Project) -> None:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    identifier = _identifier(project, "plate_a/HeLa_DMSO.csv")
    status, body = dispatch(context, "columns", {"id": [identifier], "limit": ["501"]})
    assert status == HTTPStatus.BAD_REQUEST
    assert "limit" in str(body["error"]["message"])
