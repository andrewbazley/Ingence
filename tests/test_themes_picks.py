"""The theme core's picks and weights at their edges.

A resolution picked at the edge of its sweep is tried past that edge (bounded) before it is kept,
and the result says which resolutions were tried. Identical documents are never a theme on their
own. A tiny collection is weighed as if it had a floor of experiments. Every word is invented
(``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import SENSES, theme_documents
from ingence.core.settings import ThemeSettings
from ingence.themes.core import REPEATED_TEXT, compute_themes


def _ami(left: list[str], right: list[str]) -> float:
    from sklearn.metrics import adjusted_mutual_info_score

    return float(adjusted_mutual_info_score(left, right))


@pytest.mark.parametrize(
    ("offered", "added"),
    [
        # Too fine everywhere offered: the coarsest is picked, so coarser ones are tried.
        ((6.0, 9.0, 13.5), (2.666667, 4.0)),
        # Too coarse everywhere offered: the finest is picked, so finer ones are tried.
        ((0.05, 0.08, 0.12), (0.18,)),
    ],
)
def test_a_pick_at_the_edge_of_its_sweep_is_tried_past_the_edge(
    offered: tuple[float, ...], added: tuple[float, ...]
) -> None:
    """The measurement (printed): on the generated pool of six planted themes, a sweep that
    offers only resolutions too fine (or too coarse) picks its edge; tried further, each step the
    ratio of the two outermost, the pick lies inside the resolutions tried (measured: offered
    6, 9, 13.5, the pick moved from 6 to 4 with 2.67 and 4 tried; offered 0.05, 0.08, 0.12, it
    stayed at 0.12 with 0.18 tried, which is no steadier)."""
    plant = theme_documents()
    kept = compute_themes(
        plant.documents,
        senses=SENSES,
        settings=ThemeSettings(fine_resolutions=offered, resolution_edge_steps=0),
    )
    assert kept.resolution in (min(offered), max(offered))
    assert kept.sweep_extended == ()
    result = compute_themes(
        plant.documents, senses=SENSES, settings=ThemeSettings(fine_resolutions=offered)
    )
    tried = sorted(row.resolution for row in result.sweep)
    print(offered, [(row.resolution, row.themes, row.seed_ami) for row in result.sweep])
    assert result.sweep_extended == added
    assert tried == sorted({*offered, *added})
    assert min(tried) < (result.resolution or 0.0) < max(tried)
    # The planted themes are found at least as well as at the edge (measured: offered 6, 9,
    # 13.5, AMI 0.857 with 57 of 60 placed at the edge, 0.961 with 58 inside; offered 0.05,
    # 0.08, 0.12, 1.0 with all 60 both ways).
    planted = [u for u, theme in plant.planted.items() if theme != "none"]
    for drawn in (kept, result):
        placed = [u for u in planted if drawn.assignments[u].theme_id is not None]
        found = [str(drawn.assignments[u].theme_id) for u in placed]
        scores = _ami([plant.planted[u] for u in placed], found), len(placed)
        print(drawn.resolution, scores)
    assert scores[0] >= 0.95 and scores[1] >= 0.95 * len(planted)


def test_the_default_sweep_tries_one_finer_and_keeps_its_pick_inside() -> None:
    """On the generated pool the default sweep's finest (3.0) is the steadiest: 4.5 is tried,
    and the pick stays at 3.0 with every theme unchanged."""
    plant = theme_documents()
    result = compute_themes(plant.documents, senses=SENSES)
    assert result.sweep_extended == (4.5,)
    assert result.resolution == 3.0
    assert [row.resolution for row in result.sweep][-1] == 4.5


def test_a_tie_past_the_edge_neither_moves_the_pick_nor_extends_further() -> None:
    """On a pool whose coarsest offered resolution is the steadiest, a coarser step that only ties
    it does not take the pick.

    A tie goes to a resolution offered, and a step that only ties ends the extension. Otherwise the
    same themes would be drawn for two steps for nothing. This pool uses a sweep of 0.2, 0.3 and
    0.5.
    """
    plant = theme_documents(6)
    offered = ThemeSettings(fine_resolutions=(0.2, 0.3, 0.5))
    kept = compute_themes(
        plant.documents,
        senses=SENSES,
        settings=ThemeSettings(fine_resolutions=(0.2, 0.3, 0.5), resolution_edge_steps=0),
    )
    assert kept.resolution == 0.2
    result = compute_themes(plant.documents, senses=SENSES, settings=offered)
    assert result.resolution == 0.2
    assert result.sweep_extended == (0.133333,)
    rows = {row.resolution: row for row in result.sweep}
    measure = [(round(rows[r].seed_ami or 0.0, 2), rows[r].themes) for r in (0.133333, 0.2)]
    assert measure[0] == measure[1]


def test_a_step_past_the_coarse_edge_is_never_zero() -> None:
    """With offered resolutions near zero, a coarser step that rounds to 0.0 is not tried.

    A step that rounds to zero or below ends the extension.
    """
    plant = theme_documents()
    result = compute_themes(
        plant.documents, senses=SENSES, settings=ThemeSettings(fine_resolutions=(2e-7, 1e-6))
    )
    assert all(resolution > 0 for resolution in result.sweep_extended)
    assert all(row.resolution > 0 for row in result.sweep)
    # Where the coarsest offered is steadier than the next, the step past it is still never 0.
    from ingence.themes.core import SweepRow, _past_the_edge

    edge = SweepRow(resolution=2e-7, themes=6, seed_ami=0.97, seed_nmi=0.9)
    inner = SweepRow(resolution=1e-6, themes=5, seed_ami=0.90, seed_nmi=0.9)
    assert _past_the_edge([edge, inner], edge, finer=2, coarser=2) is None
    wide = SweepRow(resolution=0.5, themes=5, seed_ami=0.90, seed_nmi=0.9)
    assert (
        _past_the_edge([wide, SweepRow(0.3, 6, 0.97, 0.9)], SweepRow(0.3, 6, 0.97, 0.9), 2, 2)
        == 0.18
    )


def test_the_pick_prefers_an_offered_resolution_over_an_added_one_on_a_tie() -> None:
    from ingence.themes.core import SweepRow, _pick

    offered = SweepRow(resolution=0.3, themes=6, seed_ami=0.951, seed_nmi=0.9)
    added = SweepRow(resolution=0.2, themes=6, seed_ami=0.954, seed_nmi=0.9)
    assert _pick([added, offered], {0.2, 0.3}, offered=(0.3,)) is offered


def test_the_extra_steps_are_bounded_by_their_setting() -> None:
    """Offered 6, 9 and 13.5 the pick lies on the coarse edge and two steps take it to 4 then
    past it (the measurement above); one step allowed, exactly one is tried, and the pick is
    the step, still on the edge."""
    plant = theme_documents()
    offered = (6.0, 9.0, 13.5)
    first = compute_themes(
        plant.documents,
        senses=SENSES,
        settings=ThemeSettings(fine_resolutions=offered, resolution_edge_steps=0),
    )
    assert first.resolution == min(offered)
    result = compute_themes(
        plant.documents,
        senses=SENSES,
        settings=ThemeSettings(fine_resolutions=offered, resolution_edge_steps=1),
    )
    assert result.sweep_extended == (4.0,)
    assert result.resolution == 4.0
    with pytest.raises(ValueError):
        ThemeSettings(resolution_edge_steps=-1)


def _with_a_repeated_text(copies: int = 4) -> dict[str, list[str]]:
    """The generated pool, and one text of words of its own deposited ``copies`` times."""
    documents = {u: list(w) for u, w in theme_documents().documents.items()}
    for number in range(copies):
        documents[f"again_{number}"] = ["zibbet", "wollop", "grenda", "alpok"]
    return documents


def test_a_repeated_text_is_never_a_theme_on_its_own() -> None:
    """One text deposited several times is not a theme of its own.

    Such a theme would have almost no stability. Identical documents are one point, and a theme
    needs two different texts, so the copies are listed as not placed, with their reason.
    """
    documents = _with_a_repeated_text()
    result = compute_themes(documents, senses=SENSES)
    copies = {u for u in documents if u.startswith("again_")}
    for summary in result.themes:
        assert not set(summary.members) <= copies, summary.label
    for unit in sorted(copies):
        assignment = result.assignments[unit]
        assert assignment.theme_id is None
        assert assignment.unplaced_reason == REPEATED_TEXT
    # The planted themes are untouched.
    plant = theme_documents()
    planted = [u for u, theme in plant.planted.items() if theme != "none"]
    assert all(result.assignments[u].theme_id is not None for u in planted)
    # The setting says how many different texts a theme needs; one makes a theme of copies.
    one = compute_themes(documents, senses=SENSES, settings=ThemeSettings(min_theme_documents=1))
    assert any(set(s.members) <= copies for s in one.themes)


def test_the_repeated_text_reason_is_given_only_where_the_copies_are_alone() -> None:
    """Copies that share their community with another document are not told they are one text
    repeated, though their community has too few different documents for a theme.

    The reason is given only where the identical documents are alone in their community.
    """
    assert REPEATED_TEXT == "its experiments read the same words, which is no theme on its own"
    documents = _with_a_repeated_text()
    documents["other_0"] = ["zibbet", "wollop", "fennick", "alpok"]
    documents["other_1"] = ["zibbet", "grenda", "fennick", "alpok"]
    documents["other_2"] = ["wollop", "grenda", "fennick", "alpok"]
    result = compute_themes(documents, senses=SENSES, settings=ThemeSettings(min_theme_documents=5))
    together = {u for u in documents if u.startswith(("again_", "other_"))}
    reasons = {result.assignments[u].unplaced_reason for u in together}
    shared = {result.assignments[u].theme_id for u in together}
    assert shared == {None}
    assert REPEATED_TEXT not in reasons, reasons


def _weights(sizes: dict[str, int], settings: ThemeSettings) -> dict[str, float]:
    documents: dict[str, list[str]] = {}
    collections: dict[str, str] = {}
    for name, size in sizes.items():
        for number in range(size):
            documents[f"{name}_{number:05d}"] = []
            collections[f"{name}_{number:05d}"] = name
    result = compute_themes(documents, collections=collections, settings=settings)
    return {c.collection: c.unit_weight for c in result.collections}


def test_a_one_item_collection_does_not_weigh_tens_of_times_an_average_experiment() -> None:
    """Beside collections of thousands, a collection of one does not weigh forty times an average
    experiment.

    Measured on this invented shape (unit weights, mean one): without a floor the one-item
    collection weighs 39.2, with the default floor of ten 12.5 (twenty would give 8.9, but changed
    one existing pool's themes). The large collections move by under two in a hundred.
    """
    sizes = {"kelt": 3000, "vorn": 1500, "brisa": 400, "zeph": 200, "pilot": 3, "lone": 1}
    unfloored = _weights(sizes, ThemeSettings(collection_weight_floor=1))
    floored = _weights(sizes, ThemeSettings())
    print(unfloored, floored)
    assert unfloored["lone"] > 30
    assert floored["lone"] < 13
    assert floored["pilot"] == floored["lone"]
    for name in ("kelt", "vorn", "brisa", "zeph"):
        assert abs(floored[name] - unfloored[name]) / unfloored[name] < 0.02
