"""Documents, tables and instrument headers built from their formats' structure, invented values.

The companions of :mod:`ingence.evaluation.formats` for the sample collection
(:mod:`ingence.evaluation.sample_lab`): a PDF, the three Office packages, a notebook, a multi-page
TIFF, a MATLAB data file's header, a header shaped like a Zeiss CZI, and the files an operating
system or an editor leaves behind. Nothing here was copied from anybody's disk, and no library saved
any of it: a library writes the properties it defaults to (the time it ran, the name of its user),
and these bytes must be the same on every machine and on every day.

Deterministic by construction. A zip package stores its parts uncompressed, with each part
dated by the moment given and marked as written on a Unix system, so no compressor's version and no
platform changes a byte. A PDF and a PNG are written the same way (stored, never deflated).

Said plainly, because a reader of the collection will ask.

- :func:`czi_header_bytes` writes the first segment of a Zeiss CZI file with invented values and the
  directory and metadata positions pointing past the end of the file. It is not a readable CZI:
  no image can be opened from it. It is there so that a name, a signature and a size look like a
  microscope's file to a program that looks only at those.
- :func:`mat_file_bytes` is a MATLAB level 5 data file holding its header and no variable.
- :func:`ds_store_bytes` and :func:`thumbs_db_bytes` hold the signature of the Finder's folder file
  and a minimal, empty compound file (the container a Windows thumbnail cache is); neither lists a
  thing.
"""

from __future__ import annotations

import datetime as dt
import json
import struct
import zipfile
import zlib
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "ab1_bytes",
    "avi_bytes",
    "czi_header_bytes",
    "dna_bytes",
    "docx_bytes",
    "ds_store_bytes",
    "fcs_bytes",
    "gif_bytes",
    "jar_bytes",
    "jpeg_bytes",
    "lif_header_bytes",
    "mat_file_bytes",
    "notebook_bytes",
    "office_lock_bytes",
    "ole_document_bytes",
    "pdf_bytes",
    "png_bytes",
    "pptx_bytes",
    "svg_bytes",
    "thumbs_db_bytes",
    "tiff_stack_bytes",
    "xlsx_bytes",
    "zip_bytes",
]

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SLIDES = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
_EXTENDED = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

#: The application every package here says made it.
APPLICATION = "LibreOffice"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _w3cdtf(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Zip packages.
# ---------------------------------------------------------------------------


def _zip(parts: Mapping[str, str | bytes], moment: dt.datetime) -> bytes:
    """A zip of ``parts`` stored uncompressed, every part dated ``moment`` and made on Unix."""
    stamp = moment.astimezone(dt.UTC)
    date_time = (max(stamp.year, 1980), stamp.month, stamp.day, stamp.hour, stamp.minute, 0)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def _core_part(*, title: str, creator: str, moment: dt.datetime, modified: dt.datetime) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_escape(title)}</dc:title><dc:creator>{_escape(creator)}</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{_w3cdtf(moment)}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{_w3cdtf(modified)}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _app_part() -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="{_EXTENDED}">'
        f"<Application>{APPLICATION}</Application><AppVersion>16.0300</AppVersion></Properties>"
    )


def _relationships(*entries: tuple[str, str, str]) -> str:
    body = "".join(
        f'<Relationship Id="{identifier}" Type="{kind}" Target="{target}"/>'
        for identifier, kind, target in entries
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_PACKAGE}">'
        f"{body}</Relationships>"
    )


def _content_types(main: tuple[str, str], *overrides: tuple[str, str]) -> str:
    parts = (
        main,
        ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        (
            "/docProps/app.xml",
            "application/vnd.openxmlformats-officedocument.extended-properties+xml",
        ),
        *overrides,
    )
    body = "".join(f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in parts)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{_TYPES}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        f"{body}</Types>"
    )


def _package_relationships(main: str) -> str:
    return _relationships(
        ("rId1", f"{_OFFICE}/officeDocument", main),
        ("rId2", f"{_PACKAGE}/metadata/core-properties", "docProps/core.xml"),
        ("rId3", f"{_OFFICE}/extended-properties", "docProps/app.xml"),
    )


