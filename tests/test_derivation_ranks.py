"""No stage word decides whether a derivation stands or which way it runs.

No proposing predicate reads a stage to form or to direct a pair, because all derivations come
before stage assignment. The stage predicate supports no pair and ranks no end. The evidence
predicates decide a pair the same whatever stages its ends hold, and a stage step adds only a
corroborating component. The rank contract still holds for a predicate that declares ranks
(``tests/test_derivation_differential.py``). Each is tested here exhaustively over a small
grammar of stages and parameters.
"""

from __future__ import annotations

import itertools

import pytest

from ingence.core.ids import PROJECT_SCOPE
from ingence.core.schema import Claim, Defeater, Entry
from ingence.core.tiers import ResolutionStatus
from ingence.infer.subjects import EntryRecords, PairRecords
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES

load_builtins()


# ---------------------------------------------------------------------------
# The contract: every pair the stage predicate supports is one its rank admits.
# ---------------------------------------------------------------------------

_STAGES = ("raw", "acquisition", "processed", "analysis", "quantification", "summary")
_TRANSITIONS = (
    # The shipped chain.
    {
        "raw": "processed",
        "acquisition": "processed",
        "processed": "analysis",
        "analysis": "quantification",
        "quantification": "summary",
    },
    # A cycle a person could write, so no rung order holds.
    {"raw": "processed", "processed": "analysis", "analysis": "processed"},
    # A step onto its own stage.
    {"raw": "processed", "processed": "processed", "analysis": "quantification"},
    # No steps at all.
    {},
)


def _entry(entry_id: str) -> Entry:
    return Entry(
        entry_id=entry_id,
        root_id="root",
        relative_path=f"kelt/{entry_id}",
        name=entry_id,
        depth=1,
        entry_type="file",
        extension="",
        compound_extension="",
        first_seen_run="r",
        last_seen_run="r",
    )


def _stage(subject: str, value: str) -> Claim:
    return Claim(
        claim_id=f"claim:{subject}:{value}",
        run_id="r",
        subject=subject,
        predicate="classifies.pipeline_stage_from_name",
        object_reference=value,
        rule_id="pipeline_stage_from_name",
        rule_version="1",
        scope=PROJECT_SCOPE,
        defeaters=(Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),),
        resolution_status=ResolutionStatus.RESOLVED,
    )


def _file(entry_id: str, stages: tuple[str, ...]) -> EntryRecords:
    return EntryRecords(
        entry=_entry(entry_id), claims=tuple(_stage(entry_id, stage) for stage in stages)
    )


def _stage_sets() -> list[tuple[str, ...]]:
    return [
        combination for size in range(3) for combination in itertools.combinations(_STAGES, size)
    ]


@pytest.mark.parametrize("transitions", _TRANSITIONS, ids=["chain", "cycle", "loop", "none"])
def test_the_stage_step_supports_no_pair_and_ranks_no_end(transitions) -> None:
    """Exhaustive over two ends' stages (none, one or two), siblings refused or not, rungs skipped
    none to two, and a class holding a processed file or not. There is no pair to support and no
    end ranked, so the drawing forms none for it."""
    predicate = PREDICATES.get("derives_from_stage_transition")
    held = _file("held", ("processed",))
    for refuse, skipped, with_held in itertools.product((True, False), (0, 1, 2), (True, False)):
        parameters = {
            "stage_transitions": transitions,
            "refuse_siblings_on_one_rung": refuse,
            "maximum_rungs_skipped": skipped,
            "unless_sharing": "",
        }
        for first, second in itertools.product(_stage_sets(), repeat=2):
            source, target = _file("source", first), _file("target", second)
            members = (source, target, held) if with_held else (source, target)
            pair = PairRecords(
                source=source,
                target=target,
                within="named_for_an_acquisition",
                source_membership="member:source",
                target_membership="member:target",
                members=members,
            )
            assert predicate.evaluate(pair, parameters) is None
            assert predicate.end_rank(source, parameters) is None


def _timed(end: EntryRecords, hour: int) -> EntryRecords:
    """One end, last modified at the given hour of 1 March 2020 (UTC)."""
    at = (1_583_020_800 + hour * 3600) * 1_000_000_000
    return EntryRecords(entry=end.entry.model_copy(update={"modified_ns": at}), claims=end.claims)


