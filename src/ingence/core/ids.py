"""Identity: canonical encoding first, then the identity functions.

Two identical runs over identical input must produce identical output, including identifiers. The
whole project rests on that reproducibility claim. The code achieves it by construction, not by
testing. Every function here is pure, every input is canonicalised before it is hashed, and
nothing that varies between runs enters a payload.

Three rules produce that property.

Canonical order before any hash. Sets and dictionaries have no order. Serialising one directly and
hashing the result gives a different identifier on each run. Every payload here passes through
:func:`canonical_json`, which sorts keys and emits without whitespace.

Evidence beside the identifier, never inside it. Hashing a human-readable evidence string,
assembled from unordered sets, into an edge identifier would make two logically identical runs
differ on many edge identifiers. An edge's identity derives only from what makes it *that edge*:
canonical source, target, primitive, respect, rule, rule version and scope. The evidence that
justified it is a column beside the identifier.

Paths are normalised before they are hashed. macOS stores filenames in decomposed form and Linux
stores whatever bytes it was given, so the same file scanned from two machines yields two
different byte sequences for one name. Paths are normalised to NFC for identity. The bytes as read
are preserved verbatim in the record, so the identifier is stable across platforms and nothing
about the original name is lost.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from blake3 import blake3

from ingence.core.relations import PRIMITIVE_PROPERTIES, Primitive

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

__all__ = [
    "DIGEST_BYTES",
    "ID_SCHEME_VERSION",
    "PROJECT_SCOPE",
    "CaseSensitivity",
    "IdKind",
    "LocatorStep",
    "Scope",
    "ScopeKind",
    "candidate_id",
    "canonical_json",
    "canonical_relative_path",
    "canonical_sorted",
    "claim_id",
    "component_id",
    "dictionary_binding_id",
    "dictionary_term_id",
    "digest_file",
    "digest_text",
    "edge_id",
    "entry_id",
    "finding_id",
    "observation_id",
    "record_event_id",
    "root_id",
    "rule_failure_id",
    "run_began_ns",
    "run_id",
    "stable_id",
]

#: Version of the identity scheme itself. It is both a prefix on every payload
#: and a field inside it, so a change to how identifiers are computed invalidates
#: every previously computed identifier rather than silently producing two
#: incompatible generations that look alike.
ID_SCHEME_VERSION = "id-v1"

#: Digest length in bytes. 16 bytes is 128 bits, which gives a collision
#: probability below one in a billion at ten billion identifiers. Identifiers are
#: carried in every row of every table, so the width is a real storage cost and
#: a wider digest buys nothing at this scale.
DIGEST_BYTES = 16


class IdKind(StrEnum):
    """The kind of thing an identifier names.

    The kind is both a prefix on the identifier and a field inside the hashed
    payload. The prefix makes an identifier readable in a log or a query result.
    The field inside guarantees two different kinds cannot collide even if their
    remaining inputs coincide.
    """

    ENTRY = "entry"
    EDGE = "edge"
    OBSERVATION = "obs"
    CLAIM = "claim"
    #: A rule unable to decide on one subject. Beside the claim because it is the
    #: other thing evaluating a rule can publish.
    RULE_FAILURE = "failure"
    #: What a flagging rule raised about one subject. Beside the failure because it is
    #: the other thing evaluating a rule publishes that is not a claim.
    FINDING = "finding"
    CANDIDATE = "cand"
    COMPONENT = "comp"
    NAME_SHAPE = "shape"
    BINDING = "binding"
    RUN = "run"
    RECORD_EVENT = "event"
    DICTIONARY_TERM = "term"
    THEME = "theme"
    ACTIVITY = "activity"
    ROOT = "root"


class CaseSensitivity(StrEnum):
    """Whether a root's filesystem distinguishes names differing only in case.

    Recorded per root rather than assumed globally, because one project can span
    a case-sensitive volume and a case-insensitive one and the same name means
    different things on each.
    """

    SENSITIVE = "sensitive"
    INSENSITIVE = "insensitive"
    #: Not determined. Treated as sensitive for identity, which is the choice
    #: that never merges two genuinely distinct files.
    UNKNOWN = "unknown"


class ScopeKind(StrEnum):
    """The kind of region a rule, an assertion or a decision applies to."""

    PROJECT = "project"
    #: One indexed root within the project.
    ROOT = "root"
    FOLDER = "folder"
    #: An explicit list of entries chosen by a person.
    SELECTION = "selection"
    #: A named equivalence class, used when a rule evaluates at a coarse level.
    LEVEL = "level"
    #: Files of one kind, anywhere in the project, named by the Dictionary term
    #: their extension binds to rather than by a spelling of it: ``tiff_image``,
    #: not ``tif`` and separately ``tiff``. This is the one scope that does not cut the
    #: filesystem into regions. People reach for it first, because what a word means in a
    #: microscope's file is not what it means in a spreadsheet, and neither statement is about a
    #: folder.
    FILE_TYPE = "file_type"


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a rule, assertion or decision applies.

    Scope is first-class because the most common real correction is not "this
    rule is wrong" but "this rule is wrong *here*". Without scoping, a user
    disables a good rule globally in order to fix one folder.
    """

    kind: ScopeKind
    #: What the scope names: a root identifier, a canonical folder path, a level
    #: name, or ``None`` for the whole project.
    value: str | None = None

    @property
    def token(self) -> str:
        """Return the canonical string form used inside identity payloads."""
        return self.kind.value if self.value is None else f"{self.kind.value}:{self.value}"


