"""The proposing phase: candidates drawn within bounded indexes, and what constrains each.

Pairs are never drawn from the pool. A proposing rule names the index it draws its pairs within,
``candidates_within``. The index is either a grouping rule, whose classes the grouping phase formed
and claimed, or ``parent``, the files of one folder. A proposing rule naming no index proposes
nothing and says so. Candidate generation that is all-pairs is a run that never finishes on a real
pool. Generation bounded by a stated equivalence is a set of proposals a person can read.

Only members whose claim into their class stands are drawn. A member that a defeater retracted is
out, so a link sharing a family's words is never paired. An index whose rule publishes no claim
leaves the proposing rule not ready, because a class nothing may claim is not a class anything
should be drawn from.

Each member takes its turn as the subject, in canonical order, with the other members as its
counterparts, also in canonical order. Both directions of a pair arise, and the proposing predicate
supplies the direction. A subject with more counterparts than a bound allows is cut, and the cut is
a claim, because a bounded answer and a complete one are different answers.

A pair is formed only where its ends' own order admits one, and pairs stream. A grouping's classes
can offer millions of ordered pairs to a derivation rule that proposes none, because no pair holds
an earlier stage and a later one. Handing every subject a copy of its whole class before any pair is
read would make that the pass's peak. A proposing predicate may rank each end on its own
(:class:`~ingence.rulebook.registry.RanksPairEnds`). Each member of a class is ranked once, before
any pair is formed, and a pair is formed only where the target stands far enough above the source. A
class holding no such two forms nothing. The ranks are a contract: the candidates are the same as
those that drawing every pair would give. A subject's counterparts are a view over its class and
never a copy, and a bound reads them through it. The draws are a generator, so the phase holds its
classes once and never a copy of one per subject. ``pairs`` still counts what the index offered
within the bounds, and ``formed`` counts what was handed to the predicate.

A pair is formed only where a key joins its ends, looked up. A proposing predicate may say its
pairs run from the entry a class's key names
(:class:`~ingence.rulebook.registry.DrawsFromTheClassKey`). Each other member of the class is then
a subject with that one entry as its counterpart, and the pair runs from it. So a class of files
named for one acquisition forms one pair per file and never its square, and a class whose key
names no member forms nothing.

The bound ranks by the evidence's strength, then by identifier. A subject's counterparts are
offered strongest first. For a file's mentions, those its calls name come first, then those
resolved beside it, then those from a root, then those by name alone. Within one strength the order
is canonical. So where a bound cuts, it cuts the weakest, and a subject whose key offers one
counterpart is never cut.

Every candidate is published through one function, :func:`candidates_to_publish`, which is where a
filter of what is written belongs.

A candidate is not a claim. It is a proposal, recorded with its support whole and with every pair
constraint in force on its primitive, fired or not. A constraint that refused it makes it
``blocked``, and one that could not be evaluated is recorded as what would. With no refusal a
candidate is ``circumstantial``: ``anchored`` and ``direct`` are not graded here.
"""

from __future__ import annotations

import bisect
import itertools
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, overload

