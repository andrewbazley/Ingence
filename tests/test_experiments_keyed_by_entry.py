"""An experiment is its folder's entry, never its path.

Every projection that counts experiments reads one set
(:func:`ingence.experiment_units.policy_experiments`). That set is keyed by the folder's entry
identifier, the path kept beside each as a display field, so a folder renamed or moved between two
runs is the experiment a Record answer (``analysed_elsewhere:<entry>``) or a topic's member names,
and two roots that each hold one relative path never collide. Every name here is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
KELT = "mira/2024-02-05_kelt_dapi"
ZEPH = "mira/2024-03-11_zeph_gfp"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("experiments_by_entry")
    pool = base / "pool"
    for number in range(1, 4):
        _write(pool / KELT / "raw" / f"kelt_dapi_{number:03d}.nd2")
        _write(pool / ZEPH / f"zeph_gfp_{number:03d}.nd2")
    _write(pool / KELT / "analysis" / "kelt_dapi_001_results.csv", "a,b\n1,2\n")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def test_the_one_set_is_keyed_by_the_folders_entry_with_its_path_beside(project: Project) -> None:
    from ingence.experiment_units import counted_folders, policy_experiments, run_units

    with open_catalog(project.path) as catalog:
        held = policy_experiments(catalog)
        entries = dict(
            catalog.query(
                "SELECT entry_id, relative_path FROM entries WHERE entry_type = 'directory'"
            ).fetchall()
        )
        units = run_units(catalog)
        counted = counted_folders(catalog)
    assert {item["path"] for item in held.values()} == {KELT, ZEPH}
    for entry_id, item in held.items():
        assert item["entry_id"] == entry_id, "the key is the entry, repeated in the value"
        assert entries[entry_id] == item["path"], "the path is the folder's own, for showing"
        assert item["kind"] in {"experiment", "acquisitions", "analysis", "named"}
    assert counted == frozenset(held) == frozenset(units.units.values())


def test_the_groupings_set_answers_by_entry_and_shows_by_path(project: Project) -> None:
    from ingence.api.routes.groupings import experiment_folders

    with open_catalog(project.path) as catalog:
        found = experiment_folders(catalog)
    kelt = found.by_path[KELT]
    assert found.folders[kelt]["path"] == KELT
    assert found.of(f"{KELT}/raw/kelt_dapi_001.nd2") == kelt
    assert found.of(KELT) == kelt
    assert found.path_of(kelt) == KELT
    assert found.of("mira/notes.txt") is None
    assert found.path_of(None) is None
    assert found.path_of("entry_nobody") is None


def test_the_experiments_route_and_the_files_page_name_the_same_entry(project: Project) -> None:
    from http import HTTPStatus

    from ingence.api.v1 import context_for, dispatch
    from ingence.experiment_units import policy_experiments

    context = context_for(project.path, settled=None, job_running=False)
    status, body = dispatch(context, "experiments", {"limit": ["50"]})
    assert status == HTTPStatus.OK, body
    with open_catalog(project.path) as catalog:
        held = policy_experiments(catalog)
    rows = {row["id"]: row for row in body["rows"]}
    assert set(rows) == set(held)
    for entry_id, row in rows.items():
        assert row["folder"] == held[entry_id]["path"]
        status, files = dispatch(context, "experiments/files", {"experiment": [entry_id]})
        assert status == HTTPStatus.OK, files
        assert files["experiment"]["id"] == entry_id
        assert files["experiment"]["folder"] == held[entry_id]["path"]


def test_a_record_answer_on_an_experiment_keeps_naming_its_entry(project: Project) -> None:
    """The one Record target about an experiment (its analysis is elsewhere) is keyed by the
    folder's entry, so a stored project's answers resolve to the same experiments. Nothing
    stored is keyed by path, so nothing is rebuilt."""
    from ingence.record.targets import analysed_elsewhere_target, experiment_analysed_elsewhere

    with open_catalog(project.path) as catalog:
        from ingence.experiment_units import policy_experiments

        (entry_id,) = [
            unit for unit, held in policy_experiments(catalog).items() if held["path"] == KELT
        ]
    target = analysed_elsewhere_target(entry_id)
    assert experiment_analysed_elsewhere(target) == entry_id
    assert KELT not in target
