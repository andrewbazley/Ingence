"""Moves confirmed by time and by their neighbours.

A move has a before and an after. Timestamps can confirm a move, so files that have moved are not
treated as new files never seen before. The rules are:

- Equal birth times confirm a name, size and time match. A later birth time on the arrived file, or
  another file number on the same volume, makes it a copy. A copy that keeps its original's name,
  size and time, with the original removed, is read as a move that says it was made by a copy. A
  setting refuses copies instead, and then they carry nothing.
- A change time between the two walks is the move's time, never the file's clock.
- A folder whose files arrived together in one new folder moved, whatever it is called now. Its
  move tells apart the files that a name, size and time alone could not.
- Where no birth time exists, files match by name, size and time alone.

The matcher is tested on built entries (every platform), and the walk on real files where the
filesystem gives birth times. Every name is invented.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from ingence.core.outcomes import TimeSource
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, MovedBy, RecordEventKind
from ingence.core.settings import Settings
from ingence.record.log import append_event, read_events
from ingence.scan.census import run_census
from ingence.scan.moves import find_moves
from ingence.storage.catalog import open_catalog

SECOND = 1_000_000_000
#: An invented instant: 2023-11-14, and a walk made a day before the move and one after.
MADE = 1_700_000_000 * SECOND
EARLIER_WALK = "run_20260101T000001Z_111111111111"
LATER_WALK = "run_20260103T000001Z_222222222222"
BETWEEN = 1_767_312_000 * SECOND  # 2026-01-02T00:00:00Z
AFTER = 1_767_484_800 * SECOND  # 2026-01-04T00:00:00Z


def _entry(
    identifier: str,
    path: str,
    *,
    parent: str | None = None,
    kind: EntryType = EntryType.FILE,
    size: int = 40,
    modified: int = MADE + 5 * SECOND,
    born: int | None = MADE,
    source: TimeSource = TimeSource.BIRTHTIME,
    changed: int | None = None,
    seen: str = EARLIER_WALK,
    device: int | None = None,
    inode: int | None = None,
) -> Entry:
    return Entry(
        entry_id=identifier,
        root_id="root_0",
        relative_path=path,
        name=path.rsplit("/", 1)[-1],
        parent_entry_id=parent,
        entry_type=kind,
        size_bytes=None if kind is EntryType.DIRECTORY else size,
        modified_ns=modified,
        created_ns=born,
        created_time_source=source if born is not None else TimeSource.UNAVAILABLE,
        changed_ns=changed,
        first_seen_run=seen,
        last_seen_run=seen,
        device=device,
        inode=inode,
    )


def _settings(**scan: object) -> Settings:
    settings = Settings()
    return settings.model_copy(update={"scan": settings.scan.model_copy(update=scan)})


def _found(departed: list[Entry], arrived: list[Entry], settings: Settings | None = None):
    live = {entry.entry_id: entry for entry in departed}
    return find_moves(
        arrived,
        live,
        gone=set(live),
        settings=settings or Settings(),
        until_ns=AFTER,
    )


# -- time -------------------------------------------------------------------------------------


def test_equal_birth_times_confirm_a_move() -> None:
    found = _found(
        [_entry("old", "kelt/zib.txt")],
        [_entry("new", "vorn/zib.txt", seen=LATER_WALK)],
    )
    assert found.files["new"].source == "old"
    assert found.files["new"].by is MovedBy.BIRTH_TIME
    assert found.tally.by_birth_time == 1


def test_a_later_birth_time_makes_a_move_made_by_a_copy() -> None:
    """A copy whose original is gone is read as a move, and says it was a copy."""
    found = _found(
        [_entry("old", "kelt/zib.txt")],
        [_entry("new", "vorn/zib.txt", born=MADE + 90 * SECOND, seen=LATER_WALK)],
    )
    assert found.files["new"].source == "old"
    assert found.files["new"].by is MovedBy.COPIED
    assert (found.tally.files, found.tally.by_copy, found.tally.refused_as_copies) == (1, 1, 0)


def test_refusing_copies_is_a_setting_and_on_a_copy_carries_nothing() -> None:
    """With the setting on, a copy starts afresh and carries nothing."""
    found = _found(
        [_entry("old", "kelt/zib.txt")],
        [_entry("new", "vorn/zib.txt", born=MADE + 90 * SECOND, seen=LATER_WALK)],
        _settings(moves_refuse_copies=True),
    )
    assert found.files == {}
    assert found.tally.refused_as_copies == 1


def test_copies_are_read_as_moves_unless_a_person_says_otherwise() -> None:
    assert Settings().scan.moves_refuse_copies is False


def test_on_one_device_the_same_file_number_confirms_a_move() -> None:
    """A rename keeps the file's number on its volume, birth time or none."""
    for born in (MADE, None):
        found = _found(
            [_entry("old", "kelt/zib.txt", born=born, device=7, inode=42)],
            [_entry("new", "vorn/zib.txt", born=born, device=7, inode=42, seen=LATER_WALK)],
        )
        assert found.files["new"].by is MovedBy.SAME_FILE
        assert found.tally.by_same_file == 1


