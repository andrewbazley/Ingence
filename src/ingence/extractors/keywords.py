"""A bounded reading of the words a text file uses, so its content can be searched.

Ingence stores no body text: the text extractor records encoding, counts, a first line and
references, and the text sketch a MinHash signature. A search of content needs words, and a store
of whole texts would be a copy of the pool's prose inside the project. That would duplicate
private data, the opposite of what the census does with a name. So this extractor records the most
frequent terms of a bounded sample, with their counts: at most :data:`TERM_LIMIT` terms, one
observation per file (``text.term_counts``).

The keywords themselves are not chosen here. Which of a file's terms are distinctive is a fact
about the pool (a TF-IDF needs every file's counts), so the search projection computes it from
these observations. This extractor makes a reading and the projection makes a ranking, which keeps
observations apart from what is made of them.

The extractor also reads a script's output names, so that an analysis script's plots can be named
from its code. Where the file is a script in a language ``infer/script_outputs.py`` reads (an
ImageJ macro, Python, MATLAB), the names its save calls write are recorded too
(``script.output_names``), as patterns (``*_fit.png``). Nothing is run, and a call the reading
does not know is not read. Search then finds the script by what it writes.

Tier: heuristic. Tokenising prose is rules of thumb over decoded text (``textword/prose.py``),
not a value read from a format's structure.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import EXTRACTORS, ExtractorResult, Isolation, Reading, failed
from ingence.extractors.limits import Limits
from ingence.extractors.text import TEXT_EXTENSIONS, TEXT_MEDIA_TYPES, decide_encoding
from ingence.infer.script_outputs import names_written
from ingence.textword.prose import count_terms, prose_tokens

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["SCRIPT_LANGUAGES", "TERM_LIMIT", "KeywordExtractor"]

#: The most terms one file's observation holds.
TERM_LIMIT = 200

#: Scripts whose save calls ``infer/script_outputs.py`` reads, by extension (dotted, as the
#: context's extensions are). Their terms are what a program says, marked so a search can rank them
#: apart from prose.
SCRIPT_LANGUAGES: dict[str, str] = {
    ".ijm": "imagej_macro",
    ".py": "python",
    ".m": "matlab",
}

#: Other code and markup: read, their terms marked as code rather than prose.
_CODE_EXTENSIONS = frozenset(
    {".r", ".sh", ".bat", ".ps1", ".c", ".h", ".cpp", ".java", ".js", ".sql", *SCRIPT_LANGUAGES}
)


class KeywordExtractor:
    """Counts the words of a bounded sample of a text file, and a script's output names."""

    name = "keywords"
    version = "1"
    isolation = Isolation.IN_PROCESS

    def supports(self, context: ExtractionContext) -> bool:
        media_type = context.media_type or ""
        if media_type.startswith("text/"):
            return True
        return bool(context.extensions & (TEXT_EXTENSIONS | _CODE_EXTENSIONS))

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        sample = int(getattr(limits, "text_sketch_bytes", 0) or (256 << 10))
        try:
            payload = context.read(min(limits.max_bytes_read, sample))
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))
        encoding, _ = decide_encoding(payload)
        text = payload.decode(encoding, errors="replace")
        terms = count_terms(prose_tokens(text), limit=TERM_LIMIT)
        code = bool(context.extensions & _CODE_EXTENSIONS)
        readings = [
            Reading(
                "text.term_counts",
                json.dumps(
                    {"terms": [term for term, _ in terms], "counts": [n for _, n in terms]},
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                ValueKind.STRUCTURE,
                ExtractionTier.HEURISTIC,
            ),
            Reading(
                "text.term_sample_bytes", len(payload), ValueKind.INTEGER, ExtractionTier.LITERAL
            ),
            Reading(
                "text.term_source",
                "code" if code else "prose",
                ValueKind.TEXT,
                ExtractionTier.HEURISTIC,
            ),
        ]
        language = next(
            (
                SCRIPT_LANGUAGES[ext]
                for ext in sorted(context.extensions)
                if ext in SCRIPT_LANGUAGES
            ),
            None,
        )
        if language is not None:
            patterns = sorted({written.pattern for written in names_written(text, language)})
            if patterns:
                readings.append(
                    Reading(
                        "script.output_names",
                        json.dumps(patterns, separators=(",", ":"), ensure_ascii=False),
                        ValueKind.STRUCTURE,
                        ExtractionTier.HEURISTIC,
                    )
                )
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=len(payload),
        )


@EXTRACTORS.register(
    "keywords",
    version="1",
    summary="the most frequent words of a bounded sample of text, and a script's output names",
    attributes={
        "extensions": ",".join(sorted(TEXT_EXTENSIONS | _CODE_EXTENSIONS)),
        "media_types": ",".join(TEXT_MEDIA_TYPES),
    },
)
def _build_keywords() -> KeywordExtractor:
    return KeywordExtractor()
