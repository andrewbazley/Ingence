"""Checks: the few questions worth a person's minute, from the ranked questions, each with the
reading the run already uses when nobody answers, or null where the engine abstains, and a body the
answer route takes. Every name is invented."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from ingence.api.v1 import context_for, dispatch
from ingence.api.v1.checks import ANSWER_ROUTE, NO_RESULT_UNTIL_ANSWERED

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("checks"))


def test_checks_are_the_ranked_questions_cut_to_the_limit_with_defaults_and_bodies(built) -> None:
    context = context_for(built.project, settled=None, job_running=False)
    status, ranked = dispatch(context, "questions/ranked", {"limit": ["1000"]})
    assert status == 200, ranked
    status, body = dispatch(context, "checks", {"limit": ["3"]})
    assert status == 200, body
    assert body["total"] == ranked["total"]
    assert len(body["checks"]) == min(3, ranked["total"])
    assert [check["question_id"] for check in body["checks"]] == [
        question["key"] for question in ranked["questions"][:3]
    ]
    for check, question in zip(body["checks"], ranked["questions"], strict=False):
        assert check["id"] == f"check:{question['key']}"
        assert check["settles_n"] == question["settles_n"]
        assert check["answer"]["route"] == ANSWER_ROUTE
        assert check["answer"]["body"]["question_id"] == question["key"]
        if question["target"]:
            assert check["answer"]["body"]["target"] == question["target"]
        # The page adds the chosen option's value as word; the body never carries a word.
        assert "word" not in check["answer"]["body"]
        if question["section"] == "undecided":
            # No guess is said by a null default, and the question is still asked in words.
            assert check["default"] is None
            assert check["text"].startswith(question["question"].rstrip(".?")[:20])
            assert check["text"] != NO_RESULT_UNTIL_ANSWERED
        elif check["default"] is not None:
            assert check["default"]["value"] in {option["value"] for option in check["options"]}
            assert check["text"].startswith(question["question"].rstrip(".?"))
        assert all(option["value"] and option["text"] for option in check["options"])


def _ranked_question(**changes: object):
    from ingence.api.v1.questions_ranked import AnswerButton, RankedQuestion

    base: dict[str, object] = {
        "rank": 1,
        "key": "question:kelt_rule:log",
        "kind": "question",
        "section": "undecided",
        "group": "rule:group_resolution_deviation",
        "title": "Group resolution deviation",
        "lead_tier": 2,
        "leads": False,
        "question": 'Possible group: "log"',
        "reason": "",
        "settles_n": 4,
        "settles_unit": "files",
        "conclusions_blocked": None,
        "rules_blocked": None,
        "classes_touched": None,
        "rule_id": "group_resolution_deviation",
        "target": "rule:kelt_rule:log",
        "on": ["questions"],
        "sheet_n": None,
        "queue_position": 1,
        "answered": False,
        "rule_version": "1",
        "address": "?question=x#review",
        "answers": [AnswerButton(word="yes", label="Yes", title=None, theme_kind=None)],
        "own_words": False,
        "examples": ["exp_A/log_1.txt", "exp_A/log_2.txt", "exp_B/log_3.txt"],
        "examples_of": 4,
        "senses": None,
        "tops": [],
        "asked_because": None,
        "value": "log",
        "answer": None,
    }
    return RankedQuestion(**{**base, **changes})  # type: ignore[arg-type]


def test_a_check_with_no_guess_still_says_its_question_its_subject_and_two_examples() -> None:
    from ingence.api.v1.checks import _check

    check = _check(_ranked_question())
    assert check.default is None
    assert check.text.startswith('Possible group: "log". For example: log_1.txt, log_2.txt')
    assert "log_1.txt" in check.text and "log_2.txt" in check.text
    assert "/" not in check.text  # file names only, never a folder
    assert "log_3.txt" not in check.text  # at most two examples
    assert check.text != NO_RESULT_UNTIL_ANSWERED
    # The title is the question in plain words, never the rule's name.
    assert check.title == 'Possible group: "log"'
    assert "deviation" not in check.title.lower()


def test_a_subject_not_in_the_sentence_is_added_and_a_rule_name_never_titles() -> None:
    from ingence.api.v1.checks import _check

    check = _check(_ranked_question(question="A file that cannot be read", value="empty"))
    assert 'about "empty"' in check.text
    assert check.title == "A file that cannot be read"
    # A heading that holds an underscore as part of a quoted name is still a heading.
    assert _check(_ranked_question(question='Possible group: "exp_A"')).title.startswith("Possible")
    bare = _check(_ranked_question(question="content_unreadable", value=None, examples=[".", ""]))
    assert "For example" not in bare.text
    assert "_" not in bare.title and bare.title != "Group resolution deviation"


def test_the_default_limit_is_five_and_the_limit_is_bounded(built) -> None:
    context = context_for(built.project, settled=None, job_running=False)
    status, body = dispatch(context, "checks", {})
    assert status == 200 and len(body["checks"]) <= 5
    status, body = dispatch(context, "checks", {"limit": ["0"]})
    assert status == 400 and body["error"]["parameter"] == "limit"
    status, body = dispatch(context, "checks", {"limit": ["51"]})
    assert status == 400


def test_a_checked_conclusion_s_default_is_the_value_the_run_concluded() -> None:
    from ingence.api.v1.checks import _default
    from ingence.api.v1.questions_ranked import AnswerButton, RankedQuestion

    def question(section: str, kind: str, value: str | None, words: list[str]) -> RankedQuestion:
        return RankedQuestion(
            rank=1,
            key="class:kelt_rule:abc",
            kind=kind,  # type: ignore[arg-type]
            section=section,  # type: ignore[arg-type]
            group="rule:kelt_rule",
            title="Conclusions to check",
            lead_tier=2,
            leads=False,
            question="Is every file read as image format right?",
            reason="",
            settles_n=12,
            settles_unit="conclusions",
            conclusions_blocked=None,
            rules_blocked=None,
            classes_touched=None,
            rule_id="kelt_rule",
            target="class:kelt_rule:abc",
            on=["claims"],
            sheet_n=None,
            queue_position=None,
            answered=False,
            rule_version="1",
            address="#claims",
            answers=[AnswerButton(word=w, label=w, title=None, theme_kind=None) for w in words],
            own_words=False,
            examples=[],
            examples_of=12,
            senses=None,
            tops=[],
            asked_because=None,
            value=value,
            answer=None,
        )

    checked = _default(question("checked", "audit", "image_format", ["correct", "wrong"]))
    assert checked is not None and checked.value == "correct" and "image format" in checked.text
    proposed = _default(question("proposed", "atypical pair", None, ["correct", "wrong"]))
    assert proposed is not None and proposed.value == "correct"
    assert _default(question("undecided", "word", "kelt", ["kelt_sense", "cannot_say"])) is None
    assert _default(question("checked", "audit", "x", [])) is None
    # A proposed pair whose options lack the keep word abstains: never a word in hash order.
    assert _default(question("proposed", "derivation pattern", None, ["keep", "drop"])) is None
    assert _default(question("checked", "audit", "x", ["wrong", "cannot_say"])) is None


# ---------------------------------------------------------------------------
# The stored ranked list: built once after inference, read by both routes.
# ---------------------------------------------------------------------------


def _no_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every way of ranking the questions at request time fail aloud."""
    from ingence.api.routes import pages
    from ingence.api.v1 import questions_ranked as module

    def refused(*args: object, **kwargs: object) -> None:
        message = "the questions were ranked at request time"
        raise AssertionError(message)

    monkeypatch.setattr(module, "ranked_readout", refused)
    monkeypatch.setattr(module, "whole_sheet", refused)
    monkeypatch.setattr(pages, "page", refused)
    monkeypatch.setattr(pages, "waiting", refused)


