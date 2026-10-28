"""Comparing where one side states no sizes.

The five main tiers of the comparison key on size, and ``named`` and ``sized`` leave a zero size
out. So an inventory that states no sizes (a folder listing from another tool, with every size
absent or zero) would match nothing against a census of the same tree. Two size-blind tiers
follow them, only where a side states no sizes. The test censuses one tree twice, with sizes
zeroed or absent on one side. Place and name still match, and the notice says that they matched
without sizes.

Two layers, as ``test_compare.py`` has them: invented rows for the counts, then the engine's own
census over an invented tree, one run's sizes rewritten, compared through the seam a page calls.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import duckdb

from ingence.compare.carry import decision_basis
from ingence.compare.fingerprints import SIZE_BLIND_TIERS, TIERS, Tier
from ingence.compare.match import Side, match_files
from ingence.compare.moves import MOVE_TIERS, carry_policy, move_map
from ingence.compare.report import compare_organisation
from ingence.compare.structure import iter_matched_pairs
from ingence.core.project import Project
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

_SECOND = 1_000_000_000

type Row = tuple[str, str, int | None, int | None]


def _views(left: list[Row], right: list[Row]) -> duckdb.DuckDBPyConnection:
    """``l_entries`` and ``r_entries`` as :func:`match.attach` leaves them, from invented rows of
    (entry identifier, path, size, modification time)."""
    connection = duckdb.connect()
    for side, rows in (("l", left), ("r", right)):
        connection.execute(
            f"CREATE TABLE {side}_entries (entry_id VARCHAR, relative_path VARCHAR, "
            "path_key VARCHAR, name VARCHAR, name_key VARCHAR, entry_type VARCHAR, "
            "is_deleted BOOLEAN, size_bytes BIGINT, modified_ns BIGINT, transformation VARCHAR, "
            "ordinal INTEGER, moved_by VARCHAR, first_seen_run VARCHAR)"
        )
        connection.execute(
            f"CREATE TABLE {side}_observations "
            "(entry_id VARCHAR, key VARCHAR, value_text VARCHAR, run_id VARCHAR)"
        )
        for entry_id, path, size, modified in rows:
            connection.execute(
                f"INSERT INTO {side}_entries VALUES (?, ?, ?, ?, ?, 'file', false, ?, ?, NULL, "
                "NULL, NULL, NULL)",
                [entry_id, path, path, *[path.rsplit("/", 1)[-1]] * 2, size, modified],
            )
    return connection


def _tree_rows(prefix: str = "", *, sized: bool = True, timed: bool = True) -> list[Row]:
    rows: list[Row] = []
    for folder in ("kelt", "vorn"):
        for n in range(3):
            path = f"{prefix}{folder}/run_{n}/{folder}_zib_{n}.tif"
            rows.append(
                (
                    f"{prefix}{path}",
                    path,
                    (100 + n) if sized else None,
                    (50 + n) * _SECOND if timed else None,
                )
            )
    return rows


# -- the tiers, on invented rows ---------------------------------------------------------------


def test_the_size_blind_tiers_come_after_every_size_bearing_tier() -> None:
    assert frozenset({Tier.PLACE_NO_SIZE, Tier.PARENT_NO_SIZE}) == SIZE_BLIND_TIERS
    last = max(TIERS.index(tier) for tier in TIERS if tier not in SIZE_BLIND_TIERS)
    assert all(TIERS.index(tier) > last for tier in SIZE_BLIND_TIERS)
    assert TIERS.index(Tier.PLACE_NO_SIZE) < TIERS.index(Tier.PARENT_NO_SIZE)


def test_one_tree_with_every_size_zeroed_on_one_side_still_matches_by_place() -> None:
    left = _tree_rows()
    right = [(f"r{entry}", path, 0, modified) for entry, path, _, modified in left]
    summary = match_files(_views(left, right))
    assert summary.states_no_sizes == {"l": False, "r": True}
    assert summary.matched[Tier.PLACE_NO_SIZE.value] == len(left)
    assert summary.matched_without_size == len(left)
    assert summary.matched[Tier.PLACE.value] == 0
    assert summary.unmatched == {"l": 0, "r": 0}


def test_a_tier_a_side_takes_no_part_in_counts_no_key_as_shared() -> None:
    """Where one side states no sizes, a size-bearing tier cannot match, and a key several files on
    the sized side share there was counted as "ambiguous" at a tier that was never really tried."""
    left: list[Row] = [
        ("a", "kelt/zib.tif", 10, 5 * _SECOND),
        ("b", "vorn/zib.tif", 10, 5 * _SECOND),
    ]
    right: list[Row] = [
        ("p", "kelt/zib.tif", 0, 5 * _SECOND),
        ("q", "vorn/zib.tif", 0, 5 * _SECOND),
    ]
    summary = match_files(_views(left, right))
    assert summary.matched_without_size == 2
    for tier in TIERS:
        if tier not in SIZE_BLIND_TIERS:
            assert summary.ambiguous[tier.value] == {"l": 0, "r": 0}


def test_one_tree_with_sizes_and_times_absent_on_one_side_still_matches_by_place() -> None:
    left = _tree_rows()
    right = _tree_rows(sized=False, timed=False)
    right = [(f"r{entry}", path, size, modified) for entry, path, size, modified in right]
    summary = match_files(_views(left, right))
    assert summary.states_no_sizes == {"l": False, "r": True}
    assert summary.matched_without_size == len(left)


def test_a_tree_moved_under_a_new_root_matches_by_its_folder_and_name() -> None:
    left = _tree_rows(sized=False)
    right = _tree_rows("trell archive/")
    summary = match_files(_views(left, right))
    assert summary.states_no_sizes == {"l": True, "r": False}
    assert summary.matched[Tier.PLACE_NO_SIZE.value] == 0
    assert summary.matched[Tier.PARENT_NO_SIZE.value] == len(left)


def test_a_key_two_files_share_on_either_side_matches_nothing_without_size() -> None:
    """Two folders named alike each hold a file named alike: which is which cannot be said."""
    left: list[Row] = [
        ("a", "kelt/run/zib.tif", None, None),
        ("b", "vorn/run/zib.tif", None, None),
        ("c", "vorn/brisa/zib.tif", None, None),
    ]
    right: list[Row] = [
        ("p", "new/kelt/run/zib.tif", 10, 5 * _SECOND),
        ("q", "new/vorn/run/zib.tif", 10, 5 * _SECOND),
        ("s", "new/vorn/brisa/zib.tif", 11, 5 * _SECOND),
    ]
    summary = match_files(_views(left, right))
    assert summary.matched[Tier.PARENT_NO_SIZE.value] == 1
    assert summary.ambiguous[Tier.PARENT_NO_SIZE.value] == {"l": 2, "r": 2}
    assert summary.not_told_apart == {"l": 2, "r": 2}


def test_sizes_on_both_sides_never_try_a_size_blind_tier() -> None:
    """A file edited where it sits stays changed: a place alone matches only where a side has no
    sizes to tell an edit by."""
    left: list[Row] = [("a", "kelt/zib.tif", 10, 5 * _SECOND)]
    right: list[Row] = [("b", "kelt/zib.tif", 12, 9 * _SECOND)]
    summary = match_files(_views(left, right))
    assert summary.states_no_sizes == {"l": False, "r": False}
    assert not SIZE_BLIND_TIERS & set(summary.matched)
    assert summary.matched_without_size == 0
    assert summary.changed == {"l": 1, "r": 1}


def test_a_few_files_without_sizes_among_sized_ones_are_never_matched_without_size() -> None:
    """ "No sizes" is decided per side, never file by file: one file whose size is absent, and an
    empty file moved, on a side where the other files have sizes, match by nothing weaker."""
    left: list[Row] = [
        ("a", "kelt/zib.tif", None, 5 * _SECOND),
        ("b", "kelt/sub/empty.txt", 0, 6 * _SECOND),
        ("c", "vorn/brisa.tif", 40, 7 * _SECOND),
    ]
    right: list[Row] = [
        ("p", "kelt/zib.tif", 10, 5 * _SECOND),
        ("q", "moved/sub/empty.txt", 0, 6 * _SECOND),
        ("s", "vorn/brisa.tif", 40, 7 * _SECOND),
    ]
    summary = match_files(_views(left, right))
    assert summary.states_no_sizes == {"l": False, "r": False}
    assert summary.matched_total == 1
    assert summary.matched_without_size == 0


def test_a_side_of_no_files_is_not_said_to_state_no_sizes() -> None:
    summary = match_files(_views([], _tree_rows()))
    assert summary.states_no_sizes == {"l": False, "r": False}


def test_nothing_is_carried_across_a_size_blind_match() -> None:
    """A person's decisions and a dive's readings follow only a match that proves the bytes, or
    name, size and time together; a place or a folder and name alone proves neither."""
    for tier in SIZE_BLIND_TIERS:
        assert carry_policy(tier) == (None, False)
        assert tier not in MOVE_TIERS
    left = _tree_rows(sized=False)
    right = _tree_rows("trell archive/")
    connection = _views(left, right)
    assert move_map(connection) == []
    match_files(connection)
    basis = decision_basis(connection, iter_matched_pairs(connection), earlier_run=None)
    assert basis.moved == frozenset()
    assert basis.left_keys == basis.right_keys == ()


# -- the engine's own census, one run's sizes rewritten ---------------------------------------

_FIRST = "run_20260101T000001Z_111111111111"
_SECOND_RUN = "run_20260101T000002Z_222222222222"


def _censused_twice(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    when = 1_700_000_000
    for folder in ("kelt", "vorn", "brisa"):
        (pool / folder).mkdir(parents=True)
        for n in range(4):
            path = pool / folder / f"{folder}_trell_{n}.csv"
            path.write_text("a,b\n" + "1,2\n" * (n + 3))
            os.utime(path, (when, when))
            when += 100
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=_FIRST, started_at="t")
    run_census(project, run_id=_SECOND_RUN, started_at="t")
    return project


def _sizes_rewritten(project: Project, run_id: str, size: str) -> None:
    """Every entry's size in one stored run set to ``size`` (SQL: ``0`` or ``NULL``)."""
    folder = project.path / "runs" / run_id / "entries"
    for part in sorted(folder.glob("*.parquet")):
        rewritten = part.with_suffix(".rewritten")
        connection = duckdb.connect()
        try:
            connection.execute(
                f"COPY (SELECT * REPLACE (CAST({size} AS BIGINT) AS size_bytes) "
                f"FROM read_parquet('{part.as_posix()}')) TO '{rewritten.as_posix()}' "
                "(FORMAT parquet)"
            )
        finally:
            connection.close()
        shutil.move(rewritten, part)


