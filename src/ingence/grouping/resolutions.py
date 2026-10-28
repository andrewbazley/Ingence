"""Resolve an approved group for every experiment that shares its core set of terms.

Approving a core set of terms resolves every occurrence that shares it. Identical sets become a
group per experiment (each day its own) and one group across the experiments. For supersets and
subsets, the shared core is concluded as a group common to them all, and each deviation (a word
added, a word missing) is left as its own question. The resolution does not recurse into finer
groups. A group defined as four conditions is probably also a group in another experiment holding
those four and a fifth. The resolution does not go on to make a finer group of the five.

What the module reads: a core is a set of terms a person confirmed (``group:<terms>`` in the Record,
:func:`~ingence.record.targets.group_target`); an experiment's terms are the words its folder's
name and its acquisitions' names hold, read by the caller
(:func:`~ingence.rulebook.predicates.experiments.experiment_terms`).
Nothing here reads the Record or the Index. Sets go in and resolutions come out, so one definition
answers the inference and a test.

How an experiment relates to a core ``C``, its terms being ``T``:

- Identical: ``T == C``. The experiment is resolved under ``C`` with no deviation.
- Superset: ``C`` is a proper subset of ``T``. The experiment is resolved under ``C`` whatever
  ``T`` adds. Each added word is a deviation and is asked, however many there are. A proposal's
  core is what its members all hold, so a dozen more words is ordinary.
- Subset: ``T`` is a proper subset of ``C`` and holds at least ``minimum_core_terms`` words. The
  experiment is resolved, and each missing word is a deviation.
- Anything else (a partial overlap, too small a core) is not resolved by this core and raises
  nothing. The proposal's own questions still stand for it.

Each experiment gets one resolution. Where two approved cores both resolve an experiment, the
core that wins is, in this order, the one sharing the most terms with the experiment, the one with
fewer deviations, the one that fewer experiments hold (the more specific group), and the first in
canonical order. An experiment holding exactly an approved set is never asked about a word that
another, smaller, approved set lacks. A grouping rule gives a file one key, so an experiment
holding two approved cores is in one of their groups.

The group common to them all is, per core, the terms that every experiment it resolved shares.
That is the core itself where it resolved only identical sets and supersets, and smaller where a
subset joined. A subset joins only while that common set keeps ``minimum_core_terms`` terms
(largest subsets first, then by path), so one small subset cannot reduce a four-term group to one
word. A subset left out is not resolved. The common set is never resolved again against the other
cores, which is what "does not recurse" means.

The setting ``derivations: lattice`` (the flagged default of rule version 2) resolves groups as a
lattice of additive derivations. A group is a set of files with common themes, and approving one
confirms that the terms the engine saw together are linked. Approval flows only to what contains
it, so a subset is never resolved by a larger approved set. The most common core is asked first.
Then each derivation is asked as an addition to what is approved, and never as a question about a
missing term. The core resolves identical sets directly. Approving a derivation flags its own
derivations. An experiment stays its own unit, sitting in the group of every approved set it holds
(a day in its series and in its derivation's group, with scales never merged). The sets are the
closed term sets of the experiments: every set that is exactly the terms shared by the
experiments holding it, ordered by inclusion (a concept lattice, :func:`group_lattice`). The
module finds them by closing the intersections of the experiments' term sets in canonical order.
``maximum_concepts`` bounds how many it keeps, and the bound is stated where it is reached
(:attr:`Lattice.bound_reached`), never cut silently. The setting ``derivations: each_deviation``
gives the older behaviour, where every word added or missing is its own question.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "APPROVED",
    "ASKED",
    "DECLINED",
    "DECLINED_ABOVE",
    "DEFAULT_MAXIMUM_CONCEPTS",
    "DEFAULT_MINIMUM_EXPERIMENTS",
    "DERIVATION_MODES",
    "EACH_DEVIATION",
    "FEWEST_EXPERIMENTS",
    "IDENTICAL",
    "LATTICE",
    "NOT_ASKED",
    "SUBSET",
    "SUPERSET",
    "WAITING",
    "Concept",
    "Lattice",
    "Resolution",
    "ScopedAnswer",
    "core_confirming",
    "deviation_parts",
    "deviation_value",
    "group_lattice",
    "lattice_terms",
    "lattice_value",
    "resolve_by_lattice",
    "resolve_groups",
    "spelled",
]

IDENTICAL = "identical"
SUPERSET = "superset"
SUBSET = "subset"

#: How approvals spread (``derivations``): the lattice of additive derivations (the flagged
#: default of rule version 2) or every word added or missing as its own question.
LATTICE = "lattice"
EACH_DEVIATION = "each_deviation"
DERIVATION_MODES = (LATTICE, EACH_DEVIATION)

#: The most closed term sets a run keeps before it says the bound was reached. The pools
#: measured needed at most a few hundred sets at the default minimum of two terms, so the bound
#: leaves room of more than an order of magnitude.
DEFAULT_MAXIMUM_CONCEPTS = 5000

#: The fewest experiments a most general set must be held by for the lattice to ask it. A person
#: may lower it on the Rules page, never below :data:`FEWEST_EXPERIMENTS`: one experiment alone
#: shares a set with nobody.
DEFAULT_MINIMUM_EXPERIMENTS = 5
FEWEST_EXPERIMENTS = 2

#: A set's state, as a question: a person approved it, declined it, it is asked now, it waits for a
#: parent's approval, a set it adds to was declined, or it is asked of nobody (held by fewer
#: experiments than the run asks about, or the lattice's bound was reached).
APPROVED = "approved"
DECLINED = "declined"
ASKED = "asked"
WAITING = "waiting"
DECLINED_ABOVE = "declined_above"
NOT_ASKED = "not_asked"


def spelled(terms: Iterable[str]) -> str:
    """A set of terms as one canonical line: casefolded, each once, in sorted order."""
    return " ".join(sorted({term.strip().casefold() for term in terms if term.strip()}))


@dataclass(frozen=True, slots=True)
class Resolution:
    """How one experiment was resolved under one approved core."""

    #: The experiment's folder, by its entry identifier (two roots may each be a folder ``.``).
    experiment: str
    #: The core a person approved, in canonical order.
    core: tuple[str, ...]
    #: ``identical``, ``superset`` or ``subset``.
    relation: str
    #: The terms the experiment shares with the core.
    shared: tuple[str, ...]
    #: The words the experiment holds that the core does not (a superset's deviations).
    added: tuple[str, ...] = ()
    #: The words the core holds that the experiment does not (a subset's deviations).
    missing: tuple[str, ...] = ()
    #: The terms every experiment the core resolved shares: the group common to them all.
    common: tuple[str, ...] = ()
    #: How the experiment is named in a key a person reads: its folder's path, and its identifier
    #: where two experiments share a path (two roots each a folder ``.``).
    label: str = ""
    #: Under the lattice, every approved set the experiment holds, most specific first (the first
    #: is ``core``): the groups it sits in at each scale. Empty under ``each_deviation``.
    held: tuple[tuple[str, ...], ...] = ()

    @property
    def series(self) -> str:
        """The group of the most general approved set it holds: its series, across days."""
        return spelled(self.held[-1]) if self.held else self.across

    @property
    def within(self) -> str:
        """The group's key inside this experiment: each experiment (each day) its own."""
        return f"{spelled(self.shared)} in {self.label or self.experiment}"

    @property
    def across(self) -> str:
        """The group's key across the experiments the core resolved, naming the approved core.

        The core is part of the key. Two cores approved apart whose subsets narrow them to one
        common set are two groups, never one group that nobody approved.
        """
        common = spelled(self.common or self.shared)
        core = spelled(self.core)
        return common if common == core else f"{common} (of the approved {core})"

    @property
    def deviations(self) -> tuple[str, ...]:
        """Each deviation as the question it raises, added words first."""
        return tuple(
            [deviation_value(word, added=True, core=self.core) for word in self.added]
            + [deviation_value(word, added=False, core=self.core) for word in self.missing]
        )


