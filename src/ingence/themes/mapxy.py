"""A map of a run's experiments in the plane.

What it is. Each experiment is placed in two dimensions by the words the themes read, so
experiments that read alike lie near each other. It is the themes' own space: the experiment's
vector is the one the themes were drawn over (:func:`ingence.themes.core.document_vectors`, the
LSA of the shown seed), and the plane is a projection of it. Distances in the plane are
approximate. A projection from many dimensions to two cannot keep every distance, so a picture
must not be read as measuring how far apart two experiments are. It shows only which lie close.

The map is a projection and never a claim. The module recomputes it from the run and the
settings, writes it nowhere but the themes projection's cache, and no inference reads it.

The map is reproducible. The vectors' rows are ordered by their content, and
never by an identifier or the order the experiments were given. The method is run with a fixed
seed, the OpenMP threads limited to one and an initial layout taken from the principal components.
The coordinates are rounded to :data:`DECIMALS` places. So a rebuild, another run of the same
Index and Rulebook and a shuffled input give the same numbers on this computer.

Placement. An experiment with no word that the themes read has no vector and no place: it is
given no coordinates and the reason, and never zero. An experiment that the themes drew into no
theme (too far from every other, or in a group too small to be a theme) is placed. The map shows
where it lies, which is the point of a map. Experiments that read exactly the same words share one
point.

:data:`METHOD` chooses the method, and :data:`TSNE_LIMIT` bounds the cost of the larger methods.
Above that many distinct documents the principal components are used, and the response says which
method it used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.themes.core import DocumentVectors

__all__ = [
    "DECIMALS",
    "MAP_VERSION",
    "METHOD",
    "METHOD_WORDS",
    "NO_PLACE_FEW",
    "NO_PLACE_WORDS",
    "TSNE_LIMIT",
    "MapPositions",
    "digest_of",
    "made_with",
    "map_positions",
    "method_for",
    "plane_of",
]

#: Moves when the method, its parameters or the scaling change. Part of the cache's key.
MAP_VERSION = "1"
#: The method for a run with up to :data:`TSNE_LIMIT` distinct documents: ``tsne`` (Barnes-Hut
#: t-SNE, seeded, its OpenMP threads limited to one, initialized from the principal components)
#: or ``pca``.
METHOD: Literal["tsne", "pca"] = "tsne"
#: The most distinct documents the slower method is run on. More are placed by ``pca``.
TSNE_LIMIT = 6000
#: t-SNE's perplexity, as the most neighbors a point is balanced against (lowered for few points).
PERPLEXITY = 30.0
#: t-SNE's iterations (scikit-learn's own default).
ITERATIONS = 1000
#: Decimal places of a coordinate in [0, 1]: more would only record floating-point noise.
DECIMALS = 6
#: The fewest distinct documents a plane is drawn for. With fewer, every experiment has no place.
MINIMUM_POINTS = 3
#: The fewest distinct documents t-SNE is run on (it needs a perplexity below the count).
MINIMUM_TSNE = 8

#: What the places mean, per method, in words a person reads.
_COMMON = (
    "Each experiment is placed by the words the themes read, so experiments that read alike lie "
    "near each other. Distances are approximate: look for which experiments lie close, not for how "
    "far apart two are. The same run, Dictionary and settings give the same places on this "
    "computer."
)
METHOD_WORDS: dict[str, str] = {
    "tsne": (
        f"{_COMMON} The layout is t-SNE, which keeps close neighbors close and does not keep "
        "larger distances, so the gaps between groups say little about how different they are."
    ),
    "pca": (
        f"{_COMMON} The layout is the two directions along which the experiments differ most "
        "(principal components), used because this run has more experiments than the slower "
        "layout is run on. It separates groups less than t-SNE does."
    ),
}

#: Why an experiment has no place. Words a person reads.
NO_PLACE_WORDS = "none of its words are among the words the themes read"
NO_PLACE_FEW = "too few experiments have words the themes read to draw a map"


@dataclass(frozen=True)
class MapPositions:
    """Where each experiment lies, and how."""

    #: Each experiment's place, ``(x, y)`` in [0, 1], or None where it has none.
    places: dict[str, tuple[float, float] | None]
    #: Why an experiment has no place. Every unit with a None place is a key.
    reasons: dict[str, str]
    #: The method used (``tsne``, ``pca``), or None where no plane was drawn.
    method: str | None
    #: Distinct documents the plane was drawn for.
    points: int
    #: The seed of the vectors and of the method.
    seed: int
    #: Experiments that share a point with at least one other experiment.
    sharing: int


def made_with() -> dict[str, object]:
    """Everything that decides the places besides the run: this module's settings and the versions
    of the libraries that compute the vectors and the layout.

    It is written into a cached map and compared when the file is read, so a map drawn under
    another setting or another library is drawn again and never served. Nobody has to remember to
    raise :data:`MAP_VERSION` for a setting to take effect.
    """
    import scipy
    import sklearn

    return {
        "map_version": MAP_VERSION,
        "method": METHOD,
        "tsne_limit": TSNE_LIMIT,
        "perplexity": PERPLEXITY,
        "iterations": ITERATIONS,
        "decimals": DECIMALS,
        "minimum_points": MINIMUM_POINTS,
        "minimum_tsne": MINIMUM_TSNE,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "scikit_learn": sklearn.__version__,
    }


def method_for(count: int, method: str) -> str:
    """The method a plane of ``count`` distinct documents is drawn by when ``method`` is asked
    for: ``tsne`` only from :data:`MINIMUM_TSNE` to :data:`TSNE_LIMIT` documents, else ``pca``."""
    return "tsne" if method == "tsne" and MINIMUM_TSNE <= count <= TSNE_LIMIT else "pca"


def plane_of(vectors: np.ndarray, *, method: str, seed: int) -> np.ndarray:
    """Two coordinates for each row, deterministically, by :func:`method_for` ``method``.

    ``pca``: the first two principal components of the rows (a full decomposition, signs fixed by
    scikit-learn). ``tsne``: Barnes-Hut t-SNE on the rows, ``random_state`` the seed, the OpenMP
    threads limited to one while it runs, initialized from the principal components.
    """
    from sklearn.decomposition import PCA

    count = vectors.shape[0]
    if method_for(count, method) == "tsne":
        from sklearn.manifold import TSNE
        from threadpoolctl import threadpool_limits

        perplexity = min(PERPLEXITY, max(2.0, (count - 1) / 3.0))
        # ``n_jobs=1`` limits only the neighbor search. The gradient step of scikit-learn runs on
        # every OpenMP thread and adds a total across them, so the threads are limited here.
        # Fewer threads cost nothing in the places (they are the same at every count, tested),
        # and the fit does not compete with everything else on a busy machine.
        with threadpool_limits(limits=1, user_api="openmp"):
            return np.asarray(
                TSNE(
                    n_components=2,
                    init="pca",
                    perplexity=perplexity,
                    max_iter=ITERATIONS,
                    random_state=seed,
                    n_jobs=1,
                ).fit_transform(vectors),
                dtype=float,
            )
    width = vectors.shape[1]
    plane = PCA(n_components=min(2, width, count), svd_solver="full").fit_transform(vectors)
    if plane.shape[1] < 2:
        plane = np.hstack([plane, np.zeros((count, 2 - plane.shape[1]))])
    return np.asarray(plane, dtype=float)


def _into_the_unit_square(plane: np.ndarray) -> np.ndarray:
    """Scale by one factor (shapes and ratios kept) so the longer side is 1, then center."""
    low = plane.min(axis=0)
    span = plane.max(axis=0) - low
    longest = float(span.max())
    if longest <= 0.0:
        return np.full(plane.shape, 0.5)
    shifted = (plane - low) / longest
    shifted += (1.0 - span / longest) / 2.0
    return np.asarray(np.clip(shifted, 0.0, 1.0), dtype=float)


def map_positions(
    found: DocumentVectors, units: list[str], *, method: str = METHOD
) -> MapPositions:
    """Where each of ``units`` lies, from the themes' vectors (``found``).

    ``units`` is every experiment of the run; one that is not a row of ``found`` has no place.
    """
    where: dict[str, int] = {
        unit: row for row, members in enumerate(found.units) for unit in members
    }
    reasons: dict[str, str] = {}
    places: dict[str, tuple[float, float] | None] = {}
    count = len(found.units)
    if count < MINIMUM_POINTS:
        for unit in sorted(units):
            places[unit] = None
            reasons[unit] = NO_PLACE_FEW if unit in where else NO_PLACE_WORDS
        return MapPositions(places, reasons, None, count, found.seed, 0)
    used = method_for(count, method)
    scaled = _into_the_unit_square(plane_of(found.vectors, method=used, seed=found.seed))
    # Adding zero turns a rounded -0.0 into 0.0, so two computations print the same digits.
    rounded = np.round(scaled, DECIMALS) + 0.0
    for unit in sorted(units):
        row = where.get(unit)
        if row is None:
            places[unit] = None
            reasons[unit] = NO_PLACE_WORDS
        else:
            places[unit] = (float(rounded[row, 0]), float(rounded[row, 1]))
    sharing = sum(len(members) for members in found.units if len(members) > 1)
    return MapPositions(places, reasons, used, count, found.seed, sharing)


def digest_of(places: Mapping[str, tuple[float, float] | None]) -> str:
    """A short digest of the coordinates, in a canonical order, to compare two computations."""
    text = json.dumps(sorted(places.items()), separators=(",", ":"))
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()
