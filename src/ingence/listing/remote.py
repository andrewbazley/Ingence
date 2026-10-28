"""A listed file's bytes read where they are: sampled and whole digests, and a ZIP's member list.

The digests are byte for byte the dive's own (``identity/bytes``). ``blake3_quick`` hashes the
decimal size, then the first 64 KiB, then -- only where the file is larger than 128 KiB -- the last
64 KiB. Over HTTP that is ``Range: bytes=0-65535`` and ``Range: bytes=-65536``, 128 KiB a file
whatever its size, so a remote file and a local copy of it get the same value and the shipped
rules group them with no change. ``blake3_whole`` (and, beside it, the MD5 an archive may declare)
is computed by streaming the file through the hashes and keeping no byte.

The size hashed is the declared one, as the dive hashes the size it ``stat``-ed. A declared size
that is wrong makes a sampled digest that matches no local copy, which is what a whole read then
shows (it records the length the server actually sent).

A ZIP's member list is at its end: the end-of-central-directory record in the last 22 bytes plus
any comment, which says where the central directory starts. One more range request reads it.
Member names, sizes and CRC-32s come from the directory without a byte of the members (ZIP64
handled).

The readings enter a project as a run of their own (:func:`publish_remote_readings`): identity
observations on the listed entries, observer ``remote.<method>``, the method, its version and its
scope as a dive records them, and a failed reading as an access error. The engine uses the network
only during indexing and file exploration, and these readings are file exploration. Nothing is
fetched by publishing.

A reading whose address answered *not found* (404, 410) records, beside its access error, the
observation a listing records for a broken address (``declared.address.broken``,
:mod:`ingence.listing.ingest`), its observer the reading's. The archive's server said it, so it is
declared, and the next inference raises it as it raises a listing's own. A server's own failure, a
refusal, or a redirect the client gave up following (it follows a permanent one, and reports what
its target answered) says nothing about the address and records the error alone.

The caller must pass the status. A failed reading records a broken address only where whoever
built the :class:`RemoteReading` passed ``status=<the HTTP code>`` (``error.code`` of the
``urllib.error.HTTPError`` it caught). A failure given as text alone records the access error and
nothing more. Nothing in the engine builds a ``RemoteReading``: the readings are made by
a tool outside it and handed to :func:`publish_remote_readings`.
"""

from __future__ import annotations

import hashlib
import struct
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from blake3 import blake3

from ingence import __version__
from ingence.core.ids import canonical_json, observation_id
from ingence.core.outcomes import IdentityScope, SourceScope
from ingence.core.run_records import build_run_record
from ingence.core.schema import (
    FailureClass,
    Observation,
    RunRecord,
    ScanError,
    ValueKind,
)
from ingence.core.tiers import ExtractionTier
from ingence.jobs.provenance import current_job_id
from ingence.listing.format import DeclaredAddress, answered_state_of, parse_instant
from ingence.listing.ingest import BROKEN_ADDRESS_KEY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.core.project import Project
    from ingence.listing.http import PoliteClient

__all__ = [
    "PROBE_BYTES",
    "REMOTE_VERSION",
    "RemoteReading",
    "ZipMember",
    "publish_remote_readings",
    "remote_quick_digest",
    "remote_whole_digests",
    "remote_zip_members",
]

#: Must equal ``identity/bytes.PROBE_BYTES``. A test holds them together.
PROBE_BYTES = 64 * 1024
#: The remote reader's own version, beside each method's.
REMOTE_VERSION = "1"
_ZIP_TAIL = 22 + 65535


@dataclass(frozen=True, slots=True)
class RemoteReading:
    """One identity read from a listed file's bytes over the network, or why it could not be."""

    entry_id: str
    method: str
    method_version: str
    scope: IdentityScope
    value: str | None
    bytes_read: int = 0
    #: Why nothing was read, where nothing was: a refusal, a range served whole, a failure.
    failure: str | None = None
    #: The whole reading's own count of the bytes the server sent, for the declared size's check.
    served_bytes: int | None = None
    #: The status a failed reading answered, where a server answered one: a 404 or
    #: a 410 records the address as broken beside the failure. The caller passes it (the
    #: ``HTTPError``'s ``code``). Without it a failure records the access error alone.
    status: int | None = None


