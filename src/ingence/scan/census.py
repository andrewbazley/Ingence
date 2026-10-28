"""The census: a read-only metadata walk that never opens a file.

The first pass over a data pool, and the one that returns value immediately.
Nobody waits two hours to see their first screen, so the census finishes fast and
the interface opens on it while deeper work continues.

What it produces: entries for every filesystem object, containment edges forming the forest those
entries sit in, entailed aggregates over each directory, and errors as data. Nothing here is an
interpretation. A count over a complete enumeration is arithmetic, not opinion, and the census
emits it at the entailed tier with a named rule like everything else.

The stat facts live on the entry and not as observations. Everything a census reads is a fixed,
universal set of filesystem fields, so they are columns. The separation that matters is between
fact and inference, and an entry has no inference field to confuse one with. Observations exist
for readings that do not fit a fixed column set, which is every reading after the census.

Failures are independent. A file that cannot be stat'd, a directory that cannot be listed and a
symlink that cannot be read are each recorded, and the walk continues. One unreadable file must
never fail a scan. A scan that reports fewer files because one of them was locked is worse than
useless, because it is quietly wrong.

The census records symlinks and never follows them. Following them turns a tree into a graph,
makes containment stop being a forest, and can turn a walk into one that does not terminate.

The walk is concurrent without being nondeterministic. Directory enumeration is latency bound,
not CPU bound, so threads help, and they help most on a network share where a serial walk spends
its life waiting. Records therefore arrive in completion order, which varies between runs, and
nothing downstream depends on that order. Every table is canonically sorted before it is written
and compacted into one sorted fragment before it is published.
"""

from __future__ import annotations

