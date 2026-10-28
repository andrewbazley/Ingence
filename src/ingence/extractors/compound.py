"""OLE compound storage: legacy Office, and every format built on the same container.

A legacy workbook is compound storage rather than OOXML, a different container entirely, and the
OOXML spreadsheet extractor does not claim it. This extractor claims ``.doc``, ``.xls`` and ``.ppt``
by name, and reads anything detection recognised as compound storage by content, whatever the file
is called, which it declares so that a processing fingerprint can see it.

Content first, which is how instrument formats arrive without being named. Several instrument
formats are built on this container, and detection already recognises it from its first eight bytes.
Reading its structure needs no knowledge of any vendor: the storage tree, the stream names and the
standard summary property set belong to the container. Two vendor formats are also claimed by name,
``.oib`` and ``.zvi``. Both are this container, so claiming them adds only what the container holds.
No acquisition time is read from a vendor's own streams, which would make this extractor a producer
of ``TimeSource.INSTRUMENT``. That waits until a sample shows where one is written.

The stream names decide the keys, because keys name the content. A
word-processing document, a workbook and a presentation each write a stream the
others do not. A workbook's properties become ``table.*``, the keys
``spreadsheet.py`` writes for an OOXML workbook, so a rule reading a table's author
reads one key whichever decade the table was saved in. A document's and a
presentation's become ``document.*``, as the PDF and OOXML extractors write them.
A container whose streams name no application keeps ``compound.*``, because saying
more would be guessing.

It fills clock tier two from the summary property set's creation time, a FILETIME the application
writes, which [MS-OLEPS] defines as UTC and which a copy does not move. It arrives through the
reader's own conversion, to the microsecond: a FILETIME counts tenths of a microsecond and that last
digit is not recoverable through it. Asked not to convert, the reader returns whole seconds since
1601 rather than the raw count. A zero FILETIME is an unset field and abstains. The last-saved time
is recorded and not offered, for the reason a PDF's modification date is not.

A workbook's sheet names come from its globals, the records before the first
sheet, within the byte ceiling. A workbook that declares encryption has encrypted
sheet names, recorded as that rather than as a failure. Word and PowerPoint
streams are not parsed: their text needs the piece table and the persist directory,
and structure and properties are what this extractor claims to read.

An encrypted OOXML package lives here too. Office stores a password-protected
``.docx`` as compound storage holding an encrypted package. The OOXML extractor
declines those bytes and this one records the encryption.

``olefile`` is the one dependency, in the ``content`` extra and never the base
install: BSD-licensed, pure Python, no dependencies of its own, and the reference
reader for the format. It is imported inside :meth:`CompoundExtractor.extract`, so
a child isolating some other format never pays for it.
"""

from __future__ import annotations

import codecs
import datetime as dt
import struct
from typing import TYPE_CHECKING, Any

from ingence.core.ids import canonical_json
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
from ingence.extractors.clocks import nanoseconds_since_epoch
from ingence.extractors.limits import Limits

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = [
    "COMPOUND_EXTENSIONS",
    "COMPOUND_MEDIA_TYPES",
    "CompoundExtractor",
    "workbook_globals",
]

#: ``.oib`` and ``.zvi`` are two microscope vendors' formats built on this container,
#: claimed by name. What either yields is what the container holds.
COMPOUND_EXTENSIONS = {".doc", ".oib", ".ppt", ".xls", ".zvi"}

#: What detection calls this container when it recognises the signature.
COMPOUND_MEDIA_TYPES = {"application/x-ole-storage"}

_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: Top-level streams an application writes, and the kind of content each says is
#: inside. Consulted in this order.
_KIND_BY_STREAM: tuple[tuple[str, str], ...] = (
    ("WordDocument", "document"),
    ("Workbook", "table"),
    ("Book", "table"),
    ("PowerPoint Document", "presentation"),
)
_ENCRYPTED_PACKAGE = frozenset({"EncryptedPackage", "EncryptionInfo"})
_NAMESPACE_OF_KIND = {"document": "document", "presentation": "document", "table": "table"}

_SUMMARY_STREAM = "\x05SummaryInformation"

#: Summary information property identifiers, from [MS-OLEPS].
_PID_CODEPAGE = 0x01
_PID_TEXT = {0x02: "title", 0x03: "subject", 0x04: "author", 0x05: "keywords", 0x12: "creator"}
_PID_EDIT_TIME = 0x0A
_PID_CREATED = 0x0C
_PID_SAVED = 0x0D

#: Where the reader puts a FILETIME of zero, which is a field nobody set.
_FILETIME_ZERO = dt.datetime(1601, 1, 1)

#: BIFF record types read from a workbook's globals, from [MS-XLS].
_BIFF_BOF = 0x0809
_BIFF_EOF = 0x000A
_BIFF_FILEPASS = 0x002F
_BIFF_BOUNDSHEET = 0x0085
_BIFF8 = 0x0600

