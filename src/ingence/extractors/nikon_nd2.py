"""Nikon ND2, version 3: the acquisition time, read from the file's own chunk map.

A tier-one clock, read from one chunk field. An ND2 version 3 file is
a run of chunks, each a magic number, a name and a length, with a chunk map at the end
naming where every chunk starts. The first frame's metadata chunk,
``ImageMetadataSeqLV|0!``, holds ``dTimeAbsolute``: a Julian day number, stored as a
double. Nikon does not write which zone it means. On a real file, read as the first frame's
time in UTC, it matched the file's own text date (read as local time at the end of
acquisition) and the file's modification time, to within a second. So it is offered as
``TimeSource.INSTRUMENT``, in UTC.

The relative times are never added to it. Every frame also carries a time relative to an origin, and
that origin can precede the first frame by minutes. Adding a frame's relative time to
``dTimeAbsolute`` counts the gap twice, which is what the nd2 library does. On a real file, the sum
put acquisition after the file's modification time. The frames' relative times are recorded only as
the span they cover, from the acquisition-times cache.

The text date is a reading, never the clock. ``TextInfoItem_9`` is local wall time in
the acquiring machine's locale format, with no zone, and ``3/4/2026`` is the fourth of
March or the third of April depending on where it was written. It is recorded as written,
with its zone recorded as unstated. Nothing else in that chunk is recorded: it also names
the author and describes the sample.

Standard library only, and bounded. The nd2 library costs 0.13 s to import in every
supervised child that reads one, and is wrong on the field that matters. Reads here are
exact seeks through :class:`~ingence.extractors.limits.CountingReader`: the first chunk,
the last forty bytes, the chunk map and a few small chunks, well under a tenth of a
percent of a real acquisition. A map or chunk past the byte ceiling is recorded as bounded,
and a clock is offered only if it was reached first. Pixels are never read.

The extractor does not guess at what it has not been checked against. A legacy ND2 inside a
JPEG 2000 container is declined. A file whose metadata is XML rather than the binary
variant has no ``ImageMetadataSeqLV|0!`` chunk, and records that it carries no time rather
than reading another field in its place.
"""

from __future__ import annotations

import math
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
from ingence.extractors.clocks import julian_day_ns
from ingence.extractors.limits import (
    CountingReader,
    LimitExceededError,
    Limits,
    StructureBoundsError,
)

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["ND2_EXTENSIONS", "ND2_MEDIA_TYPES", "Nd2Extractor", "lite_variant"]

ND2_EXTENSIONS = {".nd2"}

#: What detection calls an ND2 when it recognises the chunk magic.
ND2_MEDIA_TYPES = {"application/x-nikon-nd2"}

_CHUNK_MAGIC = 0x0ABECEDA
_CHUNK_HEADER = struct.Struct("<IIQ")
_MAP_LOCATION = struct.Struct("<32sQ")
_MAP_SIGNATURE = b"ND2 CHUNK MAP SIGNATURE 0000001!"
#: The signature box a legacy ND2, a JPEG 2000 container, opens with.
_JPEG_2000_BOX = b"\x00\x00\x00\x0cjP  "

_FRAME_METADATA = b"ImageMetadataSeqLV|0!"
_TEXT_INFORMATION = b"ImageTextInfoLV!"
_ACQUISITION_TIMES = b"CustomData|AcqTimesCache!"
_FRAME_PREFIX = b"ImageDataSeq|"
_CLOCK_FIELD = "ImageMetadataSeqLV|0!/SLxPictureMetadata/dTimeAbsolute"

#: A chunk map entry whose name runs longer than this is a corrupt map, not a long name.
_MAX_CHUNK_NAME = 256
#: The version chunk holds a few bytes such as ``Ver3.0``.
_MAX_VERSION_BYTES = 32
#: How deep metadata levels may nest before the metadata is taken as hostile.
_MAX_LEVEL_DEPTH = 16
_MAX_TEXT_CHARS = 64

