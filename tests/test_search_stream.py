"""A search's results as they are ready, and a search asked again at once.

Measured on a large project, three costs shape this design: the chips on the result cards took over
half of every search, the Dictionary and tokeniser were rebuilt for every Enter, and the first
search after a run waited for the index to be built. Held here, over the search tests' invented pool
(``kelt``, ``vorn``, ``zib``):

- the engine answers in two phases, the ranked cards and then their chips and reasons, and the two
  merged are exactly the one-shot answer;
- a search asked again is answered from what is held, and anything it read changing (a hide, the
  Record) asks the engine again;
- ``/api/search/stream`` sends one JSON per line, cards first, and merged it is ``/api/search``;
- the server warms the search as it starts, so the first search builds nothing;
- the words under the box answer while a search holds the one-search lock.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingence.api.routes import search as routes
from ingence.search import engine
from ingence.storage.catalog import open_catalog
from test_search import _project

if TYPE_CHECKING:
    from ingence.core.project import Project


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> Project:
    return _project(tmp_path_factory.mktemp("stream"))


def _parts(project: Project, text: str, **more: Any) -> list[dict[str, Any]]:
    with open_catalog(project.path) as catalog:
        return list(routes.search_stream(catalog, project.path, text=text, **more))


def test_the_cards_come_first_and_the_details_after_and_merged_they_are_the_answer(
    shared: Project,
) -> None:
    with open_catalog(shared.path) as catalog:
        normaliser, store = routes._reader(catalog, shared.path)
        options = {"normaliser": normaliser, "store": store, "dictionary_hash": store.fingerprint()}
        parts = list(engine.search_phases(catalog, shared.path, "kelt droplet", **options))
        whole = engine.search(catalog, shared.path, "kelt droplet", **options)
    assert [part["phase"] for part in parts] == ["cards", "detail"]
    cards, detail = parts
    assert cards["results"], "the pool's experiments match"
    assert all(card["tags"] is None and card["why"] == [] for card in cards["results"])
    # A card's title is there before its chips: the folder's name as a person reads it.
    assert any(card["title"] for card in cards["results"])
    assert len(detail["details"]) == len(cards["results"])
    assert [d["folder_id"] for d in detail["details"]] == [c["folder_id"] for c in cards["results"]]
    merged = {key: value for key, value in cards.items() if key != "phase"}
    for card, more in zip(merged["results"], detail["details"], strict=True):
        card["tags"], card["why"] = more["tags"], more["why"]
    # What is beside the results travels in the detail part.
    merged.update({key: detail[key] for key in ("co_occurring_words", "by_month")})
    # The phases built the projection; the one-shot search after them found it built.
    merged.pop("projection_built_now")
    whole.pop("projection_built_now")
    assert merged == whole


def test_a_query_nothing_reads_is_one_part(shared: Project) -> None:
    parts = _parts(shared, "")
    assert [part["phase"] for part in parts] == ["complete"]
    assert parts[0]["results"] == []


def test_a_search_asked_again_is_answered_at_once_until_something_it_read_changes(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    first = _parts(project, "kelt droplet")
    assert [part["phase"] for part in first] == ["cards", "detail"]
    again = _parts(project, "kelt droplet")
    assert [part["phase"] for part in again] == ["complete"]
    assert again[0]["held"] is True
    merged = {k: v for k, v in first[0].items() if k not in ("phase", "answered_ms")}
    for card, more in zip(merged["results"], first[1]["details"], strict=True):
        card["tags"], card["why"] = more["tags"], more["why"]
    # What is beside the results travels in the detail part.
    merged.update({key: first[1][key] for key in ("co_occurring_words", "by_month")})
    held = {k: v for k, v in again[0].items() if k not in ("phase", "held", "answered_ms")}
    # A held answer builds no index, and the note does not repeat on every repeat.
    assert held.pop("projection_built_now") is False
    merged.pop("projection_built_now")
    assert held == merged
    # A hide changes what the search shows: the next one asks the engine again.
    target = merged["results"][0]
    routes.hide(
        project.path,
        {
            "unit_id": target["folder_id"],
            "concept_keys": merged["concept_keys"],
            "actor": "tester",
            "typed": "kelt droplet",
        },
    )
    after = _parts(project, "kelt droplet")
    assert after[0]["phase"] == "cards"
    assert target["folder_id"] not in [card["folder_id"] for card in after[0]["results"]]
    # And a person's answer in the Record (a sense is a reader's input): asked again too.
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    append_event(
        project.path,
        kind=RecordEventKind.NOTE,
        actor="tester",
        occurred_at="2026-01-02T10:00:00Z",
        target=f"folder:{target['folder_id']}",
        against_run_id=first[0]["run_id"],
        payload={"note": "a note"},
    )
    assert _parts(project, "kelt droplet")[0]["phase"] == "cards"
    # A held answer is a copy: changing what was handed out never changes what is held.
    held_again = _parts(project, "kelt droplet")[0]
    held_again["results"].clear()
    assert _parts(project, "kelt droplet")[0]["results"]


def _serve(project: Project, **options: Any) -> Any:
    from ingence.api.server import DashboardServer

    server = DashboardServer(project.path, port=0, **options)
    server.start()
    return server


def _get(url: str) -> tuple[str, bytes]:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.headers.get("Content-Type", ""), response.read()


def test_the_stream_route_sends_the_cards_first_and_merged_it_is_the_search_route(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    server = _serve(project, warm_search=False)
    try:
        kind, body = _get(f"{server.url}api/search/stream?q=kelt%20droplet")
        assert kind.startswith("application/x-ndjson")
        lines = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
        assert [line["phase"] for line in lines] == ["cards", "detail"]
        assert lines[0]["answered_ms"] <= lines[1]["answered_ms"]
        _, whole = _get(f"{server.url}api/search?q=kelt%20droplet")
        answer = json.loads(whole)
        merged = {k: v for k, v in lines[0].items() if k not in ("phase", "answered_ms")}
        for card, more in zip(merged["results"], lines[1]["details"], strict=True):
            card["tags"], card["why"] = more["tags"], more["why"]
        # What is beside the results travels in the detail part.
        merged.update({key: lines[1][key] for key in ("co_occurring_words", "by_month")})
        # The whole answer is the stream's, held: the same but for the index built just now.
        assert answer.pop("projection_built_now") is False
        merged.pop("projection_built_now")
        assert merged == answer
        # Asked again: whole, at once, from what the server holds.
        _, again = _get(f"{server.url}api/search/stream?q=kelt%20droplet")
        held = [json.loads(line) for line in again.decode("utf-8").splitlines() if line.strip()]
        assert [line["phase"] for line in held] == ["complete"]
        assert held[0]["held"] is True
    finally:
        server.stop()


def test_the_server_makes_the_search_ready_as_it_starts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    # A run with no readouts has its views built when the Dashboard opens, the search projection
    # among them; opening it writes those derived views and nothing else.
    from ingence.readouts import read_readout, warming
    from ingence.search.projection import PROJECTIONS_DIR, SEARCH_DIR

    before = {path.relative_to(project.path) for path in project.path.rglob("*")}
    server = _serve(project)
    with open_catalog(project.path) as catalog:
        assert catalog.run_directory is not None
        warmed = catalog.run_directory.name
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and (
        warming(project.path) is not None or read_readout(project.path, warmed, "overview") is None
    ):
        time.sleep(0.1)
    server.stop()
    added = {path.relative_to(project.path) for path in project.path.rglob("*")} - before
    assert added and all(path.parts[0] in (PROJECTIONS_DIR, ".tmp") for path in added), added
    # One searched before (an index kept for an earlier run) has the run's index made current.
    (project.path / PROJECTIONS_DIR / SEARCH_DIR / "run_20250101T000000Z_000000000000").mkdir(
        parents=True
    )
    server = _serve(project)
    try:
        # The warming thread reads the Record, then builds the run's search projection.
        deadline = time.monotonic() + 60
        from ingence.search.projection import read_projection

        with open_catalog(project.path) as catalog:
            assert catalog.run_directory is not None
            run_id = catalog.run_directory.name
        while read_projection(project.path, run_id) is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert read_projection(project.path, run_id) is not None
        _, body = _get(f"{server.url}api/search?q=kelt")
        assert json.loads(body)["projection_built_now"] is False
    finally:
        server.stop()


def test_the_words_under_the_box_answer_while_a_search_holds_the_lock(shared: Project) -> None:
    _parts(shared, "kelt")  # the projection built
    lock = routes._one_at_a_time(shared.path)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        with open_catalog(shared.path) as catalog:
            started = time.monotonic()
            found = routes.suggest(catalog, shared.path, prefix="ke")
        assert time.monotonic() - started < 5
        assert any(word["token"].startswith("ke") for word in found["words"])
    finally:
        release.set()
        holder.join()


def test_a_unit_s_chips_are_read_once_and_are_the_same_when_held(tmp_path: Path) -> None:
    project = _project(tmp_path)
    engine._TAGS.clear()
    with open_catalog(project.path) as catalog:
        first = routes.search(catalog, project.path, text="kelt droplet")
    held = len(engine._TAGS)
    assert held, "the shown units' chips are held"
    engine._TAGS.clear()
    with open_catalog(project.path) as catalog:
        fresh = routes.search(catalog, project.path, text="kelt droplet repeat")
    with open_catalog(project.path) as catalog:
        # Every unit this search shows was shown before: read from what is held, the same.
        again = routes.search(catalog, project.path, text="droplet kelt repeat", limit=25)
    by_unit = {row["folder_id"]: row["tags"] for row in fresh["results"]}
    for row in again["results"]:
        if row["folder_id"] in by_unit:
            assert row["tags"] == by_unit[row["folder_id"]]
    shown = {row["folder_id"]: row["tags"] for row in first["results"]}
    common = set(shown) & set(by_unit)
    assert common
    for unit in common:
        assert shown[unit] == by_unit[unit]


# -- the search's phases and the held answers together -------------------------------------


def test_a_setting_committed_on_the_parameters_page_reaches_the_next_search(
    tmp_path: Path,
) -> None:
    """A setting committed on the Parameters page reaches the next search.

    The Parameters page commits to ``settings.dashboard.yaml``, which ``Project.open`` lays over
    ``settings.yaml`` and which writes no Record event. A search asked after a commit is asked of
    the engine again, read with the committed setting, never answered from what was held.
    """
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE

    project = _project(tmp_path)
    assert _parts(project, "kelt droplet")[0]["phase"] == "cards"
    assert _parts(project, "kelt droplet")[0]["held"] is True
    (project.path / DASHBOARD_SETTINGS_FILE).write_text(
        "dictionary:\n  key_value_pairs: 5\n", encoding="utf-8"
    )
    assert _parts(project, "kelt droplet")[0]["phase"] == "cards"
    with open_catalog(project.path) as catalog:
        normaliser, _ = routes._reader(catalog, project.path)
    assert normaliser._key_value_pairs == 5


def test_a_dictionary_file_and_a_newer_run_each_ask_the_engine_again(tmp_path: Path) -> None:
    """A file under the project's ``dictionary/`` and a newer run each ask the engine again.

    The held answers follow five kinds of change. A file under ``dictionary/`` and a newer run are
    two of them (the settings file is the test above).
    """
    from ingence.infer.run import run_inference

    project = _project(tmp_path)
    _parts(project, "kelt droplet")
    assert _parts(project, "kelt droplet")[0]["held"] is True
    dictionary = project.path / "dictionary"
    dictionary.mkdir(exist_ok=True)
    (dictionary / "a person's note.txt").write_text("kept beside the vocabulary\n", "utf-8")
    assert _parts(project, "kelt droplet")[0]["phase"] == "cards"
    assert _parts(project, "kelt droplet")[0]["held"] is True
    run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    again = _parts(project, "kelt droplet")
    assert again[0]["phase"] == "cards"
    assert again[0]["run_id"] == "run_20260101T000003Z_333333333333"


def test_what_only_the_second_phase_learns_is_said_by_the_stream_and_by_every_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The approved-group reasons are read in the second phase, and a failure to read them is said,
    never swallowed.

    It travels in the detail part. The whole answer, the engine's own merge and an answer held and
    given again all carry it.
    """
    project = _project(tmp_path)
    said = "the approved groups could not be read as reasons: kelt"
    monkeypatch.setattr(engine, "_approved_groups", lambda *_args: ({}, said))
    parts = _parts(project, "kelt droplet")
    assert said not in parts[0]["warnings"]
    assert parts[1]["warnings"] == [said]
    held = _parts(project, "kelt droplet")
    assert held[0]["held"] is True and said in held[0]["warnings"]
    with open_catalog(project.path) as catalog:
        whole = routes.search(catalog, project.path, text="kelt droplet repeat")
        normaliser, store = routes._reader(catalog, project.path)
        own = engine.search(catalog, project.path, "kelt vorn", normaliser=normaliser, store=store)
    assert said in whole["warnings"]
    assert own["warnings"].count(said) == 1


