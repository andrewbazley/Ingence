"""Jupyter notebooks: cells, their execution record, the paths they mention, and a clock.

The extractor reads a notebook and never executes it, and the boundary is structural.
Ingence never runs code it finds, and a notebook is the one format
where the reader and the executor share a library: ``nbformat`` is the data model
that ``nbclient``, ``papermill`` and every kernel client are built on. This module
imports ``nbformat`` and nothing that can start a kernel. A test extracts a
notebook in a fresh interpreter and asserts that no execution machinery was
imported and that a cell written to create a file did not create it, because the
failure this guards against is otherwise invisible: a notebook that ran would
still look read.

The extractor parses a notebook whole or not at all. A notebook is JSON, and JSON has no partial
parse, so one larger than the byte ceiling is declined with that reason rather than read
in part. Real notebooks carry their outputs and an embedded figure makes a few
megabytes ordinary, so this decline is common enough to matter. The ceiling is the
existing ``max_sample_bytes`` setting, not a new number. A user who wants larger notebooks read
raises one setting and the run record says so.

A notebook carries a time per executed cell wherever something recorded one, and this module
chooses which of them is the event. The event is the earliest recorded execution start, and it is
offered only when every executed cell recorded one.

*The start, because that is what tier one records.* An instrument's clock is when
acquisition began rather than when the last frame landed, and a computation is the
same kind of event: every output in the file was produced at or after it. It also
does not move when a later cell is re-run to look at one variable, which is how an
analysis finished last year would otherwise acquire a timestamp from this morning.

*All or nothing, because a partial record does not contain its own start.* A
notebook whose timing was switched on halfway through has untimed cells that ran
before its earliest recorded time, so that time is not when it began. The clock rules
abstain on a filename that names two dates for the same reason: an answer that is
right sometimes and silently wrong otherwise is worse than none. In practice this
offers a clock for notebooks a tool executed, where timing is recorded by default,
and abstains on interactive ones, which rarely record it. Both sets of times are
recorded as readings either way.

*Execution counts are recorded and not used to order sessions.* A kernel restart
resets the counter, so ascending counts say that cells ran in document order and
cannot say that they ran on one day.

What cells mention reaches ``ExtractorResult.references`` through the same
function the text extractor uses, over every cell's source. Outputs are not
scanned: an output is what a run printed, often megabytes of an encoded image, and
a path a program printed is evidence of a different kind from a path a person
wrote.

Schema validation is not run. ``nbformat.validate`` normalises as it checks,
adding cell identifiers to the notebook it was handed and warning as it does, and
no reading here depends on conformance. A structure that is not where the format
puts it is read as absent.
"""

from __future__ import annotations

import warnings
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.core.outcomes import ScanOutcome, TimeSource, ValueKind
from ingence.extractors.base import (
    EXTRACTORS,
    ExtractorResult,
    Isolation,
    Reading,
    declined,
    failed,
)
from ingence.extractors.clocks import parse_w3cdtf
from ingence.extractors.limits import Limits
from ingence.extractors.mentions import mentioned_paths_and_shapes

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ingence.extractors.base import ExtractionContext

__all__ = ["NOTEBOOK_EXTENSIONS", "ExecutionRecord", "NotebookExtractor", "execution_record"]

NOTEBOOK_EXTENSIONS = {".ipynb"}

#: Where a cell's execution start is written, consulted in this order. Jupyter's
#: own recorder first, which JupyterLab writes when timing is switched on and
#: ``nbclient`` writes by default, and ``nbconvert`` and ``papermill`` execute
#: through ``nbclient``. Then the classic notebook's timing extension, then
#: ``papermill``'s own per-cell record from before it used ``nbclient``. These are
#: key names the format's producers write, not vocabulary about anybody's data.
_START_KEYS: tuple[tuple[str, str], ...] = (
    ("execution", "iopub.execute_input"),
    ("execution", "iopub.status.busy"),
    ("ExecuteTime", "start_time"),
    ("papermill", "start_time"),
)

#: Where the same producers write when a cell finished.
_END_KEYS: tuple[tuple[str, str], ...] = (
    ("execution", "shell.execute_reply"),
    ("execution", "iopub.status.idle"),
    ("ExecuteTime", "end_time"),
    ("papermill", "end_time"),
)

#: How much of a free-text metadata value is kept.
_MAX_VALUE_CHARS = 200


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """What a notebook's cells record about when they ran."""

    #: Code cells carrying an execution count, which is what running leaves behind.
    executed: int
    #: Executed cells whose start time was written and parsed.
    timed: int
    started_ns: int | None
    started_resolution: str | None
    finished_ns: int | None
    #: Whether execution counts rise in document order. ``None`` below two cells,
    #: where there is no order to report.
    counts_ascending: bool | None
    #: The first start time that was written and could not be parsed, kept so a
    #: producer's odd format is visible rather than silently discarded.
    unparsed: str | None

    @property
    def event_ns(self) -> int | None:
        """The clock this notebook offers, or None. The rule is the module docstring's."""
        if self.executed and self.timed == self.executed:
            return self.started_ns
        return None