def deviation_value(word: str, *, added: bool, core: Iterable[str]) -> str:
    """The question one deviation raises, in plain words, the same wherever it is raised."""
    return f"{word}{_ADDED if added else _MISSING}{spelled(core)}"


_ADDED = ", added to the group "
_MISSING = ", missing from the group "


def core_confirming(value: str) -> tuple[str, ...] | None:
    """The core set of terms whose approval confirms a deviation as a group of its own.

    A deviation is left as its own group for confirmation. Confirming a word added approves the
    core with the word (``group:<core and word>``), and confirming a word missing approves the core
    without it. An experiment holding exactly that set is then resolved under it, and the files
    that the deviation had left out are included. The result is None for a value that no
    deviation raises.
    """
    for marker, adds in ((_ADDED, True), (_MISSING, False)):
        word, found, core = value.partition(marker)
        if found and word and core:
            terms = set(core.split())
            return tuple(sorted(terms | {word} if adds else terms - {word}))
    return None


def deviation_parts(value: str) -> tuple[str, bool, tuple[str, ...]] | None:
    """What one deviation's question names: its word, whether it was added, and the core it lies in.

    The reverse of :func:`deviation_value`; None for a value no deviation raises.
    """
    for marker, added in ((_ADDED, True), (_MISSING, False)):
        word, found, core = value.partition(marker)
        if found and word and core.strip():
            return word, added, tuple(sorted(set(core.split())))
    return None


