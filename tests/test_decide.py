"""Answering a question: the Record's one way in, and everything it refuses first.

The answer route is the Record's one way in, so the tests worth having are about what the write
will not do:

It will not attribute a decision to nobody, and it will not invent an actor to avoid saying so.

It will not be made by a page on another site. A server on a loopback port with no accounts is
reachable from any page in the person's browser, and a write there has to be guarded the way a
local write has to be.

It will not write an answer to a question the current run no longer raises. That is the one way
an append-only log can be made to hold nonsense without anybody editing it.

It will not overwrite. A person changing their mind appends, and the first answer stays
readable.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.domain_skew import ask_every_tie
from ingence.api.routes.decide import (
    ANSWER_KINDS,
    AnswerRefusedError,
    QuestionMovedError,
    answer_question,
    question_target,
    recorded_answers,
)
from ingence.api.server import ANSWER_ROUTE, WRITE_HEADER, DashboardServer
from ingence.core.ids import Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
MOMENT = "2026-09-17T14:02:11Z"

#: ``sim`` stands between a simulation and structured illumination in the shipped Dictionary, two
#: packs' techniques, so the question is real and needs no invented vocabulary.
AMBIGUOUS = "binding_is_ambiguous_outside_programs_and_domains"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    for name in ("vorn_sim_raw.csv", "vorn_sim_processed.csv"):
        (pool / "kelt" / name).write_text("a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    project = ask_every_tie(project.path)  # a tied word in a few names is still asked
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFERENCE, started_at="t")
    return project


def answer(project: Project, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "actor": "robin",
        "occurred_at": MOMENT,
        "rule_id": AMBIGUOUS,
        "raised": "sim",
        "kind": RecordEventKind.ASSIGN,
        "answer": "simulation",
    }
    arguments.update(overrides)
    with open_catalog(project.path) as catalog:
        return answer_question(catalog, project.path, **arguments)


# ---------------------------------------------------------------------------
# What an answer becomes.
# ---------------------------------------------------------------------------


def test_an_answer_becomes_a_record_event_and_nothing_else(project: Project) -> None:
    """No claim, no Dictionary file edited, no rule changed. The Record is the whole write.

    Applying an answer, which turns it into a ``USER``-origin sense that outranks the core,
    happens on the read side (:mod:`ingence.dictionary.answers`). The read side builds it from
    the Record on every load rather than writing it out beside it, so this route writes one
    event and nothing else.
    """
    event = answer(project)
    assert event.kind is RecordEventKind.ASSIGN
    assert event.target == question_target(AMBIGUOUS, "sim")
    assert event.against_run_id == INFERENCE
    with open_catalog(project.path) as catalog:
        counts = catalog.counts()
    assert counts["record_events"] == 1
    assert counts["dictionary_entries"] > 0
    after = run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    assert not after.cancelled


def test_the_decision_is_about_the_question_rather_than_one_file(project: Project) -> None:
    """A person deciding what ``sim`` means has not decided something about one file.

    So two files raising one question produce one decision, and the target names the question.
    Targeting a file would have made this two decisions and left the second file undecided
    after somebody had plainly decided it.
    """
    event = answer(project)
    assert event.target == f"question:{AMBIGUOUS}:sim"
    assert json.loads(event.payload)["as_shown"]["entries_affected"] == 2


def test_the_payload_keeps_what_the_queue_was_showing(project: Project) -> None:
    """A decision made while many files turned on it is different evidence from the
    same decision made while two did, and only the Record can say which this was."""
    shown = json.loads(answer(project).payload)["as_shown"]
    assert shown == {"entries_affected": 2, "conclusions_blocked": 2, "senses_offered": 2}


def test_a_scope_narrows_a_decision_to_where_it_was_meant(project: Project) -> None:
    """A decision such as "in this folder ``ms`` means mass spectrometry" has a folder scope.

    The scope ladder that resolves it already exists."""
    event = answer(project, scope=Scope(ScopeKind.FOLDER, "kelt"))
    assert event.scope == Scope(ScopeKind.FOLDER, "kelt")


def test_changing_your_mind_appends_and_the_first_answer_stays_readable(
    project: Project,
) -> None:
    answer(project)
    answer(project, kind=RecordEventKind.REJECT, answer=None)
    log = recorded_answers(project.path)
    assert log["total"] == 2
    in_force = [event for event in log["events"] if event["in_force"]]
    assert len(in_force) == 1
    assert in_force[0]["kind"] == "reject"
    assert {event["kind"] for event in log["events"]} == {"assign", "reject"}


# ---------------------------------------------------------------------------
# What it refuses.
# ---------------------------------------------------------------------------


def test_an_assignment_naming_a_sense_the_question_does_not_offer_is_refused(
    project: Project,
) -> None:
    """And the refusal says what is on offer, and what to say instead.

    A sense the engine does not hold is a definition rather than an assignment, so the message
    routes a person to the verb that would work rather than only telling them no.
    """
    with pytest.raises(AnswerRefusedError, match="not a sense this question offers"):
        answer(project, answer="spinning_rust")
    assert read_events(project.path) == ()


def test_an_assignment_naming_nothing_is_refused(project: Project) -> None:
    with pytest.raises(AnswerRefusedError, match="assigning nothing decides nothing"):
        answer(project, answer=None)


def test_a_definition_stating_nothing_is_refused(project: Project) -> None:
    with pytest.raises(AnswerRefusedError, match="defining nothing decides nothing"):
        answer(project, kind=RecordEventKind.DEFINE, answer=" ")


def test_a_rejection_carrying_a_sense_is_refused_rather_than_stored(
    project: Project,
) -> None:
    """The Rulebook loader's rule, applied to a decision.

    A parameter a rule's sentence never mentions is refused at load, because a setting nobody
    reading the rule can see is worse than none. A rejection carrying an assigned sense reads,
    to whoever replays the Record, exactly like an assignment.
    """
    with pytest.raises(AnswerRefusedError, match="takes no answer"):
        answer(project, kind=RecordEventKind.REJECT)


def test_a_kind_that_is_not_an_answer_to_a_question_is_refused(project: Project) -> None:
    """``merge`` and ``split`` are about classes and ``override`` is about a rule's parameters.

    Each is a real decision and none of them is an answer to a question, so each belongs to
    the view that will write it rather than being accepted here because the enum allows it.
    """
    assert RecordEventKind.MERGE not in ANSWER_KINDS
    with pytest.raises(AnswerRefusedError, match="not something a question can be answered"):
        answer(project, kind=RecordEventKind.MERGE)


def test_answering_a_question_the_run_no_longer_raises_is_refused(project: Project) -> None:
    """A page left open across an inference is answering something that is no longer there.

    Its own exception class, because it is the one refusal a page can act on by itself:
    everything else means the answer was wrong, and this means the page is out of date.
    """
    with pytest.raises(QuestionMovedError, match="raises no question"):
        answer(project, raised="a_term_nothing_raised")
    assert read_events(project.path) == ()


def test_an_answer_needs_a_project_with_a_run(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    with pytest.raises(AnswerRefusedError, match="no published run"):
        answer(project)


# ---------------------------------------------------------------------------
# Over HTTP, where the guard matters.
# ---------------------------------------------------------------------------


@pytest.fixture
def served(project: Project):
    server = DashboardServer(project.path, port=0)
    server.start()
    yield server, project
    server.stop()


def post(server: DashboardServer, body: dict[str, Any], headers: dict[str, str] | None = None):
    request = urllib.request.Request(
        server.url.rstrip("/") + ANSWER_ROUTE,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    for name, value in (
        headers if headers is not None else {"Content-Type": "application/json", WRITE_HEADER: "1"}
    ).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        outcome = (error.status, json.loads(error.read()))
        error.close()
        return outcome


GOOD = {
    "actor": "robin",
    "rule_id": AMBIGUOUS,
    "raised": "sim",
    "kind": "assign",
    "answer": "simulation",
}


def test_an_answer_over_http_is_written_and_reported_back(served) -> None:
    server, project = served
    status, body = post(server, GOOD)
    assert status == 201
    assert body["sequence"] == 1
    assert body["actor"] == "robin"
    assert body["target"] == question_target(AMBIGUOUS, "sim")
    assert len(read_events(project.path)) == 1


def test_a_write_without_the_header_a_cross_site_form_cannot_set_is_refused(served) -> None:
    """The guard that matters, and the reason it is a header rather than a token.

    A form or an image on another site can make the browser post here; neither can set a
    custom header without the browser first asking this server for permission, which it never
    grants. Not a credential -- there are no accounts on a single-user machine -- but it stops
    a page on another site writing a decision into somebody's Record without them knowing.
    """
    server, project = served
    status, body = post(server, GOOD, {"Content-Type": "application/json"})
    assert status == 403
    assert WRITE_HEADER in body["error"]
    assert read_events(project.path) == ()


def test_a_form_encoded_write_is_refused(served) -> None:
    """A form-encoded body is exactly what a cross-site post looks like."""
    server, _ = served
    status, _ = post(
        server,
        GOOD,
        {"Content-Type": "application/x-www-form-urlencoded", WRITE_HEADER: "1"},
    )
    assert status == 403


def test_a_write_from_another_origin_is_refused_by_name(served) -> None:
    server, project = served
    status, body = post(
        server,
        GOOD,
        {"Content-Type": "application/json", WRITE_HEADER: "1", "Origin": "http://elsewhere"},
    )
    assert status == 403
    assert "http://elsewhere" in body["error"]
    assert read_events(project.path) == ()


def test_a_write_from_the_page_itself_is_accepted(served) -> None:
    """The other half of the origin check: the server's own page must still be able to write."""
    server, _ = served
    status, _ = post(
        server,
        GOOD,
        {"Content-Type": "application/json", WRITE_HEADER: "1", "Origin": server.url.rstrip("/")},
    )
    assert status == 201


