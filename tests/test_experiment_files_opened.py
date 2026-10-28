"""An experiment's files say whether a dive opened them.

The class: a file says what the last run did with it, in the engine's own word, and the
experiment says how many of its files a dive opened and read, whatever page of them is listed;
a file no dive opened is never described as read. On the explorer fixture (invented names).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject

READ = {"extracted", "extracted_with_fallback"}
WORDS = {
    "metadata_only",
    "skipped_by_policy",
    "extracted",
    "extracted_with_fallback",
    "deferred",
    "content_error",
    "changed_during_read",
    "skipped_by_settings",
}


def _listing(project: Path, **query: Any) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    status, body = dispatch(
        context, "experiments/files", {name: [str(v)] for name, v in query.items()}
    )
    assert status == HTTPStatus.OK, body
    return body


def test_each_file_names_what_the_run_did_with_it(explorer_project: ExplorerProject) -> None:
    folder = explorer_project.experiments[0]
    body = _listing(explorer_project.project, experiment=explorer_project.ids[folder], limit=500)
    assert body["rows"]
    assert {row["scan_outcome"] for row in body["rows"]} <= WORDS
    assert body["opened_files"] == sum(row["scan_outcome"] in READ for row in body["rows"])
    # A dive read the fixture's files, so some experiment has files opened.
    everything = [
        _listing(explorer_project.project, experiment=explorer_project.ids[name], limit=500)
        for name in explorer_project.experiments
    ]
    assert sum(one["opened_files"] for one in everything) > 0


def test_the_count_of_opened_files_is_the_whole_experiment_s_not_the_page_s(
    explorer_project: ExplorerProject,
) -> None:
    folder = explorer_project.experiments[0]
    experiment = explorer_project.ids[folder]
    whole = _listing(explorer_project.project, experiment=experiment, limit=500)
    first = _listing(explorer_project.project, experiment=experiment, limit=1)
    assert len(first["rows"]) == 1
    assert first["opened_files"] == whole["opened_files"] <= whole["files"]
