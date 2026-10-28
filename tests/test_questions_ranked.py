"""Every question, ranked and grouped: the route behind "conclusions first".

Two pools. The pool of ``test_question_sheet`` (invented names: sixty dated folders of
instrument files) holds thirty group questions, asked first by their rule. Every domain section
loads, so no vocabulary pack is asked about. The explorer fixture holds classes of conclusions to
check and patterns of derivations. What is checked, in each: every question of the sheet and of
the queue is served once, in an order made only of what the engine holds, each with a group, a rank
and a reason. Nothing is left out without a count. Every answered question stays reachable.
"""

from __future__ import annotations

import shutil
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

from ingence.core.project import Project
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject

SECTION_ORDER = {"undecided": 0, "proposed": 1, "checked": 2}


@pytest.fixture(scope="module")
def pool(tmp_path_factory: pytest.TempPathFactory) -> Project:
    from test_question_sheet import _build

    built = _build(tmp_path_factory.mktemp("ranked"))
    # The ranked list is stored after inference, as the readouts step does.
    _store_ranked(built.path)
    return built


def _store_ranked(project: Path) -> None:
    from ingence.readouts import readout
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(project)
    assert pointer is not None
    readout(project, pointer.run_id, "questions_ranked")


def _route(project: Path, path: str, **query: Any) -> tuple[HTTPStatus, dict[str, Any]]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(v)] for name, v in query.items()})


def _ranked(project: Path, **query: Any) -> dict[str, Any]:
    from ingence.api.v1.models import RESPONSE_MODELS

    status, body = _route(project, "questions/ranked", **query)
    assert status == HTTPStatus.OK, body
    RESPONSE_MODELS["questions_ranked"].model_validate(body)
    return body


def _everything(project: Path, **query: Any) -> list[dict[str, Any]]:
    return list(_ranked(project, limit=1000, **query)["questions"])


def _queue(project: Path) -> list[dict[str, Any]]:
    from ingence.api.routes import pages

    with open_catalog(project) as catalog:
        return list(pages.page(catalog, project, "questions", limit=100_000, answered=True)["rows"])


def _sheet(project: Path) -> list[dict[str, Any]]:
    status, body = _route(project, "questions/sheet")
    assert status == HTTPStatus.OK, body
    return list(body["questions"])


def _keys(project: Path) -> set[str]:
    held = {str(row.get("answers_at") or row["target"]) for row in _queue(project)}
    held |= {str(row["target"] or f"pattern:{row['key']}") for row in _sheet(project)}
    return held


def test_every_question_of_the_sheet_and_the_queue_is_served_once(pool) -> None:
    served = _everything(pool.path)
    keys = [question["key"] for question in served]
    assert len(keys) == len(set(keys))
    # Every question of the sheet's first fifty and of the queue, and more where the sheet holds
    # more: the whole sheet is served, not its first ``sheet_budget``.
    assert set(keys) >= _keys(pool.path)
    body = _ranked(pool.path)
    assert body["total"] == len(served)
    assert body["summary"]["questions"] == len(served)
    only_queue = [q for q in served if q["on"] == ["questions"]]
    assert only_queue
    assert all(q["sheet_n"] is None and q["queue_position"] is not None for q in only_queue)


def test_the_evidence_on_a_question_is_the_queue_s_and_the_sheet_s_own(pool) -> None:
    served = {question["key"]: question for question in _everything(pool.path)}
    for row in _queue(pool.path):
        question = served[str(row.get("answers_at") or row["target"])]
        assert question["conclusions_blocked"] == row["conclusions_blocked"]
        assert question["rules_blocked"] == row["rules_blocked"]
        assert question["classes_touched"] == row["classes_touched"]
        assert question["rule_id"] == row["rule_id"]
    for row in _sheet(pool.path):
        question = served[str(row["target"] or f"pattern:{row['key']}")]
        assert question["settles_n"] >= row["settles_n"]
        assert question["sheet_n"] == row["n"]
    # A question only the sheet holds was not counted by the queue: unknown, never nought.
    for question in served.values():
        if question["on"] == ["sheet"]:
            assert question["conclusions_blocked"] is None
            assert question["rules_blocked"] is None
            assert question["classes_touched"] is None


