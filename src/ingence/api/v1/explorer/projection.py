"""The Graph workspace's projection of one published run: every file and folder, counted once.

This is a projection, not a store (as with the search projection, ``search/projection.py``).
Everything here is a function of one run's Parquet tables. So it lives beside ``runs/`` rather than
in one, inference never reads it, it is not part of the reproducibility claim, and it is rebuilt
rather than repaired: deleting ``projections/explorer/`` loses nothing but the seconds it takes to
build. What a person decides after the run (a confirmation, a label) is not baked in. The routes
read the Record for it on every request, so an answer shows at once.

Layout, beside ``projections/search/``::

    projections/explorer/<run_id>/
        meta.json        the run, the projection version, the families read, the counts
        files.parquet    one row per live file: its folder chain and its category per measure,
                         its size, and every time the run holds for it with that time's source
        folders.parquet  one row per live folder: its chain, what it holds directly and beneath,
                         per measure and per category, and whether a rule reads it as an
                         experiment's folder
        themes.parquet   one row per (file, theme kind) its own name or a folder's above it
                         carries: the Dictionary's terms of that kind and the spellings written
                         (version 2, for arranging a view by what names say)

The build uses SQL and never a Python loop over rows. Each table is one ``COPY (SELECT ...) TO``
over the catalog's views, so a census of hundreds of thousands of entries never becomes Python
objects. The tables are sorted by root and path, so a folder's subtree is a contiguous range that a
query's row-group statistics can prune to. A build is written whole or not at all: it is staged,
then moved into place with one ``os.replace``, as a run is published, so a request never reads half
a projection.

A failed build removes its staging. A build holds a lock beside its staging, so a later process
removes a staging folder whose builder is gone (its lock is free) or, with no lock, one older than
``ORPHAN_SECONDS``. A build that finds the projection already published by another process keeps
that one and discards its own. Only a folder that a build marked as its own is ever removed
(``STAGING_MARKER``): a real folder, reached through no link, holding the marker that a build
makes in it at once. A project that cannot be written is still explored. Its projection is built
in a temporary folder held for the life of the process, and removed when it ends. Every path
spelled into SQL is quoted (the catalog's ``sql_path``), so a project under a folder whose name
holds an apostrophe reads the same.

The categories are :mod:`~ingence.api.v1.explorer.categories`'s. The SQL below writes their keys
and nothing else, and a test holds the two to one spelling.
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from ingence.api.v1.explorer.categories import (
    EXAM,
    KIND,
    PROV,
    STAGE,
    kind_families,
    stage_families,
)
from ingence.storage.catalog import sql_path
from ingence.storage.connection import connect as duckdb_connection
from ingence.storage.layout import STAGING_DIR, owned_path

if TYPE_CHECKING:
    from ingence.jobs.locks import FileLock
    from ingence.storage.catalog import Catalog

__all__ = [
    "EXPLORER_DIR",
    "PROJECTION_VERSION",
    "STAGING_MARKER",
    "ExplorerProjection",
    "build_projection",
    "connect",
    "is_marked_staging",
    "projection_for",
    "projection_path",
    "read_projection",
    "sql_path",
]

#: Moves when a table's columns or a category's meaning change; an older build is rebuilt.
#: Version 2 (the arrangement): ``themes.parquet``, and each file's nearest experiment unit.
#: Version 3: a format read in conflict is the ``conflicting`` kind, and each file carries every
#: format read (``formats``) and whether they conflict.
#: Version 5: each file carries why its earliest credible time passed over a time it holds
#: (``earliest_not_believed``: a template's date inside a document).
PROJECTION_VERSION = "5"
EXPLORER_DIR = "explorer"
_PROJECTIONS = "projections"

#: The claims each measure reads. Stage and format are read by the value concluded (see
#: ``infer/claims.py``: a consumer matches the values, which are sense names). A claim counts when
#: it is a classification whose value is a sense of that theme kind in the run's Dictionary,
#: whatever its rule is called. A run whose Dictionary holds no sense of the kind is read by rule
#: name. An experiment predicate names its folder.
CLASSIFIES = "classifies.%"
STAGE_PREDICATES = "classifies.%stage%"
FORMAT_PREDICATES = "classifies.format%"
EXPERIMENT_PREDICATES = "classifies.experiment_folder%"
EARLIEST_PREDICATE = "classifies.earliest_credible_time"
NAME_DATE_PREDICATE = "classifies.date_neighbourhood_of_a_file"
#: The flag that proposes experiments as belonging together: its findings are proposals.
PROPOSED_GROUP_RULE = "experiments_may_belong_together"
#: The lattice's questions are proposed groupings too. Under group approvals as a lattice (the
#: default), a set of words that experiments share is raised by ``group_resolution_deviation`` and
#: left to it by the clustering rule. So a count of the clustering rule's findings alone would
#: miss the lattice's questions, and both rules are counted.
PROPOSED_GROUP_RULES = (PROPOSED_GROUP_RULE, "group_resolution_deviation")


#: The zone every explorer connection reads and writes times in. DuckDB otherwise takes the
#: host's zone when a connection opens, and a cast through ``TIMESTAMPTZ`` then answers in the
#: server's wall clock: an earliest credible time moved by the host's offset. Naive timestamps are
#: UTC throughout the explorer, as Python reads them.
CONNECTION_ZONE = "UTC"


def connect() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection for reading a projection, whose zone is UTC whatever the host's.

    Every explorer connection opens here, never with DuckDB's own ``connect`` directly. So a time
    expression means the same instant on every server (a test holds the modules to it), and no
    connection fetches a DuckDB extension from the network. What is not already on the machine is
    not downloaded.

    Where DuckDB's build carries its time-zone extension (ICU), its zone is set to UTC. An
    extension installed on the machine is never loaded (``storage/connection.py``). Where the build
    carries none, a ``TIMESTAMPTZ`` is read in UTC by DuckDB's core already, so the zone is UTC
    either way, and nothing is fetched to make it so.
    """
    connection = duckdb_connection()
    # Raised only where ICU cannot be loaded, and then the core reads every zone as UTC.
    with contextlib.suppress(duckdb.Error):
        connection.execute(f"SET TimeZone = '{CONNECTION_ZONE}'")
    return connection