import fnmatch
import ntpath
import os
import posixpath
import re
import stat as stat_module
import time
import unicodedata
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ingence import SCHEMA_VERSION, __version__
from ingence.core.ids import (
    CaseSensitivity,
    Scope,
    ScopeKind,
    canonical_json,
    claim_id,
    digest_text,
    edge_id,
    entry_id,
    observation_id,
)
from ingence.core.invariants import (
    CLOSED_WORLD_PREDICATE_PREFIX,
    GraphSnapshot,
    check_all,
)
from ingence.core.links import characterise_link_target
from ingence.core.platform import gives, lstat_entry, lstat_path
from ingence.core.project import Project, is_listing_root, refuse_unreachable_roots
from ingence.core.relations import Primitive
from ingence.core.run_records import build_run_record, counting_warnings
from ingence.core.schema import (
    CLOCK_TIER_OF,
    WHOLE_DIGEST_UNCHECKED_MOVES,
    Assertion,
    CaseReadingStated,
    Claim,
    ClockTier,
    Component,
    ComponentActivation,
    DictionaryBinding,
    DictionaryEntry,
    DictionaryOrigin,
    Entry,
    EntryKind,
    EntryType,
    ExcludedNames,
    ExclusionReason,
    FailureClass,
    InvariantOutcome,
    MovedBy,
    NameShape,
    Observation,
    PlatformRecord,
    RecognisedAs,
    RecognisedPath,
    Record,
    ReuseDecision,
    Root,
    RunRecord,
    ScanError,
    ScanOutcome,
    SourceScope,
    SymlinkState,
    TimeSource,
    ValueKind,
)
from ingence.core.suffixes import is_extension
from ingence.core.tiers import ExtractionTier, InferenceTier, ResolutionStatus
from ingence.dictionary.case_reading import project_answers
from ingence.dictionary.discover import (
    RESOLVER_VERSION,
    CaseBinder,
    DomainBinder,
    NameReading,
    SettledDate,
    case_reading_read_with,
    date_order_observation,
    names_read_with,
    observe_carried,
    observe_series,
    read_name,
    settled_short_date,
    split_extension,
)
from ingence.dictionary.file_types import declared_compounds, extension_domains
from ingence.dictionary.laboratory_packs import in_force
from ingence.dictionary.normalize import AtomKind, Normaliser, reading_options
from ingence.dictionary.packs.proposals import domain_priors
from ingence.dictionary.store import DictionaryStore
from ingence.infer.subjects import DirectoryCounts, Listing
from ingence.jobs.provenance import current_job_id, record_watermark
from ingence.listing.format import listing_digest, read_listing
from ingence.listing.ingest import declared_observations, entries_from_listing, placeholder_links
from ingence.record.log import read_at
from ingence.rulebook import shipped_rulebook
from ingence.rulebook.registry import DirectoryTotals
from ingence.scan.cancel import CancelledError
from ingence.scan.moves import Move, find_moves
from ingence.scan.organisation import Child, FolderInput, FolderProfile, profile_folder
from ingence.scan.progress import ProgressReporter
from ingence.scan.recognise import (
    OwnProject,
    holds_other_names,
    installation_directories,
    recognise_directory,
)
from ingence.scan.reuse import processing_policy, reuse_decision, stat_facts, stat_fingerprint
from ingence.scan.rules import (
    CENSUS_AGGREGATE,
    CENSUS_CONTAINMENT,
    CENSUS_OBJECT_KIND,
    CENSUS_RULE_IDS,
    scan_rules,
)
from ingence.storage.commit import (
    CommitError,
    build_manifest,
    commit_run,
    staging_path,
)
from ingence.storage.compact import compact_run
from ingence.storage.index_store import FragmentWriter, record_from_row
from ingence.storage.journal import Journal, NullJournal, open_journal
from ingence.storage.layout import (
    RUNS_DIR,
    TABLES,
    integer_range_violations,
    is_project_name,
    owned_path,
    spelled_otherwise,
    table_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Mapping, Sequence

    from ingence.core.project import RootDeclaration
    from ingence.core.registry import Registry
    from ingence.core.settings import Settings
    from ingence.rulebook.resolve import EffectiveRulebook
    from ingence.scan.cancel import CancellationToken
    from ingence.scan.reuse import PreviousRecord
    from ingence.scan.rules import ScanRule

__all__ = [
    "CensusCounts",
    "CensusRecords",
    "compound_extension",
    "extension_of",
    "is_extension",
    "walk_root",
]

# ---------------------------------------------------------------------------
# The rules the census cites.
#
# Every asserted edge must name a rule and a version, from the first edge the engine draws. The
# Rulebook declares the census's three rules as provisional, so the rule that every census claim
# and every containment edge names can be opened and read. A run binds each rule's version and
# predicate from the Rulebook, as ``scan/rules.py`` sets out.
# ---------------------------------------------------------------------------


def compound_extension(name: str, declared: Collection[str] = ()) -> str:
    """Return the multi-part suffix of a name, such as ``.tar.gz``.

    Bounded to the last two components. Unbounded, a file named with several
    dots produces a long meaningless suffix, and a filename that is entirely
    dots produces an absurd one. Two covers every real compound suffix.

    Both parts must be extensions. ``archive.tar.gz`` compounds, and
    ``…darwin13.4.0-ar`` does not, because neither ``4`` nor ``0-ar`` is a kind of file.

    The Dictionary can also declare the whole spelling as a format. ``declared``
    holds those spellings, casefolded and without a leading dot. A format whose name
    is written in two parts then compounds whatever its left half looks like, and a
    laboratory's pack can declare its own. The final part must still be an extension,
    because the compound only ever adds to what the last dot says.
    """
    parts = [part.casefold() for part in PurePosixPath(name).suffixes[-2:]]
    if len(parts) < 2 or not is_extension(parts[-1].removeprefix(".")):
        return ""
    whole = "".join(parts)
    if is_extension(parts[0].removeprefix(".")) or whole.removeprefix(".") in declared:
        return whole
    return ""


def extension_of(name: str, declared: Collection[str] = ()) -> tuple[str, str]:
    """Return the extension and the compound extension an entry stores for this name.

    Lower-cased and without a leading dot, which is the form the Index holds and every rule
    reading the extension slot compares. One function answers both, because the two answer one
    question (what does this name say its type is) and :func:`is_extension` decides both.

    A declared compound never changes the extension. ``plane.ome.tif`` stores ``tif`` and
    ``ome.tif``, so every rule reading a format from the extension slot reads the TIFF it always
    read.
    """
    last = PurePosixPath(name).suffix.casefold().removeprefix(".")
    extension = last if is_extension(last) else ""
    return extension, compound_extension(name, declared).removeprefix(".")


def _entry_type(mode: int) -> EntryType:
    """Return the schema's entry type.

    The schema's enumeration is deliberately small, because a type with eight
    members that six of the engine never distinguishes is six members of noise.
    The precise kind of anything else is preserved as an observation instead, read
    by ``census.object_kind``, so nothing is lost.
    """
    if stat_module.S_ISLNK(mode):
        return EntryType.SYMLINK
    if stat_module.S_ISDIR(mode):
        return EntryType.DIRECTORY
    if stat_module.S_ISREG(mode):
        return EntryType.FILE
    return EntryType.OTHER


#: Whether a directory's metadata must be read again rather than taken from the
#: enumeration that found it.
#:
#: Windows caches directory metadata and updates it late, so the same unchanged folder reports two
#: different modification times. ``DirEntry.stat`` costs no system call there because it reuses
#: what ``FindFirstFile`` already returned, and what that carries for a *directory* is the
#: parent's cached copy, which NTFS refreshes lazily. Two censuses of a tree nobody touched read a
#: folder's ``modified_ns`` as its creation time and then, moments later, as its true last-write
#: time. On a Windows CI run, three folders two milliseconds apart moved the folder-interval
#: observation by exactly the same two milliseconds, which is the second number agreeing with the
#: first.
#:
#: This breaks reproducibility for a reason outside the engine, and the only answer is to stop
#: trusting the cache: ``os.stat`` opens a handle and asks the filesystem. The rule applies to
#: directories only, because a file's timestamps are settled when it is written while a
#: directory's change whenever a child does. It applies on Windows only, because
#: ``DirEntry.stat`` already makes a real call on POSIX, so the rule costs nothing there and one
#: system call per directory on Windows.
#:
#: The same reason makes ``crosses_filesystem`` take a second look: ``DirEntry.stat`` on Windows
#: omits the volume serial number.
_RESTAT_DIRECTORIES = gives().folder_listing_stale


def _creation_time(result: os.stat_result) -> tuple[int | None, TimeSource]:
    """Return a creation time and, crucially, where it came from.

    Creation time is the least trustworthy field the filesystem offers. It does
    not exist everywhere. ``st_ctime`` means metadata change on POSIX and
    creation on Windows, which is a genuine cross-platform trap. Copying a
    file usually resets it, so a dataset moved to new storage can have every file
    created on the migration date.

    Recording the provenance alongside the value lets a rule decide
    whether to trust it, and stops a metadata-change time from being treated as a
    creation time because it happened to be in that column.

    ``st_birthtime_ns`` does not exist on macOS, so reading only that field
    would lose every creation time in the pool. CPython exposes the nanosecond
    variant on some platforms and only the float ``st_birthtime`` on others, and
    Darwin is the second kind: ``hasattr(stat, "st_birthtime_ns")`` is false there
    while ``st_birthtime`` returns a real value. Reading only the nanosecond field
    would record ``unavailable`` and a null for every entry, on the one platform where
    the filesystem knows when the file was made.

    The float carries microsecond resolution rather than nanosecond. A value slightly
    coarser than the column can hold is worth far more than no value at all.
    Both readings report ``BIRTHTIME``, because the resolution is a fact about
    the platform and the provenance is a fact about the time.
    """
    birthtime = getattr(result, "st_birthtime_ns", None)
    if birthtime:
        return int(birthtime), TimeSource.BIRTHTIME
    seconds = getattr(result, "st_birthtime", None)
    if seconds:
        # Rounded rather than truncated, and deterministic either way: the same
        # float produces the same integer on every run, which is what the
        # reproducibility gate needs of it.
        return round(seconds * 1_000_000_000), TimeSource.BIRTHTIME
    if gives().ctime_is_creation and getattr(result, "st_ctime_ns", None) is not None:
        return int(result.st_ctime_ns), TimeSource.CTIME
    return None, TimeSource.UNAVAILABLE


@dataclass
class CensusCounts:
    """What one census did, in numbers the run record carries."""

    entries: int = 0
    directories: int = 0
    files: int = 0
    symlinks: int = 0
    other: int = 0
    bytes_seen: int = 0
    errors: int = 0
    tombstones: int = 0
    #: Members of containers carried from the last run, which no census walks.
    carried: int = 0
    #: Files whose stat fingerprint was the last run's, whose dive readings a walk keeps,
    #: by entry identifier.
    unchanged: set[str] = field(default_factory=set)
    reuse: Counter[ReuseDecision] = field(default_factory=Counter)
    #: Files by extension: how many end in a pack's accepting spellings is stated on
    #: the run record, which the next reading of the names decides a pack's default from.
    extensions: Counter[str] = field(default_factory=Counter)
    #: Names left out on purpose, by root, reason, pattern and type.
    excluded: Counter[tuple[str, ExclusionReason, str | None, EntryType]] = field(
        default_factory=Counter
    )


@dataclass
class CensusRecords:
    """The records one directory produced."""

    entries: list[Entry] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    name_shapes: list[NameShape] = field(default_factory=list)
    bindings: list[DictionaryBinding] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    #: Directories recognised as the engine's own, whether or not they were indexed.
    recognised: list[RecognisedPath] = field(default_factory=list)
    #: Names this directory's listing held and the walk left out, by reason, pattern
    #: and type.
    excluded: Counter[tuple[ExclusionReason, str | None, EntryType]] = field(
        default_factory=Counter
    )


@dataclass(frozen=True, slots=True)
class _Pending:
    """A directory waiting to be enumerated."""

    path: Path
    relative: str
    entry: str
    depth: int
    #: The project this run writes, walked because it is the root or holds a person's files
    #: beside its own: each name it writes is left out.
    own: bool = False


def device_as_stored(device: int) -> int:
    """A device number as the Index stores it: the signed reading of its 64 bits.

    The operating systems hand the number over in two shapes. macOS's ``dev_t`` is signed and may
    be negative. Windows gives a volume's serial number unsigned, and on some volumes it lies at or
    above 2**63, which a signed column refuses. Reading the same 64 bits as signed keeps every
    value distinct and equal to itself, which is all a device number is used for. A rebind replays
    the one-file-system guard with the stored root's number against its children's, stored the
    same way. A walk compares live numbers only.
    """
    return device - (1 << 64) if device >= (1 << 63) else device


class _Walker:
    """Walks one root, producing records directory by directory."""

    def __init__(
        self,
        *,
        root: RootDeclaration,
        mount: Path,
        settings: Settings,
        run_id: str,
        previous: dict[str, PreviousRecord],
        extractor_registry: Registry[Any] | None,
        token: CancellationToken | None,
        vocabulary: DictionaryStore | None = None,
        normaliser: Normaliser | None = None,
        installation: frozenset[tuple[int, int]] = frozenset(),
        rules: Mapping[str, ScanRule] | None = None,
        own_project: OwnProject | None = None,
    ) -> None:
        self.root = root
        #: The project this run writes, left out wherever it lies, and its path as
        #: the walk spells the paths it lists, compared once per name.
        self.own_project = own_project
        self.own_path = None if own_project is None else own_project.text
        #: The census's own rules, bound from the Rulebook once for the run.
        self.rules = scan_rules(CENSUS_RULE_IDS) if rules is None else rules
        #: The device and inode of each directory the engine is installed from.
        self.installation = installation
        self.mount = mount
        self.settings = settings
        self.run_id = run_id
        self.previous = previous
        self.extractor_registry = extractor_registry
        self.token = token
        # Built once by the caller and shared across every worker. The structures
        # inside both are written at construction and only read afterwards, which
        # is what makes sharing them across up to eight threads safe.
        self.vocabulary = vocabulary
        self.normaliser = normaliser
        #: The two-part spellings the vocabulary declares as formats. Empty where the
        #: census reads no vocabulary, which leaves the both-halves rule alone deciding.
        self.compounds = frozenset() if vocabulary is None else declared_compounds(vocabulary)
        self.counts = CensusCounts()
        self.seen: set[str] = set()
        #: The folders a link's chain has found real, so each is looked at once.
        self.link_folders: set[str] = set()
        self.root_device: int | None = None
        #: Direct child counts per directory, keyed by relative path. Filled
        #: as directories complete, in whatever order they complete, and read
        #: only after the walk finishes, so concurrency cannot affect the result.
        self.direct_files: Counter[str] = Counter()
        self.direct_directories: Counter[str] = Counter()
        self.direct_bytes: Counter[str] = Counter()
        #: Directory children of each directory, for the bottom-up rollup.
        self.child_directories: dict[str, list[str]] = {}
        self.directory_paths: set[str] = set()
        #: Directories whose own listing is not the whole of what they hold: one that could
        #: not be listed at all, and one where a listed child could not be ``stat``-ed and so
        #: has no entry. A closed-world count is licensed only by a finished enumeration,
        #: and these are the directories where it did not finish (for example a folder the
        #: account cannot enter). The census does not state their direct counts, or any
        #: total above them, and does not state the part that was seen in their place.
        self.incompletely_listed: set[str] = set()
        #: What each directory knows about its direct children, for the
        #: organisation characteriser. It holds five small fields per child and not
        #: whole entries, because this runs over every folder in a pool and memory
        #: is the figure to watch.
        self.folder_children: dict[str, list[Child]] = {}
        #: The name facts a folder's own authorship reading needs, keyed by
        #: relative path. Gathered during the walk because the atoms are already
        #: computed there and tokenising twice would double the cost.
        self.folder_names: dict[str, tuple[int, int, bool]] = {}
        #: Skeleton digest per entry, so a folder can read its children's shapes
        #: without holding their shape records. Filled where shapes are on.
        self.shape_digests: dict[str, str] = {}
        #: A name's short date that its neighbours settled, by entry, from reading the name until
        #: the entry's clock is final. The census records its evidence only where it stands.
        self.settled_readings: dict[str, SettledDate] = {}
        #: Directory entries, kept because the characteriser reads their names
        #: and their intervals after the walk. Directories only: a pool has far
        #: fewer of them than files, so this is a small fraction of the census's
        #: peak rather than a doubling of it.
        self.directory_entries: dict[str, Entry] = {}
        #: Identifiers of every negative-lexicon sense, resolved once. That section
        #: records what a person thought of a file, so a hit in a
        #: folder's own name is evidence that a human typed it. The lexicon works as an
        #: authorship detector at no cost.
        self.doubt_terms: frozenset[str] = (
            frozenset(
                vocabulary.term_id(sense)
                for sense in vocabulary.senses()
                if sense.section == NEGATIVE_LEXICON
            )
            if vocabulary is not None
            else frozenset()
        )

    # -- exclusion --------------------------------------------------------

    def exclusion(self, relative: str, name: str) -> tuple[ExclusionReason, str | None] | None:
        """Why a path is out of scope for this scan, or None where it is in scope.

        The reason, and for a pattern the first one in the settings' order that matched,
        because a count of what was left out is only useful to a person if it says which
        of their settings left it out.
        """
        if not self.settings.scan.include_hidden and name.startswith("."):
            return ExclusionReason.HIDDEN, None
        for pattern in self.settings.scan.exclude_globs:
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern):
                return ExclusionReason.EXCLUDE_GLOB, pattern
        return None

    def excluded(self, relative: str, name: str) -> bool:
        """Whether a path is out of scope for this scan."""
        return self.exclusion(relative, name) is not None

    def this_project(self, relative: str) -> RecognisedPath:
        """What the run record says of the project it writes, or of one name of it, left out."""
        return RecognisedPath(
            root_id=self.root.root_id,
            relative_path=relative,
            recognised_as=RecognisedAs.THIS_PROJECT,
        )

    def holds_a_persons_files(self, path: Path, relative: str) -> bool:
        """Whether the project's folder holds anything but its own names that a census reads."""
        return holds_other_names(
            path, lambda name: self.exclusion(f"{relative}/{name}", name) is None
        )

    # -- record construction ----------------------------------------------

    def identifier(self, relative: str) -> str:
        return entry_id(
            self.root.root_id,
            relative,
            (),
            case_sensitivity=self.root.case_sensitivity,
        )

    def build_entry(
        self,
        *,
        name: str,
        relative: str,
        parent: str | None,
        depth: int,
        result: os.stat_result,
        symlink_target: str | None,
        symlink_state: SymlinkState = SymlinkState.NOT_A_LINK,
        kind: EntryType | None = None,
    ) -> Entry:
        """One entry from what a ``stat`` said, or, for a listing, from what the listing stated.

        ``kind`` is the entry's type where no mode says it. A listing states that a
        path is a file or a folder and gives no mode, and a mode of 0 would read as neither. Every
        fact that the result does not carry is absent on the entry, never a zero.
        """
        mode = getattr(result, "st_mode", None)
        if kind is None:
            if mode is None:
                message = f"{relative}: neither a mode nor a stated kind says what this entry is"
                raise ValueError(message)
            kind = _entry_type(int(mode))
        facts = stat_facts(result)
        created_ns, created_source = _creation_time(result)
        identifier = self.identifier(relative)

        is_directory = kind is EntryType.DIRECTORY
        extension, compound = ("", "") if is_directory else extension_of(name, self.compounds)
        blocks = getattr(result, "st_blocks", None)

        earlier = self.previous.get(identifier)
        decision = ReuseDecision.NO_PREVIOUS_RECORD
        if kind is EntryType.FILE:
            policy = processing_policy(
                extensions=frozenset(value for value in (extension, compound) if value),
                size_bytes=facts.size_bytes or 0,
                settings=self.settings,
                extractor_registry=self.extractor_registry,
            )
            decision = reuse_decision(earlier, facts=facts, policy=policy, settings=self.settings)
            processing = policy.fingerprint
            # Only files are reuse candidates, so only files are counted. A
            # directory reported as a reuse miss would make every run look as
            # though it had reprocessed work it never considered.
            self.counts.reuse[decision] += 1
        else:
            # Directories and symlinks are never opened, so there is no
            # processing policy to fingerprint. A constant marks them as
            # deliberately outside that machinery rather than missing from it.
            processing = "filesystem-v1"

        entry = Entry(
            entry_id=identifier,
            entry_kind=EntryKind.FILESYSTEM,
            root_id=self.root.root_id,
            relative_path=relative,
            name=name,
            parent_entry_id=parent,
            depth=depth,
            entry_type=kind,
            extension=extension,
            compound_extension=compound,
            size_bytes=facts.size_bytes,
            allocated_bytes=int(blocks * 512) if blocks is not None else None,
            inode=int(getattr(result, "st_ino", None) or 0) or None,
            device=device_as_stored(int(getattr(result, "st_dev", None) or 0)) or None,
            mode=None if mode is None else int(mode),
            uid=int(getattr(result, "st_uid", -1)) if hasattr(result, "st_uid") else None,
            gid=int(getattr(result, "st_gid", -1)) if hasattr(result, "st_gid") else None,
            nlink=_absent_or_whole(getattr(result, "st_nlink", None)),
            created_ns=created_ns,
            created_time_source=created_source,
            modified_ns=facts.modified_ns,
            changed_ns=facts.changed_ns,
            symlink_target=symlink_target,
            symlink_state=symlink_state,
            # A census has looked at the filesystem and nothing else. It has not
            # examined content, so every question about content is unexamined,
            # which is a state and not a null.
            resolution_status=ResolutionStatus.UNEXAMINED,
            is_deleted=False,
            # First seen means first seen. Carried forward from the previous
            # record, so an entry that has been there since the first scan says
            # so rather than claiming to be new on every scan.
            first_seen_run=(earlier.first_seen_run if earlier else None) or self.run_id,
            last_seen_run=self.run_id,
            stat_fingerprint=stat_fingerprint(facts),
            processing_fingerprint=processing,
            scan_outcome=ScanOutcome.METADATA_ONLY,
        )
        return entry

    def contained(self, directory: str, entries: Collection[str]) -> tuple[str, ...]:
        """Return what a directory contains, as the containment rule reads its listing."""
        verdict = self.rules[CENSUS_CONTAINMENT].evaluate(
            Listing(directory=directory, entries=tuple(entries))
        )
        if not isinstance(verdict, tuple):
            message = f"{CENSUS_CONTAINMENT} answered {type(verdict).__name__}, not entries"
            raise TypeError(message)
        return verdict

    def containment(self, parent_entry: str, child_entry: str) -> Assertion:
        """One containment edge, entailed and attributable.

        Entailed because enumeration either found the child inside the parent or
        it did not; there is no interpretation between the two.
        """
        bound = self.rules[CENSUS_CONTAINMENT]
        rule, version = bound.rule_id, bound.version
        scope = Scope(ScopeKind.ROOT, self.root.root_id)
        return Assertion(
            edge_id=edge_id(
                source=parent_entry,
                target=child_entry,
                primitive=Primitive.CONTAINS,
                rule_id=rule,
                rule_version=version,
                scope=scope,
            ),
            run_id=self.run_id,
            source_id=parent_entry,
            target_id=child_entry,
            primitive=Primitive.CONTAINS,
            specialization="directory_of",
            rule_id=rule,
            rule_version=version,
            scope=scope,
            inference_tier=InferenceTier.ENTAILED,
        )

    def object_kind(self, entry: Entry, mode: int) -> Observation | None:
        """Record the precise kind of an object the schema calls ``other``, as its rule reads it."""
        bound = self.rules[CENSUS_OBJECT_KIND]
        precise = bound.evaluate(mode)
        if not isinstance(precise, str):
            return None
        return Observation(
            observation_id=observation_id(
                entry=entry.entry_id,
                observer="census",
                observer_version=bound.version,
                key="filesystem.object_kind",
            ),
            entry_id=entry.entry_id,
            run_id=self.run_id,
            observer="census",
            observer_version=bound.version,
            observed_at=_now(),
            key="filesystem.object_kind",
            value_kind=ValueKind.TEXT,
            value_text=precise,
            extraction_tier=ExtractionTier.LITERAL,
            source_scope=SourceScope.HEADER,
        )

    def link_target(self, entry: Entry) -> Observation | None:
        """Characterise a link whose target lies outside the root, from its text."""
        if entry.symlink_state is not SymlinkState.OUTSIDE_ROOT or not entry.symlink_target:
            return None
        return Observation(
            observation_id=observation_id(
                entry=entry.entry_id,
                observer="census",
                observer_version=LINK_TARGET_VERSION,
                key=LINK_TARGET_KEY,
            ),
            entry_id=entry.entry_id,
            run_id=self.run_id,
            observer="census",
            observer_version=LINK_TARGET_VERSION,
            observed_at=_now(),
            key=LINK_TARGET_KEY,
            value_kind=ValueKind.STRUCTURE,
            value_structure=canonical_json(characterise_link_target(entry.symlink_target)),
            extraction_tier=ExtractionTier.LITERAL,
            source_scope=SourceScope.HEADER,
        )

    def crosses_filesystem(self, path: Path, result: os.stat_result) -> bool:
        """Whether descending into this directory would leave the root's filesystem.

        The guard stops a walk wandering into a mounted backup volume or a network
        share nobody meant to index.

        Windows needs a second look. ``DirEntry.stat`` reuses
        the data directory enumeration already returned, which is most of the
        saving on a latency-bound share. On Windows that data does not carry the
        volume serial number, so the device identifier comes back as zero. Taken
        at face value it differs from the root's, so the guard would reject every
        subdirectory and the walk would find only the top level. Where the value is
        missing, one real ``stat`` resolves it: once per directory, only when the
        guard is enabled, and never for a file.
        """
        if not self.settings.scan.one_file_system or self.root_device is None:
            return False
        device = int(result.st_dev)
        if not device:
            try:
                device = int(path.stat(follow_symlinks=False).st_dev)
            except OSError:
                # Unresolvable. Descending is the recoverable mistake; refusing
                # silently loses part of the data pool.
                return False
        return bool(device) and device != self.root_device

    def link_leaves_root(self, link: str, target: str) -> bool:
        """Whether a link's target, as written, lies outside this walker's root."""
        return outside_root(
            link,
            target,
            str(self.mount),
            case_insensitive=self.root.case_sensitivity is CaseSensitivity.INSENSITIVE,
        )

    def resolve_link(self, path: str, target: str) -> SymlinkState:
        """Whether what a symlink names is there, followed hop by hop inside the root only.

        This is its own method because of its failure modes. A target that is gone is
        the answer and not an error: the link is real and what it names is not, which is a
        fact worth recording and not a fault of the scan. A loop, or a chain longer than
        :data:`LINK_HOPS`, is never chased. Anything else, such as a permission refused or a mount
        that did not answer, leaves the question unanswered. Saying ``unreadable`` is the honest
        report there, where saying ``broken`` would assert an absence nobody observed.

        The method never calls ``stat`` down the chain. A ``stat`` that follows the links can
        pass through a link inside the root to one that leaves it, and on a share it can wait
        out a dead server's timeout. :func:`follow_link_chain` reads each hop's text and stops at
        the first hop leaving the root, which is ``outside_root``, read from its text.

        The method does not record a broken link as a scan error. An error is something a person
        can fix about the environment. A link whose target has been archived is the pool being
        what it is.
        """
        return follow_link_chain(
            path,
            target,
            str(self.mount),
            leaves=self.link_leaves_root,
            case_insensitive=self.root.case_sensitivity is CaseSensitivity.INSENSITIVE,
            folders_known=self.link_folders,
        )

    def storable(self, entry: Entry, records: CensusRecords) -> Entry:
        """Return the entry with every integer its column cannot store made absent, and say so.

        The check runs before a flush and does not wait for one to fail. A value the
        operating system hands over that the Index cannot hold is recorded against the
        entry as a scan error, and the entry is indexed without it, so one strange file
        is one entry's fact and not a census that publishes nothing. A column
        that cannot be absent is a defect in the engine and stops the census, loudly.

        This is a comprehension failure, where every other census error is access. Nothing is
        wrong with the mount or the permissions. The engine could not represent what it
        was told, and the fix is a wider column, which is coverage.
        """
        spec = table_for(Entry)
        violations = integer_range_violations(entry, spec)
        if not violations:
            return entry
        for column, value in violations:
            if not spec.schema.field(column).nullable:
                message = f"{column} {value} is outside what the Index stores and cannot be absent"
                raise OverflowError(message)
            self.counts.errors += 1
            records.errors.append(
                ScanError(
                    entry_id=entry.entry_id,
                    relative_path=entry.relative_path,
                    operation=f"stat.{column}",
                    error_type="OutOfRange",
                    message=(
                        f"the operating system reported {column} {value}, which the Index "
                        f"cannot store; the entry is indexed without it"
                    ),
                    run_id=self.run_id,
                    failure_class=FailureClass.COMPREHENSION,
                )
            )
        return entry.model_copy(update={column: None for column, _ in violations})

    def error(self, relative: str, operation: str, exc: OSError) -> ScanError:
        """Errors are data. A scan that discards them reports a cleaner and less
        true picture than the one it found.

        Every one of these is an access failure, and saying so routes the fix. The census never
        opens a file. Everything that can go wrong here is a mount that is not there, a
        permission that is not granted, or a file that vanished between the enumeration and the
        ``stat``. All three are properties of the environment, and none is a property of a rule.
        A failure rate computed over them says nothing about the engine's interpretation.
        """
        self.counts.errors += 1
        return ScanError(
            entry_id=self.identifier(relative),
            relative_path=relative,
            operation=operation,
            error_type=type(exc).__name__,
            message=str(exc),
            run_id=self.run_id,
            failure_class=FailureClass.ACCESS,
        )

    # -- the walk ---------------------------------------------------------

    def kept_content(self, entry: Entry) -> Entry:
        """Carry what a dive read of a file unchanged since the last run.

        Every stat fact that the fingerprint holds must be the last run's: the kind, the size,
        the modification and the change times. The outcome and a clock read out of the content
        are carried onto the entry, a content clock outranking the name's own as it does in a
        dive, and the file is counted so the run keeps its readings. If any fact differs, the
        dive's word on the file is dropped with it.
        """
        earlier = self.previous.get(entry.entry_id)
        if (
            not self.settings.scan.keep_dive_readings
            or earlier is None
            or entry.entry_type is not EntryType.FILE
            or earlier.stat_fingerprint != entry.stat_fingerprint
        ):
            return entry
        self.counts.unchanged.add(entry.entry_id)
        carried: dict[str, object] = {}
        if earlier.scan_outcome is not ScanOutcome.METADATA_ONLY:
            carried["scan_outcome"] = earlier.scan_outcome
            # The depth it was read at travels with its readings.
            carried["extraction_profile"] = earlier.extraction_profile
        if earlier.event_time is not None:
            instant, source, tier = earlier.event_time
            if TimeSource(source) in _CONTENT_CLOCKS:
                carried.update(
                    event_time_ns=instant,
                    event_time_source=TimeSource(source),
                    event_time_tier=ClockTier(tier),
                )
        # A move no dive has checked yet stays on the file, with the readings it carried, so the
        # next dive checks it however many walks come first. So does a move that only a sample
        # confirmed. An older build confirmed moves that way and carried a whole-file digest
        # unchecked, which the next dive compares. The marker stays until a dive has compared
        # that digest. A settled move is in the history that the earlier run keeps, and is not
        # restated.
        if earlier.move is not None and MovedBy(earlier.move[1]) in WHOLE_DIGEST_UNCHECKED_MOVES:
            source_id, how, at = earlier.move
            carried.update(moved_from=source_id, moved_by=MovedBy(how), moved_at_ns=at)
        return entry.model_copy(update=carried) if carried else entry

    def observe_series(self, siblings: Sequence[Entry]) -> None:
        """Read one folder's series of short dates before any name in it."""
        if self.normaliser is not None and self.vocabulary is not None:
            observe_series(siblings, normaliser=self.normaliser, store=self.vocabulary)

    def date_order_evidence(self, entry: Entry, records: CensusRecords) -> None:
        """Record which neighbours settled the entry's clock, where its final clock is that date.

        This applies where the name's settled date is the clock the entry ends with, and nowhere
        else. A clock read out of the content outranks the name's, and then the name's reading
        explains nothing about the clock. A walk and a rebind hand over the reading they made.
        A partial rebind carries a name's clock without reading the name, so its neighbours are
        read again here, and all three record the same evidence.
        """
        settled = self.settled_readings.pop(entry.entry_id, None)
        if (
            self.normaliser is None
            or self.vocabulary is None
            or entry.event_time_source is not TimeSource.FILENAME
            or entry.event_time_ns is None
        ):
            return
        if settled is None:
            stem, _ = split_extension(entry, store=self.vocabulary)
            settled = settled_short_date(entry, stem, self.normaliser)
        if settled is None:
            return
        day = datetime.fromtimestamp(entry.event_time_ns / 1e9, tz=UTC).date().isoformat()
        if day == settled.date:
            records.observations.append(date_order_observation(entry, settled, run_id=self.run_id))

    def read_name_of(self, entry: Entry, records: CensusRecords) -> Entry:
        """Tokenise one entry's name into the records, and return the entry dated.

        The function runs for every entry including a reused one, because each run writes a
        complete snapshot and not a delta, and a reused entry's row is written
        out in full anyway. Reuse governs whether content work is redone. A
        name costs microseconds and there is nothing to skip.

        The function returns the entry and does not mutate it, because a date written in
        a filename is an event clock and belongs on the row, and ``Entry`` is
        frozen. The clock arrives with its source and its tier. That lets a parsed date sit
        next to a ``stat`` reading without confusing the two: the row says which is which.
        """
        if self.normaliser is None or self.vocabulary is None:
            return entry
        reading = read_name(
            entry,
            run_id=self.run_id,
            normaliser=self.normaliser,
            store=self.vocabulary,
            with_shape=self.settings.dictionary.record_name_shapes,
            with_bindings=self.settings.dictionary.bind_terms,
        )
        records.components.extend(reading.components)
        records.bindings.extend(reading.bindings)
        if reading.settled is not None:
            self.settled_readings[entry.entry_id] = reading.settled
        if reading.shape is not None:
            records.name_shapes.append(reading.shape)
            self.shape_digests[entry.entry_id] = reading.shape.skeleton_hash
        if self.settings.dictionary.characterise_organisation:
            self.folder_names[entry.relative_path] = _name_facts(reading, self.doubt_terms)
        if reading.event_time_ns is None:
            return entry
        return entry.model_copy(
            update={
                "event_time_ns": reading.event_time_ns,
                "event_time_source": reading.event_time_source,
                "event_time_tier": CLOCK_TIER_OF[reading.event_time_source],
            }
        )

    def scan_directory(self, pending: _Pending) -> tuple[CensusRecords, list[_Pending]]:
        """Enumerate one directory. Never raises for anything it can record."""
        records = CensusRecords()
        children: list[_Pending] = []
        try:
            with os.scandir(pending.path) as iterator:
                found = list(iterator)
        except OSError as exc:
            records.errors.append(self.error(pending.relative, "scandir", exc))
            return records, children

        listed: list[str] = []
        #: Built in the listing's order and named once the whole listing is known, since a
        #: folder's series of short dates is read from every sibling.
        built: list[Entry] = []
        for item in found:
            relative = item.name if pending.relative == "." else f"{pending.relative}/{item.name}"
            # The project this run writes never enters its census, from its
            # first run and wherever it lies. In its own folder each name it writes is left out,
            # before anything else is asked of it, so nothing of it is counted either.
            if pending.own and (
                is_project_name(item.name) or spelled_otherwise(pending.path, item.name)
            ):
                records.recognised.append(self.this_project(relative))
                continue
            own = item.path == self.own_path
            exclusion = self.exclusion(relative, item.name)
            if exclusion is not None and own:
                # The person's exclusions and the hidden-name setting hold for it too. The
                # run record names it, and nothing in it is walked.
                records.recognised.append(self.this_project(relative))
                continue
            if exclusion is not None:
                records.excluded[(*exclusion, _listed_type(item))] += 1
                continue
            depth = pending.depth + 1
            if self.settings.scan.max_depth is not None and depth > self.settings.scan.max_depth:
                records.excluded[(ExclusionReason.MAX_DEPTH, None, _listed_type(item))] += 1
                continue
            try:
                # Reuses the data directory enumeration already returned where
                # the platform provides it, which is most of the saving on a
                # latency-bound share. On Linux the one call is ``statx``, for the
                # birth time Python's ``stat`` does not give (see core/platform).
                result = lstat_entry(item)
                if _RESTAT_DIRECTORIES and item.is_dir(follow_symlinks=False):
                    # Keeps the cached reading where a handle cannot be opened,
                    # rather than losing the entry. A folder that enumeration can
                    # see and a handle cannot open is still a folder, and
                    # recording it with a timestamp that may be stale is a
                    # smaller error than reporting a data pool with fewer
                    # directories than it has.
                    with suppress(OSError):
                        result = os.stat(item.path, follow_symlinks=False)
            except OSError as exc:
                records.errors.append(self.error(relative, "stat", exc))
                continue

            own = own or (
                self.own_project is not None and self.own_project.is_folder(item.path, result)
            )
            if own and not self.holds_a_persons_files(Path(item.path), relative):
                records.recognised.append(self.this_project(relative))
                continue

            # Recognised before its entry is built, so nothing inside a directory the
            # engine wrote enters the Index unless the settings ask for it.
            recognised = (
                None
                if own
                else recognise_directory(Path(item.path), result, installation=self.installation)
            )
            if recognised is not None:
                indexed = (
                    recognised is not RecognisedAs.INSTALLATION
                    and self.settings.scan.index_recognised_outputs
                )
                records.recognised.append(
                    RecognisedPath(
                        root_id=self.root.root_id,
                        relative_path=relative,
                        recognised_as=recognised,
                        indexed=indexed,
                    )
                )
                if not indexed:
                    continue

            target: str | None = None
            link_state = SymlinkState.NOT_A_LINK
            if stat_module.S_ISLNK(result.st_mode):
                link_state = SymlinkState.UNREADABLE
                try:
                    target = os.readlink(item.path)
                except OSError as exc:
                    records.errors.append(self.error(relative, "readlink", exc))
                if target is not None and self.link_leaves_root(item.path, target):
                    # Never stat-ed, whatever the setting: a census reads nothing outside
                    # the roots it was given.
                    link_state = SymlinkState.OUTSIDE_ROOT
                elif target is not None and self.settings.scan.resolve_symlinks:
                    link_state = self.resolve_link(item.path, target)

            entry = self.storable(
                self.build_entry(
                    name=item.name,
                    relative=relative,
                    parent=pending.entry,
                    depth=depth,
                    result=result,
                    symlink_target=target,
                    symlink_state=link_state,
                ),
                records,
            )
            built.append(entry)
            listed.append(entry.entry_id)
            if entry.entry_type is EntryType.OTHER:
                kind = self.object_kind(entry, int(result.st_mode))
                if kind is not None:
                    records.observations.append(kind)
            characterised = self.link_target(entry)
            if characterised is not None:
                records.observations.append(characterised)

            if entry.entry_type is EntryType.DIRECTORY and not self.crosses_filesystem(
                Path(item.path), result
            ):
                children.append(
                    _Pending(
                        path=Path(item.path),
                        relative=relative,
                        entry=entry.entry_id,
                        depth=depth,
                        own=own,
                    )
                )
        self.observe_series(built)
        for entry in built:
            # Checked again as it is stored: reading the name may date the entry
            # and a kept reading may carry a clock, both after the first check. A date that a
            # name spells and that no instant can hold would stop a census.
            final = self.storable(self.kept_content(self.read_name_of(entry, records)), records)
            self.date_order_evidence(final, records)
            records.entries.append(final)
        # One verdict per directory, once its listing is complete, and not an edge
        # built beside each entry as it was found.
        for child in self.contained(pending.entry, listed):
            records.assertions.append(self.containment(pending.entry, child))
        return records, children


