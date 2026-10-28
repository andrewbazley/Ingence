"""The themes of a set of documents: deterministic arithmetic over words, and nothing else.

This is the pure core. It knows nothing of the engine's storage: a unit is any identifier and its
document is its words. So the same function draws the themes of a run's experiments
(:func:`ingence.themes.experiment_themes`) and of any other documents a person's script hands it (an
archive's study texts). The steps are:

1. Documents: each unit's words, counted (a list) or weighed (a mapping). A run of words that a
   phrase names (``("single", "particle")``) is read as the phrase's one word first. A word that
   the caller does not know is read as its singular where both occur (``fold_unknown_plurals``).
2. Weights across collections: a unit weighs ``size ** (power - 1)`` where ``size`` is its
   collection's unit count, so a machine-made collection of thousands of near-identical units
   does not drown a small human one. A tiny collection is weighed as if it had
   ``collection_weight_floor`` units, so one item never weighs tens of times another.
3. Vocabulary: a word that the caller marks a sense needs ``min_experiments_sense`` units. A word
   that it does not know needs ``min_experiments_unknown`` units of one collection, each
   co-appearing with another word (unknown words still count). A word in more than ``max_share``
   of the weighted units is context.
4. Identical documents are one vertex carrying its units' weight. The rarity weights and the LSA
   use the weight, and the code never drops it.
5. TF-IDF over weighted document frequency, LSA, and a union nearest-neighbour graph, with the
   point itself excluded by index. Chance is the row-shuffle null, and never a column
   permutation. A neighbour no closer than chance is not joined (``edge_chance_quantile``; blank
   joins every one), and a document whose best similarity to the rest of its community is no
   better than chance is not placed (``chance_quantile``).
6. Communities by Louvain (:mod:`ingence.themes.communities`, seeded, and written here because
   the Leiden library is GPL). Each community is split into its connected parts (Leiden's
   guarantee), at every resolution and every seed. Every seed varies the LSA too.
7. The resolution: a granularity pick that counts only communities of ``min_theme_size`` or more
   units and ``min_theme_documents`` different documents (a text repeated is one document). A
   pick at the edge of the sweep is tried past it, a bounded number of steps
   (``resolution_edge_steps``). Smaller communities are not themes, and their units are not placed.
8. Broad themes from the fine ones: the fine themes' graph, aggregated, is partitioned again, so
   a fine theme lies inside exactly one broad theme.
9. Labels: a class-based TF-IDF. The caller's senses (by canonical name) come first, then the
   spellings of words it does not know.
10. Scores: AMI between seed 0 and each other seed (NMI beside it). Per theme, the mean best
    Jaccard over the other seeds, and its core (the units every seed keeps together).

No model authors anything here: every number is arithmetic with fixed seeds, and the same input
gives the same output byte for byte. Every order is canonical before it is used.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping as MappingType
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ingence.core.settings import ThemeSettings
from ingence.themes.words import plural_folds

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "BroadTheme",
    "DocumentVectors",
    "SweepRow",
    "ThemeAssignment",
    "ThemeResult",
    "ThemeSummary",
    "compute_themes",
    "document_vectors",
    "settings_digest",
]

#: Why a unit has no theme. Words: a person reads them.
NO_WORDS = "no theme words"
TOO_SMALL = "its group is smaller than a theme"
NOT_CLOSE = "its words are no closer to any other experiment's than chance"
#: Said where an experiment was tested only against the rest of its group, and is closer than
#: chance to some experiment outside it.
NOT_CLOSE_TO_GROUP = "its words are no closer to the rest of its group than chance"
REPEATED_TEXT = "its experiments read the same words, which is no theme on its own"
#: The floor of an edge's weight, so a neighbour at no similarity still joins (1e-6).
EDGE_FLOOR = 1e-6
#: The resolutions' common rounding of agreement before a pick compares them (2 decimals).
AGREEMENT_DECIMALS = 2
#: A resolution tried past the sweep's edge, rounded so it reads and repeats exactly.
RESOLUTION_DECIMALS = 6


@dataclass(frozen=True, slots=True)
class ThemeAssignment:
    unit_id: str
    #: The fine theme, or None where the unit is not placed.
    theme_id: int | None
    broad_id: int | None
    label: str | None
    broad_label: str | None
    #: Why it is not placed, in words. None where it is.
    unplaced_reason: str | None


@dataclass(frozen=True, slots=True)
class ThemeSummary:
    theme_id: int
    broad_id: int
    units: int
    label: str
    #: Its distinctive words, display spellings, senses first.
    words: tuple[str, ...]
    #: Which of ``words`` are senses the caller named.
    senses: tuple[str, ...]
    #: Mean over the other seeds of the best Jaccard index any of their communities reaches.
    stability: float
    #: Units every other seed keeps in this theme's best match.
    core_units: int
    members: tuple[str, ...]
    #: How often each of the label's words stays in the label when the experiments are drawn
    #: again with replacement (no refit), one share per label word, and the mean of them. None where
    #: the setting draws none.
    label_support: tuple[float, ...] = ()
    label_stability: float | None = None
    #: Where the other seeds disagree with this theme: in how many of the other seeds its best
    #: match is below ``UNCERTAIN_BELOW`` (the seed does not hold the theme), and, of those seeds,
    #: how many name each other theme as the one this theme's experiments were split with, as
    #: ``(theme, seeds)`` most seeds first. The count comes from the best matches that the
    #: stability already uses and from no other number.
    split_seeds: int = 0
    split_with: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class BroadTheme:
    broad_id: int
    units: int
    label: str
    words: tuple[str, ...]
    themes: tuple[int, ...]
    label_support: tuple[float, ...] = ()
    label_stability: float | None = None


@dataclass(frozen=True, slots=True)
class SweepRow:
    resolution: float
    #: Communities of seed 0 of at least the minimum theme size.
    themes: int
    #: Agreement between seeds, drawn only at the resolutions the pick could choose (in the
    #: readable window, or all where none is) as the sweep grew. None elsewhere.
    seed_ami: float | None
    seed_nmi: float | None


@dataclass(frozen=True, slots=True)
class CollectionWeight:
    collection: str
    units: int
    #: The weight of each of its units. The collection weighs ``units * unit_weight``.
    unit_weight: float


@dataclass(frozen=True, slots=True)
class SeedPair:
    """The adjusted Rand index of two seeds' final placements."""

    seed_a: int
    seed_b: int
    ari: float


@dataclass(frozen=True, slots=True)
class SeedPlacement:
    """One seed's final placement: what it keeps and how like the others it is."""

    seed: int
    themes: int
    #: Experiments it places in a theme (units, not distinct documents).
    placed_units: int
    #: Mean adjusted Rand index of its placement with every other seed's.
    mean_ari: float | None


@dataclass(frozen=True, slots=True)
class ChanceReport:
    """What the shuffled corpus gave, kept beside the themes so a reader sees the cutoffs used.

    Edges are cut at the share of the similarities to each shuffled document's ``edge_neighbors``
    nearest, the neighbour count of the observed graph; placement at the share of each shuffled
    document's one nearest (``placement_neighbors``), as placement tests an experiment's one
    closest. A cutoff is None where its share is blank or nothing could be shuffled."""

    measured: bool
    null_seed: int
    replicates: int
    edge_neighbors: int
    placement_neighbors: int
    #: Shuffled documents pooled (documents times replicates).
    shuffled_documents: int
    quantile_between: float | None
    quantile_within: float | None
    quantile_placement: float | None
    cutoff_between: float | None
    cutoff_within: float | None
    cutoff_placement: float | None


