"""Bring the words you taught in: a person's answers carried into another project.

What a person taught the engine about their words is theirs, and travels. A new
project can be asked to bring in the words from the earlier projects the landing page lists, and
the same answers travel between computers as one file of Record events. What is checked:

1. The answers that travel are the in-force answers about words for the whole project (what a word
   means, how a divided spelling reads, spellings that are unrelated, senses and spellings turned
   off); one kept below a folder is counted and left out, and
   a note decides nothing.
2. They are written in the new project's Record as imported events: authored by the person
   bringing them in, saying which project they came from, with the original author kept; they win
   over the core as a person's answers do; and a target already answered keeps its answer.
3. A file of Record events (``teach export-answers --as-record``) reads back through the command
   line's ``record import`` and the Dashboard's Import, the same events.
4. The landing page offers the earlier projects that hold such answers, and offers nothing where
   none does; a project asked to bring words in keeps them until its first reading has made a run.

Every word is invented (``fixtures/words_pool.py``).
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from fixtures.words_pool import build_words_project
from ingence.api.recent import remember_project
from ingence.api.routes import landing
from ingence.api.server import ANSWER_ROUTE, LANDING_ROUTES, WRITE_ROUTES, DashboardServer
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.dictionary.store import DictionaryStore
from ingence.record.imported import (
    RECORD_FORMAT,
    ImportRefusedError,
    carried_words,
    imported_from,
    read_record_file,
    record_file,
)
from ingence.record.log import read_events
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

BATCH = [
    {"target": "term:quolt", "answer": "quolt_assay", "theme_kind": "reagent"},
    {"target": "term:quolts", "answer": "quolt_assay", "theme_kind": "reagent"},
    # A meaning for a word the Dictionary places, kept below one folder: it stays behind.
    {
        "target": "term:confocal",
        "answer": "our_scope",
        "theme_kind": "instrument",
        "correcting": True,
        "below": "Dana_Whitfield",
    },
    {"target": "split:zibkelt", "word": "pieces"},
    {"target": "distinct:fennowic+fennowik", "word": "unrelated"},
    # A note decides nothing, so it is not an answer to carry.
    {"target": "term:vorn", "note": "ask Priya Raman what this is"},
]
#: What travels of the above: five answers, and one stays behind.
CARRIED = {
    "term:quolt",
    "term:quolts",
    "split:zibkelt",
    "distinct:fennowic+fennowik",
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_words_project(tmp_path_factory.mktemp("carry_built"))


@pytest.fixture
def taught(built: Path, tmp_path: Path) -> Path:
    """A project in which a person taught some words, as the Words section writes them."""
    target = tmp_path / "taught"
    shutil.copytree(built, target)
    with DashboardServer(target, port=0) as server:
        status, body = _post(server, {"actor": "Dana Whitfield", "decisions": BATCH})
    assert status == 201, body
    assert body["refused"] == []
    return target


@pytest.fixture
def fresh(built: Path, tmp_path: Path) -> Path:
    """A project that has been read and in which nobody has taught anything."""
    target = tmp_path / "fresh"
    shutil.copytree(built, target)
    return target


def _post(
    server: DashboardServer, body: dict[str, Any], route: str = ANSWER_ROUTE
) -> tuple[int, Any]:
    request = urllib.request.Request(
        server.url.rstrip("/") + route, method="POST", data=json.dumps(body).encode()
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


def _export(project: Path, tmp_path: Path) -> Path:
    out = tmp_path / "words.json"
    done = CliRunner().invoke(
        app, ["teach", "export-answers", "-p", str(project), "--out", str(out), "--as-record"]
    )
    assert done.exit_code == 0, done.output
    return out


def _import(project: Path, source: Path, *extra: str) -> str:
    done = CliRunner().invoke(
        app, ["record", "import", str(source), "-p", str(project), "--actor", "Sam Okafor", *extra]
    )
    assert done.exit_code == 0, done.output
    return done.output


# -- 1. what travels --------------------------------------------------------------------------


def test_the_answers_about_words_for_the_whole_project_travel_and_the_rest_stay(
    taught: Path,
) -> None:
    words = carried_words(taught, source="Lab notebook")
    assert {answer.target for answer in words.answers} == CARRIED
    assert [answer.target for answer in words.answers] == sorted(CARRIED)  # canonical order
    assert words.left_out == {"kept below one folder": 1}
    assert {answer.source for answer in words.answers} == {"Lab notebook"}
    by_target = {answer.target: answer for answer in words.answers}
    assert by_target["term:quolt"].kind == "define" and by_target["term:quolts"].kind == "assign"
    assert by_target["distinct:fennowic+fennowik"].kind == "reject"
    assert {answer.actor for answer in words.answers} == {"Dana Whitfield"}


def test_a_question_answered_with_a_meaning_is_not_carried(taught: Path) -> None:
    """Only what can be withdrawn one at a time travels, and a question's answer cannot."""
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.record.log import append_event
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(taught)
    assert pointer is not None
    append_event(
        taught,
        kind=RecordEventKind.DEFINE,
        actor="Dana Whitfield",
        occurred_at="2026-01-03T00:00:00Z",
        target="question:unbound_word_in_a_kinds_place:fennowic",
        against_run_id=pointer.run_id,
        payload={
            "raised": "fennowic",
            "answer": "fennowic_probe",
            "theme_kind": "reagent",
            "rule_id": "unbound_word_in_a_kinds_place",
        },
        scope=PROJECT_SCOPE,
    )
    assert {answer.target for answer in carried_words(taught).answers} == CARRIED


