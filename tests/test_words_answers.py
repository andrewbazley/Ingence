"""A person's answers about words, written in one request.

The behaviour: a person who clarifies their words makes one request (a word's meaning and the
answer about each spelling that may be the same word), and the engine writes each answer the way
the Dictionary reads it back. What is checked:

1. A batch with a meaning, a spelling that is the same thing, one that is a different thing of
   one kind and a pair that is unrelated writes the right four events in one request, and after
   the next load the head and the spellings that are the same thing bind to one term (a regression:
   two answers naming one term at one scope share a term id).
2. ``correcting`` and ``below`` pass through a ``targets`` batch, so a word the Dictionary places
   or contests is answered in one.
3. One Dictionary is loaded for a whole batch, however many words it answers.
4. ``split:<spelling>`` has a writer, and the census reads what it writes.
5. ``term:<word> below <folder>`` is read as the word, never as a word called "word below folder".
6. A person's "not a word" waits for Re-run like any other answer.
7. ``distinct:<a>+<b>`` is one target for a pair, written as a rejection.

Every word is invented (``fixtures/words_pool.py``).
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.words_pool import CENSUS, build_words_project
from ingence.api.routes import decide
from ingence.api.server import ANSWER_ROUTE, BATCH_SHARED, DashboardServer
from ingence.core.ids import Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.dictionary.case_reading import Reading, answered_cases
from ingence.dictionary.discover import CASE_WHOLE_CONSTRAINT
from ingence.dictionary.store import DictionaryStore
from ingence.record.log import read_events
from ingence.record.targets import distinct_named_by, distinct_target, target_kind
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.workspace.state import pending_rerun

if TYPE_CHECKING:
    from pathlib import Path

REBOUND = "run_20260101T000003Z_333333333333"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_words_project(tmp_path_factory.mktemp("words_answers"))


@pytest.fixture
def project(built: Path, tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(built, target)
    return target


def _post(server: DashboardServer, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        server.url.rstrip("/") + ANSWER_ROUTE, method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Ingence-Write", "1")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.load(error)
        finally:
            error.close()


def _rebind(project: Path, number: int = 3) -> None:
    run_census(
        Project.open(project),
        run_id=f"run_20260101T00000{number}Z_{str(number) * 12}",
        started_at=f"2026-01-01T00:00:0{number}Z",
        rebind=True,
    )


def _sent(events: list[Any], target: str) -> Any:
    (found,) = [event for event in events if event.target == target]
    return found


# -- 1. a word's meaning and each spelling's answer, in one request -------------------------------

HEAD = {"target": "term:quolt", "answer": "quolt_assay", "theme_kind": "reagent"}
BATCH = [
    HEAD,
    # The same thing as the head: an assignment to the term the head defines in this request.
    {"target": "term:quolts", "answer": "quolt_assay", "theme_kind": "reagent"},
    {"target": "term:quoltan", "answer": "quolt_assay", "theme_kind": "reagent"},
    # A different thing of one kind: its own meaning, on the head's kind.
    {"target": "term:fennowic", "answer": "fennowic_probe", "theme_kind": "reagent"},
    # Unrelated: never offered as one again.
    {"target": distinct_target("fennowik", "fennowic"), "word": "unrelated"},
]


def test_one_request_writes_a_meaning_and_each_spelling_s_answer(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, body = _post(server, {"actor": "Dana Whitfield", "decisions": BATCH})
    assert status == 201, body
    assert body["refused"] == []
    assert [item["target"] for item in body["written"]] == [item["target"] for item in BATCH]
    events = list(read_events(project))
    kinds = {event.target: event.kind for event in events}
    assert kinds["term:quolt"] is RecordEventKind.DEFINE
    # The spellings that are the same thing are assignments to the head's term, though that term
    # did not exist when the request began.
    assert kinds["term:quolts"] is RecordEventKind.ASSIGN
    assert kinds["term:quoltan"] is RecordEventKind.ASSIGN
    assert kinds["term:fennowic"] is RecordEventKind.DEFINE
    assert kinds["distinct:fennowic+fennowik"] is RecordEventKind.REJECT
    assert {event.actor for event in events} == {"Dana Whitfield"}
    pair = json.loads(_sent(events, "distinct:fennowic+fennowik").payload)
    assert pair["spellings"] == ["fennowic", "fennowik"] and pair["verdict"] == "unrelated"


def test_after_the_next_load_the_head_and_the_same_thing_spellings_bind_to_one_term(
    project: Path,
) -> None:
    """The regression: senses are built from each answer's own payload, with no resolution step,
    so a head defined in the same batch resolves for the assignments that name it."""
    with DashboardServer(project, port=0) as server:
        assert _post(server, {"actor": "Dana Whitfield", "decisions": BATCH})[0] == 201
    store = DictionaryStore.for_project(project)
    hits = {word: store.look_up(word) for word in ("quolt", "quolts", "quoltan", "fennowic")}
    assert all(hit is not None and not hit.ambiguous for hit in hits.values())
    senses = {word: hit.sense for word, hit in hits.items() if hit is not None}
    assert {senses[w].term for w in ("quolt", "quolts", "quoltan")} == {"quolt_assay"}
    assert len({store.term_id(senses[w]) for w in ("quolt", "quolts", "quoltan")}) == 1
    assert senses["fennowic"].term == "fennowic_probe"
    assert store.term_id(senses["fennowic"]) != store.term_id(senses["quolt"])
    # And the run binds them so: one term identifier across the three words' components.
    _rebind(project)
    with open_catalog(project) as catalog:
        bound = catalog.query(
            "SELECT lower(c.normalized_value), b.term_id, b.resolution_status "
            "FROM components c JOIN dictionary_bindings b ON b.component_id = c.component_id "
            "WHERE c.component_type = 'word' "
            "AND lower(c.normalized_value) IN ('quolt', 'quolts', 'quoltan')"
        ).fetchall()
    assert {word for word, _, _ in bound} == {"quolt", "quolts", "quoltan"}
    assert {status for _, _, status in bound} == {"resolved"}
    assert len({term_id for _, term_id, _ in bound}) == 1


def test_a_head_the_batch_names_a_kind_for_is_not_given_another_by_a_spelling(
    project: Path,
) -> None:
    """The assignment checks the kind against the term the batch itself defined."""
    batch = [
        HEAD,
        {"target": "term:quolts", "answer": "quolt_assay", "theme_kind": "technique"},
    ]
    with DashboardServer(project, port=0) as server:
        status, body = _post(server, {"actor": "Dana Whitfield", "decisions": batch})
    assert status == 201, body  # the head is written
    assert [item["target"] for item in body["written"]] == ["term:quolt"]
    (refused,) = body["refused"]
    assert refused["target"] == "term:quolts"
    assert "already a sense on the 'reagent' axis" in refused["error"]


# -- 2. correcting and below pass through the batch -----------------------------------------------


def test_the_batch_carries_correcting_and_below() -> None:
    assert {"correcting", "below"} <= set(BATCH_SHARED)


def test_a_batch_corrects_a_word_the_dictionary_places_and_one_it_contests(project: Path) -> None:
    body = {
        "actor": "Sam Okafor",
        "targets": ["term:confocal", "term:cluster"],
        "answer": "our_scope",
        "theme_kind": "instrument",
        "correcting": True,
    }
    with DashboardServer(project, port=0) as server:
        # Without saying it corrects, the old refusal stands for the word the Dictionary places.
        status, refused = _post(server, {**body, "correcting": False})
        assert status == 400
        assert "already places" in refused["error"]
        status, written = _post(server, body)
    assert status == 201, written
    assert written["refused"] == []
    events = list(read_events(project))
    assert json.loads(_sent(events, "term:confocal").payload)["answer"] == "our_scope"
    # The first correction defined the term; the contested word is assigned to it in the same batch.
    assert _sent(events, "term:confocal").kind is RecordEventKind.DEFINE
    assert _sent(events, "term:cluster").kind is RecordEventKind.ASSIGN
    store = DictionaryStore.for_project(project)
    cluster = store.look_up("cluster")
    assert cluster is not None and not cluster.ambiguous and cluster.sense.term == "our_scope"


def test_a_batch_keeps_a_meaning_below_a_folder(project: Path) -> None:
    body = {
        "actor": "Sam Okafor",
        "targets": ["term:confocal"],
        "answer": "our_scope",
        "theme_kind": "instrument",
        "correcting": True,
        "below": "Dana_Whitfield/20240308 confocal vorn",
    }
    with DashboardServer(project, port=0) as server:
        status, written = _post(server, body)
    assert status == 201, written
    (event,) = [e for e in read_events(project) if e.kind is RecordEventKind.DEFINE]
    assert event.target == "term:confocal below Dana_Whitfield/20240308 confocal vorn"
    assert event.scope.token == "folder:Dana_Whitfield/20240308 confocal vorn"


def test_a_folder_named_without_correcting_keeps_a_new_meaning_below_it_alone(
    project: Path,
) -> None:
    """``below`` is kept without ``correcting``, so a batch that names a folder writes the meaning
    for that folder only, not for the whole project."""
    folder = "Dana_Whitfield/20240301 quolt vorn"
    body = {
        "actor": "Sam Okafor",
        "targets": ["term:quolt"],
        "answer": "quolt_assay",
        "theme_kind": "reagent",
        "below": folder,
    }
    with DashboardServer(project, port=0) as server:
        status, written = _post(server, body)
    assert status == 201, written
    assert written["refused"] == []
    (event,) = [e for e in read_events(project) if e.kind is RecordEventKind.DEFINE]
    assert event.target == f"term:quolt below {folder}"
    assert event.scope.token == f"folder:{folder}"
    # The folder's own name and its two files' names carry the word; no other folder is counted.
    assert json.loads(event.payload)["as_shown"]["files"] == 3
    # Below that folder the word means it; anywhere else it still means nothing.
    store = DictionaryStore.for_project(project)
    assert store.look_up("quolt") is None
    here = store.look_up("quolt", scope=Scope(ScopeKind.FOLDER, folder))
    assert here is not None and here.sense.term == "quolt_assay"
    elsewhere = store.look_up(
        "quolt", scope=Scope(ScopeKind.FOLDER, "Dana_Whitfield/20240302 quolt vorn")
    )
    assert elsewhere is None


def test_a_folder_is_refused_where_it_cannot_hold_the_meaning(project: Path) -> None:
    one = {"actor": "Sam Okafor", "target": "term:quolt"}
    with DashboardServer(project, port=0) as server:
        # A folder the run does not hold.
        status, refused = _post(
            server,
            {**one, "answer": "a", "theme_kind": "reagent", "below": "Dana_Whitfield/nowhere"},
        )
        assert status == 400 and "holds no folder" in refused["error"]
        # A folder whose names do not carry the word.
        status, refused = _post(
            server,
            {
                **one,
                "answer": "a",
                "theme_kind": "reagent",
                "below": "Dana_Whitfield/20240305 fennowik vorn",
            },
        )
        assert status == 400 and "carries the word 'quolt' below" in refused["error"]
        # A folder is named for a meaning only: not for "not a word", and not for a note.
        for said in ({"word": "not_a_word"}, {"note": "ask Priya Raman"}):
            status, refused = _post(
                server, {**one, **said, "below": "Dana_Whitfield/20240301 quolt vorn"}
            )
            assert status == 400 and "only for a meaning" in refused["error"], said
    assert not [e for e in read_events(project) if e.target.startswith("term:quolt")]


# -- 3. one Dictionary for the whole batch --------------------------------------------------------


def test_one_dictionary_is_loaded_for_a_whole_batch(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads: list[int] = []
    real = DictionaryStore.for_project

    def counted(path: Path, **kwargs: Any) -> DictionaryStore:
        loads.append(1)
        return real(path, **kwargs)

    monkeypatch.setattr(DictionaryStore, "for_project", staticmethod(counted))
    words = ("quolt", "quolts", "quoltan", "fennowic", "fennowik", "vorn")
    with open_catalog(project) as catalog:
        context = decide.DecisionContext(catalog, project)
        written, refused = decide.decide_many(
            context,
            [
                decide.Decision(target=f"term:{w}", answer=f"{w}_meaning", theme_kind="reagent")
                for w in words
            ],
            actor="Dana Whitfield",
            occurred_at="2026-01-02T00:00:00Z",
        )
    assert refused == []
    assert len(written) == len(words)
    assert len(loads) == 1


# -- 4. split: has a writer, and the census reads what it writes ----------------------------------


def test_a_divided_spelling_is_answered_and_the_census_reads_the_answer(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, written = _post(
            server, {"actor": "Dana Whitfield", "target": "split:zibKelt", "word": "pieces"}
        )
    assert status == 201, written
    assert (written["kind"], written["target"]) == ("assign", "split:zibkelt")
    events = list(read_events(project))
    assert json.loads(_sent(events, "split:zibkelt").payload)["reading"] == "pieces"
    assert answered_cases(events) == {"zibkelt": Reading.PIECES}
    # The census takes the answer ahead of the counts: the counts alone read this spelling whole.
    _rebind(project)
    with open_catalog(project) as catalog:
        constraints = {
            row[0]
            for row in catalog.query(
                "SELECT coalesce(b.constraint, '') FROM components c "
                "JOIN dictionary_bindings b ON b.component_id = c.component_id "
                "JOIN entries e ON e.entry_id = c.entry_id "
                "WHERE e.name = 'zibKelt_vorn_01.nd2' AND lower(c.normalized_value) IN "
                "('zib', 'kelt')"
            ).fetchall()
        }
    assert CASE_WHOLE_CONSTRAINT not in constraints


def test_a_divided_spelling_read_whole_is_answered_in_its_other_words(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, _ = _post(
            server, {"actor": "Dana Whitfield", "target": "split:zibkelt", "word": "one word"}
        )
    assert status == 201
    assert answered_cases(read_events(project)) == {"zibkelt": Reading.WHOLE}


def test_a_spelling_no_name_divided_and_a_word_that_is_no_reading_are_refused(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        status, refused = _post(
            server, {"actor": "Dana Whitfield", "target": "split:nothingdivided", "word": "whole"}
        )
        assert status == 409, refused
        status, refused = _post(
            server, {"actor": "Dana Whitfield", "target": "split:zibKelt", "word": "maybe"}
        )
        assert status == 400
        assert "one word (whole) or as separate words (pieces)" in refused["error"]
    assert not [e for e in read_events(project) if e.target.startswith("split:")]


def test_the_page_can_ask_which_spellings_were_divided(project: Path) -> None:
    with open_catalog(project) as catalog:
        assert decide.glued_spellings(catalog) == {"zibkelt": "zibKelt"}


# -- 5. term:<word> below <folder> is the word ----------------------------------------------------


def test_a_word_kept_below_a_folder_is_read_as_the_word(project: Path) -> None:
    from ingence.textword.questions import _answers

    with DashboardServer(project, port=0) as server:
        status, _ = _post(
            server,
            {
                "actor": "Sam Okafor",
                "target": "term:confocal",
                "answer": "our_scope",
                "theme_kind": "instrument",
                "correcting": True,
                "below": "Dana_Whitfield",
            },
        )
    assert status == 201
    said = _answers(project)
    assert list(said) == ["confocal"]
    assert said["confocal"]["scope"] == {"kind": "folder", "value": "Dana_Whitfield"}


def test_the_whole_project_meaning_is_the_word_s_answer_over_a_folder_s(project: Path) -> None:
    from ingence.textword.questions import _answers

    common = {
        "actor": "Sam Okafor",
        "target": "term:confocal",
        "theme_kind": "instrument",
        "correcting": True,
    }
    with DashboardServer(project, port=0) as server:
        assert _post(server, {**common, "answer": "a", "below": "Dana_Whitfield"})[0] == 201
        assert _post(server, {**common, "answer": "b"})[0] == 201
        assert _post(server, {**common, "answer": "c", "below": "Dana_Whitfield"})[0] == 201
    said = _answers(project)
    assert list(said) == ["confocal"]
    assert said["confocal"]["answer"] == "b"
    assert said["confocal"]["scope"]["kind"] == "project"


# -- 6. a person's "not a word" waits like any other answer ---------------------------------------


def test_a_not_a_word_answer_counts_in_what_waits_for_rerun(project: Path) -> None:
    # Every domain section loads, so no pack waits for the pool's instrument files: nothing waits
    # before the answer.
    assert pending_rerun(project) is None
    with DashboardServer(project, port=0) as server:
        status, written = _post(
            server, {"actor": "Dana Whitfield", "target": "term:vorn", "word": "not_a_word"}
        )
    assert status == 201, written
    assert written["kind"] == "reject"
    waiting = pending_rerun(project)
    assert waiting is not None
    assert waiting.count == 1
    (change,) = [found for found in waiting.changes if found.kind == "answer"]
    assert change.rows == ('"vorn" is not a word',)
    assert change.spellings == frozenset({"vorn"})


# -- 7. an unrelated pair has one target ----------------------------------------------------------


def test_a_pair_has_one_target_whichever_spelling_it_starts_from() -> None:
    assert distinct_target("Fennowik", " fennowic ") == "distinct:fennowic+fennowik"
    assert distinct_target("fennowic", "fennowik") == distinct_target("fennowik", "fennowic")
    assert distinct_named_by("distinct:fennowic+fennowik") == ("fennowic", "fennowik")
    assert target_kind("distinct:fennowic+fennowik") == "distinct"
    # Unsorted, unpaired or half-named targets name nothing.
    assert distinct_named_by("distinct:fennowik+fennowic") is None
    # One pair, one target: capitals or spaces name a pair nothing reads, as the order does.
    assert distinct_named_by("distinct:Fennowic+fennowik") is None
    assert distinct_named_by("distinct: fennowic+fennowik") is None
    assert distinct_named_by("distinct:fennowic+fennowic") is None
    assert distinct_named_by("distinct:fennowic") is None
    assert distinct_named_by("distinct:+fennowic") is None
    assert distinct_named_by("term:fennowic+fennowik") is None
    for pair in (("a", "a"), ("", "b"), ("a+b", "c")):
        with pytest.raises(ValueError, match="two different spellings"):
            distinct_target(*pair)


def test_an_unrelated_pair_is_refused_when_it_is_not_one_the_run_could_have_offered(
    project: Path,
) -> None:
    with DashboardServer(project, port=0) as server:
        status, refused = _post(
            server,
            {"actor": "Dana Whitfield", "target": "distinct:nothing+nowhere", "word": "unrelated"},
        )
        assert status == 409, refused
        status, refused = _post(
            server,
            {"actor": "Dana Whitfield", "target": "distinct:fennowic+fennowik", "word": "same"},
        )
        assert status == 400
        assert "unrelated" in refused["error"]
        status, refused = _post(
            server,
            {
                "actor": "Dana Whitfield",
                "target": "distinct:fennowik+fennowic",
                "word": "unrelated",
            },
        )
        assert status == 400  # the pair is sorted: one pair, one target
        assert "write that pair as distinct:fennowic+fennowik" in refused["error"]
        # A pair in capitals, or padded, is refused the same way and written nowhere: the page
        # that offers a pair looks it up by its one target, so any other would never be found.
        for written in ("distinct:Fennowic+fennowik", "distinct: fennowic +fennowik"):
            status, refused = _post(
                server, {"actor": "Dana Whitfield", "target": written, "word": "unrelated"}
            )
            assert status == 400, written
            assert "write that pair as distinct:fennowic+fennowik" in refused["error"]
        status, refused = _post(
            server,
            {"actor": "Dana Whitfield", "target": "distinct:fennowic", "word": "unrelated"},
        )
        assert status == 400 and "two different spellings" in refused["error"]
    assert not [e for e in read_events(project) if e.target.startswith("distinct:")]


def test_the_run_the_pair_is_written_against_is_the_current_one(project: Path) -> None:
    with DashboardServer(project, port=0) as server:
        status, _ = _post(
            server,
            {
                "actor": "Dana Whitfield",
                "target": "distinct:fennowic+fennowik",
                "word": "unrelated",
            },
        )
    assert status == 201
    (event,) = [e for e in read_events(project) if e.target.startswith("distinct:")]
    assert event.against_run_id != CENSUS  # inference ran after the census: the current run
