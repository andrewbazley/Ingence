"""Words that stand in the same place: the paradigmatic reading of one folder's names.

The idea, in plain words. Names written by one person for one experiment are written from a
template: ``<sample>_<reagent>_<replicate>``. Line the names up by place and the second place holds
``atp``, ``adp``, ``tcep``: words that stand *instead of one another*, which linguists call a
paradigm. If the Dictionary already knows three of them are reagents, a fourth nobody has defined
may be one too, and a person can be asked what it is, with the three shown. The reading is counts
over aligned names. It binds nothing and concludes nothing.

What is aligned: names in one folder whose components have the same *types* in the same order
(``word``, ``indexed``, ``quantity``, ``extension``...). The census records that order (each
name's slots) and what the Dictionary made of each component. The skeleton the census stores also
spells the bound kinds (``{reagent}`` where a word bound). That would put a bound and an unbound
word in the same place under two skeletons, so the alignment key is the types alone.

What counts as bound: a component that the Dictionary resolved to a sense on some axis (its theme
kind). A component that the binder examined and found nothing for is *unbound*, which is what a
word question asks about. A component that the binder found several senses for (a conflict) is
neither, and is left out of both counts. It has a question of its own.

The module is pure and small. Nothing here reads a project. The callers (the flagging rule, which
is handed a folder's records, and the page function, which reads a catalog) each build
:class:`AlignedName` from what they hold, so the two cannot disagree about a folder. The few
helpers that put a place into words live here too, so the rule's reason and the page's sentence
say a place the same way.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

__all__ = [
    "ASKED_SHOWN",
    "BOUND",
    "DEFAULTS",
    "UNBOUND",
    "WORD",
    "WORDS_SHOWN",
    "AlignedName",
    "Paradigm",
    "PlaceReading",
    "Slot",
    "known_there",
    "listed",
    "ordinal",
    "paradigms",
    "places_of_a_kind",
    "plural",
    "said_as_a_kind",
    "spelled_like",
    "spelling_test",
]

#: A component the Dictionary resolved to a sense.
BOUND = "bound"
#: A component the binder examined and found no sense for.
UNBOUND = "unbound"

#: The one component type a word question asks about. A quantity, a date or an index is read by
#: its own rules. A word is what the Dictionary is the only judge of.
WORD = "word"

#: The rule's three parameters at their defaults, written once. The flagging rule's predicate, the
#: page function and the revision that declares the rule all read them from here. The defaults are
#: unmeasured until a person has read what they raise. Two bound words is the fewest a reader would
#: call a pattern. Three quarters agreeing is the fewest that lets one stray axis not spoil the
#: rest. Three names is the fewest that make a template.
DEFAULTS: Mapping[str, int | float] = {"minimum_bound": 2, "agreement": 0.75, "minimum_names": 3}

#: How many known words a sentence names beside a word. The rest are counted, never dropped.
WORDS_SHOWN = 5

#: How many words nothing binds one place's reason names. The finding raises every one of them.
ASKED_SHOWN = 3


@dataclass(frozen=True, slots=True)
class Slot:
    """One place in one name: what was written, its type, and what the Dictionary made of it."""

    token: str
    component_type: str
    #: :data:`BOUND`, :data:`UNBOUND`, or None where the binder found a conflict or never looked.
    state: str | None = None
    #: The axis the bound sense sits on (``reagent``), for a bound slot.
    kind: str | None = None
    #: The records this slot rests on: the component's and its binding's identifiers.
    records: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlignedName:
    """One name as the census cut it: its folder and its slots, in the order written."""

    entry_id: str
    folder_id: str
    slots: tuple[Slot, ...]
    #: The name as written, for showing a place by an example a person recognises.
    name: str = ""

    @property
    def shape(self) -> tuple[str, ...]:
        """The template the name was written from: its components' types, in order."""
        return tuple(slot.component_type for slot in self.slots)