def test_a_project_nobody_taught_anything_carries_nothing(fresh: Path) -> None:
    assert len(carried_words(fresh)) == 0


def test_the_file_of_events_is_the_same_bytes_twice_and_refuses_what_it_is_not(
    taught: Path,
) -> None:
    words = carried_words(taught, source="Lab notebook")
    first = json.dumps(record_file(words, source="Lab notebook"), sort_keys=True)
    assert first == json.dumps(record_file(words, source="Lab notebook"), sort_keys=True)
    document = record_file(words, source="Lab notebook")
    assert document["format"] == RECORD_FORMAT and document["left_out"] == {
        "kept below one folder": 1
    }
    again = read_record_file(json.dumps(document))
    assert [answer.as_json() for answer in again.answers] == document["events"]
    for broken in (
        "not json",
        json.dumps({"format": "a sheet", "version": 1, "events": []}),
        json.dumps({**document, "version": 9}),
        json.dumps({**document, "events": "none"}),
        json.dumps({**document, "events": [{"target": "claim:abc", "kind": "confirm"}]}),
        json.dumps({**document, "events": [{"target": "term:quolt", "kind": "bogus"}]}),
        json.dumps({**document, "events": [{"target": "term:quolt", "kind": "note"}]}),
    ):
        with pytest.raises(ImportRefusedError):
            read_record_file(broken)


# -- 2/3. written as imported events, through the command line ------------------------------------