def _parquet(path: Path) -> str:
    return f"read_parquet({sql_path(path)})"


def projection_path(project: Path, run_id: str) -> Path:
    """Where the explorer projection of one run lives."""
    return project / _PROJECTIONS / EXPLORER_DIR / run_id


@dataclass(frozen=True, slots=True)
class ExplorerProjection:
    path: Path
    run_id: str
    meta: dict[str, Any]

    @property
    def files(self) -> str:
        """The files table, as a DuckDB relation over its Parquet file."""
        return _parquet(self.path / "files.parquet")

    @property
    def folders(self) -> str:
        return _parquet(self.path / "folders.parquet")

    @property
    def themes(self) -> str:
        return _parquet(self.path / "themes.parquet")


def _quoted(values: tuple[str, ...]) -> str:
    """A SQL list of string literals. Every value is a vocabulary term or a category key,
    checked to be plain letters, digits and underscores before it is spelled here."""
    for value in values:
        if not value.replace("_", "").isalnum():
            message = f"{value!r} is not a plain term; refusing to spell it into SQL"
            raise ValueError(message)
    return ", ".join(f"'{value}'" for value in values) or "NULL"


def _case(column: str, families: dict[str, tuple[str, ...]], otherwise: str) -> str:
    whens = " ".join(
        f"WHEN {column} IN ({_quoted(held)}) THEN '{family}'"
        for family, held in families.items()
        if held
    )
    return f"CASE {whens} ELSE {otherwise} END"


def _by_value(name: str, theme_kind: str, by_name: str) -> str:
    """A CTE of the classifications whose value is a ``theme_kind`` sense of the run's
    Dictionary; where it holds none of that kind, those whose rule is named ``by_name``."""
    return f"""{name}_senses AS (
    SELECT DISTINCT term FROM dictionary_entries WHERE theme_kind = '{theme_kind}'
),
{name} AS (
    SELECT subject, object_reference, resolution_status FROM claims
    WHERE predicate LIKE '{CLASSIFIES}' AND (
        object_reference IN (SELECT term FROM {name}_senses)
        OR (NOT EXISTS (SELECT 1 FROM {name}_senses) AND predicate LIKE '{by_name}'))
)"""  # noqa: S608 -- every name here is a constant of this module