#: The project-wide scope, which is the default for a rule that names none.
PROJECT_SCOPE = Scope(ScopeKind.PROJECT)


@dataclass(frozen=True, slots=True)
class LocatorStep:
    """One step into the interior of a file.

    A locator step says how to get from a container to something inside it. A
    chain of them addresses a stream at any depth: the third sheet of a workbook
    inside a zip archive is two steps.
    """

    #: How the step opens its container: ``zip``, ``sheet``, ``page``, ``stream``,
    #: ``attachment``, ``byte_range``, and so on. A registered extractor declares
    #: the schemes it can follow. Nothing here enumerates them, because the set grows with the
    #: extractors and must not require a schema change.
    scheme: str
    #: What the step selects within the container: a member path, a sheet name, a
    #: byte offset pair rendered as a string.
    target: str
    #: Disambiguates when a container has several members the target alone does
    #: not separate. Zero where the target is already unique.
    ordinal: int = 0

    def as_payload(self) -> dict[str, Any]:
        """Return the canonical mapping used inside an identity payload."""
        return {"scheme": self.scheme, "target": self.target, "ordinal": self.ordinal}


# ---------------------------------------------------------------------------
# Canonical encoding.
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Serialise a value to the one canonical form used before every hash.

    Keys sorted, no whitespace, non-ASCII kept as itself rather than escaped.
    Two structurally equal values always produce the same string, which is the
    whole requirement.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sorted(values: Iterable[str]) -> tuple[str, ...]:
    """Return strings in canonical order.

    Ordering is by Unicode code point, which is what Python's default sort does
    and is deliberately not locale-aware. A locale-sensitive sort would make an
    identifier depend on the machine that computed it.
    """
    return tuple(sorted(values))


