"""A published run's themes: the engine adapter and its cached projection.

The units are the run's experiments, by the one definition that completeness shares
(:mod:`ingence.experiment_units`): every folder that a rule resolved as an experiment's folder,
each file belonging to the highest such folder above it, and nothing inside an installed
program's folders. A file under none belongs to no experiment and enters no theme.

A unit's document has each word of the experiment folder's own name at ``title_weight``, of each
folder inside it at ``subfolder_weight``, and of its files at ``ln(1 + files)``. A word that the
Dictionary bound is its sense (``§term``) unless the sense's kind is one of ``how_kinds``. A word
that it did not bind is read as its stems (:mod:`ingence.themes.words`), with unknown words kept
by the core's frequency and co-appearance rule. Where ``titles_or_acquisitions_only``, a word
must appear in a folder's name at or above an experiment or on an acquisition (the trend rule).

Each experiment has one year: the earliest date that a name gives for any of its files, else the
earliest credible time of any of them. With none, the experiment is undated. A person is the one
definition's (``person_of``: the folder ``person_level`` below each root). A collection is each
root, or the folder ``collection_level`` below it.

The projection is never a store. It is cached as one JSON file per run, keyed by the settings,
the Dictionary's fingerprint and this module's version, under ``projections/themes/<run_id>/``,
and rebuilt rather than repaired. Inference never reads it. A project that cannot be written is
still answered, uncached on disk.

The map of groups shows where each experiment lies in the plane (:mod:`ingence.themes.mapxy`).
It is cached beside the themes projection in a file of its own, ``map-<key>.json``. It is built
when first read and not when the themes are, because it costs more and only its own route reads
it.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import hashlib
import json
import math
import threading
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import write_atomically
from ingence.core.settings import ThemeSettings
from ingence.experiment_units import experiment_times, person_of, run_units
from ingence.storage.layout import owned_path
from ingence.themes.words import (
    SENSE_MARK,
    how_piece,
    in_series,
    series_prefixes,
    token_display,
    word_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = [
    "PROJECTION_VERSION",
    "RunUnits",
    "held_themes_map",
    "map_path",
    "project_dictionary",
    "projection_of_catalog",
    "projection_path",
    "read_run_units",
    "themes_map",
    "themes_projection",
]

#: Moves when the projection's fields or meaning change. An older file is never read.
#: Version 2 names senses by their canonical names and grows the LSA with the documents.
#: Version 3 keeps one final placement for every seed with the medoid shown and all seed pairs,
#: themes connected, the chance cutoffs, label stability, and vertices ordered by content so the
#: same words under other identifiers give the same themes.
#: Version 4 takes the experiments from the folders that the experiment rules' policy chooses
#: (``experiment_units.run_units``), so a person's folder holding several dated experiments is no
#: longer one of them. A file saved by version 3 would count the old set.
#: Version 5 says for each theme in how many other seeds it is not held and which other themes
#: those seeds split it with.
PROJECTION_VERSION = "5"
THEMES_DIR = "themes"
_PROJECTIONS = "projections"
#: The stage value a classification gives an acquisition, which the trend rule reads.
ACQUISITION = "acquisition"
#: An experiment's own words listed beside it (display length).
OWN_WORDS_LISTED = 6

_MEMORY: dict[str, dict[str, Any]] = {}
#: One lock per projection key, so one build of each runs at a time.
_BUILDING: dict[str, threading.Lock] = {}
_MEMORY_KEPT = 8
_LOCK = threading.Lock()


@dataclass
class RunUnits:
    """What the adapter read from one run, ready for the core and the timelines."""

    documents: dict[str, dict[str, float]] = field(default_factory=dict)
    words: dict[str, frozenset[str]] = field(default_factory=dict)
    senses: dict[str, str] = field(default_factory=dict)
    collections: dict[str, str] = field(default_factory=dict)
    people: dict[str, str | None] = field(default_factory=dict)
    folders: dict[str, str] = field(default_factory=dict)
    roots: dict[str, str] = field(default_factory=dict)
    dates: dict[str, str | None] = field(default_factory=dict)
    last_times: dict[str, str | None] = field(default_factory=dict)
    years: dict[str, int | None] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)


_UNITS_SQL = """
CREATE OR REPLACE TEMP TABLE theme_dirs AS
WITH RECURSIVE
live AS (
    SELECT entry_id, root_id, parent_entry_id, name, relative_path, depth, entry_type
    FROM entries WHERE NOT is_deleted AND entry_kind = 'filesystem'
),
dirs AS (SELECT entry_id, parent_entry_id FROM live WHERE entry_type = 'directory'),
chain(entry_id, ancestors) AS (
    SELECT entry_id, []::VARCHAR[] FROM dirs
    WHERE parent_entry_id IS NULL OR parent_entry_id NOT IN (SELECT entry_id FROM dirs)
    UNION ALL
    SELECT d.entry_id, list_append(c.ancestors, c.entry_id)
    FROM dirs d JOIN chain c ON d.parent_entry_id = c.entry_id
)
SELECT l.entry_id AS dir_id, l.root_id, l.name, l.relative_path, l.depth, c.ancestors,
       u.unit_id
