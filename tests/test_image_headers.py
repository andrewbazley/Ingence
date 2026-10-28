"""A PNG, JPEG and GIF header reader in the core.

The reader is a core feature, used when a deeper dive on these files is called for. Each format's
header is read with the standard library, no pixel decoded: dimensions, bit depth or colour type, a
GIF's frame count, and any embedded time (PNG ``tIME``, EXIF ``DateTimeOriginal``) recorded as a
reading and never offered as the file's clock. Every fixture is built from the format's structure
with invented values.
"""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING

from ingence.extractors import load_builtins
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractionContext,
    ExtractorResult,
    ReadingDepth,
    reading_depth_of,
)
from ingence.extractors.limits import Limits

if TYPE_CHECKING:
    from pathlib import Path

load_builtins()


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_file(
    *, width: int = 64, height: int = 48, depth: int = 16, colour: int = 0, time: bool = True
) -> bytes:
    """A PNG from its structure: signature, IHDR, an optional tIME, one IDAT, IEND."""
    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
    body = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
    if time:
        body += _chunk(b"tIME", struct.pack(">HBBBBB", 2021, 7, 14, 9, 30, 5))
    body += _chunk(b"IDAT", zlib.compress(b"\x00" * (width + 1) * 2))
    return body + _chunk(b"IEND", b"")


def _exif(original: str) -> bytes:
    """An APP1 EXIF segment: IFD0 pointing to an EXIF IFD holding DateTimeOriginal."""
    stamp = original.encode("ascii") + b"\x00"
    # Little-endian TIFF header, IFD0 at 8 with one entry (ExifIFD pointer), then the EXIF IFD.
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
    exif_ifd_at = 8 + len(ifd0)
    assert exif_ifd_at == 26
    value_at = exif_ifd_at + 2 + 12 + 4
    exif_ifd = (
        struct.pack("<H", 1)
        + struct.pack("<HHII", 0x9003, 2, len(stamp), value_at)
        + struct.pack("<I", 0)
    )
    tiff = b"II*\x00" + struct.pack("<I", 8) + ifd0 + exif_ifd + stamp
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def jpeg_file(
    *, width: int = 320, height: int = 200, components: int = 3, original: str | None = None
) -> bytes:
    """A JPEG from its structure: SOI, an optional EXIF APP1, SOF0, SOS, data, EOI."""
    body = b"\xff\xd8"
    body += b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    if original is not None:
        body += _exif(original)
    frame = struct.pack(">BHHB", 8, height, width, components) + b"\x01\x11\x00" * components
    body += b"\xff\xc0" + struct.pack(">H", len(frame) + 2) + frame
    scan = struct.pack(">B", components) + b"\x01\x00" * components + b"\x00\x3f\x00"
    body += b"\xff\xda" + struct.pack(">H", len(scan) + 2) + scan
    return body + b"\x12\x34\xff\x00\x56" + b"\xff\xd9"


def gif_file(*, width: int = 30, height: int = 20, frames: int = 3) -> bytes:
    """A GIF89a from its structure: screen, a global table, frames each with a control block."""
    body = b"GIF89a" + struct.pack("<HHBBB", width, height, 0xF1, 0, 0)
    body += b"\x00\x00\x00\xff\xff\xff" * 2  # four colours (size code 1)
    body += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # a loop extension
    for _ in range(frames):
        body += b"\x21\xf9\x04\x00\x0a\x00\x00\x00"  # a graphics control block
        body += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0)
        body += b"\x02\x03\x84\x2d\x05\x00"  # LZW minimum size, one sub-block, terminator
    return body + b"\x3b"


def _context(path: Path, extension: str, limits: Limits | None = None) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({extension}),
        size_bytes=path.stat().st_size,
        limits=limits,
    )


def _extract(
    tmp_path: Path, name: str, data: bytes, limits: Limits | None = None
) -> ExtractorResult:
    path = tmp_path / name
    path.write_bytes(data)
    extension = "." + name.rsplit(".", 1)[1]
    extractor = EXTRACTORS.get("image_header")
    assert extractor.supports(_context(path, extension))  # type: ignore[attr-defined]
    return extractor.extract(_context(path, extension, limits))  # type: ignore[attr-defined]


def _readings(result: ExtractorResult) -> dict[str, object]:
    return {reading.key: reading.value for reading in result.readings}


def test_a_png_header_gives_its_size_depth_colour_and_its_time_as_a_reading(tmp_path) -> None:
    result = _extract(tmp_path, "zib_plot.png", png_file(depth=16, colour=0))
    read = _readings(result)
    assert (read["image.width"], read["image.height"]) == (64, 48)
    assert read["image.bit_depth"] == 16
    assert read["image.colour_type"] == "greyscale"
    assert read["image.png_time"] == "2021-07-14T09:30:05Z"
    assert result.event_time_ns is None  # a time recorded, never offered as the clock


