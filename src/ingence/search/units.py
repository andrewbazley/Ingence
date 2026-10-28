"""Roll file matches up to the unit they belong to, and show each unit once.

The unit of a result is the unit that a match belongs to (see ``scale``). It is the match's
experiment, else its analysis folder, else its installed program. A product file (a figure, a Prism
project, a presentation) that belongs to none of those is a unit of its own. Only a match that
belongs to nothing falls back to the smallest folder that covers what was typed. This keeps the
results to high-level, self-contained units and protects the user from a flood of files. The steps
are each a rule that a person could check by hand:

1. Homes (``scale``, at build time). A match inside ``Exp/results/`` counts for ``Exp``.
2. The smallest covering folder, for homeless matches only (Xu and Papakonstantinou, SIGMOD
   2005). A folder is kept only where no folder beneath it covers exactly the same concepts.
3. Nesting. After ranking, a unit beneath a unit already shown is listed inside that unit, and
   never again at another depth. An experiment of a series, for example, is listed inside the
   series' dated folder.
4. Collapsing copies. Two folder units whose direct files agree on name and size throughout
   are shown once, as "also at <path>". This is a display grouping over observed metadata. It
   asserts no identity, because ``identical`` needs a whole-file digest.
5. Folding a series. Where ``fold_at`` or more units of one kind share one parent and cover the
   same number of the typed concepts, the best ``fold_keep`` stay cards and the rest become one
   card for the parent. Only equals fold. An experiment is never buried among analyses beside it.
   A better match is never buried among worse ones. The best answer is never buried in a pool of
   many experiments side by side.
6. Capping products. At most ``max_products`` product files are cards of their own on a page.
   The rest fold into one card, such as "12 more figures and presentations". A query that matches
   a hundred exported plots then shows a few and says how many more.

A unit's contents are not shown until a person asks. A card carries counts and chips. The results
send ``inside`` and ``folded`` collapsed, and the page loads a unit's files only when a person
opens it (the Explore page's children route).

Nothing here is stored. A unit is a rendering of one query over one run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ingence.search.expand import FOUND_BY_ORDER, ROUTE_ORDER, FoundBy, MatchRoute
from ingence.search.rank import RankedUnit, UnitEvidence, found_by
from ingence.search.scale import UnitKind

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "DEFAULT_FOLD_AT",
    "DEFAULT_FOLD_KEEP",
    "DEFAULT_MAX_PRODUCTS",
    "FolderFacts",
    "HitRow",
    "Shown",
    "UnitFacts",
    "evidence_from_rows",
    "present",
    "smallest_covering",
]

#: The fewest folder units that share a parent before they fold into one card.
DEFAULT_FOLD_AT = 4

#: The number of a series' best members that stay cards of their own when the series folds. Zero
#: buries the best answer in a card for the parent. Two keeps the best two in view and counts the
#: rest.
DEFAULT_FOLD_KEEP = 2

#: The number of product files that a page shows as cards of their own before it folds the rest.
#: This guards against a flood of files. The value has not been measured.
DEFAULT_MAX_PRODUCTS = 5


@dataclass(frozen=True, slots=True)
class HitRow:
    """One (unit, concept) row as the engine's SQL returns it."""

    folder_id: str
    concept: int
    route: MatchRoute
    #: True where the unit's own name, or a person's note on it, carried the concept.
    named: bool
    #: True where the concept was found only inside file content beneath the unit.
    content_only: bool
    folder_frequency: int
    #: The strongest way that any match of the concept in the unit was found.
    way: FoundBy = FoundBy.SPELLING
    #: Every way that a match of the concept in the unit was found.
    ways: frozenset[FoundBy] = frozenset()


