"""Where the other seeds disagree with a line of work.

A line of work says in how many of the other seeds it is not held, and which other lines those seeds
split it with, counted from the best matches its stability already uses. A line every seed holds
names none. The route serves what the projection computed and no number of its own. The words are
real ones in invented documents, and the pure cases are hand-built arrays.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import build_theme_project
from ingence.api import v1
from ingence.api.v1 import themes as themes_routes
from ingence.core.settings import ThemeSettings
from ingence.themes import themes_engine
from ingence.themes.core import UNCERTAIN_BELOW, _best_matches, _split_with, _stability

#: Ten vertices; the shown placement has three lines: 0 = {0,1,2,3}, 1 = {4,5,6,7}, 2 = {8,9}.
SHOWN = np.array([0, 0, 0, 0, 1, 1, 1, 1, 2, 2])
LINE = [0, 1, 2, 3]


def _seed(*communities: list[int]) -> np.ndarray:
    labels = np.full(10, -1)
    for number, members in enumerate(communities):
        labels[members] = number
    assert (labels >= 0).all()
    return labels


HELD = _seed([0, 1, 2, 3], [4, 5, 6, 7], [8, 9])
#: Takes three of the line's four into the second line's community, and leaves the fourth with the
#: third line.
MERGED = _seed([0, 1, 2, 4, 5, 6, 7], [3, 8, 9])
#: Cuts the line in two among its own experiments.
CUT = _seed([0, 1], [2, 3], [4, 5, 6, 7], [8, 9])


def test_a_line_every_seed_holds_names_no_partner() -> None:
    matches = _best_matches([HELD, HELD, HELD], LINE)
    assert _split_with(SHOWN, matches, LINE, 0) == (0, ())
    assert _stability(matches, LINE)[0] == 1.0


def test_a_seed_that_mixes_the_line_with_another_names_it() -> None:
    matches = _best_matches([HELD, MERGED, MERGED], LINE)
    seeds, partners = _split_with(SHOWN, matches, LINE, 0)
    assert seeds == 2, "two of the three other seeds do not hold the line"
    assert partners == ((1, 2),), "both put it among the second line's experiments"


def test_a_seed_that_cuts_the_line_among_its_own_counts_but_names_none() -> None:
    matches = _best_matches([HELD, CUT, MERGED], LINE)
    seeds, partners = _split_with(SHOWN, matches, LINE, 0)
    assert seeds == 2
    assert partners == ((1, 1),), "the cut seed names no one; the merged seed names the second line"


def test_the_boundary_is_the_axis_s_own() -> None:
    assert pytest.approx(0.8) == UNCERTAIN_BELOW
    # Overlap of exactly 0.80 is held: four of the line's five, nothing added (J = 4 / 5).
    shown = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])
    seed = np.array([0, 0, 0, 0, 1, 1, 1, 1, 1, 1])
    matches = _best_matches([seed], [0, 1, 2, 3, 4])
    assert matches[0][0] == pytest.approx(0.8)
    assert _split_with(shown, matches, [0, 1, 2, 3, 4], 0) == (0, ())


def _blurred() -> dict[str, list[str]]:
    """Three lines whose own words are written about half the time over words they all share."""
    chooser = random.Random(5)
    shared = ["actin", "tubulin", "dapi", "gfp", "confocal", "lysate", "kinase", "gel"]
    own = {
        "a": ["hela", "mitosis", "nocodazole"],
        "b": ["hek293", "transfection", "western"],
        "c": ["dmso", "washout", "marker"],
    }
    documents: dict[str, list[str]] = {}
    for tag, words in own.items():
        for number in range(16):
            held = [w for w in words if chooser.random() < 0.45]
            documents[f"{tag}{number:02d}"] = chooser.sample(shared, 4) + (held or words[:1])
    return documents


def test_blurred_lines_name_each_other() -> None:
    result = themes_engine().compute(_blurred(), settings=ThemeSettings(seeds=16))
    compared = len(result.seeds) - 1
    by_id = {t.theme_id: t for t in result.themes}
    assert compared == 15
    assert any(t.split_seeds > 0 for t in result.themes), "some line is not held by every seed"
    for theme in result.themes:
        assert 0 <= theme.split_seeds <= compared
        named = [seeds for other, seeds in theme.split_with]
        assert sum(named) <= theme.split_seeds, "each seed names at most one partner"
        assert named == sorted(named, reverse=True)
        for other, seeds in theme.split_with:
            assert other != theme.theme_id and other in by_id and 0 < seeds <= compared
        if theme.stability == 1.0:
            assert theme.split_seeds == 0 and theme.split_with == ()
        # A line some seed does not hold has a mean best match under one.
        if theme.split_seeds:
            assert theme.stability < 1.0
    assert any(t.split_with for t in result.themes)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    project, _ = build_theme_project(tmp_path_factory.mktemp("themes_split"))
    return v1.context_for(project, settled=None, job_running=False)


def _stability_route(context):
    status, payload = v1.dispatch(context, "themes/stability", {})
    assert status == 200, payload
    v1.DECLARED["themes/stability"].model_validate(payload)
    return payload


def test_the_route_serves_the_split_and_its_boundary(served) -> None:
    payload = _stability_route(served)
    seeds = ThemeSettings().seeds
    assert payload["uncertain_below"] == pytest.approx(UNCERTAIN_BELOW)
    assert payload["seeds_compared"] == len(payload["seed_placements"]) - 1 <= seeds - 1
    for item in payload["themes"]:
        assert "split_seeds" in item and isinstance(item["split_with"], list)
        assert item["split_seeds"] <= payload["seeds_compared"]


def test_the_route_names_a_partner_by_its_label(served, monkeypatch) -> None:
    built = themes_routes._projection(served, {})
    first, second = built["themes"][0], built["themes"][1]
    edited = {
        **built,
        "themes": [
            {
                **first,
                "split_seeds": 3,
                "split_with": [{"theme": second["theme"], "seeds": 2}],
            },
            *built["themes"][1:],
        ],
    }
    monkeypatch.setattr(themes_routes, "_projection", lambda context, query: edited)
    payload = _stability_route(served)
    item = next(t for t in payload["themes"] if t["theme"] == first["theme"])
    assert item["split_seeds"] == 3
    assert item["split_with"] == [{"theme": second["theme"], "label": second["label"], "seeds": 2}]
    others = [t for t in payload["themes"] if t["theme"] != first["theme"]]
    assert all(t["split_seeds"] == 0 and t["split_with"] == [] for t in others)