def test_on_one_device_another_file_number_with_the_same_birth_is_a_clone() -> None:
    """A clone (APFS's copy, cp -c) keeps the birth time and gets a number of its own: a copy."""
    found = _found(
        [_entry("old", "kelt/zib.txt", device=7, inode=42)],
        [_entry("new", "vorn/zib.txt", device=7, inode=43, seen=LATER_WALK)],
    )
    assert found.files["new"].by is MovedBy.COPIED
    refused = _found(
        [_entry("old", "kelt/zib.txt", device=7, inode=42)],
        [_entry("new", "vorn/zib.txt", device=7, inode=43, seen=LATER_WALK)],
        _settings(moves_refuse_copies=True),
    )
    assert refused.files == {}
    assert refused.tally.refused_as_copies == 1
    # Off, the file number decides nothing and the birth time alone confirms.
    off = _found(
        [_entry("old", "kelt/zib.txt", device=7, inode=42)],
        [_entry("new", "vorn/zib.txt", device=7, inode=43, seen=LATER_WALK)],
        _settings(moves_by_file_number=False),
    )
    assert off.files["new"].by is MovedBy.BIRTH_TIME


def test_where_the_device_changed_or_a_number_is_missing_the_birth_reads_as_before() -> None:
    """A remount renumbers a device: the file numbers are then not comparable."""
    remounted = _found(
        [_entry("old", "kelt/zib.txt", device=7, inode=42)],
        [_entry("new", "vorn/zib.txt", device=8, inode=43, seen=LATER_WALK)],
    )
    assert remounted.files["new"].by is MovedBy.BIRTH_TIME
    unnumbered = _found(
        [_entry("old", "kelt/zib.txt", device=7, inode=None)],
        [_entry("new", "vorn/zib.txt", device=7, inode=43, seen=LATER_WALK)],
    )
    assert unnumbered.files["new"].by is MovedBy.BIRTH_TIME


def test_on_one_device_another_file_number_is_a_copy_whatever_the_birth_says() -> None:
    """A rename keeps a file's number on its volume, so another number there is a copy on every
    system: with no birth time (a filesystem keeping none, or a census before statx), a later one,
    an equal one (a clone), one that is the modification time over again, or an earlier one with
    no known cause."""
    births = {
        "none": (None, None, MADE + 5 * SECOND),
        "later": (MADE, MADE + 90 * SECOND, MADE + 5 * SECOND),
        "equal": (MADE, MADE, MADE + 5 * SECOND),
        "the modification time": (MADE, MADE, MADE),
        "earlier, no known cause": (MADE + 60 * SECOND, MADE + 30 * SECOND, MADE),
    }
    for said, (before, after, modified) in births.items():
        found = _found(
            [_entry("old", "kelt/zib.txt", born=before, modified=modified, device=7, inode=42)],
            [
                _entry(
                    "new",
                    "vorn/zib.txt",
                    born=after,
                    modified=modified,
                    device=7,
                    inode=43,
                    seen=LATER_WALK,
                )
            ],
        )
        assert found.files["new"].by is MovedBy.COPIED, said


def test_a_birth_pulled_back_to_the_modification_time_is_a_copy_made_here() -> None:
    """A file born after it was last modified (a copy on the share) and copied again on APFS: the
    copy's birth is pulled back to the modification time it keeps, before the original's birth.
    A rename never moves a birth, so that is a copy too. An earlier birth with no such mark says
    nothing."""
    departed = _entry("old", "kelt/zib.txt", born=MADE + 60 * SECOND, modified=MADE)
    pulled = _found(
        [departed],
        [_entry("new", "vorn/zib.txt", born=MADE, modified=MADE, seen=LATER_WALK)],
    )
    assert pulled.files["new"].by is MovedBy.COPIED
    unexplained = _found(
        [departed],
        [_entry("new", "vorn/zib.txt", born=MADE + 30 * SECOND, modified=MADE, seen=LATER_WALK)],
    )
    assert unexplained.files["new"].by is MovedBy.NAME_SIZE_TIME


