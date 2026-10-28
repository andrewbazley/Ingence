"""Characterising how a tree is organised, without naming what it is.

Every test here checks a description. None checks a verdict, because the module deliberately
produces none: naming a regime is a question for a person, and a bundle-centric reading would make a
well-organised type-silo laboratory look chaotic.
"""

from __future__ import annotations

from ingence.scan.organisation import (
    Authorship,
    Child,
    FolderInput,
    characterise_bursts,
    composition_of,
    find_bursts,
    profile_folder,
    read_authorship,
)

SECOND = 1_000_000_000
DAY = 86_400 * SECOND


def child(time_ns: int, *, shape: str = "s1", parent: str = "exp", depth: int = 2) -> Child:
    return Child(skeleton_hash=shape, time_ns=time_ns, clock="mtime", depth=depth, parent=parent)


# ---------------------------------------------------------------------------
# Pass one: finding the modes.
# ---------------------------------------------------------------------------


def test_two_bursts_at_wildly_different_scales_are_both_found():
    """The whole argument for judging a gap locally rather than globally.

    A four-second copy and a three-day analysis session are both real bursts,
    and any fixed window either shatters the second or swallows the first. This
    input contains both, and a correct detector separates them without being
    told what scale to look at.
    """
    fast = [i * SECOND // 4 for i in range(12)]
    later = fast[-1] + 30 * DAY
    slow = [later + i * DAY for i in range(6)]

    spans = find_bursts(fast + slow)

    assert len(spans) == 2
    assert spans[0] == (0, 11)
    assert spans[1] == (12, 17)


def test_evenly_spaced_times_are_one_burst():
    """No discontinuity, no cut. A detector that always finds one is not one."""
    assert find_bursts([i * SECOND for i in range(40)]) == [(0, 39)]


def test_identical_timestamps_do_not_shatter():
    """A machine writing in one nanosecond is not a sequence of discontinuities.

    Everything is infinitely larger than zero, so a neighbourhood of identical
    times would cut at every gap without the floor. That is the copy case, and
    reporting a copy as forty bursts would be exactly backwards.
    """
    times = [1_000] * 20
    assert find_bursts(times) == [(0, 19)]


def test_the_detector_is_deterministic():
    """No seed and no random state, which reproducibility requires."""
    times = [0, SECOND, 2 * SECOND, 400 * SECOND, 401 * SECOND]
    assert find_bursts(times) == find_bursts(times) == find_bursts(list(times))


def test_an_empty_scope_has_no_bursts_rather_than_one_empty_one():
    assert find_bursts([]) == []
    assert find_bursts([5]) == [(0, 0)]


# ---------------------------------------------------------------------------
# Pass two: what kind of episode it was.
# ---------------------------------------------------------------------------


def test_a_copy_shows_the_conjunction_that_identifies_it():
    """Near-zero span and dispersion, wide directory and depth spread, together.

    The conjunction is the classifier, and no term of it works alone: a fast burst is ordinary, and
    a burst spanning many directories is ordinary. A copy is the only thing that is both.
    """
    members = [
        child(1_000 + i, parent=f"branch{i % 9}", depth=1 + i % 6, shape=f"s{i % 7}")
        for i in range(40)
    ]
    (burst,) = characterise_bursts(members)

    assert burst.span_ns < SECOND
    assert burst.dispersion_ns < 10
    assert burst.directory_spread == 9
    assert burst.depth_spread == 6


def test_a_working_session_looks_nothing_like_a_copy():
    """The control. Without it the test above only says the fields are populated."""
    members = [child(i * 900 * SECOND, parent="exp", depth=2) for i in range(12)]
    (burst,) = characterise_bursts(members)

    assert burst.span_ns > SECOND
    assert burst.directory_spread == 1
    assert burst.depth_spread == 1


def test_a_burst_of_one_reports_no_density_rather_than_an_infinity():
    """Everything at once is a fact, not a rate."""
    (burst,) = characterise_bursts([child(5)])
    assert burst.count == 1
    assert burst.span_ns == 0
    assert burst.density is None


def test_children_with_no_clock_are_left_out_rather_than_placed_at_zero():
    """A file with no time is not a file from the epoch."""
    timed = [child(i * SECOND) for i in range(4)]
    untimed = [Child(skeleton_hash="s1", time_ns=None, clock="unavailable", depth=2, parent="exp")]
    bursts = characterise_bursts(timed + untimed)
    assert sum(burst.count for burst in bursts) == 4


# ---------------------------------------------------------------------------
# Composition.
# ---------------------------------------------------------------------------


def test_the_composition_digest_ignores_order_and_notices_counts():
    """Two folders holding the same mixture compare equal. Different counts do not."""
    one = [child(0, shape="a"), child(1, shape="b"), child(2, shape="b")]
    same = [child(0, shape="b"), child(1, shape="b"), child(2, shape="a")]
    more = [child(0, shape="a"), child(1, shape="a"), child(2, shape="b")]

    assert composition_of(one)[3] == composition_of(same)[3]
    assert composition_of(one)[3] != composition_of(more)[3]


def test_a_folder_whose_children_share_no_shape_carries_no_signal():
    """The null, stated. Not disorganised: signal-free.

    Diversity of one and a dominant share at the floor is what "look elsewhere"
    looks like in the data. Reading it as weakness is the error most likely to
    lose a user's trust, because it is what a well-organised type silo looks
    like to a bundle-centric eye.
    """
    scattered = [child(i, shape=f"shape{i}") for i in range(10)]
    count, diversity, dominant, _ = composition_of(scattered)

    assert count == 10
    assert diversity == 1.0
    assert dominant == 0.1


def test_a_uniform_folder_is_the_other_extreme():
    uniform = [child(i, shape="one") for i in range(10)]
    count, diversity, dominant, _ = composition_of(uniform)
    assert (count, diversity, dominant) == (1, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Name authorship.
# ---------------------------------------------------------------------------


def test_a_negative_lexicon_hit_is_evidence_a_person_typed_the_name():
    """The negative lexicon doubles as an authorship detector, at no cost.

    The lexicon records what a person thought of a file. Nothing emits ``final_final`` or
    ``use_this_one``, so a hit is by construction a human.
    """
    reading, evidence = read_authorship(
        "analysis_FINAL_final_v2", atom_count=4, negative_hits=2, sequential_index=False
    )
    assert reading is Authorship.AUTHORED
    assert evidence.negative_hits == 2


def test_a_rigid_indexed_name_reads_as_emitted():
    reading, _ = read_authorship(
        "Position007", atom_count=1, negative_hits=0, sequential_index=True
    )
    assert reading is Authorship.EMITTED


def test_mixed_separators_and_real_words_read_as_authored():
    reading, _ = read_authorship(
        "Sarah exp-3_western blot", atom_count=5, negative_hits=0, sequential_index=False
    )
    assert reading is Authorship.AUTHORED


def test_the_reading_declines_where_the_evidence_does_not_reach():
    """Two values would force a verdict. Three is the same discipline as absence."""
    reading, _ = read_authorship("src", atom_count=1, negative_hits=0, sequential_index=False)
    assert reading is Authorship.UNCLEAR


def test_the_evidence_travels_with_the_reading():
    """A verdict whose inputs are invisible is one nobody can disagree with."""
    _, evidence = read_authorship(
        "plate_A1-run 3", atom_count=4, negative_hits=0, sequential_index=True
    )
    assert evidence.separator_styles == 3
    assert evidence.sequential_index
    assert evidence.atom_count == 4


# ---------------------------------------------------------------------------
# The whole profile.
# ---------------------------------------------------------------------------


def test_a_folder_has_a_duration_where_a_file_has_an_instant():
    """The interval is the span over which the container accumulated content.

    A directory's modification time moves on every child added, removed or
    renamed, so the pair bounds the episode. A scan file's own time records only
    when a scanner wrote bytes.
    """
    profile = profile_folder(
        FolderInput(
            name="experiment_2026",
            created_ns=1_000 * SECOND,
            modified_ns=1_000 * SECOND + 30 * DAY,
            children=tuple(child(i * DAY) for i in range(30)),
        )
    )
    first, last = profile.interval_ns
    assert first is not None and last is not None
    assert last - first == 30 * DAY


def test_an_empty_folder_profiles_without_inventing_anything():
    profile = profile_folder(FolderInput(name="empty", created_ns=None, modified_ns=None))
    assert profile.child_count == 0
    assert profile.shape_count == 0
    assert profile.burst_count == 0
    assert profile.interval_ns == (None, None)