@dataclass(frozen=True, slots=True)
class UnitFacts:
    """What the display needs about a unit, from the projection's tables.

    The tables are ``folders``, ``units`` and ``products``. A product unit is a file. Every other
    unit is a folder.
    """

    folder_id: str
    parent_id: str | None
    name: str
    relative_path: str
    depth: int
    subtree_files: int
    #: Every ancestor folder, nearest first.
    ancestors: tuple[str, ...] = ()
    #: A digest of the folder's direct files' names and sizes, for collapsing copies.
    copy_signature: str | None = None
    newest_ns: int | None = None
    kind: UnitKind = UnitKind.FOLDER
    #: Why the unit has its kind, in words that a card shows ("raw and later-stage files beneath
    #: it").
    basis: str | None = None
    doubted: bool = False
    #: Whether the unit's own name carries a word marked good. This mark is read from a name.
    good: bool = False
    #: For a product: what made it one (``figure``, ``prism_project``, ``pooled``).
    made_as: str | None = None
    #: The unit's root's place in the project's list of roots. It orders a tie after the path.
    root_place: int = 0


#: The earlier name of :class:`UnitFacts`. Callers written against it still work.
FolderFacts = UnitFacts


@dataclass
class Shown:
    """One card on the results page."""

    unit: RankedUnit
    #: Units beneath this one that also matched, listed inside the card, collapsed.
    inside: list[RankedUnit] = field(default_factory=list)
    #: Where this card stands for several units, those units, collapsed.
    folded: list[RankedUnit] = field(default_factory=list)
    #: Other places that hold what looks from outside like the same folder.
    also_at: list[str] = field(default_factory=list)
    #: For a folded series, the parent folder the card is titled by.
    group_parent: str | None = None
    #: True where the card stands for product files that folded past ``max_products``.
    more_products: bool = False


def evidence_from_rows(
    rows: Iterable[HitRow], *, facts: Mapping[str, UnitFacts]
) -> dict[str, UnitEvidence]:
    """Assemble one piece of evidence per unit from its (unit, concept) rows."""
    by_unit: dict[str, list[HitRow]] = {}
    for row in rows:
        by_unit.setdefault(row.folder_id, []).append(row)
    assembled: dict[str, UnitEvidence] = {}
    for unit_id, found in sorted(by_unit.items()):
        best: dict[int, MatchRoute] = {}
        best_way: dict[int, FoundBy] = {}
        ways: set[FoundBy] = set()
        frequency: dict[int, int] = {}
        for row in found:
            standing = best.get(row.concept)
            if standing is None or ROUTE_ORDER.index(row.route) < ROUTE_ORDER.index(standing):
                best[row.concept] = row.route
            held_way = best_way.get(row.concept)
            if held_way is None or FOUND_BY_ORDER.index(row.way) < FOUND_BY_ORDER.index(held_way):
                best_way[row.concept] = row.way
            ways |= {row.way, *row.ways}
            frequency[row.concept] = min(
                frequency.get(row.concept, row.folder_frequency), row.folder_frequency
            )
        named = frozenset(row.concept for row in found if row.named)
        in_a_name = frozenset(row.concept for row in found if not row.content_only)
        covered = frozenset(best)
        known = facts.get(unit_id)
        assembled[unit_id] = UnitEvidence(
            folder_id=unit_id,
            covered=covered,
            named=named,
            content_only=covered - in_a_name,
            best_route=best,
            folder_frequency=frequency,
            best_way=best_way,
            ways=frozenset(ways),
            path=known.relative_path if known else "",
            root_place=known.root_place if known else 0,
            newest_ns=known.newest_ns if known else None,
            kind=known.kind if known else UnitKind.FOLDER,
            doubted=known.doubted if known else False,
            good=known.good if known else False,
        )
    return assembled


def smallest_covering(
    evidence: Mapping[str, UnitEvidence], *, facts: Mapping[str, UnitFacts]
) -> list[UnitEvidence]:
    """Keep each plain folder that no folder beneath it matches as well (step 2).

    Units of every other kind are kept as they are. The run's conclusions chose them, and a
    sub-folder that happens to cover the same words does not shadow an experiment.
    """
    shadowed: set[str] = set()
    for unit_id, found in evidence.items():
        known = facts.get(unit_id)
        if known is None:
            continue
        for ancestor in known.ancestors:
            above = evidence.get(ancestor)
            if (
                above is not None
                and above.kind is UnitKind.FOLDER
                and above.covered == found.covered
            ):
                shadowed.add(ancestor)
    return [found for unit_id, found in sorted(evidence.items()) if unit_id not in shadowed]


