"""The Open Packaging Conventions, read once for the extractors that share them.

A document and a workbook are the same container. A WordprocessingML document, a
PresentationML deck and a SpreadsheetML workbook are each a ZIP of XML parts, found
through relationship parts, with the package's own properties in a core part. The
office extractor reads the first two and the spreadsheet extractor the third, and both
offer the package's creation date as a tier-two clock. Reading that date two ways is
how two extractors come to disagree about one file. So both read the package through this
module, as every W3C-DTF date is read through ``clocks.py``.

This module is not an extractor. Nothing here is registered, and deleting either extractor leaves
this in use by the other.

The parser refuses what the format forbids. The Open Packaging Conventions forbid a
document type declaration in every part, and every entity-expansion attack needs one, so
a part declaring one is refused before a single entity is read. Expat has limited
amplification since 2.4, which makes that a second layer rather than the only one.

The reads are bounded. Decompression stops at the byte ceiling a package is opened with, counted
across every part read, and a declared size cannot talk past it because the bound is on
bytes produced rather than on the size a header claims. Where the ceiling stops a read,
the package records it.
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field

from ingence.core.outcomes import TimeSource, ValueKind
from ingence.extractors.base import Reading
from ingence.extractors.clocks import parse_w3cdtf
from ingence.extractors.limits import UnsafeMemberPathError, safe_member_path

__all__ = [
    "CORE_PROPERTIES",
    "CORE_RELATIONSHIP",
    "MAX_VALUE_CHARS",
    "Handler",
    "Package",
    "PartRefusedError",
    "Properties",
    "Relationship",
    "core_readings",
    "split_name",
]

_DC = "http://purl.org/dc/elements/1.1/"
_DCTERMS = "http://purl.org/dc/terms/"
_CORE = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"

#: Core properties recorded, and the content-named key each becomes. The same keys the
#: PDF extractor writes, so a rule reading a title reads one key whatever format the
#: file arrived in.
CORE_PROPERTIES = {
    (_DC, "title"): "title",
    (_DC, "subject"): "subject",
    (_DC, "creator"): "author",
    (_CORE, "keywords"): "keywords",
    (_DCTERMS, "created"): "created",
    (_DCTERMS, "modified"): "modified",
}

#: The final segment of the core-properties relationship type. Relationship types are
#: URIs whose stem differs between Transitional and Strict and whose final segment
#: agrees, so the final segment is what is compared.
CORE_RELATIONSHIP = "core-properties"

#: How much of one property's text is recorded.
MAX_VALUE_CHARS = 500

_CHUNK_BYTES = 1 << 16

#: One relationship: identifier, the type's final segment, target, whether external.
type Relationship = tuple[str, str, str, bool]


class PartRefusedError(Exception):
    """A part declared a document type, which OPC forbids in every part."""


def _refuse_doctype(
    name: str, system_id: str | None, public_id: str | None, has_internal_subset: bool
) -> None:
    del system_id, public_id, has_internal_subset
    message = f"declares a document type {name!r}, which OPC forbids in every part"
    raise PartRefusedError(message)


def split_name(qualified: str) -> tuple[str, str]:
    """Return an expat name's namespace and local name."""
    namespace, _, local = qualified.rpartition(" ")
    return namespace, local


class Handler:
    """Receives one part's parse events. Subclasses keep what they need."""

    def start(self, name: str, attributes: dict[str, str]) -> None:
        del name, attributes

    def end(self, name: str) -> None:
        del name

    def text(self, data: str) -> None:
        del data


@dataclass
class _Relationships(Handler):
    """A relationships part, as written."""

    found: list[Relationship] = field(default_factory=list)

    def start(self, name: str, attributes: dict[str, str]) -> None:
        if split_name(name)[1] != "Relationship":
            return
        self.found.append(
            (
                attributes.get("Id", ""),
                attributes.get("Type", "").rstrip("/").rpartition("/")[2],
                attributes.get("Target", ""),
                attributes.get("TargetMode", "") == "External",
            )
        )


@dataclass
class Properties(Handler):
    """Core or extended properties: the text of each wanted element, first occurrence."""

    wanted: dict[tuple[str, str], str]
    values: dict[str, str] = field(default_factory=dict)
    _current: str | None = None
    _parts: list[str] = field(default_factory=list)

    def start(self, name: str, attributes: dict[str, str]) -> None:
        del attributes
        key = self.wanted.get(split_name(name))
        if key is not None and key not in self.values:
            self._current, self._parts = key, []

    def end(self, name: str) -> None:
        if self._current is not None and self.wanted.get(split_name(name)) == self._current:
            self.values[self._current] = "".join(self._parts).strip()
            self._current = None

    def text(self, data: str) -> None:
        if self._current is not None:
            self._parts.append(data)


