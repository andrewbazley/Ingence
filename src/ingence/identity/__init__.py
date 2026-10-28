"""The five identities, behind one protocol, reachable only through a registry.

There is no single token that tells you whether two files are the same, and
pretending otherwise causes errors. There are five, they answer different
questions, and their costs differ by orders of magnitude. Stating them
separately is what lets the engine spend input and output intelligently.

===============  =========================================  ==========
Identity         Question                                   Cost
===============  =========================================  ==========
Locational       Is this the same path?                     free
Byte             Are these literally the same bytes?        O(size)
Structural       Do these have the same shape or schema?    header only
Perceptual       Do these read or look like the same thing? samplable
Semantic         Do these describe the same experiment?     free after
===============  =========================================  ==========

Cheap identities nominate. Expensive identities verify. The engine will not
open a large file merely to guess. It opens files to settle questions it has
already found reason to ask. That is an interpretive commitment rather than an
implementation detail, which is why it is written here where a user can read it
rather than buried in a scheduler.

Only byte identity is substitutive. If two files are the same bytes, every
fact derived from one is automatically true of the other and the engine may
transfer it without re-reading. No other identity licenses that: two tables with
the same schema are not interchangeable, and two experiments under one condition
certainly are not. The protocol declares which methods are substitutive and the
publication invariants enforce that only one of them ever is.

A partial hash never asserts identity. This is a hard rule. A sampled digest
produces a candidate that something else must confirm. The whole value of a full
hash is that a *difference* is proof of non-identity, and that proof is worth
more than people expect: it prunes candidate sets permanently, so the work is
never repeated. Storing every digest is therefore how negative results are
preserved, without any separate mechanism for recording them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ingence.core.outcomes import IdentityScope
from ingence.core.registry import Registry

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = [
    "DIGEST_ATTRIBUTE",
    "IDENTITY_METHODS",
    "IdentityMethod",
    "IdentityResult",
    "digest_methods",
    "load_builtins",
]


@dataclass(frozen=True, slots=True)
class IdentityResult:
    """One identity computed for one entry."""

    method: str
    method_version: str
    #: How much of the entry was read to produce it. The difference between a
    #: proof and a candidate, and it travels with the value rather than being
    #: inferred from the method name.
    scope: IdentityScope
    #: The digest or signature. Opaque. Only comparison between values of the
    #: same method and version is meaningful.
    value: str
    bytes_read: int = 0
    #: True only for whole-content cryptographic identity. Checked rather than
    #: trusted: the INV-7 check in ``core/invariants.py`` confines substitutivity to ``identical``.
    substitutive: bool = False
    #: Counts the method read beside its value, as ``(key, number)``: the dimensions of an
    #: array, for one. Each is stored as an observation of its own, so a rule can state a
    #: floor on a count without the value being unpacked.
    counts: tuple[tuple[str, int], ...] = ()


@runtime_checkable
class IdentityMethod(Protocol):
    """What every identity method implements."""

    name: str
    version: str
    #: Whether a value from this method may license transferring a fact between
    #: two entries that share it. True for exactly one method.
    substitutive: bool

    def supports(self, context: ExtractionContext) -> bool:
        """Whether this method can produce a value for this entry."""
        ...

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        """Return the identity, or None when it could not be produced.

        Returning None is an ordinary outcome, not an error: a structural
        identity over a file with no recoverable structure has nothing to say,
        and saying nothing is more useful than inventing a signature.
        """
        ...


#: Every identity method, reachable only through here.
IDENTITY_METHODS: Registry[IdentityMethod] = Registry("identity_method")

#: The registration attribute a byte digest declares: ``whole`` or ``sampled``, the part of
#: a file it reads. ``hash.mode`` chooses among the methods declaring it and governs no other
#: identity method, because a sketch or a schema is not a digest of the bytes. Read
#: from the registration, so the dive chooses a digest without naming any.
DIGEST_ATTRIBUTE = "digest"


def load_builtins() -> None:
    """Import the modules that register the built-in identity methods.

    Explicit rather than implicit. Importing them from this module's top level
    would reintroduce the coupling the registry exists to prevent, and one
    missing optional dependency would then break the whole package rather than
    one method. Removing a method is deleting its module and its line here,
    which is the one-line deletion that the optional-method design depends on.
    """
    from ingence.identity import bytes as _bytes  # noqa: F401
    from ingence.identity import perceptual as _perceptual  # noqa: F401
    from ingence.identity import semantic as _semantic  # noqa: F401
    from ingence.identity import structure as _structure  # noqa: F401


def digest_methods() -> dict[str, str]:
    """Each available identity method declaring a byte digest, by the digest it is.

    This is the one list of byte digests. The dive chooses and carries digests by it,
    and reuse asks it whether the last run took the digest the hash settings ask for. Read from
    each registration's :data:`DIGEST_ATTRIBUTE`, so no caller names a method. An image's
    difference hash reads the whole image and a text sketch reads a sample, but neither is a
    digest of the bytes, and only a byte digest proves or nominates a copy.
    """
    load_builtins()
    found: dict[str, str] = {}
    for name in IDENTITY_METHODS.names(available_only=True):
        digest = IDENTITY_METHODS.declaration(name).attributes.get(DIGEST_ATTRIBUTE)
        if digest is not None:
            found[name] = digest
    return found
