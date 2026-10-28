"""Compound files built from nothing, so the compound extractor is tested on bytes nobody owns.

The files are written from the specifications and are not copied from any application's output.
A version 3 compound file per [MS-CFB] has 512-byte sectors, a mini stream for every stream under
4,096 bytes, a directory whose siblings are chained in the order the specification sorts names,
and a summary property set per [MS-OLEPS]. A legacy Office file from somebody's disk would test
the extractor against one producer's habits and would carry whatever that person typed into it.
A writer over the grammar tests the container, and it holds no private data.

The writer is minimal on purpose. It has no DIFAT beyond the header's 109 entries, no version 4
sectors and no red-black balancing. Siblings form a right-leaning chain, which is a valid search
tree, and no reader is required to rebalance it. The writer covers both allocation paths, nested
storages and every property type the extractor reads. It has nothing more, so that a test
exercises the extractor and not the fixture.
"""

from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "SIGNATURE",
    "compound_file",
    "filetime_of",
    "summary_information",
    "workbook_stream",
]

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_SECTOR = 512
_MINI_SECTOR = 64
_MINI_CUTOFF = 4096
_ENTRIES_PER_FAT_SECTOR = _SECTOR // 4
_FREE = 0xFFFFFFFF
_END_OF_CHAIN = 0xFFFFFFFE
_FAT_SECTOR = 0xFFFFFFFD
_NO_STREAM = 0xFFFFFFFF
_STORAGE, _STREAM, _ROOT = 1, 2, 5

#: An unallocated directory entry: empty name, no siblings, no child, zeroes.
_UNUSED_ENTRY = (
    b"\x00" * 68 + struct.pack("<III", _NO_STREAM, _NO_STREAM, _NO_STREAM) + b"\x00" * 48
)

#: {F29F85E0-4FF9-1068-AB91-08002B27B3D9}, in the mixed-endian layout a GUID has on disk.
_FMTID_SUMMARY_INFORMATION = bytes.fromhex("e0859ff2f94f6810ab9108002b27b3d9")

_VT_I2, _VT_LPSTR, _VT_FILETIME = 0x0002, 0x001E, 0x0040

#: 100-nanosecond ticks between 1601-01-01 and 1970-01-01.
_FILETIME_AT_UNIX_EPOCH = 116_444_736_000_000_000


@dataclass
class _Node:
    name: str
    kind: int
    data: bytes = b""
    children: list[_Node] = field(default_factory=list)
    identifier: int = 0
    start: int = 0
    size: int = 0
    right: int = _NO_STREAM
    child: int = _NO_STREAM


