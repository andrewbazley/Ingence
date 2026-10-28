"""Every decision that pointed at the old organisation, and where its files went.

A person's decisions are pinned to what they were about:

==============================  =============================================  ================
Record target                   pinned to                                      after a move
==============================  =============================================  ================
``folder:<entry_id>``           the folder's root and path                     points nowhere
a folder-scoped definition      the folder's path (``Scope.value``)            points nowhere
``claim:<claim_id>``            its subject entry, so its path                 follows the file
``candidate:<candidate_id>``    both ends' entries                             follows the file
``class:<rule>:<key digest>``   the class key; path-free keys survive          depends on key
``question:<rule>:<raised>``    what was raised, wherever                      survives
``extension:<ext>``             every file with that extension                 survives
``term:<word>``                 the word, project-wide                         survives
==============================  =============================================  ================

A reorganisation is not a roadblock. The engine knows enough about each file to know what moved,
and what that move will mean. So where a decision's files went together, their new place is found
and said as information. Only a folder whose files scattered has no one new place. Even that is
information, low and quiet, and never a request that waits. Nothing here applies a decision
anywhere: the Record keeps it as written, at the place it was written about.

A folder's decision is about what lay beneath the folder, however deep, and not about the folder's
own path. So where its files went is read from the whole subtree. Each matched file's path below the
folder is taken off its old path, and what is left of its new path once the same ending is taken off
is the folder it went to. A folder nested whole under a new parent goes to the new place. A folder
whose files were renamed as they moved has fewer files to vouch for it, and the share says so. A
folder's decision follows its files only where at least ``minimum_share`` of them went to one
folder. That share is a parameter, and its value is not measured.

Following here is a reading and never a new event. The engine does not write a decision on anyone's
behalf, and withdrawing is never something the engine does on its own. The Record keeps each event
as written. This module says where the files went, and the sentences are worded as that: a note
"was written about" a folder whose files "are now in" a place, and never as something the engine
did.

Nothing follows a size-blind match. A file matched by its place, or by its folder's name and its
own, where one side states no sizes (``fingerprints.SIZE_BLIND_TIERS``), is not evidence enough to
say where a decision's files went. So the file is left out of the files that speak for a folder and
of the files counted as moved. It still counts among the files a new place holds, because it is a
file there.

Where a decision's files went together, the History page's notice says so, and so does one line on
the new folder's own page (:func:`folder_lines`). Each shows while the difference stands and never
in a queue. ``SAY_ON_FOLDER_PAGE`` is that line. Setting it to False (or dropping the call) turns
the line off, and the notice stands.
"""

from __future__ import annotations

import bisect
import json
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    import duckdb

    from ingence.compare.structure import MovedPair
    from ingence.core.schema import RecordEvent

__all__ = [
    "DEFAULT_MINIMUM_SHARE",
    "SAY_ON_FOLDER_PAGE",
    "Carried",
    "CarryResult",
    "DecisionBasis",
    "Follow",
    "carried_decisions",
    "decision_basis",
    "folder_lines",
    "moved_file_decisions",
]

#: A folder's decisions follow its files where at least this share went to one new folder.
#: The value is not measured. It is shown beside every sentence it produces.
DEFAULT_MINIMUM_SHARE = 0.8

#: Say a followed decision as one line on the folder's own page. False turns the line off.
SAY_ON_FOLDER_PAGE = True


