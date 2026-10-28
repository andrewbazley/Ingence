"""Resource limits on every extraction.

A zip bomb or a path-traversal archive sitting in somebody's data directory is
not hypothetical, and neither is a file that takes an hour to parse. These are
correctness requirements, not polish, and the difference matters: polish can be
deferred, and a scan that hangs on a hostile file cannot.

Five limits, each guarding a different failure.

Depth stops an archive inside an archive inside an archive from recursing
until the stack gives out.

Ratio stops a small file that expands to a very large one. A few hundred
kilobytes of zeroes compresses to almost nothing and decompresses to gigabytes,
and the compression ratio is the only signal available before the damage is done.

Member count stops an archive with a million tiny entries, which passes both
the depth and ratio checks while still exhausting memory through sheer
bookkeeping.

Byte ceiling bounds what any single extraction may read, so an extractor
cannot be tricked into streaming a whole filesystem into memory.

Timeout bounds wall-clock time, because none of the above catches a parser
that has simply gone quadratic on adversarial input.

Alongside them, member path sanitisation. An archive member named
``../../etc/passwd`` is a real attack that is decades old. The engine never
writes extracted content anywhere, so it cannot be made to overwrite a file, but
a member name reaches locators, entry identities and displayed paths, and none of
those should be able to contain a traversal. Rejecting them at the boundary is
cheaper than auditing every consumer.

Reads at chosen offsets count against the same ceiling. Instrument formats keep
their structure away from the front of the file, an ND2's chunk map at the end
pointing back in, so their readers seek. :class:`CountingReader` gives them exact
reads, each counted against the byte ceiling before it happens, and tells a
structure pointing outside the file apart from a read the ceiling refused: the
first is a corrupt file, the second a large one.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO

    from ingence.core.settings import ExtractionSettings

__all__ = [
    "BYTES_PER_GIGABYTE",
    "CountingReader",
    "ExpansionBudget",
    "LimitExceededError",
    "Limits",
    "StructureBoundsError",
    "UnsafeMemberPathError",
    "ceiling_for",
    "file_ceiling_for",
    "safe_member_path",
]


class LimitExceededError(Exception):
    """A resource limit stopped an extraction.

    Carries which limit, because "the extraction stopped" is not actionable and
    "expansion ratio exceeded at 118 to 1" is. The value reaches the recorded
    expansion state and the run journal.
    """

    def __init__(self, limit: str, detail: str) -> None:
        self.limit = limit
        self.detail = detail
        super().__init__(f"{limit}: {detail}")


class UnsafeMemberPathError(Exception):
    """An archive member named a location outside the archive."""


class StructureBoundsError(Exception):
    """A file's own structure pointed outside the file.

    A content error rather than a limit: the file says something is at an offset
    where it has no bytes, which is a truncated or corrupt file, and reading the
    same bytes again will say the same.
    """


@dataclass(frozen=True, slots=True)
class Limits:
    """What one extraction may consume.

    Constructed from settings rather than read from them at each check, so the
    limits an extraction runs under are fixed when it starts and cannot be
    affected by a configuration change mid-run. That is also what lets an
    extraction carry its limits into a child process: this is a frozen dataclass
    of plain values, so it crosses the boundary intact and the child runs under
    the same ceilings its parent chose.
    """

    #: Most bytes any single extraction may read from a file.
    max_bytes_read: int = 4 << 20
    #: Most characters of text an extractor may retain.
    max_text_chars: int = 200_000
    #: Bytes of a text file its near-duplicate sketch reads.
    text_sketch_bytes: int = 256 << 10
    #: How many containers deep expansion may go.
    max_expansion_depth: int = 4
    #: How far expanded content may exceed its container's size.
    max_expansion_ratio: float = 100.0
    #: How many members one container may yield.
    max_expansion_members: int = 10_000
    #: Wall-clock ceiling for one file, enforced by the parent of a supervised
    #: child. Applies only to an extractor that declared ``Isolation.SUBPROCESS``:
    #: an in-process extractor still runs on past it, because there is no way to
    #: stop a thread, which is the whole reason the declaration exists.
    timeout_seconds: float = 60.0

    @staticmethod
    def from_settings(settings: ExtractionSettings, *, size_bytes: int | None = None) -> Limits:
        """Build limits from the engine's extraction settings.

        ``size_bytes`` is the file about to be read, where it is known, so the wall
        clock scales with it (:func:`ceiling_for`). Left out, the base ceiling stands,
        which is what a member of a container gets before its size is known.
        """
        return Limits(
            max_bytes_read=settings.max_sample_bytes,
            max_text_chars=settings.max_text_chars,
            text_sketch_bytes=settings.text_sketch_bytes,
            max_expansion_depth=settings.max_expansion_depth,
            max_expansion_ratio=settings.max_expansion_ratio,
            max_expansion_members=settings.max_expansion_members,
            timeout_seconds=ceiling_for(size_bytes, settings),
        )


#: One gigabyte, which the ceiling's rate is expressed per.
#:
#: Declared here rather than beside the setting on purpose: nothing on the extractor
#: path may import ``core.settings``, which reaches Pydantic and defines every record
#: type, putting 0.3 s on the start of every isolated child. A test checks that the extractor
#: path does not import ``core.settings``.
BYTES_PER_GIGABYTE = 1 << 30


def ceiling_for(size_bytes: int | None, settings: ExtractionSettings) -> float:
    """Return the wall-clock ceiling for one file's supervised reading, in seconds.

    A base plus a rate per gigabyte. A flat ceiling makes being large
    indistinguishable from being broken: a killed child is recorded as a content error,
    the outcome that says the work was attempted and something is wrong with the file or
    the parser, and a large instrument file that was reading perfectly well earns that
    verdict for its size alone. Scaling the clock with the file keeps the outcome
    meaning what it says.

    Unknown or zero size gets the base, which is the honest floor: nothing is known that
    would justify more.
    """
    gigabytes = max(size_bytes or 0, 0) / BYTES_PER_GIGABYTE
    return settings.subprocess_timeout_seconds + (
        settings.subprocess_timeout_per_gigabyte_seconds * gigabytes
    )


def file_ceiling_for(size_bytes: int | None, settings: ExtractionSettings) -> float:
    """Return the wall-clock ceiling for one file's whole reading, in seconds.

    ``file_seconds`` plus the same allowance per gigabyte :func:`ceiling_for` grants a
    supervised child, so a large file is given the time its size earns in every stage and not
    only in the child. Detection, identity, every reader and the members of a container share
    it. A file that reaches it is a content error for that file alone, and the pass goes on.
    """
    gigabytes = max(size_bytes or 0, 0) / BYTES_PER_GIGABYTE
    return settings.file_seconds + (settings.subprocess_timeout_per_gigabyte_seconds * gigabytes)


@dataclass
class ExpansionBudget:
    """Tracks one container expansion against the limits.

    Stateful and single-use. A budget shared between two expansions would let a
    small archive be starved by a large one that ran first, and the failure would
    look like a bug in the small archive.
    """

    limits: Limits
    #: Size of the container being expanded, for the ratio check.
    container_bytes: int
    depth: int = 0
    members_seen: int = 0
    bytes_produced: int = 0
    #: Reasons expansion stopped short, in the order they were hit.
    stops: list[str] = field(default_factory=list)

    def enter(self) -> ExpansionBudget:
        """Return a budget one level deeper, refusing past the depth limit."""
        if self.depth + 1 > self.limits.max_expansion_depth:
            message = f"depth {self.depth + 1} exceeds {self.limits.max_expansion_depth}"
            raise LimitExceededError("expansion_depth", message)
        return ExpansionBudget(
            limits=self.limits,
            container_bytes=self.container_bytes,
            depth=self.depth + 1,
            members_seen=self.members_seen,
            bytes_produced=self.bytes_produced,
            stops=self.stops,
        )

    def admit(self, member_bytes: int) -> None:
        """Account for one member, refusing when a limit is reached.

        Checked before the member is read rather than after, because a check
        after the fact has already paid the cost it exists to avoid.
        """
        self.members_seen += 1
        if self.members_seen > self.limits.max_expansion_members:
            message = f"{self.members_seen} members exceeds {self.limits.max_expansion_members}"
            self.stops.append(message)
            raise LimitExceededError("expansion_members", message)

        self.bytes_produced += max(0, member_bytes)
        if self.container_bytes > 0:
            ratio = self.bytes_produced / self.container_bytes
            if ratio > self.limits.max_expansion_ratio:
                message = (
                    f"expansion ratio {ratio:.1f} to 1 exceeds "
                    f"{self.limits.max_expansion_ratio:.0f} to 1"
                )
                self.stops.append(message)
                raise LimitExceededError("expansion_ratio", message)


def safe_member_path(name: str) -> str:
    """Return a container member name, or refuse one that escapes the container.

    Refuses absolute paths, drive letters, and any name that resolves above its
    own root. It refuses rather than sanitises: a silently rewritten
    name would be recorded as though the archive had contained something it did
    not, and an archive containing a traversal is a finding worth surfacing, not
    a mess to tidy away.
    """
    if not name or name in {".", ".."}:
        message = f"member name {name!r} is empty or names its own container"
        raise UnsafeMemberPathError(message)

    normalised = name.replace("\\", "/")
    if normalised.startswith("/"):
        message = f"member name {name!r} is absolute"
        raise UnsafeMemberPathError(message)
    # A Windows drive letter or a UNC share reaches outside just as an absolute
    # POSIX path does, and neither looks absolute to posixpath.
    if len(normalised) > 1 and normalised[1] == ":":
        message = f"member name {name!r} names a drive"
        raise UnsafeMemberPathError(message)

    resolved = posixpath.normpath(normalised)
    if resolved.startswith("../") or resolved == ".." or resolved.startswith("/"):
        message = f"member name {name!r} resolves outside its container"
        raise UnsafeMemberPathError(message)
    return resolved


class CountingReader:
    """Exact reads at chosen offsets, each counted against the byte ceiling.

    For formats whose structure is not at the front: an ND2 keeps its chunk map at
    the end and points back into the file, and a LIF states its header's length
    before the header, so it cannot be read forwards alone.

    Every read is exact or refused. A read the file cannot satisfy raises
    :class:`StructureBoundsError`, and one that would pass the ceiling raises
    :class:`LimitExceededError` before a byte is read, so a caller records a corrupt
    file as a content error and a large one as bounded. Neither becomes a silently
    short read, which a parser would take for data. The bounds are checked first,
    because a structure claiming more bytes than the file holds is the finding
    whatever the ceiling.

    The size is the open file's, measured when the reader is made rather than
    taken from the census, because a file can change between being indexed and
    being read.
    """

    def __init__(self, source: BinaryIO, *, limit: int) -> None:
        self._source = source
        self.limit = limit
        self.bytes_read = 0
        source.seek(0, 2)
        self.size = source.tell()

    def read_at(self, offset: int, length: int) -> bytes:
        """Return exactly ``length`` bytes starting at ``offset``."""
        if offset < 0 or length < 0 or offset + length > self.size:
            message = f"{length} bytes at offset {offset} lie outside a {self.size}-byte file"
            raise StructureBoundsError(message)
        if self.bytes_read + length > self.limit:
            message = (
                f"{length} more bytes after {self.bytes_read} would pass the "
                f"{self.limit}-byte ceiling"
            )
            raise LimitExceededError("bytes_read", message)
        self._source.seek(offset)
        data = self._source.read(length)
        if len(data) != length:
            message = f"{length} bytes at offset {offset} came back as {len(data)}"
            raise StructureBoundsError(message)
        self.bytes_read += length
        return data