def remote_quick_digest(client: PoliteClient, url: str, size_bytes: int) -> tuple[str, int]:
    """``blake3_quick`` of a remote file of this declared size, and the bytes read for it."""
    digest = blake3()
    digest.update(str(size_bytes).encode("ascii"))
    read = 0
    if size_bytes > 0:
        head = client.request(url, byte_range=f"0-{PROBE_BYTES - 1}").body
        digest.update(head)
        read += len(head)
        if size_bytes > PROBE_BYTES * 2:
            tail = client.request(url, byte_range=f"-{PROBE_BYTES}").body
            digest.update(tail)
            read += len(tail)
    return digest.hexdigest(length=32), read


def remote_whole_digests(client: PoliteClient, url: str) -> tuple[dict[str, str], int]:
    """A remote file's ``blake3_whole``, ``md5`` and ``sha256``, streamed, and its length.

    The bytes are never kept. SHA-256 is computed beside the others. An archive's SHA-256 is
    proof, and wherever it falls short the engine hashes itself. One pass computes the method an
    archive most often declares, so a declaration is checked where the engine has the bytes.
    """
    whole = blake3()
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    served = 0

    def taken(block: bytes) -> None:
        nonlocal served
        whole.update(block)
        md5.update(block)
        sha256.update(block)
        served += len(block)

    client.request(url, sink=taken)
    return {
        "blake3_whole": whole.hexdigest(length=32),
        "md5": md5.hexdigest(),
        "sha256": sha256.hexdigest(),
    }, served


@dataclass(frozen=True, slots=True)
class ZipMember:
    name: str
    compressed_size: int
    size: int
    crc32: int

    def as_json(self) -> list[object]:
        return [self.name, self.size, self.crc32]


_EOCD = b"PK\x05\x06"
_EOCD64_LOCATOR = b"PK\x06\x07"
_CENTRAL = b"PK\x01\x02"


def _members(directory: bytes) -> list[ZipMember]:
    found: list[ZipMember] = []
    offset = 0
    while directory.startswith(_CENTRAL, offset):
        (
            _sig,
            _made,
            _need,
            _flags,
            _method,
            _time,
            _date,
            crc,
            compressed,
            size,
            name_length,
            extra_length,
            comment_length,
            _disk,
            _internal,
            _external,
            _local,
        ) = struct.unpack_from("<4sHHHHHHIIIHHHHHII", directory, offset)
        start = offset + 46
        name = directory[start : start + name_length].decode("utf-8", errors="replace")
        extra = directory[start + name_length : start + name_length + extra_length]
        if 0xFFFFFFFF in (compressed, size):
            # ZIP64: the real sizes are in the extra field, id 0x0001, in the order they overflow.
            cursor = 0
            while cursor + 4 <= len(extra):
                header_id, data_size = struct.unpack_from("<HH", extra, cursor)
                if header_id == 0x0001:
                    values = iter(struct.unpack_from(f"<{data_size // 8}Q", extra, cursor + 4))
                    size = next(values) if size == 0xFFFFFFFF else size
                    compressed = next(values) if compressed == 0xFFFFFFFF else compressed
                    break
                cursor += 4 + data_size
        found.append(ZipMember(name=name, compressed_size=compressed, size=size, crc32=crc))
        offset = start + name_length + extra_length + comment_length
    return sorted(found, key=lambda member: member.name)