class Follow(StrEnum):
    """What becomes of one place-pinned decision after a reorganisation."""

    #: Its files went together to one new place: said as information.
    FOLLOWS = "follows"
    #: Its files scattered: said as information, and the person may say where it belongs.
    SCATTERED = "scattered"
    #: Its files went together, but the place they went to now holds many other files: a note
    #: about one experiment is wrong on a folder that now mixes several, so it is not followed.
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class Carried:
    """One decision pinned to a place that moved, and where its files went."""

    event_id: str
    kind: str
    old_target: str
    old_place: str
    #: The new folder's target (``folder:<entry_id>``) and place, None where the files scattered
    #: or the new place is no folder of the later census.
    new_target: str | None
    new_place: str | None
    files: int
    went: int
    follow: Follow
    #: How many other files the new place holds beside the ones that came from this folder.
    among: int = 0

    @property
    def evidence(self) -> str:
        if self.follow is Follow.FOLLOWS:
            return f"{self.went:,} of its {self.files:,} files are now in '{self.new_place}'"
        if self.follow is Follow.MIXED:
            return (
                f"its {self.went:,} files are now in '{self.new_place}', among "
                f"{self.among:,} other files"
            )
        return f"its {self.files:,} files went to several folders"

    def sentence(self) -> str:
        """The line the page shows: information either way, and asking nothing."""
        word = _WORD.get(self.kind, "answer")
        if self.follow is Follow.FOLLOWS:
            return (
                f"Your {word} on '{self.old_place}' was written about files that are now in "
                f"'{self.new_place}' ({self.evidence})."
            )
        return (
            f"Your {word} on '{self.old_place}' was written about a folder whose files went to "
            f"several places or now sit among others ({self.evidence}); it stays where it was "
            "written."
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "old_target": self.old_target,
            "old_place": self.old_place,
            "new_target": self.new_target,
            "new_place": self.new_place,
            "files": self.files,
            "went": self.went,
            "among": self.among,
            "follow": self.follow.value,
            "sentence": self.sentence(),
        }


#: How a decision's kind reads in a sentence. Anything not here is an "answer".
_WORD = {"note": "note", "define": "definition"}


@dataclass(frozen=True, slots=True)
class CarryResult:
    """The place-pinned decisions found, and how many had no matched file to speak for them."""

    carried: tuple[Carried, ...]
    #: Decisions on a folder gone from the later census with no matched file beneath it
    #: (an empty folder, or one whose files are gone): nothing to follow, so nothing said.
    no_evidence: int = 0
    #: Decisions on a folder still at its old path: not moved, so not listed.
    still_there: int = 0


@dataclass(frozen=True)
class DecisionBasis:
    """What reading the Record's decisions against one comparison needs, kept small.

    Held by a page with the comparison (``api/routes/reorganisation.py``), so that when the Record
    grows only the decisions are read again, which takes milliseconds, and the matching, which
    takes seconds on a large census, is not done again. It keeps each census's folders, only the
    matched files beneath a folder that is gone from the later census (a decision about a folder
    still there is not moved), how many matched files each later folder holds beneath it, and
    which earlier files moved.
    """

    #: The earlier census's folders: entry identifier to (path as read, path as compared).
    old_folders: dict[str, tuple[str, str]]
    #: The later census's folders: path as compared to (entry identifier, path as read).
    new_folders: dict[str, tuple[str, str]]
    #: Matched files beneath a folder gone from the later census: earlier paths as compared,
    #: sorted, and the same files' later paths in the same order.
    left_keys: tuple[str, ...]
    right_keys: tuple[str, ...]
    #: Matched files beneath each later folder, by its path as compared ("" is every one).
    beneath: dict[str, int]
    #: Earlier entry identifiers of matched files that are now at another path.
    moved: frozenset[str]
    #: The earlier run, read for what a decision about a file (a conclusion, a proposed pair) is
    #: about, only when the Record holds one; what was read is kept in ``subjects``.
    earlier_run: Path | None = None
    subjects: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _folders(connection: duckdb.DuckDBPyConnection, side: str) -> dict[str, tuple[str, str]]:
    """``entry_id`` to (path as read, path as compared) for one side's folders."""
    rows = connection.execute(
        f"SELECT entry_id, relative_path, path_key FROM {side}_entries "  # noqa: S608
        "WHERE entry_type = 'directory' AND NOT is_deleted"
    ).fetchall()
    return {str(entry_id): (str(path), str(key)) for entry_id, path, key in rows}


def _folder_of(key: str) -> str:
    return key.rsplit("/", 1)[0] if "/" in key else ""