def test_a_birth_time_that_is_the_modification_time_confirms_nothing() -> None:
    """Setting a file's modification time earlier than its birth pulls the birth back on some
    filesystems, and a copy that keeps the modification time does exactly that: a birth equal to
    the modification time is the same clock twice, no confirmation."""
    found = _found(
        [_entry("old", "kelt/zib.txt", born=MADE, modified=MADE)],
        [_entry("new", "vorn/zib.txt", born=MADE, modified=MADE, seen=LATER_WALK)],
    )
    assert found.files["new"].by is MovedBy.NAME_SIZE_TIME


def test_without_birth_times_nothing_changes() -> None:
    no_birth = _found(
        [_entry("old", "kelt/zib.txt", born=None)],
        [_entry("new", "vorn/zib.txt", born=None, seen=LATER_WALK)],
    )
    assert no_birth.files["new"].by is MovedBy.NAME_SIZE_TIME
    # A change time standing in for creation (Windows' ctime reading) is not a birth time.
    other_source = _found(
        [_entry("old", "kelt/zib.txt", source=TimeSource.CTIME)],
        [_entry("new", "vorn/zib.txt", born=MADE + 90 * SECOND, seen=LATER_WALK)],
    )
    assert other_source.files["new"].by is MovedBy.NAME_SIZE_TIME


def test_a_change_time_between_the_walks_is_the_moves_time_and_nothing_else_is() -> None:
    inside = _found(
        [_entry("old", "kelt/zib.txt")],
        [_entry("new", "vorn/zib.txt", changed=BETWEEN, seen=LATER_WALK)],
    )
    assert inside.files["new"].at_ns == BETWEEN
    before = _found(
        [_entry("old", "kelt/zib.txt")],
        [_entry("new", "vorn/zib.txt", changed=MADE + 5 * SECOND, seen=LATER_WALK)],
    )
    assert before.files["new"].at_ns is None
    ahead = _found(
        [_entry("old", "kelt/zib.txt")],
        [_entry("new", "vorn/zib.txt", changed=AFTER + SECOND, seen=LATER_WALK)],
    )
    assert ahead.files["new"].at_ns is None


def test_a_change_time_that_is_the_birth_is_no_moves_time() -> None:
    """On Windows Python's change time is the creation time. A file born after the last walk began
    must not read its birth as the move's time. A change time equal to a birth time says the file
    was never touched after it was made (on POSIX), so it was not moved then."""
    born = 1_767_225_700 * SECOND  # 2026-01-01T00:01:40Z, after the earlier walk began
    found = _found(
        [_entry("old", "kelt/zib.txt", born=born, modified=born + SECOND)],
        [
            _entry(
                "new",
                "vorn/zib.txt",
                born=born,
                modified=born + SECOND,
                changed=born,
                seen=LATER_WALK,
            )
        ],
    )
    assert found.files["new"].at_ns is None


def test_equal_birth_times_tell_two_alike_files_apart() -> None:
    found = _found(
        [
            _entry("old_a", "kelt/zib.txt", born=MADE),
            _entry("old_b", "trell/zib.txt", born=MADE + SECOND),
        ],
        [_entry("new", "vorn/zib.txt", born=MADE + SECOND, seen=LATER_WALK)],
    )
    assert (found.files["new"].source, found.files["new"].by) == ("old_b", MovedBy.BIRTH_TIME)


def test_two_alike_files_with_no_birth_times_are_still_no_move() -> None:
    found = _found(
        [
            _entry("old_a", "kelt/zib.txt", born=None),
            _entry("old_b", "trell/zib.txt", born=None),
        ],
        [_entry("new", "vorn/zib.txt", born=None, seen=LATER_WALK)],
    )
    assert found.files == {}
    assert found.tally.left_ambiguous == 1


def test_a_key_held_by_more_files_than_the_cap_matches_nothing() -> None:
    departed = [_entry(f"old_{n}", f"kelt_{n}/zib.txt", born=None) for n in range(3)]
    arrived = [
        _entry(f"new_{n}", f"vorn_{n}/zib.txt", born=None, seen=LATER_WALK) for n in range(3)
    ]
    found = _found(departed, arrived, _settings(move_candidates_cap=2))
    assert found.files == {}
    assert found.tally.left_ambiguous == 3


