"""DuckDB views over a committed run: how everything else reads the Index.

Every read path in the engine goes through a catalog, and a catalog resolves through the current
pointer. Code that opened a run directory it found by listing would bypass the write protocol, and
could see a run that was superseded halfway through its own query.

Why DuckDB and not a graph store. The engine performs two operations on its structure: selecting a
subset of relation types and grouping by an equivalence. Those are a ``WHERE`` and a ``GROUP BY``
over a join. Columnar storage handles millions of edges on a laptop. DuckDB is embedded, so there
is no server, and it reads the Parquet fragments in place with no import step. Path and
reachability queries are the exception. They belong to a graph backend constructed on demand from a
scoped result, never from the whole project.

Views, not tables. Nothing is copied into DuckDB. A view over ``read_parquet`` means opening a
catalog is instant regardless of project size, and closing it leaves nothing behind.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import duckdb

from ingence.core.ids import canonical_json
from ingence.core.schema import Assertion, Entry, ExcludedNames, RecognisedPath, ScanOutcome
from ingence.identity import digest_methods
from ingence.scan.reuse import PreviousRecord
from ingence.storage.commit import resolve_current_run, run_path
from ingence.storage.connection import connect as duckdb_connection
from ingence.storage.index_store import record_from_row
from ingence.storage.layout import JSON_SUFFIX, RECORD_DIR, TABLES, TableSpec

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from pathlib import Path

__all__ = [
    "BATCH_ROWS",
    "VERSION_STAMP",
    "VOLATILE_COLUMNS",
    "VOLATILE_TABLES",
    "Catalog",
    "census_facts",
    "compare_runs",
    "declared_default",
    "describe_differences",
    "open_catalog",
    "open_staged",
    "parquet_source",
    "sql_path",
    "sql_path_list",
]


#: The rows a streamed read hands over at a time. A batch of this size bounds a reader of a large
#: table to a few tens of megabytes whatever the pool, and what the reader reads is the same at
#: any size (a test compares one row at a time with this).
BATCH_ROWS = 50_000


class Catalog:
    """A read-only DuckDB session over one run of one project."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        project: Path,
        run_directory: Path | None,
    ) -> None:
        self.connection = connection
        self.project = project
        self.run_directory = run_directory

    @property
    def has_run(self) -> bool:
        """Whether the project has a published run to read."""
        return self.run_directory is not None

    # -- setup ------------------------------------------------------------

    def _define_views(self) -> None:
        """Create one view per table, over whatever fragments exist.

        A table with no fragments gets a view producing no rows rather than no
        view at all, so a query written against it still runs. The alternative
        makes every caller check whether a table exists before reading it, and
        somebody eventually forgets.
        """
        for spec in TABLES:
            directory = (
                self.project / RECORD_DIR
                if spec.spans_runs
                else (self.run_directory / spec.directory if self.run_directory else None)
            )
            fragments = sorted(directory.glob("*.parquet")) if directory else []
            if fragments:
                # A run an older engine wrote lacks the columns added since. Each such column
                # reads as its declared default.
                statement = (
                    f"CREATE OR REPLACE VIEW {spec.name} AS "
                    f"{parquet_source(self.connection, spec, fragments)}"
                )
            else:
                columns = ", ".join(
                    f'NULL::{_duck_type(field)} AS "{field.name}"' for field in spec.schema
                )
                statement = f"CREATE OR REPLACE VIEW {spec.name} AS SELECT {columns} WHERE false"
            self.connection.execute(statement)

    # -- querying ---------------------------------------------------------

    def query(self, sql: str, parameters: Any = None) -> duckdb.DuckDBPyConnection:
        """Run a query against the views. The escape hatch, used deliberately."""
        return self.connection.execute(sql, parameters)

    def batches(
        self, sql: str, parameters: Any = None, *, rows: int | None = None
    ) -> Iterator[list[dict[str, Any]]]:
        """A query's rows as dictionaries, :data:`BATCH_ROWS` at a time.

        This is the streamed form of ``.to_arrow_table().to_pylist()``, value for value. The rows
        pass through Arrow exactly as the whole table did, a batch at a time, so a reader of a
        table that can be large holds one batch and never the table.
        """
        result = self.connection.execute(sql, parameters)
        # ``to_arrow_reader`` where DuckDB has it; ``fetch_record_batch`` is its older name.
        read = getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
        for batch in read(rows or BATCH_ROWS):
            yield batch.to_pylist()

    def count(self, table: str) -> int:
        """Return the row count of one table."""
        row = self.connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()  # noqa: S608
        return int(row[0]) if row else 0

    def counts(self) -> dict[str, int]:
        """Return row counts for every table, in canonical order."""
        return {spec.name: self.count(spec.name) for spec in TABLES}

    # -- the reuse lookup -------------------------------------------------

    def previous_records(self, root_id: str) -> dict[str, PreviousRecord]:
        """Return what the last run knew about each entry under one root.

        A single-table read, which is what makes a re-scan of an unchanged tree
        finish in seconds. It is possible because each run writes a complete
        snapshot rather than a delta, so the current run *is* the whole picture
        and there is no history to fold.

        Deleted entries are excluded. A tombstone says the file was gone at that
        run; if it is back now it is genuinely new work, and reusing a record
        that describes an absence would be wrong.

        The two hash flags come from identity observations rather than from the
        entry, because byte identity is a reading of content and belongs with the
        other readings. Only a byte digest counts, by the digest its method declares
        (:func:`ingence.identity.digest_methods`, the list the dive chooses digests by).
        An image's difference hash reads the whole image, and a text sketch or a header
        signature reads part of a file. None of them is the digest a hash setting asks for.
        Read by their scope, they would say that a whole or a sampled digest was held where
        none had been taken.
        """
        if not self.has_run:
            return {}
        digest_of_method = digest_methods()
        whole = sorted(name for name, digest in digest_of_method.items() if digest == "whole")
        sampled = sorted(name for name, digest in digest_of_method.items() if digest == "sampled")
        cursor = self.connection.execute(
            """
            SELECT
                e.entry_id                   AS entry_id,
                e.stat_fingerprint           AS stat_fingerprint,
                e.processing_fingerprint     AS processing_fingerprint,
                e.scan_outcome               AS scan_outcome,
                e.first_seen_run             AS first_seen_run,
                e.event_time_ns              AS event_time_ns,
                e.event_time_source          AS event_time_source,
                e.event_time_tier            AS event_time_tier,
                e.extraction_profile         AS extraction_profile,
                e.moved_from                 AS moved_from,
                e.moved_by                   AS moved_by,
                e.moved_at_ns                AS moved_at_ns,
                coalesce(h.has_full, false)  AS has_full_hash,
                coalesce(h.has_quick, false) AS has_quick_hash,
                coalesce(x.had_error, false) AS had_error
            FROM entries e
            LEFT JOIN (
                SELECT
                    entry_id,
                    bool_or(list_contains(?::VARCHAR[], identity_method)) AS has_full,
                    bool_or(list_contains(?::VARCHAR[], identity_method)) AS has_quick
                FROM observations
                WHERE list_contains(?::VARCHAR[], identity_method)
                GROUP BY entry_id
            ) h ON h.entry_id = e.entry_id
            LEFT JOIN (
                SELECT entry_id, true AS had_error
                FROM scan_errors
                WHERE entry_id IS NOT NULL
                GROUP BY entry_id
            ) x ON x.entry_id = e.entry_id
            WHERE e.root_id = ? AND NOT e.is_deleted
            """,
            [whole, sampled, sorted(digest_of_method), root_id],
        )
        # Named access rather than positional. Indexing by position means that
        # inserting a column silently shifts every field after it, which is a
        # bug that type checking cannot catch and a test only catches by luck.
        names = [column[0] for column in cursor.description]
        return {
            row["entry_id"]: PreviousRecord(
                stat_fingerprint=row["stat_fingerprint"],
                processing_fingerprint=row["processing_fingerprint"],
                scan_outcome=(
                    ScanOutcome(row["scan_outcome"])
                    if row["scan_outcome"]
                    else ScanOutcome.METADATA_ONLY
                ),
                has_full_hash=bool(row["has_full_hash"]),
                has_quick_hash=bool(row["has_quick_hash"]),
                had_error=bool(row["had_error"]),
                first_seen_run=row["first_seen_run"],
                event_time=(
                    (
                        int(row["event_time_ns"]),
                        str(row["event_time_source"]),
                        str(row["event_time_tier"]),
                    )
                    if row["event_time_ns"] is not None
                    else None
                ),
                extraction_profile=row["extraction_profile"],
                move=(
                    (
                        str(row["moved_from"]),
                        str(row["moved_by"]),
                        None if row["moved_at_ns"] is None else int(row["moved_at_ns"]),
                    )
                    if row["moved_from"] is not None and row["moved_by"] is not None
                    else None
                ),
            )
            for row in (dict(zip(names, values, strict=True)) for values in cursor.fetchall())
        }

    # -- the project's own vocabulary -------------------------------------

    def recognised_vocabulary(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return what the Dictionary recognised in this pool, most used first.

        SQL against the views rather than an in-memory scan, so it holds at a
        million entries. The join is to the per-run Dictionary snapshot rather
        than to the shipped section files, because what a run recognised is a
        fact about the vocabulary that run used.
        """
        if not self.has_run:
            return []
        return self._rows(
            """
            SELECT
                d.term          AS term,
                d.canonical     AS canonical,
                d.theme_kind    AS theme_kind,
                d.origin        AS origin,
                sum(c.occurrences)          AS occurrences,
                count(DISTINCT b.entry_id)  AS entries
            FROM dictionary_bindings b
            JOIN components c ON c.component_id = b.component_id
            JOIN dictionary_entries d ON d.term_id = b.term_id
            WHERE b.resolution_status = 'resolved'
            GROUP BY d.term, d.canonical, d.theme_kind, d.origin
            ORDER BY entries DESC, occurrences DESC, d.term
            LIMIT ?
            """,
            [limit],
        )

    def unbound_tokens(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return the recurring tokens the Dictionary matched nothing for.

        This is the discovery surface, computed on demand and not persisted. Nothing is written as
        a proposed Dictionary entry, so nothing accumulates that a person never reviewed. The
        ranking sharpens as the pool grows, without any past run being rewritten. Promotion stays
        a human act: the null model that would justify an automatic one does not exist until the
        inference layer.

        Ordered by how many distinct entries carry the token rather than by raw
        count, because a token appearing forty times in one name says far less
        than one appearing once in forty.

        There are three outcomes, not one null. ``examined_no_evidence`` is the discovery surface
        proper: nobody has heard of this token. ``examined_conflicting`` is a different offer,
        because the Dictionary has the token twice and the useful action is to declare the
        ambiguity or drop the weaker surface. ``unexamined`` says the binder never looked, which is
        a warning and not a discovery. Collapsing them into one null would make the second look
        like the first, and would offer to add a term the Dictionary already has.
        """
        if not self.has_run:
            return []
        return self._rows(
            """
            SELECT
                coalesce(b.resolution_status, 'unexamined') AS outcome,
                c.component_type            AS component_type,
                c.normalized_value          AS token,
                sum(c.occurrences)          AS occurrences,
                count(DISTINCT c.entry_id)  AS entries,
                min(c.raw_value)            AS example,
                max(coalesce(b.constraint, '')) AS constraint
            FROM components c
            LEFT JOIN dictionary_bindings b ON b.component_id = c.component_id
            WHERE coalesce(b.resolution_status, 'unexamined') <> 'resolved'
            GROUP BY outcome, c.component_type, c.normalized_value
            ORDER BY entries DESC, occurrences DESC, c.normalized_value
            LIMIT ?
            """,
            [limit],
        )

    def shape_corpus(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return the name skeletons this pool uses, commonest first.

        The query aggregates over the pool, which is what shape coverage needs. ``skeleton_of``
        abstracts a name into its layout, and every run writes one per entry. The query groups
        them. Ranked by how many entries carry a skeleton rather than by raw count, for the same
        reason the unbound ranking is: a shape used forty times in one folder says much less than
        one used once in forty folders.

        This differs from the per-folder composition signature, which aggregates the same digests
        *within* a folder to characterise how it is organised. That one asks what mixture a folder
        holds. This one asks what shapes a pool uses. The input is the same, the questions differ,
        and both are wanted.

        A skeleton carries no gene, protein, clone or project code, because a skeleton is a name
        with its content abstracted away. That is what makes a corpus of skeletons safe to keep for
        a pool whose contents are not.
        """
        if not self.has_run:
            return []
        return self._rows(
            """
            SELECT
                skeleton,
                skeleton_hash,
                slot_count,
                count(*)                     AS entries,
                count(DISTINCT source_scope) AS scopes
            FROM name_shapes
            GROUP BY skeleton, skeleton_hash, slot_count
            ORDER BY entries DESC, skeleton
            LIMIT ?
            """,
            [limit],
        )

    def unbound_rate(self) -> dict[str, float | int]:
        """Return how much of this pool's vocabulary the Dictionary did not bind.

        Two denominators exist and they answer different questions, so the result names both. A
        number in a report must be reproducible from the Index.

        Word occurrences is the primary figure. It counts only components of kind ``word``. The
        census splits the extension off into a component of its own kind before anything is
        tokenised, so a stem is what remains and nobody removes a suffix.

        All kinds counts every component except the extension. It includes dates, numbers, versions
        and identifiers, which are structurally understood while intentionally lacking a binding.
        A date is not an unknown word, and a rate that treats it as one measures something else.

        Neither figure is semantic recall or accuracy. A contextually wrong Dictionary hit counts as
        bound.
        """
        if not self.has_run:
            return {}
        row = self.connection.execute(
            """
            SELECT
                sum(CASE WHEN c.component_type = 'word' THEN c.occurrences ELSE 0 END),
                sum(CASE WHEN c.component_type = 'word' AND bound THEN c.occurrences ELSE 0 END),
                sum(CASE WHEN c.component_type <> 'extension' THEN c.occurrences ELSE 0 END),
                sum(CASE WHEN c.component_type <> 'extension' AND bound THEN c.occurrences
                    ELSE 0 END)
            FROM (
                SELECT
                    c.component_type,
                    c.occurrences,
                    coalesce(b.resolution_status, 'unexamined') = 'resolved' AS bound
                FROM components c
                LEFT JOIN dictionary_bindings b ON b.component_id = c.component_id
            ) AS c
            """
        ).fetchone()
        if row is None:
            return {}
        words, words_bound, atoms, atoms_bound = (int(value or 0) for value in row)
        return {
            "word_occurrences": words,
            "word_unbound": words - words_bound,
            "word_unbound_rate": (words - words_bound) / words if words else 0.0,
            "atom_occurrences": atoms,
            "atom_unbound": atoms - atoms_bound,
            "atom_unbound_rate": (atoms - atoms_bound) / atoms if atoms else 0.0,
        }

    def vocabulary_coverage(self) -> dict[str, int]:
        """Return how much of the Dictionary this pool actually used.

        The number that turns "is the shipped Dictionary too large" from a
        judgement into a measurement. An entry that never fires costs a few bytes
        and is never seen, which is the argument for exhaustiveness; an entry
        that fires wrongly is a different thing entirely, and separating the two
        needs the count of each.

        Read against one pool it says little: a small tree exercises a small
        vocabulary and that is not evidence about the Dictionary. Read across
        several pools of different provenance it is the coverage half of the
        overfitting measurement.
        """
        if not self.has_run:
            return {}
        shipped = self.count("dictionary_entries")
        row = self.connection.execute(
            "SELECT count(DISTINCT term_id) FROM dictionary_bindings "
            "WHERE resolution_status = 'resolved'"
        ).fetchone()
        fired = int(row[0]) if row else 0
        return {"shipped": shipped, "fired": fired, "unused": shipped - fired}

    def _rows(self, sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
        """Run a query and return named rows.

        Rows are named and not positional, because inserting a column silently shifts every field
        after it.
        """
        cursor = self.connection.execute(sql, parameters)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    def live_entry_ids(self, root_id: str) -> set[str]:
        """Return the entries the last run saw under one root, for tombstoning.

        Whatever is here and not in the new census has disappeared, and gets a
        tombstone rather than being dropped. Deletion is recorded, never applied.
        """
        if not self.has_run:
            return set()
        rows = self.connection.execute(
            "SELECT entry_id FROM entries WHERE root_id = ? AND NOT is_deleted",
            [root_id],
        ).fetchall()
        return {row[0] for row in rows}

    def live_entries(self, root_id: str) -> dict[str, Entry]:
        """Return the entries the last run held live under one root, whole, by identifier.

        A census needs this for each entry it did not walk: whether it is a member of a
        container, which no census walks, or has gone, and what it was, so that its
        tombstone can say.
        """
        if not self.has_run:
            return {}
        rows = self._rows(
            "SELECT * FROM entries WHERE root_id = ? AND NOT is_deleted ORDER BY entry_id",
            [root_id],
        )
        return {str(row["entry_id"]): record_from_row(row, Entry) for row in rows}

    def embedding_edges(self) -> list[Assertion]:
        """Return the last run's ``embeds`` edges, each placing a member in its container."""
        if not self.has_run:
            return []
        rows = self._rows(
            "SELECT * FROM assertions WHERE primitive = ? ORDER BY edge_id", ["embeds"]
        )
        return [record_from_row(row, Assertion) for row in rows]

    def close(self) -> None:
        self.connection.close()


def census_facts(catalog: Catalog) -> tuple[tuple[ExcludedNames, ...], tuple[RecognisedPath, ...]]:
    """Return what the current run says a census left out and recognised, to carry forward.

    A run is a complete snapshot, and these two facts belong to the census alone: the names the
    walk left out, by reason, and the engine's own files it recognised. A dive and an inference walk
    nothing, so they carry what their parent run holds, as inference already carries the mention
    tally. Without this, any view that reads these facts from the current run would show nothing
    left out once a dive had run. A run written before either field existed carries nothing, which
    is what it holds.
    """
    columns = {row[0] for row in catalog.query("DESCRIBE SELECT * FROM run").fetchall()}
    wanted = [name for name in ("excluded_json", "recognised_json") if name in columns]
    if not wanted:
        return (), ()
    row = catalog.query(f"SELECT {', '.join(wanted)} FROM run").fetchone()  # noqa: S608
    if row is None:
        return (), ()
    held = dict(zip(wanted, row, strict=True))

    def loaded(name: str) -> list[dict[str, Any]]:
        value = held.get(name)
        return list(json.loads(value)) if value else []

    return (
        tuple(ExcludedNames.model_validate(item) for item in loaded("excluded_json")),
        tuple(RecognisedPath.model_validate(item) for item in loaded("recognised_json")),
    )


def _duck_type(field: Any) -> str:
    """Map an Arrow field to the DuckDB type used for an empty placeholder view."""
    arrow = str(field.type)
    if arrow.startswith("list"):
        return "VARCHAR[]"
    return {
        "string": "VARCHAR",
        "int64": "BIGINT",
        "uint64": "UBIGINT",
        "double": "DOUBLE",
        "bool": "BOOLEAN",
    }.get(arrow, "VARCHAR")


# ---------------------------------------------------------------------------
# Reading a run an older engine wrote.
# ---------------------------------------------------------------------------


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_sql_literal(item) for item in value) + "]"
    return "'" + str(value).replace("'", "''") + "'"


def sql_path(path: Path) -> str:
    """A path as a SQL string literal, its quotes doubled: a folder named with an apostrophe
    (``a person's runs``) is a path like any other, never the end of a string.

    This function or :func:`sql_path_list` writes every path the engine names to DuckDB. A path
    pasted between quotes as it stands would end the string at an apostrophe, and DuckDB would read
    the rest of the name as SQL. A search would fail, and a name built for it could run a statement
    of its own, which DuckDB runs even where the last statement of the text has bound parameters.
    """
    return "'" + path.as_posix().replace("'", "''") + "'"


def sql_path_list(paths: Iterable[Path]) -> str:
    """Paths as one SQL list of string literals, ``['a', 'b']``, each one :func:`sql_path`'s."""
    return "[" + ", ".join(sql_path(path) for path in paths) + "]"


def declared_default(spec: TableSpec, column: str) -> str:
    """The SQL a column reads as where a run holds no such column: its declared default, stored.

    This is the explicit compatibility projection. A column added by a later schema is absent from
    every run an older engine wrote. ``union_by_name`` fills a column only among the files it is
    given, so projecting the current schema's names from an older run would fail outright. Here the
    record's own field says what an absent value means: its default, written the way
    :func:`~ingence.storage.index_store.row_for` writes it (a ``_json`` column as canonical JSON, an
    enumeration as its value). A field declaring no default reads as null, never a guessed value.
    """
    from pydantic import TypeAdapter

    duck = _duck_type(spec.schema.field(column))
    name = column.removesuffix(JSON_SUFFIX) if column.endswith(JSON_SUFFIX) else column
    info = spec.model.model_fields.get(name)
    if info is None or info.is_required():
        return f"NULL::{duck}"
    default = info.get_default(call_default_factory=True)
    # A field's annotation is typed as possibly absent, and a newer pydantic types the adapter's
    # argument without that case. The value is passed through as it is.
    annotation: Any = info.annotation
    dumped = TypeAdapter(annotation).dump_python(default, mode="json")
    stored = canonical_json(dumped) if column.endswith(JSON_SUFFIX) else dumped
    return f"{_sql_literal(stored)}::{duck}"


def parquet_source(
    connection: duckdb.DuckDBPyConnection, spec: TableSpec, paths: Sequence[Path]
) -> str:
    """A ``SELECT`` over one table's fragments holding every column the current schema declares.

    Columns the fragments hold are read as they are. Each column that the current schema declares
    and the fragments lack is its :func:`declared_default`. A run the current engine wrote holds
    every column, and reads exactly as ``SELECT *`` does.
    """
    import pyarrow.parquet as pq

    scan = f"read_parquet({sql_path_list(paths)}, union_by_name = true)"
    # The read takes footers alone, through Arrow. A DuckDB ``DESCRIBE`` plans a query per table
    # and cost a catalog about 40 ms on opening. The newest fragment is read first. Where it holds
    # every column, as every fragment the current engine writes does, nothing else is read.
    # Otherwise every footer is read, so a column any fragment holds is read from it.
    wanted = spec.schema.names
    held = set(pq.read_schema(paths[-1]).names)
    if not held.issuperset(wanted):
        for path in paths[:-1]:
            held.update(pq.read_schema(path).names)
    absent = [name for name in wanted if name not in held]
    if not absent:
        return f"SELECT * FROM {scan}"  # noqa: S608
    added = ", ".join(f'{declared_default(spec, name)} AS "{name}"' for name in absent)
    return f"SELECT *, {added} FROM {scan}"  # noqa: S608


@contextmanager
def open_catalog(project: Path, *, run_id: str | None = None) -> Iterator[Catalog]:
    """Open a catalog over the current run, or over a named earlier one.

    Naming a run is time travel, and it is free: previous runs remain on disk and queryable. That
    is what makes "the graph is a function of the four artifacts, not an artifact itself" true in
    operation.
    """
    directory = run_path(project, run_id) if run_id else resolve_current_run(project)
    if directory is not None and not directory.is_dir():
        message = f"run directory {directory} does not exist"
        raise FileNotFoundError(message)
    connection = duckdb_connection()
    catalog = Catalog(connection, project=project, run_directory=directory)
    try:
        catalog._define_views()
        yield catalog
    finally:
        catalog.close()


@contextmanager
def open_staged(project: Path, directory: Path) -> Iterator[Catalog]:
    """Open a catalog over a run's directory that is staged and not yet published.

    The passes that describe the run they are writing use this, such as the quality figures. They
    get the same views over the same tables as a reader, taken from the staging directory before its
    pointer is swapped, so a figure counts what a reader will open.
    """
    connection = duckdb_connection()
    catalog = Catalog(connection, project=project, run_directory=directory)
    try:
        catalog._define_views()
        yield catalog
    finally:
        catalog.close()


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------

#: Columns whose values legitimately differ between two identical runs. Every one
#: of them identifies a run or records a moment; none of them describes the data.
#:
#: This list *is* the definition of the reproducibility claim, so it is written out rather than
#: derived. Two identical runs over identical input produce identical records modulo these columns,
#: and every other column, including every identifier, must match exactly. A column added here
#: quietly weakens the claim, which is why adding one needs an argument.
#:
#: ``inode`` is the one column here that describes the data, and its place is argued for. The
#: macOS ExFAT driver invents an inode for a file that owns no cluster and does not keep it from
#: one lookup to the next. On a sample that nothing had touched, seven of eight empty files had a
#: different inode an hour later. The filesystem answered one question two ways, and the census
#: recorded both answers faithfully. Nothing reads an inode back: reuse leaves it out of its
#: fingerprint and recognition takes its own ``stat``. So a comparison that counted the inode would
#: call a faithful census irreproducible, and leaving it out weakens the claim for no
#: interpretation the engine makes.
#:
#: ``device`` is the second, on the same argument. A network share is given a new device number each
#: time it is mounted. A share walked again after a remount gives every entry a new ``device``, and
#: nothing else changes except the files that did change. The number describes the mount and not the
#: file. Nothing reads a stored one back, except a rebind that replays the one-file-system guard
#: against the same census's own root, which a new number cannot disturb. Entry identifiers and
#: reuse leave it out.
#:
#: ``uid`` and ``gid`` are the third and fourth. A network share reports a file's owner and group as
#: the mounting machine maps them, so a walk of an unchanged folder from another machine differs on
#: every entry and in nothing else. They describe the mount's account and not the file. No identity,
#: fingerprint or rule reads them.
VOLATILE_COLUMNS: frozenset[str] = frozenset(
    {
        "run_id",
        "first_seen_run",
        "last_seen_run",
        "against_run_id",
        "parent_run_id",
        "observed_at",
        "occurred_at",
        "started_at",
        "ended_at",
        "elapsed_ns",
        "duration_ns",
        # The engine's own peak memory at the end of a run, in the quality figures: the machine
        # and the moment, not the data.
        "peak_bytes",
        # Which pass wrote a run's quality figures (a census, a rebind of it, a dive): it names
        # the run, not the data, so a rebind's figures equal its census's to the digit.
        "phase",
        "inode",
        "device",
        "uid",
        "gid",
    }
)

#: The column every record carries saying which version of the contract wrote it.
#:
#: It is compared as equal, and so is every copy of it serialised inside a nested record. It
#: describes the contract and not the data. Between two runs of one schema it agrees by
#: construction, so comparing it adds nothing. Across a schema bump it differs on every row. The
#: same string is embedded in every serialised rule, binding, shape and defeater, and comparing
#: those copies would report each of those rows as changed when none was. A bump that changes a
#: nested record's shape still shows, because the shape is compared. Only the version stamp is not.
VERSION_STAMP = "schema_version"

#: The embedded stamp, as canonical JSON writes it, and what it is compared as.
_EMBEDDED_STAMP = (r'"schema_version":"[^"]*"', '"schema_version":""')


def _compared(name: str) -> str:
    """Return the expression a column is compared through: itself, or its JSON unstamped."""
    if name.endswith(JSON_SUFFIX):
        pattern, replacement = _EMBEDDED_STAMP
        return f"regexp_replace(\"{name}\", '{pattern}', '{replacement}', 'g') AS \"{name}\""
    return f'"{name}"'


#: Tables excluded from the comparison entirely. The run record is a description
#: of a run and differs by construction; the journal records timings and
#: scheduling and is expected to differ between two runs that did identical work.
VOLATILE_TABLES: frozenset[str] = frozenset({"run", "journal_events"})


def compare_runs(project: Path, left: str, right: str) -> dict[str, int]:
    """Return the number of differing rows per table between two runs.

    An empty result means the two runs are identical under the reproducibility claim. This is the
    check that two clean scans of an unchanged tree produce the same output. It is a real
    comparison and not a file digest, because a digest can never pass: the run identifier is
    stamped into every row.

    The comparison runs in both directions. Counting only rows present on the left that are
    absent on the right would report nothing when the right has extra rows.
    """
    differences: dict[str, int] = {}
    connection = duckdb_connection()
    try:
        for spec in TABLES:
            if spec.name in VOLATILE_TABLES or spec.spans_runs:
                continue
            columns = [
                name
                for name in spec.schema.names
                if name not in VOLATILE_COLUMNS and name != VERSION_STAMP
            ]
            if not columns:
                continue
            projection = ", ".join(_compared(name) for name in columns)
            sources = {
                side: sorted((run_path(project, run) / spec.directory).glob("*.parquet"))
                for side, run in (("l", left), ("r", right))
            }
            if not sources["l"] and not sources["r"]:
                continue
            statements = {}
            for side, paths in sources.items():
                if paths:
                    statements[side] = (
                        f"SELECT {projection} FROM ({parquet_source(connection, spec, paths)})"  # noqa: S608
                    )
                else:
                    empty = ", ".join(
                        f'NULL::{_duck_type(field)} AS "{field.name}"'
                        for field in spec.schema
                        if field.name in columns
                    )
                    statements[side] = f"SELECT {empty} WHERE false"
            row = connection.execute(
                f"SELECT (SELECT count(*) FROM (({statements['l']}) EXCEPT ({statements['r']}))) "  # noqa: S608
                f"     + (SELECT count(*) FROM (({statements['r']}) EXCEPT ({statements['l']})))"
            ).fetchone()
            count = int(row[0]) if row else 0
            if count:
                differences[spec.name] = count
    finally:
        connection.close()
    return differences


def describe_differences(
    project: Path, left: str, right: str, *, limit: int = 8
) -> dict[str, list[str]]:
    """Return, per table, a readable account of how two runs actually differ.

    ``compare_runs`` answers whether two runs agree. This function answers why they do not, which is
    what anybody who sees a difference needs to know. A comparison that reports "four rows differ"
    and nothing else leaves a person exactly where they started.

    It reports differing *columns* where a row exists on both sides under the same primary key, and
    reports presence or absence where it does not. Column differences are what a real
    reproducibility failure looks like: the rows are the same rows, and one field is unstable.

    The rows are taken in the canonical order of the table's primary key. ``--verify`` checks that
    runs agree, so its sample rows must not depend on chance. Queries with a ``LIMIT`` and no
    ``ORDER BY`` would choose different rows, or the same rows in another order, on two calls for
    one project. Within a table the order is each column in the table's own order, then the rows
    only the earlier run holds, then the rows only the later run holds, each in the order of the
    key.
    """
    findings: dict[str, list[str]] = {}
    connection = duckdb_connection()
    try:
        for spec in TABLES:
            if spec.name in VOLATILE_TABLES or spec.spans_runs or not spec.primary_key:
                continue
            columns = [
                name
                for name in spec.schema.names
                if name not in VOLATILE_COLUMNS and name != VERSION_STAMP
            ]
            key = ", ".join(f'"{name}"' for name in spec.primary_key)
            joined_key = ", ".join(f'l."{name}"' for name in spec.primary_key)
            sides: dict[str, str | None] = {}
            for side, run in (("l", left), ("r", right)):
                paths = sorted((run_path(project, run) / spec.directory).glob("*.parquet"))
                if not paths:
                    sides[side] = None
                    continue
                sides[side] = (
                    f"SELECT {', '.join(_compared(n) for n in columns)} "  # noqa: S608
                    f"FROM ({parquet_source(connection, spec, paths)})"
                )
            left_source, right_source = sides["l"], sides["r"]
            if left_source is None or right_source is None:
                continue

            reported: list[str] = []
            for column in columns:
                if column in spec.primary_key:
                    continue
                found = connection.execute(
                    f"""
                    WITH l AS ({left_source}), r AS ({right_source})
                    SELECT l.{spec.primary_key[0]},
                           CAST(l."{column}" AS VARCHAR),
                           CAST(r."{column}" AS VARCHAR)
                    FROM l JOIN r USING ({key})
                    WHERE l."{column}" IS DISTINCT FROM r."{column}"
                    ORDER BY {joined_key}
                    LIMIT {limit}
                    """  # noqa: S608
                ).fetchall()
                for row in found:
                    reported.append(f"{column}: {row[0]} has {row[1]!r} then {row[2]!r}")
            missing = connection.execute(
                f"""
                WITH l AS ({left_source}), r AS ({right_source})
                SELECT {key}, 'only in the earlier run' AS what, 0 AS side FROM (
                    SELECT {key} FROM l EXCEPT SELECT {key} FROM r)
                UNION ALL
                SELECT {key}, 'only in the later run' AS what, 1 AS side FROM (
                    SELECT {key} FROM r EXCEPT SELECT {key} FROM l)
                ORDER BY side, {key}
                LIMIT {limit}
                """  # noqa: S608
            ).fetchall()
            reported.extend(f"{row[-2]}: {row[0]}" for row in missing)
            if reported:
                findings[spec.name] = reported[:limit]
    finally:
        connection.close()
    return findings
