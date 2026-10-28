"""TIFF: the times a TIFF carries, recorded as readings and never offered as its clock.

This extractor offers no clock, on purpose. A TIFF's ``DateTime`` tag is when the file was written,
so a processed or re-saved image carries its processing time there. Bio-Formats and Tika both
promote that tag to an acquisition or creation date.
An OME-TIFF's ``AcquisitionDate`` usually carries no zone. And the only time inside a crop
Fiji made from a Nikon acquisition was the source file's Julian day, which ImageJ copied
into its ``Info`` property with the rest of the source's metadata. Each is recorded as a
reading that says what it is, and none becomes the file's event time, so no filename or
filesystem clock is displaced by a time about something else.

What is read. The first page's size, the page count by walking the chain of pages, the
``DateTime`` tag, what kind of ``ImageDescription`` the file has, the first
``AcquisitionDate`` in OME-XML, and a Nikon Julian day in ImageJ's metadata. Classic TIFF
and BigTIFF, in either byte order. Every value is read where its tag points, through the
counting reader, so a multi-gigabyte stack costs its tags and its page chain and never a
pixel.

Version 2 reads who wrote the file: the ``Software``, ``Make``, ``Model``, ``Artist``,
``HostComputer``, ``DocumentName`` and ``PageName`` tags, the ``Make`` and ``Model`` of an Exif
directory, the private tags present as a writer's fingerprint and the vendor tags among them
(Micro-Manager, Zeiss LSM, MetaMorph, FluoView), and from the description the OME ``Creator``,
instrument, objective and channel names, the ImageJ version, and whether a plate reader's XML sits
there. Each is a reading keyed plainly (``image.software``, ``image.make``,
``image.writer_fingerprint`` ...), read by the rules that say which program made a file. None is a
conclusion here.

The reads are bounded, and a hostile chain ends. The page chain stops at a page already
visited, at a pointer outside the file, or at the byte ceiling. The extractor records which of
those stopped it.
A description longer than the ceiling leaves room for is searched as far as it was read,
and says so.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from html import unescape
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
from ingence.extractors.clocks import julian_day_ns
from ingence.extractors.limits import (
    CountingReader,
    LimitExceededError,
    Limits,
    StructureBoundsError,
)

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["TIFF_EXTENSIONS", "TIFF_MEDIA_TYPES", "TiffExtractor"]

TIFF_EXTENSIONS = {".tif", ".tiff"}

#: What detection calls every TIFF-based file, whatever its name.
TIFF_MEDIA_TYPES = {"image/tiff"}

_WIDTH = 256
_HEIGHT = 257
_DOCUMENT_NAME = 269
_DESCRIPTION = 270
_MAKE = 271
_MODEL = 272
_PAGE_NAME = 285
_SOFTWARE = 305
_DATE_TIME = 306
_ARTIST = 315
_HOST_COMPUTER = 316
_EXIF_IFD = 34665
_IMAGEJ_METADATA = 50839

#: The text tags a writer leaves its name in, and the reading each becomes.
_TEXT_TAGS: tuple[tuple[int, str], ...] = (
    (_SOFTWARE, "image.software"),
    (_MAKE, "image.make"),
    (_MODEL, "image.model"),
    (_ARTIST, "image.artist"),
    (_HOST_COMPUTER, "image.host_computer"),
    (_DOCUMENT_NAME, "image.document_name"),
    (_PAGE_NAME, "image.page_name"),
)
#: Private tags vendors' acquisition software writes, and the name each marks: MetaMorph's
#: UIC1, Olympus FluoView's, Zeiss LSM's and Micro-Manager's metadata tags.
_VENDOR_TAGS: dict[int, str] = {
    33628: "metamorph",
    34361: "fluoview",
    34412: "lsm",
    51123: "micromanager",
}
#: Where the private tags begin: every tag from here up is some writer's own.
_PRIVATE_TAGS_FROM = 32768

#: Entry types whose values are one byte each: BYTE, ASCII and UNDEFINED.
_BYTE_TYPES = frozenset({1, 2, 7})
#: Unsigned integer types read as scalars: SHORT, LONG, IFD and LONG8.
_INTEGER_TYPES: dict[int, tuple[str, int]] = {
    3: ("H", 2),
    4: ("I", 4),
    13: ("I", 4),
    16: ("Q", 8),
}

#: More entries than this on one page is a corrupt directory, not a rich one.
_MAX_ENTRIES = 4096
_MAX_PAGES = 1_000_000
_MAX_DATE_TIME_BYTES = 64
_MAX_TEXT_CHARS = 64
#: The most of a writer's tag that is kept: a name, a version, a host, never a document.
_MAX_TAG_CHARS = 256
_MAX_CHANNELS = 32
_DESCRIPTION_KIND_BYTES = 4096

_ACQUISITION_DATE = re.compile(rb"<AcquisitionDate>\s*([^<\s]{1,64})\s*</AcquisitionDate>")
_OME_CREATOR = re.compile(rb"<OME\b[^>]*?\sCreator=\"([^\"]{1,256})\"")
_OME_MICROSCOPE = re.compile(rb"<Microscope\b([^>]*)>")
_OME_OBJECTIVE = re.compile(rb"<Objective\b([^>]*)>")
_OME_CHANNEL = re.compile(rb"<Channel\b([^>]*)>")
_IMAGEJ_VERSION = re.compile(rb"^ImageJ=([^\s\x00]{1,32})")
_NIKON_JULIAN_DAY = re.compile(rb"dTimeAbsolute\s*=\s*(\d{7}\.\d{1,17})")
_STATED_ZONE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


class _DeclinedError(Exception):
    """Bytes this extractor chose not to read, carrying the reason."""


class _CorruptError(ValueError):
    """A TIFF whose structure is not as the format writes it."""


@dataclass(frozen=True, slots=True)
class _Layout:
    """Byte order and whether offsets are 32 or 64 bits wide."""

    order: str
    big: bool

    @property
    def count_format(self) -> str:
        return self.order + ("Q" if self.big else "H")

    @property
    def count_size(self) -> int:
        return 8 if self.big else 2

    @property
    def entry_size(self) -> int:
        return 20 if self.big else 12

    @property
    def pointer_format(self) -> str:
        return self.order + ("Q" if self.big else "I")

    @property
    def pointer_size(self) -> int:
        return 8 if self.big else 4


@dataclass(frozen=True, slots=True)
class _Entry:
    """One tag's type, value count, and the field holding its value or its offset."""

    kind: int
    count: int
    field: bytes


