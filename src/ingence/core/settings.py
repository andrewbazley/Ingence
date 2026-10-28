"""Engine settings: what governs how the engine runs, never what it believes.

The dividing line is a test: does changing this alter what the engine concludes, or only how
fast it gets there? Concurrency, flush intervals, buffer sizes, sampling budgets and scan depth are
operational, and they live here. How a name is interpreted, when two files count as replicates, and
what licenses a derivation are interpretive commitments. They live in the Rulebook, and mixing them
in here would dilute the distinction that makes the Rulebook worth having.

The line is not perfectly sharp and does not need to be. Some settings change what the engine finds,
and therefore what it can conclude from. A hashing policy that skips files above a threshold means
that duplicates above that size are never proven. That is a difference in evidence gathered and not
in how evidence is read, and it belongs here. Where a case is genuinely ambiguous, the question is
mainly where a control appears in the interface, and it can move later.

The engine stores settings as YAML beside the project, because a user is meant to read and edit
them, and YAML is diffable and takes comments. They are separate from the machine-written project
identity file, which nobody should hand-edit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from ingence import SCHEMA_VERSION
from ingence.core.extensions import canonical_extensions

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DASHBOARD_SETTINGS_FILE",
    "SETTINGS_FILE",
    "BeneathPrograms",
    "ColumnSettings",
    "CompletenessSettings",
    "DictionarySettings",
    "ExtractionSettings",
    "HashMode",
    "HashSettings",
    "JobSettings",
    "JournalSettings",
    "ScanSettings",
    "SearchSettings",
    "Settings",
    "SpendOrder",
    "ThemeSettings",
    "upgrade_settings_file",
    "upgraded",
]

#: Hand-editable settings, beside the machine-written ``project.json``.
SETTINGS_FILE = "settings.yaml"
#: The Dashboard's own settings file, laid over the hand-editable one. A commit on the Parameters
#: page writes here and never rewrites ``settings.yaml``.
DASHBOARD_SETTINGS_FILE = "settings.dashboard.yaml"


class HashMode(StrEnum):
    """How much of a file the engine reads in order to identify it."""

    #: Read nothing. The census default.
    NONE = "none"
    #: A bounded sample. Produces a candidate, never proof.
    QUICK = "quick"
    #: The whole file. The only mode that can prove byte identity.
    FULL = "full"
    #: Full below the size threshold, quick above it. The recommended policy: full hashing below the
    #: threshold is worth its cost because a negative result proves two files are *not* identical,
    #: which prunes candidate sets permanently.
    ADAPTIVE = "adaptive"
    #: What a comparison needs, whatever the depth. The engine hashes only what it needs to compare
    #: files and find duplicates. This is the default, and it is what a settings file that an older
    #: engine wrote reads as. The engine takes a sample of each file that shares its size with
    #: another file. It takes the whole of each file whose sample another file shares, so byte
    #: identity can be proven. A file of a size that nothing else has needs nothing to be told
    #: apart. Every other mode is a person's choice, and no depth overrules it.
    NEED = "need"


class SpendOrder(StrEnum):
    """The order a content pass spends its budget in."""

    #: Every file smallest first, in canonical order by identifier within a size. A fixed budget
    #: examines the most entries this way. On a large pool, the budget can go on the smallest files
    #: alone and reach no instrument file at all.
    SMALLEST_FIRST = "smallest_first"
    #: Families taken in turn: each family's smallest file, then each family's second smallest,
    #: and so on, smallest first within a turn. Every family gets its turn before any gets its
    #: second. So a vendor format whose reader needs only a header is reached in the first
    #: minutes and not never. A family is the file type that the Dictionary binds an extension to.
    #: Every extension bound to none shares one family, which takes its own turns over those
    #: extensions. So the order depends on the vocabulary that a run read.
    FAMILY_ROUND_ROBIN = "family_round_robin"
    #: The same rotation with the extension as the family. Earlier engines meant this by
    #: ``family_round_robin``. It is kept to be measured against: a pool's count of distinct
    #: suffixes decides how thinly the rotation spreads.
    EXTENSION_ROUND_ROBIN = "extension_round_robin"
    #: One file of every series before any series gets a second. A series is the files of one
    #: folder that share a name shape, an extension and a size class, as the file list records it
    #: (``series.key``). The series take their turns in the family rotation above, each by its
    #: first file. Within a series, two files are read: the first and the last by identifier, and
    #: any file whose size stands out. Where those two disagree on what the file is, who wrote it,
    #: its dimensions or its pages, twice as many are read, and so on until they agree or the
    #: series is read. The files not read stay unread, and a read file of the series describes
    #: each one. The content step of a first run reads this way.
    SERIES_FIRST = "series_first"


class BeneathPrograms(StrEnum):
    """What a dive does with the files beneath a program that the Rulebook defers beneath.

    A program's own files say nothing about an experiment. Every rule already defers beneath the
    folder that a project revision's ``defer_beneath`` names. This setting decides whether a dive
    also spends its budget there. On real laboratory folders, a dive that read in turn spent part
    of its budget on a copy of Fiji's libraries. The regions are the Rulebook's, read from the
    census that the dive starts from, so one declaration governs both.
    """

    #: Read them after every other file, in their own families' turns. A budget that runs out runs
    #: out on them, and one with room still reads them. So a folder wrongly taken for a program
    #: costs a later reading and never an unread one. This is the default, because what orders the
    #: spend vetoes nothing.
    READ_LAST = "read_last"
    #: Never read them. A dive leaves them as the census found them, unexamined, and counts them.
    #: Use this for a person who wants no dive of theirs ever spent on a program.
    NOT_READ = "not_read"
    #: Read them in their turns like any file. This choice is kept to be measured against.
    READ_IN_TURN = "read_in_turn"


#: An extension filter, held in the one form. A settings file that writes ``.TIF``, ``tif`` or
#: ``*.Tif`` names one extension, the census's ``tif``, and a compound keeps both parts
#: (``tar.gz``). The engine normalizes the filter where it reads the settings, so every reader of
#: the tuple compares what the Index stores.
ExtensionFilter = Annotated[tuple[str, ...], AfterValidator(canonical_extensions)]


class Setting(BaseModel):
    """Base for every settings group."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ScanSettings(Setting):
    """How the filesystem is walked."""

    #: Whether a walk keeps what a dive read of a file that has not changed at all. Where a file's
    #: stat fingerprint is the last run's, the walk takes the bytes as the ones that the dive read.
    #: The fingerprint is the file's kind, size, modification time and change time. The walk then
    #: carries the file's readings, outcome and content clock. Any change drops them, and a deleted
    #: file's go with it. When this is off, a walk states only what it saw.
    keep_dive_readings: bool = True
    #: Whether a walk knows a file that moved. A file at a path the last census did not have,
    #: whose name, size and modification time are those of exactly one file the last census had and
    #: this walk did not find, is that file moved. The entry records the file it came from
    #: (``moved_from``, ``moved_by``). A dive's readings of the file are carried to its new place
    #: as a candidate, and the next dive's digest confirms them. A person's decisions about the
    #: file are read there. When this is off, a moved file is a new file and the old one is a
    #: tombstone. The setting was added in schema 1.22.
    follow_moves: bool = True
    #: The threads that enumerate directories. Enumeration is I/O bound and not CPU bound.
    #: Concurrency matters most on a latency-bound network share, where a single-threaded walk
    #: spends its life waiting.
    workers: Annotated[int, Field(ge=1, le=256)] = 8
    #: Patterns excluded from the walk. Each is matched against the relative path and against the
    #: basename, so a bare name excludes that name at any depth and a path pattern excludes one
    #: specific location.
    #:
    #: These are ``fnmatch`` patterns and not shell globs, and the difference matters.
    #: ``fnmatch``'s ``*`` already crosses separators. A pattern written ``**/node_modules/**``
    #: requires something before the name, and it silently fails to exclude one at the top level.
    #: Bare names are what these patterns have always meant.
    #:
    #: An excluded directory is not descended into, so excluding a name costs nothing for
    #: everything beneath it.
    exclude_globs: tuple[str, ...] = (
        ".git",
        ".svn",
        ".hg",
        "__pycache__",
        "node_modules",
        ".Trash",
        ".Trashes",
        "$RECYCLE.BIN",
        "System Volume Information",
        ".ingence-case-probe",
    )
    #: Dotfiles and dot-directories. The default is off, because they are usually tooling and not
    #: research data. Turn it on when a user says so.
    include_hidden: bool = False
    #: Stop descending past this depth. ``None`` means no limit.
    max_depth: int | None = None
    #: Do not cross a filesystem boundary. Stops a walk wandering into a mounted
    #: backup volume or a network share nobody meant to index.
    one_file_system: bool = True
    #: Index the projects and runs that the census recognizes inside a pool as ordinary folders,
    #: instead of leaving them out. The default is off, because an engine that indexes its own
    #: output as data is what recognition exists to prevent. Turn it on where a pool deliberately
    #: keeps an earlier run's results as data. The run record lists each one either way, and the
    #: engine never indexes the installation.
    index_recognised_outputs: bool = False
    #: The number of records read per directory batch before the walk flushes them. Frequent small
    #: flushes are correct for durability and wrong for read performance. Compaction reconciles
    #: the two afterwards.
    flush_every_records: Annotated[int, Field(ge=1)] = 4096
    flush_every_seconds: Annotated[float, Field(gt=0)] = 30.0

    #: Symlinks are recorded and never followed. Not configurable: following
    #: them turns a tree into a graph, makes containment stop being a forest,
    #: and can turn a walk into an infinite one.
    follow_symlinks: bool = Field(default=False, frozen=True)

    #: Whether a symlink's target is ``stat``-ed to learn whether it is there. This is different
    #: from following a link, and that difference is the reason the setting above stays shut.
    #: Resolving costs one ``stat`` and changes nothing about the walk. Following changes what the
    #: walk is. The default is on, because a link whose target is gone and a link whose target is
    #: elsewhere are different facts, and only one of them is somebody's data.
    #:
    #: The setting can be switched off, because a ``stat`` through a dead network mount can block
    #: for the mount's own timeout, and a pool full of links onto an unmounted share is exactly
    #: where that bites. The census records a refusal, a loop or a timeout as ``unreadable`` and
    #: never as broken.
    #:
    #: The walk never follows a link whose text points outside the root, whatever this says. It
    #: records the link as ``outside_root``, read from its text. On a laboratory server, a link is
    #: often an archive's link to another server's path. The walk follows a chain hop by hop. It
    #: reads and tests each hop's text the same way. The first hop that leaves the root makes the
    #: link ``outside_root``, and the walk only ever looks at what lies inside the root. A loop, or
    #: a chain longer than the census's ``LINK_HOPS``, is ``unreadable``.
    resolve_symlinks: bool = True

    #: Whether a walk refuses to read a copy as a move. The default is off. A file matched by
    #: name, size and time that the filesystem says is another file is a copy whose original has
    #: gone. The filesystem says this when the file has another file number on the same volume, or
    #: when it was born after the file it would have moved from. By default, a copy that keeps its
    #: original's name, size and time, with the original removed, is read as a move, on every
    #: system. The entry then records ``MovedBy.COPIED`` (the move says that it was a copy), and
    #: what was read and decided about the original follows it. When this is on, a copy is a new
    #: file and follows nothing.
    moves_refuse_copies: bool = False
    #: A folder moved by its contents: at least this many of its files, and more than
    #: ``move_folder_min_share`` of the files and folders it held, arrived together in one new
    #: folder. The default is two, so that one file alike in name, size and time to another never
    #: moves a folder by itself. This was measured on reorganised copies of two censuses.
    move_folder_min_files: Annotated[int, Field(ge=1)] = 2
    #: "Most of its files matching one to one": more than this share of what the folder held, and
    #: more than this share of what the new folder holds.
    move_folder_min_share: Annotated[float, Field(gt=0, le=1)] = 0.5
    #: When more files than this share one name, size and time on either side, the walk does not
    #: weigh that key pair by pair. The pairs a walk weighs grow as the product of the two sides,
    #: and this bounds them. Such a key tells no file apart by itself and votes for no folder. The
    #: walk looks up each of its files only in the folder that moved by its other files.
    move_candidates_cap: Annotated[int, Field(ge=1)] = 64
    #: On one volume, the file's own number decides. Where both censuses give the same device and
    #: both give an inode, the same inode confirms a move, because a rename keeps it. Another inode
    #: is a copy, whatever the birth times say, because a clone keeps the birth. This gives one
    #: answer on every system, since Linux's stat gives no birth. Turn it off where a filesystem's
    #: inode numbers do not survive a rename. A network share that makes them up from the path
    #: would read every move there as a copy.
    moves_by_file_number: bool = True
    #: Whether a dive checks a move that a walk carried without reading the file. Where the
    #: departed file's digest was carried, the same digest of the file where it is now confirms the
    #: move (``sampled_digest``, ``whole_digest``). A different digest undoes it
    #: (``digest_differs``): the dive drops the file's readings, reads it as new, and decisions
    #: stop following it. A hash mode of ``none`` is a person's word that nothing is read to
    #: identify a file, and it checks nothing.
    moves_confirmed_by_dive: bool = True