def _column(index: int) -> str:
    """A spreadsheet column's letters: 0 is A, 25 is Z, 26 is AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def xlsx_bytes(
    rows: Sequence[Sequence[str | float | int]],
    *,
    sheet: str,
    creator: str,
    moment: dt.datetime,
    modified: dt.datetime | None = None,
) -> bytes:
    """A workbook of one sheet holding ``rows`` (text as inline strings, numbers as numbers)."""
    cells = []
    for number, row in enumerate(rows, start=1):
        entries = []
        for index, value in enumerate(row):
            ref = f"{_column(index)}{number}"
            if isinstance(value, str):
                entries.append(f'<c r="{ref}" t="inlineStr"><is><t>{_escape(value)}</t></is></c>')
            else:
                entries.append(f'<c r="{ref}"><v>{value}</v></c>')
        cells.append(f'<row r="{number}">{"".join(entries)}</row>')
    parts = {
        "[Content_Types].xml": _content_types(
            (
                "/xl/workbook.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            ),
            (
                "/xl/worksheets/sheet1.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            ),
        ),
        "_rels/.rels": _package_relationships("xl/workbook.xml"),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{_SPREADSHEET}"'
            f' xmlns:r="{_OFFICE}"><sheets><sheet name="{_escape(sheet)}" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": _relationships(
            ("rId1", f"{_OFFICE}/worksheet", "worksheets/sheet1.xml")
        ),
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_SPREADSHEET}">'
            f"<sheetData>{''.join(cells)}</sheetData></worksheet>"
        ),
        "docProps/core.xml": _core_part(
            title=sheet, creator=creator, moment=moment, modified=modified or moment
        ),
        "docProps/app.xml": _app_part(),
    }
    return _zip(parts, moment)


def docx_bytes(
    paragraphs: Sequence[str],
    *,
    title: str,
    creator: str,
    moment: dt.datetime,
    modified: dt.datetime | None = None,
) -> bytes:
    """A word-processing document of ``paragraphs``."""
    body = "".join(f"<w:p><w:r><w:t>{_escape(text)}</w:t></w:r></w:p>" for text in paragraphs)
    parts = {
        "[Content_Types].xml": _content_types(
            (
                "/word/document.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            ),
        ),
        "_rels/.rels": _package_relationships("word/document.xml"),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_WORD}">'
            f"<w:body>{body}</w:body></w:document>"
        ),
        "word/settings.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><w:settings xmlns:w="{_WORD}"/>'
        ),
        "docProps/core.xml": _core_part(
            title=title, creator=creator, moment=moment, modified=modified or moment
        ),
        "docProps/app.xml": _app_part(),
    }
    return _zip(parts, moment)


def pptx_bytes(
    slides: Sequence[str],
    *,
    title: str,
    creator: str,
    moment: dt.datetime,
    modified: dt.datetime | None = None,
) -> bytes:
    """A presentation with one slide for each text in ``slides``."""
    count = max(1, len(slides))
    texts = list(slides) or [title]
    slide_parts = {
        f"ppt/slides/slide{number}.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="{_SLIDES}"'
            f' xmlns:a="{_DRAWING}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>'
            f"<a:t>{_escape(text)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>"
            "</p:cSld></p:sld>"
        )
        for number, text in enumerate(texts, start=1)
    }
    identifiers = "".join(
        f'<p:sldId id="{255 + number}" r:id="rId{number}"/>' for number in range(1, count + 1)
    )
    parts = {
        "[Content_Types].xml": _content_types(
            (
                "/ppt/presentation.xml",
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation.main+xml",
            ),
        ),
        "_rels/.rels": _package_relationships("ppt/presentation.xml"),
        "ppt/presentation.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="{_SLIDES}"'
            f' xmlns:r="{_OFFICE}"><p:sldIdLst>{identifiers}</p:sldIdLst></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": _relationships(
            *(
                (f"rId{number}", f"{_OFFICE}/slide", f"slides/slide{number}.xml")
                for number in range(1, count + 1)
            )
        ),
        **slide_parts,
        "docProps/core.xml": _core_part(
            title=title, creator=creator, moment=moment, modified=modified or moment
        ),
        "docProps/app.xml": _app_part(),
    }
    return _zip(parts, moment)


# ---------------------------------------------------------------------------
# A PDF, a notebook, a PNG.
# ---------------------------------------------------------------------------


def _pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_bytes(lines: Sequence[str], *, title: str, moment: dt.datetime) -> bytes:
    """A one-page PDF 1.4 holding ``lines`` of text, with an information dictionary.

    The objects are written in order and the cross-reference table is computed from their
    offsets, so the file opens in any reader that follows the specification.
    """
    stamp = moment.astimezone(dt.UTC).strftime("D:%Y%m%d%H%M%SZ")
    shown = ["BT", "/F1 11 Tf", "14 TL", "72 720 Td"]
    shown.extend(f"({_pdf_text(line)}) Tj T*" for line in lines)
    shown.append("ET")
    stream = ("\n".join(shown) + "\n").encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Title ({_pdf_text(title)}) /Producer ({APPLICATION}) "
            f"/CreationDate ({stamp}) /ModDate ({stamp}) >>"
        ).encode("latin-1", "replace"),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{table}\n%%EOF\n"
    ).encode()
    return bytes(out)


def notebook_bytes(cells: Sequence[tuple[str, str]]) -> bytes:
    """A Jupyter notebook (format 4) of ``(kind, source)`` cells, never executed: no outputs."""
    body: list[dict[str, object]] = []
    for kind, source in cells:
        lines = source.splitlines(keepends=True)
        if kind == "code":
            body.append(
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": lines,
                }
            )
        else:
            body.append({"cell_type": "markdown", "metadata": {}, "source": lines})
    notebook = {
        "cells": body,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }
    return (json.dumps(notebook, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()


def png_bytes(pixels: bytes) -> bytes:
    """A PNG of one row of 8-bit gray ``pixels``, its image data stored and never deflated."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    row = b"\x00" + pixels
    deflated = (
        b"\x78\x01"
        + struct.pack("<BHH", 1, len(row), len(row) ^ 0xFFFF)
        + row
        + struct.pack(">I", zlib.adler32(row))
    )
    header = struct.pack(">IIBBBBB", len(pixels), 1, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", deflated)
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Instrument files.
# ---------------------------------------------------------------------------

_SHORT = 3
_LONG = 4
_ASCII = 2


def tiff_stack_bytes(
    pages: Sequence[bytes], *, width: int = 4, description: str | None = None
) -> bytes:
    """A classic little-endian TIFF of one page for each item of ``pages``, 8-bit gray.

    ``description`` becomes the first page's ImageDescription (the way a stack's frame count is
    written beside it). Each page is one strip; each directory points at the next.
    """
    if not pages:
        message = "a TIFF stack holds at least one page"
        raise ValueError(message)
    buffer = bytearray(b"II*\x00" + bytes(4))
    previous_next_slot = 4
    for number, pixels in enumerate(pages):
        height = max(1, len(pixels) // width)
        strip = pixels[: width * height].ljust(width * height, b"\x00")
        strip_offset = len(buffer)
        buffer += strip
        if len(buffer) % 2:
            buffer += b"\x00"
        text = b""
        text_offset = 0
        if number == 0 and description:
            text = description.encode("ascii", "replace") + b"\x00"
            text_offset = len(buffer)
            buffer += text
            if len(buffer) % 2:
                buffer += b"\x00"
        entries: list[tuple[int, int, int, bytes]] = [
            (256, _SHORT, 1, struct.pack("<H", width)),
            (257, _SHORT, 1, struct.pack("<H", height)),
            (258, _SHORT, 1, struct.pack("<H", 8)),
            (259, _SHORT, 1, struct.pack("<H", 1)),
            (262, _SHORT, 1, struct.pack("<H", 1)),
        ]
        if text:
            entries.append((270, _ASCII, len(text), struct.pack("<I", text_offset)))
        entries.extend(
            [
                (273, _LONG, 1, struct.pack("<I", strip_offset)),
                (277, _SHORT, 1, struct.pack("<H", 1)),
                (278, _SHORT, 1, struct.pack("<H", height)),
                (279, _LONG, 1, struct.pack("<I", len(strip))),
            ]
        )
        directory = len(buffer)
        struct.pack_into("<I", buffer, previous_next_slot, directory)
        buffer += struct.pack("<H", len(entries))
        for tag, kind, count, data in entries:
            buffer += struct.pack("<HHI", tag, kind, count) + data.ljust(4, b"\x00")
        previous_next_slot = len(buffer)
        buffer += bytes(4)
    return bytes(buffer)


def czi_header_bytes(token: bytes) -> bytes:
    """The first segment of a Zeiss CZI file, with invented values. Not a readable CZI.

    A 32-byte segment header (the id ``ZISRAWFILE``, the allocated and used sizes) and the 80-byte
    file header: major and minor version, two file GUIDs made from ``token``, the part number, the
    directory and metadata positions (set past the end of the file, where nothing is), the update
    flag and the attachment directory position. There is no subblock, no directory, no metadata
    and no pixel: a program that opens it as an image fails, and one that looks only at the first
    sixteen bytes and the size sees a microscope's file.
    """
    seed = (token + bytes(32))[:32]
    header = (
        struct.pack("<ii", 1, 0)
        + struct.pack("<ii", 0, 0)
        + seed[:16]
        + seed[16:32]
        + struct.pack("<i", 0)
        + struct.pack("<qq", 1 << 20, 1 << 21)
        + struct.pack("<i", 0)
        + struct.pack("<q", 1 << 22)
    )
    used = len(header)
    allocated = -(-used // 32) * 32
    segment = b"ZISRAWFILE".ljust(16, b"\x00") + struct.pack("<qq", allocated, used)
    return segment + header.ljust(allocated, b"\x00")


def mat_file_bytes(moment: dt.datetime, platform: str = "PCWIN64") -> bytes:
    """A MATLAB level 5 data file: its 128-byte header and no variable (version 0x0100)."""
    stamp = moment.astimezone(dt.UTC)
    created = (
        f"{_WEEKDAYS[stamp.weekday()]} {_MONTHS[stamp.month - 1]} {stamp.day:2d} "
        f"{stamp:%H:%M:%S %Y}"
    )
    text = f"MATLAB 5.0 MAT-file, Platform: {platform}, Created on: {created} HG version: 1.0"
    return text.encode("ascii").ljust(116, b" ")[:116] + bytes(8) + b"\x00\x01IM"


def ds_store_bytes(token: bytes) -> bytes:
    """The Finder's folder file reduced to its signature: ``Bud1`` after a four-byte header."""
    return (b"\x00\x00\x00\x01Bud1" + (token + bytes(16))[:16]).ljust(64, b"\x00")


def office_lock_bytes(owner: str, token: bytes) -> bytes:
    """An Office editor's lock file: 162 bytes, the owner's name first and its length before it."""
    name = owner.encode("ascii", "replace")[:54]
    body = bytes((len(name),)) + name
    return (body.ljust(150, b" ") + (token + bytes(12))[:12]).ljust(162, b"\x00")


def thumbs_db_bytes(token: bytes) -> bytes:
    """A minimal compound file (version 3, 512-byte sectors) with an empty root: one FAT sector."""
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + bytes(16)
        + struct.pack("<HHHHH", 0x003E, 0x0003, 0xFFFE, 9, 6)
        + bytes(6)
        + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, end, 0, end, 0)
        + struct.pack("<I", 0)
        + struct.pack("<108I", *([free] * 108))
    )
    header = header[:512].ljust(512, b"\x00")
    fat = struct.pack("<II", fat_sector, end) + struct.pack("<126I", *([free] * 126))
    name = "Root Entry".encode("utf-16-le") + b"\x00\x00"
    root = (
        name.ljust(64, b"\x00")
        + struct.pack("<HBB", len(name), 5, 1)
        + struct.pack("<III", free, free, free)
        + bytes(16)
        + struct.pack("<I", 0)
        + bytes(8)
        + (token + bytes(8))[:8]
        + struct.pack("<IQ", end, 0)
    )
    unused = bytes(64) + struct.pack("<HBB", 0, 0, 0) + struct.pack("<III", free, free, free)
    unused = unused.ljust(128, b"\x00")
    return header + fat + root + unused * 3


# ---------------------------------------------------------------------------
# More of what a laboratory keeps.
# ---------------------------------------------------------------------------

#: A baseline JPEG of one white pixel (the smallest valid one), to which a comment is added.
_JPEG_HEAD = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000"
    "ffdb004300030202020202030202020303030304060404040404080606050609080a0a090809090a0c0f0c0a0b0e0b09090d110d0e0f101011100a0c12131210130f101010"
    "ffc9000b080001000101011100"
    "ffcc00060010100500"
    "ffda00080101000003"
)