@dataclass(frozen=True)
class ThemeResult:
    assignments: dict[str, ThemeAssignment]
    themes: tuple[ThemeSummary, ...]
    broad: tuple[BroadTheme, ...]
    #: Units not placed, in canonical order.
    unplaced: tuple[str, ...]
    #: AMI between seed 0 and each other seed at the chosen resolution, averaged. NMI beside it.
    seed_ami: float | None
    seed_nmi: float | None
    resolution: float | None
    broad_resolution: float | None
    sweep: tuple[SweepRow, ...]
    units: int
    vertices: int
    vocabulary: int
    collections: tuple[CollectionWeight, ...]
    #: The kept vocabulary's display spellings by token, for a caller's own tables.
    display: dict[str, str] = field(default_factory=dict)
    #: The resolutions tried past the edge of the settings' sweep, ascending (``sweep`` lists
    #: every resolution tried). Empty where the pick lay inside it.
    sweep_extended: tuple[float, ...] = ()
    #: The seed whose placement is shown: the medoid (the seed most like the others by the
    #: adjusted Rand index of the final placements), or seed 0 where the setting asks for it.
    placement_seed: int = 0
    medoid_seed: int | None = None
    #: Each seed's final placement, and all seed pairs' agreement on them (28 for eight seeds).
    seeds: tuple[SeedPlacement, ...] = ()
    seed_pairs: tuple[SeedPair, ...] = ()
    #: The mean of the pairs' adjusted Rand index.
    seed_ari: float | None = None
    chance: ChanceReport | None = None
    #: Themes (and broad themes) not connected in the graph they were drawn from. Always zero.
    disconnected_themes: int = 0
    disconnected_broad: int = 0
    label_method: str = "tfidf"
    label_bootstrap: int = 0

    def to_json(self) -> dict[str, Any]:
        """The result as plain JSON values, canonical: keys sorted where the caller dumps it."""
        return asdict(self)


def settings_digest(settings: ThemeSettings) -> str:
    """A short digest of the settings, for keying a cached projection by what changes it."""
    text = json.dumps(settings.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _phrased(words: Sequence[str], phrases: Mapping[tuple[str, ...], str]) -> list[str]:
    """Words with each run a phrase names replaced by the phrase's word, longest first."""
    if not phrases:
        return list(words)
    longest = max(len(key) for key in phrases)
    out: list[str] = []
    index = 0
    while index < len(words):
        for size in range(min(longest, len(words) - index), 1, -1):
            joined = phrases.get(tuple(words[index : index + size]))
            if joined is not None:
                out.append(joined)
                index += size
                break
        else:
            out.append(words[index])
            index += 1
    return out


def _bags(
    documents: Mapping[str, Sequence[str] | Mapping[str, float]],
    phrases: Mapping[tuple[str, ...], str],
    ignored: frozenset[str],
    senses: Mapping[str, str] | None = None,
    *,
    fold_plurals: bool = False,
) -> dict[str, dict[str, float]]:
    read: dict[str, list[tuple[str, float]]] = {}
    for unit in sorted(documents):
        document = documents[unit]
        if isinstance(document, MappingType):
            read[unit] = [
                (str(word), float(weight))
                for word, weight in sorted(document.items())
                if word and word not in ignored and float(weight) > 0
            ]
        else:
            read[unit] = [
                (word, 1.0)
                for word in _phrased([str(w) for w in document], phrases)
                if word and word not in ignored
            ]
    # A word that the caller does not know and its regular plural are one word within the
    # documents, so a word and its plural do not both appear in one label.
    folds = (
        plural_folds({word for pairs in read.values() for word, _ in pairs}, senses or {})
        if fold_plurals
        else {}
    )
    bags: dict[str, dict[str, float]] = {}
    for unit, pairs in read.items():
        counted: dict[str, float] = defaultdict(float)
        for word, weight in pairs:
            counted[folds.get(word, word)] += weight
        bags[unit] = dict(sorted(counted.items()))
    return bags


def _unit_weights(
    units: Sequence[str], collections: Mapping[str, str] | None, power: float, floor: int = 1
) -> tuple[dict[str, float], dict[str, str], tuple[CollectionWeight, ...]]:
    """Each unit's weight (mean 1 over the units), its collection, and each collection's weight;
    a collection of fewer than ``floor`` units is weighed as if it had ``floor``."""
    belongs = {unit: (collections or {}).get(unit, "") for unit in units}
    sizes = Counter(belongs.values())

    def each(name: str) -> float:
        return float(float(max(sizes[name], floor)) ** (power - 1.0))

    raw = {unit: each(belongs[unit]) for unit in units}
    mean = sum(raw.values()) / len(raw) if raw else 1.0
    weights = {unit: value / mean for unit, value in raw.items()}
    summary = tuple(
        CollectionWeight(
            collection=name, units=sizes[name], unit_weight=round(each(name) / mean, 9)
        )
        for name in sorted(sizes)
    )
    return weights, belongs, summary


def _vocabulary(
    bags: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, float],
    belongs: Mapping[str, str],
    senses: Mapping[str, str],
    settings: ThemeSettings,
) -> list[str]:
    """The kept words, sorted (an unknown word is counted per collection)."""
    holding: dict[str, list[str]] = defaultdict(list)
    for unit, bag in bags.items():
        for word in bag:
            holding[word].append(unit)
    total = sum(weights[unit] for unit in bags) or 1.0
    kept: list[str] = []
    for word in sorted(holding):
        units = holding[word]
        weighted = sum(weights[unit] for unit in units) / total
        if weighted > settings.max_share and len(units) > 2:
            continue
        if word in senses:
            if len(units) >= settings.min_experiments_sense:
                kept.append(word)
            continue
        # A word the caller does not know: frequent within one collection, each time beside
        # another word (a unit whose only word it is shows no co-appearance).
        together = Counter(belongs[unit] for unit in units if len(bags[unit]) > 1)
        if together and max(together.values()) >= settings.min_experiments_unknown:
            kept.append(word)
    return kept


# ---------------------------------------------------------------------------
# Embedding, graph, communities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Vertices:
    """Distinct documents: each one's units, weight, and row of the count matrix."""

    units: tuple[tuple[str, ...], ...]
    mass: np.ndarray
    matrix: Any  # scipy.sparse.csr_matrix, vertices x vocabulary


def _vertices(
    bags: Mapping[str, Mapping[str, float]],
    vocabulary: Sequence[str],
    weights: Mapping[str, float],
) -> tuple[_Vertices, list[str]]:
    from scipy import sparse

    column = {word: index for index, word in enumerate(vocabulary)}
    groups: dict[tuple[tuple[int, float], ...], list[str]] = {}
    empty: list[str] = []
    for unit in sorted(bags):
        row = tuple(sorted((column[w], round(v, 9)) for w, v in bags[unit].items() if w in column))
        if not row:
            empty.append(unit)
            continue
        groups.setdefault(row, []).append(unit)
    # Vertices are ordered by their content and never by an identifier. Identifiers may hold a
    # pool's absolute path, so ordering by the first unit's would give the same words in a
    # differently named folder a different row order, and the LSA, Louvain's visiting order and
    # the resolution pick would follow. A group's key (its columns and counts) is unique.
    ordered = sorted(groups.items(), key=lambda item: item[0])
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for index, (key, _members) in enumerate(ordered):
        for col, value in key:
            rows.append(index)
            cols.append(col)
            vals.append(value)
    matrix = sparse.csr_matrix(
        (vals, (rows, cols)), shape=(len(ordered), len(vocabulary)), dtype=float
    )
    mass = np.array([math.fsum(weights[u] for u in members) for _, members in ordered], dtype=float)
    return _Vertices(tuple(tuple(m) for _, m in ordered), mass, matrix), empty


@dataclass(frozen=True)
class _Prepared:
    """What the documents become before any vector: the units, the kept words, the vertices."""

    units: list[str]
    belongs: dict[str, str]
    collection_weights: tuple[CollectionWeight, ...]
    vocabulary: list[str]
    vertices: _Vertices
    #: Units with no kept word: no vertex, no vector.
    empty: list[str]


