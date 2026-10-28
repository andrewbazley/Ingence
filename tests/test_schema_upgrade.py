"""The current schema over projects older engines wrote, and a newer engine's writing refused.

A runner upgrades its projects exactly this way: a newer engine rebinds and infers over a copy of a
project that a 1.20, a 1.21, a 1.22 or a 1.23 engine wrote (0.3.3 wrote the last). The fixtures
under ``fixtures/projects/`` were written by those engines themselves (written by a tool kept in
the development repository). So what is read here is the bytes an older engine left on disk, not
the current models relabelled. Every name in them is invented.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ingence import SCHEMA_VERSION
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind, RunRecord
from ingence.core.versions import NewerSchemaError, is_newer, version_tuple
from ingence.infer.run import run_inference
from ingence.jobs import JobState
from ingence.jobs.ledger import LEDGER_VERSION, JobLedger
from ingence.record.log import append_event, read_events, record_directory
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row
from ingence.storage.layout import CURRENT_POINTER, table_for

FIXTURES = Path(__file__).parent / "fixtures" / "projects"
OLDER = ("1_20", "1_21", "1_22", "1_23")
REBIND_RUN = "run_20260102T000001Z_555555555555"
INFER_RUN = "run_20260102T000002Z_666666666666"


def _copy(tmp_path: Path, version: str) -> Path:
    destination = tmp_path / f"copy_{version}"
    shutil.copytree(FIXTURES / f"schema_{version}", destination)
    return destination


def _next_version() -> str:
    major, minor = version_tuple(SCHEMA_VERSION)[:2]
    return f"{major}.{minor + 1}"


@pytest.mark.parametrize("version", OLDER)
def test_the_fixture_is_what_its_engine_wrote(version: str) -> None:
    identity = json.loads((FIXTURES / f"schema_{version}" / "project.json").read_text("utf-8"))
    assert identity["schema_version"] == version.replace("_", ".")


@pytest.mark.parametrize("version", OLDER)
def test_a_newer_engine_rebinds_and_infers_over_an_older_project(
    tmp_path: Path, version: str
) -> None:
    path = _copy(tmp_path, version)
    with open_catalog(path) as catalog:
        older_run = catalog.query("SELECT run_id FROM run").fetchone()[0]
        older_entries = set(
            catalog.query("SELECT entry_id FROM entries WHERE NOT is_deleted").fetchall()
        )
    older_events = read_events(path, held=False)

    project = Project.open(path)
    rebound = run_census(project, run_id=REBIND_RUN, started_at="2026-01-02T00:00:01Z", rebind=True)
    assert rebound.published
    inferred = run_inference(project, run_id=INFER_RUN, started_at="2026-01-02T00:00:02Z")
    assert inferred.entries > 0

    with open_catalog(path) as catalog:
        run_id, stamped = catalog.query("SELECT run_id, schema_version FROM run").fetchone()
        entries = set(catalog.query("SELECT entry_id FROM entries WHERE NOT is_deleted").fetchall())
        claims = catalog.query("SELECT count(*) FROM claims").fetchone()[0]
    assert (run_id, stamped) == (INFER_RUN, SCHEMA_VERSION)
    # Rebinding reads the stored census: the same entries, identified the same way.
    assert entries == older_entries
    assert claims > 0
    # The older runs stay on disk and readable by the newer engine.
    with open_catalog(path, run_id=older_run) as catalog:
        assert catalog.query("SELECT count(*) FROM entries").fetchone()[0] > 0
    # The Record the older engine wrote reads, and a 1.22 decision appends after it.
    assert read_events(path, held=False) == older_events
    appended = append_event(
        path,
        kind=RecordEventKind.WITHDRAW,
        actor="a person",
        occurred_at="2026-01-02T00:00:03Z",
        target="checkpoint:invented",
        against_run_id=INFER_RUN,
        payload={"withdrawn": [older_events[-1].event_id], "restored_to": "x", "previewed": "y"},
    )
    # A withdrawal is read back with everything; replay leaves it out.
    assert read_events(path, held=False, withdrawn=True)[-1] == appended


def test_a_project_a_1_22_engine_wrote_has_no_name_and_naming_it_makes_it_a_current_file(
    tmp_path: Path,
) -> None:
    """Schema 1.23 added the project's name. A project from before it opens, shows its folder's
    name, and holds the field only once a person names it, when its file says it is current."""
    path = _copy(tmp_path, "1_22")
    held = json.loads((path / "project.json").read_text("utf-8"))
    assert held["schema_version"] == "1.22" and "name" not in held
    project = Project.open(path)
    assert project.name == path.name
    renamed = project.rename("An invented study")
    after = json.loads((path / "project.json").read_text("utf-8"))
    assert after["name"] == "An invented study" and after["schema_version"] == SCHEMA_VERSION
    # Nothing else in the project's file moved, and it opens again as it was left.
    assert {
        key: value for key, value in after.items() if key not in ("name", "schema_version")
    } == {key: value for key, value in held.items() if key != "schema_version"}
    assert Project.open(path).name == renamed.name == "An invented study"


def test_a_project_a_1_23_engine_wrote_opens_and_reads_its_runs_with_no_clocks(
    tmp_path: Path,
) -> None:
    """Schema 1.24 added the content pass's clocks to the run record. A 0.3.3 project opens as
    it is, every run it holds reads with the new fields null (never a guessed zero), and nothing
    of it is read again or rewritten by opening it."""
    path = _copy(tmp_path, "1_23")
    held = (path / "project.json").read_bytes()
    pointer = (path / CURRENT_POINTER).read_bytes()
    project = Project.open(path)
    assert project.name == path.name
    runs = sorted(child.name for child in (path / "runs").iterdir() if child.is_dir())
    assert len(runs) == 4
    for run_id in runs:
        with open_catalog(path, run_id=run_id) as catalog:
            rows = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
            assert catalog.query("SELECT count(*) FROM entries").fetchone()[0] > 0
        assert len(rows) == 1
        record = record_from_row(rows[0], RunRecord)
        assert record.run_id == run_id
        assert record.schema_version == "1.23"
        assert (record.wall_seconds, record.work_seconds, record.workers) == (None, None, None)
    # Opening and reading wrote nothing: the project's file and its current run are as they were.
    assert (path / "project.json").read_bytes() == held
    assert (path / CURRENT_POINTER).read_bytes() == pointer


def test_a_ledger_an_earlier_engine_wrote_is_marked_current_by_the_first_write(
    tmp_path: Path,
) -> None:
    """The job ledger's tables did not change for 1.24; its version says it may now hold a
    cancelled job and a stop request, written the first time this engine writes to it."""
    import sqlite3

    path = _copy(tmp_path, "1_23")
    first = JobLedger(path)
    job = first.enqueue(steps=["inference"], record_sequence=None, base_run=None)
    # As an engine before 0.4.0 left it: version 1.
    with sqlite3.connect(first.path) as connection:
        connection.execute("UPDATE meta SET value = '1' WHERE key = 'ledger_version'")
    assert JobLedger(path).version() == "1"
    ledger = JobLedger(path)
    assert ledger.get(job.job_id) is not None, "reading leaves the version alone"
    assert ledger.version() == "1"
    stopped = ledger.request_stop(job.job_id, keep=False)
    assert stopped is not None and stopped.state is JobState.CANCELLED
    assert ledger.version() == LEDGER_VERSION == "2"


def test_a_decision_a_newer_engine_wrote_is_refused_clearly(tmp_path: Path) -> None:
    path = _copy(tmp_path, "1_21")
    spec = table_for(type(read_events(path, held=False)[0]))
    row = read_events(path, held=False)[0].model_dump(mode="json")
    row.update(schema_version=_next_version(), sequence=99, event_id="rec_newer", kind="unheard")
    columns = {
        name: [json.dumps(row[name[:-5]]) if name.endswith("_json") else row[name]]
        for name in spec.schema.names
    }
    pq.write_table(pa.table(columns, schema=spec.schema), record_directory(path) / "99.parquet")
    with pytest.raises(NewerSchemaError, match=r"decision 99\).*schema " + _next_version()):
        read_events(path, held=False)


def test_a_run_a_newer_engine_published_is_refused_clearly(tmp_path: Path) -> None:
    path = _copy(tmp_path, "1_21")
    pointer = json.loads((path / CURRENT_POINTER).read_text("utf-8"))
    pointer["schema_version"] = _next_version()
    (path / CURRENT_POINTER).write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(NewerSchemaError, match="current run"), open_catalog(path):
        pass


def test_versions_compare_as_numbers() -> None:
    assert is_newer("1.22", than="1.9")
    assert not is_newer("1.9", than="1.22")
    assert not is_newer("1.22", than="1.22")
    assert not is_newer("garbled", than="1.22")
