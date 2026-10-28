"""The tables a search reads, derived from one published run and deletable at any time.

The projection is derived data and not a store. Its state can be reconstructed from durable
records. Everything here is a function of one run's Parquet tables and the Record. So the projection
lives outside the run directory, and inference never reads it. It is not part of the reproducibility
claim, because ``storage/catalog.compare_runs`` never sees it. The engine rebuilds it and does not
repair it. Deleting ``projections/`` loses nothing but the seconds that the build takes.

The layout, beside ``runs/`` and ``record/`` in a project::

    projections/search/<run_id>/
        meta.json          the run, the projection version, the normaliser and vocabulary read
        terms.parquet      one row per (entry, token): what a name says and what it was bound to
        vocabulary.parquet one row per token: how many entries and folders carry it
        closure.parquet    one row per (entry, ancestor folder, distance): how matches roll up
        pieces.parquet     one row per (piece, token): the case pieces of words kept whole
        folders.parquet    one row per folder: its name, path, depth and how many files it holds
        units.parquet      one row per folder: its unit kind, why, doubted and marked good
        experiments.parquet the folders the experiment rules concluded on, made one set (``scale``)
        homes.parquet      one row per entry that belongs to a unit: which, and how far above
        products.parquet   one row per file made for people to read, with its home, if any
        dated.parquet      the folders whose own name carries a day's date and another word
        unit_words.parquet one row per (unit document, word): the words of the names in each unit
        unit_word_counts.parquet one row per word: how many unit documents hold it

The build uses SQL and never a Python loop over rows. A census of a large share is the size that the
build has to handle. The record objects, and not the rows, are what cost memory. Every table here is
a ``COPY (SELECT ...) TO`` over the catalog's own views. Python sees only the vocabulary, which is
thousands of rows and not hundreds of thousands.

The tables are sorted by token, so DuckDB's row-group statistics prune a query's
``WHERE token IN (...)`` to the few groups that can hold a match. That is the whole of the
"inverted index": Parquet's min/max per row group over a sorted column. It needs no extension and no
network.

The build writes the projection whole or not at all. It builds in the project's staging directory
and moves the result into place with one ``os.replace``, the way a run is published. A search then
never reads half a projection.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.search import PROJECTION_VERSION
from ingence.search.scale import DEFAULT_SCALE, ScaleParameters, write_units
from ingence.storage.catalog import sql_path
from ingence.storage.layout import STAGING_DIR, owned_path
from ingence.storage.own_files import filesystem_entry

if TYPE_CHECKING:
    from ingence.storage.catalog import Catalog

__all__ = [
    "PROJECTIONS_DIR",
    "SEARCH_DIR",
    "UNIT_DOCUMENT_SQL",
    "PoolVocabulary",
    "Projection",
    "build_projection",
    "held_projection",
    "hold_folder",
    "hold_projection",
    "pieces_of",
    "projection_path",
    "read_projection",
]

#: Beside ``RUNS_DIR`` and ``RECORD_DIR``, and outside both: derived data only.
PROJECTIONS_DIR = "projections"
SEARCH_DIR = "search"

#: The Record event kinds whose words are searched. A person's note on a folder and an annotation
#: are the most specific description that a folder will ever have.
_SEARCHED_EVENT_KINDS = ("note", "annotate")


def projection_path(project: Path, run_id: str) -> Path:
    """Return the path where the search projection of one run lives."""
    return project / PROJECTIONS_DIR / SEARCH_DIR / run_id


# ---------------------------------------------------------------------------
# Building.
# ---------------------------------------------------------------------------

#: What a name says, and what it was bound to. ``source`` is ``name`` for every row that a census
#: wrote. ``content`` rows come from the text-word module's keyword observations where a dive
#: produced them. ``note`` rows come from the Record, and Python adds them below, because a note is
#: free text that has to be tokenised.
_TERMS_SQL = """
SELECT
    c.entry_id                                  AS entry_id,
    e.parent_entry_id                           AS parent_id,
    e.entry_type                                AS entry_type,
    CASE WHEN c.source_scope = 'content' THEN 'content' ELSE 'name' END AS source,
    lower(c.normalized_value)                   AS token,
    c.component_type                            AS component_type,
    c.raw_value                                 AS raw_value,
    c.occurrences                               AS occurrences,
    c.magnitude                                 AS magnitude,
    c.dimension                                 AS dimension,
    b.term_id                                   AS term_id,
    b.term                                      AS term,
    b.theme_kind                                AS theme_kind,
    b.resolution_status                         AS resolution_status,
    c.normalizer_version                        AS normalizer_version
