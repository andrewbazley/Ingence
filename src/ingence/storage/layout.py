"""The storage layout: directory shape, table declarations, and Arrow schemas.

The directory shape and the on-disk column types are part of the contract. Two people could
implement against that contract independently.

Why the schemas are written out and not generated. The Pydantic models are the in-memory contract,
and these schemas are the on-disk one. Deriving one from the other looks tidy and goes wrong
exactly where it matters: nullable platform integers, nanosecond timestamps, and the columns that
hold canonically serialised structures. Writing both and asserting that they agree, field for
field, in a test that runs on every commit, keeps the on-disk format readable and stable
independently of any library version. Drift then fails a test and does not corrupt a column.

The naming rule links them. An Arrow field named ``x`` corresponds to a model field named ``x``. An
Arrow field named ``x_json`` holds field ``x`` serialised canonically, which is how nested
structures and variable-shape values are stored in a columnar file. The agreement test checks the
correspondence in both directions, so a model field with no column and a column with no model field
both fail.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import pyarrow as pa

from ingence.core.schema import (
    Assertion,
    Candidate,
    Claim,
    Component,
    DictionaryBinding,
    DictionaryEntry,
    Entry,
    EquivalenceLevel,
    Finding,
    JournalEvent,
    NameShape,
    Observation,
    QualityFigure,
    Record,
    RecordEvent,
    Role,
    Root,
    Rule,
    RuleFailure,
    RunRecord,
    ScanError,
    Theme,
    ThemeKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "CURRENT_POINTER",
    "DICTIONARY_DIR",
    "JSON_SUFFIX",
    "LISTINGS_DIR",
    "MANIFEST_FILE",
    "OWNED_SUBFOLDERS",
    "PROJECT_FILE",
    "PROJECT_FOLDERS",
    "PROJECT_NAMES",
    "RECORD_DIR",
    "RULEBOOK_DIR",
    "RUNS_DIR",
    "STAGING_DIR",
    "TABLES",
    "ProjectLayoutError",
    "TableSpec",
    "check_layout",
    "integer_range_violations",
    "is_project_name",
    "owned_path",
    "spelled_otherwise",
    "table_for",
]

# ---------------------------------------------------------------------------
# Directory shape.
#
# A project directory looks like this. Everything inside runs/ is immutable once
# committed. The only mutable thing in the whole layout is the pointer, and it is
# replaced atomically rather than edited.
#
#   <project>/
#     project.json              identity of the project, written once
#     CURRENT                   pointer: which run readers resolve through
#     runs/<run_id>/
#       manifest.json           run record, file list with hashes, invariant results
#       roots/ entries/ observations/ components/ claims/ candidates/
#       assertions/ rules/ dictionary_entries/ record_events/ themes/
#       theme_kinds/ equivalence_levels/ scan_errors/ run/
#     rulebook/                 00-base.yaml and the numbered revisions
#     dictionary/               builtin sections and local additions
#     record/                   the append-only decision log, which spans runs
#     .tmp/<run_id>.partial/    where a run is written before it is renamed in
# ---------------------------------------------------------------------------

#: Written once when a project is created. Its presence is what makes a
#: directory a project.
PROJECT_FILE = "project.json"

#: The one mutable file in the layout. Holds the run identifier readers resolve
#: through, and the hash of that run's manifest so a truncated pointer is
#: detectable rather than merely wrong.
CURRENT_POINTER = "CURRENT"

RUNS_DIR = "runs"
#: Where a run is assembled. Invisible to readers, because nothing outside
#: ``runs/`` is ever resolved through the pointer.
STAGING_DIR = ".tmp"
MANIFEST_FILE = "manifest.json"

#: These three span runs rather than belonging to one, because they are inputs to
#: a run rather than outputs of it. The Rulebook directory holds the project's own
#: revisions, numbered in a sequence of their own and composed after the shipped
#: Rulebook.
RULEBOOK_DIR = "rulebook"
DICTIONARY_DIR = "dictionary"
RECORD_DIR = "record"
#: Where a listing root's listing is cached, one folder per archive and identifier. The command
#: ``index --fetch`` fetches a listing once, and every ``index`` after it reads the listing offline.
LISTINGS_DIR = "listings"

#: Every folder a project writes at its own top level. ``Project.create`` makes all of them at
#: once. Each is the project's before a person who shares the folder can make one of its name,
#: which the engine would otherwise write into among their files.
PROJECT_FOLDERS = frozenset(
    {
        RUNS_DIR,
        STAGING_DIR,
        RULEBOOK_DIR,
        DICTIONARY_DIR,
        RECORD_DIR,
        LISTINGS_DIR,
        "jobs",
        "workspace",
        "projections",
        # Each answers file that a person imports, kept as it came.
        "imports",
    }
)

#: Every name a project writes at its own top level. A census leaves these names out of the
#: project it is writing, where the project's folder is also a root or holds a person's files
#: beside its own. ``Project.create`` never writes over them. The names are written out and not
#: imported, because the writers live all over the engine. A test finds each writer in the code
#: and holds it to this list.
PROJECT_NAMES = PROJECT_FOLDERS | frozenset(
    {
        PROJECT_FILE,
        "settings.yaml",
        "settings.dashboard.yaml",
        CURRENT_POINTER,
        # Older engines wrote this one (a returned file of suggestions, removed with the
        # suggestion layer). The engine does not make it, but it is still a project's own where an
        # older project holds it, so its files never read as a person's beside the project.
        "advice",
        # The file that an older engine's case probe wrote in a project and removed at once. The
        # engine does not make it, but it is still a project's own where one was left behind.
        ".ingence-case-probe",
        ".INGENCE-CASE-PROBE",
    }
)

#: A file at the top is replaced by writing it beside itself under a temporary name and moving it
#: in: ``.<name>.<process>.<eight hex digits>.incoming`` (``core.destinations.create_exclusive``).
#: Earlier engines used ``.<name>.incoming``.
_INCOMING = ".incoming"
_RANDOM_INCOMING = re.compile(r"\.(?P<name>.+)\.\d+\.[0-9a-f]{8}\.incoming")


def is_project_name(name: str) -> bool:
    """Whether a name at a project's top level is one the project writes, a temporary name of one
    included: a crashed write's leftover is the project's, never a person's file to index."""
    if name in PROJECT_NAMES:
        return True
    if not (name.startswith(".") and name.endswith(_INCOMING)):
        return False
    if name[1 : -len(_INCOMING)] in PROJECT_NAMES:
        return True
    found = _RANDOM_INCOMING.fullmatch(name)
    return found is not None and found["name"] in PROJECT_NAMES


