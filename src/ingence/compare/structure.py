"""How differently the same files are organised: counts over the matched pairs.

Every figure is a count over files or pairs of files, reported as "x of y" and never as a score.

Kept paths: matched files whose path, read the way each root reads case, is unchanged.

One rewrite, or many: for each moved file, the part of its old path and new path that is left once
their common ending is removed. Moving ``2025/A/x.tif`` to ``Archive/2025/A/x.tif`` is the rewrite
``'' -> 'Archive'``. If one rewrite accounts for nearly every move, the tree was relocated whole
(nested into a new folder, or a top folder renamed), and its organisation is unchanged.

Pairs still together: take every pair of matched files that shared a folder before. How many still
share one? And of the pairs sharing a folder now, how many shared one before? The first falls when
folders are split or scattered, and the second falls when folders are merged or pooled. Both come
from one table, the number of files that went from old folder *i* to new folder *j*, with no pair
ever enumerated: a folder of *n* files holds *n(n-1)/2* pairs. The adjusted Rand index (Hubert and
Arabie, 1985) is computed from the same table for anyone comparing with other tools. It is shown
beside the counts and not instead of them.

Folders, named: each old folder with its files' new home. A folder is *intact* (every file went to
one new folder that holds nothing else), *moved with company* (every file went to one folder that
also holds others), or *split* (the files went to several folders). New folders that hold several
old ones are *merged*.

Which folders moved at all: an old folder *stayed* when every one of its matched files is in the
folder of the same path now, and ``folders_moved`` counts the others. A file renamed inside a
folder that stayed is counted apart (``files_renamed_in_place``), because no folder changed for it.
``files_regrouped`` counts the files whose folder is not *intact*, the files that now sit among
different files than before. A verdict can then ask how many files a change touched and not only
how many pairs, because one large folder holds most of the pairs.

The counts are computed at the parent-folder level and at the grandparent level. A reorganisation
that renames every experiment folder but keeps the project folders reads differently from one that
dissolves projects, and ``levels`` says which.

Grouping and equality use each path as compared (case folded where a root is known insensitive).
A folder is named in a sentence by its spelling on the side it belongs to, as read.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    import duckdb

__all__ = [
    "REWRITES_KEPT",
    "FolderFate",
    "MovedPair",
    "Structure",
    "compare_structure",
    "iter_matched_pairs",
    "pairs_together",
    "rewrite_of",
]


@dataclass(frozen=True, slots=True)
class MovedPair:
    """One matched file: its old and new path, relative to each side's root (or subtree).

    ``left_path`` and ``right_path`` are as read; the ``_key`` forms are as compared, and are the
    same where no root folds case.
    """

    left_path: str
    right_path: str
    tier: str
    left_key: str = ""
    right_key: str = ""

    def __post_init__(self) -> None:
        if not self.left_key:
            object.__setattr__(self, "left_key", self.left_path)
        if not self.right_key:
            object.__setattr__(self, "right_key", self.right_path)


@dataclass(frozen=True, slots=True)
class FolderFate:
    """What became of one old folder's files."""

    old_folder: str
    files: int
    #: The new folder holding most of them, and how many.
    new_folder: str
    to_new_folder: int
    #: How many files the new folder holds in all (of the matched ones).
    new_folder_files: int
    fate: str  # intact | moved_with_company | split
    #: Every one of its files is in the folder of the same path now.
    stayed: bool = False


@dataclass
class Structure:
    matched: int = 0
    paths_kept: int = 0
    names_kept: int = 0
    rewrites: list[tuple[str, str, int]] = field(default_factory=list)
    #: At each level (1 = parent folder, 2 = grandparent...): pairs together before, still
    #: together, together now, and the adjusted Rand index.
    levels: dict[int, dict[str, Any]] = field(default_factory=dict)
    folders: list[FolderFate] = field(default_factory=list)
    merged: list[tuple[str, int]] = field(default_factory=list)
    #: Old folders with at least one matched file now in a folder of another path.
    folders_moved: int = 0
    #: Matched files renamed inside a folder that stayed where it was.
    files_renamed_in_place: int = 0
    #: Matched files whose old folder is not intact: they now sit among different files.
    files_regrouped: int = 0

    @property
    def dominant_rewrite_share(self) -> tuple[int, int]:
        """(files the most common rewrite explains, files moved at all)."""
        moved = self.matched - self.paths_kept
        top = self.rewrites[0][2] if self.rewrites else 0
        return top, moved


#: Matched pairs are read this many at a time. The comparison does not list every matched pair in
#: memory before counting anything, and what it measures is the same at any size (a test compares
#: one pair at a time with this).
PAIRS_BATCH_ROWS = 50_000


def iter_matched_pairs(
    connection: duckdb.DuckDBPyConnection, *, rows: int | None = None
) -> Iterator[MovedPair]:
    """The matched files' paths on each side, ordered by the left key, a batch at a time.

    A generator: the query runs at the first pair asked for, so a caller may run its own queries
    on the connection before reading any, and none while reading.
    """
    cursor = connection.execute(
        """
        SELECT l.relative_path, r.relative_path, m.tier, l.path_key, r.path_key
        FROM matched m
        JOIN l_entries l ON l.entry_id = m.left_id
        JOIN r_entries r ON r.entry_id = m.right_id
        ORDER BY l.path_key, r.path_key
        """
    )
    while batch := cursor.fetchmany(rows or PAIRS_BATCH_ROWS):
        for left, right, tier, left_key, right_key in batch:
            yield MovedPair(
                left_path=str(left),
                right_path=str(right),
                tier=str(tier),
                left_key=str(left_key),
                right_key=str(right_key),
            )