def present(
    ranked: Sequence[RankedUnit],
    *,
    facts: Mapping[str, UnitFacts],
    fold_at: int = DEFAULT_FOLD_AT,
    fold_keep: int = DEFAULT_FOLD_KEEP,
    max_products: int = DEFAULT_MAX_PRODUCTS,
    limit: int = 25,
) -> list[Shown]:
    """Nest, collapse, fold and cap ranked units into the cards a page shows (steps 3 to 6)."""
    cards: list[Shown] = []
    card_of: dict[str, Shown] = {}

    # Step 3: nesting, in rank order, so the best-ranked ancestor holds what is beneath it.
    for unit in ranked:
        unit_id = unit.evidence.folder_id
        known = facts.get(unit_id)
        holder = next(
            (card_of[a] for a in (known.ancestors if known else ()) if a in card_of), None
        )
        if holder is not None:
            holder.inside.append(unit)
            continue
        card = Shown(unit=unit)
        cards.append(card)
        card_of[unit_id] = card

    # Step 4 before step 5, so a folded series never hides a copy of itself inside it.
    seen: dict[str, Shown] = {}
    kept: list[Shown] = []
    for card in cards:
        known = facts.get(card.unit.evidence.folder_id)
        signature = known.copy_signature if known and known.kind is not UnitKind.PRODUCT else None
        if signature and signature in seen:
            seen[signature].also_at.append(known.relative_path if known else "")
            continue
        if signature:
            seen[signature] = card
        kept.append(card)

    # Step 5: fold a series, keeping its best members in view. A group is the units of one kind that
    # share one parent and cover the same number of the typed concepts. Products never fold with
    # folders. An experiment never folds with an analysis. A unit that covers more of the query is
    # never folded among ones that cover less. Only the members past ``fold_keep`` fold. With
    # ``fold_keep=0``, every sibling folds into one card for the parent, which buries the best
    # answer: a pool that holds many experiments side by side folds every matching one into a
    # single card. How a unit was found is part of being equal: a folder that holds the word as
    # typed never folds among folders that hold it only inside a longer word.
    def group_of(card: Shown) -> tuple[str, UnitKind, int, str | None] | None:
        known = facts.get(card.unit.evidence.folder_id)
        if known is None or known.kind is UnitKind.PRODUCT or known.parent_id is None:
            return None
        way = found_by(card.unit.evidence)
        return (
            known.parent_id,
            known.kind,
            len(card.unit.evidence.covered),
            way.value if way is not None else None,
        )

    by_group: dict[tuple[str, UnitKind, int, str | None] | None, list[Shown]] = {}
    for card in kept:
        by_group.setdefault(group_of(card), []).append(card)
    folded: list[Shown] = []
    sent: dict[tuple[str, UnitKind, int, str | None], Shown] = {}
    shown_here: dict[tuple[str, UnitKind, int, str | None], int] = {}
    for card in kept:
        group_key = group_of(card)
        siblings = by_group.get(group_key, [])
        if group_key is None or len(siblings) < fold_at:
            folded.append(card)
            continue
        if shown_here.get(group_key, 0) < fold_keep:
            shown_here[group_key] = shown_here.get(group_key, 0) + 1
            folded.append(card)
            continue
        if group_key not in sent:
            sent[group_key] = Shown(unit=card.unit, group_parent=group_key[0])
            folded.append(sent[group_key])
        sent[group_key].folded.append(card.unit)

    # Step 6: cap product cards. The rest fold into one card at the last one's place.
    capped: list[Shown] = []
    products_shown = 0
    overflow: Shown | None = None
    for card in folded:
        known = facts.get(card.unit.evidence.folder_id)
        if known is None or known.kind is not UnitKind.PRODUCT or card.group_parent:
            capped.append(card)
            continue
        if products_shown < max_products:
            capped.append(card)
            products_shown += 1
            continue
        if overflow is None:
            overflow = Shown(unit=card.unit, more_products=True)
            capped.append(overflow)
        overflow.folded.append(card.unit)

    return capped[:limit]