class HashSettings(Setting):
    """Which files are hashed, how, and to what depth."""

    #: A mode set here is never overruled by a dive's depth. ``none``, ``quick``, ``full`` and
    #: ``adaptive`` are a person's choice. ``need`` leaves the choice to the engine (see
    #: :class:`HashMode`), and the run record says which one chose.
    mode: HashMode = HashMode.NEED
    #: What ``need`` is read as. ``comparison`` is what comparing the pool's files needs.
    #: ``depth`` is the digest that the dive's depth preset names: ``quick`` for standard and
    #: ``adaptive`` for deep. Earlier engines chose the digest that way, and the choice is kept to
    #: be measured against.
    need_from: Literal["comparison", "depth"] = "comparison"
    algorithm: str = "blake3"
    chunk_bytes: Annotated[int, Field(ge=4096)] = 1 << 20
    #: Above this size, adaptive mode sketches instead of reading the whole file. Failing to
    #: detect a duplicate at any scale is unacceptable, and a long first scan is acceptable, so
    #: this is set generously.
    full_hash_max_bytes: Annotated[int, Field(ge=0)] = 2 << 30
    #: Skip files larger than this entirely. ``None`` means no ceiling.
    max_file_bytes: int | None = None
    #: When non-empty, only these extensions are hashed.
    include_extensions: ExtensionFilter = ()
    exclude_extensions: ExtensionFilter = ()
    #: Never hash across a network share by default at any size, because hashing is slow and it
    #: hammers the share for every other user on it.
    #:
    #: The setting has three answers, and the absence of one:
    #:
    #: - ``null``, absent, or ``true``: no choice, and the depth proposes. The depth proposes to
    #:   skip a share, except at deep. The engine reads ``true`` as no choice, because ``init``
    #:   wrote ``true`` into every settings file at one time. Every depth's preset overwrote it,
    #:   so a ``true`` in a file cannot be told from a person's.
    #: - ``false``: a person's choice to hash shares at every depth.
    #: - ``always``: a person's choice to withhold every digest on a share, including the digests
    #:   of ``need``. No ``init`` ever wrote this value, so it is always a person's.
    #:
    #: A depth's proposal to skip withholds only what the modes that hash every file ask
    #: (``quick``, ``full``, ``adaptive``). It never withholds ``need``'s digests, because
    #: comparing and deduplicating is what a share dive is for. The run records who chose
    #: (``scan.profiles.remote_choice``).
    #:
    #: A dive enforces this setting (``scan.shares`` names each root's filesystem once). Earlier
    #: runs hashed across shares whatever the setting said, because no pass could tell a share
    #: from a local disk.
    skip_remote_filesystems: bool | Literal["always"] | None = None


