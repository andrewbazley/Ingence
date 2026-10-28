"""Where the dive's settings outcome and the group lattice meet the Graph workspace.

The tests run on the explorer fixture.

- A file a person's extraction settings left unread (``skipped_by_settings``) is its own
  category, "left out by the settings", apart from what a policy or the budget set aside. The
  Examined measure does not count it as "not stated".
- The lattice's questions are proposed groupings too (group approvals as a lattice, the default).
  The Wheel counts both the clustering rule's findings and the lattice's questions. Counting only
  the clustering rule's findings would leave out the sets the lattice asks.

A later run is published on a copy holding one file left out by the settings and one lattice
question about an experiment's folder. Invented names only, as the fixture's.
"""

from __future__ import annotations

import shutil
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _ask(project: Path, path: str, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def test_a_file_left_out_by_the_settings_and_a_lattice_question_are_counted(
    explorer: ExplorerProject, tmp_path: Path
) -> None:
    from fixtures.explorer_project import LATER_RUN
    from fixtures.graph_project import publish_with
    from ingence.core.outcomes import ScanOutcome
    from ingence.core.schema import Entry, Finding
    from ingence.storage.catalog import open_catalog
    from ingence.storage.index_store import record_from_row

    copy = tmp_path / "project"
    shutil.copytree(explorer.project, copy)
    shutil.rmtree(copy / "projections", ignore_errors=True)
    experiment = explorer.experiments[0]
    folder_id = explorer.ids[experiment]
    with open_catalog(copy) as catalog:
        row = (
            catalog.query(
                "SELECT * FROM entries WHERE entry_type = 'file' AND starts_with(relative_path, ?) "
                "ORDER BY relative_path LIMIT 1",
                [f"{experiment}/"],
            )
            .to_arrow_table()
            .to_pylist()
        )
        finding = catalog.query("SELECT * FROM findings LIMIT 1").to_arrow_table().to_pylist()
    assert row and finding
    entry = record_from_row(row[0], Entry)
    left_out = entry.model_copy(
        update={
            "entry_id": "entry_explorer_settings_left_out",
            "relative_path": entry.relative_path.rsplit("/", 1)[0] + "/zib_settings_left_out.raw",
            "name": "zib_settings_left_out.raw",
            "run_id": LATER_RUN,
            "scan_outcome": ScanOutcome.SKIPPED_BY_SETTINGS,
        }
    )
    question = record_from_row(finding[0], Finding).model_copy(
        update={
            "finding_id": "finding_explorer_lattice_set",
            "rule_id": "group_resolution_deviation",
            "subject": f"folder:{folder_id}",
            "raised": ("a group: brisa kelt",),
            "run_id": LATER_RUN,
        }
    )
    publish_with(copy, LATER_RUN, [left_out, question])

    status, body = _ask(copy, "explorer/wheel")
    assert status == HTTPStatus.OK, body
    exam = next(measure for measure in body["measures"] if measure["key"] == "exam")
    keys = [category["key"] for category in exam["categories"]]
    assert "by_settings" in keys
    totals = dict(zip(keys, body["totals"]["counts"]["exam"], strict=True))
    assert totals["by_settings"] == 1
    status, card = _ask(copy, "explorer/entry", id=folder_id)
    assert status == HTTPStatus.OK, card
    found = card.get("experiment") or card.get("unit") or {}
    assert int(found.get("proposed_groups") or 0) >= 1, card
