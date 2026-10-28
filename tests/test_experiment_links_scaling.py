"""The experiments' clustering holds what grows with the experiments, not with their pairs.

Proposing groups once held every pair of experiments sharing a weighted feature, with the
features each pair shared. A feature may be held by up to a quarter of the experiments, so the
pairs grew with the square of the experiments: about 69 thousand pairs and 55 MiB at 500
experiments, 4.4 million and 2.6 GiB at 4,000 (measured on generated pools of invented names).
What the proposal reads is each experiment's few closest others and the edges both ends agree on,
so it keeps only those. Placing experiments on one another's paths once compared every folder
with every named folder; it walks each path's folders. Both give exactly what the earlier
algorithms gave: the tests compare them with those algorithms, kept here as the reference, on
generated pools.
"""

from __future__ import annotations

import math
import random
import tracemalloc
from collections import defaultdict

import pytest

from ingence.grouping.experiment_links import (
    ProposedGroup,
    _components,
    _distinct,
    _weights,
    experiment_units,
    propose_groups,
    similarities,
)

WORDS = [
    "kelt", "vorn", "zib", "brisa", "trell", "quen", "morv", "salk", "dreb", "fyn", "gorl", "hask",
    "jilt", "lorn", "nesk", "pravt", "rusk", "tamb", "ulve", "wisp", "yarr", "blen", "crod", "dask",
]  # fmt: skip


def _rows(count: int, seed: int, words: int = 4) -> dict[str, frozenset[str]]:
    rng = random.Random(seed)
    return {
        f"p{number % 7}/e{number:05d}": frozenset(f"w:{word}" for word in rng.sample(WORDS, words))
        | {f"d:{number % 11}", f"f:p{number % 7}"}
        for number in range(count)
    }


def _reference_propose_groups(
    rows,
    *,
    neighbours=3,
    minimum_similarity=0.2,
    maximum_group_size=8,
    maximum_groups=None,
    common_share=0.25,
    minimum_shared=2,
):
    """The proposal holding every scored pair (the reference), with two rules: a feature one
    experiment's neighbourhood can hold is never common, and an edge rests on ``minimum_shared``
    different things, whatever their kind."""
    floor = neighbours + 1
    scored = {
        pair: value
        for pair, value in similarities(rows, common_share=common_share, floor=floor).items()
        if len(_distinct(value[1])) >= minimum_shared
    }
    closest = defaultdict(list)
    for (first, second), (similarity, _) in scored.items():
        closest[first].append((similarity, second))
        closest[second].append((similarity, first))
    top = {
        key: {
            other for _, other in sorted(found, key=lambda item: (-item[0], item[1]))[:neighbours]
        }
        for key, found in closest.items()
    }
    mutual = {
        pair: value
        for pair, value in scored.items()
        if pair[1] in top.get(pair[0], set()) and pair[0] in top.get(pair[1], set())
    }

    def cut(members, floor):
        inside = set(members)
        edges = [
            pair
            for pair, (similarity, _) in mutual.items()
            if similarity >= floor and pair[0] in inside and pair[1] in inside
        ]
        result = []
        for group in _components(members, edges):
            if len(group) < 2:
                continue
            if len(group) > maximum_group_size and floor < 1.0:
                result.extend(cut(group, min(1.0, floor * 1.25 + 0.01)))
            else:
                result.append(group)
        return result

    groups = cut(sorted(rows), minimum_similarity)
    proposed = []
    weight = _weights(rows, common_share, floor)
    for group in groups:
        inside = set(group)
        edges = tuple(
            (pair[0], pair[1], round(similarity, 4), features[:6])
            for pair, (similarity, features) in sorted(mutual.items())
            if pair[0] in inside and pair[1] in inside
        )
        counts = defaultdict(int)
        for member in group:
            for feature in rows[member]:
                if feature in weight:
                    counts[feature] += 1
        half = max(2, math.ceil(len(group) / 2))
        naming = sorted(
            (feature for feature, times in counts.items() if times >= half),
            key=lambda feature: (-weight[feature], feature),
        )
        proposed.append(ProposedGroup(members=tuple(group), name=tuple(naming[:4]), edges=edges))
    proposed.sort(key=lambda g: (-len(g.members), -g.tightness, g.members))
    if not maximum_groups:
        return proposed, 0
    return proposed[:maximum_groups], max(0, len(proposed) - maximum_groups)


