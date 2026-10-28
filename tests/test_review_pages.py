"""The Dashboard's review pages: the review happens in the browser.

Each review page says plainly what it asks. What is held here:

- a question about a word is plain: at most fifteen names, spanning its folders, the word marked
  inside each from the name's own slots, where it sits and how much of it there is;
- every sheet kind is a page that writes, with a note on every row, and a verdict given on the
  page is the same event the sheet import writes (parity, every kind);
- no route serves anybody's guesses, and an answer imported from a file says which file, in
  neutral words, including one stored before Import asked who wrote it;
- a program is one row, and nothing raised only inside it is among the data's questions;
- progress, order and filters, and the changes between two runs, chosen by parent.

Every name here is invented. The layout names of the program are packaging conventions.
"""

from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.domain_skew import ask_every_tie
from fixtures.one_folder_pairs import allow_pairs_in_one_folder
from fixtures.revision_4 import write_revision
from ingence.api.routes import changes as change_routes
from ingence.api.routes import pages
from ingence.api.routes.decide import (
    COMPUTER_READINGS,
    AnswerRefusedError,
    Decision,
    DecisionContext,
    QuestionMovedError,
    decide_target,
    define_word,
)
from ingence.api.server import ANSWER_ROUTE, WRITE_HEADER, DashboardServer
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import latest_by_target, notes_by_target, read_events
from ingence.record.sheets import import_sheet
from ingence.record.targets import class_target, extension_target, question_target
from ingence.record.verdicts import spelled_key
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
MOMENT = "2026-09-24T02:00:00Z"
#: 2020-01-01, old enough for revision 4's empty-file rule.
LONG_AGO = 1_577_836_800

AMBIGUOUS = "binding_is_ambiguous_outside_programs_and_domains"
FOLDERS = ("kelt", "quolt", "zeph")


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    """A laboratory's folders beside an installed program, raising one of every kind of row."""
    pool = base / "pool"
    # A word standing between two senses, in eighteen names across three folders.
    for folder in FOLDERS:
        for index in range(3):
            _write(pool / folder / f"vorn_sim_raw_{index}.csv", "a,b\n1,2\n")
            _write(pool / folder / f"vorn_sim_processed_{index}.csv", "a,b\n1,2\n")
    # A raw and processed pair, and an empty file left long ago.
    _write(pool / "plates" / "raw_plate_alpha.csv", "a,b\n1,2\n")
    _write(pool / "plates" / "processed_plate_alpha.csv", "a,b\n1,2\n")
    # The raw file an hour before the processed one: a pair's direction is its time.
    for turn, name in enumerate(("raw_plate_alpha.csv", "processed_plate_alpha.csv"), start=1):
        os.utime(pool / "plates" / name, (LONG_AGO + turn * 3600, LONG_AGO + turn * 3600))
    stub = pool / "plates" / "stub.dat"
    stub.write_bytes(b"")
    os.utime(stub, (LONG_AGO, LONG_AGO))
    # An extension nobody named, and a word nobody knows.
    _write(pool / "notes" / "trace.qzx", "words\n")
    _write(pool / "notes" / "Zorbex-Quillan_tamen.csv", "a\n")
    # An installed program, whose library names raise a count nobody should be asked about.
    app = pool / "tools" / "Fiji.app"
    _write(app / "ImageJ-linux64", "launcher")
    for number in range(12):
        _write(app / "jars" / f"vorn-library-{number}.jar")
    _write(app / "jars" / "n5-kelt-3.1.jar")
    for number in range(6):
        _write(app / "plugins" / f"brisa_plugin_{number}.jar")
    _write(app / "luts" / "fire.lut")
    _write(app / "luts" / "ice.lut")
    jre = app / "java" / "linux64" / "jdk1.8.0_172" / "jre"
    _write(jre / "release", "JAVA_VERSION")
    _write(jre / "bin" / "java", "binary")
    _write(jre / "lib" / "rt.jar")
    return pool


