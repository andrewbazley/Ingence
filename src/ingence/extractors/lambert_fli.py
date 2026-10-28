"""Lambert Instruments LI-FLIM: the acquisition time, read from the file's own text header.

A tier-one clock, read from one header field. An LI-FLIM file opens with a text header of
``{BLOCK}`` and ``[SECTION]`` lines ending at ``{END}``, and its pixels start at the next
byte. The ``[TIMESTAMPS]`` section writes one line per time point, each a Windows
FILETIME's high and low 32-bit halves as two decimal numbers. A FILETIME is UTC by
definition. On a real file it fell seconds after the local time in the file's
name and seconds before its modification time. The earliest time point is the clock.

Claimed by name, confirmed by content. Autodesk's FLIC animations also end in ``.fli``, so a file
whose first bytes are not ``{FLIMIMAGE}`` is declined rather than misread. Tika's detector reads an
LI-FLIM file as an animation.

The header accounts for the file. Its layout names the sample type, the dimensions,
and whether a dark image follows, and header plus pixels plus dark image equals the size of
a well-formed file exactly. Whether it does is recorded, because a file that does not match
its own layout is truncated, or is not what it says.

Standard library only, and bounded. The header is read in four-kilobyte blocks until
``{END}``, within the byte ceiling, so no more than the rest of the block holding ``{END}``
is read past it and no pixel is ever interpreted. A header that does not end within the
ceiling is recorded as bounded and offers no clock. The comments block, which holds
whatever somebody typed, is never recorded.
"""

from __future__ import annotations

import re
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

__all__ = ["FLI_EXTENSIONS", "FLI_MEDIA_TYPES", "LiFlimExtractor"]

FLI_EXTENSIONS = {".fli"}

#: What detection calls an LI-FLIM file when it recognises the header.
FLI_MEDIA_TYPES = {"application/x-lambert-flim"}

_HEADER_START = b"{FLIMIMAGE}"
_HEADER_END = b"{END}"
_READ_BYTES = 4096
#: Bytes per sample for the types whose width is certain. Any other type leaves the
#: layout check unmade rather than guessed.
_SAMPLE_BYTES = {"UINT8": 1, "UINT16": 2}
_PIXEL_DIMENSIONS = ("channels", "x", "y", "z", "phases", "frequencies", "timestamps")
_LAYOUT_COUNTS = (
    ("x", "image.width"),
    ("y", "image.height"),
    ("channels", "image.channels"),
    ("phases", "image.phases"),
    ("frequencies", "image.frequencies"),
)
_TIME_POINT = re.compile(r"t\d{1,6}")
_FILETIME_HALVES = re.compile(r"(\d{1,10})\s+(\d{1,10})")
_MAX_TEXT_CHARS = 64


class _DeclinedError(Exception):
    """Bytes this extractor chose not to read, carrying the reason."""


class _CorruptError(ValueError):
    """An LI-FLIM file whose header is not as the format writes it."""


@dataclass(slots=True)
class _Found:
    """What a read found before it finished or was stopped."""

    clock: int | None = None


class LiFlimExtractor:
    """Reads an LI-FLIM header's layout, its time points, and the acquisition clock."""

    name = "lambert_fli"
    version = "1"
    #: A text header read until a marker that a malformed file may never write.
    isolation = Isolation.SUBPROCESS
    #: A header reading. The INI-shaped header carries the timestamps, and the image
    #: planes are not read.
    reading_depth = ReadingDepth.HEADER

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type in FLI_MEDIA_TYPES:
            return True
        return bool(FLI_EXTENSIONS & context.extensions)

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
                    readings.append(Reading("image.header_bounded", True, ValueKind.NEGATIVE))
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
            event_time_ns=found.clock,
            event_time_source=TimeSource.INSTRUMENT if found.clock is not None else None,
        )


def _read(reader: CountingReader, readings: list[Reading], found: _Found) -> None:
    """Read an LI-FLIM header into readings, recording the earliest time point on ``found``."""
    exposed = ExtractionTier.MECHANICALLY_EXPOSED
    header, pixels_start = _header(reader)
    sections = _sections(header)
    information = sections.get("FLIMIMAGE/INFO", {})
    layout = sections.get("FLIMIMAGE/LAYOUT", {})

    version = information.get("version")
    if version:
        readings.append(
            Reading("image.format_version", version[:_MAX_TEXT_CHARS], ValueKind.TEXT, exposed)
        )
    for key, reading in _LAYOUT_COUNTS:
        count = _count(layout.get(key))
        if count is not None:
            readings.append(Reading(reading, count, ValueKind.INTEGER))
    sample_type = layout.get("datatype")
    if sample_type:
        readings.append(Reading("image.sample_type", sample_type[:_MAX_TEXT_CHARS], ValueKind.TEXT))
    dark = layout.get("hasDarkImage")
    if dark in {"0", "1"}:
        readings.append(Reading("image.dark_image", dark == "1", ValueKind.BOOLEAN))
    image = _pixel_bytes(layout)
    background = _pixel_bytes(sections.get("FLIMIMAGE/BACKGROUND", {})) if dark == "1" else 0
    if image is not None and background is not None:
        accounted = pixels_start + image + background == reader.size
        readings.append(
            Reading("image.layout_accounts_for_file", accounted, ValueKind.BOOLEAN, exposed)
        )
    _time_points(sections.get("FLIMIMAGE/TIMESTAMPS", {}), readings, found)