class ExtractionSettings(Setting):
    """How hard the engine works to read inside files.

    The deepening budget governs this and nothing else. It never governs overall engine behavior,
    and an exhausted budget can never cause a run to fail. It leaves a complete run with entries
    honestly marked unexamined.

    The processing fingerprint hashes these settings. A fingerprint that ignores a policy that it
    should cover is a silently poisoned cache.
    """

    enabled: bool = False
    #: Names of registered extractors permitted to run. Empty means all
    #: registered and available ones. Nothing is addressed by name outside this
    #: list, which is what makes an extractor removable.
    adapters: tuple[str, ...] = ()
    max_sample_bytes: Annotated[int, Field(ge=0)] = 4 << 20
    #: The bytes of a text file that its near-duplicate sketch reads. The sketch's cost grows with
    #: the bytes it reads, and logs that filled an older 1 MiB sample took most of the sketch's
    #: time in a 900 s dive, in a measurement on a dived folder. 256 KiB costs less than half as
    #: much there and keeps each file's sketch under a quarter second. A smaller figure is cheaper
    #: still and says less about a long log's later lines.
    text_sketch_bytes: Annotated[int, Field(ge=1024)] = 256 << 10
    max_text_chars: Annotated[int, Field(ge=0)] = 200_000
    max_file_bytes: int | None = None
    include_extensions: ExtensionFilter = ()
    exclude_extensions: ExtensionFilter = ()
    #: Wall-clock ceiling for one file's extraction, enforced by the parent of a supervised child
    #: process. A file that exceeds it is recorded as a content error, because the work was
    #: attempted: the child started, ran and was killed, and something is known to be wrong with
    #: either the file or the parser. ``DEFERRED`` means the opposite. Content work was never
    #: attempted because the run's budget ran out. Conflating the two would make "we never looked"
    #: indistinguishable from "we looked and it fought back". Neither outcome is reusable, so a
    #: later run retries either way.
    subprocess_timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    #: Seconds of ceiling added per gigabyte of the file being read, on top of the base above.
    #: ``0`` restores a flat ceiling.
    #:
    #: A constant ceiling is a size-dependent policy that pretends not to be one. At 60 seconds
    #: flat, a small file gets a minute it cannot use. A large instrument file is killed for being
    #: large, and the run records a content error, the outcome that means "we looked and it fought
    #: back", on a file that was merely big. Neither judgment is about the file's health, which is
    #: what that outcome is for.
    #:
    #: The rate is deliberately generous and not tuned: three minutes as the order for a gigabyte,
    #: so a reader doing honest work on a large file is never the thing that trips. A ceiling
    #: exists to stop a parser that has stopped making progress. One that also stops slow but
    #: working parsers is measuring the wrong thing. The run record states the base and the rate,
    #: so what bounded a run is legible afterwards.
    subprocess_timeout_per_gigabyte_seconds: Annotated[float, Field(ge=0)] = 120.0
    #: How many files the content pool reads at once: a number, or ``auto``. The default is
    #: ``auto``, because the right number is the machine's (its cores, its memory, its share) and
    #: never the project's. The rule for ``auto`` takes the least of the cores and half the
    #: machine's memory divided by one worker's ceiling (``subprocess_memory_bytes``). It allows at
    #: most eight where a root lies on a network share, never fewer than one, and no more than one
    #: worker per sixty-four files that the pass will read (``executors.pool.FILES_PER_WORKER``).
    #: The last term comes from a measurement on a sample collection of small files. Each worker
    #: costs about 1.2 s to start and warm, so sixteen workers read the collection more slowly than
    #: one did. The files never cut a number that a person writes here.
    workers: Annotated[int, Field(ge=1, le=256)] | Literal["auto"] = "auto"
    #: The wall-clock ceiling of one file's whole reading, detection and identity included.
    #: ``subprocess_timeout_seconds`` bounds the extractor's child alone. The default is sixty
    #: seconds, the child's own order, so the pass gives up on a file that fights back within a
    #: minute, whichever step it fights in.
    file_seconds: Annotated[float, Field(gt=0)] = 60.0
    #: The wall-clock ceiling of one content pass. The default is ``None``: the depth profiles set
    #: it, and a pass asked for by hand runs to its end.
    total_seconds: Annotated[float, Field(gt=0)] | None = None
    #: How long a pass waits for a root that stopped answering before it keeps what it read and
    #: marks the rest unexamined. The default is five minutes. A share that drops for longer is
    #: gone for the session, and a pass that waits forever is one that nobody can finish.
    root_wait_seconds: Annotated[float, Field(ge=0)] = 300.0
    #: The number of consecutive access failures, across two or more folders, after which a pass
    #: looks at the root. The default is twenty. A handful of files can be gone or unreadable on
    #: any drive, and twenty in a row in more than one folder is not a handful. A root checked on
    #: every failure would stat the mount for every missing file.
    root_loss_failures: Annotated[int, Field(ge=1)] = 20
    #: The seconds between retries of a root that stopped answering, within ``root_wait_seconds``.
    #: The default is fifteen. A share that comes back comes back in seconds to a minute. A retry
    #: is one stat of the mount, so fifteen seconds is often enough to notice and rare enough to
    #: put no load on a server that is struggling.
    root_retry_seconds: Annotated[float, Field(gt=0)] = 15.0
    #: Whether an extractor that declares :class:`Isolation.SUBPROCESS` actually gets one. This is
    #: a feature flag and not a branch, so the isolated and unisolated paths can be measured
    #: against each other. Turning it off restores the behavior where one hostile file hangs the
    #: whole scan, which is why it is on.
    use_subprocess_isolation: bool = True
    #: Whether a large file's header is read before its body, and its body only where the header
    #: left the question open.
    #:
    #: The threshold is ``max_sample_bytes``, and not a new number. Above it, a content reading is
    #: already partial, so reading the header first costs nothing in completeness and can save the
    #: whole body reading. An extractor declares which kind of reading it does (``ReadingDepth``),
    #: so this needs no list of names.
    #:
    #: "The question" is what the file is and when it was made. A header reading settles it where
    #: the detectors named a type that they agreed on, or the format carried its own clock. Where
    #: the header settled neither, the body is read as usual.
    #:
    #: At any size where the file's own bytes name a binary format, a body of bytes that are not
    #: text holds nothing for a string harvest or a text sketch. Once its header reader has
    #: answered, the body readers are skipped below the threshold too. A text file, and a file
    #: that nothing named, are read whole.
    metadata_first: bool = True
    #: Whether the engine refuses a reading that the file's inode or device moved under. The
    #: engine compares a file's size and modification time before and after every reading, and a
    #: difference keeps none of the reading. Where this is on, it compares the inode and device
    #: too, which catches a file replaced under the same size and time. When it is off, a
    #: filesystem that invents an inode per lookup (some network and FUSE mounts) cannot make
    #: every reading on it look changed. The default is on. Turn it off on a mount that invents an
    #: inode per lookup.
    freshness_checks_identity: bool = True
    #: An ND2 read for its clock alone. When this is on, a file with one of
    #: ``clock_only_extensions`` is read by detection, by the digest that the hash mode names, and
    #: by the readers that declare a header read (the instrument extractor and its clock). No
    #: reader of the body reads it, the string harvest among them. Measured offline on ND2 files,
    #: this takes about half the time and a quarter of the bytes. The default is off, because the
    #: saving has not been measured on a network share.
    nd2_clock_only: bool = False
    #: The extensions ``nd2_clock_only`` reads for their clock alone, lower case, without a dot.
    clock_only_extensions: tuple[str, ...] = ("nd2",)
    #: Content larger than this many bytes is read behind the supervisor's boundary, even by an
    #: extractor that declares it runs in process, so the wall clock above applies to it. ``None``
    #: keeps every in-process extractor in process at any size.
    #:
    #: An extractor that declares it runs in process needs no ceiling on small content, but it
    #: does on medium text. An in-process reading has no ceiling. On real laboratory folders,
    #: files of a few megabytes took a large share of a 900 s budget, with no bound that could stop
    #: any of them. Routing by size keeps the declaration honest where it was measured to be. It
    #: reuses the boundary that every format parser already pays for. It makes a runaway reading a
    #: recorded outcome and not a spent budget.
    #:
    #: The default is a measurement and not a guess. On real laboratory folders, an entry of a
    #: text-like extension took a median of 0.055 s under 64 KB, 0.54 s between 64 and 256 KB, and
    #: 5.3 s between 256 KB and 1 MB. A supervised ``text`` child costs 0.144 s to start on the
    #: measuring machine (median of twelve). Above 256 KiB, supervision is about three per cent of
    #: the reading it bounds. Below 64 KB, it would nearly triple it.
    supervise_in_process_above_bytes: Annotated[int, Field(ge=0)] | None = 256 << 10
    #: Address-space ceiling for one supervised child, or ``None`` for none.
    #:
    #: This is not a memory budget and not a pool-size assumption. It does not grow with the size
    #: of the data pool, so the rule against inventing memory ceilings does not bite. It is derived
    #: from the read limits above and not guessed. An extraction may read ``max_sample_bytes`` and
    #: retain ``max_text_chars``, so a gigabyte is two orders of magnitude above anything a correct
    #: extraction can want, and it cannot fire on correct work. The engine enforces it with
    #: ``setrlimit`` on POSIX. It is unavailable on Windows, where the wall clock above is the only
    #: backstop.
    subprocess_memory_bytes: int | None = 1 << 30
    #: Resource limits on container expansion. These are correctness requirements from the first
    #: commit and not hardening added later: an archive bomb or a path-traversal archive in a data
    #: directory is not hypothetical.
    max_expansion_depth: Annotated[int, Field(ge=0)] = 4
    max_expansion_ratio: Annotated[float, Field(gt=0)] = 100.0
    max_expansion_members: Annotated[int, Field(ge=0)] = 10_000
    #: Whether a member recovered from inside a container becomes an entry of its own, and not
    #: only an observation about the container.
    #:
    #: Promotion is an event and not a default, and this is the switch that decides when the event
    #: fires. The setting is on, because making zipped content visible is the entire point of
    #: opening containers, and because a member that is genuinely the input to a downstream
    #: analysis needs to be something that an edge can point at. Off gives observations only, which
    #: is cheaper and keeps the entry table strictly filesystem objects.
    promote_container_members: bool = True

    #: Dictionary sections whose terms the byte scan looks for inside file content. Empty means no
    #: scan, which is the default, and that default rests on a measurement and not a preference.
    #:
    #: Scanning is gated on the Dictionary and not on a language, so its cost is proportional to
    #: what the project knows. That cost is real. The whole shipped vocabulary is roughly sixteen
    #: thousand byte patterns and scans at about 4 MB per second, against 36 MB per second for the
    #: negative lexicon alone. Over a large pool the difference is hours.
    #:
    #: So the scan is opt-in. The useful middle setting is
    #: ``("negative_lexicon", "filesystem_noise")``: the terms whose presence in content says most,
    #: at a tenth of the cost of everything.
    byte_scan_sections: tuple[str, ...] = ()

    #: The total work granted for content extraction across a run.
    deepening_budget_seconds: float | None = None

    #: The order in which the budget is spent. The order is a function of the census, these
    #: settings, the vocabulary and a person's answers, and it is canonical at every step. So two
    #: identical runs examine the same files in the same order. The dive's run record carries the
    #: vocabulary's fingerprint, because the default reads the vocabulary. The default is
    #: round-robin by family, where a family is a file type. The extension rotation and
    #: smallest-first stay selectable so that they can be measured against.
    spend_order: SpendOrder = SpendOrder.FAMILY_ROUND_ROBIN

    #: What a dive does with the files beneath a program: read them last (the default), never read
    #: them, or read them in turn. A program is a folder that the project's Rulebook defers beneath
    #: (``defer_beneath``). A project whose Rulebook defers beneath nothing is read as usual,
    #: whatever this says. The choice was measured on a stored dive before it was made.
    beneath_programs: BeneathPrograms = BeneathPrograms.READ_LAST


class CaseReadingSettings(Setting):
    """How the binder reads a spelling that a capital divides: whole, or as its pieces.

    The tokenizer cuts ``zibKelt`` into ``zib`` and ``Kelt`` and marks both with the whole
    spelling (``NameSlot.case_whole``). The binder then reads each spelling one way for the whole
    run (:mod:`ingence.dictionary.case_reading`). A person's answer ("one word" or "separate words")
    wins. If the Dictionary knows the whole spelling, the binder reads it whole. Otherwise, a glued
    spelling that the laboratory writes at least as often as it writes its rarest piece alone is a
    unit, or one word. Two words that are each written alone more often than together are two.
    """

    #: Whether the binder reads spellings by evidence at all. When this is off, it reads every
    #: piece on its own, as tokenizer 8 did.
    enabled: bool = True
    #: How many times its rarest piece's standalone count a glued spelling must be written to be
    #: read as one word. The default is one half. The spelling is a unit where the laboratory
    #: writes it glued at least half as often as it writes that piece alone. A value of one prefers
    #: neither reading. A measurement found that one half agrees best with a person's own verdicts
    #: on which divided spellings are one word. A larger value divides more spellings, and a
    #: smaller one keeps more whole. A partial rebind is refused across a change to this value,
    #: and the run states it.
    whole_over_alone: Annotated[float, Field(gt=0.0)] = 0.5
    #: What happens where a spelling that the counts read whole has no sense in the Dictionary.
    #: ``pieces``, the default, binds a piece that the Dictionary knows with its own sense. It
    #: leaves a piece that it does not know as a piece of the unknown whole, so a spelling with no
    #: known piece is still one word. So a glued run whose one half is a known word keeps that
    #: word. ``whole`` binds every piece as the unknown whole (one word, asked about), and each
    #: piece keeps the sense it has alone as a shadowed alternative. A person's answer that a
    #: spelling is one word is read as ``whole`` either way. A partial rebind is refused across a
    #: change to this value, and the run states it.
    unknown_whole: Literal["whole", "pieces"] = "pieces"


class GluedWordsSettings(Setting):
    """How a run of letters glued from two Dictionary words is read.

    ``channelhoechst`` divides into ``channel`` and ``hoechst`` where the whole is not a Dictionary
    word, each half is one at least ``half_minimum`` letters long, and one division is plainly the
    best. The pieces carry the whole spelling, so the case reading and a person's "one word" settle
    it as they settle a spelling that a capital divides. The engine records both settings in every
    component's normalizer version wherever they are not the default (tokenizer 13).
    """

    #: ``divide``, the default, or ``whole``, which reads every such run as one word, as tokenizer
    #: 12 did.
    reading: Literal["divide", "whole"] = "divide"
    #: The shortest half that a glued run divides into. The default is four letters. It was
    #: measured on the shipped Dictionary's single words. With the whole-word guard off, 82 words
    #: divide at three letters, 46 at four and 22 at five. With the guard on, none divide. A value
    #: of three admits ``mode|led``, and a value of five loses ``dapi``, ``mask``, ``cell`` and
    #: ``data``.
    half_minimum: Annotated[int, Field(ge=1)] = 4


class SpellingSettings(Setting):
    """Which known words a word that nothing binds is offered as a slip of.

    The Words section shows the offer beside the word's question ("Perhaps a slip for ..."). It
    never binds the word. A person's answer binds the word, on the next index or rebind, as every
    answer does.
    """

    #: How many edits a slip may be from the word offered. An edit is a letter added, dropped or
    #: changed, or two neighboring letters swapped. The default is two.
    edits: Annotated[int, Field(ge=1, le=3)] = 2
    #: The fewest letters that the word and the word offered have. The default is six. A shorter
    #: word is two edits from too many others.
    minimum_letters: Annotated[int, Field(ge=3)] = 6
    #: How many words are offered, nearest first. The default is three, as many as a person reads
    #: at a glance.
    candidates: Annotated[int, Field(ge=1)] = 3


