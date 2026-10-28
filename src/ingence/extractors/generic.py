"""The universal byte-level baseline: string harvesting and multi-encoding scanning.

This is the layer that works on everything, including formats nobody wrote a
parser for, and it is the most defensible inspection the engine does. A format
extractor can be wrong about what a file means. This can only be wrong about
what bytes are present, and it usually is not.

What it can and cannot say. Finding the bytes of a word ten times licenses
exactly one statement:

    Ten byte-level representations of this term appear in this file or in a
    stream mechanically derived from it.

It does not license "the document visibly contains this word ten times".
Those are different claims and they stay different: text can be compressed,
split across records, mapped through fonts, or drawn as pixels, and the same
visible word can produce zero byte matches or several. Every reading here carries
the encoding hypothesis that produced it so the difference is never lost.

Several encodings produce the same bytes, and that must not be counted twice.
For an ordinary ASCII term, the ASCII, UTF-8 and Windows-1252 patterns are
byte-identical. Searching for all three would report three matches where one
occurrence exists. Patterns are therefore deduplicated by their bytes before any
search happens.

The scan is gated on the Dictionary, not on a language. Scanning for every
English word produces noise proportional to the size of English. Scanning for the
terms this project actually knows produces evidence, and costs what the
Dictionary costs. With an empty Dictionary the term list is empty and the
scan does the aggregate work only.

String harvesting is bounded because exhaustive harvesting is useless. A
generalised strings pass over a large binary produces tens of thousands of
fragments, almost all meaningless, and recording them all would bury the few that
matter. Aggregates are always recorded; individual runs are recorded only as a
bounded sample and where they match a known term.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json
from ingence.core.outcomes import ScanOutcome, SourceScope, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    failed,
)
from ingence.extractors.limits import Limits

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ingence.extractors.base import ExtractionContext

__all__ = [
    "ENCODINGS",
    "GenericExtractor",
    "StringRun",
    "encode_term",
    "harvest_strings",
    "scan_for_terms",
]

#: Encodings a term is searched for, cheapest and most literal first. The order
#: matters only for labelling: when two encodings produce identical bytes the
#: first one named is the label reported, because reporting "utf-32-be" for what
#: is plainly ASCII would be true and useless.
ENCODINGS: tuple[tuple[str, str], ...] = (
    ("ascii", "ascii"),
    ("utf-8", "utf-8"),
    ("cp1252", "cp1252"),
    ("utf-16-le", "utf-16-le"),
    ("utf-16-be", "utf-16-be"),
    ("utf-32-le", "utf-32-le"),
    ("utf-32-be", "utf-32-be"),
)

#: Shortest run of printable characters worth recording. Below this, ordinary
#: binary data produces runs constantly and none of them means anything.
MIN_RUN_CHARS = 6

#: Most individual runs recorded as a sample. The aggregate counts are always
#: recorded. This bounds what is stored verbatim.
MAX_SAMPLE_RUNS = 32

#: Longest run kept verbatim. A single enormous run would otherwise dominate the
#: record and tell nobody anything they could not learn by opening the file.
MAX_RUN_CHARS = 240

_ASCII_RUN = re.compile(rb"[\x20-\x7e\t]{%d,}" % MIN_RUN_CHARS)
_WIDE_LE_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % MIN_RUN_CHARS)
_WIDE_BE_RUN = re.compile(rb"(?:\x00[\x20-\x7e]){%d,}" % MIN_RUN_CHARS)


@dataclass(frozen=True, slots=True)
class StringRun:
    """One island of readable text found in arbitrary bytes."""

    text: str
    #: Offset in the stream that was scanned. Retained so a person can go and
    #: look, which is the difference between a finding and an assertion.
    offset: int
    encoding: str


def encode_term(term: str) -> dict[bytes, str]:
    """Return the byte patterns to search for a term, deduplicated.

    Keyed by the bytes, so an ASCII term that encodes identically under three
    codecs yields one pattern rather than three. Without this, every ordinary
    English term would be counted once per encoding that happens to agree, and a
    report would claim three occurrences where one exists.
    """
    patterns: dict[bytes, str] = {}
    for label, codec in ENCODINGS:
        try:
            raw = term.encode(codec)
        except (UnicodeEncodeError, LookupError):
            continue
        if raw and raw not in patterns:
            patterns[raw] = label
    return patterns


def _chunks(
    handle: object, *, limit: int, chunk_size: int, overlap: int
) -> Iterator[tuple[int, bytes]]:
    """Yield overlapping windows over a stream, with the offset of each.

    Overlapping, and by at least the longest pattern minus one byte. A term
    beginning at the end of one chunk and finishing in the next is invisible to
    a scanner that reads adjacent blocks, and that is not a rare case: it happens
    once per chunk boundary per term on average.
    """
    read = handle.read  # type: ignore[attr-defined]
    offset = 0
    remaining = limit
    carry = b""
    while remaining > 0:
        block = read(min(chunk_size, remaining))
        if not block:
            break
        remaining -= len(block)
        window = carry + block
        yield offset - len(carry), window
        carry = window[-overlap:] if overlap else b""
        offset += len(block)


def scan_for_terms(
    handle: object,
    terms: Sequence[str],
    *,
    limit: int,
    chunk_size: int = 1 << 20,
) -> tuple[dict[str, dict[str, int]], int]:
    """Count byte-level occurrences of each term, per encoding.

    Returns counts keyed by term then encoding, and the bytes read. One pass over
    the stream for every term together rather than one pass per term, which is
    the difference between usable and not once a Dictionary has more than a
    handful of entries.
    """
    patterns: dict[bytes, tuple[str, str]] = {}
    for term in terms:
        for raw, label in encode_term(term).items():
            # First term wins a shared pattern. Two terms encoding to the same
            # bytes is pathological, and attributing a match to both would
            # double-count it.
            patterns.setdefault(raw, (term, label))
    if not patterns:
        return {}, 0

    overlap = max(len(raw) for raw in patterns) - 1
    counts: dict[str, dict[str, int]] = {}
    read = 0

    automaton = _build_automaton(patterns)
    for _, window in _chunks(handle, limit=limit, chunk_size=chunk_size, overlap=overlap):
        read += len(window) - overlap if read else len(window)
        for raw in _search(window, patterns, automaton):
            term, label = patterns[raw]
            counts.setdefault(term, {}).setdefault(label, 0)
            counts[term][label] += 1
    return counts, read


def _build_automaton(patterns: dict[bytes, tuple[str, str]]) -> object | None:
    """Return a compiled multi-pattern automaton, or None when unavailable.

    Aho-Corasick finds every pattern in one pass regardless of how many there
    are. The fallback searches each pattern separately, which is correct and
    scales badly, so it exists to keep the engine working on a base install
    rather than to be the normal path.
    """
    try:
        import ahocorasick
    except ImportError:
        return None
    automaton = ahocorasick.Automaton()
    for raw in patterns:
        # Latin-1 rather than a byte key, and this is not an encoding decision.
        # The library accepts only string keys, and Latin-1 maps each of the 256
        # byte values to exactly one codepoint and back. Decoding both the
        # patterns and the window through it is therefore a lossless byte-level
        # search that happens to be spelled in strings, with offsets still
        # one-to-one. Any other codec would be a genuine encoding step and would
        # silently change what is being searched for.
        automaton.add_word(raw.decode("latin-1"), raw)
    automaton.make_automaton()
    return automaton  # type: ignore[no-any-return]


def _search(
    window: bytes, patterns: dict[bytes, tuple[str, str]], automaton: object | None
) -> Iterator[bytes]:
    """Yield one pattern per occurrence within a window."""
    if automaton is not None:
        for _, raw in automaton.iter(window.decode("latin-1")):  # type: ignore[attr-defined]
            yield raw
        return
    for raw in patterns:
        start = window.find(raw)
        while start >= 0:
            yield raw
            start = window.find(raw, start + 1)


def harvest_strings(payload: bytes, *, base_offset: int = 0) -> list[StringRun]:
    """Extract islands of readable text from arbitrary bytes.

    Three passes: contiguous printable ASCII, and printable ASCII interleaved
    with nulls in each byte order, which is what UTF-16 looks like to a scanner.
    Every run keeps its offset, because a run without a location is a claim
    nobody can check.
    """
    runs: list[StringRun] = []
    for pattern, encoding in (
        (_ASCII_RUN, "ascii"),
        (_WIDE_LE_RUN, "utf-16-le"),
        (_WIDE_BE_RUN, "utf-16-be"),
    ):
        for match in pattern.finditer(payload):
            # Replacement rather than strict: a run matched the pattern, so it is
            # readable by construction, and an odd trailing byte should shorten
            # the run rather than discard it.
            text = match.group().decode(encoding, "replace")[:MAX_RUN_CHARS]
            runs.append(StringRun(text=text, offset=base_offset + match.start(), encoding=encoding))
    runs.sort(key=lambda run: (run.offset, run.encoding))
    return _resolve_byte_order(runs)


def _resolve_byte_order(runs: list[StringRun]) -> list[StringRun]:
    """Drop the losing byte-order reading of wide text.

    Printable ASCII interleaved with nulls matches the little-endian pattern and
    also matches the big-endian one starting one byte later, so every wide run is
    found twice under opposite hypotheses. Keeping both would double-count every
    string in a Windows-authored file.

    A file is one byte order or the other, essentially never both, so the reading
    with more total text wins for the whole payload. Where they tie, little-endian
    is kept: it is overwhelmingly the common case, and a tie means the evidence
    genuinely does not distinguish them.
    """
    little = [run for run in runs if run.encoding == "utf-16-le"]
    big = [run for run in runs if run.encoding == "utf-16-be"]
    if not little or not big:
        return runs
    losing = (
        "utf-16-be"
        if sum(map(len, (r.text for r in little))) >= sum(map(len, (r.text for r in big)))
        else "utf-16-le"
    )
    return [run for run in runs if run.encoding != losing]


class GenericExtractor:
    """Byte-level inspection that applies to every file, whatever it is.

    Registered claiming no extension, which makes it a universal fallback: it is
    in scope for every file and its version enters every file's processing
    fingerprint. That is correct and deliberate, since a change to how the
    baseline reads bytes genuinely does invalidate every record.
    """

    name = "generic"
    version = "1"
    #: Entropy, printable runs and a term scan over a capped sample. All
    #: bounded, and the whole reason isolation is declared rather than
    #: universal: making this pay for a process would cost more than every
    #: risk it carries.
    isolation = Isolation.IN_PROCESS

    def supports(self, context: ExtractionContext) -> bool:
        del context
        return True

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        readings: list[Reading] = []
        # Depth zero is the file itself, so a match is exact bytes in the
        # original. Deeper is a stream the engine produced by decompressing or
        # unpacking, which is one step further from what is on the disk.
        tier = ExtractionTier.LITERAL if context.depth == 0 else ExtractionTier.MECHANICALLY_EXPOSED

        try:
            payload = context.read(limits.max_bytes_read)
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))

        runs = harvest_strings(payload)
        readings.append(Reading("strings.run_count", len(runs), ValueKind.INTEGER, tier))
        if runs:
            longest = max(runs, key=lambda run: len(run.text))
            readings.append(
                Reading("strings.longest_chars", len(longest.text), ValueKind.INTEGER, tier)
            )
            readings.append(
                Reading(
                    "strings.total_chars",
                    sum(len(run.text) for run in runs),
                    ValueKind.INTEGER,
                    tier,
                )
            )
            # A bounded sample of the longest runs. Longest because a long run of
            # readable text is far more likely to be meaningful than a short one,
            # which is as often an accidental collision in binary data.
            sample = sorted(runs, key=lambda run: -len(run.text))[:MAX_SAMPLE_RUNS]
            readings.append(
                Reading(
                    "strings.sample",
                    canonical_json(
                        [
                            {"text": run.text, "offset": run.offset, "encoding": run.encoding}
                            for run in sorted(sample, key=lambda run: run.offset)
                        ]
                    ),
                    ValueKind.STRUCTURE,
                    tier,
                )
            )
        else:
            # A negative result, stored rather than discarded. "No readable text"
            # permanently narrows what this file can be, and an engine that threw
            # it away would ask the same question again on every run.
            readings.append(Reading("strings.none_found", None, ValueKind.NEGATIVE, tier))

        bytes_read = len(payload)
        if context.terms:
            try:
                with context.open() as handle:
                    counts, scanned = scan_for_terms(
                        handle, context.terms, limit=limits.max_bytes_read
                    )
                bytes_read += scanned
            except OSError as exc:
                return failed(self.name, self.version, type(exc).__name__, str(exc))
            for term, by_encoding in sorted(counts.items()):
                for encoding, count in sorted(by_encoding.items()):
                    readings.append(
                        Reading(
                            f"scan.term.{term}.{encoding}",
                            count,
                            ValueKind.INTEGER,
                            tier,
                            source_scope=SourceScope.CONTENT,
                        )
                    )
            readings.append(
                Reading("scan.terms_searched", len(context.terms), ValueKind.INTEGER, tier)
            )

        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            bytes_read=bytes_read,
        )


@EXTRACTORS.register(
    "generic",
    version="1",
    summary="byte-level baseline: string harvesting and multi-encoding term scanning",
)
def _build_generic() -> GenericExtractor:
    return GenericExtractor()