def canonical_relative_path(
    path: str,
    *,
    case_sensitivity: CaseSensitivity = CaseSensitivity.SENSITIVE,
) -> str:
    """Return the form of a relative path used for identity.

    Separators become forward slashes so a path is the same on Windows as
    elsewhere. The result is NFC-normalised so a decomposed macOS name and a
    precomposed Linux name agree. Leading ``./`` and surrounding slashes are
    stripped so one location has one spelling.

    Case is folded only where the root's filesystem is known to be
    case-insensitive, in which case two spellings genuinely name one file. Where
    sensitivity is unknown the path is left as it is, because keeping two files
    apart that might be one is recoverable and merging two that are not is not.

    The value returned here is for hashing. The path as read is stored verbatim
    on the entry and is what the user is shown.
    """
    normalized = unicodedata.normalize("NFC", path.replace("\\", "/"))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.strip("/")
    if case_sensitivity is CaseSensitivity.INSENSITIVE:
        normalized = normalized.casefold()
    return normalized


def digest_text(text: str) -> str:
    """Return the canonical digest of a string as lowercase hexadecimal."""
    return blake3(text.encode("utf-8")).hexdigest(length=DIGEST_BYTES)


#: Bytes of a file :func:`digest_file` reads at a time.
FILE_DIGEST_CHUNK_BYTES = 1 << 20


def digest_file(path: Path, *, chunk_bytes: int = FILE_DIGEST_CHUNK_BYTES) -> str:
    """Return ``digest_text(path.read_bytes().hex())``, reading the file a piece at a time.

    The value is the same as for the whole file, but the memory used is bounded. Manifests already
    written hold digests made by reading each file whole and hashing it as hexadecimal text, which
    needs twice the file's size in memory. The hex of a whole file is the hex of its pieces
    joined, so hashing each piece's hex in turn gives exactly the same digest.
    """
    hasher = blake3()
    with path.open("rb") as handle:
        while piece := handle.read(chunk_bytes):
            hasher.update(piece.hex().encode("ascii"))
    return hasher.hexdigest(length=DIGEST_BYTES)


def stable_id(kind: IdKind, payload: dict[str, Any]) -> str:
    """Return the identifier for one payload under one kind.

    Every identity function in this module funnels through here, so the scheme
    version, the kind tagging and the canonical encoding are applied once and
    cannot be forgotten at a call site.
    """
    envelope = {"scheme": ID_SCHEME_VERSION, "kind": kind.value, "payload": payload}
    return f"{kind.value}_{digest_text(canonical_json(envelope))}"


# ---------------------------------------------------------------------------
# Entry identity.
# ---------------------------------------------------------------------------


def root_id(root_uri: str) -> str:
    """Return the logical identifier of an indexed root.

    A root's identity is separated from the path it happens to be mounted at, so
    a project survives being moved or being reached through a different mount on
    another machine. Relocation changes the mount path and leaves every entry
    identifier untouched.
    """
    return stable_id(IdKind.ROOT, {"root_uri": root_uri})


def entry_id(
    root: str,
    relative_path: str,
    locator_chain: Sequence[LocatorStep] = (),
    *,
    case_sensitivity: CaseSensitivity = CaseSensitivity.SENSITIVE,
) -> str:
    """Return the identifier of an entry.

    The identity function is composite from the start:

        ``entry_id = hash(root, relative_path, locator_chain)``

    With an empty locator chain, which is the default and covers every ordinary
    file and directory, this is exactly path-based identity. The chain exists so
    that a derived stream can be promoted to a node of its own without a migration. An
    example is an archived spreadsheet that is genuinely the input to a downstream analysis,
    where provenance should point at the spreadsheet and not at the archive.

    Promotion is an event, not a default. Nothing is promoted unless a rule
    or a person asks for it. The overwhelming majority of derived streams are
    observations *about* the file they came from and carry a locator on the
    observation, not an entry of their own.
    """
    return stable_id(
        IdKind.ENTRY,
        {
            "root": root,
            "path": canonical_relative_path(relative_path, case_sensitivity=case_sensitivity),
            "locator": [step.as_payload() for step in locator_chain],
        },
    )


# ---------------------------------------------------------------------------
# Edge identity.
# ---------------------------------------------------------------------------