def _project(base: Path) -> Project:
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    write_revision(created.path)
    # Its pairs lie in one folder, which the identity leaves as siblings.
    allow_pairs_in_one_folder(created.path)
    created = ask_every_tie(created.path)  # a tied word in a few names is still asked
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFERENCE, started_at="t")
    return created


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """One project nothing in this module writes to: for the reading tests."""
    return _project(tmp_path_factory.mktemp("pages"))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A project of its own, for a test that writes."""
    return _project(tmp_path)


def one(project: Project, sql: str, parameters: list[Any] | None = None) -> Any:
    with open_catalog(project.path) as catalog:
        row = catalog.query(sql, parameters or []).fetchone()
    assert row is not None, sql
    return row[0] if len(row) == 1 else row


def page_of(project: Project, kind: str, **filters: Any) -> dict[str, Any]:
    with open_catalog(project.path) as catalog:
        return pages.page(catalog, project.path, kind, **filters)


def decide(project: Project, **said: Any) -> Any:
    with open_catalog(project.path) as catalog:
        context = DecisionContext(catalog, project.path)
        return decide_target(context, Decision(**said), actor="robin", occurred_at=MOMENT)


def payload_of(event: Any) -> dict[str, Any]:
    return json.loads(event.payload)


def sheet(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def imported(project: Project, path: Path, **arguments: Any) -> Any:
    arguments.setdefault("actor", "robin")
    arguments.setdefault("occurred_at", MOMENT)
    with open_catalog(project.path) as catalog:
        return import_sheet(catalog, project.path, path, **arguments)


# ---------------------------------------------------------------------------
# A question about a word, made plain.
# ---------------------------------------------------------------------------


def test_a_word_question_shows_at_most_fifteen_names_spanning_every_folder(shared) -> None:
    with open_catalog(shared.path) as catalog:
        shown = pages.word_page(catalog, rule_id=AMBIGUOUS, raised="sim")
    assert (shown["word"], shown["files"], shown["folder_count"]) == ("sim", 18, 3)
    assert len(shown["names"]) == pages.WORD_SAMPLE
    # Round-robin: the first three names come from three different folders.
    assert [name["folder"] for name in shown["names"][:3]] == list(FOLDERS)
    assert {name["folder"] for name in shown["names"]} == set(FOLDERS)
    assert shown["folders"] == [{"folder": folder, "files": 6} for folder in FOLDERS]
    # No meaning is offered: the page asks and the person answers.
    assert not {"senses", "recommended", "suggested"} & set(shown)


def test_the_word_is_marked_inside_each_name_from_the_name_s_own_slots(shared) -> None:
    with open_catalog(shared.path) as catalog:
        shown = pages.word_page(catalog, rule_id=AMBIGUOUS, raised="sim")
    for name in shown["names"]:
        assert name["found_by"] == "the name's own slots"
        ((start, end),) = name["spans"]
        assert name["name"][start:end] == "sim"


def test_a_word_no_rule_asked_about_is_found_by_its_names_and_its_case_kept(shared) -> None:
    with open_catalog(shared.path) as catalog:
        shown = pages.word_page(catalog, word="zorbex")
    ((name),) = shown["names"]
    ((start, end),) = name["spans"]
    assert name["name"][start:end] == "Zorbex"


def test_where_no_slot_names_the_word_a_search_of_the_name_marks_it() -> None:
    spans, how = pages._spans("Kelt_KELT-kelt.csv", "kelt", [], set())
    assert (spans, how) == ([[0, 4], [5, 9], [10, 14]], "a search of the name")


# ---------------------------------------------------------------------------
# The pages, their progress, order and filters.
# ---------------------------------------------------------------------------


def test_every_page_gives_each_row_its_target_its_words_and_its_top_level_folder(shared) -> None:
    for kind in pages.KINDS:
        shown = page_of(shared, kind)
        assert shown["rows"], kind
        assert shown["progress"] == {
            "rows": shown["progress"]["rows"],
            "answered": 0,
            "remaining": shown["progress"]["rows"],
        }
        for row in shown["rows"]:
            assert row["target"].split(":", 1)[0] in {
                "question",
                "class",
                "claim",
                "candidate",
                "extension",
            }
            assert row["folders"], (kind, row["target"])
            assert row["in_force"] is None and row["notes"] == []
            if kind != "questions" or not row["about_a_word"]:
                # A button shows its label, and a subtext only where the label alone would
                # mislead. The longer meaning is its hover.
                assert all(word["label"] and word["explained"] for word in row["words"]), kind


def test_the_questions_keep_the_queue_s_order_and_filter_by_folder_and_rule(shared) -> None:
    from ingence.api.routes.review import review

    shown = page_of(shared, "questions")
    with open_catalog(shared.path) as catalog:
        queue = review(catalog, limit=1000)
    asked = [row["target"] for row in shown["rows"]]
    expected = [
        question_target(item["rule_id"], item["raised"])
        for item in queue["questions"]
        if "installed_software" not in item["rule_id"]
    ]
    assert asked == expected
    kelt = page_of(shared, "questions", top="kelt")
    assert [row["raised"] for row in kelt["rows"]] == ["sim"]
    assert kelt["shown"]["rows"] == 1 and kelt["progress"]["rows"] == len(asked)
    only = page_of(shared, "questions", rule="content_unreadable")
    assert {row["rule_id"] for row in only["rows"]} == {"content_unreadable"}
    assert {"value": "kelt", "n": 1} in shown["filters"]["tops"]


def test_a_question_s_words_come_from_its_rule_and_a_word_question_offers_meanings(
    shared,
) -> None:
    rows = {row["rule_id"]: row for row in page_of(shared, "questions")["rows"]}
    stub = rows["content_unreadable"]
    assert [word["word"] for word in stub["words"]] == [
        "placeholder",
        "empty_output",
        "archive_link",
        "cannot_say",
    ]
    assert not stub["about_a_word"]
    # The row names what its rule tests, which is what its words come with.
    from ingence.record.verdicts import question_words

    assert stub["predicate"]
    assert [word["word"] for word in stub["words"]] == list(question_words(stub["predicate"]))
    word = rows[AMBIGUOUS]
    assert word["about_a_word"] and word["senses"] and word["words"] == []


def test_a_class_row_lists_its_members_and_a_claim_sample_spans_its_values(shared) -> None:
    classes = page_of(shared, "classes")
    assert all(row["members"] >= 2 for row in classes["rows"])
    plate = next(row for row in classes["rows"] if "plate" in row["spelled"])
    assert {member["name"] for member in plate["listed"]} == {
        "raw_plate_alpha.csv",
        "processed_plate_alpha.csv",
    }
    # The program's own classes are the program's, left off and counted.
    assert classes["inside_programs"] >= 1
    assert not any(row["folders"] == ["tools"] for row in classes["rows"])
    claims = page_of(shared, "claims", rule="pipeline_stage_from_name")
    by_value: dict[str, list[str]] = {}
    for row in claims["rows"]:
        by_value.setdefault(str(row["value"]), []).append(row["folder"])
    assert set(by_value) == {"raw", "processed"}
    # Each value lies in four folders, each holding at least a tenth of its files, so it is
    # shown in all four (sized to the value, where a fixed three would hide one).
    for folders in by_value.values():
        assert sorted(folders) == ["kelt", "plates", "quolt", "zeph"], "one per part"


def test_an_extension_row_is_every_file_of_an_extension_no_file_type_names(shared) -> None:
    rows = {row["extension"]: row for row in page_of(shared, "extensions")["rows"]}
    assert rows["qzx"]["files"] == 1
    assert rows["qzx"]["folders"] == ["notes"]
    assert "csv" not in rows


# ---------------------------------------------------------------------------
# A program is one row, and never a data question.
# ---------------------------------------------------------------------------


def test_a_program_is_one_row_saying_what_it_is_and_how_big(shared) -> None:
    with open_catalog(shared.path) as catalog:
        found = pages.programs(catalog, shared.path)
        files = one(
            shared,
            "SELECT count(*) FROM entries WHERE entry_type = 'file' "
            "AND starts_with(relative_path, 'tools/Fiji.app/')",
        )
    (fiji,) = found["programs"]
    assert fiji["title"] == f"a copy of Fiji, {files} files"
    assert fiji["path"] == "tools/Fiji.app"
    assert [part["name"] for part in fiji["parts"]][:2] == ["jars", "plugins"]
    assert [inside["names"] for inside in fiji["programs_inside"]] == [["Java runtime"]]
    assert fiji["target"] == question_target("installed_software_in_folder", "Fiji")
    assert [word["word"] for word in fiji["words"]] == ["correct", "wrong", "cannot_say"]
    # What was raised only inside it is listed under it, and nowhere among the questions.
    aside = {item["rule_id"] for item in fiji["set_aside"]}
    assert "stated_count_may_mark_replicates" in aside
    asked = page_of(shared, "questions")["rows"]
    assert not any(row["rule_id"] in aside for row in asked)
    assert not any("installed_software" in row["rule_id"] for row in asked)


# ---------------------------------------------------------------------------
# Every sheet kind writes, and a note on every row.
# ---------------------------------------------------------------------------


def _same_event(left: Any, right: Any) -> None:
    """Parity: kind, target, actor, scope, and the payload but for where it came from."""

    def without_source(event: Any) -> dict[str, Any]:
        said = payload_of(event)
        said.pop("source", None)
        return said

    assert (left.kind, left.target, left.actor, left.scope) == (
        right.kind,
        right.target,
        right.actor,
        right.scope,
    )
    assert without_source(left) == without_source(right)


def _last(project: Project) -> Any:
    return read_events(project.path)[-1]


def test_a_question_verdict_from_the_page_is_the_event_the_sheet_writes(project) -> None:
    finding_id, entry_id, raised = one(
        project,
        "SELECT finding_id, subject, raised[1] FROM findings WHERE rule_id = 'content_unreadable'",
    )
    headers = ["finding_id", "entry_id", "name", "verdict (placeholder | empty_output)", "note"]
    path = sheet(
        project.path.parent / "stubs.csv",
        headers,
        [[finding_id, entry_id, "stub.dat", "empty_output", "a run that found nothing"]],
    )
    assert imported(project, path).written == 1
    from_sheet = _last(project)
    from_page = decide(
        project,
        target=question_target("content_unreadable", raised),
        word="empty_output",
        note="a run that found nothing",
    )
    _same_event(from_sheet, from_page)
    assert payload_of(from_page)["source"] == {"page": "review"}


def test_a_verdict_on_a_word_question_from_the_page_is_the_event_the_sheet_writes(
    project,
) -> None:
    """Parity: the findings sheet answers a word question "correct" (the word does stand between
    these senses), and the page writes the sheet's event. The page does not take any word on a
    word question for a meaning and refuse it."""
    raised = one(project, "SELECT raised[1] FROM findings WHERE rule_id = ? LIMIT 1", [AMBIGUOUS])
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT finding_id, subject FROM findings WHERE rule_id = ? AND raised[1] = ?",
            [AMBIGUOUS, raised],
        ).fetchall()
    # Every row of the question filled, which is what one answer on the page says.
    path = sheet(
        project.path.parent / "words.csv",
        ["finding_id", "entry_id", "name", "verdict (correct | wrong)", "note"],
        [[finding, entry, "x", "correct", "it does stand between them"] for finding, entry in rows],
    )
    assert imported(project, path).written == 1
    from_sheet = _last(project)
    from_page = decide(
        project,
        target=question_target(AMBIGUOUS, raised),
        word="correct",
        note="it does stand between them",
    )
    _same_event(from_sheet, from_page)


def test_a_class_verdict_from_the_page_is_the_event_the_sheet_writes(project) -> None:
    key = one(
        project,
        "SELECT DISTINCT object_reference FROM claims WHERE rule_id = 'same_name_identity' "
        "AND object_reference LIKE '%plate%'",
    )
    path = sheet(
        project.path.parent / "classes.csv",
        ["class key", "members", "folder(s)", "verdict (one_thing | mixed)", "note"],
        [[spelled_key(key), "2", "plates", "mixed", "two batches"]],
    )
    assert imported(project, path, rule_id="same_name_identity").written == 1
    from_page = decide(
        project,
        target=class_target("same_name_identity", key),
        word="mixed",
        note="two batches",
    )
    _same_event(read_events(project.path)[0], from_page)


def test_a_claim_verdict_with_its_correction_is_the_event_the_sheet_writes(project) -> None:
    claim_id = one(
        project,
        "SELECT c.claim_id FROM claims c JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.rule_id = 'pipeline_stage_from_name' AND e.name = 'raw_plate_alpha.csv'",
    )
    path = sheet(
        project.path.parent / "stages.csv",
        ["rule", "claim_id", "name", "verdict (correct | wrong)", "if wrong, the stage", "note"],
        [["pipeline_stage_from_name", claim_id, "x", "wrong", "processed", "renamed later"]],
    )
    assert imported(project, path).written == 1
    from_page = decide(
        project,
        target=f"claim:{claim_id}",
        word="wrong",
        correct_value="processed",
        note="renamed later",
    )
    _same_event(read_events(project.path)[0], from_page)
    assert payload_of(from_page)["correct_stage"] == "processed"
    with pytest.raises(AnswerRefusedError, match="beside 'wrong'"):
        decide(project, target=f"claim:{claim_id}", word="correct", correct_value="raw")


def test_a_pair_verdict_from_the_page_is_the_event_the_sheet_writes(project) -> None:
    candidate_id = one(
        project,
        "SELECT c.candidate_id FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
        "WHERE s.name = 'raw_plate_alpha.csv'",
    )
    path = sheet(
        project.path.parent / "pairs.csv",
        ["candidate_id", "verdict (derived | reversed | unrelated)", "note"],
        [[candidate_id, "reversed", "the processed file came first"]],
    )
    assert imported(project, path).written == 1
    from_page = decide(
        project,
        target=f"candidate:{candidate_id}",
        word="reversed",
        note="the processed file came first",
    )
    _same_event(read_events(project.path)[0], from_page)
    with pytest.raises(AnswerRefusedError, match="not something a pair"):
        decide(project, target=f"candidate:{candidate_id}", word="one_thing")


def test_an_extension_answer_from_the_page_is_the_event_the_sheet_writes(project) -> None:
    path = sheet(
        project.path.parent / "extensions.csv",
        [
            "extension",
            "files",
            "what it is (your words)",
            "format / program / instrument it belongs to",
            "worth reading? (yes | no | never)",
        ],
        [["qzx", "1", "a tracker's trace", "a tracking program", "never"]],
    )
    assert imported(project, path).written == 1
    from_page = decide(
        project,
        target=extension_target("qzx"),
        word="never",
        described="a tracker's trace",
        belongs_to="a tracking program",
    )
    _same_event(read_events(project.path)[0], from_page)


def test_a_note_from_the_page_is_the_note_the_sheet_keeps_and_decides_nothing(project) -> None:
    finding_id, entry_id, raised = one(
        project,
        "SELECT finding_id, subject, raised[1] FROM findings WHERE rule_id = 'content_unreadable'",
    )
    path = sheet(
        project.path.parent / "noted.csv",
        ["finding_id", "entry_id", "name", "verdict (placeholder | empty_output)", "note"],
        [[finding_id, entry_id, "stub.dat", "", "left by the tracker"]],
    )
    assert imported(project, path).noted == 1
    target = question_target("content_unreadable", raised)
    from_page = decide(project, target=target, note="left by the tracker")
    _same_event(read_events(project.path)[0], from_page)
    assert latest_by_target(read_events(project.path)) == {}
    assert len(notes_by_target(read_events(project.path))[target]) == 2


def test_a_term_answer_from_the_page_is_the_event_the_term_sheet_writes(project) -> None:
    path = sheet(
        project.path.parent / "terms.csv",
        ["term as written", "files", "your answer (a sense, or a definition)"],
        [["sim", "18", "simulation [technique]"]],
    )
    assert imported(project, path).written == 1
    from_page = decide(
        project,
        target=question_target(AMBIGUOUS, "sim"),
        word="simulation [technique]",
    )
    _same_event(read_events(project.path)[0], from_page)


def test_a_meaning_of_the_person_s_own_is_stored_as_they_wrote_it(project) -> None:
    event = decide(
        project,
        target=question_target(AMBIGUOUS, "sim"),
        answer="Spindle Density (per cell)",
        theme_kind="measurement",
    )
    assert payload_of(event)["answer"] == "Spindle Density (per cell)"
    dismissed = decide(project, target="term:zorbex", word="not_a_word")
    assert (dismissed.kind.value, payload_of(dismissed)["verdict"]) == ("reject", "not_a_word")


def test_a_row_the_current_run_does_not_hold_is_a_page_out_of_date(project) -> None:
    with pytest.raises(QuestionMovedError):
        decide(project, target="claim:claim_0000", word="correct")
    with pytest.raises(QuestionMovedError):
        decide(project, target=extension_target("nothing"), word="yes")
    with pytest.raises(AnswerRefusedError, match="not a row"):
        decide(project, target="entry_0000", word="correct")
    assert read_events(project.path) == ()


def test_the_answers_show_on_their_rows_and_the_progress_counts_them(project) -> None:
    raised = one(project, "SELECT raised[1] FROM findings WHERE rule_id = 'content_unreadable'")
    decide(project, target=question_target("content_unreadable", raised), word="placeholder")
    decide(project, target=question_target(AMBIGUOUS, "sim"), note="ask the kelt group")
    shown = page_of(project, "questions", answered=True)
    rows = {row["target"]: row for row in shown["rows"]}
    assert rows[question_target("content_unreadable", raised)]["in_force"]["word"] == "placeholder"
    word = rows[question_target(AMBIGUOUS, "sim")]
    assert word["in_force"] is None
    assert [note["note"] for note in word["notes"]] == ["ask the kelt group"]
    assert shown["progress"]["answered"] == 1
    assert shown["progress"]["remaining"] == shown["progress"]["rows"] - 1


def test_an_answered_row_leaves_every_page_and_comes_back_when_asked_for(project) -> None:
    """A row whose issue is resolved disappears from sight, to focus on the remaining issues. A
    decision in force takes a row off its page at the next read, which is a reload's read too,
    since it comes from the Record. A note decides nothing and leaves the row where it was.
    ``answered`` brings the answered rows back."""
    raised = one(project, "SELECT raised[1] FROM findings WHERE rule_id = 'content_unreadable'")
    answered = question_target("content_unreadable", raised)
    noted = question_target(AMBIGUOUS, "sim")
    claim = page_of(project, "claims")["rows"][0]["target"]
    candidate = page_of(project, "candidates")["rows"][0]["target"]
    group = page_of(project, "classes")["rows"][0]["target"]
    before = {kind: page_of(project, kind) for kind in pages.KINDS}
    decide(project, target=answered, word="placeholder")
    decide(project, target=noted, note="ask the kelt group")
    decide(project, target=claim, word="correct")
    decide(project, target=candidate, word="derived")
    decide(project, target=group, word="one_thing")
    decide(project, target=extension_target("qzx"), word="never")
    gone = {
        "questions": answered,
        "claims": claim,
        "candidates": candidate,
        "classes": group,
        "extensions": extension_target("qzx"),
    }
    for kind, target in gone.items():
        after = page_of(project, kind)
        assert target not in {row["target"] for row in after["rows"]}, kind
        assert after["hidden_answered"] == 1, kind
        assert len(after["rows"]) == len(before[kind]["rows"]) - 1, kind
        # The progress still counts every row, answered or not.
        assert after["progress"]["rows"] == before[kind]["progress"]["rows"], kind
        assert after["progress"]["remaining"] == after["progress"]["rows"] - 1, kind
        shown = page_of(project, kind, answered=True)
        assert target in {row["target"] for row in shown["rows"]}, kind
        assert shown["hidden_answered"] == 0 and shown["answered_shown"], kind
    # A note alone answers nothing: the row stays, with its note.
    kept = {row["target"]: row for row in page_of(project, "questions")["rows"]}
    assert [note["note"] for note in kept[noted]["notes"]] == ["ask the kelt group"]
    # Over HTTP: the default leaves the row out and ``answered=show`` asks for it.
    server = DashboardServer(project.path, port=0)
    server.start()
    try:
        status, body = _get(server, "/api/pages/questions")
        assert status == 200 and json.loads(body)["hidden_answered"] == 1
        assert answered not in {row["target"] for row in json.loads(body)["rows"]}
        status, body = _get(server, "/api/pages/questions?answered=show")
        assert answered in {row["target"] for row in json.loads(body)["rows"]}
    finally:
        server.stop()


def test_each_review_tab_counts_what_waits_on_its_page(project) -> None:
    """If there is nothing to review, the dashboard makes that known clearly. A tab's count is its
    page's own remaining, and an answer lowers it at once."""
    from ingence.api.server import WEB_ROOT

    with open_catalog(project.path) as catalog:
        before = pages.waiting(catalog, project.path)
    assert set(before) == {*pages.KINDS, "programs"}
    for kind in pages.KINDS:
        assert before[kind] == page_of(project, kind)["progress"]["remaining"], kind
    assert before["programs"] == 1
    decide(project, target=extension_target("qzx"), word="never")
    with open_catalog(project.path) as catalog:
        after = pages.waiting(catalog, project.path)
    assert after["extensions"] == before["extensions"] - 1
    assert {kind: n for kind, n in after.items() if kind != "extensions"} == {
        kind: n for kind, n in before.items() if kind != "extensions"
    }
    # Over HTTP, and every count has its tab.
    server = DashboardServer(project.path, port=0)
    server.start()
    try:
        # Counted from the server's start, on a thread of its own: once counted, the request is
        # answered with the counts, never "counting".
        from ingence.api.server import waiting_settled

        assert waiting_settled(60)
        status, body = _get(server, "/api/waiting")
        assert status == 200 and json.loads(body) == after
    finally:
        server.stop()
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    for kind in after:
        assert f'data-waiting="{kind}"' in page, kind


