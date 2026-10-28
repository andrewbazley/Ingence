"""Themes from words: the pure core over a generated pool with known themes.

A theme is a group of documents that share words more than chance. Themes are found by deterministic
arithmetic and labelled by the Dictionary's senses first. What cannot be placed is listed rather
than forced, and collections of very different sizes are weighed so each keeps its themes. Every
word is invented (``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import (
    FILLER_ONLY,
    FREQUENT_UNKNOWN,
    OUTLIERS,
    PAIR,
    PLANT,
    SENSES,
    machine_and_human,
    theme_documents,
)
from ingence.core.settings import ThemeSettings
from ingence.themes import themes_engine
from ingence.themes.core import NO_WORDS, NOT_CLOSE, TOO_SMALL


def _compute(*args, **kwargs):
    return themes_engine().compute(*args, **kwargs)


def _ami(left: list[str], right: list[str]) -> float:
    from sklearn.metrics import adjusted_mutual_info_score

    return float(adjusted_mutual_info_score(left, right))


@pytest.fixture(scope="module")
def drawn():
    plant = theme_documents()
    result = _compute(plant.documents, senses=SENSES)
    return plant, result


def test_the_themes_recover_the_planted_groups(drawn) -> None:
    plant, result = drawn
    planted_units = [unit for unit, theme in plant.planted.items() if theme != "none"]
    placed = [unit for unit in planted_units if result.assignments[unit].theme_id is not None]
    assert len(placed) == len(planted_units)
    found = [str(result.assignments[unit].theme_id) for unit in placed]
    truth = [plant.planted[unit] for unit in placed]
    # AMI against the plant: every planted group is one theme, and no theme mixes two.
    assert _ami(truth, found) >= 0.95
    assert len(result.themes) == len(PLANT)
    assert result.seed_ami is not None
    assert result.seed_nmi is not None


def test_labels_name_the_dictionary_senses_first(drawn) -> None:
    _, result = drawn
    for theme, (words, _, _) in PLANT.items():
        unit = f"{theme}_00"
        summary = result.themes[result.assignments[unit].theme_id]
        known = [SENSES[w].replace("_", " ") for w in words if w in SENSES]
        # The theme's sense, by its canonical name, leads its words and its label.
        assert summary.words[0] == known[0]
        assert summary.label.split(" · ")[0] == known[0]
        assert summary.senses == tuple(known)
        # The spellings it does not know follow.
        own = {w for w in words if w not in SENSES} | (
            {FREQUENT_UNKNOWN} if theme == "t2" else set()
        )
        assert set(summary.words[len(known) : len(known) + len(own)]) <= own


def test_a_frequent_co_appearing_unknown_word_joins_its_theme(drawn) -> None:
    _, result = drawn
    summary = result.themes[result.assignments["t2_00"].theme_id]
    assert FREQUENT_UNKNOWN in summary.words


def test_what_cannot_be_placed_is_listed_never_forced(drawn) -> None:
    _, result = drawn
    for unit in OUTLIERS:
        assert result.assignments[unit].theme_id is None
        assert result.assignments[unit].unplaced_reason == NO_WORDS
    for unit in PAIR:
        assert result.assignments[unit].theme_id is None
        assert result.assignments[unit].unplaced_reason == TOO_SMALL
    for unit in FILLER_ONLY:
        assert result.assignments[unit].theme_id is None
        assert result.assignments[unit].unplaced_reason == NOT_CLOSE
    assert set(result.unplaced) == set(OUTLIERS) | set(PAIR) | set(FILLER_ONLY)


def test_broad_themes_hold_whole_fine_themes(drawn) -> None:
    _, result = drawn
    for summary in result.themes:
        broads = {result.assignments[unit].broad_id for unit in summary.members}
        assert broads == {summary.broad_id}
    assert {theme for broad in result.broad for theme in broad.themes} == {
        summary.theme_id for summary in result.themes
    }


def test_seed_agreement_is_given_per_theme(drawn) -> None:
    _, result = drawn
    for summary in result.themes:
        assert 0.0 <= summary.stability <= 1.0
        assert 0 <= summary.core_units <= summary.units
    # The plant is clean: every theme comes back whole under every seed.
    assert min(summary.stability for summary in result.themes) >= 0.9


def test_a_phrase_is_read_as_one_word() -> None:
    plant = theme_documents()
    documents = {unit: list(words) for unit, words in plant.documents.items()}
    for unit in list(documents)[:12]:
        documents[unit] = [*documents[unit], "glass", "plate"]
    result = _compute(
        documents,
        senses={**SENSES, "glassplate": "glass_plate"},
        phrases={("glass", "plate"): "glassplate"},
    )
    assert "glass" not in result.display
    assert result.display.get("glassplate") == "glass plate"


def test_the_same_input_gives_the_same_output_byte_for_byte(drawn, tmp_path: Path) -> None:
    plant, result = drawn
    again = _compute(plant.documents, senses=SENSES)
    first = json.dumps(result.to_json(), sort_keys=True)
    assert first == json.dumps(again.to_json(), sort_keys=True)
    # And in another process, under another string-hash seed.
    script = tmp_path / "draw.py"
    script.write_text(
        "import json, sys\n"
        "from fixtures.theme_pool import SENSES, theme_documents\n"
        "from ingence.themes import themes_engine\n"
        "plant = theme_documents()\n"
        "result = themes_engine().compute(plant.documents, senses=SENSES)\n"
        "sys.stdout.write(json.dumps(result.to_json(), sort_keys=True))\n"
    )
    tests = Path(__file__).parent
    environment = {
        **os.environ,
        "PYTHONHASHSEED": "12345",
        "PYTHONPATH": os.pathsep.join([str(tests), *sys.path]),
    }
    out = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=environment, check=True
    )
    assert out.stdout == first


def _per_collection_ami(result, collections, plant, name):
    units = [u for u, c in collections.items() if c == name]
    placed = [u for u in units if result.assignments[u].theme_id is not None]
    return (
        _ami([plant[u] for u in placed], [str(result.assignments[u].theme_id) for u in placed]),
        len(placed) / len(units),
        len({result.assignments[u].theme_id for u in placed}),
    )


@pytest.mark.parametrize("campaigns", [3, 60])
def test_a_huge_machine_collection_does_not_drown_a_small_human_one(campaigns: int) -> None:
    """A huge machine-made collection does not drown a small human one under the default weighting.

    Pooled with one machine-made collection of three thousand near-identical units (three campaigns,
    or sixty: more words than the LSA's dimensions), a human collection of forty keeps its four
    themes, and the machine collection keeps its campaigns. The numbers for each power are printed.
    """
    documents, collections, plant = machine_and_human(campaigns=campaigns)
    measured = {}
    for power in (1.0, 0.5, 0.0):
        # The weighting measured with the chance floor on edges within a collection too, which
        # isolates it: without that floor, campaigns sharing the machine's words merge. The floor is
        # off by default.
        settings = ThemeSettings(collection_weight_power=power, edge_chance_quantile_within=0.75)
        result = _compute(documents, collections=collections, settings=settings)
        measured[power] = {
            name: _per_collection_ami(result, collections, plant, name)
            for name in ("machine", "human")
        }
    print(json.dumps({str(k): v for k, v in measured.items()}))
    human_ami, human_placed, human_themes = measured[0.5]["human"]
    machine_ami, machine_placed, machine_themes = measured[0.5]["machine"]
    assert human_ami >= 0.9 and human_placed >= 0.9 and human_themes == 4
    assert machine_ami >= 0.9 and machine_placed >= 0.9 and machine_themes == campaigns


def test_alone_and_pooled_agree_for_each_collection() -> None:
    """Each collection's themes pooled are its themes alone (AMI), under the default weights and
    a chance floor within collections (see the test above)."""
    documents, collections, _ = machine_and_human()
    floored = ThemeSettings(edge_chance_quantile_within=0.75)
    pooled = _compute(documents, collections=collections, settings=floored)
    for name in ("machine", "human"):
        own = {u: d for u, d in documents.items() if collections[u] == name}
        alone = _compute(own, collections=dict.fromkeys(own, name), settings=floored)
        units = sorted(u for u in own if alone.assignments[u].theme_id is not None)
        left = [str(alone.assignments[u].theme_id) for u in units]
        right = [str(pooled.assignments[u].theme_id) for u in units]
        assert _ami(left, right) >= 0.9, name
        assert Counter(right).most_common(1)[0][1] < len(units)


def test_louvain_finds_two_cliques_joined_by_one_weak_edge_and_splits_disconnected_parts() -> None:
    import numpy as np

    from ingence.themes.communities import adjacency, louvain, split_connected

    edges = {
        (a, b): 1.0 for group in (range(5), range(5, 10)) for a in group for b in group if a < b
    }
    edges[(4, 5)] = 0.05
    graph = adjacency(10, edges)
    for seed in range(4):
        labels = louvain(graph, 1.0, seed)
        assert len(set(labels[:5].tolist())) == 1
        assert len(set(labels[5:].tolist())) == 1
        assert labels[0] != labels[9]
        assert np.array_equal(labels, louvain(graph, 1.0, seed))
    # One label over two pieces with no edge between them: two parts.
    apart = adjacency(4, {(0, 1): 1.0, (2, 3): 1.0})
    parts = split_connected(apart, np.zeros(4, dtype=int))
    assert parts[0] == parts[1] and parts[2] == parts[3] and parts[0] != parts[2]


def test_more_themes_than_fifty_dimensions_are_each_recovered() -> None:
    """With fifty LSA dimensions, sixty planted themes over 2,500 experiments would come back as 75
    themes with a quarter not placed.

    The dimensions grow with the distinct documents, so each planted theme is one theme.
    """
    from fixtures.theme_pool import many_themes

    documents, plant = many_themes(units=2500, themes=60)
    result = _compute(documents)
    placed = [u for u in documents if result.assignments[u].theme_id is not None]
    assert 55 <= len(result.themes) <= 62
    assert len(placed) >= 0.9 * len(documents)
    assert (
        _ami([str(plant[u]) for u in placed], [str(result.assignments[u].theme_id) for u in placed])
        >= 0.95
    )


def test_resolutions_must_be_given_and_positive() -> None:
    from pydantic import ValidationError

    for bad in (
        {"fine_resolutions": ()},
        {"broad_resolutions": ()},
        {"fine_resolutions": (1.0, 0.0)},
        {"broad_resolutions": (-0.1,)},
    ):
        with pytest.raises(ValidationError):
            ThemeSettings(**bad)


def test_no_closer_than_chance_is_said_only_where_chance_was_measured() -> None:
    plant = theme_documents()
    result = _compute(plant.documents, senses=SENSES, settings=ThemeSettings(chance_quantile=None))
    reasons = {a.unplaced_reason for a in result.assignments.values() if a.theme_id is None}
    assert NOT_CLOSE not in reasons


def test_a_sense_spread_over_every_theme_does_not_lead_their_labels() -> None:
    """A common sense in two of five experiments of every theme is not moved into every label ahead
    of the theme's own words.

    A sense moves forward only where it is concentrated in the theme.
    """
    plant = theme_documents()
    documents = {u: list(w) for u, w in plant.documents.items()}
    for index, unit in enumerate(sorted(documents)):
        if plant.planted[unit] != "none" and index % 5 in (0, 1):
            documents[unit].append("plenko")
    result = _compute(documents, senses={**SENSES, "plenko": "plenko_method"})
    for summary in result.themes:
        assert "plenko method" not in summary.label.split(" · "), summary.label


def _sharing_one_word(seed: int) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    """A large collection of 8 themes beside a small one of 3, each small theme sharing one of its
    four words with a large theme.
    """
    import random

    chooser = random.Random(seed)
    syllables = ("ka", "lo", "mi", "nu", "pe", "ro", "si", "tu", "va", "ze", "bo", "di")
    made = sorted({"".join(chooser.choice(syllables) for _ in range(3)) + "q" for _ in range(3000)})
    chooser.shuffle(made)
    filler, made = made[:6], made[6:]
    large = [made[4 * t : 4 * t + 4] for t in range(8)]
    small = [made[32 + 4 * t : 36 + 4 * t] for t in range(3)]
    for index, words in enumerate(small):
        words[0] = large[index][0]
    documents: dict[str, list[str]] = {}
    collections: dict[str, str] = {}
    plant: dict[str, str] = {}
    for name, groups, size in (("large", large, 120), ("small", small, 6)):
        for theme, words in enumerate(groups):
            for number in range(size):
                unit = f"{name}{theme}_{number:03d}"
                documents[unit] = [*chooser.sample(words, 3), chooser.choice(filler)]
                collections[unit] = name
                plant[unit] = f"{name}{theme}"
    return documents, collections, plant


#: The measurement below was taken over the offered resolutions only. Tried past the sweep's edge
#: (``resolution_edge_steps``), seed 1 without the floor picks a finer resolution where no small
#: theme joins a large one, which would measure something else.
PINNED_SWEEP = {"resolution_edge_steps": 0}


def test_a_small_collection_sharing_a_word_with_a_large_one_keeps_its_own_themes() -> None:
    """Edges between collections must beat chance.

    A small collection's themes, each sharing one word of four with a large collection's, stay in
    themes of their own instead of joining the large ones.
    """
    joined_without: list[int] = []
    for seed in range(2):
        documents, collections, _ = _sharing_one_word(seed)
        small = [u for u in documents if collections[u] == "small"]
        for floor in (0.75, None):
            result = _compute(
                documents,
                collections=collections,
                settings=ThemeSettings(edge_chance_quantile=floor, **PINNED_SWEEP),
            )
            mixed = [s for s in result.themes if len({collections[m] for m in s.members}) > 1]
            if floor is None:
                joined_without.append(len(mixed))
                continue
            assert mixed == []
            assert all(result.assignments[u].theme_id is not None for u in small)
    # Without the floor between collections, small themes join the large ones they share a word
    # with (measured: 3 and 2 of the 3 in these two pools, at the resolutions offered).
    assert joined_without == [3, 2]