FROM components c
JOIN entries e ON e.entry_id = c.entry_id
LEFT JOIN dictionary_bindings b ON b.component_id = c.component_id
WHERE NOT e.is_deleted
UNION ALL
SELECT * FROM content_terms
ORDER BY token, entry_id
"""

#: Search's content channel. It holds a file's distinctive words, taken from the keyword
#: extractor's term counts (``text.term_counts``) and weighed against every file's counts, as a
#: TF-IDF over files as documents. The file's top :data:`CONTENT_TERMS_PER_FILE` words become
#: ``content`` rows. The channel also holds the names that a script's save calls write
#: (``script.output_names``), with each piece of a pattern as a ``script_output`` row. Neither is a
#: name that the file carries, so neither binds a sense.
CONTENT_TERMS_PER_FILE = 25
#: The query below holds ``@CONTENT_TERMS_PER_FILE@`` where that number goes. The builder fills it
#: in.

_CONTENT_TERMS_SQL = """
CREATE OR REPLACE TEMP VIEW content_terms AS
WITH counts AS (
    SELECT o.entry_id,
           unnest(from_json(json_extract(o.value_structure, '$.terms'), '["VARCHAR"]')) AS term,
           unnest(from_json(json_extract(o.value_structure, '$.counts'), '["BIGINT"]')) AS n
    FROM observations o
    WHERE o.key = 'text.term_counts'
),
df AS (SELECT term, count(DISTINCT entry_id) AS files FROM counts GROUP BY term),
total AS (SELECT count(DISTINCT entry_id) AS files FROM counts),
weighted AS (
    SELECT c.entry_id, c.term, c.n,
           (1 + ln(c.n)) * (1 + ln((1 + total.files) / (1 + df.files))) AS weight
    FROM counts c JOIN df USING (term) CROSS JOIN total
    QUALIFY row_number() OVER (PARTITION BY c.entry_id ORDER BY weight DESC, c.term)
        <= @CONTENT_TERMS_PER_FILE@
),
outputs AS (
    SELECT o.entry_id, unnest(from_json(o.value_structure, '["VARCHAR"]')) AS pattern
    FROM observations o
    WHERE o.key = 'script.output_names'
),
output_pieces AS (
    SELECT DISTINCT entry_id, pattern, lower(piece) AS piece
    FROM (SELECT entry_id, pattern,
                 unnest(regexp_split_to_array(pattern, '[^0-9A-Za-z]+')) AS piece FROM outputs)
    WHERE length(piece) >= 2
)
SELECT w.entry_id, e.parent_entry_id, e.entry_type, 'content', lower(w.term), 'content_term',
       w.term, CAST(w.n AS INTEGER), CAST(NULL AS DOUBLE), CAST(NULL AS VARCHAR),
       CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR),
       CAST(NULL AS VARCHAR), 'keywords-1'
FROM weighted w JOIN entries e ON e.entry_id = w.entry_id
WHERE NOT e.is_deleted
UNION ALL
SELECT p.entry_id, e.parent_entry_id, e.entry_type, 'script_output', p.piece, 'script_output',
       p.pattern, 1, CAST(NULL AS DOUBLE), CAST(NULL AS VARCHAR),
       CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR),
       CAST(NULL AS VARCHAR), 'keywords-1'