@dataclass
class Paradigm:
    """One place of one template in one folder, and every word written there."""

    folder_id: str
    shape: tuple[str, ...]
    #: Zero-based. A page says "the second part" for position 1.
    position: int
    #: How many names wrote each word here.
    values: Counter[str] = field(default_factory=Counter)
    #: The axis each bound word sits on, and the records behind it.
    bound: dict[str, str] = field(default_factory=dict)
    unbound: dict[str, set[str]] = field(default_factory=dict)
    evidence: dict[str, set[str]] = field(default_factory=dict)
    names: int = 0
    #: One name written from the template, the first in canonical order: the place's example.
    example: str = ""

    @property
    def varies(self) -> bool:
        """Whether more than one word is written here: a place that never varies is a constant."""
        return len(self.values) > 1

    def kind_agreement(self) -> tuple[str | None, int, int]:
        """The axis most bound words here share, how many share it, and how many are bound.

        Ties break by the axis's spelling, so two readings of one folder never differ.
        """
        if not self.bound:
            return None, 0, 0
        counted = Counter(self.bound.values())
        (kind, agreeing), *_ = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
        return kind, agreeing, len(self.bound)


def paradigms(
    names: Iterable[AlignedName], *, minimum_names: int = int(DEFAULTS["minimum_names"])
) -> list[Paradigm]:
    """Every word place, in every folder, of a template at least ``minimum_names`` names share.

    Grouped by (folder, template): a template is one person's habit in one place, and pooling one
    template across the pool would line ``<sample>_atp`` in one project up with ``Plate_A1`` in
    another. Canonical order (folder, template, position), so the same folder always reads alike.
    """
    groups: dict[tuple[str, tuple[str, ...]], list[AlignedName]] = defaultdict(list)
    for name in names:
        groups[(name.folder_id, name.shape)].append(name)
    found: list[Paradigm] = []
    for (folder_id, shape), members in sorted(groups.items()):
        if len(members) < minimum_names:
            continue
        example = min((member.name, member.entry_id) for member in members)[0]
        for position, component_type in enumerate(shape):
            if component_type != WORD:
                continue
            place = Paradigm(folder_id=folder_id, shape=shape, position=position, example=example)
            place.names = len(members)
            for member in members:
                slot = member.slots[position]
                place.values[slot.token] += 1
                if slot.state == BOUND and slot.kind:
                    place.bound.setdefault(slot.token, slot.kind)
                    place.evidence.setdefault(slot.token, set()).update(slot.records)
                elif slot.state == UNBOUND:
                    place.unbound.setdefault(slot.token, set()).update(slot.records)
            found.append(place)
    return found


@dataclass(frozen=True, slots=True)
class PlaceReading:
    """One place where words of one kind stand beside words nothing binds: the engine's reading."""

    folder_id: str
    position: int
    #: The bound words that stand here on the leading axis, canonical order.
    alongside: tuple[str, ...]
    kind: str
    #: How many bound words here share the axis, of how many are bound.
    agreeing: int
    bound: int
    #: The unbound words that stand here, canonical order.
    unbound: tuple[str, ...]
    #: How many names of the folder were written from this template.
    names: int
    #: The template, so two templates of one folder with a word at the same place are two places.
    shape: tuple[str, ...] = ()
    #: One name written from the template, for showing the place.
    example: str = ""
    #: How many names wrote each unbound word here.
    counts: dict[str, int] = field(default_factory=dict, compare=False)
    records: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False)

    def where(self) -> str:
        """The place in words a person reads without the glossary."""
        such = f", such as {self.example}" if self.example else ""
        return f"the {ordinal(self.position + 1)} part of {self.names} names written alike{such}"