def test_a_question_on_both_lists_is_one_entry_that_says_it_is_on_both(tied_project) -> None:
    # A tied word stands on both lists.
    served = _everything(tied_project)
    both = next(question for question in served if question["kind"] == "word")
    assert both["on"] == ["sheet", "questions"]
    assert both["sheet_n"] is not None and both["queue_position"] is not None


def test_the_first_question_leads_and_says_why(pool) -> None:
    # The rule that asks first leads.
    served = _everything(pool.path)
    first = served[0]
    assert first["rank"] == 1
    assert first["lead_tier"] == 0
    assert first["leads"] is True
    assert first["section"] == "undecided"
    assert first["reason"].startswith("settles ")
    assert first["reason"].endswith("its rule asks it first")


def test_a_rule_that_asks_first_leads_its_questions_and_says_so(pool) -> None:
    served = _everything(pool.path)
    groups = [q for q in served if q["rule_id"] == "group_resolution_deviation"]
    assert len(groups) >= 30
    for question in groups:
        assert question["lead_tier"] == 0
        assert question["leads"] is True
        assert question["reason"].endswith("its rule asks it first")
        assert question["group"] == "rule:group_resolution_deviation"
        assert question["settles_unit"] in ("files", "folders")


def test_the_order_is_made_of_the_documented_tuple_and_nothing_else(pool) -> None:
    served = _everything(pool.path)
    assert [question["rank"] for question in served] == list(range(1, len(served) + 1))

    def key(question: dict[str, Any]) -> tuple[int, int, int, int, int]:
        return (
            0 if question["kind"] == "word" else 1,
            question["lead_tier"],
            SECTION_ORDER[question["section"]],
            -question["settles_n"],
            -(question["conclusions_blocked"] or 0),
        )

    keys = [key(question) for question in served]
    assert keys == sorted(keys)
    # Leading is the first two tiers, and nothing else.
    assert all((q["lead_tier"] < 2) == q["leads"] for q in served)
    # The same run, asked twice, gives the same order.
    assert served == _everything(pool.path)


def test_every_question_names_its_group_and_the_groups_hold_them_all(pool) -> None:
    body = _ranked(pool.path, limit=3)
    full = _everything(pool.path)
    for question in full:
        expected = (
            f"rule:{question['rule_id']}" if question["rule_id"] else f"kind:{question['kind']}"
        )
        assert question["group"] == expected
    groups = body["groups"]
    # The groups cover every question, whatever page is asked for, best-ranked first.
    assert sum(group["questions"] for group in groups) == len(full)
    assert sum(group["leading"] for group in groups) == sum(q["leads"] for q in full)
    assert [group["first_rank"] for group in groups] == sorted(g["first_rank"] for g in groups)
    for group in groups:
        members = [q for q in full if q["group"] == group["key"]]
        assert group["first_rank"] == members[0]["rank"]
        assert group["label"]
        assert sorted(group["kinds"]) == sorted({q["kind"] for q in members})
    deviation = next(g for g in groups if g["key"] == "rule:group_resolution_deviation")
    assert deviation["questions"] >= 30


def test_paging_filters_and_the_counts_of_what_a_page_left_out(pool) -> None:
    whole = _everything(pool.path)
    page = _ranked(pool.path, limit=5)
    assert [q["rank"] for q in page["questions"]] == [1, 2, 3, 4, 5]
    assert page["total"] == len(whole)
    # How many lie after the page.
    assert page["left_out"] == len(whole) - 5
    nxt = _ranked(pool.path, limit=5, offset=5)
    assert [q["rank"] for q in nxt["questions"]] == [6, 7, 8, 9, 10]
    assert nxt["left_out"] == len(whole) - 10
    last = _ranked(pool.path, limit=5, offset=len(whole) - 2)
    assert [q["rank"] for q in last["questions"]] == [len(whole) - 1, len(whole)]
    assert last["left_out"] == 0
    leading = _ranked(pool.path, limit=1000, leads="true")
    assert leading["total"] == sum(q["leads"] for q in whole)
    assert all(q["leads"] for q in leading["questions"])
    assert leading["only_leading"] is True
    # Ranks are those of the whole order, so a filtered page keeps its question's place.
    assert [q["rank"] for q in leading["questions"]] == sorted(
        q["rank"] for q in leading["questions"]
    )
    one_group = _ranked(pool.path, limit=1000, group="rule:group_resolution_deviation")
    assert {q["group"] for q in one_group["questions"]} == {"rule:group_resolution_deviation"}
    assert one_group["group"] == "rule:group_resolution_deviation"
    # The summary and the groups count the whole order, never the page.
    assert one_group["summary"]["questions"] == len(whole)


