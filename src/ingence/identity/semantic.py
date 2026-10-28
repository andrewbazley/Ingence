"""Semantic identity: the sorted tuple of an entry's normalised components.

The cheapest identity of the five, because by the time it can be computed the
work is already done. Once names have been broken into atoms and those atoms
normalised, two entries describing the same experimental thing carry the same
component set however differently they were written. ``SubX_800ngmL_2.5h_r1`` and
``subx-800-ng-per-ml-2h30-rep1`` reduce to the same tuple, and no amount of
string comparison would have connected them.

It depends on the Dictionary. The method class is registered because it is part of the identity
surface and its shape affects how components are stored. It computes nothing from the extraction
context, and :func:`signature_of` hashes components that normalisation produced. Returning
nothing is correct rather than a stub: inventing a signature from raw filename fragments would be
exactly the unsupported certainty the design refuses.

Never substitutive. Two entries describing the same experimental thing are
emphatically not interchangeable. They are two experiments, and merging their
facts would be the most damaging thing this engine could do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, digest_text
from ingence.identity import IDENTITY_METHODS, IdentityResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.extractors.base import ExtractionContext

__all__ = ["SemanticSignature", "signature_of"]


def signature_of(components: Sequence[tuple[str, str]]) -> str:
    """Return the identity of a set of normalised components.

    Canonically sorted before hashing, because a component set has no order and
    hashing one in discovery order would give a different value on every run.
    Duplicates are collapsed: a term appearing twice in a name says the same
    thing as a term appearing once, and counting it twice would make a verbose
    name a different experiment from a terse one.
    """
    unique = sorted({(kind, value) for kind, value in components})
    return digest_text(canonical_json([list(pair) for pair in unique]))


class SemanticSignature:
    """Identity by what an entry describes, once components exist."""

    name = "semantic_signature"
    version = "1"
    substitutive = False

    def supports(self, context: ExtractionContext) -> bool:
        del context
        return True

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        del context
        # This method reads no components from the extraction context, so it has no semantic
        # identity to report. Reporting one anyway would be inventing a fact.
        return None


@IDENTITY_METHODS.register(
    "semantic_signature",
    version="1",
    summary="the sorted tuple of an entry's normalised components",
)
def _build_semantic() -> SemanticSignature:
    return SemanticSignature()
