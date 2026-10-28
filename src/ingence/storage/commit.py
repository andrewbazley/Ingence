"""The transactional write protocol: stage, validate, fsync, rename, swap.

Every artifact the engine produces is written the same way, and readers resolve everything through
one small pointer file. Nothing else is ever a source of truth.

    write into .tmp/<run_id>.partial/
      -> validate: row counts, foreign keys, the publication invariants
      -> fsync the files, then their directories
      -> atomically rename the run directory into runs/<run_id>/
      -> atomically replace the CURRENT pointer

The engine never writes live graph files before the run completes, so an interruption cannot
expose a mixed view of two runs. The protocol also gives time travel for nothing: previous runs
stay queryable. That is what makes "the graph is a function of the four artifacts, not an artifact
itself" true in operation.

These are the ideas that table formats like Iceberg and Delta encode: immutable data files, a
manifest listing what constitutes a snapshot, and atomic commit by swapping one pointer. The engine
does not adopt those systems, which solve a distribution problem this tool does not have.

Behaviour across platforms is the part that is easy to get wrong. Renaming a directory onto an
existing directory fails on Windows and silently replaces on POSIX. Neither is wanted. So run
directories carry unique identifiers and are never overwritten, and the commit refuses if the
target exists. The pointer is a small file replaced with :func:`os.replace`, which is atomic on all
three platforms. Directories cannot be opened for fsync on Windows, so directory durability is
best-effort there, and the code says so.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ingence import SCHEMA_VERSION
from ingence.core.destinations import write_atomically
from ingence.core.ids import canonical_json, digest_file, digest_text
from ingence.core.schema import InvariantOutcome, Record
from ingence.core.versions import refuse_newer
from ingence.storage.layout import (
    CURRENT_POINTER,
    MANIFEST_FILE,
    RUNS_DIR,
    STAGING_DIR,
    owned_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "CommitError",
    "CurrentPointer",
    "Manifest",
    "ManifestFile",
    "build_manifest",
    "commit_run",
    "fsync_directory",
    "fsync_file",
    "manifest_path",
    "point_back_to",
    "read_pointer",
    "resolve_current_run",
    "run_path",
    "staging_path",
    "write_pointer",
]

#: Directory fsync is unavailable on Windows, which cannot open a directory
#: handle through the standard library. Recorded as a named constant rather than
#: an inline platform test so the limitation is greppable.
DIRECTORY_FSYNC_AVAILABLE = sys.platform != "win32"


class CommitError(Exception):
    """A run could not be published, and nothing was changed on disk."""


class ManifestFile(Record):
    """One file that constitutes a committed run."""

    #: Path relative to the run directory, with forward slashes on every
    #: platform, so a manifest written on Windows reads correctly on Linux.
    path: str
    size_bytes: int
    #: Digest of the file's bytes. A manifest that lists a file without its
    #: digest cannot detect a truncated write, which is the failure a manifest
    #: exists to catch.
    digest: str


class Manifest(Record):
    """What constitutes one committed run.

    A run is exactly the files this lists and nothing else. A file present in the
    directory but absent here is not part of the run, which is what makes a
    partially written directory recognisable rather than merely suspicious.
    """

    run_id: str
    engine_version: str
    created_at: str
    files: tuple[ManifestFile, ...] = ()
    #: Row counts per table, checked against the files before the rename.
    counts: tuple[tuple[str, int], ...] = ()
    #: Every publication invariant and how it fared. A run whose invariants
    #: failed never reaches this point, but the outcomes are recorded so a
    #: passing run can prove it was checked.
    invariants: tuple[InvariantOutcome, ...] = ()

    def digest(self) -> str:
        """Return the digest a pointer stores alongside the run identifier."""
        return digest_text(canonical_json(self.model_dump(mode="json")))


class CurrentPointer(BaseModel):
    """The one mutable thing in the layout.

    Small on purpose. A pointer that fits in a single filesystem block is
    replaced atomically by every filesystem that matters, and a reader either
    sees the whole old pointer or the whole new one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    run_id: str
    #: Digest of the run's manifest. Lets a reader detect a pointer that survived
    #: while its run did not, rather than failing obscurely much later.
    manifest_digest: str
    updated_at: str