def _prepare(
    documents: Mapping[str, Sequence[str] | Mapping[str, float]],
    collections: Mapping[str, str] | None,
    senses: Mapping[str, str],
    phrases: Mapping[tuple[str, ...], str] | None,
    settings: ThemeSettings,
) -> _Prepared:
    """The steps both the themes and the map begin with, in one place so they cannot drift."""
    bags = _bags(
        documents,
        dict(phrases or {}),
        frozenset(settings.ignored_words),
        senses,
        fold_plurals=settings.fold_unknown_plurals,
    )
    units = sorted(bags)
    weights, belongs, collection_weights = _unit_weights(
        units, collections, settings.collection_weight_power, settings.collection_weight_floor
    )
    vocabulary = _vocabulary(bags, weights, belongs, senses, settings)
    vertices, empty = _vertices(bags, vocabulary, weights)
    return _Prepared(units, belongs, collection_weights, vocabulary, vertices, empty)


def _tfidf(vertices: _Vertices, *, sublinear: bool) -> Any:
    """Rarity-weighted counts, rows L2-normalised; rarity by weighted document frequency."""
    from scipy import sparse

    counts = vertices.matrix.copy()
    if sublinear:
        counts.data = 1.0 + np.log(counts.data)
    present = (vertices.matrix > 0).astype(float)
    weighted_df = np.asarray(present.T @ vertices.mass).ravel()
    total = float(vertices.mass.sum())
    idf = np.log((1.0 + total) / (1.0 + weighted_df)) + 1.0
    scaled = counts @ sparse.diags(idf)
    norms = np.sqrt(np.asarray(scaled.multiply(scaled).sum(axis=1)).ravel())
    norms[norms == 0] = 1.0
    return sparse.diags(1.0 / norms) @ scaled


#: The fewest LSA dimensions where the setting is blank.
LSA_FLOOR = 50


def lsa_dimensions(setting: int | None, distinct: int) -> int:
    """The dimensions asked of the LSA: the setting, or ``max(50, 2 * ceil(1.5 * sqrt(n)))``."""
    if setting is not None:
        return setting
    return max(LSA_FLOOR, 2 * math.ceil(1.5 * math.sqrt(max(distinct, 1))))


def _embedding(tfidf: Any, mass: np.ndarray, dimensions: int, seed: int) -> np.ndarray:
    """LSA fitted on rows weighed by their mass, every row projected, rows L2-normalised."""
    from scipy import sparse
    from sklearn.decomposition import TruncatedSVD

    vertices, words = tfidf.shape
    k = max(1, min(dimensions, vertices - 1, words - 1))
    if k < 2 or vertices < 3:
        dense = np.asarray(tfidf.todense(), dtype=float)
    else:
        svd = TruncatedSVD(n_components=k, random_state=seed)
        svd.fit(sparse.diags(np.sqrt(mass)) @ tfidf)
        dense = np.asarray(svd.transform(tfidf), dtype=float)
    norms = np.linalg.norm(dense, axis=1)
    norms[norms == 0] = 1.0
    return np.asarray(dense / norms[:, None], dtype=float)


@dataclass(frozen=True)
class DocumentVectors:
    """Each distinct document's vector in the space the themes were drawn in, for one seed.

    The map of groups (:mod:`ingence.themes.mapxy`) is drawn over these, so a point lies where the
    themes see it. A document read twice is one row carrying both units.
    """

    #: The units of each row, rows ordered by their content (never by an identifier).
    units: tuple[tuple[str, ...], ...]
    #: One L2-normalized LSA row per entry of ``units``.
    vectors: np.ndarray
    #: Units with no kept word: no row, so no place.
    empty: tuple[str, ...]
    #: The seed the LSA was fitted with (``ThemeResult.placement_seed`` for the shown placement).
    seed: int


def document_vectors(
    documents: Mapping[str, Sequence[str] | Mapping[str, float]],
    *,
    collections: Mapping[str, str] | None = None,
    senses: Mapping[str, str] | None = None,
    phrases: Mapping[tuple[str, ...], str] | None = None,
    settings: ThemeSettings | None = None,
    seed: int = 0,
) -> DocumentVectors:
    """The LSA vectors :func:`compute_themes` draws seed ``seed``'s themes over.

    The same preparation as ``compute_themes`` (:func:`_prepare`), the same rarity weights and the
    same fitted LSA, so the same arguments give the same rows, byte for byte.
    """
    settings = settings or ThemeSettings()
    prepared = _prepare(documents, collections, dict(senses or {}), phrases, settings)
    vertices = prepared.vertices
    distinct = len(vertices.units)
    if distinct == 0:
        return DocumentVectors((), np.zeros((0, 0)), tuple(prepared.empty), seed)
    tfidf = _tfidf(vertices, sublinear=settings.sublinear_counts)
    dimensions = lsa_dimensions(settings.lsa_dimensions, distinct)
    vectors = _embedding(tfidf, vertices.mass, dimensions, seed)
    return DocumentVectors(vertices.units, vectors, tuple(prepared.empty), seed)


#: How many nearest neighbours of each shuffled document every cutoff was taken over in earlier
#: versions (the archive's study themes: ten). ``chance_null = "nearest_ten"`` keeps it.
LEGACY_NULL_NEIGHBOURS = 10

#: Rows of a similarity matrix computed at once, so memory stays a band, never the square.
SIMILARITY_ROWS = 512


def _nearest(embedded: np.ndarray, k: int) -> list[list[tuple[int, float]]]:
    """Each row's ``k`` most similar other rows by cosine, best first; ties by the lower index,
    whatever a sort's internals do. The row itself is excluded by its index."""
    count = embedded.shape[0]
    k = max(0, min(k, count - 1))
    found: list[list[tuple[int, float]]] = []
    for start in range(0, count, SIMILARITY_ROWS):
        band = np.clip(embedded[start : start + SIMILARITY_ROWS] @ embedded.T, -1.0, 1.0)
        for offset, row in enumerate(band):
            i = start + offset
            row = row.copy()
            row[i] = -np.inf
            if k == 0:
                found.append([])
                continue
            kth = np.partition(row, count - k)[count - k]
            candidates = np.flatnonzero(row >= kth)
            order = candidates[np.lexsort((candidates, -row[candidates]))][:k]
            found.append([(int(j), float(row[j])) for j in order])
    return found


def _chance_rows(
    vertices: _Vertices,
    dimensions: int,
    seed: int,
    *,
    sublinear: bool,
    neighbours: int,
    replicates: int,
) -> list[list[float]]:
    """The similarity chance gives: the row-shuffle null.

    Each document keeps its number of distinct words and their counts and draws which words they
    are, without replacement, in proportion to how many distinct documents use each. The shuffled
    documents are embedded the same way, and chance is each one's similarities to its nearest
    ``neighbours``, best first, one row per shuffled document. ``replicates`` shuffles are drawn,
    with seeds ``seed``, ``seed + 1``, and so on, and their rows pooled. The caller takes the
    whole rows for the graph's edges (the observed graph joins each document to ``neighbours``)
    and the first similarity of each for placement (the observed test is of a document's one
    closest). The null is never a column permutation, which renames the words and leaves every
    similarity as it was.
    """
    from scipy import sparse

    matrix = vertices.matrix.tocsr()
    # Each distinct document counts once in the draw: weighed by mass, a collection of thousands
    # of near-identical documents makes every shuffled document draw its words, and chance then
    # looks as close as a theme (measured on the generated pool of tests/test_themes_core.py).
    frequency = np.asarray((matrix > 0).sum(axis=0), dtype=float).ravel()
    if frequency.sum() <= 0 or matrix.shape[0] < 3:
        return []
    chances = frequency / frequency.sum()
    rows_out: list[list[float]] = []
    for replicate in range(replicates):
        generator = np.random.default_rng(seed + replicate)
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []
        for row in range(matrix.shape[0]):
            start, end = matrix.indptr[row], matrix.indptr[row + 1]
            drawn = generator.choice(matrix.shape[1], size=end - start, replace=False, p=chances)
            rows.extend([row] * (end - start))
            cols.extend(int(c) for c in drawn)
            vals.extend(float(v) for v in matrix.data[start:end])
        shuffled = _Vertices(
            vertices.units,
            vertices.mass,
            sparse.csr_matrix((vals, (rows, cols)), shape=matrix.shape, dtype=float),
        )
        embedded = _embedding(
            _tfidf(shuffled, sublinear=sublinear), shuffled.mass, dimensions, seed + replicate
        )
        rows_out.extend([s for _, s in row] for row in _nearest(embedded, neighbours))
    return rows_out


