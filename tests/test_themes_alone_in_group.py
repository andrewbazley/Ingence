"""Themes: an experiment alone in its group is never called "no closer than chance".

The reason an experiment is not placed is true of it. "Its words are no closer to any other
experiment's than chance" is said only where closeness was measured against chance for it. An
experiment alone in its group was compared with nothing, and is too small to compare.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")

from ingence.core.settings import ThemeSettings
from ingence.themes import core, themes_engine
from ingence.themes.core import NOT_CLOSE, TOO_SMALL

#: Twenty-four ordinary words; the corpus below names them by number so it reads as a table.
WORDS = (
    "dapi",
    "gfp",
    "hela",
    "mitochondria",
    "actin",
    "tubulin",
    "nucleus",
    "golgi",
    "lysosome",
    "confocal",
    "sted",
    "storm",
    "zeiss",
    "leica",
    "nikon",
    "olympus",
    "membrane",
    "vesicle",
    "kinase",
    "ribosome",
    "centrosome",
    "peroxisome",
    "chromatin",
    "cytoskeleton",
)

#: Fifteen experiments of two to six words each. With two neighbours and one coarse resolution the
#: graph leaves experiment ``u10`` alone in its group, though it shares "olympus" with five others.
CORPUS = {
    "u0": [12, 4, 6, 22],
    "u1": [2, 4],
    "u2": [16, 6, 12],
    "u3": [14, 15],
    "u4": [12, 15, 18, 6, 12],
    "u5": [15, 7],
    "u6": [22, 8],
    "u7": [13, 15, 12, 23, 3, 21],
    "u8": [3, 2, 12, 19],
    "u9": [3, 21, 1, 10, 7],
    "u10": [15, 20],
    "u11": [6, 18, 4, 19, 2, 17],
    "u12": [15, 22],
    "u13": [4, 18, 14],
    "u14": [14, 9, 17, 11, 13, 4],
}
DOCUMENTS = {unit: [WORDS[i] for i in numbers] for unit, numbers in CORPUS.items()}
SETTINGS = ThemeSettings(
    min_experiments_unknown=1,
    neighbours=2,
    edge_chance_quantile=None,
    fine_resolutions=(3.0,),
    resolution_edge_steps=0,
    max_share=0.9,
)


def _compute(settings: ThemeSettings = SETTINGS):
    return themes_engine().compute(DOCUMENTS, settings=settings)


def test_the_closest_within_of_a_lone_vertex_is_not_a_similarity() -> None:
    embedded = np.eye(3)
    best = core._closest_within(embedded, np.array([0, 0, 1]))
    assert best[0] == 0.0 and best[1] == 0.0
    assert np.isnan(best[2])


def test_an_experiment_alone_in_its_group_but_closer_than_chance_is_too_small(monkeypatch) -> None:
    """Chance measured, and low: ``u10`` is closer than that to the others, so it is not "no
    closer than chance"; the communities left it alone."""
    monkeypatch.setattr(core, "_chance_rows", lambda *args, **kwargs: [[-0.5]] * 200)
    result = _compute()
    lone = result.assignments["u10"]
    assert lone.theme_id is None
    assert lone.unplaced_reason == TOO_SMALL


def test_where_chance_was_not_measured_no_reason_speaks_of_chance(monkeypatch) -> None:
    monkeypatch.setattr(core, "_chance_rows", lambda *args, **kwargs: [])
    result = _compute()
    reasons = {a.unplaced_reason for a in result.assignments.values() if a.theme_id is None}
    assert reasons
    assert NOT_CLOSE not in reasons


def test_an_experiment_with_no_neighbour_above_chance_is_still_said_to_be_far() -> None:
    """Measured, and found no closer: the reason stands (the filler-only experiment of the
    generated pool in ``test_themes_core.py`` keeps it)."""
    from fixtures.theme_pool import FILLER_ONLY, SENSES, theme_documents

    plant = theme_documents()
    result = themes_engine().compute(plant.documents, senses=SENSES)
    for unit in FILLER_ONLY:
        assert result.assignments[unit].unplaced_reason == NOT_CLOSE


def _shuffled(copies: int = 3):
    """The pool's words permuted across its experiments, each keeping its word count."""
    import random

    from fixtures.theme_pool import theme_documents

    plant = theme_documents()
    units = sorted(plant.documents)
    tokens = [word for unit in units for word in plant.documents[unit]]
    lengths = [len(plant.documents[unit]) for unit in units]
    for k in range(copies):
        pool = tokens[:]
        random.Random(1000 + k).shuffle(pool)
        docs, start = {}, 0
        for unit, length in zip(units, lengths, strict=True):
            docs[unit] = pool[start : start + length]
            start += length
        yield docs


def test_no_closer_than_chance_is_told_only_of_what_was_tested_against_every_experiment(
    monkeypatch,
) -> None:
    """On shuffled copies of the pool, an experiment inside a group is tested against the rest of
    its group. It is told it is "no closer than chance" to any other experiment only where its best
    similarity to every other one is no better than chance; where it is closer than chance to
    some experiment outside its group, it is told only that it is no closer to the rest of its
    group."""
    from fixtures.theme_pool import SENSES

    seen = []
    real = core._finalise

    def spy(embedded, graph, raw, sizes, settings, threshold, *, measured):
        final = real(embedded, graph, raw, sizes, settings, threshold, measured=measured)
        seen.append((embedded, threshold, final))
        return final

    monkeypatch.setattr(core, "_finalise", spy)
    told_group = 0
    for docs in _shuffled():
        seen.clear()
        result = themes_engine().compute(docs, senses=SENSES)
        embedded, threshold, final = seen[result.placement_seed]
        everything = core._closest_overall(embedded, list(range(embedded.shape[0])))
        flagged = np.flatnonzero(final.far).tolist()
        for vertex in flagged:
            truly_none = bool(everything[vertex] <= threshold)
            assert bool(final.far_overall[vertex]) == truly_none
        reasons = [a.unplaced_reason for a in result.assignments.values()]
        # Whoever is told "no closer than chance" to any, is told it of a flagged vertex.
        assert reasons.count(NOT_CLOSE) <= int(final.far_overall.sum())
        told_group += reasons.count(core.NOT_CLOSE_TO_GROUP)
    assert told_group > 0, "the shuffled copies hold experiments closer to others than chance"


def test_a_blank_placement_share_still_draws_chance_for_the_edges() -> None:
    """Chance is drawn whenever any of the three chance settings is set.

    A blank ``chance_quantile`` does not drop the edge cutoffs, which stay set.
    """
    plant = _shuffled(1)
    docs = next(plant)
    blank = themes_engine().compute(docs, settings=ThemeSettings(chance_quantile=None, seeds=2))
    assert blank.chance is not None and blank.chance.measured
    assert blank.chance.cutoff_between is not None and blank.chance.cutoff_placement is None
    assert NOT_CLOSE not in {a.unplaced_reason for a in blank.assignments.values()}
    none = themes_engine().compute(
        docs,
        settings=ThemeSettings(chance_quantile=None, edge_chance_quantile=None, seeds=2),
    )
    assert none.chance is not None and not none.chance.measured
    assert none.chance.cutoff_between is None and none.chance.shuffled_documents == 0