def test_what_the_view_must_not_lose_is_counted_beside_the_questions(pool) -> None:
    summary = _ranked(pool.path)["summary"]
    pages = {page["view"]: page for page in summary["elsewhere"]}
    names = {
        "classes": "Groupings",
        "claims": "Conclusion samples",
        "candidates": "Proposed pairs",
        "extensions": "Extensions",
        "programs": "Programs",
    }
    assert set(pages) <= set(names)
    assert all(
        page["label"] == names[view] and page["waiting"] >= 1 for view, page in pages.items()
    )
    # In the order the tabs stand, and each count is the page's own.
    assert [page["view"] for page in summary["elsewhere"]] == [v for v in names if v in pages]
    from ingence.api.routes import pages as page_routes

    with open_catalog(pool.path) as catalog:
        waiting = page_routes.waiting(catalog, pool.path)
    assert {view: page["waiting"] for view, page in pages.items()} == {
        view: waiting[view] for view in pages
    }
    assert summary["by_section"]["undecided"] >= 31
    assert len(summary["ranked_by"]) == 4
    assert sum(summary["by_kind"].values()) == summary["questions"]


def test_an_answered_question_is_left_out_with_a_count_and_stays_reachable(pool, tmp_path) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target

    copy = tmp_path / "copy"
    shutil.copytree(pool.path.parent, copy)
    project = copy / "project"
    # The first group question is answered.
    first = _everything(pool.path)[0]
    with open_catalog(project) as catalog:
        context = DecisionContext(catalog, project)
        decide_target(
            context,
            Decision(target=first["target"], word="correct"),
            actor="a. person",
            occurred_at="2026-01-02T10:00:00Z",
        )
    before = len(_everything(pool.path))
    hidden = _ranked(project, limit=1000)
    assert hidden["summary"]["answered_left_out"] == 1
    assert first["key"] not in {q["key"] for q in hidden["questions"]}
    assert hidden["total"] == before - 1
    shown = _ranked(project, limit=1000, answered="show")
    answered = next(q for q in shown["questions"] if q["key"] == first["key"])
    assert answered["answered"] is True
    assert answered["target"] == first["target"]
    assert shown["summary"]["answered_left_out"] == 0
    assert shown["total"] == before


def test_a_check_ranks_after_what_the_engine_could_not_decide_and_says_what_it_settles(
    explorer_project: ExplorerProject,
) -> None:
    served = _everything(explorer_project.project)
    assert {"checked", "proposed"} <= {question["section"] for question in served}
    checks = [q for q in served if q["kind"] == "audit"]
    assert checks
    for question in checks:
        assert question["section"] == "checked"
        assert question["settles_unit"] == "conclusions"
        assert question["reason"] == f"settles {question['settles_n']:,} conclusions" or (
            question["settles_n"] == 1 and question["reason"] == "settles 1 conclusion"
        )
        assert question["target"].startswith("class:")
        assert question["leads"] is False
    patterns = [q for q in served if q["kind"] == "derivation pattern"]
    assert patterns
    for question in patterns:
        assert question["section"] == "proposed"
        assert question["settles_unit"] == "pairs"
        assert question["target"] is None
        assert question["key"].startswith("pattern:")
        assert "pairs" in question["reason"] or question["reason"] == "settles 1 pair"
    # A conclusion the engine reached is taken as right: every check ranks after every
    # question about what it could not decide that does not lead.
    last_undecided = max(
        (q["rank"] for q in served if q["section"] == "undecided" and not q["leads"]), default=0
    )
    assert all(q["rank"] > last_undecided for q in checks if not q["leads"])