FROM live l JOIN chain c ON c.entry_id = l.entry_id
LEFT JOIN theme_unit_map u ON u.dir_id = l.entry_id
"""

_FILES_SQL = """
CREATE OR REPLACE TEMP TABLE theme_files AS
SELECT e.entry_id AS file_id, d.unit_id, coalesce(e.size_bytes, 0) AS bytes,
       EXISTS (
           SELECT 1 FROM claims s WHERE s.subject = e.entry_id
           AND s.predicate LIKE 'classifies.%stage%' AND s.object_reference = ?
           AND s.resolution_status = 'resolved'
       ) AS acquisition
FROM entries e JOIN theme_dirs d ON d.dir_id = e.parent_entry_id
WHERE NOT e.is_deleted AND e.entry_type = 'file' AND d.unit_id IS NOT NULL
"""

_WORDS_SQL = """
SELECT c.entry_id, c.normalized_value, b.resolution_status, b.term, b.theme_kind
FROM components c LEFT JOIN dictionary_bindings b ON b.component_id = c.component_id
WHERE c.component_type IN ('word', 'indexed') AND c.entry_id IN (SELECT entry_id FROM {source})
ORDER BY c.entry_id, c.normalized_value, c.component_id
"""


#: Whether a projection built now may be written to its cache file. The agent interface
#: (``ingence mcp``, ``ingence export brief --out -``) reads with writes off, so it writes
#: nothing under the project. The projection is then held in memory only.
_CACHE_WRITES: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "themes_cache_writes", default=True
)


@contextlib.contextmanager
def without_cache_writes() -> Iterator[None]:
    """Read projections without writing a cache file (a context manager)."""
    token = _CACHE_WRITES.set(False)
    try:
        yield
    finally:
        _CACHE_WRITES.reset(token)


def cache_writes_allowed() -> bool:
    """Whether a projection built now may be written under the project (off for the agent
    interface, :func:`without_cache_writes`)."""
    return _CACHE_WRITES.get()


def projection_path(project: Path, run_id: str) -> Path:
    """Where a run's themes projections are cached."""
    return project / _PROJECTIONS / THEMES_DIR / run_id