class VariantSettings(Setting):
    """Which spellings the Words section offers as possibly the same word.

    A word that nothing binds is shown with the spellings in the run that may be the same word,
    each with the reason. The sources are a slip a letter or two away (``dictionary.spelling``
    sets how far), the same word with other capitals or separators, a plural, the pieces of a
    spelling that a capital divides, initials, and two spellings that take each other's place. The
    section shows counts and never a probability, and it only offers. A person says which are the
    same thing, a different thing of one kind, or unrelated. Each source has its own switch. None
    changes what a run reads, so none waits for Re-run.
    """

    #: How many variants a word shows, nearest first, then by files. The default is five, as many
    #: as a person reads in one glance at a row.
    shown: Annotated[int, Field(ge=1, le=50)] = 5
    #: Known words that the word is a slip of (``dictionary.spelling``'s limits), and other words
    #: of the run that it is a slip of.
    slips: bool = True
    slips_among_unknown: bool = True
    #: Two unknown spellings that are alike once capitals and separators are taken out.
    case_and_separators: bool = True
    #: A word and its regular plural, by the rule the themes use.
    plurals: bool = True
    #: The pieces of a spelling that a capital divides, and the spelling that a piece is part of.
    glued: bool = True
    #: A short spelling written in capitals whose letters are the initials of a known multi-part
    #: word, where both are in the run. The letters it may have are two to six, a size that
    #: abbreviations take. No measurement supports these limits.
    initials: bool = True
    initials_minimum_letters: Annotated[int, Field(ge=2, le=10)] = 2
    initials_maximum_letters: Annotated[int, Field(ge=2, le=10)] = 6
    #: Two spellings that stand where the other could, in names of folders close together and
    #: never in one name, with words in common beside them. The next three settings bound this
    #: source. The words in common that count are two, because one shared word is a frequent one.
    #: The most names that a shared word may be in before it says nothing is 200, because then it
    #: is in every name. The number of unknown words compared, most files first, is 2,000, because
    #: the comparison grows with the square of them. No measurement supports these values.
    same_slot: bool = True
    same_slot_neighbors: Annotated[int, Field(ge=1, le=20)] = 2
    same_slot_hub_names: Annotated[int, Field(ge=2)] = 200
    same_slot_words: Annotated[int, Field(ge=2)] = 2_000


class DomainSkewSettings(Setting):
    """When a folder's own data decides between a word's domain meanings.

    A word that two domain packs spell the same collides. Where the files of the folder lean far
    enough one way, the engine takes that domain without a question. An even skew is asked about
    only where enough files hang on the answer. Nothing here changes a word that a person defined.
    """

    #: The fewest files that must vote before the folder decides. Each file is read as one
    #: domain's. The default is twenty. Below that number, one subfolder's files would decide a
    #: word for the whole folder.
    minimum_files: Annotated[int, Field(ge=1)] = 20
    #: The share of the voting files that the leading domain must hold. The default is three in
    #: five: a majority with room to spare and not a coin's edge.
    minimum_share: Annotated[float, Field(gt=0, le=1)] = 0.6
    #: The number of times the runner-up's files that the leading domain must hold. The default is
    #: three. Two domains at six and four meet a share alone, and they do not meet a ratio.
    minimum_ratio: Annotated[float, Field(ge=1)] = 3.0
    #: A collision with an even skew becomes a question only above this many files. The default is
    #: twenty. A question about a word that a dozen files carry is not worth a person's minute.
    question_above_files: Annotated[int, Field(ge=0)] = 20


class DictionarySettings(Setting):
    """What the engine asks the Dictionary about a name.

    This group is separate from :class:`ExtractionSettings`, which governs what the engine asks
    about content. The two have opposite economics, and one setting would hide that. Content
    scanning is metered in megabytes and defaults off, on a measurement. A name is a few dozen
    characters that the census already holds in memory.
    """

    #: Tokenize every entry's name and record what the Dictionary made of it.
    #:
    #: The default is on, on a measurement. Tokenising the names of a whole source tree costs a
    #: small fraction of a second against a census of seconds, because most names never reach the
    #: unit matcher at all. A quantity-dense pool is the expensive case at roughly 10,000 names per
    #: second, which is still well under a tenth of the walk.
    tokenise_names: bool = True

    #: How a glued run of letters and digits is read. ``letter_led`` reads a run that begins with a
    #: letter as one word (``vorn3d``, ``Q3XY``). It reads a run that begins with a number as a
    #: quantity and what it quantifies (``3mMvorn``). ``whole`` reads every glued run as one word.
    #: ``split`` is the reading of earlier engines. The choice was measured on two laboratory
    #: folders. The engine records the value in every component's normalizer version wherever it is
    #: not the default.
    glued_runs: Literal["letter_led", "whole", "split"] = "letter_led"

    #: The years that a date written in a name may fall in, the first and the last inclusive. A run
    #: shaped like a date whose year falls outside is read as the numbers it is written with, such
    #: as a sample's ``4217-09-13`` or a counter's ``6-2-013``. The default is a threshold chosen
    #: against a measurement of laboratory censuses, argued at ``DEFAULT_PLAUSIBLE_YEARS`` in the
    #: normalizer. Its last year is a date that will pass, so a project may move it. The engine
    #: records the value in every component's normalizer version wherever it is not the default.
    plausible_years: tuple[int, int] = (1990, 2035)
    #: The years that a date written in a name may fall in, as the person names them before a run. A
    #: year is a date only among the years that the person says the data spans, and six-digit date
    #: codes are read the same way. The values are the first and the last year, inclusive. The last
    #: may be ``present``, for the reading run's own year, so a rerun is reproducible from the years
    #: that its run record states (``RunRecord.date_years``). A date-shaped run outside the years is
    #: the numbers it is written with, such as a sample's number. Tokenizer version 9 and later read
    #: this setting. ``plausible_years`` above is kept so that a project written before it still
    #: loads, and nothing reads it. The first year defaults to 2013, because an earlier year was
    #: probably a sample's number in the laboratory folders that were measured.
    years: tuple[int, int | Literal["present"]] = (2013, "present")

    #: How six digits standing alone are read. ``year_first`` reads ``YYMMDD`` as a date inside the
    #: years. ``none`` reads six digits as the number they are. The engine never reads day first,
    #: because a day-first code would read as another real date, unmarked. It records the value in
    #: every component's normalizer version wherever it is not the default.
    six_digit_dates: Literal["year_first", "none"] = "year_first"

    #: The number of lower-case key-value pairs (``sub-01``, ``run-02``) that a name must write
    #: before each is read as one indexed label. The default is two. It was measured on two
    #: laboratory censuses, where a lone pair was a reagent or a day, and its number appeared
    #: thousands of times. The engine records the value in every component's normalizer version
    #: wherever it is not the default.
    key_value_pairs: Annotated[int, Field(ge=1)] = 2

    #: How the binder reads a spelling that a capital divides.
    case_reading: CaseReadingSettings = CaseReadingSettings()

    #: Whether a folder's series of short dates settles their order. ``settle``, the default,
    #: reads the ambiguous siblings in the one field order that every sibling allows. ``off``
    #: leaves them to a dated folder alone. One sibling can decide a folder, which is why a person
    #: can switch it off. The engine records the value in every component's normalizer version
    #: wherever it is not the default (tokenizer version 10).
    date_series: Literal["settle", "off"] = "settle"

    #: How a run of letters glued from two Dictionary words is read.
    glued_words: GluedWordsSettings = GluedWordsSettings()

    #: Which known words a word that nothing binds is offered as a slip of.
    spelling: SpellingSettings = SpellingSettings()

    #: Which spellings the Words section offers as possibly the same word.
    variants: VariantSettings = VariantSettings()

    #: The fewest letters that a word read as a plural has. The default is four. A three-letter
    #: word that ends in ``s`` is a unit (``ms``, ``ns``) or an abbreviation far more often than
    #: the plural of a two-letter word, and no shipped two-letter spelling is a word that a plural
    #: is made from. The engine records the value in every component's normalizer version wherever
    #: it is not the default.
    plural_minimum: Annotated[int, Field(ge=2)] = 4

    #: When a folder's own data decides between a word's domain meanings.
    domain_skew: DomainSkewSettings = DomainSkewSettings()

    @model_validator(mode="after")
    def _years_run_forward(self) -> DictionarySettings:
        """Check that the years are a first and a last, with the first no later than the last."""
        first, last = self.years
        if isinstance(last, int) and first > last:
            message = (
                "dictionary.years needs a first year and a last year (or 'present'), the first no "
                f"later than the last. You gave {list(self.years)!r}."
            )
            raise ValueError(message)
        return self

    def years_read_on(self, started_at: str) -> tuple[int, int]:
        """Return the years that a run starting at ``started_at`` (an ISO instant) reads dates in.

        ``present`` is the run's own year. A start that names no year, such as a test's placeholder,
        is read as this year by the clock.
        """
        first, last = self.years
        if last != "present":
            return first, last
        year = started_at[:4]
        return first, int(year) if year.isdigit() else datetime.now(UTC).year

    #: Ask the Dictionary what each component means, and record the answer.
    #:
    #: This setting is separate from tokenising, because the two halves of reading a name are
    #: different kinds of work. Splitting a name into comparable atoms is mechanical, cheap and
    #: certain. Deciding that ``md`` denotes markdown is interpretation, and the engine records it
    #: at the semantic rung to say so.
    #:
    #: Switching this off records what every name says and nothing about what it means. That makes
    #: deferring the Dictionary until higher-confidence evidence has been tried a settings change
    #: and not a rewrite. A run that did not bind writes no binding rows at all, and its run record
    #: says the binder was inactive, so the absence is readable.
    bind_terms: bool = True

    #: The packs that a laboratory installed into this project and that a run loads, by name and in
    #: this order. A pack adds vocabulary at the ``pack`` origin, level with the core. So a spelling
    #: that both claim is a reported tie and never a silent win. ``ingence teach pack install``
    #: copies one in and names it here.
    packs: tuple[str, ...] = ()
    #: Which of the eight shipped domain packs load. ``all`` loads every pack. ``named`` loads only
    #: the domains listed in ``domain_packs_named``, which makes a domain opt-in and leaves the
    #: core and a person's answers as they were. The default is ``all``.
    domain_packs: Literal["all", "named"] = "all"
    #: The shipped domain packs loaded where ``domain_packs`` is ``named``.
    domain_packs_named: tuple[str, ...] = ()
    #: How many of a pool's files must be in an instrument's format before that format's pack is
    #: accepted by default. A pack accepts itself where it declares a format's spelling with
    #: ``accepts_pack`` under its ``origins`` (the microscopy pack: ND2) and the latest census
    #: holds this many files that carry the spelling. A person's refusal wins, and 0 switches the
    #: default off. The default is 3, the pack rule's own floor for an instrument's format alone
    #: (``minimum_files``). So the count that proposes the pack also accepts it, and "several" is
    #: one number in both places. No measurement supports the value.
    files_to_accept_a_pack: Annotated[int, Field(ge=0)] = 3

    #: Record where in each name the parts sat, as a separate shape record.
    #:
    #: The engine stores content either way. This setting governs position only, which makes
    #: "should content and position be weighed together" an experiment and not a storage rewrite.
    record_name_shapes: bool = True

    #: Whether each folder's organization is characterized: what mixture of name shapes it holds,
    #: whether its own name looks authored or emitted, the interval over which it accumulated
    #: content, and its bursts of activity.
    #:
    #: These are observations and never claims. Naming a regime, such as workflow bundle, type silo
    #: or date bucket, is a question for a person and a rule for later. A bundle-centric reading
    #: would make a well-organised type-silo laboratory look chaotic. The engine records the
    #: features, and no assignment scheme exists.
    #:
    #: This requires ``record_name_shapes``. Composition is a mixture of skeletons, and there is
    #: nothing to mix without them.
    characterise_organisation: bool = True

    #: How much larger than its local neighborhood a gap must be to end a burst. This is a starting
    #: position and not a measured default. No scale is correct everywhere: a four-second copy and a
    #: three-day analysis session are both real bursts. So the engine judges the cut locally, and
    #: this is the one parameter that the judgment takes.
    burst_gap_factor: Annotated[float, Field(gt=1.0)] = 4.0

    #: The number of neighboring gaps that the local median is taken over. It is odd, so the
    #: median is an element and not an average of two.
    burst_gap_window: Annotated[int, Field(ge=3)] = 9


