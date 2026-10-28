"""A run or a decision a newer build wrote is refused in words by every verb that reads it.

A project whose current run, or one decision of whose Record, is stamped at a newer schema than
this build reads is refused in words, with a status of 1 and no traceback, whichever verb meets it
and wherever it meets it. The refusal (``NewerSchemaError``) is raised deep inside the reads, at the
current run's pointer and at the Record's fragments. It must reach the person as words and not as a
traceback with the words on its last line. The README promises that an older engine "stops with a
message naming the version", and that promise must hold for the stop and not only for the words.

Both projects are made with this build's engine and then stamped at a version after it, derived
from the current one so the test never goes stale. Every name is invented.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from ingence import SCHEMA_VERSION
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.record.log import append_event, record_directory
from ingence.scan.census import run_census
from ingence.storage.layout import CURRENT_POINTER

REPOSITORY = Path(__file__).resolve().parents[1]
MAJOR, MINOR = (int(part) for part in SCHEMA_VERSION.split("."))
NEWER = f"{MAJOR}.{MINOR + 7}"
RUN = "run_20260101T000001Z_aaaaaaaaaaaa"

#: Every verb that opens a project, with where it takes the project's name and whether it reads
#: the current run (``run``) and the Record (``record``): the verbs that must refuse each project.
#: A verb absent from a column does not read that thing, so it has nothing to refuse there.
VERBS: tuple[tuple[list[str], str, bool, bool], ...] = (
    (["index"], "-p", True, True),
    (["dive"], "-p", True, True),
    (["infer"], "-p", True, True),
    (["fix"], "-p", True, False),
    (["rule", "show"], "-p", True, True),
    (["teach", "vocabulary"], "-p", True, False),
    (["teach", "unknown"], "-p", True, False),
    (["teach", "collisions"], "-p", True, True),
    (["teach", "shapes"], "-p", True, False),
    (["export", "themes"], "--project", True, True),
    (["export", "quality"], "--project", True, False),
    (["teach", "pack", "list"], "-p", False, False),
)


def _made(base: Path, name: str) -> Path:
    pool = base / "pool"
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "kelt_01.csv").write_text("a,b\n1,2\n")
    project = Project.create(base / name, created_at="t", roots=[pool])
    run_census(project, run_id=RUN, started_at="t")
    return project.path


@pytest.fixture(scope="module")
def newer_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project whose current run was published at a schema after this build's."""
    path = _made(tmp_path_factory.mktemp("newer_run"), "project")
    pointer = json.loads((path / CURRENT_POINTER).read_text(encoding="utf-8"))
    pointer["schema_version"] = NEWER
    (path / CURRENT_POINTER).write_text(json.dumps(pointer), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def newer_decision(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project holding one decision a newer build wrote, beside a run this build can read."""
    path = _made(tmp_path_factory.mktemp("newer_decision"), "project")
    append_event(
        path,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-02T00:00:00Z",
        target="term:kelt",
        against_run_id=RUN,
        payload={"text": "an invented note"},
    )
    for fragment in record_directory(path).glob("*.parquet"):
        table = pq.read_table(fragment)
        at = table.schema.get_field_index("schema_version")
        stamped = pa.array([NEWER] * table.num_rows)
        pq.write_table(table.set_column(at, table.schema.field(at), stamped), fragment)
    return path


def _said(ran: object) -> str:
    return " ".join(str(getattr(ran, "output", "")).split())


def _refused_in_words(ran: object, *, what: str) -> None:
    assert getattr(ran, "exit_code", 0) == 1, _said(ran)
    # A refusal is an exit, never an exception the runner had to catch.
    assert isinstance(getattr(ran, "exception", None), SystemExit), repr(ran)
    said = _said(ran)
    assert "Traceback" not in said
    assert what in said and f"written at schema {NEWER}" in said
    assert f"this engine reads schema {SCHEMA_VERSION}" in said
    assert "upgrade Ingence" in said


@pytest.mark.parametrize(
    ("verb", "flag", "reads_run", "reads_record"), VERBS, ids=[" ".join(v[0]) for v in VERBS]
)
def test_every_verb_that_reads_a_newer_run_refuses_it_in_words(
    newer_run: Path, verb: list[str], flag: str, reads_run: bool, reads_record: bool
) -> None:
    del reads_record
    ran = CliRunner().invoke(app, [*verb, flag, str(newer_run)])
    if reads_run:
        _refused_in_words(ran, what="the current run of the project at")
    else:
        assert ran.exception is None or isinstance(ran.exception, SystemExit), repr(ran.exception)
        assert "Traceback" not in _said(ran)


@pytest.mark.parametrize(
    ("verb", "flag", "reads_run", "reads_record"), VERBS, ids=[" ".join(v[0]) for v in VERBS]
)
def test_every_verb_that_reads_a_newer_decision_refuses_it_in_words(
    newer_decision: Path, verb: list[str], flag: str, reads_run: bool, reads_record: bool
) -> None:
    del reads_run
    ran = CliRunner().invoke(app, [*verb, flag, str(newer_decision)])
    if reads_record:
        _refused_in_words(ran, what="the Record at")
        assert "(decision 1)" in _said(ran)
    else:
        assert ran.exception is None or isinstance(ran.exception, SystemExit), repr(ran.exception)
        assert "Traceback" not in _said(ran)


def _free_port() -> int:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return int(held.getsockname()[1])


@pytest.mark.parametrize("which", ["newer_run", "newer_decision"])
def test_the_dashboard_refuses_to_start_on_a_newer_projects_run_or_decision(
    which: str, request: pytest.FixtureRequest
) -> None:
    path = request.getfixturevalue(which)
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "see",
            "--no-open",
            "-p",
            str(path),
            "--port",
            str(_free_port()),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY / "src")},
        timeout=120,
        check=False,
    )
    both = " ".join((ran.stdout + ran.stderr).split())
    assert ran.returncode == 1, both
    assert "Traceback" not in both and "NewerSchemaError" not in both
    assert f"written at schema {NEWER}" in both and "upgrade Ingence" in both


def test_the_command_a_person_types_prints_no_traceback_for_a_newer_run(newer_run: Path) -> None:
    ran = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "infer", "-p", str(newer_run)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY / "src")},
        timeout=120,
        check=False,
    )
    both = ran.stdout + ran.stderr
    assert ran.returncode == 1
    assert "Traceback" not in both and "NewerSchemaError" not in both
    assert "upgrade Ingence" in " ".join(both.split())
