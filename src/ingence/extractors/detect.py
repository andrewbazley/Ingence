"""Type detection: several detectors, all recorded, and their disagreement kept.

No single detector is unquestionable, and agreement between them is evidence.
That is the governing idea here and it shapes the whole module. An extension is a
hint that anybody can rename. Magic bytes are strong but only cover the first few
hundred bytes of formats somebody wrote a signature for. A learned classifier
generalises to formats nobody wrote a signature for and is occasionally
confidently wrong. Each is useful; none is authoritative.

So every detector's opinion is recorded, a routing hypothesis is chosen from them
by declared strength, and the *agreement* between them is recorded alongside. A
file where every detector agrees is a different situation from one where they
split, and an engine that stored only the winner would make those look identical.
Disagreement is also how the most useful cheap finding falls out for free: a
file whose extension contradicts its content, which is either a mistake worth
telling somebody about or a rename worth knowing about.

A model may route work. A model may never author a claim. That boundary is
what lets a learned detector participate at all. A type hypothesis is not a
conclusion in the graph: it selects which extractor opens the file, and whatever
that extractor actually finds supersedes it. No edge, no claim and no grouping
ever rests on a detector's output, so the thesis that every conclusion traces to
a named rule survives intact.

Byte character is separate from type. Whether a file reads as text, as
structured binary, or as high-entropy noise is a cheap universal property that
does not depend on recognising a format. It costs one sample read, it is often
decisive on its own, and it is the honest answer for the files no detector
places.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ingence.core.registry import Registry
from ingence.core.tiers import ExtractionTier
from ingence.dictionary.file_types import file_type_table

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DETECTORS",
    "SIGNATURES",
    "Agreement",
    "ByteCharacter",
    "ByteProfile",
    "Signature",
    "TypeDetector",
    "TypeOpinion",
    "TypeVerdict",
    "detect",
    "profile_bytes",
    "sample_head_and_tail",
]

#: Bytes read from each end of a file for detection. Enough for every signature
#: in the table and for a representative entropy estimate, small enough that
#: detecting a terabyte costs the same as detecting a kilobyte.
SAMPLE_BYTES = 8192


class Agreement(StrEnum):
    """How well the detectors that had an opinion agreed with each other."""

    #: Every detector that spoke said the same thing.
    UNANIMOUS = "unanimous"
    #: More than one spoke, most agreed, at least one differed.
    MAJORITY = "majority"
    #: More than one spoke and no answer had more support than another. The
    #: interesting case, and the one that should reach a person.
    SPLIT = "split"
    #: Exactly one detector had an opinion, so there is nothing to agree with.
    SINGLE = "single"
    #: Nobody recognised it.
    NONE = "none"


class ByteCharacter(StrEnum):
    """What a file's bytes look like, independent of recognising a format."""

    EMPTY = "empty"
    #: Predominantly printable in a single-byte encoding.
    TEXT = "text"
    #: Printable with the null-interleaving that marks a wide encoding.
    WIDE_TEXT = "wide_text"
    #: Structured binary: low entropy, non-printable, recognisable structure.
    BINARY = "binary"
    #: Entropy near the maximum. Compressed, encrypted, or random, and those
    #: three are not distinguishable from bytes alone. Saying so is the honest
    #: answer. Guessing which would be an unsupported certainty.
    HIGH_ENTROPY = "high_entropy"


@dataclass(frozen=True, slots=True)
class ByteProfile:
    """Cheap universal facts about a file's bytes."""

    character: ByteCharacter
    #: Shannon entropy in bits per byte, zero to eight.
    entropy: float
    printable_ratio: float
    null_ratio: float
    sampled_bytes: int


