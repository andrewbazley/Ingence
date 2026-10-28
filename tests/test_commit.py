"""The write protocol publishes atomically, or changes nothing at all."""

from __future__ import annotations

import pytest

from ingence.core.schema import InvariantOutcome
from ingence.storage.commit import (
    CommitError,
    build_manifest,
    commit_run,
    fsync_directory,
    fsync_file,
    read_pointer,
    resolve_current_run,
    run_path,
    staging_path,
)


def stage(project, run_id: str, payload: bytes = b"rows"):
    staged = staging_path(project, run_id)
    (staged / "entries").mkdir(parents=True)
    (staged / "entries" / "part-0.parquet").write_bytes(payload)
    return staged


def manifest_for(project, run_id: str, **kwargs):
    return build_manifest(
        staging_path(project, run_id),
        run_id=run_id,
        engine_version="test",
        created_at="2026-01-01T00:00:00Z",
        **kwargs,
    )


def test_a_project_with_no_committed_run_resolves_to_nothing(tmp_path):
    assert read_pointer(tmp_path) is None
    assert resolve_current_run(tmp_path) is None


def test_committing_publishes_the_run_and_swaps_the_pointer(tmp_path):
    stage(tmp_path, "run_1")
    pointer = commit_run(tmp_path, manifest=manifest_for(tmp_path, "run_1"), updated_at="t")
    assert pointer.run_id == "run_1"
    assert resolve_current_run(tmp_path) == run_path(tmp_path, "run_1")
    assert not staging_path(tmp_path, "run_1").exists()


def test_the_manifest_lists_the_files_with_their_digests(tmp_path):
    stage(tmp_path, "run_1")
    manifest = manifest_for(tmp_path, "run_1")
    assert [f.path for f in manifest.files] == ["entries/part-0.parquet"]
    assert manifest.files[0].digest
    assert manifest.files[0].size_bytes == 4


def test_the_pointer_records_the_manifest_digest(tmp_path):
    stage(tmp_path, "run_1")
    manifest = manifest_for(tmp_path, "run_1")
    pointer = commit_run(tmp_path, manifest=manifest, updated_at="t")
    assert pointer.manifest_digest == manifest.digest()


def test_a_failing_invariant_blocks_publication(tmp_path):
    """Blocked, not warned about. A warning nobody reads is no property at all."""
    stage(tmp_path, "run_1")
    failure = InvariantOutcome(
        invariant_id="INV-2", statement="derives is acyclic", passed=False, violation_count=1
    )
    with pytest.raises(CommitError, match="invariants failed"):
        commit_run(
            tmp_path,
            manifest=manifest_for(tmp_path, "run_1"),
            updated_at="t",
            invariant_outcomes=[failure],
        )
    assert resolve_current_run(tmp_path) is None
    assert staging_path(tmp_path, "run_1").exists()


def test_a_published_run_is_never_overwritten(tmp_path):
    """Unique run identifiers, and a refusal rather than a silent replacement."""
    stage(tmp_path, "run_1")
    commit_run(tmp_path, manifest=manifest_for(tmp_path, "run_1"), updated_at="t")
    stage(tmp_path, "run_1")
    with pytest.raises(CommitError, match="already published"):
        commit_run(tmp_path, manifest=manifest_for(tmp_path, "run_1"), updated_at="t2")


def test_committing_a_run_that_was_never_staged_is_refused(tmp_path):
    """A manifest without its files is a description of something that is not there."""
    stage(tmp_path, "run_staged")
    orphan = manifest_for(tmp_path, "run_staged").model_copy(update={"run_id": "run_absent"})
    with pytest.raises(CommitError, match="nothing staged"):
        commit_run(tmp_path, manifest=orphan, updated_at="t")
    assert resolve_current_run(tmp_path) is None


def test_a_second_run_supersedes_the_first_and_the_first_stays_readable(tmp_path):
    """Time travel falls out of the protocol rather than being built."""
    stage(tmp_path, "run_1")
    commit_run(tmp_path, manifest=manifest_for(tmp_path, "run_1"), updated_at="t1")
    stage(tmp_path, "run_2")
    commit_run(tmp_path, manifest=manifest_for(tmp_path, "run_2"), updated_at="t2")
    assert resolve_current_run(tmp_path) == run_path(tmp_path, "run_2")
    assert run_path(tmp_path, "run_1").is_dir()


def test_an_interrupted_run_leaves_the_project_valid(tmp_path):
    """A staging directory is never resolved, so an interruption is invisible."""
    stage(tmp_path, "run_1")
    commit_run(tmp_path, manifest=manifest_for(tmp_path, "run_1"), updated_at="t1")
    stage(tmp_path, "run_2")  # abandoned mid-write
    assert resolve_current_run(tmp_path) == run_path(tmp_path, "run_1")


def test_flushing_a_file_works_on_every_platform(tmp_path):
    """Windows flushes through FlushFileBuffers, which needs a writable handle.

    A read-only open passes on Linux and macOS and fails on Windows with a bad
    file descriptor, so this asserts the primitive directly rather than leaving
    it to be inferred from five commit tests failing at once.
    """
    path = tmp_path / "durable.bin"
    path.write_bytes(b"contents")
    fsync_file(path)
    assert path.read_bytes() == b"contents"


def test_flushing_a_directory_reports_whether_it_happened(tmp_path):
    """Windows cannot open a directory handle, so durability is best-effort there."""
    import sys

    assert fsync_directory(tmp_path) is (sys.platform != "win32")