FROM output_pieces p JOIN entries e ON e.entry_id = p.entry_id
WHERE NOT e.is_deleted
"""

#: The number of entries and folders that carry each token. The ranking's tiebreak uses these
#: document frequencies to discount common words. A suggestion reads the whole vocabulary here.
_VOCABULARY_SQL = """
SELECT
    token,
    count(DISTINCT entry_id)                    AS entries,
    count(DISTINCT parent_id)                   AS folders,
    min(raw_value)                              AS example,
    arg_max(term, occurrences)                  AS term,
    arg_max(theme_kind, occurrences)            AS theme_kind,
    bool_or(resolution_status = 'resolved')     AS ever_bound
FROM terms
GROUP BY token
ORDER BY token
"""

#: Every entry's ancestor folders with their distance, by a recursive walk up ``parent_entry_id``.
#: For a large census at a typical depth near ten, this is millions of narrow rows. Parquet holds
#: them in tens of megabytes.
_CLOSURE_SQL = """
WITH RECURSIVE up(entry_id, ancestor_id, distance) AS (
    SELECT entry_id, parent_entry_id, 1
    FROM entries
    WHERE parent_entry_id IS NOT NULL AND NOT is_deleted
    UNION ALL
    SELECT up.entry_id, e.parent_entry_id, up.distance + 1
    FROM up JOIN entries e ON e.entry_id = up.ancestor_id
    WHERE e.parent_entry_id IS NOT NULL
)
SELECT up.entry_id, up.ancestor_id, up.distance
FROM up JOIN entries a ON a.entry_id = up.ancestor_id
WHERE a.entry_type = 'directory'
ORDER BY up.entry_id, up.distance
"""

#: One row per folder. ``subtree_files`` counts ordinary files beneath the folder at any depth. It
#: never counts a container's members, because a person means ordinary files by "how many files are
#: in this".
_FOLDERS_SQL = f"""
SELECT
    f.entry_id                                  AS folder_id,
    f.parent_entry_id                           AS parent_id,
    f.name                                      AS name,
    f.relative_path                             AS relative_path,
    f.depth                                     AS depth,
    count(DISTINCT CASE WHEN c.distance = 1 THEN c.entry_id END) AS direct_entries,
    count(DISTINCT m.entry_id)                  AS subtree_files
FROM entries f
LEFT JOIN closure c ON c.ancestor_id = f.entry_id
LEFT JOIN entries m ON m.entry_id = c.entry_id
    AND m.entry_type = 'file' AND {filesystem_entry("m")}
WHERE f.entry_type = 'directory' AND NOT f.is_deleted
GROUP BY f.entry_id, f.parent_entry_id, f.name, f.relative_path, f.depth
ORDER BY f.relative_path
"""  # noqa: S608 -- the one value interpolated is the shared condition


#: A unit document holds the words of the names that belong to one unit. The words that come with a
#: query use it (pointwise mutual information over unit documents). Each name belongs to exactly
#: one unit document, chosen in this order:
#:
#: 1. its home unit (its experiment, analysis folder or program);
#: 2. itself, for a figure or document of its own;
#: 3. itself, for a folder;
#: 4. the folder that it sits in.
#:
#: Only words count, never numbers, dates, quantities or extensions, and a word has at least two
#: letters. This is SQL over the build's ``terms`` view and the staged ``homes`` and ``products``.
UNIT_DOCUMENT_SQL = """coalesce(
    {homes}.home_id,
    CASE WHEN {alone}.entry_id IS NOT NULL THEN {row}.entry_id END,
    CASE WHEN {row}.entry_type = 'directory' THEN {row}.entry_id END,
    {row}.parent_id
)"""

_UNIT_WORDS_SQL = """
SELECT DISTINCT {document} AS doc_id, t.token AS token
FROM terms t
LEFT JOIN {homes} h ON h.entry_id = t.entry_id
LEFT JOIN {products} p ON p.entry_id = t.entry_id AND p.home_id IS NULL
WHERE t.source = 'name' AND t.component_type = 'word' AND length(t.token) >= 2
  AND {document} IS NOT NULL
