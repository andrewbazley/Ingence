"""Instrument files built from their formats' structure, with invented values only.

The smallest file each extractor reads, written the way ``tests/fixtures/instrument_files.py``
writes it: nothing here was copied from anybody's disk. A generated pool needs them inside the
package, so a person scoring an installed release can generate one without the repository's tests.

- Nikon ND2, version 3: chunks (a magic number, a name, a length), the first frame's metadata
  in Nikon's binary "lite variant" holding the acquisition time as a Julian day, and the chunk map
  found through the file's last forty bytes.
- TIFF: classic and little-endian, one page, one strip of the pixels given.
"""

from __future__ import annotations

import datetime as dt
import struct

__all__ = ["JULIAN_DAY_OF_UNIX_EPOCH", "julian_day", "nd2_bytes", "tiff_bytes"]

#: The Julian day number at 1970-01-01T00:00Z, which is how an ND2 counts its clock.
JULIAN_DAY_OF_UNIX_EPOCH = 2_440_587.5
#: Seconds in a day, the Julian day's unit.
SECONDS_PER_DAY = 86_400
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

ND2_CHUNK_MAGIC = 0x0ABECEDA
ND2_MAP_SIGNATURE = b"ND2 CHUNK MAP SIGNATURE 0000001!"
ND2_MAP_NAME = b"ND2 FILEMAP SIGNATURE NAME 0001!"
ND2_VERSION_NAME = b"ND2 FILE SIGNATURE CHUNK NAME01!"

_LV_DOUBLE = 6
_LV_LEVEL = 11

_SHORT = 3
_LONG = 4


def julian_day(moment: dt.datetime) -> float:
    """Return an aware datetime as the Julian day number an ND2 stores."""
    delta = moment - _EPOCH
    return JULIAN_DAY_OF_UNIX_EPOCH + (delta.days + delta.seconds / SECONDS_PER_DAY)


def _lv_item(kind: int, name: str, payload: bytes) -> bytes:
    encoded = f"{name}\x00".encode("utf-16-le")
    return bytes((kind, len(encoded) // 2)) + encoded + payload


def _lv_level(name: str, items: list[bytes]) -> bytes:
    """A level: its item count, its length from its own first byte, its items, their offsets."""
    header = _lv_item(_LV_LEVEL, name, b"")
    body = b"".join(items)
    length = len(header) + 12 + len(body)
    return header + struct.pack("<IQ", len(items), length) + body + bytes(8 * len(items))


def _nd2_chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack("<IIQ", ND2_CHUNK_MAGIC, len(name), len(data)) + name + data


def nd2_bytes(moment: dt.datetime, *, frames: int = 2, pixels: bytes = b"") -> bytes:
    """Return an ND2 acquired at ``moment``: a version chunk, frames, metadata, the chunk map.

    ``pixels`` fills the frames, so two acquisitions at one moment still differ in their bytes.
    """
    frame = (pixels or bytes(8)).ljust(24, b"\x00")[:24]
    chunks: list[tuple[bytes, bytes]] = [(ND2_VERSION_NAME, b"Ver3.0")]
    chunks.extend((f"ImageDataSeq|{index}!".encode(), frame) for index in range(frames))
    metadata = _lv_level(
        "SLxPictureMetadata",
        [
            _lv_item(_LV_DOUBLE, "dTimeAbsolute", struct.pack("<d", julian_day(moment))),
            _lv_item(_LV_DOUBLE, "dTimeMSec", struct.pack("<d", 0.0)),
        ],
    )
    chunks.append((b"ImageMetadataSeqLV|0!", metadata))
    body = bytearray()
    table = bytearray()
    for name, data in chunks:
        table += name + struct.pack("<QQ", len(body), len(data))
        body += _nd2_chunk(name, data)
    map_offset = len(body)
    body += _nd2_chunk(ND2_MAP_NAME, bytes(table) + ND2_MAP_SIGNATURE)
    body += ND2_MAP_SIGNATURE + struct.pack("<Q", map_offset)
    return bytes(body)


def tiff_bytes(pixels: bytes, *, width: int = 4) -> bytes:
    """Return a classic little-endian TIFF of one page holding ``pixels`` as 8-bit grey."""
    height = max(1, len(pixels) // width)
    strip = pixels[: width * height].ljust(width * height, b"\x00")
    buffer = bytearray(b"II*\x00" + bytes(4))
    strip_offset = len(buffer)
    buffer += strip
    if len(buffer) % 2:
        buffer += b"\x00"
    entries = [
        (256, _SHORT, 1, struct.pack("<H", width)),
        (257, _SHORT, 1, struct.pack("<H", height)),
        (258, _SHORT, 1, struct.pack("<H", 8)),
        (259, _SHORT, 1, struct.pack("<H", 1)),
        (262, _SHORT, 1, struct.pack("<H", 1)),
        (273, _LONG, 1, struct.pack("<I", strip_offset)),
        (277, _SHORT, 1, struct.pack("<H", 1)),
        (278, _SHORT, 1, struct.pack("<H", height)),
        (279, _LONG, 1, struct.pack("<I", len(strip))),
    ]
    directory = len(buffer)
    buffer += struct.pack("<H", len(entries))
    for tag, kind, count, data in entries:
        buffer += struct.pack("<HHI", tag, kind, count) + data.ljust(4, b"\x00")
    buffer += bytes(4)
    struct.pack_into("<I", buffer, 4, directory)
    return bytes(buffer)