from ingence.core.ids import candidate_id
from ingence.core.relations import Primitive
from ingence.core.schema import Candidate, Claim, Defeater
from ingence.core.tiers import DecisionTier, ResolutionStatus
from ingence.infer.claims import constraint_defeater, truncation_claim_for, why_not_claim_ready
from ingence.infer.evaluate import (
    cut_for_subject,
    parameters_in_force,
    refusal_of_pair,
    support_for_pair,
)
from ingence.infer.subjects import CandidateSet, PairRecords
from ingence.rulebook.registry import (
    CO_MENTION_INDEX,
    MENTION_INDEX,
    PARENT_INDEX,
    DrawsFromTheClassKey,
    PairsWithinTheClass,
    PredicateNotEvaluableError,
    RanksPairEnds,
    Reads,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from ingence.core.schema import Assertion, Defeater, Rule
    from ingence.infer.references import Resolution
    from ingence.infer.subjects import EntryRecords
    from ingence.record.answers import AnswersRead, Spelling
    from ingence.rulebook.registry import Predicate
    from ingence.scan.cancel import CancellationToken

__all__ = ["ProposalTally", "Proposed", "candidates_to_publish", "propose"]


@dataclass
class ProposalTally:
    """What one proposing rule came to across a run."""

    rule_id: str
    rule_version: str
    #: The index the rule drew its pairs within, or None where it names none.
    within: str | None = None
    #: Ordered pairs the index offered within the bounds.
    pairs: int = 0
    #: Of those, the pairs formed and handed to the proposing predicate: every one, unless the
    #: predicate ranks the ends and the ranks refuse it before it is formed.
    formed: int = 0
    #: Pairs the predicate found support for, each of them a candidate.
    supported: int = 0
    circumstantial: int = 0
    blocked: int = 0
    #: Subjects whose counterparts a bound cut.
    truncated: int = 0
    #: Pairs left unpublished because a person answered them on the same evidence.
    answered_before: int = 0
    #: How many candidates each constraint refused.
    blocked_by: Counter[str] = field(default_factory=Counter)
    #: Why nothing was drawn, where nothing could be.
    not_ready: str | None = None
    #: Why the rule could not be evaluated at all, where it could not.
    not_evaluable: str | None = None


@dataclass(frozen=True, slots=True)
class Proposed:
    """What the proposing phase produced for one rule."""

    candidates: tuple[Candidate, ...] = ()
    #: One claim per subject a bound cut, per index.
    truncations: tuple[Claim, ...] = ()
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class _Member:
    """One member of a class of an index, and what places it there."""

    records: EntryRecords
    #: The member's claim into its class, the containment edge from its folder, or the
    #: observation of the mention that named it.
    membership: str
    #: The records its place in the class rests on, which a truncation claim cites.
    key_records: tuple[str, ...]
    #: The resolved mentions that placed it, where the index is drawn from a resolution.
    resolutions: tuple[Resolution, ...] = ()

    @property
    def entry_id(self) -> str:
        return self.records.entry.entry_id


@dataclass(frozen=True, slots=True)
class _Draw:
    """One subject and the counterparts its index offers it, before any bound cuts them.

    A class hands every member the others. A resolution hands the mentioning file what its
    mentions name (``mentioned``), or hands each thing one file names the others it names
    beside it (``co_mentioned``), with the file that named them as ``describing``.

    The counterparts are the pool but for the subject's own place in it, so every draw of one class
    shares the class's tuple and none holds a copy of it.
    """

    subject: _Member
    #: The class or the named files the counterparts are drawn from, in canonical order.
    pool: tuple[_Member, ...]
    #: The subject's place in ``pool``, which no pair is formed with; None where the subject is
    #: not in it (a mentioning file, whose counterparts are every file it names).
    position: int | None
    describing: EntryRecords | None = None
    #: Every member of a grouping's class, the subject among them, handed to each pair so a
    #: predicate can say what the class holds. It is empty for a folder or a resolution.
    members: tuple[EntryRecords, ...] = ()
    #: True where the subject is each pair's target: a member drawn by its class's key looks up
    #: the entry the key names, which is the pair's source.
    reversed: bool = False


def propose(
    rule: Rule,
    records: Sequence[EntryRecords],
    *,
    tally: ProposalTally,
    published: Sequence[Claim],
    edges: Sequence[Assertion],
    unpublished: Mapping[str, str],
    constraints: Sequence[Rule],
    predicates: Mapping[str, Predicate],
    shapes: Mapping[str, Reads],
    in_force: Mapping[str, Rule],
    run_id: str,
    token: CancellationToken | None = None,
    defeat: Callable[[EntryRecords], tuple[Defeater, ...]] | None = None,
    mentioned: Mapping[str, tuple[Resolution, ...]] | None = None,
) -> Proposed:
    """Draw one proposing rule's pairs within its index, and return its candidates.

    ``records`` are the entries at the rule's level, carrying the claims the earlier phases
    published about them. ``unpublished`` says, for each grouping rule whose classes may not be
    drawn within, why not. The function lets
    :class:`~ingence.rulebook.registry.PredicateNotEvaluableError` through from the proposing
    predicate, because it is a fact about the rule and the caller records it once and not once per
    pair.

    ``defeat`` evaluates the rule's own defeaters on one entry. A proposing rule naming defeaters
    has them read on both ends of every pair it supports. A pair is about both its files, so a
    defeater that fires on either fires on the pair. The candidate is published blocked with the
    defeater recorded, as a refusing constraint's is, and is never dropped.

    ``mentioned`` holds each mentioning entry's resolved mentions. A rule drawing within
    ``mentioned`` or ``co_mentioned`` takes its pairs from them: the resolution decides which pairs
    exist, and the predicate decides which it supports.
    """
    tally.within = rule.candidates_within
    if rule.candidates_within is None:
        tally.not_ready = "names no index to draw candidates within, so it proposes nothing"
        return Proposed()
    primitive = rule.primitive
    if primitive is None:
        tally.not_ready = "names no primitive, so a candidate could not say what it proposes"
        return Proposed()
    if rule.candidates_within in (MENTION_INDEX, CO_MENTION_INDEX):
        if mentioned is None:
            tally.not_ready = (
                f"draws candidates within {rule.candidates_within}, and no mention was resolved"
            )
            return Proposed()
        draws: Iterator[_Draw] = _from_mentions(rule.candidates_within, records, mentioned)
    else:
        classes, reason = _classes(
            rule.candidates_within,
            records,
            published=published,
            edges=edges,
            unpublished=unpublished,
            in_force=in_force,
        )
        if reason is not None:
            tally.not_ready = reason
            return Proposed()
        keyed = predicates[rule.rule_id]
        if isinstance(keyed, DrawsFromTheClassKey):
            draws = _from_class_keys(
                rule.candidates_within, classes, keyed, parameters_in_force(rule)
            )
        elif isinstance(keyed, PairsWithinTheClass):
            draws = _from_class_pairs(
                rule.candidates_within, classes, keyed, parameters_in_force(rule)
            )
        else:
            draws = _from_classes(rule.candidates_within, [members for _, members in classes])

    predicate = predicates[rule.rule_id]
    ranks = _Ranks.of(predicate, rule)
    bounds = [
        constraint
        for constraint in constraints
        if shapes.get(constraint.rule_id) is Reads.CANDIDATES
        and constraint.primitive in (None, primitive)
    ]
    on_pairs = [
        constraint
        for constraint in constraints
        if shapes.get(constraint.rule_id) is Reads.PAIR and constraint.primitive is primitive
    ]
    candidates: dict[str, Candidate] = {}
    truncations: dict[str, Claim] = {}
    for draw in draws:
        subject = draw.subject
        if token is not None and token.cancelled:
            return Proposed(cancelled=True)
        kept = _within_bounds(
            subject,
            draw,
            within=rule.candidates_within,
            bounds=bounds,
            predicates=predicates,
            in_force=in_force,
            tally=tally,
            truncations=truncations,
            run_id=run_id,
        )
        tally.pairs += kept
        for counterpart in _formed(draw, kept, ranks):
            source, target = (counterpart, subject) if draw.reversed else (subject, counterpart)
            pair = PairRecords(
                source=source.records,
                target=target.records,
                within=rule.candidates_within,
                source_membership=source.membership,
                target_membership=target.membership,
                resolutions=(
                    counterpart.resolutions
                    if draw.describing is None
                    else subject.resolutions + counterpart.resolutions
                ),
                describing=draw.describing,
                members=draw.members,
            )
            tally.formed += 1
            support = support_for_pair(rule, predicate, pair)
            if not support:
                continue
            tally.supported += 1
            own = (
                _on_both_ends(defeat(source.records), defeat(target.records))
                if defeat is not None and rule.defeated_by
                else ()
            )
            defeaters = own + tuple(
                _constrained(constraint, pair, predicates) for constraint in on_pairs
            )
            refused = [defeater.rule_id for defeater in defeaters if defeater.fired]
            tally.blocked_by.update(refused)
            if refused:
                tally.blocked += 1
            else:
                tally.circumstantial += 1
            candidate = Candidate(
                candidate_id=candidate_id(
                    source=source.entry_id,
                    target=target.entry_id,
                    primitive=primitive,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    scope=rule.scope,
                    respect=rule.respect,
                ),
                run_id=run_id,
                source_id=source.entry_id,
                target_id=target.entry_id,
                primitive=primitive,
                respect=rule.respect,
                specialization=rule.specialization,
                rule_id=rule.rule_id,
                rule_version=rule.version,
                scope=rule.scope,
                level=rule.level,
                decision_tier=DecisionTier.BLOCKED if refused else DecisionTier.CIRCUMSTANTIAL,
                support=support,
                defeaters=defeaters,
            )
            candidates[candidate.candidate_id] = candidate
    return Proposed(
        candidates=tuple(candidates[key] for key in sorted(candidates)),
        truncations=tuple(truncations[key] for key in sorted(truncations)),
    )


def _on_both_ends(
    source: tuple[Defeater, ...], target: tuple[Defeater, ...]
) -> tuple[Defeater, ...]:
    """Return one defeater per defeating rule for a pair: fired where it fired on either end."""
    by_rule = {defeater.rule_id: defeater for defeater in target}
    combined = []
    for defeater in source:
        other = by_rule.get(defeater.rule_id)
        if other is None:
            combined.append(defeater)
            continue
        combined.append(
            defeater.model_copy(
                update={
                    "fired": defeater.fired or other.fired,
                    "observations": tuple(sorted({*defeater.observations, *other.observations})),
                }
            )
        )
    return tuple(combined)


def _from_classes(within: str, classes: Sequence[tuple[_Member, ...]]) -> Iterator[_Draw]:
    """Yield each member of each class as a subject, the class its counterparts' pool.

    This is a generator, with the class handed whole and not copied per subject without the
    subject. The drawing holds the index's classes once, and never a copy of one per subject.
    """
    for members in classes:
        # A grouping's class is handed whole to each of its pairs. A folder is not a class anybody
        # stated, and hands nothing.
        held = () if within == PARENT_INDEX else tuple(member.records for member in members)
        for position, subject in enumerate(members):
            yield _Draw(subject, pool=members, position=position, members=held)


def _from_class_keys(
    within: str,
    classes: Sequence[tuple[str, tuple[_Member, ...]]],
    predicate: DrawsFromTheClassKey,
    parameters: Mapping[str, object],
) -> Iterator[_Draw]:
    """Yield each member with the one source the predicate places it after.

    The member is the subject and the pair's target. Its one counterpart is its source: the entry
    the key names, or the member a chain places it after (``DrawsFromTheClassKey.sources``, read
    once per class). A class whose key names no member yields nothing, and no class yields more
    draws than it has members.
    """
    for key, members in classes:
        held = tuple(member.records for member in members)
        placed = predicate.sources(key, held, parameters)
        if not placed:
            continue
        by_id = {member.entry_id: member for member in members}
        drawn = () if within == PARENT_INDEX else held
        pools: dict[str, tuple[_Member, ...]] = {}
        for subject in members:
            source = placed.get(subject.entry_id)
            if source is None or source not in by_id or source == subject.entry_id:
                continue
            pool = pools.setdefault(source, (by_id[source],))
            yield _Draw(subject, pool=pool, position=None, members=drawn, reversed=True)


def _from_class_pairs(
    within: str,
    classes: Sequence[tuple[str, tuple[_Member, ...]]],
    predicate: PairsWithinTheClass,
    parameters: Mapping[str, object],
) -> Iterator[_Draw]:
    """Yield each source member with the targets its class's key pairs it with.

    The member is the subject and each pair's source. Its counterparts are its targets, in
    canonical order. A class whose key pairs nothing yields nothing, and no class yields its
    square.
    """
    for key, members in classes:
        held = tuple(member.records for member in members)
        paired = predicate.pairs_within(key, held, parameters)
        if not paired:
            continue
        by_id = {member.entry_id: member for member in members}
        drawn = () if within == PARENT_INDEX else held
        for subject in members:
            targets = tuple(
                by_id[target]
                for target in sorted(set(paired.get(subject.entry_id, ())))
                if target in by_id and target != subject.entry_id
            )
            if targets:
                yield _Draw(subject, pool=targets, position=None, members=drawn)


def _from_mentions(
    within: str,
    records: Sequence[EntryRecords],
    mentioned: Mapping[str, tuple[Resolution, ...]],
) -> Iterator[_Draw]:
    """Yield the draws a resolution offers, in canonical order, one at a time.

    ``mentioned``: each mentioning file is a subject once, and what its mentions name are its
    counterparts, each placed by the observation of the mention that named it. The draw is directed,
    because a mention is. ``co_mentioned``: the entries one file's mentions name, two or more, each
    take their turn as the subject with the others as counterparts, and the file that named them
    all is the pair's ``describing`` file.

    Both ends must be entries at the rule's level, so a mention naming a folder draws nothing for a
    rule about files.
    """
    by_entry = {entry_records.entry.entry_id: entry_records for entry_records in records}
    for source_id in sorted(mentioned):
        named: dict[str, list[Resolution]] = {}
        for resolution in mentioned[source_id]:
            if resolution.target_id is not None and resolution.target_id != source_id:
                named.setdefault(resolution.target_id, []).append(resolution)
        # Strongest evidence first, then canonical order, so a bound cuts the weakest counterparts
        # and not those whose identifiers sort last.
        members = tuple(
            _Member(
                records=by_entry[target_id],
                membership=held[0].mention.observation_id,
                key_records=tuple(sorted({r.mention.observation_id for r in held})),
                resolutions=tuple(held),
            )
            for target_id, held in sorted(
                named.items(), key=lambda item: (_strength(item[1]), item[0])
            )
            if target_id in by_entry
        )
        source = by_entry.get(source_id)
        if within == MENTION_INDEX:
            if source is None or not members:
                continue
            placed_by = tuple(sorted({member.membership for member in members}))
            yield _Draw(
                subject=_Member(records=source, membership=source_id, key_records=placed_by),
                pool=members,
                position=None,
            )
            continue
        if len(members) < 2 or source is None:
            continue
        # A subject here was placed by another file's reading, the describing file's mention. A
        # claim about it, such as a truncation when a bound cuts its counterparts, may not rest on
        # an observation made about another entry. So what the claim cites is the subject itself.
        # The mention stays each pair's own record, through the pair's resolutions and its
        # describing file. Otherwise a macro that names enough files to trip the bound would make a
        # truncation claim that cites another entry's observation, and the INV-7 check in
        # ``core/invariants.py`` would refuse to publish the run.
        for position, subject in enumerate(members):
            yield _Draw(
                subject=replace(subject, key_records=(subject.records.entry.entry_id,)),
                pool=members,
                position=position,
                describing=source,
            )


def _classes(
    within: str,
    records: Sequence[EntryRecords],
    *,
    published: Sequence[Claim],
    edges: Sequence[Assertion],
    unpublished: Mapping[str, str],
    in_force: Mapping[str, Rule],
) -> tuple[list[tuple[str, tuple[_Member, ...]]], str | None]:
    """Return an index's classes of two or more, each by its key and in canonical order, or why
    there are none."""
    if within == PARENT_INDEX:
        return _folders(records, edges), None
    if within not in in_force:
        return [], f"draws candidates within {within}, which is not in force"
    if within in unpublished:
        reason = unpublished[within]
        return [], f"draws candidates within {within}, whose classes are not claimed: {reason}"
    by_entry = {entry_records.entry.entry_id: entry_records for entry_records in records}
    grouped: dict[str, list[_Member]] = {}
    for claim in sorted(published, key=lambda claim: claim.claim_id):
        if claim.rule_id != within or claim.resolution_status is not ResolutionStatus.RESOLVED:
            continue
        entry_records = by_entry.get(claim.subject)
        if entry_records is None or claim.object_reference is None:
            continue
        grouped.setdefault(claim.object_reference, []).append(
            _Member(records=entry_records, membership=claim.claim_id, key_records=claim.inputs)
        )
    return _canonical(grouped), None


def _folders(
    records: Sequence[EntryRecords], edges: Sequence[Assertion]
) -> list[tuple[str, tuple[_Member, ...]]]:
    """Return the entries of each folder, each placed there by its folder's containment edge.

    A member promoted from a container sits in no folder, so it holds no containment edge and
    is in no class of this index.
    """
    contained = {edge.target_id: edge for edge in edges if edge.primitive is Primitive.CONTAINS}
    grouped: dict[str, list[_Member]] = {}
    for entry_records in records:
        edge = contained.get(entry_records.entry.entry_id)
        if edge is None:
            continue
        grouped.setdefault(edge.source_id, []).append(
            _Member(records=entry_records, membership=edge.edge_id, key_records=(edge.edge_id,))
        )
    return _canonical(grouped)


def _canonical(grouped: Mapping[str, list[_Member]]) -> list[tuple[str, tuple[_Member, ...]]]:
    """Return the classes of two or more, by key, each member in canonical order."""
    return [
        (key, tuple(sorted(members, key=lambda member: member.entry_id)))
        for key, members in sorted(grouped.items())
        if len(members) >= 2
    ]


#: How firmly a mention placed a counterpart, strongest first: a script's call, then a mention
#: resolved in its own folder or under its root, from a root of the pool, by name alone.
_OUTCOME_STRENGTH = {"by_name": 3, "from_root": 2}


def _strength(resolutions: Sequence[Resolution]) -> int:
    """The strongest way a counterpart's mentions placed it: 0 for a call, then by outcome."""
    return min(
        (
            0
            if getattr(resolution.mention, "direction", "")
            else _OUTCOME_STRENGTH.get(resolution.outcome, 1)
            for resolution in resolutions
        ),
        default=4,
    )


def candidates_to_publish(
    candidates: Sequence[Candidate],
    answers: AnswersRead | None = None,
    spell: Spelling | None = None,
) -> tuple[tuple[Candidate, ...], int]:
    """Return what the proposing phase writes of one rule's candidates, and how many it left out.

    This is the one way a candidate reaches the run: every proposing rule's candidates pass here
    before they are written. A pair that a person answered is not proposed again on the evidence
    they answered it on (:func:`ingence.record.answers.unanswered_pairs`, given the answers read at
    the run's watermark and the spelling of the claims a support cites). With no answers, every
    candidate is written, in canonical order.
    """
    if answers is None:
        return tuple(candidates), 0
    from ingence.record.answers import unanswered_pairs

    return unanswered_pairs(candidates, answers, spell)


def _within_bounds(
    subject: _Member,
    draw: _Draw,
    *,
    within: str,
    bounds: Sequence[Rule],
    predicates: Mapping[str, Predicate],
    in_force: Mapping[str, Rule],
    tally: ProposalTally,
    truncations: dict[str, Claim],
    run_id: str,
) -> int:
    """Return how many counterparts every bound in force leaves a subject, recording each cut.

    The cut is on count, in canonical order, and never on a score. Identifiers decide which
    counterparts survive, so the same pool cuts the same way on every run, and nothing about the
    pairs themselves is weighed to decide it. The survivors are the first so many of the pool but
    for the subject. A bound reads them through a view of the pool and not a copy made for each
    subject.
    """
    kept = len(draw.pool) - (0 if draw.position is None else 1)
    for bound in bounds:
        predicate = predicates.get(bound.rule_id)
        if predicate is None:
            continue
        offered = CandidateSet(
            subject=subject.records,
            counterparts=_Counterparts(draw.pool, draw.position, kept),
            within=within,
        )
        try:
            cut = cut_for_subject(bound, predicate, offered)
        except PredicateNotEvaluableError:
            continue
        if cut <= 0:
            continue
        # As many as slicing the first ``kept`` to ``kept - cut`` leaves.
        kept = len(range(kept)[: kept - cut])
        tally.truncated += 1
        if why_not_claim_ready(bound, in_force) is None:
            claim = truncation_claim_for(
                bound,
                subject=subject.entry_id,
                within=within,
                cut=cut,
                inputs=subject.key_records,
                run_id=run_id,
            )
            truncations[claim.claim_id] = claim
    return kept


class _Counterparts(Sequence["EntryRecords"]):
    """A subject's counterparts' records as a bound reads them, never copied.

    The first ``length`` members of the pool but for the subject's own place, in canonical order.
    """

    __slots__ = ("_length", "_pool", "_position")

    def __init__(self, pool: tuple[_Member, ...], position: int | None, length: int) -> None:
        self._pool = pool
        self._position = position
        self._length = length

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> EntryRecords: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[EntryRecords, ...]: ...

    def __getitem__(self, index: int | slice) -> EntryRecords | tuple[EntryRecords, ...]:
        if isinstance(index, slice):
            return tuple(self[at] for at in range(*index.indices(self._length)))
        at = index + self._length if index < 0 else index
        if not 0 <= at < self._length:
            raise IndexError(index)
        if self._position is not None and at >= self._position:
            at += 1
        return self._pool[at].records


class _Ranks:
    """Each end's rank, read once per member of a pool, and where the ranks admit a pair.

    The cheap refusal comes first, per class, before any pair is formed. The draws of one class come
    together and share its tuple, so the pool last ranked is kept, by identity, with the members'
    ranks and, for each least rank asked, the places holding one at or above it. A class whose
    members hold no two ranks far enough apart forms nothing, and each subject costs a lookup and a
    bisection.
    """

    @classmethod
    def of(cls, predicate: Predicate, rule: Rule) -> _Ranks | None:
        """The ranks of a predicate declaring them, or None, so a predicate without them has every
        pair formed. A predicate declares ranks by inheriting :class:`RanksPairEnds`. The ranks are
        never read from the methods' names: a mock, or a predicate with methods of those names for
        another purpose, is not ranked."""
        if not isinstance(predicate, RanksPairEnds):
            return None
        return cls(predicate, parameters_in_force(rule))

    def __init__(self, predicate: RanksPairEnds, parameters: Mapping[str, object]) -> None:
        self._predicate = predicate
        self._parameters = parameters
        self.rise = predicate.least_rise(parameters)
        self._pool: tuple[_Member, ...] | None = None
        self._ranks: list[int | None] = []
        self._at_or_above: dict[int, list[int]] = {}

    def _of_pool(self, pool: tuple[_Member, ...]) -> list[int | None]:
        if pool is not self._pool:
            self._pool = pool
            self._ranks = [
                self._predicate.end_rank(member.records, self._parameters) for member in pool
            ]
            self._at_or_above = {}
        return self._ranks

    def of_subject(self, draw: _Draw) -> int | None:
        """The subject's rank: read with its pool where it is a member, and on its own if not."""
        if draw.position is None:
            return self._predicate.end_rank(draw.subject.records, self._parameters)
        return self._of_pool(draw.pool)[draw.position]

    def places_at_or_above(self, pool: tuple[_Member, ...], least: int) -> list[int]:
        """The pool's places whose member is ranked ``least`` or higher, in canonical order."""
        ranks = self._of_pool(pool)
        found = self._at_or_above.get(least)
        if found is None:
            found = [at for at, rank in enumerate(ranks) if rank is not None and rank >= least]
            self._at_or_above[least] = found
        return found


def _formed(draw: _Draw, kept: int, ranks: _Ranks | None) -> Iterator[_Member]:
    """Yield the counterparts a subject's pairs are formed with, in canonical order.

    These are the first ``kept`` of its pool but for itself, which is what the bounds left. Where
    the predicate ranks the ends, only those ranked at least its rise above the subject are
    yielded, and none where the subject has no rank. The function is a generator, so no pair is
    held beyond its own turn.
    """
    pool, position = draw.pool, draw.position
    # The places of the first ``kept`` counterparts all lie below ``end``.
    end = kept if position is None or kept <= position else kept + 1
    if ranks is None:
        for at in range(end):
            if at != position:
                yield pool[at]
        return
    rank = ranks.of_subject(draw)
    if rank is None:
        return
    places = ranks.places_at_or_above(pool, rank + ranks.rise)
    for at in itertools.islice(places, bisect.bisect_left(places, end)):
        if at != position:
            yield pool[at]


def _constrained(
    constraint: Rule, pair: PairRecords, predicates: Mapping[str, Predicate]
) -> Defeater:
    """Return what one pair constraint says about a candidate, fired or not.

    A constraint that cannot be evaluated is recorded unfired, as what would refuse the candidate,
    and is not dropped. The candidate stands only as far as its constraints could look.
    """
    predicate = predicates.get(constraint.rule_id)
    if predicate is None:
        return constraint_defeater(constraint, None)
    try:
        return constraint_defeater(constraint, refusal_of_pair(constraint, predicate, pair))
    except PredicateNotEvaluableError:
        return constraint_defeater(constraint, None)