def rewrite_of(left: str, right: str) -> tuple[str, str]:
    """The prefixes left once two paths' common ending is removed."""
    a, b = PurePosixPath(left).parts, PurePosixPath(right).parts
    shared = 0
    while shared < min(len(a), len(b)) and a[len(a) - 1 - shared] == b[len(b) - 1 - shared]:
        shared += 1
    return "/".join(a[: len(a) - shared]), "/".join(b[: len(b) - shared])


def ancestor(path: str, level: int) -> str:
    """The folder ``level`` steps up from a file's folder: 1 is its own folder, 2 the one above."""
    parts = PurePosixPath(path).parts[:-1]
    return "/".join(parts[: max(0, len(parts) - (level - 1))]) if len(parts) >= level - 1 else ""


def _choose2(n: int) -> int:
    return n * (n - 1) // 2


def pairs_together(
    assignments: Iterable[tuple[str, str]] | Counter[tuple[str, str]],
) -> dict[str, Any]:
    """From (old group, new group) per file: pairs together before, still, now, and the ARI.

    ``assignments`` is one pair of groups a file, or those pairs already counted.
    """
    table: Counter[tuple[str, str]] = (
        assignments if isinstance(assignments, Counter) else Counter(assignments)
    )
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    for (old, new), count in table.items():
        before[old] += count
        after[new] += count
    files = sum(table.values())
    both = sum(_choose2(count) for count in table.values())
    together_before = sum(_choose2(count) for count in before.values())
    together_after = sum(_choose2(count) for count in after.values())
    all_pairs = _choose2(files)
    expected = (together_before * together_after / all_pairs) if all_pairs else 0.0
    ceiling = (together_before + together_after) / 2
    ari = (both - expected) / (ceiling - expected) if ceiling != expected else 1.0
    return {
        "files": files,
        "pairs_together_before": together_before,
        "pairs_still_together": both,
        "pairs_together_now": together_after,
        "adjusted_rand_index": round(ari, 4) if not math.isnan(ari) else None,
    }


#: How many of the most common rewrites a structure keeps (the counts of the rest are not kept).
REWRITES_KEPT = 20


def compare_structure(
    pairs: Iterable[MovedPair],
    *,
    levels: tuple[int, ...] = (1, 2),
    rewrites_kept: int = REWRITES_KEPT,
) -> Structure:
    """Every structural count over the matched files; the ``rewrites_kept`` most common rewrites.

    The function makes one pass over the pairs, holding counts by folder and never the pairs, so
    ``pairs`` may be a stream (:func:`iter_matched_pairs`).
    """
    structure = Structure(matched=0)
    rewrites: Counter[tuple[str, str]] = Counter()
    grouped: dict[int, Counter[tuple[str, str]]] = {level: Counter() for level in levels}
    by_old: dict[str, Counter[str]] = {}
    new_totals: Counter[str] = Counter()
    sources: dict[str, set[str]] = {}
    spelled: dict[str, str] = {}
    for pair in pairs:
        structure.matched += 1
        for level in levels:
            grouped[level][(ancestor(pair.left_key, level), ancestor(pair.right_key, level))] += 1
        old, new = ancestor(pair.left_key, 1), ancestor(pair.right_key, 1)
        spelled.setdefault("l:" + old, ancestor(pair.left_path, 1))
        spelled.setdefault("r:" + new, ancestor(pair.right_path, 1))
        by_old.setdefault(old, Counter())[new] += 1
        new_totals[new] += 1
        sources.setdefault(new, set()).add(old)
        if pair.left_key == pair.right_key:
            structure.paths_kept += 1
            structure.names_kept += 1
            continue
        if PurePosixPath(pair.left_key).name == PurePosixPath(pair.right_key).name:
            structure.names_kept += 1
        elif ancestor(pair.left_key, 1) == ancestor(pair.right_key, 1):
            structure.files_renamed_in_place += 1
        rewrites[rewrite_of(pair.left_path, pair.right_path)] += 1
    structure.rewrites = [
        (old, new, count)
        for (old, new), count in sorted(rewrites.items(), key=lambda item: (-item[1], item[0]))[
            :rewrites_kept
        ]
    ]
    for level in levels:
        structure.levels[level] = pairs_together(grouped[level])
    for old, destinations in sorted(by_old.items()):
        (new, count), *_ = sorted(destinations.items(), key=lambda item: (-item[1], item[0]))
        files = sum(destinations.values())
        if count == files and new_totals[new] == files:
            fate = "intact"
        elif count == files:
            fate = "moved_with_company"
        else:
            fate = "split"
        stayed = count == files and new == old
        structure.folders_moved += 0 if stayed else 1
        structure.files_regrouped += 0 if fate == "intact" else files
        structure.folders.append(
            FolderFate(
                old_folder=spelled["l:" + old],
                files=files,
                new_folder=spelled["r:" + new],
                to_new_folder=count,
                new_folder_files=new_totals[new],
                fate=fate,
                stayed=stayed,
            )
        )
    structure.merged = sorted(
        ((spelled["r:" + new], len(olds)) for new, olds in sources.items() if len(olds) > 1),
        key=lambda item: (-item[1], item[0]),
    )
    return structure