def places_of_a_kind(
    found: Iterable[Paradigm],
    *,
    minimum_bound: int = int(DEFAULTS["minimum_bound"]),
    agreement: float = float(DEFAULTS["agreement"]),
) -> list[PlaceReading]:
    """The places where at least ``minimum_bound`` bound words stand, at least ``agreement`` of
    them on one axis, beside at least one word nothing binds.

    Both parameters are named, and both are unmeasured until a person has read what they raise
    (:data:`DEFAULTS` says why each is where it is).
    """
    readings: list[PlaceReading] = []
    for place in found:
        # A word standing bound in some names and unbound in others (an extension-only spelling
        # met as a plain word) is a bound word here, not a question.
        asked = sorted(word for word in place.unbound if word not in place.bound)
        if not place.varies or not asked:
            continue
        kind, agreeing, bound = place.kind_agreement()
        if kind is None or bound < minimum_bound or agreeing < minimum_bound:
            continue
        if agreeing / bound < agreement:
            continue
        beside = tuple(sorted(word for word, axis in place.bound.items() if axis == kind))
        readings.append(
            PlaceReading(
                folder_id=place.folder_id,
                position=place.position,
                alongside=beside,
                kind=kind,
                agreeing=agreeing,
                bound=bound,
                unbound=tuple(asked),
                names=place.names,
                shape=place.shape,
                example=place.example,
                counts={word: place.values[word] for word in asked},
                records={word: tuple(sorted(place.unbound[word])) for word in asked}
                | {word: tuple(sorted(place.evidence[word])) for word in beside},
            )
        )
    return readings


def spelled_like(name: str, spellings: Iterable[str]) -> bool:
    """Whether a name is one of these spellings, or begins or ends with one and holds more.

    How the Dictionary's spellings of files nobody chose to make are read: ``Thumbs.db``
    as a whole name, ``._`` at the start, ``.part`` at the end. Exact and case-sensitive, because
    a tool writes its mark the same way every time.
    """
    return spelling_test(spellings)(name)


def spelling_test(spellings: Iterable[str]) -> Callable[[str], bool]:
    """:func:`spelled_like` for many names against one set of spellings, read once.

    Reading every name of a large census against every spelling one at a time would take most of
    the Questions page's time. A name that begins or ends with a spelling and holds more is one
    that begins or ends with it and is not it, so one test of the whole set answers the same.
    """
    surfaces = tuple(sorted({surface for surface in spellings if surface}))
    whole = frozenset(surfaces)

    def spelled(name: str) -> bool:
        return name in whole or name.startswith(surfaces) or name.endswith(surfaces)

    return spelled


# --- a place, in words --------------------------------------------------------------------

_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")


def ordinal(number: int) -> str:
    """``1`` as "first", ``12`` as "12th": how a person counts the parts of a name."""
    if 1 <= number <= len(_ORDINALS):
        return _ORDINALS[number - 1]
    suffix = (
        "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    )
    return f"{number}{suffix}"


def said_as_a_kind(kind: str) -> str:
    """A kind as a person reads it: ``file_role`` as "file role", never its internal spelling."""
    return kind.replace("_", " ").strip()


def plural(kind: str) -> str:
    """A kind, said of several words: "reagents", "file roles", "qualities"."""
    said = said_as_a_kind(kind)
    if said.endswith(("s", "x", "ch", "sh")):
        return f"{said}es"
    if len(said) > 1 and said.endswith("y") and said[-2] not in "aeiou":
        return f"{said[:-1]}ies"
    return f"{said}s"


def listed(words: Sequence[str], *, shown: int = WORDS_SHOWN) -> str:
    """Words as a sentence lists them, the rest counted: "atp, adp and tcep", "a, b and 3 more"."""
    named = list(words[:shown])
    more = len(words) - len(named)
    if more:
        return f"{', '.join(named)} and {more} more"
    if len(named) <= 1:
        return "".join(named)
    return f"{', '.join(named[:-1])} and {named[-1]}"


def known_there(agreeing: int, bound: int, kind: str) -> str:
    """How many of the words the Dictionary knows in a place are of the leading kind, in words."""
    said = said_as_a_kind(kind)
    article = "an" if said[:1] in "aeiou" else "a"
    if bound == 1:
        return f"the one word known there is {article} {said}"
    if agreeing == bound:
        return f"all {bound} words known there are {plural(kind)}"
    if agreeing == 1:
        return f"1 of the {bound} words known there is {article} {said}"
    return f"{agreeing} of the {bound} words known there are {plural(kind)}"
