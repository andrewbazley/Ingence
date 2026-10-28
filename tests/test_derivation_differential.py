"""The ranked drawing against the unranked one, over generated stage setups.

No shipped predicate ranks its ends, because derivations come before stages and the stage step
proposes nothing. The drawing's ranks stay for a predicate that declares them. The differential
below runs a test predicate (``_Rungs``) that ranks the ends by stage.

The row tests in ``tests/test_derivation_drawing.py`` run revision 4, whose stage rules refuse
siblings over an acyclic chain, so the ranks' rise there is always one. The contract tests in
``tests/test_derivation_ranks.py`` never run ``propose``. This file closes both gaps with invented
names: ``propose`` is called directly over a few hundred seeded classes and rules, the ranks in
force against the ranks set aside, every candidate, truncation and count compared. The setups hold
cycles and steps onto their own stage, no workflow, siblings admitted and refused, zero to three
rungs skipped, ``unless_sharing``, a stage stated twice by the engine and by a person's answer,
conflicting, unexamined and no-evidence stage claims, an entry in one class twice, none to two
bounds of one to twenty-five, and the describing file's rule within ``co_mentioned`` and
``mentioned``.

The ranks are declared, never guessed from names. An object that merely has callables called
``end_rank`` and ``least_rise`` is not ranked, and every pair its index offers is formed.
"""

from __future__ import annotations

import json
import random
from unittest.mock import MagicMock

import pytest

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.relations import Primitive
from ingence.core.schema import (
    Claim,
    Defeater,
    Entry,
    ParameterSpec,
    ParameterType,
    Rule,
    RuleEffect,
    SupportComponent,
)
from ingence.core.tiers import ResolutionStatus
from ingence.infer import candidates
from ingence.infer.references import Mention, Resolution
from ingence.infer.subjects import EntryRecords
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES, RanksPairEnds, reads_of

load_builtins()

WITHIN = "named_for_kelt"
SHARING = "share_vorn"
STAGES = ("raw", "acquisition", "processed", "analysis", "quantification", "summary", "zeph")
STATUSES = (
    ResolutionStatus.RESOLVED,
    ResolutionStatus.RESOLVED,
    ResolutionStatus.RESOLVED,
    ResolutionStatus.EXAMINED_CONFLICTING,
    ResolutionStatus.EXAMINED_NO_EVIDENCE,
    ResolutionStatus.UNEXAMINED,
)
DEFEATERS = (Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),)
SETUPS = 300


def _entry(entry_id: str) -> Entry:
    return Entry(
        entry_id=entry_id,
        root_id="root",
        relative_path=f"brisa/{entry_id}",
        name=entry_id,
        depth=1,
        entry_type="file",
        extension="",
        compound_extension="",
        first_seen_run="r",
        last_seen_run="r",
    )


def _claim(
    subject: str, rule_id: str, predicate: str, value: str, status: ResolutionStatus, n: int
) -> Claim:
    return Claim(
        claim_id=f"claim:{subject}:{rule_id}:{value}:{n}",
        run_id="r",
        subject=subject,
        predicate=predicate,
        object_reference=value,
        rule_id=rule_id,
        rule_version="1",
        scope=PROJECT_SCOPE,
        defeaters=DEFEATERS,
        resolution_status=status,
        inputs=(f"obs:{subject}",),
    )


def _spec(name: str, kind: ParameterType, value: object) -> ParameterSpec:
    return ParameterSpec(name=name, type=kind, default=json.dumps(value, sort_keys=True))


def _transitions(rng: random.Random) -> dict[str, str]:
    shape = rng.randrange(5)
    if shape == 0:
        return {
            "raw": "processed",
            "acquisition": "processed",
            "processed": "analysis",
            "analysis": "quantification",
            "quantification": "summary",
        }
    if shape == 1:
        return {}
    # Cycles and steps onto their own stage allowed.
    return {before: rng.choice(STAGES) for before in rng.sample(STAGES, rng.randrange(1, 7))}


def _records(rng: random.Random, count: int, keys: int) -> tuple[list[EntryRecords], list[Claim]]:
    records, grouping = [], []
    for at in range(count):
        entry_id = f"e{at:03d}"
        claims = []
        # A stage may be stated twice, by the engine and by a person's answer, at any status.
        for n, stage in enumerate(rng.choices(STAGES, k=rng.randrange(0, 5))):
            predicate = "classifies.pipeline_stage_from_name" if rng.random() < 0.9 else "flags.x"
            author = rng.choice(("pipeline_stage_from_name", "pipeline_stage_from_name", "answer"))
            claims.append(_claim(entry_id, author, predicate, stage, rng.choice(STATUSES), n))
        if rng.random() < 0.4:
            share = f"s{rng.randrange(2)}"
            claims.append(
                _claim(entry_id, SHARING, "groups.share", share, ResolutionStatus.RESOLVED, 0)
            )
        records.append(EntryRecords(entry=_entry(entry_id), claims=tuple(claims)))
        # None, one or two memberships: an entry may stand in one class twice.
        for n in range(rng.choice((0, 1, 1, 1, 2))):
            status = (
                ResolutionStatus.RESOLVED
                if rng.random() < 0.9
                else ResolutionStatus.EXAMINED_CONFLICTING
            )
            key = f"k{rng.randrange(keys)}"
            grouping.append(_claim(entry_id, WITHIN, "groups.named_for", key, status, n))
    return records, grouping