ORDER BY doc_id, token
"""


def _write_unit_words(catalog: Catalog, staging: Path) -> int:
    """Write each unit document's words and each word's count of documents.

    The function returns the number of unit documents.
    """
    homes = sql_path(staging / "homes.parquet")
    products = sql_path(staging / "products.parquet")
    document = UNIT_DOCUMENT_SQL.format(homes="h", alone="p", row="t")
    words = staging / "unit_words.parquet"
    _copy(
        catalog,
        _UNIT_WORDS_SQL.format(document=document, homes=homes, products=products),
        words,
    )
    _copy(
        catalog,
        f"SELECT token, count(*) AS units FROM {sql_path(words)} "  # noqa: S608
        "GROUP BY token ORDER BY token",
        staging / "unit_word_counts.parquet",
    )
    counted = catalog.query(
        f"SELECT count(DISTINCT doc_id) FROM {sql_path(words)}"  # noqa: S608
    ).fetchone()
    return int(counted[0]) if counted else 0


@dataclass(frozen=True, slots=True)
class Projection:
    """A built projection: where it is and what it was built from."""

    path: Path
    run_id: str
    meta: dict[str, Any] = field(default_factory=dict)

    def table(self, name: str) -> str:
        """Return the Parquet file that backs one table, as a DuckDB literal (:func:`sql_path`)."""
        return sql_path(self.path / f"{name}.parquet")


def _copy(catalog: Catalog, sql: str, destination: Path) -> None:
    # The COPY runs over the catalog's own views, so the rows never enter Python.
    catalog.query(f"COPY ({sql}) TO {sql_path(destination)} (FORMAT PARQUET)")


def _searched_events(project: Path) -> list[tuple[str, str, str, str]]:
    """Return the notes and annotations on folders as (event id, entry id, kind of entry, text)."""
    from ingence.record.log import read_events

    found: list[tuple[str, str, str, str]] = []
    for event in read_events(project):
        if event.kind.value not in _SEARCHED_EVENT_KINDS:
            continue
        prefix, _, subject = event.target.partition(":")
        if prefix not in ("folder", "entry") or not subject.startswith("entry_"):
            continue
        payload = json.loads(event.payload or "{}")
        text = " ".join(
            str(payload.get(key) or "") for key in ("text", "note", "described")
        ).strip()
        if text:
            found.append(
                (event.event_id, subject, "directory" if prefix == "folder" else "file", text)
            )
    return found


def _note_rows(project: Path, run_id: str, tokenise: Any) -> list[dict[str, Any]]:
    """Return a person's notes and annotations on folders and entries, tokenised as names are.

    The function reads them from the Record, which spans runs. A note written against an earlier run
    still describes the folder, if the folder is still there. The census's own normaliser tokenises
    them. The caller passes it in as ``tokenise``, so this module names no tokeniser.
    """
    rows: list[dict[str, Any]] = []
    for event_id, subject, entry_type, text in _searched_events(project):
        for token, raw in tokenise(text):
            rows.append(
                {
                    "entry_id": subject,
                    "entry_type": entry_type,
                    "source": "note",
                    "token": token,
                    "raw_value": raw,
                    "event_id": event_id,
                    "run_id": run_id,
                }
            )
    return rows


#: Where a written word divides for search purposes: a lower letter or digit before a capital, a
#: capital before a capital-and-lower, and a letter against a digit. This is finer than the
#: binder's ``split_words`` on purpose, because a piece here is only ever a way to find a word.
_PIECE_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])"
)


def pieces_of(raw: str, *, minimum: int = 3) -> tuple[str, ...]:
    """Return every run of adjacent pieces of a written word, casefolded, at least ``minimum`` long.

    ``eSubX`` gives ``esub``, ``esubx``, ``sub`` and ``subx``. ``rawFRAP`` gives ``raw``,
    ``rawfrap`` and ``frap``. ``Kelt8`` gives ``kelt`` and ``kelt8``. The result includes the
    word itself. A caller that wants only proper pieces drops it.
    """
    fine = [part for part in _PIECE_BOUNDARY.split(raw) if part]
    found: set[str] = set()
    for start in range(len(fine)):
        for end in range(start + 1, len(fine) + 1):
            joined = "".join(fine[start:end]).casefold()
            if len(joined) >= minimum and joined.isalnum():
                found.add(joined)
    return tuple(sorted(found))


def _write_pieces(catalog: Catalog, staging: Path) -> None:
    """Write the pieces of every word that the pool uses, from the distinct spellings only.

    This step uses Python, because the pattern needs look-around and DuckDB's regular expressions
    (RE2) have none. It runs over distinct spellings, which is the vocabulary's size and not the
    pool's.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    spellings = catalog.query(
        f"SELECT DISTINCT token, raw_value FROM {sql_path(staging / 'terms.parquet')} "  # noqa: S608
        "WHERE component_type = 'word' AND length(raw_value) >= 4"
    ).fetchall()
    rows = sorted(
        {
            (piece, str(token))
            for token, raw in spellings
            for piece in pieces_of(str(raw))
            if piece != token
        }
    )
    table = pa.table({"piece": [row[0] for row in rows], "token": [row[1] for row in rows]})
    pq.write_table(table, staging / "pieces.parquet")


