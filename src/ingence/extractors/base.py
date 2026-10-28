"""The extractor contract: what an extractor is given, and what it may return.

Every content extractor in the engine implements this and registers itself. The
engine never imports one by name, so removing an extractor means deleting a module
and a registration line.

Extractors do not construct records. They return :class:`Reading` values, and
the deepening pass turns those into observations with identities, run identifiers
and timestamps, for three reasons.
An extractor that mints identities has to know the identity scheme, so changing
the scheme means changing every extractor. An extractor that stamps a run
identifier cannot be tested without a run. And an extractor that builds a
validated record does validation work once per reading in a child process, then
ships it across a process boundary, where it is validated again.

Results cross a process boundary, and every value here is built to. An
extractor declaring :attr:`Isolation.SUBPROCESS` runs in a supervised child that
the parent kills rather than asks, because a cancelled future does not stop a
parser that has gone quadratic and only killing the process does. Everything
here is a plain frozen dataclass of plain values. Such values are picklable, cheap
to send, and cannot depend on an open file handle, so no extractor needs to know
about the supervisor. The supervisor is :mod:`ingence.executors.supervisor`.

An extractor may not raise to signal an ordinary outcome. A file it cannot
read, a format it does not recognise, a limit it hit: each is a result with an
outcome, not an exception. Exceptions are for the genuinely unexpected, and the
supervising pass turns those into a recorded content error rather than a failed
run. One unreadable file must never fail a scan.

Negative results are returned, not discarded. "This file contains no
readable text" is a finding. It permanently narrows what the file can be, and an
engine that throws it away asks the same question again on every run.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ingence.core.ids import LocatorStep
from ingence.core.outcomes import ScanOutcome, SourceScope, TimeSource, ValueKind
from ingence.core.registry import Registry
from ingence.core.tiers import ExtractionTier

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO

    from ingence.core.relations import ExpansionState
    from ingence.extractors.limits import Limits

__all__ = [
    "EXTRACTORS",
    "DerivedStream",
    "ExtractionContext",
    "Extractor",
    "ExtractorResult",
    "Isolation",
    "Reading",
    "ReadingDepth",
    "declined",
    "failed",
    "isolation_of",
    "reading_depth_of",
]


class ReadingDepth(StrEnum):
    """How much of a file an extractor has to read before it can answer.

    The declaration is not a measurement, because the caller needs it before opening the file.
    A vendor image format's acquisition time sits in a header a few kilobytes long. A text
    sketch, a string harvest or a table's shape is the file's body. On a large file the difference
    is the whole cost of the reading, and the cheap half is the useful one.

    An extractor declares it like :class:`Isolation`, and :func:`reading_depth_of` reads it. An
    extractor that says nothing gets the conservative answer: it reads the body.
    """

    #: A bounded prefix: the format's own header, its metadata, its instrument clock.
    HEADER = "header"
    #: The file's body, whole or sampled: text, tables, strings, members.
    CONTENT = "content"


class Isolation(StrEnum):
    """Whether an extractor needs a process boundary around it.

    Each extractor declares its own isolation as a field. No isolation is applied to every
    extractor. Reading eight kilobytes and hashing a stream cannot hang. A format parser on
    adversarial input can, and a cancelled future will not stop it because the thread is inside a
    blocking call. Isolating everything makes a cheap pass expensive, and isolating nothing lets one
    bad file hang a whole scan. Putting the declaration on the extractor puts the cost where the
    risk is.

    The default is deliberately the cheap one. An extractor that says nothing
    runs in process, so a new extractor is safe to write and its author has to
    make one decision rather than remember one.
    """

    #: Runs in the calling thread. Cheap, bounded work that cannot hang.
    IN_PROCESS = "in_process"
    #: Runs in a supervised child the parent can kill. For anything that parses
    #: a format it did not write, which is every long-tail extractor.
    SUBPROCESS = "subprocess"


def reading_depth_of(extractor: object) -> ReadingDepth:
    """Return how much of a file this extractor reads, defaulting to the body.

    The default is the cautious one: an extractor that has not thought about it is
    assumed to read everything, so a file is never left half-read because a
    declaration was missing.
    """
    declared = getattr(extractor, "reading_depth", ReadingDepth.CONTENT)
    return declared if isinstance(declared, ReadingDepth) else ReadingDepth(declared)


def isolation_of(extractor: object) -> Isolation:
    """Return what a registered extractor asked for, defaulting to in process.

    Read through a function rather than an attribute access so that an extractor
    written before the declaration existed keeps working and gets the safe-to-run
    default rather than an :class:`AttributeError` at scan time.
    """
    declared = getattr(extractor, "isolation", Isolation.IN_PROCESS)
    return declared if isinstance(declared, Isolation) else Isolation(declared)


@dataclass(frozen=True, slots=True)
class Reading:
    """One thing an extractor read out of a file.

    The unit an extractor speaks in. It becomes an observation, and the mapping
    is mechanical, so an extractor never has to think about the record schema.
    """

    #: A stable key in the extractor's own vocabulary: ``text.character_count``,
    #: ``image.width``, ``table.column_names``. The keys are not listed anywhere,
    #: because a list would make adding an extractor a schema change.
    key: str
    value: str | int | float | bool | None
    kind: ValueKind = ValueKind.TEXT
    #: How far this sits from the bytes. An extractor is responsible for being
    #: honest here: a value read from a header is not the same kind of fact as
    #: one inferred from a regular expression, and only the extractor knows
    #: which it did.
    extraction_tier: ExtractionTier = ExtractionTier.FORMALLY_EXTRACTED
    source_scope: SourceScope = SourceScope.CONTENT
    #: Where inside the file this came from. Most readings about the interior of
    #: a file belong on that file's entry carrying a locator, rather than
    #: becoming an entry of their own.
    locator: tuple[LocatorStep, ...] = ()


@dataclass(frozen=True, slots=True)
class DerivedStream:
    """A stream recovered from inside a file.

    Usually an observation about the file it came from. Occasionally it needs to
    be an endpoint of an edge in its own right, and then it is promoted to an
    entry. Promotion is an event, not a default: nothing is promoted unless a
    rule or a person asks for it, which is why ``promote`` defaults to false and
    an extractor almost never sets it.
    """

    locator: tuple[LocatorStep, ...]
    transformation: str
    transformation_version: str
    ordinal: int = 0
    content_hash: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None
    #: Why expansion of this member stopped. Required on the embedding edge, and
    #: what makes the absence of a sibling readable.
    expansion_state: ExpansionState | None = None
    promote: bool = False


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Everything an extractor is given, and nothing more.

    No project, no catalog, no settings object, no registry. An extractor that
    could reach the project could write to it. Ingence never changes the files it reads, and the
    engine's own state is not something a parser gets to touch.
    """

    entry_id: str
    path: Path
    relative_path: str
    #: Final and compound suffixes, lowercased, with the leading dot.
    extensions: frozenset[str]
    size_bytes: int
    #: The type hypothesis routing produced, if any. Superseded by whatever
    #: the extractor actually finds. A model may route work. It may never
    #: author a claim, and this field is the whole reason that boundary holds.
    media_type: str | None = None
    #: Which detector produced the hypothesis, so a misroute is attributable.
    media_type_source: str | None = None
    limits: Limits | None = None
    #: The vocabulary a byte scan looks for. Gated on the Dictionary rather than
    #: on a language, so the cost is proportional to what the project knows
    #: rather than to the size of English.
    terms: tuple[str, ...] = ()
    #: How deep inside a container this content sits. Zero is the file itself.
    depth: int = 0
    #: Content held in memory, for a stream recovered from inside a container.
    #: Present instead of a readable path, never as well as one.
    #:
    #: Derived content is never written to disk. A member of an archive is
    #: inspected in memory and discarded, so the engine leaves nothing behind,
    #: needs no temporary directory to clean up, and cannot be tricked by a
    #: hostile archive into writing anywhere. Extractors reach content through
    #: :meth:`open` and never touch ``path`` directly, so the same extractor
    #: works on a file and on a stream three containers deep without knowing
    #: which it has.
    payload: bytes | None = None
    #: Where this content sits inside its file. Empty at the top level. A member
    #: three containers deep carries the whole chain, which is what makes its
    #: provenance readable and what lets it be read again later.
    locator: tuple[LocatorStep, ...] = ()

    def open(self) -> BinaryIO:
        """Return a readable binary stream over this content.

        The one way an extractor reads. Backed by the file on disk, or by bytes
        in memory for a derived stream.
        """
        if self.payload is not None:
            return io.BytesIO(self.payload)
        return self.path.open("rb")

    def read(self, limit: int) -> bytes:
        """Read up to a byte ceiling. Returns what there was, never raises for size."""
        with self.open() as handle:
            return handle.read(limit)