#: Each name a project writes, by its spelling with case folded.
_FOLDED = {name.casefold(): name for name in PROJECT_NAMES}


def spelled_otherwise(folder: Path, name: str) -> str | None:
    """The project's name that a listed name reaches in another spelling of case, or None.

    Only where the disk ignores case (macOS's and Windows' by default) do ``Record`` and
    ``record`` reach one entry; the disk is asked by reading both, never by writing.
    """
    ours = _FOLDED.get(name.casefold())
    if ours is None or ours == name:
        return None
    try:
        listed, spelled = os.lstat(folder / name), os.lstat(folder / ours)
    except OSError:
        return None
    if (listed.st_dev, listed.st_ino) != (spelled.st_dev, spelled.st_ino):
        return None
    return ours


#: The folders inside the top-level ones that the engine deletes, moves or replaces in. Each is
#: checked by :func:`check_layout` as the top-level folders are. ``projections/exports`` is left
#: out: a person names files there, and ``core/destinations.py`` has its own rule for it.
OWNED_SUBFOLDERS = (
    "projections/search",
    "projections/explorer",
    "projections/themes",
    "jobs/logs",
    "workspace/drafts",
    "workspace/plans",
    "dictionary/packs",
    "dictionary/removed-packs",
    "rulebook/retired",
)


class ProjectLayoutError(OSError):
    """A folder or file the engine writes inside a project is a link, or leads out of the project:
    nothing is written, moved or deleted through it."""


