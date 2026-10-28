"""The two tier axes, the absence states, and the decision tiers.

Four vocabularies live here. The first two are orthogonal and must never be merged.

``ExtractionTier`` measures *distance from the original bytes*: how much
machinery stands between what the storage device holds and what the engine
recorded. It is a property of an observation.

``InferenceTier`` measures *what kind of reasoning produced a claim*: whether it
follows necessarily, defeasibly, as the best available explanation, or because a
person said so. It is a property of a claim.

A formally extracted string can support an entailed claim or an abductive one.
Collapsing the two axes into a single "confidence tier" loses that, and it is
exactly the conflation that makes a coverage number a lie. They cost one column
each and they stay apart.

``ResolutionStatus`` is the third vocabulary. It exists because "unknown", "nothing
found" and "conflicting" are three different states. A system with one null cannot
tell them apart, and a user reading its output cannot either.

``DecisionTier`` is the fourth and smallest. It is the lexicographic classification
that a candidate receives. The tier is the decision. A score is only a tiebreak
within a tier. This module deliberately has no threshold that works as an accept or
reject boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DECISION_TIER_PROPERTIES",
    "EXTRACTION_TIER_PROPERTIES",
    "INFERENCE_TIER_PROPERTIES",
    "DecisionTier",
    "DecisionTierProperties",
    "ExtractionTier",
    "ExtractionTierProperties",
    "InferenceTier",
    "InferenceTierProperties",
    "ResolutionStatus",
    "extraction_distance",
    "inference_standing",
    "is_absence",
    "join_inference_tiers",
    "never_lowers",
]


# ---------------------------------------------------------------------------
# Axis one: distance from the original bytes.
# ---------------------------------------------------------------------------


class ExtractionTier(StrEnum):
    """How far an observation sits from the bytes on the storage device.

    Six graded tiers, lettered A to F, plus two states that are not points on the
    ladder at all. ``INACCESSIBLE`` and ``NOT_EXAMINED`` record that the ladder
    was never climbed. Treating them as a seventh and eighth rung would make "we
    could not read it" look like a weak reading.
    """

    #: A. The bytes themselves, or a value the filesystem reported directly.
    LITERAL = "literal"
    #: B. Present in the file and revealed by reading its structure without
    #: interpreting it: an archive member list, a header field, a magic number.
    MECHANICALLY_EXPOSED = "mechanically_exposed"
    #: C. Recovered by a reversible transformation: decompression, base64,
    #: character-encoding conversion. The result is still the original content.
    DECODED = "decoded"
    #: D. Produced by a parser that implements a published format specification.
    #: Correct if the parser and the specification are correct.
    FORMALLY_EXTRACTED = "formally_extracted"
    #: E. Produced by a rule of thumb over content: a regular expression, a
    #: signature table, a printable-string harvest. Often right, never certain.
    HEURISTIC = "heuristic"
    #: F. Produced by interpreting meaning rather than form: a token resolved
    #: through the Dictionary, a name matched against a naming grammar.
    SEMANTIC = "semantic"

    #: Not on the ladder. The engine tried and could not read the content:
    #: permissions, encryption, corruption, an unsupported format.
    INACCESSIBLE = "inaccessible"
    #: Not on the ladder. The engine has not looked. Distinct from having looked
    #: and found nothing, which is a resolution status rather than a tier.
    NOT_EXAMINED = "not_examined"


@dataclass(frozen=True, slots=True)
class ExtractionTierProperties:
    """Declared metadata for one extraction tier."""

    tier: ExtractionTier
    #: The tier's letter, or ``None`` for the two states that are not rungs on the
    #: ladder.
    letter: str | None
    #: Steps away from the bytes, lower being closer. ``None`` where the tier
    #: records that no reading happened, which is not a distance.
    distance: int | None
    #: True when the tier records an absence of reading rather than a reading.
    absence: bool
    definition: str


EXTRACTION_TIER_PROPERTIES: dict[ExtractionTier, ExtractionTierProperties] = {
    props.tier: props
    for props in (
        ExtractionTierProperties(
            ExtractionTier.LITERAL,
            "A",
            0,
            False,
            "The bytes themselves, or a value the filesystem reported directly.",
        ),
        ExtractionTierProperties(
            ExtractionTier.MECHANICALLY_EXPOSED,
            "B",
            1,
            False,
            "Revealed by reading structure without interpreting it.",
        ),
        ExtractionTierProperties(
            ExtractionTier.DECODED,
            "C",
            2,
            False,
            "Recovered by a reversible transformation; still the original content.",
        ),
        ExtractionTierProperties(
            ExtractionTier.FORMALLY_EXTRACTED,
            "D",
            3,
            False,
            "Produced by a parser implementing a published format specification.",
        ),
        ExtractionTierProperties(
            ExtractionTier.HEURISTIC,
            "E",
            4,
            False,
            "Produced by a rule of thumb over content; often right, never certain.",
        ),
        ExtractionTierProperties(
            ExtractionTier.SEMANTIC,
            "F",
            5,
            False,
            "Produced by interpreting meaning rather than form.",
        ),
        ExtractionTierProperties(
            ExtractionTier.INACCESSIBLE,
            None,
            None,
            True,
            "The content could not be read. Not a weak reading; no reading.",
        ),
        ExtractionTierProperties(
            ExtractionTier.NOT_EXAMINED,
            None,
            None,
            True,
            "The engine has not looked. Distinct from having looked and found nothing.",
        ),
    )
}


def extraction_distance(tier: ExtractionTier) -> int | None:
    """Return steps from the bytes, or ``None`` when the tier records an absence.

    Callers must handle ``None`` rather than defaulting it. Substituting a
    number for "we did not look" is how an absence becomes a weak positive.
    """
    return EXTRACTION_TIER_PROPERTIES[tier].distance


def is_absence(tier: ExtractionTier) -> bool:
    """Return True when the tier records that no reading took place."""
    return EXTRACTION_TIER_PROPERTIES[tier].absence


# ---------------------------------------------------------------------------
# Axis two: what kind of reasoning produced a claim.
# ---------------------------------------------------------------------------


class InferenceTier(StrEnum):
    """The kind of reasoning that produced a claim.

    Four kinds, and the differences between them are differences in what could
    make the claim wrong, which is why they are a vocabulary rather than a
    number.
    """

    #: Follows from observations by a rule that cannot be wrong if the
    #: observations are right. Examples are filesystem containment and matching full hashes.
    ENTAILED = "entailed"
    #: Follows by default and is retractable when a conflicting fact arrives.
    #: Every such claim must be able to name what would defeat it.
    DEFEASIBLE = "defeasible"
    #: Inference to the best explanation. Not "these facts entail X" but "X, if
    #: true, would explain these facts better than any alternative".
    ABDUCTIVE = "abductive"
    #: A person said so. Outranks everything below it within the scope the
    #: person addressed, and nowhere else.
    TESTIFIED = "testified"


@dataclass(frozen=True, slots=True)
class InferenceTierProperties:
    """Declared metadata for one inference tier."""

    tier: InferenceTier
    #: Higher is stronger. See :func:`inference_standing` for the one important
    #: qualification, which concerns testimony.
    standing: int
    #: True when the claim must carry the observations that would retract it.
    requires_defeaters: bool
    #: True when standing holds only inside a stated scope.
    scope_limited: bool
    definition: str


INFERENCE_TIER_PROPERTIES: dict[InferenceTier, InferenceTierProperties] = {
    props.tier: props
    for props in (
        InferenceTierProperties(
            InferenceTier.ABDUCTIVE,
            1,
            True,
            False,
            "The best available explanation of the observations.",
        ),
        InferenceTierProperties(
            InferenceTier.DEFEASIBLE,
            2,
            True,
            False,
            "Follows by default; retractable when a conflicting fact arrives.",
        ),
        InferenceTierProperties(
            InferenceTier.ENTAILED,
            3,
            False,
            False,
            "Cannot be wrong if the observations it consumed are right.",
        ),
        InferenceTierProperties(
            InferenceTier.TESTIFIED,
            4,
            False,
            True,
            "A person said so, within a stated scope.",
        ),
    )
}


def inference_standing(tier: InferenceTier, *, in_scope: bool = True) -> int | None:
    """Return the standing of a tier, or ``None`` when it does not apply here.

    Testimony is the one tier whose standing is conditional. A person who
    confirms that two folders are one session has settled that question and no
    other. The answer is never retroactively converted into a fact about
    anything else. So ``inference_standing(TESTIFIED, in_scope=False)`` returns
    ``None``, meaning the testimony is not evidence at this location at all,
    rather than a low number, which would make it weak evidence everywhere.

    Every other tier ignores ``in_scope``.
    """
    props = INFERENCE_TIER_PROPERTIES[tier]
    if props.scope_limited and not in_scope:
        return None
    return props.standing


def join_inference_tiers(left: InferenceTier, right: InferenceTier) -> InferenceTier:
    """Return the stronger of two tiers for the same claim.

    Used when independent evidence supports one claim. Both arguments are taken
    to apply at the claim's own location, so scope has already been checked by
    the caller.
    """
    left_standing = INFERENCE_TIER_PROPERTIES[left].standing
    right_standing = INFERENCE_TIER_PROPERTIES[right].standing
    return left if left_standing >= right_standing else right


def never_lowers(before: InferenceTier, after: InferenceTier) -> bool:
    """Return True when a tier change is permitted by the monotonicity rule.

    More evidence must never lower a claim's standing. Adding an extractor may
    raise a tier, leave it unchanged, or introduce an explicit defeater, which
    is a visible event recorded on the claim. What it may never do is quietly
    erode a claim by diluting a score, which is the failure mode of any additive
    confidence model and the reason this project does not have one.

    This predicate is the test behind the INV-10 check in ``core/invariants.py``: adding an
    extractor never lowers a claim's tier.
    """
    return INFERENCE_TIER_PROPERTIES[after].standing >= INFERENCE_TIER_PROPERTIES[before].standing


# ---------------------------------------------------------------------------
# Absence, kept in three pieces.
# ---------------------------------------------------------------------------


class ResolutionStatus(StrEnum):
    """Whether a question about an entity has been settled, and if not, why not.

    Three of these four are absence states, and they mean different things. A
    coverage figure computed over their union is not a coverage figure. It is
    the conflation that this vocabulary exists to prevent.
    """

    #: The engine has not looked. Says nothing about what is there.
    UNEXAMINED = "unexamined"
    #: The engine looked and found no evidence either way. A finding, not a gap.
    EXAMINED_NO_EVIDENCE = "examined_no_evidence"
    #: The engine looked and found evidence pointing both ways, unresolved by
    #: any rule. The most interesting of the three and the most useful to show.
    EXAMINED_CONFLICTING = "examined_conflicting"
    #: Settled. The only one of the four that is not an absence.
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------
# The candidate decision.
# ---------------------------------------------------------------------------


class DecisionTier(StrEnum):
    """The lexicographic classification of a candidate's evidence.

    Classification replaces summation. Three views of one underlying fact do not
    become three pieces of evidence by being added together, and a threshold on
    such a sum cannot be defended. The tier decides. A score orders candidates
    within a tier and nothing else.
    """

    #: An explicit reference in code, a log, or a sidecar naming both endpoints,
    #: or matching full cryptographic hashes. Accept.
    DIRECT = "direct"
    #: At least one identity-grade component match plus at least one independent
    #: corroborating family. Accept when no defeater is present.
    ANCHORED = "anchored"
    #: Co-location, name similarity and temporal adjacency only. Never accepted
    #: automatically. The engine queues it by salience for a person to decide.
    CIRCUMSTANTIAL = "circumstantial"
    #: A hard conflict is present. Rejected, with the conflict recorded.
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class DecisionTierProperties:
    """Declared metadata for one decision tier."""

    tier: DecisionTier
    #: True when a candidate in this tier may be asserted without a person.
    auto_acceptable: bool
    #: True when a defeater present on the candidate blocks acceptance.
    defeater_sensitive: bool
    definition: str


DECISION_TIER_PROPERTIES: dict[DecisionTier, DecisionTierProperties] = {
    props.tier: props
    for props in (
        DecisionTierProperties(
            DecisionTier.DIRECT,
            True,
            False,
            "An explicit reference naming both endpoints, or matching full hashes.",
        ),
        DecisionTierProperties(
            DecisionTier.ANCHORED,
            True,
            True,
            "An identity-grade match plus an independent corroborating family.",
        ),
        DecisionTierProperties(
            DecisionTier.CIRCUMSTANTIAL,
            False,
            True,
            "Co-location, name similarity and temporal adjacency only.",
        ),
        DecisionTierProperties(
            DecisionTier.BLOCKED,
            False,
            True,
            "A hard conflict is present; rejected with the conflict recorded.",
        ),
    )
}
