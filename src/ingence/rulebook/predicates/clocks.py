"""The source clock a tool copied into what it wrote, matched to the recording it read.

A crop an image-analysis program cut from a recording keeps the recording's acquisition clock
in its own metadata (``image.source_acquired_ns``, read by ``extractors/tiff.py``), and the
recording carries the same instant as its own clock (``image.acquired_ns``). Two files of one
experiment holding one instant, one of them where data entered, are the recording and a file
made from it, whatever their names say: the crop is named for the cells, not the recording, so
no name joins the two.

:class:`SameAcquisitionClock` groups files on the instant. Its class holds the files in one
experiment whose own clock or copied source clock falls in one second. So the proposing rule
draws its pairs within a class and never across every file of an experiment.

:class:`DerivesFromSourceClock` names the recording as the source. The source is the one member
that carries its own clock and sits at the acquisition stage. Every member that carries a copied
clock and no own clock is made from it. Two recordings of one instant name no source, because a
tie abstains. A file that carries both clocks is a recording and never a target.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json
from ingence.core.schema import RuleEffect, SupportComponent
from ingence.core.tiers import ResolutionStatus
from ingence.rulebook.registry import (
    Abstention,
    ClassKey,
    PairsWithinTheClass,
    Predicate,
    Reads,
    declare,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.schema import Observation
    from ingence.infer.subjects import EntryRecords

__all__ = ["DerivesFromSourceClock", "SameAcquisitionClock"]

#: The stage a recording's claim concludes.
ACQUISITION = "acquisition"


def _names(parameters: Mapping[str, object], key: str, default: tuple[str, ...]) -> frozenset[str]:
    value = parameters.get(key)
    if isinstance(value, (list, tuple)):
        return frozenset(str(item) for item in value)
    return frozenset(default)


def _clock(records: EntryRecords, keys: frozenset[str]) -> tuple[int, str] | None:
    """The earliest clock among the entry's observations of these keys, with its observation."""
    found: list[tuple[int, str]] = []
    for observation in records.observations:
        if observation.key in keys and observation.value_integer is not None:
            found.append((int(observation.value_integer), observation.observation_id))
    return min(found) if found else None


def _records(subject: object) -> EntryRecords | None:
    if getattr(subject, "entry", None) is None or not hasattr(subject, "observations"):
        return None
    return subject  # type: ignore[return-value]


def _tolerance(parameters: Mapping[str, object]) -> int:
    value = parameters.get("tolerance_seconds", 1)
    return max(1, int(str(value)))


def _recording(records: EntryRecords, stage: str) -> bool:
    return any(
        claim.predicate.startswith("classifies.")
        and str(claim.resolution_status) == str(ResolutionStatus.RESOLVED.value)
        and claim.object_reference == stage
        for claim in records.claims
    )


class SameAcquisitionClock:
    """Files of one experiment carrying one acquisition instant, their own or a copied one."""

    name = "same_acquisition_clock"
    version = "1"

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> ClassKey | None:
        """Return the class of the instant the file's clock names within its experiment."""
        records = _records(subject)
        if records is None:
            return None
        own = _clock(records, _names(parameters, "own_clock_keys", ("image.acquired_ns",)))
        copied = _clock(
            records, _names(parameters, "source_clock_keys", ("image.source_acquired_ns",))
        )
        held = own or copied
        if held is None:
            return None
        instant, observation_id = held
        place = getattr(records, "experiment_place", None)
        where = (
            {"experiment": place.experiment}
            if place is not None
            else {"folder": records.entry.parent_entry_id}
        )
        second = instant // (1_000_000_000 * _tolerance(parameters))
        inputs = (observation_id, *(place.claims if place is not None else ()))
        return ClassKey(key=canonical_json({"clock": second, **where}), inputs=inputs)

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the file carries no acquisition clock, its own or copied."""
        del parameters
        return Abstention(reason="the file's records hold no acquisition clock, own or copied")


class DerivesFromSourceClock(PairsWithinTheClass):
    """Within a class of one instant, the recording is the source of each file that copied it."""

    name = "derives_from_source_clock"
    version = "1"

    def pairs_within(
        self, key: str, members: Sequence[object], parameters: Mapping[str, object]
    ) -> Mapping[str, tuple[str, ...]]:
        """The one recording of the class to every file carrying a copied clock; nothing where the
        class holds no recording, or two."""
        del key
        own_keys = _names(parameters, "own_clock_keys", ("image.acquired_ns",))
        stage = str(parameters.get("acquisition_stage", ACQUISITION))
        sources: list[str] = []
        targets: list[str] = []
        for member in members:
            records = _records(member)
            if records is None:
                continue
            if _clock(records, own_keys) is not None:
                if _recording(records, stage):
                    sources.append(records.entry.entry_id)
            else:
                targets.append(records.entry.entry_id)
        if len(sources) != 1 or not targets:
            return {}
        return {sources[0]: tuple(sorted(targets))}

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Return the support for the target having been cut from the source, or None."""
        found = self._read(subject, parameters)
        return None if isinstance(found, str) else found

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say why the pair is not a recording and a file cut from it."""
        found = self._read(subject, parameters)
        return Abstention(reason=found if isinstance(found, str) else "")

    def _read(
        self, subject: object, parameters: Mapping[str, object]
    ) -> tuple[SupportComponent, ...] | str:
        members = getattr(subject, "members", ())
        if not members:
            return "the pair was not drawn from a class, so no instant joins its ends"
        pairs = self.pairs_within("", members, parameters)
        source = getattr(getattr(subject, "source", None), "entry", None)
        target = getattr(getattr(subject, "target", None), "entry", None)
        if source is None or target is None:
            return "the pair has no two ends"
        if target.entry_id not in pairs.get(source.entry_id, ()):
            return (
                "the class holds no single recording at the acquisition stage, or the target "
                "carries a clock of its own"
            )
        cited = tuple(
            sorted(
                {
                    str(getattr(subject, "source_membership", "")),
                    str(getattr(subject, "target_membership", "")),
                }
                - {""}
            )
        )
        return (
            SupportComponent(
                name="source_clock_names_the_recording",
                family="source_clock",
                value=1.0,
                observations=cited,
            ),
        )


def _observations_of(records: EntryRecords) -> tuple[Observation, ...]:
    return records.observations


@declare(
    "same_acquisition_clock",
    version="1",
    summary="Group a recording with the files that copied its acquisition clock",
    effect=RuleEffect.GROUPS,
    reads=Reads.RECORDS,
    parameters=("own_clock_keys", "source_clock_keys", "tolerance_seconds"),
    support_families=("source_clock",),
)
def _build_same_acquisition_clock() -> Predicate:
    return SameAcquisitionClock()


@declare(
    "derives_from_source_clock",
    version="1",
    summary="Propose a file that copied a recording's clock as made from that recording",
    effect=RuleEffect.PROPOSES,
    reads=Reads.PAIR,
    parameters=("acquisition_stage", "own_clock_keys"),
    support_families=("source_clock",),
    reach="pair",
)
def _build_derives_from_source_clock() -> Predicate:
    return DerivesFromSourceClock()
