"""The keys a file keeps when it moves, strongest first.

An entry identifier is a digest of root and path, so it is exactly the thing a move changes. What
a move does not change, from strongest to weakest:

=========  ===========================================  ===================================
tier       key                                          survives
=========  ===========================================  ===================================
``place``  root, path, size and modification time       nothing moved: the same file again
``bytes``  the whole-file digest and the size           move, copy, rename, another drive
``head``   the sampled digest and the size              move, copy, rename (a candidate)
``named``  name, size and modification time             move, most copies (time kept)
``sized``  size and modification time                   move and rename together
=========  ===========================================  ===================================

Two tiers need no size. Every tier above keys on size, and ``named`` and ``sized`` leave a zero size
out. So an inventory that states no sizes (a folder listing an earlier tool wrote, every size
absent or zero) would match nothing against a census of the same tree. These two tiers come after
every tier above, and only where a side states no sizes:

====================  ===================================  ====================================
tier                  key                                  survives
====================  ===================================  ====================================
``place_no_size``     the path alone                       nothing moved (sizes not stated)
``parent_no_size``    the parent folder's name and the     a tree moved under a new root
                      file's name
====================  ===================================  ====================================

These two tiers are a weaker kind of match, and the report says so. A place, or a folder and a
name, says where a file is and never what it holds. So a size-blind match is never said to prove
the same bytes, and nothing is carried across one (``moves.carry_policy``). A person's decisions
and a dive's readings follow only a match that proves the bytes, or name, size and time together.

The function ``match.match_files`` decides per side whether a side states no sizes: a side of at
least one file where every file's size is absent or zero. It never guesses file by file. On a side
where the other files have sizes, an empty file, or one whose size is absent, is matched only by
the tiers above (an empty file only by its place). A side that states no sizes is left out of
every tier above, whose keys would read its zeros as sizes, so its files match only here. Neither
size-blind tier holds a time, because an earlier tool's inventory may state none, or state it at
another grain or zone. Case is merged as each root's label says (``path_key``, ``name_key``).

``place`` matters for a tree walked twice. Without it, every file whose name is shared by another
(a template copied into forty folders) would stay unmatched, though each sits exactly where it
was. A path names one file on one side, so a match on it needs no tiebreak. Only a file that is
*not* where it was falls through to the tiers that look elsewhere.

Birth time is in no key. A copy gives a file a new birth, which is what makes a copied tree look
new. Inode and device are in no key either (``scan/reuse.py``), because they are not comparable
across machines or even across mounts. Change time is in no key: a rename or a move sets it, so
the stat fingerprint that the reuse model keeps is useless here by design.

A key shared by many files does not identify a file. A template copied into forty folders shares its
name, size and time forty times. A key held by more than one file on either side is *ambiguous* and
matches nothing at that tier. The files fall through to the next tier or stay unmatched, and the
counts say how many. Zero-byte files are never matched by anything but their place, except on a side
that states no sizes at all (above).

Modification time is compared at a grain and not to the nanosecond. A copy between filesystems
rounds it: ExFAT keeps 10 ms, FAT 2 s, NTFS 100 ns, APFS 1 ns. ``mtime_grain_ns`` floors both
sides to one grain. The default, 2 s, is the coarsest grain a drive is likely to have.
It is a parameter because a coarser grain makes more keys ambiguous. The grain works only inside a
key that also holds the name and the size, so a looser grain costs ambiguity and never a match
between different files.
"""

from __future__ import annotations

from enum import StrEnum

from ingence.core.schema import WHOLE_DIGEST_UNCHECKED_MOVES

__all__ = [
    "DEFAULT_MTIME_GRAIN_NS",
    "DIGEST_KEYS",
    "SIZE_BLIND_TIERS",
    "TIERS",
    "Tier",
    "key_sql",
]


class Tier(StrEnum):
    PLACE = "place"
    BYTES = "bytes"
    HEAD = "head"
    NAMED = "named"
    SIZED = "sized"
    #: The path alone, where a side states no sizes.
    PLACE_NO_SIZE = "place_no_size"
    #: The parent folder's name and the file's name: a tree moved under a new root, where a side
    #: states no sizes.
    PARENT_NO_SIZE = "parent_no_size"


