"""Order results by a tuple of counts, each shown beside the result it ranked.

A list ordered by ``0.87`` cannot be argued with, and every element of a tuple can. So nothing here
is weighted and nothing is summed into the order. The ranking compares a unit by how many of the
typed concepts it covers, then by how many of them its own folder name carries, then by the weakest
way any of them was matched. Only then does a tiebreak number decide. That number is not shown as
a percentage and is not comparable across tiers. No score may look like a probability: tiers
decide, and scores break ties. The Review queue follows the same argument.

Each dimension, its direction, and what it measures:

- ``concepts_covered``, more first: typed concepts found in the unit's name, its members' names,
  its notes or its content keywords.
- ``found_by_step``, the word itself first, directly after coverage: how broadly the weakest
  covered concept was reached, in four steps. The steps are the word itself (as written, by its
  sense, a plural, or a piece of a word kept whole), a word's start, inside a name, and a near
  spelling. A dated folder that holds the word only inside a longer one never outranks the folder
  that the word names. A plural bound to the word's sense counts as the word itself, so
  experiments still lead.
- ``unit_kind``, experiments first: experiment, analysis folder, product file, plain folder,
  installed program. Final-stage files and analysis folders appear alongside the experiments, with
  less priority.
- ``concepts_named``, more first: how many of those concepts the unit's own name, or a person's
  note on it, carries. A folder named for a thing is about that thing.
- ``found_by``, stronger first: the finer way, which is the weakest of the strongest ways across
  covered concepts. The ways are the spelling, a Dictionary sense, starts-with, plain name and a
  near spelling. This breaks ties among units of one kind that their own names carry alike.
- ``weakest_route``, better first: the worst of the best routes across covered concepts. A unit
  matched only by a near spelling ranks below one matched as typed.
- ``marked_good``, marked first: a unit whose own name carries a word of the quality kind marked
  good ranks above an equal one that does not. The ranking says what the name says and no more. It
  never says that the unit is good, and the card words it "named good".
- ``doubted``, undoubted first: a unit whose name a doubt rule raised (``failed``, ``bad`` or
  ``test`` in the negative lexicon) ranks below an equal one that the rule did not raise. The page
  shows the unit with the doubt and never hides it, because doubt never withholds content.
- ``content_only``, fewer first: covered concepts found only inside file content and never in a
  name. This is the broad channel, and it ranks below the narrow one.
- ``newest``, newer first: the latest earliest-credible time beneath the unit. This dimension is
  off by default and ``order=`` turns it on.

The order is a request parameter, as ``/api/review?order=`` is. Reordering a page changes no claim
and needs no re-run. The ranking refuses a dimension that it does not compute and never ignores
it.

The tiebreak is an inverse-document-frequency sum over the covered concepts' tokens, at folder
granularity: it counts how many folders carry the token, not how many files. TF-IDF recovers
same-source files better than a plain count, so rare words should separate equals. One long
dissertation can hold most of a folder's tokens, so the unit of counting is the folder and not the
file. A folder of five thousand exported frames would otherwise make every word in it look common.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.search.expand import (
    FOUND_BY_ORDER,
    ROUTE_ORDER,
    STEP_WORDS,
    FoundBy,
    MatchRoute,
    route_step,
)
from ingence.search.scale import UNIT_KIND_ORDER, UnitKind

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "DEFAULT_ORDER",
    "DIMENSIONS",
    "RankedUnit",
    "UnitEvidence",
    "UnknownDimensionError",
    "found_by",
    "rank",
    "tie_path",
]


@dataclass(frozen=True, slots=True)
class UnitEvidence:
    """What the ranking reads about one candidate unit. All of it is counts."""

    folder_id: str
    covered: frozenset[int]
    named: frozenset[int]
    content_only: frozenset[int]
    #: Per covered concept, the best route that matched it anywhere in the unit.
    best_route: Mapping[int, MatchRoute]
    #: Per covered concept, the number of folders in the pool that carry its matched tokens. Where
    #: several tokens matched, the smallest number. The tiebreak uses it.
    folder_frequency: Mapping[int, int]
    newest_ns: int | None = None
    #: What the unit stands for (``scale.UnitKind``). A plain folder where nothing says more.
    kind: UnitKind = UnitKind.FOLDER
    #: Whether a doubt rule raised the unit's own name.
    doubted: bool = False
    #: Whether the unit's own name carries a word of the quality kind marked good. This mark is
    #: read from a name. It is never a statement that the unit is good.
    good: bool = False
    #: Per covered concept, the strongest way a match of it was found.
    best_way: Mapping[int, FoundBy] = field(default_factory=dict)
    #: Every way any match in the unit was found.
    ways: frozenset[FoundBy] = frozenset()
    #: The unit's path within its root. It orders a tie, because every build of one pool shares it,
    #: while an identifier differs with where the pool sits.
    path: str = ""
    #: The unit's root's place in the project's list of roots. It orders two roots that hold one
    #: path.
    root_place: int = 0


#: Every dimension and its direction. Direction ``-1`` means "more first". Direction ``+1`` means
#: "fewer (or better) first". Sorting ascending on the signed key gives the stated direction for
#: every dimension at once.
DIMENSIONS: dict[str, tuple[int, str]] = {
    "concepts_covered": (-1, "more first"),
    "found_by_step": (+1, "the word itself first"),
    "unit_kind": (+1, "experiments first"),
    "concepts_named": (-1, "more first"),
    "found_by": (+1, "stronger first"),
    "weakest_route": (+1, "better first"),
    "marked_good": (-1, "named good first"),
    "doubted": (+1, "undoubted first"),
    "content_only": (+1, "fewer first"),
    "newest": (-1, "newer first"),
}

#: ``unit_kind`` comes after coverage and after how a unit was found, and not first. A figure that
#: covers all three typed words should rank above an experiment that covers one. A folder named by
#: the word as typed should rank above an experiment that holds the word only inside a longer
#: word. Among equals, experiments lead.
DEFAULT_ORDER: tuple[str, ...] = (
    "concepts_covered",
    "found_by_step",
    "unit_kind",
    "concepts_named",
    "found_by",
    "weakest_route",
    "marked_good",
    "doubted",
    "content_only",
)


class UnknownDimensionError(ValueError):
    """An ``order=`` named a dimension that the ranking does not compute. The ranking refuses it."""


@dataclass(frozen=True, slots=True)
class RankedUnit:
    """One unit, its tuple as shown, and its place."""

    evidence: UnitEvidence
    dimensions: dict[str, int | str | None]
    tiebreak: float
    position: int = 0


def _weakest(evidence: UnitEvidence) -> int:
    if not evidence.best_route:
        return len(ROUTE_ORDER)
    return max(ROUTE_ORDER.index(route) for route in evidence.best_route.values())


def tie_path(path: str) -> str:
    """Return a path as a tie reads it, in composed form (NFC), for the sort key only.

    A path is stored as the filesystem gave it. One name may be read decomposed on one volume and
    composed on another, and a tie ordered by the bytes would order them apart.
    """
    import unicodedata

    return unicodedata.normalize("NFC", path)


def found_by(evidence: UnitEvidence) -> FoundBy | None:
    """Return how a unit was found, or None.

    The result is the weakest of the strongest ways across the unit's covered concepts.
    """
    if not evidence.best_way:
        return None
    return max(evidence.best_way.values(), key=FOUND_BY_ORDER.index)


def _step(evidence: UnitEvidence) -> int:
    """Return the weakest covered concept's step: how broadly the unit's matches reached."""
    if not evidence.best_route:
        return 0
    return max(route_step(route) for route in evidence.best_route.values())


def _value(name: str, evidence: UnitEvidence) -> int:
    if name == "found_by_step":
        return _step(evidence)
    if name == "found_by":
        way = found_by(evidence)
        return FOUND_BY_ORDER.index(way) if way is not None else 0
    if name == "concepts_covered":
        return len(evidence.covered)
    if name == "concepts_named":
        return len(evidence.named)
    if name == "weakest_route":
        return _weakest(evidence)
    if name == "unit_kind":
        return UNIT_KIND_ORDER.index(evidence.kind)
    if name == "marked_good":
        return int(evidence.good)
    if name == "doubted":
        return int(evidence.doubted)
    if name == "content_only":
        return len(evidence.content_only)
    if name == "newest":
        return evidence.newest_ns or 0
    message = f"no ranking dimension {name!r}"
    raise UnknownDimensionError(message)


def _tiebreak(evidence: UnitEvidence, *, folders_in_pool: int) -> float:
    """Return an IDF sum over the covered concepts, with folders as documents.

    The sum orders units within a tier only.
    """
    total = 0.0
    for concept in sorted(evidence.covered):
        carried = max(1, evidence.folder_frequency.get(concept, folders_in_pool))
        total += math.log(1.0 + folders_in_pool / carried)
    return round(total, 6)


def rank(
    candidates: Sequence[UnitEvidence],
    *,
    folders_in_pool: int,
    order: Sequence[str] | None = None,
) -> list[RankedUnit]:
    """Return the candidates in order, each with its tuple as shown and its position.

    The order is deterministic. The last keys are the unit's path (in one Unicode form,
    :func:`tie_path`), its root's place in the project's list of roots, then its identifier. Two
    units that tie on every dimension and on the tiebreak therefore come out in one order on every
    machine and in every build of one pool. An identifier depends on where the pool sits. A tie
    ordered by it would choose differently which units a folded series keeps in view.
    """
    chosen = tuple(order) if order else DEFAULT_ORDER
    for name in chosen:
        if name not in DIMENSIONS:
            message = f"no ranking dimension {name!r}; the dimensions are {sorted(DIMENSIONS)}"
            raise UnknownDimensionError(message)

    ranked: list[RankedUnit] = []
    for evidence in candidates:
        shown: dict[str, int | str | None] = {name: _value(name, evidence) for name in chosen}
        if "weakest_route" in shown:
            weakest = _weakest(evidence)
            shown["weakest_route"] = (
                ROUTE_ORDER[weakest].value if weakest < len(ROUTE_ORDER) else None
            )
        if "found_by_step" in shown:
            shown["found_by_step"] = STEP_WORDS[_step(evidence)]
        if "found_by" in shown:
            way = found_by(evidence)
            shown["found_by"] = way.value if way is not None else None
        if "unit_kind" in shown:
            shown["unit_kind"] = evidence.kind.value
        if "marked_good" in shown:
            shown["marked_good"] = "named good" if evidence.good else "no"
        if "doubted" in shown:
            shown["doubted"] = "doubted" if evidence.doubted else "no"
        ranked.append(
            RankedUnit(
                evidence=evidence,
                dimensions=shown,
                tiebreak=_tiebreak(evidence, folders_in_pool=folders_in_pool),
            )
        )

    def key(unit: RankedUnit) -> tuple[object, ...]:
        signed = tuple(DIMENSIONS[name][0] * _value(name, unit.evidence) for name in chosen)
        return (
            *signed,
            -unit.tiebreak,
            tie_path(unit.evidence.path),
            unit.evidence.root_place,
            unit.evidence.folder_id,
        )

    ranked.sort(key=key)
    return [
        RankedUnit(
            evidence=unit.evidence,
            dimensions=unit.dimensions,
            tiebreak=unit.tiebreak,
            position=index + 1,
        )
        for index, unit in enumerate(ranked)
    ]
