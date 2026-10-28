"""Byte identity: the only substitutive relation, and the only one that proves.

Two files with the same full digest are the same bytes. Every fact derived from
one is therefore true of the other, and the engine may transfer it rather than
re-reading, which means content work happens once per distinct content rather
than once per path.

Two files with *different* full digests are proven not identical, and that
negative is worth more than it looks. It is permanent, it prunes candidate sets
that would otherwise be re-examined on every run, and it is obtained for free as
a side effect of the positive test. Every digest is therefore stored, which is
how negative results are preserved without any separate mechanism for them.

A quick digest never proves anything. It reads the head, the tail and the
size, which is enough to say two files are probably different and never enough to
say they are the same: two files can agree on all three and differ in the middle.
It produces a candidate that a full digest must confirm. The scope recorded on
every result is what keeps that distinction visible downstream, rather than
leaving it to be inferred from a method name somebody might change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from blake3 import blake3

from ingence.core.outcomes import IdentityScope
from ingence.identity import DIGEST_ATTRIBUTE, IDENTITY_METHODS, IdentityResult

if TYPE_CHECKING:
    from ingence.extractors.base import ExtractionContext

__all__ = ["QuickDigest", "WholeDigest"]

#: Read size for streaming. Large enough that syscall overhead disappears, small
#: enough that a single read never dominates memory.
CHUNK_BYTES = 1 << 20

#: Bytes taken from each end for a quick digest.
PROBE_BYTES = 64 * 1024


class WholeDigest:
    """BLAKE3 over the entire file. The only identity that proves.

    BLAKE3 sustains a gigabyte or more per second per core, so hashing is bound
    by input and output rather than by processor in essentially every real case.
    On a local disk a terabyte is tens of minutes. On a network share it is hours
    and it hammers the share for everybody else on it, which is why the settings
    decline to hash across one by default.
    """

    name = "blake3_whole"
    version = "1"
    substitutive = True

    def supports(self, context: ExtractionContext) -> bool:
        del context
        return True

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        digest = blake3()
        read = 0
        try:
            with context.path.open("rb") as handle:
                while True:
                    block = handle.read(CHUNK_BYTES)
                    if not block:
                        break
                    digest.update(block)
                    read += len(block)
        except OSError:
            # Unreadable is not "no identity". It is a different fact, recorded
            # as a scan error by the caller. Returning None here keeps this
            # method from inventing a digest for content it never saw.
            return None
        return IdentityResult(
            method=self.name,
            method_version=self.version,
            scope=IdentityScope.WHOLE,
            value=digest.hexdigest(length=32),
            bytes_read=read,
            substitutive=True,
        )


class QuickDigest:
    """BLAKE3 over the head, the tail and the size. Nominates, never proves.

    Cheap enough to run on everything and strong enough to eliminate most
    non-matches immediately. Two files agreeing here are worth a full comparison;
    two files disagreeing need no further work, which is the whole point.
    """

    name = "blake3_quick"
    version = "1"
    substitutive = False

    def supports(self, context: ExtractionContext) -> bool:
        del context
        return True

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        digest = blake3()
        # The size is part of the payload, so two files with identical ends and
        # different lengths never collide here.
        digest.update(str(context.size_bytes).encode("ascii"))
        read = 0
        try:
            with context.path.open("rb") as handle:
                head = handle.read(PROBE_BYTES)
                digest.update(head)
                read += len(head)
                if context.size_bytes > PROBE_BYTES * 2:
                    handle.seek(-PROBE_BYTES, 2)
                    tail = handle.read(PROBE_BYTES)
                    digest.update(tail)
                    read += len(tail)
        except OSError:
            return None
        return IdentityResult(
            method=self.name,
            method_version=self.version,
            # Sampled, and the scope says so. Nothing downstream may promote a
            # sampled digest into an identity assertion.
            scope=IdentityScope.SAMPLED,
            value=digest.hexdigest(length=32),
            bytes_read=read,
            substitutive=False,
        )


@IDENTITY_METHODS.register(
    "blake3_whole",
    version="1",
    summary="BLAKE3 over the whole file; proves identity and non-identity",
    attributes={DIGEST_ATTRIBUTE: "whole"},
)
def _build_whole() -> WholeDigest:
    return WholeDigest()


@IDENTITY_METHODS.register(
    "blake3_quick",
    version="1",
    summary="BLAKE3 over head, tail and size; nominates candidates only",
    attributes={DIGEST_ATTRIBUTE: "sampled"},
)
def _build_quick() -> QuickDigest:
    return QuickDigest()