class JournalSettings(Setting):
    """The development build's structured run journal.

    The journal records every parameter, timing, decision and run state in full, so that runs can
    be compared against each other and not only read. The engine writes structured rows and not
    free text, because the questions asked of the journal are queries. The journal can be switched
    off for the stable release.
    """

    enabled: bool = True
    #: Record one row per entry that a content pass examines. This is large, which is the reason
    #: it is separable. The census examines nothing and writes no per-entry row either way.
    #:
    #: The dive reads this setting. If nothing read it, a run "with per-entry journaling off" would
    #: measure nothing.
    per_entry: bool = True
    #: Timings for each stage of each entry: detection, each identity method, each
    #: Timings for each stage of each entry: detection, each identity method, each extractor and a
    #: container's members, on the entry's own row where ``per_entry`` writes one. The setting also
    #: covers the pass's per-stage totals on its summary row, and the timed phases of publishing,
    #: which the journal can see up to its own compaction. The dive reads this setting.
    timings: bool = True


class ListingSettings(Setting):
    """How an archive's listing is fetched and read as a root.

    A listing root is ``listing:<archive>:<identifier>``. The registry of listing sources fetches
    it into the project's own cache with ``ingence index --fetch``, and every ``index`` after that
    reads it offline. An archive is somebody else's server. The fetch makes one request at a time,
    pauses between requests, and sends a user agent that says what the fetch is. A refusal (429,
    503) stops that archive for the run, and the fetch does not retry it.
    """

    #: Seconds between two requests to one archive.
    pause_seconds: Annotated[float, Field(ge=0.0, le=60.0)] = 0.5
    #: What every request says it is.
    user_agent: str = "Ingence listing ingest (research pilot; local, read-only)"
    #: Whether the links that a listing declares enter the Index, as declared observations that a
    #: rule could read. A declared link says which file was made from which. The default is
    #: ``withheld``. A declared link is an answer key for scoring the engine's own derivation and
    #: identity proposals, and a run whose rules could read the key would score itself.
    #: ``recorded`` writes the links.
    declared_links: Literal["withheld", "recorded"] = "withheld"
    #: Ask the archive for each listed file's own headers (one ``HEAD`` request per file) where its
    #: listing states no time, to read when the archive wrote its copy. The default is off, because
    #: it costs a request per file.
    read_archive_clocks: bool = False
    #: The most bytes that a study's file lists may weigh before the fetch refuses them in words,
    #: and does not read them whole. A response larger than this, by its stated length or by what
    #: has arrived, is closed unread past the limit, and the fetch keeps nothing.
    max_listing_bytes: Annotated[int, Field(ge=1)] = 256 * 1024 * 1024
    #: The most files that a study may list before the fetch refuses the listing in words, and
    #: does not convert it whole.
    max_listing_files: Annotated[int, Field(ge=1)] = 2_000_000


class JobSettings(Setting):
    """How the Dashboard runs what a person's answers ask for.

    A job is the partial rebind of an answered spelling, or a re-run of committed changes. The
    server writes it to the project's job ledger before it runs, and it runs in a worker that the
    Dashboard's server supervises (``ingence.jobs``). None of these settings changes what a run
    concludes.
    """

    #: Where a job runs. ``subprocess`` runs it in a process of its own, so a worker's death leaves
    #: the server serving and the job recoverable. ``thread`` runs it in the server's own process,
    #: for a build that cannot start its own interpreter. The default is ``subprocess``.
    executor: Literal["subprocess", "thread"] = "subprocess"
    #: The number of times that an interrupted job is queued again before the engine marks it
    #: failed with its reason. A job interrupted by the Dashboard closing is queued again without
    #: spending one. The default is 2. Only interruptions that may be accidents of the machine
    #: spend these retries: a worker killed (by a person, or by the machine's memory) or found
    #: gone. A step that raises on its input fails at once and is never retried. A full disk fails
    #: at once, in words. A crash or a stopped silence that repeats in the same phase fails the job
    #: at the second occurrence, and not the third.
    retry_limit: Annotated[int, Field(ge=0, le=20)] = 2
    #: The seconds between a worker's heartbeats in the ledger, which the page shows as "last
    #: heard".
    heartbeat_seconds: Annotated[float, Field(gt=0.0, le=60.0)] = 2.0
    #: A worker not heard from for this long is reported silent. The count uses the supervisor's
    #: own clock, which stands still while the machine sleeps. A worker's heartbeat comes from a
    #: thread of its own, and one long call that holds the interpreter lock starves it. A sort of
    #: millions of items stopped the heartbeat for 16 s in a measurement. So a healthy job can be
    #: silent for a while. The default is 60 s. A whole rebind and inference of two large
    #: projects, run as Dashboard jobs on a laptop that was swapping, beat every 2 s at the
    #: median. They were never silent longer than 13.5 s, always in inference. 60 s is four and a
    #: half times that. Every job records its own longest gap (``beat_gap_max_seconds`` in its
    #: progress), so another machine's gap is readable.
    silent_seconds: Annotated[float, Field(gt=0.0, le=3600.0)] = 60.0
    #: Whether the engine stops a silent worker and runs its job again, spending a retry, once the
    #: worker has been silent for ``stop_silent_seconds``. The default is on. A frozen worker holds
    #: the project's worker lock, so without this every later answer waits behind the frozen one
    #: until a person closes the Dashboard. The page says that a worker is silent from
    #: ``silent_seconds`` on, and says when it will be stopped, so a slow machine is told long
    #: before anything is stopped.
    stop_silent: bool = True
    #: How long a worker must be silent before the engine stops it, where ``stop_silent`` says to.
    #: The value is never sooner than ``silent_seconds``. The default is 900 s, fifteen times the
    #: report threshold and sixty-seven times the longest healthy silence measured. A worker frozen
    #: by ``SIGSTOP``, or holding the interpreter lock in a loop, is silent from its first missed
    #: beat. The page says so at ``silent_seconds``, and the engine stops the worker here.
    stop_silent_seconds: Annotated[float, Field(gt=0.0, le=86400.0)] = 900.0
    #: The number of jobs' worker logs kept in ``jobs/logs/``, one file per attempt. The engine
    #: prunes them when a job ends. A job that is queued or running keeps its logs whatever this
    #: says, and so does a failed job that nothing has succeeded since. The default is 100. A
    #: healthy job's log is empty, because the steps report through their runs and not their
    #: output. A failed attempt's log holds its traceback, a few kilobytes. So this setting bounds
    #: how many files accumulate and not their size.
    logs_kept: Annotated[int, Field(ge=1, le=100000)] = 100


class InferSettings(Setting):
    """How an inference holds its pool: whole, or streamed folder by folder.

    None of these settings changes what an inference publishes. A streamed run and a whole run
    publish the same rows (``ingence.infer.streaming``). The settings decide how much of the pool
    the inference holds at once.
    """

    #: ``auto`` streams where the projected peak exceeds the machine's memory less the margin.
    #: ``always`` and ``never`` say so. A rule whose predicate declares no reach runs whole
    #: whatever this says, and the run record says why. The default is ``never``. In a measurement,
    #: streaming lowered the peak on no census, and the peak went up on a large generated census.
    #: Seven rules read a root or the whole pool and keep every entry's names held, so streaming
    #: does not lower the peak while they run.
    streaming: Literal["auto", "always", "never"] = "never"
    #: The number of entries that a chunk gathers, whole folders at a time. A folder that holds
    #: more is a chunk of its own. This is a granularity and not a ceiling. The default is 16,384.
    #: It was measured on a dived census, as the observations of one chunk against the time of
    #: loading each chunk again.
    entries_per_chunk: Annotated[int, Field(ge=1)] = 16_384
    #: What one entry costs the whole pass at its peak, in bytes. ``auto`` multiplies it by the
    #: pool's entries. The default comes from a measurement with every memory lever in use: the
    #: peak resident memory over entries on the largest stored census measured was 16.6 KiB an
    #: entry, rounded up to the next KiB. A small pool costs more an entry, because a fixed part is
    #: spread thin, and it never comes near the threshold.
    projected_bytes_per_entry: Annotated[int, Field(ge=1)] = 17_408
    #: The share of the machine's physical memory that ``auto`` leaves to everything else: the
    #: system, the Dashboard, and the merge that publishes the run. The default is a quarter. No
    #: measurement supports the value.
    memory_margin_fraction: Annotated[float, Field(ge=0.0, lt=1.0)] = 0.25
    #: Whether the pass suggests workflows and places files by the workflow in force. When this is
    #: on, the pass reads experiments and derivation candidates with the rules. It then suggests a
    #: workflow from the chains of file types that it saw, holds the workflow as assumed beside the
    #: ones that a person declared, and reads each file's stage by it (``stage_from_workflow``).
    #: When this is off, the pass suggests no workflow and reads only the declared ones. The
    #: evaluation's "workflow off" column is what the rules give without one.
    workflows: bool = True
    #: The fewest experiments that a suggested workflow must be counted from. One chain seen once
    #: is an observation, and two or more are a habit. The default is 2. A value of 1 suggests a
    #: workflow from a single experiment, for a small collection that a person wants read as it
    #: stands.
    workflow_minimum_experiments: Annotated[int, Field(ge=1)] = 2