@dataclass(frozen=True, slots=True)
class ExtractorResult:
    """What one extractor produced for one file."""

    extractor: str
    extractor_version: str
    outcome: ScanOutcome = ScanOutcome.EXTRACTED
    readings: tuple[Reading, ...] = ()
    derived: tuple[DerivedStream, ...] = ()
    #: References this file makes to other files, as raw strings. Resolution is
    #: an inference and happens later. Recording the string is an observation.
    references: tuple[str, ...] = ()
    #: The shape of each reference, in the same order, where the extractor could read
    #: one off the reference's own line: ``code_include``, ``absolute``, ``relative``
    #: or ``bare``. Empty where it read none, and the deepening pass then takes
    #: the shape from each reference's spelling alone.
    reference_shapes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    error_type: str | None = None
    error_message: str | None = None
    #: Bytes actually read, so the deepening budget can be spent against real
    #: cost rather than an estimate.
    bytes_read: int = 0

    #: The event this file records, where the format carries one, in nanoseconds
    #: since the epoch at UTC.
    #:
    #: An extractor that reaches a content clock must emit it. The top two tiers of the clock
    #: ladder have no source of their own, so a format extractor supplies the time. So no
    #: extractor's author has to decide again whether to emit the clock.
    #:
    #: One clock per extractor per file, and the extractor chooses which. A
    #: PDF carries a creation date and a modification date, a notebook carries a
    #: time per executed cell, and an instrument file carries one per frame. Only
    #: the extractor knows which of them is the event the file records, so the
    #: choice is made here and not by a later pass comparing timestamps it cannot
    #: interpret. The others are ordinary readings and are still recorded.
    event_time_ns: int | None = None
    #: Where that time came from. Required whenever a time is present, because a
    #: time whose provenance is unrecorded cannot be reasoned about, and the tier
    #: is derived from it through ``CLOCK_TIER_OF`` rather than stated twice.
    event_time_source: TimeSource | None = None

    def __post_init__(self) -> None:
        """Refuse a time without a source, and a source without a time.

        Checked here rather than trusted, because the pair is the whole point:
        ``event_time_ns`` on its own is a number the engine cannot rank against
        any other clock, and a source on its own says a clock was found and
        withholds it.
        """
        if self.reference_shapes and len(self.reference_shapes) != len(self.references):
            message = (
                f"{self.extractor}: {len(self.reference_shapes)} reference shapes for "
                f"{len(self.references)} references; a shape belongs to one reference"
            )
            raise ValueError(message)
        if (self.event_time_ns is None) != (self.event_time_source is None):
            message = (
                f"{self.extractor}: an event time and its source travel together; "
                f"a time without a source cannot be ranked and a source without a "
                f"time says a clock was found and withholds it"
            )
            raise ValueError(message)

    @property
    def succeeded(self) -> bool:
        """Whether this result carries content rather than a reason it does not."""
        return self.outcome in {
            ScanOutcome.EXTRACTED,
            ScanOutcome.EXTRACTED_WITH_FALLBACK,
        }


