"""The search routes: ``/api/search`` and ``/api/search/suggest``, and three writes behind the
write guard: ``/api/search/build``, ``/api/search/hide`` and ``/api/search/unhide``.

The functions here are plain-dict functions and not handlers, as in ``groupings.py`` and
``pages.py``. The server calls them and sends the dict as JSON. ``server.py`` holds the wiring,
and the box is on the Explore page. Like ``review.py`` and unlike ``views.py``, this module
computes (a ranking), so it is its own module and ``views.py`` keeps its promise to compute
nothing.

A search writes nothing into a run and nothing into the Record. Its one side effect is building
the search projection of the run on first use. The projection is derived, deletable and outside
``runs/`` (``search/projection.py``). ``/api/search/build`` builds it deliberately, so that a
person can build it ahead of time on a large pool and see how long it took.

Hiding a result from similar searches is a write, but to the Dashboard's workspace and never to
the Record. It is a preference about what a person wants to see, and no inference reads it
(``search/hidden.py``). It goes through the server's write guard like the answer route, as one of
the workspace routes.

The normalizer is constructed the way the census constructs it (``scan/census.py``: the project's
Dictionary and ``settings.dictionary.glued_runs``), so a query is read exactly as the names were.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import duckdb

from ingence.core.project import Project
from ingence.dictionary.discover import names_read_with
from ingence.dictionary.normalize import Normaliser, reading_options
from ingence.dictionary.store import DictionaryStore
from ingence.search import engine
from ingence.search.hidden import DEFAULT_SIMILAR_AT
from ingence.search.hidden import hide as hide_result
from ingence.search.hidden import unhide as unhide_result
from ingence.search.projection import build_projection
from ingence.search.scale import scale_from_dictionary

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from ingence.storage.catalog import Catalog

__all__ = ["LIMIT", "build", "hide", "search", "search_stream", "suggest", "unhide", "warm"]

LIMIT = 25

#: How long the words under the box wait for a search index another request is building.
SUGGEST_WAIT = 30.0

#: Who a hidden result is hidden for: ``everyone`` on the project, with the person who hid it
#: recorded, or ``person``, the one who hid it. Changing this string changes the whole policy.
HIDDEN_SCOPE = "everyone"


#: One search at a time per project. A search builds the projection on first use and replaces it
#: when it is stale. Two requests on the threaded server would otherwise build into one staging
#: folder, or read files that another is replacing. A person searches one box at a time, so
#: waiting a moment costs nothing a page can see.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _one_at_a_time(project: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(project), threading.Lock())


#: The reader, held. Building the Dictionary and the tokenizer for every search took seconds the
#: first time on a large clone and a few tenths after, every time a person pressed Enter. What the
#: reader reads changes only with the run, the project's settings and dictionary files, and the
#: Record (a person's answers are senses), so the reader is held under those.
_READERS: OrderedDict[tuple[Any, ...], tuple[Normaliser, DictionaryStore, str]] = OrderedDict()
_READERS_HELD = 4
#: Recent answers, held. A search asked again (the back button, a second Enter, the page
#: reopened) is answered from here while nothing it read has changed: the reader's state, the
#: hides, and the question itself. This is a search engine's result cache, a few dozen deep.
_ANSWERS: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_ANSWERS_HELD = 32
_HELD_GUARD = threading.Lock()


def _stamp(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _reader_key(catalog: Catalog, project: Path) -> tuple[Any, ...]:
    """What the reader is a function of: the run, the settings, the dictionary files, the Record.

    The function stamps both settings files. ``Project.open`` lays ``settings.dashboard.yaml``,
    where the Parameters page commits, over ``settings.yaml``. A commit writes no Record event, so
    without its stamp a tokenizer setting or a pack named on that page would be read by nothing
    until the server restarted.
    """
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE
    from ingence.record.log import read_events
    from ingence.storage.layout import PROJECT_FILE

    run = catalog.run_directory.name if catalog.run_directory is not None else ""
    events = read_events(project)
    dictionary = project / "dictionary"
    files = (
        tuple(
            sorted((str(path), *_stamp(path)) for path in dictionary.rglob("*") if path.is_file())
        )
        if dictionary.is_dir()
        else ()
    )
    return (
        str(project.resolve()),
        run,
        _stamp(project / PROJECT_FILE),
        _stamp(project / "settings.yaml"),
        _stamp(project / DASHBOARD_SETTINGS_FILE),
        files,
        max((event.sequence for event in events), default=0),
        len(events),
    )


def _reader(catalog: Catalog, project: Path) -> tuple[Normaliser, DictionaryStore]:
    normaliser, store, _ = _held_reader(catalog, project)
    return normaliser, store


def _held_reader(catalog: Catalog, project: Path) -> tuple[Normaliser, DictionaryStore, str]:
    """The reader for this run and project state, built once and held; its key's digest last."""
    key = _reader_key(catalog, project)
    with _HELD_GUARD:
        held = _READERS.get(key)
        if held is not None:
            _READERS.move_to_end(key)
            return held
    normaliser, store = _build_reader(catalog, project)
    built = (normaliser, store, repr(hash(key)))
    with _HELD_GUARD:
        _READERS[key] = built
        while len(_READERS) > _READERS_HELD:
            _READERS.popitem(last=False)
    return built


