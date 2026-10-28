"""The map of groups: where each experiment lies in the plane.

The same documents give the same places, to the digit, on a rebuild, in another process, with
another number of threads and under any order or naming of the input. An experiment with no word the
themes read has no place (never zero). An experiment in no theme is still placed. Experiments
reading the same words share a point. Every place lies in the unit square with one scale for both
axes. Every word is invented (``fixtures.theme_pool``).
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import (
    FILLER_ONLY,
    OUTLIERS,
    PAIR,
    SENSES,
    theme_documents,
)
from ingence.core.settings import ThemeSettings
from ingence.themes import mapxy
from ingence.themes.core import DocumentVectors, compute_themes, document_vectors
from ingence.themes.mapxy import NO_PLACE_FEW, NO_PLACE_WORDS, digest_of, map_positions

REPOSITORY = Path(__file__).resolve().parents[1]


def _map(documents, method: str = "tsne", seed: int = 0):
    found = document_vectors(documents, senses=SENSES, seed=seed)
    return map_positions(found, sorted(documents), method=method)


@pytest.fixture(scope="module")
def plant():
    return theme_documents()


@pytest.fixture(scope="module")
def reference(plant):
    """The plant's places by each method, computed once for the tests that compare with them."""
    return {method: _map(plant.documents, method) for method in ("tsne", "pca")}


@pytest.mark.parametrize("method", ["tsne", "pca"])
def test_two_computations_are_identical_to_the_digit(plant, reference, method) -> None:
    first = reference[method]
    second = _map(plant.documents, method)
    assert first.places == second.places
    assert first.method == method
    assert json.dumps(first.places, sort_keys=True) == json.dumps(second.places, sort_keys=True)


@pytest.mark.parametrize("method", ["tsne", "pca"])
def test_the_order_and_the_names_of_the_input_do_not_change_a_place(
    plant, reference, method
) -> None:
    for trial in range(3):
        items = list(plant.documents.items())
        random.Random(trial).shuffle(items)
        names = {
            unit: hashlib.blake2b(f"{trial}:{unit}".encode(), digest_size=8).hexdigest()
            for unit, _ in items
        }
        found = _map({names[unit]: words for unit, words in items}, method).places
        assert {unit: found[names[unit]] for unit in plant.documents} == reference[method].places