def owned_path(project: Path, *parts: str | os.PathLike[str]) -> Path:
    """A folder or file of the project's own, once nothing on the way to it is a link.

    The engine trusts its internal folders by where they are, never by their spelling. A folder
    the engine deletes in, moves into or replaces (``.tmp``, ``projections/search`` and the rest)
    that is a link would take every delete into the folder it leads to. ``shutil.rmtree`` refuses
    only a link given as its own argument, never one higher up the path. So the walk starts at the
    project's real path and looks at each part on the way down with ``os.lstat``, which never
    follows a link. A part that is a link or a junction is refused (:class:`ProjectLayoutError`).
    As a backstop, the real path of the whole must lie inside the project's. A part that does not
    exist yet is fine: the engine will make it a real folder.

    ``parts`` are names relative to the project (``".tmp", "search-<run>"``), or one whole ``Path``
    inside it, spelled as the caller spells the project (absolute or not). The function returns the
    path as the caller spells the project, so what the path names and says is what the caller
    named.
    """
    root = Path(os.path.realpath(project))
    if len(parts) == 1 and isinstance(parts[0], PurePath):
        relative = _relative_inside(parts[0], project, root)
    else:
        relative = Path(*parts)
        if relative.is_absolute():
            relative = _relative_inside(relative, project, root)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            status = os.lstat(current)
        except (FileNotFoundError, NotADirectoryError):
            break
        if stat.S_ISLNK(status.st_mode) or os.path.isjunction(current):
            raise ProjectLayoutError(_linked(root, current))
    target = root / relative
    real = Path(os.path.realpath(target))
    if real != root and root not in real.parents:
        message = (
            f"{target} leads to {real}, outside the project at {root}. Ingence writes, moves and "
            "deletes only inside the project, so nothing was changed."
        )
        raise ProjectLayoutError(message)
    return Path(project) / relative


def _relative_inside(path: PurePath, project: Path, root: Path) -> Path:
    """A whole path as the parts beneath the project: lexically against the project as spelled,
    then as an absolute path, then against its real path."""
    for whole, base in (
        (path, Path(project)),
        (Path(os.path.abspath(path)), Path(os.path.abspath(project))),
        (Path(os.path.abspath(path)), root),
    ):
        try:
            return Path(whole.relative_to(base))
        except ValueError:
            continue
    message = (
        f"{path} is not inside the project at {root}. Ingence writes, moves and deletes only "
        "inside the project, so nothing was changed."
    )
    raise ProjectLayoutError(message)


def _linked(root: Path, part: Path) -> str:
    kind = "a junction" if os.path.isjunction(part) else "a link"
    return (
        f"{part.relative_to(root).as_posix()} in the project at {root} is {kind} that leads to "
        f"{os.path.realpath(part)}. Ingence's internal folders must be real folders: nothing is "
        "written, moved or deleted through a link, so nothing was changed. To keep a project on "
        "another disk, move the whole project folder there, and remove the link."
    )


def check_layout(project: Path) -> None:
    """Refuse a project whose own folders are not real folders inside it (:func:`owned_path`).

    Every top-level folder (:data:`PROJECT_FOLDERS`) and every folder the engine deletes, moves or
    replaces in beneath one (:data:`OWNED_SUBFOLDERS`) is checked, before anything is written: a
    folder that is not there yet is fine.
    """
    for name in (*sorted(PROJECT_FOLDERS), *OWNED_SUBFOLDERS):
        owned_path(project, name)


#: Suffix marking a column that holds a canonically serialised structure.
JSON_SUFFIX = "_json"


@dataclass(frozen=True, slots=True)
class TableSpec:
    """One table: its directory, its model, its columns, and how it is ordered."""

    name: str
    #: Directory under a run, or under the project for the tables that span runs.
    directory: str
    model: type[Record]
    schema: pa.Schema
    #: Columns a reader may rely on for uniqueness. Checked before commit.
    primary_key: tuple[str, ...]
    #: Canonical sort applied before writing. Determinism by construction: a
    #: fragment written from an unsorted iterable is a different file on every
    #: run even when the rows are identical.
    sort_by: tuple[str, ...]
    #: True for tables that belong to the project rather than to one run.
    spans_runs: bool = False


def _string(name: str, *, nullable: bool = True) -> pa.Field:
    return pa.field(name, pa.string(), nullable=nullable)