#: Lite-variant value types of fixed width, with the width Nikon writes each in.
_FIXED_WIDTH: dict[int, tuple[str, int]] = {
    1: ("<?", 1),
    2: ("<i", 4),
    3: ("<I", 4),
    4: ("<q", 8),
    5: ("<Q", 8),
    6: ("<d", 8),
    # An address in the writing program's memory, meaningless here but eight bytes wide.
    7: ("<Q", 8),
}
_STRING = 8
_BYTE_ARRAY = 9
_LEVEL = 11


class _DeclinedError(Exception):
    """Bytes this extractor chose not to read, carrying the reason."""


class _CorruptError(ValueError):
    """An ND2 whose structure is not as the format writes it."""


class _NotLiteVariantError(ValueError):
    """Bytes that do not decode as Nikon's binary lite variant."""


@dataclass(slots=True)
class _Found:
    """What a read found before it finished or was stopped."""

    clock: int | None = None


def lite_variant(buffer: bytes, *, count: int | None = None, depth: int = 0) -> dict[str, object]:
    """Decode Nikon's binary "lite variant" metadata into nested dictionaries.

    Each item is a type byte, its name's length in UTF-16 code units, the name, then a
    value whose width the type decides. A level holds its item count and its length
    measured from its own first byte, then its items, then an offset per item, which is
    skipped. A compressed variant, a deprecated type and anything unknown are refused,
    because a wrong guess at one width misreads every value after it. A repeated name
    keeps its first value. Every length is checked against the buffer, and a level's
    count bounds nothing but the loop, so a hostile count costs no memory.
    """
    if depth > _MAX_LEVEL_DEPTH:
        message = f"levels nest deeper than {_MAX_LEVEL_DEPTH}"
        raise _NotLiteVariantError(message)
    decoded: dict[str, object] = {}
    position = 0
    seen = 0
    while position < len(buffer) and (count is None or seen < count):
        start = position
        if position + 2 > len(buffer):
            message = f"an item header is cut off at byte {start}"
            raise _NotLiteVariantError(message)
        kind, units = buffer[position], buffer[position + 1]
        position += 2 + units * 2
        if position > len(buffer):
            message = f"an item name runs past the buffer at byte {start}"
            raise _NotLiteVariantError(message)
        name = buffer[start + 2 : position].decode("utf-16-le", errors="replace").rstrip("\x00")
        value: object
        if kind == _LEVEL:
            if position + 12 > len(buffer):
                message = f"a level header is cut off at byte {start}"
                raise _NotLiteVariantError(message)
            items, length = struct.unpack_from("<IQ", buffer, position)
            end = start + length
            if not position + 12 <= end <= len(buffer):
                message = f"a level at byte {start} claims {length} bytes the buffer does not hold"
                raise _NotLiteVariantError(message)
            value = lite_variant(buffer[position + 12 : end], count=items, depth=depth + 1)
            position = end + items * 8
        elif kind in _FIXED_WIDTH:
            layout, width = _FIXED_WIDTH[kind]
            if position + width > len(buffer):
                message = f"a value is cut off at byte {start}"
                raise _NotLiteVariantError(message)
            (value,) = struct.unpack_from(layout, buffer, position)
            position += width
        elif kind == _STRING:
            end = position
            while end + 1 < len(buffer) and buffer[end : end + 2] != b"\x00\x00":
                end += 2
            if end + 1 >= len(buffer):
                message = f"a string at byte {start} is never terminated"
                raise _NotLiteVariantError(message)
            value = buffer[position:end].decode("utf-16-le", errors="replace")
            position = end + 2
        elif kind == _BYTE_ARRAY:
            if position + 8 > len(buffer):
                message = f"a byte array's length is cut off at byte {start}"
                raise _NotLiteVariantError(message)
            (length,) = struct.unpack_from("<Q", buffer, position)
            position += 8 + length
            if position > len(buffer):
                message = f"a byte array at byte {start} runs past the buffer"
                raise _NotLiteVariantError(message)
            value = None
        else:
            message = f"type {kind} at byte {start} is not one this decodes"
            raise _NotLiteVariantError(message)
        decoded.setdefault(name, value)
        seen += 1
    return decoded


