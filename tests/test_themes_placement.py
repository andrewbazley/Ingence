"""Themes: connected themes, one final placement per seed, the chance record, labels.

A displayed theme is one connected piece of its graph. Every seed is placed by the same steps, and
the seed most like the others is shown. The cutoffs chance gave are kept beside the themes. A label
says how steady it is. Words are invented or ordinary (``fixtures``).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import SENSES, theme_documents
from ingence.core.settings import ThemeSettings
from ingence.themes import core, themes_engine
from ingence.themes.communities import adjacency
from ingence.themes.quality import theme_quality


@pytest.fixture(scope="module")
def plant():
    return theme_documents()


@pytest.fixture(scope="module")
def drawn(plant):
    return themes_engine().compute(plant.documents, senses=SENSES)


# ---------------------------------------------------------------------------
# Connected themes
# ---------------------------------------------------------------------------


def test_taking_out_a_document_that_joined_two_halves_leaves_two_themes() -> None:
    """A path of five documents is one community; the middle one is no closer than chance and
    leaves, which cuts the community in two. Each half is a theme of its own, and each is
    connected."""
    embedded = np.array(
        [[1.0, 0.0, 0.0], [0.99, 0.14, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [0.14, 0.99, 0.0]]
    )
    embedded /= np.linalg.norm(embedded, axis=1)[:, None]
    graph = adjacency(5, {(0, 1): 0.9, (1, 2): 0.1, (2, 3): 0.1, (3, 4): 0.9})
    settings = ThemeSettings(min_theme_size=2, min_theme_documents=2)
    final = core._finalise(
        embedded, graph, np.zeros(5, dtype=int), [1] * 5, settings, 0.5, measured=True
    )
    assert final.far.tolist() == [False, False, True, False, False]
    assert final.themes == 2
    assert final.theme.tolist() == [0, 0, -1, 1, 1]
    assert core._disconnected(graph, {0: 0, 1: 0, 3: 1, 4: 1}) == 0
    # Without the split the four would have been one "theme" no edge of the graph joins.
    assert core._disconnected(graph, {0: 0, 1: 0, 3: 0, 4: 0}) == 1


def test_a_broad_theme_is_split_where_no_edge_joins_its_fine_themes(monkeypatch) -> None:
    """Louvain is made to put every fine theme in one group; the aggregated graph has two
    components, so there are two broad themes."""
    from ingence.themes import communities

    monkeypatch.setattr(communities, "louvain", lambda matrix, resolution, seed: np.zeros(4, int))
    edges = {(0, 1): 1.0, (2, 3): 1.0, (4, 5): 1.0, (6, 7): 1.0, (1, 2): 0.5, (5, 6): 0.5}
    graph = adjacency(8, edges)
    fine = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3}
    broad_of, _ = core._broad(graph, fine, 4, (0.5,), [1] * 8)
    assert len(set(broad_of.values())) == 2
    assert broad_of[0] == broad_of[1] != broad_of[2] == broad_of[3]


def test_every_displayed_theme_is_connected_in_its_graph(drawn) -> None:
    assert drawn.disconnected_themes == 0 and drawn.disconnected_broad == 0
    assert drawn.themes


# ---------------------------------------------------------------------------
# One final placement for every seed
# ---------------------------------------------------------------------------


def test_every_seed_pair_is_compared_and_the_medoid_is_shown(drawn) -> None:
    seeds = ThemeSettings().seeds
    assert len(drawn.seeds) == seeds == 8
    assert len(drawn.seed_pairs) == seeds * (seeds - 1) // 2 == 28
    assert all(-1.0 <= pair.ari <= 1.0 for pair in drawn.seed_pairs)
    means = {s.seed: s.mean_ari for s in drawn.seeds}
    assert drawn.medoid_seed == max(means, key=lambda s: (means[s], -s))
    assert drawn.placement_seed == drawn.medoid_seed
    for seed in range(seeds):
        mine = [p.ari for p in drawn.seed_pairs if seed in (p.seed_a, p.seed_b)]
        assert means[seed] == pytest.approx(np.mean(mine), abs=1e-6)
    assert drawn.seed_ari == pytest.approx(np.mean([p.ari for p in drawn.seed_pairs]), abs=1e-6)


def test_seed_zero_stays_available_through_a_setting(plant, drawn) -> None:
    first = themes_engine().compute(
        plant.documents, senses=SENSES, settings=ThemeSettings(final_placement="first")
    )
    assert first.placement_seed == 0
    assert first.medoid_seed == drawn.medoid_seed
    assert first.seed_pairs == drawn.seed_pairs


def test_two_computations_are_identical(plant, drawn) -> None:
    again = themes_engine().compute(plant.documents, senses=SENSES)
    assert again.to_json() == drawn.to_json()


def test_final_placement_gives_every_seed_the_same_steps(drawn) -> None:
    """Each seed's placed experiments are counted by the same steps; none is more than all."""
    assert all(0 <= s.placed_units <= drawn.units for s in drawn.seeds)
    assert all(s.themes >= 1 for s in drawn.seeds)