def test_a_bad_page_or_switch_is_refused_in_words(pool) -> None:
    for name, value in (("limit", 0), ("limit", 1001), ("offset", -1), ("leads", "maybe")):
        status, body = _route(pool.path, "questions/ranked", **{name: value})
        assert status == HTTPStatus.BAD_REQUEST, (name, value)
        assert body["error"]["parameter"] == name


def test_the_route_writes_nothing(pool) -> None:
    def state() -> list[tuple[str, int]]:
        return sorted(
            (str(path.relative_to(pool.path)), path.stat().st_size)
            for path in pool.path.rglob("*")
            if path.is_file()
        )

    before = state()
    _ranked(pool.path)
    assert state() == before


def _sheet_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["target"] or f"pattern:{row['key']}") for row in rows}


def test_every_check_and_pattern_is_served_whatever_the_sheet_budget(
    explorer_project: ExplorerProject, tmp_path: Path
) -> None:
    """The route serves every question, not only the sheet's first ``sheet_budget`` ones. A
    project that cuts its sheet to three questions still has every class of conclusions and every
    pattern of derivations served here, each saying which page answers it."""
    copy = tmp_path / "copy"
    shutil.copytree(explorer_project.project, copy)
    project = Project.open(copy)
    questions = project.settings.questions.model_copy(update={"sheet_budget": 3})
    project.save_settings(project.settings.model_copy(update={"questions": questions}))
    status, page = _route(copy, "questions/sheet")
    assert status == HTTPStatus.OK, page
    assert len(page["questions"]) == 3
    status, largest = _route(copy, "questions/sheet", budget=10_000, tied=10_000)
    assert status == HTTPStatus.OK, largest
    summary = largest["summary"]
    assert summary["audit_classes_total"] > 3 and summary["patterns_total"] >= 1
    body = _ranked(copy, limit=1000)
    served = body["questions"]
    kinds = [question["kind"] for question in served]
    assert kinds.count("audit") == summary["audit_classes_total"]
    assert kinds.count("derivation pattern") == summary["patterns_total"]
    assert body["summary"]["sheet_left_out"] == 0
    assert {question["key"] for question in served} >= _sheet_keys(largest["questions"])
    # The Sheet page's own three keep their numbers there. Every other question has none and is
    # answered where such a class is answered.
    on_page = [question for question in served if question["sheet_n"] is not None]
    assert sorted(question["sheet_n"] for question in on_page) == [1, 2, 3]
    assert {question["key"]: question["sheet_n"] for question in on_page} == {
        str(row["target"] or f"pattern:{row['key']}"): row["n"] for row in page["questions"]
    }
    for question in served:
        if question["sheet_n"] is not None:
            assert "sheet" in question["on"]
        elif question["kind"] == "audit":
            assert question["on"] == ["claims"]
        elif question["kind"] in ("derivation pattern", "atypical pair"):
            assert question["on"] == ["candidates"]
    assert len(on_page) < len(served)