class TiffExtractor:
    """Reads a TIFF's size, pages, the times it carries and who wrote it, as readings only."""

    name = "tiff"
    #: Version 2 reads the writer's tags, the Exif make and model, the private tags and what the
    #: description names. Version 1 read the size, the pages and the times.
    version = "2"
    #: A chain of directories whose offsets whoever wrote the file chose, and which a
    #: hostile file can point back on itself.
    isolation = Isolation.SUBPROCESS
    #: A header reading: the IFD chain and its tags, read without decoding a single
    #: pixel.
    reading_depth = ReadingDepth.HEADER

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in TIFF_MEDIA_TYPES:
            return True
        return bool(TIFF_EXTENSIONS & context.extensions)

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
        except _DeclinedError as exc:
            return declined(self.name, self.version, str(exc))
        except (_CorruptError, StructureBoundsError, OSError) as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=reader.bytes_read,
        )


def _read(reader: CountingReader, readings: list[Reading]) -> None:
    """Read a TIFF's first page, its page chain and its description into readings."""
    exposed = ExtractionTier.MECHANICALLY_EXPOSED
    layout, first = _header(reader)
    entries, following = _directory(reader, layout, first)
    variant = "bigtiff" if layout.big else "classic"
    readings.append(Reading("image.tiff_variant", variant, ValueKind.TEXT, exposed))
    for tag, key in ((_WIDTH, "image.width"), (_HEIGHT, "image.height")):
        size = _integer(layout, entries[tag]) if tag in entries else None
        if size is not None:
            readings.append(Reading(key, size, ValueKind.INTEGER))
    if _DATE_TIME in entries:
        stamp, _ = _bytes_of(reader, layout, entries[_DATE_TIME], cap=_MAX_DATE_TIME_BYTES)
        text = stamp.split(b"\x00", 1)[0].decode("latin-1").strip()
        if text:
            readings.append(Reading("image.tiff_datetime", text[:_MAX_TEXT_CHARS], ValueKind.TEXT))
            readings.append(Reading("image.tiff_datetime_zone", "unstated", ValueKind.TEXT))
    if _IMAGEJ_METADATA in entries:
        _imagej_source_time(reader, layout, entries[_IMAGEJ_METADATA], readings)
    for tag, key in _TEXT_TAGS:
        if tag in entries:
            text = _text(reader, layout, entries[tag])
            if text:
                readings.append(Reading(key, text, ValueKind.TEXT))
    if _EXIF_IFD in entries:
        _exif(reader, layout, entries[_EXIF_IFD], readings)
    private = sorted(tag for tag in entries if tag >= _PRIVATE_TAGS_FROM)
    if private:
        fingerprint = ",".join(str(tag) for tag in private)
        readings.append(Reading("image.writer_fingerprint", fingerprint, ValueKind.TEXT, exposed))
    vendors = sorted(name for tag, name in _VENDOR_TAGS.items() if tag in entries)
    if vendors:
        readings.append(Reading("image.vendor_tags", ",".join(vendors), ValueKind.TEXT, exposed))

    pages, stopped = _page_count(reader, layout, first, following)
    readings.append(Reading("image.page_count", pages, ValueKind.INTEGER, exposed))
    if stopped == "the byte ceiling":
        readings.append(Reading("image.page_count_bounded", True, ValueKind.NEGATIVE))
    elif stopped is not None:
        readings.append(Reading("image.page_chain_stopped", stopped, ValueKind.TEXT, exposed))
    if _DESCRIPTION in entries:
        _description(reader, layout, entries[_DESCRIPTION], readings)