def test_the_held_chips_follow_the_quality_marks_in_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chip for a quality mark says it is one, and the chips are held.

    They are held under the marks in force, so a mark the Dictionary no longer declares is not shown
    from what was held.
    """
    project = _project(tmp_path)
    engine._TAGS.clear()

    def marks(found: dict[str, Any]) -> set[str]:
        return {
            chip["mark"]
            for row in found["results"]
            for chip in (row["tags"] or {}).get("chips", [])
            if "mark" in chip
        }

    with open_catalog(project.path) as catalog:
        normaliser, store = routes._reader(catalog, project.path)
        first = engine.search(
            catalog, project.path, "kelt analysis", normaliser=normaliser, store=store
        )
        assert "good" in marks(first), "the pool's folder named good carries the mark"
        monkeypatch.setattr(engine, "quality_marks", lambda _store: {})
        again = engine.search(
            catalog, project.path, "kelt analysis", normaliser=normaliser, store=store
        )
    assert marks(again) == set()


def test_what_matched_a_date_filter_is_said_on_that_search_s_cards_and_never_held(
    tmp_path: Path,
) -> None:
    """The date match depends on the query, and the chips it is added to are held for any search.

    It is added to the copy a search hands out, so a later search without a date says nothing about
    dates.
    """
    project = _project(tmp_path)
    engine._TAGS.clear()
    with open_catalog(project.path) as catalog:
        dated = routes.search(catalog, project.path, text="kelt after:2019-01-01")
        plain = routes.search(catalog, project.path, text="kelt")
    assert dated["results"] and all(
        "date_match" in (row["tags"] or {}) for row in dated["results"] if row["tags"]
    )
    shared_units = {row["folder_id"] for row in dated["results"]} & {
        row["folder_id"] for row in plain["results"]
    }
    assert shared_units, "the two searches show some of the same units, from held chips"
    assert not any("date_match" in (row["tags"] or {}) for row in plain["results"])


def test_a_search_asked_while_the_same_one_is_read_waits_and_takes_its_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Enter for the query being prefetched does not compute it again.

    The held answers are looked at again once the one-search lock is taken, so the second search is
    the first one's answer, held.
    """
    project = _project(tmp_path)
    _parts(project, "kelt")  # the projection built, so the timings below are the search's own
    slowed = engine._explain

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(1.0)
        return slowed(*args, **kwargs)

    monkeypatch.setattr(engine, "_explain", slow)
    found: dict[str, list[dict[str, Any]]] = {}
    first = threading.Thread(target=lambda: found.setdefault("first", _parts(project, "kelt vorn")))
    first.start()
    time.sleep(0.3)
    second = _parts(project, "kelt vorn")
    first.join()
    assert [part["phase"] for part in found["first"]] == ["cards", "detail"]
    assert [part["phase"] for part in second] == ["complete"]
    assert second[0]["held"] is True


def test_a_search_left_behind_by_a_newer_one_stops_before_its_details(
    tmp_path: Path,
) -> None:
    """A search the page abandoned does not keep reading its chips under the one-search lock.

    Reading the chips is the expensive half, and the newer search would wait for it. A search asked
    for another query stops after its cards, says so, holds nothing, and lets the newer one run.
    """
    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        older = routes.search_stream(catalog, project.path, text="kelt droplet")
        assert next(older)["phase"] == "cards"
        arrived = routes._arrivals(project.path)
        newer: dict[str, list[dict[str, Any]]] = {}
        thread = threading.Thread(target=lambda: newer.setdefault("parts", _parts(project, "vorn")))
        thread.start()
        deadline = time.monotonic() + 10
        while routes._arrivals(project.path) == arrived and time.monotonic() < deadline:
            time.sleep(0.01)
        rest = list(older)
    thread.join(timeout=60)
    assert [part["phase"] for part in rest] == ["stopped"]
    assert "newer search" in rest[0]["said"]
    assert [part["phase"] for part in newer["parts"]] == ["cards", "detail"]
    # Nothing was held for the search that stopped: asked again, the engine answers it.
    assert _parts(project, "kelt droplet")[0]["phase"] == "cards"