def _header(reader: CountingReader) -> tuple[str, int]:
    """Return the header's text and the offset its pixels start at, reading block by block."""
    buffer = bytearray()
    while True:
        remaining = reader.size - len(buffer)
        if remaining <= 0:
            message = "the header never reaches {END}"
            raise _CorruptError(message)
        room = reader.limit - reader.bytes_read
        if room <= 0:
            message = "the header does not reach {END} within the byte ceiling"
            raise LimitExceededError("bytes_read", message)
        block = reader.read_at(len(buffer), min(_READ_BYTES, remaining, room))
        if not buffer and not block.startswith(_HEADER_START):
            message = "not a Lambert Instruments LI-FLIM file; Autodesk FLIC animations share .fli"
            raise _DeclinedError(message)
        search_from = max(0, len(buffer) - len(_HEADER_END) + 1)
        buffer += block
        marker = buffer.find(_HEADER_END, search_from)
        if marker >= 0:
            return buffer[:marker].decode("latin-1"), marker + len(_HEADER_END)


def _sections(header: str) -> dict[str, dict[str, str]]:
    """Return every section's keys and values, by ``BLOCK/SECTION``, the comments block excluded.

    A key repeated within one section keeps its first value.
    """
    sections: dict[str, dict[str, str]] = {}
    block = ""
    section = ""
    for line in header.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            block, section = stripped[1:-1].strip(), ""
        elif stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
        elif "=" in stripped and block != "COMMENTS":
            key, _, value = stripped.partition("=")
            sections.setdefault(f"{block}/{section}", {}).setdefault(key.strip(), value.strip())
    return sections


def _count(text: str | None) -> int | None:
    """Return a layout count, or None when it is absent or not a plain decimal number."""
    if text is None or not (text.isascii() and text.isdigit()) or len(text) > 9:
        return None
    return int(text)


def _pixel_bytes(table: dict[str, str]) -> int | None:
    """Return the bytes a layout's pixels occupy, or None when any part of it is unknown."""
    width = _SAMPLE_BYTES.get(table.get("datatype", "").upper())
    if width is None:
        return None
    total = width
    for dimension in _PIXEL_DIMENSIONS:
        count = _count(table.get(dimension, "1"))
        if count is None:
            return None
        total *= count
    return total


def _time_points(table: dict[str, str], readings: list[Reading], found: _Found) -> None:
    """Record the time points, and the earliest as the clock, or that there is none."""
    exposed = ExtractionTier.MECHANICALLY_EXPOSED
    moments: list[int] = []
    unparsed = 0
    for key, value in table.items():
        if not _TIME_POINT.fullmatch(key):
            continue
        halves = _FILETIME_HALVES.fullmatch(value)
        moment = None
        if halves is not None:
            high, low = int(halves.group(1)), int(halves.group(2))
            if high < 1 << 32 and low < 1 << 32:
                moment = filetime_ns((high << 32) | low)
        if moment is None:
            unparsed += 1
        else:
            moments.append(moment)
    readings.append(Reading("image.timestamp_count", len(moments), ValueKind.INTEGER, exposed))
    if unparsed:
        readings.append(Reading("image.timestamps_unparsed", unparsed, ValueKind.INTEGER, exposed))
    if not moments:
        readings.append(Reading("image.acquisition_time_absent", True, ValueKind.NEGATIVE))
        return
    found.clock = min(moments)
    readings.extend(
        (
            Reading("image.acquired_ns", found.clock, ValueKind.INTEGER),
            Reading("image.acquired_resolution", "subsecond", ValueKind.TEXT),
            Reading("image.acquired_source", "[TIMESTAMPS], earliest", ValueKind.TEXT, exposed),
        )
    )
    if len(moments) > 1:
        readings.append(Reading("image.acquired_last_ns", max(moments), ValueKind.INTEGER))


@EXTRACTORS.register(
    "lambert_fli",
    version="1",
    summary="Lambert Instruments LI-FLIM: the earliest time point and the header's layout",
    attributes={"extensions": ",".join(sorted(FLI_EXTENSIONS))},
)
def _build_lambert_fli() -> LiFlimExtractor:
    return LiFlimExtractor()