class Nd2Extractor:
    """Reads an ND2 version 3 file's chunk map, its first frame's clock and its text date."""

    name = "nikon_nd2"
    version = "1"
    #: A chunk map and a binary metadata variant, both laid out by whoever wrote the file,
    #: where a level can claim any length it likes.
    isolation = Isolation.SUBPROCESS
    #: A header reading. The chunk map and the first frame's clock sit in a prefix, and
    #: the frames themselves are never read.
    reading_depth = ReadingDepth.HEADER

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in ND2_MEDIA_TYPES:
            return True
        return bool(ND2_EXTENSIONS & context.extensions)

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
                    # What was read stays recorded, the clock included if it was reached.
                    readings.append(Reading("image.metadata_bounded", True, ValueKind.NEGATIVE))
        except _DeclinedError as exc:
            return declined(self.name, self.version, str(exc))
        except (_CorruptError, _NotLiteVariantError, StructureBoundsError, OSError) as exc:
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
    """Read an ND2 into readings, recording its clock on ``found`` as soon as it is known."""
    exposed = ExtractionTier.MECHANICALLY_EXPOSED
    if reader.size < _CHUNK_HEADER.size + _MAP_LOCATION.size:
        message = "too short to hold an ND2 chunk and the location of its map"
        raise _DeclinedError(message)
    header = reader.read_at(0, _CHUNK_HEADER.size)
    if header.startswith(_JPEG_2000_BOX):
        message = "a legacy ND2 inside a JPEG 2000 container, which is not read"
        raise _DeclinedError(message)
    magic, name_length, data_length = _CHUNK_HEADER.unpack(header)
    if magic != _CHUNK_MAGIC:
        message = "not an ND2 version 3 file: no chunk magic at the start"
        raise _DeclinedError(message)
    stated = reader.read_at(_CHUNK_HEADER.size + name_length, min(data_length, _MAX_VERSION_BYTES))
    version = stated.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    if version:
        readings.append(
            Reading("image.format_version", version.removeprefix("Ver"), ValueKind.TEXT, exposed)
        )

    location = reader.read_at(reader.size - _MAP_LOCATION.size, _MAP_LOCATION.size)
    signature, map_offset = _MAP_LOCATION.unpack(location)
    if signature != _MAP_SIGNATURE:
        message = "no chunk map signature at the end, which an acquisition stopped mid-write leaves"
        raise _CorruptError(message)
    chunks = _chunk_map(_chunk_data(reader, map_offset))
    frames = sum(1 for name in chunks if name.startswith(_FRAME_PREFIX))
    readings.append(Reading("image.frame_count", frames, ValueKind.INTEGER, exposed))

    _first_frame_clock(reader, chunks, readings, found)
    _text_date(reader, chunks, readings)
    _acquisition_span(reader, chunks, readings)


def _chunk_location(reader: CountingReader, offset: int) -> tuple[int, int]:
    """Return where a chunk's data starts and how many bytes it holds, from its header."""
    magic, name_length, data_length = _CHUNK_HEADER.unpack(
        reader.read_at(offset, _CHUNK_HEADER.size)
    )
    if magic != _CHUNK_MAGIC:
        message = f"no chunk at byte {offset}, where the chunk map says one starts"
        raise _CorruptError(message)
    return offset + _CHUNK_HEADER.size + name_length, data_length


def _chunk_data(reader: CountingReader, offset: int) -> bytes:
    start, length = _chunk_location(reader, offset)
    return reader.read_at(start, length)