@dataclass(frozen=True, slots=True)
class TypeOpinion:
    """One detector's answer, kept whether or not it won."""

    detector: str
    detector_version: str
    media_type: str | None
    #: The detector's own label, which is often more specific than a media type.
    label: str | None = None
    #: Reported only by detectors that produce one. Absent is not zero.
    confidence: float | None = None
    #: How far this answer sits from the bytes. A signature match is
    #: mechanically exposed. An extension is a heuristic over the name.
    extraction_tier: ExtractionTier = ExtractionTier.MECHANICALLY_EXPOSED
    #: Higher wins when opinions differ. Declared per detector rather than
    #: inferred, so changing the routing order is a one-line edit that shows up
    #: in a diff.
    strength: int = 0


@dataclass(frozen=True, slots=True)
class TypeVerdict:
    """Everything detection concluded, including what it could not settle."""

    opinions: tuple[TypeOpinion, ...] = ()
    #: The routing hypothesis. Selects an extractor and nothing else.
    media_type: str | None = None
    chosen_by: str | None = None
    agreement: Agreement = Agreement.NONE
    #: Whether the extension is consistent with what the content says. ``None``
    #: when there is no extension or nothing to compare it against. False is a
    #: finding: the file is misnamed, or it was renamed deliberately.
    extension_agrees: bool | None = None
    profile: ByteProfile | None = None

    @property
    def contested(self) -> bool:
        """Whether detectors disagreed, which is worth a person's attention."""
        return self.agreement in {Agreement.SPLIT, Agreement.MAJORITY}


@runtime_checkable
class TypeDetector(Protocol):
    """What every type detector implements."""

    name: str
    version: str
    strength: int

    def look(self, path: Path, sample: bytes, extensions: frozenset[str]) -> TypeOpinion | None:
        """Return an opinion, or None when the detector does not recognise it.

        Given a sample rather than the path alone, so that detectors share one
        read. Opening a large file once per detector would make detection cost
        scale with how many detectors are installed, which is exactly backwards.
        """
        ...


#: Every type detector, reachable only through here. Removing one is deleting a
#: module and a registration. Adding one changes routing without touching any
#: extractor.
DETECTORS: Registry[TypeDetector] = Registry("type_detector")


# ---------------------------------------------------------------------------
# Sampling and byte character.
# ---------------------------------------------------------------------------


def sample_head_and_tail(path: Path, *, size: int = SAMPLE_BYTES) -> bytes:
    """Read a bounded sample from the start and end of a file.

    Both ends, because several formats put their structure at the end: a ZIP's
    central directory, an ID3v1 tag, some container indexes. Reading only the
    head would miss them and reading the whole file would make detection cost
    proportional to size, which for a data pool of microscopy stacks is the
    difference between seconds and hours.
    """
    # An OSError is the caller's to record. If this function swallowed it, a file on a share
    # that had stopped answering would look empty and be detected as nothing, and the pass
    # would go on to read a body that was not there. Recorded, it is an access failure against
    # the file, and twenty of them across two folders make the pass look at the root.
    with path.open("rb") as handle:
        head = handle.read(size)
        handle.seek(0, 2)
        length = handle.tell()
        if length <= size:
            return head
        handle.seek(max(size, length - size))
        return head + handle.read(size)


def profile_bytes(sample: bytes) -> ByteProfile:
    """Characterise a sample without recognising any format.

    Deliberately refuses to distinguish compressed from encrypted from random.
    All three look the same from bytes alone, and reporting one of them would be
    an unsupported certainty. ``high_entropy`` is what is actually known.
    """
    if not sample:
        return ByteProfile(ByteCharacter.EMPTY, 0.0, 0.0, 0.0, 0)

    counts = Counter(sample)
    total = len(sample)
    entropy = -sum((count / total) * math.log2(count / total) for count in counts.values())
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    nulls = counts.get(0, 0)
    printable_ratio = printable / total
    null_ratio = nulls / total

    # Wide text: printable ASCII interleaved with nulls, which is what UTF-16
    # looks like to a byte scanner and is common in Windows-authored files.
    if 0.30 <= null_ratio <= 0.60 and printable_ratio >= 0.30:
        character = ByteCharacter.WIDE_TEXT
    elif entropy >= 7.5:
        character = ByteCharacter.HIGH_ENTROPY
    elif printable_ratio >= 0.85 and null_ratio < 0.01:
        character = ByteCharacter.TEXT
    else:
        character = ByteCharacter.BINARY

    return ByteProfile(
        character=character,
        entropy=round(entropy, 4),
        printable_ratio=round(printable_ratio, 4),
        null_ratio=round(null_ratio, 4),
        sampled_bytes=total,
    )