def test_a_key_past_the_cap_is_told_apart_by_the_folders_that_moved() -> None:
    """A template file alike in every folder is held by more files than the cap, so its key
    matches nothing. Each folder's move still names its own copy of it."""
    departed: list[Entry] = []
    arrived: list[Entry] = []
    for n in range(3):
        departed.append(_entry(f"o{n}", f"kelt/f{n}", kind=EntryType.DIRECTORY, born=None))
        arrived.append(
            _entry(f"n{n}", f"vorn/f{n}", kind=EntryType.DIRECTORY, born=None, seen=LATER_WALK)
        )
        departed.append(_entry(f"o{n}_t", f"kelt/f{n}/settings.xml", parent=f"o{n}", born=None))
        arrived.append(
            _entry(f"n{n}_t", f"vorn/f{n}/settings.xml", parent=f"n{n}", born=None, seen=LATER_WALK)
        )
        for m in range(2):
            departed.append(
                _entry(
                    f"o{n}_{m}",
                    f"kelt/f{n}/img{n}{m}.tif",
                    parent=f"o{n}",
                    size=500 + 10 * n + m,
                    born=None,
                )
            )
            arrived.append(
                _entry(
                    f"n{n}_{m}",
                    f"vorn/f{n}/img{n}{m}.tif",
                    parent=f"n{n}",
                    size=500 + 10 * n + m,
                    born=None,
                    seen=LATER_WALK,
                )
            )
    found = _found(departed, arrived, _settings(move_candidates_cap=2))
    assert {new: move.source for new, move in found.folders.items()} == {
        "n0": "o0",
        "n1": "o1",
        "n2": "o2",
    }
    for n in range(3):
        assert found.files[f"n{n}_t"].source == f"o{n}_t"
        assert found.files[f"n{n}_t"].by is MovedBy.NEIGHBOURS
    assert found.tally.left_ambiguous == 0


# -- neighbours -------------------------------------------------------------------------------


def _two_folders(*, born: int | None = None) -> tuple[list[Entry], list[Entry]]:
    """Two folders, each holding a file alike in name, size and time to the other's, and two files
    of their own. Both moved, and one renamed on the way."""
    departed = [
        _entry("x", "kelt_a", kind=EntryType.DIRECTORY, born=born),
        _entry("w", "kelt_b", kind=EntryType.DIRECTORY, born=born),
        _entry("x_data", "kelt_a/data.csv", parent="x", born=born),
        _entry("x_one", "kelt_a/one.txt", parent="x", size=11, born=born),
        _entry("x_two", "kelt_a/two.txt", parent="x", size=12, born=born),
        _entry("w_data", "kelt_b/data.csv", parent="w", born=born),
        _entry("w_three", "kelt_b/three.txt", parent="w", size=13, born=born),
        _entry("w_four", "kelt_b/four.txt", parent="w", size=14, born=born),
    ]
    arrived = [
        _entry("x2", "vorn/kelt_a_renamed", kind=EntryType.DIRECTORY, born=born, seen=LATER_WALK),
        _entry("w2", "vorn/kelt_b", kind=EntryType.DIRECTORY, born=born, seen=LATER_WALK),
        _entry("x2_data", "vorn/kelt_a_renamed/data.csv", parent="x2", born=born, seen=LATER_WALK),
        _entry("x2_one", "vorn/kelt_a_renamed/one.txt", parent="x2", size=11, born=born),
        _entry("x2_two", "vorn/kelt_a_renamed/two.txt", parent="x2", size=12, born=born),
        _entry("w2_data", "vorn/kelt_b/data.csv", parent="w2", born=born, seen=LATER_WALK),
        _entry("w2_three", "vorn/kelt_b/three.txt", parent="w2", size=13, born=born),
        _entry("w2_four", "vorn/kelt_b/four.txt", parent="w2", size=14, born=born),
    ]
    return departed, arrived


def test_a_folder_moved_by_its_contents_tells_its_alike_files_apart() -> None:
    departed, arrived = _two_folders()
    found = _found(departed, arrived)
    assert {new: move.source for new, move in found.folders.items()} == {"x2": "x", "w2": "w"}
    assert {move.by for move in found.folders.values()} == {MovedBy.CONTENTS}
    assert found.files["x2_data"].source == "x_data"
    assert found.files["w2_data"].source == "w_data"
    assert found.files["x2_data"].by is MovedBy.NEIGHBOURS
    assert found.files["x2_one"].by is MovedBy.NAME_SIZE_TIME
    assert (found.tally.folders, found.tally.by_neighbours) == (2, 2)