_LATTICE_MARK = "a group: "
_ADDS = " + "


def lattice_value(base: Iterable[str], added: Iterable[str]) -> str:
    """The question a closed set raises: a core alone, or what a derivation adds to its parents.

    ``a group: kelt vorn zib`` asks whether the core is a group, and ``a group: kelt vorn zib +
    brisa`` whether the core with ``brisa`` is: every question after the first is an addition. A
    term never holds a space, so the two parts part at the one spaced plus.
    """
    first = spelled(base)
    more = spelled(added)
    return (
        f"{_LATTICE_MARK}{first}{_ADDS}{more}"
        if first and more
        else f"{_LATTICE_MARK}{first or more}"
    )


def lattice_terms(value: str) -> tuple[str, ...] | None:
    """The whole set a lattice question asks about, in canonical order, or None for another value.

    Its answer is written on ``group:<these terms>``, as any group's is: the engine knows a
    derivation from a core by inclusion, so the Record needs no new target.
    """
    if not value.startswith(_LATTICE_MARK):
        return None
    base, _, added = value[len(_LATTICE_MARK) :].partition(_ADDS)
    terms = set(base.split()) | set(added.split())
    return tuple(sorted(terms)) or None


@dataclass(frozen=True, slots=True)
class ScopedAnswer:
    """An earlier answer that reaches only some experiments.

    A set answered under the earlier rules held a word that is not a term now (a month's, a
    replicate's). The answer is read without the word (``terms``), and it reaches only the
    experiments whose own names still hold that word (``experiments``), because an approval cannot
    apply to something it does not contain. ``approved`` is the answer's sense.
    """

    terms: tuple[str, ...]
    approved: bool
    experiments: frozenset[str]
    answered_as: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Concept:
    """One closed term set: the terms, the experiments holding them, its parents, its state."""

    #: The set, in canonical order.
    terms: tuple[str, ...]
    #: Every experiment holding the set (identifiers, sorted).
    experiments: tuple[str, ...]
    #: The closed sets immediately more general, each of at least ``minimum_core_terms`` terms.
    parents: tuple[tuple[str, ...], ...] = ()
    #: ``approved``, ``declined``, ``asked``, ``waiting``, ``declined_above`` or ``not_asked``.
    state: str = NOT_ASKED
    #: The experiments an earlier answer read without a word covers (:class:`ScopedAnswer`),
    #: where it covers some of the set's and not all: they are not asked it again.
    covered: tuple[str, ...] = ()

    @property
    def base(self) -> tuple[str, ...]:
        """What its parents share, which it adds to: empty for a most general set.

        Where the parents share no word, the set is spelled as an addition to its first parent,
        so it never reads like a most general set's question.
        """
        if not self.parents:
            return ()
        shared = frozenset.intersection(*(frozenset(p) for p in self.parents))
        return tuple(sorted(shared)) if shared else self.parents[0]

    @property
    def added(self) -> tuple[str, ...]:
        """The terms it adds to its parents' shared terms: all of them for a most general set."""
        return tuple(sorted(frozenset(self.terms) - frozenset(self.base)))

    @property
    def value(self) -> str:
        """The question it raises, the same wherever it is raised."""
        return lattice_value(self.base, self.added)