def _stored(project: Path) -> str:
    from ingence.readouts import readout
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(project)
    assert pointer is not None
    readout(project, pointer.run_id, "questions_ranked")
    return pointer.run_id


def test_checks_and_the_ranked_questions_answer_from_the_stored_list(
    built, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = context_for(built.project, settled=None, job_running=False)
    _stored(built.project)
    before = {
        "checks": dispatch(context, "checks", {"limit": ["5"]}),
        "ranked": dispatch(context, "questions/ranked", {"limit": ["1000"]}),
        "page": dispatch(context, "questions/ranked", {"limit": ["2"], "offset": ["1"]}),
    }
    _no_ranking(monkeypatch)
    status, checks = dispatch(context, "checks", {"limit": ["5"]})
    assert status == 200, checks
    assert (status, checks) == before["checks"]
    assert dispatch(context, "questions/ranked", {"limit": ["1000"]}) == before["ranked"]
    paged = dispatch(context, "questions/ranked", {"limit": ["2"], "offset": ["1"]})
    assert paged == before["page"]
    assert checks["total"] == before["ranked"][1]["total"] > 0


def test_an_answered_question_leaves_the_stored_list_without_a_rebuild(
    built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.storage.catalog import open_catalog

    copy = tmp_path / "copy"
    shutil.copytree(built.project, copy)
    _stored(copy)
    context = context_for(copy, settled=None, job_running=False)
    status, before = dispatch(context, "checks", {"limit": ["5"]})
    assert status == 200 and before["checks"], before
    first = before["checks"][0]
    assert first["options"] and "target" in first["answer"]["body"]
    with open_catalog(copy) as catalog:
        decide_target(
            DecisionContext(catalog, copy),
            Decision(target=first["answer"]["body"]["target"], word=first["options"][0]["value"]),
            actor="a. person",
            occurred_at="2026-01-02T10:00:00Z",
        )
    _no_ranking(monkeypatch)
    status, after = dispatch(context, "checks", {"limit": ["5"]})
    assert status == 200, after
    assert after["total"] == before["total"] - 1
    assert first["question_id"] not in {check["question_id"] for check in after["checks"]}
    assert [check["question_id"] for check in after["checks"][:3]] == [
        check["question_id"] for check in before["checks"][1:4]
    ]
    status, shown = dispatch(context, "questions/ranked", {"limit": ["1000"], "answered": ["show"]})
    assert status == 200, shown
    held = next(q for q in shown["questions"] if q["key"] == first["question_id"])
    assert held["answered"] is True and held["answer"]["waits_for_rerun"] is True
    assert held["answer"]["actor"] == "a. person"
    status, hidden = dispatch(context, "questions/ranked", {"limit": ["1000"]})
    assert hidden["summary"]["answered_left_out"] == 1


# ---------------------------------------------------------------------------
# What made ranking slow on a large run: two lookups that grew with the run twice over.
# ---------------------------------------------------------------------------


def test_the_experiment_holding_a_path_is_found_from_one_map_kept_while_the_folders_stay() -> None:
    from ingence.api.routes.groupings import Experiments

    held = Experiments(
        folders={
            "e1": {"entry_id": "e1", "path": "kelt/run_a", "name": "run_a", "kind": "experiment"},
            "e2": {"entry_id": "e2", "path": "kelt/run_b", "name": "run_b", "kind": "experiment"},
        }
    )
    assert held.of("kelt/run_a/vorn_001.tif") == "e1"
    assert held.of("kelt/run_b") == "e2"
    assert held.of("kelt/elsewhere/vorn.tif") is None
    first = held.by_path
    assert held.by_path is first  # made once, not on every call
    held.folders["e3"] = {"entry_id": "e3", "path": "kelt", "name": "kelt", "kind": "named"}
    assert held.of("kelt/elsewhere/vorn.tif") == "e3"  # a folder added is read
    held.folders = {"e4": {"entry_id": "e4", "path": "zib", "name": "zib", "kind": "named"}}
    assert held.of("kelt/run_a/vorn_001.tif") is None and held.of("zib/a.tif") == "e4"


def test_the_folders_questions_ask_about_are_read_at_once_and_as_one_at_a_time() -> None:
    import duckdb

    from ingence.api.routes.proposals import (
        MISCELLANEOUS_PREDICATE,
        folders_asked_about,
        proposal_of,
    )

    class Held:
        def __init__(self) -> None:
            self.connection = duckdb.connect()
            self.connection.execute(
                "CREATE TABLE entries AS SELECT * FROM (VALUES "
                "('d1', 'kelt/odds', 'directory'), ('d2', 'zib', 'directory'), "
                "('f1', 'kelt/odds', 'file')) AS t(entry_id, relative_path, entry_type)"
            )
            self.asked = 0

        def query(self, sql: str, parameters: object = None) -> duckdb.DuckDBPyConnection:
            self.asked += 1
            return self.connection.execute(sql, parameters)

    catalog = Held()
    rows = [
        {"predicate": MISCELLANEOUS_PREDICATE, "raised": f"{path}: miscellaneous files"}
        for path in ("kelt/odds", "zib", "nowhere")
    ] + [{"predicate": "something_else", "raised": "kelt/odds: miscellaneous files"}]
    folders = folders_asked_about(catalog, rows)  # type: ignore[arg-type]
    assert folders == {"kelt/odds": "d1", "zib": "d2"} and catalog.asked == 1
    for row in rows:
        together = proposal_of(catalog, row, folders=folders)  # type: ignore[arg-type]
        alone = proposal_of(catalog, row)  # type: ignore[arg-type]
        assert together == alone
    assert proposal_of(catalog, rows[0], folders=folders)["target"].endswith(":d1")  # type: ignore[arg-type,index]