# ---------------------------------------------------------------------------
# Paths.
# ---------------------------------------------------------------------------


def staging_path(project: Path, run_id: str) -> Path:
    """Return where a run is assembled before it is published.

    Under the project rather than the system temporary directory, so the rename
    that publishes it stays within one filesystem. A cross-filesystem rename is
    a copy, and a copy is not atomic.
    """
    return project / STAGING_DIR / f"{run_id}.partial"


def run_path(project: Path, run_id: str) -> Path:
    """Return where a published run lives."""
    return project / RUNS_DIR / run_id


def manifest_path(project: Path, run_id: str) -> Path:
    """Return the manifest of a published run."""
    return run_path(project, run_id) / MANIFEST_FILE


# ---------------------------------------------------------------------------
# Durability.
# ---------------------------------------------------------------------------


def fsync_file(path: Path) -> None:
    """Flush one file to stable storage.

    The file is opened read-write, and that is deliberate. Windows implements the flush as
    ``FlushFileBuffers``, which requires a handle with write access and fails with
    ``OSError: [Errno 9] Bad file descriptor`` on a read-only one. POSIX accepts either. So a
    read-only open passes on Linux and macOS and fails on Windows. The tests on all three platforms
    exist to catch that kind of defect at once.

    ``r+b`` opens for writing without truncating, so the file's contents are untouched.
    """
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def fsync_directory(path: Path) -> bool:
    """Flush a directory entry to stable storage where the platform allows it.

    Returns whether the flush happened. Renaming a file is only durable once the
    containing directory is flushed, so this is not optional on POSIX. Windows
    cannot open a directory handle through the standard library, so it returns
    False there and the caller records that durability is best-effort rather than
    claiming a guarantee it does not have.
    """
    if not DIRECTORY_FSYNC_AVAILABLE:
        return False
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


# ---------------------------------------------------------------------------
# The pointer.
# ---------------------------------------------------------------------------


def read_pointer(project: Path) -> CurrentPointer | None:
    """Return the current pointer, or None when the project has no committed run."""
    path = project / CURRENT_POINTER
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    # A run that a newer engine published is refused before it is read. Every read resolves
    # through this pointer, so this is the one place to stop a newer run.
    try:
        stamped = json.loads(text).get("schema_version", "")
    except (ValueError, AttributeError):
        stamped = ""
    refuse_newer(str(stamped), what=f"the current run of the project at {project}")
    return CurrentPointer.model_validate_json(text)


def write_pointer(project: Path, pointer: CurrentPointer) -> None:
    """Replace the pointer atomically.

    Written to a sibling temporary file, created exclusively under a name no other writer uses,
    flushed, then moved into place with :func:`os.replace`, which is atomic on POSIX and on
    Windows (``core.destinations.write_atomically``). A reader concurrent with this sees either
    the whole previous pointer or the whole new one, never a partial file and never no file at
    all.
    """
    write_atomically(project / CURRENT_POINTER, canonical_json(pointer.model_dump(mode="json")))


def point_back_to(project: Path, run_id: str, *, updated_at: str) -> bool:
    """Make a run published earlier current again, as when a person stops a job.

    The pointer moves back to that run, as ``workspace/restore`` moves it. The run that was current
    stays on disk, pointed to by nobody. The function returns False where the pointer already names
    the run, or the run is not there.

    The worker that ends a stopped job calls this, and so does the supervisor that stopped one. A
    rebind's census (which holds no conclusions) is then never left as the run every route serves.
    """
    current = read_pointer(project)
    if current is not None and current.run_id == run_id:
        return False
    manifest_file = manifest_path(project, run_id)
    if not manifest_file.is_file():
        return False
    manifest = Manifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
    write_pointer(
        project,
        CurrentPointer(run_id=run_id, manifest_digest=manifest.digest(), updated_at=updated_at),
    )
    return True


def resolve_current_run(project: Path) -> Path | None:
    """Return the directory of the run readers should read.

    Every read path in the engine goes through here. A reader that opens a run
    directory it found by listing has bypassed the protocol and can see a run
    that was published and then superseded halfway through its own query.
    """
    pointer = read_pointer(project)
    if pointer is None:
        return None
    return run_path(project, pointer.run_id)