def declined(extractor: str, version: str, reason: str) -> ExtractorResult:
    """Return the result of an extractor that chose not to read a file.

    A deliberate decision, not a failure. The file is intact, the engine simply
    decided not to open it, and that answer stays true on the next run so the
    record is reusable.
    """
    return ExtractorResult(
        extractor=extractor,
        extractor_version=version,
        outcome=ScanOutcome.SKIPPED_BY_POLICY,
        warnings=(reason,),
    )


def failed(extractor: str, version: str, error_type: str, message: str) -> ExtractorResult:
    """Return the result of an extraction that was attempted and did not work.

    Never reusable. A scan interrupted by a stalled filesystem must retry exactly
    the files that failed, rather than serving their failures forever, which is
    why this outcome and the deferred one are excluded from reuse.
    """
    return ExtractorResult(
        extractor=extractor,
        extractor_version=version,
        outcome=ScanOutcome.CONTENT_ERROR,
        error_type=error_type,
        error_message=message,
        warnings=(message,),
    )


@runtime_checkable
class Extractor(Protocol):
    """What every content extractor implements.

    The interface is deliberately small. With many methods, a new extractor could not be written
    without reading an existing one, and adding or removing extractors is meant to be cheap.
    """

    #: Registry name. Also what appears on every observation this extractor
    #: produces, so a reading is always attributable to the thing that made it.
    name: str
    #: Bumped whenever the extractor's output could change for unchanged input.
    #: Enters the processing fingerprint of every file whose extension this
    #: extractor claims, and *only* those, so a change here re-examines what it
    #: affects and nothing else.
    version: str
    #: Whether this extractor needs a process boundary. Optional: an extractor
    #: that does not declare one runs in process, which is the cheap and safe
    #: default for bounded work. Read through :func:`isolation_of`, never
    #: directly, so the default applies uniformly.
    isolation: Isolation

    def supports(self, context: ExtractionContext) -> bool:
        """Whether this extractor is willing to read this file.

        Consulted before the file is opened, so it must decide from the context
        alone. An extractor that needs to look inside to know should say yes and
        return a declining result, which is honest and costs one open.
        """
        ...

    def extract(self, context: ExtractionContext) -> ExtractorResult:
        """Read the file and return what was found.

        Must not raise for an ordinary outcome. Must not write anything, execute
        anything, or open a network connection.
        """
        ...


#: Every content extractor, reachable only through here.
#:
#: A registration declares the extensions it claims in its attributes, as a
#: comma-separated list. The processing fingerprint reads exactly that, so
#: bumping one extractor's version re-examines the files it claims and leaves
#: every other file alone. An extractor claiming nothing is a universal fallback
#: and is in scope for every file.
EXTRACTORS: Registry[Extractor] = Registry("extractor")


def _extensions_of(name: str) -> frozenset[str]:
    """Return the final and compound suffixes of a member name, lowercased."""
    if not name:
        return frozenset()
    pure = PurePosixPath(name)
    final = pure.suffix.casefold()
    parts = pure.suffixes[-2:]
    compound = "".join(part.casefold() for part in parts) if len(parts) > 1 else ""
    return frozenset(value for value in (final, compound) if value)
