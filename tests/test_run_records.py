"""Every command's run record carries its provenance.

The census, the dive, inference and a listing's remote readings each build a run record. Each record
must state the settings the command ran under and the libraries whose behaviour can change a result.
One builder fills them, and a command that publishes a run missing any of them fails here.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from ingence.core.project import Project
from ingence.core.provenance import engine_commit
from ingence.core.run_records import (
    RESULT_LIBRARIES,
    build_run_record,
    config_hash,
    missing_provenance,
)
from ingence.core.schema import RunRecord
from ingence.core.settings import HashSettings, Settings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

RUNS = [f"run_20260101T00000{index}Z_{str(index) * 12}" for index in range(1, 6)]


def published(project: Project) -> RunRecord:
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    return record_from_row(row, RunRecord)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_01.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "vorn_02.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "notes.txt").write_text("zib buffer\n")
    return Project.create(tmp_path / "project", created_at="t", roots=[pool])


def test_every_command_publishes_a_run_stating_its_provenance(project: Project) -> None:
    assert run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z").published
    census = published(project)
    assert missing_provenance(census) == ()
    assert census.config_hash == config_hash(project.settings)

    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:02Z", rebind=True)
    assert missing_provenance(published(project)) == ()

    run_deepen(project, run_id=RUNS[2], started_at="2026-01-01T00:00:03Z", budget_seconds=60)
    dived = published(project)
    assert missing_provenance(dived) == ()
    # The dive was bound by the settings its depth made, not the file's.
    assert dived.config_hash is not None

    run_inference(project, run_id=RUNS[3], started_at="2026-01-01T00:00:04Z")
    assert missing_provenance(published(project)) == ()


def test_a_remote_run_states_its_provenance(tmp_path: Path) -> None:
    from ingence.core.outcomes import IdentityScope
    from ingence.listing.format import Listing, ListingHeader, ListingRow, write_listing
    from ingence.listing.remote import RemoteReading, publish_remote_readings

    project = Project.create(tmp_path / "project", created_at="t", listings=["listing:kelt:K-1"])
    header = ListingHeader(
        archive="kelt", identifier="K-1", fetched_at="t", converter="t", converter_version="1"
    )
    write_listing(
        Listing(header, [ListingRow(path="vorn/a.bin", size_bytes=3)]),
        Path(project.identity.roots[0].mount_path),
    )
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    with open_catalog(project.path) as catalog:
        (entry_id,) = catalog.query("SELECT entry_id FROM entries WHERE name = 'a.bin'").fetchone()
    reading = RemoteReading(entry_id, "blake3_whole", "1", IdentityScope.WHOLE, "f" * 64, 3)
    publish_remote_readings(project, [reading], run_id=RUNS[1], started_at="t")
    assert missing_provenance(published(project)) == ()


def test_the_builder_states_the_engine_its_settings_its_libraries_and_the_machine() -> None:
    settings = Settings()
    record = build_run_record(run_id=RUNS[0], started_at="t", profile="census", settings=settings)
    assert record.engine_commit == engine_commit()
    assert record.config_hash == config_hash(settings)
    versions = dict(record.library_versions)
    assert set(RESULT_LIBRARIES) <= set(versions)
    assert versions["duckdb"] == duckdb.__version__
    assert record.platform is not None
    assert missing_provenance(record) == ()


def test_a_record_built_without_the_builder_is_named_for_what_it_lacks() -> None:
    bare = RunRecord(run_id=RUNS[0], started_at="t", engine_version="0")
    missing = set(missing_provenance(bare))
    assert {"config_hash", "library_versions", "platform"} <= missing
    assert ("engine_commit" in missing) == (engine_commit() is not None)


def test_one_policy_is_one_configuration_whatever_its_notation() -> None:
    dotted = Settings(hash=HashSettings(include_extensions=(".TIF", "csv")))
    plain = Settings(hash=HashSettings(include_extensions=("csv", "tif")))
    assert config_hash(dotted) == config_hash(plain)
    assert config_hash(plain) != config_hash(Settings())


def test_a_run_from_before_warnings_were_counted_says_so(tmp_path: Path) -> None:
    """An older run reads its warnings as ``[]``, which says "raised none" of an engine that never
    counted.

    ``warnings_counted`` tells the two apart by the schema that wrote the run, so a reader shows the
    older run's as unknown.
    """
    import shutil

    from ingence.core.run_records import warnings_counted

    fixture = Path(__file__).parent / "fixtures" / "projects" / "schema_1_21"
    copied = tmp_path / "copy"
    shutil.copytree(fixture, copied)
    older = published(Project.open(copied))
    assert older.warnings == ()
    assert not warnings_counted(older)
    assert warnings_counted(
        build_run_record(
            run_id=RUNS[0], started_at="t", profile="census", settings=None, warnings_tallied=True
        )
    )