def _files_sql() -> str:
    kind = _case("fmt.fmt", kind_families(), "'other'")
    kind_of_value = _case("object_reference", kind_families(), "'other'")
    stage_family = _case("object_reference", stage_families(), "'other'")
    return f"""
WITH RECURSIVE
live AS (
    SELECT entry_id, root_id, parent_entry_id, name, relative_path, depth, entry_type,
           extension, coalesce(size_bytes, 0) AS bytes, scan_outcome, symlink_state,
           symlink_target, modified_ns, created_ns, event_time_ns, event_time_source
    FROM entries WHERE NOT is_deleted AND entry_kind = 'filesystem'
),
dirs AS (SELECT entry_id, parent_entry_id FROM live WHERE entry_type = 'directory'),
chain(entry_id, ancestors) AS (
    SELECT entry_id, []::VARCHAR[] FROM dirs
    WHERE parent_entry_id IS NULL OR parent_entry_id NOT IN (SELECT entry_id FROM dirs)
    UNION ALL
    SELECT d.entry_id, list_append(c.ancestors, c.entry_id)
    FROM dirs d JOIN chain c ON d.parent_entry_id = c.entry_id
),
{_by_value("format_claims", "format", FORMAT_PREDICATES)},
{_by_value("stage_claims", "stage", STAGE_PREDICATES)},
fmt AS (
    -- A format read in conflict is never the settled kind: a claim published as conflicting
    -- (its defeater fired), or two families read for one file.
    -- Conflicting stays apart from a settled kind.
    SELECT subject AS entry_id, min(object_reference) AS fmt,
           list_sort(list(DISTINCT object_reference)) AS formats,
           bool_or(resolution_status = 'examined_conflicting')
               OR count(DISTINCT {kind_of_value}) > 1 AS conflicting
    FROM format_claims GROUP BY subject
),
stages AS (
    SELECT subject AS entry_id, list(DISTINCT {stage_family}) AS families,
           bool_or(resolution_status = 'examined_conflicting') AS conflicted
    FROM stage_claims GROUP BY subject
),
published AS (
    SELECT entry_id, bool_or(primitive = 'derives') AS derives,
           bool_or(primitive = 'references') AS mentions
    FROM (
        SELECT source_id AS entry_id, primitive FROM assertions
        WHERE decision_status = 'asserted' AND primitive IN ('derives', 'references')
        UNION ALL
        SELECT target_id, primitive FROM assertions
        WHERE decision_status = 'asserted' AND primitive IN ('derives', 'references')
    ) GROUP BY entry_id
),
proposed AS (
    SELECT DISTINCT entry_id FROM (
        SELECT c.source_id AS entry_id, c.candidate_id FROM candidates c
        WHERE c.primitive IN ('derives', 'references')
        UNION ALL
        SELECT c.target_id, c.candidate_id FROM candidates c
        WHERE c.primitive IN ('derives', 'references')
    ) p WHERE NOT EXISTS (
        SELECT 1 FROM assertions a
        WHERE a.candidate_id = p.candidate_id AND a.decision_status = 'asserted'
    )
),
earliest AS (
    SELECT subject AS entry_id,
           min(json_extract_string(object_reference, '$.at')) AS earliest_at,
           min(json_extract_string(object_reference, '$.source')) AS earliest_source,
           bool_or(resolution_status = 'examined_conflicting') AS earliest_conflicting,
           min(json_extract_string(object_reference, '$.not_believed[0]'))
               AS earliest_not_believed
    FROM claims WHERE predicate = '{EARLIEST_PREDICATE}' GROUP BY subject
),
named AS (
    -- One named date whole: its from, its to and its source from one claim (the earliest),
    -- never a span made of two dates or a source from a third. How
    -- many dates the name holds is said beside it.
    SELECT subject AS entry_id,
           arg_min(json_extract_string(object_reference, '$.from'), date_key) AS name_from,
           arg_min(json_extract_string(object_reference, '$.to'), date_key) AS name_to,
           arg_min(json_extract_string(object_reference, '$.source'), date_key) AS name_source,
           count(DISTINCT day_span) AS name_dates
    FROM (
        SELECT *,
               coalesce(json_extract_string(object_reference, '$.from'), '') || '/' ||
               coalesce(json_extract_string(object_reference, '$.to'), '') AS day_span,
               coalesce(json_extract_string(object_reference, '$.from'), '') || '/' ||
               object_reference AS date_key
        FROM claims WHERE predicate = '{NAME_DATE_PREDICATE}'
    ) GROUP BY subject
),
units AS (
    SELECT DISTINCT subject AS entry_id FROM claims WHERE predicate LIKE '{EXPERIMENT_PREDICATES}'
),
folder_unit AS (
    -- Each folder's nearest experiment unit: itself, else the closest folder above it that a
    -- rule reads as an experiment's folder. Folders times depth rows, never files.
    SELECT z.folder_id, arg_max(z.above, z.place) AS unit_id
    FROM (
        SELECT entry_id AS folder_id, unnest(list_append(ancestors, entry_id)) AS above,
               generate_subscripts(list_append(ancestors, entry_id), 1) AS place
        FROM chain
    ) z JOIN units u ON u.entry_id = z.above
    GROUP BY z.folder_id
)
SELECT
    f.entry_id, f.root_id, f.parent_entry_id AS folder_id, f.name, f.relative_path, f.depth,
    f.entry_type, lower(coalesce(f.extension, '')) AS extension, f.bytes,
    coalesce(list_append(c.ancestors, c.entry_id), []::VARCHAR[]) AS ancestors,
    f.symlink_state = 'outside_root' AS is_archive_link,
    f.symlink_state, f.symlink_target,
    CASE
        WHEN f.symlink_state = 'outside_root' THEN 'elsewhere'
        WHEN f.scan_outcome IN ('extracted', 'extracted_with_fallback') THEN 'read'
        WHEN f.scan_outcome IN ('skipped_by_policy', 'deferred') THEN 'set_aside'
        WHEN f.scan_outcome = 'skipped_by_settings' THEN 'by_settings'
        WHEN f.scan_outcome IN ('content_error', 'changed_during_read') THEN 'failed'
        WHEN f.scan_outcome = 'metadata_only' THEN 'named'
        ELSE 'unknown'
    END AS exam,
    CASE
        WHEN s.entry_id IS NULL THEN 'none'
        WHEN s.conflicted OR len(s.families) > 1 THEN 'conflicting'
        ELSE s.families[1]
    END AS stage,
    CASE
        WHEN p.derives THEN 'derivation'
        WHEN p.mentions THEN 'reference'
        WHEN q.entry_id IS NOT NULL THEN 'proposed'
        ELSE 'none'
    END AS prov,
    CASE
        WHEN fmt.fmt IS NULL THEN 'unknown'
        WHEN fmt.conflicting THEN 'conflicting'
        ELSE {kind}
    END AS kind,
    fmt.fmt AS format,
    coalesce(fmt.formats, []::VARCHAR[]) AS formats,
    coalesce(fmt.conflicting, false) AS format_conflicting,
    e.earliest_at, e.earliest_source, coalesce(e.earliest_conflicting, false)
        AS earliest_conflicting, e.earliest_not_believed,
    n.name_from, n.name_to, n.name_source, coalesce(n.name_dates, 0) AS name_dates,
    f.modified_ns, f.created_ns, f.event_time_ns, f.event_time_source,
    fu.unit_id
FROM live f
LEFT JOIN chain c ON c.entry_id = f.parent_entry_id
LEFT JOIN folder_unit fu ON fu.folder_id = f.parent_entry_id
LEFT JOIN fmt ON fmt.entry_id = f.entry_id
LEFT JOIN stages s ON s.entry_id = f.entry_id
LEFT JOIN published p ON p.entry_id = f.entry_id
LEFT JOIN proposed q ON q.entry_id = f.entry_id
LEFT JOIN earliest e ON e.entry_id = f.entry_id
LEFT JOIN named n ON n.entry_id = f.entry_id
WHERE f.entry_type <> 'directory'
ORDER BY f.root_id, f.relative_path, f.entry_id
"""  # noqa: S608


