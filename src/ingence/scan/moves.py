"""Find files and folders that moved between two walks.

A file at a new path matches a file gone from its old path when both have the same name, size and
modification time, to the second. The match is one to one: a key that two files share matches
no file on its own. The filesystem's own clocks and file numbers, and a file's neighbours, confirm
a match or tell alike files apart.

A copy whose original is gone counts as a move, and the move says it was a copy. The filesystem's
evidence of a copy is another file number on the same volume, or a later birth time. That evidence
labels the move ``MovedBy.COPIED`` (``copied_same_bytes`` once a dive finds the same bytes). What
Ingence read and decided about the original follows the copy as it follows any move. The setting
``scan.moves_refuse_copies`` (off by default) refuses copies instead. With it off, a copy counts as
a move whether or not the system records birth times. Only the label differs: ``MovedBy.COPIED``
where a birth time or file number shows the copy, ``MovedBy.NAME_SIZE_TIME`` where nothing does.

The file's own number confirms a move on its volume. A rename keeps a file's inode. A copy never
does, a clone included. This rule applies when both censuses give the same device and both give an
inode (``scan.moves_by_file_number``, on by default). The same inode confirms the move
(``MovedBy.SAME_FILE``). Another inode is a copy, whatever the birth times say. The birth time
cannot decide here, because a clone keeps its original's birth time (APFS's ``cp -c``, and likely
Finder's copy on one volume, robocopy's ``/COPY:DAT``, rsync's ``--crtimes``). A system or
filesystem that keeps no birth time gives none to read. Where the device changed (a remount
renumbers it) or either side has no inode (Windows' directory listing gives none), the number
decides nothing and the birth times decide, as follows.

Time confirms a move and tells a move from most copies. On one volume a rename keeps a file's birth
time and modification time and changes its change time. A copy is usually born when it is made
(a clone is not, as above). Where both sides carry a filesystem birth time
(``TimeSource.BIRTHTIME``):

- Equal birth times confirm the match (``MovedBy.BIRTH_TIME``), unless the birth time equals the
  modification time. Setting a file's modification time earlier than its birth pulls the birth back
  on APFS and HFS+, and a copy that keeps the modification time does exactly that. Such a birth
  time is one clock read twice and confirms nothing.
- A later birth time on the arrived file makes it a copy (``MovedBy.COPIED``, or a refusal where
  ``scan.moves_refuse_copies`` is on). So does an earlier birth time that equals the arrived file's
  own modification time. On APFS and HFS+, setting a copy's modification time pulls its birth back
  to that time. The copy's birth is then earlier than the original's when the original was born
  after it was last modified, as a file copied onto a share is.
- Equal birth times also tell alike files apart. Of two departed files alike in name, size and
  time, the one whose birth time the arrived file keeps is the one that moved.

Where either side has no birth time and the file numbers say nothing, the match stands on name, size
and time (``MovedBy.NAME_SIZE_TIME``). A listing, a filesystem that keeps no birth time, and a
census written on Linux by a version that did not read birth times with ``statx`` all give none.

The census reads what each system gives (a birth time, file numbers) through ``core/platform.py``.
This module reads only what the two censuses recorded, never the machine it runs on, so a project's
moves are the same wherever its Index is read.

A change time between the two walks is the move's time (``Entry.moved_at_ns``). A move changes a
file's change time. If that time falls after the start of the walk that last saw the file at its old
place, and not after this walk, the move happened at or before that change time. Renaming a folder
changes the folder's change time and none of its files' change times, so a file moved with its
folder takes the later of its own and the folder's. A change time equal to the birth time counts as
none (on Windows, Python reads the creation time there). The change time is never the file's clock.

Neighbours break ties. A folder gone from its place, whose files arrived together in one new
folder, most of them one to one, has moved (``scan.move_folder_min_files`` and
``scan.move_folder_min_share``). Whatever the new folder is called, it records where it came from
(``MovedBy.CONTENTS``). Each of its files whose name, size and time matched more than one file is
told apart by that move (``MovedBy.NEIGHBOURS``). A folder that holds only folders moves by them,
level by level. A folder whose move rests on copies alone says so (``MovedBy.COPIED_CONTENTS``):
every file vote is a copy and every folder beneath moved by copies. Such a folder still moves, so
a folder made afresh from the same template files as a removed one takes its answers.

The module is a pure function of two censuses' entries and one moment, the moment the walk matched
them. That moment is the upper bound of a move's time. A change time later than that moment comes
from another machine's clock, and the module ignores it. The result is reproducible from the Index
except for that bound. Nothing here reads a file.

"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.ids import run_began_ns
from ingence.core.outcomes import TimeSource
from ingence.core.schema import UNCHECKED_MOVES, EntryKind, EntryType, MovedBy

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from ingence.core.schema import Entry
    from ingence.core.settings import Settings

__all__ = [
    "BIRTH_GRAIN_NS",
    "MOVE_TIME_GRAIN_NS",
    "UNCHECKED_MOVES",
    "Move",
    "MoveTally",
    "MovesFound",
    "birth_says",
    "filesystem_says",
    "find_moves",
    "move_key",
    "moved_at",
]

#: The grain a move's modification time is compared at: a second, because some filesystems and
#: every copy between them keep a time only to the second (``compare/fingerprints.py`` agrees).
MOVE_TIME_GRAIN_NS = 1_000_000_000

#: The grain two birth times are compared at: a microsecond. macOS hands Python a birth time as
#: a float of seconds, whose last bit at the present epoch is about 240 nanoseconds, so one file's
#: birth read on two walks is equal exactly while a birth pulled back to a modification time
#: differs from that time by up to half a bit. A microsecond covers the float and is far finer than
#: any copy: a copy is born at least one system call after its original.
BIRTH_GRAIN_NS = 1_000

#: What the filesystem says about one departed and one arrived file: the same file on its volume,
#: a birth time that confirms, a copy, or nothing.
SAME = "same_file"
CONFIRMS = "confirms"
COPY = "copy"


@dataclass(frozen=True, slots=True)
class Move:
    """Where one new entry came from, how that was known, and when the move happened at latest."""

    source: str
    by: MovedBy
    at_ns: int | None = None


@dataclass
class MoveTally:
    """What one root's matching found, in numbers the journal carries."""

    #: Files matched, and of them those confirmed as the same file on their volume, those
    #: confirmed by birth time, those told apart by a folder and those that are copies whose
    #: original is gone.
    files: int = 0
    by_same_file: int = 0
    by_birth_time: int = 0
    by_neighbours: int = 0
    by_copy: int = 0
    #: Arrived files every one of whose matches was a copy, where copies are refused.
    refused_as_copies: int = 0
    #: Folders moved by their contents, and of them those whose move rests on copies alone.
    folders: int = 0
    folders_by_copy: int = 0
    #: Arrived files whose key matched more than one file and that nothing told apart.
    left_ambiguous: int = 0
    #: Files with a move's time.
    with_time: int = 0

    def as_detail(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "by_same_file": self.by_same_file,
            "by_birth_time": self.by_birth_time,
            "by_neighbours": self.by_neighbours,
            "by_copy": self.by_copy,
            "refused_as_copies": self.refused_as_copies,
            "folders": self.folders,
            "folders_by_copy": self.folders_by_copy,
            "left_ambiguous": self.left_ambiguous,
            "with_time": self.with_time,
        }