def edge_id(
    *,
    source: str,
    target: str,
    primitive: Primitive,
    rule_id: str,
    rule_version: str,
    scope: Scope,
    respect: str | None = None,
) -> str:
    """Return the identifier of an asserted edge.

    Identity derives from exactly seven things and no others: canonical source,
    canonical target, primitive, respect, rule identifier, rule version and
    scope. Nothing about the evidence enters. The evidence is stored beside the
    identifier, where it can be read, disputed and replaced without the edge
    becoming a different edge.

    Endpoints are ordered for a symmetric primitive, because the identity
    condition for every symmetric relation in the set names an *unordered* pair.
    Asserting that A is identical to B and that B is identical to A must produce
    one edge, not two.

    A respect is required where the primitive declares one, which is
    ``equivalent``. Equivalence without a stated dimension is not a claim.
    """
    properties = PRIMITIVE_PROPERTIES[primitive]
    if properties.requires_respect and respect is None:
        message = f"primitive {primitive.value!r} requires a respect"
        raise ValueError(message)
    endpoints = (
        list(canonical_sorted((source, target))) if properties.symmetric else [source, target]
    )
    return stable_id(
        IdKind.EDGE,
        {
            "endpoints": endpoints,
            "primitive": primitive.value,
            "respect": respect,
            "rule_id": rule_id,
            "rule_version": rule_version,
            "scope": scope.token,
        },
    )


# ---------------------------------------------------------------------------
# The remaining record identities.
# ---------------------------------------------------------------------------


def observation_id(
    *,
    entry: str,
    observer: str,
    observer_version: str,
    key: str,
    locator_chain: Sequence[LocatorStep] = (),
) -> str:
    """Return the identifier of one observation.

    An observation is identified by what was observed, of what, by whom, and at
    which version. Two runs of one extractor at one version over one unchanged
    file produce the same observation identifier, which is what makes reuse
    detectable and a re-scan a diff rather than a duplication.

    The time of observation is deliberately absent. Time is a column. It is not
    part of what makes an observation that observation.
    """
    return stable_id(
        IdKind.OBSERVATION,
        {
            "entry": entry,
            "observer": observer,
            "observer_version": observer_version,
            "key": key,
            "locator": [step.as_payload() for step in locator_chain],
        },
    )


def claim_id(
    *,
    subject: str,
    predicate: str,
    object_reference: str | None,
    rule_id: str,
    rule_version: str,
    scope: Scope,
) -> str:
    """Return the identifier of one claim.

    A claim is identified by what it says, about what, under which rule at which
    version, within which scope. The observations it consumed are recorded on the
    claim and do not enter its identity: a claim supported by more evidence is
    the same claim, better supported.
    """
    return stable_id(
        IdKind.CLAIM,
        {
            "subject": subject,
            "predicate": predicate,
            "object": object_reference,
            "rule_id": rule_id,
            "rule_version": rule_version,
            "scope": scope.token,
        },
    )


def rule_failure_id(*, rule_id: str, rule_version: str, subject: str, scope: Scope) -> str:
    """Return the identifier of one rule's failure to decide on one subject.

    Identified by the rule, its version, the subject and the scope, as a claim is by
    what it says. A rule failing on one subject in two runs over unchanged input is
    one failure seen twice, which is what lets a comparison of the runs say the
    failure persisted rather than that a new one appeared.
    """
    return stable_id(
        IdKind.RULE_FAILURE,
        {
            "rule_id": rule_id,
            "rule_version": rule_version,
            "subject": subject,
            "scope": scope.token,
        },
    )


def finding_id(*, rule_id: str, rule_version: str, subject: str, scope: Scope) -> str:
    """Return the identifier of what one flagging rule raised about one subject.

    Identified as a rule failure is, by the rule, its version, the subject and the scope,
    and never by what was raised. One rule raising one subject in two runs is one finding
    seen twice, even where what drew attention changed, so a comparison of the runs says the
    finding persisted and shows what it raises now.
    """
    return stable_id(
        IdKind.FINDING,
        {
            "rule_id": rule_id,
            "rule_version": rule_version,
            "subject": subject,
            "scope": scope.token,
        },
    )