def _counts(column: str, keys: tuple[str, ...], prefix: str) -> str:
    return ", ".join(
        f"count(*) FILTER (WHERE {column} = '{key}') AS {prefix}_{key}" for key in keys
    )


def _folders_sql(files: str) -> str:
    measures = ", ".join(
        _counts(measure.key, measure.category_keys(), measure.key)
        for measure in (EXAM, STAGE, PROV, KIND)
    )
    zeroes = ", ".join(
        f"coalesce(b.{measure.key}_{key}, 0) AS {measure.key}_{key}"
        for measure in (EXAM, STAGE, PROV, KIND)
        for key in measure.category_keys()
    )
    return f"""
WITH RECURSIVE
live AS (
    SELECT entry_id, root_id, parent_entry_id, name, relative_path, depth, entry_type
    FROM entries WHERE NOT is_deleted AND entry_kind = 'filesystem'
),
dirs AS (SELECT * FROM live WHERE entry_type = 'directory'),
chain(entry_id, ancestors) AS (
    SELECT entry_id, []::VARCHAR[] FROM dirs
    WHERE parent_entry_id IS NULL OR parent_entry_id NOT IN (SELECT entry_id FROM dirs)
    UNION ALL
    SELECT d.entry_id, list_append(c.ancestors, c.entry_id)
    FROM dirs d JOIN chain c ON d.parent_entry_id = c.entry_id
),
up AS (SELECT unnest(ancestors) AS folder_id, * EXCLUDE (ancestors) FROM {files}),
beneath AS (
    SELECT folder_id, count(*) AS files, sum(bytes) AS bytes, {measures}
    FROM up GROUP BY folder_id
),
direct AS (
    SELECT folder_id, count(*) AS direct_files, sum(bytes) AS direct_bytes
    FROM {files} GROUP BY folder_id
),
folder_up AS (SELECT unnest(ancestors) AS folder_id FROM chain),
folders_beneath AS (SELECT folder_id, count(*) AS folders FROM folder_up GROUP BY folder_id),
child_folders AS (
    SELECT parent_entry_id AS folder_id, count(*) AS direct_folders FROM dirs
    GROUP BY parent_entry_id
),
experiments AS (
    SELECT subject AS entry_id,
           min(claim_id) AS exp_claim_id,
           min(object_reference) AS exp_kind,
           string_agg(DISTINCT rule_id, ',' ORDER BY rule_id) AS exp_rules,
           bool_or(resolution_status = 'examined_conflicting') AS exp_conflicting,
           list(claim_id ORDER BY claim_id) AS exp_claim_ids
    FROM claims WHERE predicate LIKE '{EXPERIMENT_PREDICATES}' GROUP BY subject
),
proposals AS (
    SELECT replace(subject, 'folder:', '') AS entry_id, count(*) AS proposed_groups
    FROM findings WHERE rule_id IN ({", ".join(f"'{rule}'" for rule in PROPOSED_GROUP_RULES)})
    GROUP BY 1
)
SELECT
    d.entry_id, d.root_id, d.parent_entry_id AS parent_id, d.name, d.relative_path, d.depth,
    c.ancestors,
    coalesce(b.files, 0) AS files, coalesce(b.bytes, 0) AS bytes,
    coalesce(t.direct_files, 0) AS direct_files, coalesce(t.direct_bytes, 0) AS direct_bytes,
    coalesce(fb.folders, 0) AS folders, coalesce(cf.direct_folders, 0) AS direct_folders,
    {zeroes},
    x.exp_claim_id, x.exp_kind, x.exp_rules, coalesce(x.exp_conflicting, false)
        AS exp_conflicting, coalesce(x.exp_claim_ids, []::VARCHAR[]) AS exp_claim_ids,
    coalesce(g.proposed_groups, 0) AS proposed_groups
FROM dirs d
JOIN chain c ON c.entry_id = d.entry_id
LEFT JOIN beneath b ON b.folder_id = d.entry_id
LEFT JOIN direct t ON t.folder_id = d.entry_id
LEFT JOIN folders_beneath fb ON fb.folder_id = d.entry_id
LEFT JOIN child_folders cf ON cf.folder_id = d.entry_id
LEFT JOIN experiments x ON x.entry_id = d.entry_id
LEFT JOIN proposals g ON g.entry_id = d.entry_id
ORDER BY d.root_id, d.relative_path, d.entry_id
"""  # noqa: S608