def execution_record(cells: Iterable[Any]) -> ExecutionRecord:
    """Summarise when a notebook's code cells ran, from the cells alone.

    Pure over plain mappings, so the clock rule is testable without writing a
    notebook and without ``nbformat``.
    """
    counts: list[int] = []
    starts: list[tuple[int, str]] = []
    ends: list[int] = []
    unparsed: str | None = None
    for cell in cells:
        if not isinstance(cell, Mapping) or cell.get("cell_type") != "code":
            continue
        count = cell.get("execution_count")
        if not isinstance(count, int) or isinstance(count, bool):
            continue
        counts.append(count)
        metadata = cell.get("metadata")
        raw_start = _first_time(metadata, _START_KEYS)
        if raw_start is None:
            continue
        start = parse_w3cdtf(raw_start)
        if start is None:
            unparsed = unparsed or raw_start[:64]
            continue
        starts.append(start)
        raw_end = _first_time(metadata, _END_KEYS)
        end = parse_w3cdtf(raw_end) if raw_end is not None else None
        ends.append(end[0] if end is not None else start[0])

    earliest = min(starts) if starts else None
    return ExecutionRecord(
        executed=len(counts),
        timed=len(starts),
        started_ns=earliest[0] if earliest else None,
        started_resolution=earliest[1] if earliest else None,
        finished_ns=max(ends) if ends else None,
        counts_ascending=counts == sorted(set(counts)) if len(counts) > 1 else None,
        unparsed=unparsed,
    )


def _first_time(metadata: object, keys: tuple[tuple[str, str], ...]) -> str | None:
    """Return the first non-empty time string written under any of these keys."""
    if not isinstance(metadata, Mapping):
        return None
    for group, key in keys:
        section = metadata.get(group)
        if isinstance(section, Mapping):
            value = section.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


class NotebookExtractor:
    """Reads a Jupyter notebook's structure and execution record without running it."""

    name = "notebook"
    #: Version 2 reports each reference's shape.
    version = "2"
    #: JSON parsed by a library, on input the engine did not write. A notebook is
    #: also the format most likely to be enormous for an ordinary reason.
    isolation = Isolation.SUBPROCESS

    def supports(self, context: ExtractionContext) -> bool:
        return bool(NOTEBOOK_EXTENSIONS & context.extensions)

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        try:
            import nbformat
        except ImportError:
            return declined(self.name, self.version, "nbformat is not installed")

        limits = context.limits or Limits()
        try:
            payload = context.read(limits.max_bytes_read + 1)
        except OSError as exc:
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])
        if len(payload) > limits.max_bytes_read:
            return declined(
                self.name,
                self.version,
                f"larger than the {limits.max_bytes_read}-byte ceiling, and JSON cannot "
                f"be parsed in part",
            )

        # Reached through ``Any``: nbformat declares itself typed and leaves these
        # three unannotated, so strict checking refuses the calls wherever the
        # library is installed and accepts them wherever it is not.
        library: Any = nbformat
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                original = library.reader.reads(payload.decode("utf-8-sig"))
                major, minor = library.reader.get_version(original)
                notebook = library.convert(original, 4)
        except Exception as exc:
            # Not JSON, JSON that is not a notebook, a version nothing reads. The
            # file is intact and this extractor could not make sense of it, which
            # is a content error and a question of coverage.
            return failed(self.name, self.version, type(exc).__name__, str(exc)[:500])

        cells = [cell for cell in _list_of(notebook.get("cells")) if isinstance(cell, Mapping)]
        readings = _structure_readings(notebook, cells, major=major, minor=minor)
        record = execution_record(cells)
        readings.extend(_execution_readings(record))

        sources = "\n".join(_source_of(cell) for cell in cells)
        readings.append(Reading("notebook.source_character_count", len(sources), ValueKind.INTEGER))
        references, shapes = mentioned_paths_and_shapes(
            sources[: limits.max_text_chars].encode("utf-8", "replace")
        )
        if references:
            readings.append(Reading("notebook.reference_count", len(references), ValueKind.INTEGER))
        if caught:
            readings.append(
                Reading("notebook.format_warning_count", len(caught), ValueKind.INTEGER)
            )

        event_ns = record.event_ns
        return ExtractorResult(
            extractor=self.name,
            extractor_version=self.version,
            outcome=ScanOutcome.EXTRACTED,
            readings=tuple(readings),
            references=references,
            reference_shapes=shapes,
            bytes_read=len(payload),
            event_time_ns=event_ns,
            event_time_source=TimeSource.DOCUMENT if event_ns is not None else None,
        )