@dataclass
class MovesFound:
    """Every move one walk found under one root: new file and new folder, each to its source."""

    files: dict[str, Move] = field(default_factory=dict)
    folders: dict[str, Move] = field(default_factory=dict)
    tally: MoveTally = field(default_factory=MoveTally)


def move_key(entry: Entry) -> tuple[str, int, int] | None:
    """What a moved file keeps: its name, its size and its modification time at the grain."""
    if entry.size_bytes is None or entry.modified_ns is None:
        return None
    return (entry.name, entry.size_bytes, entry.modified_ns // MOVE_TIME_GRAIN_NS)


def _birth(entry: Entry) -> int | None:
    if entry.created_time_source is not TimeSource.BIRTHTIME:
        return None
    return entry.created_ns


def birth_says(departed: Entry, arrived: Entry) -> str | None:
    """``CONFIRMS``, ``COPY`` or None: what two files' birth times say about one having moved."""
    before, after = _birth(departed), _birth(arrived)
    if before is None or after is None:
        return None
    if abs(after - before) < BIRTH_GRAIN_NS:
        modified = departed.modified_ns
        if modified is not None and abs(before - modified) < BIRTH_GRAIN_NS:
            return None
        return CONFIRMS
    if after > before:
        return COPY
    # Earlier: a rename never moves a birth, and a copy made where setting the modification time
    # pulls the birth back to it (APFS, HFS+) is born at that time, before an original born after
    # it was last modified. Any other earlier birth has no known cause, so it decides nothing.
    modified = arrived.modified_ns
    return COPY if modified is not None and abs(after - modified) < BIRTH_GRAIN_NS else None


def filesystem_says(departed: Entry, arrived: Entry, *, file_numbers: bool = True) -> str | None:
    """``SAME``, ``CONFIRMS``, ``COPY`` or None: what the filesystem says about one having moved.

    On one unchanged device with both inodes known (and ``file_numbers``), the same inode is the
    same file and another inode is a copy, whatever the birth times say. A rename keeps a file's
    number on every system that gives one. Otherwise the birth times decide (:func:`birth_says`).
    """
    one_volume = (
        file_numbers
        and departed.device is not None
        and departed.device == arrived.device
        and departed.inode is not None
        and arrived.inode is not None
    )
    if one_volume:
        return SAME if departed.inode == arrived.inode else COPY
    return birth_says(departed, arrived)


def _label(said: str | None, otherwise: MovedBy) -> MovedBy:
    """The ``moved_by`` a match takes: the filesystem's confirmation where it gave one, and a
    copy labelled as a copy however it was found."""
    if said == SAME:
        return MovedBy.SAME_FILE
    if said == CONFIRMS:
        return MovedBy.BIRTH_TIME
    if said == COPY:
        return MovedBy.COPIED
    return otherwise


def moved_at(departed: Entry, arrived: Entry, *, until_ns: int) -> int | None:
    """The arrived entry's change time where it falls between the two walks, else None.

    The window opens when the run that last saw the departed entry began (its identifier's stamp)
    and closes at ``until_ns``, the moment this walk matched it: a change time outside it is not
    this move's (earlier: the move did not touch it; later: another machine's clock).

    A change time that equals the birth time is not caused by a move. On Windows, Python's change
    time is the creation time. On POSIX, a change time equal to the birth time says the file was
    never touched after it was made, so it was not moved then either.
    """
    changed = arrived.changed_ns
    began = run_began_ns(departed.last_seen_run)
    if changed is None or began is None:
        return None
    born = _birth(arrived)
    if born is not None and abs(changed - born) < BIRTH_GRAIN_NS:
        return None
    return changed if began < changed <= until_ns else None


def _later(*moments: int | None) -> int | None:
    """The latest of the moments given, or None where none is."""
    known = [moment for moment in moments if moment is not None]
    return max(known) if known else None


def _is_filesystem(entry: Entry) -> bool:
    return entry.entry_kind is EntryKind.FILESYSTEM and not entry.is_deleted


def find_moves(
    held: Sequence[Entry],
    live: Mapping[str, Entry],
    *,
    gone: Collection[str],
    settings: Settings,
    until_ns: int,
) -> MovesFound:
    """Match the files and folders at new paths to those gone from their old ones (one root).

    ``held`` is every entry this walk found at a path the last census did not have; ``live`` the
    last census's entries, and ``gone`` those of them this walk neither found nor carried.
    """
    scan = settings.scan
    found = MovesFound()
    tally = found.tally
    gone_folders: dict[str, Entry] = {}
    departed: dict[tuple[str, int, int], list[Entry]] = {}
    for identifier in sorted(gone):
        earlier = live.get(identifier)
        if earlier is None or not _is_filesystem(earlier):
            continue
        if earlier.entry_type is EntryType.DIRECTORY:
            gone_folders[identifier] = earlier
            continue
        key = move_key(earlier) if earlier.entry_type is EntryType.FILE else None
        # An empty file carries no evidence of being any particular file, so it never moves.
        if key is not None and key[1] > 0:
            departed.setdefault(key, []).append(earlier)
    new_folders: dict[str, Entry] = {}
    arrived: dict[tuple[str, int, int], list[Entry]] = {}
    for entry in sorted(held, key=lambda held_entry: held_entry.entry_id):
        if not _is_filesystem(entry):
            continue
        if entry.entry_type is EntryType.DIRECTORY:
            new_folders[entry.entry_id] = entry
            continue
        key = move_key(entry) if entry.entry_type is EntryType.FILE else None
        if key is not None and key[1] > 0:
            arrived.setdefault(key, []).append(entry)

    # Every pair a key admits, less the copies a birth time names.
    admitted: dict[tuple[str, int, int], list[tuple[Entry, Entry, str | None]]] = {}
    refused: set[str] = set()
    #: Keys held by more files than the cap: weighed pair by pair nowhere, looked up by folder.
    over_cap: list[tuple[str, int, int]] = []
    for key in sorted(departed.keys() & arrived.keys()):
        sources, targets = departed[key], arrived[key]
        if len(sources) > scan.move_candidates_cap or len(targets) > scan.move_candidates_cap:
            over_cap.append(key)
            continue
        pairs = []
        for source in sources:
            for target in targets:
                said = filesystem_says(source, target, file_numbers=scan.moves_by_file_number)
                if said == COPY and scan.moves_refuse_copies:
                    refused.add(target.entry_id)
                    continue
                pairs.append((source, target, said))
        admitted[key] = pairs

    matched: dict[str, tuple[Entry, MovedBy]] = {}
    taken: set[str] = set()

    def take(source: Entry, target: Entry, by: MovedBy) -> None:
        matched[target.entry_id] = (source, by)
        taken.add(source.entry_id)

    ambiguous: list[tuple[str, int, int]] = []
    for key, pairs in admitted.items():
        if len(departed[key]) == 1 and len(arrived[key]) == 1:
            if pairs:
                source, target, said = pairs[0]
                take(source, target, _label(said, MovedBy.NAME_SIZE_TIME))
            continue
        # The same file, or equal birth times, tell alike files apart, where each side is
        # confirmed with exactly one.
        confirmed = [
            (source, target, said) for source, target, said in pairs if said in (SAME, CONFIRMS)
        ]
        per_source = Counter(source.entry_id for source, _, _ in confirmed)
        per_target = Counter(target.entry_id for _, target, _ in confirmed)
        for source, target, said in confirmed:
            if per_source[source.entry_id] == 1 and per_target[target.entry_id] == 1:
                take(source, target, _label(said, MovedBy.BIRTH_TIME))
        ambiguous.append(key)

    # Neighbours: folders that moved by their contents.
    folder_pairs = _folders_moved(admitted, gone_folders, new_folders, departed, arrived, settings)
    for key in ambiguous:
        for source, target, said in admitted[key]:
            if target.entry_id in matched or source.entry_id in taken:
                continue
            moved_with = folder_pairs.get(source.parent_entry_id or "")
            if moved_with is not None and moved_with == target.parent_entry_id:
                take(source, target, _label(said, MovedBy.NEIGHBOURS))
    # A key past the cap is looked up by the folders that moved. A template file alike in every
    # folder matches no single file on its own, but each folder's move names its own copy, found by
    # (key, the folder it left) in one pass over the key's files.
    old_of = {new: old for old, new in folder_pairs.items()}
    for key in over_cap:
        by_folder: dict[str, list[Entry]] = {}
        for source in departed[key]:
            by_folder.setdefault(source.parent_entry_id or "", []).append(source)
        for target in arrived[key]:
            left = old_of.get(target.parent_entry_id or "")
            candidates = by_folder.get(left, []) if left is not None else []
            if target.entry_id in matched or len(candidates) != 1:
                continue
            source = candidates[0]
            if source.entry_id in taken:
                continue
            said = filesystem_says(source, target, file_numbers=scan.moves_by_file_number)
            if said == COPY and scan.moves_refuse_copies:
                refused.add(target.entry_id)
                continue
            take(source, target, _label(said, MovedBy.NEIGHBOURS))

    copied = _moved_by_copies(admitted, folder_pairs, gone_folders, new_folders)
    for old, new in sorted(folder_pairs.items()):
        found.folders[new] = Move(
            source=old,
            by=MovedBy.COPIED_CONTENTS if old in copied else MovedBy.CONTENTS,
            at_ns=moved_at(gone_folders[old], new_folders[new], until_ns=until_ns),
        )

    own = {new: move.at_ns for new, move in found.folders.items()}

    def folder_time(folder: str | None) -> int | None:
        """The latest move's time of the moved folders a file arrived in, up the new tree.

        A folder's rename changes its own change time and none of its files'. The move that
        brought a file here is the latest of these times, so the result is never a time before
        the move.
        """
        latest: int | None = None
        seen: set[str] = set()
        while folder is not None and folder in found.folders and folder not in seen:
            seen.add(folder)
            latest = _later(latest, own[folder])
            folder = new_folders[folder].parent_entry_id
        return latest

    # A folder moved inside a moved folder takes the later of its own time and that folder's.
    for new, move in sorted(found.folders.items()):
        at = folder_time(new)
        if at != move.at_ns:
            found.folders[new] = Move(source=move.source, by=move.by, at_ns=at)

    arrived_by_id = {target.entry_id: target for targets in arrived.values() for target in targets}
    for target_id, (source, by) in sorted(matched.items()):
        target = arrived_by_id[target_id]
        at = _later(
            moved_at(source, target, until_ns=until_ns), folder_time(target.parent_entry_id)
        )
        found.files[target_id] = Move(source=source.entry_id, by=by, at_ns=at)
        tally.files += 1
        tally.by_same_file += by is MovedBy.SAME_FILE
        tally.by_birth_time += by is MovedBy.BIRTH_TIME
        tally.by_neighbours += by is MovedBy.NEIGHBOURS
        tally.by_copy += by is MovedBy.COPIED
        tally.with_time += at is not None
    tally.folders = len(found.folders)
    tally.folders_by_copy = sum(
        move.by is MovedBy.COPIED_CONTENTS for move in found.folders.values()
    )
    # A file is refused as a copy where the filesystem named every match it had a copy.
    still_candidates = {target.entry_id for pairs in admitted.values() for _, target, _ in pairs}
    tally.refused_as_copies = len(refused - still_candidates)
    tally.left_ambiguous += sum(
        1
        for key in (*ambiguous, *over_cap)
        for target in arrived[key]
        if target.entry_id not in matched and target.entry_id not in refused
    )
    return found


def _folders_moved(
    admitted: Mapping[tuple[str, int, int], Sequence[tuple[Entry, Entry, str | None]]],
    gone_folders: Mapping[str, Entry],
    new_folders: Mapping[str, Entry],
    departed: Mapping[tuple[str, int, int], Sequence[Entry]],
    arrived: Mapping[tuple[str, int, int], Sequence[Entry]],
    settings: Settings,
) -> dict[str, str]:
    """Each gone folder whose contents arrived together in one new folder, mapped to it.

    A folder's size is the files it holds directly that could move (larger than zero, with a
    modification time) and the folders it holds, on each side. A gone folder's votes for a new
    folder are the pairs of files, one in each, a key admits, and, once folders beneath both have
    been paired, those pairs. It moved where the votes reach ``move_folder_min_files`` and exceed
    ``move_folder_min_share`` of both folders' sizes, and each is the other's one best choice.
    Most of what the gone folder held arrived there, and most of what is there came from it. Three
    of four files landing among fifty others is not a moved folder. A folder of folders is paired
    in a later round than the folders it holds.
    """
    scan = settings.scan
    size: Counter[str] = Counter()
    for sources in departed.values():
        for source in sources:
            if source.parent_entry_id in gone_folders:
                size[source.parent_entry_id or ""] += 1
    for folder in gone_folders.values():
        if folder.parent_entry_id in gone_folders:
            size[folder.parent_entry_id or ""] += 1
    new_size: Counter[str] = Counter()
    for targets in arrived.values():
        for target in targets:
            if target.parent_entry_id in new_folders:
                new_size[target.parent_entry_id or ""] += 1
    for folder in new_folders.values():
        if folder.parent_entry_id in new_folders:
            new_size[folder.parent_entry_id or ""] += 1
    by_files: Counter[tuple[str, str]] = Counter()
    for pairs in admitted.values():
        for source, target, _ in pairs:
            old, new = source.parent_entry_id, target.parent_entry_id
            if old in gone_folders and new in new_folders:
                by_files[(old or "", new or "")] += 1

    paired: dict[str, str] = {}
    paired_new: set[str] = set()
    while True:
        votes = Counter(by_files)
        for old, new in paired.items():
            above_old = gone_folders[old].parent_entry_id
            above_new = new_folders[new].parent_entry_id
            if above_old in gone_folders and above_new in new_folders:
                votes[(above_old or "", above_new or "")] += 1
        # "Most of its files matching one to one": more than the share, and at least the count.
        open_votes = {
            (old, new): number
            for (old, new), number in votes.items()
            if old not in paired
            and new not in paired_new
            and number >= scan.move_folder_min_files
            and number > scan.move_folder_min_share * size[old]
            and number > scan.move_folder_min_share * new_size[new]
        }
        best_new = _one_best((old, new, n) for (old, new), n in open_votes.items())
        best_old = _one_best((new, old, n) for (old, new), n in open_votes.items())
        chosen = {old: new for old, new in best_new.items() if best_old.get(new) == old}
        if not chosen:
            return paired
        paired.update(chosen)
        paired_new.update(chosen.values())


def _moved_by_copies(
    admitted: Mapping[tuple[str, int, int], Sequence[tuple[Entry, Entry, str | None]]],
    folder_pairs: Mapping[str, str],
    gone_folders: Mapping[str, Entry],
    new_folders: Mapping[str, Entry],
) -> frozenset[str]:
    """The moved folders whose move rests on copies alone.

    A folder's votes are the file pairs a key admitted between it and its new place, and the
    folders beneath both that were paired: it rests on copies where every file vote is a copy and
    every folder beneath was itself moved by copies alone. A folder renamed in place keeps its
    files' numbers and births, so any such file's vote says it was no copy.
    """
    not_copies = {
        (source.parent_entry_id, target.parent_entry_id)
        for pairs in admitted.values()
        for source, target, said in pairs
        if said != COPY
    }
    beneath: dict[str, list[str]] = {}
    for old in folder_pairs:
        above = gone_folders[old].parent_entry_id
        if above is None or above not in folder_pairs:
            continue
        if new_folders[folder_pairs[old]].parent_entry_id == folder_pairs[above]:
            beneath.setdefault(above, []).append(old)
    settled: dict[str, bool] = {}

    def by_copies(old: str, seen: frozenset[str]) -> bool:
        if old in settled:
            return settled[old]
        if old in seen:
            return False
        said = (old, folder_pairs[old]) not in not_copies and all(
            by_copies(child, seen | {old}) for child in beneath.get(old, ())
        )
        settled[old] = said
        return said

    return frozenset(old for old in folder_pairs if by_copies(old, frozenset()))


def _one_best(choices: Iterable[tuple[str, str, int]]) -> dict[str, str]:
    """For each first item, the second with the most votes where no other ties it."""
    ranked: dict[str, list[tuple[int, str]]] = {}
    for first, second, number in choices:
        ranked.setdefault(first, []).append((number, second))
    best: dict[str, str] = {}
    for first, options in ranked.items():
        options.sort(reverse=True)
        if len(options) == 1 or options[0][0] > options[1][0]:
            best[first] = options[0][1]
    return best