class ThemeSettings(Setting):
    """How the themes of a run's experiments are drawn.

    None of these settings changes what a run publishes. Themes are a projection that the engine
    recomputes from a published run (``ingence.themes``). A theme is never a claim and never a
    question. Changing one of these settings changes only the projection, which the engine caches
    by the run and by these values together. Where a setting has a reason, it is written beside the
    setting.
    """

    #: Dictionary kinds whose words say how a file was made and never what it is about. The
    #: engine leaves them out of every theme. The default holds fourteen "how" kinds, plus
    #: ``experiment``, ``project``, ``status``, ``count`` and ``identifier``. The words
    #: ``experiment``, ``project``, ``failed``, ``n=3`` and a record number name no subject either.
    #: It also holds ``part``, because ``frame`` and ``segment``, read as plain words so that a
    #: counter glued to them splits off, name no subject. It also holds ``quantifier``, because a
    #: bare ``single`` says how many and never what.
    how_kinds: tuple[str, ...] = (
        "channel",
        "connective",
        "count",
        "dataset",
        "date",
        "experiment",
        "file_role",
        "format",
        "identifier",
        "measurement",
        "part",
        "process",
        "project",
        "quality",
        "quantifier",
        "replicate",
        "stage",
        "statistic",
        "status",
        "unit",
        "version",
    )
    #: Spellings that a person wants left out of every theme. The default is empty, because the
    #: shipped Dictionary binds all but a handful of the generic words to a kind above.
    ignored_words: tuple[str, ...] = ()
    #: A word that the Dictionary does not know and its regular plural are one word (s, es, ies
    #: to y), where both occur among the documents' words and neither is a sense. The plural is
    #: read as the singular and never onto a sense. The default is on. Without it, a word and its
    #: plural can stand side by side in one label, because the Dictionary folds a plural only onto
    #: a word that it knows.
    fold_unknown_plurals: bool = True
    #: A word that only a tool writes is left out. A word kept for a theme must appear in an
    #: experiment's folder name or a folder above it, or in the name of a file read as an
    #: acquisition.
    titles_or_acquisitions_only: bool = True
    #: The weight of a word in the experiment folder's own name, of a word in a folder below it,
    #: and of a word on files (``ln(1 + files)``). The defaults 3 and 1 have no recorded
    #: derivation.
    title_weight: Annotated[float, Field(ge=0.0)] = 3.0
    subfolder_weight: Annotated[float, Field(ge=0.0)] = 1.0
    #: The number of experiments that must share a word the Dictionary knows before the word
    #: counts. The default is two, because one experiment alone cannot co-appear with anything.
    min_experiments_sense: Annotated[int, Field(ge=1)] = 2
    #: The number of experiments of one collection that must share a word the Dictionary does not
    #: know. The default is three. An unknown word that keeps appearing beside other words is
    #: worth grouping by. Three is one more than a known word needs, so a sample's code typed twice
    #: is not a theme word. The count is within one collection, so a small collection's own words
    #: count however large the others are.
    min_experiments_unknown: Annotated[int, Field(ge=1)] = 3
    #: A word in more than this share of the weighted experiments is context and not a theme word.
    #: The default is one half, the value for one project. A lab-wide value would be a fifth.
    max_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    #: How collections of very different sizes are weighed. A collection can hold millions of
    #: machine-made files beside a small human dataset, and each should keep its themes. Each
    #: experiment weighs ``size ** (power - 1)``, so a collection weighs ``size ** power``. With 1,
    #: every experiment weighs alike. With 0, every collection weighs alike. The default is one
    #: half. It keeps the order, so a larger collection still weighs more, and it turns a
    #: thousand-fold difference into about thirty-fold. This was measured on a generated pool
    #: (``tests/test_themes_core.py``, the weighting measurement).
    collection_weight_power: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    #: A tiny collection is weighed as if it had this many experiments. Without a floor, a
    #: collection of one can weigh about forty times an average experiment beside collections of
    #: thousands. The default is ten. On an invented pool (``tests/test_themes_picks.py``), a
    #: one-item collection then weighs about twelve times an average, and the large collections move
    #: by under two in a hundred. A value of one sets no floor.
    collection_weight_floor: Annotated[int, Field(ge=1)] = 10
    #: What a collection is: ``0`` means each declared root, and ``1`` means each folder directly
    #: below a root.
    collection_level: Annotated[int, Field(ge=0, le=8)] = 0
    #: The most LSA dimensions. A blank value grows them with the distinct documents ``n``:
    #: ``max(50, 2 * ceil(1.5 * sqrt(n)))``. That is twice the top of the readable window of
    #: themes, so every theme the pick may choose has room. With a fixed 50, sixty planted themes
    #: over 2,500 experiments came back as 75 with a quarter not placed. 50 is the floor.
    lsa_dimensions: Annotated[int, Field(ge=2)] | None = None
    #: Whether word counts are dampened (``1 + ln``) before weighting by rarity. The default is
    #: off.
    sublinear_counts: bool = False
    #: The number of nearest neighbors that each experiment is joined to. A blank value means
    #: ``max(5, round(sqrt(n)))`` over distinct documents. There is no similarity threshold.
    neighbours: Annotated[int, Field(ge=1)] | None = None
    #: An experiment no closer to its theme than chance is not placed. Chance is the row-shuffle
    #: null: each document keeps its number of words and their counts, and draws which words they
    #: are in proportion to how many documents use each. An experiment's best similarity to
    #: another of its theme must beat this share of the similarities that chance gives each
    #: document's nearest ten. The default is three in four, which is looser than nine in ten. The
    #: themes list every experiment that is not placed. A blank value places every experiment that
    #: its community holds.
    chance_quantile: Annotated[float, Field(gt=0.0, lt=1.0)] | None = 0.75
    #: The number of shuffled corpora that the chance similarities are pooled over. The default is
    #: one. More shuffles steady the cutoffs and cost one embedding each. The shuffles' seeds are
    #: 0, 1, and so on.
    chance_replicates: Annotated[int, Field(ge=1, le=100)] = 1
    #: Which similarities chance is read from. ``nearest_ten`` takes every cutoff from each
    #: shuffled document's ten nearest. ``matched`` takes the edge cutoffs from each one's nearest
    #: neighbours, as many as the observed graph joins each experiment to. It takes the placement
    #: cutoff from each one's single nearest, because placement tests an experiment's single
    #: closest. Matched is the fairer comparison and the stricter. On an invented pool of sixty
    #: themes over 2,500 experiments, it left 19 in 100 experiments unplaced against 2 in 100, with
    #: no gain measured. On pools of a dozen distinct documents it leaves nothing placed. The
    #: default is ``nearest_ten``, because ``matched`` has not been measured on real projects.
    chance_null: Literal["nearest_ten", "matched"] = "nearest_ten"
    #: A neighbor in another collection no closer than chance is not joined. The same null and
    #: share apply to the graph's edges between collections before the themes are drawn, so one
    #: collection's words never pull another's experiments in by chance. The default is three in
    #: four. A blank value joins every nearest neighbor.
    edge_chance_quantile: Annotated[float, Field(gt=0.0, lt=1.0)] | None = 0.75
    #: The same threshold, for neighbors within one collection. The default is blank: within a
    #: collection, every nearest neighbor is joined. On generated pools, blank recovered weakly
    #: written and uneven themes better than three in four. But blank can merge a machine
    #: collection's campaigns that share the machine's words, and beside many campaigns it can
    #: merge a small human collection's themes. Three in four keeps those apart.
    edge_chance_quantile_within: Annotated[float, Field(gt=0.0, lt=1.0)] | None = None
    #: Whether a gathered vertex keeps its weight in the graph. Identical documents are one
    #: vertex. When this is on, the vertex carries a self-loop of their weight times the copies
    #: each would be joined to. The default is off. In a test on a small machine-made collection,
    #: the self-loops outweighed every edge. A collection of 3 campaigns came back as 12 themes,
    #: and beside 60 campaigns a human collection of 4 themes came back as 1. The weight still
    #: enters the rarity weights and the LSA.
    vertex_weight_in_graph: bool = False
    #: The resolutions tried for the fine themes. There must be at least one, and each is above 0.
    fine_resolutions: Annotated[
        tuple[Annotated[float, Field(gt=0.0)], ...], Field(min_length=1)
    ] = (0.3, 0.5, 0.7, 1.0, 1.4, 2.0, 3.0)
    #: A pick at the edge of the sweep is tried past it. Where the resolution kept is the finest
    #: (or coarsest) tried and steadier than the one inside it, the sweep tries one more beyond it.
    #: The step is the ratio between the two outermost resolutions (3.0 then 4.5). The sweep takes
    #: at most this many steps each way. A step that only ties ends the sweep, and the tie goes to
    #: a resolution already offered. The default is two. On the generated pool of six themes
    #: (``tests/test_themes_picks.py``), a sweep that offered only resolutions that were too fine
    #: picked its edge. Two steps put the pick inside the resolutions tried, and the planted themes
    #: were recovered better (AMI 0.857 to 0.961). Zero tries only the resolutions given.
    resolution_edge_steps: Annotated[int, Field(ge=0, le=8)] = 2
    #: The resolutions tried for the broad themes, which are drawn from the fine ones.
    broad_resolutions: Annotated[
        tuple[Annotated[float, Field(gt=0.0)], ...], Field(min_length=1)
    ] = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
    #: The number of seeds that each resolution is drawn with. Seed agreement compares seed 0 with
    #: the rest. Each seed varies the LSA and the communities both.
    seeds: Annotated[int, Field(ge=2, le=64)] = 8
    #: Which seed's placement is shown. Every seed gets the same final placement: experiments no
    #: closer than chance are taken out, groups are split into their connected parts, and small
    #: groups are dropped. The adjusted Rand index compares every pair of seeds on that placement.
    #: ``medoid`` shows the seed most like the others (the highest mean index, with ties going to
    #: the lower seed). ``first`` shows seed 0.
    final_placement: Literal["medoid", "first"] = "medoid"
    #: A community smaller than this is not a theme. The result lists its experiments as not
    #: placed and never forces them into a neighbor. The default is three, the size asked of a theme
    #: before a year's story says that it began or ended, and of a person before they count in it.
    min_theme_size: Annotated[int, Field(ge=1)] = 3
    #: The number of different documents that a theme needs. Identical documents are one point, so
    #: experiments that read the same words (the same name, or the same study text deposited again)
    #: are never a theme on their own. The result lists them as not placed, because the chance test
    #: already set the point apart from its neighbors. The default is two: one document co-appears
    #: with nothing, as a known word needs two experiments.
    min_theme_documents: Annotated[int, Field(ge=1)] = 2
    #: A sense leads a label only where it is concentrated in the theme: at least this share of
    #: the sense's weight lies in the theme's experiments. The default is one half, the share asked
    #: of a theme's specific word (``specific_min_specificity``). A sense that is spread over every
    #: theme stays in its place by distinctiveness.
    label_sense_specificity: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    #: The number of words in a theme's label, and the number of words kept per theme. By default
    #: three words are shown of six kept.
    label_words: Annotated[int, Field(ge=1)] = 3
    theme_words: Annotated[int, Field(ge=1)] = 6
    #: How a theme's words are ranked. ``tfidf`` ranks a word by the share of its weight that lies
    #: in the theme, times the log of how concentrated it is there (a class-based TF-IDF).
    #: ``log_odds`` ranks a word by the z-score of its log-odds between the theme and the rest of
    #: the experiments, on how many experiments hold it, with a Dirichlet prior drawn from all the
    #: experiments (Monroe, Colaresi and Quinn, 2008, the informative-prior form). TF-IDF is the
    #: default and has been measured. Log-odds has not.
    label_method: Literal["tfidf", "log_odds"] = "tfidf"
    #: The prior of the log-odds ranking, as a total count of experiments spread over the words in
    #: proportion to how many experiments hold each. A larger number pulls a rare word's score
    #: toward zero. The default is 100, a starting point of a few percent of a mid-sized project.
    #: No measurement supports the value.
    label_prior_strength: Annotated[float, Field(gt=0.0)] = 100.0
    #: How many times the experiments are drawn again with replacement, to see how steady each
    #: label is. There is no refit: every experiment keeps its theme, and only the words' counts
    #: change. Each label word then has the share of the draws in which it stayed in the label. The
    #: value 0 draws none.
    label_bootstrap: Annotated[int, Field(ge=0, le=1000)] = 100
    #: What a person is, for who else: the folder this many levels below each root. Level 1 is the
    #: first level, the common layout of one folder per member. A blank value means nobody. The
    #: engine never reads it from names, and the Dashboard flags the value as the default.
    person_level: Annotated[int, Field(ge=1, le=8)] | None = 1
    #: What an area is: the folder this many levels below each root, whatever it is named for (a
    #: person, an instrument, a year, a project). A blank value means no area. This is the same
    #: number as ``person_level`` under the name that the newer pages use. A file that sets one
    #: sets the other. A file that sets both to different values is refused by name.
    area_level: Annotated[int, Field(ge=1, le=8)] | None = 1
    #: The number of experiments that a person needs in a theme or with a word before they count
    #: there. The default is three.
    person_min_experiments: Annotated[int, Field(ge=1)] = 3
    #: Rising or falling: a going theme's share of the last this many years against its share of
    #: the years before. The default is two, the span that ``going`` already allows (last seen in
    #: the latest year or the one before).
    direction_recent_years: Annotated[int, Field(ge=1)] = 2
    #: The ratio of the two shares that counts as rising, and its inverse counts as falling. The
    #: default is one and a half: a half again more, or a third less. This is a stated choice and
    #: has not been measured.
    direction_ratio: Annotated[float, Field(gt=1.0)] = 1.5
    #: Years with fewer experiments than this get no line in the year profile. A theme is named
    #: over its usual share in a year only with this many experiments that year. The default is
    #: two.
    year_min_experiments: Annotated[int, Field(ge=1)] = 2
    #: A theme's specific word is carried by at most this share of the theme, because more is the
    #: theme's context. At least two of the theme's experiments carry it, and at least
    #: ``specific_min_specificity`` of the experiments that carry it are in the theme. The defaults
    #: are 0.8 and 0.5.
    specific_max_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.8
    specific_min_specificity: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    #: Words used in each other's place (a line handed on). Their contexts are at least this alike,
    #: by the cosine of their co-appearance profiles. They are found together in at most this share
    #: of the experiments that either word is in. The defaults are 0.35 and 0.1.
    substitute_min_similarity: Annotated[float, Field(ge=0.0, le=1.0)] = 0.35
    substitute_max_together: Annotated[float, Field(ge=0.0, le=1.0)] = 0.1

    @model_validator(mode="before")
    @classmethod
    def _area_level_and_person_level_are_one_setting(cls, data: object) -> object:
        """``area_level`` and ``person_level`` are one number under two names.

        The validator works on the raw mapping, because a settings group is frozen. A file that sets
        one of them sets the other to the same value. A file that sets both to different values is
        refused with both names. Earlier code reads ``person_level`` and later code reads
        ``area_level``. A run that read one number for "a person" and another for "an area" would
        count the same folders twice over.
        """
        if not isinstance(data, dict):
            return data
        has_area = "area_level" in data
        has_person = "person_level" in data
        if has_area and has_person:
            if data["area_level"] != data["person_level"]:
                message = (
                    f"themes.area_level ({data['area_level']!r}) and themes.person_level "
                    f"({data['person_level']!r}) are one setting under two names and must "
                    "agree: set one of them, or both to the same value"
                )
                raise ValueError(message)
            return data
        if has_area:
            return {**data, "person_level": data["area_level"]}
        if has_person:
            return {**data, "area_level": data["person_level"]}
        return data