def _build_reader(catalog: Catalog, project: Path) -> tuple[Normaliser, DictionaryStore]:
    """The tokenizer a query is read with: the census's, with the years and the day it read the
    run's names on (tokenizer 9), so a query's date is a date exactly where a name's was."""
    opened = Project.open(project)
    store = DictionaryStore.for_project(project)
    run = catalog.query("SELECT run_id FROM run").fetchone() if catalog.has_run else None
    years, read_on = names_read_with(project, str(run[0]) if run else None)
    normaliser = Normaliser(
        store, years=years, read_on=read_on, **reading_options(opened.settings.dictionary)
    )
    return normaliser, store


def search(
    catalog: Catalog,
    project: Path,
    *,
    text: str | None,
    order: Sequence[str] | None = None,
    limit: int = LIMIT,
    offset: int = 0,
    show_hidden: bool = False,
    actor: str | None = None,
    hidden_scope: str = HIDDEN_SCOPE,
    fallback_reach: int = engine.DEFAULT_FALLBACK_REACH,
) -> dict[str, Any]:
    """One page of results for ``?q=``; an empty query returns the reading of nothing.

    ``show_hidden`` (``?hidden=show``) includes results a person hid from similar searches,
    each marked, as ``answered=show`` brings answered rows back to a review page. ``actor`` is who
    is searching (the Dashboard's decider); it matters only where ``hidden_scope`` is ``person``.
    """
    merged: dict[str, Any] | None = None
    for part in search_stream(
        catalog,
        project,
        text=text,
        order=order,
        limit=limit,
        offset=offset,
        show_hidden=show_hidden,
        actor=actor,
        hidden_scope=hidden_scope,
        fallback_reach=fallback_reach,
        yield_to_newer=False,
    ):
        if merged is None:
            merged = {
                key: value
                for key, value in part.items()
                if key not in ("phase", "held", "answered_ms")
            }
        elif part.get("phase") == "detail":
            engine.merge_detail(merged, part)
    assert merged is not None
    return merged


def search_stream(
    catalog: Catalog,
    project: Path,
    *,
    text: str | None,
    order: Sequence[str] | None = None,
    limit: int = LIMIT,
    offset: int = 0,
    show_hidden: bool = False,
    actor: str | None = None,
    hidden_scope: str = HIDDEN_SCOPE,
    fallback_reach: int = engine.DEFAULT_FALLBACK_REACH,
    yield_to_newer: bool = True,
) -> Iterator[dict[str, Any]]:
    """One page of results in the engine's phases (``engine.search_phases``): the ranked cards,
    then their chips and reasons, each part a dict a page can show as it comes.

    A search that is asked again is answered at once from the recent answers held, as one part
    (``phase: complete``, ``held: true``), while nothing it read has changed. The function looks
    again once the one-search lock is taken, so an Enter for the query just prefetched takes that
    answer and does not read it again.

    A search left behind stops after its cards (``yield_to_newer``). Where a newer search for
    another query has arrived by then, the function does not read the details, which are most of
    the time. A ``stopped`` part says so, and nothing is held. A page that moved on has gone, and
    the server learns that only when it next writes. Without this, the abandoned search would read
    its chips under the lock while the newer one waited.
    """
    from ingence.search.hidden import HIDDEN_FILE

    started = time.perf_counter()
    normaliser, store, reader_digest = _held_reader(catalog, project)
    key = (
        reader_digest,
        text or "",
        tuple(order or ()),
        limit,
        offset,
        show_hidden,
        actor if hidden_scope == "person" else None,
        _stamp(project / HIDDEN_FILE),
        fallback_reach,
    )
    ticket = _arrive(project, key)
    held = _held_answer(key)
    if held is not None:
        yield _from_held(held, started)
        return
    merged: dict[str, Any] | None = None
    stopped = False
    with _one_at_a_time(project):
        held = _held_answer(key)
        if held is not None:
            yield _from_held(held, started)
            return
        # The search box's own settings: read where the engine is asked, under the reader's key,
        # which holds both settings files' stamps.
        chosen = Project.open(project).settings.search
        phases = engine.search_phases(
            catalog,
            project,
            text or "",
            normaliser=normaliser,
            store=store,
            order=order,
            limit=limit,
            offset=offset,
            dictionary_hash=store.fingerprint(),
            show_hidden=show_hidden,
            fallback_reach=fallback_reach,
            hidden_by=actor if hidden_scope == "person" else None,
            co_occurring_minimum=chosen.co_occurring_minimum,
            co_occurring_limit=chosen.co_occurring_limit,
            inside_min_length=chosen.inside_min_length,
        )
        try:
            for part in phases:
                answered = round(1000 * (time.perf_counter() - started), 1)
                if merged is None:
                    merged = _copied({k: v for k, v in part.items() if k != "phase"})
                elif part.get("phase") == "detail":
                    engine.merge_detail(merged, part)
                yield {**part, "answered_ms": answered}
                if (
                    part.get("phase") == "cards"
                    and yield_to_newer
                    and _left_behind(project, ticket, key)
                ):
                    stopped = True
                    yield {
                        "phase": "stopped",
                        "said": (
                            "a newer search was asked for, so what each of these holds was not read"
                        ),
                        "answered_ms": round(1000 * (time.perf_counter() - started), 1),
                    }
                    break
        finally:
            phases.close()
    if merged is not None and not stopped and limit <= HELD_LIMIT:
        with _HELD_GUARD:
            _ANSWERS[key] = merged
            while len(_ANSWERS) > _ANSWERS_HELD:
                _ANSWERS.popitem(last=False)


