"""The quality export's run table carries answers, settled claims and time.

``quality_runs.csv`` has one row per run. Beside the versions that wrote it, each row holds the
number of answers in the answer log that the run had read, the number of claims it settled and the
seconds it took. Each is checked against a count taken another way, from the stored tables.

Every name is invented.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.quality.export import RUN_COLUMNS
from ingence.record.log import append_event
from ingence.record.questions import question_target
from ingence.record.targets import pack_target
from ingence.rulebook.predicates.packs import pack_value
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
INFER = "run_20260101T000003Z_333333333333"

FILES = {
    "vorn/imaging/vorn_DAPI_001.csv": "a,b\n1,2\n",
    "vorn/imaging/vorn_DAPI_002.csv": "a,b\n3,4\n",
    "vorn/analysis/vorn_DAPI_001_results.csv": "cell,area\n1,40\n",
    "notes/westernblot_protocol.txt": "block for one hour\n",
}


def _stamp(seconds: int) -> str:
    return datetime.fromtimestamp(1_767_225_600 + seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _answer(project: Path, kind: RecordEventKind, domain: str) -> None:
    append_event(
        project,
        kind=kind,
        actor="zib (an invented person)",
        occurred_at="2026-01-01T00:00:00Z",
        target=pack_target(domain),
        against_run_id=CENSUS,
        payload={
            "question": question_target("domain_pack_would_read_this_pool", pack_value(domain))
        },
    )


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    base = tmp_path_factory.mktemp("quality_runs")
    pool = base / "pool"
    for relative, text in FILES.items():
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at=_stamp(0))
    # Two answers and a note (which decides nothing) are written after the first run and before
    # the second, which reads them.
    _answer(project.path, RecordEventKind.CONFIRM, "microscopy")
    _answer(project.path, RecordEventKind.REJECT, "sequencing")
    _answer(project.path, RecordEventKind.NOTE, "microscopy")
    run_deepen(project, run_id=DIVE, started_at=_stamp(10), budget_seconds=60)
    run_inference(project, run_id=INFER, started_at=_stamp(20))
    out = base / "out"
    arguments = ["export", "quality", "--project", str(project.path), "--all", "--out", str(out)]
    done = CliRunner().invoke(app, arguments)
    assert done.exit_code == 0, done.output
    with (out / "quality_runs.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["run"]: row for row in csv.DictReader(handle)}
    return {"project": project, "rows": rows, "out": out}


def test_the_run_table_has_the_three_columns_after_the_versions(
    exported: dict[str, object],
) -> None:
    assert RUN_COLUMNS[-3:] == ("record_answers", "settled_claims", "wall_seconds")
    rows = exported["rows"]
    assert isinstance(rows, dict) and set(rows) == {CENSUS, DIVE, INFER}
    for row in rows.values():
        assert list(row)[-3:] == ["record_answers", "settled_claims", "wall_seconds"]


def test_a_run_counts_the_answers_it_had_read_and_no_note(exported: dict[str, object]) -> None:
    rows = exported["rows"]
    assert isinstance(rows, dict)
    # The first run read an empty answer log. The next two read the two answers, not the note.
    assert rows[CENSUS]["record_answers"] == "0"
    assert rows[DIVE]["record_answers"] == "2"
    assert rows[INFER]["record_answers"] == "2"


def test_a_run_counts_the_claims_it_settled_where_it_concluded_any(
    exported: dict[str, object],
) -> None:
    rows, project = exported["rows"], exported["project"]
    assert isinstance(rows, dict) and isinstance(project, Project)
    with open_catalog(project.path, run_id=INFER) as catalog:
        row = catalog.query(
            "SELECT count(*) FROM claims c LEFT JOIN rules r ON r.rule_id = c.rule_id "
            "AND r.version = c.rule_version "
            "WHERE c.resolution_status = 'resolved' AND coalesce(r.effect, '') <> 'enumerates'"
        ).fetchone()
    assert row is not None and row[0] > 0
    assert rows[INFER]["settled_claims"] == str(row[0])
    # A pass that concluded no claims leaves the cell blank, never zero.
    assert rows[CENSUS]["settled_claims"] == "" and rows[DIVE]["settled_claims"] == ""


def test_a_run_states_its_wall_time_in_seconds_from_its_own_record(
    exported: dict[str, object],
) -> None:
    rows, project = exported["rows"], exported["project"]
    assert isinstance(rows, dict) and isinstance(project, Project)
    for run_id in (CENSUS, DIVE, INFER):
        with open_catalog(project.path, run_id=run_id) as catalog:
            began, ended = catalog.query("SELECT started_at, ended_at FROM run").fetchone()
        stamp = "%Y-%m-%dT%H:%M:%SZ"
        taken = (
            datetime.strptime(ended, stamp).replace(tzinfo=UTC)
            - datetime.strptime(began, stamp).replace(tzinfo=UTC)
        ).total_seconds()
        assert float(rows[run_id]["wall_seconds"]) == pytest.approx(taken)