def decision_basis(
    connection: duckdb.DuckDBPyConnection, pairs: Iterable[MovedPair], *, earlier_run: Path | None
) -> DecisionBasis:
    """Read what :func:`carried_decisions` and :func:`moved_file_decisions` need from one
    comparison's connection (views ``l_*``, ``r_*`` and table ``matched``).

    ``pairs`` must be ordered by ``left_key`` (as :func:`structure.iter_matched_pairs` gives
    them). The function reads ``pairs`` once, after its own queries, so it may be a stream that
    reads the same connection.
    """
    from ingence.compare.fingerprints import SIZE_BLIND_TIERS

    blind = sorted(tier.value for tier in SIZE_BLIND_TIERS)
    old = _folders(connection, "l")
    new = {key: (entry_id, path) for entry_id, (path, key) in _folders(connection, "r").items()}
    moved = connection.execute(
        "SELECT m.left_id FROM matched m "
        "JOIN l_entries l ON l.entry_id = m.left_id "
        "JOIN r_entries r ON r.entry_id = m.right_id "
        "WHERE l.path_key <> r.path_key AND m.tier NOT IN (SELECT unnest(?::VARCHAR[]))",
        [blind],
    ).fetchall()
    kept: list[tuple[str, str]] = []
    beneath: Counter[str] = Counter()
    for pair in pairs:
        if (
            pair.tier not in blind
            and _folder_of(pair.left_key)
            and _folder_of(pair.left_key) not in new
        ):
            kept.append((pair.left_key, pair.right_key))
        beneath[""] += 1
        folder = _folder_of(pair.right_key)
        while folder:
            beneath[folder] += 1
            folder = _folder_of(folder)
    return DecisionBasis(
        old_folders=old,
        new_folders=new,
        left_keys=tuple(left for left, _ in kept),
        right_keys=tuple(right for _, right in kept),
        beneath=dict(beneath),
        moved=frozenset(str(row[0]) for row in moved),
        earlier_run=earlier_run,
    )


def _place_of(event: RecordEvent, old: dict[str, tuple[str, str]]) -> tuple[str, str | None] | None:
    """The folder a decision was written about, as read, and as compared where its target names
    a folder of the earlier census (a path alone cannot say which root it is on): from its
    target, its scope or its payload."""
    prefix, _, subject = event.target.partition(":")
    if prefix == "folder" and subject in old:
        return old[subject]
    if event.scope.kind.value == "folder" and event.scope.value:
        return event.scope.value.replace("\\", "/").strip("/"), None
    if prefix != "folder":
        return None
    try:
        said = json.loads(event.payload)
    except ValueError:
        return None
    path = said.get("path") if isinstance(said, dict) else None
    return (str(path).replace("\\", "/").strip("/"), None) if path else None


def carried_decisions(
    basis: DecisionBasis,
    events: Iterable[RecordEvent],
    *,
    minimum_share: float = DEFAULT_MINIMUM_SHARE,
) -> CarryResult:
    """Where the files under each folder-pinned decision went, for every folder now gone.

    Covers ``folder:`` targets and folder-scoped events. Decisions about files (claims, candidates)
    are counted by :func:`moved_file_decisions`, never listed: a thousand moved files are one
    sentence.
    """
    old = basis.old_folders
    new_by_key = basis.new_folders
    old_keys = dict(old.values())
    left_keys = basis.left_keys
    found: list[Carried] = []
    no_evidence = still_there = 0
    for event in events:
        found_place = _place_of(event, old)
        if found_place is None:
            continue
        place, known = found_place
        key = known if known is not None else old_keys.get(place, place)
        if key in new_by_key:
            still_there += 1
            continue
        low = bisect.bisect_left(left_keys, key + "/")
        high = bisect.bisect_left(left_keys, key + "0")
        if high == low:
            no_evidence += 1
            continue
        went: Counter[str] = Counter()
        for index in range(low, high):
            rest = left_keys[index][len(key) + 1 :]
            right_key = basis.right_keys[index]
            if right_key == rest:
                went[""] += 1
            elif right_key.endswith("/" + rest):
                went[right_key[: -len(rest) - 1]] += 1
        files = high - low
        target_key, count = (
            max(went.items(), key=lambda item: (item[1], item[0])) if went else ("", 0)
        )
        placed = (
            count / files >= minimum_share
            and target_key != key
            and (target_key == "" or target_key in new_by_key)
        )
        entry_id, new_place = (
            ("", "(top)") if target_key == "" else new_by_key.get(target_key, ("", ""))
        )
        if new_place == ".":  # a root's own folder, where the root is part of the path
            new_place = "(top)"
        among = 0
        follow = Follow.SCATTERED
        if placed:
            # What the new place holds in all, beneath it: a folder that now mixes several
            # experiments is not one experiment's home, whoever wrote the note.
            held = basis.beneath.get(target_key, 0)
            among = max(held - count, 0)
            follow = Follow.FOLLOWS if count / max(held, 1) >= minimum_share else Follow.MIXED
        found.append(
            Carried(
                event_id=event.event_id,
                kind=event.kind.value,
                old_target=event.target,
                old_place=place,
                new_target=f"folder:{entry_id}" if follow is Follow.FOLLOWS and entry_id else None,
                new_place=new_place if placed else None,
                files=files,
                went=count,
                follow=follow,
                among=among,
            )
        )
    found.sort(key=lambda carried: (carried.follow is Follow.FOLLOWS, carried.old_place))
    return CarryResult(tuple(found), no_evidence=no_evidence, still_there=still_there)