def _int(name: str, *, nullable: bool = True) -> pa.Field:
    return pa.field(name, pa.int64(), nullable=nullable)


def _unsigned(name: str, *, nullable: bool = True) -> pa.Field:
    """An unsigned 64-bit integer, for a fact the operating system hands over unsigned.

    Use it only where a signed column would refuse a real value. An inode is the case: POSIX
    makes ``ino_t`` unsigned, and macOS's ExFAT driver gives every empty file an inode
    whose top 32 bits are all set. A signed column would refuse the first empty file on
    such a volume, and the census could not publish at all.
    """
    return pa.field(name, pa.uint64(), nullable=nullable)


def _real(name: str, *, nullable: bool = True) -> pa.Field:
    return pa.field(name, pa.float64(), nullable=nullable)


def _bool(name: str, *, nullable: bool = True) -> pa.Field:
    return pa.field(name, pa.bool_(), nullable=nullable)


def _strings(name: str) -> pa.Field:
    """A list of strings. Order is canonical, so the column is comparable."""
    return pa.field(name, pa.list_(pa.string()), nullable=False)


def _json(name: str) -> pa.Field:
    """A canonically serialised structure, stored as a string.

    Arrow can hold nested structs and this deliberately does not use them.
    Nested columns commit the on-disk format to the exact shape of a model, so
    adding an optional field to a nested structure becomes a file-format change.
    A canonically serialised string keeps the structure readable, comparable and
    hashable, and the schema version on every row says how to interpret it.
    """
    return pa.field(f"{name}{JSON_SUFFIX}", pa.string(), nullable=False)


def integer_range_violations(record: Record, spec: TableSpec) -> list[tuple[str, int]]:
    """Return every integer a record holds that its table's column cannot store.

    The check reads the record's own fields against the column types, so it is the check a flush
    would make, made before the flush. Without it, one value out of range would stop a census at its
    first flush, and the census would publish nothing. An ExFAT volume's empty file, whose synthetic
    inode lies above 2**63, is such a value for a signed column, which is why the inode column is
    unsigned. This check covers every integer column, so the next value that a filesystem hands over
    out of range is a recorded fact about one entry and not a lost run.
    """
    found: list[tuple[str, int]] = []
    for column in spec.schema:
        if not pa.types.is_integer(column.type):
            continue
        value = getattr(record, column.name, None)
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        width = column.type.bit_width
        if pa.types.is_signed_integer(column.type):
            low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
        else:
            low, high = 0, (1 << width) - 1
        if not low <= value <= high:
            found.append((column.name, value))
    return found


# ---------------------------------------------------------------------------
# The tables.
# ---------------------------------------------------------------------------

_ROOTS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("root_id", nullable=False),
        _string("root_uri", nullable=False),
        _string("mount_path", nullable=False),
        _string("case_sensitivity", nullable=False),
        _string("case_established_by"),
        _string("label"),
        _string("first_seen_run", nullable=False),
        _string("last_seen_run", nullable=False),
    ]
)

_ENTRIES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("entry_id", nullable=False),
        _string("entry_kind", nullable=False),
        _string("root_id", nullable=False),
        _string("relative_path", nullable=False),
        _string("name", nullable=False),
        _string("parent_entry_id"),
        _int("depth", nullable=False),
        _string("entry_type", nullable=False),
        _string("extension", nullable=False),
        _string("compound_extension", nullable=False),
        # Every one of these is nullable because none of them exists everywhere.
        # A schema that requires an inode is a schema that lies on some machine.
        _int("size_bytes"),
        _int("allocated_bytes"),
        # Unsigned, because the operating system's own type is (see `_unsigned`).
        _unsigned("inode"),
        _int("device"),
        _int("mode"),
        _int("uid"),
        _int("gid"),
        _int("nlink"),
        # Nanoseconds as int64 rather than a timestamp type, so the value that
        # was read is the value that is stored and no library reinterprets it.
        _int("created_ns"),
        _string("created_time_source", nullable=False),
        _int("modified_ns"),
        _int("changed_ns"),
        _int("event_time_ns"),
        _string("event_time_source", nullable=False),
        _string("event_time_tier", nullable=False),
        _string("symlink_target"),
        _string("symlink_state", nullable=False),
        _json("locator_chain"),
        _string("transformation"),
        _string("transformation_version"),
        _int("ordinal"),
        _string("derived_content_hash"),
        _string("resolution_status", nullable=False),
        _bool("is_deleted", nullable=False),
        _string("first_seen_run", nullable=False),
        _string("last_seen_run", nullable=False),
        _string("stat_fingerprint"),
        _string("processing_fingerprint"),
        _string("scan_outcome", nullable=False),
        # Added in schema 1.22: the depth a file's content was read at, and a file's move between
        # two censuses (the entry it came from and how that was known). All are nullable, so a
        # fragment written before 1.22 compacts with each as null.
        _string("extraction_profile"),
        _string("moved_from"),
        _string("moved_by"),
        # Added in schema 1.22: when a move happened, at the latest.
        _int("moved_at_ns"),
    ]
)

