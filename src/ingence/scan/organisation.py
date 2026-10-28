"""How a tree is organised, described rather than judged.

This module characterises a folder's organisation. It does not detect bundles.
Some scientists bundle by workflow: one folder per experiment holding every
artifact type a pipeline produced. Others keep type silos: one folder for blots,
one for microscopy, one for gels, pooling by assay across every experiment.
Others bucket by date, a folder per year. Instrument software makes folders of
its own. These are different grammars, and a signature computed uniformly means
something different in each of them.

So the module records what a folder looks like and stops. Naming a regime is a
question for a person and for a later rule. A wrong call here would lose a
user's trust: a bundle-centric view makes a well-organised type-silo laboratory
look chaotic.

A folder whose composition matches no pattern is not disorganised. Its
composition carries no signal, and the right response is to look elsewhere and
not to report weakness.

Everything here is an observation. ``contains`` is the one observationally
complete primitive, so after enumeration these are facts about what was found.
They are not claims, edges or a grouping.

Burst structure and composition answer the regime question together, so they are
one measurement. A workflow bundle holds one episode and should show one burst
or a few. A date bucket accumulates unrelated work across a year: many bursts,
high composition diversity. A type silo accumulates many experiments' worth of
one assay: many bursts, low directory spread, homogeneous composition. A copy
shows near-zero span, near-zero dispersion, high directory spread and high depth
spread together. That combination is the copy classifier, and it is more
reliable than an identical-timestamp check.

The features are unvalidated and are the first thing to test. Real trees will be
mixtures: a laboratory that bundled until 2023 and uses silos now, or a shared
drive holding four people's habits. Per-folder assignment may be the wrong unit,
and per-branch or per-author assignment may be needed. The module records the
features and commits to no assignment scheme.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, digest_text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Authorship",
    "Burst",
    "Child",
    "FolderProfile",
    "NameEvidence",
    "characterise_bursts",
    "composition_of",
    "find_bursts",
    "profile_folder",
    "read_authorship",
]


class Authorship(StrEnum):
    """Whether a folder's own name looks like a person typed it.

    The reading has three values because two would force a verdict where the
    evidence does not reach one. Absence has three states everywhere else in the
    engine for the same reason.
    """

    #: Irregular, mixes separator styles, carries natural-language words, or
    #: carries a negative-lexicon hit.
    AUTHORED = "authored"
    #: Near-deterministic: one separator style, a sequential index, no words.
    EMITTED = "emitted"
    #: The evidence points both ways or nowhere. A finding, not a gap.
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class NameEvidence:
    """What the reading of a folder's name rested on.

    Recorded beside the reading, never instead of it. A verdict whose inputs are
    not visible is a verdict nobody can disagree with, and this one is a heuristic
    over five weak signals.
    """

    #: Distinct separator characters in the name. A person mixes them; a program
    #: does not.
    separator_styles: int
    #: Words the Dictionary recognised, or alphabetic runs long enough to be
    #: words rather than codes.
    natural_words: int
    #: Hits in the negative lexicon, which records what a person thought of a
    #: file. ``final_final``, ``use_this_one``, ``ACTUAL`` and ``redo`` are
    #: evidence that a human typed the name, so the lexicon doubles as an
    #: authorship detector at no cost.
    negative_hits: int
    #: True where the name ends in an index: ``run_004``, ``Position 7``.
    sequential_index: bool
    #: Atoms in the name. Low is characteristic of emitted names.
    atom_count: int


@dataclass(frozen=True, slots=True)
class Child:
    """What a folder knows about one of its direct children.

    The record has five small fields on purpose. The characteriser runs over every
    folder in a pool, and holding whole entries would multiply the census's memory.
    Memory is the figure to watch.
    """

    #: Digest of the child's name skeleton, or ``None`` where shapes are off.
    skeleton_hash: str | None
    #: The clock this child was placed by, in nanoseconds.
    time_ns: int | None
    #: Which clock that was, so the descriptor can say what it is worth.
    clock: str
    #: Depth below the root, for the depth spread a copy shows.
    depth: int
    #: Directory the child itself sits in. Constant for direct children and not
    #: for a branch-wide burst, which is why the field exists.
    parent: str
    is_directory: bool = False


@dataclass(frozen=True, slots=True)
class Burst:
    """One episode of activity, and what kind of episode it looks like."""

    #: Last member's time minus the first's, in nanoseconds.
    span_ns: int
    count: int
    #: Members per second across the span. Zero span gives ``None`` rather than
    #: an infinity, because "everything at once" is a fact and not a rate.
    density: float | None
    #: Spread of the within-burst gaps. Near zero means a machine wrote them.
    dispersion_ns: float
    #: Distinct parent directories among the members. Local work against a
    #: tree-wide operation.
    directory_spread: int
    #: Distinct depths among the members. A flat episode against a recursive one.
    depth_spread: int
    #: Distinct skeleton digests among the members, and the share the commonest
    #: takes. What kind of episode this was.
    shape_count: int
    dominant_share: float


@dataclass(frozen=True, slots=True)
class FolderProfile:
    """Everything recorded about how one folder is organised."""

    child_count: int
    #: Distinct name skeletons among the direct children.
    shape_count: int
    #: Distinct skeletons over children, from zero to one. Zero where every
    #: child shares a shape; near one where no two do.
    shape_diversity: float
    #: The share the commonest skeleton takes.
    dominant_share: float
    #: Digest of the multiset of skeletons, so "these two folders hold the same
    #: mixture" is an equality on a short column rather than a comparison.
    composition_digest: str
    #: When the container began and last changed. A folder has a duration
    #: where a file has an instant: a directory's modification time moves on
    #: every child added, removed or renamed, so the interval is the span over
    #: which the container accumulated content. A scan file's own time records
    #: when a scanner wrote bytes, and the enclosing folder's interval may bound
    #: the whole episode.
    interval_ns: tuple[int | None, int | None]
    authorship: Authorship
    evidence: NameEvidence
    bursts: tuple[Burst, ...] = ()

    @property
    def burst_count(self) -> int:
        return len(self.bursts)


# ---------------------------------------------------------------------------
# Burst detection, passes one and two. Three and four are not built.
# ---------------------------------------------------------------------------

#: How much larger than its neighbourhood a gap must be to cut a burst.
#: This is a starting position and not a measured default. Four is the smallest factor
#: at which a gap reads as a discontinuity rather than as variation, and the
#: right value is a question for the corpus rather than for taste.
DEFAULT_GAP_FACTOR = 4.0

#: How many neighbouring gaps the local median is taken over. Odd, so the median
#: is an element rather than an average of two, and small enough to adapt inside
#: one folder.
DEFAULT_GAP_WINDOW = 9


def find_bursts(
    times: Sequence[int],
    *,
    factor: float = DEFAULT_GAP_FACTOR,
    window: int = DEFAULT_GAP_WINDOW,
) -> list[tuple[int, int]]:
    """Return index ranges over the sorted times, one per burst.

    The cut falls where a gap is large relative to its local neighbourhood, never
    against a global threshold. There is no correct global scale: a four-second
    copy and a three-day analysis session are both real bursts, and any fixed
    window either shatters the second or swallows the first. Judging locally
    finds both without the engine choosing a scale it has no basis to choose.

    The function is deterministic, with no seed and no random state, as the
    reproducibility requirement demands. It makes one pass over sorted data and
    has effectively one parameter. It does not assume one burst per folder, which
    is what admits the multi-burst case at all.

    Passes three and four are not built. They are membership with abstention, and which burst
    deserves attention. Whether they are needed depends on how many bursts a real folder
    contains, which is unmeasured.
    """
    if len(times) < 2:
        return [(0, len(times) - 1)] if times else []

    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    cuts: list[int] = []
    half = max(1, window // 2)
    for index, gap in enumerate(gaps):
        low = max(0, index - half)
        high = min(len(gaps), index + half + 1)
        neighbours = gaps[low:index] + gaps[index + 1 : high]
        if not neighbours:
            continue
        local = statistics.median(neighbours)
        # A neighbourhood of identical timestamps has a median of zero, and
        # everything is infinitely larger than zero. A gap next to a machine
        # writing files in the same nanosecond is not a discontinuity, so the
        # comparison needs a floor: cut only where the gap is nonzero and the
        # neighbourhood is not.
        if local > 0 and gap > factor * local:
            cuts.append(index)

    spans: list[tuple[int, int]] = []
    start = 0
    for cut in cuts:
        spans.append((start, cut))
        start = cut + 1
    spans.append((start, len(times) - 1))
    return spans


def characterise_bursts(children: Sequence[Child], **options: float | int) -> tuple[Burst, ...]:
    """Return one descriptor per burst among these children. Pass two.

    Children with no clock are left out rather than placed at zero, because a
    file with no time is not a file from the epoch.
    """
    timed = sorted(
        (child for child in children if child.time_ns is not None),
        key=lambda child: (child.time_ns, child.parent, child.depth),
    )
    if not timed:
        return ()
    times = [child.time_ns for child in timed if child.time_ns is not None]

    bursts: list[Burst] = []
    for first, last in find_bursts(times, **options):  # type: ignore[arg-type]
        members = timed[first : last + 1]
        stamps = times[first : last + 1]
        span = stamps[-1] - stamps[0]
        inner = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
        shapes = [child.skeleton_hash for child in members if child.skeleton_hash]
        commonest = max((shapes.count(s) for s in set(shapes)), default=0)
        bursts.append(
            Burst(
                span_ns=span,
                count=len(members),
                density=(len(members) / (span / 1e9)) if span > 0 else None,
                dispersion_ns=statistics.pstdev(inner) if len(inner) > 1 else 0.0,
                directory_spread=len({child.parent for child in members}),
                depth_spread=len({child.depth for child in members}),
                shape_count=len(set(shapes)),
                dominant_share=(commonest / len(members)) if members else 0.0,
            )
        )
    return tuple(bursts)


# ---------------------------------------------------------------------------
# Composition.
# ---------------------------------------------------------------------------


def composition_of(children: Sequence[Child]) -> tuple[int, float, float, str]:
    """Return the shape count, diversity, dominant share and digest.

    The digest is over the *multiset* of skeletons, sorted canonically, so two
    folders holding the same mixture in a different order compare equal and two
    holding different counts of one shape do not.
    """
    shapes = [child.skeleton_hash for child in children if child.skeleton_hash]
    if not shapes:
        return 0, 0.0, 0.0, digest_text(canonical_json([]))
    counts: dict[str, int] = {}
    for shape in shapes:
        counts[shape] = counts.get(shape, 0) + 1
    dominant = max(counts.values())
    diversity = (len(counts) - 1) / (len(shapes) - 1) if len(shapes) > 1 else 0.0
    digest = digest_text(canonical_json(sorted(counts.items())))
    return len(counts), diversity, dominant / len(shapes), digest


# ---------------------------------------------------------------------------
# Name authorship.
# ---------------------------------------------------------------------------

#: Separator characters a person or a program might put between name parts.
_SEPARATOR_CHARACTERS = frozenset(" _-.+()[]")

#: Below this, an alphabetic run is a code rather than a word. Four, because
#: three-letter codes are everywhere in a laboratory and four-letter ones are
#: mostly words.
_WORD_LENGTH = 4


def read_authorship(
    name: str,
    *,
    atom_count: int,
    negative_hits: int,
    sequential_index: bool,
) -> tuple[Authorship, NameEvidence]:
    """Read a folder's own name as authored, emitted, or neither.

    The reading is a heuristic over five weak signals. The evidence travels with
    the reading so a rule can disagree with it, and the middle value lets the
    reading decline.

    The negative lexicon is the strongest single signal and costs nothing. It
    records what a person thought of a file, and a name containing
    ``final_final`` or ``redo`` is one a human typed. Nothing emits those.
    """
    styles = len({character for character in name if character in _SEPARATOR_CHARACTERS})
    words = sum(
        1 for run in _alphabetic_runs(name) if len(run) >= _WORD_LENGTH and not run.isupper()
    )
    evidence = NameEvidence(
        separator_styles=styles,
        natural_words=words,
        negative_hits=negative_hits,
        sequential_index=sequential_index,
        atom_count=atom_count,
    )

    if negative_hits > 0:
        return Authorship.AUTHORED, evidence
    if styles > 1 and words > 0:
        return Authorship.AUTHORED, evidence
    # Two real words and no index is a phrase somebody wrote. "western blot
    # repeat" is not something software emits. The test does not require mixed
    # separators as well, because that would reject the commonest authored shape.
    if words >= 2 and not sequential_index:
        return Authorship.AUTHORED, evidence
    # One word and an index is the canonical emitted shape: instrument software
    # writes ``Position007`` and ``Series012``, a label and a number. The test does
    # not require zero words, because that would reject exactly these names, which
    # the reading most needs to catch. A person writing a name puts more than one
    # word in it.
    if sequential_index and styles <= 1 and words <= 1:
        return Authorship.EMITTED, evidence
    return Authorship.UNCLEAR, evidence


def _alphabetic_runs(name: str) -> list[str]:
    """Return the maximal runs of letters in a name."""
    runs: list[str] = []
    current: list[str] = []
    for character in name:
        if character.isalpha():
            current.append(character)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


# ---------------------------------------------------------------------------
# The whole profile.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FolderInput:
    """One folder and what is known about it, as the characteriser needs it."""

    name: str
    created_ns: int | None
    modified_ns: int | None
    atom_count: int = 0
    negative_hits: int = 0
    sequential_index: bool = False
    children: tuple[Child, ...] = field(default_factory=tuple)


def profile_folder(folder: FolderInput, **options: float | int) -> FolderProfile:
    """Return everything recorded about one folder's organisation."""
    shape_count, diversity, dominant, digest = composition_of(folder.children)
    authorship, evidence = read_authorship(
        folder.name,
        atom_count=folder.atom_count,
        negative_hits=folder.negative_hits,
        sequential_index=folder.sequential_index,
    )
    return FolderProfile(
        child_count=len(folder.children),
        shape_count=shape_count,
        shape_diversity=diversity,
        dominant_share=dominant,
        composition_digest=digest,
        interval_ns=(folder.created_ns, folder.modified_ns),
        authorship=authorship,
        evidence=evidence,
        bursts=characterise_bursts(folder.children, **options),
    )