def test_a_jpeg_header_gives_its_size_and_its_exif_original_time(tmp_path) -> None:
    result = _extract(tmp_path, "brisa.jpg", jpeg_file(original="2022:03:04 05:06:07"))
    read = _readings(result)
    assert (read["image.width"], read["image.height"]) == (320, 200)
    assert read["image.bit_depth"] == 8
    assert read["image.colour_type"] == "three components"
    assert read["image.exif_original_time"] == "2022:03:04 05:06:07"
    assert read["image.exif_original_time_zone"] == "unstated"
    assert result.event_time_ns is None


def test_a_jpeg_without_exif_reads_its_size_alone(tmp_path) -> None:
    read = _readings(_extract(tmp_path, "vorn.jpeg", jpeg_file(components=1)))
    assert read["image.colour_type"] == "greyscale"
    assert "image.exif_original_time" not in read


def test_a_gif_header_gives_its_size_and_counts_its_frames(tmp_path) -> None:
    read = _readings(_extract(tmp_path, "kelt_movie.gif", gif_file(frames=3)))
    assert (read["image.width"], read["image.height"]) == (30, 20)
    assert read["image.frame_count"] == 3
    assert read["image.bit_depth"] == 2  # four colours in the global table


def test_the_reader_declares_a_header_read_and_reads_no_pixel(tmp_path) -> None:
    extractor = EXTRACTORS.get("image_header")
    assert reading_depth_of(extractor) is ReadingDepth.HEADER
    big = png_file(width=2000, height=2000)
    result = _extract(tmp_path, "big.png", big)
    assert result.bytes_read < 200  # the signature, IHDR and tIME; never IDAT's data


def test_a_frame_count_past_the_ceiling_is_said_to_be_bounded(tmp_path) -> None:
    limits = Limits(max_bytes_read=60)
    read = _readings(_extract(tmp_path, "long.gif", gif_file(frames=40), limits))
    assert read["image.frame_count_bounded"] is True
    assert int(str(read["image.frame_count"])) < 40


def test_a_file_named_png_holding_something_else_is_declined(tmp_path) -> None:
    result = _extract(tmp_path, "fake.png", b"not an image at all")
    assert result.readings == ()


def test_a_long_animation_is_walked_only_as_far_as_the_ceiling(tmp_path) -> None:
    """A header read never pulls a whole long GIF from the disk."""
    import io

    frame = b"\x2c" + struct.pack("<HHHHB", 0, 0, 30, 20, 0) + b"\x02"
    frame += (b"\xff" + b"\x00" * 255) * 400 + b"\x00"
    body = b"GIF89a" + struct.pack("<HHBBB", 30, 20, 0x80, 0, 0) + b"\x00" * 6
    body += frame * 60 + b"\x3b"
    path = tmp_path / "long.gif"
    path.write_bytes(body)
    pulled = 0

    class Counting(io.FileIO):
        def readinto(self, buffer):  # type: ignore[no-untyped-def]
            nonlocal pulled
            count = super().readinto(buffer)
            pulled += count or 0
            return count

    class Context(ExtractionContext):
        def open(self):  # type: ignore[no-untyped-def]
            return io.BufferedReader(Counting(str(self.path), "r"))

    limits = Limits(max_bytes_read=256 << 10)
    context = Context(
        entry_id="entry-1",
        path=path,
        relative_path=path.name,
        extensions=frozenset({".gif"}),
        size_bytes=len(body),
        limits=limits,
    )
    result = EXTRACTORS.get("image_header").extract(context)  # type: ignore[attr-defined]
    read = _readings(result)
    assert read["image.frame_count_bounded"] is True
    assert pulled < 2 * limits.max_bytes_read < len(body) // 10


def test_a_file_that_ends_early_keeps_what_its_header_said(tmp_path) -> None:
    cut = b"GIF89a" + struct.pack("<HHBBB", 30, 20, 0x80, 0, 0) + b"\x00" * 6
    cut += b"\x2c" + struct.pack("<HHHHB", 0, 0, 30, 20, 0) + b"\x02" + b"\xff" + b"\x00" * 40
    read = _readings(_extract(tmp_path, "cut.gif", cut))
    assert (read["image.width"], read["image.height"]) == (30, 20)
    assert read["image.ends_early"] is True
    jpeg = jpeg_file()
    frame_end = jpeg.index(b"\xff\xda")
    # After the frame header, an EXIF segment claiming more bytes than the file holds.
    cut_jpeg = jpeg[:frame_end] + b"\xff\xe1" + struct.pack(">H", 1000) + b"Exif\x00\x00II"
    read = _readings(_extract(tmp_path, "cut.jpg", cut_jpeg))
    assert (read["image.width"], read["image.height"]) == (320, 200)
    assert read["image.ends_early"] is True