def _reading_day(started_at: str) -> date | None:
    """The day a run starting at ``started_at`` reads names on, or None where it names none."""
    try:
        return date.fromisoformat(started_at[:10])
    except ValueError:
        return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _absent_or_whole(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


def walk_root(
    root: RootDeclaration,
    *,
    settings: Settings,
    run_id: str,
    previous: dict[str, PreviousRecord] | None = None,
    extractor_registry: Registry[Any] | None = None,
    token: CancellationToken | None = None,
    counts_out: CensusCounts | None = None,
    vocabulary: DictionaryStore | None = None,
    normaliser: Normaliser | None = None,
    installation: frozenset[tuple[int, int]] | None = None,
    rules: Mapping[str, ScanRule] | None = None,
    own_project: OwnProject | None = None,
) -> Iterator[CensusRecords]:
    """Walk one root, yielding the records of each directory as it completes.

    Yields rather than returning a list, so a caller can stream records to disk
    and hold a bounded amount in memory regardless of how large the tree is.

    Cancellation is checked between directories, which bounds the delay at
    roughly one directory listing. That is fast enough to feel immediate, and it
    means a unit of work either completes and is recorded or never started, so
    there is never a half-enumerated directory to reason about.

    ``installation`` holds the device and inode of each directory the engine is
    installed from, and ``None`` reads the running engine's own.

    ``rules`` holds the census's own rules bound from a Rulebook, and ``None`` binds
    the shipped ones.

    ``own_project`` is the project the run writes, left out wherever it lies in the root;
    ``None`` leaves out none.
    """
    mount = Path(root.mount_path)
    walker = _Walker(
        root=root,
        mount=mount,
        settings=settings,
        run_id=run_id,
        previous=previous or {},
        extractor_registry=extractor_registry,
        token=token,
        vocabulary=vocabulary,
        normaliser=normaliser,
        installation=installation_directories() if installation is None else installation,
        rules=rules,
        own_project=own_project,
    )
    # The caller's counter, filled in place. The reuse decisions live on the
    # walker and are the one thing a consumer cannot recompute from the records,
    # because a reused entry looks identical to a freshly examined one.
    if counts_out is not None:
        walker.counts = counts_out

    try:
        root_stat = lstat_path(mount)
    except OSError as exc:
        records = CensusRecords()
        records.errors.append(walker.error(".", "lstat", exc))
        yield records
        return
    walker.root_device = int(root_stat.st_dev) or None

    root_entry = walker.build_entry(
        name=mount.name or str(mount),
        relative=".",
        parent=None,
        depth=0,
        result=root_stat,
        symlink_target=None,
    )
    opening = CensusRecords()
    # Dated before it is appended, or the list keeps the undated copy and the
    # root is the one entry in the pool whose filename clock is silently lost; and
    # checked as it is stored, as every other entry is.
    root_entry = walker.storable(walker.read_name_of(root_entry, opening), opening)
    opening.entries.append(root_entry)
    if root_entry.entry_type is EntryType.OTHER:
        kind = walker.object_kind(root_entry, int(root_stat.st_mode))
        if kind is not None:
            opening.observations.append(kind)
    # The root is an entry like any other and must enter the counts and the
    # rollup, or every aggregate about it is missing and the entry total is one
    # short of what was actually found.
    _accumulate(walker, opening)
    yield opening

    # A project made in the folder it reads is walked, and each name it writes left out.
    start = _Pending(
        path=mount,
        relative=".",
        entry=root_entry.entry_id,
        depth=0,
        own=own_project is not None and own_project.is_folder(mount, root_stat),
    )
    with ThreadPoolExecutor(
        max_workers=settings.scan.workers, thread_name_prefix="ingence-census"
    ) as pool:
        outstanding = {pool.submit(walker.scan_directory, start)}
        while outstanding:
            if token is not None and token.cancelled:
                for future in outstanding:
                    future.cancel()
                raise CancelledError(token.reason or "cancelled")
            done, outstanding = wait(outstanding, return_when=FIRST_COMPLETED)
            for future in done:
                records, children = future.result()
                _accumulate(walker, records)
                yield records
                for child in children:
                    outstanding.add(pool.submit(walker.scan_directory, child))

    yield _aggregate_claims(walker)
    yield _organisation_observations(walker, _directory_entries(walker))


class _StoredStat:
    """A ``stat`` result rebuilt from what an entry recorded, for a census replayed offline.

    Carries exactly the fields :meth:`_Walker.build_entry` reads, with the values the walk that
    wrote the entry read, so a replayed entry is the entry a walk of an unchanged tree would
    build. A field the entry recorded as absent is absent here too, which is how
    ``build_entry`` tells a platform without it from a zero.
    """

    def __init__(self, entry: Entry) -> None:
        # Absent stays absent. A stored census always has a mode and both clocks, so this
        # changes nothing a rebind replays. An entry that a listing declared has none of them,
        # and a zero here would be a file modified on 1 January 1970 with a mode that read as
        # neither file nor folder.
        self.st_mode = entry.mode
        self.st_size = entry.size_bytes
        self.st_ino = entry.inode
        self.st_dev = entry.device
        self.st_nlink = entry.nlink
        self.st_mtime_ns = entry.modified_ns
        self.st_ctime_ns = entry.changed_ns
        if entry.allocated_bytes is not None:
            self.st_blocks = int(entry.allocated_bytes) // 512
        if entry.uid is not None:
            self.st_uid = int(entry.uid)
        if entry.gid is not None:
            self.st_gid = int(entry.gid)
        if entry.created_ns is not None and entry.created_time_source is TimeSource.BIRTHTIME:
            self.st_birthtime_ns = int(entry.created_ns)


def replay_root(
    root: RootDeclaration,
    stored: Sequence[Entry],
    *,
    settings: Settings,
    run_id: str,
    reread: frozenset[str] | None = None,
    stored_shapes: Mapping[str, str] | None = None,
    errors: Sequence[ScanError] = (),
    previous: dict[str, PreviousRecord] | None = None,
    extractor_registry: Registry[Any] | None = None,
    token: CancellationToken | None = None,
    counts_out: CensusCounts | None = None,
    vocabulary: DictionaryStore | None = None,
    normaliser: Normaliser | None = None,
    rules: Mapping[str, ScanRule] | None = None,
) -> Iterator[CensusRecords]:
    """Yield what :func:`walk_root` would, from a census's stored entries, touching no root.

    Re-binding without re-walking: about half the time between an answer and its effect can go
    on a walk that the answer does not need, because an answer changes
    what a name means and nothing about the filesystem. So the function replays the entries
    that a census stored through the same walker in place of ``scandir`` and ``stat``. It
    rebuilds each entry from the facts it recorded and reads its name again under the
    vocabulary in force. The rollup, the aggregates and the organisation readings are computed
    by the same functions a walk calls. Nothing here opens, lists or ``stat``-s a path, so it
    runs with the root unmounted.

    A replay cannot find out what a walk would: whether anything changed on
    disk. A replay states the tree as the stored census saw it. A fresh walk is how a person
    asks what is there now. The function carries the census's scan errors, because what could
    not be listed then is still what the stored tree does not hold.
    """
    mount = Path(root.mount_path)
    walker = _Walker(
        root=root,
        mount=mount,
        settings=settings,
        run_id=run_id,
        previous=previous or {},
        extractor_registry=extractor_registry,
        token=token,
        vocabulary=vocabulary,
        normaliser=normaliser,
        installation=frozenset(),
        rules=rules,
    )
    if counts_out is not None:
        walker.counts = counts_out

    by_parent: dict[str | None, list[Entry]] = {}
    for entry in stored:
        if (
            entry.root_id == root.root_id
            and entry.entry_kind is EntryKind.FILESYSTEM
            and not entry.is_deleted
        ):
            by_parent.setdefault(entry.parent_entry_id, []).append(entry)
    tops = by_parent.get(None, [])
    if len(tops) != 1:
        message = (
            f"a replay needs the stored census's root entry for {root.root_id}, and found "
            f"{len(tops)}; a run holding no census of this root cannot be re-bound"
        )
        raise ValueError(message)

    def rebuilt(entry: Entry, parent: str | None, records: CensusRecords) -> Entry:
        # A link's place against the root is read from its stored text, as a walk reads it;
        # whether an inside target exists is what the stored census found.
        state = entry.symlink_state
        if entry.symlink_target is not None and walker.link_leaves_root(
            str(mount / entry.relative_path), entry.symlink_target
        ):
            state = SymlinkState.OUTSIDE_ROOT
        built = walker.build_entry(
            name=entry.name,
            relative=entry.relative_path,
            parent=parent,
            depth=entry.depth,
            result=_StoredStat(entry),  # type: ignore[arg-type]
            symlink_target=entry.symlink_target,
            symlink_state=state,
            kind=entry.entry_type if entry.mode is None else None,
        )
        # The creation time is carried as recorded, never recomputed through a float.
        built = built.model_copy(
            update={
                "created_ns": entry.created_ns,
                "created_time_source": entry.created_time_source,
            }
        )
        if reread is not None and entry.entry_id not in reread:
            # A partial rebind carries this name's reading. Its components,
            # bindings and shape are copied from the parent run afterwards, and its shape's
            # digest and its name's clock are what that reading gave, so the folder's
            # organisation is read from the same children a whole rebind reads.
            named = walker.storable(built, records)
            digest = (stored_shapes or {}).get(entry.entry_id)
            if digest is not None:
                walker.shape_digests[entry.entry_id] = digest
            if entry.event_time_ns is not None and entry.event_time_source is not None:
                named = named.model_copy(
                    update={
                        "event_time_ns": entry.event_time_ns,
                        "event_time_source": entry.event_time_source,
                        "event_time_tier": entry.event_time_tier,
                    }
                )
        else:
            named = walker.read_name_of(walker.storable(built, records), records)
        # What a dive read is carried: an outcome, and a clock read out of the
        # content, describe bytes a rebind does not change, where a walk would have to read
        # them again. The name's own clock yields to a content clock, as it does in a dive.
        carried: dict[str, object] = {}
        if entry.scan_outcome is not ScanOutcome.METADATA_ONLY:
            carried["scan_outcome"] = entry.scan_outcome
            # And the depth it was read at, which a deeper dive chooses by.
            carried["extraction_profile"] = entry.extraction_profile
        if entry.event_time_source in _CONTENT_CLOCKS:
            carried.update(
                event_time_ns=entry.event_time_ns,
                event_time_source=entry.event_time_source,
                event_time_tier=entry.event_time_tier,
            )
        # And the move the stored census recorded. A rebind states the tree that census saw, so
        # a move no dive has checked yet still waits for one.
        if entry.moved_from is not None:
            carried.update(
                moved_from=entry.moved_from,
                moved_by=entry.moved_by,
                moved_at_ns=entry.moved_at_ns,
            )
        final = walker.storable(named.model_copy(update=carried) if carried else named, records)
        walker.date_order_evidence(final, records)
        return final

    stored_root = tops[0]
    walker.root_device = stored_root.device
    opening = CensusRecords()
    opening.errors.extend(error.model_copy(update={"run_id": run_id}) for error in errors)
    walker.counts.errors += len(opening.errors)
    root_entry = rebuilt(stored_root, None, opening)
    opening.entries.append(root_entry)
    if root_entry.entry_type is EntryType.OTHER:
        kind = walker.object_kind(root_entry, int(stored_root.mode or 0))
        if kind is not None:
            opening.observations.append(kind)
    _accumulate(walker, opening)
    yield opening

    pending = [root_entry]
    while pending:
        if token is not None and token.cancelled:
            raise CancelledError(token.reason or "cancelled")
        directory = pending.pop()
        records = CensusRecords()
        listed: list[str] = []
        walker.observe_series(by_parent.get(directory.entry_id, ()))
        for child in sorted(by_parent.get(directory.entry_id, ()), key=lambda item: item.name):
            entry = rebuilt(child, directory.entry_id, records)
            records.entries.append(entry)
            listed.append(entry.entry_id)
            if entry.entry_type is EntryType.OTHER:
                kind = walker.object_kind(entry, int(child.mode or 0))
                if kind is not None:
                    records.observations.append(kind)
            characterised = walker.link_target(entry)
            if characterised is not None:
                records.observations.append(characterised)
            if entry.entry_type is EntryType.DIRECTORY:
                pending.append(entry)
        for child_id in walker.contained(directory.entry_id, listed):
            records.assertions.append(walker.containment(directory.entry_id, child_id))
        _accumulate(walker, records)
        yield records

    yield _aggregate_claims(walker)
    yield _organisation_observations(walker, _directory_entries(walker))


#: The observation a census records about a link whose target lies outside its root.
LINK_TARGET_KEY = "link.target"
#: Its version, bumped whenever the same text would be characterised differently.
LINK_TARGET_VERSION = "1"


#: The clocks only a dive reads, which a rebind carries rather than re-reads from the name.
_CONTENT_CLOCKS = frozenset({TimeSource.INSTRUMENT, TimeSource.DOCUMENT})

#: The census's own observers, whose readings a rebind recomputes; every other observer's is
#: a reading of content, and a rebind carries it.
_CENSUS_OBSERVERS = frozenset({"census", "census.organisation"})

#: A link target written as a Windows path: a drive (``C:\``), a UNC share (``\\server``) or the
#: object namespace (``\??\``, which smbfs shows as ``/??/``). Another machine's path by its
#: spelling alone, on any platform, as the mention harvest reads one.
_WINDOWS_TARGET = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|[\\/]\?\?[\\/])")


