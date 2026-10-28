"""Root loss: a root that stops answering is noticed and waited for.

The pass never mistakes a lost root for a pool of broken files.

A file whose stat fails before anything is read is not reached, with one access record, and nothing
of it is read. Detection says its errors rather than reading nothing. After twenty such failures
across two folders the pass looks at the root, pauses, retries, resumes where the root returns and
otherwise stops keeping what it read. Every name is invented.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, FailureClass, ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors.detect import detect, sample_head_and_tail
from ingence.scan import deepen
from ingence.scan.cancel import CancellationToken
from ingence.scan.census import run_census
from ingence.scan.deepen import deepen_entry, run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.commit import read_pointer


def _entry(path: Path, size: int = 10) -> Entry:
    return Entry(
        entry_id="entry_gone",
        root_id="root_x",
        relative_path=path.name,
        name=path.name,
        entry_type=EntryType.FILE,
        extension=path.suffix.lstrip("."),
        size_bytes=size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


# -- one file -------------------------------------------------------------------------------------


def test_a_file_whose_stat_fails_is_not_reached_and_nothing_of_it_is_read(tmp_path) -> None:
    gone = tmp_path / "plate_01.csv"
    outcome = deepen_entry(
        _entry(gone),
        gone,
        run_id="r",
        observed_at="t",
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    assert outcome.outcome is ScanOutcome.DEFERRED, "unexamined, not a content error"
    (error,) = outcome.errors
    assert error.operation == "stat"
    assert error.failure_class is FailureClass.ACCESS
    assert "ENOENT" in error.message
    assert outcome.observations == [], "detection never ran"
    assert outcome.stages == []


def test_detection_says_an_access_error_rather_than_reading_nothing(tmp_path) -> None:
    gone = tmp_path / "plate_01.csv"
    with pytest.raises(OSError):
        sample_head_and_tail(gone)
    with pytest.raises(OSError):
        detect(gone, frozenset({".csv"}))


# -- the pass -------------------------------------------------------------------------------------


def _pool(base: Path, files_per_folder: int = 30) -> Path:
    pool = base / "pool"
    for folder in ("plates_a", "plates_b", "plates_c"):
        (pool / folder).mkdir(parents=True)
        for number in range(files_per_folder):
            (pool / folder / f"well_{number:02d}.csv").write_text(
                f"well,value\nA{number},{number}\n"
            )
    return pool


class _RenamesAway:
    """A progress reporter that moves the root away once the pass has recorded ``after`` files."""

    def __init__(self, pool: Path, after: int = 5) -> None:
        self.pool = pool
        self.after = after
        self.examined = 0
        self.moved_to = pool.with_name("pool_gone")

    def advance(self, **deltas: int) -> None:
        self.examined += deltas.get("entries_examined", 0)
        if self.examined == self.after and self.pool.exists():
            os.rename(self.pool, self.moved_to)

    def set(self, **values: object) -> None:
        return None


def _project(tmp_path: Path, **extraction: object) -> tuple[Project, Path]:
    pool = _pool(tmp_path)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True, workers=1, **extraction)),  # type: ignore[arg-type]
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project, pool


def test_a_root_renamed_away_mid_pass_stops_the_pass_and_keeps_what_was_read(
    tmp_path, monkeypatch
) -> None:
    del monkeypatch
    project, pool = _project(tmp_path, root_wait_seconds=0.5, root_retry_seconds=0.1)
    reporter = _RenamesAway(pool, after=5)
    token = CancellationToken()
    try:
        report = run_deepen(
            project,
            run_id="run_20260101T000002Z_222222222222",
            started_at="t",
            budget_seconds=600.0,
            token=token,
            reporter=reporter,
        )
    finally:
        if reporter.moved_to.exists():
            os.rename(reporter.moved_to, pool)
    assert report.stopped, "the root did not return: the pass stopped and kept what it read"
    assert report.root_lost is not None
    assert "stopped answering" in (report.stop_reason or "")
    assert report.extracted >= 5
    assert report.not_reached >= project.settings.extraction.root_loss_failures
    assert report.failed == 0, "a file out of reach is not a content error"
    assert report.budget is not None
    # The file in hand when the root went may be recorded as changed under its reading; every
    # file after the stop is counted as not reached, with the digest work left undone.
    read_or_not = report.extracted + report.not_reached + report.changed_during_read
    assert read_or_not == report.examined
    assert report.budget.unreached >= 90 - report.examined
    pointer = read_pointer(project.path)
    assert pointer is not None
    assert pointer.run_id == "run_20260101T000002Z_222222222222", "published"
    with open_catalog(project.path) as catalog:
        outcomes = dict(
            catalog.query(
                "SELECT scan_outcome, count(*) FROM entries WHERE entry_type = 'file' "
                "GROUP BY scan_outcome"
            ).fetchall()
        )
        events = [
            row[0]
            for row in catalog.query(
                "SELECT event FROM journal_events WHERE event LIKE 'root.%' ORDER BY sequence"
            ).fetchall()
        ]
        errors = {
            row[0]
            for row in catalog.query("SELECT DISTINCT failure_class FROM scan_errors").fetchall()
        }
    assert "content_error" not in outcomes
    assert outcomes.get("deferred", 0) == report.not_reached
    assert outcomes.get("extracted", 0) == report.extracted
    assert outcomes.get("changed_during_read", 0) == report.changed_during_read
    assert events[:2] == ["root.checked", "root.lost"]
    assert errors <= {"access"}


def test_a_root_that_returns_while_the_pass_waits_is_read_on(tmp_path, monkeypatch) -> None:
    project, pool = _project(tmp_path, root_wait_seconds=30.0, root_retry_seconds=0.1)
    reporter = _RenamesAway(pool, after=5)
    waits: list[float] = []

    def returning(token: CancellationToken, seconds: float) -> bool:
        waits.append(seconds)
        if reporter.moved_to.exists():
            os.rename(reporter.moved_to, pool)
        return token.wait(0.0)

    monkeypatch.setattr(deepen, "_root_wait", returning)
    report = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=600.0,
        reporter=reporter,
    )
    assert not report.stopped
    assert report.root_lost is None
    assert waits, "the pass waited for the root"
    assert report.not_reached >= project.settings.extraction.root_loss_failures
    assert report.failed == 0
    assert report.examined == 90, "the rest was read once the root was back"
    assert report.extracted + report.not_reached + report.changed_during_read == 90
    with open_catalog(project.path) as catalog:
        events = [
            row[0]
            for row in catalog.query(
                "SELECT event FROM journal_events WHERE event LIKE 'root.%' ORDER BY sequence"
            ).fetchall()
        ]
    assert events == ["root.checked", "root.returned"]