_OBSERVATIONS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("observation_id", nullable=False),
        _string("entry_id", nullable=False),
        _string("run_id", nullable=False),
        _string("observer", nullable=False),
        _string("observer_version", nullable=False),
        _string("observed_at", nullable=False),
        _string("key", nullable=False),
        _string("value_kind", nullable=False),
        _string("value_text"),
        _int("value_integer"),
        _real("value_real"),
        _bool("value_boolean"),
        _string("value_structure"),
        _string("extraction_tier", nullable=False),
        _string("source_scope", nullable=False),
        _json("locator_chain"),
        _string("identity_method"),
        _string("identity_method_version"),
        _string("identity_scope"),
    ]
)

_COMPONENTS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("component_id", nullable=False),
        _string("entry_id", nullable=False),
        _string("run_id", nullable=False),
        _string("component_type", nullable=False),
        _string("normalized_value", nullable=False),
        _string("raw_value"),
        _string("source_scope", nullable=False),
        _string("normalizer", nullable=False),
        _string("normalizer_version", nullable=False),
        _string("extraction_tier", nullable=False),
        _real("magnitude"),
        _string("dimension"),
        _string("unit"),
        _int("occurrences", nullable=False),
        _strings("observations"),
    ]
)

_NAME_SHAPES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("name_shape_id", nullable=False),
        _string("entry_id", nullable=False),
        _string("run_id", nullable=False),
        _string("source_scope", nullable=False),
        _string("normalizer", nullable=False),
        _string("normalizer_version", nullable=False),
        _string("skeleton", nullable=False),
        _string("skeleton_hash", nullable=False),
        _int("slot_count", nullable=False),
        _json("slots"),
    ]
)

_DICTIONARY_BINDINGS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("binding_id", nullable=False),
        _string("component_id", nullable=False),
        _string("entry_id", nullable=False),
        _string("run_id", nullable=False),
        _string("source_scope", nullable=False),
        _string("resolver", nullable=False),
        _string("resolver_version", nullable=False),
        _string("method", nullable=False),
        _json("lookup_scope"),
        _string("constraint"),
        _string("extraction_tier", nullable=False),
        _string("resolution_status", nullable=False),
        _string("term_id"),
        _string("term"),
        _string("theme_kind"),
        _string("quality"),
        _string("sense_origin"),
        _int("sense_origin_rank"),
        _json("sense_scope"),
        _int("sense_scope_rank"),
        _string("sense_status"),
        _string("sense_digest"),
        _int("alternative_count", nullable=False),
        _json("alternatives"),
    ]
)

_CLAIMS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("claim_id", nullable=False),
        _string("run_id", nullable=False),
        _string("subject", nullable=False),
        _string("predicate", nullable=False),
        _string("object_reference"),
        _string("inference_tier", nullable=False),
        _string("rule_id", nullable=False),
        _string("rule_version", nullable=False),
        _json("scope"),
        _strings("inputs"),
        _json("support"),
        _json("defeaters"),
        _json("salience"),
        _string("resolution_status", nullable=False),
    ]
)

