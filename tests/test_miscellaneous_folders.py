"""A user's miscellaneous files: kept as they are, proposed for approval, never an experiment.

A folder of files that users keep on their servers, perhaps unrelated to one experiment or to
actual data collection, can be thought of as user-specific miscellaneous files that they can
approve. The file structure of such files is kept, and they are not forced into experiments or
experimental archetypes.

So the outermost folder that neither is, nor lies in, nor holds an experiment or an analysis, and
holds files, is raised as a question proposing it as miscellaneous. A person's yes (any person's,
here an invented one) makes it a claim. Nothing is moved or regrouped. Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import tune, write_revision
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import append_event
from ingence.record.targets import miscellaneous_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _raised(project: Project) -> dict[str, list[str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e "
            "ON f.subject LIKE '%' || e.entry_id || '%' "
            "WHERE f.rule_id = 'miscellaneous_files_to_approve' AND e.entry_type = 'directory'"
        ).fetchall()
    return {path: list(values) for path, values in rows}


def test_a_folder_of_no_experiment_is_proposed_as_miscellaneous_and_claimed_once_approved(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2", "Traj_kelt_vorn_01.csv"):
        _write(pool / "20240301 kelt vorn" / name)
    _write(pool / "brisa assay" / "plate layout.xlsx")
    _write(pool / "brisa assay" / "brisa scores.csv")
    _write(pool / "brisa assay" / "older" / "brisa scores draft.csv")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    first = "run_20260101T000002Z_000000000002"
    run_inference(created, run_id=first, started_at="t")

    raised = _raised(created)
    assert set(raised) == {"brisa assay"}  # the outermost only, never the experiment
    assert raised["brisa assay"] == ["brisa assay: miscellaneous files"]

    with open_catalog(created.path) as catalog:
        (folder_id,) = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'brisa assay'"
        ).fetchone()
    append_event(
        created.path,
        kind=RecordEventKind.CONFIRM,
        actor="trell (an invented person)",
        occurred_at="2026-01-01T00:00:03Z",
        target=miscellaneous_target(folder_id),
        against_run_id=first,
    )
    run_inference(created, run_id="run_20260101T000004Z_000000000004", started_at="t")
    with open_catalog(created.path) as catalog:
        claimed = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = 'miscellaneous_files_approved' "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
    assert dict(claimed) == {"brisa assay": "miscellaneous"}
    assert _raised(created) == {}  # answered: no longer asked


def test_a_folder_mostly_of_other_files_is_asked_once_though_a_dated_run_lies_inside(
    tmp_path: Path,
) -> None:
    """A stray dated folder inside does not split the question into fragments."""
    pool = tmp_path / "pool"
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
        _write(pool / "20240301 kelt vorn" / name)
    for folder in ("brisa plasmids", "brisa plasmids/drafts", "brisa plasmids/maps"):
        for number in range(4):
            _write(pool / folder / f"brisa map {number}.txt")
    _write(pool / "brisa plasmids" / "reads_20240302" / "brisa read.txt")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")
    assert set(_raised(created)) == {"brisa plasmids"}


def test_a_folder_of_ones_own_experiments_is_offered_only_where_switched_on(tmp_path: Path) -> None:
    """A named folder holding two experiments is offered only where the setting is on."""

    pool = tmp_path / "pool"
    for day, word in (("20240301 brisa assay", "zib"), ("20240309 brisa assay", "trell")):
        for number in (1, 2):
            _write(pool / "brisa assays" / day / f"{word}_movie_{number:02d}.nd2")
    for name in ("kelt_vorn_01.nd2", "kelt_vorn_02.nd2"):
        _write(pool / "20240301 kelt vorn" / name)
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")
    assert _raised(created) == {}
    tune(created.path, "organising_folders", True, rules=("miscellaneous_files_to_approve",))
    run_inference(created, run_id="run_20260101T000003Z_000000000003", started_at="t")
    assert set(_raised(created)) == {"brisa assays"}