def build_projection(
    catalog: Catalog,
    *,
    tokenise: Any,
    normalizer_version: str,
    dictionary_hash: str | None,
    scale: ScaleParameters = DEFAULT_SCALE,
    outside: Path | None = None,
) -> Projection:
    """Build the search projection of the catalog's run, and publish it whole.

    ``tokenise`` maps free text to ``(normalised token, raw)`` pairs with the census's own
    normaliser. The caller passes it in and this module never constructs it. The projection then
    reads text exactly as the run it describes read names. ``scale`` holds every choice that the
    unit kinds rest on. The build records it in ``meta.json``, so two projections built with
    different choices are told apart. ``outside``, where given, is a folder outside the project.
    The projection is built and held there, so a reader that writes nothing under the project (the
    agent interface) can still search. The function returns the projection as published.
    """
    if catalog.run_directory is None:
        message = "a project with no published run has nothing to search"
        raise LookupError(message)
    run_id = catalog.run_directory.name
    # The code checks that both are the project's own folders, with no link on the way, before it
    # removes or moves anything (``storage.layout.owned_path``).
    if outside is None:
        final = owned_path(catalog.project, projection_path(catalog.project, run_id))
        staging = owned_path(catalog.project, STAGING_DIR, f"search-{run_id}")
    else:
        final, staging = outside / run_id, outside / f"staging-{run_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    catalog.query(
        _CONTENT_TERMS_SQL.replace("@CONTENT_TERMS_PER_FILE@", str(CONTENT_TERMS_PER_FILE))
    )
    _copy(catalog, _TERMS_SQL, staging / "terms.parquet")
    terms = sql_path(staging / "terms.parquet")
    catalog.query(f"CREATE OR REPLACE TEMP VIEW terms AS SELECT * FROM {terms}")  # noqa: S608
    _copy(catalog, _VOCABULARY_SQL, staging / "vocabulary.parquet")
    _copy(catalog, _CLOSURE_SQL, staging / "closure.parquet")
    closure = sql_path(staging / "closure.parquet")
    catalog.query(f"CREATE OR REPLACE TEMP VIEW closure AS SELECT * FROM {closure}")  # noqa: S608
    _copy(catalog, _FOLDERS_SQL, staging / "folders.parquet")
    unit_counts = write_units(catalog, staging, parameters=scale)
    unit_documents = _write_unit_words(catalog, staging)

    _write_pieces(catalog, staging)

    notes = _note_rows(catalog.project, run_id, tokenise)
    if notes:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(pa.Table.from_pylist(notes), staging / "notes.parquet")

    versions = [
        row[0]
        for row in catalog.query(
            "SELECT DISTINCT normalizer_version FROM components ORDER BY 1"
        ).fetchall()
    ]
    meta = {
        "projection_version": PROJECTION_VERSION,
        "run_id": run_id,
        "run_normalizer_versions": versions,
        "query_normalizer_version": normalizer_version,
        "dictionary_hash": dictionary_hash,
        "notes": len(notes),
        "note_events": len(_searched_events(catalog.project)),
        "scale": {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in ((n, getattr(scale, n)) for n in scale.__dataclass_fields__)
        },
        "units": unit_counts,
        "unit_documents": unit_documents,
        "counts": {
            name: int(
                catalog.query(
                    f"SELECT count(*) FROM {sql_path(staging / f'{name}.parquet')}"  # noqa: S608
                ).fetchone()[0]  # type: ignore[index]
            )
            for name in ("terms", "vocabulary", "closure", "folders", "homes", "products")
        },
    }
    (staging / "meta.json").write_text(json.dumps(meta, sort_keys=True, indent=2), "utf-8")

    # Drop the views over the staging files before moving them. Windows refuses to rename a
    # directory while any handle inside it is open, and a lazy view is the likeliest holder.
    catalog.query("DROP VIEW IF EXISTS terms")
    catalog.query("DROP VIEW IF EXISTS closure")
    if outside is None:
        final = owned_path(catalog.project, final)
        staging = owned_path(catalog.project, staging)
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        shutil.rmtree(final)
    os.replace(staging, final)
    return Projection(path=final, run_id=run_id, meta=meta)