#: How many links a chain may pass through before it is called unreadable: the
#: smaller of the operating systems' own bounds (macOS's ``MAXSYMLINKS`` is 32, Linux's 40), so a
#: chain the system itself would refuse is refused here too, and a loop is never chased.
LINK_HOPS = 32


def follow_link_chain(
    link: str,
    target: str,
    root: str,
    *,
    leaves: Callable[[str, str], bool],
    case_insensitive: bool,
    windows: bool = os.name == "nt",
    hops: int = LINK_HOPS,
    folders_known: set[str] | None = None,
    lstat: Callable[[str], Any] | None = None,
    readlink: Callable[[str], str] | None = None,
) -> SymlinkState:
    """Where a link's chain ends, followed hop by hop and never past the root.

    Each hop's text is tested with ``leaves`` (:func:`outside_root`, read from the text and
    never from the filesystem) before anything is looked at: the first hop leaving the root makes
    the link ``outside_root``. A hop inside is spelled as :func:`outside_root` spells it (a
    Windows prefix taken off, joined to the link's folder) and walked from the
    root one name at a time with ``lstat``: ``.`` is nothing, ``..`` is one step back to a folder
    already found real (a step above the root leaves it), a folder that is itself a link is one
    more hop, read the same way, so a ``..`` after it is taken from where it leads, as the system
    takes it. A name after a file, or a target written with a trailing separator that is not a
    folder, is ``broken``, as the system says. Nothing outside the root is ever looked at, and
    nothing is followed by the operating system. More hops than ``hops`` (a loop, or a chain the
    system would refuse too) is ``unreadable``.

    ``folders_known`` holds the folders a walk has already found real, so a link to a sibling
    costs one ``lstat``, not one per folder above it. A Windows junction is not a hop: the
    system reports it as a folder and walks through it.
    """
    look = lstat if lstat is not None else os.lstat
    read = readlink if readlink is not None else os.readlink
    known = folders_known if folders_known is not None else set()
    paths = ntpath if windows else posixpath
    folding = windows or case_insensitive
    base = _components(root, windows)
    for _hop in range(hops):
        if leaves(link, target):
            return SymlinkState.OUTSIDE_ROOT
        spelled = _link_path(link, target, windows=windows)
        as_folder = spelled.endswith(("\\", "/") if windows else "/")
        parts = _components(spelled, windows)
        head = parts[: len(base)]
        if len(parts) < len(base) or _folded(head, folding) != _folded(base, folding):
            # Inside by its text's reading, yet not spelled beneath the root: never looked at.
            return SymlinkState.OUTSIDE_ROOT
        rest = parts[len(base) :]
        walked = [root]
        followed = False
        for position, part in enumerate(rest):
            if part == ".":
                continue
            if part == "..":
                if len(walked) == 1:
                    return SymlinkState.OUTSIDE_ROOT
                walked.pop()
                continue
            step = paths.join(walked[-1], part)
            last = position == len(rest) - 1
            if step in known:
                walked.append(step)
                continue
            try:
                found = look(step)
            except (FileNotFoundError, NotADirectoryError):
                return SymlinkState.BROKEN
            except OSError:
                return SymlinkState.UNREADABLE
            mode = found.st_mode
            if stat_module.S_ISLNK(mode):
                try:
                    text = read(step)
                except OSError:
                    return SymlinkState.UNREADABLE
                after = rest[position + 1 :]
                link = step
                target = paths.join(text, *after) if after else text
                if as_folder and not target.endswith(paths.sep):
                    target += paths.sep
                followed = True
                break
            if stat_module.S_ISDIR(mode):
                known.add(step)
            elif not last or as_folder:
                return SymlinkState.BROKEN  # a name after a file, as the system refuses it
            walked.append(step)
        if not followed:
            return SymlinkState.RESOLVES
    return SymlinkState.UNREADABLE


def _link_path(link: str, target: str, *, windows: bool) -> str:
    """A link's target as :func:`outside_root` reads it: Windows' prefixes off, then joined."""
    if windows:
        spelled = target
        for prefix in ("\\\\?\\UNC\\", "\\??\\UNC\\"):
            if spelled.startswith(prefix):
                spelled = "\\\\" + spelled[len(prefix) :]
        for prefix in ("\\\\?\\", "\\??\\"):
            if spelled.startswith(prefix):
                spelled = spelled[len(prefix) :]
        return spelled if ntpath.isabs(spelled) else ntpath.join(ntpath.dirname(link), spelled)
    return target if target.startswith("/") else posixpath.join(posixpath.dirname(link), target)


def _components(path: str, windows: bool) -> list[str]:
    """A path's names in order: its first as written (a drive, or empty for the top), no empties."""
    parts = path.replace("/", "\\").split("\\") if windows else path.split("/")
    return parts[:1] + [part for part in parts[1:] if part]