def _header(reader: CountingReader) -> tuple[_Layout, int]:
    """Return the byte order, the offset width, and where the first page's directory is."""
    if reader.size < 8:
        message = "too short to be a TIFF"
        raise _DeclinedError(message)
    head = reader.read_at(0, 8)
    order = {b"II": "<", b"MM": ">"}.get(head[:2])
    if order is None:
        message = "not a TIFF: no byte-order mark"
        raise _DeclinedError(message)
    (version,) = struct.unpack(order + "H", head[2:4])
    if version == 42:
        (first,) = struct.unpack(order + "I", head[4:8])
        return _Layout(order, big=False), first
    if version == 43 and reader.size >= 16:
        (first,) = struct.unpack(order + "Q", reader.read_at(8, 8))
        return _Layout(order, big=True), first
    message = f"not a TIFF this reads: version {version}"
    raise _DeclinedError(message)


def _directory(
    reader: CountingReader, layout: _Layout, offset: int
) -> tuple[dict[int, _Entry], int]:
    """Return one page's entries by tag, and the offset of the next page's directory."""
    (count,) = struct.unpack(layout.count_format, reader.read_at(offset, layout.count_size))
    if count > _MAX_ENTRIES:
        message = f"the page at byte {offset} claims {count} entries"
        raise _CorruptError(message)
    table = reader.read_at(offset + layout.count_size, count * layout.entry_size)
    pointer_at = offset + layout.count_size + count * layout.entry_size
    (following,) = struct.unpack(
        layout.pointer_format, reader.read_at(pointer_at, layout.pointer_size)
    )
    head = layout.order + ("HHQ" if layout.big else "HHI")
    field_start = 12 if layout.big else 8
    entries: dict[int, _Entry] = {}
    for index in range(count):
        start = index * layout.entry_size
        tag, kind, number = struct.unpack_from(head, table, start)
        field = table[start + field_start : start + layout.entry_size]
        entries.setdefault(tag, _Entry(kind, number, field))
    return entries, following