def compound_file(tree: dict[str, object]) -> bytes:
    """Return a compound file holding a tree of storages (dicts) and streams (bytes)."""
    root = _Node("Root Entry", _ROOT, children=_nodes(tree))
    nodes: list[_Node] = []
    _number(root, nodes)
    for node in nodes:
        ordered = sorted(node.children, key=_sibling_order)
        if ordered:
            node.child = ordered[0].identifier
        for index in range(1, len(ordered)):
            ordered[index - 1].right = ordered[index].identifier

    streams = [node for node in nodes if node.kind == _STREAM]
    for node in streams:
        if not node.data:
            message = f"zero-length stream {node.name!r} is not supported by this fixture"
            raise ValueError(message)

    mini = bytearray()
    minifat: list[int] = []
    for node in (node for node in streams if len(node.data) < _MINI_CUTOFF):
        count = -(-len(node.data) // _MINI_SECTOR)
        node.start, node.size = len(minifat), len(node.data)
        minifat.extend(range(node.start + 1, node.start + count))
        minifat.append(_END_OF_CHAIN)
        mini += node.data.ljust(count * _MINI_SECTOR, b"\x00")

    large = [node for node in streams if len(node.data) >= _MINI_CUTOFF]
    directory = b"".join(_entry(node) for node in nodes)
    directory += _UNUSED_ENTRY * (-len(directory) // 128 % (_SECTOR // 128))
    minifat_bytes = (
        _padded(struct.pack(f"<{len(minifat)}I", *minifat), fill=_FREE) if minifat else b""
    )
    mini_bytes = bytes(mini).ljust(-(-len(mini) // _SECTOR) * _SECTOR, b"\x00")

    chains = [len(directory) // _SECTOR, len(minifat_bytes) // _SECTOR, len(mini_bytes) // _SECTOR]
    chains += [-(-len(node.data) // _SECTOR) for node in large]
    fat_sectors = 1
    while fat_sectors * _ENTRIES_PER_FAT_SECTOR < sum(chains) + fat_sectors:
        fat_sectors += 1

    fat = [_FAT_SECTOR] * fat_sectors
    starts: list[int] = []
    for length in chains:
        start = len(fat)
        starts.append(start if length else _END_OF_CHAIN)
        fat.extend(range(start + 1, start + length))
        if length:
            fat.append(_END_OF_CHAIN)
    fat.extend([_FREE] * (fat_sectors * _ENTRIES_PER_FAT_SECTOR - len(fat)))

    directory_start, minifat_start, mini_start = starts[0], starts[1], starts[2]
    root.start, root.size = (mini_start, len(mini)) if mini else (_END_OF_CHAIN, 0)
    for node, start in zip(large, starts[3:], strict=True):
        node.start, node.size = start, len(node.data)
    directory = b"".join(_entry(node) for node in nodes)
    directory += _UNUSED_ENTRY * (-len(directory) // 128 % (_SECTOR // 128))

    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII",
        SIGNATURE,
        b"\x00" * 16,
        0x003E,
        0x0003,
        0xFFFE,
        9,
        6,
        b"\x00" * 6,
        0,
        fat_sectors,
        directory_start,
        0,
        _MINI_CUTOFF,
        minifat_start if minifat else _END_OF_CHAIN,
        len(minifat_bytes) // _SECTOR,
        _END_OF_CHAIN,
        0,
    )
    header += struct.pack("<109I", *(list(range(fat_sectors)) + [_FREE] * (109 - fat_sectors)))

    body = struct.pack(f"<{len(fat)}I", *fat) + directory + minifat_bytes + mini_bytes
    body += b"".join(_padded(node.data, fill=0) for node in large)
    return header + body


def summary_information(
    *,
    codepage: int = 1252,
    title: str | None = None,
    author: str | None = None,
    application: str | None = None,
    created: int | None = None,
    saved: int | None = None,
) -> bytes:
    """Return a ``\\x05SummaryInformation`` stream: one property set, typed values."""
    properties: list[tuple[int, bytes]] = [(0x01, _typed_i2(codepage))]
    encoding = {1200: "utf-16-le", 65001: "utf-8"}.get(codepage, f"cp{codepage}")
    for identifier, text in ((0x02, title), (0x04, author), (0x12, application)):
        if text is not None:
            properties.append((identifier, _typed_lpstr(text.encode(encoding))))
    for identifier, filetime in ((0x0C, created), (0x0D, saved)):
        if filetime is not None:
            properties.append((identifier, struct.pack("<HHQ", _VT_FILETIME, 0, filetime)))

    index_size = 8 + 8 * len(properties)
    values = b""
    index = b""
    for identifier, value in properties:
        index += struct.pack("<II", identifier, index_size + len(values))
        values += value
    section = struct.pack("<II", index_size + len(values), len(properties)) + index + values
    preamble = struct.pack("<HHI16sI", 0xFFFE, 0, 0x00020006, b"\x00" * 16, 1)
    return preamble + _FMTID_SUMMARY_INFORMATION + struct.pack("<I", 48) + section


def workbook_stream(
    sheet_names: Sequence[str], *, encrypted: bool = False, biff8: bool = True, pad_to: int = 0
) -> bytes:
    """Return a workbook stream's globals: a BOF, optionally FILEPASS, BOUNDSHEETs, EOF."""
    records = [
        _record(0x0809, struct.pack("<HHHHII", 0x0600 if biff8 else 0x0500, 0x0005, 0, 0, 0, 0))
    ]
    if encrypted:
        records.append(_record(0x002F, struct.pack("<H", 1) + b"\x00" * 52))
    for name in sheet_names:
        if biff8:
            wide = any(ord(character) > 0xFF for character in name)
            encoded = name.encode("utf-16-le" if wide else "latin-1")
            body = struct.pack("<IBBBB", 0, 0, 0, len(name), int(wide)) + encoded
        else:
            encoded = name.encode("cp1252")
            body = struct.pack("<IBBB", 0, 0, 0, len(encoded)) + encoded
        records.append(_record(0x0085, body))
    records.append(_record(0x000A, b""))
    return b"".join(records).ljust(pad_to, b"\x00")


def filetime_of(moment: dt.datetime) -> int:
    """Return an aware datetime as a FILETIME, exactly."""
    delta = moment - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    ticks = (delta.days * 86_400 + delta.seconds) * 10_000_000 + delta.microseconds * 10
    return ticks + _FILETIME_AT_UNIX_EPOCH


def _nodes(tree: dict[str, object]) -> list[_Node]:
    nodes = []
    for name, value in tree.items():
        if isinstance(value, dict):
            nodes.append(_Node(name, _STORAGE, children=_nodes(value)))
        elif isinstance(value, bytes):
            nodes.append(_Node(name, _STREAM, data=value))
        else:
            message = f"{name!r} must be a dict of children or the bytes of a stream"
            raise TypeError(message)
    return nodes


def _number(node: _Node, into: list[_Node]) -> None:
    node.identifier = len(into)
    into.append(node)
    for child in sorted(node.children, key=_sibling_order):
        _number(child, into)


def _sibling_order(node: _Node) -> tuple[int, str]:
    """Shorter names first, then upper-cased comparison, as [MS-CFB] orders siblings."""
    return len(node.name), node.name.upper()


def _entry(node: _Node) -> bytes:
    encoded = node.name.encode("utf-16-le")
    if len(encoded) > 62:
        message = f"directory name {node.name!r} exceeds 31 characters"
        raise ValueError(message)
    start, size = (node.start, node.size) if node.kind != _STORAGE else (0, 0)
    return struct.pack(
        "<64sHBBIII16sIQQIQ",
        encoded.ljust(64, b"\x00"),
        len(encoded) + 2,
        node.kind,
        1,
        _NO_STREAM,
        node.right,
        node.child,
        b"\x00" * 16,
        0,
        0,
        0,
        start,
        size,
    )


def _padded(data: bytes, *, fill: int) -> bytes:
    remainder = -len(data) % _SECTOR
    if not remainder:
        return data
    if fill == _FREE:
        return data + struct.pack(f"<{remainder // 4}I", *([_FREE] * (remainder // 4)))
    return data + b"\x00" * remainder


def _typed_i2(value: int) -> bytes:
    return struct.pack("<HHhH", _VT_I2, 0, value if value < 32768 else value - 65536, 0)


def _typed_lpstr(encoded: bytes) -> bytes:
    terminated = encoded + b"\x00"
    return struct.pack("<HHI", _VT_LPSTR, 0, len(terminated)) + terminated.ljust(
        -(-len(terminated) // 4) * 4, b"\x00"
    )


def _record(record_type: int, body: bytes) -> bytes:
    return struct.pack("<HH", record_type, len(body)) + body