def _folded(parts: Sequence[str], folding: bool) -> list[str]:
    """Names compared as :func:`outside_root` compares them: NFC, and case folded where it folds."""
    normal = [unicodedata.normalize("NFC", part) for part in parts]
    return [part.casefold() for part in normal] if folding else normal


def outside_root(
    link: str,
    target: str,
    root: str,
    *,
    case_insensitive: bool,
    windows: bool = os.name == "nt",
) -> bool:
    """Whether a link's target, as written, lies outside the root the link was found under.

    The function reads the link's text and never the filesystem, so it can be asked of
    every link without reaching past the root. A census reads nothing outside the roots it was
    given, and a target on an archive server, a detached drive or another person's tree is where
    a ``stat`` would go. A relative target is joined to the link's own folder and normalised
    lexically. An absolute one is compared as written.

    Which spellings are another machine's depends on the platform reading them
    (``windows`` is a parameter, so both are tested everywhere). On POSIX a
    Windows spelling, such as a drive letter, a UNC share or the object namespace, names no path
    this machine can reach, so it is outside. On Windows a drive-letter path is the native
    absolute path and the root is one too, so it is compared like any other, once the
    extended-length and object-namespace prefixes are taken off.
    """
    if windows:
        joined = _link_path(link, target, windows=True)
        candidate = ntpath.normpath(unicodedata.normalize("NFC", joined))
        base = ntpath.normpath(unicodedata.normalize("NFC", root))
        candidate, base = candidate.casefold(), base.casefold()
        return not (candidate == base or candidate.startswith(base.rstrip("\\") + "\\"))
    if _WINDOWS_TARGET.match(target):
        return True
    joined = _link_path(link, target, windows=False)
    candidate = posixpath.normpath(unicodedata.normalize("NFC", joined))
    base = posixpath.normpath(unicodedata.normalize("NFC", root))
    if case_insensitive:
        candidate, base = candidate.casefold(), base.casefold()
    return not (candidate == base or candidate.startswith(base.rstrip("/") + "/"))


def _directory_entries(walker: _Walker) -> dict[str, Entry]:
    """Return the directory entries the characteriser needs, by identifier.

    The directory entries are kept apart from the walk and not accumulated during it. The walk
    yields directories in completion order, and holding every entry to the end would add the
    whole entry table to the census's peak memory. The result holds directories only, and a pool
    has far fewer directories than files.
    """
    return dict(walker.directory_entries)


def _accumulate(walker: _Walker, records: CensusRecords) -> None:
    """Fold one directory's records into the running totals and the rollup.

    The consuming thread calls this and not the worker threads, so the
    counters need no lock and the arithmetic cannot race. The walk yields
    directories in completion order and this runs serially over that stream.
    """
    for entry in records.entries:
        walker.counts.entries += 1
        walker.seen.add(entry.entry_id)
        relative = entry.relative_path
        parent = _parent_relative(relative)

        # Each file's series key, a fact the file list records.
        if entry.entry_type is EntryType.FILE and entry.entry_kind is EntryKind.FILESYSTEM:
            records.observations.extend(_series_observations(walker, entry, parent))

        if walker.settings.dictionary.characterise_organisation and parent is not None:
            walker.folder_children.setdefault(parent, []).append(
                Child(
                    skeleton_hash=walker.shape_digests.get(entry.entry_id),
                    # The event clock where the name supplied one, and the
                    # filesystem's otherwise. Which it was travels with it, so a
                    # burst can say what its own clock is worth rather than
                    # implying they are interchangeable.
                    time_ns=entry.event_time_ns if entry.event_time_ns else entry.modified_ns,
                    clock=(
                        entry.event_time_tier.value
                        if entry.event_time_ns
                        else ClockTier.MTIME.value
                    ),
                    depth=entry.depth,
                    parent=parent,
                    is_directory=entry.entry_type is EntryType.DIRECTORY,
                )
            )

        if entry.entry_type is EntryType.DIRECTORY:
            walker.counts.directories += 1
            walker.directory_paths.add(relative)
            if walker.settings.dictionary.characterise_organisation:
                walker.directory_entries[entry.entry_id] = entry
            walker.child_directories.setdefault(relative, [])
            if parent is not None:
                walker.direct_directories[parent] += 1
                walker.child_directories.setdefault(parent, []).append(relative)
            continue

        if entry.entry_type is EntryType.FILE:
            walker.counts.files += 1
            walker.counts.bytes_seen += entry.size_bytes or 0
            walker.counts.extensions[entry.extension] += 1
        elif entry.entry_type is EntryType.SYMLINK:
            walker.counts.symlinks += 1
        else:
            walker.counts.other += 1
        if parent is not None:
            walker.direct_files[parent] += 1
            walker.direct_bytes[parent] += entry.size_bytes or 0

    for error in records.errors:
        if error.operation in _UNLISTED_OPERATIONS:
            walker.incompletely_listed.add(error.relative_path)
        elif error.operation == _UNSTATED_CHILD_OPERATION:
            parent = _parent_relative(error.relative_path)
            if parent is not None:
                walker.incompletely_listed.add(parent)


#: The operations whose failure leaves a directory unlisted: its own listing, or, at the root,
#: the ``lstat`` before any listing. Nothing beneath such a directory was observed.
_UNLISTED_OPERATIONS = frozenset({"scandir", "lstat"})

#: The operation whose failure leaves a listed child without an entry, so its directory's
#: listing holds a name the Index does not.
_UNSTATED_CHILD_OPERATION = "stat"


#: The Dictionary section whose terms record doubt rather than subject matter.
NEGATIVE_LEXICON = "negative_lexicon"


def _name_facts(reading: NameReading, doubt: frozenset[str]) -> tuple[int, int, bool]:
    """Return the atom count, doubt hits and index flag a name authorship reading needs.

    The function reads off a tokenisation that already happened. Tokenising a folder's name a
    second time to answer this would double the cost of the one thing measured
    to be 3.6 times the census.
    """
    atoms = len(reading.components)
    hits = sum(1 for binding in reading.bindings if binding.term_id in doubt)
    indexed = any(
        component.component_type == AtomKind.INDEXED.value for component in reading.components
    )
    if not indexed and reading.shape is not None and reading.shape.slots:
        last = reading.shape.slots[-1]
        indexed = last.component_type == AtomKind.NUMBER.value
    return atoms, hits, indexed


#: What the organisation characteriser calls itself on every observation it
#: makes, and the version that invalidates them when the reading changes.
ORGANISATION_OBSERVER = "census.organisation"
ORGANISATION_VERSION = "1"

#: Each file's series key: the folder it sits in, its name shape,
#: its extension and its log2 size class, digested into one short value, and the size class
#: beside it. These are facts about the file, recorded by the file list. A content pass reads one
#: file of every series before any series gets a second, and a later rule may carry a read
#: file's type and writer to the unread members of its series. The key is never a claim. It says
#: which files are alike in name, place and size, and nothing about what is in them.
SERIES_KEY = "series.key"
SERIES_SIZE_CLASS_KEY = "series.size_class"
SERIES_KEY_VERSION = "1"


def series_key(
    root_id: str, parent: str | None, skeleton_hash: str | None, extension: str, size_bytes: int
) -> str:
    """The series key of one file: a digest of its root, folder, name shape, extension and size
    class, so grouping is an equality on a short column (as ``skeleton_hash`` is for shapes)."""
    parts = {
        "root": root_id,
        "parent": parent or ".",
        "shape": skeleton_hash or "",
        "extension": extension.casefold(),
        "size_class": size_bytes.bit_length(),
    }
    return digest_text(canonical_json(parts))[:32]


def _series_observations(walker: _Walker, entry: Entry, parent: str | None) -> list[Observation]:
    """The series key of one file and its size class, as the file list records them."""
    size = max(entry.size_bytes or 0, 0)
    key = series_key(
        entry.root_id,
        parent,
        walker.shape_digests.get(entry.entry_id),
        entry.extension or "",
        size,
    )
    made: list[Observation] = []
    for name, kind, value in (
        (SERIES_KEY, ValueKind.TEXT, key),
        (SERIES_SIZE_CLASS_KEY, ValueKind.INTEGER, size.bit_length()),
    ):
        observation = Observation(
            observation_id=observation_id(
                entry=entry.entry_id,
                observer="census",
                observer_version=SERIES_KEY_VERSION,
                key=name,
            ),
            entry_id=entry.entry_id,
            run_id=walker.run_id,
            observer="census",
            observer_version=SERIES_KEY_VERSION,
            observed_at=_now(),
            key=name,
            value_kind=kind,
            # The name shape is the name reader's reading, a rule of thumb over the name. The
            # folder, the extension and the size are literal, and one record carries one tier.
            extraction_tier=ExtractionTier.HEURISTIC,
            source_scope=SourceScope.NAME,
        )
        made.append(
            observation.model_copy(
                update=(
                    {"value_integer": int(value)}
                    if kind is ValueKind.INTEGER
                    else {"value_text": str(value)}
                )
            )
        )
    return made


def _organisation_observations(walker: _Walker, entries: dict[str, Entry]) -> CensusRecords:
    """Describe how each folder is organised. Observations only.

    This is not a bundle detector. A workflow bundle, a type silo and a date bucket
    are three different grammars, and a signature computed uniformly means
    something different in each. The function records what a folder looks like.
    Naming the regime is a question for a person and for a later rule.

    A folder whose composition matches no pattern is not disorganised. Its composition
    carries no signal, and the engine should look elsewhere and not report weakness.
    Reading it the other way would make a well-organised type-silo laboratory look
    chaotic, which is the error most likely to lose a user's trust.

    Recurrence is counted twice, among siblings and within the top-level branch,
    because those answer different questions. Siblings sharing a mixture is a
    template, and a branch repeating one is a habit.
    """
    records = CensusRecords()
    if not walker.settings.dictionary.characterise_organisation:
        return records

    options = {
        "factor": walker.settings.dictionary.burst_gap_factor,
        "window": walker.settings.dictionary.burst_gap_window,
    }

    profiles: dict[str, FolderProfile] = {}
    for relative in sorted(walker.directory_paths):
        entry = entries.get(walker.identifier(relative))
        if entry is None:
            continue
        atoms, hits, indexed = walker.folder_names.get(relative, (0, 0, False))
        profiles[relative] = profile_folder(
            FolderInput(
                name=entry.name,
                created_ns=entry.created_ns,
                modified_ns=entry.modified_ns,
                atom_count=atoms,
                negative_hits=hits,
                sequential_index=indexed,
                children=tuple(walker.folder_children.get(relative, ())),
            ),
            **options,
        )

    siblings = Counter(
        (_parent_relative(relative) or ".", profile.composition_digest)
        for relative, profile in profiles.items()
        if profile.child_count
    )
    branches = Counter(
        (_branch_of(relative), profile.composition_digest)
        for relative, profile in profiles.items()
        if profile.child_count
    )

    for relative in sorted(profiles):
        profile = profiles[relative]
        entry = entries[walker.identifier(relative)]
        readings: list[tuple[str, Any, ValueKind]] = [
            ("folder.child_count", profile.child_count, ValueKind.INTEGER),
            ("folder.shape_count", profile.shape_count, ValueKind.INTEGER),
            ("folder.shape_diversity", profile.shape_diversity, ValueKind.REAL),
            ("folder.dominant_shape_share", profile.dominant_share, ValueKind.REAL),
            ("folder.composition_digest", profile.composition_digest, ValueKind.TEXT),
            (
                "folder.composition_recurrence_siblings",
                siblings[(_parent_relative(relative) or ".", profile.composition_digest)],
                ValueKind.INTEGER,
            ),
            (
                "folder.composition_recurrence_branch",
                branches[(_branch_of(relative), profile.composition_digest)],
                ValueKind.INTEGER,
            ),
            ("folder.name_authorship", profile.authorship.value, ValueKind.TEXT),
            ("folder.name_evidence", asdict(profile.evidence), ValueKind.STRUCTURE),
            ("folder.burst_count", profile.burst_count, ValueKind.INTEGER),
            (
                "folder.bursts",
                [asdict(burst) for burst in profile.bursts],
                ValueKind.STRUCTURE,
            ),
        ]
        if profile.interval_ns[0] is not None and profile.interval_ns[1] is not None:
            readings.append(
                (
                    "folder.interval_ns",
                    profile.interval_ns[1] - profile.interval_ns[0],
                    ValueKind.INTEGER,
                )
            )
        for key, value, kind in readings:
            records.observations.append(_organisation_observation(walker, entry, key, value, kind))
    return records


def _organisation_observation(
    walker: _Walker, entry: Entry, key: str, value: Any, kind: ValueKind
) -> Observation:
    """Build one observation about a folder.

    The tier is ``HEURISTIC`` and not ``LITERAL``. A child count is arithmetic over a
    complete enumeration, but a composition digest, an authorship reading and a
    burst boundary are all rules of thumb over what was found, and one record
    must not carry one tier for two facts at different distances from the bytes.
    Recording the weaker tier for the group is the honest simplification.
    """
    observation = Observation(
        observation_id=observation_id(
            entry=entry.entry_id,
            observer=ORGANISATION_OBSERVER,
            observer_version=ORGANISATION_VERSION,
            key=key,
        ),
        entry_id=entry.entry_id,
        run_id=walker.run_id,
        observer=ORGANISATION_OBSERVER,
        observer_version=ORGANISATION_VERSION,
        observed_at=_now(),
        key=key,
        value_kind=kind,
        extraction_tier=ExtractionTier.HEURISTIC,
        source_scope=SourceScope.NAME,
    )
    if kind is ValueKind.INTEGER:
        return observation.model_copy(update={"value_integer": int(value)})
    if kind is ValueKind.REAL:
        return observation.model_copy(update={"value_real": float(value)})
    if kind is ValueKind.STRUCTURE:
        return observation.model_copy(update={"value_structure": canonical_json(value)})
    return observation.model_copy(update={"value_text": str(value)})


def _branch_of(relative: str) -> str:
    """Return the top-level branch a path sits under.

    Recurrence within a branch is a different question from recurrence among
    siblings: siblings sharing a mixture is a template somebody follows, and a
    branch repeating one is a habit somebody has.
    """
    if relative in ("", "."):
        return "."
    return PurePosixPath(relative).parts[0]


def _parent_relative(relative: str) -> str | None:
    if relative == ".":
        return None
    parent = PurePosixPath(relative).parent.as_posix()
    return "." if parent in ("", ".") else parent