def _chunk_map(table: bytes) -> dict[bytes, int]:
    """Return each chunk's name and the offset of its header, from the chunk map's data.

    An entry is a name ending in ``!`` then two little-endian 64-bit integers, the chunk's
    offset and size, and the map ends with its own signature. A name repeated keeps its
    first offset.
    """
    chunks: dict[bytes, int] = {}
    position = 0
    while position < len(table):
        end = table.find(b"!", position, position + _MAX_CHUNK_NAME)
        if end < 0:
            message = f"a chunk map entry at byte {position} has no name ending within bounds"
            raise _CorruptError(message)
        name = table[position : end + 1]
        if name == _MAP_SIGNATURE:
            break
        if end + 17 > len(table):
            message = f"a chunk map entry at byte {position} is cut off"
            raise _CorruptError(message)
        (offset,) = struct.unpack_from("<Q", table, end + 1)
        chunks.setdefault(name, offset)
        position = end + 17
    return chunks


def _first_frame_clock(
    reader: CountingReader, chunks: dict[bytes, int], readings: list[Reading], found: _Found
) -> None:
    """Record the first frame's Julian day as the clock, or that there is none."""
    if _FRAME_METADATA in chunks:
        metadata = lite_variant(_chunk_data(reader, chunks[_FRAME_METADATA]))
        picture = metadata.get("SLxPictureMetadata")
        day = picture.get("dTimeAbsolute") if isinstance(picture, dict) else None
        if isinstance(day, float):
            found.clock = julian_day_ns(day)
    if found.clock is None:
        readings.append(Reading("image.acquisition_time_absent", True, ValueKind.NEGATIVE))
        return
    readings.extend(
        (
            Reading("image.acquired_ns", found.clock, ValueKind.INTEGER),
            Reading("image.acquired_resolution", "subsecond", ValueKind.TEXT),
            Reading(
                "image.acquired_source",
                _CLOCK_FIELD,
                ValueKind.TEXT,
                ExtractionTier.MECHANICALLY_EXPOSED,
            ),
        )
    )


def _text_date(reader: CountingReader, chunks: dict[bytes, int], readings: list[Reading]) -> None:
    """Record the text date as written, with its zone unstated, and nothing else from its chunk."""
    if _TEXT_INFORMATION not in chunks:
        return
    information = lite_variant(_chunk_data(reader, chunks[_TEXT_INFORMATION]))
    level = information.get("SLxImageTextInfo")
    date = level.get("TextInfoItem_9") if isinstance(level, dict) else None
    if isinstance(date, str) and date.strip():
        readings.append(Reading("image.text_date", date.strip()[:_MAX_TEXT_CHARS], ValueKind.TEXT))
        readings.append(Reading("image.text_date_zone", "unstated", ValueKind.TEXT))


def _acquisition_span(
    reader: CountingReader, chunks: dict[bytes, int], readings: list[Reading]
) -> None:
    """Record the milliseconds the frames' relative times cover, reading only both ends."""
    if _ACQUISITION_TIMES not in chunks:
        return
    start, length = _chunk_location(reader, chunks[_ACQUISITION_TIMES])
    count = length // 8
    if count == 0:
        return
    (first,) = struct.unpack("<d", reader.read_at(start, 8))
    last = first
    if count > 1:
        (last,) = struct.unpack("<d", reader.read_at(start + (count - 1) * 8, 8))
    if math.isfinite(first) and math.isfinite(last) and last >= first:
        readings.append(Reading("image.acquisition_span_ms", float(last - first), ValueKind.REAL))


@EXTRACTORS.register(
    "nikon_nd2",
    version="1",
    summary="Nikon ND2 version 3: the first frame's acquisition time and the frame count",
    attributes={"extensions": ",".join(sorted(ND2_EXTENSIONS))},
)
def _build_nikon_nd2() -> Nd2Extractor:
    return Nd2Extractor()