def _themes_sql(files: str) -> str:
    """What each file's name, and the names of the folders above it, say, by theme kind.

    Only names count, and only what the Dictionary resolved: a word bound to a sense of a theme
    kind (``technique``, ``condition``, ``reagent``...) in the census's reading of a name. A
    file inherits what its folders' names say, because a folder named for a technique holds that
    technique's files; ``own`` says whether the file's own name carries it too. A content word
    (from a dive) is never a theme here: it is what a file mentions, not what it is called.
    Themes overlap freely (``core.schema.Theme``): a file may carry two techniques, and the
    arrangement shows the pair as the pair, never one of them.
    """
    return f"""
WITH bound AS (
    SELECT DISTINCT b.entry_id, lower(b.theme_kind) AS theme_kind, b.term,
           lower(coalesce(c.raw_value, c.normalized_value, b.term)) AS spelling
    FROM dictionary_bindings b JOIN components c ON c.component_id = b.component_id
    WHERE b.resolution_status = 'resolved' AND b.theme_kind IS NOT NULL AND b.term IS NOT NULL
      AND coalesce(b.source_scope, 'name') = 'name'
),
carriers AS (
    SELECT entry_id, unnest(list_append(ancestors, entry_id)) AS carrier FROM {files}
)
SELECT c.entry_id, b.theme_kind,
       list(DISTINCT b.term ORDER BY b.term) AS terms,
       list(DISTINCT b.spelling ORDER BY b.spelling) AS spellings,
       bool_or(b.entry_id = c.entry_id) AS own
FROM carriers c JOIN bound b ON b.entry_id = c.carrier
GROUP BY c.entry_id, b.theme_kind
ORDER BY b.theme_kind, c.entry_id
"""  # noqa: S608