def _aggregate_claims(walker: _Walker) -> CensusRecords:
    """Emit the entailed closed-world statements about each directory.

    "This directory holds exactly forty files, and everything beneath it totals
    two gigabytes." Statements of this shape are legitimate only because
    ``contains`` is the one observationally complete primitive: once enumeration
    finishes, the absence of a containment edge means the thing is not there
    rather than that the engine failed to look. No other relation licenses one,
    which is why the predicate carries the closed-world marker that the INV-8 check in
    ``core/invariants.py`` reads. The statements are made only where enumeration
    finished. A directory that could not be listed, or whose listed child could not be
    ``stat``-ed, is given no direct count, and no directory above it a total.

    These rollups make folder nodes meaningful without opening anything. The function
    computes them here and not at inference because the data is already in hand, it costs
    nothing, and it lets the first screen after a census show something worth looking at.
    """
    records = CensusRecords()
    bound = walker.rules[CENSUS_AGGREGATE]
    rule, version = bound.rule_id, bound.version
    scope = Scope(ScopeKind.ROOT, walker.root.root_id)

    # Deepest first, so every subdirectory's totals exist before its parent asks for
    # them. Sorted by depth then path, which is canonical, so two runs over unchanged
    # data produce identical claims in identical order.
    totals: dict[str, DirectoryTotals] = {}
    for relative in sorted(walker.directory_paths, key=lambda value: (-_depth_of(value), value)):
        verdict = bound.evaluate(
            DirectoryCounts(
                files=walker.direct_files[relative],
                directories=walker.direct_directories[relative],
                size_bytes=walker.direct_bytes[relative],
                children=tuple(
                    totals[child]
                    for child in walker.child_directories.get(relative, [])
                    if child in totals
                ),
            )
        )
        if not isinstance(verdict, DirectoryTotals):
            message = f"{rule} answered {type(verdict).__name__}, not a directory's totals"
            raise TypeError(message)
        totals[relative] = verdict

    # Where enumeration did not finish, nothing closed-world is said: not the unfinished
    # directory's own counts, and not the totals of any directory above it, which would
    # otherwise state the part that was seen as the whole (unexamined is not
    # zero). The direct counts of a directory above it still stand, because its own listing
    # finished.
    unfinished_below = _with_ancestors(walker.incompletely_listed)
    for relative in sorted(walker.directory_paths):
        identifier = walker.identifier(relative)
        counted = totals[relative]
        stated: list[tuple[str, int]] = []
        if relative not in walker.incompletely_listed:
            stated.extend(
                (
                    ("direct_file_count", counted.direct_file_count),
                    ("direct_directory_count", counted.direct_directory_count),
                )
            )
        if relative not in unfinished_below:
            stated.extend(
                (
                    ("recursive_file_count", counted.recursive_file_count),
                    ("recursive_directory_count", counted.recursive_directory_count),
                    ("recursive_size_bytes", counted.recursive_size_bytes),
                )
            )
        for predicate, value in stated:
            full = f"{CLOSED_WORLD_PREDICATE_PREFIX}{predicate}"
            records.claims.append(
                Claim(
                    claim_id=claim_id(
                        subject=identifier,
                        predicate=full,
                        object_reference=str(value),
                        rule_id=rule,
                        rule_version=version,
                        scope=scope,
                    ),
                    run_id=walker.run_id,
                    subject=identifier,
                    predicate=full,
                    # The object of a triple may be a literal. Canonically
                    # rendered, so the value is comparable as text and identical
                    # between runs.
                    object_reference=canonical_json(value),
                    inference_tier=InferenceTier.ENTAILED,
                    rule_id=rule,
                    rule_version=version,
                    scope=scope,
                    resolution_status=ResolutionStatus.RESOLVED,
                )
            )
    return records


def _depth_of(relative: str) -> int:
    """Return how deep a relative path sits. The root is zero."""
    return 0 if relative == "." else relative.count("/") + 1


def _with_ancestors(relatives: set[str]) -> frozenset[str]:
    """Return each directory with every directory above it, up to the root."""
    found: set[str] = set()
    for relative in relatives:
        current: str | None = relative
        while current is not None and current not in found:
            found.add(current)
            current = _parent_relative(current)
    return frozenset(found)


# ---------------------------------------------------------------------------
# Orchestration: a census run, from an empty staging directory to a published
# pointer.
# ---------------------------------------------------------------------------


@dataclass
class CensusReport:
    """What a completed run did, returned to whoever asked for it."""

    run_id: str
    published: bool
    counts: CensusCounts
    table_rows: dict[str, int]
    invariants: tuple[InvariantOutcome, ...] = ()
    cancelled: bool = False
    cancellation_reason: str | None = None
    #: Directories recognised as the engine's own, as the run record lists them.
    recognised: tuple[RecognisedPath, ...] = ()


@counting_warnings("census")
def run_census(
    project: Project,
    *,
    run_id: str,
    started_at: str,
    token: CancellationToken | None = None,
    reporter: ProgressReporter | None = None,
    extractor_registry: Registry[Any] | None = None,
    installation: frozenset[tuple[int, int]] | None = None,
    rulebook: EffectiveRulebook | None = None,
    rebind: bool = False,
    spellings: Sequence[str] | None = None,
) -> CensusReport:
    """Walk every declared root, then publish the result atomically.

    Each step below is where it is for a reason.

    Previous records are read before anything is written, so the reuse
    decision is made against a stable snapshot and not against a run in
    progress. Records stream to fragments as directories complete, so an
    interruption an hour in has not lost an hour. Tombstones are written after
    the walk, because "what disappeared" is only knowable once "what is here" is
    complete. Compaction runs before the manifest, so the manifest describes the
    files a reader will actually open. Invariants run before the rename, because
    a failing invariant must block publication and not be discovered in it.

    A cancelled run publishes nothing and leaves the project valid. That
    falls out of the write protocol and needs no special care: the staging
    directory that no reader resolves through is simply abandoned, and the previous
    run stays current.

    ``rulebook`` is the Rulebook whose scan-layer rules the census cites, and ``None``
    reads the shipped one.

    ``rebind`` replays the current run's entries instead of walking.
    Every name is read again under the vocabulary and Record in force, and nothing under a
    root is opened, listed or ``stat``-ed. The run is its own, with the current run as its
    parent, its profile ``rebind``, its own ``dictionary_hash`` and packs, and the census's
    scan errors, exclusions and recognised paths carried, because the tree it states is the
    one that census saw.

    With ``spellings`` the rebind is partial. Only the names that could hold an answered
    spelling are read again: every folder, and every file whose name, case folded and with its
    separators taken out, contains a spelling so written. This is a superset on purpose,
    because a new sense can change how a name is split and not only how a part is looked up.
    Every other file's stored name reading is carried from the parent run as it was. The result
    equals the whole rebind's to the digit where the vocabulary changed by those answers alone.
    That is the purpose: a definition made in the Dashboard takes effect without re-reading the
    pool. The run record names the spellings. The function refuses where the stored names were
    read by another tokeniser, because carrying them would mix two readings in one run.
    """
    # Imported inside the function purely to keep the import graph acyclic:
    # the catalog reads the tables this function writes.
    from ingence.storage.catalog import census_facts, open_catalog

    # Bound once for the whole run, and before anything is staged, so a Rulebook that
    # does not hold the census's rules stops the run before it writes.
    book = shipped_rulebook() if rulebook is None else rulebook
    rules = scan_rules(CENSUS_RULE_IDS, book)

    # A walk reads every declared root, and refuses before it stages anything where one cannot
    # be reached. With the root absent it would publish a run stating every file the
    # last census held deleted. A rebind reads no root and is the offline path.
    if not rebind:
        refuse_unreachable_roots(project.identity.roots, verb="A walk reads every declared root")

    # The project's own staging, nothing on the way to it a link (``owned_path``).
    staging = owned_path(project.path, staging_path(project.path, run_id))
    staging.mkdir(parents=True, exist_ok=False)
    settings = project.settings

    journal = open_journal(staging / "journal", run_id, enabled=settings.journal.enabled)
    writers: dict[type[Record], FragmentWriter] = {
        spec.model: FragmentWriter(
            spec,
            staging / spec.directory,
            flush_every_records=settings.scan.flush_every_records,
            flush_every_seconds=settings.scan.flush_every_seconds,
        )
        for spec in TABLES
        if not spec.spans_runs
    }

    # Built once for the whole run and shared by every worker. Loading the
    # shipped vocabulary costs 0.23 seconds and is paid once rather than per
    # root, and nothing mutates it after construction. It carries what the
    # project's Record says a person decided, so an answer reaches the binder
    # on the next index.
    # The Record watermark: read before the vocabulary reads the Record's
    # answers. None where no Record is read.
    watermark = record_watermark(project.path) if settings.dictionary.tokenise_names else None
    # Every answer the run reads, read at that watermark: the
    # vocabulary's and the divided spellings', so one written while the run reads names is the
    # next run's.
    with read_at(project.path, watermark):
        # One dictionary: every shipped section, but for a vetoed domain.
        vocabulary = (
            DictionaryStore.for_project(project.path)
            if settings.dictionary.tokenise_names
            else None
        )
        cases_answered = project_answers(project.path) if vocabulary is not None else {}
        # The domains a person made priors: a yes to "load the X pack", or
        # ``init --domain``. Read at the same watermark as every other answer.
        priors = domain_priors(project.path) if vocabulary is not None else frozenset()
    # The years the run reads dates in, and the day it reads them on: the
    # project's years, or by default up to this run's own year, stated on the run record.
    date_years = settings.dictionary.years_read_on(started_at)
    normaliser = (
        Normaliser(
            vocabulary,
            years=date_years,
            read_on=_reading_day(started_at),
            **reading_options(settings.dictionary),
        )
        if vocabulary is not None
        else None
    )
    # The run reads each spelling a capital divides once, whole or as pieces,
    # from every name it reads and a person's answers. The normaliser carries it to every walker.
    if (
        normaliser is not None
        and vocabulary is not None
        and settings.dictionary.bind_terms
        and settings.dictionary.case_reading.enabled
    ):
        normaliser.cases = CaseBinder(
            vocabulary,
            answers=cases_answered,
            whole_over_alone=settings.dictionary.case_reading.whole_over_alone,
            unknown_whole=settings.dictionary.case_reading.unknown_whole,
        )
    # The run reads each spelling two domains hold differently once every name is read,
    # from the folder's own files. The normaliser carries it to every walker.
    if normaliser is not None and vocabulary is not None and settings.dictionary.bind_terms:
        skew = settings.dictionary.domain_skew
        normaliser.domains = DomainBinder(
            vocabulary,
            minimum_files=skew.minimum_files,
            minimum_share=skew.minimum_share,
            minimum_ratio=skew.minimum_ratio,
            question_above_files=skew.question_above_files,
            priors=priors,
            extension_domains=extension_domains(),
        )
    # What the run states about it, wherever it reads names, so a later
    # partial rebind can refuse to carry names read another way.
    case_reading = (
        CaseReadingStated(
            enabled=normaliser.cases is not None,
            whole_over_alone=settings.dictionary.case_reading.whole_over_alone,
            unknown_whole=settings.dictionary.case_reading.unknown_whole,
        )
        if normaliser is not None
        else None
    )

    counts = CensusCounts()
    kept_members: dict[str, Entry] = {}
    #: Files that moved since the last census: the new entry's identifier to the old one's.
    moves: dict[str, str] = {}
    assertions: list[Assertion] = []
    claims: list[Claim] = []
    recognised: list[RecognisedPath] = []
    # Read once for the whole run, from the running engine and never from the pool.
    installed = installation_directories() if installation is None else installation
    # The project this run writes, left out of every root wherever it lies.
    own_project = OwnProject.of(project.path)
    cancelled = False
    reason: str | None = None

    journal.record(
        "census",
        "run.started",
        run_id=run_id,
        roots=len(project.identity.roots),
        profile="census",
    )

    parent_run: str | None = None
    carried_excluded: tuple[ExcludedNames, ...] = ()
    carried_errors: list[ScanError] = []
    readings_carried = 0
    #: Declared links whose target is a placeholder, refused, said on the run record.
    link_placeholders = 0
    reread: frozenset[str] | None = None
    stored_shapes: dict[str, str] = {}
    filesystem: frozenset[str] = frozenset()
    if spellings is not None and not rebind:
        message = "only a rebind reads some names again and carries the rest; a walk reads all"
        raise ValueError(message)
    if rebind:
        with open_catalog(project.path) as catalog:
            if not catalog.has_run:
                message = "there is no census to re-bind: index the project first"
                raise ValueError(message)
            parent_run = catalog.run_directory.name if catalog.run_directory else None
            carried_excluded, carried_recognised = census_facts(catalog)
            recognised.extend(carried_recognised)
            carried_errors = [
                record_from_row(row, ScanError)
                for row in catalog.query(
                    "SELECT * FROM scan_errors ORDER BY relative_path, operation"
                )
                .to_arrow_table()
                .to_pylist()
            ]
            # What a dive read of the content: the bytes are unchanged, so the readings stand
            # and only the names are read again. Streamed, a batch at a time.
            for rows in catalog.batches(
                "SELECT * FROM observations WHERE observer NOT IN (SELECT unnest(?)) "
                "ORDER BY observation_id",
                [sorted(_CENSUS_OBSERVERS)],
            ):
                for row in rows:
                    writers[Observation].add(
                        record_from_row(row, Observation), now=time.monotonic()
                    )
                    readings_carried += 1
        if spellings is not None:
            with open_catalog(project.path) as catalog:
                reread, stored_shapes, filesystem = _partial_rebind_plan(
                    catalog, spellings, normaliser, vocabulary, case_reading
                )
        journal.record(
            "census",
            "rebind.parent",
            parent_run_id=parent_run,
            readings_carried=readings_carried,
            partial_spellings=sorted(spellings) if spellings is not None else None,
            names_read_again=len(reread) if reread is not None else None,
        )

    try:
        for declaration in project.identity.roots:
            with open_catalog(project.path) as catalog:
                previous = catalog.previous_records(declaration.root_id)
                live = catalog.live_entries(declaration.root_id)
                embedding = catalog.embedding_edges()
            if normaliser is not None and vocabulary is not None and reread is not None:
                # A partial rebind reads the names it carries for what the run's other names are
                # read by (each folder's year, each divided spelling's counts), as a whole
                # rebind reads them.
                observe_carried(
                    (entry for entry in live.values() if entry.entry_id not in reread),
                    normaliser=normaliser,
                    store=vocabulary,
                )
            journal.record(
                "census",
                "root.started",
                root_id=declaration.root_id,
                previous_entries=len(previous),
            )

            # A listing root is replayed from its listing: the entries the
            # archive's listing states, built with every fact it does not state absent, through
            # the same walker a rebind uses, and its statements recorded as declared
            # observations. A rebind replays what the last run stored, as for any root.
            listing_entries: tuple[Entry, ...] | None = None
            if not rebind and is_listing_root(declaration):
                listing_entries, declared, placeholders = _read_listing_root(
                    declaration, settings=settings, run_id=run_id, observed_at=started_at
                )
                link_placeholders += placeholders
                for observation in declared:
                    writers[Observation].add(observation, now=time.monotonic())
                journal.record(
                    "census",
                    "listing.read",
                    root_id=declaration.root_id,
                    root_uri=declaration.root_uri,
                    listing_digest=listing_digest(Path(declaration.mount_path)),
                    entries=len(listing_entries),
                    declared_observations=len(declared),
                    declared_links=settings.listing.declared_links,
                    link_placeholders=placeholders,
                )
            held: list[Entry] | None = (
                [] if not rebind and settings.scan.follow_moves and live else None
            )
            walker_counts, seen = _drain_root(
                declaration,
                held=held,
                stored=tuple(live.values()) if rebind else listing_entries,
                errors=tuple(carried_errors) if rebind else (),
                reread=reread,
                stored_shapes=stored_shapes,
                settings=settings,
                run_id=run_id,
                previous=previous,
                extractor_registry=extractor_registry,
                token=token,
                writers=writers,
                journal=journal,
                reporter=reporter,
                assertions=assertions,
                claims=claims,
                recognised=recognised,
                vocabulary=vocabulary,
                normaliser=normaliser,
                installation=installed,
                rules=rules,
                own_project=own_project,
            )
            _merge_counts(counts, walker_counts)

            # A member of a container is never on disk to walk, so the census carries it where
            # it walked its container, with the edge placing it there. Otherwise every index
            # would tombstone it and the next dive would promote it again under the same
            # identifier.
            members = _carried_members(live, seen)
            kept_members.update(members)
            for member in members.values():
                writers[Entry].add(member, now=time.monotonic())
            standing = seen | set(members)
            for edge in embedding:
                if edge.target_id in members and edge.source_id in standing:
                    writers[Assertion].add(edge, now=time.monotonic())
                    assertions.append(edge)
            counts.carried += len(members)

            if held is not None:
                # Moves confirmed by time and by their neighbours: a birth time
                # confirms a match or names it a copy, a change time between the walks is the
                # move's time, and a folder whose files arrived together moved (``scan/moves``).
                found = find_moves(
                    held,
                    live,
                    gone=set(live) - standing,
                    settings=settings,
                    until_ns=time.time_ns(),
                )
                for entry in held:
                    move = found.files.get(entry.entry_id)
                    if move is not None:
                        entry = _moved_entry(
                            entry, live[move.source], previous.get(move.source), move
                        )
                    elif (move := found.folders.get(entry.entry_id)) is not None:
                        entry = entry.model_copy(
                            update={
                                "moved_from": move.source,
                                "moved_by": move.by,
                                "moved_at_ns": move.at_ns,
                            }
                        )
                    writers[Entry].add(entry, now=time.monotonic())
                moves.update({new: move.source for new, move in found.files.items()})
                if found.files or found.folders or found.tally.refused_as_copies:
                    journal.record(
                        "census",
                        "walk.moves",
                        root_id=declaration.root_id,
                        **found.tally.as_detail(),
                    )

            # Whatever the previous run held and this one neither walked nor carried has
            # gone. Deletion is recorded, never applied: the row stays and gains a
            # tombstone, so the history of a file that existed is not erased by the file
            # ceasing to exist, and the tombstone keeps what the entry was.
            for missing in sorted(set(live) - standing):
                writers[Entry].add(_tombstone(live[missing], run_id=run_id), now=time.monotonic())
                counts.tombstones += 1

            writers[Root].add(
                Root(
                    root_id=declaration.root_id,
                    root_uri=declaration.root_uri,
                    mount_path=declaration.mount_path,
                    case_sensitivity=declaration.case_sensitivity,
                    case_established_by=declaration.case_established_by,
                    label=declaration.label,
                    first_seen_run=run_id,
                    last_seen_run=run_id,
                ),
                now=time.monotonic(),
            )
            journal.record("census", "root.finished", root_id=declaration.root_id)

    except CancelledError as exc:
        cancelled = True
        reason = exc.reason
        journal.record("census", "run.cancelled", reason=reason)

    # Checked again after the walk. Cancellation requested while the last
    # directory was completing must still mean nothing is published, or whether
    # a run survives a Ctrl-C depends on a race the user cannot see.
    if token is not None and token.cancelled and not cancelled:
        cancelled = True
        reason = token.reason
        journal.record("census", "run.cancelled", reason=reason)

    # A walk keeps a dive's readings of the files that have not changed at all, and of the
    # members of such a file, which no census reopens.
    if not rebind and not cancelled and settings.scan.keep_dive_readings and counts.unchanged:
        kept = _kept_with_members(counts.unchanged, kept_members)
        with open_catalog(project.path) as catalog:
            # Streamed, a batch at a time.
            for rows in catalog.batches(
                "SELECT * FROM observations WHERE observer NOT IN (SELECT unnest(?)) "
                "AND entry_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY observation_id",
                [sorted(_CENSUS_OBSERVERS), sorted(kept)],
            ):
                for row in rows:
                    writers[Observation].add(
                        record_from_row(row, Observation), now=time.monotonic()
                    )
                    readings_carried += 1
        journal.record(
            "census",
            "walk.readings_kept",
            files_unchanged=len(counts.unchanged),
            entries_kept=len(kept),
            readings_carried=readings_carried,
        )

    # A moved file keeps what a dive read of it: the readings of
    # the entry it came from, under its new identifier. Matched without reading the file, they are
    # a candidate (``moved_by``). The next dive checks them by the digest they carried, and
    # confirms them or drops them (``deepen._check_moves``).
    if not rebind and not cancelled and settings.scan.keep_dive_readings and moves:
        by_old = {old: new for new, old in moves.items()}
        with open_catalog(project.path) as catalog:
            moved_readings = [
                _rekeyed(record_from_row(row, Observation), by_old, run_id=run_id)
                for row in catalog.query(
                    "SELECT * FROM observations WHERE observer NOT IN (SELECT unnest(?)) "
                    "AND entry_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY observation_id",
                    [sorted(_CENSUS_OBSERVERS), sorted(by_old)],
                )
                .to_arrow_table()
                .to_pylist()
            ]
        for reading in moved_readings:
            writers[Observation].add(reading, now=time.monotonic())
        journal.record(
            "census",
            "walk.moved_readings_kept",
            files_moved=len(moves),
            readings_carried=len(moved_readings),
        )

    # A partial rebind carries every name reading it did not make, as the
    # parent run stored it, under this run's identifier: the name tables' rows for each file
    # whose name held no answered spelling.
    carried_names: dict[str, int] = {}
    if reread is not None and parent_run is not None and not cancelled:
        carried_names = _carry_name_readings(
            project.path / RUNS_DIR / parent_run,
            staging,
            keep=filesystem - reread,
            run_id=run_id,
        )

    # The pieces of every spelling a capital divided, bound now the run has counted every name.
    if normaliser is not None and normaliser.cases is not None and not cancelled:
        for binding in normaliser.cases.bind(run_id=run_id):
            writers[DictionaryBinding].add(binding, now=time.monotonic())
        journal.record("census", "case.readings", **normaliser.cases.summary())
    # The spellings two domains hold differently, bound now the run has read every folder's files,
    # and each folder's profile stored beside it.
    if normaliser is not None and normaliser.domains is not None and not cancelled:
        for binding in normaliser.domains.bind(run_id=run_id):
            writers[DictionaryBinding].add(binding, now=time.monotonic())
        for observation in normaliser.domains.observations(run_id=run_id):
            writers[Observation].add(observation, now=time.monotonic())
        journal.record("census", "domain.readings", **normaliser.domains.summary())

    # -- publish ----------------------------------------------------------

    # The vocabulary a run consulted is written into the run, because the engine promises
    # reproducibility from the Index, the Rulebook, the Dictionary and the Record, and that
    # is not true of a Dictionary nobody wrote down. The file is absent where the Dictionary
    # was never consulted, which is the honest reading of a run that did not use one and
    # not an omission.
    if vocabulary is not None:
        for record in vocabulary.records():
            writers[DictionaryEntry].add(record, now=time.monotonic())

    snapshot = GraphSnapshot(assertions=tuple(assertions), claims=tuple(claims))
    outcomes = check_all(snapshot)

    platform = PlatformRecord(**project.platform_record())
    listed = tuple(sorted(recognised, key=lambda path: (path.root_id, path.relative_path)))
    left_out = carried_excluded if rebind else excluded_names(counts)
    components = (
        tuple(ComponentActivation(**row) for row in extractor_registry.snapshot())
        if extractor_registry is not None
        else ()
    )
    writers[RunRecord].add(
        build_run_record(
            run_id=run_id,
            parent_run_id=parent_run,
            started_at=started_at,
            ended_at=_now(),
            profile="rebind" if rebind else "census",
            settings=project.settings,
            project=project,
            components=components,
            platform=platform,
            counts=tuple(
                sorted(
                    [
                        *(
                            (
                                table_for(model).name,
                                writer.rows_written
                                + writer.pending
                                + carried_names.get(table_for(model).name, 0),
                            )
                            for model, writer in writers.items()
                        ),
                        # Beside the tables' rows only where a listing held some.
                        *(
                            (("declared_link_placeholders", link_placeholders),)
                            if link_placeholders
                            else ()
                        ),
                    ]
                )
            ),
            reuse_decisions=tuple(sorted(counts.reuse.items())),
            recognised=listed,
            excluded=left_out,
            dictionary_hash=vocabulary.fingerprint() if vocabulary is not None else None,
            packs=in_force(vocabulary),
            rulebook_hash=book.digest,
            rebind_spellings=tuple(sorted(set(spellings))) if spellings is not None else None,
            record_watermark=watermark,
            job_id=current_job_id(),
            date_years=date_years if normaliser is not None else None,
            normalizer_version=normaliser.version if normaliser is not None else None,
            case_reading=case_reading,
            invariants=outcomes,
            published=not cancelled,
        ),
        now=time.monotonic(),
    )

    for writer in writers.values():
        writer.close(now=time.monotonic())
    journal_rows = journal.close()

    if cancelled:
        journal.record("census", "run.abandoned", reason=reason)
        return CensusReport(
            run_id=run_id,
            published=False,
            counts=counts,
            table_rows={},
            invariants=outcomes,
            cancelled=True,
            cancellation_reason=reason,
            recognised=listed,
        )

    table_rows = compact_run(staging, TABLES)
    table_rows["journal_events"] = journal_rows
    # The run's quality figures, counted from the tables just compacted. Imported
    # here so the walk loads the quality package only when it publishes.
    from ingence.quality.publish import write_quality

    table_rows["quality_figures"] = write_quality(
        project.path, staging, run_id=run_id, phase="rebind" if rebind else "census"
    )
    manifest = build_manifest(
        staging,
        run_id=run_id,
        engine_version=__version__,
        created_at=started_at,
        counts=tuple(sorted(table_rows.items())),
        invariants=outcomes,
    )
    try:
        commit_run(project.path, manifest=manifest, updated_at=_now())
    except CommitError:
        return CensusReport(
            run_id=run_id,
            published=False,
            counts=counts,
            table_rows=table_rows,
            invariants=outcomes,
            recognised=listed,
        )
    return CensusReport(
        run_id=run_id,
        published=True,
        counts=counts,
        table_rows=table_rows,
        invariants=outcomes,
        recognised=listed,
    )