def test_a_sheet_that_keeps_an_answered_question_is_read_as_answered(
    explorer_project: ExplorerProject, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Where a sheet keeps an answered question with its ``answer`` until the next run, the
    question is answered here too, left out by default and counted, served when asked for."""
    from ingence.api.v1 import questions_ranked as module
    from ingence.readouts import READOUTS_DIR

    # A copy whose ranked list is built here, through the sheet below (the shared project's
    # stored list was built without it).
    project = tmp_path / "copy"
    shutil.copytree(explorer_project.project, project)
    shutil.rmtree(project / "projections" / READOUTS_DIR, ignore_errors=True)
    status, sheet = _route(project, "questions/sheet")
    assert status == HTTPStatus.OK, sheet
    first = sheet["questions"][0]
    key = str(first["target"] or f"pattern:{first['key']}")
    real = module.whole_sheet

    def answered(context: Any, catalog: Any) -> dict[str, Any]:
        built = real(context, catalog)
        rows = [dict(row) for row in built["questions"]]
        rows[0]["answer"] = {"word": "right", "by": "a. person"}
        return {**built, "questions": rows}

    monkeypatch.setattr(module, "whole_sheet", answered)
    hidden = _ranked(project, limit=1000)
    assert key not in {question["key"] for question in hidden["questions"]}
    assert hidden["summary"]["answered_left_out"] == 1
    shown = _ranked(project, limit=1000, answered="show")
    held = next(question for question in shown["questions"] if question["key"] == key)
    assert held["answered"] is True
    assert shown["summary"]["answered_left_out"] == 0
    assert shown["total"] == hidden["total"] + 1


# ---------------------------------------------------------------------------
# What a page needs to answer a question, and to open a group of them 50 at a time.
# ---------------------------------------------------------------------------


def test_every_question_names_its_rule_s_version_and_the_address_that_answers_it(pool) -> None:
    from urllib.parse import parse_qs, urlparse

    served = _everything(pool.path)
    by_key = {str(row.get("answers_at") or row["target"]): row for row in _queue(pool.path)}
    for question in served:
        if question["key"] in by_key:
            assert question["rule_version"] == str(by_key[question["key"]]["rule_version"])
        if question["rule_id"]:
            assert question["rule_version"], question["key"]
        # A question the Questions page answers is addressed by its key. A pattern's pairs are
        # answered one by one, on Proposed pairs.
        if question["target"]:
            parts = urlparse(question["address"])
            assert parts.fragment == "review"
            assert parse_qs(parts.query)["question"] == [question["key"]]
        else:
            assert question["address"] == "#candidates"


def test_the_buttons_are_the_ones_the_kind_takes_and_the_last_is_cannot_say(pool) -> None:
    served = _everything(pool.path)
    # A group question takes its own buttons.
    group = next(q for q in served if q["rule_id"] == "group_resolution_deviation")
    assert [(a["word"], a["label"]) for a in group["answers"]] == [
        ("correct", "Correct"),
        ("wrong", "Wrong"),
        ("cannot_say", "Can't say"),
    ]
    for question in served:
        if question["target"]:
            assert question["answers"][-1]["label"] == "Can't say", question["key"]
            assert [a["label"] for a in question["answers"]].count("Can't say") == 1
            assert all(a["label"] != "Other" for a in question["answers"])
        else:
            assert question["answers"] == []


def test_a_class_of_conclusions_takes_right_wrong_and_cannot_say(
    explorer_project: ExplorerProject,
) -> None:
    served = _everything(explorer_project.project)
    checks = [q for q in served if q["kind"] == "audit"]
    assert checks
    for question in checks:
        assert [(a["word"], a["label"]) for a in question["answers"]] == [
            ("correct", "Right"),
            ("wrong", "Wrong"),
            ("cannot_say", "Can't say"),
        ]
    patterns = [q for q in served if q["kind"] == "derivation pattern"]
    assert patterns
    assert all(q["answers"] == [] and q["address"] == "#candidates" for q in patterns)


def test_the_examples_are_three_at_most_relative_to_the_root_and_counted(
    pool, explorer_project: ExplorerProject
) -> None:
    for project in (pool.path, explorer_project.project):
        for question in _everything(project):
            assert len(question["examples"]) <= 3
            assert question["examples_of"] == question["settles_n"]
            for example in question["examples"]:
                # Never the machine's own path: not /Users, not any absolute prefix.
                assert not example.startswith(("/", "~")), example
                assert "/Users/" not in example
            for top in question["tops"]:
                assert top and not top.startswith("/")
    shown = [q for q in _everything(pool.path) if q["examples"]]
    assert shown, "a pool of dated instrument folders gives some question its examples"


def test_a_tied_word_s_buttons_are_its_senses_in_order_and_never_a_split_of_its_sentence(
    tied_project: Path,
) -> None:
    served = _everything(tied_project)
    tied = [q for q in served if q["kind"] == "word" and q["senses"]]
    assert tied
    for question in tied:
        senses = [sense["term"] for sense in question["senses"]]
        buttons = [a["word"] for a in question["answers"]]
        assert buttons[:-1] == senses
        assert buttons[-1] == "cannot_say"
        assert question["own_words"] is True
        assert [a["theme_kind"] for a in question["answers"][:-1]] == [
            sense["theme_kind"] for sense in question["senses"]
        ]


def test_the_questions_about_words_come_before_every_other_question(tied_project: Path) -> None:
    """The Words section leads Questions, so the first line of questions is the words.

    A question about what a word means ranks before a vocabulary pack, a rule that asks first and a
    check of conclusions, whatever its tier. Among the words the documented tuple still orders them.
    """
    served = _everything(tied_project)
    kinds = [question["kind"] for question in served]
    assert "word" in kinds and any(kind != "word" for kind in kinds)
    last_word = max(at for at, kind in enumerate(kinds) if kind == "word")
    assert kinds[: last_word + 1] == ["word"] * (last_word + 1)
    assert [question["rank"] for question in served] == list(range(1, len(served) + 1))
    words = served[: last_word + 1]
    keys = [
        (
            question["lead_tier"],
            SECTION_ORDER[question["section"]],
            -question["settles_n"],
            -(question["conclusions_blocked"] or 0),
        )
        for question in words
    ]
    assert keys == sorted(keys)
    # Moving the words first changes no question's standing: leading is still the first two tiers.
    assert all((q["lead_tier"] < 2) == q["leads"] for q in served)


def test_rest_keeps_what_does_not_lead_and_the_chips_add_up(pool) -> None:
    whole = _everything(pool.path)
    body = _ranked(pool.path, limit=1000, rest="true")
    assert [q["key"] for q in body["questions"]] == [q["key"] for q in whole if not q["leads"]]
    assert body["only_rest"] is True
    lead = _ranked(pool.path, limit=1000, leads="true")
    # The leads and the rest are the whole order, and the titles say how many each holds.
    assert lead["total"] + body["total"] == len(whole)
    titles = body["titles"]
    assert sum(t["questions"] for t in titles) == len(whole)
    assert sum(t["leading"] for t in titles) == lead["total"]
    assert [t["first_rank"] for t in titles] == sorted(t["first_rank"] for t in titles)
    for title in titles:
        held = [q for q in whole if q["title"] == title["title"]]
        assert title["questions"] == len(held)
        assert title["first_rank"] == held[0]["rank"]
    status, refused = _route(pool.path, "questions/ranked", leads="true", rest="true")
    assert status == HTTPStatus.BAD_REQUEST
    assert refused["error"]["parameter"] == "rest"


def test_a_title_a_folder_and_a_rule_each_narrow_a_group_and_say_what_they_keep(pool) -> None:
    whole = _everything(pool.path)
    title = whole[-1]["title"]
    narrowed = _ranked(pool.path, limit=1000, title=title)
    assert {q["title"] for q in narrowed["questions"]} == {title}
    assert narrowed["total"] == sum(1 for q in whole if q["title"] == title)
    options = narrowed["filters"]
    assert options["tops"] and options["rules"]
    top = options["tops"][0]["value"]
    by_top = _ranked(pool.path, limit=1000, title=title, top=top)
    assert by_top["total"] == options["tops"][0]["n"]
    assert all(top in q["tops"] for q in by_top["questions"])
    # The options do not shrink when one is chosen: a person can change their mind.
    assert by_top["filters"] == options
    rule = options["rules"][0]["value"]
    by_rule = _ranked(pool.path, limit=1000, title=title, rule=rule)
    assert by_rule["total"] == options["rules"][0]["n"]
    assert {q["rule_id"] for q in by_rule["questions"]} == {rule}
    # Both together keep only what both keep.
    both = _ranked(pool.path, limit=1000, title=title, top=top, rule=rule)
    assert {q["key"] for q in both["questions"]} == (
        {q["key"] for q in by_top["questions"]} & {q["key"] for q in by_rule["questions"]}
    )


def test_an_answered_question_stays_with_its_answer_until_re_run_reads_it(pool, tmp_path) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target

    copy = tmp_path / "copy"
    shutil.copytree(pool.path.parent, copy)
    project = copy / "project"
    # The first group question is answered.
    first = _everything(pool.path)[0]
    with open_catalog(project) as catalog:
        context = DecisionContext(catalog, project)
        decide_target(
            context,
            Decision(target=first["target"], word="correct", note="we use it"),
            actor="Dana Whitfield",
            occurred_at="2026-01-02T10:00:00Z",
        )
    shown = _ranked(project, limit=1000, answered="show")
    answered = next(q for q in shown["questions"] if q["key"] == first["key"])
    assert answered["answered"] is True
    given = answered["answer"]
    assert (given["word"], given["label"], given["actor"]) == (
        "correct",
        "Correct",
        "Dana Whitfield",
    )
    assert given["note"] == "we use it"
    assert given["occurred_at"] == "2026-01-02T10:00:00Z"
    # The Record's sequence is above what the run read: it waits for Re-run.
    assert given["waits_for_rerun"] is True
    assert given["sequence"] is not None
    # The rest still carry none, and the summary says how many are answered, and of them lead.
    assert all(q["answer"] is None for q in shown["questions"] if q["key"] != first["key"])
    assert shown["summary"]["answered"] == 1
    assert shown["summary"]["leading_answered"] == 1
    hidden = _ranked(project, limit=1000)
    assert hidden["summary"]["answered"] == 0
    assert hidden["summary"]["answered_left_out"] == 1


def test_a_queue_question_carries_the_rule_s_own_words_for_why_it_asked(pool) -> None:
    served = {question["key"]: question for question in _everything(pool.path)}
    for row in _queue(pool.path):
        question = served[str(row.get("answers_at") or row["target"])]
        assert question["asked_because"] == (row["reason"] or None)
    # A question only the sheet holds has none to give.
    assert all(q["asked_because"] is None for q in served.values() if q["on"] == ["sheet"])


def test_the_folder_filter_reads_every_file_of_a_class_and_not_only_its_examples(
    explorer_project: ExplorerProject,
) -> None:
    from ingence.api.routes.pages import _top
    from ingence.review.claim_classes import class_members

    project = explorer_project.project
    served = _everything(project)
    checks = [q for q in served if q["kind"] == "audit"]
    assert checks
    with open_catalog(project) as catalog:
        held = class_members(catalog)
    wider = 0
    for question in checks:
        members = held[(question["rule_id"], question["value"])]
        every = sorted(
            {_top(member.path, "directory" if member.is_folder else "file") for member in members}
        )
        assert sorted(question["tops"]) == every, question["key"]
        by_path = {member.path: member for member in members}
        shown = {
            _top(example, "directory" if by_path[example].is_folder else "file")
            for example in question["examples"]
        }
        assert shown <= set(every)
        wider += len(set(every) - shown)
        # The filter keeps a class by any folder it lies in, not only those its examples are in.
        for top in every:
            kept = _ranked(project, top=top, limit=1000, answered="show")["questions"]
            assert question["key"] in {q["key"] for q in kept}, (question["key"], top)
    # Patterns of pairs lie in the folders of both files of every pair.
    for question in (q for q in served if q["kind"] == "derivation pattern"):
        assert question["tops"]
        assert {_top(example.split(" -> ")[0], "file") for example in question["examples"]} <= set(
            question["tops"]
        )
    assert wider >= 0


def test_one_question_is_asked_for_by_its_key_and_says_what_it_is_about(
    tied_project: Path,
) -> None:
    whole = _everything(tied_project)
    tied = next(q for q in whole if q["kind"] == "word" and q["senses"])
    # A tied word is about its word.
    assert tied["value"] and tied["value"] in tied["question"]
    one = _ranked(tied_project, key=tied["key"], answered="show")
    assert [q["key"] for q in one["questions"]] == [tied["key"]]
    assert one["total"] == 1 and one["key"] == tied["key"]
    # The key narrows nothing else: the summary and the chips still count the whole order.
    assert one["summary"]["questions"] == len(whole)
    nothing = _ranked(tied_project, key="question:no_such_rule:nothing")
    assert nothing["questions"] == [] and nothing["total"] == 0


@pytest.fixture(scope="module")
def tied_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # The standing pool's planted word is read by the folder's domain, never tied. The review
    # pages' pool keeps a word that two domains spell differently, which is asked.
    from test_review_pages import _project

    return _project(tmp_path_factory.mktemp("ranked_tied")).path