def jpeg_bytes(token: bytes) -> bytes:
    """A JPEG file: the start of image, a comment holding ``token`` and a one-pixel frame header.

    The scan data is empty, so it is not a viewable picture: it has the signature, the
    length and the marker segments of a JPEG, which is what a program that looks at those sees.
    """
    comment = b"\xff\xfe" + struct.pack(">H", len(token) + 2) + token
    return b"\xff\xd8" + comment + _JPEG_HEAD[2:] + b"\xff\xd9"


def gif_bytes(token: bytes) -> bytes:
    """A GIF89a of one pixel, with a comment extension holding ``token``."""
    comment = b"\x21\xfe" + bytes((min(len(token), 255),)) + token[:255] + b"\x00"
    return (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        + comment
        + b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
    )


def svg_bytes(title: str, token: bytes) -> bytes:
    """A vector figure: one rectangle and a title."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30">'
        f'<title>{_escape(title)}</title><rect width="40" height="30" fill="#cccccc"/>'
        f"<!-- {token.hex()} --></svg>\n"
    ).encode()


def zip_bytes(parts: Mapping[str, str | bytes], moment: dt.datetime) -> bytes:
    """A ZIP archive of ``parts``, stored uncompressed and dated ``moment``."""
    return _zip(parts, moment)


def jar_bytes(name: str, moment: dt.datetime) -> bytes:
    """A Java archive: a ZIP with a manifest, as the plugins of a Fiji folder are."""
    manifest = f"Manifest-Version: 1.0\nImplementation-Title: {name}\n"
    return _zip({"META-INF/MANIFEST.MF": manifest}, moment)


def lif_header_bytes(token: bytes) -> bytes:
    """The start of a Leica LIF file: test byte, length and a header of XML in UTF-16.

    Not a readable LIF: the memory blocks that hold the images are not there.
    """
    xml = (
        f'<LMSDataContainerHeader Version="2"><Element Name="{token.hex()}"/>'
        "</LMSDataContainerHeader>"
    )
    body = xml.encode("utf-16-le")
    return (
        b"\x70\x00\x00\x00"
        + struct.pack("<I", len(body) + 4)
        + b"\x2a"
        + struct.pack("<I", len(xml))
        + body
    )


def ole_document_bytes(stream: str, token: bytes) -> bytes:
    """A compound file with one empty stream named ``stream``: the container of an older Office
    file (``WordDocument``, ``Workbook``, ``PowerPoint Document``) or of an Olympus OIB.

    Not readable by the program that saves such a file: the stream holds nothing.
    """
    base = bytearray(thumbs_db_bytes(token))
    free = 0xFFFFFFFF
    # The directory is the third sector: the root entry (first 128 bytes), then three more.
    directory = 512 + 512
    name = stream.encode("utf-16-le") + b"\x00\x00"
    entry = (
        name.ljust(64, b"\x00")
        + struct.pack("<HBB", len(name), 2, 1)
        + struct.pack("<III", free, free, free)
        + bytes(16)
        + struct.pack("<I", 0)
        + bytes(16)
        + struct.pack("<IQ", 0xFFFFFFFE, 0)
    )
    base[directory + 128 : directory + 256] = entry.ljust(128, b"\x00")
    struct.pack_into("<I", base, directory + 64 + 4 + 8, 1)  # the root's child is the stream
    return bytes(base)


def ab1_bytes(token: bytes) -> bytes:
    """The start of an ABI sequencing trace: ``ABIF``, a version and one directory entry."""
    return (
        b"ABIF"
        + struct.pack(">H", 101)
        + b"tdir"
        + struct.pack(">iHHiii", 1, 1023, 28, 1, 28, 28)
        + bytes(4)
        + (token + bytes(16))[:16]
    )


def dna_bytes(token: bytes) -> bytes:
    """The start of a plasmid map file: the type byte, a length and the ``SnapGene`` cookie."""
    return b"\t\x00\x00\x00\x0e\x00\x01SnapGene\x00\x01\x00\x0f\x00\x1d" + (token + bytes(8))[:8]


def fcs_bytes(token: bytes) -> bytes:
    """The header of a flow cytometry standard file: the version, offsets and no events."""
    text = f"/$TOT/0/$PAR/1/$P1N/FSC-A/${token.hex()}/"
    return b"FCS3.0    " + b"%8d%8d%8d%8d%8d%8d" % (58, 58 + len(text), 0, 0, 0, 0) + text.encode()


def avi_bytes(token: bytes) -> bytes:
    """A RIFF container of the AVI kind with a header list and no frame."""
    body = b"AVI LIST" + struct.pack("<I", 4) + b"hdrl" + (token + bytes(8))[:8]
    return b"RIFF" + struct.pack("<I", len(body)) + body