def _copy(catalog: Catalog, sql: str, destination: Path) -> None:
    catalog.query(f"COPY ({sql}) TO {sql_path(destination)} (FORMAT PARQUET)")


#: A staging folder with no lock beside it (an older build left it, or its filesystem cannot
#: lock) is an orphan once older than this: a build takes seconds to minutes.
ORPHAN_SECONDS = 6 * 3600.0
_STAGING_PREFIX = "explorer-"
_LOCK_SUFFIX = ".lock"

#: The file a build makes in its staging folder at once, created exclusively and holding the
#: build's identifier. Only a folder holding it is a build's own: a folder of this name a person
#: made, or a link one leads through, is never judged an orphan and never removed, however old.
STAGING_MARKER = ".ingence-staging"


def _mark(staging: Path) -> None:
    """Make the marker in a staging folder just made, refusing one already there."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(staging / STAGING_MARKER, flags, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(uuid.uuid4().hex)


def is_marked_staging(home: Path, staged: Path) -> bool:
    """Whether a folder in ``home``'s ``.tmp`` is a build's own staging: a real folder, nothing on
    the way to it a link (``storage.layout.owned_path``), holding a marker that is a file."""
    try:
        owned_path(home, STAGING_DIR, staged.name)
        folder = os.lstat(staged)
        marker = os.lstat(staged / STAGING_MARKER)
    except OSError:
        return False
    return stat.S_ISDIR(folder.st_mode) and stat.S_ISREG(marker.st_mode)


def _clear_orphans(home: Path, *, older_than: float = ORPHAN_SECONDS) -> list[Path]:
    """Remove the staging folders in ``home``'s ``.tmp`` of builds that are gone: its builder's
    lock is free, or it has no lock and is older than ``older_than`` seconds. A build in progress
    is never touched, and nor is a folder no build marked as its own (:func:`is_marked_staging`)."""
    from ingence.jobs.locks import held_elsewhere

    removed: list[Path] = []
    staging_root = owned_path(home, STAGING_DIR)
    if not staging_root.is_dir():
        return removed
    now = time.time()
    for staged in sorted(staging_root.glob(f"{_STAGING_PREFIX}*")):
        if not is_marked_staging(home, staged):
            continue
        lock = staged.with_name(staged.name + _LOCK_SUFFIX)
        try:
            gone = (
                not held_elsewhere(lock)
                if lock.exists()
                else now - staged.stat().st_mtime > older_than
            )
        except OSError:
            continue  # a filesystem that cannot lock, or a folder already gone: left alone
        if gone:
            shutil.rmtree(staged, ignore_errors=True)
            with contextlib.suppress(OSError):
                lock.unlink(missing_ok=True)
            removed.append(staged)
    return removed


def _writable(home: Path, run_id: str) -> bool:
    """Whether a projection can be staged and published under ``home``: both folders made."""
    try:
        owned_path(home, STAGING_DIR).mkdir(parents=True, exist_ok=True)
        owned_path(home, projection_path(home, run_id).parent).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS) or isinstance(
            error, PermissionError
        ):
            return False
        raise
    return os.access(home / STAGING_DIR, os.W_OK) and os.access(
        projection_path(home, run_id).parent, os.W_OK
    )


def build_projection(catalog: Catalog, *, home: Path | None = None) -> ExplorerProjection:
    """Build the explorer projection of the catalog's run, and publish it whole.

    ``home`` is the folder it is published under (``projections/explorer/<run>`` and its staging
    in ``.tmp``): the project, or a temporary folder where the project cannot be written.
    """
    if catalog.run_directory is None:
        message = "a project with no published run has nothing to explore"
        raise LookupError(message)
    run_id = catalog.run_directory.name
    home = catalog.project if home is None else home
    final = owned_path(home, projection_path(home, run_id))
    staging_root = owned_path(home, STAGING_DIR)
    staging_root.mkdir(parents=True, exist_ok=True)
    _clear_orphans(home)
    staging, lock = _own_staging(staging_root, f"{_STAGING_PREFIX}{run_id}-{os.getpid()}")
    try:
        if staging.exists():
            shutil.rmtree(owned_path(home, staging))
        staging.mkdir(parents=True)
        _mark(staging)
        try:
            meta = _stage(catalog, run_id, staging)
            return _publish(home, staging, final, run_id, meta)
        finally:
            # Failed or discarded, the staging never outlives the build that made it.
            if staging.exists():
                shutil.rmtree(owned_path(home, staging), ignore_errors=True)
    finally:
        lock.release()
        with contextlib.suppress(OSError):
            lock.path.unlink(missing_ok=True)


#: Names a build tries for its staging before it gives up: another build holding each.
_STAGING_TRIES = 64


def _own_staging(staging_root: Path, stem: str) -> tuple[Path, FileLock]:
    """A staging folder this build holds the lock of: ``stem``, else ``stem-2``, ``stem-3``...
    where another build holds it (the same process identifier, another thread or a reused one),
    so no build ever takes another's staging. Where the
    filesystem cannot lock (some network mounts), the first name is used unlocked, and a later
    build judges it by age alone."""
    from ingence.jobs.locks import FileLock

    for attempt in range(1, _STAGING_TRIES + 1):
        staging = staging_root / (stem if attempt == 1 else f"{stem}-{attempt}")
        lock = FileLock(staging.with_name(staging.name + _LOCK_SUFFIX))
        try:
            if lock.acquire():
                return staging, lock
        except OSError:
            with contextlib.suppress(OSError):
                lock.path.unlink(missing_ok=True)
            return staging, lock
    message = f"every staging name for {stem} is held by another build"
    raise RuntimeError(message)


def _stage(catalog: Catalog, run_id: str, staging: Path) -> dict[str, Any]:
    """Write every table and the meta into ``staging``; return the meta."""
    started = time.perf_counter()
    files_file = staging / "files.parquet"
    _copy(catalog, _files_sql(), files_file)
    files = _parquet(files_file)
    _copy(catalog, _folders_sql(files), staging / "folders.parquet")
    _copy(catalog, _themes_sql(files), staging / "themes.parquet")
    counted = catalog.query(
        f"SELECT (SELECT count(*) FROM {files}), "  # noqa: S608
        f"(SELECT count(*) FROM {_parquet(staging / 'folders.parquet')})"
    ).fetchone()
    lines = catalog.query(
        "SELECT "
        "(SELECT count(*) FROM assertions WHERE decision_status = 'asserted' "
        "AND primitive = 'derives'), "
        "(SELECT count(*) FROM assertions WHERE decision_status = 'asserted' "
        "AND primitive = 'references'), "
        "(SELECT count(*) FROM candidates c WHERE c.primitive IN ('derives', 'references') "
        "AND NOT EXISTS (SELECT 1 FROM assertions a WHERE a.candidate_id = c.candidate_id "
        "AND a.decision_status = 'asserted'))"
    ).fetchone()
    meta = {
        "projection_version": PROJECTION_VERSION,
        "run_id": run_id,
        "kinds": {family: list(held) for family, held in kind_families().items()},
        "stages": {family: list(held) for family, held in stage_families().items()},
        "counts": {"files": int(counted[0]), "folders": int(counted[1])} if counted else {},
        # The run's lines of provenance, whole: the Wheel says "none published" from these.
        "provenance": {
            "published_derivations": int(lines[0]) if lines else 0,
            "published_mentions": int(lines[1]) if lines else 0,
            "proposed_pairs": int(lines[2]) if lines else 0,
        },
        "build_seconds": round(time.perf_counter() - started, 3),
    }
    (staging / "meta.json").write_text(json.dumps(meta, sort_keys=True, indent=2), "utf-8")
    return meta


def _at(path: Path, run_id: str) -> ExplorerProjection | None:
    """The projection published at ``path`` where it is at this version, else None."""
    meta_file = path / "meta.json"
    try:
        meta = json.loads(meta_file.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("projection_version") != PROJECTION_VERSION:
        return None
    # Built with other display families (``explorer_kinds.yaml`` edited since, which is read
    # again when it changes): rebuilt, so an edit shows on the next request with no restart.
    if meta.get("kinds") != _families_now("kinds") or meta.get("stages") != _families_now("stages"):
        return None
    return ExplorerProjection(path=path, run_id=run_id, meta=meta)


def _families_now(which: str) -> dict[str, list[str]]:
    held = kind_families() if which == "kinds" else stage_families()
    return {family: list(values) for family, values in held.items()}


def _publish(
    home: Path, staging: Path, final: Path, run_id: str, meta: dict[str, Any]
) -> ExplorerProjection:
    """Move the staging into place, unless another process published this version first: then
    its projection is kept and this one discarded (the same run, the same tables)."""
    final = owned_path(home, final)
    final.parent.mkdir(parents=True, exist_ok=True)
    published = _at(final, run_id)
    if published is not None:
        return published
    if final.exists():
        # An older version, rebuilt: moved aside in one step and removed there, so two processes
        # rebuilding it never trip over each other's removal. Otherwise one process could
        # find the other's files already gone and answer 500. A process that finds it moved
        # already has nothing to move.
        aside = final.with_name(f"{final.name}.old-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        with contextlib.suppress(FileNotFoundError):
            os.replace(final, owned_path(home, aside))
        shutil.rmtree(owned_path(home, aside), ignore_errors=True)
    # The marker says a folder in ``.tmp`` is a build's; a published projection is not in ``.tmp``.
    (staging / STAGING_MARKER).unlink(missing_ok=True)
    try:
        os.replace(owned_path(home, staging), final)
    except OSError:
        # Another process published between the look and the move (POSIX refuses to replace a
        # folder that holds files; Windows refuses any folder that exists).
        published = _at(final, run_id)
        if published is None:
            raise
        return published
    return ExplorerProjection(path=final, run_id=run_id, meta=meta)


#: Projections built in a temporary folder because their project cannot be written, by the path
#: they would have had in the project; each folder is removed when the process ends.
_HELD: dict[str, ExplorerProjection] = {}


def _held_folder() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="ingence-explorer-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return folder


def read_projection(project: Path, run_id: str) -> ExplorerProjection | None:
    """The projection of one run where one is built at this version, else None: in the project,
    or where the project cannot be written, the one this process holds for it."""
    found = _at(projection_path(project, run_id), run_id)
    if found is not None:
        return found
    held = _HELD.get(str(projection_path(project, run_id)))
    # Read like any other: at this version, with the families in force.
    return _at(held.path, run_id) if held is not None else None


#: One build at a time per projection: two requests arriving together wait for one build.
_BUILDING: dict[str, threading.Lock] = {}
_BUILDING_GUARD = threading.Lock()


def projection_for(catalog: Catalog) -> ExplorerProjection:
    """The catalog's run's projection: read where built, built once where not."""
    if catalog.run_directory is None:
        message = "a project with no published run has nothing to explore"
        raise LookupError(message)
    run_id = catalog.run_directory.name
    found = read_projection(catalog.project, run_id)
    if found is not None:
        return found
    key = str(projection_path(catalog.project, run_id))
    with _BUILDING_GUARD:
        lock = _BUILDING.setdefault(key, threading.Lock())
    with lock:
        found = read_projection(catalog.project, run_id)
        if found is not None:
            return found
        if _writable(catalog.project, run_id):
            return build_projection(catalog)
        # A project that cannot be written (a read-only share, a folder set to 555): built in a
        # temporary folder held for this process, so the workspace still answers.
        built = build_projection(catalog, home=_held_folder())
        _HELD[key] = built
        return built