def test_a_folder_whose_files_were_moved_and_copied_moves_by_its_contents() -> None:
    """One file renamed with the folder (the same file on its volume) is no copy: the folder's
    move does not rest on copies alone, and says only where it came from."""
    departed, arrived = _two_folders(born=MADE)
    arrived = [
        entry.model_copy(update={"created_ns": MADE + 90 * SECOND})
        if entry.entry_type is EntryType.FILE and entry.entry_id != "x2_one"
        else entry
        for entry in arrived
    ]
    found = _found(departed, arrived)
    assert found.folders["x2"].by is MovedBy.CONTENTS
    assert found.folders["w2"].by is MovedBy.COPIED_CONTENTS
    assert found.files["x2_one"].by is MovedBy.BIRTH_TIME


def test_a_folder_of_folders_moved_by_copies_alone_says_so() -> None:
    departed, arrived = _two_folders(born=MADE)
    departed = [
        *(
            e.model_copy(update={"parent_entry_id": "p"}) if e.entry_id in {"x", "w"} else e
            for e in departed
        ),
        _entry("p", "outer", kind=EntryType.DIRECTORY, born=MADE),
    ]
    arrived = [
        *(
            e.model_copy(update={"parent_entry_id": "p2"}) if e.entry_id in {"x2", "w2"} else e
            for e in arrived
        ),
        _entry("p2", "outer_moved", kind=EntryType.DIRECTORY, born=MADE, seen=LATER_WALK),
    ]
    arrived = [
        entry.model_copy(update={"created_ns": MADE + 90 * SECOND})
        if entry.entry_type is EntryType.FILE
        else entry
        for entry in arrived
    ]
    found = _found(departed, arrived)
    assert found.folders["p2"].source == "p"
    assert found.folders["p2"].by is MovedBy.COPIED_CONTENTS


def test_a_file_moved_with_its_folder_takes_the_folders_moves_time() -> None:
    """Renaming a folder changes the folder's change time and none of its files': the files' move
    happened when the folder's did."""
    departed, arrived = _two_folders()
    arrived = [
        entry.model_copy(update={"changed_ns": BETWEEN})
        if entry.entry_id == "x2"
        else entry.model_copy(update={"changed_ns": MADE + 5 * SECOND})
        for entry in arrived
    ]
    found = _found(departed, arrived)
    assert found.folders["x2"].at_ns == BETWEEN
    assert found.files["x2_one"].at_ns == BETWEEN
    assert found.files["x2_data"].at_ns == BETWEEN
    # The other folder's change time was outside the window: nothing to take.
    assert found.files["w2_three"].at_ns is None


def test_a_file_changed_before_its_folder_moved_takes_the_later_time() -> None:
    """A file whose own change time fell inside the window (a permissions change) before its
    folder was renamed. The move happened at the folder's time, the later."""
    touched = BETWEEN
    renamed = BETWEEN + 3600 * SECOND
    departed = [_entry("o", "kelt", kind=EntryType.DIRECTORY)] + [
        _entry(f"o{n}", f"kelt/f{n}.txt", parent="o", size=100 + n) for n in range(3)
    ]
    arrived = [_entry("n", "vorn", kind=EntryType.DIRECTORY, changed=renamed, seen=LATER_WALK)]
    arrived += [
        _entry(
            f"n{n}",
            f"vorn/f{n}.txt",
            parent="n",
            size=100 + n,
            changed=touched if n == 0 else MADE,
            seen=LATER_WALK,
        )
        for n in range(3)
    ]
    found = _found(departed, arrived)
    assert found.folders["n"].at_ns == renamed
    assert found.files["n0"].at_ns == renamed
    assert found.files["n1"].at_ns == renamed


def test_a_folder_most_of_whose_files_stayed_behind_did_not_move() -> None:
    departed, arrived = _two_folders()
    # Of the first folder's three files only the alike one arrived. The other two are gone.
    arrived = [entry for entry in arrived if entry.entry_id not in {"x2_one", "x2_two"}]
    found = _found(departed, arrived)
    assert "x2" not in found.folders
    assert "x2_data" not in found.files
    # The second folder still moved, and its alike file is told apart by it.
    assert found.files["w2_data"].source == "w_data"