#: Tiers in the order matching tries them.
TIERS: tuple[Tier, ...] = (
    Tier.PLACE,
    Tier.BYTES,
    Tier.HEAD,
    Tier.NAMED,
    Tier.SIZED,
    Tier.PLACE_NO_SIZE,
    Tier.PARENT_NO_SIZE,
)

#: The tiers that key on no size: tried only where a side states no sizes, after every other,
#: and said in the report as matched without size.
SIZE_BLIND_TIERS: frozenset[Tier] = frozenset({Tier.PLACE_NO_SIZE, Tier.PARENT_NO_SIZE})

#: The observation keys the dive writes for each digest (``identity/bytes.py``).
DIGEST_KEYS = {Tier.BYTES: "identity.blake3_whole", Tier.HEAD: "identity.blake3_quick"}

#: Two seconds: FAT's grain, the coarsest a copy is likely to have passed through.
DEFAULT_MTIME_GRAIN_NS = 2_000_000_000


def key_sql(side: str, tier: Tier, *, mtime_grain_ns: int = DEFAULT_MTIME_GRAIN_NS) -> str:
    """SQL producing ``(entry_id, key)`` for one side at one tier.

    Reads views ``<side>_entries`` and ``<side>_observations`` (:func:`match.attach`), whose
    ``path_key`` and ``name_key`` are the path and name as compared: separators forward, one
    composed form, case merged on both sides where a root ignores it, and the root in the path
    where both sides read the same several roots. Files only, never a container's members.
    """
    grain = int(mtime_grain_ns)
    files = (
        f"FROM {side}_entries e WHERE e.entry_type = 'file' AND NOT e.is_deleted "
        "AND e.transformation IS NULL AND e.ordinal IS NULL"
    )
    if tier in DIGEST_KEYS:
        # A whole-file digest that a walk carried across a move, and that no dive has checked, is
        # not "the same bytes": it was read from the file that left. The walk's run is the entry's
        # first run, so a digest that a dive computed at the new path is told apart from it.
        unchecked = ""
        if tier is Tier.BYTES:
            moves = ", ".join(f"'{move.value}'" for move in sorted(WHOLE_DIGEST_UNCHECKED_MOVES))
            unchecked = (
                f"AND NOT coalesce(coalesce(e.moved_by IN ({moves}), false) "
                "AND o.run_id = e.first_seen_run, false) "
            )
        return (
            "SELECT DISTINCT e.entry_id, o.value_text || ':' || "  # noqa: S608
            "CAST(e.size_bytes AS VARCHAR) AS key "
            f"FROM {side}_entries e JOIN {side}_observations o ON o.entry_id = e.entry_id "
            f"WHERE o.key = '{DIGEST_KEYS[tier]}' AND e.entry_type = 'file' AND NOT e.is_deleted "
            "AND e.transformation IS NULL AND e.ordinal IS NULL "
            f"{unchecked}"
            "AND o.value_text IS NOT NULL AND e.size_bytes > 0"
        )
    if tier is Tier.PLACE:
        return (
            "SELECT e.entry_id, e.path_key || '|' || CAST(e.size_bytes AS VARCHAR) || '|' || "
            f"CAST(e.modified_ns // {grain} AS VARCHAR) AS key {files} "
            "AND e.size_bytes IS NOT NULL AND e.modified_ns IS NOT NULL"
        )
    if tier is Tier.PLACE_NO_SIZE:
        return f"SELECT e.entry_id, e.path_key AS key {files}"
    if tier is Tier.PARENT_NO_SIZE:
        # The folder's own name, as compared, then the file's: "run/x.tif" wherever "run" sits.
        return (
            "SELECT e.entry_id, regexp_extract(e.path_key, '([^/]*)/[^/]*$', 1) || '/' || "
            f"e.name_key AS key {files}"
        )
    if tier is Tier.NAMED:
        return (
            "SELECT e.entry_id, e.name_key || '|' || CAST(e.size_bytes AS VARCHAR) || '|' || "
            f"CAST(e.modified_ns // {grain} AS VARCHAR) AS key {files} "
            "AND e.size_bytes > 0 AND e.modified_ns IS NOT NULL"
        )
    return (
        "SELECT e.entry_id, CAST(e.size_bytes AS VARCHAR) || '|' || "
        f"CAST(e.modified_ns // {grain} AS VARCHAR) AS key {files} "
        "AND e.size_bytes > 0 AND e.modified_ns IS NOT NULL"
    )