def _integer(layout: _Layout, entry: _Entry) -> int | None:
    """Return a single unsigned integer value, or None when the entry is not one."""
    shape = _INTEGER_TYPES.get(entry.kind)
    if shape is None or entry.count != 1 or shape[1] > len(entry.field):
        return None
    (value,) = struct.unpack_from(layout.order + shape[0], entry.field)
    return int(value)


def _bytes_of(
    reader: CountingReader, layout: _Layout, entry: _Entry, *, cap: int
) -> tuple[bytes, bool]:
    """Return a byte-typed entry's value, up to a cap and the ceiling, and whether it was whole."""
    if entry.kind not in _BYTE_TYPES:
        return b"", False
    if entry.count <= layout.pointer_size:
        return entry.field[: entry.count], True
    (pointer,) = struct.unpack(layout.pointer_format, entry.field)
    length = min(entry.count, cap, max(reader.limit - reader.bytes_read, 0))
    return reader.read_at(pointer, length), length == entry.count


def _text(reader: CountingReader, layout: _Layout, entry: _Entry) -> str:
    """Return a text tag's value up to the first NUL, stripped and bounded; empty for none."""
    raw, _ = _bytes_of(reader, layout, entry, cap=_MAX_TAG_CHARS + 1)
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()[:_MAX_TAG_CHARS]


def _exif(reader: CountingReader, layout: _Layout, entry: _Entry, readings: list[Reading]) -> None:
    """Record the make and model an Exif directory carries, where the pointer reaches one.

    A pointer outside the file or a directory claiming thousands of entries records nothing:
    the Exif block is one writer's, and a broken one says nothing about the TIFF around it.
    """
    pointer = _integer(layout, entry)
    if pointer is None or pointer == 0:
        return
    try:
        exif, _ = _directory(reader, layout, pointer)
    except (_CorruptError, StructureBoundsError, LimitExceededError):
        return
    for tag, key in ((_MAKE, "image.exif_make"), (_MODEL, "image.exif_model")):
        if tag in exif:
            text = _text(reader, layout, exif[tag])
            if text:
                readings.append(Reading(key, text, ValueKind.TEXT))


def _imagej_source_time(
    reader: CountingReader, layout: _Layout, entry: _Entry, readings: list[Reading]
) -> None:
    """Record a Nikon Julian day ImageJ copied from a source file, as the source's time."""
    metadata, _ = _bytes_of(reader, layout, entry, cap=reader.limit)
    # ImageJ writes its properties in UTF-16, and a Julian day is ASCII digits either way.
    found = _NIKON_JULIAN_DAY.search(metadata.replace(b"\x00", b""))
    moment = julian_day_ns(float(found.group(1))) if found is not None else None
    if moment is None:
        return
    readings.append(
        Reading("image.source_acquired_ns", moment, ValueKind.INTEGER, ExtractionTier.HEURISTIC)
    )
    readings.append(
        Reading(
            "image.source_acquired_field",
            "ImageJ Info, dTimeAbsolute",
            ValueKind.TEXT,
            ExtractionTier.MECHANICALLY_EXPOSED,
        )
    )


def _page_count(
    reader: CountingReader, layout: _Layout, first: int, following: int
) -> tuple[int, str | None]:
    """Count pages along the chain, returning the count and what stopped it early, if anything.

    Two small reads per page, its entry count and its next pointer, so a stack of many
    pages costs a few bytes each rather than its directories in full.
    """
    pages = 1
    seen = {first}
    offset = following
    try:
        while offset:
            if offset in seen:
                return pages, "a page pointing back into the chain"
            seen.add(offset)
            (count,) = struct.unpack(layout.count_format, reader.read_at(offset, layout.count_size))
            pointer_at = offset + layout.count_size + count * layout.entry_size
            pointer = reader.read_at(pointer_at, layout.pointer_size)
            pages += 1
            if pages >= _MAX_PAGES:
                return pages, "the page limit"
            (offset,) = struct.unpack(layout.pointer_format, pointer)
    except StructureBoundsError:
        return pages, "a pointer outside the file"
    except LimitExceededError:
        return pages, "the byte ceiling"
    return pages, None


