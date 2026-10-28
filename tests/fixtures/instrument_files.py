"""Instrument files built from their formats' structure, never copied from anybody's disk.

Each writer produces the smallest file its extractor reads: the structures that carry an
acquisition time, and the ones around them a reader has to walk past to reach it. Every
value is invented. The structures were read off real files, and the readers that these fixtures
test were checked against those files. What is here is each format's shape and none of anybody's
data.

- Nikon ND2, version 3. A sequence of chunks, each a magic number, a name and a
  length, and a chunk map at the end found through the file's last forty bytes. Metadata
  is Nikon's binary "lite variant": a type byte, a UTF-16 name and a value, with levels
  nesting.
- Leica LIF. A memory block holding the project's XML header in UTF-16, where each
  image lists its acquisition times as hexadecimal Windows FILETIMEs.
- Lambert Instruments LI-FLIM. A text header of ``{BLOCK}`` and ``[SECTION]`` lines
  ending at ``{END}``, the pixels straight after it, and each timestamp written as a
  FILETIME's high and low halves in decimal.
- TIFF. Classic and little-endian, one strip per page, with the tags an ImageJ or an
  OME writer adds.
"""

from __future__ import annotations

import datetime as dt
import struct

FILETIME_TICKS_BEFORE_UNIX = 116_444_736_000_000_000
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def filetime_ticks(moment: dt.datetime) -> int:
    """Return an aware datetime as a Windows FILETIME, exactly."""
    delta = moment - _EPOCH
    whole_seconds = delta.days * 86_400 + delta.seconds
    return FILETIME_TICKS_BEFORE_UNIX + whole_seconds * 10_000_000 + delta.microseconds * 10


def julian_day(moment: dt.datetime) -> float:
    """Return an aware datetime as a Julian day number, the double an ND2 stores."""
    delta = moment - _EPOCH
    return 2_440_587.5 + (delta.days + (delta.seconds + delta.microseconds / 1e6) / 86_400)


# ---------------------------------------------------------------------------
# Nikon ND2, version 3.
# ---------------------------------------------------------------------------

ND2_CHUNK_MAGIC = 0x0ABECEDA
ND2_MAP_SIGNATURE = b"ND2 CHUNK MAP SIGNATURE 0000001!"
ND2_MAP_NAME = b"ND2 FILEMAP SIGNATURE NAME 0001!"
ND2_VERSION_NAME = b"ND2 FILE SIGNATURE CHUNK NAME01!"

_LV_UINT32 = 3
_LV_DOUBLE = 6
_LV_STRING = 8
_LV_LEVEL = 11