def remote_zip_members(
    client: PoliteClient, url: str, size_bytes: int
) -> tuple[list[ZipMember], int]:
    """Every member of a remote ZIP from its central directory, and the bytes read for it."""
    tail = client.request(url, byte_range=f"-{min(size_bytes, _ZIP_TAIL)}").body
    read = len(tail)
    at = tail.rfind(_EOCD)
    if at < 0:
        message = f"{url}: no end of a ZIP's central directory in its last {len(tail)} bytes"
        raise ValueError(message)
    _sig, _disk, _cd_disk, _here, _total, cd_size, cd_offset, _comment = struct.unpack_from(
        "<4sHHHHIIH", tail, at
    )
    locator = tail.rfind(_EOCD64_LOCATOR, 0, at)
    if (cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF) and locator >= 0:
        (eocd64,) = struct.unpack_from("<Q", tail, locator + 8)
        record = client.request(url, byte_range=f"{eocd64}-{eocd64 + 55}").body
        read += len(record)
        cd_size, cd_offset = struct.unpack_from("<QQ", record, 40)
    start = size_bytes - len(tail)
    if cd_offset >= start:
        directory = tail[cd_offset - start : cd_offset - start + cd_size]
    else:
        directory = client.request(url, byte_range=f"{cd_offset}-{cd_offset + cd_size - 1}").body
        read += len(directory)
    return _members(directory), read


def publish_remote_readings(
    project: Project,
    readings: Sequence[RemoteReading],
    *,
    run_id: str,
    started_at: str,
) -> int:
    """Publish the readings as a run of their own over the current one.

    Every table the readings do not change is carried as the current run holds it. The readings
    are added to its observations, each failure to its scan errors, and the run record names the
    profile ``remote``. Nothing is fetched here. The function returns the number of observations
    added.
    """
    from ingence.core.invariants import GraphSnapshot, check_all
    from ingence.core.schema import Assertion, Claim
    from ingence.storage.carry import carry_run
    from ingence.storage.commit import (
        CommitError,
        build_manifest,
        commit_run,
        resolve_current_run,
        staging_path,
    )
    from ingence.storage.compact import compact_run
    from ingence.storage.index_store import FragmentWriter, record_from_row
    from ingence.storage.layout import TABLES, owned_path, table_for

    previous = resolve_current_run(project.path)
    if previous is None:
        message = "no run has been published; index before recording what was read remotely"
        raise CommitError(message)
    # The project's own staging, nothing on the way to it a link (``owned_path``).
    staging = owned_path(project.path, staging_path(project.path, run_id))
    staging.mkdir(parents=True, exist_ok=False)
    rewritten = {"observations", "scan_errors", "run", "journal_events", "quality_figures"}
    carry_run(previous, staging, rewritten=rewritten)

    from ingence.storage.catalog import open_catalog

    observations = FragmentWriter(table_for(Observation), staging / "observations")
    errors = FragmentWriter(table_for(ScanError), staging / "scan_errors")
    added: dict[str, Observation] = {}
    for reading in readings:
        if reading.value is None:
            continue
        observer = f"remote.{reading.method}"
        key = f"identity.{reading.method}"
        identifier = observation_id(
            entry=reading.entry_id, observer=observer, observer_version=REMOTE_VERSION, key=key
        )
        added[identifier] = Observation(
            observation_id=identifier,
            entry_id=reading.entry_id,
            run_id=run_id,
            observer=observer,
            observer_version=REMOTE_VERSION,
            observed_at=started_at,
            key=key,
            value_kind=ValueKind.TEXT,
            value_text=reading.value,
            extraction_tier=ExtractionTier.MECHANICALLY_EXPOSED,
            source_scope=SourceScope.CONTENT,
            identity_method=reading.method,
            identity_method_version=reading.method_version,
            identity_scope=reading.scope,
        )
    # The observations table is carried a batch at a time, never read whole. A broken address
    # recorded again by this run replaces the one it carried.
    added.update(
        (observation.observation_id, observation)
        for observation in _broken_addresses(readings, run_id=run_id, started_at=started_at)
    )
    failing = sorted({reading.entry_id for reading in readings if reading.failure is not None})
    with open_catalog(project.path, run_id=previous.name) as catalog:
        for rows in catalog.batches("SELECT * FROM observations"):
            for row in rows:
                if row["observation_id"] not in added:
                    observations.add(record_from_row(row, Observation), now=time.monotonic())
        for rows in catalog.batches("SELECT * FROM scan_errors"):
            for row in rows:
                errors.add(record_from_row(row, ScanError), now=time.monotonic())
        paths = dict(
            catalog.query(
                "SELECT entry_id, relative_path FROM entries "
                "WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
                [failing],
            ).fetchall()
        )
    for observation in added.values():
        observations.add(observation, now=time.monotonic())
    for reading in readings:
        if reading.failure is None:
            continue
        errors.add(
            ScanError(
                entry_id=reading.entry_id,
                relative_path=str(paths.get(reading.entry_id, "")),
                operation=f"remote.{reading.method}",
                error_type="RemoteReadFailed",
                message=reading.failure,
                run_id=run_id,
                failure_class=FailureClass.ACCESS,
            ),
            now=time.monotonic(),
        )
    # What the census left out and recognised travels with every later run, and the
    # vocabulary and packs it read: the parent's record is carried, with this run's own identity.
    with open_catalog(project.path) as catalog:
        parent_rows = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    carried_fields: dict[str, Any] = {}
    if parent_rows:
        parent = record_from_row(parent_rows[0], RunRecord)
        carried_fields = {
            name: getattr(parent, name)
            for name in (
                "excluded",
                "recognised",
                "mentions",
                "dictionary_hash",
                "packs",
            )
            if hasattr(parent, name)
        }
    run = FragmentWriter(table_for(RunRecord), staging / "run")
    run.add(
        build_run_record(
            run_id=run_id,
            parent_run_id=previous.name,
            started_at=started_at,
            profile="remote",
            settings=project.settings,
            project=project,
            published=True,
            job_id=current_job_id(),
            **carried_fields,
        ),
        now=time.monotonic(),
    )
    for writer in (observations, errors, run):
        writer.close(now=time.monotonic())
    with open_catalog(project.path, run_id=previous.name) as catalog:
        snapshot = GraphSnapshot(
            assertions=tuple(
                record_from_row(row, Assertion)
                for rows in catalog.batches("SELECT * FROM assertions")
                for row in rows
            ),
            claims=tuple(
                record_from_row(row, Claim)
                for rows in catalog.batches("SELECT * FROM claims")
                for row in rows
            ),
        )
    outcomes = check_all(snapshot)
    compact_run(staging, tuple(spec for spec in TABLES if not spec.spans_runs))
    from ingence.quality.publish import write_quality

    write_quality(project.path, staging, run_id=run_id, phase="listing")
    manifest = build_manifest(
        staging,
        run_id=run_id,
        engine_version=__version__,
        created_at=started_at,
        invariants=outcomes,
    )
    commit_run(project.path, manifest=manifest, updated_at=started_at)
    return len(added)


