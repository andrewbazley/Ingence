"""PNG, JPEG and GIF: what each header says, read with the standard library, no pixel decoded.

This extractor is part of the core, behind the registry like every extractor. It declares a
header read, for use when a deeper dive on these files is called for.

What is read:

- PNG: the ``IHDR`` chunk (width, height, bit depth, colour type) and a ``tIME`` chunk where one
  comes before the image data (the last time the image was modified, in UTC by the format's own
  definition). Chunks are walked by their lengths and the walk stops at the first ``IDAT``, so the
  compressed image is never read.
- JPEG: the frame header (``SOF0`` to ``SOF15``, not the tables): width, height, sample
  precision and the number of components; and an ``APP1`` EXIF segment's ``DateTimeOriginal`` (in
  the EXIF IFD) or, failing it, ``DateTime`` (in IFD0), both written without a zone. Segments are
  walked by their lengths and the walk stops at the start of the scan.
- GIF: the logical screen (width, height, the global colour table's size, read as a bit depth)
  and the frame count, by walking every block: an image descriptor is a frame, and each block's
  data sub-blocks are skipped by their lengths, never decompressed.

A time is a reading and never the clock, as in the TIFF extractor. A PNG's ``tIME`` is when the
image was last changed, which a re-save moves, and EXIF's times carry no zone. So each is recorded
as what it is and no filesystem or name time is displaced by it.

The reads are bounded. Every read goes through the counting reader under the extraction's ceiling. A
GIF whose blocks pass the ceiling reports the frames counted so far and says the count is bounded.
An EXIF IFD pointer is followed once and its entries capped, so a hostile file cannot loop.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, ValueKind
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
from ingence.extractors.limits import (
    CountingReader,
    LimitExceededError,
    Limits,
    StructureBoundsError,
)

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["HEADER_EXTENSIONS", "HEADER_MEDIA_TYPES", "ImageHeaderExtractor"]

HEADER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".jpe", ".gif"}
HEADER_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_COLOUR = {
    0: "greyscale",
    2: "colour",
    3: "indexed colour",
    4: "greyscale with alpha",
    6: "colour with alpha",
}
_JPEG_COMPONENTS = {1: "greyscale", 3: "three components", 4: "four components"}
#: Start-of-frame markers: every SOFn but the tables and the arithmetic conditioning marker.
_JPEG_FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_JPEG_SCAN = 0xDA
_JPEG_END = 0xD9
_EXIF_POINTER = 0x8769
_DATE_TIME_ORIGINAL = 0x9003
_DATE_TIME = 0x0132
#: More entries than this in one EXIF directory is a corrupt directory, not a rich one.
_MAX_IFD_ENTRIES = 512
#: More chunks or segments than this before the image data is a corrupt header.
_MAX_STEPS = 10_000
_MAX_TIME_CHARS = 32


class _DeclinedError(Exception):
    """Bytes this extractor chose not to read, carrying the reason."""


class _CorruptError(ValueError):
    """A file whose structure is not as its format writes it."""


class ImageHeaderExtractor:
    """Reads a PNG's, a JPEG's or a GIF's header: size, depth, frames and times, as readings."""

    name = "image_header"
    version = "1"
    #: Linear walks whose every step moves forward by a length the file states, bounded by the
    #: byte ceiling and a step cap, and one EXIF pointer followed once: nothing here can hang.
    isolation = Isolation.IN_PROCESS
    #: A header reading: the format's own header and metadata, never a pixel.
    reading_depth = ReadingDepth.HEADER

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in HEADER_MEDIA_TYPES:
            return True
        return bool(HEADER_EXTENSIONS & {e.casefold() for e in context.extensions})

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        readings: list[Reading] = []
        try:
            with context.open() as handle:
                reader = CountingReader(handle, limit=limits.max_bytes_read)
                try:
                    _read(reader, readings)
                except LimitExceededError:
                    readings.append(Reading("image.metadata_bounded", True, ValueKind.NEGATIVE))
                except StructureBoundsError:
                    # A file that ends early keeps what its header said. The size and depth were
                    # read before the structure ran past the end.
                    if not readings:
                        raise
                    readings.append(Reading("image.ends_early", True, ValueKind.NEGATIVE))
        except _DeclinedError as exc:
            return declined(self.name, self.version, str(exc))
        except (_CorruptError, StructureBoundsError, OSError, struct.error) as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=reader.bytes_read,
        )


def _read(reader: CountingReader, readings: list[Reading]) -> None:
    """Read whichever of the three formats the file's first bytes say it is."""
    if reader.size < 6:
        message = "too short to be a PNG, a JPEG or a GIF"
        raise _DeclinedError(message)
    head = reader.read_at(0, 6)
    if head[:2] == b"\xff\xd8":
        readings.append(_format_reading("jpeg"))
        _jpeg(reader, readings)
    elif head in (b"GIF87a", b"GIF89a"):
        readings.append(_format_reading("gif"))
        _gif(reader, readings)
    elif reader.size >= 8 and head + reader.read_at(6, 2) == _PNG_SIGNATURE:
        readings.append(_format_reading("png"))
        _png(reader, readings)
    else:
        message = "not a PNG, a JPEG or a GIF: no signature this reads"
        raise _DeclinedError(message)