# ---------------------------------------------------------------------------
# The signature table.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signature:
    """One magic-byte pattern and what it means."""

    magic: bytes
    media_type: str
    label: str
    offset: int = 0
    #: Extensions consistent with this content. Used to decide whether the name
    #: contradicts the bytes, which is the cheapest useful finding in the module.
    extensions: tuple[str, ...] = ()


#: Signatures the engine recognises without any optional dependency, read from the file-type
#: table (``dictionary/file_types.yaml``). Each row gives its magic bytes and the extensions
#: consistent with them, the owning row's and every format built on it. The table is matched
#: longest magic first, so a specific signature beats a general one that happens to be its
#: prefix. The list is not exhaustive: a signature for a format that nothing can open buys a
#: label and no capability.
SIGNATURES: tuple[Signature, ...] = tuple(
    Signature(
        magic=spec.magic,
        media_type=spec.media_type,
        label=spec.label,
        offset=spec.offset,
        extensions=spec.extensions,
    )
    for spec in file_type_table().signatures()
)

#: Extension to media type, used only as a hint and only when nothing stronger spoke. It holds
#: every row of the table that states a media type. It is a guess about a name and never
#: knowledge. A row that states no media type (binary data, an environment file) hints nothing,
#: so unrecognised bytes stay unrecognised.
EXTENSION_HINTS: dict[str, str] = file_type_table().extension_hints()


def _match_signature(sample: bytes) -> Signature | None:
    """Return the most specific signature matching a sample, or None."""
    best: Signature | None = None
    for signature in SIGNATURES:
        end = signature.offset + len(signature.magic)
        matched = len(sample) >= end and sample[signature.offset : end] == signature.magic
        if matched and (best is None or len(signature.magic) > len(best.magic)):
            best = signature
    return best


# ---------------------------------------------------------------------------
# The built-in detectors.
# ---------------------------------------------------------------------------


class SignatureDetector:
    """Magic bytes, from the table above. No dependencies, always available."""

    name = "signature"
    #: Version 2 reads the file-type table's signatures. Version 1 read a table of its own.
    version = "2"
    strength = 60

    def look(self, path: Path, sample: bytes, extensions: frozenset[str]) -> TypeOpinion | None:
        del path, extensions
        signature = _match_signature(sample)
        if signature is None:
            return None
        return TypeOpinion(
            detector=self.name,
            detector_version=self.version,
            media_type=signature.media_type,
            label=signature.label,
            # Read from the file's own structure without interpreting it.
            extraction_tier=ExtractionTier.MECHANICALLY_EXPOSED,
            strength=self.strength,
        )


class ExtensionDetector:
    """The filename suffix, treated as a hint and nothing more.

    Weakest by declared strength, because anybody can rename anything and people
    routinely do. It earns its place by covering the plain-text formats that have
    no magic bytes at all, where the name is genuinely the only evidence.
    """

    name = "extension"
    #: Version 2 reads the file-type table's hints, one per row that states a media type.
    #: Version 1 read a short list of its own.
    version = "2"
    strength = 10

    def look(self, path: Path, sample: bytes, extensions: frozenset[str]) -> TypeOpinion | None:
        del path, sample
        for extension in sorted(extensions, key=len, reverse=True):
            hint = EXTENSION_HINTS.get(extension)
            if hint is not None:
                return TypeOpinion(
                    detector=self.name,
                    detector_version=self.version,
                    media_type=hint,
                    label=extension,
                    # A guess from the name, not a reading of the content.
                    extraction_tier=ExtractionTier.HEURISTIC,
                    strength=self.strength,
                )
        return None