def _description(
    reader: CountingReader, layout: _Layout, entry: _Entry, readings: list[Reading]
) -> None:
    """Record what kind of description the file has, and OME-XML's first acquisition date."""
    text, whole = _bytes_of(reader, layout, entry, cap=reader.limit)
    head = text[:_DESCRIPTION_KIND_BYTES].lstrip()
    if b"<OME" in head:
        kind = "ome-xml"
    elif head.startswith(b"ImageJ="):
        kind = "imagej"
    elif head.startswith(b"<"):
        # A plate reader or another program writes its own XML here. What it says is its own.
        kind = "xml"
    else:
        kind = "other"
    readings.append(
        Reading("image.description_kind", kind, ValueKind.TEXT, ExtractionTier.HEURISTIC)
    )
    if not whole:
        readings.append(Reading("image.description_bounded", True, ValueKind.NEGATIVE))
    if kind == "imagej":
        version = _IMAGEJ_VERSION.match(head)
        if version is not None:
            readings.append(
                Reading("image.imagej_version", _decoded(version.group(1)), ValueKind.TEXT)
            )
        return
    if kind != "ome-xml":
        return
    found = _ACQUISITION_DATE.search(text)
    if found is not None:
        raw = found.group(1).decode("utf-8", errors="replace")[:_MAX_TEXT_CHARS]
        zone = "stated" if _STATED_ZONE.search(raw) else "unstated"
        readings.append(Reading("image.ome_acquisition_date", raw, ValueKind.TEXT))
        readings.append(Reading("image.ome_acquisition_date_zone", zone, ValueKind.TEXT))
    _ome_writer(text, readings)


def _ome_writer(text: bytes, readings: list[Reading]) -> None:
    """Record what OME-XML says about who made the image: the creator, the microscope, the
    objective and the channel names, each as the writer wrote it."""
    creator = _OME_CREATOR.search(text)
    if creator is not None:
        readings.append(Reading("image.ome_creator", _decoded(creator.group(1)), ValueKind.TEXT))
    microscope = _OME_MICROSCOPE.search(text)
    if microscope is not None:
        parts = [_attribute(microscope.group(1), name) for name in (b"Manufacturer", b"Model")]
        named = " ".join(part for part in parts if part)
        if named:
            readings.append(Reading("image.ome_instrument", named, ValueKind.TEXT))
    objective = _OME_OBJECTIVE.search(text)
    if objective is not None:
        model = _attribute(objective.group(1), b"Model")
        magnification = _attribute(objective.group(1), b"NominalMagnification")
        aperture = _attribute(objective.group(1), b"LensNA")
        described = model or " ".join(
            part for part in (f"{magnification}x" if magnification else "", aperture) if part
        )
        if described:
            readings.append(Reading("image.ome_objective", described, ValueKind.TEXT))
    names = [
        name
        for attributes in _OME_CHANNEL.findall(text)[:_MAX_CHANNELS]
        if (name := _attribute(attributes, b"Name"))
    ]
    if names:
        readings.append(Reading("image.ome_channel_names", ", ".join(names), ValueKind.TEXT))


def _attribute(attributes: bytes, name: bytes) -> str:
    """Return one XML attribute's value from an element's attribute text, or empty."""
    found = re.search(rb"\s" + name + rb"=\"([^\"]*)\"", attributes)
    return _decoded(found.group(1)) if found is not None else ""


def _decoded(raw: bytes) -> str:
    """Text as the writer wrote it, bounded, with XML's escapes read back."""
    return unescape(raw.decode("utf-8", errors="replace"))[:_MAX_TAG_CHARS].strip()


@EXTRACTORS.register(
    "tiff",
    version="2",
    summary="TIFF: size, pages, the times it carries and who wrote it, as readings only",
    attributes={"extensions": ",".join(sorted(TIFF_EXTENSIONS))},
)
def _build_tiff() -> TiffExtractor:
    return TiffExtractor()
