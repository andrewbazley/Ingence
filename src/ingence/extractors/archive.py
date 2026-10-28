"""The container cascade: turning one file into the subtree it actually holds.

An archive is not a file, it is a directory that happens to be stored as one, and
an engine that treats it as an opaque blob is blind to whatever fraction of a
data pool has been zipped up. This is the extractor that opens them.

Every member re-enters the pipeline. A member is inspected exactly as a file
would be, at one greater depth, and a member that is itself a container is opened
again. That recursion is what makes a spreadsheet inside a document inside an
archive reachable, and it is bounded at every step so that the same recursion
cannot be used against the engine.

Nothing is ever written to disk. Members are read into memory, bounded, and
discarded. The engine leaves nothing behind, needs no temporary directory to
clean up, and cannot be induced by a hostile archive to write anywhere at all.
That removes the entire class of extraction vulnerabilities rather than
mitigating it.

Member names are refused, not repaired. A member called
``../../etc/passwd`` is a decades-old attack and still ships. Since nothing is
written, it cannot overwrite anything here, but the name reaches locators,
identities and displayed paths, and a silently rewritten name would record an
archive that never existed. An archive containing a traversal is a finding.

Every stop is recorded as an expansion state. The absence of a member edge
means one of two entirely different things: the container held nothing else, or
the engine did not look far enough. ``complete`` licenses a closed-world
statement about the container. ``bounded``, ``refused`` and ``failed`` do not. The INV-8 check
in ``core/invariants.py`` refuses a closed-world statement about a container whose expansion is
not ``complete``.

Encrypted members are reported and skipped. Not failed: there is nothing
wrong with the archive and nothing wrong with the engine. The content is simply
unavailable without a key nobody supplied, which is a fact about the member worth
recording.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import tarfile
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from blake3 import blake3

from ingence.core.ids import LocatorStep, canonical_json
from ingence.core.outcomes import ScanOutcome, ValueKind
from ingence.core.relations import ExpansionState
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    DerivedStream,
    ExtractorResult,
    Isolation,
    Reading,
    failed,
)
from ingence.extractors.limits import (
    ExpansionBudget,
    LimitExceededError,
    Limits,
    UnsafeMemberPathError,
    safe_member_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from ingence.extractors.base import ExtractionContext

__all__ = ["ArchiveExtractor", "MemberStream", "materialise", "members_of"]

#: Media types and extensions this extractor opens. Everything here is handled
#: by the standard library, so the base install can expand containers without a
#: single additional dependency. Broader archive support is an optional adapter.
ZIP_TYPES = {"application/zip"}
TAR_TYPES = {"application/x-tar"}
STREAM_TYPES = {
    "application/gzip": "gzip",
    "application/x-bzip2": "bzip2",
    "application/x-xz": "xz",
}
ZIP_EXTENSIONS = {".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".epub", ".whl"}
TAR_EXTENSIONS = {".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}
STREAM_EXTENSIONS = {".gz": "gzip", ".bz2": "bzip2", ".xz": "xz", ".lzma": "xz"}


@dataclass(frozen=True, slots=True)
class MemberStream:
    """One member read out of a container."""

    name: str
    payload: bytes
    #: Size the container claims, which may differ from what was read when a
    #: byte ceiling stopped the read. Both are recorded, because a difference
    #: between them is exactly the signal that content was truncated.
    declared_size: int
    ordinal: int
    scheme: str


def _hash_payload(payload: bytes) -> str:
    """Return the content identity of a derived stream.

    Hashing every derived stream is what stops the same payload being analysed
    repeatedly when it arrives through several routes, which archives make
    common: the same library vendored into four wheels is one distinct content.
    """
    return blake3(payload).hexdigest(length=32)


def _zip_members(
    context: ExtractionContext, budget: ExpansionBudget, limits: Limits
) -> Iterator[MemberStream]:
    """Yield the members of a ZIP container."""
    with context.open() as handle, zipfile.ZipFile(handle) as archive:
        for ordinal, info in enumerate(archive.infolist()):
            if info.is_dir():
                continue
            name = safe_member_path(info.filename)
            # Checked before reading, from what the container claims. A check
            # after the fact has already paid the cost it exists to avoid.
            budget.admit(info.file_size)
            if info.flag_bits & 0x1:
                message = f"member {name!r} is encrypted"
                raise LimitExceededError("encrypted", message)
            with archive.open(info) as member:
                payload = member.read(min(info.file_size, limits.max_bytes_read))
            yield MemberStream(
                name=name,
                payload=payload,
                declared_size=info.file_size,
                ordinal=ordinal,
                scheme="zip",
            )


def _tar_members(
    context: ExtractionContext, budget: ExpansionBudget, limits: Limits
) -> Iterator[MemberStream]:
    """Yield the members of a TAR container, compressed or not."""
    with context.open() as handle, tarfile.open(fileobj=handle, mode="r|*") as archive:
        for ordinal, info in enumerate(archive):
            # Only regular files. A tar link entry is an instruction to create a
            # link somewhere, and the engine has no business acting on one.
            if not info.isfile():
                continue
            name = safe_member_path(info.name)
            budget.admit(info.size)
            extracted = archive.extractfile(info)
            if extracted is None:
                continue
            with extracted:
                payload = extracted.read(min(info.size, limits.max_bytes_read))
            yield MemberStream(
                name=name,
                payload=payload,
                declared_size=info.size,
                ordinal=ordinal,
                scheme="tar",
            )


def _stream_member(
    context: ExtractionContext, budget: ExpansionBudget, limits: Limits, kind: str
) -> Iterator[MemberStream]:
    """Yield the single member of a one-stream compressor.

    gzip, bzip2 and xz compress one stream rather than holding a directory, so
    the member has no name of its own. It takes the container's name without its
    compression suffix, which is the convention every tool follows and what a
    person would expect to see.
    """
    openers = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}
    from pathlib import PurePosixPath

    stem = PurePosixPath(context.relative_path).name
    for suffix in (".gz", ".bz2", ".xz", ".lzma"):
        if stem.casefold().endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    with context.open() as handle, openers[kind](handle, "rb") as stream:  # type: ignore[operator]
        payload = stream.read(limits.max_bytes_read)
    budget.admit(len(payload))
    yield MemberStream(
        name=stem or "stream",
        payload=payload,
        declared_size=len(payload),
        ordinal=0,
        scheme=kind,
    )


def sniff_container(sample: bytes) -> str | None:
    """Return the container kind from the bytes themselves, or None.

    Content first, name second. A stated constraint of the design is that the
    engine handles unknown and misleading extensions, and dispatching on a name
    means a ``.tar.gz`` behaves differently depending on whether the compound
    suffix happened to be detected. Sniffing makes every path identical: the
    bytes say what the container is, and the name is only consulted when the
    bytes say nothing.
    """
    if sample.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if sample.startswith(b"\x1f\x8b"):
        return "gzip"
    if sample.startswith(b"BZh"):
        return "bzip2"
    if sample.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    # The tar magic sits at offset 257, which is why a tar is invisible to any
    # detector that only reads the first few bytes.
    if len(sample) >= 262 and sample[257:262] in (b"ustar",):
        return "tar"
    return None


def members_of(
    context: ExtractionContext, budget: ExpansionBudget, limits: Limits
) -> Iterator[MemberStream]:
    """Yield the members of whatever container this is.

    A compressed tar is expanded in two steps, not one. The file genuinely is
    a compressed stream whose content is a tar, so it yields one member and that
    member re-enters the pipeline and is recognised as a tar. The locator chain
    then reads gzip, then tar, then the member, which is what actually happened.
    Collapsing it into one step would produce a different chain depending on how
    the file was named, and provenance that changes shape with a filename is
    provenance nobody can rely on.
    """
    sample = context.read(512)
    kind = sniff_container(sample)

    if kind is None:
        # Nothing recognisable in the bytes. Fall back to what the file claims.
        if context.media_type in ZIP_TYPES or context.extensions & ZIP_EXTENSIONS:
            kind = "zip"
        elif context.media_type in TAR_TYPES or ".tar" in context.extensions:
            kind = "tar"
        else:
            kind = STREAM_TYPES.get(context.media_type or "")
            if kind is None:
                for suffix, name in STREAM_EXTENSIONS.items():
                    if suffix in context.extensions:
                        kind = name
                        break

    if kind == "zip":
        yield from _zip_members(context, budget, limits)
    elif kind == "tar":
        yield from _tar_members(context, budget, limits)
    elif kind in {"gzip", "bzip2", "xz"}:
        yield from _stream_member(context, budget, limits, kind)


class ArchiveExtractor:
    """Opens containers the standard library can read, within strict limits."""

    name = "archive"
    #: Version 2 also says what the members are: their extensions, and how many begin with ImageJ's
    #: region-of-interest header. That count tells a Fiji ROI archive from any other ZIP without
    #: writing or running anything. In version 3 a member's context carries its nesting of
    #: containers (the locator chain's length), not the number of folders above the archive. The
    #: extractor's depth limit then counts the nesting.
    version = "3"
    #: A container format is parsed by a library that reads structure a hostile file controls.
    #: The resource limits catch the bombs they were written for, but nothing in the process
    #: catches a decompressor that goes quadratic. The PDF, Office, spreadsheet, notebook, TIFF,
    #: compound-storage and instrument-format extractors declare the same isolation.
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in ZIP_TYPES | TAR_TYPES | set(STREAM_TYPES):
            return True
        return bool(context.extensions & (ZIP_EXTENSIONS | TAR_EXTENSIONS | set(STREAM_EXTENSIONS)))

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        if context.depth >= limits.max_expansion_depth:
            # Refusing at the limit rather than one past it, so the recorded
            # state is accurate: nothing was attempted, so nothing failed.
            return ExtractorResult(
                extractor=self.name,
                extractor_version=self.version,
                outcome=ScanOutcome.SKIPPED_BY_POLICY,
                readings=(
                    Reading(
                        "archive.expansion_state",
                        ExpansionState.BOUNDED.value,
                        ValueKind.TEXT,
                        ExtractionTier.MECHANICALLY_EXPOSED,
                    ),
                ),
                warnings=(f"depth {context.depth} is at the expansion limit",),
            )

        budget = ExpansionBudget(
            limits=limits,
            container_bytes=context.size_bytes,
            depth=context.depth,
        )
        derived: list[DerivedStream] = []
        readings: list[Reading] = []
        warnings: list[str] = []
        state = ExpansionState.COMPLETE
        bytes_read = 0
        tier = ExtractionTier.MECHANICALLY_EXPOSED
        extensions: dict[str, int] = {}
        roi_headers = 0

        try:
            for member in members_of(context, budget, limits):
                bytes_read += len(member.payload)
                extension = _member_extension(member.name)
                extensions[extension] = extensions.get(extension, 0) + 1
                roi_headers += member.payload[: len(IMAGEJ_ROI_HEADER)] == IMAGEJ_ROI_HEADER
                locator = (
                    *_parent_locator(context),
                    LocatorStep(scheme=member.scheme, target=member.name, ordinal=member.ordinal),
                )
                truncated = len(member.payload) < member.declared_size
                derived.append(
                    DerivedStream(
                        locator=locator,
                        transformation=member.scheme,
                        transformation_version=self.version,
                        ordinal=member.ordinal,
                        content_hash=_hash_payload(member.payload),
                        size_bytes=member.declared_size,
                        # Each member records its own state. A container fully
                        # enumerated can still hold one member that was too
                        # large to read whole, and those are different facts.
                        expansion_state=(
                            ExpansionState.BOUNDED if truncated else ExpansionState.COMPLETE
                        ),
                    )
                )
                if truncated:
                    warnings.append(
                        f"member {member.name!r} read to {len(member.payload)} of "
                        f"{member.declared_size} bytes"
                    )
        except LimitExceededError as exc:
            state = ExpansionState.REFUSED if exc.limit == "encrypted" else ExpansionState.BOUNDED
            warnings.append(exc.detail)
        except UnsafeMemberPathError as exc:
            # A finding, not a mess to tidy away. The container is recorded as
            # refused and the reason says exactly what was in it.
            state = ExpansionState.REFUSED
            warnings.append(str(exc))
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, ValueError) as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))

        readings.append(Reading("archive.member_count", len(derived), ValueKind.INTEGER, tier))
        readings.append(
            Reading(
                "archive.member_extensions",
                canonical_json(dict(sorted(extensions.items()))),
                ValueKind.STRUCTURE,
                tier,
            )
        )
        readings.append(Reading("archive.imagej_roi_headers", roi_headers, ValueKind.INTEGER, tier))
        readings.append(Reading("archive.expansion_state", state.value, ValueKind.TEXT, tier))
        readings.append(
            Reading(
                "archive.uncompressed_bytes",
                sum(stream.size_bytes or 0 for stream in derived),
                ValueKind.INTEGER,
                tier,
            )
        )
        if context.size_bytes > 0 and derived:
            readings.append(
                Reading(
                    "archive.expansion_ratio",
                    round(
                        sum(stream.size_bytes or 0 for stream in derived) / context.size_bytes,
                        3,
                    ),
                    ValueKind.REAL,
                    tier,
                )
            )

        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            derived=tuple(derived),
            warnings=tuple(warnings),
            bytes_read=bytes_read,
        )


#: The four bytes every region of interest ImageJ writes begins with (its ``RoiEncoder``): a
#: fact about the format, read from a member already in memory, never from a name.
IMAGEJ_ROI_HEADER = b"Iout"


def _member_extension(name: str) -> str:
    """A member's extension, case folded, and ``""`` for a member with none."""
    last = name.rsplit("/", 1)[-1]
    return last.rsplit(".", 1)[1].casefold() if "." in last.strip(".") else ""


