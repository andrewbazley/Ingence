"""Where each new path came from: the move map between two censuses, and what follows a file.

A moved file's dive readings are reused automatically when the file is matched by digest (safe: the
bytes are the same) and by name, size and time (a strong candidate, not proof). The purpose of the
whole feature is to stop data that was analysed before from being read as new.

:func:`move_map` matches the files gone from an earlier census to the files new in a later one,
one to one, by the comparison's tiers. It is a pure read of two runs. Nothing here is stored. The
walk finds moves itself (``scan/census.py``) and stores them as the entry columns ``moved_from`` and
``moved_by``, so that a dive keeps a moved file's readings and replay reads a decision at its files'
new place. The table below says what follows a move found by each of the comparison's tiers.

What follows a moved file (:func:`carry_policy`):

===========  ==========================  ====================================================
tier         readings (digests, clocks)  a person's decisions about it
===========  ==========================  ====================================================
``bytes``    carried, confirmed          read onto the new entry
``head``     carried, confirmed          read onto the new entry
``named``    carried, candidate          read onto the new entry
``sized``    not carried                 not read across; shown on the History page only
===========  ==========================  ====================================================

The two size-blind tiers (``place_no_size`` and ``parent_no_size``) match a file by where it is,
where a side states no sizes, which never proves what the file holds. Nothing is carried across
them, and they are not in :data:`MOVE_TIERS`.

A candidate reading is used like any other and marked so, and the next dive that reaches the file
checks it cheaply. The dive computes the sampled digest anyway. A digest equal to the carried one
confirms every carried reading. A different one drops them, and the file is read afresh. A match
by size and time without the name (``sized``: a move and a rename together) is not carried and
stays a line in the comparison. The ``place`` tier is not a move at all: a file at its own path is
not in the map.

``match.match_files`` compares every file on both sides. The move map compares only the paths that
changed, which on a re-walk where two files changed is two rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ingence.compare.fingerprints import DEFAULT_MTIME_GRAIN_NS, Tier, key_sql

if TYPE_CHECKING:
    import duckdb

__all__ = [
    "CARRIED_TIERS",
    "DECISION_TIERS",
    "MOVE_TIERS",
    "Move",
    "Standing",
    "carry_policy",
    "move_map",
]


class Standing(StrEnum):
    """How far a carried reading is trusted."""

    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"


#: The tiers whose moves carry a dive's readings, and how each is trusted.
CARRIED_TIERS: dict[Tier, Standing] = {
    Tier.BYTES: Standing.CONFIRMED,
    Tier.HEAD: Standing.CONFIRMED,
    Tier.NAMED: Standing.CANDIDATE,
}

#: The tiers across which a person's decisions are read onto the moved file.
DECISION_TIERS: frozenset[Tier] = frozenset({Tier.BYTES, Tier.HEAD, Tier.NAMED})

#: The tiers a move can be known by: every tier but ``place``, which is a file not moved.
MOVE_TIERS: tuple[Tier, ...] = (Tier.BYTES, Tier.HEAD, Tier.NAMED, Tier.SIZED)


@dataclass(frozen=True, slots=True)
class Move:
    """One file at a new path, the earlier census's entry it came from, and how that is known."""

    new_entry_id: str
    old_entry_id: str
    tier: Tier
    old_path: str
    new_path: str


def carry_policy(tier: Tier) -> tuple[Standing | None, bool]:
    """What a move at this tier carries: the readings' standing (None: not carried), and
    whether a person's decisions are read across."""
    return CARRIED_TIERS.get(tier), tier in DECISION_TIERS


def move_map(
    connection: duckdb.DuckDBPyConnection,
    *,
    tiers: tuple[Tier, ...] = MOVE_TIERS,
    mtime_grain_ns: int = DEFAULT_MTIME_GRAIN_NS,
) -> list[Move]:
    """Match files gone from the earlier census (``l_*`` views) to files new in the later one
    (``r_*`` views, both from :func:`match.attach`).

    One to one per tier, a key held by several files on either side matching nothing, stronger
    tiers first: ``match.match_files``' rule, applied only to the paths that changed.
    """
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE gone AS
        SELECT entry_id, relative_path FROM l_entries
        WHERE entry_type = 'file' AND NOT is_deleted
          AND path_key NOT IN (SELECT path_key FROM r_entries WHERE NOT is_deleted)
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE arrived AS
        SELECT entry_id, relative_path FROM r_entries
        WHERE entry_type = 'file' AND NOT is_deleted
          AND path_key NOT IN (SELECT path_key FROM l_entries WHERE NOT is_deleted)
        """
    )
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE moved (new_id VARCHAR, old_id VARCHAR, tier VARCHAR)"
    )
    for tier in tiers:
        old_keys = (
            f"SELECT k.* FROM ({key_sql('l', tier, mtime_grain_ns=mtime_grain_ns)}) k "  # noqa: S608
            "JOIN gone g USING (entry_id) WHERE k.entry_id NOT IN (SELECT old_id FROM moved)"
        )
        new_keys = (
            f"SELECT k.* FROM ({key_sql('r', tier, mtime_grain_ns=mtime_grain_ns)}) k "  # noqa: S608
            "JOIN arrived a USING (entry_id) WHERE k.entry_id NOT IN (SELECT new_id FROM moved)"
        )
        connection.execute(
            f"""
            INSERT INTO moved
            SELECT n.entry_id, o.entry_id, ?
            FROM (SELECT key, any_value(entry_id) AS entry_id FROM ({old_keys})
                  GROUP BY key HAVING count(*) = 1) o
            JOIN (SELECT key, any_value(entry_id) AS entry_id FROM ({new_keys})
                  GROUP BY key HAVING count(*) = 1) n USING (key)
            """,  # noqa: S608
            [tier.value],
        )
    rows = connection.execute(
        """
        SELECT m.new_id, m.old_id, m.tier, g.relative_path, a.relative_path
        FROM moved m JOIN gone g ON g.entry_id = m.old_id JOIN arrived a ON a.entry_id = m.new_id
        ORDER BY a.relative_path
        """
    ).fetchall()
    return [
        Move(
            new_entry_id=str(new_id),
            old_entry_id=str(old_id),
            tier=Tier(tier),
            old_path=str(old_path),
            new_path=str(new_path),
        )
        for new_id, old_id, tier, old_path, new_path in rows
    ]