def test_a_write_naming_no_actor_is_refused(served) -> None:
    """The engine never supplies an attribution. It is the person's name or it is nothing."""
    server, project = served
    status, body = post(server, {**GOOD, "actor": ""})
    assert status == 400
    assert "actor" in body["error"]
    assert read_events(project.path) == ()


def test_a_stale_page_gets_a_conflict_rather_than_a_bad_request(served) -> None:
    """So a page can tell "reload me" from "your answer was wrong" without reading prose."""
    server, _ = served
    status, _ = post(server, {**GOOD, "raised": "a_term_nothing_raised"})
    assert status == 409


def test_a_refused_answer_is_a_bad_request_rather_than_a_server_error(served) -> None:
    server, _ = served
    status, body = post(server, {**GOOD, "answer": "spinning_rust"})
    assert status == 400
    assert "not a sense this question offers" in body["error"]


def test_a_scope_the_engine_does_not_have_is_refused_rather_than_widened(served) -> None:
    """Silently deciding something everywhere when a person meant one folder is the worst
    available failure, so an unreadable scope is refused rather than defaulted."""
    server, project = served
    status, body = post(server, {**GOOD, "scope": {"kind": "galaxy"}})
    assert status == 400
    assert "not a scope kind" in body["error"]
    assert read_events(project.path) == ()


def test_no_other_route_accepts_a_write(served) -> None:
    """Named routes rather than a guess: every path but the one is refused the verb."""
    server, _ = served
    for route in ("/api/project", "/api/review", "/api/record", "/api/inventory"):
        request = urllib.request.Request(server.url.rstrip("/") + route, data=b"{}", method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header(WRITE_HEADER, "1")
        with pytest.raises(urllib.error.HTTPError) as raised, urllib.request.urlopen(request):
            pass
        raised.value.close()
        assert raised.value.code == 404, route


def test_the_record_route_shows_every_decision_and_which_is_in_force(served) -> None:
    server, _ = served
    post(server, GOOD)
    post(server, {**GOOD, "kind": "reject", "answer": None})
    with urllib.request.urlopen(server.url.rstrip("/") + "/api/record") as response:
        log = json.loads(response.read())
    assert log["total"] == 2
    assert [event["in_force"] for event in log["events"]] == [True, False]
    assert log["events"][0]["kind"] == "reject"