_CANDIDATES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("candidate_id", nullable=False),
        _string("run_id", nullable=False),
        _string("source_id", nullable=False),
        _string("target_id", nullable=False),
        _string("primitive", nullable=False),
        _string("respect"),
        _string("specialization"),
        _string("rule_id", nullable=False),
        _string("rule_version", nullable=False),
        _json("scope"),
        _string("level", nullable=False),
        _string("decision_tier", nullable=False),
        _json("support"),
        _json("defeaters"),
        _json("salience"),
        _real("tiebreak", nullable=False),
    ]
)

_ASSERTIONS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("edge_id", nullable=False),
        _string("run_id", nullable=False),
        _string("source_id", nullable=False),
        _string("target_id", nullable=False),
        _string("primitive", nullable=False),
        _string("respect"),
        _string("specialization"),
        _string("rule_id", nullable=False),
        _string("rule_version", nullable=False),
        _json("scope"),
        _string("level", nullable=False),
        _string("quotient_origin", nullable=False),
        _string("inference_tier", nullable=False),
        _string("decision_status", nullable=False),
        _string("candidate_id"),
        _json("support"),
        _json("defeaters"),
        _string("expansion_state"),
        _string("reference_resolution"),
        _string("allen_relation"),
        _string("clock"),
        _string("activity_id"),
        _real("weight"),
    ]
)

_RULES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("rule_id", nullable=False),
        _string("version", nullable=False),
        _string("primitive"),
        _string("specialization"),
        _string("respect"),
        _string("statement", nullable=False),
        _json("parameters"),
        _json("scope"),
        _string("level", nullable=False),
        _string("predicate", nullable=False),
        _string("predicate_version", nullable=False),
        _string("effect", nullable=False),
        _string("tier", nullable=False),
        _strings("defeated_by"),
        _string("candidates_within"),
        _bool("enabled", nullable=False),
        _string("status", nullable=False),
        _string("rationale", nullable=False),
        _json("composition"),
        _string("origin", nullable=False),
        _int("added_revision", nullable=False),
        _int("superseded_revision"),
    ]
)

_DICTIONARY_ENTRIES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("term_id", nullable=False),
        _string("term", nullable=False),
        _strings("surfaces"),
        _string("theme_kind", nullable=False),
        _string("canonical", nullable=False),
        _json("scope"),
        _string("origin", nullable=False),
        _string("status", nullable=False),
        _string("evidence", nullable=False),
        _int("added_revision", nullable=False),
        _strings("ontology_ids"),
        _string("sense_digest", nullable=False),
        _string("section", nullable=False),
        _string("domain"),
    ]
)

_RECORD_EVENTS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("event_id", nullable=False),
        _int("sequence", nullable=False),
        _string("kind", nullable=False),
        _string("actor", nullable=False),
        _string("occurred_at", nullable=False),
        _json("scope"),
        _string("target", nullable=False),
        _string("against_run_id", nullable=False),
        _string("payload", nullable=False),
        _bool("orphaned", nullable=False),
    ]
)

_THEMES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("theme_id", nullable=False),
        _string("theme_kind", nullable=False),
        _string("value", nullable=False),
        _string("label"),
        _int("member_count", nullable=False),
        _string("run_id", nullable=False),
    ]
)

_THEME_KINDS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("name", nullable=False),
        _string("definition", nullable=False),
        _string("origin", nullable=False),
        _bool("enabled", nullable=False),
    ]
)

#: Same five columns as the theme kinds, and a separate table on purpose. An
#: axis and a relationship are different things, and one table with a
#: discriminator would invite exactly the conflation the two records exist to
#: keep apart.
_ROLES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("name", nullable=False),
        _string("definition", nullable=False),
        _string("origin", nullable=False),
        _bool("enabled", nullable=False),
    ]
)

_EQUIVALENCE_LEVELS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("name", nullable=False),
        _string("respect", nullable=False),
        _string("definition", nullable=False),
        _int("granularity", nullable=False),
        _int("default_temporal_window_seconds"),
        _string("origin", nullable=False),
        _bool("enabled", nullable=False),
    ]
)

