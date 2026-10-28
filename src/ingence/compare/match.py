"""Matching the files of two runs one to one, tier by tier, in SQL.

Two runs may belong to one project (a re-walk of a reorganised tree) or to two (the same files
scanned from another machine, or copied to another drive and scanned there). One side may be a
subtree of a run: two folders of one census (a backup beside its source) are two sides. Either way
each side is read straight from its run directory's Parquet, through one DuckDB connection, and
nothing is loaded into Python but counts and the matched pairs' paths.

The match is one to one and never many to many. At each tier a key held by exactly one unmatched
file on each side matches those two. A key held by several files on either side matches nothing
and is counted as ambiguous. Files matched at a stronger tier leave the pool before a weaker one
is tried, so a file matched by its digest is never re-matched by its size.

Paths are compared as a person reads them. Separators are forward (a Windows census stores what it
read, backslashes and all). Composed characters are in one form (NFC), so an accented letter stored
as one character or as a letter and its accent is one spelling. Case is merged where a root is known
to ignore it. Where sensitivity is unknown on both sides, a path is left as it is. Case is merged on
both sides alike (:func:`attach_both`): where either side's root ignores case, both sides' paths are
compared with it merged. This is because a path folded on one side only matches nothing on the
other, and one census read twice with its root recorded two ways would read as "the whole tree
moved". A subtree side is compared by the path beneath its folder, so ``Backup/2025/x.tif`` and
``Source/2025/x.tif`` share ``2025/x.tif``.

A path is compared with its root where both sides read the same several roots. Two roots of one
project can hold the same relative paths (a second root that mirrors the first). A path alone
would then name two files on each side and match nothing, and a tree walked twice unchanged would
read as "different files". Where the two sides read the same roots at the same folder, the root is
part of every path as compared, so the same-place match and the grouping into folders both know
it. Where the roots differ (another computer, another drive, two folders of one census), the root
is left out, since the same file has another root there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.compare.fingerprints import (
    DEFAULT_MTIME_GRAIN_NS,
    SIZE_BLIND_TIERS,
    TIERS,
    Tier,
    key_sql,
)

if TYPE_CHECKING:
    from pathlib import Path

    import duckdb

__all__ = ["Keying", "MatchSummary", "Side", "attach", "attach_both", "match_files"]


@dataclass(frozen=True, slots=True)
class Side:
    """One run to compare: its project directory, its run identifier, and optionally a folder.

    ``under`` names a folder of the census (its path as read, forward slashes, no trailing one)
    and restricts the side to what lies beneath it, paths taken from below it.
    """

    project: Path
    run_id: str
    label: str = ""
    under: str = ""


@dataclass
class MatchSummary:
    """How many files each tier matched, and what stayed unmatched, per side.

    An unmatched file is one of three things, counted apart so that none is said as another:

    - changed: a file at the same path on the other side, of another size or time (edited, or
      replaced);
    - not told apart: a file whose key the other side also holds, among several files holding it
      on one side or both. Identical copies are there, but which is which cannot be said;
    - absent: nothing on the other side answers to it (new on the later side, gone from the
      earlier).

    ``states_no_sizes`` says, per side, whether it states no sizes at all (every file's size
    absent or zero). Only then are the size-blind tiers tried, and ``matched_without_size`` counts
    what they matched, a weaker kind of match that never proves the same bytes.
    """

    files: dict[str, int] = field(default_factory=dict)
    matched: dict[str, int] = field(default_factory=dict)
    ambiguous: dict[str, dict[str, int]] = field(default_factory=dict)
    unmatched: dict[str, int] = field(default_factory=dict)
    changed: dict[str, int] = field(default_factory=dict)
    not_told_apart: dict[str, int] = field(default_factory=dict)
    absent: dict[str, int] = field(default_factory=dict)
    states_no_sizes: dict[str, bool] = field(default_factory=dict)

    @property
    def matched_total(self) -> int:
        return sum(self.matched.values())

    @property
    def matched_without_size(self) -> int:
        """Files matched by a tier that keys on no size: never proof of the same bytes."""
        return sum(self.matched.get(tier.value, 0) for tier in SIZE_BLIND_TIERS)

    def present(self, side: str) -> int:
        """Files of one side that are on the other: matched, changed there, or not told apart."""
        return self.files.get(side, 0) - self.absent.get(side, self.unmatched.get(side, 0))


def _parquet_list(directory: Path) -> str:
    from ingence.storage.catalog import sql_path_list

    paths = sorted(directory.glob("*.parquet"))
    if not paths:
        return ""
    return sql_path_list(paths)


def _source(connection: duckdb.DuckDBPyConnection, table: str, directory: Path) -> str:
    """One table of one run as a ``SELECT`` holding every current column.

    Through the catalog's compatibility projection, so a run an older engine wrote is read with
    each column added since at its declared default, rather than refused.
    """
    from ingence.storage.catalog import parquet_source
    from ingence.storage.layout import TABLES

    spec = next(spec for spec in TABLES if spec.name == table)
    return parquet_source(connection, spec, sorted(directory.glob("*.parquet")))


@dataclass(frozen=True, slots=True)
class Keying:
    """How the two sides' paths are compared (:func:`attach_both`), shown in the report.

    ``folded`` names the roots whose paths are compared with case merged, on both sides;
    ``every_root`` merges case for every path on both sides. ``by_root`` puts each file's root
    into its path as compared.
    """

    every_root: bool = False
    folded: tuple[str, ...] = ()
    by_root: bool = False


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _attach_tables(connection: duckdb.DuckDBPyConnection, prefix: str, side: Side) -> str:
    """Views over every table of one run but its entries; returns the ``SELECT`` of its entries."""
    from ingence.storage.commit import run_path

    run = run_path(side.project, side.run_id)
    if not _parquet_list(run / "entries"):
        message = f"run {side.run_id} of {side.project.name} holds no entries to compare"
        raise ValueError(message)
    listed = {
        table: _parquet_list(run / table)
        for table in ("entries", "observations", "roots", "claims", "candidates")
    }
    for table, files in listed.items():
        if files and table != "entries":
            connection.execute(
                f"CREATE OR REPLACE TEMP VIEW {prefix}_{table} AS "
                f"{_source(connection, table, run / table)}"
            )
        elif not files and table in ("observations", "roots"):
            connection.execute(
                f"CREATE OR REPLACE TEMP VIEW {prefix}_{table} AS SELECT NULL::VARCHAR AS "
                "entry_id, NULL::VARCHAR AS key, NULL::VARCHAR AS value_text, "
                "NULL::VARCHAR AS run_id, NULL::VARCHAR AS root_id, NULL::VARCHAR AS mount_path, "
                "NULL::VARCHAR AS case_sensitivity WHERE false"
            )
    return _source(connection, "entries", run / "entries")


def _entries_view(
    connection: duckdb.DuckDBPyConnection, prefix: str, side: Side, entries: str, keying: Keying
) -> None:
    """``<prefix>_entries``, with ``path_key`` and ``name_key``: the path and name as compared.

    ``entries`` is the ``SELECT`` reading the side's entries (:func:`_attach_tables`).
    """
    under = side.under.replace("\\", "/").strip("/")
    beneath = f"substr(relative_path, {len(under) + 2})" if under else "relative_path"
    where = f"WHERE starts_with(relative_path, {_quote(under + '/')})" if under else ""

    def folded(column: str) -> str:
        composed = f"nfc_normalize({column})"
        if keying.every_root:
            return f"lower({composed})"
        if keying.folded:
            roots = ", ".join(_quote(root) for root in keying.folded)
            return f"CASE WHEN root_id IN ({roots}) THEN lower({composed}) ELSE {composed} END"
        return composed

    path_key = folded("relative_path")
    if keying.by_root:
        path_key = (
            f"CASE WHEN relative_path = '.' THEN root_id ELSE root_id || '/' || {path_key} END"
        )
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW {prefix}_entries AS "  # noqa: S608
        f"SELECT *, {path_key} AS path_key, {folded('name')} AS name_key "
        f"FROM (SELECT * REPLACE ({beneath} AS relative_path) "
        "FROM (SELECT * REPLACE (replace(relative_path, '\\', '/') AS relative_path) "
        f"FROM ({entries})) {where})"
    )


def _roots_read(connection: duckdb.DuckDBPyConnection, prefix: str) -> dict[str, bool]:
    """Each root of one side, and whether it is recorded as ignoring case."""
    rows = connection.execute(
        f"SELECT root_id, case_sensitivity FROM {prefix}_roots"  # noqa: S608
    ).fetchall()
    return {str(root): str(case) == "insensitive" for root, case in rows if root is not None}


def attach(
    connection: duckdb.DuckDBPyConnection, prefix: str, side: Side, *, keying: Keying | None = None
) -> None:
    """Views ``<prefix>_entries``, ``<prefix>_observations``, ``<prefix>_roots`` and, where the
    run holds them, ``<prefix>_claims`` and ``<prefix>_candidates``, over one run.

    Read from the run directory directly, as ``storage/catalog.compare_runs`` does, because the
    two runs may belong to two projects and one catalog opens one project. ``<prefix>_entries``
    gains ``path_key`` and ``name_key``, the path and name as compared (see the module's
    account), and its ``relative_path`` has separators forward and, for a subtree side, the
    folder's own path taken off; the folder itself is not one of its entries.

    ``keying`` None reads one side on its own (case merged where every root it reads ignores
    case); a comparison attaches its two sides with :func:`attach_both`, which decides it once.
    """
    entries = _attach_tables(connection, prefix, side)
    if keying is None:
        roots = _roots_read(connection, prefix)
        keying = Keying(every_root=bool(roots) and all(roots.values()))
    _entries_view(connection, prefix, side, entries, keying)


def attach_both(connection: duckdb.DuckDBPyConnection, left: Side, right: Side) -> Keying:
    """Attach the two sides as ``l_*`` and ``r_*``, their paths compared alike.

    Case: where the two sides read the same roots, a root's paths are compared with case merged
    when either side records it as ignoring case. Where they read different roots, every path is
    compared with case merged when any root of either side ignores case. Merging costs at most a
    key held by two files (which then match by a later key), never a match between two different
    files.

    Roots: see the module's account.
    """
    entries = {
        prefix: _attach_tables(connection, prefix, side)
        for prefix, side in (("l", left), ("r", right))
    }
    left_roots, right_roots = _roots_read(connection, "l"), _roots_read(connection, "r")
    shared = bool(left_roots) and set(left_roots) == set(right_roots)
    if shared:
        keying = Keying(
            folded=tuple(
                sorted(root for root in left_roots if left_roots[root] or right_roots[root])
            ),
            by_root=len(left_roots) > 1 and left.under == right.under,
        )
    else:
        keying = Keying(every_root=any(left_roots.values()) or any(right_roots.values()))
    for prefix, side in (("l", left), ("r", right)):
        _entries_view(connection, prefix, side, entries[prefix], keying)
    return keying


def match_files(
    connection: duckdb.DuckDBPyConnection,
    *,
    tiers: tuple[Tier, ...] = TIERS,
    mtime_grain_ns: int = DEFAULT_MTIME_GRAIN_NS,
) -> MatchSummary:
    """Fill a temporary table ``matched(left_id, right_id, tier)`` and return the counts.

    Expects views ``l_*`` and ``r_*`` from :func:`attach`.

    The function decides once, per side, whether a side states no sizes: a side of at least one
    file where every file's size is absent or zero. Only then are the size-blind tiers
    (``fingerprints.SIZE_BLIND_TIERS``) tried, after every other. That side takes no part in a
    size-bearing tier, whose keys would read its zeros as sizes. Those tiers are passed over, each
    matching nothing and counting no key as shared. Where both sides state sizes, the size-blind
    tiers are not tried and are not in ``matched``.
    """
    summary = MatchSummary()
    for side in ("l", "r"):
        row = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE size_bytes > 0) "  # noqa: S608
            f"FROM {side}_entries WHERE entry_type = 'file' AND NOT is_deleted "
            "AND transformation IS NULL AND ordinal IS NULL"
        ).fetchone()
        summary.files[side] = int(row[0]) if row else 0
        summary.states_no_sizes[side] = bool(row and row[0] and not row[1])
    sizeless = any(summary.states_no_sizes.values())
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE matched (left_id VARCHAR, right_id VARCHAR, tier VARCHAR)"
    )
    connection.execute("CREATE OR REPLACE TEMP TABLE unsettled (side VARCHAR, entry_id VARCHAR)")
    for tier in tiers:
        blind = tier in SIZE_BLIND_TIERS
        if blind and not sizeless:
            continue
        if sizeless and not blind:
            # A side that states no sizes takes no part in a tier whose key holds a size, so the
            # tier can match nothing, and no key of the other side is "shared" there.
            summary.matched[tier.value] = 0
            summary.ambiguous[tier.value] = {"l": 0, "r": 0}
            continue
        for side, table, done in (("l", "lk", "left_id"), ("r", "rk", "right_id")):
            connection.execute(
                f"CREATE OR REPLACE TEMP TABLE {table} AS "  # noqa: S608
                f"SELECT * FROM ({key_sql(side, tier, mtime_grain_ns=mtime_grain_ns)}) "
                f"WHERE entry_id NOT IN (SELECT {done} FROM matched)"
            )
        connection.execute(
            """
            INSERT INTO matched
            SELECT l.entry_id, r.entry_id, ?
            FROM (SELECT key, any_value(entry_id) AS entry_id FROM lk
                  GROUP BY key HAVING count(*) = 1) l
            JOIN (SELECT key, any_value(entry_id) AS entry_id FROM rk
                  GROUP BY key HAVING count(*) = 1) r USING (key)
            """,
            [tier.value],
        )
        # A key both sides hold that matched nothing: identical copies, there but not told apart.
        connection.execute(
            """
            INSERT INTO unsettled
            SELECT 'l', entry_id FROM lk WHERE key IN (SELECT key FROM rk)
              AND entry_id NOT IN (SELECT left_id FROM matched)
            UNION ALL
            SELECT 'r', entry_id FROM rk WHERE key IN (SELECT key FROM lk)
              AND entry_id NOT IN (SELECT right_id FROM matched)
            """
        )
        row = connection.execute(
            "SELECT count(*) FROM matched WHERE tier = ?", [tier.value]
        ).fetchone()
        summary.matched[tier.value] = int(row[0]) if row else 0
        ambiguous: dict[str, int] = {}
        for side, table in (("l", "lk"), ("r", "rk")):
            found = connection.execute(
                f"SELECT coalesce(sum(n), 0) FROM (SELECT count(*) AS n FROM {table} "  # noqa: S608
                "GROUP BY key HAVING count(*) > 1)"
            ).fetchone()
            ambiguous[side] = int(found[0]) if found else 0
        summary.ambiguous[tier.value] = ambiguous
    total = summary.matched_total
    summary.unmatched = {side: summary.files[side] - total for side in ("l", "r")}
    _unmatched_kinds(connection, summary)
    return summary


def _unmatched_kinds(connection: duckdb.DuckDBPyConnection, summary: MatchSummary) -> None:
    """Split each side's unmatched files into changed, not told apart, and absent."""
    for side, done in (("l", "left_id"), ("r", "right_id")):
        connection.execute(
            f"CREATE OR REPLACE TEMP TABLE {side}_rest AS SELECT entry_id, path_key "  # noqa: S608
            f"FROM {side}_entries WHERE entry_type = 'file' AND NOT is_deleted "
            "AND transformation IS NULL AND ordinal IS NULL "
            f"AND entry_id NOT IN (SELECT {done} FROM matched)"
        )
    for side in ("l", "r"):
        connection.execute(
            f"CREATE OR REPLACE TEMP TABLE {side}_changed AS "  # noqa: S608
            f"SELECT DISTINCT {side}.entry_id FROM l_rest l JOIN r_rest r USING (path_key)"
        )
        changed = connection.execute(f"SELECT count(*) FROM {side}_changed").fetchone()  # noqa: S608
        untold = connection.execute(
            f"SELECT count(DISTINCT entry_id) FROM unsettled WHERE side = '{side}' "  # noqa: S608
            f"AND entry_id IN (SELECT entry_id FROM {side}_rest) "
            f"AND entry_id NOT IN (SELECT entry_id FROM {side}_changed)"
        ).fetchone()
        summary.changed[side] = int(changed[0]) if changed else 0
        summary.not_told_apart[side] = int(untold[0]) if untold else 0
        summary.absent[side] = (
            summary.unmatched[side] - summary.changed[side] - summary.not_told_apart[side]
        )
