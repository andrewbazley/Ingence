"""Budgets on every clock.

A reading is bounded in every stage, and a pass is bounded on the wall. Every stage has a ceiling,
not only a supervised child, so detection, identity and an in-process reader cannot run for as
long as a file makes them. The deep depth has a ceiling too. Every name is invented.
"""

from __future__ import annotations

import time
from pathlib import Path

from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors.limits import ceiling_for, file_ceiling_for
from ingence.scan.census import run_census
from ingence.scan.deepen import FILE_CLOCK_OPERATION, Budget, deepen_entry, run_deepen
from ingence.scan.profiles import PROFILES, ProfileName, apply_profile
from ingence.storage.catalog import open_catalog


def _entry(path: Path) -> Entry:
    return Entry(
        entry_id=f"entry_{path.stem}",
        root_id="root_x",
        relative_path=path.name,
        name=path.name,
        entry_type=EntryType.FILE,
        extension=path.suffix.lstrip("."),
        size_bytes=path.stat().st_size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


# -- the file's own clock -------------------------------------------------------------------------


def test_the_file_ceiling_keeps_the_allowance_per_gigabyte() -> None:
    settings = ExtractionSettings(
        file_seconds=30.0,
        subprocess_timeout_seconds=60.0,
        subprocess_timeout_per_gigabyte_seconds=120.0,
    )
    assert file_ceiling_for(None, settings) == 30.0
    assert file_ceiling_for(0, settings) == 30.0
    assert file_ceiling_for(1 << 30, settings) == 150.0
    # The child's own ceiling is separate from the file ceiling.
    assert ceiling_for(1 << 30, settings) == 180.0


def test_a_file_whose_clock_runs_out_is_a_content_error_for_that_file_alone(tmp_path) -> None:
    """Detection alone outruns a ceiling of a microsecond, so identity is refused and the file
    is recorded as a content error naming the clock. A neighbour with the usual ceiling reads."""
    pool = tmp_path / "pool"
    pool.mkdir()
    slow = pool / "readings_a.csv"
    slow.write_text("sample,value\n1,2\n" * 50)
    fine = pool / "readings_b.csv"
    fine.write_text("sample,value\n3,4\n" * 50)

    hurried = Settings(
        extraction=ExtractionSettings(
            enabled=True, file_seconds=1e-6, subprocess_timeout_per_gigabyte_seconds=0.0
        )
    )
    stopped = deepen_entry(_entry(slow), slow, run_id="r", observed_at="t", settings=hurried)
    assert stopped.outcome is ScanOutcome.CONTENT_ERROR
    operations = [error.operation for error in stopped.errors]
    assert operations.count(FILE_CLOCK_OPERATION) == 1, "recorded once, whatever stages it refused"
    (clock,) = [error for error in stopped.errors if error.operation == FILE_CLOCK_OPERATION]
    assert clock.error_type == "FileSecondsExceeded"
    assert "file_seconds" in clock.message
    # What was read before the clock ran out is kept: detection's own observations.
    assert any(observation.key.startswith("type.") for observation in stopped.observations)
    assert not any(observation.identity_method for observation in stopped.observations)

    usual = Settings(extraction=ExtractionSettings(enabled=True))
    read = deepen_entry(_entry(fine), fine, run_id="r", observed_at="t", settings=usual)
    assert read.outcome is ScanOutcome.EXTRACTED
    assert not any(error.operation == FILE_CLOCK_OPERATION for error in read.errors)


# -- the pass's wall clock ------------------------------------------------------------------------


def test_a_budget_exhausts_on_the_wall_clock_as_well_as_on_work() -> None:
    by_work = Budget(granted_seconds=1.0)
    assert by_work.exhausted_by is None
    by_work.spend(1.5)
    assert by_work.exhausted_by == "work"

    by_wall = Budget(granted_seconds=None, wall_seconds=0.02)
    assert not by_wall.exhausted
    time.sleep(0.05)
    assert by_wall.exhausted
    assert by_wall.exhausted_by == "wall"
    assert by_wall.elapsed >= 0.02

    unbounded = Budget(granted_seconds=None)
    unbounded.spend(10_000.0)
    assert not unbounded.exhausted


def test_deep_has_four_hours_on_the_wall_and_a_person_s_own_ceiling_stands() -> None:
    assert PROFILES[ProfileName.DEEP].total_seconds == 4 * 3600.0
    assert PROFILES[ProfileName.STANDARD].total_seconds is None, "standard keeps its budget"
    assert PROFILES[ProfileName.STANDARD].budget_seconds == 900.0

    deep = apply_profile(Settings(), ProfileName.DEEP)
    assert deep.extraction.total_seconds == 4 * 3600.0
    assert deep.extraction.deepening_budget_seconds is None

    own = apply_profile(
        Settings(extraction=ExtractionSettings(total_seconds=600.0)), ProfileName.DEEP
    )
    assert own.extraction.total_seconds == 600.0

    standard = apply_profile(Settings(), ProfileName.STANDARD)
    assert standard.extraction.total_seconds is None
    assert standard.extraction.deepening_budget_seconds == 900.0


def _indexed(tmp_path: Path, files: int = 6) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for index in range(files):
        (pool / f"plate_{index:02d}.csv").write_text(f"well,value\nA{index},{index}\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project


def test_a_pass_that_runs_out_of_wall_clock_publishes_with_honest_gaps(tmp_path) -> None:
    """A wall ceiling that has already passed reaches no file: every candidate is counted
    unreached, the run is published, and the record says the wall clock was the budget."""
    project = _indexed(tmp_path)
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        total_seconds=1e-6,
    )
    assert not report.cancelled
    assert report.budget is not None
    assert report.budget.exhausted_by == "wall"
    assert report.budget.unreached == 6
    assert report.examined == 0
    # A caller naming the wall clock and no work grant named the budget: no work grant applies.
    assert report.budget.granted_seconds is None
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT deepening_budget_granted_seconds, wall_seconds, work_seconds, workers, "
            "published FROM run"
        ).fetchone()
        unexamined = catalog.query(
            "SELECT count(*) FROM entries WHERE entry_type = 'file' "
            "AND scan_outcome = 'metadata_only'"
        ).fetchone()[0]
    assert row[0] is None
    assert row[1] is not None and row[1] >= 0.0
    assert row[2] == 0.0
    assert row[3] == 1
    assert row[4] is True
    assert unexamined == 6


def test_the_run_record_carries_wall_time_work_time_and_the_worker_count(tmp_path) -> None:
    project = _indexed(tmp_path, files=3)
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=120.0,
    )
    assert report.examined == 3
    assert report.workers == 1
    assert report.wall_seconds is not None and report.wall_seconds > 0.0
    with open_catalog(project.path) as catalog:
        wall, work, workers, granted = catalog.query(
            "SELECT wall_seconds, work_seconds, workers, deepening_budget_granted_seconds FROM run"
        ).fetchone()
    assert wall == report.wall_seconds
    assert work == round(report.budget.spent_seconds, 4)
    assert work > 0.0
    assert workers == 1
    assert granted == 120.0
