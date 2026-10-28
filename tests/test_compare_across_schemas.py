"""Comparing runs written under two schemas.

A run that an older engine wrote holds none of the columns added since. So a project upgraded in
place must compare its new runs with its old ones without asking DuckDB for a column the older run
lacks. Each side is read through an explicit compatibility projection: a column the older run lacks
is its declared default, or null where it declares none. The older side is a fixture that its
own engine wrote, copied, never modified.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ingence.compare.match import Side
from ingence.core.project import Project
from ingence.core.schema import Entry, RunRecord
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import (
    compare_runs,
    declared_default,
    describe_differences,
    open_catalog,
)
from ingence.storage.layout import table_for

FIXTURES = Path(__file__).parent / "fixtures" / "projects"
OLDER = ("1_20", "1_21")
#: And the schema before this one, which 0.3.3 wrote (schema 1.24 added the run's clocks).
COMPARED = (*OLDER, "1_23")
#: The older engines' last run in each fixture: an inference after a dive.
OLDER_INFERENCE = "run_20260101T000004Z_444444444444"
OLDER_CENSUS = "run_20260101T000001Z_111111111111"
REBIND_RUN = "run_20260102T000001Z_555555555555"
INFER_RUN = "run_20260102T000002Z_666666666666"


def _upgraded(tmp_path: Path, version: str) -> Path:
    """A copy of the fixture with a rebind and an inference the current engine published."""
    path = tmp_path / f"copy_{version}"
    shutil.copytree(FIXTURES / f"schema_{version}", path)
    project = Project.open(path)
    run_census(project, run_id=REBIND_RUN, started_at="2026-01-02T00:00:01Z", rebind=True)
    run_inference(project, run_id=INFER_RUN, started_at="2026-01-02T00:00:02Z")
    return path


@pytest.mark.parametrize("version", COMPARED)
def test_a_run_an_older_engine_wrote_compares_with_one_the_current_engine_wrote(
    tmp_path: Path, version: str
) -> None:
    path = _upgraded(tmp_path, version)
    differences = compare_runs(path, OLDER_INFERENCE, INFER_RUN)
    # A column the older run never held reads as its declared default, which is what the
    # current engine writes for the same entry, so it is not reported as a difference.
    assert "entries" not in differences, describe_differences(path, OLDER_INFERENCE, INFER_RUN)
    # Both directions, and the account of why, run too.
    assert compare_runs(path, INFER_RUN, OLDER_INFERENCE) == differences
    assert isinstance(describe_differences(path, OLDER_CENSUS, REBIND_RUN), dict)


@pytest.mark.parametrize("version", OLDER)
def test_an_older_run_opened_by_the_current_engine_holds_every_current_column(
    tmp_path: Path, version: str
) -> None:
    path = tmp_path / f"copy_{version}"
    shutil.copytree(FIXTURES / f"schema_{version}", path)
    with open_catalog(path, run_id=OLDER_INFERENCE) as catalog:
        rows = catalog.query(
            "SELECT count(*), count(moved_from), count(extraction_profile) FROM entries"
        ).fetchone()
        run = catalog.query("SELECT hash_mode, warnings_json, engine_commit FROM run").fetchone()
    assert rows[0] > 0
    assert rows[1:] == (0, 0)
    assert run == (None, "[]", None)


def test_a_1_23_run_reads_the_clocks_1_24_added_as_null(tmp_path: Path) -> None:
    """The content pass's clocks (schema 1.24) were never held by a run 0.3.3 wrote: each reads
    as null, which the run record declares, never as a zero that would claim a pass took no time.
    """
    path = tmp_path / "copy_1_23"
    shutil.copytree(FIXTURES / "schema_1_23", path)
    dive = "run_20260101T000003Z_333333333333"
    for run_id in (OLDER_CENSUS, dive, OLDER_INFERENCE):
        with open_catalog(path, run_id=run_id) as catalog:
            held = catalog.query("SELECT wall_seconds, work_seconds, workers FROM run").fetchone()
        assert held == (None, None, None), run_id
    spec = table_for(RunRecord)
    for column in ("wall_seconds", "work_seconds", "workers"):
        assert declared_default(spec, column).startswith("NULL"), column


def test_a_reorganisation_comparison_reads_an_older_run(tmp_path: Path) -> None:
    from ingence.compare.report import measure

    path = _upgraded(tmp_path, "1_20")
    measured = measure(
        Side(project=path, run_id=OLDER_INFERENCE), Side(project=path, run_id=INFER_RUN)
    )
    assert measured.summary.matched_total == measured.summary.files["l"] > 0


def test_the_declared_default_is_the_stored_form_of_the_field_default() -> None:
    entries = table_for(Entry)
    run = table_for(RunRecord)
    assert declared_default(entries, "moved_from") == "NULL::VARCHAR"
    assert declared_default(run, "warnings_json") == "'[]'::VARCHAR"
    assert declared_default(run, "published") == "false::BOOLEAN"
    # A field with no default is null, never a guess.
    assert declared_default(entries, "entry_id") == "NULL::VARCHAR"