_SCAN_ERRORS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("entry_id"),
        _string("relative_path", nullable=False),
        _string("operation", nullable=False),
        _string("error_type", nullable=False),
        _string("message", nullable=False),
        _string("run_id", nullable=False),
        _string("failure_class", nullable=False),
    ]
)
_RULE_FAILURES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("failure_id", nullable=False),
        _string("run_id", nullable=False),
        _string("rule_id", nullable=False),
        _string("rule_version", nullable=False),
        _json("scope"),
        _string("subject", nullable=False),
        _string("failure_class", nullable=False),
        _strings("inputs"),
        _string("reason", nullable=False),
    ]
)

_FINDINGS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("finding_id", nullable=False),
        _string("run_id", nullable=False),
        _string("rule_id", nullable=False),
        _string("rule_version", nullable=False),
        _json("scope"),
        _string("subject", nullable=False),
        _strings("raised"),
        _string("subject_kind", nullable=False),
        _strings("inputs"),
        _string("reason", nullable=False),
        _json("defeaters"),
    ]
)

_JOURNAL_EVENTS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("journal_id", nullable=False),
        _string("run_id", nullable=False),
        _int("sequence", nullable=False),
        _string("occurred_at", nullable=False),
        _int("elapsed_ns", nullable=False),
        _string("stage", nullable=False),
        _string("event", nullable=False),
        _string("worker", nullable=False),
        _string("entry_id"),
        _string("relative_path"),
        _int("duration_ns"),
        _string("detail", nullable=False),
    ]
)

_QUALITY_FIGURES = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("run_id", nullable=False),
        _string("phase", nullable=False),
        _string("figure", nullable=False),
        _string("facet", nullable=False),
        _string("state", nullable=False),
        _int("count"),
        _int("total"),
        _int("duration_ns"),
        _int("peak_bytes"),
        _string("note", nullable=False),
    ]
)

_RUN_RECORDS = pa.schema(
    [
        _string("schema_version", nullable=False),
        _string("run_id", nullable=False),
        _string("parent_run_id"),
        _string("started_at", nullable=False),
        _string("ended_at"),
        _string("engine_version", nullable=False),
        _string("id_scheme_version", nullable=False),
        _string("profile", nullable=False),
        _real("deepening_budget_granted_seconds"),
        _real("deepening_budget_spent_seconds"),
        # The content pass's clocks and its worker count.
        _real("wall_seconds"),
        _real("work_seconds"),
        _int("workers"),
        # The base and the per-gigabyte rate of one file's reading ceiling, as a pair
        # and not two columns, because a base without its rate is not a policy.
        _json("extraction_ceiling_seconds"),
        _string("rulebook_hash"),
        _string("dictionary_hash"),
        _string("config_hash"),
        _json("components"),
        _json("seeds"),
        _json("library_versions"),
        _json("platform"),
        _json("counts"),
        _json("reuse_decisions"),
        _json("recognised"),
        _json("excluded"),
        _json("mentions"),
        _json("packs"),
        _json("resolutions"),
        _json("rebind_spellings"),
        # Schema 1.22: provenance and the Record's reach. Nullable, so a run written before
        # 1.22 reads each as null.
        _string("engine_commit"),
        _int("record_watermark"),
        _string("job_id"),
        _string("hash_mode"),
        _string("hash_mode_set_by"),
        _json("date_years"),
        # Added in schema 1.22, compatibly: the case reading that the names were read with, so a
        # partial rebind can refuse across a change to it.
        _json("case_reading"),
        _json("warnings"),
        # Added in schema 1.22, compatibly: whether an inference streamed, and why.
        _bool("streamed"),
        _string("streaming_reason"),
        _json("invariants"),
        _bool("published", nullable=False),
        # Added in schema 1.22, compatibly: who chose whether a dive hashes across a network
        # share. Nullable, so a run written before it reads each as null.
        _bool("skip_remote_filesystems"),
        _string("skip_remote_filesystems_set_by"),
        # Added in schema 1.22, compatibly: whether the pass counted warnings.
        _bool("warnings_tallied"),
        # Added in schema 1.22, compatibly: the name reader that a census or a rebind read names
        # with. Nullable, so a run written before it reads as null.
        _string("normalizer_version"),
    ]
)