def test_another_process_with_one_thread_gives_the_same_places(reference) -> None:
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from fixtures.theme_pool import SENSES, theme_documents\n"
        "from ingence.themes.core import document_vectors\n"
        "from ingence.themes.mapxy import digest_of, map_positions\n"
        "documents = theme_documents().documents\n"
        "found = document_vectors(documents, senses=SENSES, seed=0)\n"
        "print(digest_of(map_positions(found, sorted(documents)).places))\n"
    )
    names = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
    threads = dict.fromkeys(names, "1")
    environment = {
        **os.environ,
        **threads,
        "PYTHONPATH": os.pathsep.join([str(REPOSITORY / "src"), str(REPOSITORY / "tests")]),
    }
    done = subprocess.run(
        [sys.executable, "-c", code, str(REPOSITORY / "tests")],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == digest_of(reference["tsne"].places)


def test_the_seed_of_the_vectors_is_the_seed_of_the_map(plant) -> None:
    one = _map(plant.documents, seed=1)
    assert one.seed == 1
    # The placement seed of the themes names the space; the map follows it.
    result = compute_themes(plant.documents, senses=SENSES)
    assert _map(plant.documents, seed=result.placement_seed).seed == result.placement_seed


def test_the_vectors_are_the_ones_the_themes_were_drawn_over(plant, monkeypatch) -> None:
    from ingence.themes import core

    seen: list[np.ndarray] = []
    original = core._embedding

    def recording(tfidf, mass, dimensions, seed):
        seen.append(original(tfidf, mass, dimensions, seed))
        return seen[-1]

    monkeypatch.setattr(core, "_embedding", recording)
    result = compute_themes(plant.documents, senses=SENSES)
    monkeypatch.setattr(core, "_embedding", original)
    found = document_vectors(plant.documents, senses=SENSES, seed=result.placement_seed)
    # The last embeddings drawn are one per seed, in seed order (the chance replicates come before
    # them): the shown seed's is what the map is drawn over.
    seeds = ThemeSettings().seeds
    assert np.array_equal(seen[len(seen) - seeds + result.placement_seed], found.vectors)


def test_an_experiment_with_no_word_the_themes_read_has_no_place_and_says_why(reference) -> None:
    found = reference["tsne"]
    for unit in OUTLIERS:
        assert found.places[unit] is None
        assert found.reasons[unit] == NO_PLACE_WORDS
    assert set(found.reasons) == {u for u, place in found.places.items() if place is None}


def test_an_experiment_in_no_theme_is_still_placed(plant, reference) -> None:
    result = compute_themes(plant.documents, senses=SENSES)
    found = reference["tsne"]
    for unit in FILLER_ONLY:
        assert result.assignments[unit].theme_id is None
        assert found.places[unit] is not None


def test_experiments_reading_the_same_words_share_one_point(reference) -> None:
    found = reference["tsne"]
    a, b = PAIR
    assert found.places[a] == found.places[b] is not None
    assert found.sharing >= 2


@pytest.mark.parametrize("method", ["tsne", "pca"])
def test_every_place_lies_in_the_unit_square_with_one_scale(reference, method) -> None:
    placed = [p for p in reference[method].places.values() if p is not None]
    assert placed
    xs = [x for x, _ in placed]
    ys = [y for _, y in placed]
    assert min(xs) >= 0.0 and max(xs) <= 1.0 and min(ys) >= 0.0 and max(ys) <= 1.0
    # One factor for both axes: the longer side is exactly 1.
    assert max(max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx(1.0, abs=1e-5)


def test_experiments_of_one_planted_theme_lie_nearer_each_other_than_others(
    plant, reference
) -> None:
    places = reference["tsne"].places
    themes = sorted({t for t in plant.planted.values() if t != "none"})

    def distance(a: str, b: str) -> float:
        (ax, ay), (bx, by) = places[a], places[b]
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    for theme in themes:
        inside = [u for u, t in plant.planted.items() if t == theme]
        outside = [u for u, t in plant.planted.items() if t not in (theme, "none")]
        within = [distance(a, b) for a in inside for b in inside if a < b]
        between = [distance(a, b) for a in inside for b in outside]
        assert sum(within) / len(within) < sum(between) / len(between), theme


def test_too_few_distinct_documents_draw_no_map() -> None:
    found = DocumentVectors(
        units=(("a",), ("b", "b_copy")), vectors=np.eye(2), empty=("c",), seed=0
    )
    placed = map_positions(found, ["a", "b", "b_copy", "c"])
    assert placed.method is None and placed.points == 2
    assert all(place is None for place in placed.places.values())
    assert placed.reasons == {
        "a": NO_PLACE_FEW,
        "b": NO_PLACE_FEW,
        "b_copy": NO_PLACE_FEW,
        "c": NO_PLACE_WORDS,
    }


def test_nothing_to_place_is_no_map() -> None:
    found = map_positions(document_vectors({}, senses={}), [])
    assert found.places == {} and found.method is None and found.points == 0


def test_a_run_with_more_documents_than_the_limit_is_placed_by_principal_components(
    plant, reference, monkeypatch
) -> None:
    monkeypatch.setattr(mapxy, "TSNE_LIMIT", 10)
    found = _map(plant.documents)
    assert found.method == "pca"
    assert found.places == reference["pca"].places


def test_a_few_documents_are_placed_by_principal_components_not_t_sne(monkeypatch) -> None:
    documents = {f"u{i}": ["keltan", "quolt", f"w{i % 3}"] for i in range(30)}
    senses = {"keltan": "keltane", "quolt": "quolting", "w0": "w0", "w1": "w1", "w2": "w2"}
    found = document_vectors(documents, senses=senses)
    count = len(found.units)
    assert count < mapxy.MINIMUM_TSNE
    placed = map_positions(found, sorted(documents), method="tsne")
    # The method said is the one used.
    assert placed.method == "pca"
    assert mapxy.method_for(count, "tsne") == "pca"
    plane = mapxy.plane_of(found.vectors, method="tsne", seed=0)
    assert plane.shape == (count, 2)


def test_the_method_words_say_that_distances_are_approximate() -> None:
    for words in mapxy.METHOD_WORDS.values():
        assert "approximate" in words
        assert "same run" in words
        assert "on this computer" in words


@pytest.mark.parametrize(
    ("name", "other"),
    [
        ("MAP_VERSION", "0"),
        ("METHOD", "pca"),
        ("TSNE_LIMIT", 10),
        ("PERPLEXITY", 20.0),
        ("ITERATIONS", 500),
        ("DECIMALS", 4),
        ("MINIMUM_POINTS", 4),
        ("MINIMUM_TSNE", 9),
    ],
)
def test_the_record_of_what_drew_a_map_changes_with_every_setting(monkeypatch, name, other) -> None:
    before = mapxy.made_with()
    monkeypatch.setattr(mapxy, name, other)
    assert mapxy.made_with() != before


def test_the_record_of_what_drew_a_map_names_the_libraries() -> None:
    record = mapxy.made_with()
    assert {"numpy", "scipy", "scikit_learn"} <= set(record)
    assert all(isinstance(record[name], str) and record[name] for name in ("numpy", "scipy"))


def test_the_fit_runs_with_the_openmp_threads_limited_to_one(monkeypatch) -> None:
    import sklearn.manifold
    from threadpoolctl import threadpool_info

    def counts() -> list[int]:
        return [lib["num_threads"] for lib in threadpool_info() if lib["user_api"] == "openmp"]

    seen: list[list[int]] = []

    class Recording:
        def __init__(self, **_arguments: object) -> None:
            pass

        def fit_transform(self, vectors: np.ndarray) -> np.ndarray:
            seen.append(counts())
            return np.asarray(vectors[:, :2])

    monkeypatch.setattr(sklearn.manifold, "TSNE", Recording)
    before = counts()
    plane = mapxy.plane_of(np.random.default_rng(0).normal(size=(20, 5)), method="tsne", seed=0)
    assert plane.shape == (20, 2)
    assert seen and all(count == 1 for each in seen for count in each)
    # The limit is lifted when the fit is done.
    assert counts() == before


def test_limiting_the_threads_does_not_change_the_places(plant) -> None:
    from sklearn.manifold import TSNE

    vectors = document_vectors(plant.documents, senses=SENSES, seed=0).vectors
    limited = mapxy.plane_of(vectors, method="tsne", seed=0)
    unlimited = TSNE(
        n_components=2,
        init="pca",
        perplexity=min(mapxy.PERPLEXITY, max(2.0, (len(vectors) - 1) / 3.0)),
        max_iter=mapxy.ITERATIONS,
        random_state=0,
        n_jobs=1,
    ).fit_transform(vectors)
    assert np.array_equal(limited, unlimited)
