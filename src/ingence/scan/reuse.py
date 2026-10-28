"""The paired-fingerprint reuse model: can this file be skipped on a re-scan?

Nearly every indexer answers that question with some version of "has the path,
size, or modification time changed?" That question is incomplete in a way that
gets worse over time, because it asks only whether the input changed. It never
asks whether the work you would do changed. So you improve a parser, re-run
the index, and the engine serves records produced by the old parser, for every
file nobody has touched, with no end. The cache is silently poisoned and nothing
surfaces it.

This model asks both questions, with two fingerprints. Its decision returns a reason, and not
only yes or no.

The stat fingerprint asks whether the file changed. It hashes a narrow payload:
a version tag, the file kind, size, modification time and change time. What the
payload leaves out matters as much as what it holds.

- Device identifier and inode are excluded. Distributed filesystems expose one
  object under different device identifiers on different nodes, and FUSE and NFS
  gateways synthesise or remap inodes. Including them made a file re-scanned from
  another node look new, which destroyed cross-node reuse.
- Ownership, permissions and allocated size are excluded because they change
  without the content changing.
- Access time is excluded by design. The engine reads files. A fingerprint that
  included access time would record the engine's own reading as a change to the
  file, so every scan would invalidate everything the previous scan looked at.
  Excluding it makes a pure read invisible. This also lets the scanner detect
  third-party interference: it captures a stat before and after opening a file and
  compares them, and a mismatch is a recorded event and not a silent corruption.
- The file kind is included, so a regular file cannot compare equal to a symlink
  that replaced it at the same size and time.

The processing fingerprint asks whether the engine would do the same work today.
Its payload carries the policy version, the dispatch version, the extension and,
only where that class of work is selected, the full hashing policy and the full
extraction policy, including every sampling budget and external tool version.

The payload includes extractor versions only for extractors that claim this file's
extension. Including every extractor's version in every file's fingerprint would
mean that bumping the text extractor invalidated every image in the project.
Over-invalidation makes people turn incremental mode off, which costs more than
the bug it would avoid.

Both fingerprints invalidate themselves across generations. The algorithm version
is both a prefix on the value and a field inside the hashed payload, so a record
made by an older generation cannot silently compare equal to one made by a newer
one, even when one dataset contains both.

The stat fingerprint is a change detector, not proof of identity. Modification-time
granularity, time-preserving copies and clock skew can all produce a false
"unchanged". So byte identity stays a separate, explicit request and is never
inferred from this fingerprint.
"""

from __future__ import annotations

import re
import stat as stat_module
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ingence.core.extensions import canonical_extension, canonical_extensions
from ingence.core.ids import canonical_json, digest_text
from ingence.core.schema import NON_REUSABLE_OUTCOMES, ReuseDecision, ScanOutcome
from ingence.core.settings import HashMode

if TYPE_CHECKING:
    import os

    from ingence.core.registry import Registry
    from ingence.core.settings import Settings

__all__ = [
    "ABOVE_SIZE_LIMIT",
    "DISPATCH_VERSION",
    "EXTENSION_EXCLUDED",
    "EXTENSION_NOT_INCLUDED",
    "FIRST_READER_VERSION",
    "PROCESSING_FINGERPRINT_VERSION",
    "STAT_FINGERPRINT_FIELDS",
    "STAT_FINGERPRINT_VERSION",
    "PreviousRecord",
    "ProcessingPolicy",
    "StatFacts",
    "digests_wanted",
    "policy_refusal",
    "policy_selects",
    "processing_policy",
    "reader_version_is_older",
    "record_satisfies_policy",
    "reuse_decision",
    "stat_facts",
    "stat_fingerprint",
]

#: Both are a prefix on the value and a field inside the hashed payload, so a
#: change here invalidates every previously computed fingerprint by construction
#: rather than by anyone remembering to clear a cache.
STAT_FINGERPRINT_VERSION = "stat-v1"
PROCESSING_FINGERPRINT_VERSION = "processing-v1"

#: Bumped when the *routing* changes -- which extractor would be chosen for a
#: file -- without any individual extractor changing. Separate from the policy
#: version because routing and policy fail differently.
DISPATCH_VERSION = "dispatch-v1"

#: The only stat fields that enter the fingerprint. Everything omitted is
#: omitted for a reason given in this module's docstring, and adding one back
#: without reading that first will reintroduce a known bug.
STAT_FINGERPRINT_FIELDS = ("size_bytes", "modified_ns", "changed_ns")