def test_an_answered_program_leaves_its_page_too(project) -> None:
    with open_catalog(project.path) as catalog:
        (fiji,) = pages.programs(catalog, project.path)["programs"]
    decide(project, target=fiji["target"], word="correct")
    with open_catalog(project.path) as catalog:
        hidden = pages.programs(catalog, project.path)
        shown = pages.programs(catalog, project.path, answered=True)
    assert hidden["programs"] == [] and hidden["hidden_answered"] == 1
    assert [row["target"] for row in shown["programs"]] == [fiji["target"]]
    assert hidden["progress"] == {"rows": 1, "answered": 1, "remaining": 0}


# ---------------------------------------------------------------------------
# No layer of guesses. An imported answer says which file it came from.
# ---------------------------------------------------------------------------


def _get(server: DashboardServer, route: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + route) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        error.close()
        return error.code, body


def _post(server: DashboardServer, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        server.url.rstrip("/") + ANSWER_ROUTE, data=json.dumps(body).encode(), method="POST"
    )
    request.add_header("Content-Type", "application/json")
    request.add_header(WRITE_HEADER, "1")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        outcome = (error.code, json.loads(error.read()))
        error.close()
        return outcome


def _every_read(project: Project) -> list[str]:
    word = f"/api/word?rule={AMBIGUOUS}&raised=sim"
    return [
        "/api/project",
        "/api/inventory",
        "/api/review",
        "/api/record",
        "/api/coverage",
        "/api/programs",
        "/api/runs",
        word,
        "/api/word?word=zorbex",
        *[f"/api/pages/{kind}" for kind in pages.KINDS],
    ]