# ---------------------------------------------------------------------------
# The commit.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StagedFile:
    relative: str
    absolute: Path


def _staged_files(staging: Path) -> tuple[_StagedFile, ...]:
    """Return every file in the staging directory, in canonical order.

    Canonical order rather than walk order, because directory iteration order is
    filesystem-dependent and anything derived from it would differ between two
    otherwise identical runs.
    """
    found: list[_StagedFile] = []
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            found.append(_StagedFile(relative=path.relative_to(staging).as_posix(), absolute=path))
    return tuple(sorted(found, key=lambda item: item.relative))


def build_manifest(
    staging: Path,
    *,
    run_id: str,
    engine_version: str,
    created_at: str,
    counts: Sequence[tuple[str, int]] = (),
    invariants: Sequence[InvariantOutcome] = (),
) -> Manifest:
    """Describe what a staged run contains, so the commit can verify it."""
    files = tuple(
        ManifestFile(
            path=item.relative,
            size_bytes=item.absolute.stat().st_size,
            # The digest is streamed. It equals the digest of reading the whole file at once.
            digest=digest_file(item.absolute),
        )
        for item in _staged_files(staging)
        if item.relative != MANIFEST_FILE
    )
    return Manifest(
        run_id=run_id,
        engine_version=engine_version,
        created_at=created_at,
        files=files,
        counts=tuple(counts),
        invariants=tuple(invariants),
    )


def commit_run(
    project: Path,
    *,
    manifest: Manifest,
    updated_at: str,
    invariant_outcomes: Iterable[InvariantOutcome] = (),
) -> CurrentPointer:
    """Publish a staged run, or change nothing at all.

    The order below is the whole protocol and each step exists because skipping
    it produces a specific, observed failure.

    1. Refuse if the destination exists. A run identifier is unique, so a
       collision means something is wrong that overwriting would hide.
    2. Refuse if any invariant failed. Publication is blocked, not warned about.
    3. Write the manifest into the staging directory, so the run describes itself
       before it is visible.
    4. Flush every file, then the staging directory. A rename is only durable
       once the directory entry is on stable storage.
    5. Rename the staging directory into place. This is the moment the run
       becomes visible, and it is a single atomic operation.
    6. Replace the pointer. This is the moment the run becomes *current*.

    An interruption between 5 and 6 leaves a complete, valid, unreferenced run,
    which is recoverable and harmless. An interruption anywhere earlier leaves
    the staging directory, which no reader ever resolves.
    """
    outcomes = tuple(invariant_outcomes) or manifest.invariants
    failed = [outcome for outcome in outcomes if not outcome.passed]
    if failed:
        names = ", ".join(outcome.invariant_id for outcome in failed)
        message = f"refusing to publish run {manifest.run_id}: invariants failed ({names})"
        raise CommitError(message)

    staging = staging_path(project, manifest.run_id)
    if not staging.is_dir():
        message = f"nothing staged for run {manifest.run_id} at {staging}"
        raise CommitError(message)

    destination = run_path(project, manifest.run_id)
    if destination.exists():
        message = (
            f"run {manifest.run_id} is already published at {destination}; "
            f"run directories are immutable and are never overwritten"
        )
        raise CommitError(message)

    manifest_file = staging / MANIFEST_FILE
    manifest_file.write_text(canonical_json(manifest.model_dump(mode="json")), encoding="utf-8")

    for item in _staged_files(staging):
        fsync_file(item.absolute)
    for directory in sorted({item.absolute.parent for item in _staged_files(staging)}):
        fsync_directory(directory)
    fsync_directory(staging)

    # Both the project's own, nothing on the way to either a link, before the run is moved in.
    destination = owned_path(project, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(owned_path(project, staging), destination)
    fsync_directory(destination.parent)

    pointer = CurrentPointer(
        run_id=manifest.run_id,
        manifest_digest=manifest.digest(),
        updated_at=updated_at,
    )
    write_pointer(project, pointer)
    return pointer