def _format_reading(name: str) -> Reading:
    return Reading("image.header_format", name, ValueKind.TEXT, ExtractionTier.MECHANICALLY_EXPOSED)


def _size(readings: list[Reading], width: int, height: int) -> None:
    readings.append(Reading("image.width", width, ValueKind.INTEGER))
    readings.append(Reading("image.height", height, ValueKind.INTEGER))


def _png(reader: CountingReader, readings: list[Reading]) -> None:
    """Walk a PNG's chunks up to its image data: IHDR first, and a tIME where one is."""
    offset = 8
    for _ in range(_MAX_STEPS):
        if offset + 8 > reader.size:
            return
        length, kind = struct.unpack(">I4s", reader.read_at(offset, 8))
        if kind in (b"IDAT", b"IEND"):
            return
        if kind == b"IHDR":
            if length < 13:
                message = "an IHDR chunk shorter than thirteen bytes"
                raise _CorruptError(message)
            width, height, depth, colour = struct.unpack(">IIBB", reader.read_at(offset + 8, 10))
            _size(readings, width, height)
            readings.append(Reading("image.bit_depth", depth, ValueKind.INTEGER))
            readings.append(Reading("image.colour_type", _PNG_COLOUR.get(colour, f"type {colour}")))
        elif kind == b"tIME" and length == 7:
            year, month, day, hour, minute, second = struct.unpack(
                ">HBBBBB", reader.read_at(offset + 8, 7)
            )
            stamp = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"
            readings.append(Reading("image.png_time", stamp))
            readings.append(
                Reading(
                    "image.png_time_meaning",
                    "last modified (the format's tIME chunk, UTC)",
                    ValueKind.TEXT,
                    ExtractionTier.MECHANICALLY_EXPOSED,
                )
            )
        offset += 12 + length
    message = "more chunks before the image data than any PNG writes"
    raise _CorruptError(message)


def _jpeg(reader: CountingReader, readings: list[Reading]) -> None:
    """Walk a JPEG's segments to the scan: the frame header and an EXIF segment's times."""
    offset = 2
    framed = False
    for _ in range(_MAX_STEPS):
        if offset + 4 > reader.size:
            return
        marker = reader.read_at(offset, 2)
        if marker[0] != 0xFF:
            message = f"a segment at byte {offset} does not begin with a marker"
            raise _CorruptError(message)
        code = marker[1]
        if code == 0xFF:  # a fill byte before the marker
            offset += 1
            continue
        if code in (_JPEG_SCAN, _JPEG_END):
            return
        (length,) = struct.unpack(">H", reader.read_at(offset + 2, 2))
        if length < 2:
            message = f"a segment at byte {offset} states a length below two"
            raise _CorruptError(message)
        if code in _JPEG_FRAMES and not framed and length >= 8:
            precision, height, width, components = struct.unpack(
                ">BHHB", reader.read_at(offset + 4, 6)
            )
            _size(readings, width, height)
            readings.append(Reading("image.bit_depth", precision, ValueKind.INTEGER))
            readings.append(
                Reading(
                    "image.colour_type",
                    _JPEG_COMPONENTS.get(components, f"{components} components"),
                )
            )
            framed = True
        elif code == 0xE1 and length > 8:
            body = reader.read_at(offset + 4, length - 2)
            if body.startswith(b"Exif\x00\x00"):
                _exif_times(body[6:], readings)
        offset += 2 + length
    message = "more segments before the scan than any JPEG writes"
    raise _CorruptError(message)


def _exif_times(tiff: bytes, readings: list[Reading]) -> None:
    """Record DateTimeOriginal from the EXIF IFD, or DateTime from IFD0, zone unstated."""
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None or len(tiff) < 8:
        return
    (first,) = struct.unpack(order + "I", tiff[4:8])
    ifd0 = _ifd(tiff, order, first)
    found = None
    pointer = ifd0.get(_EXIF_POINTER)
    if pointer is not None and pointer[0] == 4 and pointer[1] == 1:
        (exif_at,) = struct.unpack(order + "I", pointer[2])
        found = _ascii(tiff, order, _ifd(tiff, order, exif_at).get(_DATE_TIME_ORIGINAL))
        field = "EXIF DateTimeOriginal"
    if found is None:
        found = _ascii(tiff, order, ifd0.get(_DATE_TIME))
        field = "EXIF DateTime"
    if found is None:
        return
    readings.append(Reading("image.exif_original_time", found))
    readings.append(Reading("image.exif_original_time_zone", "unstated"))
    readings.append(
        Reading(
            "image.exif_original_time_field",
            field,
            ValueKind.TEXT,
            ExtractionTier.MECHANICALLY_EXPOSED,
        )
    )