def test_the_runners_recipe_one_tree_censused_twice_sizes_zeroed_on_one_side(
    tmp_path: Path,
) -> None:
    project = _censused_twice(tmp_path)
    _sizes_rewritten(project, _SECOND_RUN, "0")
    report = compare_organisation(Side(project.path, _FIRST), Side(project.path, _SECOND_RUN))
    assert report["states_no_sizes"] == {"l": False, "r": True}
    assert report["matched_without_size"] == report["files"]["l"] == 12
    assert report["matched_by_tier"]["place_no_size"] == 12
    assert report["verdict"] == "same_organisation"
    assert report["size_blind_tiers"] == ["place_no_size", "parent_no_size"]
    assert any("without their sizes" in line for line in report["verdict_because"])
    assert report["notice"] == (
        "Nothing was reorganised: the files are where they were. The later reading states no "
        "file sizes, so 12 files were matched by their place or their folder and name alone: "
        "likely the same files, though nothing here shows they hold the same bytes."
    )


def test_sizes_absent_on_the_earlier_reading_are_said_of_that_reading(tmp_path: Path) -> None:
    project = _censused_twice(tmp_path)
    _sizes_rewritten(project, _FIRST, "NULL")
    report = compare_organisation(Side(project.path, _FIRST), Side(project.path, _SECOND_RUN))
    assert report["states_no_sizes"] == {"l": True, "r": False}
    assert report["matched_without_size"] == 12
    assert (
        "The earlier reading states no file sizes, so 12 files were matched" in (report["notice"])
    )


def test_a_comparison_with_sizes_on_both_sides_says_nothing_of_sizes(tmp_path: Path) -> None:
    project = _censused_twice(tmp_path)
    report = compare_organisation(Side(project.path, _FIRST), Side(project.path, _SECOND_RUN))
    assert report["states_no_sizes"] == {"l": False, "r": False}
    assert report["matched_without_size"] == 0
    assert "sizes" not in report["notice"]
    assert report["matched_by_tier"]["place"] == 12


def test_the_history_route_says_how_many_matched_without_sizes(tmp_path: Path) -> None:
    from ingence.api.routes.reorganisation import reorganisation

    project = _censused_twice(tmp_path)
    _sizes_rewritten(project, _SECOND_RUN, "0")
    answer = reorganisation(project.path, before=_FIRST, after=_SECOND_RUN)
    assert answer["matched_without_size"] == 12
    assert answer["states_no_sizes"] == {"before": False, "after": True}
    assert "matched by their place or their folder and name alone" in answer["notice"]