#: The largest page an answer is held for. The page asks for ``LIMIT`` cards, and an answer of
#: thousands, copied on every repeat, is not worth holding.
HELD_LIMIT = LIMIT

#: Each project's searches, counted as they arrive, with the latest one's question. This is how
#: a search learns that a newer one was asked for another query.
_ARRIVED: dict[str, tuple[int, tuple[Any, ...] | None]] = {}


def _arrive(project: Path, key: tuple[Any, ...]) -> int:
    with _HELD_GUARD:
        count = _ARRIVED.get(str(project), (0, None))[0] + 1
        _ARRIVED[str(project)] = (count, key)
        return count


def _arrivals(project: Path) -> int:
    """How many searches have arrived for a project since the server started."""
    with _HELD_GUARD:
        return _ARRIVED.get(str(project), (0, None))[0]


def _left_behind(project: Path, ticket: int, key: tuple[Any, ...]) -> bool:
    """Whether a newer search arrived after this one, asking something else. A newer search
    for the same question does not stop this one: it waits and takes this answer."""
    with _HELD_GUARD:
        count, latest = _ARRIVED.get(str(project), (0, None))
    return count != ticket and latest != key


def _held_answer(key: tuple[Any, ...]) -> dict[str, Any] | None:
    with _HELD_GUARD:
        held = _ANSWERS.get(key)
        if held is not None:
            _ANSWERS.move_to_end(key)
        return held


def _from_held(held: dict[str, Any], started: float) -> dict[str, Any]:
    """A held answer as one part. It built no index now: the search that made it may have."""
    return {
        **_copied(held),
        "projection_built_now": False,
        "phase": "complete",
        "held": True,
        "answered_ms": round(1000 * (time.perf_counter() - started), 1),
    }


def _copied(answer: dict[str, Any]) -> dict[str, Any]:
    """A deep enough copy that a caller changing its cards never changes what is held."""
    import copy

    return copy.deepcopy(answer)


def warm(catalog: Catalog, project: Path) -> dict[str, Any]:
    """Read the Dictionary into memory now, and keep a search index current where one is kept, so
    the first search does not wait. The server calls this as it starts.

    Opening the Dashboard writes nothing that a person did not ask for. The function rebuilds the
    index only in a project that has one already (somebody searched, in an earlier run). A project
    that was never searched builds the index on its first search.
    """
    from ingence.search.projection import PROJECTIONS_DIR, SEARCH_DIR

    started = time.perf_counter()
    normaliser, store, _ = _held_reader(catalog, project)
    built = False
    searched = project / PROJECTIONS_DIR / SEARCH_DIR
    kept = searched.is_dir() and any(path.is_dir() for path in searched.iterdir())
    if catalog.run_directory is not None and kept:
        with _one_at_a_time(project):
            built = _current_projection(catalog, project, normaliser, store)
    return {"built": built, "seconds": round(time.perf_counter() - started, 3)}