def _graph(
    embedded: np.ndarray,
    neighbours: int,
    *,
    within: float = -1.0,
    between: float = -1.0,
    groups: Sequence[frozenset[str]] = (),
    loops: Sequence[float] = (),
) -> Any:
    """The union nearest-neighbour graph by cosine, the point itself excluded by its index.

    An edge between two documents of one collection is kept where its similarity beats
    ``within``; between collections, where it beats ``between`` (``groups``: each vertex's
    collections; vertices sharing one are of one collection). ``loops``: each vertex's weight in
    the graph, a self-loop standing for the edges among the identical documents it gathers.
    """
    from ingence.themes.communities import adjacency

    edges: dict[tuple[int, int], float] = {}
    for i, row in enumerate(_nearest(embedded, neighbours)):
        for j, similarity in row:
            same = not groups or bool(groups[i] & groups[j])
            if similarity <= (within if same else between):
                continue
            a, b = (i, j) if i < j else (j, i)
            edges[(a, b)] = max(similarity, EDGE_FLOOR)
    for vertex, loop in enumerate(loops):
        if loop > 0:
            # adjacency() counts an edge in both directions, a self-loop twice on the diagonal.
            edges[(vertex, vertex)] = loop / 2.0
    return adjacency(embedded.shape[0], edges)