@dataclass(frozen=True, slots=True)
class Lattice:
    """The closed term sets of a run's experiments, and whether the bound on them was reached."""

    #: Ordered by how many experiments hold each (most first), then canonical order.
    concepts: tuple[Concept, ...]
    maximum_concepts: int
    #: True where the closing stopped at ``maximum_concepts``: the sets are then those of the
    #: experiments read before it, in canonical order, and a reader must say so.
    bound_reached: bool = False


def _closure(wanted: frozenset[str], held: Mapping[str, frozenset[str]]) -> frozenset[str] | None:
    """The closed set with the experiments holding ``wanted``: what they all share, or None."""
    holding = [found for found in held.values() if wanted <= found]
    return frozenset.intersection(*holding) if holding else None


def group_lattice(
    terms: Mapping[str, Iterable[str]],
    *,
    minimum_core_terms: int = 2,
    maximum_concepts: int = DEFAULT_MAXIMUM_CONCEPTS,
    approved: Iterable[Iterable[str]] = (),
    declined: Iterable[Iterable[str]] = (),
    minimum_experiments: int = DEFAULT_MINIMUM_EXPERIMENTS,
    scoped: Iterable[ScopedAnswer] = (),
) -> Lattice:
    """The closed term sets of the experiments, each with its state as a question.

    ``terms`` maps each experiment to the terms it holds; ``approved`` and ``declined`` are the sets
    a person confirmed or rejected (``group:<terms>``). A set that a person named and that is not
    closed stands for its closure, the set every experiment holding it shares (an earlier answer,
    or a subset of words ticked on the page). The closure has the same experiments, so it raises
    the same question.

    A most general set (no parent of ``minimum_core_terms`` or more terms) held by
    ``minimum_experiments`` or more experiments (five by default) is asked until
    answered; a derivation is asked once every parent is approved,
    waits while any is unanswered, and is not asked through a declined parent. Deterministic: the
    experiments are closed in canonical order, and the bound, where reached, is stated.
    """
    held = {
        experiment: frozenset(t.casefold() for t in found) for experiment, found in terms.items()
    }
    intents = sorted(
        {found for found in held.values() if len(found) >= minimum_core_terms},
        key=lambda found: (len(found), sorted(found)),
    )
    closed: set[frozenset[str]] = set()
    reached = False
    for intent in intents:
        found = {intent} | {
            shared for known in closed if len(shared := known & intent) >= minimum_core_terms
        }
        if len(closed | found) > maximum_concepts:
            reached = True
            break
        closed |= found
    extents = {
        concept: tuple(sorted(e for e, found in held.items() if concept <= found))
        for concept in closed
    }
    parents: dict[frozenset[str], tuple[tuple[str, ...], ...]] = {}
    for concept, extent in extents.items():
        inside = set(extent)
        # A parent is the concept's terms shared with an experiment outside it, the largest
        # such. Every more general closed set is held by some experiment that the concept is not.
        general = {
            shared
            for e, found in held.items()
            if e not in inside
            and len(shared := concept & found) >= minimum_core_terms
            and shared in closed
        }
        nearest = [p for p in general if not any(p < other for other in general)]
        parents[concept] = tuple(sorted(tuple(sorted(p)) for p in nearest))
    approved_sets = {
        closure
        for wanted in approved
        if (closure := _closure(frozenset(t.casefold() for t in wanted), held)) is not None
    }
    declined_sets = {
        closure
        for wanted in declined
        if (closure := _closure(frozenset(t.casefold() for t in wanted), held)) is not None
    } - approved_sets
    # An earlier answer read without a word decides a set only where it covers every
    # experiment holding it. Otherwise the set is asked of the rest, and not again of the covered.
    covered: dict[frozenset[str], set[str]] = {}
    for answer in scoped:
        closure = _closure(frozenset(t.casefold() for t in answer.terms), held)
        if closure is None:
            continue
        holding = {e for e, found in held.items() if closure <= found}
        answered_for = holding & answer.experiments
        if not answered_for:
            continue
        if answered_for == holding:
            (approved_sets if answer.approved else declined_sets).add(closure)
        else:
            covered.setdefault(closure, set()).update(answered_for)
    declined_sets -= approved_sets
    states: dict[frozenset[str], str] = {}
    # Under the bound nothing is asked. A partial closure can miss a set's true parent and ask an
    # addition before it. The sets found are shown, and the decisions are kept.
    unasked = (ASKED, WAITING) if reached else ()
    for concept in sorted(closed, key=lambda c: (len(c), sorted(c))):  # parents first
        above = [states.get(frozenset(p), NOT_ASKED) for p in parents[concept]]
        if concept in approved_sets:
            states[concept] = APPROVED
        elif concept in declined_sets:
            states[concept] = DECLINED
        elif not above:
            held_by = len(extents[concept])
            fewest = max(FEWEST_EXPERIMENTS, minimum_experiments)
            states[concept] = ASKED if held_by >= fewest else NOT_ASKED
        elif any(state in (DECLINED, DECLINED_ABOVE) for state in above):
            states[concept] = DECLINED_ABOVE
        elif all(state == APPROVED for state in above):
            states[concept] = ASKED
        else:
            states[concept] = WAITING
        if states[concept] in unasked:
            states[concept] = NOT_ASKED
    concepts = sorted(
        (
            Concept(
                terms=tuple(sorted(concept)),
                experiments=extents[concept],
                parents=parents[concept],
                state=states[concept],
                covered=tuple(sorted(covered.get(concept, ()))),
            )
            for concept in closed
        ),
        key=lambda c: (-len(c.experiments), c.terms),
    )
    return Lattice(
        concepts=tuple(concepts), maximum_concepts=maximum_concepts, bound_reached=reached
    )


