"""The files a script names in its own read and write calls, read and never run.

A script names its inputs and outputs in its calls: ``read_csv("plate_counts.csv")``,
``savefig("plate_plot.png")``, ``open(dir + "plate_001.tif")``. Each call naming a file by a literal
name is recorded with its direction, read or written (``script.file_names``,
``{"read": [...], "written": [...]}``), and inference resolves them as mentions within the
script's own folder or experiment (:mod:`ingence.infer.references`). The grammar is
:func:`ingence.infer.script_outputs.names_called`. Nothing is executed, and a call
the reading does not know is not read.

This is an extractor of its own, declared for scripts' extensions only. Folded into the keyword
extractor, it would raise that extractor's version and have every text file of a pool read
again on the next dive, for readings that only a script can hold.

Tier: heuristic. Reading a call's arguments is grammar over decoded text, not a value a
format's structure states.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ingence.core.outcomes import ScanOutcome, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.extractors.base import EXTRACTORS, ExtractorResult, Isolation, Reading, failed
from ingence.extractors.keywords import SCRIPT_LANGUAGES
from ingence.extractors.limits import Limits
from ingence.extractors.text import decide_encoding
from ingence.infer.script_outputs import READ, SCRIPT_FILE_NAMES_KEY, WRITTEN, names_called

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["ScriptCallsExtractor"]


class ScriptCallsExtractor:
    """Records the files a script's read and write calls name by a literal name."""

    name = "script_calls"
    version = "1"
    isolation = Isolation.IN_PROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return bool(context.extensions & set(SCRIPT_LANGUAGES))

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        limits = context.limits or Limits()
        sample = int(getattr(limits, "text_sketch_bytes", 0) or (256 << 10))
        try:
            payload = context.read(min(limits.max_bytes_read, sample))
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc))
        encoding, _ = decide_encoding(payload)
        text = payload.decode(encoding, errors="replace")
        language = next(
            SCRIPT_LANGUAGES[extension]
            for extension in sorted(context.extensions)
            if extension in SCRIPT_LANGUAGES
        )
        called = names_called(text, language)
        readings: tuple[Reading, ...] = ()
        if called:
            named = {
                direction: sorted({name.name for name in called if name.direction == direction})
                for direction in (READ, WRITTEN)
            }
            readings = (
                Reading(
                    SCRIPT_FILE_NAMES_KEY,
                    json.dumps(named, separators=(",", ":"), ensure_ascii=False),
                    ValueKind.STRUCTURE,
                    ExtractionTier.HEURISTIC,
                ),
            )
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=readings,
            bytes_read=len(payload),
        )


@EXTRACTORS.register(
    "script_calls",
    version="1",
    summary="the files a script's read and write calls name by a literal name, with direction",
    attributes={"extensions": ",".join(sorted(SCRIPT_LANGUAGES))},
)
def _build_script_calls() -> ScriptCallsExtractor:
    return ScriptCallsExtractor()