# ---------------------------------------------------------------------------
# The chance record
# ---------------------------------------------------------------------------


def test_the_cutoffs_the_null_seed_and_the_neighbour_counts_are_kept(drawn) -> None:
    chance = drawn.chance
    assert chance is not None and chance.measured
    assert (chance.null_seed, chance.replicates) == (0, 1)
    assert chance.quantile_placement == 0.75 and chance.quantile_between == 0.75
    assert chance.quantile_within is None and chance.cutoff_within is None
    assert -1.0 <= chance.cutoff_placement <= 1.0 and -1.0 <= chance.cutoff_between <= 1.0
    assert chance.shuffled_documents == drawn.vertices
    # The default null is the ten nearest, for edges and placement alike.
    assert chance.edge_neighbors == chance.placement_neighbors == 10
    assert chance.cutoff_placement == chance.cutoff_between


def test_the_matched_null_uses_the_graphs_neighbours_and_the_one_nearest(plant) -> None:
    matched = themes_engine().compute(
        plant.documents,
        senses=SENSES,
        settings=ThemeSettings(chance_null="matched", chance_replicates=2, seeds=2),
    )
    chance = matched.chance
    assert chance is not None
    assert chance.placement_neighbors == 1 and chance.replicates == 2
    assert chance.edge_neighbors == max(5, round(matched.vertices**0.5))
    assert chance.shuffled_documents == 2 * matched.vertices
    # The single nearest is at least as close as any of the nearest few.
    assert chance.cutoff_placement >= chance.cutoff_between


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_log_odds_ranks_a_word_held_by_every_member_above_a_word_held_by_one() -> None:
    from scipy import sparse

    #            a  b  c  d
    rows = np.array(
        [
            [1, 0, 1, 1],  # theme members: a in all three, b in one
            [1, 0, 1, 0],
            [1, 1, 0, 1],
            [0, 0, 1, 1],  # the rest
            [0, 0, 1, 0],
            [0, 0, 0, 1],
            [0, 0, 1, 1],
        ],
        dtype=float,
    )
    matrix = sparse.csr_matrix(rows)
    ranked = core._distinctive(matrix, {0: [0, 1, 2]}, ["a", "b", "c", "d"], "log_odds", 4.0)
    words = [w for w, _ in ranked[0]]
    assert words[0] == "a"
    assert words.index("a") < words.index("b")
    # A word spread evenly over the theme and the rest is not distinctive.
    assert words.index("c") > words.index("a")


def test_tfidf_stays_the_default_and_log_odds_is_chosen_by_a_setting(plant) -> None:
    assert ThemeSettings().label_method == "tfidf"
    chosen = themes_engine().compute(
        plant.documents,
        senses=SENSES,
        settings=ThemeSettings(label_method="log_odds", label_bootstrap=0, seeds=2),
    )
    assert chosen.label_method == "log_odds" and chosen.themes
    planted = {w for words, _, _ in theme_words(plant) for w in words}
    assert any(set(s.words) & planted for s in chosen.themes)


def theme_words(plant):
    from fixtures.theme_pool import PLANT

    return [(words, years, people) for words, years, people in PLANT.values()]