@dataclass(frozen=True, slots=True)
class StatFacts:
    """The narrow set of filesystem facts the stat fingerprint reads."""

    size_bytes: int | None
    modified_ns: int | None
    changed_ns: int | None
    #: ``stat.S_IFMT`` of the mode: regular file, directory, symlink. Not the
    #: permission bits, which change without the content changing.
    file_kind: int | None


def stat_facts(result: os.stat_result) -> StatFacts:
    """Extract the fingerprint inputs from a stat result.

    A fact that the result does not carry is absent, never zero. A real ``stat`` carries every
    fact. A census replayed from a listing, such as an archive's file list, states a path and
    perhaps a size and nothing else. It carries none of the clocks and no mode. A zero there
    would be a file "modified on 1 January 1970", which every temporal rule would read.
    """
    mode = getattr(result, "st_mode", None)
    return StatFacts(
        size_bytes=_whole(getattr(result, "st_size", None)),
        modified_ns=_whole(getattr(result, "st_mtime_ns", None)),
        changed_ns=_whole(getattr(result, "st_ctime_ns", None)),
        file_kind=None if mode is None else stat_module.S_IFMT(int(mode)),
    )


def _whole(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


def stat_fingerprint(facts: StatFacts) -> str | None:
    """Return the change detector for one filesystem object, or None where nothing was stat-ed.

    Conservative by design: it answers "might this have changed", and a caller
    needing proof of identity must ask for a content hash instead. Nothing stat-ed has no
    fingerprint. An entry that an archive's listing declared was never ``stat``-ed, and
    a fingerprint over absent facts would call every such file unchanged since the last run.
    """
    if facts.file_kind is None:
        return None
    payload: dict[str, Any] = {
        "version": STAT_FINGERPRINT_VERSION,
        "file_kind": facts.file_kind,
        "size_bytes": facts.size_bytes,
        "modified_ns": facts.modified_ns,
        "changed_ns": facts.changed_ns,
    }
    return f"{STAT_FINGERPRINT_VERSION}:{digest_text(canonical_json(payload))}"


# ---------------------------------------------------------------------------
# The processing fingerprint.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessingPolicy:
    """What the engine would do to one file today, and the hash of that."""

    hash_selected: bool
    extraction_selected: bool
    fingerprint: str


#: Why a person's filters leave a file out of a class of work, in the words a run
#: records beside each file a dive sets aside (``scan/deepen.py``).
ABOVE_SIZE_LIMIT = "above the settings' size limit"
EXTENSION_EXCLUDED = "the settings exclude this extension"
EXTENSION_NOT_INCLUDED = "the settings read only other extensions"


def policy_refusal(
    *,
    extensions: frozenset[str],
    size_bytes: int,
    include_extensions: tuple[str, ...],
    exclude_extensions: tuple[str, ...],
    max_file_bytes: int | None,
) -> str | None:
    """Why a class of work does not apply to one file, or None where it does.

    The size limit first, then an excluded extension, then an include list that names none of
    the file's: :func:`policy_selects` is this answer's absence, so the two can never disagree.
    """
    if max_file_bytes is not None and size_bytes > max_file_bytes:
        return ABOVE_SIZE_LIMIT
    held = set(canonical_extensions(extensions))
    if held & set(canonical_extensions(exclude_extensions)):
        return EXTENSION_EXCLUDED
    included = canonical_extensions(include_extensions)
    if included and not held & set(included):
        return EXTENSION_NOT_INCLUDED
    return None


def policy_selects(
    *,
    extensions: frozenset[str],
    size_bytes: int,
    include_extensions: tuple[str, ...],
    exclude_extensions: tuple[str, ...],
    max_file_bytes: int | None,
) -> bool:
    """Return whether a class of work applies to one file.

    Exclusion wins over inclusion, and an empty include list means everything,
    which is the behaviour a person expects from a filter they have not set.
    Both the final suffix and the compound suffix are considered, so a policy
    naming ``tar.gz`` matches and one naming ``gz`` matches too.

    Both sides are compared in one form. The census stores ``txt``, a person writes ``.TXT``,
    and either may reach here, so neither is trusted to be canonical already.
    :func:`policy_refusal` says why not.
    """
    return (
        policy_refusal(
            extensions=extensions,
            size_bytes=size_bytes,
            include_extensions=include_extensions,
            exclude_extensions=exclude_extensions,
            max_file_bytes=max_file_bytes,
        )
        is None
    )


def digests_wanted(
    *,
    extensions: frozenset[str],
    size_bytes: int,
    settings: Settings,
    is_remote: bool = False,
) -> frozenset[str]:
    """Return which byte digests the hash settings ask for on one file.

    ``whole``, ``sampled``, or neither, and never both: ``quick`` reads a sample,
    ``full`` the whole file, ``adaptive`` the whole file up to ``full_hash_max_bytes``
    and a sample above it, and ``none`` nothing. The file must also pass the hash
    settings' own filters, which are the ones a processing fingerprint records.

    The dive and reuse's fingerprint both use this one definition. If the dive ran every byte
    digest on every file, the standard profile's ``quick`` mode would read every byte of what
    it examined, and a default that reads every byte of a data pool gets turned off. So the
    dive asks here, and reuse's fingerprint is computed from the same answer.
    """
    hashing = settings.hash
    # ``need`` is decided over the pool and not per file: the dive asks for the
    # digests a comparison needs itself, so the settings alone ask for none.
    # A share is skipped unless someone said otherwise: ``None`` is settings no depth was
    # applied to, and reads as the skip every preset but deep's proposes.
    if hashing.mode in (HashMode.NONE, HashMode.NEED) or (
        is_remote and hashing.skip_remote_filesystems is not False
    ):
        return frozenset()
    if not policy_selects(
        extensions=extensions,
        size_bytes=size_bytes,
        include_extensions=hashing.include_extensions,
        exclude_extensions=hashing.exclude_extensions,
        max_file_bytes=hashing.max_file_bytes,
    ):
        return frozenset()
    if hashing.mode is HashMode.QUICK:
        return frozenset({"sampled"})
    if hashing.mode is HashMode.FULL:
        return frozenset({"whole"})
    return frozenset({"whole" if size_bytes <= hashing.full_hash_max_bytes else "sampled"})


#: The first version any reader had. A reading that records no version is older than a reader's
#: version today only where today's is above this one.
FIRST_READER_VERSION = "1"


_LEADING_NUMBER = re.compile(r"\d+(?:\.\d+)*")


def _version_parts(version: str) -> tuple[int, ...] | None:
    """A version's number (``3`` or ``2.1``), or None where it does not begin with one.

    Only the leading number is the reader's version. A reader may add a tag for the variant
    it ran, as the text sketch does for another sample size (``1+4194304``). The tag says which
    variant made the reading and never marks an older or newer reader.
    """
    found = _LEADING_NUMBER.match(version.strip())
    return tuple(int(part) for part in found.group().split(".")) if found else None


def reader_version_is_older(recorded: str | None, today: str) -> bool:
    """Whether a reading made by a reader at ``recorded`` is older than the reader's ``today``.

    Only an older reading counts. A reading made by a newer reader than today's is left alone,
    as is one made by the same version. Versions are compared as numbers (``3``, ``2.1``,
    ``9`` before ``10``), each by its leading number, so a variant tag (``1+4194304``) never
    makes a reading older. A reading is never called older than a version that is not a number,
    so a reader that states none, or one in words, cannot be read again for good. A recorded
    version in words, against a reader whose version is a number, is read again once.

    A reading with no recorded version (an empty string) is older only for a reader whose
    version today is above :data:`FIRST_READER_VERSION`: a reader still at its first version has
    not changed since any reading could have been made, so nothing about it is read again.
    """
    now = _version_parts(today)
    if now is None:
        return False
    if recorded is None or not recorded.strip():
        first = _version_parts(FIRST_READER_VERSION)
        return first is not None and _padded(now, first) > _padded(first, now)
    then = _version_parts(recorded)
    if then is None:
        return True
    return _padded(then, now) < _padded(now, then)


def _padded(parts: tuple[int, ...], other: tuple[int, ...]) -> tuple[int, ...]:
    """``parts`` with zeros added to match ``other``'s length, so ``1`` equals ``1.0``."""
    return parts + (0,) * (len(other) - len(parts))


def _extractor_versions(
    extensions: frozenset[str],
    registry: Registry[Any] | None,
) -> dict[str, str]:
    """Return versions of exactly the extractors that could run on a file with these extensions.

    The narrowing is the purpose of the function. Including every extractor's version in
    every file's fingerprint means bumping one extractor invalidates the entire
    project, which is over-invalidation, and over-invalidation is what makes
    people switch incremental mode off.

    Three kinds of extractor are in scope. The first kind claims one of the extensions. The
    second is a universal fallback claiming none, which would run on anything. The third
    applies where no extractor claims the file by extension: every extractor declaring media
    types it routes by content. Content routing is then the only way the file reaches an
    extractor, and the census cannot see content, so a binary file counts too. A file
    some extractor claims stays that extractor's: a misnamed file whose extension
    another claims escapes, rather than one content router's change re-examining every
    claimed file in the pool.

    Reached through the registry and never by name, so removing an extractor
    changes a fingerprint honestly rather than leaving a dangling reference.
    """
    if registry is None:
        return {}
    # Both sides use one form: the readers declare ``.csv`` and the census
    # passes ``csv``, so compared as written no reader would ever claim a file here.
    held = set(canonical_extensions(extensions))
    versions: dict[str, str] = {}
    routers: dict[str, str] = {}
    claimed = False
    for name in registry.names(enabled_only=True):
        declaration = registry.declaration(name)
        declared = declaration.attributes.get("extensions", "")
        claims = {canonical_extension(value) for value in declared.split(",") if value.strip()}
        if not claims:
            # An extractor claiming nothing is a universal fallback and is always
            # in scope, because it would run on this file whatever its extension.
            versions[name] = declaration.version
        elif claims & held:
            versions[name] = declaration.version
            claimed = True
        elif declaration.attributes.get("media_types"):
            routers[name] = declaration.version
    if not claimed:
        versions.update(routers)
    return versions


def processing_policy(
    *,
    extensions: frozenset[str],
    size_bytes: int,
    settings: Settings,
    extractor_registry: Registry[Any] | None = None,
    external_versions: dict[str, str | None] | None = None,
    is_remote: bool = False,
) -> ProcessingPolicy:
    """Decide what work this file gets, and fingerprint that decision.

    Note what does *not* enter the payload: the path, the entry identifier, the
    run, the time. The fingerprint describes a policy applied to a class of file,
    not a particular file, so two identical files in different folders produce
    the same processing fingerprint and differ only on their stat fingerprints.
    """
    hashing = settings.hash
    extraction = settings.extraction

    hash_selected = bool(
        digests_wanted(
            extensions=extensions, size_bytes=size_bytes, settings=settings, is_remote=is_remote
        )
    )
    extraction_selected = extraction.enabled and policy_selects(
        extensions=extensions,
        size_bytes=size_bytes,
        include_extensions=extraction.include_extensions,
        exclude_extensions=extraction.exclude_extensions,
        max_file_bytes=extraction.max_file_bytes,
    )

    payload: dict[str, Any] = {
        "version": PROCESSING_FINGERPRINT_VERSION,
        "dispatch_version": DISPATCH_VERSION,
        # Canonically ordered: a set has no order, and hashing one directly is
        # one of the two mechanical causes of nondeterminism between runs.
        "extensions": list(canonical_extensions(extensions)),
        "hash": {"selected": False},
        "extraction": {"selected": False},
    }
    if hash_selected:
        payload["hash"] = {
            "selected": True,
            "mode": hashing.mode.value,
            "algorithm": hashing.algorithm,
            "chunk_bytes": hashing.chunk_bytes,
            "full_hash_max_bytes": hashing.full_hash_max_bytes,
        }
    if extraction_selected:
        payload["extraction"] = {
            "selected": True,
            "max_sample_bytes": extraction.max_sample_bytes,
            "max_text_chars": extraction.max_text_chars,
            "text_sketch_bytes": extraction.text_sketch_bytes,
            "subprocess_timeout_seconds": extraction.subprocess_timeout_seconds,
            # Isolation changes what a hostile file produces: a killed child is a
            # recorded content error where an unisolated one runs on. A record
            # made under one policy does not answer a run made under the other.
            "use_subprocess_isolation": extraction.use_subprocess_isolation,
            "subprocess_memory_bytes": extraction.subprocess_memory_bytes,
            "max_expansion_depth": extraction.max_expansion_depth,
            "max_expansion_ratio": extraction.max_expansion_ratio,
            "max_expansion_members": extraction.max_expansion_members,
            "adapters": sorted(extraction.adapters),
            "extractor_versions": _extractor_versions(extensions, extractor_registry),
            # External tool versions, for the extractors that shell out. A tool
            # upgraded underneath the engine must invalidate its own records and
            # nobody else's.
            "external_versions": dict(sorted((external_versions or {}).items())),
        }
        # A file read for its clock alone is read differently. The fingerprint records this only
        # where the setting is on and applies, so every other file's fingerprint is unchanged.
        clocked = {e.casefold().removeprefix(".") for e in extraction.clock_only_extensions}
        if extraction.nd2_clock_only and clocked & {
            e.casefold().removeprefix(".") for e in extensions
        }:
            payload["extraction"]["clock_only"] = True
    return ProcessingPolicy(
        hash_selected=hash_selected,
        extraction_selected=extraction_selected,
        fingerprint=f"{PROCESSING_FINGERPRINT_VERSION}:{digest_text(canonical_json(payload))}",
    )


# ---------------------------------------------------------------------------
# The decision.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreviousRecord:
    """What the last run recorded about one entry, as far as reuse cares.

    A small explicit structure rather than an ``Entry``, so the decision is a
    pure function that a test can drive directly, and so the storage layer owns
    the join that discovers what the previous run actually delivered.
    """

    stat_fingerprint: str | None
    processing_fingerprint: str | None
    scan_outcome: ScanOutcome
    #: Whether the previous run took a whole-file byte digest: a method declaring the ``whole``
    #: digest, never another identity that reads a whole file.
    has_full_hash: bool = False
    #: Whether it took a sampled byte digest (a method declaring ``sampled``).
    has_quick_hash: bool = False
    #: Whether the previous run recorded an error against this entry.
    had_error: bool = False
    #: The run that first saw this entry. Carried forward on reuse, because the
    #: field means "when did this appear" and overwriting it with the current
    #: run would make every entry look new on every scan, destroying the one
    #: piece of history a census can cheaply keep.
    first_seen_run: str | None = None
    #: The clock the last run recorded and where it came from, so a walk can keep a clock a
    #: dive read out of an unchanged file's content: the instant, its source and
    #: its tier, or None where there was none.
    event_time: tuple[int, str, str] | None = None
    #: The depth the last reading of the content was made at, carried with its readings.
    extraction_profile: str | None = None
    #: The move the last run recorded for this entry: ``moved_from``,
    #: ``moved_by`` and ``moved_at_ns``, or None. A walk carries a move no dive has checked yet
    #: onto the unchanged file, with the readings the check needs.
    move: tuple[str, str, int | None] | None = None


def record_satisfies_policy(
    previous: PreviousRecord,
    policy: ProcessingPolicy,
    *,
    settings: Settings,
    size_bytes: int,
) -> bool:
    """Return whether a previous record delivers what today's policy asks for.

    This is the fourth reuse miss, and the one that is easy to leave out. Both
    fingerprints can match while the previous record is still useless: a
    metadata-only record cannot satisfy a run with extraction enabled, and a
    quick hash cannot satisfy a policy that now asks for a full one.

    Errors and deferrals are never reusable. The function never caches a failure,
    so a scan interrupted by a stalled filesystem retries exactly the files that
    failed, and does not serve their failures forever.
    """
    if previous.scan_outcome in NON_REUSABLE_OUTCOMES or previous.had_error:
        return False

    if policy.hash_selected:
        mode = settings.hash.mode
        if mode is HashMode.FULL and not previous.has_full_hash:
            return False
        if mode is HashMode.QUICK and not previous.has_quick_hash:
            return False
        if mode is HashMode.ADAPTIVE:
            wants_full = size_bytes <= settings.hash.full_hash_max_bytes
            if wants_full and not previous.has_full_hash:
                return False
            if not wants_full and not previous.has_quick_hash:
                return False

    if not policy.extraction_selected:
        return True
    return previous.scan_outcome in {
        ScanOutcome.EXTRACTED,
        ScanOutcome.EXTRACTED_WITH_FALLBACK,
        # A deliberate policy exclusion satisfies an extraction-enabled run: the
        # answer was "this file is not to be opened", and that is still true.
        ScanOutcome.SKIPPED_BY_POLICY,
    }


def reuse_decision(
    previous: PreviousRecord | None,
    *,
    facts: StatFacts,
    policy: ProcessingPolicy,
    settings: Settings,
) -> ReuseDecision:
    """Decide whether a file may be skipped, and say why when it may not.

    Returns one of five values, evaluated in order, and the order matters: the
    cheapest and most decisive test runs first, and each answer is a different
    thing to tell a user.

    The run record counts all five, so "why was reuse lower than expected" is a
    query and not an investigation. This matters because a reuse rate that quietly
    drops is otherwise invisible until someone notices a scan taking hours.
    """
    if previous is None:
        return ReuseDecision.NO_PREVIOUS_RECORD
    current = stat_fingerprint(facts)
    # Nothing stat-ed says nothing is unchanged: a listed file is never reused.
    if current is None or previous.stat_fingerprint != current:
        return ReuseDecision.STAT_CHANGED
    if previous.processing_fingerprint != policy.fingerprint:
        return ReuseDecision.PROCESSING_CHANGED
    if not record_satisfies_policy(
        previous, policy, settings=settings, size_bytes=facts.size_bytes or 0
    ):
        return ReuseDecision.PREVIOUS_INCOMPLETE
    return ReuseDecision.REUSE