def candidate_id(
    *,
    source: str,
    target: str,
    primitive: Primitive,
    rule_id: str,
    rule_version: str,
    scope: Scope,
    respect: str | None = None,
) -> str:
    """Return the identifier of a proposed edge.

    Deliberately the same inputs as :func:`edge_id`, under a different kind. A
    candidate that is accepted becomes an assertion about the same pair under the
    same rule, so the two identifiers correspond one to one and the path from
    proposal to assertion is traceable without a join table.
    """
    properties = PRIMITIVE_PROPERTIES[primitive]
    if properties.requires_respect and respect is None:
        message = f"primitive {primitive.value!r} requires a respect"
        raise ValueError(message)
    endpoints = (
        list(canonical_sorted((source, target))) if properties.symmetric else [source, target]
    )
    return stable_id(
        IdKind.CANDIDATE,
        {
            "endpoints": endpoints,
            "primitive": primitive.value,
            "respect": respect,
            "rule_id": rule_id,
            "rule_version": rule_version,
            "scope": scope.token,
        },
    )


def component_id(
    *,
    entry: str,
    component_type: str,
    normalized_value: str,
    normalizer: str,
    normalizer_version: str,
    source_scope: str,
) -> str:
    """Return the identifier of one normalised component.

    The normaliser and its version are part of the identity because a component
    is the *output* of a normalisation. Improving the normaliser produces a
    different component rather than silently changing an existing one, which is
    what keeps a past run replayable.

    Scope is part of the identity too, because the record carries it and a field stored on a
    record but absent from its identity is a collision waiting for its first user. The same
    normalised token drawn from an entry's name and from its content is two facts about two
    different places. Without the scope here they are one identifier carrying two values: either a
    silent collapse or two rows sharing a key, neither of which a reader can detect.
    """
    return stable_id(
        IdKind.COMPONENT,
        {
            "entry": entry,
            "component_type": component_type,
            "normalized_value": normalized_value,
            "normalizer": normalizer,
            "normalizer_version": normalizer_version,
            "source_scope": source_scope,
        },
    )


def name_shape_id(
    *,
    entry: str,
    source_scope: str,
    normalizer: str,
    normalizer_version: str,
) -> str:
    """Return the identifier of one entry's name shape.

    One shape per entry per scope per normaliser, which is what the inputs say.
    The skeleton is deliberately not in the payload: it is a function of
    these inputs, so including it would buy nothing and would make the identifier
    unpredictable from the entry, which is the thing a rule joining shapes to
    components most needs it to be.
    """
    return stable_id(
        IdKind.NAME_SHAPE,
        {
            "entry": entry,
            "source_scope": source_scope,
            "normalizer": normalizer,
            "normalizer_version": normalizer_version,
        },
    )


def dictionary_term_id(*, term: str, scope: Scope, origin: str) -> str:
    """Return the identifier of a Dictionary entry.

    Scope is part of the identity because the same word means different things in
    different places, and a project-level override must not destroy the builtin
    sense it shadows.

    Origin is part of it for the same reason, one level along. A user entry that redefines a
    shipped term is a *second* entry that shadows the first, and the store says so: the builtin
    entry stays and is shadowed. Two entries need two identifiers. Without origin here they
    collide, and a run's vocabulary snapshot then holds two rows under one key on a table that
    declares ``term_id`` as its primary key. Nothing enforces a primary key before a commit, so
    the collision raises no error. Instead the recognised vocabulary view counts every shadowed
    term twice, and those are exactly the terms a user has taken the trouble to correct.
    """
    return stable_id(
        IdKind.DICTIONARY_TERM,
        {"term": term, "scope": scope.token, "origin": origin},
    )