def _rule(rule_id: str, predicate: str, within: str, *parameters: ParameterSpec) -> Rule:
    return Rule(
        rule_id=rule_id,
        version="1",
        primitive=Primitive.DERIVES,
        statement="A test rule.",
        rationale="A test.",
        parameters=parameters,
        scope=PROJECT_SCOPE,
        predicate=predicate,
        effect=RuleEffect.PROPOSES,
        candidates_within=within,
    )


def _stage_rule(rng: random.Random) -> Rule:
    return _rule(
        "stage_derivation",
        "derives_from_stage_transition",
        WITHIN,
        _spec("stage_transitions", ParameterType.MAPPING, _transitions(rng)),
        _spec("refuse_siblings_on_one_rung", ParameterType.BOOLEAN, rng.random() < 0.5),
        _spec("maximum_rungs_skipped", ParameterType.INTEGER, rng.randrange(4)),
        _spec("unless_sharing", ParameterType.STRING, rng.choice(("", SHARING))),
    )


def _described_rule(rng: random.Random) -> Rule:
    return _rule(
        "described_derivation",
        "derives_from_declared_reference",
        rng.choice(("co_mentioned", "mentioned")),
        _spec("stage_transitions", ParameterType.MAPPING, _transitions(rng)),
        _spec("describing_formats", ParameterType.STRING_LIST, ["python_source"]),
        _spec("describing_roles", ParameterType.STRING_LIST, []),
        _spec(
            "minimum_resolution",
            ParameterType.STRING,
            rng.choice(("exact", "unique_suffix", "unique_basename")),
        ),
    )


def _bound(n: int, maximum: int) -> Rule:
    return Rule(
        rule_id=f"bound_{n}",
        version="1",
        statement="A test bound.",
        rationale="A test.",
        parameters=(_spec("maximum_candidates", ParameterType.INTEGER, maximum),),
        scope=PROJECT_SCOPE,
        predicate="candidates_are_bounded_per_subject",
        effect=RuleEffect.CONSTRAINT,
    )


def _grouping() -> Rule:
    """The index the pairs are drawn within: in force, and nothing about it is read here."""
    return Rule(
        rule_id=WITHIN,
        version="1",
        statement="A test grouping.",
        rationale="A test.",
        scope=PROJECT_SCOPE,
        predicate="format_from_extension",
        effect=RuleEffect.CLASSIFIES,
    )


def _mentions(rng: random.Random, records: list[EntryRecords]) -> dict[str, tuple[Resolution, ...]]:
    held = []
    for at, named in enumerate(records):
        if rng.random() < 0.8:
            held.append(
                Resolution(
                    mention=Mention(
                        entry_id="script",
                        observer="text",
                        path=f"{named.entry.entry_id}.csv",
                        shape="relative",
                        observation_id=f"mention:{at}",
                        direction=rng.choice(("", "", "", "read", "written")),
                    ),
                    outcome=rng.choice(("in_folder", "from_root", "by_name")),
                    target_id=named.entry.entry_id,
                )
            )
    return {"script": tuple(held)}


def _propose(
    seed: int, predicate: object | None = None
) -> tuple[dict[str, object], candidates.ProposalTally]:
    """One generated setup through ``propose``; its rows, and its tally."""
    rng = random.Random(seed)
    describing = seed % 3 == 2
    records, grouping = _records(rng, rng.randrange(2, 16), keys=rng.randrange(1, 4))
    rule = _described_rule(rng) if describing else _stage_rule(rng)
    bounds = [_bound(n, rng.choice((1, 2, 3, 5, 25))) for n in range(rng.choice((0, 1, 1, 2)))]
    mentioned = None
    if describing:
        script = _claim(
            "script",
            "format_from_extension",
            "classifies.format_from_extension",
            "python_source",
            ResolutionStatus.RESOLVED,
            0,
        )
        mentioned = _mentions(rng, records)
        records = [*records, EntryRecords(entry=_entry("script"), claims=(script,))]
    predicates = {rule.rule_id: predicate or PREDICATES.get(rule.predicate)}
    shapes = {rule.rule_id: reads_of(PREDICATES.declaration(rule.predicate))}
    for bound in bounds:
        predicates[bound.rule_id] = PREDICATES.get(bound.predicate)
        shapes[bound.rule_id] = reads_of(PREDICATES.declaration(bound.predicate))
    tally = candidates.ProposalTally(rule_id=rule.rule_id, rule_version=rule.version)
    proposed = candidates.propose(
        rule,
        records,
        tally=tally,
        published=grouping,
        edges=(),
        unpublished={},
        constraints=bounds,
        predicates=predicates,  # type: ignore[arg-type]
        shapes=shapes,
        in_force={WITHIN: _grouping(), rule.rule_id: rule, **{b.rule_id: b for b in bounds}},
        run_id="run_test",
        mentioned=mentioned,
    )
    rows = {
        "candidates": [candidate.model_dump(mode="json") for candidate in proposed.candidates],
        "truncations": [claim.model_dump(mode="json") for claim in proposed.truncations],
        "tally": [
            tally.pairs,
            tally.supported,
            tally.circumstantial,
            tally.blocked,
            tally.truncated,
            tally.not_ready,
        ],
    }
    return rows, tally