#: How many top-level names are listed. The counts are always recorded, so a
#: truncation is visible rather than silent.
MAX_NAMES_DESCRIBED = 64
#: The same bound ``spreadsheet.py`` sets, for the same reason.
MAX_SHEETS_DESCRIBED = 32
_MAX_VALUE_CHARS = 500


def workbook_globals(stream: bytes) -> tuple[tuple[str, ...], bool] | None:
    """Return a BIFF workbook's sheet names and whether it declares encryption.

    ``None`` when the stream does not open with a BIFF5 or BIFF8 workbook record,
    which is a stream this cannot read rather than a workbook with no sheets.
    Bounded by the bytes handed in: every record's declared length is checked
    against them, and a record claiming more than the stream holds ends the walk.
    """
    if len(stream) < 8:
        return None
    record_type, size = struct.unpack_from("<HH", stream, 0)
    if record_type != _BIFF_BOF or size < 2:
        return None
    (biff_version,) = struct.unpack_from("<H", stream, 4)
    names: list[str] = []
    offset = 4 + size
    while offset + 4 <= len(stream):
        record_type, size = struct.unpack_from("<HH", stream, offset)
        if offset + 4 + size > len(stream):
            break
        body = stream[offset + 4 : offset + 4 + size]
        offset += 4 + size
        if record_type == _BIFF_FILEPASS:
            return tuple(names), True
        if record_type == _BIFF_EOF:
            break
        if record_type == _BIFF_BOUNDSHEET and len(body) > 6:
            names.append(_sheet_name(body[6:], wide=biff_version == _BIFF8))
    return tuple(names), False


def _sheet_name(encoded: bytes, *, wide: bool) -> str:
    """Decode a sheet name: a short Unicode string in BIFF8, a byte string before it."""
    count = encoded[0]
    if not wide:
        return encoded[1 : 1 + count].decode("cp1252", errors="replace")
    high_byte = len(encoded) > 1 and bool(encoded[1] & 0x01)
    if high_byte:
        return encoded[2 : 2 + count * 2].decode("utf-16-le", errors="replace")
    # "Compressed" characters are UTF-16 code units whose high byte is zero, which
    # is exactly Latin-1 and not Windows-1252.
    return encoded[2 : 2 + count].decode("latin-1")


class CompoundExtractor:
    """Reads compound storage's structure, its summary properties, and a clock."""

    name = "compound"
    version = "1"
    #: A sector allocation table, a directory tree and a property set, all laid out
    #: by whoever wrote the file, and a chain in any of them can loop.
    isolation = Isolation.SUBPROCESS
    #: A header reading: the directory and the summary property streams, not the
    #: streams' own contents.
    reading_depth = ReadingDepth.HEADER

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in COMPOUND_MEDIA_TYPES:
            return True
        return bool(COMPOUND_EXTENSIONS & context.extensions)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        try:
            import olefile
        except ImportError:
            return declined(self.name, self.version, "olefile is not installed")

        limits = context.limits or Limits()
        try:
            signature = context.read(len(_SIGNATURE))
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])
        if signature != _SIGNATURE:
            # A web page or rich text saved with a legacy name is common and is not
            # this container. Declined, because the answer stays true next run.
            return declined(
                self.name, self.version, "not OLE compound storage: no compound signature"
            )

        readings: list[Reading] = []
        created: int | None = None
        try:
            with context.open() as handle, olefile.OleFileIO(handle) as storage:
                streams = storage.listdir(streams=True, storages=False)
                storages = storage.listdir(streams=False, storages=True)
                top_streams = {path[0] for path in streams if len(path) == 1}
                kind = _kind_of(top_streams)
                readings.extend(_structure_readings(streams, storages, kind))

                namespace = _NAMESPACE_OF_KIND.get(kind, "compound")
                if storage.exists(_SUMMARY_STREAM):
                    properties = storage.getproperties(
                        _SUMMARY_STREAM, convert_time=True, no_conversion=[_PID_EDIT_TIME]
                    )
                    created = _summary_readings(properties, namespace, readings)
                if kind == "table":
                    stream = "Workbook" if "Workbook" in top_streams else "Book"
                    readings.extend(_workbook_readings(storage, stream, limits))
        except Exception as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])

        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=context.size_bytes,
            event_time_ns=created,
            event_time_source=TimeSource.DOCUMENT if created is not None else None,
        )


def _kind_of(top_streams: set[str]) -> str:
    """Return what the top-level streams say the container holds."""
    if _ENCRYPTED_PACKAGE.issubset(top_streams):
        return "encrypted_package"
    return next((kind for stream, kind in _KIND_BY_STREAM if stream in top_streams), "unknown")


