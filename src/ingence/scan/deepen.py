"""The bounded content pass: opening files, within limits, against a budget.

The census never opens a file. This does, and everything about it is arranged so
that opening files cannot damage the run.

The user grants an amount of work and the pass spends it. The pass stops when the work is gone
and marks what it did not reach as unexamined. An exhausted budget is a complete run with
honest gaps, never a failure. So a small budget is safe to offer: the worst outcome is less
information, not a broken project.

Isolation is declared, not universal. Reading eight kilobytes and hashing a
stream cannot hang. A format parser on adversarial input can, and a cancelled
future will not stop it because the thread is inside a blocking call. So each
component declares whether it needs a separate process, and only those pay for
one. Universal isolation would make a cheap pass expensive, and universal trust would
make one bad file hang a scan. An extractor declaring
:class:`~ingence.extractors.base.Isolation.SUBPROCESS` runs under
:class:`~ingence.executors.supervisor.Supervisor`, which holds the wall clock and
kills rather than asks. Everything else runs here, in this thread.

One file's failure is one file's failure. Every outcome is recorded against
the entry that produced it, and the pass continues. A file that cannot be read, a
parser that crashes and a limit that fires are each a recorded fact about that file.
The engine is built from parts that fail independently and not as a whole, and this is
the layer where that is either true or not.

Nothing here concludes anything. Detection routes, extractors read and identity
methods measure. Every output is an observation carrying the tier that says how
far it sits from the bytes. Claims come later, from rules, over this evidence.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.ids import (
    LocatorStep,
    Scope,
    ScopeKind,
    canonical_json,
    edge_id,
    entry_id,
    observation_id,
)
from ingence.core.invariants import GraphSnapshot, check_all
from ingence.core.outcomes import (
    CLOCK_TIER_OF,
    CLOCK_TIER_PROPERTIES,
    ClockTier,
    TimeSource,
)
from ingence.core.project import is_listing_root, refuse_unreachable_roots
from ingence.core.relations import Primitive
from ingence.core.schema import (
    Assertion,
    Claim,
    ComponentActivation,
    Entry,
    EntryKind,
    EntryType,
    FailureClass,
    InvariantOutcome,
    JournalEvent,
    MovedBy,
    Observation,
    ScanError,
    ScanOutcome,
    SourceScope,
    SymlinkState,
    ValueKind,
)
from ingence.core.settings import BeneathPrograms, HashMode, SpendOrder
from ingence.core.tiers import ExtractionTier, InferenceTier, ResolutionStatus
from ingence.dictionary.file_types import file_type_of, instrument_spellings
from ingence.dictionary.laboratory_packs import in_force
from ingence.executors.pool import (
    ReadContext,
    ReadPool,
    ReadResult,
    ReadTask,
    SerialReader,
    extra_modules,
    worker_count,
)
from ingence.executors.supervisor import Supervisor
from ingence.extractors import load_builtins as load_extractor_builtins
from ingence.extractors.base import (
    EXTRACTORS,
    DerivedStream,
    ExtractionContext,
    ExtractorResult,
    Isolation,
    ReadingDepth,
    isolation_of,
    reading_depth_of,
)
from ingence.extractors.captured import WarningTallies, captured, summarise
from ingence.extractors.detect import DETECTORS, ByteCharacter, detect
from ingence.extractors.limits import Limits, ceiling_for, file_ceiling_for
from ingence.identity import DIGEST_ATTRIBUTE, IDENTITY_METHODS, digest_methods
from ingence.identity import load_builtins as load_identity_builtins
from ingence.jobs.provenance import current_job_id, record_watermark
from ingence.record.log import read_at

# Aliased because the sheet's own name for the answer is the natural name for the map a
# dive reads it into, and a variable and the function that fills it must not share a spelling.
from ingence.record.verdicts import worth_reading as reading_answers
from ingence.rulebook.registry import Embedding
from ingence.scan.mention_shapes import SHAPES_KEY, PoolRoot, pool_roots, shapes_of, tally
from ingence.scan.moves import UNCHECKED_MOVES
from ingence.scan.profiles import (
    DEPTH_RANK,
    PROFILES,
    ProfileName,
    apply_profile,
    hash_choice,
    remote_choice,
)
from ingence.scan.reuse import (
    ABOVE_SIZE_LIMIT,
    EXTENSION_EXCLUDED,
    EXTENSION_NOT_INCLUDED,
    digests_wanted,
    policy_refusal,
    policy_selects,
    reader_version_is_older,
)
from ingence.scan.rules import CASCADE_MEMBERSHIP, ScanRule, scan_rules

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from ingence.core.settings import Settings
    from ingence.dictionary.store import DictionaryStore
    from ingence.extractors.base import Extractor
    from ingence.extractors.detect import TypeVerdict
    from ingence.rulebook.resolve import EffectiveRulebook
    from ingence.scan.cancel import CancellationToken
    from ingence.storage.catalog import Catalog
    from ingence.storage.journal import Journal, NullJournal

__all__ = [
    "ABOVE_SIZE_LIMIT",
    "DEEPEN_OBSERVER",
    "EXTENSION_EXCLUDED",
    "EXTENSION_NOT_INCLUDED",
    "Budget",
    "DeepenOutcome",
    "DeepenReport",
    "StageTiming",
    "deepen_entry",
    "spend_order",
]

#: The observer name on every observation this pass emits that did not come from
#: a named extractor. Type detection and byte character are readings this module
#: takes itself, and they are attributed to it rather than to nothing.
DEEPEN_OBSERVER = "deepen"
DEEPEN_OBSERVER_VERSION = "1"

#: Why a dive left files on a network share unhashed, or why it did not.
SHARE_SKIPPED_BY_SETTINGS = (
    "the settings skip a network share (hash.skip_remote_filesystems: always), need's digests too"
)
SHARE_SKIPPED_BY_DEPTH = "the depth skips a network share for a mode that hashes every file"
SHARE_HASHED_BY_NEED = (
    "need's digests are taken on a network share whatever the depth; only a person's "
    "hash.skip_remote_filesystems: always withholds them"
)

#: The readings that two files of one series are compared on: what
#: the file is, who wrote it, its dimensions and its pages. These are exact keys, and the
#: suffixes a reader's own key for one of those ends in, so a reader added later takes part by
#: naming its reading plainly.
SERIES_AGREEMENT_KEYS = frozenset({"type.media_type"})
SERIES_AGREEMENT_SUFFIXES = (
    ".software",
    ".writer",
    ".program",
    ".width",
    ".height",
    ".dimensions",
    ".pages",
    ".page_count",
    ".frame_count",
    ".image_count",
    ".tiff_variant",
    ".header_format",
    ".format_version",
)

#: Root loss: ``extraction.root_loss_failures`` consecutive access
#: failures, across two or more folders, make the pass look at the root. It then retries the
#: root every ``extraction.root_retry_seconds`` up to ``extraction.root_wait_seconds``, and
#: stops keeping what it read where the root does not return. The settings carry the reasons.

#: The operation a file that could not be reached before reading is recorded under.
STAT_OPERATION = "stat"


def _root_wait(token: CancellationToken, seconds: float) -> bool:
    """Wait for the root to come back, or for the token; True where the token spoke first."""
    return token.wait(seconds)


def _not_reached(entry: Entry, path: Path, *, run_id: str) -> DeepenOutcome:
    """A file whose stat failed before anything was read: not reached, with one access record.

    Nothing is read of a file that cannot be stat-ed. If the pass read it, detection would name
    nothing, every reader would fail, and the file would become a content error with a pile of
    errors. Instead the file is unexamined (``DEFERRED``: content work was never attempted), the
    next pass reads it, and the one record says what the system said.
    """
    import errno

    try:
        path.stat()
        kind, said = "OSError", "the file could not be stat-ed"
    except OSError as exc:
        name = errno.errorcode.get(exc.errno or 0, "")
        kind = type(exc).__name__
        said = f"the file could not be reached{f' ({name})' if name else ''}: {exc.strerror or exc}"
    outcome = DeepenOutcome(entry_id=entry.entry_id, outcome=ScanOutcome.DEFERRED)
    outcome.warnings.append((STAT_OPERATION, said))
    outcome.errors.append(
        ScanError(
            entry_id=entry.entry_id,
            relative_path=entry.relative_path,
            operation=STAT_OPERATION,
            error_type=kind,
            message=said,
            run_id=run_id,
            # The environment's doing: the file, the folder or the whole root is out of reach.
            failure_class=FailureClass.ACCESS,
        )
    )
    return outcome


def _access_failure(result: ReadResult) -> bool:
    """Whether a result says the file could not be reached, as against read and refused."""
    if result.killed_for == "timeout" and (result.stage or "").split(".")[0] in ("stat", "detect"):
        return True
    return any(
        error.failure_class is FailureClass.ACCESS and error.operation in (STAT_OPERATION, "detect")
        for error in result.outcome.errors
    )


#: The census's own facts about a file that a reading of its content never replaces:
#: its series key and size class are carried into a run even for a file this pass
#: read again, where every other old row of a re-read file gives way to the new reading.
SERIES_KEYS = frozenset({"series.key", "series.size_class"})


def series_signature(observations: Iterable[Observation]) -> frozenset[tuple[str, str]]:
    """What a read file says on the readings a series is compared on, as a set to compare."""
    found: set[tuple[str, str]] = set()
    for observation in observations:
        key = observation.key
        if key in SERIES_AGREEMENT_KEYS or key.endswith(SERIES_AGREEMENT_SUFFIXES):
            value = (
                observation.value_text
                if observation.value_text is not None
                else observation.value_integer
                if observation.value_integer is not None
                else observation.value_real
                if observation.value_real is not None
                else observation.value_structure
            )
            found.add((key, "" if value is None else str(value)))
    return frozenset(found)


def series_picks(members: Sequence[Entry], k: int) -> list[Entry]:
    """The ``k`` members of one series read first: evenly spaced through the series by identifier,
    the first and the last among them, so two runs pick the same files."""
    ordered = sorted(members, key=lambda entry: entry.entry_id)
    count = len(ordered)
    if count <= k:
        return ordered
    positions = (
        sorted({round(index * (count - 1) / (k - 1)) for index in range(k)}) if k > 1 else [0]
    )
    return [ordered[position] for position in positions]


def series_outliers(members: Sequence[Entry]) -> list[Entry]:
    """The members whose size stands out from the series' median by half again, either way."""
    sizes = sorted(entry.size_bytes or 0 for entry in members)
    if len(sizes) < 3:
        return []
    median = sizes[len(sizes) // 2]
    if median <= 0:
        return []
    return sorted(
        (
            entry
            for entry in members
            if (entry.size_bytes or 0) > 1.5 * median or (entry.size_bytes or 0) < median / 1.5
        ),
        key=lambda entry: entry.entry_id,
    )


#: The key extractor references are recorded under: one observation per extractor
#: per entry, holding the distinct paths that extractor found mentioned, sorted.
#:
#: The tier is heuristic, whatever the extractor. The contract carries no tier
#: for a reference, and the text extractor finds them with a pattern, which is
#: what the heuristic rung is. An extractor reading a format's declared links is
#: closer to the bytes than that, and saying so would need a tier on the
#: contract.
REFERENCES_KEY = "references.paths"


@dataclass
class Budget:
    """Work granted for content extraction, and what has been spent.

    Seconds rather than bytes. Bytes are what a user can predict and seconds
    are what they actually care about, and the two diverge wildly: a gigabyte of
    plain text costs less than a megabyte of deeply nested archive.

    Two clocks. ``granted_seconds`` is work: the sum of every file's
    own reading time, which a pool of N workers spends N times as fast as one. ``wall_seconds``
    is the clock on the wall, what a person waits on, and the ceiling ``extraction.total_seconds``
    names. Either running out exhausts the budget; ``None`` on either is no ceiling on that clock.
    """

    granted_seconds: float | None
    spent_seconds: float = 0.0
    #: Entries not reached because the budget ran out. Reported, not hidden.
    unreached: int = 0
    #: The wall-clock ceiling on the whole pass, or None for none.
    wall_seconds: float | None = None
    #: When the pass began, on the monotonic clock the wall ceiling is measured from.
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        """Seconds on the wall clock since the pass began."""
        return max(0.0, time.monotonic() - self.started)

    @property
    def exhausted_by(self) -> str | None:
        """Which clock ran out: ``work``, ``wall``, or None while neither has."""
        if self.granted_seconds is not None and self.spent_seconds >= self.granted_seconds:
            return "work"
        if self.wall_seconds is not None and self.elapsed >= self.wall_seconds:
            return "wall"
        return None

    @property
    def exhausted(self) -> bool:
        """Whether the budget is spent, on either clock. ``None`` granted never exhausts."""
        return self.exhausted_by is not None

    def spend(self, seconds: float) -> None:
        self.spent_seconds += max(0.0, seconds)


@dataclass(frozen=True, slots=True)
class StageTiming:
    """How long one stage of one entry's content pass took, and what it read.

    The stages are the pass's own units of work: ``detect``, one
    ``identity.<method>`` per identity method that ran, one ``extract.<name>`` per
    extractor that ran, and ``cascade`` for everything done to the members of a
    container. The pass always measures them, because a monotonic clock read is nothing beside
    opening a file. It writes them to the journal only where ``journal.timings`` says so.

    Measuring one duration per entry would not show whether a file that took
    minutes spent its time reading, hashing, in one extractor or on the members of an
    archive. The journal can say, with stages.
    """

    stage: str
    duration_ns: int
    #: Bytes the stage reports reading, where it reports any. Detection and the
    #: cascade report none, which is recorded as ``None`` rather than as nothing read.
    bytes_read: int | None = None
    #: Whether the stage ran behind a process boundary, so its duration includes a
    #: child's start-up and the time the supervisor spent waiting on it.
    isolated: bool = False

    def as_detail(self) -> list[object]:
        """Return the timing as the journal records it: stage, nanoseconds, bytes, isolated."""
        return [self.stage, self.duration_ns, self.bytes_read, self.isolated]


@dataclass
class DeepenOutcome:
    """Everything one entry's content pass produced."""

    entry_id: str
    outcome: ScanOutcome = ScanOutcome.METADATA_ONLY
    observations: list[Observation] = field(default_factory=list)
    #: Members promoted to entries of their own, and the edges saying where they
    #: came from. Empty unless the settings enable promotion.
    derived_entries: list[Entry] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    bytes_read: int = 0
    seconds: float = 0.0
    #: The same span in whole nanoseconds, read from the clock the stages are timed on, so the
    #: stages of an entry never sum to more than the entry. Converting ``seconds`` back could
    #: truncate a nanosecond below them, and a clock ticking in 16 ms steps shows it.
    nanoseconds: int = 0
    #: The content clock this pass found, where its extractors agreed on one, and
    #: where that clock is more copy-robust than the one the entry already holds.
    event_time_ns: int | None = None
    event_time_source: TimeSource | None = None
    #: Each stage of the work, in the order it ran.
    stages: list[StageTiming] = field(default_factory=list)
    #: What each stage's reader said about the file: an extractor's own reason for
    #: declining or failing, and what a library logged or warned, summarised.
    #: Each is the stage it came from and the message.
    warnings: list[tuple[str, str]] = field(default_factory=list)
    #: Members of a container that a person's extraction settings set aside unread:
    #: each member's entry identifier, its name, and the reason.
    members_set_aside: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class DeepenReport:
    """What a whole content pass did."""

    examined: int = 0
    extracted: int = 0
    skipped: int = 0
    deferred: int = 0
    failed: int = 0
    #: Files that changed while they were read, whose readings were not kept.
    changed_during_read: int = 0
    #: Why each file was chosen: ``unread`` (never read, or its reading not
    #: kept), ``deeper`` (read in part at a shallower depth), ``reader_newer`` (its kept reading
    #: came from an older version of a reader that claims its type) or ``digest`` (a
    #: digest the mode in force asks for is missing).
    chosen_because: dict[str, int] = field(default_factory=dict)
    #: Which readers chose files for ``reader_newer``: each reader's name and
    #: the number of files chosen because of it. A file whose kept reading is older for two
    #: readers is in both counts, so these can add up to more than ``chosen_because`` says.
    reader_newer_by: dict[str, int] = field(default_factory=dict)
    #: How many of the files chosen for ``reader_newer`` this pass read again successfully, a
    #: part of ``extracted``. A file the budget did not reach, or whose new reading failed, is
    #: not counted, and the next dive chooses it again.
    reader_newer_read: int = 0
    #: How many of those were signed again by themselves: a file whose only stale
    #: reading was a signature of its content (a table's columns, an array's shape) has that
    #: signature made again and nothing else read, so it counts in ``reader_newer_read`` and
    #: ``extracted`` and here.
    signed_again: int = 0
    #: Whole digests computed because two files' samples agreed, so identity can be proven.
    digested_by_need: int = 0
    #: Files whose samples agreed and which are larger than ``hash.full_hash_max_bytes``: left a
    #: nomination, never read whole.
    nominated_by_need: int = 0
    #: Files that lacked only a digest and had it computed alone, their readings kept.
    digested_alone: int = 0
    #: Moves a walk carried without reading, checked by the digest they carried:
    #: confirmed where the digest is the same, undone where it differs.
    moves_confirmed: int = 0
    moves_undone: int = 0
    #: Whether this pass wrote an older settings file's ``hash.mode: none`` as ``need``.
    settings_upgraded: bool = False
    #: The digest the pass computed and who chose it, as the run record states them.
    hash_mode: str | None = None
    hash_mode_set_by: str | None = None
    #: Whether the pass left files on a network share unhashed, and who chose.
    skip_remote_filesystems: bool | None = None
    skip_remote_filesystems_set_by: str | None = None
    #: The roots found on a network share, by filesystem type. Also the roots whose
    #: filesystem could not be named (read as local, so hashed), and the files this pass read
    #: on a share and left unhashed, with the reason. Asked only where the setting skips.
    roots_on_shares: dict[str, int] = field(default_factory=dict)
    roots_unnamed: int = 0
    #: Said where a person's ``always`` could not be applied to a root read as local because its
    #: filesystem could not be named: shown on the terminal and in the journal.
    unnamed_said: str | None = None
    unhashed_on_shares: int = 0
    unhashed_on_shares_because: str | None = None
    #: Members recovered from containers and promoted to entries of their own.
    promoted: int = 0
    #: Entries whose event clock this pass improved. The number that says whether
    #: the long-tail format extractors are reaching tiers one and two at all.
    clocks_filled: int = 0
    #: Files that a person's extraction settings set aside unread, by the reason
    #: (``scan.reuse.policy_refusal``'s words): an excluded extension, an include list naming
    #: others, a size above the limit. These are never counted with ``skipped``, which is content
    #: that cannot be read. A person chose not to have these read.
    set_aside_by_settings: dict[str, int] = field(default_factory=dict)
    #: Files read whose extension a person answered ``no`` or ``never`` about, by the word.
    #: These are never counted with ``skipped``: that says there was nothing to read, and
    #: this says a person would rather it had been read later.
    read_despite: dict[str, int] = field(default_factory=dict)
    #: The programs this pass found and what lay beneath them: the folders the
    #: project's Rulebook defers beneath, the files beneath them the pass would have read, and
    #: of those how many it left unread because ``beneath_programs`` says never. Read last, the
    #: default, they are in ``examined`` where the budget reached them.
    programs_found: int = 0
    files_beneath_programs: int = 0
    left_beneath_programs: int = 0
    bytes_read: int = 0
    budget: Budget | None = None
    #: The pass's clocks: seconds on the wall clock from the first file to
    #: the run record, and how many workers read files at once (one until a pool reads them).
    wall_seconds: float | None = None
    workers: int = 1
    #: Series first: how many series the candidates formed, how
    #: many files were left unread because a read file of their series described them, and how
    #: many rounds of reading the pass took after the first two (each doubling what a series
    #: whose read files disagreed had read).
    series_found: int = 0
    left_to_series: int = 0
    series_rounds: int = 0
    #: Files that could not be reached: their stat failed before
    #: anything was read, so they stay unexamined with one access record each. Also the root the
    #: pass found gone after twenty such failures across two folders, if any, with what it did.
    not_reached: int = 0
    root_lost: str | None = None
    invariants: tuple[InvariantOutcome, ...] = ()
    #: True when the pass was cancelled and therefore published nothing. Named
    #: rather than inferred from a count, because "we stopped" and "there was
    #: nothing to do" are different states and a caller must be able to tell.
    cancelled: bool = False
    cancellation_reason: str | None = None
    #: True when the pass was stopped and kept what it had: it published
    #: exactly as an exhausted budget does, the files not reached counted and left unexamined.
    stopped: bool = False
    stop_reason: str | None = None
    #: Seconds each phase of publishing took: ``invariants``, ``compaction``,
    #: ``journal``, ``manifest`` and ``commit``, in that order.
    #:
    #: The first two are also in the run's journal and the last three cannot be.
    #: The journal is a table of the run it describes, so it closes before its own
    #: compaction, before the manifest that digests it and before the rename that
    #: makes the run visible. A row about any of those would have to be written into
    #: a run that is already sealed. They are reported here, where the terminal shows
    #: them, and are not left untimed.
    publish_seconds: dict[str, float] = field(default_factory=dict)


def supervisor_for(settings: Settings) -> Supervisor | None:
    """Return the supervisor this run's settings ask for, or nothing.

    One function, one call site, so switching isolation off is a settings change
    rather than a code path anybody has to find. ``None`` means every extractor
    runs in this thread whatever it declared. That behaviour exists to be measured
    against and not to be relied on.

    The isolation setting does not change the budget. ``Budget`` counts work seconds, the sum
    of every file's own reading time. A pool of N workers spends it N times as fast, so
    ``--budget 30`` buys the same work at eight workers as at one.
    """
    extraction = settings.extraction
    if not extraction.use_subprocess_isolation:
        return None
    return Supervisor(
        timeout_seconds=extraction.subprocess_timeout_seconds,
        memory_bytes=extraction.subprocess_memory_bytes,
        workers=1,
    )


def _turns(ranked: Iterable[Entry], family: Callable[[Entry], str]) -> dict[str, int]:
    """Return each entry's turn within its family: its family's first file 0, its second 1.

    ``ranked`` is already in the order a family spends its own turns, so this only counts.
    """
    turn: dict[str, int] = {}
    taken: dict[str, int] = {}
    for entry in ranked:
        key = family(entry)
        turn[entry.entry_id] = taken.get(key, 0)
        taken[key] = turn[entry.entry_id] + 1
    return turn


def spend_order(
    entries: Sequence[Entry],
    order: SpendOrder = SpendOrder.FAMILY_ROUND_ROBIN,
    *,
    answers: Mapping[str, str] | None = None,
    file_type: Callable[[Entry], str | None] | None = None,
    leads: Callable[[Entry], bool] | None = None,
    deferred: Callable[[Entry], bool] | None = None,
    series: Callable[[Entry], str] | None = None,
) -> list[Entry]:
    """Return entries in the order the budget should be spent on them.

    ``series`` names each entry's series key, and the order is one file of every series before
    any series gets a second. The series take their turns in the family rotation by their first
    member, each contributing its first file by identifier in the first round and, in the
    second, its last and any file whose size stands out. What a series' two files say decides
    whether more of it are read. The pass settles that as the readings come back
    (``run_deepen``), never here.

    This is the strategy point for the prioritiser. The design calls for
    spending by descending uncertainty times salience, so that a limited budget
    buys the most useful information and not the first. Until those exist the
    order is one of three fixed ones, each canonical by size and then identifier, so
    two identical runs spend the budget on the same files in the same order.

    Smallest first examines the most entries, which is the argument for it.
    Spent that way, a whole budget can examine only the smallest files and reach none of the
    instrument files whose readers need only a header.

    Family round-robin is the default. The families take turns, every family's
    first file before any family's second, and within a round smallest first. The files
    a budget reaches are fewer and far more varied, which is what the formats a
    laboratory owns its readers for need.

    A family is the file type that an entry's extension binds to. ``file_type`` names it, and
    ``.tif`` and ``.tiff`` take one turn between them because they are one kind of file. A
    spelling is not a kind of file, and counting each extension as a family would add dozens of
    families and change how thinly the same budget spreads. Every extension bound to no
    type shares one family, so five thousand one-off suffixes take one turn a round
    between them and not five thousand, and an unknown extension is still reached.

    A family spends its own turns rotating over its extensions, smallest first within
    each. The unbound family needs that for its suffixes to be reached at all, and a type
    needs it because the Dictionary's types are broad. Taken smallest first, a
    type's files can spend every turn of ``vendor_image_format`` on its smallest
    spelling and every turn of ``image_format`` on PNG and GIF, so a whole dive's worth of files
    holds no ND2, LIF, CZI or TIFF at all. The rotation is inside a
    family's turn and mints no turn of its own, so junk suffixes still cannot swamp it.

    Without a vocabulary every extension is unbound and, with no answers, the order is the
    extension rotation exactly. *Extension round-robin* is that rotation by setting, kept to
    measure against.

    What a person said about opening each extension orders the spend and vetoes
    nothing. ``answers`` maps an extension to the word a
    person gave on sheet 08. A family spends its turns on its unanswered and ``yes`` files
    first, then ``no``, then ``never``; and within a round the same order holds across
    families. A budget that runs out runs out on the files nobody wanted read, and a budget
    with room reads them too.

    Within a family, a spelling an instrument writes takes the family's turns first.
    Images are one type, so ND2, CZI and LIF share one family's turns with TIFF, PNG and GIF.
    With all images in one type and without this rule, the ND2 files among the first a dive
    reads can fall by more than half. ``leads`` names the entries whose spelling the Dictionary
    declares an instrument writes. They rotate among themselves first, within each answer tier,
    and the family's other spellings follow. This rotation is inside the family and mints no
    turn: images still take one turn a round.

    Files beneath a program come after everything else. ``deferred`` names the files that lie
    beneath a folder the Rulebook defers beneath. The function orders them on their own, by
    every rule above, and they follow every other file. Each half is the order it would be
    without the other, so the files a dive reads first are exactly those it would read in a pool
    holding no program, and a dive with room left reads the program's files in their own
    families' turns.
    """
    if deferred is not None:
        kept = [entry for entry in entries if not deferred(entry)]
        later = [entry for entry in entries if deferred(entry)]
        return [
            *spend_order(
                kept, order, answers=answers, file_type=file_type, leads=leads, series=series
            ),
            *spend_order(
                later, order, answers=answers, file_type=file_type, leads=leads, series=series
            ),
        ]
    if order is SpendOrder.SERIES_FIRST:
        rotation = spend_order(
            entries,
            SpendOrder.FAMILY_ROUND_ROBIN,
            answers=answers,
            file_type=file_type,
            leads=leads,
        )
        rank = {entry.entry_id: position for position, entry in enumerate(rotation)}
        grouped: dict[str, list[Entry]] = {}
        for entry in rotation:
            key = series(entry) if series is not None else entry.entry_id
            grouped.setdefault(key, []).append(entry)
        first_round: list[Entry] = []
        second_round: list[Entry] = []
        for members in grouped.values():
            picked = series_picks(members, 2)
            first_round.append(picked[0])
            rest = picked[1:] + [entry for entry in series_outliers(members) if entry not in picked]
            second_round.extend(rest)
        first_round.sort(key=lambda entry: rank[entry.entry_id])
        second_round.sort(key=lambda entry: rank[entry.entry_id])
        return [*first_round, *second_round]
    said = {extension.casefold(): word for extension, word in (answers or {}).items()}

    def follows(entry: Entry) -> int:
        return 0 if leads is None or leads(entry) else 1

    def tier(entry: Entry) -> int:
        word = said.get((entry.extension or "").casefold())
        return 2 if word == NEVER_WORTH_READING else 1 if word == LAST_WORTH_READING else 0

    def canonical(entry: Entry) -> tuple[int, int, str]:
        return tier(entry), entry.size_bytes or 0, entry.entry_id

    def extension(entry: Entry) -> str:
        return (entry.extension or "").casefold()

    if order is SpendOrder.SMALLEST_FIRST:
        return sorted(entries, key=lambda entry: (entry.size_bytes or 0, entry.entry_id))
    ranked = sorted(entries, key=canonical)
    families: dict[str | None, list[Entry]] = {}
    for entry in ranked:
        family = (
            extension(entry)
            if order is SpendOrder.EXTENSION_ROUND_ROBIN
            else None
            if file_type is None
            else file_type(entry)
        )
        families.setdefault(family, []).append(entry)
    turn: dict[str, int] = {}
    for members in families.values():
        inner = _turns(members, extension)
        in_its_turns = sorted(
            members,
            key=lambda entry: (
                tier(entry),
                follows(entry),
                inner[entry.entry_id],
                *canonical(entry)[1:],
            ),
        )
        turn.update((entry.entry_id, position) for position, entry in enumerate(in_its_turns))
    return sorted(ranked, key=lambda entry: (turn[entry.entry_id], *canonical(entry)))


def _reading_observation(
    *,
    entry_id: str,
    run_id: str,
    observer: str,
    observer_version: str,
    observed_at: str,
    key: str,
    value: str | int | float | bool | None,
    kind: ValueKind,
    tier: ExtractionTier,
    source_scope: SourceScope = SourceScope.CONTENT,
    locator: tuple[LocatorStep, ...] = (),
) -> Observation:
    """Turn one reading into an observation, filling the typed value column."""
    fields: dict[str, object] = {
        "value_text": None,
        "value_integer": None,
        "value_real": None,
        "value_boolean": None,
        "value_structure": None,
    }
    if kind is ValueKind.INTEGER:
        fields["value_integer"] = int(value) if value is not None else None
    elif kind is ValueKind.REAL:
        fields["value_real"] = float(value) if value is not None else None
    elif kind is ValueKind.BOOLEAN:
        fields["value_boolean"] = bool(value) if value is not None else None
    elif kind is ValueKind.STRUCTURE:
        fields["value_structure"] = str(value) if value is not None else None
    elif kind is not ValueKind.NEGATIVE:
        fields["value_text"] = str(value) if value is not None else None

    return Observation(
        observation_id=observation_id(
            entry=entry_id,
            observer=observer,
            observer_version=observer_version,
            key=key,
            locator_chain=locator,
        ),
        entry_id=entry_id,
        run_id=run_id,
        observer=observer,
        observer_version=observer_version,
        observed_at=observed_at,
        key=key,
        value_kind=kind,
        extraction_tier=tier,
        source_scope=source_scope,
        locator_chain=locator,
        **fields,
    )


_HEADER_WAS_ENOUGH = (
    "the header said what this file is and when it was made, so its body was not read; "
    "a file above the sample ceiling is read in part either way"
)

#: What a binary file of any size records when its header reader answered.
_HEADER_WAS_ENOUGH_FOR_A_BINARY = (
    "the file's first bytes name a binary format and its header reader answered, so its "
    "body was not read; a body of bytes that are not text holds no text to harvest"
)

#: The byte characters a file whose first bytes name a format may have and still be text, which
#: is a body worth reading whatever its header said.
_TEXT_CHARACTERS = frozenset({ByteCharacter.TEXT, ByteCharacter.WIDE_TEXT, ByteCharacter.EMPTY})


def _signature_names_a_binary_format(verdict: TypeVerdict) -> bool:
    """Whether the file's own bytes name a format, and the bytes are not text.

    This is a reading of the bytes and never a guess from the name. The opinion has to sit at
    the mechanically exposed tier, which is where a magic number is and an extension is not, and
    the media type it names has to lie outside ``text/``. The sample's byte character has to
    say the same, because a format whose signature is a few bytes can front a file that is
    text after them, and a text body is the one worth reading. No detector is named here.
    """
    if verdict.profile is None or verdict.profile.character in _TEXT_CHARACTERS:
        return False
    return any(
        opinion.media_type is not None
        and not opinion.media_type.startswith("text/")
        and opinion.extraction_tier is ExtractionTier.MECHANICALLY_EXPOSED
        for opinion in verdict.opinions
    )


def _header_first(names: Sequence[str]) -> tuple[str, ...]:
    """Return extractor names with the header readers first, order otherwise kept.

    Stable, so two runs spend a budget on the same readings in the same order, and it
    asks each extractor rather than holding a list: nothing here names a component.
    """
    depths: dict[str, ReadingDepth] = {}
    for name in names:
        if not EXTRACTORS.available(name):
            depths[name] = ReadingDepth.CONTENT
            continue
        try:
            depths[name] = reading_depth_of(EXTRACTORS.get(name))
        except Exception:
            depths[name] = ReadingDepth.CONTENT
    return tuple(sorted(names, key=lambda name: depths[name] is not ReadingDepth.HEADER))


def _a_header_reader_answered(outcomes: Sequence[ExtractorResult]) -> bool:
    """Whether a reader that only reads a header produced anything for this file.

    The test is a reader's answer and not a detector's guess, and the name says so on
    purpose. The word "inconclusive" is not used, because the engine's vocabulary avoids it.
    Taking a detector's verdict as the test does not work in any case. On this engine the
    detectors name something for almost every file. For example, a byte pattern invented for a
    test came back as an ISO 9660 image, confidently, from one detector, so a large file would
    almost never have its body read. Naming a type also says what a file *is* and nothing about
    what is *in* it: a `.csv` that the detectors identify perfectly still keeps its columns,
    its mentions and its text in the body.

    So the test asks: did an instrument clock, a vendor's metadata or a TIFF's tags come back?
    Where no header reader spoke, the body is read in full.
    """
    return any(produced.readings or produced.event_time_ns is not None for produced in outcomes)


#: The answer that sends a file to the back of every turn, as a person writes it on a
#: sheet. A person's judgement about value is an ordering and not a fact about the file, so
#: the answer does not stop a dive opening the file.
NEVER_WORTH_READING = "never"

#: The answer that reads a file after the unanswered and the ``yes``, and before ``never``.
LAST_WORTH_READING = "no"


def _content_out_of_reach(entry: Entry) -> str | None:
    """Why this entry's content cannot be read, or None where it can.

    The same facts ``content_unreadable`` raises, read here from the entry the census wrote:
    the dive and the rule must agree about which files have no content, and a second reading of
    the same facts is the copy that drifts. Only the two a dive can do nothing about are here,
    no bytes and a link with no target, because a format with no reader is a file the dive
    still opens and still learns from.
    """
    if entry.symlink_state is SymlinkState.BROKEN:
        return "the link's target is not there, so there is no content to read"
    if entry.entry_type is EntryType.FILE and (entry.size_bytes or 0) == 0:
        return (
            "it holds no bytes, so there is nothing to detect, digest or extract; what it is "
            "is read from its name and its position"
        )
    return None


def _detection_observations(
    verdict: object, *, entry_id: str, run_id: str, observed_at: str
) -> list[Observation]:
    """Record what detection concluded, including the disagreement.

    Every detector's opinion is stored, not only the winner. A file where three
    detectors agreed is a different situation from one where two split, and an
    engine that kept only the winning label could not tell them apart later. The
    disagreement is also the cheapest useful finding available: an extension that
    contradicts the content.
    """
    observations: list[Observation] = []

    def add(key: str, value: object, kind: ValueKind, tier: ExtractionTier) -> None:
        if value is None:
            return
        observations.append(
            _reading_observation(
                entry_id=entry_id,
                run_id=run_id,
                observer=DEEPEN_OBSERVER,
                observer_version=DEEPEN_OBSERVER_VERSION,
                observed_at=observed_at,
                key=key,
                value=value,  # type: ignore[arg-type]
                kind=kind,
                tier=tier,
            )
        )

    add("type.media_type", verdict.media_type, ValueKind.TEXT, ExtractionTier.MECHANICALLY_EXPOSED)  # type: ignore[attr-defined]
    add("type.chosen_by", verdict.chosen_by, ValueKind.TEXT, ExtractionTier.LITERAL)  # type: ignore[attr-defined]
    add("type.agreement", verdict.agreement.value, ValueKind.TEXT, ExtractionTier.LITERAL)  # type: ignore[attr-defined]
    if verdict.extension_agrees is not None:  # type: ignore[attr-defined]
        add(
            "type.extension_agrees",
            verdict.extension_agrees,  # type: ignore[attr-defined]
            ValueKind.BOOLEAN,
            ExtractionTier.HEURISTIC,
        )
    for opinion in verdict.opinions:  # type: ignore[attr-defined]
        add(
            f"type.opinion.{opinion.detector}",
            opinion.media_type or opinion.label,
            ValueKind.TEXT,
            opinion.extraction_tier,
        )
    profile = verdict.profile  # type: ignore[attr-defined]
    if profile is not None:
        add("bytes.character", profile.character.value, ValueKind.TEXT, ExtractionTier.LITERAL)
        add("bytes.entropy", profile.entropy, ValueKind.REAL, ExtractionTier.LITERAL)
        add(
            "bytes.printable_ratio", profile.printable_ratio, ValueKind.REAL, ExtractionTier.LITERAL
        )
    return observations


def scan_terms_for(settings: Settings, store: DictionaryStore | None) -> tuple[str, ...]:
    """Return the byte-scan vocabulary the settings ask for.

    Empty unless a project opts in. The scan is gated on the Dictionary rather
    than on a language, which is what keeps its cost proportional to what the
    project knows instead of to the size of English, and the settings decide how
    much of what it knows is worth the time.
    """
    if store is None or not settings.extraction.byte_scan_sections:
        return ()
    wanted = set(settings.extraction.byte_scan_sections)
    return tuple(
        sorted(
            {
                surface
                for sense in store.senses()
                if sense.section in wanted
                for surface in sense.surfaces
            }
        )
    )


def _supervised(
    extractor: Extractor,
    context: ExtractionContext,
    *,
    supervisor: Supervisor | None,
    above_bytes: int | None,
) -> bool:
    """Whether this extractor reads this content behind the supervisor's boundary.

    Where the extractor declared one, or where the content is too large to trust in
    process. An extractor declaring :attr:`Isolation.IN_PROCESS` asserts
    cheap, bounded work, which holds for small content and not
    for medium text: a text file of a few megabytes ran for minutes with no ceiling
    that could stop it. Above ``above_bytes`` the declaration is overruled and the wall
    clock applies. With isolation switched off nothing is supervised.
    """
    if supervisor is None:
        return False
    if isolation_of(extractor) is Isolation.SUBPROCESS:
        return True
    return above_bytes is not None and context.size_bytes > above_bytes


def _extract_once(
    name: str,
    extractor: Extractor,
    context: ExtractionContext,
    *,
    supervisor: Supervisor | None,
    entry: Entry,
    run_id: str,
    supervise_above_bytes: int | None = None,
    ceiling_seconds: float | None = None,
) -> tuple[ExtractorResult | None, list[ScanError], bool]:
    """Run one extractor over one piece of content, behind the boundary it declared.

    One route serves a file and a member of a container alike. A whole-file path
    that consults :func:`isolation_of`, beside a member path that calls ``extract`` directly,
    would give a format parser declaring a process boundary one for a file on disk and none
    for the same file inside an archive. With a hanging fixture and a two-second ceiling, such
    a split killed and recorded the parser as a file, but left it still running after thirty
    seconds as a member, where it had to be killed from outside. Archives are
    where adversarial input arrives, and that is why every format parser the engine
    ships declares the boundary.

    Returns the contained result where supervision stopped an extractor, because a
    contained failure is an outcome the entry's combined outcome has to count, and
    no result where an extractor running in this process raised. The third value
    says whether the extractor ran behind the boundary, for the stage timing.
    """
    errors: list[ScanError] = []
    if supervisor is not None and _supervised(
        extractor, context, supervisor=supervisor, above_bytes=supervise_above_bytes
    ):
        # The parent holds the clock. Nothing the child does reaches here as an
        # exception, so there is no except clause to write: a hang, a crash and an
        # exhausted address space all arrive as a result whose outcome is a
        # content error, which is exactly the point of the boundary.
        supervised = supervisor.run(name, context, ceiling_seconds=ceiling_seconds)
        produced = supervised.result
        if supervised.contained:
            errors.append(
                ScanError(
                    entry_id=entry.entry_id,
                    relative_path=entry.relative_path,
                    operation=f"extract.{name}",
                    error_type=produced.error_type or "IsolationError",
                    message=produced.error_message or "contained by the supervisor",
                    run_id=run_id,
                    # A parser that hung, crashed or outgrew its ceiling did not
                    # fail to reach the file: it failed to make sense of it. That
                    # is coverage, and the fix is an extractor.
                    failure_class=FailureClass.COMPREHENSION,
                )
            )
        return produced, errors, True
    try:
        with captured() as said:
            produced = extractor.extract(context)
    except Exception as exc:
        errors.append(
            ScanError(
                entry_id=entry.entry_id,
                relative_path=entry.relative_path,
                operation=f"extract.{name}",
                error_type=type(exc).__name__,
                message=str(exc),
                run_id=run_id,
                failure_class=FailureClass.COMPREHENSION,
            )
        )
        return None, errors, False
    if said:
        produced = dataclasses.replace(produced, warnings=produced.warnings + summarise(said))
    return produced, errors, False


def _extractor_observations(
    produced: ExtractorResult,
    *,
    entry_id: str,
    run_id: str,
    observed_at: str,
    roots: Sequence[PoolRoot] = (),
) -> list[Observation]:
    """Turn what one extractor produced into observations: readings and references.

    One function serves both routes into the extractors, for the reason
    :func:`_extract_once` is one function: the member path recorded readings too, and a
    second copy of this loop would drop references on one route when the other changed.
    """
    observations = [
        _reading_observation(
            entry_id=entry_id,
            run_id=run_id,
            observer=produced.extractor,
            observer_version=produced.extractor_version,
            observed_at=observed_at,
            key=reading.key,
            value=reading.value,
            kind=reading.kind,
            tier=reading.extraction_tier,
            source_scope=reading.source_scope,
            locator=reading.locator,
        )
        for reading in produced.readings
    ]
    if produced.references:
        observations.append(
            _reading_observation(
                entry_id=entry_id,
                run_id=run_id,
                observer=produced.extractor,
                observer_version=produced.extractor_version,
                observed_at=observed_at,
                key=REFERENCES_KEY,
                value=canonical_json(sorted(set(produced.references))),
                kind=ValueKind.STRUCTURE,
                tier=ExtractionTier.HEURISTIC,
            )
        )
        # Each mention's shape, beside it: what the extractor read off the
        # mention's line, or its spelling where it read nothing, with a rooted path
        # placed against this pool's roots. Nothing is resolved.
        read = dict(zip(produced.references, produced.reference_shapes, strict=False))
        distinct = sorted(set(produced.references))
        observations.append(
            _reading_observation(
                entry_id=entry_id,
                run_id=run_id,
                observer=produced.extractor,
                observer_version=produced.extractor_version,
                observed_at=observed_at,
                key=SHAPES_KEY,
                value=canonical_json(
                    shapes_of(
                        distinct,
                        [read[path] for path in distinct] if read else [],
                        roots,
                    )
                ),
                kind=ValueKind.STRUCTURE,
                tier=ExtractionTier.HEURISTIC,
            )
        )
    return observations


#: What a file's own state is taken as, before and after a reading: its size, its modification
#: time, and its inode and device where the filesystem gives them.
SourceState = tuple[int | None, int | None, int | None, int | None]

#: The operation a reading the file changed under is recorded as.
FRESHNESS_OPERATION = "freshness"


def source_state(path: Path) -> SourceState | None:
    """The file's size, modification time, inode and device now, or None where it cannot be stat-ed.

    This is not the census's fingerprint (``scan/reuse.py``). The change time is left out, because
    a permission change moves it without touching a byte, and a reading is guarded against the
    bytes changing, not the metadata. The inode and device are in, because within one reading a
    file replaced by another (a program saving by writing a new file and renaming it over the
    old) can keep its size and time.
    """
    try:
        result = path.stat()
    except OSError:
        return None
    return (
        int(result.st_size),
        int(result.st_mtime_ns),
        int(result.st_ino) or None,
        int(result.st_dev) or None,
    )


def _moved(before: SourceState, after: SourceState | None) -> str:
    """Say in words what differed between two states of one file."""
    if after is None:
        return "the file could no longer be stat-ed after it was read"
    names = ("size", "modification time", "inode", "device")
    changed = [
        f"{name} {was} then {now}"
        for name, was, now in zip(names, before, after, strict=True)
        if was != now
    ]
    return "the file changed while it was read: " + "; ".join(changed)


#: What a file read for its clock alone records, once.
_CLOCK_ONLY = "an ND2 read for its clock alone (extraction.nd2_clock_only)"


def deepen_entry(
    entry: Entry,
    path: Path,
    *,
    run_id: str,
    observed_at: str,
    settings: Settings,
    terms: tuple[str, ...] = (),
    detectors: tuple[str, ...] = (),
    identity_methods: tuple[str, ...] = (),
    extractors: tuple[str, ...] = (),
    membership: ScanRule | None = None,
    roots: Sequence[PoolRoot] = (),
    need_digests: frozenset[str] = frozenset(),
    extract: bool = True,
    is_remote: bool = False,
    isolated: bool = True,
    progress: Callable[[str], None] | None = None,
) -> DeepenOutcome:
    """Open one file, within limits, and record what was found.

    ``isolated`` False runs every reader in this process: the caller is a
    read worker, itself the boundary a hostile file is contained by, so no reader asks for a
    child of its own. ``progress`` is told each stage's name as it starts, so a worker that is
    killed is recorded with the stage it was in.

    ``need_digests`` are the byte digests a comparison needs of this file beyond what the hash
    settings ask for (``need``). ``extract`` False computes the identity stage alone,
    which is how a whole digest a comparison needs is added to a file already read. ``is_remote``
    says the file lies on a network share, which the hash settings may leave unhashed.

    The function never raises for an ordinary outcome. A file that cannot be read, a parser
    that fails and a limit that fires each become a recorded fact about that file,
    and the pass continues to the next one.

    A file that changed while it was read keeps none of the reading. Its :func:`source_state`
    is taken before anything is opened and again after everything is read. Where they differ,
    what was read may describe neither version, so the outcome is ``changed_during_read``
    (never reusable, so the next pass reads it again), every reading, digest, member and clock
    is dropped, and one access record says what moved.
    """
    if progress is not None:
        progress("stat")
    before = source_state(path)
    if before is None:
        return _not_reached(entry, path, run_id=run_id)
    since = _changed_since_census(entry, before)
    if since is not None:
        # Not the file the census described: its readings would be
        # filed under an entry of another size or time, so it is not opened until a walk.
        refused = DeepenOutcome(entry_id=entry.entry_id, outcome=ScanOutcome.CHANGED_DURING_READ)
        refused.warnings.append((FRESHNESS_OPERATION, since))
        refused.errors.append(_freshness_error(entry, since, run_id=run_id))
        return refused
    result = _read_entry(
        entry,
        path,
        run_id=run_id,
        observed_at=observed_at,
        settings=settings,
        terms=terms,
        detectors=detectors,
        identity_methods=identity_methods,
        extractors=extractors,
        membership=membership,
        roots=roots,
        need_digests=need_digests,
        extract=extract,
        is_remote=is_remote,
        isolated=isolated,
        progress=progress,
    )
    if before is None or result.outcome is ScanOutcome.SKIPPED_BY_POLICY:
        return result
    if progress is not None:
        progress("stat.again")
    after = source_state(path)
    compared = 4 if settings.extraction.freshness_checks_identity else 2
    if after is not None and after[:compared] == before[:compared]:
        return result
    result.outcome = ScanOutcome.CHANGED_DURING_READ
    result.observations.clear()
    result.derived_entries.clear()
    result.assertions.clear()
    result.errors.clear()
    result.event_time_ns = None
    result.event_time_source = None
    said = _moved(before, after)
    result.warnings.append((FRESHNESS_OPERATION, said))
    result.errors.append(_freshness_error(entry, said, run_id=run_id))
    return result


#: The operation a reading the file's own clock stopped is recorded under.
FILE_CLOCK_OPERATION = "file_seconds"


def _file_clock_error(entry: Entry, *, run_id: str, ceiling: float, stage: str) -> ScanError:
    """The record of a file whose reading reached ``file_seconds`` before ``stage`` could run."""
    return ScanError(
        entry_id=entry.entry_id,
        relative_path=entry.relative_path,
        operation=FILE_CLOCK_OPERATION,
        error_type="FileSecondsExceeded",
        message=(
            f"the file's reading reached its ceiling of {ceiling:.1f} s before {stage} ran "
            "(extraction.file_seconds, plus the allowance per gigabyte); what was read before "
            "it is kept with this error and the next pass reads the file again"
        ),
        run_id=run_id,
        # The work was attempted and the file, or a reader, did not finish in time: coverage,
        # as a supervised child's timeout is.
        failure_class=FailureClass.COMPREHENSION,
    )


def _freshness_error(entry: Entry, said: str, *, run_id: str) -> ScanError:
    return ScanError(
        entry_id=entry.entry_id,
        relative_path=entry.relative_path,
        operation=FRESHNESS_OPERATION,
        error_type="ChangedDuringRead",
        message=said,
        run_id=run_id,
        # Another program was writing: the environment's doing, never a rule's or a reader's.
        failure_class=FailureClass.ACCESS,
    )


def _changed_since_census(entry: Entry, before: SourceState | None) -> str | None:
    """Say how a file differs from what the census recorded of it, or None where it does not.

    The check uses the size and modification time only, where both were recorded. The census's
    inode and device are volatile, and a reading is refused for the bytes and not the mount.
    """
    if before is None or entry.size_bytes is None or entry.modified_ns is None:
        return None
    recorded = (entry.size_bytes, entry.modified_ns)
    if before[:2] == recorded:
        return None
    names = ("size", "modification time")
    changed = [
        f"{name} {was} then {now}"
        for name, was, now in zip(names, recorded, before[:2], strict=True)
        if was != now
    ]
    return (
        "the file changed since the census recorded it: "
        + "; ".join(changed)
        + ". Walk the folder again (ingence index) to read it"
    )


def _read_entry(
    entry: Entry,
    path: Path,
    *,
    run_id: str,
    observed_at: str,
    settings: Settings,
    terms: tuple[str, ...] = (),
    detectors: tuple[str, ...] = (),
    identity_methods: tuple[str, ...] = (),
    extractors: tuple[str, ...] = (),
    membership: ScanRule | None = None,
    roots: Sequence[PoolRoot] = (),
    need_digests: frozenset[str] = frozenset(),
    extract: bool = True,
    is_remote: bool = False,
    isolated: bool = True,
    progress: Callable[[str], None] | None = None,
) -> DeepenOutcome:
    """Open one file, within limits, and record what was found (:func:`deepen_entry`'s body)."""

    def stage(name: str) -> int:
        """Say the stage is starting, and return the clock it is timed from."""
        if progress is not None:
            progress(name)
        return time.monotonic_ns()

    started = time.monotonic_ns()
    result = DeepenOutcome(entry_id=entry.entry_id)
    limits = Limits.from_settings(settings.extraction, size_bytes=entry.size_bytes)
    # One file, one clock, every stage. ``file_seconds`` plus the
    # per-gigabyte allowance bounds the whole reading: detection, identity, every reader and the
    # members. Between stages the clock is read and a stage is refused once it has run out. A
    # supervised child is given no more than what is left of it, and a pool's worker is killed
    # at it.
    file_ceiling = file_ceiling_for(entry.size_bytes, settings.extraction)
    deadline_ns = started + int(file_ceiling * 1e9)
    clock_ran_out = False

    def out_of_time(stage: str) -> bool:
        """Whether the file's clock has run out; the first time, it is recorded against the file."""
        nonlocal clock_ran_out
        if time.monotonic_ns() < deadline_ns:
            return False
        if not clock_ran_out:
            clock_ran_out = True
            result.errors.append(
                _file_clock_error(entry, run_id=run_id, ceiling=file_ceiling, stage=stage)
            )
        return True

    def seconds_left() -> float:
        return max(0.001, (deadline_ns - time.monotonic_ns()) / 1e9)

    # A file whose content cannot be read is skipped by policy, and the reason is recorded
    # per entry. Such files cannot be read deeply or hashed, so the analysis goes on without a
    # content reading or a digest for them. The omission is deliberate
    # and not a failure, and saying so per entry keeps it from looking like a gap. It also
    # keeps a digest from being computed over no bytes at all, which every empty file would
    # share: a copy rule reading content would then claim one class of every empty file in
    # the pool.
    # Nothing is skipped for what a person said about its extension. Their `never` and `no`
    # order the spend (:func:`spend_order`) and reach no further: an abstention the user
    # caused must not look like one the engine chose, and an answer given in one month must
    # not stop the engine getting evidence in the next.
    # Skipped by policy means one thing: there is nothing here to read.
    out_of_reach = _content_out_of_reach(entry)
    if out_of_reach is not None:
        result.warnings.append(("content", out_of_reach))
        result.outcome = ScanOutcome.SKIPPED_BY_POLICY
        result.nanoseconds = time.monotonic_ns() - started
        result.seconds = result.nanoseconds / 1e9
        return result

    extensions = frozenset(
        value
        for value in (
            f".{entry.extension}" if entry.extension else "",
            f".{entry.compound_extension}" if entry.compound_extension else "",
        )
        if value
    )

    # -- routing ---------------------------------------------------------
    stage_started = stage("detect")
    try:
        verdict = detect(path, extensions, enabled=detectors)
    except OSError as exc:
        result.stages.append(StageTiming("detect", time.monotonic_ns() - stage_started))
        result.errors.append(
            ScanError(
                entry_id=entry.entry_id,
                relative_path=entry.relative_path,
                operation="detect",
                error_type=type(exc).__name__,
                message=str(exc),
                run_id=run_id,
                # An ``OSError`` reaching here means the file could not be opened
                # or read, not that nothing understood it. That is the
                # environment's problem and never a rule's.
                failure_class=FailureClass.ACCESS,
            )
        )
        result.outcome = ScanOutcome.CONTENT_ERROR
        result.nanoseconds = time.monotonic_ns() - started
        result.seconds = result.nanoseconds / 1e9
        return result
    result.stages.append(StageTiming("detect", time.monotonic_ns() - stage_started))

    result.observations.extend(
        _detection_observations(
            verdict, entry_id=entry.entry_id, run_id=run_id, observed_at=observed_at
        )
    )

    context = ExtractionContext(
        entry_id=entry.entry_id,
        path=path,
        relative_path=entry.relative_path,
        extensions=extensions,
        size_bytes=entry.size_bytes or 0,
        media_type=verdict.media_type,
        media_type_source=verdict.chosen_by,
        limits=limits,
        terms=terms,
    )

    # -- identity --------------------------------------------------------
    load_identity_builtins()
    wanted = identity_methods or IDENTITY_METHODS.names(available_only=True)
    # The hash settings choose the byte digest. A method declaring which digest it is runs
    # only where the settings ask for that one. Every other identity method is not a digest
    # and runs as it always does.
    digests = (
        digests_wanted(
            extensions=frozenset(
                value for value in (entry.extension, entry.compound_extension) if value
            ),
            size_bytes=entry.size_bytes or 0,
            settings=settings,
            is_remote=is_remote,
        )
        | need_digests
    )
    for name in wanted:
        if not IDENTITY_METHODS.available(name):
            continue
        declared_digest = IDENTITY_METHODS.declaration(name).attributes.get(DIGEST_ATTRIBUTE)
        if declared_digest is not None and declared_digest not in digests:
            continue
        if out_of_time(f"identity.{name}"):
            break
        stage_started = stage(f"identity.{name}")
        try:
            method = IDENTITY_METHODS.get(name)
            if not method.supports(context):
                continue
            with captured() as said:
                identity = method.compute(context)
            result.warnings.extend((f"identity.{name}", message) for message in summarise(said))
        except Exception as exc:
            result.stages.append(
                StageTiming(f"identity.{name}", time.monotonic_ns() - stage_started)
            )
            result.errors.append(
                ScanError(
                    entry_id=entry.entry_id,
                    relative_path=entry.relative_path,
                    operation=f"identity.{name}",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    run_id=run_id,
                    failure_class=FailureClass.COMPREHENSION,
                )
            )
            continue
        result.stages.append(
            StageTiming(
                f"identity.{name}",
                time.monotonic_ns() - stage_started,
                identity.bytes_read if identity is not None else None,
            )
        )
        if identity is None:
            continue
        result.bytes_read += identity.bytes_read
        result.observations.append(
            Observation(
                observation_id=observation_id(
                    entry=entry.entry_id,
                    observer=identity.method,
                    observer_version=identity.method_version,
                    key=f"identity.{identity.method}",
                ),
                entry_id=entry.entry_id,
                run_id=run_id,
                observer=identity.method,
                observer_version=identity.method_version,
                observed_at=observed_at,
                key=f"identity.{identity.method}",
                value_kind=ValueKind.TEXT,
                value_text=identity.value,
                # An identity is read from the content itself, whole or sampled,
                # without interpreting the format.
                extraction_tier=ExtractionTier.MECHANICALLY_EXPOSED,
                source_scope=SourceScope.CONTENT,
                identity_method=identity.method,
                identity_method_version=identity.method_version,
                identity_scope=identity.scope,
            )
        )
        # The counts a method read beside its value (an array's dimensions), each its own
        # observation, so a rule's floor on a count reads an integer and not a digest.
        result.observations.extend(
            _reading_observation(
                entry_id=entry.entry_id,
                run_id=run_id,
                observer=identity.method,
                observer_version=identity.method_version,
                observed_at=observed_at,
                key=count_key,
                value=count,
                kind=ValueKind.INTEGER,
                tier=ExtractionTier.MECHANICALLY_EXPOSED,
            )
            for count_key, count in identity.counts
        )

    if not extract:
        # The identity stage alone: a digest a comparison needs, added to a file
        # whose readings stand. Its outcome is the entry's own; nothing else was read.
        result.outcome = ScanOutcome.CONTENT_ERROR if clock_ran_out else entry.scan_outcome
        result.nanoseconds = time.monotonic_ns() - started
        result.seconds = result.nanoseconds / 1e9
        return result

    # -- extraction ------------------------------------------------------
    load_extractor_builtins()
    outcomes: list[ExtractorResult] = []
    #: What the readers that only read a header produced, which is what decides whether a
    #: large file's body is worth reading at all.
    header_outcomes: list[ExtractorResult] = []
    # A worker is its own boundary: inside one, no reader asks for a child.
    supervisor = supervisor_for(settings) if isolated else None
    chosen = extractors or EXTRACTORS.names(available_only=True)
    # A header before a body, on a file big enough for the difference to matter.
    # Ordered by what each extractor declared it reads, so no
    # list of names lives here and an extractor added later takes its place by saying
    # what it does. On a small file this changes the order and nothing else.
    chosen = _header_first(chosen)
    large = settings.extraction.metadata_first and (entry.size_bytes or 0) > (
        settings.extraction.max_sample_bytes
    )
    # A header first at any size, where the bytes name a binary format.
    # A file whose first bytes name a binary format and whose header reader answered has
    # nothing in its body for a string harvest or a text sketch, so the body readers are
    # skipped whatever the file's size. A text file and a file nothing named are read as
    # usual, and the whole policy is still ``metadata_first``.
    signed_binary = settings.extraction.metadata_first and _signature_names_a_binary_format(verdict)
    # An ND2 read for its clock alone, off by default: only the readers
    # that declare a header read, which is where an instrument's clock is.
    clock_only = settings.extraction.nd2_clock_only and (entry.extension or "").casefold() in {
        extension.casefold().removeprefix(".")
        for extension in settings.extraction.clock_only_extensions
    }
    for name in chosen:
        if not EXTRACTORS.available(name):
            continue
        try:
            extractor = EXTRACTORS.get(name)
            if not extractor.supports(context):
                continue
        except Exception as exc:
            result.errors.append(
                ScanError(
                    entry_id=entry.entry_id,
                    relative_path=entry.relative_path,
                    operation=f"extract.{name}",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    run_id=run_id,
                    failure_class=FailureClass.COMPREHENSION,
                )
            )
            continue

        if clock_only and reading_depth_of(extractor) is not ReadingDepth.HEADER:
            if not any(key == "clock_only" for key, _ in result.warnings):
                result.warnings.append(("clock_only", _CLOCK_ONLY))
            continue

        if (
            (large or signed_binary)
            and reading_depth_of(extractor) is ReadingDepth.CONTENT
            and _a_header_reader_answered(header_outcomes)
        ):
            # Recorded once per file and not once per extractor: what a person needs
            # to know is that the body was not read and why, not which readers were
            # skipped. The entry's outcome stays what the header reading made it, because
            # the file was read, as much of it as the question needed.
            if not any(key == "metadata_first" for key, _ in result.warnings):
                result.warnings.append(
                    (
                        "metadata_first",
                        _HEADER_WAS_ENOUGH if large else _HEADER_WAS_ENOUGH_FOR_A_BINARY,
                    )
                )
            continue

        if out_of_time(f"extract.{name}"):
            break
        stage_started = stage(f"extract.{name}")
        produced, errors, behind_boundary = _extract_once(
            name,
            extractor,
            context,
            supervisor=supervisor,
            entry=entry,
            run_id=run_id,
            supervise_above_bytes=settings.extraction.supervise_in_process_above_bytes,
            # The child's own ceiling, and never more than what is left of the file's.
            ceiling_seconds=min(ceiling_for(entry.size_bytes, settings.extraction), seconds_left()),
        )
        result.stages.append(
            StageTiming(
                f"extract.{name}",
                time.monotonic_ns() - stage_started,
                produced.bytes_read if produced is not None else None,
                isolated=behind_boundary,
            )
        )
        result.errors.extend(errors)
        if produced is None:
            continue
        result.warnings.extend((f"extract.{name}", message) for message in produced.warnings)
        outcomes.append(produced)
        if reading_depth_of(extractor) is ReadingDepth.HEADER:
            header_outcomes.append(produced)
        result.bytes_read += produced.bytes_read
        result.observations.extend(
            _extractor_observations(
                produced,
                entry_id=entry.entry_id,
                run_id=run_id,
                observed_at=observed_at,
                roots=roots,
            )
        )

    # The cascade. Members recovered from containers become entries and
    # embedding edges, or observations alone where promotion is switched off.
    streams: list[DerivedStream] = []
    for produced in outcomes:
        streams.extend(produced.derived)
    if streams and not out_of_time("cascade"):
        stage_started = stage("cascade")
        promoted, edges, member_observations = expand_derived(
            context,
            streams,
            entry=entry,
            run_id=run_id,
            observed_at=observed_at,
            settings=settings,
            membership=membership,
        )
        result.derived_entries.extend(promoted)
        result.assertions.extend(edges)
        result.observations.extend(member_observations)

        # Every member re-enters the pipeline. A member that is itself a
        # container is opened again, which is what makes a spreadsheet inside a
        # document inside an archive reachable. Bounded by the expansion depth
        # at every step, so the same recursion cannot be turned against the
        # engine. The depth is the nesting of containers inside containers
        # (``nesting_of``), never how many folders lie above the file.
        if nesting_of(entry) < limits.max_expansion_depth:
            for member in promoted:
                _examine_member(
                    member,
                    container=path,
                    run_id=run_id,
                    observed_at=observed_at,
                    settings=settings,
                    limits=limits,
                    into=result,
                    terms=terms,
                    supervisor=supervisor,
                    membership=membership,
                    roots=roots,
                )
        result.stages.append(StageTiming("cascade", time.monotonic_ns() - stage_started))

    result.outcome = (
        # A file whose clock ran out is a content error for that file alone, whatever a reader
        # managed before it did: the reading is partial in a way the next pass must redo.
        ScanOutcome.CONTENT_ERROR
        if clock_ran_out
        else _combined_outcome(outcomes, had_errors=bool(result.errors))
    )
    chosen_clock = _selected_clock(outcomes, standing=entry)
    if chosen_clock is not None:
        result.event_time_ns, result.event_time_source = chosen_clock
    result.nanoseconds = time.monotonic_ns() - started
    result.seconds = result.nanoseconds / 1e9
    return result


def _selected_clock(
    results: Iterable[ExtractorResult], *, standing: Entry
) -> tuple[int, TimeSource] | None:
    """Return the content clock to put on the entry, or None to leave it alone.

    Content clocks are ranked by copy-robustness, which is what the ladder grades. A PDF's
    creation date is tier two and a date parsed from the filename is tier three,
    so the document clock displaces the filename one. Nothing displaces a clock
    at a better tier than its own, and a filesystem clock never reaches here at
    all because the deepening pass reads content.

    Two extractors that agree at one tier and disagree on the value produce
    nothing, which is abstention and not arbitration. Nothing about the two
    readings distinguishes them, picking the first would depend on registry order,
    and a conflict is its own state and not a value. The
    readings both stay in the observation table, so the conflict is visible to
    anything that goes looking.
    """
    offered = [
        (result.event_time_ns, result.event_time_source)
        for result in results
        if result.event_time_ns is not None and result.event_time_source is not None
    ]
    if not offered:
        return None

    def rank_of(source: TimeSource) -> int:
        properties = CLOCK_TIER_PROPERTIES[CLOCK_TIER_OF[source]]
        # The absence state is not a rung and must not sort as the worst one.
        return properties.rank if properties.rank is not None else 99

    best = min(rank_of(source) for _, source in offered)
    at_best = {(value, source) for value, source in offered if rank_of(source) == best}
    if len({value for value, _ in at_best}) != 1:
        return None
    if best >= rank_of(standing.event_time_source):
        return None
    value, source = next(iter(sorted(at_best)))
    return value, source


def _combined_outcome(results: Iterable[ExtractorResult], *, had_errors: bool) -> ScanOutcome:
    """Return the outcome for an entry from what its extractors reported.

    Success wins over failure. An entry that one extractor read and another
    failed on has been read, and recording it as an error would make the next run
    redo work that succeeded. The individual failure is still recorded as a scan
    error against the entry, so nothing is hidden.
    """
    outcomes = {result.outcome for result in results}
    if ScanOutcome.EXTRACTED in outcomes:
        return ScanOutcome.EXTRACTED
    if ScanOutcome.EXTRACTED_WITH_FALLBACK in outcomes:
        return ScanOutcome.EXTRACTED_WITH_FALLBACK
    if had_errors or ScanOutcome.CONTENT_ERROR in outcomes:
        return ScanOutcome.CONTENT_ERROR
    if ScanOutcome.SKIPPED_BY_POLICY in outcomes:
        return ScanOutcome.SKIPPED_BY_POLICY
    # Detection and identity ran and nothing claimed the format. The file was
    # examined and the honest record is that only metadata came of it.
    return ScanOutcome.METADATA_ONLY


# ---------------------------------------------------------------------------
# Orchestration: a content pass over a published run, producing a new one.
# ---------------------------------------------------------------------------


#: Outcomes that say a file's content was not read, or that its reading was not kept.
UNREAD_OUTCOMES: frozenset[ScanOutcome] = frozenset(
    {
        ScanOutcome.METADATA_ONLY,
        ScanOutcome.DEFERRED,
        ScanOutcome.CONTENT_ERROR,
        # Read again: what was read last time described neither version.
        ScanOutcome.CHANGED_DURING_READ,
        # Considered again: the settings that set it aside may have been lifted.
        ScanOutcome.SKIPPED_BY_SETTINGS,
    }
)
#: Outcomes whose readings stand, and which a pass reads again only for evidence they lack.
READ_OUTCOMES: frozenset[ScanOutcome] = frozenset(
    {ScanOutcome.EXTRACTED, ScanOutcome.EXTRACTED_WITH_FALLBACK}
)


#: The moves a dive checks: those a walk matched without reading the file
#: (``scan.moves.UNCHECKED_MOVES``). A digest's own match is settled; ``digest_differs`` was
#: refuted and is never checked again.
UNREAD_MOVES = UNCHECKED_MOVES

#: The digest a check computes, cheapest first, of those the carried readings hold.
_CHECKED_DIGESTS = ("sampled", "whole")

#: What a check upgrades a confirmed move to, by the digest that confirmed it.
_CONFIRMED_BY = {"sampled": MovedBy.SAMPLED_DIGEST, "whole": MovedBy.WHOLE_DIGEST}


@dataclass(frozen=True, slots=True)
class MoveCheck:
    """One carried move checked by a digest: which digest, and whether it was the same."""

    digest: str
    same: bool


def _check_moves(
    readable: Sequence[Entry],
    held: Mapping[str, Mapping[str, str]],
    *,
    versions: Mapping[tuple[str, str], str],
    enabled: bool,
    check: Callable[[Entry, str], tuple[str, str] | None],
    budget: Budget,
    token: CancellationToken | None,
    stale: set[str] | None = None,
) -> dict[str, MoveCheck]:
    """Each carried move whose digest could be computed, with what the digest said.

    Where the departed file's digests were carried (its readings, rekeyed by the walk), the
    same digests of the file where it is now are computed alone, the cheapest first. A digest
    that differs refutes the move, as soon as it is found. The move is confirmed by the
    strongest digest the readings hold, and only when that one is the same. A sample says
    nothing of the bytes between its ends, so a move confirmed by a sample while a whole-file
    digest was carried would leave that digest standing unchecked as the new file's. A move with
    no carried digest has nothing to be compared with and stays as the walk left it. So does a
    move whose strongest digest was not checked, because the budget did not reach it, the file
    changed while it was read (``check`` answers None) or the method's version changed.

    Only a digest of the same method version is compared (``versions``, per entry and digest
    kind, the carried reading's ``observer_version``). A method's algorithm can change with its
    version, the same bytes then give another value, and a check across versions would undo a
    real move. The function says nothing instead, and a whole-file digest left so is named in
    ``stale``, so the dive reads the file again and its own digest replaces the carried one.

    ``held`` holds only the digests a walk carried: one a dive computed at the file's new path is
    the file's own and there is nothing to compare it with.
    """
    found: dict[str, MoveCheck] = {}
    if not enabled:
        return found
    for entry in readable:
        if entry.entry_kind is not EntryKind.FILESYSTEM:
            continue
        carried = held.get(entry.entry_id, {})
        # A move a sample confirmed, before a whole-file digest was required for it, is checked
        # again by that digest, once: a confirmed whole-file digest upgrades it.
        sampled_only = entry.moved_by is MovedBy.SAMPLED_DIGEST and bool(carried.get("whole"))
        if entry.moved_by not in UNREAD_MOVES and not sampled_only:
            continue
        # A move a sample already confirmed is not sampled again: only its whole digest is new.
        kinds = [
            name
            for name in _CHECKED_DIGESTS
            if carried.get(name) and not (sampled_only and name == "sampled")
        ]
        if not kinds:
            continue
        agreed: set[str] = set()
        other_version: set[str] = set()
        for kind in kinds:
            if (token is not None and token.cancelled) or budget.exhausted:
                return found
            now = check(entry, kind)
            if now is None:
                continue
            value, version = now
            if versions.get((entry.entry_id, kind)) != version:
                other_version.add(kind)
                continue
            if value != carried[kind]:
                found[entry.entry_id] = MoveCheck(digest=kind, same=False)
                break
            agreed.add(kind)
        else:
            if kinds[-1] in agreed:
                found[entry.entry_id] = MoveCheck(digest=kinds[-1], same=True)
            elif "whole" in other_version and stale is not None:
                stale.add(entry.entry_id)
    return found


def _digest_now(
    entry: Entry,
    kind: str,
    *,
    roots: Mapping[str, Any],
    run_id: str,
    started_at: str,
    settings: Settings,
    pool: Sequence[PoolRoot],
    digest_of_method: Mapping[str, str],
    budget: Budget,
    report: DeepenReport,
) -> tuple[str, str] | None:
    """One digest of one file, computed alone and written nowhere, with its method's version: the
    check's own reading.

    Not written because the carried reading holds the same identifier: where the digest is the
    same the carried one stands, and where it differs the file is read afresh, digest and all.
    """
    from pathlib import Path as _Path

    declaration = roots.get(entry.root_id)
    method = next((name for name, digest in digest_of_method.items() if digest == kind), None)
    if declaration is None or method is None:
        return None
    outcome = deepen_entry(
        entry,
        _Path(declaration.mount_path) / entry.relative_path,
        run_id=run_id,
        observed_at=started_at,
        settings=settings,
        identity_methods=(method,),
        need_digests=frozenset({kind}),
        extract=False,
        roots=pool,
    )
    budget.spend(outcome.seconds)
    report.bytes_read += outcome.bytes_read
    if outcome.outcome is ScanOutcome.CHANGED_DURING_READ:
        report.changed_during_read += 1
        return None
    return next(
        (
            (observation.value_text, observation.observer_version)
            for observation in outcome.observations
            if digest_of_method.get(observation.identity_method or "") == kind
            and observation.value_text
        ),
        None,
    )


def _after_check(
    entry: Entry, verdict: MoveCheck | None, stale: Collection[str] = frozenset()
) -> Entry:
    """An entry as a move check leaves it: upgraded, or undone and unread, or (``stale``) left
    as the walk matched it but unread, so that a dive reads it again."""
    if verdict is None:
        if entry.entry_id in stale:
            return entry.model_copy(
                update={"scan_outcome": ScanOutcome.METADATA_ONLY, "extraction_profile": None}
            )
        return entry
    if verdict.same:
        # A copy's bytes confirmed: still a copy, whichever digest said so.
        confirmed = (
            MovedBy.COPIED_SAME_BYTES
            if entry.moved_by is MovedBy.COPIED
            else _CONFIRMED_BY[verdict.digest]
        )
        return entry.model_copy(update={"moved_by": confirmed})
    undone: dict[str, object] = {
        "moved_by": MovedBy.DIGEST_DIFFERS,
        "scan_outcome": ScanOutcome.METADATA_ONLY,
        "extraction_profile": None,
    }
    # A content clock the walk carried was the other file's; the name's own returns at a walk.
    if entry.event_time_source in (TimeSource.INSTRUMENT, TimeSource.DOCUMENT):
        undone.update(
            event_time_ns=None,
            event_time_source=TimeSource.UNAVAILABLE,
            event_time_tier=ClockTier.UNAVAILABLE,
        )
    return entry.model_copy(update=undone)


def nesting_of(entry: Entry) -> int:
    """How many containers lie between a file and the bytes of this entry: 0 for a file on the
    filesystem, 1 for a member of a file, 2 for a member of a member.

    The expansion limit is held to this count. An archive inside an archive inside an archive
    is what the limit stops, and the locator chain has one step per container opened, and
    nothing else. Counting the entry's ``depth`` instead, the number of folders above it,
    would leave a table in an archive lying four folders down never examined.
    """
    return len(entry.locator_chain)


def read_in_part(entry: Entry, *, rank: int, held_whole: bool = False) -> bool:
    """Whether a deeper pass would read more of this file than its reading did.

    A file no larger than the sample its depth read was read whole, and a deeper pass has
    nothing to add to it. A larger one was read in part, and is read again. A reading from
    before schema 1.22 states no depth. With a whole digest it was a deep dive's, because before
    1.22 the depth's preset chose the digest and only deep's took whole ones. Otherwise it is
    taken as ``standard``, the depth every dive ran at unless a person said otherwise.
    """
    read_at = entry.extraction_profile or (
        ProfileName.DEEP.value if held_whole else ProfileName.STANDARD.value
    )
    if DEPTH_RANK.get(read_at, DEPTH_RANK[ProfileName.STANDARD.value]) >= rank:
        return False
    try:
        sample = PROFILES[ProfileName(read_at)].max_sample_bytes
    except ValueError:
        sample = PROFILES[ProfileName.STANDARD].max_sample_bytes
    return (entry.size_bytes or 0) > sample


def read_deeper_than(entry: Entry, *, rank: int, held_whole: bool = False) -> bool:
    """Whether the reading this file keeps was made at a deeper depth than a pass of ``rank``.

    A pass never reads again at a depth that would find less. A file read for
    a newer reader at a shallower depth would lose rows a deeper dive had counted, and a whole
    digest it held. Such a file waits for a pass at its depth or deeper. The depth is taken as
    :func:`read_in_part` takes it, including for a reading from before 1.22 that states none.
    """
    read_at = entry.extraction_profile or (
        ProfileName.DEEP.value if held_whole else ProfileName.STANDARD.value
    )
    return DEPTH_RANK.get(read_at, DEPTH_RANK[ProfileName.STANDARD.value]) > rank


def readers_today(settings: Settings) -> dict[str, str]:
    """The readers a dive may run, each with the version a reading made now would record.

    Two kinds, both recorded as an observation's ``observer``. The extractors the settings
    permit, and the identity methods that read a file's content and sign what they find (a
    table's columns, an array's shape): not the byte digests, which a move check and the mode in
    force already govern. The version a reading records is the reader's own
    (``Extractor.version``, written into every observation it makes), so that is the one a kept
    reading is compared with. The registry's declared version is for the processing
    fingerprint, and a test holds the two equal. A reader the settings do not permit, or that
    cannot run here, reads nothing today and is not in the answer, so a file only it read is
    never read again for its sake.
    """
    load_extractor_builtins()
    load_identity_builtins()
    today: dict[str, str] = {}
    for name in settings.extraction.adapters or EXTRACTORS.names(available_only=True):
        if not EXTRACTORS.available(name):
            continue
        try:
            today[name] = EXTRACTORS.get(name).version
        except Exception:  # noqa: S112 -- a reader that cannot be built reads nothing today
            continue
    today.update(signing_methods_today())
    return today


def signing_methods_today() -> dict[str, str]:
    """The identity methods that sign a file's content, each with the version it records now.

    A method naming the extensions it reads signs content (a table's columns, an array's shape);
    a byte digest or a signature of names does not, and is not a reader in this sense.
    """
    load_identity_builtins()
    found: dict[str, str] = {}
    for name in IDENTITY_METHODS.names(available_only=True):
        attributes = IDENTITY_METHODS.declaration(name).attributes
        if DIGEST_ATTRIBUTE in attributes or "extensions" not in attributes:
            continue
        try:
            found[name] = IDENTITY_METHODS.get(name).version
        except Exception:  # noqa: S112 -- a method that cannot be built signs nothing today
            continue
    return found


#: The (entry, reader) pairs holding no reading at the reader's version today, and the versions
#: they do hold. The query filters first and then groups: the pairs with a version
#: other than today's are found by one pass over three columns, and only their readings are
#: grouped, so a project already read by today's readers costs a scan and no memory to speak of.
#: A version tagged after today's (the text sketch's ``1+262144``) is today's, so it never passes.
_OLDER_READINGS = (
    "WITH today AS (SELECT unnest(?::VARCHAR[]) AS observer, unnest(?::VARCHAR[]) AS version), "
    "other AS (SELECT DISTINCT o.entry_id, o.observer FROM observations AS o "
    "  JOIN today ON o.observer = today.observer WHERE o.observer_version <> today.version "
    "  AND NOT starts_with(o.observer_version, today.version || '+')) "
    "SELECT o.entry_id, o.observer, list(DISTINCT o.observer_version) AS versions "
    "FROM observations AS o JOIN other ON o.entry_id = other.entry_id "
    "AND o.observer = other.observer JOIN today ON o.observer = today.observer "
    "GROUP BY o.entry_id, o.observer, today.version "
    "HAVING NOT bool_or(o.observer_version = today.version)"
)


def older_reader_readings(catalog: Catalog, today: Mapping[str, str]) -> dict[str, set[str]]:
    """Per entry, each reader whose every kept reading of it is older than the reader today.

    The stored readings narrow the comparison: a reader has a reading of an entry only where
    it read that entry, by its extension or by its content, so a change to one reader chooses
    only the files that reader read and never the whole project. A reading made by a newer
    version than today's is not older and is left alone.
    """
    if not today:
        return {}
    names = sorted(today)
    found: dict[str, set[str]] = {}
    #: A handful of distinct versions meet a handful of readers, so each pair is judged once.
    judged: dict[tuple[str, str], bool] = {}
    for rows in catalog.batches(_OLDER_READINGS, [names, [today[name] for name in names]]):
        for row in rows:
            observer = row["observer"]
            older = True
            for version in row["versions"]:
                key = (observer, version or "")
                if key not in judged:
                    judged[key] = reader_version_is_older(key[1], today[observer])
                if not judged[key]:
                    older = False
                    break
            if older:
                found.setdefault(row["entry_id"], set()).add(observer)
    return found


def files_read_by_older_readers(
    entries: Iterable[Entry], older: Mapping[str, Collection[str]]
) -> dict[str, set[str]]:
    """The files on disk whose kept reading came from an older version of a reader.

    A member of a container chooses the file it lies in, because a container is read again
    whole and its members with it. Each file is mapped to the readers whose older readings
    chose it.
    """
    if not older:
        return {}
    by_id = {entry.entry_id: entry for entry in entries}
    found: dict[str, set[str]] = {}
    for held_id, observers in older.items():
        holder = by_id.get(held_id)
        if holder is None or holder.is_deleted:
            continue
        file, seen = holder, {holder.entry_id}
        while file.entry_kind is EntryKind.DERIVED and file.parent_entry_id in by_id:
            if file.parent_entry_id in seen:
                break
            seen.add(file.parent_entry_id)
            file = by_id[file.parent_entry_id]
        if file.entry_kind is EntryKind.FILESYSTEM:
            found.setdefault(file.entry_id, set()).update(observers)
    return found


def signature_only_files(
    entries: Iterable[Entry], older: Mapping[str, Collection[str]], signing: Collection[str]
) -> dict[str, tuple[str, ...]]:
    """The files whose every stale reading is a signature of their own content.

    Such a file needs its signatures made again and nothing else read: each is mapped to the
    methods to run. A file with any other stale reader is read whole, as is a file whose
    stale reading lies in a member of a container (a container is read again with its members,
    and a member has no path to sign), so both stay out.
    """
    if not older:
        return {}
    by_id = {entry.entry_id: entry for entry in entries}
    found: dict[str, set[str]] = {}
    whole: set[str] = set()
    for held_id, observers in older.items():
        holder = by_id.get(held_id)
        if holder is None or holder.is_deleted:
            continue
        file, seen = holder, {holder.entry_id}
        while file.entry_kind is EntryKind.DERIVED and file.parent_entry_id in by_id:
            if file.parent_entry_id in seen:
                break
            seen.add(file.parent_entry_id)
            file = by_id[file.parent_entry_id]
        if file.entry_kind is not EntryKind.FILESYSTEM:
            continue
        if file is not holder or any(observer not in signing for observer in observers):
            whole.add(file.entry_id)
            continue
        found.setdefault(file.entry_id, set()).update(observers)
    return {file: tuple(sorted(found[file])) for file in sorted(found) if file not in whole}


def _shared_sizes(entries: Iterable[Entry]) -> frozenset[int]:
    """The sizes more than one file of the pool has: only such files can be byte-identical."""
    seen: dict[int, int] = {}
    for entry in entries:
        size = entry.size_bytes or 0
        if entry.entry_kind is EntryKind.FILESYSTEM and size > 0:
            seen[size] = seen.get(size, 0) + 1
    return frozenset(size for size, count in seen.items() if count > 1)


def run_deepen(
    project: object,
    *,
    run_id: str,
    started_at: str,
    profile: str = "standard",
    budget_seconds: float | None = None,
    token: CancellationToken | None = None,
    reporter: object = None,
    rulebook: EffectiveRulebook | None = None,
    filesystem_type: Callable[[Path], str | None] | None = None,
    announce: Callable[[int, float | None], None] | None = None,
    total_seconds: float | None = None,
    spend_order_override: SpendOrder | None = None,
) -> DeepenReport:
    """Open files the last run did not, and publish the result as a new run.

    Reads the current run, examines the entries that need it, and writes a new
    complete run carrying everything forward plus what was learned. Tables this
    pass does not touch are copied verbatim rather than rebuilt, which is faster
    and keeps them byte-identical, so a determinism comparison across a deepening
    still means what it says.

    The pass is resumable by construction. Each run records what it examined, so
    a second pass with a fresh budget picks up the entries the first did not
    reach, and neither pass has to know about the other.

    ``announce`` is called once, before the first file is opened, with how many files the pass will
    try and the seconds granted (None: no limit), so a terminal can say the dive is working.

    ``spend_order_override`` is the order to spend in, over the settings'. A
    first run's content step reads series first whatever the settings file says.

    ``total_seconds`` is a ceiling on the wall clock for the whole pass, beside
    the work granted. When given, it beats the settings' ``extraction.total_seconds`` and the
    depth's preset, as ``budget_seconds`` beats the depth's work grant. A caller that names the
    wall clock and no work grant has named the pass's budget. The depth's work grant is not
    applied on top of it, so a pool of N workers reads up to N times as much in the same
    minutes, which is what a wall clock is for.

    ``rulebook`` is the Rulebook whose cascade rule the pass cites, and ``None`` reads
    the shipped one. It is also the Rulebook whose deferral the order follows: a dive reads
    the files beneath a folder it defers beneath last, or never, as
    ``beneath_programs`` says, and ``None`` reads the project's own Rulebook for that, since a
    deferral is a project revision's.

    ``filesystem_type`` names the filesystem a root lies on (``scan.shares.filesystem_type`` by
    default; a test injects one). A root on a network share is not hashed where
    ``hash.skip_remote_filesystems`` says skip, asked once for each root. A
    depth's proposal withholds only what the modes that hash every file ask, never ``need``'s
    digests. A person's ``always`` withholds every digest on a share. A ``true`` leaves the
    choice to the depth, because older settings files hold it.
    """

    from ingence import __version__
    from ingence.core.run_records import build_run_record
    from ingence.core.schema import RunRecord
    from ingence.rulebook import shipped_rulebook
    from ingence.storage.carry import carry_run
    from ingence.storage.catalog import census_facts, open_catalog
    from ingence.storage.commit import (
        CommitError,
        build_manifest,
        commit_run,
        resolve_current_run,
        staging_path,
    )
    from ingence.storage.compact import compact_run, compact_table
    from ingence.storage.index_store import FragmentWriter
    from ingence.storage.journal import open_journal
    from ingence.storage.layout import TABLES, owned_path, table_for

    # The cascade's rule, bound once for the whole pass and before anything is staged.
    # Every embedding edge the pass draws cites it.
    book = shipped_rulebook() if rulebook is None else rulebook
    membership = scan_rules((CASCADE_MEMBERSHIP,), book)[CASCADE_MEMBERSHIP]

    # A token of the pass's own where the caller gave none: a root that
    # stops answering asks it to stop and keep, as a person's Stop does.
    if token is None:
        from ingence.scan.cancel import CancellationToken as _Token

        token = _Token()
    settings = apply_profile(project.settings, ProfileName(profile))  # type: ignore[attr-defined]
    if spend_order_override is not None:
        settings = settings.model_copy(
            update={
                "extraction": settings.extraction.model_copy(
                    update={"spend_order": spend_order_override}
                )
            }
        )
    # Precedence, stated once: an explicit grant beats the profile's preset,
    # which beats whatever the settings file holds. An option that is silently
    # overridden by a default is worse than an option that does not exist.
    granted = (
        budget_seconds
        if budget_seconds is not None
        else settings.extraction.deepening_budget_seconds
        if total_seconds is None
        else None
    )
    wall = total_seconds if total_seconds is not None else settings.extraction.total_seconds
    budget = Budget(granted_seconds=granted, wall_seconds=wall)
    report = DeepenReport(budget=budget)

    previous = resolve_current_run(project.path)  # type: ignore[attr-defined]
    if previous is None:
        message = "no run has been published; index before deepening"
        raise CommitError(message)

    # A dive opens files, and refuses before it stages anything where a root cannot be
    # reached: with the root absent it would record every file it tried as a failed
    # reading, and a file out of reach is unexamined, not unreadable.
    refuse_unreachable_roots(
        project.identity.roots,  # type: ignore[attr-defined]
        verb="A dive opens files under every declared root",
    )

    # The project's own staging, nothing on the way to it a link (``owned_path``).
    staging = owned_path(project.path, staging_path(project.path, run_id))  # type: ignore[attr-defined]
    staging.mkdir(parents=True, exist_ok=False)
    journal = open_journal(staging / "journal", run_id, enabled=settings.journal.enabled)

    # Carry forward everything this pass does not rewrite. Copying rather than
    # rebuilding keeps those tables byte-identical, so a run comparison across a
    # deepening reports only what actually changed.
    rewritten = {
        "entries",
        "observations",
        "run",
        "journal_events",
        "scan_errors",
        # Rewritten because the cascade adds embedding edges. The census's
        # containment edges are carried through unchanged rather than recomputed.
        "assertions",
        # Written afresh for every run, from the tables it holds.
        "quality_figures",
    }
    carry_run(previous, staging, rewritten=rewritten)

    entries_writer = FragmentWriter(table_for(Entry), staging / "entries")
    observations_writer = FragmentWriter(table_for(Observation), staging / "observations")
    errors_writer = FragmentWriter(table_for(ScanError), staging / "scan_errors")
    assertions_writer = FragmentWriter(table_for(Assertion), staging / "assertions")

    roots = {
        declaration.root_id: declaration
        for declaration in project.identity.roots  # type: ignore[attr-defined]
    }
    # The pool's roots, as a mention is placed against them.
    pool = pool_roots(project.identity.roots)  # type: ignore[attr-defined]

    # Read in pieces, never whole: the entries are needed as
    # records and are built a batch at a time; the digests the choice of files reads are read
    # alone; every other carried table is streamed into the new run after the examination.
    digest_of_method = digest_methods()
    #: Per entry, the digests its carried readings hold, by kind and value.
    held: dict[str, dict[str, str]] = {}
    #: Per entry and digest kind, the version of the method that computed the carried digest: a
    #: move check compares only digests of one version.
    held_versions: dict[tuple[str, str], str] = {}
    #: The same, of the digests a walk carried across a move: those recorded under the run that
    #: first saw the entry, which is the walk that matched it. A digest a dive
    #: computed at the new path is not one, and a move check has nothing to compare it with.
    carried: dict[str, dict[str, str]] = {}
    carried_versions: dict[tuple[str, str], str] = {}
    with open_catalog(project.path, run_id=previous.name) as catalog:  # type: ignore[attr-defined]
        entries = [
            _entry_from_row(row)
            for rows in catalog.batches("SELECT * FROM entries ORDER BY entry_id")
            for row in rows
        ]
        first_seen = {entry.entry_id: entry.first_seen_run for entry in entries}
        # Each file's series, as the file list recorded it.
        series_of: dict[str, str] = {}
        if settings.extraction.spend_order is SpendOrder.SERIES_FIRST:
            for rows in catalog.batches(
                "SELECT entry_id, value_text FROM observations WHERE key = ? "
                "ORDER BY observation_id",
                ["series.key"],
            ):
                for row in rows:
                    series_of[str(row["entry_id"])] = str(row["value_text"] or "")
        for rows in catalog.batches(
            "SELECT entry_id, identity_method, value_text, observer_version, run_id "
            "FROM observations "
            "WHERE identity_method IN (SELECT unnest(?::VARCHAR[])) ORDER BY observation_id",
            [sorted(digest_of_method)],
        ):
            for row in rows:
                kind = digest_of_method[str(row["identity_method"])]
                value, version = (
                    str(row.get("value_text") or ""),
                    str(row["observer_version"] or ""),
                )
                held.setdefault(row["entry_id"], {})[kind] = value
                held_versions[(row["entry_id"], kind)] = version
                if row.get("run_id") == first_seen.get(row["entry_id"]):
                    carried.setdefault(row["entry_id"], {})[kind] = value
                    carried_versions[(row["entry_id"], kind)] = version
        # What the census left out and recognised, carried forward.
        left_out, recognised = census_facts(catalog)
        # The readers whose kept readings are older than they are today: asked
        # of the readers this pass may run, and answering only for entries holding no reading
        # at today's version.
        reader_versions = readers_today(settings)
        older_readings = older_reader_readings(catalog, reader_versions)
    older_files = files_read_by_older_readers(entries, older_readings)
    # A file whose only stale readings are signatures has those made again and nothing else
    # read: the extractors that read its content would produce what they produced.
    signing_today = signing_methods_today()
    signed_alone = signature_only_files(
        entries, older_readings, set(signing_today) - set(EXTRACTORS.names())
    )
    del older_readings

    # Carried records are written after the examination loop, not before it.
    # What this pass re-reads replaces what the last pass recorded about
    # it, and which entries it re-reads is known only once the loop has run,
    # because a budget or a cancellation can stop it anywhere. If the carried records were
    # written first, an entry re-read after a failure would have its old readings and its new
    # ones under the same identifiers, and a run would carry nine duplicate rows per such file
    # into a table that declares ``observation_id`` its primary key.
    all_edges: list[Assertion] = []
    reread: set[str] = set()

    # A listing's files are not on this machine: their bytes are read only by a
    # listing's own remote stages, so a dive leaves them as the listing stated them, unexamined.
    listed_roots = {
        declaration.root_id
        for declaration in project.identity.roots  # type: ignore[attr-defined]
        if is_listing_root(declaration)
    }
    readable = [
        entry
        for entry in entries
        if entry.entry_type.value == "file"
        and not entry.is_deleted
        and entry.root_id not in listed_roots
    ]

    # The digest, and who chose it: a mode a person set stands at every
    # depth; ``need`` digests what comparing the pool's files needs.
    choice = hash_choice(project.settings, ProfileName(profile))  # type: ignore[attr-defined]
    report.hash_mode, report.hash_mode_set_by = choice.recorded, choice.set_by
    remote = remote_choice(project.settings, ProfileName(profile))  # type: ignore[attr-defined]
    report.skip_remote_filesystems = remote.skip
    report.skip_remote_filesystems_set_by = remote.set_by
    # A share is not hashed where the setting says skip: each root's filesystem
    # named once, and only where there is something to decide. A depth's proposal to skip
    # withholds only what the modes that hash every file ask. The digests that ``need``
    # asks for are withheld only by a person's own ``always``: hash what comparing and
    # deduplicating need, whatever the depth.
    on_share: dict[str, str] = {}
    need_withheld = remote.need_too
    mode_withheld = remote.skip and not choice.by_need
    if need_withheld:
        report.unhashed_on_shares_because = SHARE_SKIPPED_BY_SETTINGS
    elif mode_withheld:
        report.unhashed_on_shares_because = SHARE_SKIPPED_BY_DEPTH
    from pathlib import Path as _Path

    from ingence.scan.shares import is_share, name_roots

    # Each root's filesystem, named once: for the hash decision below where the setting skips a
    # share, and for the size of the read pool, which is capped on a share.
    named = name_roots(
        {
            root_id: _Path(declaration.mount_path)
            for root_id, declaration in roots.items()
            if root_id not in listed_roots
        },
        detector=filesystem_type,
    )
    any_root_on_a_share = any(is_share(filesystem) for filesystem in named.values())
    # Each root's device and filesystem at the start: a root that
    # answers again on another device or filesystem was remounted, not returned.
    root_marks: dict[str, tuple[int | None, str | None]] = {}
    for root_id, declaration in roots.items():
        if root_id in listed_roots:
            continue
        try:
            device: int | None = os.stat(declaration.mount_path).st_dev
        except OSError:
            device = None
        root_marks[root_id] = (device, named.get(root_id))

    def root_lost(root_id: str) -> str | None:
        """Why the root cannot be reached now, or None where it answers as it did at the start."""
        from ingence.core.project import unreachable_roots

        declaration = roots.get(root_id)
        if declaration is None:
            return None
        gone = unreachable_roots([declaration])
        if gone:
            return gone[0][1]
        device_then, filesystem_then = root_marks.get(root_id, (None, None))
        try:
            device_now: int | None = os.stat(declaration.mount_path).st_dev
        except OSError as exc:
            return f"it cannot be read: {exc.strerror or exc}"
        if device_then is not None and device_now != device_then:
            return "it answers from another device: remounted, not returned"
        filesystem_now = name_roots(
            {root_id: _Path(declaration.mount_path)}, detector=filesystem_type
        ).get(root_id)
        if filesystem_then is not None and filesystem_now != filesystem_then:
            return f"it answers from another filesystem ({filesystem_now}, was {filesystem_then})"
        return None

    if remote.skip:
        for root_id, filesystem in named.items():
            if filesystem is None:
                report.roots_unnamed += 1
            elif is_share(filesystem):
                on_share[root_id] = filesystem
                report.roots_on_shares[filesystem] = report.roots_on_shares.get(filesystem, 0) + 1
        unnamed = sorted(root for root, filesystem in named.items() if filesystem is None)
        if unnamed and remote.set_by == "settings":
            report.unnamed_said = (
                f"{len(unnamed):,} {'root' if len(unnamed) == 1 else 'roots'} could not be named "
                "a network share or a local disk, so "
                f"{'it was' if len(unnamed) == 1 else 'they were'} read as local and hashed; "
                "hash.skip_remote_filesystems: always was not applied there"
            )
        journal.record(
            "deepen",
            "hash.shares",
            run_id=run_id,
            unnamed=unnamed,
            unnamed_said=report.unnamed_said,
            skip_remote_filesystems=remote.skip,
            set_by=remote.set_by,
            filesystems={root: filesystem or "" for root, filesystem in sorted(named.items())},
            left_unhashed=sorted(on_share) if need_withheld or mode_withheld else [],
            because=report.unhashed_on_shares_because or SHARE_HASHED_BY_NEED,
        )

    def on_a_share(entry: Entry) -> bool:
        """Whether this file lies on a root found on a network share (asked where skip)."""
        return entry.root_id in on_share

    def unhashed(entry: Entry) -> bool:
        """Whether even ``need``'s digests are withheld from this file: a person said skip."""
        return need_withheld and entry.root_id in on_share

    # An older file's none, written as need once, and said.
    from ingence.core.settings import (
        HASH_MODE_UPGRADED,
        SETTINGS_FILE,
        upgrade_settings_file,
    )

    report.settings_upgraded = upgrade_settings_file(
        project.path / SETTINGS_FILE  # type: ignore[attr-defined]
    )
    shared = (
        _shared_sizes(entry for entry in readable if not unhashed(entry))
        if choice.by_need
        else frozenset()
    )

    def suffixes(entry: Entry) -> frozenset[str]:
        return frozenset(value for value in (entry.extension, entry.compound_extension) if value)

    # A dive confirms a move by its digest, or undoes it. A walk matched these
    # files without reading them and carried what a dive read of the file each came from; the
    # digest it carried is checked against the file's own, before anything is chosen to read.
    stale_digests: set[str] = set()
    checked = _check_moves(
        readable,
        carried,
        versions=carried_versions,
        stale=stale_digests,
        enabled=settings.scan.moves_confirmed_by_dive and choice.mode is not HashMode.NONE,
        check=lambda entry, kind: _digest_now(
            entry,
            kind,
            roots=roots,
            run_id=run_id,
            started_at=started_at,
            settings=settings,
            pool=pool,
            digest_of_method=digest_of_method,
            budget=budget,
            report=report,
        ),
        budget=budget,
        token=token,
    )
    by_identifier = {entry.entry_id: entry for entry in readable if entry.entry_id in checked}
    for checked_id, verdict in checked.items():
        before = by_identifier[checked_id]
        if verdict.same:
            report.moves_confirmed += 1
        else:
            # Its carried readings are dropped with it (``reread``), and it is read as new.
            report.moves_undone += 1
            held.pop(checked_id, None)
            reread.add(checked_id)
        journal.record(
            "deepen",
            "move.confirmed" if verdict.same else "move.undone",
            entry_id=checked_id,
            relative_path=before.relative_path,
            moved_from=before.moved_from,
            digest=verdict.digest,
        )
    # A carried whole-file digest of another method version is read again, with the rest:
    # it cannot be compared with the file's own, and left in place it would be
    # refused as the departed file's for good. The file is read as unread; what it is read to be
    # replaces the carried readings, and its own whole-file digest at the new path counts.
    for stale_id in sorted(stale_digests - set(checked)):
        held.pop(stale_id, None)
        journal.record("deepen", "move.digest_stale", entry_id=stale_id)
    if checked or stale_digests:
        entries = [
            _after_check(entry, checked.get(entry.entry_id), stale_digests) for entry in entries
        ]
        readable = [
            _after_check(entry, checked.get(entry.entry_id), stale_digests) for entry in readable
        ]

    def need_of(entry: Entry) -> frozenset[str]:
        """The sample a comparison needs of one file: only where another file shares its size."""
        size = entry.size_bytes or 0
        hashing = settings.hash
        if (
            not choice.by_need
            or entry.entry_kind is not EntryKind.FILESYSTEM
            or unhashed(entry)
            or size not in shared
            or not policy_selects(
                extensions=suffixes(entry),
                size_bytes=size,
                include_extensions=hashing.include_extensions,
                exclude_extensions=hashing.exclude_extensions,
                max_file_bytes=hashing.max_file_bytes,
            )
        ):
            return frozenset()
        return frozenset({"sampled"})

    rank = DEPTH_RANK.get(profile, DEPTH_RANK[ProfileName.STANDARD.value])

    def reason_to_read(entry: Entry) -> str | None:
        """Why a pass at this depth, under this digest, reads one file; None where it need not.

        The reason depends on the evidence the pass asks for, and not only on how the last
        reading ended. A file read at a shallower depth is read again where that reading was
        partial. A file whose reading came from an older version of a reader that claims its
        type is read again. A file lacking a digest the mode in force asks for is read again.
        """
        if entry.scan_outcome in UNREAD_OUTCOMES:
            return "unread"
        if entry.entry_kind is not EntryKind.FILESYSTEM or entry.scan_outcome not in READ_OUTCOMES:
            return None
        held_whole = "whole" in held.get(entry.entry_id, {})
        if read_in_part(entry, rank=rank, held_whole=held_whole):
            return "deeper"
        # A reader that changed reads its files again, once: the kept reading
        # came from an older version of a reader that claims this file's type. Never at a depth
        # shallower than the reading it replaces, which would find less.
        if entry.entry_id in older_files and not read_deeper_than(
            entry, rank=rank, held_whole=held_whole
        ):
            return "reader_newer"
        if missing_digests(entry):
            return "digest"
        return None

    def missing_digests(entry: Entry) -> frozenset[str]:
        """The digests the mode in force asks of a file and its readings do not hold.

        Under need, a whole digest answers what a sample asks: a proof
        of what the file is tells it apart from any other at least as well as a nomination.
        """
        have = set(held.get(entry.entry_id, {}))
        if choice.by_need and "whole" in have:
            have.add("sampled")
        asked = digests_wanted(
            extensions=suffixes(entry),
            size_bytes=entry.size_bytes or 0,
            settings=settings,
            is_remote=on_a_share(entry),
        ) | need_of(entry)
        return frozenset(asked - have)

    def settings_refusal(entry: Entry) -> str | None:
        """Why a person's extraction settings leave this file unread, or None.

        A person's settings are honoured, and a person's answers order the spend. Including or
        excluding an extension, or limiting the size of what is read, is a deliberate
        configuration, written into the project's settings, so a dive does not open a file they
        leave out: it records the file as set aside by the settings with its reason, apart from
        content that cannot be read. A ``no`` or ``never`` answered on the Coverage page is a
        review verdict about a kind of file, which orders what a budget reads first and vetoes
        nothing. An answer given in one month must not stop the engine getting evidence
        in the next, where a setting says exactly what it says until a person changes it.
        """
        extraction = settings.extraction
        return policy_refusal(
            extensions=suffixes(entry),
            size_bytes=entry.size_bytes or 0,
            include_extensions=extraction.include_extensions,
            exclude_extensions=extraction.exclude_extensions,
            max_file_bytes=extraction.max_file_bytes,
        )

    # A file lacking only a digest is not read again: its digest is
    # computed alone after the examination, and every reading it holds, at whatever depth, stands.
    # A file the settings leave out is not read at all. One a pass would read
    # again deeper keeps what an earlier pass read of it, and only a digest the hash settings ask
    # for is still taken (their own filters govern that).
    candidates: list[Entry] = []
    digest_only: list[Entry] = []
    set_aside: list[tuple[Entry, str]] = []
    chosen_for_reader: set[str] = set()
    #: The files chosen for a stale signature alone, each with the methods to run.
    sign_only: dict[str, tuple[str, ...]] = {}
    for entry in readable:
        reason = reason_to_read(entry)
        if reason is None:
            continue
        refused = None if reason == "digest" else settings_refusal(entry)
        if refused is not None:
            set_aside.append((entry, refused))
            report.set_aside_by_settings[refused] = report.set_aside_by_settings.get(refused, 0) + 1
            # The hash filters govern digests, not the extraction filters: a file set aside
            # unread still takes the digests its mode or need asks.
            if missing_digests(entry):
                digest_only.append(entry)
            continue
        report.chosen_because[reason] = report.chosen_because.get(reason, 0) + 1
        if reason == "reader_newer":
            chosen_for_reader.add(entry.entry_id)
            for name in older_files[entry.entry_id]:
                report.reader_newer_by[name] = report.reader_newer_by.get(name, 0) + 1
            if entry.entry_id in signed_alone:
                sign_only[entry.entry_id] = signed_alone[entry.entry_id]
                # Its readings stand, so a digest the mode asks for is added alone, as ever.
                if missing_digests(entry):
                    digest_only.append(entry)
        (digest_only if reason == "digest" else candidates).append(entry)
    from ingence.dictionary.store import DictionaryStore

    store = DictionaryStore.builtin() if settings.extraction.byte_scan_sections else None
    terms = scan_terms_for(settings, store)
    journal.record(
        "deepen",
        "run.started",
        run_id=run_id,
        profile=profile,
        candidates=len(candidates),
        budget_seconds=budget.granted_seconds,
        total_seconds=budget.wall_seconds,
        scan_terms=len(terms),
        chosen_because=dict(sorted(report.chosen_because.items())),
        reader_newer_by=dict(sorted(report.reader_newer_by.items())),
        set_aside_by_settings=dict(sorted(report.set_aside_by_settings.items())),
        hash_mode=choice.recorded,
        hash_mode_set_by=choice.set_by,
    )

    examined: dict[str, ScanOutcome] = {}
    #: Consecutive access failures, each the root, the folder and the entry it was against.
    #: Any file that was reached clears them.
    access_run: list[tuple[str, str, str]] = []
    #: Per entry this pass read, the digests it computed, by kind and value.
    fresh: dict[str, dict[str, str]] = {}
    clocks: dict[str, tuple[int, TimeSource]] = {}
    cancelled = False

    # Stop and keep, or discard. A token asked to stop and keep
    # ends the pass the way an exhausted budget does: the file in hand finishes, every file after
    # it is counted as not reached, and the run is published with honest gaps. A token asked to
    # discard ends it the way it always has: nothing is published.
    def asked() -> str | None:
        """``stop``, ``discard`` or None, reading the token once per checkpoint."""
        if token is None or not token.cancelled:
            return None
        return "stop" if token.keeps else "discard"

    def stop_asked() -> bool:
        return asked() == "stop"

    def discard_asked() -> bool:
        return asked() == "discard"

    # Both journal settings do what they say. ``per_entry`` decides whether an examined
    # entry gets a row of its own. ``timings`` decides whether that row carries each stage's
    # duration, and whether the run's per-stage totals and its publishing phases are
    # recorded at all. The census examines nothing and writes no per-entry row
    # either way.
    per_entry = settings.journal.per_entry
    timings = settings.journal.timings
    # Each file the settings set aside: recorded as such where it had no reading to keep, and
    # said with its reason in its own journal row.
    for entry, refused in set_aside:
        if entry.scan_outcome not in READ_OUTCOMES:
            examined[entry.entry_id] = ScanOutcome.SKIPPED_BY_SETTINGS
        if per_entry:
            journal.record(
                "deepen",
                "entry.set_aside",
                entry_id=entry.entry_id,
                relative_path=entry.relative_path,
                because=refused,
                size_bytes=entry.size_bytes,
                kept_reading=entry.scan_outcome in READ_OUTCOMES,
            )
    #: Per entry and observer, the mentions this run will publish and their shapes.
    mentioned: dict[tuple[str, str], dict[str, str | None]] = {}
    #: The signatures this pass made again by themselves: per file, the methods
    #: whose old reading is replaced, and the operations whose old errors are replaced. A method
    #: that failed keeps its old reading, and its error is the new one.
    resigned: dict[str, frozenset[str]] = {}
    retried: dict[str, frozenset[str]] = {}
    extension_of = {entry.entry_id: (entry.extension or "").casefold() for entry in entries}
    #: Per stage: how many times it ran, its nanoseconds and its bytes, over the pass.
    stage_totals: dict[str, list[int]] = {}
    #: Messages the readers gave, over the pass, and by kind for the run record, in
    #: bounded memory with exact counts.
    warned = 0
    tallies = WarningTallies()
    if report.settings_upgraded:
        tallies.add("settings", HASH_MODE_UPGRADED)
        journal.record("deepen", "settings.upgraded", said=HASH_MODE_UPGRADED)
    # What a person said about opening each extension, read once. A dive consulting the
    # Record is the first time a scan reads a decision, and it reads it the way inference
    # does: the latest answer about each target, in the person's own words. An unanswered
    # extension is not in the map and is read as it always was.
    # The Record watermark: read before the worth-reading answers are.
    watermark = record_watermark(project.path)  # type: ignore[attr-defined]
    # Every answer the dive reads, read at that watermark, here and
    # below: one written while the dive runs is the next run's.
    with read_at(project.path, watermark):  # type: ignore[attr-defined]
        answered = reading_answers(project.path)  # type: ignore[attr-defined]
    journal.record(
        "deepen",
        "worth_reading.read",
        run_id=run_id,
        answered=len(answered),
        never=sum(1 for word in answered.values() if word == NEVER_WORTH_READING),
        read_last=sum(1 for word in answered.values() if word == LAST_WORTH_READING),
    )
    # The families are the file types this project's vocabulary distinguishes,
    # so the order a budget is spent in depends on the Dictionary, and the run records the
    # fingerprint of the one it read. Read once, with the project's answers in it, the
    # way the census reads it: a lookup per distinct pair of suffixes, not per file.
    with read_at(project.path, watermark):  # type: ignore[attr-defined]
        vocabulary = (
            DictionaryStore.for_project(project.path)  # type: ignore[attr-defined]
            if settings.extraction.spend_order
            in (SpendOrder.FAMILY_ROUND_ROBIN, SpendOrder.SERIES_FIRST)
            else None
        )
    typed: dict[tuple[str, str], str | None] = {}

    def family_of(entry: Entry) -> str | None:
        if vocabulary is None:
            return None
        suffixes = (entry.compound_extension or "", entry.extension or "")
        if suffixes not in typed:
            scope = file_type_of(vocabulary, entry)
            typed[suffixes] = None if scope is None else scope.value
        return typed[suffixes]

    # Who writes each spelling, read once: the spellings the vocabulary declares an
    # instrument writes lead their family's turns, so one image type does not cost the
    # instrument formats their share of a budget.
    written_by_an_instrument = (
        frozenset() if vocabulary is None else instrument_spellings(vocabulary)
    )

    def leads(entry: Entry) -> bool:
        return (entry.extension or "").casefold() in written_by_an_instrument

    # Beneath a program, last or never: a first dive once read a
    # copy of Fiji's libraries, beneath which every rule already defers. The programs are the
    # folders the project's Rulebook defers beneath, found by its own rule over this census, so
    # one declaration governs both. A Rulebook deferring beneath nothing leaves the order alone.
    from ingence.infer.deferral import DeferredRegions

    deferral = settings.extraction.beneath_programs
    regions = DeferredRegions()
    if deferral is not BeneathPrograms.READ_IN_TURN:
        from ingence.dictionary.programs import program_layouts
        from ingence.infer.deferral import deferred_regions
        from ingence.rulebook import project_rulebook

        deferring = (
            project_rulebook(project.path)  # type: ignore[attr-defined]
            if rulebook is None
            else rulebook
        )
        # The book's deferral or a project's own.
        if deferring.shipped_deferred or deferring.deferred:
            from ingence.infer.deferral import folders_said_programs
            from ingence.record.log import read_events
            from ingence.record.verdicts import folders_not_programs

            with read_at(project.path, watermark):  # type: ignore[attr-defined]
                layouts_from_store = (
                    vocabulary
                    if vocabulary is not None
                    else DictionaryStore.for_project(project.path)  # type: ignore[attr-defined]
                )
                said = read_events(project.path)  # type: ignore[attr-defined]
            regions = deferred_regions(
                entries,
                deferring,
                layouts=program_layouts(layouts_from_store.sections),
                not_programs=folders_not_programs(said),
                # A folder a person said is a program is read last too.
                programs=folders_said_programs(said),
            )
    beneath = regions.beneath
    report.programs_found = len(regions.raised)
    report.files_beneath_programs = sum(1 for entry in candidates if entry.entry_id in beneath)
    if regions.raised:
        path_of = {entry.entry_id: entry.relative_path for entry in entries}
        journal.record(
            "deepen",
            "programs.deferred",
            run_id=run_id,
            beneath_programs=deferral.value,
            scopes=list(regions.scopes),
            rulebook_digest=regions.rulebook_digest,
            folders=[
                [path_of.get(folder, folder), rule_id, list(values)]
                for folder, (rule_id, values) in sorted(regions.raised.items())
            ],
            files_beneath=report.files_beneath_programs,
        )
    if deferral is BeneathPrograms.NOT_READ and beneath:
        report.left_beneath_programs = report.files_beneath_programs
        candidates = [entry for entry in candidates if entry.entry_id not in beneath]

    series_first = settings.extraction.spend_order is SpendOrder.SERIES_FIRST

    def series_key_of(entry: Entry) -> str:
        """The series a candidate belongs to; a file the list gave no key is a series of one."""
        return series_of.get(entry.entry_id) or entry.entry_id

    ordered = spend_order(
        candidates,
        settings.extraction.spend_order,
        answers=answered,
        file_type=family_of,
        leads=leads,
        deferred=(
            (lambda entry: entry.entry_id in beneath)
            if beneath and deferral is BeneathPrograms.READ_LAST
            else None
        ),
        series=series_key_of if series_first else None,
    )
    #: Series first: every candidate by series, what each read member said, and what is read.
    series_members: dict[str, list[Entry]] = {}
    if series_first:
        for entry in candidates:
            series_members.setdefault(series_key_of(entry), []).append(entry)
        report.series_found = len(series_members)
    series_said: dict[str, dict[str, frozenset[tuple[str, str]]]] = {}
    series_k: dict[str, int] = dict.fromkeys(series_members, 2)
    chosen_by_series: set[str] = {entry.entry_id for entry in ordered}
    journal.record(
        "deepen",
        "spend.ordered",
        run_id=run_id,
        order=settings.extraction.spend_order.value,
        families=len({family_of(entry) for entry in candidates}),
        dictionary_hash=None if vocabulary is None else vocabulary.fingerprint(),
        beneath_programs=deferral.value,
        files_beneath_programs=report.files_beneath_programs,
    )
    if announce is not None:
        announce(len(ordered), budget.granted_seconds)
    # The files are read by a pool of workers, handed out in spend order and written in it.
    # Each worker is spawned once, loads the readers once and runs
    # the whole per-file read. The parent is the only writer and yields each result only once
    # every earlier file's has been, so the published tables are the same for any number of
    # workers. With isolation off the files are read here, one at a time, to measure against.
    context = ReadContext(
        settings=settings,
        run_id=run_id,
        observed_at=started_at,
        terms=terms,
        # The setting reaches the parameter that implements it: `adapters` is documented as
        # "names of registered extractors permitted to run", and a user narrowing the list
        # gets only those extractors.
        extractors=settings.extraction.adapters,
        membership=membership,
        roots=tuple(pool),
        extra_modules=extra_modules(),
    )
    reader: ReadPool | SerialReader = (
        ReadPool(
            context,
            workers=worker_count(
                settings.extraction, on_a_share=any_root_on_a_share, files=len(ordered)
            ),
            memory_bytes=settings.extraction.subprocess_memory_bytes,
            token=token,
        )
        if settings.extraction.use_subprocess_isolation
        else SerialReader(context)
    )
    report.workers = reader.started_with
    journal.record(
        "deepen",
        "pool.sized",
        run_id=run_id,
        workers=report.workers,
        asked=str(settings.extraction.workers),
        files=len(ordered),
        on_a_share=any_root_on_a_share,
        isolated=settings.extraction.use_subprocess_isolation,
    )

    def task_for(sequence: int, entry: Entry, **asks: Any) -> ReadTask | None:
        declaration = roots.get(entry.root_id)
        if declaration is None:
            return None
        return ReadTask(
            sequence=sequence,
            entry=entry,
            path=str(_Path(declaration.mount_path) / entry.relative_path),
            is_remote=on_a_share(entry),
            deadline_seconds=file_ceiling_for(entry.size_bytes, settings.extraction),
            **asks,
        )

    def root_check() -> None:
        """After enough access failures across folders, look at the root; pause, retry, resume
        or stop and keep."""
        enough = settings.extraction.root_loss_failures
        if len(access_run) < enough or len({folder for _, folder, _ in access_run}) < 2:
            return
        root_id = access_run[-1][0]
        why = root_lost(root_id)
        journal.record(
            "deepen",
            "root.checked",
            root_id=root_id,
            failures=len(access_run),
            folders=len({folder for _, folder, _ in access_run}),
            lost=why,
        )
        if why is None:
            access_run.clear()
            return
        # The files that failed since the root went are not reached, not content errors: their
        # access records stand, and their outcome says nothing was read.
        for _, _, entry_id in access_run:
            if examined.get(entry_id) is ScanOutcome.CONTENT_ERROR:
                examined[entry_id] = ScanOutcome.DEFERRED
                report.failed -= 1
                report.not_reached += 1
        waited = 0.0
        allowed = settings.extraction.root_wait_seconds
        while waited < allowed and not token.cancelled:
            step = min(settings.extraction.root_retry_seconds, allowed - waited)
            if _root_wait(token, step):
                break
            waited += step
            why = root_lost(root_id)
            if why is None:
                break
        if why is None and not token.cancelled:
            journal.record(
                "deepen", "root.returned", root_id=root_id, waited_seconds=round(waited, 1)
            )
            access_run.clear()
            return
        report.root_lost = root_id
        journal.record(
            "deepen", "root.lost", root_id=root_id, waited_seconds=round(waited, 1), because=why
        )
        declaration = roots[root_id]
        token.stop(
            f"the root at {declaration.mount_path} stopped answering ({why}) and did not return "
            f"in {allowed:.0f} s; what was read is kept"
        )

    def handing_out(chosen: Sequence[Entry], start: int = 0) -> Iterator[ReadTask]:
        """The files to read, in spend order, as far as the budget and the token allow."""
        nonlocal cancelled
        for sequence, entry in enumerate(chosen, start):
            root_check()
            halted = asked()
            if halted == "discard":
                cancelled = True
                return
            if budget.exhausted or halted == "stop":
                budget.unreached += 1
                continue
            signing = sign_only.get(entry.entry_id)
            task = task_for(
                sequence,
                entry,
                need_digests=need_of(entry),
                # Only the signatures that changed: the identity stage by
                # itself, the way a missing digest is added to a file whose readings stand.
                identity_methods=signing or (),
                extract=signing is None,
            )
            if task is not None:
                yield task

    def consume(result: ReadResult) -> None:
        """Record what one file's reading came to: the pass's bookkeeping, one file at a time."""
        nonlocal warned
        entry = result.task.entry
        signing = sign_only.get(entry.entry_id)
        outcome = result.outcome
        budget.spend(outcome.seconds)
        if series_first and signing is None:
            series_said.setdefault(series_key_of(entry), {})[entry.entry_id] = series_signature(
                outcome.observations
            )
        # A file that changed since the census, or while it was read, keeps none of its
        # readings, whichever pass read it. The signature-only pass follows the digest-only
        # pass, which keeps what stands, so without this rule it would keep the old rows of a
        # file whose bytes were no longer the ones they described. The file is set as examined
        # and read again, as a whole re-read is, so every reading it held is dropped and the
        # next walk reads it.
        changed = outcome.outcome is ScanOutcome.CHANGED_DURING_READ
        if signing is None or changed:
            examined[entry.entry_id] = outcome.outcome
        if outcome.event_time_ns is not None and outcome.event_time_source is not None:
            clocks[entry.entry_id] = (outcome.event_time_ns, outcome.event_time_source)
            report.clocks_filled += 1
        report.examined += 1
        if signing is None:
            report.unhashed_on_shares += on_a_share(entry) and (need_withheld or mode_withheld)
        report.bytes_read += outcome.bytes_read
        if signing is not None and changed:
            report.changed_during_read += 1
        elif signing is not None:
            # The file's kept readings and depth stand; only the signatures it was chosen for
            # are replaced, and a method that failed keeps its old reading beside the error.
            failed_here = {
                error.operation
                for error in outcome.errors
                if error.operation.startswith("identity.")
            }
            resigned[entry.entry_id] = frozenset(
                method for method in signing if f"identity.{method}" not in failed_here
            )
            retried[entry.entry_id] = frozenset(f"identity.{method}" for method in signing)
            if not failed_here:
                report.extracted += 1
                report.reader_newer_read += 1
                report.signed_again += 1
        elif outcome.outcome is ScanOutcome.EXTRACTED:
            report.extracted += 1
            # Counted with the files read, so it is never more than they are.
            report.reader_newer_read += entry.entry_id in chosen_for_reader
        elif outcome.outcome is ScanOutcome.SKIPPED_BY_POLICY:
            report.skipped += 1
        elif outcome.outcome is ScanOutcome.CONTENT_ERROR:
            report.failed += 1
        elif outcome.outcome is ScanOutcome.DEFERRED:
            report.not_reached += 1
        elif outcome.outcome is ScanOutcome.CHANGED_DURING_READ:
            report.changed_during_read += 1
        # Consecutive access failures, for the root check. Any file reached clears them.
        if signing is None:
            if _access_failure(result):
                folder = (
                    entry.relative_path.rsplit("/", 1)[0] if "/" in entry.relative_path else "."
                )
                access_run.append((entry.root_id, folder, entry.entry_id))
            else:
                access_run.clear()
        answer = answered.get((entry.extension or "").casefold())
        if answer in (LAST_WORTH_READING, NEVER_WORTH_READING):
            report.read_despite[answer] = report.read_despite.get(answer, 0) + 1
        if signing is None or changed:
            reread.add(entry.entry_id)
            reread.update(member.entry_id for member in outcome.derived_entries)
        # A signature made by itself keeps its own readings only: the routing stage's, written
        # when the file was first read, stand.
        produced = (
            outcome.observations
            if signing is None
            else [seen for seen in outcome.observations if seen.observer in signing]
        )
        for observation in produced:
            observations_writer.add(observation, now=time.monotonic())
            digest = digest_of_method.get(observation.identity_method or "")
            if digest is not None:
                fresh.setdefault(observation.entry_id, {})[digest] = observation.value_text or ""
            if observation.key in (REFERENCES_KEY, SHAPES_KEY):
                mentioned.setdefault((observation.entry_id, observation.observer), {})[
                    observation.key
                ] = observation.value_structure
        for member in outcome.derived_entries:
            extension_of[member.entry_id] = (member.extension or "").casefold()
        for error in outcome.errors:
            errors_writer.add(error, now=time.monotonic())
        for member in outcome.derived_entries:
            entries_writer.add(member, now=time.monotonic())
            report.promoted += 1
        for member_id, member_name, refused in outcome.members_set_aside:
            report.set_aside_by_settings[refused] = report.set_aside_by_settings.get(refused, 0) + 1
            if per_entry:
                journal.record(
                    "deepen",
                    "entry.set_aside",
                    entry_id=member_id,
                    relative_path=entry.relative_path,
                    because=refused,
                    member=member_name,
                    kept_reading=False,
                )
        for edge in outcome.assertions:
            assertions_writer.add(edge, now=time.monotonic())
            all_edges.append(edge)
        warned += len(outcome.warnings)
        for stage, message in outcome.warnings:
            tallies.add(stage, message)
        for timing in outcome.stages:
            total = stage_totals.setdefault(timing.stage, [0, 0, 0])
            total[0] += 1
            total[1] += timing.duration_ns
            total[2] += timing.bytes_read or 0
        if per_entry:
            staged: dict[str, Any] = (
                {"stages": [timing.as_detail() for timing in outcome.stages]} if timings else {}
            )
            if outcome.warnings:
                staged["warnings"] = [list(pair) for pair in outcome.warnings]
            if answer is not None:
                staged["worth_reading"] = answer
            if entry.entry_id in beneath:
                staged["beneath_program"] = True
            if signing is not None:
                staged["signed_alone"] = list(signing)
            journal.record(
                "deepen",
                "entry.examined",
                entry_id=entry.entry_id,
                relative_path=entry.relative_path,
                duration_ns=outcome.nanoseconds,
                outcome=outcome.outcome.value,
                bytes_read=outcome.bytes_read,
                size_bytes=entry.size_bytes,
                family=family_of(entry),
                **staged,
            )
        if reporter is not None:
            reporter.advance(entries_examined=1)  # type: ignore[attr-defined]

    def series_wanting_more() -> list[Entry]:
        """The next files of the series whose read members disagree: twice as many of each,
        evenly spaced by identifier, until they agree or the series is read."""
        more: list[Entry] = []
        for key, members in series_members.items():
            said = series_said.get(key, {})
            if len(said) < 2 or len(set(said.values())) == 1:
                continue
            if all(entry.entry_id in chosen_by_series for entry in members):
                continue
            series_k[key] = min(len(members), series_k[key] * 2)
            for entry in series_picks(members, series_k[key]):
                if entry.entry_id not in chosen_by_series:
                    chosen_by_series.add(entry.entry_id)
                    more.append(entry)
        return more

    reader.__enter__()
    for result in reader.read(handing_out(ordered)):
        consume(result)
    handed = len(ordered)
    while series_first and not cancelled:
        more = series_wanting_more()
        if not more:
            break
        report.series_rounds += 1
        for result in reader.read(handing_out(more, handed)):
            consume(result)
        handed += len(more)
    if series_first:
        report.left_to_series = sum(
            1 for entry in candidates if entry.entry_id not in chosen_by_series
        )
        journal.record(
            "deepen",
            "series.read",
            series=report.series_found,
            candidates=len(candidates),
            read_first=len(ordered),
            rounds_more=report.series_rounds,
            left_to_series=report.left_to_series,
        )

    # Checked again after the loop. Cancellation requested while the last entry
    # was being examined must still mean nothing is published, or whether a run
    # survives a Ctrl-C depends on a race the user cannot see. This is the
    # census's own check, at ``census.py``.
    if discard_asked():
        cancelled = True

    totals: dict[str, Any] = (
        {"stages": [[stage, *stage_totals[stage]] for stage in sorted(stage_totals)]}
        if timings
        else {}
    )
    journal.record(
        "deepen",
        "run.examined",
        examined=report.examined,
        extracted=report.extracted,
        failed=report.failed,
        changed_during_read=report.changed_during_read,
        bytes_read=report.bytes_read,
        budget_spent_seconds=round(budget.spent_seconds, 4),
        wall_seconds=round(budget.elapsed, 3),
        exhausted_by=budget.exhausted_by,
        unreached=budget.unreached,
        cancelled=cancelled,
        stopped=stop_asked(),
        warnings=warned,
        **totals,
    )

    # Everything this pass did not re-read is carried unchanged, scan errors
    # included. A table rewritten with this pass's own errors alone would make a run published
    # by a dive record none of the census's access failures. A re-read entry's observations and
    # errors are this pass's alone. A member promoted from a re-read container is replaced by
    # what the cascade found this time, which may be less, and so is everything recorded
    # against that member or anything promoted from it.
    superseded = set(reread)
    members_of: dict[str, list[str]] = {}
    for entry in entries:
        if entry.entry_kind is EntryKind.DERIVED and entry.parent_entry_id is not None:
            members_of.setdefault(entry.parent_entry_id, []).append(entry.entry_id)
    frontier = sorted(reread)
    while frontier:
        for member_id in members_of.get(frontier.pop(), ()):
            if member_id not in superseded:
                superseded.add(member_id)
                frontier.append(member_id)

    # Digests alone: the identity stage by itself, on a file whose readings
    # stand, for the digests a mode asks and a comparison needs. Each is written beside the
    # readings the file keeps; a file that changed while it was hashed keeps none of it.
    method_of = {digest: name for name, digest in digest_of_method.items()}
    added: dict[str, dict[str, str]] = {}

    def digest_task(sequence: int, entry: Entry, kinds: frozenset[str]) -> ReadTask | None:
        """The identity stage alone, for the digests named, as a task for the pool."""
        methods = tuple(method_of[kind] for kind in sorted(kinds) if kind in method_of)
        if not methods:
            return None
        return task_for(
            sequence, entry, identity_methods=methods, need_digests=kinds, extract=False
        )

    def digest_received(result: ReadResult, kinds: frozenset[str]) -> bool:
        """Keep the digests one such task came back with; whether any of those asked were.

        A digest task killed at its deadline, whose worker died, or whose own clock refused
        the digest, is a failure of that file and is said: its errors are
        written and it is counted. Its earlier readings stand, as a file whose digest alone was
        asked keeps them.
        """
        entry, outcome = result.task.entry, result.outcome
        budget.spend(outcome.seconds)
        report.bytes_read += outcome.bytes_read
        if result.killed_for is not None or outcome.outcome is ScanOutcome.CONTENT_ERROR:
            for error in outcome.errors:
                errors_writer.add(error, now=time.monotonic())
            report.failed += 1
            return False
        if outcome.outcome is ScanOutcome.CHANGED_DURING_READ:
            report.changed_during_read += 1
            return False
        found = False
        for observation in outcome.observations:
            kind = digest_of_method.get(observation.identity_method or "")
            if kind in kinds and observation.value_text:
                observations_writer.add(observation, now=time.monotonic())
                added.setdefault(entry.entry_id, {})[kind] = observation.value_text
                found = True
        return found

    def digests_handing_out(
        entries: Sequence[Entry], kinds_of: Callable[[Entry], frozenset[str]]
    ) -> Iterator[ReadTask]:
        """Digest tasks in order, as far as the budget and the token allow."""
        nonlocal cancelled
        for sequence, entry in enumerate(entries):
            halted = asked()
            if halted == "discard":
                cancelled = True
                return
            if budget.exhausted or halted == "stop":
                budget.unreached += 1
                continue
            task = digest_task(sequence, entry, kinds_of(entry))
            if task is not None:
                yield task

    def hashable(entry: Entry) -> bool:
        """Whether the hash settings' own filters let a digest be taken of this file at all."""
        hashing = settings.hash
        return policy_selects(
            extensions=suffixes(entry),
            size_bytes=entry.size_bytes or 0,
            include_extensions=hashing.include_extensions,
            exclude_extensions=hashing.exclude_extensions,
            max_file_bytes=hashing.max_file_bytes,
        )

    if not cancelled:
        kinds_missing = {entry.entry_id: missing_digests(entry) for entry in digest_only}
        for result in reader.read(
            digests_handing_out(digest_only, lambda entry: kinds_missing[entry.entry_id])
        ):
            if digest_received(result, kinds_missing[result.task.entry.entry_id]):
                report.digested_alone += 1

    # The whole digests a comparison needs: where two files' samples agree,
    # only the whole of each proves them byte-identical, so each lacking one is digested whole,
    # within what is left of the budget. Only files the hash filters admit, and none larger
    # than ``full_hash_max_bytes``: above it two agreeing samples stay a
    # nomination, counted, never read whole. Where a file of a shared size holds a whole digest
    # and no sample and another lacks a whole one, its sample is taken first, so the two compare.
    if choice.by_need and not cancelled:
        # Files on disk only: a container's member has no path of its own to read whole.
        by_id = {
            entry.entry_id: entry
            for entry in readable
            if entry.entry_kind is EntryKind.FILESYSTEM
            and (entry.size_bytes or 0) in shared
            and hashable(entry)
            and not unhashed(entry)
        }
        standing: dict[str, dict[str, str]] = {}
        for entry_id in by_id:
            if entry_id in reread:
                standing[entry_id] = dict(fresh.get(entry_id, {}))
            elif entry_id not in superseded:
                standing[entry_id] = {**held.get(entry_id, {}), **added.get(entry_id, {})}
        by_size: dict[int, list[str]] = {}
        for entry_id in standing:
            by_size.setdefault(by_id[entry_id].size_bytes or 0, []).append(entry_id)
        # A file holding a whole digest and no sample is sampled, and so is one of a shared size
        # holding no digest yet, but never one beneath a program a person's
        # ``beneath_programs: never`` leaves unread.
        never_read = beneath if deferral is BeneathPrograms.NOT_READ else frozenset()
        to_sample = sorted(
            (size, entry_id)
            for size, members in by_size.items()
            if len(members) > 1 and not all("whole" in standing[member] for member in members)
            for entry_id in members
            if "sampled" not in standing[entry_id] and entry_id not in never_read
        )

        def sampling() -> Iterator[ReadTask]:
            for sequence, (_, entry_id) in enumerate(to_sample):
                if budget.exhausted or asked() is not None:
                    return  # a stop and a discard alike: the loop below counts or abandons
                task = digest_task(sequence, by_id[entry_id], frozenset({"sampled"}))
                if task is not None:
                    yield task

        for result in reader.read(sampling()):
            sampled_id = result.task.entry.entry_id
            if digest_received(result, frozenset({"sampled"})):
                standing[sampled_id]["sampled"] = added[sampled_id]["sampled"]
        samples: dict[tuple[int, str], list[str]] = {}
        for entry_id, digests in standing.items():
            if digests.get("sampled"):
                size = by_id[entry_id].size_bytes or 0
                samples.setdefault((size, digests["sampled"]), []).append(entry_id)
        agreeing = sorted(
            (by_id[entry_id].size_bytes or 0, entry_id)
            for members in samples.values()
            if len(members) > 1
            for entry_id in members
            if "whole" not in standing[entry_id]
        )
        ceiling = settings.hash.full_hash_max_bytes
        wanted = [(size, entry_id) for size, entry_id in agreeing if size <= ceiling]
        report.nominated_by_need = len(agreeing) - len(wanted)
        digested = 0
        for result in reader.read(
            digests_handing_out(
                [by_id[entry_id] for _, entry_id in wanted], lambda entry: frozenset({"whole"})
            )
        ):
            if digest_received(result, frozenset({"whole"})):
                digested += 1
        report.digested_by_need = digested
        journal.record(
            "deepen",
            "need.digested",
            wanted=len(wanted),
            digested=digested,
            sampled_to_compare=len(to_sample),
            nominated_above_ceiling=report.nominated_by_need,
            full_hash_max_bytes=ceiling,
            groups=sum(1 for members in samples.values() if len(members) > 1),
        )
    reader.close()
    for note in reader.notes:
        journal.record(
            "deepen", str(note["event"]), **{k: v for k, v in note.items() if k != "event"}
        )
    journal.record(
        "deepen",
        "pool.finished",
        workers_started=reader.started_with,
        workers_at_end=reader.size,
        worker_timeouts=reader.timeouts,
        worker_deaths=reader.deaths,
        workers_recycled=reader.recycled,
        # Whether the kernel took the workers' address-space limit.
        kernel_limit=getattr(reader, "kernel_limit", None),
    )
    with open_catalog(project.path, run_id=previous.name) as catalog:  # type: ignore[attr-defined]
        for rows in catalog.batches("SELECT * FROM observations ORDER BY observation_id"):
            for row in rows:
                if row["entry_id"] in superseded and not (
                    row["key"] in SERIES_KEYS
                    # The file list's series facts stand through a re-read, and fall with the
                    # rest of a file that changed, which the next walk describes again.
                    and examined.get(row["entry_id"]) is not ScanOutcome.CHANGED_DURING_READ
                ):
                    continue
                if row["observer"] in resigned.get(row["entry_id"], ()):
                    continue  # signed again by itself: the new reading replaces it
                observations_writer.add(_observation_from_row(row), now=time.monotonic())
                if row["key"] in (REFERENCES_KEY, SHAPES_KEY):
                    mentioned.setdefault((row["entry_id"], row["observer"]), {})[row["key"]] = row[
                        "value_structure"
                    ]
        for rows in catalog.batches("SELECT * FROM scan_errors ORDER BY relative_path, operation"):
            for row in rows:
                if row["entry_id"] in superseded:
                    continue
                if row["operation"] in retried.get(row["entry_id"], ()):
                    continue  # tried again by itself: this pass's error, if any, replaces it
                errors_writer.add(ScanError.model_validate(row), now=time.monotonic())
        for rows in catalog.batches("SELECT * FROM assertions ORDER BY edge_id"):
            for row in rows:
                if row["source_id"] in superseded:
                    continue
                edge = _assertion_from_row(row)
                assertions_writer.add(edge, now=time.monotonic())
                all_edges.append(edge)

    # Entries not reached keep the outcome they had. An unexamined entry is not
    # a failure and must not be recorded as one.
    for entry in entries:
        if entry.entry_kind is EntryKind.DERIVED and entry.parent_entry_id in superseded:
            continue
        updated = examined.get(entry.entry_id)
        changes: dict[str, object] = {"last_seen_run": run_id}
        if updated is not None:
            changes["scan_outcome"] = updated
            # The depth its content was read at: what a deeper pass reads by.
            changes["extraction_profile"] = profile if updated in READ_OUTCOMES else None
        clock = clocks.get(entry.entry_id)
        if clock is not None:
            # The tier is derived from the source through one mapping rather than
            # stated beside it, so the two can never disagree.
            changes["event_time_ns"] = clock[0]
            changes["event_time_source"] = clock[1]
            changes["event_time_tier"] = CLOCK_TIER_OF[clock[1]]
        entries_writer.add(entry.model_copy(update=changes), now=time.monotonic())

    report.wall_seconds = round(budget.elapsed, 3)
    run_writer = FragmentWriter(table_for(RunRecord), staging / "run")
    run_writer.add(
        build_run_record(
            run_id=run_id,
            parent_run_id=previous.name,
            started_at=started_at,
            profile=profile,
            # The settings the pass was bound by: the file's, with its depth's presets applied.
            settings=settings,
            project=project,
            deepening_budget_granted_seconds=budget.granted_seconds,
            deepening_budget_spent_seconds=round(budget.spent_seconds, 4),
            # The pass's clocks: the wall, the work, and the workers.
            wall_seconds=report.wall_seconds,
            work_seconds=round(budget.spent_seconds, 4),
            workers=report.workers,
            extraction_ceiling_seconds=(
                settings.extraction.subprocess_timeout_seconds,
                settings.extraction.subprocess_timeout_per_gigabyte_seconds,
            ),
            components=_component_snapshot(),
            rulebook_hash=book.digest,
            # The spend order read the vocabulary, so which one is part of what the
            # run was bound by. Absent where the order never consulted it.
            dictionary_hash=None if vocabulary is None else vocabulary.fingerprint(),
            packs=in_force(vocabulary),
            excluded=left_out,
            recognised=recognised,
            mentions=tally(
                (
                    (entry_id, observer, held.get(REFERENCES_KEY), held.get(SHAPES_KEY))
                    for (entry_id, observer), held in sorted(mentioned.items())
                    if held.get(REFERENCES_KEY)
                ),
                extension_of,
            ),
            record_watermark=watermark,
            job_id=current_job_id(),
            published=not cancelled,
            warnings=tallies.tallies(),
            # The dive counts what libraries say around every file it reads: each counting
            # pass says so.
            warnings_tallied=True,
            hash_mode=choice.recorded,
            hash_mode_set_by=choice.set_by,
            skip_remote_filesystems=remote.skip,
            skip_remote_filesystems_set_by=remote.set_by,
        ),
        now=time.monotonic(),
    )

    for writer in (
        entries_writer,
        observations_writer,
        errors_writer,
        assertions_writer,
        run_writer,
    ):
        writer.close(now=time.monotonic())

    # The journal stays open through the invariants and the compaction. A journal closed
    # earlier would leave the phase that holds a whole run in memory unmeasured. With the
    # journal open, the engine times that phase while the run is checked.
    #
    # The cascade draws embedding edges, so this pass publishes a graph and must
    # be held to the same gate the census is. An unchecked publication path is
    # how a graph that violates an invariant reaches a reader, and it would only
    # take one pass that forgot.
    # The claims table is carried into this run, so its claims are published with
    # it and are checked with it. Handing the invariants an empty tuple would let
    # the one invariant that reads a closed-world claim pass without reading any.
    from ingence.storage.index_store import record_from_row

    phase_started = time.monotonic()
    with (
        _publishing_phase(journal, timings, "publish.invariants"),
        open_catalog(project.path, run_id=previous.name) as catalog,  # type: ignore[attr-defined]
    ):
        claims = tuple(
            record_from_row(row, Claim)
            for rows in catalog.batches("SELECT * FROM claims ORDER BY claim_id")
            for row in rows
        )
        snapshot = GraphSnapshot(assertions=tuple(all_edges), claims=claims)
        outcomes = check_all(snapshot)
    report.publish_seconds["invariants"] = round(time.monotonic() - phase_started, 3)
    report.invariants = outcomes

    # A cancelled pass publishes nothing. The staging directory is simply
    # abandoned: no reader ever resolves through it, so the previous run stays
    # current and the project stays valid. That is what makes cancellation safe at any
    # moment, including in the middle of a supervised extraction, where the token itself
    # has already killed the children.
    #
    # The check matters because a pass that breaks out of its loop on cancellation and then
    # commits anyway publishes a partial run and moves the pointer to it, so a Ctrl-C
    # during content work would leave a partial run current. The census has the same check.
    if cancelled:
        journal.record("deepen", "run.abandoned", reason=token.reason if token else "cancelled")
        journal.close()
        report.cancelled = True
        report.cancellation_reason = token.reason if token else "cancelled"
        return report
    if stop_asked():
        # Stopped and kept: published below exactly as an exhausted budget
        # is, the invariants checked, the files not reached counted and carried as unexamined.
        report.stopped = True
        report.stop_reason = token.reason if token else "stopped"
        journal.record(
            "deepen", "run.stopped", reason=report.stop_reason, unreached=budget.unreached
        )

    journal_spec = table_for(JournalEvent)
    phase_started = time.monotonic()
    with _publishing_phase(journal, timings, "publish.compaction"):
        compact_run(staging, tuple(spec for spec in TABLES if spec is not journal_spec))
    report.publish_seconds["compaction"] = round(time.monotonic() - phase_started, 3)

    # The journal is sealed here and compacted last, alone, because it cannot
    # record its own compaction; see ``DeepenReport.publish_seconds``.
    phase_started = time.monotonic()
    journal.close()
    compact_table(staging / journal_spec.directory, journal_spec)
    report.publish_seconds["journal"] = round(time.monotonic() - phase_started, 3)

    # The run's quality figures, counted from the tables just compacted.
    from ingence.quality.publish import write_quality

    phase_started = time.monotonic()
    # No journal timer here: the journal is sealed and compacted by now, so an event would be lost.
    write_quality(project.path, staging, run_id=run_id, phase="dive")  # type: ignore[attr-defined]
    report.publish_seconds["quality"] = round(time.monotonic() - phase_started, 3)

    phase_started = time.monotonic()
    manifest = build_manifest(
        staging,
        run_id=run_id,
        engine_version=__version__,
        created_at=started_at,
        invariants=outcomes,
    )
    report.publish_seconds["manifest"] = round(time.monotonic() - phase_started, 3)
    phase_started = time.monotonic()
    commit_run(project.path, manifest=manifest, updated_at=started_at)  # type: ignore[attr-defined]
    report.publish_seconds["commit"] = round(time.monotonic() - phase_started, 3)
    return report


def _publishing_phase(
    journal: Journal | NullJournal, timings: bool, event: str
) -> contextlib.AbstractContextManager[object]:
    """Return a journal timer for one phase of publishing, or nothing where timings are off."""
    if timings:
        return journal.timed("deepen", event)
    return contextlib.nullcontext()


def _component_snapshot() -> tuple[ComponentActivation, ...]:
    """Record which optional components were configured, enabled and usable.

    The function covers every registry, not only the ones that fired. A run that lists only
    what ran cannot be compared with a run that had a different set configured, and
    comparing runs is the purpose of the experiment register.
    """
    rows: list[ComponentActivation] = []
    for registry in (DETECTORS, IDENTITY_METHODS, EXTRACTORS):
        for name in registry.names():
            registry.probe(name)
        rows.extend(ComponentActivation(**row) for row in registry.snapshot())
    return tuple(rows)


def _entry_from_row(row: dict[str, Any]) -> Entry:
    """Rebuild an entry from a catalog row.

    The one place the storage naming rule is reversed: a column named
    ``x_json`` holds field ``x`` serialised, so it is parsed back on the way in.
    """
    payload = dict(row)
    payload["locator_chain"] = json.loads(str(payload.pop("locator_chain_json", "[]")))
    return Entry.model_validate(payload)


def _observation_from_row(row: dict[str, Any]) -> Observation:
    """Rebuild an observation from a catalog row."""
    payload = dict(row)
    payload["locator_chain"] = json.loads(str(payload.pop("locator_chain_json", "[]")))
    return Observation.model_validate(payload)


def _assertion_from_row(row: dict[str, Any]) -> Assertion:
    """Rebuild an assertion from a catalog row."""
    payload = dict(row)
    for column in ("scope", "support", "defeaters"):
        payload[column] = json.loads(str(payload.pop(f"{column}_json", "null")))
    return Assertion.model_validate(payload)


# ---------------------------------------------------------------------------
# The cascade: members recovered from inside a container.
# ---------------------------------------------------------------------------


def expand_derived(
    context: ExtractionContext,
    streams: Sequence[DerivedStream],
    *,
    entry: Entry,
    run_id: str,
    observed_at: str,
    settings: Settings,
    depth: int = 0,
    membership: ScanRule | None = None,
) -> tuple[list[Entry], list[Assertion], list[Observation]]:
    """Turn recovered streams into entries, embedding edges and observations.

    Each member is inspected as a file would be and, where the settings say so,
    promoted to an entry of its own so that an edge can point at it. The edge is
    ``embeds`` and not ``contains``, and the distinction is the reason
    that primitive exists: filesystem containment is observationally complete
    after enumeration, while structural containment is bounded by a budget, a
    recursion limit, encryption and format support. Every embedding edge
    therefore carries the state that says why expansion stopped, so the absence
    of a sibling member is readable and not ambiguous.

    Members are inspected in memory. Nothing is written to disk at any depth.

    ``membership`` is ``cascade.container_member`` bound from the Rulebook. Every
    embedding edge cites it, and its predicate says how the container holds each member;
    ``None`` binds the shipped rule.
    """
    entries: list[Entry] = []
    assertions: list[Assertion] = []
    observations: list[Observation] = []
    if not streams:
        return entries, assertions, observations

    scope = Scope(ScopeKind.ROOT, entry.root_id)
    bound = membership
    if bound is None:
        bound = scan_rules((CASCADE_MEMBERSHIP,))[CASCADE_MEMBERSHIP]
    rule, rule_version = bound.rule_id, bound.version
    promote = settings.extraction.promote_container_members

    for stream in streams:
        member_id = entry_id(
            entry.root_id,
            entry.relative_path,
            stream.locator,
        )
        if promote:
            entries.append(
                Entry(
                    entry_id=member_id,
                    entry_kind=EntryKind.DERIVED,
                    root_id=entry.root_id,
                    # A derived entry has no path of its own. It keeps its
                    # container's, and the locator chain is what distinguishes
                    # it, which is exactly what the composite identity function
                    # was made composite for.
                    relative_path=entry.relative_path,
                    name=stream.locator[-1].target if stream.locator else "",
                    parent_entry_id=entry.entry_id,
                    # One level below its container, not the length of the
                    # whole chain: a member of a member is two deep, and the
                    # chain is already the record of how it was reached.
                    depth=entry.depth + 1,
                    entry_type=EntryType.FILE,
                    extension=_suffix_of(stream.locator),
                    size_bytes=stream.size_bytes,
                    locator_chain=stream.locator,
                    transformation=stream.transformation,
                    transformation_version=stream.transformation_version,
                    ordinal=stream.ordinal,
                    derived_content_hash=stream.content_hash,
                    resolution_status=ResolutionStatus.UNEXAMINED,
                    first_seen_run=run_id,
                    last_seen_run=run_id,
                    scan_outcome=ScanOutcome.EXTRACTED,
                )
            )
            embedding = bound.evaluate(stream)
            if not isinstance(embedding, Embedding):
                message = f"{rule} answered {type(embedding).__name__}, not an embedding"
                raise TypeError(message)
            assertions.append(
                Assertion(
                    edge_id=edge_id(
                        source=entry.entry_id,
                        target=member_id,
                        primitive=Primitive.EMBEDS,
                        rule_id=rule,
                        rule_version=rule_version,
                        scope=scope,
                    ),
                    run_id=run_id,
                    source_id=entry.entry_id,
                    target_id=member_id,
                    primitive=Primitive.EMBEDS,
                    specialization=embedding.specialization,
                    rule_id=rule,
                    rule_version=rule_version,
                    scope=scope,
                    # Entailed: the member either came out of the container or it
                    # did not. There is no interpretation between the two.
                    inference_tier=InferenceTier.ENTAILED,
                    expansion_state=embedding.expansion_state,
                )
            )

        # Recorded on the container whether or not the member was promoted, so
        # turning promotion off loses the node and never the evidence.
        observations.append(
            _reading_observation(
                entry_id=entry.entry_id,
                run_id=run_id,
                observer="cascade",
                observer_version=rule_version,
                observed_at=observed_at,
                key=f"derived.{stream.transformation}.content_hash",
                value=stream.content_hash,
                kind=ValueKind.TEXT,
                tier=ExtractionTier.MECHANICALLY_EXPOSED,
                locator=stream.locator,
            )
        )
    del context, depth
    return entries, assertions, observations


def _suffix_of(locator: Sequence[LocatorStep]) -> str:
    """Return the extension of a member name, without its dot."""
    if not locator:
        return ""
    from pathlib import PurePosixPath

    return PurePosixPath(locator[-1].target).suffix.casefold().removeprefix(".")


def _member_set_aside(
    member: Entry, size_bytes: int | None, *, settings: Settings, into: DeepenOutcome
) -> bool:
    """Set a member aside unread where a person's extraction settings leave it out.

    Its promoted entry, already among ``into.derived_entries``, is recorded as
    ``skipped_by_settings``, and the reason is kept for the pass to count and journal.
    """
    extraction = settings.extraction
    refused = policy_refusal(
        extensions=frozenset({member.extension} if member.extension else ()),
        size_bytes=size_bytes or 0,
        include_extensions=extraction.include_extensions,
        exclude_extensions=extraction.exclude_extensions,
        max_file_bytes=extraction.max_file_bytes,
    )
    if refused is None:
        return False
    for index, held in enumerate(into.derived_entries):
        if held.entry_id == member.entry_id:
            into.derived_entries[index] = held.model_copy(
                update={"scan_outcome": ScanOutcome.SKIPPED_BY_SETTINGS}
            )
            break
    into.members_set_aside.append((member.entry_id, member.name, refused))
    return True


def _examine_member(
    member: Entry,
    *,
    container: Path,
    run_id: str,
    observed_at: str,
    settings: Settings,
    limits: Limits,
    into: DeepenOutcome,
    terms: tuple[str, ...],
    supervisor: Supervisor | None,
    membership: ScanRule | None = None,
    roots: Sequence[PoolRoot] = (),
) -> None:
    """Examine one promoted member exactly as a file would be examined.

    The member's bytes are recovered by walking its locator chain back through
    the container and are never written anywhere. Its readings are recorded
    against the member's own entry, so a table inside an archive has its columns
    on the table rather than smeared onto the archive.
    """
    from ingence.extractors.archive import materialise

    # A member is judged by the settings as a file on disk is: its own
    # extension, and its size once extracted, where the container stated it.
    if _member_set_aside(member, member.size_bytes, settings=settings, into=into):
        return
    payload = materialise(container, member.locator_chain, limit=limits.max_bytes_read)
    if (
        payload is not None
        and member.size_bytes is None
        and _member_set_aside(member, len(payload), settings=settings, into=into)
    ):
        return
    if payload is None:
        # The chain no longer resolves. This is a fact about the container, recorded as
        # such and not raised: the member was seen and could not be re-read.
        into.observations.append(
            _reading_observation(
                entry_id=member.entry_id,
                run_id=run_id,
                observer="cascade",
                observer_version="1",
                observed_at=observed_at,
                key="derived.unreadable",
                value=None,
                kind=ValueKind.NEGATIVE,
                tier=ExtractionTier.INACCESSIBLE,
            )
        )
        return

    context = ExtractionContext(
        entry_id=member.entry_id,
        path=container,
        relative_path=member.relative_path,
        extensions=frozenset({f".{member.extension}"} if member.extension else set()),
        size_bytes=len(payload),
        limits=limits,
        terms=terms,
        depth=nesting_of(member),
        payload=payload,
        locator=member.locator_chain,
    )

    load_extractor_builtins()
    produced: list[ExtractorResult] = []
    # The same permitted set as the whole-file path, and the same route
    # into each extractor. A member of a container is still a thing an extractor
    # reads, so a list or a process boundary that applied there and not here would
    # make what ran, and how safely, depend on how a file was packaged.
    permitted = settings.extraction.adapters or EXTRACTORS.names(available_only=True)
    for name in permitted:
        if not EXTRACTORS.available(name):
            continue
        try:
            extractor = EXTRACTORS.get(name)
            if not extractor.supports(context):
                continue
        except Exception as exc:
            into.errors.append(
                ScanError(
                    entry_id=member.entry_id,
                    relative_path=member.relative_path,
                    operation=f"extract.{name}",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    run_id=run_id,
                    failure_class=FailureClass.COMPREHENSION,
                )
            )
            continue
        outcome, errors, _ = _extract_once(
            name,
            extractor,
            context,
            supervisor=supervisor,
            entry=member,
            run_id=run_id,
            supervise_above_bytes=settings.extraction.supervise_in_process_above_bytes,
            # A member's own size, which the container reported before expanding it.
            ceiling_seconds=ceiling_for(member.size_bytes, settings.extraction),
        )
        into.errors.extend(errors)
        if outcome is None:
            continue
        into.warnings.extend((f"extract.{name}", message) for message in outcome.warnings)
        produced.append(outcome)
        into.observations.extend(
            _extractor_observations(
                outcome,
                entry_id=member.entry_id,
                run_id=run_id,
                observed_at=observed_at,
                roots=roots,
            )
        )

    nested: list[DerivedStream] = []
    for outcome in produced:
        nested.extend(outcome.derived)
    if not nested:
        return

    promoted, edges, observations = expand_derived(
        context,
        nested,
        entry=member,
        run_id=run_id,
        observed_at=observed_at,
        settings=settings,
        membership=membership,
    )
    into.derived_entries.extend(promoted)
    into.assertions.extend(edges)
    into.observations.extend(observations)
    if nesting_of(member) < limits.max_expansion_depth:
        for deeper in promoted:
            _examine_member(
                deeper,
                container=container,
                run_id=run_id,
                observed_at=observed_at,
                settings=settings,
                limits=limits,
                into=into,
                terms=terms,
                supervisor=supervisor,
                membership=membership,
                roots=roots,
            )