class _Rungs(RanksPairEnds):
    """A predicate ranking its ends, for the drawing's own contract.

    No shipped predicate ranks its ends: the stage step proposes nothing and ranks no end. The
    drawing's ranks stay, for a predicate that declares them. This one ranks by stage: a pair
    stands where its target's latest stage is on a later rung than its source's, or on the same
    where siblings are admitted.
    """

    name = "derives_from_stage_transition"
    version = "test"

    def evaluate(self, subject: object, parameters: object) -> object:
        source = self.end_rank(getattr(subject, "source", None), parameters)  # type: ignore[arg-type]
        target = self.end_rank(getattr(subject, "target", None), parameters)  # type: ignore[arg-type]
        if source is None or target is None or target - source < self.least_rise(parameters):  # type: ignore[arg-type]
            return None
        return (SupportComponent(name="rung", family="stage_transition", value=1.0),)

    def end_rank(self, records: object, parameters) -> int | None:  # type: ignore[no-untyped-def]
        transitions = dict(parameters.get("stage_transitions") or {})
        rung = dict.fromkeys(set(transitions) | set(transitions.values()), 0)
        for _ in range(len(rung)):
            for before, after in transitions.items():
                rung[after] = max(rung[after], min(rung[before] + 1, len(rung)))
        held = [
            rung[claim.object_reference]
            for claim in getattr(records, "claims", ())
            if claim.predicate.startswith("classifies.")
            and claim.resolution_status is ResolutionStatus.RESOLVED
            and claim.object_reference in rung
        ]
        return max(held) if held else None

    def least_rise(self, parameters) -> int:  # type: ignore[no-untyped-def]
        return 1 if parameters.get("refuse_siblings_on_one_rung") else 0


def test_the_ranked_drawing_publishes_what_the_unranked_one_does(monkeypatch) -> None:
    def ranking(seed: int) -> object | None:
        return None if seed % 3 == 2 else _Rungs()

    ranked = {seed: _propose(seed, ranking(seed)) for seed in range(SETUPS)}
    with monkeypatch.context() as unranked:
        unranked.setattr(candidates._Ranks, "of", classmethod(lambda cls, predicate, rule: None))
        every = {seed: _propose(seed, ranking(seed)) for seed in range(SETUPS)}

    differing = [seed for seed in range(SETUPS) if ranked[seed][0] != every[seed][0]]
    assert differing == []
    # Not vacuous: pairs are supported, some under no rise, and the ranks spare most of the reads.
    assert sum(rows["tally"][1] for rows, _ in ranked.values()) > 50  # type: ignore[index]
    formed = sum(tally.formed for _, tally in ranked.values())
    assert formed < sum(tally.formed for _, tally in every.values()) // 2


class _LooksRanked:
    """A proposing predicate that supports every pair, with methods merely named like the ranks."""

    name = "derives_from_stage_transition"
    version = "3"

    def evaluate(self, subject: object, parameters: object) -> object:
        return (SupportComponent(name="stage_transition", family="stage_transition", value=1.0),)

    def end_rank(self, records: object, parameters: object) -> None:
        return None

    def least_rise(self, parameters: object) -> int:
        return 99


@pytest.mark.parametrize("lookalike", [MagicMock(), _LooksRanked()], ids=["mock", "same_names"])
def test_callables_named_like_the_ranks_do_not_rank_a_predicate(lookalike) -> None:
    rule = _stage_rule(random.Random(0))
    assert candidates._Ranks.of(lookalike, rule) is None
    assert candidates._Ranks.of(PREDICATES.get(rule.predicate), rule) is not None


def test_a_predicate_not_declaring_ranks_has_every_offered_pair_formed() -> None:
    for seed in (0, 1, 3, 4):
        _, tally = _propose(seed, predicate=_LooksRanked())
        assert tally.formed == tally.pairs
        assert tally.supported == tally.pairs