def _key(settings: ThemeSettings, fingerprint: str) -> str:
    text = json.dumps(
        {
            "version": PROJECTION_VERSION,
            "settings": settings.model_dump(mode="json"),
            "dictionary": fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.blake2b(text.encode(), digest_size=10).hexdigest()


def _year(text: str | None) -> int | None:
    if not text or len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def _how_piece(store: Any, how: frozenset[str]) -> Any:
    """Whether a piece of an unbound word is a "how" word (``words.how_piece``)."""
    return how_piece(store, how)


def read_run_units(catalog: Catalog, settings: ThemeSettings, store: Any = None) -> RunUnits:
    """Read one run's experiments as documents, words, years, people and collections."""
    how = frozenset(settings.how_kinds)
    ignored = frozenset(word.casefold() for word in settings.ignored_words)
    is_how = _how_piece(store, how)
    # The one definition of an experiment (``ingence.experiment_units``, shared with
    # completeness): each folder's highest experiment folder, none inside a program's folders.
    shared = run_units(catalog)
    catalog.query("CREATE OR REPLACE TEMP TABLE theme_unit_map (dir_id VARCHAR, unit_id VARCHAR)")
    if shared.units:
        mapped = sorted(shared.units.items())
        catalog.query(
            "INSERT INTO theme_unit_map SELECT unnest(?::VARCHAR[]), unnest(?::VARCHAR[])",
            [[folder for folder, _ in mapped], [unit for _, unit in mapped]],
        )
    catalog.query(_UNITS_SQL)
    catalog.query(_FILES_SQL, [ACQUISITION])
    out = RunUnits()

    dirs = catalog.query(
        "SELECT dir_id, root_id, name, relative_path, depth, ancestors, unit_id FROM theme_dirs "
        "ORDER BY dir_id"
    ).fetchall()
    by_id = {row[0]: row for row in dirs}
    units = sorted({row[6] for row in dirs if row[6] is not None})
    for unit in units:
        _, root, _, path, depth, ancestors, _ = by_id[unit]
        out.folders[unit] = str(path)
        out.roots[unit] = str(root)
        chain = [by_id[a] for a in (ancestors or []) if a in by_id]

        def at_level(level: int, chain_of: list[Any] = chain, own_depth: int = depth) -> Any:
            if own_depth <= level:
                return None
            return next((row for row in chain_of if row[4] == level), None)

        level_row = at_level(settings.collection_level) if settings.collection_level else None
        out.collections[unit] = str(root) if level_row is None else f"{root}/{level_row[3]}"
        # The one definition of a person (``ingence.experiment_units.person_of``).
        out.people[unit] = person_of(str(path), settings.person_level)

    # Words of every entry a document reads: units, folders inside them, their files, and the
    # folders above them (titles, for the trend rule only).
    above = {a for unit in units for a in (by_id[unit][5] or [])}
    catalog.query("CREATE OR REPLACE TEMP TABLE theme_title_dirs (entry_id VARCHAR)")
    if above:
        catalog.query("INSERT INTO theme_title_dirs SELECT unnest(?::VARCHAR[])", [sorted(above)])
    raw_tokens: dict[str, list[str]] = defaultdict(list)

    canonical: dict[str, str] = {}

    def name_of(term: str) -> str:
        """A sense's canonical name as the Dictionary writes it (``TrackMate``, a pack's
        ``CryoEM``), else its term."""
        if term not in canonical:
            sense = store.sense_for_term(term) if store is not None else None
            canonical[term] = (
                str(sense.canonical) if sense is not None and sense.canonical else term
            )
        return canonical[term]

    def tokens_of(value: str, status: str | None, term: str | None, kind: str | None) -> list[str]:
        # The one word rule (``ingence.themes.words.word_tokens``), shared with free text.
        found = word_tokens(value, status, term, kind, how=how, is_how=is_how, ignored=ignored)
        for token in found:
            if token.startswith(SENSE_MARK):
                out.senses[token] = name_of(token.removeprefix(SENSE_MARK))
        return found

    def read(source: str) -> None:
        for entry, value, status, term, kind in catalog.query(
            _WORDS_SQL.format(source=source)
        ).fetchall():
            raw_tokens[str(entry)].extend(tokens_of(value, status, term, kind))

    catalog.query(
        "CREATE OR REPLACE TEMP TABLE theme_read AS "
        "SELECT dir_id AS entry_id FROM theme_dirs WHERE unit_id IS NOT NULL "
        "UNION SELECT entry_id FROM theme_title_dirs UNION SELECT file_id FROM theme_files"
    )
    read("theme_read")
    prefixes = series_prefixes(
        t for tokens in raw_tokens.values() for t in tokens if not t.startswith(SENSE_MARK)
    )
    entry_tokens = {
        entry: list(dict.fromkeys(in_series(t, prefixes) for t in tokens))
        for entry, tokens in raw_tokens.items()
    }

    files = catalog.query(
        "SELECT file_id, unit_id, bytes, acquisition FROM theme_files ORDER BY file_id"
    ).fetchall()
    allowed: set[str] = set()
    for unit in units:
        allowed.update(entry_tokens.get(unit, []))
        for ancestor in by_id[unit][5] or []:
            allowed.update(entry_tokens.get(ancestor, []))
    for file_id, _, _, acquisition in files:
        if acquisition:
            allowed.update(entry_tokens.get(file_id, []))

    def keep(token: str) -> bool:
        return token in allowed or not settings.titles_or_acquisitions_only

    documents: dict[str, dict[str, float]] = {unit: defaultdict(float) for unit in units}
    word_index: dict[str, set[str]] = {unit: set() for unit in units}
    for unit in units:
        for token in entry_tokens.get(unit, []):
            if keep(token):
                documents[unit][token] += settings.title_weight
                word_index[unit].add(token)
    for dir_id, _, _, _, _, _, unit in dirs:
        if unit is None or dir_id == unit:
            continue
        for token in entry_tokens.get(dir_id, []):
            if keep(token):
                documents[unit][token] += settings.subfolder_weight
                word_index[unit].add(token)
    on_files: dict[str, Counter[str]] = defaultdict(Counter)
    for file_id, unit, size, _ in files:
        out.files[unit] = out.files.get(unit, 0) + 1
        out.sizes[unit] = out.sizes.get(unit, 0) + int(size or 0)
        on_files[unit].update(t for t in entry_tokens.get(file_id, []) if keep(t))
    for unit, counted in on_files.items():
        for token, count in counted.items():
            documents[unit][token] += math.log1p(count)
            word_index[unit].add(token)
    out.documents = {
        unit: dict(sorted((t, round(w, 9)) for t, w in documents[unit].items())) for unit in units
    }
    out.words = {unit: frozenset(word_index[unit]) for unit in units}

    # One year per experiment: the shared rule of an experiment's date (``experiment_units``).
    for unit, (date, last) in experiment_times(catalog, shared).items():
        out.dates[unit] = date
        out.years[unit] = _year(date)
        out.last_times[unit] = last
    return out


def _own_words(document: dict[str, float], kept: dict[str, str]) -> list[str]:
    ranked = sorted(
        ((t, w) for t, w in document.items() if t in kept), key=lambda item: (-item[1], item[0])
    )
    return [kept[t] for t, _ in ranked[:OWN_WORDS_LISTED]]


def build_projection(
    units: RunUnits, run_id: str, settings: ThemeSettings, fingerprint: str
) -> dict[str, Any]:
    """The projection's JSON from what the adapter read: the core, then the timelines."""
    from ingence.themes.core import compute_themes, settings_digest
    from ingence.themes.time import substitutes, theme_timelines, undated, who_else, year_profile

    result = compute_themes(
        units.documents, collections=units.collections, senses=units.senses, settings=settings
    )
    display = {
        t: token_display(t, units.senses)
        for units_words in units.words.values()
        for t in units_words
    }
    placed = {u: a.theme_id for u, a in result.assignments.items() if a.theme_id is not None}
    labels = {s.theme_id: s.label for s in result.themes}
    broad_labels = {b.broad_id: b.label for b in result.broad}
    broad_of = {s.theme_id: s.broad_id for s in result.themes}
    timelines = {
        row["theme"]: row
        for row in theme_timelines(
            placed,
            labels,
            broad_labels,
            broad_of,
            units.years,
            units.words,
            display,
            units.people,
            settings,
        )
    }
    themes = []
    for summary in result.themes:
        row = dict(timelines.get(summary.theme_id, {}))
        row.update(
            {
                "theme": summary.theme_id,
                "label": summary.label,
                "words": list(summary.words),
                "senses": list(summary.senses),
                "broad_theme": summary.broad_id,
                "broad": broad_labels.get(summary.broad_id, ""),
                "experiments": summary.units,
                "stability": summary.stability,
                "core_experiments": summary.core_units,
                "label_stability": summary.label_stability,
                "label_support": list(summary.label_support),
                "split_seeds": summary.split_seeds,
                "split_with": [
                    {"theme": other, "seeds": seeds} for other, seeds in summary.split_with
                ],
                "examples": [units.folders[u] for u in summary.members[:3]],
            }
        )
        themes.append(row)
    kept_display = {t: d for t, d in display.items() if t in result.display}
    experiments = []
    for unit in sorted(units.documents):
        assignment = result.assignments[unit]
        experiments.append(
            {
                "id": unit,
                "folder": units.folders[unit],
                "root": units.roots[unit],
                "date": units.dates.get(unit),
                "last_file_time": units.last_times.get(unit),
                "year": units.years.get(unit),
                "files": units.files.get(unit, 0),
                "bytes": units.sizes.get(unit, 0),
                "theme": assignment.theme_id,
                "theme_label": assignment.label,
                "broad_theme": assignment.broad_id,
                "broad_label": assignment.broad_label,
                "unplaced_reason": assignment.unplaced_reason,
                "person": units.people.get(unit),
                "collection": units.collections.get(unit, ""),
                "words": _own_words(units.documents[unit], kept_display),
                # Every word read for it, for who else's query (no frequency filter).
                "index_words": sorted({display[t] for t in units.words.get(unit, frozenset())}),
            }
        )
    all_units = sorted(units.documents)
    return {
        "projection_version": PROJECTION_VERSION,
        "run_id": run_id,
        "settings_digest": settings_digest(settings),
        "dictionary_fingerprint": fingerprint,
        "summary": {
            "experiments": result.units,
            "placed": result.units - len(result.unplaced),
            "unplaced": len(result.unplaced),
            "undated": sum(1 for u in all_units if units.years.get(u) is None),
            "themes": len(result.themes),
            "broad_themes": len(result.broad),
            "seed_ami": result.seed_ami,
            "seed_nmi": result.seed_nmi,
            "seed_ari": result.seed_ari,
            "placement_seed": result.placement_seed,
            "medoid_seed": result.medoid_seed,
            "final_placement": settings.final_placement,
            "seeds": [
                {
                    "seed": s.seed,
                    "themes": s.themes,
                    "placed_experiments": s.placed_units,
                    "mean_ari": s.mean_ari,
                }
                for s in result.seeds
            ],
            "seed_pairs": [
                {"seed_a": p.seed_a, "seed_b": p.seed_b, "ari": p.ari} for p in result.seed_pairs
            ],
            "chance": None if result.chance is None else asdict(result.chance),
            "chance_null": settings.chance_null,
            "disconnected_themes": result.disconnected_themes,
            "disconnected_broad_themes": result.disconnected_broad,
            "label_method": result.label_method,
            "label_bootstrap": result.label_bootstrap,
            "resolution": result.resolution,
            "broad_resolution": result.broad_resolution,
            "vocabulary": result.vocabulary,
            "distinct_documents": result.vertices,
            "person_level": settings.person_level,
            "collections": [
                {"collection": c.collection, "experiments": c.units, "unit_weight": c.unit_weight}
                for c in result.collections
            ],
            "sweep": [
                {
                    "resolution": r.resolution,
                    "themes": r.themes,
                    "seed_ami": r.seed_ami,
                    "seed_nmi": r.seed_nmi,
                }
                for r in result.sweep
            ],
        },
        "experiments": experiments,
        "themes": themes,
        "broad": [
            {
                "broad_theme": b.broad_id,
                "label": b.label,
                "words": list(b.words),
                "themes": list(b.themes),
                "experiments": b.units,
                "label_stability": b.label_stability,
                "label_support": list(b.label_support),
            }
            for b in result.broad
        ],
        "story": year_profile(placed, labels, units.years, units.words, display, settings),
        "substitutes": substitutes(placed, units.years, units.words, display, settings),
        "who_else": who_else(
            all_units,
            units.years,
            units.words,
            display,
            frozenset(units.senses),
            units.people,
            units.folders,
            settings,
        ),
        "undated": undated(all_units, units.years),
        "unplaced": list(result.unplaced),
    }


#: What a project's Dictionary is composed from, beside the engine's own sections: its settings
#: (the packs it names), its Record (a person's answers, inactivations, accepted packs) and its
#: own vocabulary folder. Their files' sizes and times say cheaply whether it may have changed.
_FINGERPRINTS: dict[str, tuple[str, Any, str]] = {}


def _inputs_signature(project: Path) -> str:
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE
    from ingence.storage.layout import DICTIONARY_DIR, PROJECT_FILE, RECORD_DIR

    files = (project / PROJECT_FILE, project / SETTINGS_FILE, project / DASHBOARD_SETTINGS_FILE)
    folders = (project / RECORD_DIR, project / DICTIONARY_DIR)
    stated: list[str] = []
    for path in (
        *files,
        *(f for base in folders if base.is_dir() for f in sorted(base.rglob("*"))),
    ):
        with contextlib.suppress(OSError):
            if path.is_file():
                found = path.stat()
                relative = path.relative_to(project).as_posix()
                stated.append(f"{relative}:{found.st_size}:{found.st_mtime_ns}")
    return hashlib.blake2b("\n".join(stated).encode(), digest_size=12).hexdigest()


def _dictionary_of(project: Path, compose: Any) -> tuple[Any, str]:
    """The project's Dictionary and its fingerprint, composed again only when what it is made
    of has changed (composing it takes about a second, too long for every request)."""
    signature = _inputs_signature(project)
    place = str(project.resolve())
    with _LOCK:
        held = _FINGERPRINTS.get(place)
    if held is not None and held[0] == signature:
        return held[1], held[2]
    store = compose(project)
    fingerprint = str(store.fingerprint())
    with _LOCK:
        _FINGERPRINTS[place] = (signature, store, fingerprint)
    return store, fingerprint


def themes_projection(
    project: Path, run_id: str, *, settings: ThemeSettings | None = None
) -> dict[str, Any]:
    """The run's themes projection: from memory, from its cached file, or built and cached."""
    from ingence.dictionary.store import DictionaryStore

    if settings is None:
        from ingence.core.project import Project

        settings = Project.open(project).settings.themes
    store, fingerprint = _dictionary_of(project, DictionaryStore.for_project)
    key = _key(settings, fingerprint)
    path = projection_path(project, run_id) / f"{key}.json"
    memory_key = f"{project.resolve()}::{run_id}::{key}"
    with _LOCK:
        held = _MEMORY.get(memory_key)
        building = _BUILDING.setdefault(memory_key, threading.Lock())
    if held is not None:
        return held
    # One build per key at a time: a second request for the same projection waits for the
    # first's result rather than spending the whole build again.
    with building:
        with _LOCK:
            held = _MEMORY.get(memory_key)
        if held is not None:
            return held
        return _load_or_build(project, run_id, settings, store, fingerprint, path, memory_key)


def project_dictionary(project: Path) -> tuple[Any, str]:
    """The project's Dictionary store and its fingerprint, composed again only when what it is
    made of has changed (what the readouts are keyed by, ``ingence.readouts``)."""
    from ingence.dictionary.store import DictionaryStore

    return _dictionary_of(project, DictionaryStore.for_project)


def projection_of_catalog(
    catalog: Catalog, run_id: str, settings: ThemeSettings | None = None, *, keep: bool = False
) -> dict[str, Any]:
    """The projection of a run read through a catalog already open, built and, with ``keep``,
    kept where :func:`themes_projection` will find it.

    This is for a run that is staged and not yet published (the quality figures read it so),
    where the published projection cannot be asked for. The result is the one that
    :func:`themes_projection` gives for the published run, round-tripped through JSON as the
    cached file is. With ``keep``, the function writes the copy that inference built to the run's
    cache file under its key, and does not throw it away. So the readouts step after inference
    finds the themes made and builds them once. The file is a cache (``projections/``), so one
    written for a run that is then not published is read by nobody.
    """
    from ingence.dictionary.store import DictionaryStore

    if settings is None:
        from ingence.core.project import Project

        settings = Project.open(catalog.project).settings.themes
    store, fingerprint = _dictionary_of(catalog.project, DictionaryStore.for_project)
    units = read_run_units(catalog, settings, store)
    built = build_projection(units, run_id, settings, fingerprint)
    text = json.dumps(built, sort_keys=True, ensure_ascii=False)
    if keep:
        key = _key(settings, fingerprint)
        path = projection_path(catalog.project, run_id) / f"{key}.json"
        with contextlib.suppress(OSError):
            path = owned_path(catalog.project, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(path, text)
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _load_or_build(
    project: Path,
    run_id: str,
    settings: ThemeSettings,
    store: Any,
    fingerprint: str,
    path: Path,
    memory_key: str,
) -> dict[str, Any]:
    """The projection from its cached file, or built from the run and cached (held under the
    key's lock)."""
    from ingence.storage.catalog import open_catalog

    built: dict[str, Any] | None = None
    if path.is_file():
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("projection_version") == PROJECTION_VERSION:
                built = loaded
    if built is None:
        with open_catalog(project, run_id=run_id) as catalog:
            units = read_run_units(catalog, settings, store)
        built = build_projection(units, run_id, settings, fingerprint)
        text = json.dumps(built, sort_keys=True, ensure_ascii=False)
        # A cache, so where it cannot be written (a folder on the way is a link: ``owned_path``)
        # it is not, and the projection is answered from memory.
        if _CACHE_WRITES.get():
            with contextlib.suppress(OSError):
                path = owned_path(project, path)
                path.parent.mkdir(parents=True, exist_ok=True)
                write_atomically(path, text)
        built = json.loads(text)
    with _LOCK:
        _MEMORY[memory_key] = built
        while len(_MEMORY) > _MEMORY_KEPT:
            _MEMORY.pop(next(iter(_MEMORY)))
    return built


def map_path(project: Path, run_id: str, key: str) -> Path:
    """Where a run's map of groups is cached (``key`` is the themes projection's)."""
    from ingence.themes.mapxy import MAP_VERSION

    return projection_path(project, run_id) / f"map-{key}-{MAP_VERSION}.json"


def build_map(units: RunUnits, run_id: str, settings: ThemeSettings, seed: int) -> dict[str, Any]:
    """The map's JSON from what the adapter read: the themes' vectors for ``seed``, projected.

    ``seed`` is the themes projection's ``placement_seed``, so the map lies in the space the
    shown placement was drawn over. Arguments to the vectors are the ones the themes were drawn
    with (:func:`build_projection`), so a unit's row is the row its theme was found from.
    """
    from ingence.themes.core import document_vectors
    from ingence.themes.mapxy import made_with, map_positions

    found = document_vectors(
        units.documents,
        collections=units.collections,
        senses=units.senses,
        settings=settings,
        seed=seed,
    )
    placed = map_positions(found, sorted(units.documents))
    return {
        # What drew it: the map's settings and the libraries' versions. A file whose record is not
        # the one in force now is drawn again, never served.
        "made_with": made_with(),
        "projection_version": PROJECTION_VERSION,
        "run_id": run_id,
        "method": placed.method,
        "points": placed.points,
        "seed": placed.seed,
        "sharing": placed.sharing,
        "places": {unit: None if xy is None else list(xy) for unit, xy in placed.places.items()},
        "reasons": placed.reasons,
    }


def _map_keys(
    project: Path, run_id: str, settings: ThemeSettings | None
) -> tuple[ThemeSettings, Any, Path, dict[str, object], str]:
    """What a run's map is looked up by: the settings, the Dictionary's store, the map's cache
    file, what drew it, and its key in memory."""
    from ingence.dictionary.store import DictionaryStore
    from ingence.themes.mapxy import made_with

    if settings is None:
        from ingence.core.project import Project

        settings = Project.open(project).settings.themes
    store, fingerprint = _dictionary_of(project, DictionaryStore.for_project)
    key = _key(settings, fingerprint)
    path = map_path(project, run_id, key)
    drawn_with = made_with()
    drawn_key = hashlib.blake2b(
        json.dumps(drawn_with, sort_keys=True).encode(), digest_size=6
    ).hexdigest()
    memory_key = f"{project.resolve()}::{run_id}::{key}::map::{drawn_key}"
    return settings, store, path, drawn_with, memory_key


def _cached_map(path: Path, drawn_with: dict[str, object]) -> dict[str, Any] | None:
    """The map in its cache file, where the file is there and was drawn as it would be drawn now."""
    if not path.is_file():
        return None
    with contextlib.suppress(OSError, ValueError):
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if (
            loaded.get("made_with") == drawn_with
            and loaded.get("projection_version") == PROJECTION_VERSION
        ):
            return loaded
    return None


def held_themes_map(
    project: Path, run_id: str, *, settings: ThemeSettings | None = None
) -> dict[str, Any] | None:
    """The run's map where it is already held (in memory or in its cache file), never built.

    The answer to "is the layout ready?" without the cost of drawing it: ``None`` where the next
    :func:`themes_map` would build it (nothing is read from the run and nothing is written).
    """
    _, _, path, drawn_with, memory_key = _map_keys(project, run_id, settings)
    with _LOCK:
        held = _MEMORY.get(memory_key)
    if held is not None:
        return held
    loaded = _cached_map(path, drawn_with)
    if loaded is None:
        return None
    with _LOCK:
        _MEMORY[memory_key] = loaded
        while len(_MEMORY) > _MEMORY_KEPT:
            _MEMORY.pop(next(iter(_MEMORY)))
    return loaded


def themes_map(
    project: Path, run_id: str, *, settings: ThemeSettings | None = None
) -> dict[str, Any]:
    """The run's map of groups: from memory, from its cached file, or built and cached.

    Keyed as the themes projection is (the settings and the Dictionary's fingerprint) and by the
    map's own version; recomputed when next read after a run or a change either key sees.
    """
    settings, store, path, drawn_with, memory_key = _map_keys(project, run_id, settings)
    with _LOCK:
        held = _MEMORY.get(memory_key)
        building = _BUILDING.setdefault(memory_key, threading.Lock())
    if held is not None:
        return held
    with building:
        with _LOCK:
            held = _MEMORY.get(memory_key)
        if held is not None:
            return held
        built = _cached_map(path, drawn_with)
        if built is None:
            from ingence.storage.catalog import open_catalog

            shown = themes_projection(project, run_id, settings=settings)
            seed = int(shown["summary"]["placement_seed"])
            with open_catalog(project, run_id=run_id) as catalog:
                units = read_run_units(catalog, settings, store)
            made = build_map(units, run_id, settings, seed)
            text = json.dumps(made, sort_keys=True, ensure_ascii=False)
            if _CACHE_WRITES.get():
                with contextlib.suppress(OSError):
                    path = owned_path(project, path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    write_atomically(path, text)
            built = json.loads(text)
        with _LOCK:
            _MEMORY[memory_key] = built
            while len(_MEMORY) > _MEMORY_KEPT:
                _MEMORY.pop(next(iter(_MEMORY)))
    return built


def now_iso() -> str:
    """This moment, UTC, ISO 8601 with its zone (for a response's ``answered_at``)."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