def resolve_by_lattice(
    approved: Iterable[Iterable[str]],
    terms: Mapping[str, Iterable[str]],
    *,
    labels: Mapping[str, str] | None = None,
    scoped: Iterable[ScopedAnswer] = (),
) -> dict[str, Resolution]:
    """Resolve every experiment holding an approved set, under each it holds.

    Approval flows only to what contains it: an experiment is resolved by every approved set
    it holds, never by one it lacks a term of. Its label is the most specific (the most terms,
    then the set fewer experiments hold, then canonical order), which names its group across the
    experiments; the most general names its series (:attr:`Resolution.series`). Nothing is
    deviated from, so nothing is asked here: the lattice's questions are :func:`group_lattice`'s.
    """
    reaching: dict[frozenset[str], frozenset[str] | None] = {
        frozenset(t.casefold() for t in wanted): None for wanted in approved if wanted
    }
    for answer in scoped:
        if answer.approved and answer.terms:
            wanted = frozenset(t.casefold() for t in answer.terms)
            if wanted not in reaching or reaching[wanted] is not None:
                reaching[wanted] = (reaching.get(wanted) or frozenset()) | answer.experiments
    sets = sorted(reaching, key=lambda wanted: (len(wanted), sorted(wanted)))  # a total order
    held = {e: frozenset(t.casefold() for t in found) for e, found in terms.items()}
    holders = {wanted: sum(1 for found in held.values() if wanted <= found) for wanted in sets}
    result: dict[str, Resolution] = {}
    for experiment in sorted(held):
        found = held[experiment]
        holding = sorted(
            (
                wanted
                for wanted in sets
                if wanted <= found
                # An answer read without a word reaches only the experiments naming it.
                and _reaches(reaching[wanted], experiment)
            ),
            key=lambda wanted: (-len(wanted), holders[wanted], sorted(wanted)),
        )
        if not holding:
            continue
        label = tuple(sorted(holding[0]))
        result[experiment] = Resolution(
            experiment=experiment,
            core=label,
            relation=IDENTICAL if found == holding[0] else SUPERSET,
            shared=label,
            common=label,
            label=(labels or {}).get(experiment, experiment),
            held=tuple(tuple(sorted(wanted)) for wanted in holding),
        )
    return result