def _ifd(tiff: bytes, order: str, offset: int) -> dict[int, tuple[int, int, bytes]]:
    """One EXIF directory's entries by tag: type, count and the four-byte field."""
    if offset <= 0 or offset + 2 > len(tiff):
        return {}
    (count,) = struct.unpack_from(order + "H", tiff, offset)
    if count > _MAX_IFD_ENTRIES or offset + 2 + 12 * count > len(tiff):
        return {}
    entries: dict[int, tuple[int, int, bytes]] = {}
    for index in range(count):
        start = offset + 2 + 12 * index
        tag, kind, number = struct.unpack_from(order + "HHI", tiff, start)
        entries.setdefault(tag, (kind, number, tiff[start + 8 : start + 12]))
    return entries


def _ascii(tiff: bytes, order: str, entry: tuple[int, int, bytes] | None) -> str | None:
    """An ASCII entry's text, where it is one and lies inside the segment."""
    if entry is None or entry[0] != 2 or entry[1] == 0:
        return None
    if entry[1] <= 4:
        raw = entry[2][: entry[1]]
    else:
        (at,) = struct.unpack(order + "I", entry[2])
        if at + entry[1] > len(tiff):
            return None
        raw = tiff[at : at + entry[1]]
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    return text[:_MAX_TIME_CHARS] or None


def _gif(reader: CountingReader, readings: list[Reading]) -> None:
    """Read a GIF's logical screen and count its frames by walking its blocks."""
    width, height, packed = struct.unpack("<HHB", reader.read_at(6, 5))
    _size(readings, width, height)
    offset = 13
    if packed & 0x80:
        size_code = packed & 0x07
        readings.append(Reading("image.bit_depth", size_code + 1, ValueKind.INTEGER))
        offset += 3 * (2 ** (size_code + 1))
    frames = 0
    bounded = ended = False
    try:
        frames = _gif_frames(reader, offset)
    except _GifBoundedError as exc:
        frames, bounded = exc.frames, True
    except _GifEndsEarlyError as exc:
        frames, ended = exc.frames, True
    readings.append(
        Reading("image.frame_count", frames, ValueKind.INTEGER, ExtractionTier.MECHANICALLY_EXPOSED)
    )
    if bounded:
        readings.append(Reading("image.frame_count_bounded", True, ValueKind.NEGATIVE))
    if ended:
        readings.append(Reading("image.ends_early", True, ValueKind.NEGATIVE))


class _GifEndsEarlyError(Exception):
    """The file ended inside a block, carrying the frames counted so far."""

    def __init__(self, frames: int) -> None:
        super().__init__(f"{frames} frames counted before the file ended")
        self.frames = frames


class _GifBoundedError(Exception):
    """The walk reached the byte ceiling, carrying the frames counted so far."""

    def __init__(self, frames: int) -> None:
        super().__init__(f"{frames} frames counted before the ceiling")
        self.frames = frames


def _gif_frames(reader: CountingReader, offset: int) -> int:
    """Count image descriptors, skipping every block's data sub-blocks by their lengths.

    Bounded by position as well as by bytes. A walk reads a byte or two per block, but seeking
    through a long animation pulls the whole file from the disk. So the walk stops at the byte
    ceiling's offset and says the count is bounded. That keeps a header read a header read.
    """
    frames = 0
    window = reader.limit
    try:
        while offset < reader.size:
            if offset >= window:
                raise _GifBoundedError(frames)
            (introducer,) = reader.read_at(offset, 1)
            if introducer == 0x3B:  # the trailer
                return frames
            if introducer == 0x21:  # an extension: its label, then data sub-blocks
                offset = _skip_sub_blocks(reader, offset + 2)
            elif introducer == 0x2C:  # an image descriptor: one frame
                frames += 1
                (packed,) = reader.read_at(offset + 9, 1)
                offset += 10
                if packed & 0x80:
                    offset += 3 * (2 ** ((packed & 0x07) + 1))
                offset = _skip_sub_blocks(reader, offset + 1)  # past the LZW minimum size
            else:
                message = f"a block at byte {offset} begins with {introducer:#04x}"
                raise _CorruptError(message)
    except LimitExceededError as exc:
        raise _GifBoundedError(frames) from exc
    except StructureBoundsError:
        raise _GifEndsEarlyError(frames) from None
    return frames


def _skip_sub_blocks(reader: CountingReader, offset: int) -> int:
    """Return the offset past a run of data sub-blocks, reading only their length bytes."""
    while True:
        (length,) = reader.read_at(offset, 1)
        offset += 1 + length
        if length == 0:
            return offset


@EXTRACTORS.register(
    "image_header",
    version="1",
    summary="PNG, JPEG and GIF headers: size, depth, frames and times, as readings",
    attributes={"extensions": ",".join(sorted(HEADER_EXTENSIONS))},
)
def _build_image_header() -> ImageHeaderExtractor:
    return ImageHeaderExtractor()