def dictionary_binding_id(
    *,
    component: str,
    scope: Scope,
    resolver: str,
    resolver_version: str,
) -> str:
    """Return the identifier of one Dictionary binding.

    The resolver and its version are part of the identity for the reason the
    normaliser is part of a component's: a binding is the *output* of a
    resolution, so improving the resolver produces a different binding rather
    than silently rewriting an existing one.

    What the Dictionary said is deliberately not in the payload. The term, the sense's origin and
    the vocabulary's digest all sit beside the identifier rather than inside it. Hashing them
    would give every binding in a pool a new identifier the moment one unrelated sense was added
    to one unrelated section, because the Dictionary is versioned by a digest of the whole
    composed vocabulary. Two consequences follow. Run comparison reports which bindings an edit
    changed instead of reporting that every binding was replaced. A decision a person recorded
    against a binding survives a Dictionary edit instead of being orphaned by it.

    The lookup scope is in the payload although it changes nothing today, because every lookup
    currently runs at project scope. When scoped resolution is added, one component resolved
    under two scopes would otherwise collide on one identifier. Adding the scope now costs a
    version bump. Adding it later would cost a migration.
    """
    return stable_id(
        IdKind.BINDING,
        {
            "component": component,
            "scope": scope.token,
            "resolver": resolver,
            "resolver_version": resolver_version,
        },
    )


def record_event_id(
    *,
    actor: str,
    occurred_at: str,
    kind: str,
    target: str,
    sequence: int,
) -> str:
    """Return the identifier of one human decision.

    Unlike every other identity here, a record event includes its time and a
    sequence number, because two identical decisions made at different moments
    are two decisions. The Record is a log, not a set.
    """
    return stable_id(
        IdKind.RECORD_EVENT,
        {
            "actor": actor,
            "occurred_at": occurred_at,
            "kind": kind,
            "target": target,
            "sequence": sequence,
        },
    )


#: The stamp :func:`run_id` leads with: ``run_<YYYYMMDD>T<HHMMSS>...Z_<digest>``.
_RUN_STAMP = re.compile(r"^run_(\d{8})T(\d{6})\d*Z?_")


def run_began_ns(identifier: str) -> int | None:
    """When a run began, in nanoseconds since the epoch, read from its identifier's stamp.

    The identifier leads with the run's start by construction (:func:`run_id`), so a record
    naming the run that observed it carries when it was observed. None where the identifier
    carries no stamp (a test's shorthand, a supplied name), which a reader treats as unknown.
    """
    stamped = _RUN_STAMP.match(identifier or "")
    if stamped is None:
        return None
    import datetime as dt

    try:
        began = dt.datetime.strptime(stamped.group(1) + stamped.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return int(began.replace(tzinfo=dt.UTC).timestamp()) * 1_000_000_000


def run_id(*, started_at: str, nonce: str) -> str:
    """Return the identifier of one run.

    Unique per run, and therefore the one identifier in this module that is not a
    pure function of the data. That is deliberate: two runs over unchanged input
    are two runs, and collapsing them would destroy the time series the Change
    Log and the convergence indicator are built from.

    The reproducibility claim holds in this form. Two identical runs produce identical records
    except for run identity and wall-clock timings. The comparison that checks the claim projects
    the run-identifying and timing columns away. Every other column, including every identifier
    above, must match exactly.

    The identifier is sortable on purpose. It leads with a compact timestamp, so run directories
    list in chronological order and "the two most recent runs" is a lexical question. No manifest
    has to be opened. The random half makes the identifier unique when two runs start within one
    second.

    Both inputs are passed in rather than read from the clock, so a run can be
    reproduced exactly in a test.
    """
    stamp = "".join(character for character in started_at if character.isalnum())
    digest = digest_text(canonical_json({"started_at": started_at, "nonce": nonce}))
    return f"{IdKind.RUN.value}_{stamp}_{digest[:12]}"