def _reaches(scope: frozenset[str] | None, experiment: str) -> bool:
    """Whether an approved set reaches an experiment: every one, or only those in its scope."""
    return scope is None or experiment in scope


def _relate(
    experiment: str,
    core: frozenset[str],
    terms: frozenset[str],
    *,
    minimum_core_terms: int,
) -> Resolution | None:
    ordered = tuple(sorted(core))
    if terms == core:
        return Resolution(experiment, ordered, IDENTICAL, ordered)
    if core < terms:
        # Every experiment holding the core shares it, and every word it adds is a question.
        # A proposal's core is what all its members hold, so each member adds words, often a
        # dozen. The function asks each, however many.
        return Resolution(experiment, ordered, SUPERSET, ordered, added=tuple(sorted(terms - core)))
    if terms < core and len(terms) >= minimum_core_terms:
        return Resolution(
            experiment,
            ordered,
            SUBSET,
            tuple(sorted(terms)),
            missing=tuple(sorted(core - terms)),
        )
    return None


def resolve_groups(
    cores: Iterable[Iterable[str]],
    terms: Mapping[str, Iterable[str]],
    *,
    minimum_core_terms: int = 2,
    labels: Mapping[str, str] | None = None,
) -> dict[str, Resolution]:
    """Resolve every experiment sharing an approved core, by the experiment's identifier.

    ``cores`` are the sets of terms a person approved; ``terms`` maps each experiment (its folder's
    entry identifier) to the terms it holds; ``labels`` names each for a key a person reads.
    Returns the experiments resolved, each under the one core that fits it best, with the group
    common to that core's experiments filled in.
    """
    approved = sorted(
        {frozenset(t.casefold() for t in core) for core in cores if core},
        key=lambda core: (-len(core), sorted(core)),
    )
    held = {path: frozenset(t.casefold() for t in found) for path, found in terms.items()}
    # How many experiments hold each core: of two cores an experiment holds equally, the one fewer
    # experiments hold is the more specific group, and it wins. For example, a pair of
    # nucleotides held by most experiments loses to a pair of proteins held by a few.
    holders = {core: sum(1 for found in held.values() if core <= found) for core in approved}
    chosen: dict[str, Resolution] = {}
    for path in sorted(held):
        best: tuple[tuple[int, int, int, int], Resolution] | None = None
        for rank, core in enumerate(approved):
            found = _relate(path, core, held[path], minimum_core_terms=minimum_core_terms)
            if found is None:
                continue
            order = (
                -len(found.shared),
                len(found.added) + len(found.missing),
                holders[core],
                rank,
            )
            if best is None or order < best[0]:
                best = (order, found)
        if best is not None:
            chosen[path] = best[1]

    by_core: dict[tuple[str, ...], list[Resolution]] = {}
    for resolution in chosen.values():
        by_core.setdefault(resolution.core, []).append(resolution)
    result: dict[str, Resolution] = {}
    for core_terms, resolved in by_core.items():
        common = frozenset(core_terms)
        admitted = [r for r in resolved if r.relation != SUBSET]
        subsets = sorted(
            (r for r in resolved if r.relation == SUBSET),
            key=lambda r: (-len(r.shared), r.experiment),
        )
        for resolution in subsets:
            narrowed = common & frozenset(resolution.shared)
            if len(narrowed) < minimum_core_terms:
                continue
            common = narrowed
            admitted.append(resolution)
        shared_by_all = tuple(sorted(common))
        for resolution in admitted:
            result[resolution.experiment] = Resolution(
                experiment=resolution.experiment,
                core=resolution.core,
                relation=resolution.relation,
                shared=resolution.shared,
                added=resolution.added,
                missing=resolution.missing,
                common=shared_by_all,
                label=(labels or {}).get(resolution.experiment, resolution.experiment),
            )
    return dict(sorted(result.items()))