def test_no_route_serves_a_guess_and_no_page_carries_one(project) -> None:
    server = DashboardServer(project.path, port=0)
    server.start()
    try:
        status, _ = _get(server, "/api/suggestions")
        assert status == 404
        for route in _every_read(project):
            status, body = _get(server, route)
            assert status == 200, (route, body)
            for word in ('"guesses"', '"suggested_by"', '"take_guess"', '"from_guess"'):
                assert word not in body, (route, word)
    finally:
        server.stop()


def _stored(project: Project, target: str, source: dict[str, Any]) -> None:
    """An answer as a Record written before Import asked who wrote a file holds it."""
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    append_event(
        project.path,
        kind=RecordEventKind.ANNOTATE,
        actor="tomas",
        occurred_at=MOMENT,
        target=target,
        against_run_id=INFERENCE,
        payload={"worth_reading": "never", "extension": "qzx", "source": source},
        scope=PROJECT_SCOPE,
    )


def test_a_stored_pre_filled_answer_shows_the_file_it_came_from_and_never_a_name(
    project,
) -> None:
    """Records hold events whose source names who pre-filled them (``prefilled_by``). They load
    and show, and what is shown is where the answer came from, never that name."""
    _stored(project, extension_target("qzx"), {"page": "extensions", "prefilled_by": "wren"})
    shown = page_of(project, "extensions", answered=True)
    (row,) = [row for row in shown["rows"] if row["target"] == extension_target("qzx")]
    assert row["in_force"]["imported_from"] == "a file"
    assert row["in_force"]["actor"] == "tomas"
    assert "wren" not in json.dumps(shown)

    _stored(project, extension_target("qzx"), {"sheet": "extensions.csv", "prefilled_by": "wren"})
    shown = page_of(project, "extensions", answered=True)
    (row,) = [row for row in shown["rows"] if row["target"] == extension_target("qzx")]
    assert row["in_force"]["imported_from"] == "extensions.csv"
    assert "wren" not in json.dumps(shown)


