"""OOXML documents and presentations: parsed text, structure, and the package's own clock.

A package the cascade already opens, read for what the cascade cannot see.
``archive.py`` opens ``.docx`` and ``.pptx`` as the ZIP archives they are, so their
parts are already derived entries. What this adds is text as a reader meets it,
paragraphs in order and slides in presentation order rather than member order, and
the properties a package declares about itself. Both run on the same file and
neither claims the other's work, exactly as the spreadsheet extractor coexists with
the cascade on a workbook. Workbooks are not claimed: ``.xlsx`` is
``spreadsheet.py``'s.

The extractor uses no library beyond ``zipfile`` and ``pyexpat``, both streaming, and the
parser refuses what the format forbids. The Open Packaging Conventions forbid a document type
declaration in any part, and every entity-expansion attack needs one, so a part
declaring one is refused before a single entity is read and recorded as a finding
rather than parsed carefully. Expat has limited amplification since 2.4, which
makes that a second layer rather than the only one.

The reads are bounded twice. Decompression stops at the byte ceiling, counted across every
part read, and a declared size cannot talk past it because the bound is on bytes
produced rather than on the size a header claims. Text stops at
``max_text_chars``. Either stop is recorded, so a count that is a lower bound says
so.

The package itself is read by :mod:`ingence.extractors.ooxml`, which the spreadsheet
extractor shares, so a document and a workbook cannot be dated two ways.

It fills clock tier two from ``dcterms:created``, the package's own metadata, which travels with the
bytes. A missing offset is read as UTC, because OOXML specifies core properties as UTC and producers
omit the designator, and a date short of a day abstains. ``dcterms:modified`` is recorded and not
offered, because any tool that rewrites the file moves it.

References are the package's declared external relationships: a linked
workbook, a linked image, a hyperlink to a file, recorded as the strings written
and resolved later like every other reference. Web and mail addresses are not
references to anything in a data pool and are not recorded.

An encrypted document is not a ZIP at all. Office stores a password-protected
package inside OLE compound storage. Those bytes are declined here with that
reason, and detection routes them by content to the compound extractor, which
records the encryption.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, TimeSource, ValueKind
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    declined,
    failed,
)
from ingence.extractors.limits import Limits
from ingence.extractors.ooxml import (
    CORE_PROPERTIES,
    CORE_RELATIONSHIP,
    MAX_VALUE_CHARS,
    Handler,
    Package,
    PartRefusedError,
    Properties,
    Relationship,
    core_readings,
    split_name,
)

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["OFFICE_EXTENSIONS", "OfficeExtractor"]

#: WordprocessingML and PresentationML packages: documents, presentations and
#: slide shows, with their macro-enabled and template variants. One structure
#: under ten names. A macro-enabled package is read exactly like any other and its
#: macro part is never opened, because Ingence never runs code it finds.
OFFICE_EXTENSIONS = {
    ".docx",
    ".docm",
    ".dotx",
    ".dotm",
    ".pptx",
    ".pptm",
    ".potx",
    ".potm",
    ".ppsx",
    ".ppsm",
}

_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: Transitional and Strict spell every namespace differently, and a reader that
#: knows only one silently reads nothing from the other.
_WORDPROCESSING = frozenset(
    {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }
)
_DRAWING = frozenset(
    {
        "http://schemas.openxmlformats.org/drawingml/2006/main",
        "http://purl.oclc.org/ooxml/drawingml/main",
    }
)
_PRESENTATION = frozenset(
    {
        "http://schemas.openxmlformats.org/presentationml/2006/main",
        "http://purl.oclc.org/ooxml/presentationml/main",
    }
)
_RELATIONSHIPS = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
)
_EXTENDED = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
        "http://purl.oclc.org/ooxml/officeDocument/extendedProperties",
    }
)

#: The extended property naming the producing application. Recorded as
#: ``document.creator``, which is what the PDF extractor calls the same fact.
_APPLICATION = {(namespace, "Application"): "creator" for namespace in _EXTENDED}

#: The core properties recorded as text, under the PDF extractor's keys.
_DOCUMENT_TEXT = ("title", "subject", "author", "keywords")

#: Relationship types are URIs whose stem differs between Transitional and Strict
#: and whose final segment agrees, so the final segment is what is compared.
_OFFICE_DOCUMENT = "officeDocument"
_EXTENDED_RELATIONSHIPS = frozenset({"extended-properties", "extendedProperties"})
_SLIDE = "slide"

#: Schemes of external targets that are not files a data pool could hold.
_NOT_FILE_SCHEMES = ("http:", "https:", "mailto:", "ftp:", "news:", "tel:")

#: Elements that are a line break or a tab where they sit in running text, and
#: the parents under which the same names are tab-stop definitions instead.
_BREAKS = frozenset({"tab", "br", "cr"})
_TAB_STOP_LISTS = frozenset({"tabs", "tabLst"})

_MAX_REFERENCES = 200


class _TextBudgetSpentError(Exception):
    """The text budget is spent. Stopping here is the bound working, not a failure."""


@dataclass
class _Body(Handler):
    """Visible text, paragraphs and tables in document order, within a character budget.

    Deleted text in a tracked change and field instructions are not what a reader
    sees, and their elements are named differently, so they are not collected.
    """

    budget: int
    root: tuple[str, str] | None = None
    characters: int = 0
    paragraphs: int = 0
    tables: int = 0
    slide_ids: list[str] = field(default_factory=list)
    _open: list[str] = field(default_factory=list)
    _in_text: bool = False

    def start(self, name: str, attributes: dict[str, str]) -> None:
        namespace, local = split_name(name)
        if self.root is None:
            self.root = (namespace, local)
        parent = self._open[-1] if self._open else ""
        self._open.append(local)
        if namespace in _WORDPROCESSING or namespace in _DRAWING:
            if local == "t":
                self._in_text = True
            elif local == "tbl":
                self.tables += 1
            elif local in _BREAKS and parent not in _TAB_STOP_LISTS:
                self._take(1)
        elif namespace in _PRESENTATION and local == "sldId":
            for attribute, value in attributes.items():
                attribute_namespace, attribute_local = split_name(attribute)
                if attribute_namespace in _RELATIONSHIPS and attribute_local == "id":
                    self.slide_ids.append(value)

    def end(self, name: str) -> None:
        namespace, local = split_name(name)
        if self._open:
            self._open.pop()
        if namespace in _WORDPROCESSING or namespace in _DRAWING:
            if local == "t":
                self._in_text = False
            elif local == "p":
                self.paragraphs += 1
                self._take(1)

    def text(self, data: str) -> None:
        if self._in_text:
            self._take(len(data))

    def _take(self, count: int) -> None:
        self.characters += count
        if self.characters >= self.budget:
            self.characters = self.budget
            raise _TextBudgetSpentError


class OfficeExtractor:
    """Reads a WordprocessingML or PresentationML package's text, structure and clock."""

    name = "office"
    version = "1"
    #: ZIP and XML the engine did not write. The parsers are the interpreter's own
    #: and the input is not, and a package can be built to make any parser work
    #: very hard.
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return bool(OFFICE_EXTENSIONS & context.extensions)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        try:
            signature = context.read(len(_OLE_SIGNATURE))
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])
        if signature == _OLE_SIGNATURE:
            return declined(
                self.name,
                self.version,
                "OLE compound storage rather than an OOXML package, which is how Office "
                "stores an encrypted document; the compound extractor reads it",
            )

        try:
            with context.open() as handle, zipfile.ZipFile(handle) as archive:
                return self._read(Package(archive, remaining=limits.max_bytes_read), limits)
        except Exception as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])

    def _read(self, package: Package, limits: Limits) -> ExtractorResult:
        readings: list[Reading] = []
        references: set[str] = set()
        event: tuple[int, TimeSource] | None = None
        body = _Body(budget=limits.max_text_chars)
        text_bounded = False

        try:
            roots = package.relationships("")
            main = next(
                (part for _, kind, part, ext in roots if kind == _OFFICE_DOCUMENT and not ext),
                None,
            )
            if main is None:
                # The name claimed a document and the bytes are some other ZIP. A
                # content error, as an impostor workbook is for the spreadsheet
                # extractor: this was attempted and could not be read as claimed.
                return failed(
                    self.name, self.version, "NotAnOfficePackage", "the package names no main part"
                )

            for _, kind, part, external in roots:
                if external:
                    continue
                if kind == CORE_RELATIONSHIP:
                    core = Properties(dict(CORE_PROPERTIES))
                    package.stream(part, core)
                    event = core_readings(
                        core.values, readings, namespace="document", text_keys=_DOCUMENT_TEXT
                    )
                elif kind in _EXTENDED_RELATIONSHIPS:
                    extended = Properties(dict(_APPLICATION))
                    package.stream(part, extended)
                    if extended.values.get("creator"):
                        creator = extended.values["creator"][:MAX_VALUE_CHARS]
                        readings.append(Reading("document.creator", creator, ValueKind.TEXT))

            try:
                package.stream(main, body)
            except _TextBudgetSpentError:
                text_bounded = True
            main_relationships = package.relationships(main)
            references.update(_external_targets(main_relationships))

            if body.root is None or body.root not in _MAIN_ROOTS:
                return failed(
                    self.name,
                    self.version,
                    "NotAnOfficeDocument",
                    "the main part is not a word-processing document or a presentation",
                )

            if body.root[0] in _PRESENTATION:
                slides = _slides_in_order(main_relationships, body.slide_ids)
                readings.append(Reading("document.slide_count", len(slides), ValueKind.INTEGER))
                sampled = 0
                for slide in slides:
                    if text_bounded or package.remaining <= 0:
                        break
                    sampled += 1
                    try:
                        package.stream(slide, body)
                    except _TextBudgetSpentError:
                        text_bounded = True
                    references.update(_external_targets(package.relationships(slide)))
                readings.append(Reading("document.slides_sampled", sampled, ValueKind.INTEGER))
        except PartRefusedError as exc:
            # A finding rather than a mess to tidy away, on the archive extractor's
            # precedent for a traversing member: recorded, and nothing more is read.
            readings.append(Reading("document.part_refused", True, ValueKind.NEGATIVE))
            return self._result(readings, (), (str(exc),), package, event)

        readings.append(Reading("document.paragraph_count", body.paragraphs, ValueKind.INTEGER))
        readings.append(Reading("document.table_count", body.tables, ValueKind.INTEGER))
        readings.append(Reading("document.character_count", body.characters, ValueKind.INTEGER))
        bounded = text_bounded or package.bounded
        readings.append(Reading("document.text_bounded", bounded, ValueKind.BOOLEAN))
        if body.characters == 0 and not bounded:
            readings.append(Reading("document.text_absent", True, ValueKind.NEGATIVE))
        ordered = tuple(sorted(references))[:_MAX_REFERENCES]
        if ordered:
            readings.append(
                Reading("document.external_reference_count", len(ordered), ValueKind.INTEGER)
            )
        return self._result(readings, ordered, (), package, event)

    def _result(
        self,
        readings: list[Reading],
        references: tuple[str, ...],
        warnings: tuple[str, ...],
        package: Package,
        event: tuple[int, TimeSource] | None,
    ) -> ExtractorResult:
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            references=references,
            warnings=warnings,
            bytes_read=package.consumed,
            event_time_ns=event[0] if event else None,
            event_time_source=event[1] if event else None,
        )