def _current_projection(
    catalog: Catalog, project: Path, normaliser: Normaliser, store: DictionaryStore
) -> bool:
    """Build the run's search projection where it is missing or stale; whether it built one.
    The caller holds the one-search lock."""
    from ingence.search.projection import projection_is_current, read_projection

    if catalog.run_directory is None:
        return False
    scale = scale_from_dictionary(store)
    found = read_projection(project, catalog.run_directory.name)
    if found is not None and projection_is_current(
        found, project, dictionary_hash=store.fingerprint(), scale=scale
    ):
        return False
    build_projection(
        catalog,
        tokenise=lambda words: [(a.normalised, a.text) for a in normaliser.atoms(words)],
        normalizer_version=normaliser.version,
        dictionary_hash=store.fingerprint(),
        scale=scale,
    )
    return True


def _concepts(body: dict[str, Any]) -> frozenset[str]:
    keys = body.get("concept_keys")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        message = "a hide names the search's concept_keys, as the search response gave them"
        raise ValueError(message)
    return frozenset(keys)


def hide(project: Path, body: dict[str, Any]) -> dict[str, Any]:
    """``POST /api/search/hide``: ``{unit_id, concept_keys, actor, typed}``."""
    hidden = hide_result(
        project,
        str(body.get("unit_id") or ""),
        _concepts(body),
        actor=str(body.get("actor") or ""),
        typed=str(body.get("typed") or ""),
    )
    return {"hidden": hidden.unit_id, "from": sorted(hidden.concepts), "at": hidden.at}


def unhide(project: Path, body: dict[str, Any]) -> dict[str, Any]:
    """``POST /api/search/unhide``: ``{unit_id, concept_keys, actor}``.

    This brings a result back for searches like this one. Every hide of the unit that still
    stands, and that a search with these concepts would honor, is undone, each by its own
    concepts. So a result hidden from ``FRAP kelt`` comes back when asked from ``kelt FRAP V2``.
    """
    from ingence.search.hidden import read_hidden, similarity

    unit_id = str(body.get("unit_id") or "")
    asked = _concepts(body)
    actor = str(body.get("actor") or "").strip()
    if not actor:
        message = "bringing a result back names who did it"
        raise ValueError(message)
    undone = [
        item
        for item in read_hidden(project)
        if item.unit_id == unit_id and similarity(asked, item.concepts) >= DEFAULT_SIMILAR_AT
    ]
    for item in undone:
        unhide_result(project, unit_id, item.concepts, actor=actor)
    return {"shown_again": unit_id, "hides_undone": len(undone)}


def suggest(
    catalog: Catalog, project: Path, *, prefix: str | None, limit: int = 10
) -> dict[str, Any]:
    """The drop-down under the box: the pool's words and the Dictionary's senses for a prefix.

    This function does not take the one-search lock. It reads the published vocabulary alone, and
    the page asks at every pause in typing, so it must not wait for a search (or the prefetch of
    one) to finish. Where the index is not built yet, typing in the box is asking to search, so
    the function builds the index now, as a first search builds it. It waits for an index that is
    being built already.
    """
    normaliser, store, _ = _held_reader(catalog, project)
    if catalog.run_directory is not None:
        from ingence.search.projection import read_projection

        if read_projection(project, catalog.run_directory.name) is None:
            lock = _one_at_a_time(project)
            if lock.acquire(timeout=SUGGEST_WAIT):
                try:
                    _current_projection(catalog, project, normaliser, store)
                finally:
                    lock.release()
    try:
        return engine.suggest(catalog, project, prefix or "", store=store, limit=limit)
    except (OSError, duckdb.Error):
        return {"prefix": prefix or "", "words": [], "note": "the search index is being rebuilt"}


def build(catalog: Catalog, project: Path) -> dict[str, Any]:
    """Build (or rebuild) the projection of the catalog's run now, and say how long it took.

    ``POST /api/search/build``, behind the write guard: it writes the derived projection beside
    the run, which a search otherwise does on first use.
    """
    with _one_at_a_time(project):
        return _build(catalog, project)


def _build(catalog: Catalog, project: Path) -> dict[str, Any]:
    normaliser, store = _reader(catalog, project)
    started = time.perf_counter()
    projection = build_projection(
        catalog,
        tokenise=lambda words: [(a.normalised, a.text) for a in normaliser.atoms(words)],
        normalizer_version=normaliser.version,
        dictionary_hash=store.fingerprint(),
        scale=scale_from_dictionary(store),
    )
    return {
        "run_id": projection.run_id,
        "seconds": round(time.perf_counter() - started, 2),
        "meta": projection.meta,
    }