class CompletenessSettings(Setting):
    """How much analysis an experiment shows, as the engine found it.

    A projection reads these settings after inference, and no rule reads them (see
    ``ingence.completeness``). None of them changes what a run publishes. They decide which
    experiments the Dashboard lists as having "no analysis found", and which reason it gives. The
    wording is "found in what was read" and never "never analyzed": a lack of either raw data or
    analysis files does not mean that they do not exist. It means that the engine did not find
    them.
    """

    #: The number of raw acquisitions that an experiment needs before the Dashboard judges it. The
    #: default is 5. With fewer, a share of followed acquisitions is too coarse to read, because
    #: one of two is a half.
    judged_from_acquisitions: Annotated[int, Field(ge=1, le=1_000_000)] = 5
    #: The share of an experiment's raw acquisitions found followed by an analysis file, below
    #: which the Dashboard lists the experiment. The default is 0.5: under half followed.
    listed_below_share: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    #: The number of analysis files found in an experiment that count as an analysis of its own,
    #: whatever its share. The default is 5. This rule catches per-image measurement tables, which
    #: a per-acquisition rule misses.
    own_analysis_files: Annotated[int, Field(ge=1, le=1_000_000)] = 5
    #: The number of results tables found in an experiment that count as an analysis of its own.
    #: The default is 3, because summary tables are an analysis.
    own_result_tables: Annotated[int, Field(ge=1, le=1_000_000)] = 3
    #: Reason "forgotten": the rest of the experiment's theme has its raw acquisitions found
    #: followed by analysis at least this share. The default is 0.5, or "at least half".
    forgotten_at_least: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    #: Reason "a line that stopped": the rest of the experiment's theme has its raw acquisitions
    #: found followed below this share, so the line of work ended and one experiment was not
    #: forgotten. The default is 0.2. Between this value and ``forgotten_at_least`` the Dashboard
    #: gives no reason, and it still lists the experiment.
    stopped_below: Annotated[float, Field(ge=0.0, lt=1.0)] = 0.2

    @model_validator(mode="after")
    def _stopped_below_forgotten(self) -> CompletenessSettings:
        if self.stopped_below > self.forgotten_at_least:
            message = (
                f"completeness.stopped_below ({self.stopped_below}) must not be above "
                f"completeness.forgotten_at_least ({self.forgotten_at_least}). A theme cannot be "
                "both mostly analyzed and mostly not analyzed."
            )
            raise ValueError(message)
        return self


class DesignSettings(Setting):
    """How an experiment's design grid is drawn from its files' names.

    A projection reads these settings after inference, and no rule reads them (see
    ``ingence.design``). None of them changes what a run publishes. The grid reads a file's name
    parts, its condition class and the run's conclusions about it. It draws each varying condition
    as a factor and each combination of levels as a cell. Every default is a first guess that has
    not been measured on a laboratory's names. The first setting to turn off is ``enabled``.
    """

    #: Whether the design grid is computed and drawn. The default is off, because on a sample
    #: collection the grid recovered only about a tenth of the conditions that an experiment's
    #: names vary. Turn it on to see a grid of those conditions. The Rules page switches off the
    #: rule that orders a series, ``succeeds_by_series_index``, which ships off.
    enabled: bool = False
    #: Whether two parts of a name that always go together are one factor, such as ``GFP`` always
    #: beside ``488``. Off, each part is a factor of its own.
    merge_parts: bool = True
    #: The number of files that each level of a part needs before the part may be merged with
    #: another.
    merge_minimum_observations: Annotated[int, Field(ge=1, le=1_000_000)] = 2
    #: The share of an experiment's files that may be off the pairing of two parts while the parts
    #: still go together. With 0.1, one exception in 14 files is about 7 per cent.
    merge_exception_share: Annotated[float, Field(ge=0.0, le=0.5)] = 0.1
    #: Whether a file with no condition class is drawn when its name has a recorded layout. The
    #: replicate grouping publishes no class for a condition with one file. With this off, every
    #: single-replicate condition is left out of the grid, and its cell reads "not found" though its
    #: file exists. With it on, files of scripts, figures and documents whose names merely share a
    #: layout are drawn too, and they make grids of their own. The default is off, because the
    #: classes gate the grid.
    include_unclassed: bool = False
    #: The most levels that a part of a name may take and still be a condition. A part with more
    #: values than this is an identifier or a frame number, and an experiment did not compare it.
    factor_levels_at_most: Annotated[int, Field(ge=2, le=1_000_000)] = 50
    #: The most combinations of levels listed as not found. The grid counts a larger set of
    #: combinations and does not list them.
    cells_listed_at_most: Annotated[int, Field(ge=1, le=1_000_000)] = 2000


class QuestionSettings(Setting):
    """How the question sheet is cut: what a person is asked, with each answer a class.

    None of these settings changes what an inference concludes. The sheet is read from a published
    run and decides only which questions it shows, and in which order. The defaults come from an
    earlier run whose sheet a person read. No measurement against answers has tested them since.
    """

    #: The most questions on the sheet. No measurement has tested the default of 50.
    sheet_budget: Annotated[int, Field(ge=1, le=10_000)] = 50
    #: The most words standing between senses that the sheet asks about, most files first. The
    #: default is 10.
    sheet_tied_words: Annotated[int, Field(ge=0, le=10_000)] = 10
    #: The share of the classifying claims that the first audits are chosen to cover. The default
    #: is 95%.
    audit_cover: Annotated[float, Field(gt=0.0, le=1.0)] = 0.95
    #: The most audits asked before the fill, whatever they cover. The default is 20.
    audit_first: Annotated[int, Field(ge=0, le=10_000)] = 20
    #: The share of the proposed derivations that the first patterns are chosen to cover. The
    #: default is 90%.
    pattern_cover: Annotated[float, Field(gt=0.0, le=1.0)] = 0.90
    #: The most patterns asked before the fill. The default is 12.
    pattern_first: Annotated[int, Field(ge=0, le=10_000)] = 12
    #: A pattern is unusual at no more than this many pairs, or ``atypical_share`` of all pairs
    #: where that is more. The default is 2.
    atypical_floor: Annotated[int, Field(ge=0, le=10_000)] = 2
    #: The share of all pairs at or under which a pattern is unusual. The default is 0.001.
    atypical_share: Annotated[float, Field(ge=0.0, le=1.0)] = 0.001
    #: The most questions about unusual pairs. The default is 5.
    atypical_most: Annotated[int, Field(ge=0, le=10_000)] = 5