def _structure_readings(
    notebook: Mapping[str, Any], cells: list[Mapping[str, Any]], *, major: object, minor: object
) -> list[Reading]:
    """Return the readings about what a notebook holds, independent of when it ran."""
    kinds = Counter(str(cell.get("cell_type")) for cell in cells)
    outputs = [
        output
        for cell in cells
        for output in _list_of(cell.get("outputs"))
        if isinstance(output, Mapping)
    ]
    readings = [
        Reading("notebook.format_version", f"{major}.{minor}", ValueKind.TEXT),
        Reading("notebook.cell_count", len(cells), ValueKind.INTEGER),
        Reading("notebook.code_cell_count", kinds.get("code", 0), ValueKind.INTEGER),
        Reading("notebook.markdown_cell_count", kinds.get("markdown", 0), ValueKind.INTEGER),
        Reading("notebook.raw_cell_count", kinds.get("raw", 0), ValueKind.INTEGER),
        Reading("notebook.output_count", len(outputs), ValueKind.INTEGER),
        # An error output is a run that failed and was saved that way. Failure is
        # evidence, and a notebook whose last run raised is a different artefact
        # from one that completed.
        Reading(
            "notebook.error_output_count",
            sum(1 for output in outputs if output.get("output_type") == "error"),
            ValueKind.INTEGER,
        ),
    ]

    metadata = notebook.get("metadata")
    kernelspec = metadata.get("kernelspec") if isinstance(metadata, Mapping) else None
    language_info = metadata.get("language_info") if isinstance(metadata, Mapping) else None
    kernel = kernelspec.get("name") if isinstance(kernelspec, Mapping) else None
    language = language_info.get("name") if isinstance(language_info, Mapping) else None
    if language is None and isinstance(kernelspec, Mapping):
        language = kernelspec.get("language")
    if isinstance(kernel, str) and kernel:
        readings.append(Reading("notebook.kernel", kernel[:_MAX_VALUE_CHARS], ValueKind.TEXT))
    if isinstance(language, str) and language:
        readings.append(Reading("notebook.language", language[:_MAX_VALUE_CHARS], ValueKind.TEXT))
    return readings


def _execution_readings(record: ExecutionRecord) -> list[Reading]:
    """Return the readings about when a notebook ran, whether or not a clock is offered."""
    readings = [
        Reading("notebook.executed_cell_count", record.executed, ValueKind.INTEGER),
        Reading("notebook.timed_cell_count", record.timed, ValueKind.INTEGER),
    ]
    if record.executed == 0:
        # Never run, or cleared before saving. A template and an analysis look
        # alike by name, and this is the fact that separates them.
        readings.append(Reading("notebook.never_executed", True, ValueKind.NEGATIVE))
    elif record.timed == 0 and record.unparsed is None:
        # Run, and nothing recorded when. The common interactive case, and a
        # different finding from a notebook that was never run at all.
        readings.append(Reading("notebook.execution_time_absent", True, ValueKind.NEGATIVE))
    if record.counts_ascending is not None:
        readings.append(
            Reading(
                "notebook.execution_counts_ascending", record.counts_ascending, ValueKind.BOOLEAN
            )
        )
    if record.started_ns is not None:
        readings.append(
            Reading("notebook.execution_started_ns", record.started_ns, ValueKind.INTEGER)
        )
        readings.append(
            Reading(
                "notebook.execution_started_resolution",
                str(record.started_resolution),
                ValueKind.TEXT,
            )
        )
    if record.finished_ns is not None:
        readings.append(
            Reading("notebook.execution_finished_ns", record.finished_ns, ValueKind.INTEGER)
        )
    if record.unparsed is not None:
        readings.append(Reading("notebook.execution_unparsed", record.unparsed, ValueKind.TEXT))
    return readings


def _list_of(value: object) -> list[Any]:
    """Return a value as a list, or an empty one where it is not a list."""
    return value if isinstance(value, list) else []


def _source_of(cell: Mapping[str, Any]) -> str:
    """Return a cell's source as one string, whichever of the two spellings it uses."""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(part for part in source if isinstance(part, str))
    return source if isinstance(source, str) else ""


@EXTRACTORS.register(
    "notebook",
    version="2",
    summary="Jupyter notebooks: cells, execution record, mentioned paths, and a tier-two clock",
    requires=("nbformat",),
    attributes={"extensions": ",".join(sorted(NOTEBOOK_EXTENSIONS))},
)
def _build_notebook() -> NotebookExtractor:
    return NotebookExtractor()