def _decided_alike_whatever_the_stages(predicate_name: str, pair_of, parameters) -> int:  # type: ignore[no-untyped-def]
    """Every two stage sets, either way round in time and at one time: the pair stands or not
    exactly as it does with no stage at all, its components but a stage step the same."""
    predicate = PREDICATES.get(predicate_name)
    stepped = 0
    for source_hour, target_hour in ((1, 2), (2, 1), (1, 1)):
        bare = predicate.evaluate(
            pair_of(
                _timed(_file("source", ()), source_hour), _timed(_file("target", ()), target_hour)
            ),
            parameters,
        )
        for first, second in itertools.product(_stage_sets(), repeat=2):
            source = _timed(_file("source", first), source_hour)
            target = _timed(_file("target", second), target_hour)
            support = predicate.evaluate(pair_of(source, target), parameters)
            assert (support is None) == (bare is None), (first, second, source_hour, target_hour)
            if support is None:
                continue
            kept = [component for component in support if component.name != "stage_step"]
            assert [component.name for component in kept] == [c.name for c in bare]
            stepped += len(support) - len(kept)
    return stepped


@pytest.mark.parametrize("direction", ["earliest_first", "undirected"])
@pytest.mark.parametrize("transitions", _TRANSITIONS, ids=["chain", "cycle", "loop", "none"])
def test_an_identity_s_pair_is_decided_by_time_and_never_by_its_stages(
    transitions, direction
) -> None:
    def pair_of(source: EntryRecords, target: EntryRecords) -> PairRecords:
        return PairRecords(
            source=source,
            target=target,
            within="same_name_identity_within_experiment",
            source_membership="member:source",
            target_membership="member:target",
        )

    stepped = _decided_alike_whatever_the_stages(
        "derives_within_a_name_identity",
        pair_of,
        {"stage_transitions": transitions, "direction": direction, "tolerance_seconds": 60},
    )
    # A stage step corroborates a derivation the time directs, and nothing undirected.
    assert (stepped > 0) == bool(transitions and direction == "earliest_first")


# ---------------------------------------------------------------------------
# The describing file's derivation: an end holding no stage is never supported.
# ---------------------------------------------------------------------------


def _described(source: EntryRecords, target: EntryRecords) -> PairRecords:
    """A pair two of one script's mentions named, both resolved beside it."""
    from ingence.infer.references import Mention, Resolution

    script = EntryRecords(
        entry=_entry("script"),
        claims=(
            Claim(
                claim_id="claim:script:python_source",
                run_id="r",
                subject="script",
                predicate="classifies.format_from_extension",
                object_reference="python_source",
                rule_id="format_from_extension",
                rule_version="1",
                scope=PROJECT_SCOPE,
                defeaters=(
                    Defeater(reason="a test defeater", rule_id="defeater", rule_version="1"),
                ),
                resolution_status=ResolutionStatus.RESOLVED,
            ),
        ),
    )
    resolutions = tuple(
        Resolution(
            mention=Mention(
                entry_id="script",
                observer="text",
                path=f"{end.entry.entry_id}.csv",
                shape="relative",
                observation_id=f"mention:{end.entry.entry_id}",
            ),
            outcome="in_folder",
            target_id=end.entry.entry_id,
        )
        for end in (source, target)
    )
    return PairRecords(
        source=source,
        target=target,
        within="co_mentioned",
        source_membership="mention:source",
        target_membership="mention:target",
        resolutions=resolutions,
        describing=script,
    )


@pytest.mark.parametrize("ordered", [True, False])
@pytest.mark.parametrize("transitions", _TRANSITIONS, ids=["chain", "cycle", "loop", "none"])
def test_a_describing_file_s_pair_is_decided_by_its_evidence_and_never_by_its_stages(
    transitions, ordered
) -> None:
    """The stages decide nothing about a describing file's pair, either way time is read."""
    parameters = {
        "stage_transitions": transitions,
        "describing_formats": ["python_source"],
        "describing_roles": [],
        "minimum_resolution": "unique_suffix",
        "ordered_by_time": ordered,
        "tolerance_seconds": 60,
    }
    for direction in ("earliest_first", "undirected"):
        _decided_alike_whatever_the_stages(
            "derives_from_declared_reference", _described, {**parameters, "direction": direction}
        )