class SearchSettings(Setting):
    """What the search box reads beside its results.

    None of these settings changes a run. A search reads a published run and writes nothing into it.
    """

    #: The fewest matched units that a word must share with the query to be listed as coming with
    #: it. Pointwise mutual information is largest for the rarest words, so a word in one matched
    #: unit and nowhere else would lead the list from a single coincidence. The default is 2, the
    #: least count that is a pattern and not one name. No measurement supports the value beyond that
    #: reason.
    co_occurring_minimum: Annotated[int, Field(ge=1)] = 2
    #: The number of words that come with the query and are listed. The default is 12, a strip
    #: beside the results and not a page. No measurement supports the value.
    co_occurring_limit: Annotated[int, Field(ge=0, le=200)] = 12
    #: The shortest typed word that the search finds where a name's word begins with it or a name
    #: holds it anywhere. The default is 4. With 3, the tokenizer's own shortest case piece, a
    #: three-letter word on a large generated pool began words in many thousands of units, and the
    #: search took seconds instead of a fraction of a second. The Dashboard asks for a search once
    #: three letters are typed and the typing pauses. A person who wants three-letter words found
    #: inside longer ones sets 3 and accepts that cost.
    inside_min_length: Annotated[int, Field(ge=1, le=64)] = 4
    #: Whether a question is also answered with associated experiments. These are experiments that
    #: the question's words do not name, whose words include a word that comes with those words
    #: across the project. The word is one of the strongest Dictionary-bound co-occurring words, a
    #: handful, each shown. The default is off. On the sample collection, the tier added almost no
    #: right answers, and most of the experiments that it added were wrong. This is because the
    #: words that come with a question are a project's own cell line and target, so the tier pulls
    #: in every experiment of the project. When this setting is on, a question also answers with
    #: the associated tier, and each experiment says the words that brought it.
    associated_tier: bool = False


class ColumnSettings(Setting):
    """How tables with the same columns, and tables whose columns contain another's, are listed.

    None of these settings changes a run. The columns are read from what a dive stored, the index
    is recomputed from a published run when it is next read, and nothing here writes a claim. The
    rule that groups tables with the same columns is in the Rulebook. These settings govern the
    file page's lines and the inclusion index only.
    """

    #: Whether the inclusion index is built. The index lists, for a table, the tables whose columns
    #: contain its own. Off, the file page shows the tables with the same columns and no inclusion
    #: lines. The default is on, because the index reads names that the dive already stored.
    inclusion: bool = True
    #: The fewest columns that a table needs for the same-columns lines. This applies only where
    #: the rule ``same_column_schema`` is not in force. Where the rule is in force, the page follows
    #: the rule's own ``minimum_columns``, so the page and the rule never disagree. The default is
    #: two, the least that is more than one name.
    minimum_columns: Annotated[int, Field(ge=1, le=1000)] = 2
    #: The fewest columns that a table needs to be listed as contained in another. A table of two
    #: columns, such as a time and a value, is contained in a great many tables by chance. The
    #: default is three, the least at which a shared set of names is hard to reach by chance. No
    #: measurement supports the value.
    inclusion_minimum_columns: Annotated[int, Field(ge=1, le=1000)] = 3
    #: The number of files that a list names. The count beside the list is always the whole. The
    #: default is twenty, a short list on a page and not a wall of paths. No measurement supports
    #: the value.
    files_listed: Annotated[int, Field(ge=0, le=500)] = 20


class GroupSettings(Setting):
    """How the groups are made of words.

    The lattice keys its groups by the words of experiments' names. ``words_as`` says which word a
    name's bound word is. ``terms``, the default, reads a word that the Dictionary binds as its
    Dictionary term. Two spellings that a person called one thing are then one word, which sharpens
    the groups. ``spellings`` reads the word as the spelling that the name wrote. A word that no
    sense holds, or that two senses claim level, is its spelling either way. The lattice reads a
    person's stored approval of a group in whichever words it holds, so switching loses no approval
    (:mod:`ingence.grouping.lattice_words`). The setting changes what inference concludes, so a
    change waits for Re-run.
    """

    words_as: Literal["terms", "spellings"] = "terms"


class Settings(Setting):
    """Everything a user may tune about how the engine runs."""

    schema_version: str = SCHEMA_VERSION
    #: How deep a dive goes when the command does not say: ``metadata`` (never opens a file),
    #: ``standard`` or ``deep``. ``init`` declares it, so that a project whose pool sits on somebody
    #: else's share can say once that nothing is to be opened. The project then does not rely on
    #: whoever types the next command to remember. A profile's presets are applied over these
    #: settings at dive time and never written back (:func:`~ingence.scan.profiles.apply_profile`).
    #: So this setting stays the user's own choice, and the run record states what was in force. The
    #: engine validates it where it is read, which is where a wrong value has to produce a sentence
    #: that a person can act on.
    depth: str = "standard"
    scan: ScanSettings = ScanSettings()
    hash: HashSettings = HashSettings()
    extraction: ExtractionSettings = ExtractionSettings()
    dictionary: DictionarySettings = DictionarySettings()
    journal: JournalSettings = JournalSettings()
    listing: ListingSettings = ListingSettings()
    jobs: JobSettings = JobSettings()
    infer: InferSettings = InferSettings()
    completeness: CompletenessSettings = CompletenessSettings()
    design: DesignSettings = DesignSettings()
    themes: ThemeSettings = ThemeSettings()
    questions: QuestionSettings = QuestionSettings()
    search: SearchSettings = SearchSettings()
    columns: ColumnSettings = ColumnSettings()
    groups: GroupSettings = GroupSettings()

    @model_validator(mode="before")
    @classmethod
    def _an_older_file_s_hash_mode_was_nobody_s_choice(cls, data: object) -> object:
        """Read a settings mapping that an older engine wrote as this engine's.

        :func:`upgraded` does the reading.
        """
        return upgraded(data) if isinstance(data, dict) else data


#: The schema whose settings first said ``need``.
NEED_SCHEMA = "1.22"

#: Defaults that an engine changed after ``init`` had written them into projects. Each entry holds
#: the setting's path, its former default, and what it is now. ``init`` wrote every default into
#: ``settings.yaml`` for a time, so an older project holds each former value as if somebody chose
#: it, and the engine never overrules a written value. ``ingence fix`` names each one that it
#: finds. Deleting the line lets the current default apply.
FORMER_DEFAULTS: tuple[tuple[tuple[str, ...], object, str], ...] = (
    (
        ("dictionary", "case_reading", "whole_over_alone"),
        1.0,
        "0.5 now",
    ),
    (
        ("jobs", "stop_silent"),
        False,
        "true now (a silent worker is told, then stopped much later)",
    ),
    (
        ("dictionary", "case_reading", "unknown_whole"),
        "whole",
        "pieces now: a glued word keeps its known half",
    ),
)


def written_former_defaults(hand: dict[str, object]) -> list[tuple[str, object, str]]:
    """Return each setting that a hand file holds at a default that an engine has since changed.

    The result lists the dotted path, the value written, and what the default is now. It is a
    finding for a person and never a change, because whoever wrote the value may have meant it.
    """
    found: list[tuple[str, object, str]] = []
    for path, former, now in FORMER_DEFAULTS:
        value: object = hand
        for part in path:
            value = value.get(part, _NOT_WRITTEN) if isinstance(value, dict) else _NOT_WRITTEN
        if value is not _NOT_WRITTEN and value == former:
            found.append((".".join(path), value, now))
    return found


_NOT_WRITTEN = object()


def upgraded(data: dict[str, object]) -> dict[str, object]:
    """Return a settings mapping from before schema 1.22, read as 1.22 writes it.

    A ``hash.mode: none`` from before 1.22 is not a choice. ``init`` wrote every default into the
    file, ``none`` among them, and every dive overruled it with its depth's preset. So the value
    never said what anybody wanted. Read as written, it would now stop every dive of every older
    project from hashing anything. Read as ``need``, it leaves the choice to the engine, as it
    always did.

    The function also stamps the current version on the mapping. A ``none`` that a person says
    after this, saved to the file or laid over it by the Dashboard, then reads as theirs. The
    engine applies it where it reads the hand file (``core/project.read_settings_file``) and in
    :class:`Settings` itself. A mapping already at 1.22 or later is returned unchanged.
    """
    from ingence.core.versions import is_newer

    written = str(data.get("schema_version") or SCHEMA_VERSION)
    if not is_newer(NEED_SCHEMA, than=written):
        return data
    result = {**data, "schema_version": SCHEMA_VERSION}
    hashing = data.get("hash")
    if isinstance(hashing, dict) and (
        str(hashing.get("mode", "")).strip().casefold() == HashMode.NONE.value
    ):
        result["hash"] = {**hashing, "mode": HashMode.NEED.value}
    return result


#: What a dive says when it writes an older file's ``none`` as ``need``.
HASH_MODE_UPGRADED = (
    "hash.mode none, which init wrote into settings.yaml before 1.22 and every dive overruled, "
    "is now need there: write none in settings.yaml to take no digest at all"
)


def upgrade_settings_file(path: Path) -> bool:
    """Write an older settings file's ``hash.mode: none`` as ``need``, once, and say whether it did.

    The upgrade is written so that it happens once. :func:`upgraded` reads an older file's ``none``
    as ``need`` on every read, keyed on the file's own version. If nothing rewrote that version, a
    ``none`` that a person then wrote into the file would read as ``need`` for ever. A dive is the
    one command that takes digests. It writes the two lines, the mode and the version, in place and
    leaves every other line, a person's comments among them, as it was. After that, a ``none`` in
    the file is the person's. The function leaves a file alone if its lines are not in the form that
    ``init`` writes, and :func:`upgraded` reads that file.
    """
    import re

    import yaml

    try:
        text = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return False
    if not isinstance(loaded, dict) or upgraded(loaded) is loaded:
        return False
    hashing = loaded.get("hash")
    if not isinstance(hashing, dict) or str(hashing.get("mode", "")).strip() != HashMode.NONE.value:
        return False
    lines = text.splitlines(keepends=True)
    version = [index for index, line in enumerate(lines) if re.match(r"schema_version:", line)]
    section = [index for index, line in enumerate(lines) if re.match(r"hash:\s*$", line)]
    if len(version) != 1 or len(section) != 1:
        return False
    mode = []
    for index in range(section[0] + 1, len(lines)):
        if lines[index].strip() and not lines[index][:1].isspace():
            break
        if re.match(r"\s+mode:\s*['\"]?none['\"]?\s*$", lines[index]):
            mode.append(index)
    if len(mode) != 1:
        return False
    indent = lines[mode[0]][: len(lines[mode[0]]) - len(lines[mode[0]].lstrip())]
    lines[mode[0]] = f"{indent}mode: {HashMode.NEED.value}\n"
    lines[version[0]] = f"schema_version: '{SCHEMA_VERSION}'\n"
    rewritten = "".join(lines)
    check = yaml.safe_load(rewritten)
    if check.get("hash", {}).get("mode") != HashMode.NEED.value:
        return False
    from ingence.core.destinations import write_atomically

    write_atomically(path, rewritten)
    return True