def test_a_folder_does_not_move_into_a_folder_mostly_of_other_files() -> None:
    """Three of a folder's four files land in a new folder that also holds fifty files of its
    own. The old folder's files arrived, but the new folder is not it, and a decision about the
    folder must not follow into it."""
    departed = [_entry("o", "kelt", kind=EntryType.DIRECTORY)] + [
        _entry(f"o{n}", f"kelt/f{n}.txt", parent="o", size=100 + n) for n in range(4)
    ]
    arrived = [_entry("n", "vorn", kind=EntryType.DIRECTORY, seen=LATER_WALK)]
    arrived += [
        _entry(f"n{n}", f"vorn/f{n}.txt", parent="n", size=100 + n, seen=LATER_WALK)
        for n in range(3)
    ]
    arrived += [
        _entry(f"x{n}", f"vorn/other{n}.dat", parent="n", size=900 + n, seen=LATER_WALK)
        for n in range(50)
    ]
    found = _found(departed, arrived)
    assert found.folders == {}
    # The files themselves still moved.
    assert {found.files[f"n{n}"].source for n in range(3)} == {"o0", "o1", "o2"}


def test_the_share_a_folder_needs_is_a_setting() -> None:
    departed, arrived = _two_folders()
    arrived = [entry for entry in arrived if entry.entry_id != "x2_two"]
    assert "x2" in _found(departed, arrived).folders
    strict = _found(departed, arrived, _settings(move_folder_min_share=0.9))
    assert "x2" not in strict.folders


def test_a_folder_of_folders_moves_by_its_folders() -> None:
    departed, arrived = _two_folders()
    parent_old = _entry("p", "kelt", kind=EntryType.DIRECTORY)
    parent_new = _entry("p2", "brisa", kind=EntryType.DIRECTORY, changed=BETWEEN, seen=LATER_WALK)
    departed = [
        parent_old,
        *(
            e.model_copy(update={"parent_entry_id": "p"}) if e.entry_id in {"x", "w"} else e
            for e in departed
        ),
    ]
    arrived = [
        parent_new,
        *(
            e.model_copy(update={"parent_entry_id": "p2"}) if e.entry_id in {"x2", "w2"} else e
            for e in arrived
        ),
    ]
    found = _found(departed, arrived)
    assert found.folders["p2"].source == "p"
    assert found.folders["p2"].by is MovedBy.CONTENTS
    # Only the renamed folder's change time moved. What moved inside it takes its time.
    assert found.folders["p2"].at_ns == BETWEEN
    assert found.folders["x2"].at_ns == BETWEEN
    assert found.files["x2_one"].at_ns == BETWEEN


def test_a_folder_copied_its_original_removed_moves_by_its_copies() -> None:
    """Its files are moves made by copies, and the folder moves by its contents. When copies are
    refused, nothing moves."""
    departed, arrived = _two_folders(born=MADE)
    arrived = [
        entry.model_copy(update={"created_ns": MADE + 90 * SECOND})
        if entry.entry_type is EntryType.FILE
        else entry
        for entry in arrived
    ]
    found = _found(departed, arrived)
    assert set(found.folders) == {"x2", "w2"}
    # A folder whose move rests on copies alone says so.
    assert {move.by for move in found.folders.values()} == {MovedBy.COPIED_CONTENTS}
    assert found.tally.folders_by_copy == 2
    assert len(found.files) == 6
    assert {move.by for move in found.files.values()} == {MovedBy.COPIED}
    refused = _found(departed, arrived, _settings(moves_refuse_copies=True))
    assert refused.folders == {}
    assert refused.files == {}
    assert refused.tally.refused_as_copies == 6


# -- the walk ---------------------------------------------------------------------------------

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 8)]


def _births_kept(tmp_path: Path) -> bool:
    """Whether the walk reads a birth time here, through the layer it reads (on Linux with
    ``statx``, where the filesystem keeps one)."""
    from ingence.core.platform import lstat_path

    probe = tmp_path / "probe.txt"
    probe.write_text("x")
    read = lstat_path(probe)
    return bool(getattr(read, "st_birthtime_ns", None) or getattr(read, "st_birthtime", None))


