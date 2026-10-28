"""Leica LIF: the acquisition time, read from the project's own XML header.

A tier-one clock, read from one header field. A LIF file opens with a memory block
holding the project's XML header in UTF-16, and each image there lists the times its data
was acquired, as hexadecimal Windows FILETIMEs in a ``TimeStampList``. A FILETIME is UTC by
definition. On a real file every one preceded the file's modification time by
minutes. One project file holds many images, so the file's clock is the earliest time
in the header, when acquisition for the project began. The latest is recorded beside it.

Standard library only, and no XML parser. Not ``readlif``, which exposes no time at all
and is GPL-licensed. The header is decoded and the two shapes read here, a timestamp list
and an image description, are found by pattern: a producer writes them, and a pattern
cannot be made to expand entities or fetch a DTD. Images whose list is empty, as the ones a
FLIM project derives from its acquisitions are, count as images without times.

Bounded by the header, not the file. A header past the byte ceiling is recorded as
bounded and offers no clock, because the earliest time might sit in the part not read.
Pixels, which follow in further memory blocks, are never read.

A second shape is seen and not used. A stamp can also be written as a ``TimeStamp``
element with ``HighInteger`` and ``LowInteger`` attributes. On a real file that
shape occurred once, on the project's root ``Experiment`` element rather than on any image,
dated after every image's stamp and before the file was last written. What event it records
is not established, so it is neither the clock nor the latest acquisition.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, TimeSource, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    ReadingDepth,
    declined,
    failed,
)
from ingence.extractors.clocks import filetime_ns
from ingence.extractors.limits import (
    CountingReader,
    LimitExceededError,
    Limits,
    StructureBoundsError,
)

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["LIF_EXTENSIONS", "LIF_MEDIA_TYPES", "LifExtractor"]

LIF_EXTENSIONS = {".lif"}

#: What detection calls a LIF when its name says so.
LIF_MEDIA_TYPES = {"application/x-leica-lif"}

#: A memory block's tag, its length, a marker byte, and the XML header's length in
#: UTF-16 code units.
_BLOCK = struct.Struct("<IIBI")
_BLOCK_TAG = 0x70
_BLOCK_MARKER = 0x2A

_TIMESTAMP_LIST = re.compile(r"<TimeStampList\b[^>]*>([^<]*)</TimeStampList>")
_IMAGE_DESCRIPTION = re.compile(r"<ImageDescription\b")
_HEADER_VERSION = re.compile(r'<LMSDataContainerHeader\b[^>]*?\bVersion="(\d{1,4})"')
_HEX_FILETIME = re.compile(r"[0-9A-Fa-f]{1,16}")


class _DeclinedError(Exception):
    """Bytes this extractor chose not to read, carrying the reason."""


@dataclass(slots=True)
class _Found:
    """What a read found before it finished or was stopped."""

    clock: int | None = None


class LifExtractor:
    """Reads a LIF header's images, their acquisition times, and the project's clock."""

    name = "leica_lif"
    version = "1"
    #: An XML header whose length the file itself declares, decoded by whoever wrote it.
    isolation = Isolation.SUBPROCESS
    #: A header reading. The project's XML header carries every acquisition time
    #: the reader wants.
    reading_depth = ReadingDepth.HEADER

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in LIF_MEDIA_TYPES:
            return True
        return bool(LIF_EXTENSIONS & context.extensions)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        readings: list[Reading] = []
        found = _Found()
        try:
            with context.open() as handle:
                reader = CountingReader(handle, limit=limits.max_bytes_read)
                try:
                    _read(reader, readings, found)
                except LimitExceededError:
                    readings.append(Reading("image.metadata_bounded", True, ValueKind.NEGATIVE))
        except _DeclinedError as exc:
            return declined(self.name, self.version, str(exc))
        except (StructureBoundsError, OSError) as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=reader.bytes_read,
            event_time_ns=found.clock,
            event_time_source=TimeSource.INSTRUMENT if found.clock is not None else None,
        )


def _read(reader: CountingReader, readings: list[Reading], found: _Found) -> None:
    """Read a LIF header into readings, recording the earliest acquisition time on ``found``."""
    exposed = ExtractionTier.MECHANICALLY_EXPOSED
    if reader.size < _BLOCK.size:
        message = "too short to hold a LIF memory block"
        raise _DeclinedError(message)
    tag, _, marker, characters = _BLOCK.unpack(reader.read_at(0, _BLOCK.size))
    if tag != _BLOCK_TAG or marker != _BLOCK_MARKER:
        message = "not a LIF file: no memory block at the start"
        raise _DeclinedError(message)
    header = reader.read_at(_BLOCK.size, characters * 2).decode("utf-16-le", errors="replace")

    version = _HEADER_VERSION.search(header)
    if version is not None:
        readings.append(Reading("image.format_version", version.group(1), ValueKind.TEXT, exposed))
    images = sum(1 for _ in _IMAGE_DESCRIPTION.finditer(header))
    stamps: list[int] = []
    unparsed = 0
    images_with_times = 0
    for listed in _TIMESTAMP_LIST.finditer(header):
        before = len(stamps)
        for token in listed.group(1).split():
            moment = filetime_ns(int(token, 16)) if _HEX_FILETIME.fullmatch(token) else None
            if moment is None:
                unparsed += 1
            else:
                stamps.append(moment)
        if len(stamps) > before:
            images_with_times += 1
    readings.extend(
        (
            Reading("image.image_count", images, ValueKind.INTEGER, exposed),
            Reading("image.images_with_timestamps", images_with_times, ValueKind.INTEGER, exposed),
            Reading("image.timestamp_count", len(stamps), ValueKind.INTEGER, exposed),
        )
    )
    if unparsed:
        readings.append(Reading("image.timestamps_unparsed", unparsed, ValueKind.INTEGER, exposed))
    if not stamps:
        readings.append(Reading("image.acquisition_time_absent", True, ValueKind.NEGATIVE))
        return
    found.clock = min(stamps)
    readings.extend(
        (
            Reading("image.acquired_ns", found.clock, ValueKind.INTEGER),
            Reading("image.acquired_resolution", "subsecond", ValueKind.TEXT),
            Reading("image.acquired_source", "TimeStampList, earliest", ValueKind.TEXT, exposed),
            Reading("image.acquired_last_ns", max(stamps), ValueKind.INTEGER),
        )
    )


@EXTRACTORS.register(
    "leica_lif",
    version="1",
    summary="Leica LIF: the project's earliest acquisition time and its image count",
    attributes={"extensions": ",".join(sorted(LIF_EXTENSIONS))},
)
def _build_leica_lif() -> LifExtractor:
    return LifExtractor()