def _closest_within(embedded: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Each vertex's best similarity to another vertex of its own community, NaN where it is alone.

    A vertex compared with nothing has no similarity, and the function never reads it as one below
    chance. Without the NaN, a lone vertex would score -1, below every threshold."""
    best = np.full(embedded.shape[0], np.nan)
    groups: dict[int, list[int]] = defaultdict(list)
    for vertex, label in enumerate(labels.tolist()):
        groups[label].append(vertex)
    for members in groups.values():
        if len(members) < 2:
            continue
        inside = np.array(members)
        for start in range(0, len(inside), SIMILARITY_ROWS):
            rows = inside[start : start + SIMILARITY_ROWS]
            band = embedded[rows] @ embedded[inside].T
            for offset in range(len(rows)):
                band[offset, start + offset] = -np.inf
            best[rows] = band.max(axis=1)
    return best


def _closest_overall(embedded: np.ndarray, vertices: Sequence[int]) -> np.ndarray:
    """Each listed vertex's best similarity to any other vertex (NaN where there is no other)."""
    best = np.full(len(vertices), np.nan)
    count = embedded.shape[0]
    if count < 2:
        return best
    for start in range(0, len(vertices), SIMILARITY_ROWS):
        chunk = np.asarray(vertices[start : start + SIMILARITY_ROWS], dtype=int)
        band = np.clip(embedded[chunk] @ embedded.T, -1.0, 1.0)
        band[np.arange(len(chunk)), chunk] = -np.inf
        best[start : start + len(chunk)] = band.max(axis=1)
    return best


def _canonical(parts_of: np.ndarray, sizes: Sequence[int]) -> np.ndarray:
    """Groups numbered canonically: most units first, then by the smallest vertex."""
    members: dict[int, list[int]] = defaultdict(list)
    for vertex, part in enumerate(parts_of.tolist()):
        members[part].append(vertex)
    parts = sorted(members.values(), key=lambda part: (-sum(sizes[v] for v in part), part[0]))
    labels = np.empty(len(parts_of), dtype=int)
    for number, part in enumerate(parts):
        labels[part] = number
    return labels


def _communities(graph: Any, resolution: float, seed: int, sizes: Sequence[int]) -> np.ndarray:
    """Louvain at one resolution and seed, each community split into its connected parts,
    numbered canonically: most units first, then by the smallest vertex."""
    from ingence.themes.communities import louvain, split_connected

    if graph.shape[0] == 0:
        return np.zeros(0, dtype=int)
    return _canonical(split_connected(graph, louvain(graph, resolution, seed)), sizes)


def _agreement(left: np.ndarray, right: np.ndarray) -> tuple[float, float]:
    from sklearn.metrics import adjusted_mutual_info_score, normalized_mutual_info_score

    if len(left) < 2:
        return 1.0, 1.0
    return (
        float(adjusted_mutual_info_score(left, right)),
        float(normalized_mutual_info_score(left, right)),
    )


def _big(labels: np.ndarray, sizes: Sequence[int], minimum: int, texts: int = 1) -> list[int]:
    """The communities of at least ``minimum`` units and ``texts`` different documents."""
    totals: dict[int, int] = defaultdict(int)
    distinct: dict[int, int] = defaultdict(int)
    for vertex, label in enumerate(labels.tolist()):
        totals[label] += sizes[vertex]
        distinct[label] += 1
    return sorted(
        label for label, total in totals.items() if total >= minimum and distinct[label] >= texts
    )


def _window(counted: Mapping[float, int], distinct: int) -> set[float]:
    """The resolutions whose theme count lies in the readable window, or all where none."""
    low = max(3.0, 0.5 * math.sqrt(distinct))
    high = max(4.0, 1.5 * math.sqrt(distinct))
    inside = {resolution for resolution, themes in counted.items() if low <= themes <= high}
    return inside or set(counted)


def _measure(row: SweepRow) -> tuple[float, int]:
    """The pick's measure of a resolution: its seed agreement, rounded, then its theme count."""
    return round(row.seed_ami or 0.0, AGREEMENT_DECIMALS), row.themes


def _pick(
    sweep: Sequence[SweepRow], candidates: set[float], offered: Sequence[float] = ()
) -> SweepRow:
    """The granularity pick: in the readable window, the most stable, then the finer. A tie goes
    to a resolution offered (``offered``) before one tried past the sweep's edge."""
    given = set(offered)
    window = [row for row in sweep if row.resolution in candidates and row.seed_ami is not None]
    window = [r for r in window if r.resolution in given] + [
        r for r in window if r.resolution not in given
    ]
    best = window[0]
    for row in window[1:]:
        if _measure(row) > _measure(best):
            best = row
    return best


def _past_the_edge(
    sweep: Sequence[SweepRow], chosen: SweepRow, finer: int, coarser: int
) -> float | None:
    """The next resolution past the edge the pick lies on.

    The step is the ratio between the two outermost resolutions tried there. The function takes
    it only where the pick beats its inner neighbour by the pick's own measure (or the neighbour
    could not be picked). It returns None where the pick lies inside, where it only ties, where
    no step is left, or where the step would not be above zero."""
    ordered = sorted(sweep, key=lambda row: row.resolution)
    if len(ordered) < 2:
        return None
    if chosen.resolution == ordered[-1].resolution and finer > 0:
        inner, step = ordered[-2], ordered[-1].resolution / ordered[-2].resolution
    elif chosen.resolution == ordered[0].resolution and coarser > 0:
        inner, step = ordered[1], ordered[0].resolution / ordered[1].resolution
    else:
        return None
    if inner.seed_ami is not None and _measure(chosen) <= _measure(inner):
        return None
    beyond = round(chosen.resolution * step, RESOLUTION_DECIMALS)
    # A step rounded to nothing is no resolution (offered ones near zero).
    return beyond if beyond > 0 else None


def _broad(
    graph: Any,
    fine: Mapping[int, int],
    themes: int,
    resolutions: Sequence[float],
    sizes: Sequence[int],
) -> tuple[dict[int, int], float | None]:
    """Each fine theme's broad theme, from the fine themes' aggregated graph (nested).

    A broad theme is connected. The function splits each group that Louvain forms over the
    aggregated graph into its connected parts, as the fine themes' communities are, so a broad
    theme never joins fine themes that no edge of the graph links."""
    from ingence.themes.communities import aggregate, louvain, split_connected

    target = max(3, min(8, round(themes / 3)))
    if themes <= target:
        return {theme: theme for theme in range(themes)}, None
    placed = sorted(fine)
    labels = np.array([fine[v] for v in placed], dtype=int)
    aggregated = aggregate(graph[placed][:, placed], labels, themes)
    theme_sizes = [0] * themes
    for vertex, theme in fine.items():
        theme_sizes[theme] += sizes[vertex]
    best: tuple[int, float, np.ndarray] | None = None
    for resolution in resolutions:
        found = split_connected(aggregated, louvain(aggregated, resolution, 0))
        distance = abs(len(set(found.tolist())) - target)
        if best is None or distance < best[0]:
            best = (distance, resolution, found)
    assert best is not None
    grouped: dict[int, list[int]] = defaultdict(list)
    for theme, group in enumerate(best[2].tolist()):
        grouped[group].append(theme)
    parts = sorted(grouped.values(), key=lambda part: (-sum(theme_sizes[t] for t in part), part[0]))
    return {theme: number for number, part in enumerate(parts) for theme in part}, best[1]


def _disconnected(graph: Any, groups: Mapping[int, int]) -> int:
    """How many groups of vertices (``{vertex: group}``) are not one connected piece of the graph
    (an edge counts only between two vertices of one group)."""
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components

    if not groups:
        return 0
    count = graph.shape[0]
    label = np.arange(count, dtype=int) + len(set(groups.values()))
    ids = {group: number for number, group in enumerate(sorted(set(groups.values())))}
    for vertex, group in groups.items():
        label[vertex] = ids[group]
    coordinates = graph.tocoo()
    inside = (label[coordinates.row] == label[coordinates.col]) & (
        coordinates.row != coordinates.col
    )
    within = sparse.csr_matrix(
        (coordinates.data[inside], (coordinates.row[inside], coordinates.col[inside])),
        shape=graph.shape,
    )
    _, parts = connected_components(within, directed=False)
    pieces: dict[int, set[int]] = defaultdict(set)
    for vertex, group in groups.items():
        pieces[group].add(int(parts[vertex]))
    return sum(1 for found in pieces.values() if len(found) > 1)


# ---------------------------------------------------------------------------
# The final placement of one seed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Final:
    """One seed's final placement, by the same steps for every seed."""

    #: Each vertex's group once the far ones are out and every group is split into its connected
    #: parts, numbered canonically.
    labels: np.ndarray
    #: Vertices tested against chance and found no closer (to the rest of their group, or where
    #: alone in it, to any other).
    far: np.ndarray
    #: Of those, the vertices whose best similarity to any other vertex is no better than
    #: chance, so that "no closer than chance to any other experiment" is true of them.
    far_overall: np.ndarray
    #: Each vertex's theme number (most units first), or -1 where it is in none.
    theme: np.ndarray
    themes: int

    def placed_labels(self) -> np.ndarray:
        """A label per vertex for comparing placements: its theme, or a label of its own where it
        is in none (an experiment left out agrees with nothing)."""
        count = len(self.theme)
        return np.where(self.theme >= 0, self.theme, -1 - np.arange(count))


def _finalise(
    embedded: np.ndarray,
    graph: Any,
    raw: np.ndarray,
    sizes: Sequence[int],
    settings: ThemeSettings,
    threshold: float,
    *,
    measured: bool,
) -> _Final:
    """The placement a community partition ends as.

    What cannot be placed is flagged and never forced. The groups left are split into their
    connected parts (taking a document out can cut one in two), and a group smaller than a theme
    is not a theme.

    A document is flagged where chance was measured and its best similarity to another of its
    group is no better than chance. A document alone in its group is tested on its best
    similarity to any other document. A document with nothing to compare with, or closer than
    chance and merely left alone by the communities, is too small. The function never calls it
    "no closer than chance"."""
    from ingence.themes.communities import split_connected

    closest = _closest_within(embedded, raw)
    far = np.zeros(len(raw), dtype=bool)
    if measured:
        far = np.isfinite(closest) & (closest <= threshold)
        lone = np.flatnonzero(np.isnan(closest)).tolist()
        for vertex, best in zip(lone, _closest_overall(embedded, lone), strict=True):
            far[vertex] = bool(np.isfinite(best) and best <= threshold)
    # What is told of a flagged document is what was measured: a document closer than chance to
    # some document outside its group is no reason to say it is no closer than chance to any.
    far_overall = np.zeros(len(raw), dtype=bool)
    flagged = np.flatnonzero(far).tolist()
    for vertex, best in zip(flagged, _closest_overall(embedded, flagged), strict=True):
        far_overall[vertex] = bool(np.isfinite(best) and best <= threshold)
    labels = raw.copy()
    fresh = int(labels.max()) + 1 if len(labels) else 0
    for vertex in np.flatnonzero(far).tolist():
        labels[vertex] = fresh
        fresh += 1
    # A repeated text is never a theme on its own. Identical documents are one vertex, and a
    # community of one vertex is not a theme however many copies it holds. Its units are listed as
    # not placed and never forced into a neighbour.
    labels = _canonical(split_connected(graph, labels), sizes)
    big = _big(labels, sizes, settings.min_theme_size, settings.min_theme_documents)
    number = {label: index for index, label in enumerate(big)}
    theme = np.array([number.get(label, -1) for label in labels.tolist()], dtype=int)
    return _Final(labels, far, far_overall, theme, len(big))


# ---------------------------------------------------------------------------
# Labels and stability
# ---------------------------------------------------------------------------

#: A floor under a count in a log, so a word that fills its whole background never takes log(0).
LOG_FLOOR = 1e-12


def _scores(
    counts: Any, groups: Sequence[Sequence[int]], method: str, strength: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each group's score for every word, the group's counts of every word, and each word's
    count over all rows: ``(score, sub, total)``, one row per group.

    ``tfidf`` is a class-based TF-IDF: the share of the group's weight a word has times
    ``log(1 + groups * sub / total)``. ``log_odds`` is the z-score of the log-odds ratio between
    the group and the rest of the rows, with a Dirichlet prior of ``strength`` counts spread by
    each word's share of all rows (Monroe, Colaresi and Quinn, 2008). ``counts`` holds the rows
    the caller weighs (``tfidf`` on weights, ``log_odds`` on how many experiments hold a word)."""
    from scipy import sparse

    rows = counts.shape[0]
    member = sparse.csr_matrix(
        (
            np.ones(sum(len(g) for g in groups)),
            (
                [i for i, g in enumerate(groups) for _ in g],
                [r for g in groups for r in g],
            ),
        ),
        shape=(len(groups), rows),
        dtype=float,
    )
    sub = np.asarray((member @ counts).todense(), dtype=float)
    total = np.asarray(counts.sum(axis=0)).ravel()
    if method == "log_odds":
        everything = float(total.sum())
        prior = strength * total / everything if everything > 0 else np.zeros_like(total)
        inside = sub.sum(axis=1, keepdims=True)
        outside_counts = total[None, :] - sub
        outside = everything - inside
        a = sub + prior
        b = outside_counts + prior
        delta = np.log(np.maximum(a, LOG_FLOOR) / np.maximum(inside + strength - a, LOG_FLOOR))
        delta -= np.log(np.maximum(b, LOG_FLOOR) / np.maximum(outside + strength - b, LOG_FLOOR))
        variance = 1.0 / np.maximum(a, LOG_FLOOR) + 1.0 / np.maximum(b, LOG_FLOOR)
        return delta / np.sqrt(variance), sub, total + 1e-9
    total = total + 1e-9
    mass = sub.sum(axis=1, keepdims=True)
    mass[mass <= 0] = 1.0
    return (sub / mass) * np.log1p(len(groups) * sub / total[None, :]), sub, total


def _top(score: np.ndarray, present: np.ndarray, limit: int | None) -> np.ndarray:
    """Indices of the present words, best score first, ties by the word (the vocabulary is
    sorted, so by index), at most ``limit``."""
    index = np.flatnonzero(present)
    if len(index) == 0:
        return index
    order = np.lexsort((index, -np.round(score[index], 12)))
    return index[order[:limit]] if limit is not None else index[order]


def _distinctive(
    matrix: Any,
    groups: Mapping[int, Sequence[int]],
    vocabulary: Sequence[str],
    method: str = "tfidf",
    strength: float = 100.0,
) -> dict[int, list[tuple[str, float]]]:
    """Each group's words by the ranking ``method`` chose, best first, ties by the word; each
    with its concentration, the share of the word's whole count that lies in the group."""
    ordered = sorted(groups.items())
    if not ordered:
        return {}
    score, sub, total = _scores(matrix, [list(rows) for _, rows in ordered], method, strength)
    ranked: dict[int, list[tuple[str, float]]] = {}
    for position, (group, _) in enumerate(ordered):
        if sub[position].sum() <= 0:
            ranked[group] = []
            continue
        order = _top(score[position], sub[position] > 0, None)
        ranked[group] = [
            (vocabulary[index], float(sub[position, index] / total[index])) for index in order
        ]
    return ranked


def _label_tokens(
    ranked: Sequence[tuple[str, float]], senses: Mapping[str, str], settings: ThemeSettings
) -> list[str]:
    """The words a label is chosen from, in order: senses by canonical name first where
    concentrated in the theme, then the rest in order of distinctiveness."""
    top = list(ranked[: max(settings.theme_words * 2, settings.theme_words)])
    leading = [w for w, share in top if w in senses and share >= settings.label_sense_specificity]
    rest = [w for w, _ in top if w not in leading]
    return (leading + rest)[: settings.theme_words]


def _label(
    ranked: Sequence[tuple[str, float]], senses: Mapping[str, str], settings: ThemeSettings
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """A theme's words (display spellings), which are senses, and its label, the first few."""
    chosen = _label_tokens(ranked, senses, settings)
    shown = tuple(_display(word, senses) for word in chosen)
    sense_names = tuple(_display(word, senses) for word in chosen if word in senses)
    return shown, sense_names, " · ".join(shown[: settings.label_words])


def _display(word: str, senses: Mapping[str, str]) -> str:
    name = senses.get(word, word)
    return name.lstrip("§").replace("_", " ").strip()


def _label_support(
    base: Any,
    sizes: Sequence[int],
    groups: Mapping[int, Sequence[int]],
    vocabulary: Sequence[str],
    senses: Mapping[str, str],
    settings: ThemeSettings,
    labelled: Mapping[int, Sequence[str]],
) -> dict[int, tuple[float, ...]]:
    """How steady each label is under drawing the experiments again.

    The experiments are drawn with replacement ``label_bootstrap`` times (each distinct document
    in proportion to its experiments), every one keeping its theme, with no refit. Each draw ranks
    the words afresh by the same method and forms the labels by the same rule. A label word's
    support is the share of the draws in which it is among the label's words. The function is
    deterministic: the draws use a fixed seed."""
    draws = settings.label_bootstrap
    ordered = sorted(groups.items())
    if draws <= 0 or not ordered:
        return {}
    weights = np.asarray(sizes, dtype=float)
    experiments = int(weights.sum())
    if experiments <= 0:
        return {}
    generator = np.random.default_rng(0)
    shown = settings.label_words
    held: dict[int, dict[str, int]] = {
        group: dict.fromkeys(labelled.get(group, ())[:shown], 0) for group, _ in ordered
    }
    members = [list(rows) for _, rows in ordered]
    for _ in range(draws):
        again = generator.multinomial(experiments, weights / weights.sum()).astype(float)
        counts = base.multiply(again[:, None]).tocsr()
        score, sub, total = _scores(
            counts, members, settings.label_method, settings.label_prior_strength
        )
        for position, (group, _) in enumerate(ordered):
            if sub[position].sum() <= 0:
                continue
            order = _top(score[position], sub[position] > 0, max(settings.theme_words * 2, 1))
            ranked = [
                (vocabulary[index], float(sub[position, index] / total[index])) for index in order
            ]
            kept = set(_label_tokens(ranked, senses, settings)[:shown])
            for word in held[group]:
                if word in kept:
                    held[group][word] += 1
    return {
        group: tuple(
            round(held[group][w] / draws, 6) for w in list(labelled.get(group, ()))[:shown]
        )
        for group, _ in ordered
    }


#: A theme is held by a seed where that seed's best match for it overlaps it at least this much
#: (Jaccard). Below it the boundary is called uncertain, as the Dashboard's stability axis draws it.
UNCERTAIN_BELOW = 0.8
#: The other themes a theme is said to split with, most seeds first, at most this many.
SPLIT_PARTNERS_KEPT = 5


def _best_matches(
    others: Sequence[np.ndarray], members: Sequence[int]
) -> list[tuple[float, set[int], np.ndarray]]:
    """Per other seed, the best Jaccard any of its communities reaches with a theme, that
    community's vertices, and the seed's labels."""
    theme = set(members)
    matches: list[tuple[float, set[int], np.ndarray]] = []
    for labels in others:
        best_score, best_set = 0.0, set()
        for label in sorted(set(labels[list(theme)].tolist())):
            candidate = set(np.flatnonzero(labels == label).tolist())
            score = len(theme & candidate) / len(theme | candidate)
            if score > best_score:
                best_score, best_set = score, candidate
        matches.append((best_score, best_set, labels))
    return matches


def _stability(
    matches: Sequence[tuple[float, set[int], np.ndarray]], members: Sequence[int]
) -> tuple[float, list[int]]:
    """Mean best Jaccard of a theme over the other seeds, and the vertices every seed keeps."""
    theme = set(members)
    if not matches:
        return 1.0, sorted(theme)
    core = set(theme)
    for _, best_set, _ in matches:
        core &= best_set
    return float(np.mean([score for score, _, _ in matches])), sorted(core)


def _split_with(
    reference_theme: np.ndarray,
    matches: Sequence[tuple[float, set[int], np.ndarray]],
    members: Sequence[int],
    mine: int,
) -> tuple[int, tuple[tuple[int, int], ...]]:
    """How many other seeds do not hold a theme, and which other themes they split it with.

    A seed does not hold the theme where its best match overlaps it below
    :data:`UNCERTAIN_BELOW`. In such a seed the experiments are split between this theme and
    another when what the seed put with it (its best match) or what it moved away (the community
    taking most of the theme's experiments the best match left out) holds experiments of that
    other theme of the shown placement; the one with most experiments there is the seed's partner.
    A seed that splits the theme among its own experiments names none. ``reference_theme`` is the
    shown placement's theme per vertex (-1 where none).
    """
    theme = set(members)
    held_below = 0
    partners: Counter[int] = Counter()
    for score, best_set, labels in matches:
        if score >= UNCERTAIN_BELOW:
            continue
        held_below += 1
        moved = theme - best_set
        where: set[int] = set(best_set)
        if moved:
            taken = Counter(labels[sorted(moved)].tolist()).most_common(1)[0][0]
            where |= set(np.flatnonzero(labels == taken).tolist())
        others_here = Counter(
            int(reference_theme[v]) for v in where - theme if reference_theme[v] >= 0
        )
        others_here.pop(mine, None)
        if others_here:
            top = max(others_here.values())
            partners[min(t for t, n in others_here.items() if n == top)] += 1
    ordered = sorted(partners.items(), key=lambda item: (-item[1], item[0]))
    return held_below, tuple(ordered[:SPLIT_PARTNERS_KEPT])


def _seed_agreement(finals: Sequence[_Final]) -> tuple[list[SeedPair], list[float]]:
    """The adjusted Rand index of every pair of final placements, and each seed's mean index
    with the others."""
    from sklearn.metrics import adjusted_rand_score

    placed = [final.placed_labels() for final in finals]
    pairs: list[SeedPair] = []
    for a in range(len(finals)):
        for b in range(a + 1, len(finals)):
            index = 1.0 if len(placed[a]) < 2 else float(adjusted_rand_score(placed[a], placed[b]))
            pairs.append(SeedPair(a, b, round(index, 6)))
    means: list[float] = []
    for seed in range(len(finals)):
        mine = [p.ari for p in pairs if seed in (p.seed_a, p.seed_b)]
        means.append(round(float(np.mean(mine)), 6) if mine else 1.0)
    return pairs, means


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------


def compute_themes(
    documents: Mapping[str, Sequence[str] | Mapping[str, float]],
    *,
    collections: Mapping[str, str] | None = None,
    senses: Mapping[str, str] | None = None,
    phrases: Mapping[tuple[str, ...], str] | None = None,
    settings: ThemeSettings | None = None,
) -> ThemeResult:
    """Draw the themes of ``documents``, ``{unit_id: words}``.

    ``documents``: a unit's words as a list (each occurrence counts one) or as ``{word: weight}``.
    ``collections``: ``{unit_id: collection}``; a unit not named belongs to one unnamed
    collection. ``senses``: ``{word: canonical name}`` for the words the caller's Dictionary
    knows (a label names a theme by these first); every other word is an unknown spelling.
    ``phrases``: ``{(word, word, ...): word}``, a run of words read as one before anything
    else (a list document only). Deterministic: the same arguments give the same result.
    """
    settings = settings or ThemeSettings()
    senses = dict(senses or {})
    prepared = _prepare(documents, collections, senses, phrases, settings)
    units = prepared.units
    belongs = prepared.belongs
    collection_weights = prepared.collection_weights
    vocabulary = prepared.vocabulary
    vertices = prepared.vertices
    empty = prepared.empty
    display = {word: _display(word, senses) for word in vocabulary}

    def unplaced_result(reason: str, placed_none: Iterable[str]) -> ThemeResult:
        missing = dict.fromkeys(empty, NO_WORDS)
        missing.update(dict.fromkeys(placed_none, reason))
        return ThemeResult(
            assignments={
                unit: ThemeAssignment(unit, None, None, None, None, missing.get(unit, NO_WORDS))
                for unit in units
            },
            themes=(),
            broad=(),
            unplaced=tuple(units),
            seed_ami=None,
            seed_nmi=None,
            resolution=None,
            broad_resolution=None,
            sweep=(),
            units=len(units),
            vertices=len(vertices.units),
            vocabulary=len(vocabulary),
            collections=collection_weights,
            display=display,
        )

    distinct = len(vertices.units)
    sizes = [len(members) for members in vertices.units]
    placed_units = [unit for members in vertices.units for unit in members]
    if distinct == 0 or sum(sizes) < settings.min_theme_size:
        return unplaced_result(TOO_SMALL, placed_units)

    tfidf = _tfidf(vertices, sublinear=settings.sublinear_counts)
    dimensions = lsa_dimensions(settings.lsa_dimensions, distinct)
    neighbours = settings.neighbours or max(5, round(math.sqrt(distinct)))
    seeds = list(range(settings.seeds))
    # Chance is a property of the documents: it is drawn once (seeds 0, 1, ... for the
    # replicates), for every seed's graph and placement. The edges are cut by the similarities to
    # each shuffled document's ``neighbours`` nearest (the observed graph joins each document to
    # as many), and placement by the similarity to its one nearest (placement tests a document's
    # one closest).
    null_seed = 0
    wants_chance = (
        settings.chance_quantile is not None
        or settings.edge_chance_quantile is not None
        or settings.edge_chance_quantile_within is not None
    )
    matched = settings.chance_null == "matched"
    null_neighbours = neighbours if matched else LEGACY_NULL_NEIGHBOURS
    chance_rows = (
        _chance_rows(
            vertices,
            dimensions,
            null_seed,
            sublinear=settings.sublinear_counts,
            neighbours=null_neighbours,
            replicates=settings.chance_replicates,
        )
        if wants_chance
        else []
    )
    edge_pool = [s for row in chance_rows for s in row]
    nearest_pool = [row[0] for row in chance_rows if row] if matched else edge_pool

    def cutoff(pool: Sequence[float], share: float | None) -> float | None:
        return None if share is None or not pool else float(np.quantile(pool, share))

    cut_between = cutoff(edge_pool, settings.edge_chance_quantile)
    cut_within = cutoff(edge_pool, settings.edge_chance_quantile_within)
    cut_placement = cutoff(nearest_pool, settings.chance_quantile)
    threshold = -1.0 if cut_placement is None else cut_placement
    between = -1.0 if cut_between is None else cut_between
    within = -1.0 if cut_within is None else cut_within
    chance_report = ChanceReport(
        measured=bool(chance_rows),
        null_seed=null_seed,
        replicates=settings.chance_replicates,
        edge_neighbors=min(null_neighbours, max(distinct - 1, 0)),
        placement_neighbors=1 if matched else min(null_neighbours, max(distinct - 1, 0)),
        shuffled_documents=len(chance_rows),
        quantile_between=settings.edge_chance_quantile,
        quantile_within=settings.edge_chance_quantile_within,
        quantile_placement=settings.chance_quantile,
        cutoff_between=None if cut_between is None else round(cut_between, 6),
        cutoff_within=None if cut_within is None else round(cut_within, 6),
        cutoff_placement=None if cut_placement is None else round(cut_placement, 6),
    )
    measured = cut_placement is not None
    groups = [frozenset(belongs[u] for u in members) for members in vertices.units]
    # A gathered vertex keeps its documents' weight in the graph. Each of its identical documents
    # would be joined to its copies, so the vertex carries a self-loop of its weight times the
    # copies a document's neighbours reach (none for a document alone).
    loops = (
        [
            float(vertices.mass[v]) * min(neighbours, len(members) - 1)
            for v, members in enumerate(vertices.units)
        ]
        if settings.vertex_weight_in_graph
        else []
    )
    embeddings = [_embedding(tfidf, vertices.mass, dimensions, s) for s in seeds]
    graphs = [
        _graph(embedded, neighbours, within=within, between=between, groups=groups, loops=loops)
        for embedded in embeddings
    ]
    partitions: dict[tuple[float, int], np.ndarray] = {}
    counted: dict[float, int] = {}

    def first_seed(resolution: float) -> None:
        partitions[(resolution, 0)] = _communities(graphs[0], resolution, 0, sizes)
        counted[resolution] = len(
            _big(
                partitions[(resolution, 0)],
                sizes,
                settings.min_theme_size,
                settings.min_theme_documents,
            )
        )

    for resolution in settings.fine_resolutions:
        first_seed(resolution)
    agreement: dict[float, tuple[float, float]] = {}
    # A pick at the edge of the sweep is tried past it, so a pick at the finest or coarsest
    # resolution offered is checked against one beyond. Each step is the ratio of the two
    # outermost resolutions there, at most ``resolution_edge_steps`` steps each way.
    coarser: list[float] = []
    finer: list[float] = []
    while True:
        # The other seeds are drawn only where the pick can fall: the resolutions in the
        # readable window (all of them where none is), as the pick never looks elsewhere.
        candidates = _window(counted, distinct)
        for resolution in sorted(candidates):
            if resolution in agreement:
                continue
            for seed, graph in zip(seeds[1:], graphs[1:], strict=True):
                partitions[(resolution, seed)] = _communities(graph, resolution, seed, sizes)
            scores = [
                _agreement(partitions[(resolution, 0)], partitions[(resolution, s)])
                for s in seeds[1:]
            ]
            agreement[resolution] = (
                round(float(np.mean([a for a, _ in scores])), 6),
                round(float(np.mean([n for _, n in scores])), 6),
            )
        sweep = [
            SweepRow(
                resolution=resolution,
                themes=counted[resolution],
                seed_ami=agreement[resolution][0] if resolution in agreement else None,
                seed_nmi=agreement[resolution][1] if resolution in agreement else None,
            )
            for resolution in (*sorted(coarser), *settings.fine_resolutions, *sorted(finer))
        ]
        chosen = _pick(sweep, candidates, settings.fine_resolutions)
        beyond = _past_the_edge(
            sweep,
            chosen,
            settings.resolution_edge_steps - len(finer),
            settings.resolution_edge_steps - len(coarser),
        )
        if beyond is None or beyond in counted:
            break
        (finer if beyond > chosen.resolution else coarser).append(beyond)
        first_seed(beyond)
    # One final placement for every seed: the same removal of what is no closer than chance, the
    # same splitting into connected parts and the same minimum sizes, each over that seed's own
    # embedding and graph. Every pair of seeds is compared on it, and the seed most like the others
    # (the medoid) is the one shown, unless the setting asks for seed 0.
    finals = [
        _finalise(
            embeddings[s],
            graphs[s],
            partitions[(chosen.resolution, s)],
            sizes,
            settings,
            threshold,
            measured=measured,
        )
        for s in seeds
    ]
    pairs, mean_ari = _seed_agreement(finals)
    medoid = max(seeds, key=lambda s: (mean_ari[s], -s))
    shown_seed = 0 if settings.final_placement == "first" else medoid
    final = finals[shown_seed]
    reference = final.labels
    far = final.far
    far_overall = final.far_overall
    fine = {vertex: int(theme) for vertex, theme in enumerate(final.theme.tolist()) if theme >= 0}
    if not fine:
        return unplaced_result(TOO_SMALL, placed_units)
    others = [finals[s].placed_labels() for s in seeds if s != shown_seed]
    big = list(range(final.themes))
    seed_placements = tuple(
        SeedPlacement(
            seed=s,
            themes=finals[s].themes,
            placed_units=sum(sizes[v] for v in np.flatnonzero(finals[s].theme >= 0).tolist()),
            mean_ari=mean_ari[s],
        )
        for s in seeds
    )
    broad_of, broad_resolution = _broad(
        graphs[shown_seed], fine, len(big), settings.broad_resolutions, sizes
    )
    disconnected_themes = _disconnected(graphs[shown_seed], fine)
    disconnected_broad = _disconnected(
        graphs[shown_seed], {vertex: broad_of[theme] for vertex, theme in fine.items()}
    )

    # Rows of the count matrix per unit, for labels over units rather than vertices.
    theme_rows: dict[int, list[int]] = defaultdict(list)
    broad_rows: dict[int, list[int]] = defaultdict(list)
    for vertex, theme in fine.items():
        theme_rows[theme].append(vertex)
        broad_rows[broad_of[theme]].append(vertex)
    # tfidf weighs every occurrence; log-odds counts the experiments that hold a word.
    base = (
        (vertices.matrix > 0).astype(float).tocsr()
        if settings.label_method == "log_odds"
        else vertices.matrix.tocsr()
    )
    counts = base.multiply(np.array(sizes, dtype=float)[:, None]).tocsr()
    strength = settings.label_prior_strength
    fine_ranked = _distinctive(counts, theme_rows, vocabulary, settings.label_method, strength)
    broad_ranked = _distinctive(counts, broad_rows, vocabulary, settings.label_method, strength)
    fine_support = _label_support(
        base,
        sizes,
        theme_rows,
        vocabulary,
        senses,
        settings,
        {g: _label_tokens(r, senses, settings) for g, r in fine_ranked.items()},
    )
    broad_support = _label_support(
        base,
        sizes,
        broad_rows,
        vocabulary,
        senses,
        settings,
        {g: _label_tokens(r, senses, settings) for g, r in broad_ranked.items()},
    )

    def steadiness(found: tuple[float, ...]) -> float | None:
        return round(float(np.mean(found)), 6) if found else None

    broad_labels: dict[int, tuple[tuple[str, ...], str]] = {}
    for broad_id, ranked in broad_ranked.items():
        words, _, label = _label(ranked, senses, settings)
        broad_labels[broad_id] = (words, label)

    summaries: list[ThemeSummary] = []
    labels_of: dict[int, str] = {}
    for theme in range(len(big)):
        rows = sorted(theme_rows[theme])
        words, sense_names, label = _label(fine_ranked[theme], senses, settings)
        labels_of[theme] = label
        matches = _best_matches(others, rows)
        stability, core = _stability(matches, rows)
        split_seeds, split_partners = _split_with(final.theme, matches, rows, theme)
        members = tuple(sorted(unit for vertex in rows for unit in vertices.units[vertex]))
        support = fine_support.get(theme, ())
        summaries.append(
            ThemeSummary(
                theme_id=theme,
                broad_id=broad_of[theme],
                units=len(members),
                label=label,
                words=words,
                senses=sense_names,
                stability=round(stability, 6),
                core_units=sum(sizes[vertex] for vertex in core),
                members=members,
                label_support=support,
                label_stability=steadiness(support),
                split_seeds=split_seeds,
                split_with=split_partners,
            )
        )
    broad_summaries = tuple(
        BroadTheme(
            broad_id=broad_id,
            units=sum(s.units for s in summaries if s.broad_id == broad_id),
            label=broad_labels[broad_id][1],
            words=broad_labels[broad_id][0],
            themes=tuple(s.theme_id for s in summaries if s.broad_id == broad_id),
            label_support=broad_support.get(broad_id, ()),
            label_stability=steadiness(broad_support.get(broad_id, ())),
        )
        for broad_id in sorted(set(broad_of.values()))
    )

    assignments: dict[str, ThemeAssignment] = {}
    for unit in empty:
        assignments[unit] = ThemeAssignment(unit, None, None, None, None, NO_WORDS)
    community_vertices = Counter(reference.tolist())
    for vertex, members in enumerate(vertices.units):
        theme_id = fine.get(vertex)
        # A lone document tested against chance and found no closer is too far to place;
        # identical documents alone in their community, as many as a theme, read the same words;
        # a small community is too small.
        alone = bool(far[vertex]) and len(members) == 1
        in_group = alone and not bool(far_overall[vertex])
        repeated = (
            len(members) > 1
            and len(members) >= settings.min_theme_size
            and community_vertices[int(reference[vertex])] == 1
        )
        for unit in members:
            if theme_id is None:
                reason = (
                    NOT_CLOSE_TO_GROUP
                    if in_group
                    else NOT_CLOSE
                    if alone
                    else REPEATED_TEXT
                    if repeated
                    else TOO_SMALL
                )
                assignments[unit] = ThemeAssignment(unit, None, None, None, None, reason)
            else:
                broad_id = broad_of[theme_id]
                assignments[unit] = ThemeAssignment(
                    unit,
                    theme_id,
                    broad_id,
                    labels_of[theme_id],
                    broad_labels[broad_id][1],
                    None,
                )
    ordered = {unit: assignments[unit] for unit in units}
    return ThemeResult(
        assignments=ordered,
        themes=tuple(summaries),
        broad=broad_summaries,
        unplaced=tuple(unit for unit in units if ordered[unit].theme_id is None),
        seed_ami=chosen.seed_ami,
        seed_nmi=chosen.seed_nmi,
        resolution=chosen.resolution,
        broad_resolution=broad_resolution,
        sweep=tuple(sweep),
        units=len(units),
        vertices=distinct,
        vocabulary=len(vocabulary),
        collections=collection_weights,
        display=display,
        sweep_extended=tuple(sorted((*coarser, *finer))),
        placement_seed=shown_seed,
        medoid_seed=medoid,
        seeds=seed_placements,
        seed_pairs=tuple(pairs),
        seed_ari=round(float(np.mean([p.ari for p in pairs])), 6) if pairs else None,
        chance=chance_report,
        disconnected_themes=disconnected_themes,
        disconnected_broad=disconnected_broad,
        label_method=settings.label_method,
        label_bootstrap=settings.label_bootstrap,
    )