def _read_listing_root(
    declaration: RootDeclaration, *, settings: Settings, run_id: str, observed_at: str
) -> tuple[tuple[Entry, ...], list[Observation], int]:
    """A listing root's entries, declared observations and placeholder links, from its cache.

    A placeholder link is refused and counted: the count is the run record's.
    """
    listing = read_listing(Path(declaration.mount_path))
    if listing.header.root_uri != declaration.root_uri:
        message = (
            f"the listing cached at {declaration.mount_path} is {listing.header.root_uri}, "
            f"not the root it is declared for ({declaration.root_uri})"
        )
        raise ValueError(message)
    entries = tuple(entries_from_listing(listing, declaration, run_id=run_id))
    declared = declared_observations(
        listing,
        declaration,
        run_id=run_id,
        observed_at=observed_at,
        links=settings.listing.declared_links == "recorded",
    )
    return entries, declared, placeholder_links(listing)


def _drain_root(
    declaration: RootDeclaration,
    *,
    stored: Sequence[Entry] | None = None,
    errors: Sequence[ScanError] = (),
    reread: frozenset[str] | None = None,
    stored_shapes: Mapping[str, str] | None = None,
    settings: Settings,
    run_id: str,
    previous: dict[str, PreviousRecord],
    extractor_registry: Registry[Any] | None,
    token: CancellationToken | None,
    writers: dict[type[Record], FragmentWriter],
    journal: Journal | NullJournal,
    reporter: ProgressReporter | None,
    assertions: list[Assertion],
    claims: list[Claim],
    recognised: list[RecognisedPath],
    vocabulary: DictionaryStore | None,
    normaliser: Normaliser | None,
    installation: frozenset[tuple[int, int]],
    rules: Mapping[str, ScanRule],
    held: list[Entry] | None = None,
    own_project: OwnProject | None = None,
) -> tuple[CensusCounts, set[str]]:
    """Stream one root's records into the writers, returning counts and ids seen.

    ``held`` collects the files and folders at paths the last census did not have, instead of
    writing them. Whether one moved is known only once the walk has found every path, so the
    function writes them at the close, each with where it came from where it moved.
    """
    counts = CensusCounts()
    seen: set[str] = set()

    walked = (
        walk_root(
            declaration,
            settings=settings,
            run_id=run_id,
            previous=previous,
            extractor_registry=extractor_registry,
            token=token,
            counts_out=counts,
            vocabulary=vocabulary,
            normaliser=normaliser,
            installation=installation,
            rules=rules,
            own_project=own_project,
        )
        if stored is None
        else replay_root(
            declaration,
            stored,
            settings=settings,
            run_id=run_id,
            reread=reread,
            stored_shapes=stored_shapes,
            errors=[error for error in errors if _error_root(error, declaration)],
            previous=previous,
            extractor_registry=extractor_registry,
            token=token,
            counts_out=counts,
            vocabulary=vocabulary,
            normaliser=normaliser,
            rules=rules,
        )
    )
    for records in walked:
        now = time.monotonic()
        for entry in records.entries:
            seen.add(entry.entry_id)
            if (
                held is not None
                and entry.entry_type in (EntryType.FILE, EntryType.DIRECTORY)
                and entry.entry_kind is EntryKind.FILESYSTEM
                and entry.entry_id not in previous
            ):
                held.append(entry)
                continue
            writers[Entry].add(entry, now=now)
        for observation in records.observations:
            writers[Observation].add(observation, now=now)
        for component in records.components:
            writers[Component].add(component, now=now)
        for shape in records.name_shapes:
            writers[NameShape].add(shape, now=now)
        for binding in records.bindings:
            writers[DictionaryBinding].add(binding, now=now)
        for assertion in records.assertions:
            writers[Assertion].add(assertion, now=now)
            assertions.append(assertion)
        for claim in records.claims:
            writers[Claim].add(claim, now=now)
            claims.append(claim)
        for error in records.errors:
            writers[ScanError].add(error, now=now)
        for (reason, pattern, entry_type), number in records.excluded.items():
            counts.excluded[(declaration.root_id, reason, pattern, entry_type)] += number
        for path in records.recognised:
            recognised.append(path)
            journal.record(
                "census",
                "path.recognised",
                root_id=path.root_id,
                relative_path=path.relative_path,
                recognised_as=path.recognised_as.value,
                indexed=path.indexed,
            )

        if reporter is not None:
            reporter.advance(
                entries_seen=len(records.entries),
                errors=len(records.errors),
                directories_seen=1,
                folders_found=sum(
                    1 for entry in records.entries if entry.entry_type is EntryType.DIRECTORY
                ),
                files_found=sum(
                    1 for entry in records.entries if entry.entry_type is EntryType.FILE
                ),
                # The bytes the files hold, counted as the census's own total is (``_accumulate``).
                bytes_seen=sum(
                    entry.size_bytes or 0
                    for entry in records.entries
                    if entry.entry_type is EntryType.FILE
                ),
            )
    return counts, seen