def _structure_readings(
    streams: list[list[str]], storages: list[list[str]], kind: str
) -> list[Reading]:
    """Return the container's own structure, which needs no knowledge of any application.

    Top-level names are listed in the order of the names themselves, which is
    canonical. Rendering a control character as an escape happens after sorting,
    so a summary stream's leading ``\\x05`` still sorts first.
    """
    exposed = ExtractionTier.MECHANICALLY_EXPOSED
    top = sorted({path[0] for path in (*streams, *storages) if path})
    readings = [
        Reading("compound.stream_count", len(streams), ValueKind.INTEGER, exposed),
        Reading("compound.storage_count", len(storages), ValueKind.INTEGER, exposed),
        Reading(
            "compound.top_level_names",
            canonical_json([_printable(name) for name in top[:MAX_NAMES_DESCRIBED]]),
            ValueKind.STRUCTURE,
            exposed,
        ),
        # The names are literal. What they say about the content is a reading of a
        # convention, and a file could name a stream anything.
        Reading("compound.content_kind", kind, ValueKind.TEXT, ExtractionTier.HEURISTIC),
    ]
    if kind == "encrypted_package":
        readings.append(Reading("compound.encrypted_package", True, ValueKind.BOOLEAN))
    return readings


def _summary_readings(
    properties: dict[int, Any], namespace: str, readings: list[Reading]
) -> int | None:
    """Append summary-property readings under a namespace and return the creation time."""
    codepage = properties.get(_PID_CODEPAGE)
    for identifier, key in _PID_TEXT.items():
        text = _decoded(properties.get(identifier), codepage)
        if text:
            readings.append(Reading(f"{namespace}.{key}", text, ValueKind.TEXT))
    created = _moment_ns(properties.get(_PID_CREATED))
    saved = _moment_ns(properties.get(_PID_SAVED))
    if created is not None:
        readings.append(Reading(f"{namespace}.created_ns", created, ValueKind.INTEGER))
    if saved is not None:
        readings.append(Reading(f"{namespace}.modified_ns", saved, ValueKind.INTEGER))
    return created


def _workbook_readings(storage: Any, stream: str, limits: Limits) -> list[Reading]:
    """Return a workbook's sheet names and encryption, read from its globals."""
    if int(storage.get_size(stream)) > limits.max_bytes_read:
        # ``olefile`` reads a whole stream to open it, so a workbook past the byte
        # ceiling is not opened at all, and that is recorded rather than implied.
        return [Reading("table.sheet_names_bounded", True, ValueKind.NEGATIVE)]
    with storage.openstream(stream) as source:
        parsed = workbook_globals(source.read(limits.max_bytes_read))
    if parsed is None:
        return [Reading("table.sheet_names_unreadable", True, ValueKind.NEGATIVE)]
    names, encrypted = parsed
    readings = [Reading("table.encrypted", encrypted, ValueKind.BOOLEAN)]
    if not encrypted:
        readings.append(Reading("table.sheet_count", len(names), ValueKind.INTEGER))
        readings.append(
            Reading(
                "table.sheet_names",
                canonical_json(list(names[:MAX_SHEETS_DESCRIBED])),
                ValueKind.STRUCTURE,
            )
        )
    return readings


def _moment_ns(value: object) -> int | None:
    """Return a converted FILETIME as nanoseconds since the epoch, or None when unset."""
    if not isinstance(value, dt.datetime) or value == _FILETIME_ZERO:
        return None
    return nanoseconds_since_epoch(value)


def _decoded(value: object, codepage: object) -> str | None:
    """Return a property's text, decoded by the property set's own code page."""
    if isinstance(value, bytes):
        text = value.decode(_encoding_of(codepage), errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return None
    cleaned = text.replace("\x00", "").strip()
    return cleaned[:_MAX_VALUE_CHARS] or None


def _encoding_of(codepage: object) -> str:
    """Return the codec for a code page property, defaulting to Windows-1252.

    The property is a signed 16-bit integer, so UTF-8's 65001 arrives as -535.
    """
    if isinstance(codepage, int) and not isinstance(codepage, bool):
        unsigned = codepage % 65536
        name = {1200: "utf-16-le", 1201: "utf-16-be", 65001: "utf-8"}.get(unsigned, f"cp{unsigned}")
        try:
            return codecs.lookup(name).name
        except LookupError:
            pass
    return "cp1252"


def _printable(name: str) -> str:
    """Return a storage name with control characters written as escapes.

    The summary streams are named with a leading ``\\x05``, and a raw control
    character in a recorded value is invisible to anybody reading it.
    """
    return "".join(
        character if character.isprintable() else f"\\x{ord(character):02x}" for character in name
    )


@EXTRACTORS.register(
    "compound",
    version="1",
    summary="OLE compound storage: structure, summary properties, sheet names, and a clock",
    requires=("olefile",),
    attributes={
        "extensions": ",".join(sorted(COMPOUND_EXTENSIONS)),
        "media_types": ",".join(sorted(COMPOUND_MEDIA_TYPES)),
    },
)
def _build_compound() -> CompoundExtractor:
    return CompoundExtractor()
