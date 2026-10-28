"""A dive signs a file again, and reads nothing else of it, when only a signature changed.

A dive reads again a file whose kept reading came from an older version of a reader. The readers
that sign a file's content (a table's columns, an array's shape) are readers in that rule. A file
whose only stale reading is a signature is not read again whole, because every extractor would run
on it though none of their readings had changed.

The signature is made again by itself, the way a missing digest is added to a file whose readings
stand. Its new reading replaces the old; every other reading the file holds is kept as it was, and
the file stays at the depth it was read at.

- Only the stale signature runs: no extractor, and no other identity method.
- Every other reading of the file is the same row it was.
- A file with a stale extractor beside the stale signature is read whole.
- A stale reading held by a member of a container reads the container whole.
- The budget bounds the work, and a file the budget did not reach is chosen again.

Every name is invented.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

import ingence.scan.deepen as deepen_module
from ingence.cli.main import app
from ingence.core.ids import LocatorStep
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryKind, EntryType
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors import load_builtins
from ingence.extractors.base import EXTRACTORS
from ingence.extractors.tabular import TabularExtractor
from ingence.identity import IDENTITY_METHODS
from ingence.identity import load_builtins as load_identity_builtins
from ingence.identity.structure import ArraySignature, TabularSchema
from ingence.scan.census import run_census
from ingence.scan.deepen import DeepenReport, run_deepen, signature_only_files
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

load_builtins()
load_identity_builtins()

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 9)]
SCHEMA_NOW = TabularSchema.version
ARRAY_NOW = ArraySignature.version
TABULAR_NOW = TabularExtractor.version

#: Isolation off so the readers run in this process, where the test can count their calls and
#: change their versions; a child process would record the versions it imported.
IN_PROCESS = Settings(extraction=ExtractionSettings(use_subprocess_isolation=False))


def _table(rows: int) -> str:
    return "well,intensity\n" + "".join(f"A{n},{n}\n" for n in range(1, rows + 1))


def _project(tmp_path: Path, files: dict[str, str | bytes]) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for name, body in files.items():
        if isinstance(body, bytes):
            (pool / name).write_bytes(body)
        else:
            (pool / name).write_text(body)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=IN_PROCESS
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    return project


def _in_process(project: Path) -> None:
    """Read in this process rather than in a read worker: a worker is a fresh interpreter, where a
    version this test patches onto a reader does not exist."""
    settings = project / "settings.yaml"
    settings.write_text(
        settings.read_text().rstrip("\n") + "\nextraction:\n  use_subprocess_isolation: false\n"
    )


def _dive(project: Project, run: str, budget: float = 60) -> DeepenReport:
    return run_deepen(
        project, run_id=run, started_at="t", profile="standard", budget_seconds=budget
    )


def _readings(project: Project, name: str) -> list[tuple[Any, ...]]:
    """Every reading of one file, as its identifier, reader, version, key and value."""
    with open_catalog(project.path) as catalog:
        return catalog.query(
            "SELECT o.observation_id, o.observer, o.observer_version, o.key, o.value_text, "
            "o.value_integer FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = ? ORDER BY o.observation_id",
            [name],
        ).fetchall()


def _versions(project: Project, name: str, observer: str) -> set[str]:
    return {row[2] for row in _readings(project, name) if row[1] == observer}


def _without(rows: list[tuple[Any, ...]], observer: str) -> list[tuple[Any, ...]]:
    return [row for row in rows if row[1] != observer]


def _entry(project: Project, name: str) -> tuple[Any, ...]:
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT scan_outcome, extraction_profile FROM entries WHERE name = ?", [name]
        ).fetchone()
    assert row is not None
    return row


class _Calls:
    """How many times each reader ran: extractors by name, identity methods by name."""

    def __init__(self, patch: pytest.MonkeyPatch) -> None:
        self.extractors: Counter[str] = Counter()
        self.signatures: Counter[str] = Counter()
        for name in EXTRACTORS.names(available_only=True):
            self._count(patch, type(EXTRACTORS.get(name)), "extract", name, self.extractors)
        for name in IDENTITY_METHODS.names(available_only=True):
            self._count(patch, type(IDENTITY_METHODS.get(name)), "compute", name, self.signatures)

    @staticmethod
    def _count(
        patch: pytest.MonkeyPatch, owner: type, method: str, name: str, into: Counter[str]
    ) -> None:
        real = getattr(owner, method)

        def counted(self: object, *arguments: object, **keywords: object) -> object:
            into[name] += 1
            return real(self, *arguments, **keywords)

        patch.setattr(owner, method, counted)


# ---------------------------------------------------------------------------
# 1. Only the stale signature runs.
# ---------------------------------------------------------------------------


def test_a_table_whose_only_stale_reading_is_its_columns_is_signed_again_and_read_no_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(
        tmp_path, {"HeLa_DMSO_001.csv": _table(300), "HeLa_notes.txt": "Plate read at 620 nm.\n"}
    )
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        _dive(project, RUNS[1])
    assert _versions(project, "HeLa_DMSO_001.csv", "tabular_schema") == {"1"}
    before = _readings(project, "HeLa_DMSO_001.csv")
    notes_before = _readings(project, "HeLa_notes.txt")
    kept_state = _entry(project, "HeLa_DMSO_001.csv")

    with monkeypatch.context() as counting:
        calls = _Calls(counting)
        report = _dive(project, RUNS[2])

    # The file was chosen for its reader, and signed again.
    assert (report.chosen_because, report.reader_newer_by) == (
        {"reader_newer": 1},
        {"tabular_schema": 1},
    )
    assert report.reader_newer_read == 1
    # No extractor ran, and no identity method but the one that changed.
    assert sum(calls.extractors.values()) == 0, calls.extractors
    assert dict(calls.signatures) == {"tabular_schema": 1}
    # Its new signature replaced the old one.
    assert _versions(project, "HeLa_DMSO_001.csv", "tabular_schema") == {SCHEMA_NOW}
    # Every other reading of the file is the row it was, and so is the file's depth and outcome.
    after = _readings(project, "HeLa_DMSO_001.csv")
    assert _without(after, "tabular_schema") == _without(before, "tabular_schema")
    assert len(_without(after, "tabular_schema")) > 0
    assert _entry(project, "HeLa_DMSO_001.csv") == kept_state
    # A file nothing about changed is untouched.
    assert _readings(project, "HeLa_notes.txt") == notes_before
    # And once only.
    again = _dive(project, RUNS[3])
    assert (again.examined, again.chosen_because) == (0, {})


def test_an_array_whose_only_stale_reading_is_its_signature_is_signed_again_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    numpy = pytest.importorskip("numpy")
    pool = tmp_path / "pool"
    pool.mkdir()
    numpy.save(pool / "HeLa_stack_001.npy", numpy.zeros((4, 5, 6), dtype=numpy.float32))
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=IN_PROCESS
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    with monkeypatch.context() as older:
        older.setattr(ArraySignature, "version", "1")
        _dive(project, RUNS[1])
    before = _readings(project, "HeLa_stack_001.npy")

    with monkeypatch.context() as counting:
        calls = _Calls(counting)
        report = _dive(project, RUNS[2])

    assert report.reader_newer_by == {"array_signature": 1}
    assert sum(calls.extractors.values()) == 0, calls.extractors
    assert dict(calls.signatures) == {"array_signature": 1}
    assert _versions(project, "HeLa_stack_001.npy", "array_signature") == {ARRAY_NOW}
    after = _readings(project, "HeLa_stack_001.npy")
    assert _without(after, "array_signature") == _without(before, "array_signature")
    # The dimension count the method writes beside its value is made again with it.
    assert any(row[3] == "array.dimension_count" and row[2] == ARRAY_NOW for row in after)
    assert _dive(project, RUNS[3]).examined == 0


def test_the_journal_says_the_file_was_signed_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        _dive(project, RUNS[1])
    _dive(project, RUNS[2])
    with open_catalog(project.path) as catalog:
        stages = catalog.query(
            "SELECT json_extract_string(detail, '$.stages[*][0]') "
            "FROM journal_events WHERE event = 'entry.examined' AND run_id = ?",
            [RUNS[2]],
        ).fetchall()
    said = " ".join(str(row[0]) for row in stages)
    assert "identity.tabular_schema" in said
    assert "extract." not in said


# ---------------------------------------------------------------------------
# 2. A file with anything else stale is read whole, as before.
# ---------------------------------------------------------------------------


def test_a_file_whose_extractor_is_stale_too_is_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        older.setattr(TabularExtractor, "version", "1")
        _dive(project, RUNS[1])

    with monkeypatch.context() as counting:
        calls = _Calls(counting)
        report = _dive(project, RUNS[2])
    assert report.reader_newer_by == {"tabular": 1, "tabular_schema": 1}
    assert calls.extractors["tabular"] == 1
    assert _versions(project, "HeLa_DMSO_001.csv", "tabular") == {TABULAR_NOW}
    assert _versions(project, "HeLa_DMSO_001.csv", "tabular_schema") == {SCHEMA_NOW}


def _file(
    entry_id: str, kind: EntryKind = EntryKind.FILESYSTEM, parent: str | None = None
) -> Entry:
    return Entry(
        entry_id=entry_id,
        root_id="root_pool",
        relative_path=entry_id,
        name=entry_id,
        entry_type=EntryType.FILE,
        entry_kind=kind,
        parent_entry_id=parent,
        locator_chain=(LocatorStep(scheme="zip", target=entry_id),) if parent else (),
        transformation="zip" if parent else None,
        first_seen_run="run_one",
        last_seen_run="run_one",
    )


def test_only_a_file_whose_every_stale_reader_signs_its_own_content_is_signed_alone() -> None:
    plain = _file("entry_plain")
    mixed = _file("entry_mixed")
    zipped = _file("entry_zipped")
    member = _file("entry_member", EntryKind.DERIVED, parent="entry_zipped")
    older = {
        "entry_plain": {"tabular_schema"},
        # A stale extractor beside a stale signature: the file is read whole.
        "entry_mixed": {"tabular_schema", "tabular"},
        # A stale signature held by a member of a container: the container is read whole.
        "entry_member": {"tabular_schema"},
    }
    signing = {"tabular_schema", "array_signature"}
    found = signature_only_files([plain, mixed, zipped, member], older, signing)
    assert found == {"entry_plain": ("tabular_schema",)}


# ---------------------------------------------------------------------------
# 3. The budget bounds the work, and the scan errors a signature left stay its own.
# ---------------------------------------------------------------------------


def test_the_budget_bounds_the_signing_and_the_next_dive_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sizes that differ, so no digest is taken for a comparison and each file costs one pass.
    project = _project(tmp_path, {f"HeLa_DMSO_00{n}.csv": _table(450 + n) for n in range(1, 6)})
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        _dive(project, RUNS[1])

    real = deepen_module.deepen_entry

    def one_second_each(*arguments: object, **keywords: object) -> object:
        outcome = real(*arguments, **keywords)  # type: ignore[arg-type]
        outcome.seconds = 1.0
        return outcome

    def current(project: Project) -> int:
        with open_catalog(project.path) as catalog:
            row = catalog.query(
                "SELECT count(DISTINCT entry_id) FROM observations "
                f"WHERE observer = 'tabular_schema' AND observer_version = '{SCHEMA_NOW}'"
            ).fetchone()
        assert row is not None
        return int(row[0])

    assert current(project) == 0
    with monkeypatch.context() as clocked:
        clocked.setattr(deepen_module, "deepen_entry", one_second_each)
        second = _dive(project, RUNS[2], budget=2.5)
    assert second.chosen_because == {"reader_newer": 5}
    assert (second.examined, second.reader_newer_read) == (3, 3)
    assert second.budget is not None
    assert second.budget.unreached == 2
    assert current(project) == 3

    third = _dive(project, RUNS[3])
    assert third.chosen_because == {"reader_newer": 2}
    assert (third.examined, third.reader_newer_read) == (2, 2)
    assert current(project) == 5
    assert _dive(project, RUNS[4]).examined == 0


def test_a_signature_that_fails_now_keeps_the_other_readings_and_records_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        _dive(project, RUNS[1])
    before = _readings(project, "HeLa_DMSO_001.csv")

    def broken(self: object, context: object) -> object:
        message = "the header could not be read"
        raise OSError(message)

    with monkeypatch.context() as failing:
        failing.setattr(TabularSchema, "compute", broken)
        report = _dive(project, RUNS[2])
    assert report.chosen_because == {"reader_newer": 1}
    # Every reading stands, the stale signature too: a signature that cannot be made again is
    # not dropped, and the next dive tries it again.
    assert _readings(project, "HeLa_DMSO_001.csv") == before
    with open_catalog(project.path) as catalog:
        errors = catalog.query(
            "SELECT operation, error_type FROM scan_errors s JOIN entries e USING (entry_id) "
            "WHERE e.name = 'HeLa_DMSO_001.csv'"
        ).fetchall()
    assert errors == [("identity.tabular_schema", "OSError")]


def test_a_file_changed_since_the_census_keeps_none_of_its_readings_as_a_whole_reread_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes are no longer the ones the old readings describe, so none of them is kept."""
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        _dive(project, RUNS[1])
    assert len(_readings(project, "HeLa_DMSO_001.csv")) > 1
    # The file grows after the census saw it.
    (tmp_path / "pool" / "HeLa_DMSO_001.csv").write_text(_table(320))

    report = _dive(project, RUNS[2])

    assert report.chosen_because == {"reader_newer": 1}
    assert (report.changed_during_read, report.reader_newer_read, report.signed_again) == (1, 0, 0)
    # Every reading it held is gone, the stale signature with the rest, as after a whole re-read.
    assert _readings(project, "HeLa_DMSO_001.csv") == []
    outcome, depth = _entry(project, "HeLa_DMSO_001.csv")
    assert (outcome, depth) == ("changed_during_read", None)
    with open_catalog(project.path) as catalog:
        errors = catalog.query(
            "SELECT s.error_type FROM scan_errors s JOIN entries e USING (entry_id) "
            "WHERE e.name = 'HeLa_DMSO_001.csv'"
        ).fetchall()
    assert errors == [("ChangedDuringRead",)]
    # A walk reads it as it now is, and the next dive reads it whole.
    run_census(Project.open(project.path), run_id=RUNS[3], started_at="t")
    again = _dive(project, RUNS[4])
    assert again.chosen_because == {"unread": 1}


def test_the_dive_says_in_words_how_many_files_it_signed_again_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "HeLa_DMSO_001.csv").write_text(_table(300))
    runner = CliRunner()

    def run(*arguments: str) -> str:
        done = runner.invoke(app, list(arguments))
        assert done.exit_code == 0, done.output
        return " ".join(done.output.split())

    run("init", str(tmp_path / "project"), "--root", str(pool))
    _in_process(tmp_path / "project")
    run("index", "-p", str(tmp_path / "project"))
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        run("dive", "-p", str(tmp_path / "project"), "--budget", "30")
    detail = run("dive", "-p", str(tmp_path / "project"), "--budget", "30", "--verbose")
    assert "1 read again because the reader for their type changed" in detail
    assert (
        "by reader: tabular_schema 1; 1 of 1 read in this dive (1 of them by their signature alone)"
        in detail
    )
    assert "Nothing left to read" in run("dive", "-p", str(tmp_path / "project"), "--budget", "30")