#: What a spelling and a name are compared as for a partial rebind: case folded, and without the
#: separators a name may write a term's parts with.
_SEPARATORS_IN_A_TERM = re.compile(r"[\s_\-.]+")


def _squashed(text: str) -> str:
    return _SEPARATORS_IN_A_TERM.sub("", unicodedata.normalize("NFC", text).casefold())


def _refuse_names_bound_at_other_standings(catalog: Any, vocabulary: DictionaryStore) -> None:
    """Refuse a partial rebind where a stored binding ranks a sense otherwise than this engine does.

    A sense's digest holds no rank. A shipped pack's senses moved below the core
    without any digest moving, so names read before stood the pack level with the core and kept
    its ties, while the names read again take the core's sense. Each binding and each alternative
    stores the rank it was made at, so those are compared, a person's answers aside.
    """
    from ingence.dictionary.store import origin_rank

    current = {
        vocabulary.term_id(sense): origin_rank(sense)
        for sense in vocabulary.senses()
        if sense.origin is not DictionaryOrigin.USER
    }
    stored = catalog.query(
        "SELECT DISTINCT term_id, sense_origin_rank FROM dictionary_bindings "
        "WHERE term_id IS NOT NULL AND sense_origin_rank IS NOT NULL "
        "AND sense_origin IS DISTINCT FROM 'user' "
        "UNION SELECT DISTINCT alt.term_id, alt.origin_rank FROM dictionary_bindings b "
        "CROSS JOIN unnest(from_json(b.alternatives_json, "
        '\'[{"term_id":"VARCHAR","origin":"VARCHAR","origin_rank":"INTEGER"}]\''
        ")) AS t(alt) WHERE b.alternative_count > 0 AND alt.origin <> 'user'"
    ).fetchall()
    moved = sorted(
        str(term_id)
        for term_id, rank in stored
        if term_id in current and rank is not None and int(rank) != current[str(term_id)]
    )
    if moved:
        count = len(set(moved))
        message = (
            f"the stored names were read when {count} {'sense' if count == 1 else 'senses'} stood "
            "otherwise against the core than now; a partial rebind would mix two readings in one "
            "run, so re-bind them all"
        )
        raise ValueError(message)


def _partial_rebind_plan(
    catalog: Any,
    spellings: Sequence[str],
    normaliser: Normaliser | None,
    vocabulary: DictionaryStore | None = None,
    case_reading: CaseReadingStated | None = None,
) -> tuple[frozenset[str], dict[str, str], frozenset[str]]:
    """Which names a partial rebind reads again, the stored shapes of the rest, and every entry.

    The names are every folder, and every file whose squashed name contains a squashed spelling.
    This is a superset on purpose. The function refuses where the stored names were read by
    another tokeniser than this one, or with another vocabulary than this one's but for a
    person's answers. For example, names carried from a census read before new formats and
    reagents were shipped kept the old readings, and many bindings differed from the whole
    rebind's. An answer is what a partial rebind exists to apply. Anything else that changed
    the Dictionary changes names no spelling names, and only reading them all again applies it.
    The function also refuses where the case reading differs: the ratio at which a divided
    spelling is read whole decides the carried names' bindings as much as the vocabulary does,
    and a stored run that does not state its reading is refused too, since what it used is
    then unknown.
    """
    # The readers come first: a changed reader is the change
    # that needs the whole rebind, and the refusal names it.
    if normaliser is not None:
        versions = {
            str(row[0])
            for row in catalog.query(
                "SELECT DISTINCT normalizer_version FROM components"
            ).fetchall()
        }
        if versions and versions != {normaliser.version}:
            found = ", ".join(sorted(versions))
            message = (
                f"the stored names were read by tokeniser version {found} and this engine reads "
                f"them as {normaliser.version}; a partial rebind would mix two readings in one "
                "run, so re-bind them all"
            )
            raise ValueError(message)
        # And the binder's version: a resolver that reads
        # plurals or labels otherwise binds the carried names otherwise.
        resolvers = {
            str(row[0])
            for row in catalog.query(
                "SELECT DISTINCT resolver_version FROM dictionary_bindings"
            ).fetchall()
        }
        if resolvers and resolvers != {RESOLVER_VERSION}:
            found = ", ".join(sorted(resolvers))
            message = (
                f"the stored names were bound by resolver version {found} and this engine binds "
                f"them as {RESOLVER_VERSION}; a partial rebind would mix two readings in one run, "
                "so re-bind them all"
            )
            raise ValueError(message)
    if vocabulary is not None:
        stored = {
            (str(term_id), str(digest))
            for term_id, digest in catalog.query(
                "SELECT term_id, sense_digest FROM dictionary_entries WHERE origin <> 'user'"
            ).fetchall()
        }
        current = {
            (record.term_id, str(record.sense_digest))
            for record in vocabulary.records()
            if record.origin is not DictionaryOrigin.USER
        }
        if stored and stored != current:
            message = (
                f"the stored names were read with another vocabulary than this engine's "
                f"({len(stored ^ current)} senses differ, not counting a person's answers); a "
                "partial rebind would mix two readings in one run, so re-bind them all"
            )
            raise ValueError(message)
        _refuse_names_bound_at_other_standings(catalog, vocabulary)
    if normaliser is not None:
        # The years are not in the version, so they are compared here:
        # after New Year's Day, or a person editing ``dictionary.years``, the carried names were
        # read with other years than the ones this run reads with.
        run_directory = catalog.run_directory
        if run_directory is not None:
            stored_years, _ = names_read_with(run_directory.parent.parent, run_directory.name)
            if versions and stored_years != normaliser.years:
                message = (
                    f"the stored names were read with the years {stored_years[0]}-"
                    f"{stored_years[1]} and this run reads them with {normaliser.years[0]}-"
                    f"{normaliser.years[1]}; a partial rebind would mix two readings in one run, "
                    "so re-bind them all"
                )
                raise ValueError(message)
            if versions and case_reading is not None:
                stored_reading = case_reading_read_with(
                    run_directory.parent.parent, run_directory.name
                )
                current_reading = case_reading.model_dump(exclude={"schema_version"})
                if stored_reading is None:
                    message = (
                        "the stored names do not state the case reading they were read with (a run "
                        "written before 29 September 2026); a partial rebind might read a divided "
                        "spelling two ways in one run, so re-bind them all"
                    )
                    raise ValueError(message)
                stored_reading.pop("schema_version", None)
                if stored_reading != current_reading:
                    message = (
                        f"the stored names were read with the case reading {stored_reading} and "
                        f"this run reads them with {current_reading}; a partial rebind would read "
                        "a divided spelling two ways in one run, so re-bind them all"
                    )
                    raise ValueError(message)
    wanted = [_squashed(spelling) for spelling in spellings if _squashed(spelling)]
    reread: set[str] = set()
    filesystem: set[str] = set()
    for identifier, name, entry_type in catalog.query(
        "SELECT entry_id, name, entry_type FROM entries "
        "WHERE NOT is_deleted AND entry_kind = 'filesystem'"
    ).fetchall():
        filesystem.add(str(identifier))
        if entry_type == "directory" or any(word in _squashed(str(name)) for word in wanted):
            reread.add(str(identifier))
    shapes = {
        str(identifier): str(digest)
        for identifier, digest in catalog.query(
            "SELECT entry_id, skeleton_hash FROM name_shapes"
        ).fetchall()
    }
    return frozenset(reread), shapes, frozenset(filesystem)


def _carry_name_readings(
    parent: Path, staging: Path, *, keep: frozenset[str], run_id: str
) -> dict[str, int]:
    """Copy the parent run's name tables' rows for the entries kept, under this run's id."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from ingence.storage.index_store import COMPRESSION, ROW_GROUP_SIZE, read_table

    carried: dict[str, int] = {}
    kept = pa.array(sorted(keep), type=pa.string())
    for model in (Component, DictionaryBinding, NameShape):
        spec = table_for(model)
        table = read_table(parent / spec.directory, spec)
        table = table.filter(pc.is_in(table["entry_id"], value_set=kept))
        if table.num_rows == 0:
            carried[spec.name] = 0
            continue
        for column, value in (("run_id", run_id), ("schema_version", SCHEMA_VERSION)):
            index = table.schema.get_field_index(column)
            table = table.set_column(
                index, column, pa.array([value] * table.num_rows, type=pa.string())
            )
        target = staging / spec.directory / "carried-names.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            table,
            target,
            compression=COMPRESSION,
            row_group_size=ROW_GROUP_SIZE,
            write_statistics=True,
            store_schema=True,
        )
        carried[spec.name] = table.num_rows
    return carried


def _error_root(error: ScanError, declaration: RootDeclaration) -> bool:
    """Whether a stored scan error was recorded under this root, by the identifier it names.

    A scan error carries its path's identifier, which hashes the root, so it is matched by
    recomputing that identifier here rather than by a column the record does not have.
    """
    return error.entry_id == entry_id(
        declaration.root_id,
        error.relative_path,
        (),
        case_sensitivity=declaration.case_sensitivity,
    )


def _moved_entry(
    entry: Entry, earlier: Entry, previous: PreviousRecord | None, move: Move
) -> Entry:
    """The new entry of a moved file: where it came from, how and when, and what a dive had read
    of it."""
    carried: dict[str, object] = {
        "moved_from": earlier.entry_id,
        "moved_by": move.by,
        "moved_at_ns": move.at_ns,
    }
    if previous is not None and previous.scan_outcome is not ScanOutcome.METADATA_ONLY:
        carried["scan_outcome"] = previous.scan_outcome
    if earlier.extraction_profile is not None:
        carried["extraction_profile"] = earlier.extraction_profile
    if previous is not None and previous.event_time is not None:
        instant, source, tier = previous.event_time
        if TimeSource(source) in _CONTENT_CLOCKS:
            carried.update(
                event_time_ns=instant,
                event_time_source=TimeSource(source),
                event_time_tier=ClockTier(tier),
            )
    return entry.model_copy(update=carried)


def _rekeyed(reading: Observation, new_of: Mapping[str, str], *, run_id: str) -> Observation:
    """A dive's reading of a file that moved, under the file's new entry identifier."""
    entry = new_of[reading.entry_id]
    return reading.model_copy(
        update={
            "entry_id": entry,
            "observation_id": observation_id(
                entry=entry,
                observer=reading.observer,
                observer_version=reading.observer_version,
                key=reading.key,
                locator_chain=reading.locator_chain,
            ),
            "run_id": run_id,
        }
    )


def _kept_with_members(unchanged: Collection[str], members: Mapping[str, Entry]) -> set[str]:
    """The unchanged files, and every carried member whose container is one of them or is a
    member kept the same way, so a member of a member follows its container."""
    kept = set(unchanged)
    progressed = True
    while progressed:
        progressed = False
        for member in members.values():
            if member.entry_id not in kept and member.parent_entry_id in kept:
                kept.add(member.entry_id)
                progressed = True
    return kept


def _carried_members(live: Mapping[str, Entry], seen: Collection[str]) -> dict[str, Entry]:
    """Return the members of containers this census walked, as the last run held them.

    A member is carried unchanged where its container was walked, or was itself carried,
    so a member of a member follows its container. A dive that reads the container again
    replaces all of them with what it finds, which may be less.
    """
    standing = set(seen)
    pending = sorted(
        (
            entry
            for entry in live.values()
            if entry.entry_kind is EntryKind.DERIVED and entry.entry_id not in standing
        ),
        key=lambda entry: entry.entry_id,
    )
    carried: dict[str, Entry] = {}
    progressed = True
    while progressed:
        progressed = False
        for entry in pending:
            if entry.entry_id in carried or entry.parent_entry_id not in standing:
                continue
            carried[entry.entry_id] = entry
            standing.add(entry.entry_id)
            progressed = True
    return carried


def _tombstone(previous: Entry, *, run_id: str) -> Entry:
    """Return the row recording that an entry has gone, keeping what it was.

    The row keeps the kind, path, name and depth of the row it replaces, and its type and
    parent beside them, so a deleted file and a member whose container went can be told
    apart and found again. A member also keeps where it sat inside its container,
    which a derived entry cannot be recorded without. Without these fields the row would be
    blank: an empty path and name, the kind a filesystem entry defaults to, and depth zero.
    """
    return Entry(
        entry_id=previous.entry_id,
        entry_kind=previous.entry_kind,
        root_id=previous.root_id,
        relative_path=previous.relative_path,
        name=previous.name,
        parent_entry_id=previous.parent_entry_id,
        depth=previous.depth,
        entry_type=previous.entry_type,
        locator_chain=previous.locator_chain,
        transformation=previous.transformation,
        transformation_version=previous.transformation_version,
        ordinal=previous.ordinal,
        is_deleted=True,
        resolution_status=ResolutionStatus.UNEXAMINED,
        first_seen_run=previous.first_seen_run,
        last_seen_run=run_id,
        scan_outcome=ScanOutcome.METADATA_ONLY,
    )


def _listed_type(item: os.DirEntry[str]) -> EntryType:
    """Return what a listed name is, from the listing and without following a link.

    The census never looks further at a name it leaves out, so the type is the one the
    directory listing gives, and a name whose type cannot be told is counted as other
    rather than dropped.
    """
    try:
        if item.is_symlink():
            return EntryType.SYMLINK
        if item.is_dir(follow_symlinks=False):
            return EntryType.DIRECTORY
        if item.is_file(follow_symlinks=False):
            return EntryType.FILE
    except OSError:
        return EntryType.OTHER
    return EntryType.OTHER


def excluded_names(counts: CensusCounts) -> tuple[ExcludedNames, ...]:
    """Return a census's exclusion counts as the run record carries them, canonically ordered."""
    return tuple(
        ExcludedNames(
            root_id=root_id, reason=reason, pattern=pattern, entry_type=entry_type, count=number
        )
        for (root_id, reason, pattern, entry_type), number in sorted(
            counts.excluded.items(),
            key=lambda item: (item[0][0], item[0][1].value, item[0][2] or "", item[0][3].value),
        )
    )


def _merge_counts(target: CensusCounts, source: CensusCounts) -> None:
    """Fold one root's counts into the run totals."""
    target.entries += source.entries
    target.directories += source.directories
    target.files += source.files
    target.symlinks += source.symlinks
    target.other += source.other
    target.bytes_seen += source.bytes_seen
    target.errors += source.errors
    target.reuse.update(source.reuse)
    target.extensions.update(source.extensions)
    target.excluded.update(source.excluded)
    target.unchanged |= source.unchanged