def _written(path: Path, text: str) -> None:
    """A file made, then written a moment later, so its birth is not its modification time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text[:1])
    time.sleep(0.02)
    path.write_text(text)


def _row(project: Path, name: str) -> dict:
    with open_catalog(project) as catalog:
        row = catalog.query(
            "SELECT entry_id, relative_path, moved_from, moved_by, moved_at_ns, changed_ns, "
            "inode, device FROM entries WHERE name = ? AND NOT is_deleted",
            [name],
        ).fetchone()
    keys = ("id", "path", "moved_from", "moved_by", "moved_at", "changed", "inode", "device")
    return dict(zip(keys, row, strict=True))


def _note(project: Path, target: str, against: str = RUNS[0]) -> None:
    append_event(
        project,
        kind=RecordEventKind.NOTE,
        actor="a person",
        occurred_at="2026-01-01T00:00:02Z",
        target=target,
        against_run_id=against,
        payload={"text": "an invented note"},
    )


def _walked_now(project: Project, nonce: str) -> str:
    """A walk whose run is stamped with the moment it began, so the window between two walks is
    the real one. It starts a second later than anything before it, so runs sort in the order
    they ran."""
    from ingence.core.ids import run_id as make_run_id

    time.sleep(1.1)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = make_run_id(started_at=started, nonce=nonce)
    run_census(project, run_id=run, started_at=started)
    return run


def test_a_renamed_file_is_confirmed_by_its_birth_time_and_a_copy_says_it_is_one(
    tmp_path: Path,
) -> None:
    if not _births_kept(tmp_path):
        pytest.skip("this filesystem gives no birth time to confirm a move with")
    pool = tmp_path / "pool"
    _written(pool / "kelt" / "zib_notes.txt", "three vorn replicates\n" * 20)
    _written(pool / "kelt" / "brisa_table.csv", "a,b\n1,2\n" * 10)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    # The files were made over a second before the first walk began: outside the window.
    first = _walked_now(project, "one")
    moved_id = _row(project.path, "zib_notes.txt")["id"]
    copied_id = _row(project.path, "brisa_table.csv")["id"]
    _note(project.path, f"file:{moved_id}", first)
    _note(project.path, f"file:{copied_id}", first)

    (pool / "vorn").mkdir()
    changed_before = (pool / "kelt" / "zib_notes.txt").stat().st_ctime_ns
    shutil.move(pool / "kelt" / "zib_notes.txt", pool / "vorn" / "zib_notes.txt")
    # A copy that keeps the modification time, and the original deleted: a move made by a copy.
    shutil.copy2(pool / "kelt" / "brisa_table.csv", pool / "vorn" / "brisa_table.csv")
    (pool / "kelt" / "brisa_table.csv").unlink()
    _walked_now(project, "two")

    moved = _row(project.path, "zib_notes.txt")
    # The same file on the same volume where the census records the file's number (a rename
    # keeps it). Where it records none, the birth time confirms. Windows' directory listing gives
    # no file number (``DirEntry.stat`` sets ``st_ino`` to 0, which the census stores as none),
    # so there it is the birth time: what each platform can show, not a different behaviour.
    numbered = moved["inode"] is not None and moved["device"] is not None
    confirmed_by = MovedBy.SAME_FILE if numbered else MovedBy.BIRTH_TIME
    assert (moved["moved_from"], moved["moved_by"]) == (moved_id, confirmed_by.value)
    if os.name == "nt":
        # Python's change time on Windows is the creation time: no move's time is read from it.
        assert moved["moved_at"] is None
    else:
        # The change time the rename wrote: inside the window, and not the one the file had.
        assert moved["moved_at"] == moved["changed"]
        assert moved["moved_at"] > changed_before
    # The copy is read as a move on every system, and says it was a copy.
    copied = _row(project.path, "brisa_table.csv")
    assert (copied["moved_from"], copied["moved_by"]) == (copied_id, MovedBy.COPIED.value)
    targets = {event.target for event in read_events(project.path)}
    assert f"file:{moved['id']}" in targets
    assert f"file:{copied['id']}" in targets
    # The walk's journal counts what it confirmed, what was a copy and what it refused.
    with open_catalog(project.path) as catalog:
        (detail,) = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'walk.moves'"
        ).fetchone()
    said = json.loads(detail)
    assert (said["files"], said["by_copy"], said["refused_as_copies"]) == (2, 1, 0)
    assert (said["by_same_file"], said["by_birth_time"]) == ((1, 0) if numbered else (0, 1))


def test_a_renamed_folder_moves_and_tells_its_alike_files_apart_in_a_walk(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    same = 1_700_000_000 * SECOND
    for folder in ("kelt_a", "kelt_b"):
        (pool / "trell" / folder).mkdir(parents=True)
        (pool / "trell" / folder / "data.csv").write_text("same bytes\n")
        os.utime(pool / "trell" / folder / "data.csv", ns=(same, same))
        (pool / "trell" / folder / f"{folder}_own.txt").write_text(f"{folder} alone\n")
        (pool / "trell" / folder / f"{folder}_more.txt").write_text(f"{folder} more\n" * 3)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    with open_catalog(project.path) as catalog:
        folder_id, data_id = catalog.query(
            "SELECT (SELECT entry_id FROM entries WHERE relative_path = 'trell/kelt_a'), "
            "(SELECT entry_id FROM entries WHERE relative_path = 'trell/kelt_a/data.csv')"
        ).fetchone()
    _note(project.path, f"file:{data_id}")
    _note(project.path, f"folder:{folder_id}")

    (pool / "zib").mkdir()
    os.rename(pool / "trell" / "kelt_a", pool / "zib" / "kelt_a_renamed")
    os.rename(pool / "trell" / "kelt_b", pool / "zib" / "kelt_b")
    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:03Z")

    with open_catalog(project.path) as catalog:
        folder = catalog.query(
            "SELECT entry_id, moved_from, moved_by FROM entries "
            "WHERE relative_path = 'zib/kelt_a_renamed'"
        ).fetchone()
        data = catalog.query(
            "SELECT entry_id, moved_from, moved_by FROM entries "
            "WHERE relative_path = 'zib/kelt_a_renamed/data.csv'"
        ).fetchone()
    assert (folder[1], folder[2]) == (folder_id, MovedBy.CONTENTS.value)
    assert data[1] == data_id
    # Told apart by its own number on the volume, its birth time, or its folder's move.
    assert data[2] in (MovedBy.SAME_FILE.value, MovedBy.BIRTH_TIME.value, MovedBy.NEIGHBOURS.value)
    targets = {event.target for event in read_events(project.path)}
    assert {f"file:{data[0]}", f"folder:{folder[0]}"} <= targets


def test_the_entry_contract_says_when_a_file_moved() -> None:
    from ingence.api.v1.entries import entry_record

    moved = _entry("new", "vorn/zib.txt", seen=LATER_WALK).model_copy(
        update={"moved_from": "old", "moved_by": MovedBy.BIRTH_TIME, "moved_at_ns": BETWEEN}
    )
    record = entry_record(moved.model_dump(mode="json"))
    assert (record.moved_from, record.moved_by) == ("old", "birth_time")
    assert record.moved_at is not None
    assert record.moved_at.startswith("2026-01-02T00:00:00")
    assert record.moved_at.endswith("Z")
    assert entry_record(_entry("kept", "kelt/zib.txt").model_dump(mode="json")).moved_at is None


def test_a_clone_whose_original_was_deleted_is_a_move_made_by_a_copy_in_a_walk(
    tmp_path: Path,
) -> None:
    """APFS's clone (Finder's copy on one volume, cp -c) keeps the birth time and the modification
    time. It must not be labelled a move as though it were a rename. Its own file number says it
    is a copy, and a copy whose original is gone is a move that says so. The note follows it."""
    import subprocess
    import sys

    if sys.platform != "darwin":
        pytest.skip("cp -c clones only on macOS")
    pool = tmp_path / "pool"
    _written(pool / "kelt" / "zib_notes.txt", "three vorn replicates\n" * 20)
    (pool / "vorn").mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUNS[0], started_at="2026-01-01T00:00:01Z")
    old = _row(project.path, "zib_notes.txt")["id"]
    _note(project.path, f"file:{old}")
    source = pool / "kelt" / "zib_notes.txt"
    cloned = subprocess.run(
        ["cp", "-c", "-p", str(source), str(pool / "vorn" / "zib_notes.txt")], check=False
    )
    if cloned.returncode != 0:
        pytest.skip("this volume cannot clone")
    source.unlink()
    run_census(project, run_id=RUNS[1], started_at="2026-01-01T00:00:03Z")
    new = _row(project.path, "zib_notes.txt")
    assert (new["moved_from"], new["moved_by"]) == (old, MovedBy.COPIED.value)
    assert {event.target for event in read_events(project.path)} == {f"file:{new['id']}"}
