"""Communities of a weighted graph by Louvain, over sparse arrays, seeded.

The Leiden libraries (``leidenalg``, ``python-igraph``) are GPL and the engine is MIT, so the
engine does not use them. The networkx Louvain implementation is permissive but spent
four fifths of a themes draw copying its graph objects (measured: about a second a run on a
thousand documents, fifty-six runs a draw). This module is the same method over scipy's sparse
matrices: local moving in a seeded random order, then aggregation, level after level, maximising
the configuration-model modularity at a resolution
(``sum_ij (A_ij - resolution * k_i k_j / 2m) delta(c_i, c_j)``, the quality that Leiden
maximises). Leiden's guarantee that a community is connected is kept by the caller, which splits
each community into its connected parts (:func:`split_connected`).

The result is deterministic: the same graph, resolution and seed give the same partition, and
ties between communities go to the lowest-numbered one.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["adjacency", "aggregate", "louvain", "split_connected"]

#: A move must gain more than this to be made, so float noise never moves a node back and forth.
GAIN_EPSILON = 1e-12
#: Passes of local moving at one level at most (a guard). A pass that moves nothing ends it.
MAX_PASSES = 100
#: Levels at most (a guard). A level that merges nothing ends it.
MAX_LEVELS = 100


def adjacency(count: int, edges: dict[tuple[int, int], float]) -> Any:
    """A symmetric sparse adjacency from undirected edges ``{(a, b): weight}``, ``a < b``."""
    from scipy import sparse

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for (a, b), weight in sorted(edges.items()):
        rows.extend((a, b))
        cols.extend((b, a))
        vals.extend((weight, weight))
    matrix = sparse.csr_matrix((vals, (rows, cols)), shape=(count, count), dtype=float)
    matrix.sum_duplicates()
    matrix.sort_indices()
    return matrix


def aggregate(matrix: Any, labels: np.ndarray, groups: int) -> Any:
    """The graph of the groups: ``S^T A S``, a group's internal weight on its diagonal."""
    from scipy import sparse

    count = matrix.shape[0]
    member = sparse.csr_matrix(
        (np.ones(count), (np.arange(count), labels)), shape=(count, groups), dtype=float
    )
    combined = (member.T @ matrix @ member).tocsr()
    combined.sum_duplicates()
    combined.sort_indices()
    return combined


def _one_level(
    matrix: Any, resolution: float, generator: np.random.Generator
) -> tuple[np.ndarray, bool]:
    """Local moving until no node gains by moving; each node's community, and whether any moved."""
    count = matrix.shape[0]
    degrees = np.asarray(matrix.sum(axis=1)).ravel()
    total = float(degrees.sum())
    community = list(range(count))
    if total <= 0 or count < 2:
        return np.arange(count), False
    pointers = matrix.indptr.tolist()
    neighbours = matrix.indices.tolist()
    weights = matrix.data.tolist()
    degree = degrees.tolist()
    sums = degrees.tolist()
    order = generator.permutation(count).tolist()
    scale = resolution / total
    moved = False
    for _ in range(MAX_PASSES):
        changed = False
        for node in order:
            own = community[node]
            links: dict[int, float] = {}
            for position in range(pointers[node], pointers[node + 1]):
                other = neighbours[position]
                if other != node:
                    group = community[other]
                    links[group] = links.get(group, 0.0) + weights[position]
            sums[own] -= degree[node]
            best, best_gain = own, links.get(own, 0.0) - scale * degree[node] * sums[own]
            for group in sorted(links):
                gain = links[group] - scale * degree[node] * sums[group]
                if gain > best_gain + GAIN_EPSILON:
                    best, best_gain = group, gain
            sums[best] += degree[node]
            if best != own:
                community[node] = best
                changed = moved = True
        if not changed:
            break
    return np.asarray(community, dtype=int), moved


def louvain(matrix: Any, resolution: float, seed: int) -> np.ndarray:
    """Each node's community (numbered 0.. in order of first appearance), by seeded Louvain."""
    count = matrix.shape[0]
    membership = np.arange(count)
    generator = np.random.default_rng(seed)
    current = matrix
    for _ in range(MAX_LEVELS):
        labels, moved = _one_level(current, resolution, generator)
        if not moved:
            break
        _, renumbered = np.unique(labels, return_inverse=True)
        membership = renumbered[membership]
        groups = int(renumbered.max()) + 1
        if groups == current.shape[0]:
            break
        current = aggregate(current, renumbered, groups)
    _, first_seen = np.unique(membership, return_index=True)
    order = np.argsort(first_seen, kind="stable")
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    _, inverse = np.unique(membership, return_inverse=True)
    return np.asarray(rank[inverse], dtype=int)


def split_connected(matrix: Any, labels: np.ndarray) -> np.ndarray:
    """Each community split into its connected parts: a part's label for every node."""
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components

    coordinates = matrix.tocoo()
    inside = labels[coordinates.row] == labels[coordinates.col]
    within = sparse.csr_matrix(
        (coordinates.data[inside], (coordinates.row[inside], coordinates.col[inside])),
        shape=matrix.shape,
    )
    _, parts = connected_components(within, directed=False)
    return np.asarray(parts, dtype=int)