def _parent_locator(context: ExtractionContext) -> tuple[LocatorStep, ...]:
    """Return the chain leading to this context's content.

    Empty at the top level, so a member of a file gets a one-step chain. Inside a
    container it is that container's own chain, so a member three deep carries
    the whole route: archive, then the document inside it, then the stream inside
    that. An absolute chain is what makes the member re-readable later; a
    relative one would only be meaningful to whoever produced it.
    """
    return context.locator


@EXTRACTORS.register(
    "archive",
    version="3",
    summary="opens ZIP, TAR and single-stream compressors within strict limits",
    attributes={
        "extensions": ",".join(sorted(ZIP_EXTENSIONS | TAR_EXTENSIONS | set(STREAM_EXTENSIONS)))
    },
)
def _build_archive() -> ArchiveExtractor:
    return ArchiveExtractor()


def materialise(path: Path, locator: Sequence[LocatorStep], *, limit: int) -> bytes | None:
    """Read the bytes of a stream inside a container by following its locator.

    The other half of the cascade. Expansion discovers what is inside a container
    and records where; this walks that record back to the content, so a member is
    examinable exactly as a file is without ever having been written to disk.

    That is what makes a derived entry a first-class thing rather than a note. A
    later pass, with a fresh budget, can examine members the first pass only
    discovered, and neither pass has to know about the other.

    Returns ``None`` where the chain no longer resolves: the container changed,
    the member is gone, the format is unsupported. A missing member is a fact
    about the container, not an error in the reader.
    """
    payload: bytes | None = None
    with path.open("rb") as handle:
        current: bytes = handle.read(limit)

    for step in locator:
        payload = _member_bytes(current, step, limit=limit)
        if payload is None:
            return None
        current = payload
    return payload


def _member_bytes(container: bytes, step: LocatorStep, *, limit: int) -> bytes | None:
    """Return one member's bytes from a container held in memory."""
    stream = io.BytesIO(container)
    try:
        if step.scheme == "zip":
            with (
                zipfile.ZipFile(stream) as zip_archive,
                zip_archive.open(step.target) as member,
            ):
                return bytes(member.read(limit))
        if step.scheme == "tar":
            with tarfile.open(fileobj=stream, mode="r|*") as tar_archive:
                for info in tar_archive:
                    if not info.isfile() or safe_member_path(info.name) != step.target:
                        continue
                    extracted = tar_archive.extractfile(info)
                    if extracted is None:
                        return None
                    with extracted:
                        return bytes(extracted.read(limit))
            return None
        opener = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}.get(step.scheme)
        if opener is not None:
            with opener(stream, "rb") as decompressed:  # type: ignore[operator]
                return bytes(decompressed.read(limit))
    except (
        KeyError,
        OSError,
        ValueError,
        EOFError,
        zipfile.BadZipFile,
        tarfile.TarError,
        UnsafeMemberPathError,
    ):
        return None
    return None
