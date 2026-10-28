"""What a run says about the name reader that read it.

The run record states the reader. A partial rebind is refused across tokenisers 10 and 11, in words
naming both, so a stored project needs one whole rebind. History says the reader changed rather than
calling every word new. A run read by tokeniser 10 is made by reading with the version the module
states set to ``10``. Every name here is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ingence.api.routes import changes as change_routes
from ingence.core.project import Project
from ingence.core.schema import RunRecord
from ingence.dictionary.normalize import NORMALIZER_VERSION
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _census(project: Project, suffix: str, **options: object):
    report = run_census(
        project,
        run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
        started_at="2026-01-01T00:00:00Z",
        **options,
    )
    assert report.published
    return report


def _pool(base: Path) -> Path:
    pool = base / "pool"
    pool.mkdir()
    for name in ("scan_7_12_09_2016.txt", "kelt_zib.txt", "run_Tue_Mar__9_14-07-45_2021_log.txt"):
        (pool / name).write_text("a")
    return pool


def test_the_run_record_states_the_name_reader(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    _census(project, "1")
    with open_catalog(project.path) as catalog:
        stated = catalog.query("SELECT normalizer_version FROM run").fetchall()
    assert stated == [(NORMALIZER_VERSION,)]
    assert RunRecord.model_fields["normalizer_version"].default is None


def test_a_partial_rebind_is_refused_across_readers_10_and_11(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project read by tokeniser 10 needs one whole rebind: a partial one would carry names read
    by 10 beside names read by 11, and is refused in words naming both."""
    import ingence.dictionary.normalize as normalize

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    with monkeypatch.context() as patched:
        patched.setattr(normalize, "NORMALIZER_VERSION", "10")
        _census(project, "1")
    with pytest.raises(
        ValueError, match=f"tokeniser version 10 and this engine reads them as {NORMALIZER_VERSION}"
    ):
        _census(Project.open(project.path), "2", rebind=True, spellings=("zib",))
    whole = _census(Project.open(project.path), "3", rebind=True)
    assert whole.published
    with open_catalog(project.path) as catalog:
        readers = catalog.query("SELECT DISTINCT normalizer_version FROM components").fetchall()
    assert readers == [(NORMALIZER_VERSION,)]


def test_history_says_the_name_reader_changed_across_10_and_11(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingence.dictionary.normalize as normalize

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    with monkeypatch.context() as patched:
        patched.setattr(normalize, "NORMALIZER_VERSION", "10")
        first = _census(project, "1")
    second = _census(Project.open(project.path), "2", rebind=True)
    found = change_routes.changes(project.path, before=first.run_id, after=second.run_id)
    assert found["bindings"]["readers"] == {"before": ["10"], "after": [NORMALIZER_VERSION]}
    assert (
        f"(version 10 before, {NORMALIZER_VERSION} after)" in change_routes.said_plainly(found)[0]
    )