def test_the_see_verb_takes_no_file_of_guesses(project) -> None:
    import subprocess
    import sys

    helped = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "see", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert helped.returncode == 0
    assert "--suggestions" not in helped.stdout and "--suggested-by" not in helped.stdout


# ---------------------------------------------------------------------------
# The one writing route, and what it will not do.
# ---------------------------------------------------------------------------


def test_the_new_reads_refuse_a_write_and_the_one_route_keeps_its_guards(shared) -> None:
    server = DashboardServer(shared.path, port=0)
    server.start()
    try:
        for route in (
            "/api/pages/questions",
            "/api/word",
            "/api/programs",
            "/api/runs",
            "/api/changes",
        ):
            request = urllib.request.Request(
                server.url.rstrip("/") + route, data=b"{}", method="POST"
            )
            request.add_header("Content-Type", "application/json")
            request.add_header(WRITE_HEADER, "1")
            with pytest.raises(urllib.error.HTTPError) as raised, urllib.request.urlopen(request):
                pass
            raised.value.close()
            assert raised.value.code == 404, route
        body = json.dumps({"actor": "robin", "target": "extension:qzx", "word": "yes"}).encode()
        for headers in (
            {"Content-Type": "application/json"},
            {"Content-Type": "application/json", WRITE_HEADER: "1", "Origin": "http://elsewhere"},
        ):
            request = urllib.request.Request(
                server.url.rstrip("/") + ANSWER_ROUTE, data=body, method="POST"
            )
            for name, value in headers.items():
                request.add_header(name, value)
            with pytest.raises(urllib.error.HTTPError) as raised, urllib.request.urlopen(request):
                pass
            raised.value.close()
            assert raised.value.code == 403
        assert _post(server, {"target": "extension:qzx", "word": "yes"})[0] == 400
    finally:
        server.stop()
    assert read_events(shared.path) == ()


