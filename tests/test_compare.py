"""The reorganisation comparison: files matched by what they are, the organisation read from the
matches, a decision followed to where its files went, and the notice that says so in plain words.

Two layers. The first runs on invented paths (the counts and verdicts, cheap and exact); the second
runs the engine's own census over invented trees on disk, moves things the way a person would, and
compares the two censuses it wrote, because a comparison that only works on hand-built rows is not
yet one that works on what the engine stores.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest

from ingence.compare.fingerprints import Tier
from ingence.compare.match import MatchSummary, Side, attach, match_files
from ingence.compare.moves import Standing, carry_policy, move_map
from ingence.compare.structure import MovedPair, compare_structure, pairs_together, rewrite_of
from ingence.compare.verdict import (
    HISTORY,
    Context,
    Sides,
    Verdict,
    notice,
    read_context,
    read_verdict,
    where_it_went,
)

if TYPE_CHECKING:
    from ingence.compare.structure import Structure

_OLD = [
    f"{p}/{e}/f{i}.tif" for p in ("projA", "projB") for e in ("e1", "e2", "e3") for i in range(10)
]


def _summary(count: int) -> MatchSummary:
    summary = MatchSummary()
    summary.files = {"l": count, "r": count}
    summary.matched = {"bytes": count}
    summary.unmatched = {"l": 0, "r": 0}
    return summary


def _structure(moves: list[tuple[str, str]]) -> Structure:
    return compare_structure([MovedPair(a, b, "bytes") for a, b in moves])


def _verdict(moves: list[tuple[str, str]]) -> Verdict:
    return read_verdict(_summary(len(moves)), _structure(moves))[0]


# -- the counts and the verdicts, on invented paths ------------------------------------------


@pytest.mark.parametrize(
    ("moved", "expected"),
    [
        (lambda p: p, Verdict.SAME_ORGANISATION),
        (lambda p: "Archive/2026/" + p, Verdict.RELOCATED_WHOLE),
        (
            lambda p: (
                p.replace("/e1/", "/2026-01 kelt/")
                .replace("/e2/", "/2026-02 kelt/")
                .replace("/e3/", "/2026-03 kelt/")
            ),
            Verdict.FOLDERS_RENAMED,
        ),
        (lambda p: "tifs/" + p.replace("/", "_"), Verdict.REORGANISED),
    ],
)
def test_each_kind_of_move_reads_as_itself(moved, expected) -> None:  # type: ignore[no-untyped-def]
    assert _verdict([(p, moved(p)) for p in _OLD]) is expected


def test_splitting_some_folders_is_a_partial_reorganisation() -> None:
    def split(path: str) -> str:
        if "/e1/" not in path:
            return path
        return path.replace("/e1/", "/e1a/" if int(path[-5]) < 5 else "/e1b/")

    assert _verdict([(p, split(p)) for p in _OLD]) is Verdict.PARTLY_REORGANISED


def test_pooling_many_folders_into_one_is_read_as_a_reorganisation() -> None:
    """Every old pair stays together and many new pairs appear: the direction the prototype's first
    version missed, calling it partly reorganised."""
    moves = [(p, "pool/" + p.rsplit("/", 1)[1]) for p in _OLD]
    structure = _structure(moves)
    parent = structure.levels[1]
    assert parent["pairs_still_together"] == parent["pairs_together_before"]
    assert parent["pairs_together_now"] > parent["pairs_still_together"]
    assert read_verdict(_summary(len(moves)), structure)[0] is Verdict.REORGANISED


def test_files_that_mostly_did_not_match_are_different_files() -> None:
    summary = _summary(60)
    summary.matched = {"named": 10}
    assert read_verdict(summary, _structure([(p, p) for p in _OLD[:10]]))[0] is (
        Verdict.DIFFERENT_FILES
    )


def test_the_pair_counts_need_no_pair_enumerated() -> None:
    counts = pairs_together([("a", "x"), ("a", "x"), ("a", "y"), ("b", "y")])
    assert counts["pairs_together_before"] == 3
    assert counts["pairs_still_together"] == 1
    assert counts["pairs_together_now"] == 2


def test_how_many_rewrites_a_structure_keeps_is_a_parameter() -> None:
    pairs = [MovedPair(p, f"moved {n}/" + p, "bytes") for n, p in enumerate(_OLD)]
    assert len(compare_structure(pairs).rewrites) == 20
    assert len(compare_structure(pairs, rewrites_kept=3).rewrites) == 3


def test_a_rewrite_is_what_is_left_once_the_common_ending_goes() -> None:
    assert rewrite_of("2025/A/x.tif", "Archive/2025/A/x.tif") == ("", "Archive")
    assert rewrite_of("P/e1/x.tif", "P/2026 e1/x.tif") == ("P/e1", "P/2026 e1")


def test_case_only_differences_are_one_folder_when_a_root_folds_case() -> None:
    """Equality is by the path as compared; the sentence names the folder as read."""
    pairs = [
        MovedPair("Kelt/a.tif", "kelt/a.tif", "place", "kelt/a.tif", "kelt/a.tif"),
        MovedPair("Kelt/b.tif", "kelt/b.tif", "place", "kelt/b.tif", "kelt/b.tif"),
    ]
    structure = compare_structure(pairs)
    assert structure.paths_kept == 2
    assert structure.folders[0].old_folder == "Kelt"
    assert structure.folders[0].new_folder == "kelt"


# -- the context, from births ----------------------------------------------------------------


def test_files_born_after_they_were_last_changed_read_as_a_copy_and_which_side() -> None:
    structure = _structure([(p, p) for p in _OLD[:5]])
    roots = {"same_root_id": True, "same_mount_path": True, "same_place": False}
    later = {"both": 10, "same": 0, "later": 10, "earlier": 0, "copied": 10, "copied_left": 0}
    assert read_context(roots, later, structure)[0::2] == (Context.COPIED, "right")
    earlier = {"both": 10, "same": 0, "later": 0, "earlier": 10, "copied": 0, "copied_left": 10}
    assert read_context(roots, earlier, structure)[0::2] == (Context.COPIED, "left")


def test_the_same_births_under_another_mount_read_as_another_computer() -> None:
    structure = _structure([(p, p) for p in _OLD[:5]])
    roots = {"same_root_id": False, "same_mount_path": False, "same_place": True}
    same = {"both": 10, "same": 10, "later": 0, "earlier": 0, "copied": 0, "copied_left": 0}
    assert read_context(roots, same, structure)[0] is Context.OTHER_MACHINE


def test_two_folders_of_one_census_are_never_the_same_tree() -> None:
    structure = _structure([(p, p) for p in _OLD[:5]])
    roots = {"same_root_id": True, "same_mount_path": True, "same_place": False}
    assert read_context(roots, {}, structure)[0] is Context.UNCLEAR


# -- the notice: plain words, and honest about what the engine does --------------------------


def test_the_notice_is_plain_and_asks_nothing() -> None:
    structure = _structure([(p, p.replace("/e1/", "/2026-01 kelt/")) for p in _OLD])
    text = notice(
        Verdict.FOLDERS_RENAMED, Context.SAME_TREE, structure=structure, followed=3, scattered=1
    )
    assert text.startswith("Some folders were renamed or moved; each kept all of its files.")
    assert "3 of your notes and answers were written about folders" in text
    assert (
        "1 of your notes and answers was about a folder whose files went to several places or "
        "now sit among other files; nothing waits on that." in text
    )
    for jargon in ("tier", "rewrite", "pair", "orphan", "carried", " side"):
        assert jargon not in text


def test_the_notice_never_says_the_engine_applied_a_decision() -> None:
    """The notice only says where the files went. It never says the engine applied a decision."""
    structure = _structure([(p, "Archive/" + p) for p in _OLD])
    text = notice(Verdict.RELOCATED_WHOLE, Context.SAME_TREE, structure=structure, followed=2)
    assert text.startswith(
        "The whole tree moved as one piece: everything now sits inside 'Archive', and every "
        "folder kept its files."
    )
    assert "now read" not in text and "applied" not in text and "carried" not in text
    assert "is now 'Archive'" not in text  # read as the top folder renamed


def test_a_copy_of_the_same_layout_is_said_as_a_copy_by_its_name() -> None:
    structure = _structure([(p, p) for p in _OLD])
    text = notice(
        Verdict.SAME_ORGANISATION,
        Context.COPIED,
        structure=structure,
        sides=Sides("'kelt'", "'kelt backup'"),
        copy_side="right",
    )
    assert text.startswith("'kelt' and 'kelt backup' are laid out the same way.")
    assert "'kelt backup' looks like a copy: its files were created after" in text
    unnamed = notice(
        Verdict.SAME_ORGANISATION, Context.COPIED, structure=structure, copy_side="left"
    )
    assert "The first reading looks like a copy" in unnamed and " side" not in unnamed


def test_where_a_tree_moved_whole_now_sits_is_said_as_a_place() -> None:
    """A tree that moved whole is said as a place, not as the top folder renamed."""

    def said(moved) -> str:  # type: ignore[no-untyped-def]
        return where_it_went(_structure([(p, moved(p)) for p in _OLD]))

    assert said(lambda p: "Archive/" + p) == "everything now sits inside 'Archive'"
    assert said(lambda p: p.replace("projA/", "done/projA/").replace("projB/", "done/projB/")) == (
        "everything now sits inside 'done'"
    )
    unnested = [("old/" + p, p) for p in _OLD]
    assert where_it_went(_structure(unnested)) == "what was inside 'old' now sits at the top"
    renamed = [(p, p.replace("projA/", "vorn/")) for p in _OLD if p.startswith("projA/")]
    assert where_it_went(_structure(renamed)) == "everything in 'projA' is now in 'vorn'"


# -- the move map ----------------------------------------------------------------------------

_SECOND = 1_000_000_000


def _views(
    left: list[tuple[str, str, int, int]], right: list[tuple[str, str, int, int]]
) -> duckdb.DuckDBPyConnection:
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


def test_a_moved_file_is_matched_to_the_path_it_left_and_nothing_else() -> None:
    left = [
        ("a", "e1/kelt_1.tif", 10, 100 * _SECOND),
        ("b", "e1/kelt_2.tif", 11, 101 * _SECOND),
        ("c", "e1/same.tif", 12, 102 * _SECOND),
    ]
    right = [
        ("x", "2026-01 kelt/kelt_1.tif", 10, 100 * _SECOND),
        # a nanosecond late: the two-second grain still matches it
        ("y", "2026-01 kelt/kelt_2.tif", 11, 101 * _SECOND + 1),
        ("c", "e1/same.tif", 12, 102 * _SECOND),
    ]
    moves = move_map(_views(left, right))
    assert {(m.old_entry_id, m.new_entry_id, m.tier) for m in moves} == {
        ("a", "x", Tier.NAMED),
        ("b", "y", Tier.NAMED),
    }


def test_two_files_sharing_a_key_match_nothing() -> None:
    left = [("a", "e1/x.tif", 10, 5), ("b", "e2/x.tif", 10, 5)]
    right = [("p", "n1/x.tif", 10, 5), ("q", "n2/x.tif", 10, 5)]
    assert move_map(_views(left, right)) == []


def test_a_file_at_its_own_place_matches_even_where_its_name_is_shared() -> None:
    """Forty copies of one template in forty folders are ambiguous by name; each is still exactly
    where it was, which is a match no tiebreak is needed for."""
    rows = [(f"t{n}", f"f{n}/template.docx", 30, 7 * _SECOND) for n in range(5)]
    connection = _views(
        rows, [(f"u{n}", path, size, m) for n, (_, path, size, m) in enumerate(rows)]
    )
    summary = match_files(connection)
    assert summary.matched == {"place": 5, "bytes": 0, "head": 0, "named": 0, "sized": 0}
    assert summary.unmatched == {"l": 0, "r": 0}


def test_empty_files_are_never_matched_by_anything_but_their_place() -> None:
    left = [("a", "one/empty.txt", 0, 5 * _SECOND)]
    right = [("b", "two/empty.txt", 0, 5 * _SECOND)]
    assert match_files(_views(left, right)).matched_total == 0


def test_readings_follow_a_digest_as_proof_and_a_name_as_a_candidate() -> None:
    assert carry_policy(Tier.BYTES) == (Standing.CONFIRMED, True)
    assert carry_policy(Tier.NAMED) == (Standing.CANDIDATE, True)
    assert carry_policy(Tier.SIZED) == (None, False)


def test_attach_refuses_a_run_that_holds_no_entries(tmp_path: Path) -> None:
    (tmp_path / "runs" / "run_x" / "entries").mkdir(parents=True)
    with pytest.raises(ValueError, match="no entries"):
        attach(duckdb.connect(), "l", Side(tmp_path, "run_x"))


def test_a_side_with_no_files_is_not_the_same_as_anything() -> None:
    """A folder holding only shortcuts read as 'nothing was reorganised' (nothing over nothing is
    all of it); an empty side has nothing to be the same as."""
    summary = MatchSummary()
    summary.files = {"l": 40, "r": 0}
    verdict, because = read_verdict(summary, compare_structure([]))
    assert verdict is Verdict.DIFFERENT_FILES
    assert "one side holds no files" in because
    said = notice(
        verdict,
        Context.UNCLEAR,
        structure=compare_structure([]),
        summary=summary,
        sides=Sides("'kelt'", "'kelt backup'"),
    )
    assert said == "'kelt backup' holds no files, so there is nothing to match."


def test_different_files_are_said_with_their_counts_and_none_as_none() -> None:
    """ "fewer than half" was a threshold written into the words; the counts are said instead,
    and a count of nothing reads as none, never as "only 0 of the ... files"."""
    sides = Sides("'kelt'", "'vorn'")
    some = _counts({"l": 60, "r": 40}, 12)
    said = notice(
        Verdict.DIFFERENT_FILES,
        Context.UNCLEAR,
        structure=compare_structure([]),
        summary=some,
        sides=sides,
    )
    assert said == (
        "These look like different files: only 12 of the 40 files in 'vorn' could be matched to "
        "one in 'kelt'."
    )
    none = notice(
        Verdict.DIFFERENT_FILES,
        Context.UNCLEAR,
        structure=compare_structure([]),
        summary=_counts({"l": 30, "r": 4}, 0),
        sides=sides,
    )
    assert none == (
        "These look like different files: none of the 4 files in 'vorn' could be matched to one "
        "in 'kelt'."
    )
    one = notice(
        Verdict.DIFFERENT_FILES,
        Context.UNCLEAR,
        structure=compare_structure([]),
        summary=_counts({"l": 1, "r": 9}, 0),
        sides=sides,
    )
    assert one == "These look like different files: the one file in 'kelt' has no match in 'vorn'."


def _counts(files: dict[str, int], matched: int, **apart: dict[str, int]) -> MatchSummary:
    summary = MatchSummary()
    summary.files = files
    summary.matched = {"place": matched}
    summary.unmatched = {side: count - matched for side, count in files.items()}
    summary.changed = apart.get("changed", {"l": 0, "r": 0})
    summary.not_told_apart = apart.get("untold", {"l": 0, "r": 0})
    summary.absent = {
        side: summary.unmatched[side] - summary.changed[side] - summary.not_told_apart[side]
        for side in files
    }
    return summary


def test_a_copy_of_only_part_of_a_folder_says_how_much_of_it_by_name() -> None:
    moves = [(p, p) for p in _OLD[:20]]
    structure = _structure(moves)
    said = notice(
        Verdict.SAME_ORGANISATION,
        Context.COPIED,
        structure=structure,
        summary=_counts({"l": 60, "r": 20}, 20),
        sides=Sides("'kelt'", "'kelt backup'"),
        copy_side="right",
    )
    assert "Only 20 of the 60 files in 'kelt' are also in 'kelt backup'." in said
    whole = notice(
        Verdict.SAME_ORGANISATION,
        Context.SAME_TREE,
        structure=structure,
        summary=_counts({"l": 20, "r": 20}, 20),
    )
    assert "Only" not in whole


def test_identical_copies_not_told_apart_are_never_said_as_missing() -> None:
    """Three copies of one template, moved with their folders, are on both sides but match
    nothing: they were said as missing ("Only 12 of the 15 files on the first side...")."""
    left = [(f"t{n}", f"e{n}/template.txt", 30, 7 * _SECOND) for n in range(3)]
    left += [(f"k{n}", f"e{n}/kelt_{n}.csv", 40 + n, (8 + n) * _SECOND) for n in range(3)]
    right = [(f"u{n}", f"n{n}/template.txt", 30, 7 * _SECOND) for n in range(3)]
    right += [(f"v{n}", f"n{n}/kelt_{n}.csv", 40 + n, (8 + n) * _SECOND) for n in range(3)]
    summary = match_files(_views(left, right))
    assert summary.matched_total == 3
    assert summary.not_told_apart == {"l": 3, "r": 3}
    assert summary.absent == {"l": 0, "r": 0}
    moves = [(f"e{n}/kelt_{n}.csv", f"n{n}/kelt_{n}.csv") for n in range(3)]
    said = notice(
        Verdict.FOLDERS_RENAMED,
        Context.SAME_TREE,
        structure=_structure(moves),
        summary=summary,
        sides=Sides("'kelt'", "'kelt backup'"),
    )
    assert "Only" not in said


def test_a_file_changed_where_it_sits_is_neither_new_nor_gone() -> None:
    left = [("a", "e1/kelt.csv", 10, 5 * _SECOND), ("b", "e1/vorn.csv", 11, 6 * _SECOND)]
    right = [("c", "e1/kelt.csv", 12, 9 * _SECOND), ("d", "e1/zib.csv", 13, 7 * _SECOND)]
    summary = match_files(_views(left, right))
    assert summary.changed == {"l": 1, "r": 1}
    assert summary.absent == {"l": 1, "r": 1}


def test_the_history_page_says_what_is_new_changed_and_gone() -> None:
    """New files read as a shortfall ("Only 100 of the 130 files on the second side are on the
    other"), and "side" was never said."""
    structure = _structure([(p, p.replace("/e1/", "/e1 done/")) for p in _OLD])
    summary = _counts({"l": 64, "r": 72}, 60, changed={"l": 2, "r": 2}, untold={"l": 1, "r": 1})
    said = notice(
        Verdict.FOLDERS_RENAMED,
        Context.SAME_TREE,
        structure=structure,
        summary=summary,
        sides=HISTORY,
    )
    assert said.endswith(
        "Since the earlier reading, 9 files are new, 2 were changed and 1 is gone."
    )
    assert "Only" not in said and " side" not in said
    one = notice(
        Verdict.FOLDERS_RENAMED,
        Context.SAME_TREE,
        structure=structure,
        summary=_counts({"l": 60, "r": 61}, 60),
        sides=HISTORY,
    )
    assert one.endswith("Since the earlier reading, 1 file is new.")
    none = notice(
        Verdict.FOLDERS_RENAMED,
        Context.SAME_TREE,
        structure=structure,
        summary=_counts({"l": 60, "r": 60}, 60),
        sides=HISTORY,
    )
    assert "Since" not in none


def test_files_moved_within_one_drive_are_the_same_tree_not_another_computer() -> None:
    """A move within a volume keeps every birth, which looks like 'the same files seen from
    another machine' unless the root is asked: one root at one place is one drive."""
    structure = _structure([(p, "Archive/" + p) for p in _OLD])
    roots = {"same_root_id": True, "same_mount_path": True, "same_place": True}
    same = {"both": 60, "same": 60, "later": 0, "earlier": 0, "copied": 0, "copied_left": 0}
    assert read_context(roots, same, structure)[0] is Context.SAME_TREE
    other = {"same_root_id": False, "same_mount_path": False, "same_place": True}
    assert read_context(other, same, structure)[0] is Context.OTHER_MACHINE


def test_three_files_leaving_one_folder_of_twelve_is_not_the_whole_tree_moving() -> None:
    """The pair share alone (95 percent still together) read this as relocated whole."""
    moves = [
        (
            p,
            p.replace("/e1/", "/e1_b/")
            if "/e1/" in p and p.endswith(("0.tif", "1.tif", "2.tif")) and "projA" in p
            else p,
        )
        for p in _OLD
    ]
    assert _verdict(moves) is Verdict.PARTLY_REORGANISED
    said = notice(Verdict.PARTLY_REORGANISED, Context.SAME_TREE, structure=_structure(moves))
    assert said.startswith(
        "We noticed some reorganising: files were split up or joined together in 1 folder "
        "('projA/e1'); the rest kept their files."
    )


def test_one_folder_renamed_among_many_is_that_folder_renamed() -> None:
    moves = [(p, p.replace("projA/e1/", "projA/e1_done/")) for p in _OLD]
    assert _verdict(moves) is Verdict.FOLDERS_RENAMED


def test_one_folder_moved_among_hundreds_of_files_is_never_the_same_organisation() -> None:
    """One folder of ten renamed among six hundred files kept 98 percent of paths, which read
    "Nothing was reorganised" while a note about that folder had lost its place."""
    old = [f"g{g:02d}/zib_{g}_{i}.tif" for g in range(60) for i in range(10)]
    moves = [(p, p.replace("g07/", "g07 done/")) for p in old]
    verdict, because = read_verdict(_summary(len(moves)), _structure(moves))
    assert verdict is Verdict.FOLDERS_RENAMED
    assert "590 of 600 matched files kept their path" in because


def test_files_renamed_inside_folders_that_stayed_are_not_folders_renamed() -> None:
    """Every file renamed inside its own folder read "Some folders were renamed or moved"."""
    moves = [(p, p.replace("/f", "/kelt_f")) for p in _OLD]
    structure = _structure(moves)
    assert structure.folders_moved == 0
    assert structure.files_renamed_in_place == len(_OLD)
    assert read_verdict(_summary(len(moves)), structure)[0] is Verdict.FILES_RENAMED
    said = notice(Verdict.FILES_RENAMED, Context.SAME_TREE, structure=structure)
    assert said == "Some files were renamed inside their folders; no folder moved."


def test_one_large_folder_split_among_many_untouched_is_partly_reorganised_and_named() -> None:
    """A 1,000-file folder holds most of the pairs.

    Split in two among a hundred untouched ten-file folders, it halves the pairs. The notice must
    not read "most files now sit in folders with different files than before" while half the files
    have not moved at all."""
    big = [
        (f"big/vorn_{i:04d}.tif", f"big{'2' if i >= 500 else ''}/vorn_{i:04d}.tif")
        for i in range(1000)
    ]
    rest = [(p, p) for p in (f"s{g:03d}/kelt_{i}.tif" for g in range(100) for i in range(10))]
    moves = big + rest
    structure = _structure(moves)
    assert (
        structure.levels[1]["pairs_still_together"]
        < structure.levels[1]["pairs_together_before"] * 0.6
    )
    assert structure.files_regrouped == 1000
    assert read_verdict(_summary(len(moves)), structure)[0] is Verdict.PARTLY_REORGANISED
    said = notice(Verdict.PARTLY_REORGANISED, Context.SAME_TREE, structure=structure)
    assert "in 1 folder ('big')" in said
    assert "most files" not in said


def test_the_partial_notice_names_a_few_folders_and_counts_the_rest() -> None:
    moves = [(p, p.replace("/e1/", "/e1x/") if p.endswith("0.tif") else p) for p in _OLD]
    structure = _structure(moves)
    said = notice(
        Verdict.PARTLY_REORGANISED, Context.SAME_TREE, structure=structure, named_in_notice=2
    )
    assert "in 2 folders ('projA/e1', 'projB/e1')" in said
    one = notice(
        Verdict.PARTLY_REORGANISED, Context.SAME_TREE, structure=structure, named_in_notice=1
    )
    assert "in 2 folders ('projA/e1' and 1 more)" in one