def lv_item(kind: int, name: str, payload: bytes) -> bytes:
    """One lite-variant item: its type, its name's length in UTF-16 units, the name, the value."""
    encoded = f"{name}\x00".encode("utf-16-le")
    return bytes((kind, len(encoded) // 2)) + encoded + payload


def lv_double(name: str, value: float) -> bytes:
    return lv_item(_LV_DOUBLE, name, struct.pack("<d", value))


def lv_uint32(name: str, value: int) -> bytes:
    return lv_item(_LV_UINT32, name, struct.pack("<I", value))


def lv_string(name: str, value: str) -> bytes:
    return lv_item(_LV_STRING, name, f"{value}\x00".encode("utf-16-le"))


def lv_level(name: str, items: list[bytes], *, claimed_items: int | None = None) -> bytes:
    """A level: its item count, its length from its own first byte, its items, their offsets.

    ``claimed_items`` states a count the items do not match, for a hostile level.
    """
    encoded = f"{name}\x00".encode("utf-16-le")
    body = b"".join(items)
    length = 2 + len(encoded) + 12 + len(body)
    count = len(items) if claimed_items is None else claimed_items
    return (
        bytes((_LV_LEVEL, len(encoded) // 2))
        + encoded
        + struct.pack("<IQ", count, length)
        + body
        + bytes(8 * len(items))
    )


def nd2_chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack("<IIQ", ND2_CHUNK_MAGIC, len(name), len(data)) + name + data


def nd2_file(
    *,
    julian_day: float | None,
    frames: int = 3,
    text_date: str | None = None,
    relative_ms: tuple[float, ...] = (),
    version: bytes = b"Ver3.0",
    metadata: bytes | None = None,
    with_map: bool = True,
) -> bytes:
    """Return an ND2: a version chunk, frames, the first frame's metadata, the chunk map.

    ``metadata`` replaces the first frame's metadata chunk outright, which is how a test
    hands the reader a hostile one. ``relative_ms`` fills the acquisition-times cache.
    """
    chunks: list[tuple[bytes, bytes]] = [(ND2_VERSION_NAME, version)]
    chunks.extend((f"ImageDataSeq|{index}!".encode(), bytes(24)) for index in range(frames))
    if metadata is None and julian_day is not None:
        first = relative_ms[0] if relative_ms else 0.0
        metadata = lv_level(
            "SLxPictureMetadata",
            [lv_double("dTimeAbsolute", julian_day), lv_double("dTimeMSec", first)],
        )
    if metadata is not None:
        chunks.append((b"ImageMetadataSeqLV|0!", metadata))
    if text_date is not None:
        information = lv_level(
            "SLxImageTextInfo",
            [lv_string("TextInfoItem_4", "A. Person"), lv_string("TextInfoItem_9", text_date)],
        )
        chunks.append((b"ImageTextInfoLV!", information))
    if relative_ms:
        packed = struct.pack(f"<{len(relative_ms)}d", *relative_ms)
        chunks.append((b"CustomData|AcqTimesCache!", packed))

    body = bytearray()
    table = bytearray()
    for name, data in chunks:
        table += name + struct.pack("<QQ", len(body), len(data))
        body += nd2_chunk(name, data)
    if not with_map:
        return bytes(body)
    map_offset = len(body)
    body += nd2_chunk(ND2_MAP_NAME, bytes(table) + ND2_MAP_SIGNATURE)
    body += ND2_MAP_SIGNATURE + struct.pack("<Q", map_offset)
    return bytes(body)


# ---------------------------------------------------------------------------
# Leica LIF.
# ---------------------------------------------------------------------------

LIF_BLOCK_TAG = 0x70
LIF_MARKER = 0x2A


def lif_header(xml: str, *, claimed_characters: int | None = None) -> bytes:
    """One memory block holding an XML header. ``claimed_characters`` can overstate it."""
    encoded = xml.encode("utf-16-le")
    characters = len(encoded) // 2 if claimed_characters is None else claimed_characters
    return struct.pack("<IIBI", LIF_BLOCK_TAG, len(encoded) + 5, LIF_MARKER, characters) + encoded


def lif_file(
    *, timestamps: list[list[int]], images_without_times: int = 0, stamp_text: str | None = None
) -> bytes:
    """Return a LIF header with one image per list of FILETIMEs.

    ``images_without_times`` adds images with an empty list, as the images a FLIM project
    derives from its acquisitions have. ``stamp_text`` replaces every list's text.
    """
    lists = [*timestamps, *([] for _ in range(images_without_times))]
    images = []
    for index, stamps in enumerate(lists):
        text = " ".join(f"{stamp:x}" for stamp in stamps) if stamp_text is None else stamp_text
        images.append(
            f'<Element Name="image {index}"><Data><Image TextDescription="">'
            "<ImageDescription><Channels/><Dimensions/></ImageDescription>"
            f'<TimeStampList NumberOfTimeStamps="{len(stamps)}">{text}</TimeStampList>'
            f'</Image></Data><Memory Size="0" MemoryBlockID="MemBlock_{index}"/>'
            "<Children/></Element>"
        )
    xml = (
        '<LMSDataContainerHeader Version="2"><Element Name="project"><Data><Experiment/></Data>'
        '<Memory Size="0" MemoryBlockID="MemBlock_root"/>'
        f"<Children>{''.join(images)}</Children></Element></LMSDataContainerHeader>"
    )
    return lif_header(xml)


# ---------------------------------------------------------------------------
# Lambert Instruments LI-FLIM.
# ---------------------------------------------------------------------------


def li_flim_file(
    *,
    timestamps: list[int],
    width: int = 4,
    height: int = 3,
    phases: int = 2,
    dark_image: bool = True,
    datatype: str = "UINT16",
    stamp_values: list[str] | None = None,
) -> bytes:
    """Return an LI-FLIM file: its text header ending at ``{END}``, then zeroed pixels.

    ``stamp_values`` replaces the timestamp lines' values, for malformed ones.
    """
    points = max(1, len(timestamps))
    lines = [
        "{FLIMIMAGE}",
        "[INFO]",
        "version = 1.0",
        "compression = 0",
        "[LAYOUT]",
        f"datatype = {datatype}",
        "channels = 1",
        f"x = {width}",
        f"y = {height}",
        "z = 1",
        f"phases = {phases}",
        f"timestamps = {points}",
        "frequencies = 1",
        f"hasDarkImage = {int(dark_image)}",
    ]
    if dark_image:
        lines.extend(
            [
                "[BACKGROUND]",
                f"datatype = {datatype}",
                "channels = 1",
                f"x = {width}",
                f"y = {height}",
                "z = 1",
                "phases = 1",
                "timestamps = 1",
                "frequencies = 1",
            ]
        )
    lines.extend(["[FREQUENCIES]", "f0 = 25000000.000000"])
    if stamp_values is None:
        stamp_values = [f"{stamp >> 32} {stamp & 0xFFFFFFFF}" for stamp in timestamps]
    if stamp_values:
        lines.append("[TIMESTAMPS]")
        lines.extend(f"t{index} = {value}" for index, value in enumerate(stamp_values))
    lines.extend(
        [
            "{PARAMETERS}",
            "[ACQUISITION SETTINGS]",
            "exposureTime = 100.000000",
            "{COMMENTS}",
            "a comment somebody typed",
        ]
    )
    header = ("\n".join(lines) + "\n{END}").encode("latin-1")
    sample = {"UINT8": 1, "UINT16": 2}.get(datatype, 2)
    image = width * height * phases * points * sample
    dark = width * height * sample if dark_image else 0
    return header + bytes(image + dark)


# ---------------------------------------------------------------------------
# TIFF.
# ---------------------------------------------------------------------------

_ASCII = 2
_SHORT = 3
_LONG = 4
_BYTE = 1


def imagej_metadata(info: str) -> tuple[bytes, bytes]:
    """Return ImageJ's metadata tags for an ``Info`` property, little-endian.

    The layout ImageJ writes and tifffile reads: a header naming each property's type
    and count, then the strings in UTF-16. Returns the byte-counts tag's value and the
    metadata tag's value.
    """
    header = b"JIJI" + b"info"[::-1] + struct.pack("<I", 1)
    body = info.encode("utf-16-le")
    return struct.pack("<II", len(header), len(body)), header + body


def _pack_entries(buffer: bytearray, entries: list[tuple[int, int, int, bytes]]) -> list[bytes]:
    """Pack directory entries in tag order.

    A value of four bytes or fewer goes inline. A longer value is appended to the buffer on a word
    boundary, and the entry points at it.
    """
    packed = []
    for tag, kind, count, data in sorted(entries):
        if len(data) <= 4:
            packed.append(struct.pack("<HHI", tag, kind, count) + data.ljust(4, b"\x00"))
            continue
        if len(buffer) % 2:
            buffer += b"\x00"
        packed.append(struct.pack("<HHII", tag, kind, count, len(buffer)))
        buffer += data
    return packed


def tiff_file(
    *,
    pages: int = 1,
    width: int = 2,
    height: int = 2,
    date_time: str | None = None,
    description: str | None = None,
    imagej_info: str | None = None,
    looping: bool = False,
    text_tags: dict[int, str] | None = None,
    private_tags: dict[int, bytes] | None = None,
    exif: dict[int, str] | None = None,
) -> bytes:
    """Return a classic little-endian TIFF with one strip per page.

    Page one carries the optional ``DateTime``, ``ImageDescription`` and ImageJ tags,
    ``text_tags`` (ASCII tags by number: Software, Make, Model ...), ``private_tags`` (byte
    tags a vendor's software writes) and ``exif`` (an Exif directory holding ASCII tags).
    ``looping`` points the last page back at the first, for a reader that must stop.
    """
    if imagej_info is not None and description is None:
        description = f"ImageJ=1.54f\nimages={pages}\n"
    buffer = bytearray(8)
    buffer[:4] = b"II*\x00"
    positions: list[int] = []
    for page in range(pages):
        strip = len(buffer)
        buffer += bytes(width * height)
        entries: list[tuple[int, int, int, bytes]] = [
            (256, _SHORT, 1, struct.pack("<H", width)),
            (257, _SHORT, 1, struct.pack("<H", height)),
            (258, _SHORT, 1, struct.pack("<H", 8)),
            (259, _SHORT, 1, struct.pack("<H", 1)),
            (262, _SHORT, 1, struct.pack("<H", 1)),
            (273, _LONG, 1, struct.pack("<I", strip)),
            (277, _SHORT, 1, struct.pack("<H", 1)),
            (278, _SHORT, 1, struct.pack("<H", height)),
            (279, _LONG, 1, struct.pack("<I", width * height)),
        ]
        if page == 0 and description is not None:
            text = description.encode("latin-1") + b"\x00"
            entries.append((270, _ASCII, len(text), text))
        if page == 0 and date_time is not None:
            stamp = date_time.encode("ascii") + b"\x00"
            entries.append((306, _ASCII, len(stamp), stamp))
        if page == 0 and imagej_info is not None:
            counts, metadata = imagej_metadata(imagej_info)
            entries.append((50838, _LONG, len(counts) // 4, counts))
            entries.append((50839, _BYTE, len(metadata), metadata))
        if page == 0 and text_tags:
            for tag, value in text_tags.items():
                encoded = value.encode("latin-1") + b"\x00"
                entries.append((tag, _ASCII, len(encoded), encoded))
        if page == 0 and private_tags:
            for tag, data in private_tags.items():
                entries.append((tag, _BYTE, len(data), data))
        if page == 0 and exif:
            exif_entries: list[tuple[int, int, int, bytes]] = []
            for tag, value in exif.items():
                encoded = value.encode("latin-1") + b"\x00"
                exif_entries.append((tag, _ASCII, len(encoded), encoded))
            exif_packed = _pack_entries(buffer, exif_entries)
            if len(buffer) % 2:
                buffer += b"\x00"
            exif_at = len(buffer)
            buffer += struct.pack("<H", len(exif_packed)) + b"".join(exif_packed) + bytes(4)
            entries.append((34665, _LONG, 1, struct.pack("<I", exif_at)))
        packed = _pack_entries(buffer, entries)
        if len(buffer) % 2:
            buffer += b"\x00"
        positions.append(len(buffer))
        buffer += struct.pack("<H", len(packed)) + b"".join(packed) + bytes(4)
    struct.pack_into("<I", buffer, 4, positions[0])
    for index, position in enumerate(positions):
        following = positions[index + 1] if index + 1 < len(positions) else 0
        if looping and index == len(positions) - 1:
            following = positions[0]
        count = struct.unpack_from("<H", buffer, position)[0]
        struct.pack_into("<I", buffer, position + 2 + count * 12, following)
    return bytes(buffer)