def _read_subjects(run: Path, table: str, wanted: set[str]) -> dict[str, tuple[str, ...]]:
    """What each wanted conclusion or proposed pair of one run is about: its entry identifiers."""
    from ingence.storage.catalog import sql_path_list
    from ingence.storage.connection import connect as duckdb_connection

    files = sorted((run / table).glob("*.parquet"))
    found: dict[str, tuple[str, ...]] = dict.fromkeys(wanted, ())
    if not files:
        return found
    listed = sql_path_list(files)
    identifier, about = (
        ("claim_id", "subject") if table == "claims" else ("candidate_id", "source_id, target_id")
    )
    connection = duckdb_connection()
    try:
        rows = connection.execute(
            f"SELECT {identifier}, {about} FROM read_parquet({listed}, union_by_name = true) "  # noqa: S608
            f"WHERE {identifier} IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(wanted)],
        ).fetchall()
    finally:
        connection.close()
    for identifier_value, *ends in rows:
        found[str(identifier_value)] = tuple(str(end) for end in ends if end is not None)
    return found


def moved_file_decisions(basis: DecisionBasis, events: Iterable[RecordEvent]) -> int:
    """How many decisions are about a file (a claim's subject, or either end of a proposed pair)
    that is in the later census at another path. Counted, never listed.

    What each conclusion or proposed pair is about is read from the earlier run once and kept
    in ``basis.subjects``, so a Record that grows reads only what it has not read before."""
    said: dict[str, set[str]] = {"claim": set(), "candidate": set()}
    for event in events:
        prefix, _, subject = event.target.partition(":")
        if prefix in said and subject:
            said[prefix].add(subject)
    if not any(said.values()) or not basis.moved or basis.earlier_run is None:
        return 0
    for prefix, table in (("claim", "claims"), ("candidate", "candidates")):
        unread = {item for item in said[prefix] if f"{prefix}:{item}" not in basis.subjects}
        if unread:
            for item, ends in _read_subjects(basis.earlier_run, table, unread).items():
                basis.subjects[f"{prefix}:{item}"] = ends
    return sum(
        1
        for prefix, items in said.items()
        for item in items
        if any(end in basis.moved for end in basis.subjects.get(f"{prefix}:{item}", ()))
    )


def folder_lines(carried: Iterable[Carried], *, enabled: bool | None = None) -> dict[str, str]:
    """The one line each folder's own page shows: folder place to a sentence.

    Keyed by the new place as read. Several decisions about one old folder are one line, and
    several old folders arriving in one are one line naming how many. A scattered decision gets no
    line, because it has no folder to stand on. Nothing is returned at all where ``enabled`` is
    False. ``enabled`` None reads ``SAY_ON_FOLDER_PAGE`` when called, so setting that after import
    turns the line off too.
    """
    if not (SAY_ON_FOLDER_PAGE if enabled is None else enabled):
        return {}
    by_new: dict[str, list[Carried]] = {}
    for item in carried:
        if item.follow is Follow.FOLLOWS and item.new_place:
            by_new.setdefault(item.new_place, []).append(item)
    lines: dict[str, str] = {}
    for new_place, items in by_new.items():
        olds = sorted({item.old_place for item in items})
        many = len(items)
        what = _WORD.get(items[0].kind, "answer") if many == 1 else "notes and answers"
        if len(olds) == 1:
            went = sum(item.went for item in items[:1])
            files = items[0].files
            counted = ("An" if what[0] in "aeiou" else "A") if many == 1 else f"{many:,}"
            lines[new_place] = (
                f"{counted} {what} {'was' if many == 1 else 'were'} written about '{olds[0]}', "
                f"whose files ({went:,} of {files:,}) are now here."
            )
        else:
            lines[new_place] = (
                f"{many:,} notes and answers were written about {len(olds):,} folders "
                "whose files are now here."
            )
    return lines
