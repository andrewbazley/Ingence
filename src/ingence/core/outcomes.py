"""The four enums that cross a process boundary, and why they live apart.

These enums live apart from ``core.schema`` because of import cost. An isolated extraction runs in
a spawned child, which starts a fresh interpreter and imports whatever the extractor path imports.
Every name that the extractor path needs from :mod:`ingence.core.schema` is an enum and never a
model. Five extractor modules take :class:`ScanOutcome`, :class:`SourceScope` and
:class:`ValueKind`, and four identity methods take :class:`IdentityScope`. Nothing on that path
touches a Pydantic model.

Importing ``core.schema`` to reach them cost a fresh interpreter 0.408 seconds, against 0.096 for
the rest of the extractor path together and 0.029 for a bare interpreter. Avoiding model
construction is not the saving. The saving is avoiding Pydantic entirely, which alone costs 0.115
seconds, more than the whole schema-free path.

That is why the engine needs no pool of worker processes here. An isolated file cost 0.46
seconds, and 0.36 of that was this import. A pool would spread that cost over many files. It
would also bring a watchdog, a replacement policy, and a change of the deepening budget from
wall-clock seconds to work-seconds, because at N workers a wall-clock budget buys N times the
work. Moving four enums removes three quarters of the same cost and changes no call site.

The contract of this module is to stay light. It imports from the standard library and from
nothing in the engine. A single import added here that reaches a model puts 0.3 seconds back on
every isolated extraction, and nothing would fail. The only symptom would be a slower scan. A test
asserts that the extractor path never reaches ``core.schema``.

The clock ladder is here for the same reason. An extractor that reaches a content clock has to
emit it, so a format extractor has to name a :class:`TimeSource` from inside a spawned child.
Keeping the ladder in ``core.schema`` would put the record schema back on the child's path as
soon as the first such extractor ran. Those extractors are exactly the ones that declare
isolation, so they are the ones that need the saving most.

``core.schema`` re-exports everything here, so an existing import keeps working and no consumer
has to know where the enum lives.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CLOCK_TIERS",
    "CLOCK_TIER_OF",
    "CLOCK_TIER_PROPERTIES",
    "NON_REUSABLE_OUTCOMES",
    "ClockTier",
    "ClockTierProperties",
    "IdentityScope",
    "ScanOutcome",
    "SourceScope",
    "TimeSource",
    "ValueKind",
]


class ValueKind(StrEnum):
    """The type of an observation's value.

    An observation's value is stored in a typed column chosen by this
    discriminator rather than stringified into one. Columnar storage rewards
    that, and it keeps a numeric comparison from silently becoming a string
    comparison.
    """

    TEXT = "text"
    INTEGER = "integer"
    REAL = "real"
    BOOLEAN = "boolean"
    #: A structure. Serialized canonically. The shape is the observer's business.
    STRUCTURE = "structure"
    #: The observation asserts that something is *not* the case. Negative results
    #: are stored, never discarded: proof that two entries are not identical
    #: prunes candidate sets permanently and is worth more than people expect.
    NEGATIVE = "negative"


class SourceScope(StrEnum):
    """Where in an entry a component or observation came from."""

    #: The entry's own name.
    NAME = "name"
    #: The path leading to it, excluding the name.
    PATH = "path"
    #: A structural header read without interpreting the body.
    HEADER = "header"
    #: The body of the file.
    CONTENT = "content"
    #: Supplied by a person.
    USER = "user"
    #: Stated by a third party: an archive's listing, a depositor's free-text attribute, a size or
    #: a checksum an archive publishes. The engine did not read it from the bytes, and the person
    #: using the engine did not say it. A rule reading one does so deliberately and never mistakes
    #: it for a filesystem fact. This extends the rule that a fact the engine read and a conclusion
    #: drawn from it are never stored in the same field. A third party's statement is kept apart
    #: from both, as a third kind of source.
    DECLARED = "declared"


class IdentityScope(StrEnum):
    """How much of an entry an identity method read."""

    WHOLE = "whole"
    SAMPLED = "sampled"
    HEADER = "header"


class ScanOutcome(StrEnum):
    """What the last run actually did with an entry.

    Read by the reuse decision, which must know not only whether the file
    changed but whether the previous record *delivers what today's policy asks
    for*. A metadata-only record cannot satisfy a run with extraction enabled,
    and an error or a deferral is never reusable. Failure is never cached, so a scan interrupted by
    a stalled filesystem retries exactly the files that failed instead of serving their failures
    forever.
    """

    #: Recorded from the filesystem without opening the file.
    METADATA_ONLY = "metadata_only"
    #: The entry's content cannot be read: no bytes, or a link with no target. This is a deliberate
    #: omission, not a failure. A file that a person's settings set aside is
    #: :attr:`SKIPPED_BY_SETTINGS` instead, so the two are counted apart.
    SKIPPED_BY_POLICY = "skipped_by_policy"
    #: Content was read successfully.
    EXTRACTED = "extracted"
    #: Content was read, but by a fallback after the preferred path declined.
    EXTRACTED_WITH_FALLBACK = "extracted_with_fallback"
    #: Content work was not attempted because a budget or a timeout ran out.
    #: Distinct from failure: nothing is known to be wrong with the file.
    DEFERRED = "deferred"
    #: Content work was attempted and failed. Never reusable.
    CONTENT_ERROR = "content_error"
    #: Content was read, but the file's size or modification time differed after the reading
    #: from before it (schema 1.22). What was read may describe neither version, so none of it
    #: is kept as the file's, and the next pass reads it again. Never reusable.
    CHANGED_DURING_READ = "changed_during_read"
    #: Content work was configured, and a person's extraction settings set this file aside
    #: unread: an extension they exclude, an include list that does not name it, or a size above
    #: their limit (a compatible addition in schema 1.22). This is a deliberate omission that a
    #: person chose, counted apart from content that cannot be read. The first dive after the
    #: setting is lifted reads the file again.
    SKIPPED_BY_SETTINGS = "skipped_by_settings"


#: Outcomes that must never be reused: a failure, a deferral and a reading the file moved under.
#: Serving any of them from cache would make a transient problem permanent.
NON_REUSABLE_OUTCOMES: frozenset[ScanOutcome] = frozenset(
    {ScanOutcome.CONTENT_ERROR, ScanOutcome.DEFERRED, ScanOutcome.CHANGED_DURING_READ}
)


class TimeSource(StrEnum):
    """Where a recorded time came from. Every clock carries one.

    The source is kept explicitly because a time whose provenance is unrecorded cannot be reasoned
    about, and the filesystem's own clocks are the least trustworthy it offers. Creation time does
    not exist on every filesystem. ``st_ctime`` means metadata change on POSIX and creation on
    Windows. Copying a file usually resets both.

    The content sources are split instead of gathered under one ``embedded`` value, because an
    instrument's acquisition time and a word processor's "created" field are not the same kind of
    fact and :class:`ClockTier` grades them differently. One value covering both would make the
    tier unrecoverable from the source, which is what the pair exists to prevent.
    """

    #: A genuine creation time reported by the filesystem.
    BIRTHTIME = "birthtime"
    #: POSIX metadata change time, standing in for creation. Not the same thing.
    CTIME = "ctime"
    #: Filesystem modification time, read as itself.
    MTIME = "mtime"
    #: Modification time used because no creation time was available.
    MTIME_FALLBACK = "mtime_fallback"
    #: When an instrument recorded the measurement. The best clock there is, and it
    #: travels with the bytes. Three extractors produce it, each reading a vendor's own format
    #: that the engine claims by name: ``nikon_nd2`` from an ND2 file's first frame, ``leica_lif``
    #: from the earliest image in a LIF header, and ``lambert_fli`` from a LI-FLIM header, each
    #: UTC by its format's definition. A time stated without a zone, or copied from another file,
    #: stays a reading.
    INSTRUMENT = "instrument"
    #: A document or application's own metadata: PDF creation, notebook cell
    #: execution, a table's header date. Produced by the format extractors that
    #: read such metadata. Each extractor offers one clock per file, and the deepening pass ranks
    #: them.
    DOCUMENT = "document"
    #: Parsed out of the filename or a path component. Weaker than the two
    #: above and stronger than the filesystem's, because it survives a copy.
    FILENAME = "filename"
    #: No time of this kind could be obtained.
    UNAVAILABLE = "unavailable"


class ClockTier(StrEnum):
    """How much a time is worth, graded by whether it survives being copied.

    The ordering principle is copy-robustness, not accuracy. Tiers one to three travel with the
    bytes. Tiers four and five describe this copy on this disk. A filesystem clock can be exact to
    the nanosecond and still be a fact about when somebody moved a folder. An instrument's
    acquisition time can be coarse and still be the answer to when the measurement happened.

    So the ladder is principled and not arbitrary. It produces the scope boundary as a
    consequence and not as a rule: prefer copy-robust clocks and the engine stops caring how files
    moved.

    Every rung has a source. The ND2, LIF and LI-FLIM readers fill tier one, from vendors' own
    formats that the engine claims by name. The format extractors that read a document's own
    metadata fill tier two. The census supplies tiers three, four and five.
    """

    #: Instrument acquisition time, read from ND2, LIF and LI-FLIM files. Survives copying.
    INSTRUMENT = "instrument"
    #: Document or application metadata. Survives copying.
    DOCUMENT = "document"
    #: A date parsed from the filename or path. Survives copying.
    NAME = "name"
    #: Filesystem modification time. Does not survive copying.
    MTIME = "mtime"
    #: ``ctime`` or ``birthtime``. Does not survive copying, and means different
    #: things on different platforms.
    FILESYSTEM = "filesystem"
    #: No clock of this kind was found. Not a rung on the ladder.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ClockTierProperties:
    """Declared metadata for one clock tier, so the ordering is data not lore."""

    tier: ClockTier
    #: One to five, lower being better. ``None`` for the absence state, which is
    #: not a rung and must not sort as though it were the worst one.
    rank: int | None
    #: Whether a time from this source is still true after the file is copied.
    survives_copying: bool
    definition: str


CLOCK_TIERS: tuple[ClockTierProperties, ...] = (
    ClockTierProperties(
        ClockTier.INSTRUMENT,
        1,
        survives_copying=True,
        definition="when an instrument recorded the measurement",
    ),
    ClockTierProperties(
        ClockTier.DOCUMENT,
        2,
        survives_copying=True,
        definition="a document or application's own metadata",
    ),
    ClockTierProperties(
        ClockTier.NAME,
        3,
        survives_copying=True,
        definition="a date parsed from the filename or path",
    ),
    ClockTierProperties(
        ClockTier.MTIME,
        4,
        survives_copying=False,
        definition="filesystem modification time",
    ),
    ClockTierProperties(
        ClockTier.FILESYSTEM,
        5,
        survives_copying=False,
        definition="ctime or birthtime, and platform-dependent",
    ),
    ClockTierProperties(
        ClockTier.UNAVAILABLE,
        None,
        survives_copying=False,
        definition="no clock of this kind was found",
    ),
)

#: The tiers by name, derived from the tuple above rather than written again.
#: Ranking a clock needs a lookup and the declaration needs an order, and keeping
#: both means one of them eventually disagrees with the other.
CLOCK_TIER_PROPERTIES: dict[ClockTier, ClockTierProperties] = {
    properties.tier: properties for properties in CLOCK_TIERS
}

#: Which tier each source lands on. One mapping rather than a rule written twice:
#: a source and a tier stored independently can disagree, and nothing downstream
#: could tell which was right.
CLOCK_TIER_OF: dict[TimeSource, ClockTier] = {
    TimeSource.INSTRUMENT: ClockTier.INSTRUMENT,
    TimeSource.DOCUMENT: ClockTier.DOCUMENT,
    TimeSource.FILENAME: ClockTier.NAME,
    TimeSource.MTIME: ClockTier.MTIME,
    TimeSource.MTIME_FALLBACK: ClockTier.MTIME,
    TimeSource.BIRTHTIME: ClockTier.FILESYSTEM,
    TimeSource.CTIME: ClockTier.FILESYSTEM,
    TimeSource.UNAVAILABLE: ClockTier.UNAVAILABLE,
}