# ---------------------------------------------------------------------------
# What changed between two runs, chosen by parent.
# ---------------------------------------------------------------------------


def test_the_changes_between_two_inferences_count_what_moved(project) -> None:
    """A word defined, the names read again, the rules applied again: the page says what moved."""
    with open_catalog(project.path) as catalog:
        define_word(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            word="zorbex",
            answer="zorbex_reagent",
            theme_kind="reagent",
        )
    _write(project.path.parent / "pool" / "plates" / "raw_plate_beta.csv", "a,b\n1,2\n")
    run_census(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    run_inference(project, run_id="run_20260101T000004Z_444444444444", started_at="t")

    listed = change_routes.runs(project.path)
    kinds = [(run["run_id"][-4:], run["inference"]) for run in listed["runs"]]
    assert kinds == [("1111", False), ("2222", True), ("3333", False), ("4444", True)]
    # Chosen by parent and by what each run holds, never by profile: both inferences say census.
    assert {run["profile"] for run in listed["runs"]} == {"census"}
    assert listed["pair"] == {"before": INFERENCE, "after": "run_20260101T000004Z_444444444444"}
    # Every run named for a person, numbered by when it began, and an inference described by the
    # run it was built from, named the same way.
    assert [run["number"] for run in listed["runs"]] == [1, 2, 3, 4]
    assert all(run["label"].startswith(f"Scan {run['number']}, ") for run in listed["runs"])
    assert listed["runs"][1]["described"] == "rules applied to Scan 1"
    assert listed["runs"][1]["parent_label"] == listed["runs"][0]["label"]

    moved = change_routes.changes(project.path, before=None, after=None)
    assert moved["bindings"]["moved"] >= 1
    assert {"group": "word", "n": moved["bindings"]["moved"]} in moved["bindings"]["by_group"]
    (example,) = [item for item in moved["bindings"]["examples"] if item["token"] == "zorbex"]
    assert example["now"] == "zorbex_reagent"
    assert moved["bindings"]["new"] >= 1  # the new file's components
    gained = {held["rule_id"]: held["gained"] for held in moved["claims"]["by_rule"]}
    assert gained.get("pipeline_stage_from_name", 0) >= 1
    names = {
        example["name"]
        for held in moved["claims"]["by_rule"]
        for example in held["examples"]
        if example["way"] == "gained"
    }
    assert "raw_plate_beta.csv" in names
    assert {"raised", "cleared", "by_rule"} <= set(moved["findings"])
    # Said first in plain sentences, every figure one the comparison counted.
    said = moved["said"]
    gained = moved["claims"]["gained"]
    assert any(sentence.startswith(f"Conclusions: {gained:,} more") for sentence in said)
    assert any("in names now read differently" in sentence for sentence in said)
    # The word was defined at a moment outside the two runs, so nothing was written between.
    assert moved["answers"] == {"decided": 0, "noted": 0, "edited": 0}
    assert "Nothing was written to the Record between the two runs." in said
    with pytest.raises(LookupError, match="two different runs"):
        change_routes.changes(project.path, before=INFERENCE, after=INFERENCE)


def test_a_comparison_is_said_in_plain_sentences_the_largest_change_first() -> None:
    def section(gained: int, lost: int, rules: list[tuple[str, int, int]]) -> dict[str, Any]:
        return {
            "gained": gained,
            "lost": lost,
            "by_rule": [{"rule_id": rule, "gained": g, "lost": lo} for rule, g, lo in rules],
        }

    found = {
        "before": {"counts": {"entries": 100}},
        "after": {"counts": {"entries": 112}},
        "bindings": {"moved": 0, "new": 0, "gone": 0},
        "claims": section(412, 3, [("format_rule", 412, 0), ("stage_rule", 0, 3)]),
        "findings": {
            **section(0, 0, []),
            "raised": 5,
            "cleared": 9,
            "by_rule": [{"rule_id": "doubt_rule", "raised": 5, "cleared": 9}],
        },
        "candidates": section(0, 0, []),
        "answers": {"decided": 3, "noted": 1},
    }
    assert change_routes.said_plainly(found) == [
        "Conclusions: 412 more, 3 fewer; most of it format_rule (+412).",
        "Flags: 5 raised, 9 cleared; all of it doubt_rule (+5, -9).",
        "12 more items in the index.",
        "Written to the Record between the two runs: 3 answers and 1 note.",
        "No change in words or proposed pairs.",
    ]


def test_a_comparison_across_a_new_name_reader_says_the_reader_changed() -> None:
    """After a whole rebind under a new tokeniser, the comparison must not say that thousands of
    words in names are new and as many gone. A component's identity holds its reader's version, so
    every word is new by identity although no name changed. The sentence says the reader changed,
    and that the names are what they were."""
    empty = {"gained": 0, "lost": 0, "by_rule": []}
    found = {
        "before": {"counts": {"entries": 40}},
        "after": {"counts": {"entries": 40}},
        "bindings": {
            "moved": 0,
            "new": 212,
            "gone": 211,
            "readers": {"before": ["9"], "after": ["10"]},
        },
        "claims": empty,
        "findings": {"raised": 0, "cleared": 0, "by_rule": []},
        "candidates": empty,
        "answers": {"decided": 0, "noted": 0},
    }
    said = change_routes.said_plainly(found)
    assert said[0] == (
        "The name reader changed between these runs (version 9 before, 10 after), so every "
        "word's reading was made again and none can be matched word for word: 212 readings "
        "now, 211 before. The names themselves are what they were."
    )
    assert not any("words in names are new" in sentence for sentence in said)
    # One reader on both sides: the words' own sentence.
    found["bindings"]["readers"] = {"before": ["10"], "after": ["10"]}
    assert any(
        "words in names are new" in sentence for sentence in change_routes.said_plainly(found)
    )


# ---------------------------------------------------------------------------
# A claim sample sized to its value.
# ---------------------------------------------------------------------------


def _members(spec: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    """Invented conclusions: ``(folder, extension, how many)`` each."""
    made: list[dict[str, Any]] = []
    for folder, form, count in spec:
        for index in range(count):
            made.append(
                {
                    "claim_id": f"claim_{folder}_{form}_{index:03d}".replace("/", "_"),
                    "folder": folder,
                    "form": form,
                    "name": f"{folder.replace('/', '_')}_vorn_{index:03d}.{form}",
                }
            )
    return made


def test_a_value_in_one_place_shows_three_and_all_of_a_small_one() -> None:
    """Three, where three is representative: one folder, one form."""
    one_place = pages.representative_sample(_members([("kelt/run1", "csv", 40)]))
    assert len(one_place.files) == pages.CLAIMS_AT_LEAST
    assert one_place.parts == 1
    assert len(pages.representative_sample(_members([("kelt", "csv", 2)])).files) == 2


def test_every_part_holding_a_tenth_is_shown() -> None:
    """Five folders of an experiment, each a fifth of the value: five files, one from each."""
    spec = [(f"zib/sub{index}", "tif", 8) for index in range(5)]
    sample = pages.representative_sample(_members(spec))
    assert sorted(row["folder"] for row in sample.files) == [f"zib/sub{i}" for i in range(5)]


def test_a_second_form_in_one_folder_is_a_part_of_its_own() -> None:
    sample = pages.representative_sample(
        _members([("kelt", "csv", 30), ("kelt", "txt", 10), ("quolt", "csv", 10)])
    )
    assert {(row["folder"], row["form"]) for row in sample.files} == {
        ("kelt", "csv"),
        ("kelt", "txt"),
        ("quolt", "csv"),
    }


def test_a_thin_spread_shows_parts_until_half_is_covered_and_never_more_than_the_ceiling() -> None:
    """Thirty folders of three each: no part is a tenth, so parts are shown until half is."""
    spec = [(f"brisa{index:02d}", "nd2", 3) for index in range(30)]
    sample = pages.representative_sample(_members(spec))
    assert len(sample.files) == pages.CLAIMS_AT_MOST
    assert len({row["folder"] for row in sample.files}) == pages.CLAIMS_AT_MOST
    # Twelve such parts, none a tenth: six of them hold half, so six files.
    few = pages.representative_sample(_members([(f"brisa{i}", "nd2", 3) for i in range(12)]))
    assert len(few.files) == 6


def test_the_parts_are_counted_below_the_deepest_folder_holding_every_file() -> None:
    """A value inside one experiment is divided by that experiment's subfolders."""
    spec = [("zeph/exp/a", "csv", 5), ("zeph/exp/b", "csv", 5), ("zeph/exp/c/deeper", "csv", 5)]
    sample = pages.representative_sample(_members(spec))
    assert sample.parts == 3
    assert {row["folder"].split("/")[2] for row in sample.files} == {"a", "b", "c"}


def test_copies_of_one_name_are_shown_once() -> None:
    """A thumbnail cache in every folder is one name: shown once, not once per folder."""
    copies = [
        {
            "claim_id": f"claim_{index:02d}",
            "folder": f"kelt{index}",
            "form": "db",
            "name": "vorn.db",
        }
        for index in range(12)
    ]
    assert len(pages.representative_sample(copies).files) == 1
    mixed = [*copies, *_members([("kelt0", "db", 2)])]
    names = [row["name"] for row in pages.representative_sample(mixed).files]
    assert len(names) == len(set(names)) == pages.CLAIMS_AT_LEAST


def test_a_sample_is_the_same_however_its_members_arrive() -> None:
    spec = [("kelt", "csv", 7), ("quolt", "csv", 4), ("zeph", "txt", 2)]
    forward = pages.representative_sample(_members(spec))
    backward = pages.representative_sample(list(reversed(_members(spec))))
    assert [row["claim_id"] for row in forward.files] == [row["claim_id"] for row in backward.files]


# ---------------------------------------------------------------------------
# "Not a word" asks only whether it is a person's word.
# ---------------------------------------------------------------------------


def test_not_a_word_says_what_the_string_is_to_a_computer(project) -> None:
    """Answering "not a word" is a human response. There are many word-shaped things, like
    extensions, signifiers, or even just letter combinations, that mean something to a computer
    but not necessarily to a human. The reading is kept beside the rejection."""
    unasked = decide(project, target="term:zorbex", word="not_a_word", reading="a_code")
    assert unasked.kind.value == "reject"
    assert payload_of(unasked)["verdict"] == "not_a_word"
    assert payload_of(unasked)["reading"] == "a_code"
    asked = decide(
        project,
        target=question_target(AMBIGUOUS, "sim"),
        word="not_a_word",
        reading="an_extension",
    )
    assert (asked.kind.value, payload_of(asked)["reading"]) == ("reject", "an_extension")
    rows = {row["target"]: row for row in page_of(project, "questions", answered=True)["rows"]}
    shown = rows[question_target(AMBIGUOUS, "sim")]["in_force"]
    assert (shown["word"], shown["reading"]) == ("not_a_word", "an_extension")
    # "Not a word" alone is still an answer, and says no more.
    plain = decide(project, target="term:zorbex", word="not_a_word")
    assert "reading" not in payload_of(plain)


def test_a_reading_is_one_of_the_four_and_only_beside_not_a_word(project) -> None:
    with pytest.raises(AnswerRefusedError, match="not something a string can be"):
        decide(project, target="term:zorbex", word="not_a_word", reading="a_poem")
    with pytest.raises(AnswerRefusedError, match="only beside 'not a word'"):
        decide(project, target="term:zorbex", note="kelt", reading="a_code")
    with pytest.raises(AnswerRefusedError, match="only beside 'not a word'"):
        decide(
            project,
            target=question_target(AMBIGUOUS, "sim"),
            word="simulation [technique]",
            reading="a_code",
        )
    assert read_events(project.path) == ()


def test_the_shape_of_a_string_is_evidence_for_a_computer_reading() -> None:
    def names(*spelled: tuple[str, int, int]) -> list[dict[str, Any]]:
        return [{"name": name, "spans": [[start, end]]} for name, start, end in spelled]

    assert pages.computer_shape("kelt", extension_of=None, carried=0, names=[]) == {}
    extension = pages.computer_shape("zorbex", extension_of="ZORBEX trace", carried=3, names=[])
    assert extension["an_extension"] == "3 files here end in .zorbex (ZORBEX trace)"
    # A spelling some file type has and no file here carries is not enough (a time's "PM").
    assert pages.computer_shape("pm", extension_of="Perl module", carried=0, names=[]) == {}
    # A code is offered and never suggested: its shape fits a laboratory's glued shorthand too.
    for spelled in ("vx650", "kelt30min", "10x"):
        assert pages.computer_shape(spelled, extension_of=None, carried=0, names=[]) == {}
    first = pages.computer_shape(
        "brisa",
        extension_of=None,
        carried=0,
        names=names(("brisa_kelt_1.csv", 0, 5), ("brisa_vorn_2.csv", 0, 5)),
    )
    assert first["a_program_mark"] == "it stands first in all 2 names shown"
    last = pages.computer_shape(
        "quolt",
        extension_of=None,
        carried=0,
        names=names(("kelt_quolt.csv", 5, 10), ("vorn_2_quolt.txt", 7, 12)),
    )
    assert last["a_program_mark"].startswith("it stands last, before the extension")
    # One name is not a pattern, nor are copies of one name. A word in the middle is no mark.
    for copies in (1, 2):
        shown = names(*[("brisa_1.csv", 0, 5)] * copies)
        assert pages.computer_shape("brisa", extension_of=None, carried=0, names=shown) == {}
    assert (
        pages.computer_shape(
            "kelt",
            extension_of=None,
            carried=0,
            names=names(("vorn_kelt_1.csv", 5, 9), ("zib_kelt_2.csv", 4, 8)),
        )
        == {}
    )


def test_a_word_page_offers_the_computer_readings_those_its_shape_fits_first(
    tmp_path: Path,
) -> None:
    """An extension written inside a name: the table measured from a ``.zorbex`` trace."""
    pool = tmp_path / "pool"
    for index in (1, 2):
        _write(pool / "kelt" / f"trace_{index:02d}.zorbex")
        _write(pool / "kelt" / f"vorn_{index:02d}.zorbex_results.csv", "a,b\n1,2\n")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    created = ask_every_tie(created.path)  # a tied word in a few names is still asked
    run_census(created, run_id=CENSUS, started_at="t")
    with open_catalog(created.path) as catalog:
        shown = pages.word_page(catalog, word="zorbex", project=created.path)
    offered = [reading["word"] for reading in shown["computer"]]
    assert offered[0] == "an_extension"
    assert sorted(offered) == sorted(COMPUTER_READINGS)
    assert shown["computer"][0]["shape"] == "2 files here end in .zorbex"
    assert all(reading["label"] and reading["explained"] for reading in shown["computer"])
    assert [item["word"] for item in shown["cannot_say"]] == ["cannot_say"]


# ---------------------------------------------------------------------------
# One timeline of runs, each answer marked where it took effect.
# ---------------------------------------------------------------------------


def test_each_answer_is_marked_at_the_first_run_that_began_after_it(project) -> None:
    """Written after the inference, the census that follows is where it took effect. A note
    written after the latest run began is waiting for the next run."""
    with open_catalog(project.path) as catalog:
        define_word(
            catalog,
            project.path,
            actor="robin",
            occurred_at="2026-01-01T12:00:00Z",
            word="zorbex",
            answer="zorbex_reagent",
            theme_kind="reagent",
        )
    later_census = "run_20260102T000000Z_333333333333"
    run_census(project, run_id=later_census, started_at="t")
    run_inference(project, run_id="run_20260102T000001Z_444444444444", started_at="t")
    decide(project, target="term:zorbex", note="the kelt group's reagent")

    shown = change_routes.timeline(project.path)
    by_run = {run["run_id"]: run for run in shown["runs"]}
    assert [run["number"] for run in shown["runs"]] == [1, 2, 3, 4]
    assert by_run[CENSUS]["took_effect"]["decided"] == 0
    assert by_run[INFERENCE]["took_effect"]["decided"] == 0
    landed = by_run[later_census]["took_effect"]
    assert (landed["decided"], landed["noted"]) == (1, 0)
    assert landed["by_actor"] == {"robin": 1}
    assert landed["by_page"] == {"words": 1}
    (answer,) = landed["answers"]
    assert (answer["target"], answer["word"]) == ("term:zorbex", "zorbex_reagent")
    # Made while reading the inference, taking effect in the census after it.
    assert by_run[INFERENCE]["made_here"] == 1
    assert answer["against_run_id"] == INFERENCE
    waiting = shown["waiting"]
    assert (waiting["decided"], waiting["noted"]) == (0, 1)
    assert waiting["answers"][0]["note"] == "the kelt group's reagent"
    assert waiting["by_page"] == {"words": 1}
    # Each run opens the comparison with the latest earlier run of its own kind: an inference
    # with the inference before it, as the page's pair does. Names read again with the census.
    assert by_run["run_20260102T000001Z_444444444444"]["compare_with"] == INFERENCE
    assert by_run[later_census]["compare_with"] == CENSUS
    assert by_run[INFERENCE]["compare_with"] == CENSUS
    assert by_run[CENSUS]["compare_with"] is None
    assert shown["pair"] == change_routes.runs(project.path)["pair"]


def test_the_timeline_is_served_and_writes_nothing(project) -> None:
    # A server warms a run that has no readouts as it starts (derived views under projections/).
    # It is warmed first here, so what is compared is the timeline's own writing.
    from ingence.readouts import warm

    warm(project.path, INFERENCE)
    before = sorted(str(path) for path in project.path.rglob("*"))
    with DashboardServer(project.path, port=0, warm_search=False) as server:
        request = urllib.request.urlopen(server.url.rstrip("/") + "/api/timeline")
        with request as response:
            served = json.loads(response.read())
    assert [run["run_id"] for run in served["runs"]] == [CENSUS, INFERENCE]
    assert served["waiting"]["decided"] == 0
    assert sorted(str(path) for path in project.path.rglob("*")) == before