@DETECTORS.register("signature", version="2", summary="magic bytes from the file-type table")
def _build_signature() -> TypeDetector:
    return SignatureDetector()


@DETECTORS.register("extension", version="2", summary="the filename suffix, as a hint only")
def _build_extension() -> TypeDetector:
    return ExtensionDetector()


# ---------------------------------------------------------------------------
# Optional detectors.
#
# Both are registered whether or not their dependency is installed. That is the
# point of declaring requirements: a run record can then say that a detector was
# configured and could not run, which is a different fact from it not being
# configured, and the experiment register needs to tell those apart.
# ---------------------------------------------------------------------------


class LibmagicDetector:
    """The signature database behind the ``file`` command.

    Far broader than the built-in table and maintained by people who do nothing
    else. Optional because it needs a system library, which is exactly the kind
    of install friction that makes a tool feel institutional.
    """

    name = "libmagic"
    version = "1"
    strength = 70

    def __init__(self) -> None:
        import magic

        self._magic = magic.Magic(mime=True)
        self._describe = magic.Magic()

    def look(self, path: Path, sample: bytes, extensions: frozenset[str]) -> TypeOpinion | None:
        del path, extensions
        if not sample:
            return None
        try:
            media_type = self._magic.from_buffer(sample)
            label = self._describe.from_buffer(sample)
        except Exception:
            return None
        if not media_type or media_type == "application/octet-stream":
            # Refusing to answer is an answer. Recording the fallback as an
            # opinion would let "I do not know" outvote a detector that does.
            return None
        return TypeOpinion(
            detector=self.name,
            detector_version=self.version,
            media_type=media_type,
            label=label,
            extraction_tier=ExtractionTier.MECHANICALLY_EXPOSED,
            strength=self.strength,
        )


class MagikaDetector:
    """A local learned content-type classifier.

    Generalises to formats nobody wrote a signature for, which is most of them.
    It runs entirely locally: a packaged model, CPU inference, telemetry disabled,
    no server component.

    It routes. It never concludes. Its output selects an extractor and is
    superseded by whatever that extractor actually finds. Nothing in the graph
    rests on it, so the claim that every conclusion traces to a named rule is
    untouched by its presence. That boundary is the whole reason a learned
    component is admissible here at all, and it is why this class returns a
    ``TypeOpinion`` rather than anything that could become a claim.
    """

    name = "magika"
    version = "1"
    strength = 50

    def __init__(self) -> None:
        from magika import Magika

        self._magika = Magika()

    def look(self, path: Path, sample: bytes, extensions: frozenset[str]) -> TypeOpinion | None:
        del path, extensions
        if not sample:
            return None
        try:
            result = self._magika.identify_bytes(sample)
        except Exception:
            return None
        output = getattr(result, "output", None)
        if output is None:
            return None
        label = getattr(output, "label", None) or getattr(output, "ct_label", None)
        media_type = getattr(output, "mime_type", None)
        score = getattr(result, "score", None) or getattr(output, "score", None)
        if not media_type and not label:
            return None
        return TypeOpinion(
            detector=self.name,
            detector_version=self.version,
            media_type=media_type,
            label=label,
            confidence=float(score) if score is not None else None,
            # A learned guess over sampled bytes, not a structural reading.
            extraction_tier=ExtractionTier.HEURISTIC,
            strength=self.strength,
        )


@DETECTORS.register(
    "libmagic",
    version="1",
    summary="the signature database behind the file command",
    requires=("magic",),
)
def _build_libmagic() -> TypeDetector:
    return LibmagicDetector()


@DETECTORS.register(
    "magika",
    version="1",
    summary="a local learned content-type classifier; routes, never concludes",
    requires=("magika",),
)
def _build_magika() -> TypeDetector:
    return MagikaDetector()