def _broken_addresses(
    readings: Sequence[RemoteReading], *, run_id: str, started_at: str
) -> list[Observation]:
    """The observation each failed reading whose address answered no file records, one per entry
    and method, in canonical order."""
    at = parse_instant(started_at) or 0
    found: dict[str, Observation] = {}
    for reading in readings:
        state = answered_state_of(reading.status) if reading.failure is not None else None
        if state is None:
            continue
        observer = f"remote.{reading.method}"
        stated = DeclaredAddress(state=state, at_ns=at, found_by=observer, status=reading.status)
        identifier = observation_id(
            entry=reading.entry_id,
            observer=observer,
            observer_version=REMOTE_VERSION,
            key=BROKEN_ADDRESS_KEY,
        )
        found[identifier] = Observation(
            observation_id=identifier,
            entry_id=reading.entry_id,
            run_id=run_id,
            observer=observer,
            observer_version=REMOTE_VERSION,
            observed_at=started_at,
            key=BROKEN_ADDRESS_KEY,
            value_kind=ValueKind.STRUCTURE,
            value_structure=canonical_json(stated.as_json()),
            extraction_tier=ExtractionTier.LITERAL,
            # The archive's server said it: neither read from the file's bytes nor said by the
            # person using the engine.
            source_scope=SourceScope.DECLARED,
        )
    return [found[identifier] for identifier in sorted(found)]
