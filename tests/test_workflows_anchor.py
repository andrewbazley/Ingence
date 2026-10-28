"""The generalised raw anchor: a declared workflow's entering types count as acquisitions, and
the safeguards of what is named for an acquisition hold.

Every name is invented. A person declares a workflow entering on ``tif``; the run then reads
plain TIFF recordings as acquisitions for the one grouping that names files for them
(``named_for_an_acquisition``), where no format rule would (a TIFF declares no instrument). The
four safeguards stand: an acquisition's name needs six letters and digits; the nearest of two
acquisitions of one name wins and a tie names none; a backup of an acquisition is named for
nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.workflow import Workflow, WorkflowStage, declare_workflow
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
FOLDER = "mira/2024-02-05_kelt_dapi"
PERSON = "mira"

#: The pool: plain TIFF recordings and tables named for them, and the four safeguards' cases.
FILES = (
    "raw/kelt_dapi_001.tif",
    "analysis/kelt_dapi_001_results.csv",
    "raw/kelt_dapi_002.tif",
    "raw/kelt_dapi_002.tif.bak",
    "analysis/kelt_dapi_002_results.csv",
    # Too short a name to be matched on (fewer than six letters and digits).
    "raw/ab.tif",
    "analysis/ab_results.csv",
    # Two acquisitions of one name, equally near the table named for it: a tie names none.
    "raw_a/zeph_gfp_01.tif",
    "raw_b/zeph_gfp_01.tif",
    "analysis/zeph_gfp_01_results.csv",
)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for name in FILES:
        path = pool / FOLDER / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a,b\n1,2\n" if name.endswith(".csv") else "x")
    return pool


def _classes(project: Project) -> list[set[str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.object_reference, e.name FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'named_for_an_acquisition' AND c.resolution_status = 'resolved'"
        ).fetchall()
    by_key: dict[str, set[str]] = {}
    for key, name in rows:
        by_key.setdefault(str(key), set()).add(str(name))
    return [members for members in by_key.values() if len(members) > 1]


def _run(base: Path, *, declared: bool) -> Project:
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    run_census(created, run_id=CENSUS, started_at="t")
    if declared:
        declare_workflow(
            created.path,
            Workflow(
                name="Plain images",
                rungs=("acquisition", "analysis"),
                stages=(
                    WorkflowStage("acquisition", types=("tif",)),
                    WorkflowStage("analysis", types=("csv",)),
                ),
            ),
            actor=PERSON,
            occurred_at="2026-10-22T01:00:00Z",
            against_run_id=CENSUS,
            known_stages={"acquisition", "analysis"},
        )
    run_inference(created, run_id=INFER, started_at="t")
    return created


@pytest.fixture(scope="module")
def anchored(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return _run(tmp_path_factory.mktemp("anchor_declared"), declared=True)


@pytest.fixture(scope="module")
def plain(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return _run(tmp_path_factory.mktemp("anchor_plain"), declared=False)


def test_a_declared_workflows_entering_type_anchors_what_is_named_for_it(
    anchored: Project, plain: Project
) -> None:
    classes = _classes(anchored)
    assert {"kelt_dapi_001.tif", "kelt_dapi_001_results.csv"} in classes
    assert {"kelt_dapi_002.tif", "kelt_dapi_002_results.csv"} in classes
    # Without the declaration a plain TIFF is nobody's acquisition, so nothing is named for it.
    assert _classes(plain) == []


def test_the_safeguards_hold_under_the_generalised_anchor(anchored: Project) -> None:
    classes = _classes(anchored)
    names = {name for members in classes for name in members}
    # Six letters and digits: a two-letter name is matched on nothing.
    assert "ab_results.csv" not in names and "ab.tif" not in names
    # A tie between two acquisitions of one name, equally near, names none.
    assert "zeph_gfp_01_results.csv" not in names
    # A backup holds the acquisition's whole name and is named for nothing.
    assert "kelt_dapi_002.tif.bak" not in names
    assert not any(name.endswith(".bak") for name in names)