# ---------------------------------------------------------------------------
# Putting it together.
# ---------------------------------------------------------------------------


def _agreement_of(opinions: tuple[TypeOpinion, ...]) -> Agreement:
    """Classify how well the detectors that spoke agreed."""
    answers = [opinion.media_type for opinion in opinions if opinion.media_type]
    if not answers:
        return Agreement.NONE
    if len(answers) == 1:
        return Agreement.SINGLE
    counts = Counter(answers)
    top = counts.most_common()
    if len(counts) == 1:
        return Agreement.UNANIMOUS
    if len(top) > 1 and top[0][1] == top[1][1]:
        return Agreement.SPLIT
    return Agreement.MAJORITY


def _types_compatible(left: str | None, right: str | None) -> bool:
    """Whether two media types describe the same thing at different granularity.

    A markdown file *is* plain text and a CSV *is* plain text, so a detector
    saying ``text/plain`` and an extension saying ``text/markdown`` are not in
    conflict. Treating them as conflicting would report a disagreement on every
    text file in a data pool, and a finding that fires constantly is a finding
    nobody reads, which would destroy the value of the one case that matters: a
    file whose extension genuinely contradicts its content.

    Generic answers are compatible with any specific answer under the same
    top-level type, and nothing else is.
    """
    if left is None or right is None:
        return False
    if left == right:
        return True
    generic = {"text/plain", "application/octet-stream"}
    if left in generic or right in generic:
        return left.split("/", 1)[0] == right.split("/", 1)[0]
    return False


def _extension_agrees(
    extensions: frozenset[str], chosen: TypeOpinion | None, sample: bytes
) -> bool | None:
    """Whether the name is consistent with what the content says.

    ``None`` where there is nothing to compare: no extension, or no content
    opinion to compare it against. Only ``False`` is a finding, and it is one of
    the cheapest useful things the engine can notice.
    """
    if not extensions or chosen is None or chosen.detector == "extension":
        return None
    signature = _match_signature(sample)
    if signature is not None and signature.extensions:
        return bool(extensions & set(signature.extensions))
    hinted = {
        EXTENSION_HINTS[extension] for extension in extensions if extension in EXTENSION_HINTS
    }
    if not hinted:
        return None
    return any(_types_compatible(chosen.media_type, hint) for hint in hinted)


def detect(
    path: Path,
    extensions: frozenset[str],
    *,
    enabled: tuple[str, ...] = (),
    sample: bytes | None = None,
) -> TypeVerdict:
    """Ask every available detector, and record all of them.

    ``enabled`` restricts which detectors run, which is how the experiment
    register compares routing configurations: the same corpus under three
    detector sets, with every run record stating which were active. An empty
    tuple means every registered detector that is available.

    One sample is read and shared, so adding a detector costs inference time and
    not another pass over the file.
    """
    payload = sample_head_and_tail(path) if sample is None else sample
    profile = profile_bytes(payload)

    names = enabled or DETECTORS.names(available_only=True)
    opinions: list[TypeOpinion] = []
    for name in names:
        if not DETECTORS.available(name):
            continue
        try:
            detector = DETECTORS.get(name)
            opinion = detector.look(path, payload, extensions)
        except Exception:  # noqa: S112
            # One detector must not fail detection for the others, and the
            # reason is not lost: a component that cannot be built has its
            # failure recorded by the registry and reported in the run record,
            # and a detector that raises on one file simply has no opinion
            # about that file.
            continue
        if opinion is not None:
            opinions.append(opinion)

    ordered = tuple(sorted(opinions, key=lambda opinion: (-opinion.strength, opinion.detector)))
    chosen = next((opinion for opinion in ordered if opinion.media_type), None)
    return TypeVerdict(
        opinions=ordered,
        media_type=chosen.media_type if chosen else None,
        chosen_by=chosen.detector if chosen else None,
        agreement=_agreement_of(ordered),
        extension_agrees=_extension_agrees(extensions, chosen, payload),
        profile=profile,
    )