#: Projections built outside a project for a reader that writes nothing under it, keyed by the
#: project's projection folder. Each lives in a temporary folder that the engine removes when the
#: process ends.
_HELD: dict[str, Projection] = {}
_HELD_GUARD = threading.Lock()


def held_projection(project: Path, run_id: str) -> Projection | None:
    """Return the projection that this process built outside the project for one run, else None."""
    with _HELD_GUARD:
        return _HELD.get(str(projection_path(project, run_id)))


def hold_folder() -> Path:
    """Return a temporary folder outside every project.

    The engine removes the folder when the process ends.
    """
    folder = Path(tempfile.mkdtemp(prefix="ingence-search-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return folder


def hold_projection(project: Path, projection: Projection) -> None:
    """Keep a projection built outside the project for the rest of this process."""
    with _HELD_GUARD:
        _HELD[str(projection_path(project, projection.run_id))] = projection


def read_projection(project: Path, run_id: str) -> Projection | None:
    """Return the projection of one run where one is built at this version, else None.

    A projection built by another version counts as absent and is never read. The caller rebuilds.
    """
    path = projection_path(project, run_id)
    meta_file = path / "meta.json"
    if not meta_file.is_file():
        return None
    meta = json.loads(meta_file.read_text("utf-8"))
    if meta.get("projection_version") != PROJECTION_VERSION:
        return None
    return Projection(path=path, run_id=run_id, meta=meta)


def projection_is_current(
    projection: Projection,
    project: Path,
    *,
    dictionary_hash: str | None,
    scale: ScaleParameters,
) -> bool:
    """Return whether a built projection still describes what a search would build now.

    A projection is a function of the run, the Dictionary in force (a definition takes effect at
    once), the choices in ``scale`` and the Record's notes. The search rebuilds it where the
    Dictionary, the choices or the notes differ from what it was built with. So a word that a person
    just defined, a product term that a pack just added, or a note just written is searched without
    anyone asking for a rebuild.
    """
    meta = projection.meta
    if meta.get("dictionary_hash") != dictionary_hash:
        return False
    recorded = meta.get("scale", {})
    for name in scale.__dataclass_fields__:
        value = getattr(scale, name)
        if recorded.get(name) != (list(value) if isinstance(value, tuple) else value):
            return False
    return int(meta.get("note_events", 0)) == len(_searched_events(project))


# ---------------------------------------------------------------------------
# The vocabulary, in memory. It is small, and a suggestion and a correction read it.
# ---------------------------------------------------------------------------


class PoolVocabulary:
    """Every token that a pool uses, with its document frequency, and a near-spelling index.

    The near-spelling index is a deletion index, built as SymSpell builds it. The index files every
    token under each string one deletion away, and a query probes its own deletions. This finds
    every token within one edit in a few dictionary lookups, with no dependency. The index is built
    on first use, because most searches never need it.
    """

    def __init__(
        self,
        frequencies: dict[str, int],
        *,
        pieces: dict[str, tuple[str, ...]] | None = None,
        min_length: int = 5,
    ) -> None:
        self._frequencies = frequencies
        self._pieces = pieces or {}
        self._min_length = min_length
        self._deletions: dict[str, set[str]] | None = None

    @classmethod
    def from_projection(cls, catalog: Catalog, projection: Projection) -> PoolVocabulary:
        rows = catalog.query(
            f"SELECT token, entries FROM {projection.table('vocabulary')}"  # noqa: S608
        ).fetchall()
        held: dict[str, list[str]] = {}
        for piece, token in catalog.query(
            f"SELECT piece, token FROM {projection.table('pieces')} ORDER BY piece, token"  # noqa: S608
        ).fetchall():
            held.setdefault(str(piece), []).append(str(token))
        return cls(
            {str(token): int(entries) for token, entries in rows},
            pieces={piece: tuple(tokens) for piece, tokens in held.items()},
        )

    def containing(self, piece: str) -> tuple[str, ...]:
        """Return the tokens that hold ``piece`` between case or digit boundaries.

        The most used token comes first.
        """
        holders = self._pieces.get(piece.casefold(), ())
        return tuple(sorted(holders, key=lambda token: (-self._frequencies.get(token, 0), token)))

    def entries_with(self, token: str) -> int:
        return self._frequencies.get(token, 0)

    def beginning(self, text: str) -> bool:
        """Return whether a token of the pool begins with ``text``, except the text itself."""
        folded = text.casefold()
        return bool(folded) and any(
            token.startswith(folded) and token != folded for token in self._frequencies
        )

    def holding(self, text: str) -> bool:
        """Return whether a token of the pool holds ``text`` anywhere, except the text itself.

        This scans the vocabulary, which is the vocabulary's size and not the pool's. The scan runs
        only for a word that nothing holds as written, to decide whether to offer a near spelling.
        """
        folded = text.casefold()
        return bool(folded) and any(
            folded in token and token != folded for token in self._frequencies
        )

    def _index(self) -> dict[str, set[str]]:
        if self._deletions is None:
            index: dict[str, set[str]] = {}
            for token in self._frequencies:
                if len(token) < self._min_length or not token.isalpha():
                    continue
                index.setdefault(token, set()).add(token)
                for position in range(len(token)):
                    index.setdefault(token[:position] + token[position + 1 :], set()).add(token)
            self._deletions = index
        return self._deletions

    def near(self, token: str, *, distance: int = 1) -> tuple[str, ...]:
        """Return the tokens within one edit: a deletion, an insertion, a substitution or a swap.

        The deletion index nominates tokens and an exact edit distance verifies them, because two
        strings that share a one-deletion form can be two edits apart (``abxcd`` and ``abcyd``). The
        index covers distance one only. The method refuses a larger ``distance`` and does not
        half-serve it.
        """
        if distance != 1 or len(token) < self._min_length:
            return ()
        index = self._index()
        probes = {token, *(token[:i] + token[i + 1 :] for i in range(len(token)))}
        found: set[str] = set()
        for probe in probes:
            found |= index.get(probe, set())
        found.discard(token)
        found = {near for near in found if _edits(token, near) <= 1}
        # Most used first, then canonical order, because a person reads a suggestion list from the
        # top down.
        return tuple(sorted(found, key=lambda near: (-self._frequencies.get(near, 0), near)))


def _edits(left: str, right: str) -> int:
    """Optimal-string-alignment distance: insertions, deletions, substitutions, adjacent swaps."""
    if abs(len(left) - len(right)) > 1:
        return 2
    previous_previous: list[int] = []
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i, *([0] * len(right))]
        for j, b in enumerate(right, start=1):
            cost = 0 if a == b else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a == right[j - 2] and left[i - 2] == b:
                current[j] = min(current[j], previous_previous[j - 2] + 1)
        previous_previous, previous = previous, current
    return previous[-1]
