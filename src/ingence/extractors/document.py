"""PDF: metadata, page structure, a bounded sample of text, and a clock.

A PDF parser on adversarial input is the case that per-component isolation exists for. Test
fixtures that hang, allocate without bound and die exercise it. This extractor declares
:attr:`Isolation.SUBPROCESS` with one line. A hang, a crash and an exhausted address space are
each then a recorded content error, and the run completes.

It fills clock tier two, which has no source of its own. A PDF's
``CreationDate`` is the document's own metadata, it travels with the bytes, and it
is therefore more copy-robust than any filesystem clock however precise. Filling the tier is
an obligation of the extractor and not a choice.

Creation, not modification, and the difference matters. ``ModDate`` moves
when a tool rewrites the file, which a linearizer, a signature or a page rotation
all do without anybody authoring anything. ``CreationDate`` is the closest thing
a PDF has to when the document came into being. Both are recorded. Only one is
offered as the event.

Dates are parsed strictly and abstained on otherwise. PDF date strings are
``D:YYYYMMDDHHmmSSOHH'mm'`` with almost every field optional, and producers
write them wrongly often. A year alone is not an event: reading ``D:2026`` as the
first of January asserts a day nobody wrote. This is the same abstention that the clock rules
make for a year-month in a filename.

Text is sampled, not harvested. The page count bounds the work and
``Limits.max_text_chars`` bounds the output. The bound is the point: an extractor that reads a
whole 4,000-page scan to count its words has spent a budget that would have examined a
thousand other files.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, TimeSource, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    declined,
    failed,
)
from ingence.extractors.limits import Limits

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["DOCUMENT_EXTENSIONS", "DocumentExtractor", "parse_pdf_date"]

DOCUMENT_EXTENSIONS = {".pdf"}

#: ``D:YYYYMMDDHHmmSS`` with a trailing offset, every field after the year
#: optional. Written as one pattern rather than parsed field by field, because
#: the optionality is the specification and spelling it out is what makes the
#: partial cases visible.
_PDF_DATE = re.compile(
    r"""
    ^D?:?
    (?P<year>\d{4})
    (?P<month>\d{2})?
    (?P<day>\d{2})?
    (?P<hour>\d{2})?
    (?P<minute>\d{2})?
    (?P<second>\d{2})?
    \s*
    (?:
        (?P<sign>[+\-Zz])
        (?P<offset_hours>\d{2})?
        '?
        (?P<offset_minutes>\d{2})?
        '?
    )?
    $
    """,
    re.VERBOSE,
)

#: Metadata keys worth recording, and what to call them. Not every key a producer
#: might write. An arbitrary key is a string somebody's tool chose, and recording
#: all of them would make the observation table a dumping ground.
_METADATA_KEYS = {
    "/Title": "document.title",
    "/Author": "document.author",
    "/Subject": "document.subject",
    "/Keywords": "document.keywords",
    "/Creator": "document.creator",
    "/Producer": "document.producer",
}


def parse_pdf_date(raw: str) -> tuple[int, str] | None:
    """Return nanoseconds since the epoch and what was resolved, or None.

    Abstains on anything short of a day, which is the same rule that applies
    to a filename date and for the same reason: reading a bare year as the first
    of January asserts a day nobody wrote, and a wrong day is worse than no day
    because it looks like an answer.

    A missing time of day is not an abstention. A date with no time is a real
    statement about a day, and midnight UTC is how the engine already stores one.
    A missing offset is read as UTC, and recorded as such, because a PDF with no
    offset genuinely does not say and the alternative is to discard a good day.
    """
    match = _PDF_DATE.match(raw.strip())
    if match is None:
        return None
    fields = match.groupdict()
    if not fields["month"] or not fields["day"]:
        return None

    try:
        offset = _offset_of(fields)
        moment = dt.datetime(
            year=int(fields["year"]),
            month=int(fields["month"]),
            day=int(fields["day"]),
            hour=int(fields["hour"] or 0),
            minute=int(fields["minute"] or 0),
            second=int(fields["second"] or 0),
            tzinfo=offset,
        )
    except ValueError:
        # A producer wrote month 13 or day 32. A malformed date is not an event
        # and is not an error either: the file is intact and this field is not.
        return None

    resolution = "second" if fields["second"] else ("minute" if fields["minute"] else "day")
    return int(moment.timestamp() * 1_000_000_000), resolution


def _offset_of(fields: dict[str, str | None]) -> dt.timezone:
    """Return the declared UTC offset, defaulting to UTC where none is written."""
    sign = fields.get("sign")
    if not sign or sign in {"Z", "z"}:
        return dt.UTC
    hours = int(fields.get("offset_hours") or 0)
    minutes = int(fields.get("offset_minutes") or 0)
    delta = dt.timedelta(hours=hours, minutes=minutes)
    return dt.timezone(-delta if sign == "-" else delta)


class DocumentExtractor:
    """Reads a PDF's metadata, its page structure and a bounded text sample."""

    name = "document"
    version = "1"
    #: A format parser on input it did not write. This is the risk that the process boundary
    #: exists for, and declaring it is all an extractor has to do to be contained.
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return bool(DOCUMENT_EXTENSIONS & context.extensions)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return declined(self.name, self.version, "pypdf is not installed")

        limits = context.limits or Limits()
        readings: list[Reading] = []
        event_time: int | None = None
        event_source: TimeSource | None = None

        try:
            with context.open() as handle:
                reader = PdfReader(handle, strict=False)
                encrypted = bool(getattr(reader, "is_encrypted", False))
                readings.append(
                    Reading(
                        "document.encrypted",
                        encrypted,
                        ValueKind.BOOLEAN,
                        ExtractionTier.FORMALLY_EXTRACTED,
                    )
                )
                if encrypted:
                    # An encrypted PDF is a fact about the file, not a failure to
                    # read it. The structure is still countable and the text is
                    # not, and saying so is worth more than an error.
                    return ExtractorResult(
                        extractor=self.name,
                        extractor_version=self.version,
                        outcome=ScanOutcome.EXTRACTED,
                        readings=tuple(readings),
                        warnings=("encrypted: structure read, text not",),
                        bytes_read=context.size_bytes,
                    )

                pages = len(reader.pages)
                readings.append(Reading("document.page_count", pages, ValueKind.INTEGER))

                metadata: dict[str, object] = dict(reader.metadata or {})
                for key, name in _METADATA_KEYS.items():
                    value = metadata.get(key)
                    if value:
                        readings.append(Reading(name, str(value)[:500], ValueKind.TEXT))

                for key, name in (("/CreationDate", "created"), ("/ModDate", "modified")):
                    raw = metadata.get(key)
                    if not raw:
                        continue
                    parsed = parse_pdf_date(str(raw))
                    if parsed is None:
                        readings.append(
                            Reading(f"document.{name}_unparsed", str(raw)[:64], ValueKind.TEXT)
                        )
                        continue
                    nanoseconds, resolution = parsed
                    readings.append(Reading(f"document.{name}_ns", nanoseconds, ValueKind.INTEGER))
                    readings.append(
                        Reading(f"document.{name}_resolution", resolution, ValueKind.TEXT)
                    )
                    if name == "created":
                        event_time = nanoseconds
                        event_source = TimeSource.DOCUMENT

                text, sampled = _sample_text(reader, pages, limits)
                readings.append(Reading("document.pages_sampled", sampled, ValueKind.INTEGER))
                readings.append(Reading("document.character_count", len(text), ValueKind.INTEGER))
                if not text.strip():
                    # A PDF with no extractable text is very often a scan, and
                    # that is a finding rather than nothing: it permanently
                    # narrows what the file can be and says an OCR pass would be
                    # the thing that reads it.
                    readings.append(Reading("document.text_absent", True, ValueKind.NEGATIVE))
        except Exception as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])

        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=context.size_bytes,
            event_time_ns=event_time,
            event_time_source=event_source,
        )


def _sample_text(reader: object, pages: int, limits: Limits) -> tuple[str, int]:
    """Return a bounded text sample and how many pages it came from.

    From the front, not from a spread across the document. A PDF's first pages
    carry its title, its abstract and its method, which is where the vocabulary a
    Dictionary can recognise actually is; sampling the middle of a long appendix
    costs the same and says less.
    """
    collected: list[str] = []
    total = 0
    sampled = 0
    for index in range(pages):
        if total >= limits.max_text_chars:
            break
        try:
            page = reader.pages[index]  # type: ignore[attr-defined]
            extracted = page.extract_text() or ""
        except Exception:
            sampled += 1
            continue
        collected.append(extracted)
        total += len(extracted)
        sampled += 1
    return "".join(collected)[: limits.max_text_chars], sampled


@EXTRACTORS.register(
    "document",
    version="1",
    summary="PDF: metadata, page structure, a bounded text sample, and a tier-two clock",
    requires=("pypdf",),
    attributes={"extensions": ",".join(sorted(DOCUMENT_EXTENSIONS))},
)
def _build_document() -> DocumentExtractor:
    return DocumentExtractor()