#: The root elements of a main part this extractor reads.
_MAIN_ROOTS = frozenset(
    {(namespace, "document") for namespace in _WORDPROCESSING}
    | {(namespace, "presentation") for namespace in _PRESENTATION}
)


def _slides_in_order(relationships: list[Relationship], slide_ids: list[str]) -> list[str]:
    """Return slide parts in the order the presentation lists them.

    Presentation order, not member order: ``slide10.xml`` sorts before
    ``slide2.xml``, and nobody reading the deck ever sees it that way.
    """
    targets = {
        identifier: part
        for identifier, kind, part, external in relationships
        if kind == _SLIDE and not external
    }
    return [targets[identifier] for identifier in slide_ids if identifier in targets]


def _external_targets(relationships: list[Relationship]) -> set[str]:
    """Return external targets that could name a file, exactly as written."""
    return {
        target
        for _, _, target, external in relationships
        if external and target and not target.casefold().startswith(_NOT_FILE_SCHEMES)
    }


@EXTRACTORS.register(
    "office",
    version="1",
    summary="OOXML documents and presentations: parsed text, structure, and a tier-two clock",
    attributes={"extensions": ",".join(sorted(OFFICE_EXTENSIONS))},
)
def _build_office() -> OfficeExtractor:
    return OfficeExtractor()