def test_the_words_round_trip_through_a_file_as_imported_events(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    out = _export(taught, tmp_path)
    document = json.loads(out.read_text())
    assert [item["target"] for item in document["events"]] == sorted(CARRIED)
    said = _import(fresh, out)
    assert "written 4" in said and "already answered 0" in said
    events = {event.target: event for event in read_events(fresh) if event.target in CARRIED}
    assert set(events) == CARRIED
    for event in events.values():
        # The person bringing them in is the author; the project they came from is the source.
        assert event.actor == "Sam Okafor"
        payload = json.loads(event.payload)
        assert payload["carried_from"] == {
            "actor": "Dana Whitfield",
            "occurred_at": payload["carried_from"]["occurred_at"],
        }
        assert payload["source"]["imported_from"] == "taught"
        assert payload["source"]["carried"] is True
        assert imported_from(payload["source"]) == "taught"
    assert events["term:quolt"].kind is RecordEventKind.DEFINE
    assert events["distinct:fennowic+fennowik"].kind is RecordEventKind.REJECT
    # Nothing of the source project's own run travels in the payload.
    assert "as_shown" not in json.loads(events["term:quolt"].payload)


def _define(word: str, term: str, kind: str, verb: str = "define") -> dict[str, Any]:
    return {
        "target": f"term:{word}",
        "kind": verb,
        "actor": "Dana Whitfield",
        "occurred_at": "2026-01-02T00:00:00Z",
        "payload": {"raised": word, "answer": term, "theme_kind": kind},
    }


def _file(tmp_path: Path, *events: dict[str, Any]) -> Path:
    path = tmp_path / "handmade.json"
    path.write_text(
        json.dumps(
            {"format": RECORD_FORMAT, "version": 1, "from": "an earlier project", "events": events}
        )
    )
    return path


def test_a_carried_meaning_naming_a_term_held_on_another_kind_is_refused_not_written(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    """The page refuses a term that is already another kind of thing; a carried answer must not
    make it two at once. The rest of the file is written beside the refusals."""
    with DashboardServer(fresh, port=0) as server:
        status, body = _post(
            server,
            {
                "actor": "Priya Raman",
                "target": "term:vorn",
                "answer": "quolt_assay",
                "theme_kind": "instrument",
            },
        )
        assert status == 201, body
    said = " ".join(_import(fresh, _export(taught, tmp_path)).split())  # the console wraps lines
    assert "written 2" in said and "refused 2" in said
    assert "refused term:quolt: 'quolt_assay' is already a sense on the 'instrument' axis" in said
    assert "refused term:quolts: " in said
    written = {e.target for e in read_events(fresh) if e.actor == "Sam Okafor"}
    assert written == {"split:zibkelt", "distinct:fennowic+fennowik"}
    store = DictionaryStore.for_project(fresh)
    assert store.look_up("quolt") is None
    hit = store.look_up("vorn")
    assert hit is not None and hit.sense.theme_kind == "instrument"


def test_two_carried_meanings_in_one_file_cannot_put_a_term_on_two_kinds(
    fresh: Path, tmp_path: Path
) -> None:
    said = " ".join(
        _import(
            fresh,
            _file(
                tmp_path,
                _define("quolt", "shared_name", "reagent"),
                _define("quolts", "shared_name", "instrument", "assign"),
                _define("quoltan", "shared_name", "reagent", "assign"),
            ),
        ).split()
    )
    assert "written 2" in said and "refused 1" in said
    assert "refused term:quolts: 'shared_name' is already a sense on the 'reagent' axis" in said
    assert {e.target for e in read_events(fresh)} == {"term:quolt", "term:quoltan"}


def test_a_carried_meaning_this_project_turned_off_is_refused(fresh: Path, tmp_path: Path) -> None:
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(fresh)
    assert pointer is not None
    # A person here inactivated the sense, as the Dictionary section does.
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.record.log import append_event

    append_event(
        fresh,
        kind=RecordEventKind.REJECT,
        actor="Priya Raman",
        occurred_at="2026-01-03T00:00:00Z",
        target="sense:quolt_assay",
        against_run_id=pointer.run_id,
        payload={},
        scope=PROJECT_SCOPE,
    )
    said = " ".join(
        _import(fresh, _file(tmp_path, _define("quolt", "quolt_assay", "reagent"))).split()
    )
    assert "written 0" in said and "refused 1" in said
    assert "quolt_assay is inactive since 2026-01-03" in said
    assert not [e for e in read_events(fresh) if e.target == "term:quolt"]


def test_a_carried_answer_wins_over_the_core_and_is_in_the_run_that_reads_it(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    # The core reads "cluster" as a workstation or as clustering, tied; the source's person said
    # what it means there. (The source here only taught its own words; teach one the core holds.)
    with DashboardServer(taught, port=0) as server:
        status, _ = _post(
            server,
            {
                "actor": "Dana Whitfield",
                "target": "term:cluster",
                "answer": "cluster_of_cells",
                "theme_kind": "subject",
                "correcting": True,
            },
        )
        assert status == 201
    _import(fresh, _export(taught, tmp_path))
    hit = DictionaryStore.for_project(fresh).look_up("cluster")
    assert hit is not None and not hit.ambiguous
    assert (hit.sense.term, hit.sense.origin.value) == ("cluster_of_cells", "user")
    # The next run reads the Record at its watermark: the carried answers are in the run it makes.
    run_census(
        Project.open(fresh),
        run_id="run_20260101T000003Z_333333333333",
        started_at="2026-01-01T00:00:03Z",
        rebind=True,
    )
    with open_catalog(fresh) as catalog:
        senses = {
            row[0]
            for row in catalog.query(
                "SELECT term FROM dictionary_entries WHERE origin = 'user'"
            ).fetchall()
        }
        found = catalog.query("SELECT record_watermark FROM run").fetchone()
        assert found is not None
        (watermark,) = found
    assert {"quolt_assay", "cluster_of_cells"} <= senses
    assert watermark >= max(event.sequence for event in read_events(fresh))


def test_an_answer_already_in_force_keeps_its_place_and_a_second_import_writes_nothing(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    out = _export(taught, tmp_path)
    with DashboardServer(fresh, port=0) as server:
        status, _ = _post(
            server,
            {
                "actor": "Priya Raman",
                "target": "term:quolt",
                "answer": "priyas_meaning",
                "theme_kind": "reagent",
            },
        )
    assert status == 201
    first = _import(fresh, out)
    assert "written 3" in first and "already answered 1" in first
    kept = next(e for e in read_events(fresh) if e.target == "term:quolt")
    assert json.loads(kept.payload)["answer"] == "priyas_meaning"
    assert "written 0" in _import(fresh, out)


def test_each_carried_answer_is_an_event_of_its_own_so_one_can_be_answered_again(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    _import(fresh, _export(taught, tmp_path))
    carried = [e for e in read_events(fresh) if e.target in CARRIED]
    assert len({event.event_id for event in carried}) == len(carried) == 4
    # Answering the word again takes the carried answer's place (the latest on a target wins).
    with DashboardServer(fresh, port=0) as server:
        status, _ = _post(
            server,
            {"actor": "Sam Okafor", "target": "term:quolt", "word": "not_a_word"},
        )
    assert status == 201
    assert DictionaryStore.for_project(fresh).look_up("quolt") is None


def test_checking_a_file_writes_nothing(taught: Path, fresh: Path, tmp_path: Path) -> None:
    out = _export(taught, tmp_path)
    before = len(read_events(fresh))
    said = _import(fresh, out, "--check")
    assert "checked, nothing written" in said and "would be written 4" in said
    assert len(read_events(fresh)) == before


def test_a_file_of_another_kind_or_a_project_never_read_is_refused_in_words(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"format": "something else"}')
    done = CliRunner().invoke(
        app, ["record", "import", str(broken), "-p", str(fresh), "--actor", "x"]
    )
    assert done.exit_code == 1 and "The file is refused" in done.output
    unread = tmp_path / "unread"
    pool = tmp_path / "pool"
    pool.mkdir()
    Project.create(unread, created_at="t", roots=[pool])
    out = _export(taught, tmp_path)
    done = CliRunner().invoke(
        app, ["record", "import", str(out), "-p", str(unread), "--actor", "x"]
    )
    assert done.exit_code == 1 and "read it first" in done.output


def test_a_file_holding_a_withdrawal_is_refused_whole_by_every_way_in(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    """An import writes answers and never a withdrawal: a file naming one is refused before
    anything is written, the good answers beside it included, by the command line and the
    Dashboard's Import alike."""
    document = json.loads(_export(taught, tmp_path).read_text())
    document["events"].append(
        {
            "target": "term:quolt",
            "kind": "withdraw",
            "actor": "Sam Okafor",
            "occurred_at": "2026-01-05T00:00:00Z",
            "payload": {"withdrawn": ["an-event"], "answer": {"sequence": 1, "digest": "x"}},
        }
    )
    with pytest.raises(ImportRefusedError, match="not one a person's words carry"):
        read_record_file(document)
    before = len(read_events(fresh, withdrawn=True))
    withdrawing = tmp_path / "withdrawing.json"
    withdrawing.write_text(json.dumps(document))
    done = CliRunner().invoke(
        app, ["record", "import", str(withdrawing), "-p", str(fresh), "--actor", "Sam Okafor"]
    )
    assert done.exit_code == 1 and "The file is refused" in done.output
    with DashboardServer(fresh, port=0) as server:
        status, body = _post(
            server,
            {
                "actor": "Sam Okafor",
                "import": {
                    "kind": "record",
                    "name": "withdrawing.json",
                    "document": json.dumps(document),
                },
            },
        )
    assert status == 400 and "nothing was written" in body["error"]
    assert len(read_events(fresh, withdrawn=True)) == before


def test_the_dashboard_s_import_reads_the_same_file(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    document = _export(taught, tmp_path).read_text()
    with DashboardServer(fresh, port=0) as server:
        status, body = _post(
            server,
            {
                "actor": "Sam Okafor",
                "import": {"kind": "record", "name": "words.json", "document": document},
            },
        )
        assert status == 201, body
        assert sorted(item["target"] for item in body["written"]) == sorted(CARRIED)
        assert body["left_out"] == 1 and body["author"] == "Sam Okafor"
        status, again = _post(
            server,
            {
                "actor": "Sam Okafor",
                "import": {"kind": "record", "name": "words.json", "document": document},
            },
        )
        assert status == 201 and again["written"] == []
        assert sorted(again["kept"]) == sorted(CARRIED)
        status, refused = _post(
            server,
            {
                "actor": "Sam Okafor",
                "import": {"kind": "record", "name": "x.json", "document": "{}"},
            },
        )
        assert status == 400 and "nothing was written" in refused["error"]
    events = [e for e in read_events(fresh) if e.target in CARRIED]
    assert {json.loads(e.payload)["source"]["imported_from"] for e in events} == {"taught"}


# -- 4. the landing page --------------------------------------------------------------------------


def test_the_form_offers_the_earlier_projects_that_hold_words_and_nothing_where_none_does(
    taught: Path, fresh: Path, tmp_path: Path
) -> None:
    home = tmp_path / "config"
    assert landing.carry_choices(home) == []  # no earlier projects: nothing to offer
    for project in (taught, fresh):
        remember_project(project, opened_at="2026-01-01T00:00:00Z", config_home=home)
    (offered,) = landing.carry_choices(home)
    assert (offered["path"], offered["name"]) == (str(taught), "taught")
    assert (offered["words"], offered["left_out"]) == (4, 1)
    # The project being shown is never offered to itself.
    assert landing.carry_choices(home, exclude=taught) == []
    state = landing.landing_state(None, config_home=home)
    assert [item["path"] for item in state["carry"]] == [str(taught)] and state["carried"] is None


def test_a_new_project_keeps_the_words_until_it_has_a_run_and_writes_them_once(
    taught: Path, built: Path, tmp_path: Path
) -> None:
    home = tmp_path / "config"
    remember_project(taught, opened_at="2026-01-01T00:00:00Z", config_home=home)
    pool = tmp_path / "newpool"
    pool.mkdir()
    (pool / "quolt_vorn_01.nd2").write_text("x")
    body = {"root": str(pool), "project": str(tmp_path / "new"), "carry": [str(taught)]}
    made = landing.create_project(body, created_at="2026-01-02T00:00:00Z", config_home=home)
    asked = landing.carry_state(made)
    assert asked == {"from": "taught", "staged": 4, "left_out": 1, "applied": None}
    # No run yet, so no event can be made: asked in words, and nothing written.
    with pytest.raises(landing.LandingRefusedError, match="read it first"):
        landing.carry_words(made, {"actor": "Sam Okafor"}, occurred_at="2026-01-02T00:00:01Z")
    assert len(read_events(made)) == 0
    run_census(Project.open(made), run_id="run_20260102T000001Z_111111111111", started_at="t")
    done = landing.carry_words(made, {"actor": "Sam Okafor"}, occurred_at="2026-01-02T00:00:02Z")
    assert (done["written"], done["kept"], done["left_out"]) == (4, 0, 1)
    assert done["author"] == "Sam Okafor"
    assert {e.target for e in read_events(made)} == CARRIED
    assert landing.carry_state(made)["applied"] == 4  # type: ignore[index]
    # Done once: asked again, it says what it did and writes nothing more.
    again = landing.carry_words(made, {"actor": "Sam Okafor"}, occurred_at="2026-01-02T00:00:03Z")
    assert again["again"] is True and len(read_events(made)) == 4
    # The staged file is itself a file of Record events a person can import by hand.
    staged = json.loads((made / landing.CARRY_FILE).read_text())
    assert staged["format"] == RECORD_FORMAT and len(staged["events"]) == 4


def test_the_form_refuses_a_project_it_did_not_offer_and_a_new_project_asked_for_none_keeps_none(
    taught: Path, tmp_path: Path
) -> None:
    home = tmp_path / "config"
    remember_project(taught, opened_at="2026-01-01T00:00:00Z", config_home=home)
    pool = tmp_path / "newpool"
    pool.mkdir()
    for carry, why in (
        ([str(tmp_path / "elsewhere")], "not a project you taught words in"),
        ("taught", "are a list"),
    ):
        with pytest.raises(landing.LandingRefusedError, match=why):
            landing.create_project(
                {"root": str(pool), "project": str(tmp_path / "x"), "carry": carry},
                created_at="t",
                config_home=home,
            )
    assert not (tmp_path / "x").exists()
    made = landing.create_project(
        {"root": str(pool), "project": str(tmp_path / "plain")}, created_at="t", config_home=home
    )
    assert landing.carry_state(made) is None
    assert not (made / landing.CARRY_FILE).exists()


def test_the_carry_route_is_a_guarded_write_of_the_landing_page(fresh: Path) -> None:
    assert "/api/landing/carry" in LANDING_ROUTES and "/api/landing/carry" in WRITE_ROUTES
    with DashboardServer(fresh, port=0) as server:
        # A project asked to bring no words in is told so, in words.
        status, refused = _post(server, {"actor": "Sam Okafor"}, "/api/landing/carry")
        assert status == 400 and "asked to bring no words in" in refused["error"]
        request = urllib.request.Request(
            server.url.rstrip("/") + "/api/landing/carry", method="POST", data=b"{}"
        )
        with pytest.raises(urllib.error.HTTPError) as unguarded:
            urllib.request.urlopen(request)
        assert unguarded.value.code == 403
        unguarded.value.close()
