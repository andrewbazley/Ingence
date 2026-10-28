"""A file that changed while it was read.

A file that another program is writing gives readings that describe neither version. The dive
takes the file's size, modification time, inode and device before and after the reading. A
difference is the outcome ``changed_during_read`` (never reusable). None of the reading is kept
as the file's, and the next pass reads the file again.

The writer is a fixture extractor that appends to its file mid-read, in process and behind the
supervisor (``fixtures/hostile.py``; a bare script re-importing ``__main__`` fakes failures).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.hostile import REWRITE_SUFFIX, register_all
from ingence.core.project import Project
from ingence.core.schema import ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors.base import EXTRACTORS
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
FIRST_DIVE = "run_20260101T000002Z_222222222222"
REFUSED_DIVE = "run_20260101T000003Z_333333333333"
WALK_AGAIN = "run_20260101T000004Z_444444444444"
SECOND_DIVE = "run_20260101T000005Z_555555555555"


@pytest.fixture
def fixtures():
    names = register_all()
    yield names
    for name in names:
        EXTRACTORS.unregister(name)


def _project(tmp_path: Path, *, isolated: bool) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / f"vorn{REWRITE_SUFFIX}").write_bytes(b"one line\n")
    (pool / "kelt.txt").write_text("a settled file\n")
    return Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            extraction=ExtractionSettings(
                enabled=True,
                adapters=("hostile_rewrite", "text"),
                use_subprocess_isolation=isolated,
            )
        ),
    )


def _state(project: Project, name: str) -> tuple[str, int, int]:
    with open_catalog(project.path) as catalog:
        outcome, observations, errors = catalog.query(
            "SELECT e.scan_outcome, "
            "(SELECT count(*) FROM observations o WHERE o.entry_id = e.entry_id "
            " AND o.observer <> 'census'), "
            "(SELECT count(*) FROM scan_errors s WHERE s.entry_id = e.entry_id) "
            "FROM entries e WHERE e.name = ?",
            [name],
        ).fetchone()
    return str(outcome), int(observations), int(errors)


@pytest.mark.parametrize("isolated", [False, True])
def test_a_file_changed_while_it_was_read_keeps_none_of_the_reading(
    tmp_path: Path, fixtures, isolated: bool
) -> None:
    project = _project(tmp_path, isolated=isolated)
    run_census(project, run_id=CENSUS, started_at="t")
    report = run_deepen(project, run_id=FIRST_DIVE, started_at="t", budget_seconds=60)

    outcome, observations, errors = _state(project, f"vorn{REWRITE_SUFFIX}")
    assert outcome == ScanOutcome.CHANGED_DURING_READ.value
    # Neither the reader's readings nor its digests are the file's: they describe neither version.
    assert observations == 0
    # One access record says what moved, so the reason is readable beside the file.
    assert errors == 1
    assert report.changed_during_read == 1
    # A file nothing wrote to is read as ever.
    settled, kept, _ = _state(project, "kelt.txt")
    assert settled != ScanOutcome.CHANGED_DURING_READ.value
    assert kept > 0


def test_a_file_that_changed_is_read_again_after_a_walk(tmp_path: Path, fixtures) -> None:
    project = _project(tmp_path, isolated=False)
    run_census(project, run_id=CENSUS, started_at="t")
    run_deepen(project, run_id=FIRST_DIVE, started_at="t", budget_seconds=60)
    # The file is not the one the census recorded, so a dive refuses it until a walk records it
    # again. After the walk it is read.
    refused = run_deepen(project, run_id=REFUSED_DIVE, started_at="t")
    assert refused.changed_during_read == 1
    run_census(project, run_id=WALK_AGAIN, started_at="t")
    report = run_deepen(project, run_id=SECOND_DIVE, started_at="t", budget_seconds=60)

    assert report.examined >= 1
    outcome, observations, errors = _state(project, f"vorn{REWRITE_SUFFIX}")
    assert outcome == ScanOutcome.EXTRACTED.value
    assert observations > 0
    assert errors == 0
    with open_catalog(project.path) as catalog:
        (lines,) = catalog.query(
            "SELECT value_text FROM observations WHERE key = 'hostile.lines'"
        ).fetchone()
    assert lines == "2"


@pytest.mark.parametrize("identity", [True, False])
def test_an_inode_a_filesystem_invents_is_checked_only_where_the_settings_say(
    tmp_path: Path, monkeypatch, identity: bool
) -> None:
    """A file replaced under the same size and time differs by inode alone; a filesystem that
    invents an inode per lookup (some network and FUSE mounts) differs the same way, so the
    identity half of the check is a setting (on by default)."""
    from ingence.core.schema import Entry, EntryType
    from ingence.scan import deepen

    path = tmp_path / "kelt.txt"
    path.write_text("a settled file\n")
    states = iter([(15, 1, 7, 3), (15, 1, 8, 3)])
    monkeypatch.setattr(deepen, "source_state", lambda _: next(states))
    entry = Entry(
        entry_id="entry_kelt",
        root_id="root_x",
        relative_path="kelt.txt",
        name="kelt.txt",
        entry_type=EntryType.FILE,
        extension="txt",
        size_bytes=path.stat().st_size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )
    outcome = deepen.deepen_entry(
        entry,
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(extraction=ExtractionSettings(freshness_checks_identity=identity)),
    )
    assert (outcome.outcome is ScanOutcome.CHANGED_DURING_READ) is identity


def test_a_file_changed_since_the_census_is_not_read_as_the_census_saw_it(tmp_path: Path) -> None:
    """A file changed since the census is refused before it is opened, until a walk.

    The check compares the file before and after the reading, and also with what the census
    recorded. Without the second comparison, a file changed between the census and the dive would
    be read as the entry the census described."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("the census saw this\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    (pool / "kelt.txt").write_text("and then somebody wrote more than the census saw\n")
    report = run_deepen(project, run_id=FIRST_DIVE, started_at="t", budget_seconds=60)
    outcome, observations, errors = _state(project, "kelt.txt")
    assert (outcome, observations, errors) == (ScanOutcome.CHANGED_DURING_READ.value, 0, 1)
    assert report.changed_during_read == 1
    with open_catalog(project.path) as catalog:
        (message,) = catalog.query("SELECT message FROM scan_errors").fetchone()
    assert "since the census" in message