TABLES: tuple[TableSpec, ...] = (
    TableSpec("roots", "roots", Root, _ROOTS, ("root_id",), ("root_id",)),
    TableSpec("entries", "entries", Entry, _ENTRIES, ("entry_id",), ("entry_id",)),
    TableSpec(
        "observations",
        "observations",
        Observation,
        _OBSERVATIONS,
        ("observation_id",),
        ("entry_id", "observation_id"),
    ),
    TableSpec(
        "components",
        "components",
        Component,
        _COMPONENTS,
        ("component_id",),
        ("entry_id", "component_id"),
    ),
    TableSpec(
        "name_shapes",
        "name_shapes",
        NameShape,
        _NAME_SHAPES,
        ("name_shape_id",),
        ("entry_id", "name_shape_id"),
    ),
    TableSpec(
        "dictionary_bindings",
        "dictionary_bindings",
        DictionaryBinding,
        _DICTIONARY_BINDINGS,
        ("binding_id",),
        ("entry_id", "binding_id"),
    ),
    TableSpec("claims", "claims", Claim, _CLAIMS, ("claim_id",), ("claim_id",)),
    TableSpec(
        "candidates", "candidates", Candidate, _CANDIDATES, ("candidate_id",), ("candidate_id",)
    ),
    TableSpec("assertions", "assertions", Assertion, _ASSERTIONS, ("edge_id",), ("edge_id",)),
    TableSpec("rules", "rules", Rule, _RULES, ("rule_id", "version"), ("rule_id", "version")),
    TableSpec(
        "dictionary_entries",
        "dictionary_entries",
        DictionaryEntry,
        _DICTIONARY_ENTRIES,
        ("term_id",),
        ("term_id",),
    ),
    # The Record spans runs: a decision is made against a run but outlives it,
    # and is replayed over every run that follows.
    TableSpec(
        "record_events",
        RECORD_DIR,
        RecordEvent,
        _RECORD_EVENTS,
        ("event_id",),
        ("sequence", "event_id"),
        spans_runs=True,
    ),
    TableSpec("themes", "themes", Theme, _THEMES, ("theme_id",), ("theme_id",)),
    TableSpec("theme_kinds", "theme_kinds", ThemeKind, _THEME_KINDS, ("name",), ("name",)),
    TableSpec("roles", "roles", Role, _ROLES, ("name",), ("name",)),
    TableSpec(
        "equivalence_levels",
        "equivalence_levels",
        EquivalenceLevel,
        _EQUIVALENCE_LEVELS,
        ("name",),
        ("granularity", "name"),
    ),
    # Scan errors have no natural key: one entry can fail several operations, and
    # two runs can fail the same one. They are ordered rather than keyed.
    TableSpec(
        "scan_errors", "scan_errors", ScanError, _SCAN_ERRORS, (), ("relative_path", "operation")
    ),
    # A rule unable to decide, recorded against the rule rather than against the
    # file: the third failure class, which a scan error refuses to carry.
    TableSpec(
        "rule_failures",
        "rule_failures",
        RuleFailure,
        _RULE_FAILURES,
        ("failure_id",),
        ("failure_id",),
    ),
    # What a flagging rule raised for a person's attention: never a claim, and rewritten by
    # every inference.
    TableSpec(
        "findings",
        "findings",
        Finding,
        _FINDINGS,
        ("finding_id",),
        ("finding_id",),
    ),
    # The development build's activity log. One module and one line to delete
    # when the stable release is produced by subtraction.
    TableSpec(
        "journal_events",
        "journal",
        JournalEvent,
        _JOURNAL_EVENTS,
        ("journal_id",),
        ("sequence",),
    ),
    # Added in schema 1.22, compatibly: the quality figures of the run, one row per figure and
    # breakdown. Absent from a run an older engine wrote, which reads as no rows.
    TableSpec(
        "quality_figures",
        "quality_figures",
        QualityFigure,
        _QUALITY_FIGURES,
        ("figure", "facet"),
        ("figure", "facet"),
    ),
    TableSpec("run", "run", RunRecord, _RUN_RECORDS, ("run_id",), ("run_id",)),
)


_BY_MODEL: Mapping[type[Record], TableSpec] = {spec.model: spec for spec in TABLES}


def table_for(model: type[Record]) -> TableSpec:
    """Return the table declaration for a record type."""
    return _BY_MODEL[model]
