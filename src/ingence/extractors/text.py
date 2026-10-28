"""Text: deciding an encoding conservatively, then reading what is there.

Encoding detection on arbitrary bytes is guesswork, so this does not do it on
arbitrary bytes. It decodes only content that already looks textual, and it
prefers evidence over statistics: a byte-order mark is decisive, valid UTF-8 is
strong, and a null-interleaving pattern says which wide encoding is in play. Only
when none of those speaks does it fall back, and it records which hypothesis it
used so a downstream reader knows the text is encoding-dependent rather than
literal.

Readings from decoded text are formally extracted, not literal. The bytes
are literal, but the characters are the product of a decoding hypothesis that could
be wrong. Keeping those apart is the whole point of having a tier on every
observation, and it is what lets a report distinguish four literal byte matches
from two encoding-dependent ones.

References are recorded as strings and resolved later. A path mentioned in a
file is an observation about that file. Whether it resolves to anything is an
inference over the whole Index, and it belongs to the inference step. Recording
the string and resolving it later is what lets the engine eventually say
"this script claims to write that file, and that file does not exist", which is
a finding rather than an error.
"""

from __future__ import annotations

import codecs
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    failed,
)
from ingence.extractors.limits import Limits
from ingence.extractors.mentions import mentioned_paths_and_shapes

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["TextExtractor", "decide_encoding"]

#: Media types this extractor reads whatever a file is called, where detection recognised
#: one by content. Declared at registration, so that a processing fingerprint can see the
#: files content routing reaches. ``text/*`` is every text type.
TEXT_MEDIA_TYPES = ("text/*", "application/json", "application/xml", "application/yaml")

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".py",
    ".r",
    ".m",
    ".ijm",
    ".sh",
    ".bat",
    ".ps1",
    ".c",
    ".h",
    ".cpp",
    ".java",
    ".js",
    ".sql",
    ".ini",
    ".cfg",
    ".toml",
}

#: Byte-order marks, longest first so UTF-32 is not mistaken for UTF-16. A mark
#: is the only decisive evidence about an encoding that a file can carry.
BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decide_encoding(payload: bytes) -> tuple[str, str]:
    """Return the encoding to decode with, and the evidence that chose it.

    Evidence first, statistics last. Reported alongside every reading, so a
    consumer can tell a decoding backed by a byte-order mark from one backed by
    a guess.
    """
    for mark, encoding in BOMS:
        if payload.startswith(mark):
            return encoding, "byte_order_mark"

    sample = payload[:4096]
    if sample:
        # Null interleaving is what a wide encoding looks like to a byte reader,
        # and which side the nulls fall on says which byte order it is.
        even_nulls = sum(1 for index in range(0, len(sample), 2) if sample[index] == 0)
        odd_nulls = sum(1 for index in range(1, len(sample), 2) if sample[index] == 0)
        half = len(sample) // 2
        if half and odd_nulls > half * 0.6 and even_nulls < half * 0.1:
            return "utf-16-le", "null_interleaving"
        if half and even_nulls > half * 0.6 and odd_nulls < half * 0.1:
            return "utf-16-be", "null_interleaving"

    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return "utf-8", "valid_utf8"

    # Nothing decisive. Windows-1252 decodes every byte, so it always succeeds
    # and always might be wrong, which is exactly why the evidence is recorded
    # as a fallback rather than left to be assumed.
    return "cp1252", "fallback"


class TextExtractor:
    """Reads plain text: its encoding, its shape, and what it points at."""

    name = "text"
    #: Version 2 reports each reference's shape.
    version = "2"
    #: Decoding a bounded sample cannot hang: the read is capped and the
    #: codecs are the interpreter's own.
    isolation = Isolation.IN_PROCESS

    def supports(self, context: ExtractionContext) -> bool:
        media_type = context.media_type or ""
        if any(
            media_type.startswith(declared[:-1])
            if declared.endswith("*")
            else media_type == declared
            for declared in TEXT_MEDIA_TYPES
        ):
            return True
        return bool(context.extensions & TEXT_EXTENSIONS)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        try:
            payload = context.read(limits.max_bytes_read)
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))

        if not payload:
            return ExtractorResult(
                extractor=self.name,
                extractor_version=self.version,
                outcome=ScanOutcome.EXTRACTED,
                readings=(Reading("text.empty", None, ValueKind.NEGATIVE, ExtractionTier.LITERAL),),
            )

        encoding, evidence = decide_encoding(payload)
        try:
            text = payload.decode(encoding, errors="strict")
            clean = True
        except (UnicodeDecodeError, LookupError):
            text = payload.decode(encoding, errors="replace")
            clean = False

        # Literal, because the bytes were read directly and nothing interpreted
        # them. Everything derived from the decoded characters is one step
        # further out.
        literal = ExtractionTier.LITERAL
        decoded = ExtractionTier.FORMALLY_EXTRACTED

        readings = [
            Reading("text.encoding", encoding, ValueKind.TEXT, literal),
            Reading("text.encoding_evidence", evidence, ValueKind.TEXT, literal),
            Reading("text.decoded_cleanly", clean, ValueKind.BOOLEAN, literal),
            Reading("text.character_count", len(text), ValueKind.INTEGER, decoded),
            Reading("text.line_count", text.count("\n") + 1, ValueKind.INTEGER, decoded),
        ]

        stripped = text.strip()
        if stripped:
            first = stripped.splitlines()[0][:200]
            readings.append(Reading("text.first_line", first, ValueKind.TEXT, decoded))

        references, shapes = mentioned_paths_and_shapes(payload)
        if references:
            readings.append(
                Reading("text.reference_count", len(references), ValueKind.INTEGER, decoded)
            )

        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            references=references,
            reference_shapes=shapes,
            bytes_read=len(payload),
        )


@EXTRACTORS.register(
    "text",
    version="2",
    summary="plain text: encoding decided conservatively, shape, and references",
    attributes={
        "extensions": ",".join(sorted(TEXT_EXTENSIONS)),
        "media_types": ",".join(TEXT_MEDIA_TYPES),
    },
)
def _build_text() -> TextExtractor:
    return TextExtractor()