def _on_path(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _reference_units(structure, named, *, policy):
    names = set(named)
    if policy == "structure_then_name":
        kept = set(structure)
        kept |= {n for n in names if not any(_on_path(n, s) for s in structure)}
        return sorted(kept)
    if policy == "name_first":
        rest = {s for s in structure if not any(_on_path(s, n) for n in names)}
        return sorted(
            names | {e for e in rest if not any(e.startswith(o + "/") for o in rest if o != e)}
        )
    complete = [path for path, kind in structure.items() if kind == "experiment"]
    containers = {n for n in names if sum(1 for c in complete if c.startswith(n + "/")) >= 2}
    evidence = (set(structure) | names) - containers
    return sorted(e for e in evidence if not any(e.startswith(o + "/") for o in evidence if o != e))


@pytest.mark.parametrize("seed", range(6))
def test_the_proposal_keeping_only_close_pairs_is_the_proposal_holding_every_pair(seed) -> None:
    rows = _rows(60 + 40 * seed, seed, words=3 + seed % 3)
    for settings in ({}, {"neighbours": 1}, {"minimum_shared": 1}, {"maximum_group_size": 3}):
        assert propose_groups(rows, **settings) == _reference_propose_groups(rows, **settings)


def test_the_proposal_holds_what_its_closest_pairs_hold_not_every_pair() -> None:
    rows = _rows(2000, 29)
    tracemalloc.start()
    try:
        propose_groups(rows)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Every pair sharing a weighted feature is several hundred thousand pairs here, over half a
    # gigabyte as the reference holds them; each experiment's closest three and their edges are not.
    assert peak < 64 * 1024 * 1024


def _paths(count: int, seed: int) -> tuple[dict[str, str], list[str]]:
    rng = random.Random(seed)
    folders = [f"{rng.choice(WORDS)}" for _ in range(4)]
    for _ in range(count):
        parent = rng.choice(folders)
        folders.append(f"{parent}/{rng.choice(WORDS)}{rng.randint(0, 9)}")
    kinds = ("experiment", "acquisitions", "analysis")
    structure = {path: rng.choice(kinds) for path in rng.sample(folders, len(folders) // 2)}
    named = rng.sample(folders, len(folders) // 3)
    return structure, named


@pytest.mark.parametrize("seed", range(8))
def test_experiments_placed_by_their_paths_are_the_experiments_placed_pair_by_pair(seed) -> None:
    structure, named = _paths(80 + 30 * seed, seed)
    for policy in ("name_first", "outermost_evidence", "structure_then_name"):
        assert experiment_units(structure, named, policy=policy) == _reference_units(
            structure, named, policy=policy
        )


def _tied(seed: int) -> dict[str, frozenset[str]]:
    """Pools whose pairs share identical feature sets, so the closest few are chosen among ties.

    The shapes: every row drawn from a few shared words; short vocabularies;
    clusters of identical rows; and wider draws, some rows holding nothing.
    """
    rng = random.Random(seed)
    kind = seed % 4
    rows: dict[str, frozenset[str]] = {}
    count = rng.randint(5, 60)
    if kind == 0:
        base = [f"w{index}" for index in range(rng.randint(2, 5))]
        for number in range(count):
            rows[f"e{number:03d}"] = frozenset(rng.sample(base, rng.randint(2, len(base))))
    elif kind == 1:
        words = [f"t{index}" for index in range(rng.randint(3, 8))]
        for number in range(count):
            rows[f"x{rng.randint(0, 999):03d}_{number}"] = frozenset(
                rng.sample(words, rng.randint(1, min(4, len(words))))
            )
    elif kind == 2:
        for cluster in range(rng.randint(2, 8)):
            held = frozenset(f"c{cluster}_{index}" for index in range(rng.randint(2, 4)))
            for member in range(rng.randint(2, 9)):
                rows[f"g{cluster}m{member}"] = held | {f"shared{rng.randint(0, 2)}"}
    else:
        words = [f"v{index}" for index in range(40)]
        for number in range(count):
            rows[f"r{number}"] = frozenset(rng.sample(words, rng.randint(0, 6)))
    return rows


@pytest.mark.parametrize("seed", range(48))
def test_ties_among_the_closest_are_broken_as_the_proposal_holding_every_pair_broke_them(
    seed,
) -> None:
    rows = _tied(seed)
    for neighbours in (1, 2, 3, 5):
        for common_share in (0.25, 1.0):
            for minimum_shared in (1, 2):
                settings = {
                    "neighbours": neighbours,
                    "common_share": common_share,
                    "minimum_shared": minimum_shared,
                }
                assert propose_groups(rows, **settings) == _reference_propose_groups(
                    rows, **settings
                )