@dataclass
class Package:
    """One package being read, and the byte ceiling every part read from it shares."""

    archive: zipfile.ZipFile
    remaining: int
    consumed: int = 0
    bounded: bool = False
    _names: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._names = {name.casefold(): name for name in self.archive.namelist()}

    def stream(self, part: str, handler: Handler) -> None:
        """Feed one part to expat, stopping at the shared byte ceiling."""
        member = self._names.get(part.casefold())
        if member is None:
            return
        if self.remaining <= 0:
            self.bounded = True
            return
        # Imported here and not at the top, measured rather than tidied: every
        # isolated child imports this module whatever format it was started for,
        # and the parser costs 1.8 ms each time it is imported.
        from xml.parsers import expat

        parser = expat.ParserCreate(namespace_separator=" ")
        parser.buffer_text = True
        parser.StartDoctypeDeclHandler = _refuse_doctype
        parser.StartElementHandler = handler.start
        parser.EndElementHandler = handler.end
        parser.CharacterDataHandler = handler.text
        with self.archive.open(member) as source:
            while True:
                chunk = source.read(min(_CHUNK_BYTES, self.remaining))
                if not chunk:
                    parser.Parse(b"", True)
                    return
                self.remaining -= len(chunk)
                self.consumed += len(chunk)
                parser.Parse(chunk, False)
                if self.remaining > 0:
                    continue
                if source.read(1):
                    # Stopped by the ceiling partway through a part. The XML is
                    # unfinished by construction, so it is neither closed nor an
                    # error, and the stop is recorded.
                    self.bounded = True
                    return
                parser.Parse(b"", True)
                return

    def relationships(self, source_part: str) -> list[Relationship]:
        """Return a part's relationships, with internal targets resolved to part names."""
        handler = _Relationships()
        self.stream(_relationships_part_of(source_part), handler)
        resolved: list[Relationship] = []
        for identifier, kind, target, external in handler.found:
            if external:
                resolved.append((identifier, kind, target, True))
                continue
            part = _resolve(source_part, target)
            if part is not None:
                resolved.append((identifier, kind, part, False))
        return resolved


def _relationships_part_of(part: str) -> str:
    """Return where OPC keeps a part's relationships. The package's own are ``_rels/.rels``."""
    directory, _, name = part.rpartition("/")
    return f"{directory}/_rels/{name}.rels" if directory else f"_rels/{name}.rels"


def _resolve(source_part: str, target: str) -> str | None:
    """Resolve an internal relationship target against its source, refusing an escape."""
    if target.startswith("/"):
        joined = target.lstrip("/")
    else:
        directory = source_part.rpartition("/")[0]
        joined = posixpath.join(directory, target) if directory else target
    try:
        return safe_member_path(joined)
    except UnsafeMemberPathError:
        return None


def core_readings(
    values: dict[str, str],
    readings: list[Reading],
    *,
    namespace: str,
    text_keys: tuple[str, ...],
) -> tuple[int, TimeSource] | None:
    """Append a core part's readings under a namespace, and return the clock it offers, if any.

    The creation date is the clock and the modification date is not, because any
    tool that rewrites the file moves the second. A missing offset is read as UTC,
    because OOXML specifies core properties as UTC and producers omit the designator, and
    a date short of a day abstains and is recorded as written. A property the
    part does not state is not recorded at all.
    """
    event: tuple[int, TimeSource] | None = None
    for key in text_keys:
        value = values.get(key)
        if value:
            readings.append(Reading(f"{namespace}.{key}", value[:MAX_VALUE_CHARS], ValueKind.TEXT))
    for key in ("created", "modified"):
        raw = values.get(key)
        if not raw:
            continue
        parsed = parse_w3cdtf(raw)
        if parsed is None:
            readings.append(Reading(f"{namespace}.{key}_unparsed", raw[:64], ValueKind.TEXT))
            continue
        nanoseconds, resolution = parsed
        readings.append(Reading(f"{namespace}.{key}_ns", nanoseconds, ValueKind.INTEGER))
        readings.append(Reading(f"{namespace}.{key}_resolution", resolution, ValueKind.TEXT))
        if key == "created":
            event = (nanoseconds, TimeSource.DOCUMENT)
    return event