def test_each_label_says_how_steady_it_is_under_drawing_the_experiments_again(drawn) -> None:
    assert drawn.label_bootstrap == 100
    for summary in drawn.themes:
        assert summary.label_stability is not None and 0.0 <= summary.label_stability <= 1.0
        assert len(summary.label_support) == min(3, len(summary.words))
        assert all(0.0 <= x <= 1.0 for x in summary.label_support)
    for broad in drawn.broad:
        assert broad.label_stability is not None


def test_no_bootstrap_says_none_not_zero(plant) -> None:
    result = themes_engine().compute(
        plant.documents, senses=SENSES, settings=ThemeSettings(label_bootstrap=0, seeds=2)
    )
    assert all(s.label_stability is None and s.label_support == () for s in result.themes)


# ---------------------------------------------------------------------------
# The summary for the quality figures
# ---------------------------------------------------------------------------


def test_the_quality_summary_reads_a_projection() -> None:
    built = {
        "projection_version": "3",
        "summary": {
            "seed_ami": 0.9,
            "medoid_seed": 2,
            "placement_seed": 2,
            "final_placement": "medoid",
            "seeds": [{"seed": 0, "mean_ari": 0.8}, {"seed": 1, "mean_ari": 0.9}],
            "seed_pairs": [
                {"seed_a": 0, "seed_b": 1, "ari": 0.7},
                {"seed_a": 1, "seed_b": 2, "ari": 0.9},
            ],
            "themes": 2,
            "disconnected_themes": 0,
            "broad_themes": 1,
            "disconnected_broad_themes": 0,
            "chance": {"cutoff_placement": 0.4},
            "chance_null": "nearest_ten",
        },
        "experiments": [
            {"theme": 0, "unplaced_reason": None},
            {"theme": None, "unplaced_reason": core.TOO_SMALL},
            {"theme": 1, "unplaced_reason": None},
        ],
        "themes": [
            {"stability": 0.9, "label_stability": 1.0},
            {"stability": 0.5, "label_stability": None},
        ],
    }
    quality = theme_quality(built)
    assert quality["seed_agreement"]["pairs"]["count"] == 2
    assert quality["seed_agreement"]["pairs"]["minimum"] == 0.7
    assert quality["medoid_seed"] == 2
    assert quality["placement"] == {
        "experiments": 3,
        "placed": 2,
        "unplaced": 1,
        "placed_share": pytest.approx(2 / 3, abs=1e-6),
        "unplaced_reasons": {core.TOO_SMALL: 1},
    }
    assert quality["connectivity"]["disconnected_themes"] == 0
    assert quality["label_stability"]["count"] == 1
    assert quality["chance"] == {"cutoff_placement": 0.4}
    empty = theme_quality({**built, "summary": {**built["summary"], "seed_pairs": []}})
    assert empty["seed_agreement"]["pairs"]["mean"] is None


def test_the_log_odds_score_is_the_published_formula() -> None:
    """Monroe, Colaresi and Quinn (2008), the informative Dirichlet prior, worked by hand on three
    words and a theme of two of four rows: each term below is written out as the paper writes it."""
    import math

    from scipy import sparse

    rows = [[2, 0, 1], [1, 1, 0], [0, 3, 1], [1, 1, 1]]  # the counts of experiments holding words
    theme = [0, 1]
    strength = 6.0
    total = [sum(r[w] for r in rows) for w in range(3)]  # y of everything
    y_in = [sum(rows[r][w] for r in theme) for w in range(3)]
    n_all, n_in = sum(total), sum(y_in)
    n_out = n_all - n_in
    expected = []
    for w in range(3):
        alpha = strength * total[w] / n_all
        y_out = total[w] - y_in[w]
        delta = math.log((y_in[w] + alpha) / (n_in + strength - y_in[w] - alpha)) - math.log(
            (y_out + alpha) / (n_out + strength - y_out - alpha)
        )
        variance = 1 / (y_in[w] + alpha) + 1 / (y_out + alpha)
        expected.append(delta / math.sqrt(variance))
    score, sub, found_total = core._scores(
        sparse.csr_matrix(np.array(rows, dtype=float)), [theme], "log_odds", strength
    )
    assert score[0].tolist() == pytest.approx(expected, abs=1e-9)
    assert sub[0].tolist() == y_in
    assert found_total.tolist() == pytest.approx(total, abs=1e-6)
