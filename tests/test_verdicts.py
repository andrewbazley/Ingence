"""Verdicts become Record events.

A review sheet carries verdicts in a fixed vocabulary, and the Candidates view offers four of the
same words. What each word decides is one table. What a row is about is found in the current run or
the row is refused. Nothing a person decides stays a spreadsheet.

Every name here is invented, and every sheet is built from the review pack's column shapes with
invented rows.
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
from ingence.api.routes import views
from ingence.api.routes.decide import (
    AnswerRefusedError,
    CandidateMovedError,
    answer_candidate,
    current_questions,
)
from ingence.api.server import ANSWER_ROUTE, WRITE_HEADER, DashboardServer
from ingence.core.ids import Scope, ScopeKind
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.infer.run import run_inference
from ingence.record.log import latest_by_target, notes_by_target, read_events
from ingence.record.precision import precision_by_rule
from ingence.record.sheets import (
    DISAGREES,
    NOT_A_FOLDER,
    NOT_OFFERED,
    OTHER_FOLDER,
    STALE,
    SheetRefusedError,
    import_sheet,
)
from ingence.record.targets import (
    candidate_target,
    claim_target,
    class_target,
    extension_named_by,
    extension_target,
    folder_target,
    question_target,
    rule_named_by,
    target_kind,
)
from ingence.record.verdicts import CANDIDATE_WORDS, VERDICTS, WORTH_READING, spelled_key
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFERENCE = "run_20260101T000002Z_222222222222"
MOMENT = "2026-09-21T22:00:00Z"
#: 2020-01-01, before the date revision 4's stub rule names.
LONG_AGO = 1_577_836_800
#: An hour, in seconds.
HOUR = 3600


def _written_in_turn(*paths: Path) -> None:
    """Each file is written an hour after the one before.

    A pair's direction is the earlier file's, by its earliest credible time, and never its stage
    words.
    """
    for turn, path in enumerate(paths):
        at = LONG_AGO + (turn + 1) * HOUR
        os.utime(path, (at, at))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A stub, a raw and processed pair, and a tied term, under revision 4."""
    pool = tmp_path / "pool"
    plates = pool / "plates"
    plates.mkdir(parents=True)
    (plates / "raw_plate_alpha.csv").write_text("a,b\n1,2\n")
    (plates / "processed_plate_alpha.csv").write_text("a,b\n1,2\n")
    stub = plates / "stub.dat"
    stub.write_bytes(b"")
    os.utime(stub, (LONG_AGO, LONG_AGO))
    (pool / "kelt").mkdir()
    for name in ("vorn_sim_raw.csv", "vorn_sim_processed.csv"):
        (pool / "kelt" / name).write_text("a,b\n1,2\n")
    _written_in_turn(plates / "raw_plate_alpha.csv", plates / "processed_plate_alpha.csv")
    _written_in_turn(pool / "kelt" / "vorn_sim_raw.csv", pool / "kelt" / "vorn_sim_processed.csv")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    allow_pairs_in_one_folder(created.path)
    created = ask_every_tie(created.path)  # a tied word in a few names is still asked (0.4.0)
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFERENCE, started_at="t")
    return created


def one(project: Project, sql: str) -> Any:
    with open_catalog(project.path) as catalog:
        row = catalog.query(sql).fetchone()
    assert row is not None, sql
    return row[0] if len(row) == 1 else row


#: The plate pair, which is one of the two proposed pairs.
#: The plate pair the name-identity derivation proposes. The one-file-at-two-steps rule
#: proposes the same two files; it is left out here.
PAIR = (
    "SELECT c.candidate_id FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
    "WHERE s.name = 'raw_plate_alpha.csv' AND c.rule_id <> 'same_file_later_step'"
)


def sheet(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def imported(project: Project, path: Path, **arguments: Any):
    arguments.setdefault("actor", "robin")
    arguments.setdefault("occurred_at", MOMENT)
    with open_catalog(project.path) as catalog:
        return import_sheet(catalog, project.path, path, **arguments)


def payload_of(event: Any) -> dict[str, Any]:
    return json.loads(event.payload)


#: The unread-vocabulary rule as revision 4 declares it, naming the program scope.
UNREAD_IN_FOLDER = "vocabulary_unread_in_folder_outside_programs"

STUB_HEADERS = ["finding_id", "entry_id", "name", "verdict (stub | not_stub | cannot_say)", "note"]
PAIR_HEADERS = [
    "candidate_id",
    "source (earlier stage: the proposed origin)",
    "verdict (derived | reversed | unrelated | cannot_say)",
    "note",
]
STAGE_HEADERS = [
    "rule",
    "claim_id",
    "name",
    "verdict (correct | wrong | cannot_say)",
    "if wrong, the stage it is",
    "note",
]
CLASS_HEADERS = ["class key", "members", "folder(s)", "verdict (one_thing | mixed | cannot_say)"]
TERM_HEADERS = ["term as written", "files", "your answer (one of the senses, or a definition)"]


# ---------------------------------------------------------------------------
# The targets and the table.
# ---------------------------------------------------------------------------


def test_each_target_is_spelled_once_and_says_what_it_is_about() -> None:
    assert target_kind(question_target("rule_a", "sd")) == "question"
    assert target_kind(candidate_target("candidate_1")) == "candidate"
    assert target_kind(claim_target("claim_1")) == "claim"
    assert target_kind(class_target("rule_b", '[["word","vorn"]]')) == "class"
    assert target_kind("entry_1") == ""
    assert rule_named_by(question_target("rule_a", "sd")) == "rule_a"
    assert rule_named_by(class_target("rule_b", "key")) == "rule_b"
    assert rule_named_by(candidate_target("candidate_1")) is None


def test_a_class_target_digests_its_key_so_it_stays_one_short_line() -> None:
    long_key = json.dumps([["word", "x" * 500]])
    target = class_target("same_name_identity", long_key)
    assert len(target) < 100
    assert target == class_target("same_name_identity", long_key)
    assert target != class_target("name_family_without_stages", long_key)


def test_every_word_decides_confirm_reject_or_annotate_and_not_deciding_is_counted() -> None:
    assert set(VERDICTS.values()) == {
        RecordEventKind.CONFIRM,
        RecordEventKind.REJECT,
        RecordEventKind.ANNOTATE,
    }
    assert VERDICTS["cannot_say"] is RecordEventKind.ANNOTATE
    assert VERDICTS["reversed"] is VERDICTS["unrelated"] is RecordEventKind.REJECT
    assert set(CANDIDATE_WORDS) <= set(VERDICTS)


def test_a_class_key_is_read_as_its_components() -> None:
    assert spelled_key('[["word","sd"],["word","vorn"]]') == "word:sd word:vorn"
    keyed = '{"components":[["word","sd"]],"folder":"entry_1"}'
    assert spelled_key(keyed) == "word:sd"
    assert spelled_key("tabular_format") == "tabular_format"


# ---------------------------------------------------------------------------
# The Candidates view writes, through the one route.
# ---------------------------------------------------------------------------


def test_a_verdict_on_a_pair_is_one_event_about_the_candidate(project: Project) -> None:
    candidate_id = one(project, PAIR)
    with open_catalog(project.path) as catalog:
        event = answer_candidate(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            candidate_id=candidate_id,
            verdict="reversed",
            note="the processed file came first",
        )
    assert event.target == candidate_target(candidate_id)
    assert event.kind is RecordEventKind.REJECT
    said = payload_of(event)
    assert said["verdict"] == "reversed"
    assert said["rule_id"] == "derivation_within_name_identity"
    assert said["note"] == "the processed file came first"
    assert {"source_id", "target_id", "decision_tier"} <= set(said)


def test_a_pair_takes_only_the_four_words(project: Project) -> None:
    candidate_id = one(project, PAIR)
    with open_catalog(project.path) as catalog, pytest.raises(AnswerRefusedError):
        answer_candidate(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            candidate_id=candidate_id,
            verdict="stub",
        )
    assert read_events(project.path) == ()


def test_a_pair_the_current_run_does_not_propose_is_a_page_out_of_date(project: Project) -> None:
    with open_catalog(project.path) as catalog, pytest.raises(CandidateMovedError):
        answer_candidate(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            candidate_id="candidate_0000",
            verdict="derived",
        )


def test_the_candidates_view_shows_each_end_s_stage_and_the_folder(project: Project) -> None:
    with open_catalog(project.path) as catalog:
        # The one-file-at-two-steps rule proposes the plate pair too; the identity's is read.
        (pair,) = [
            row
            for row in views.candidates(catalog)
            if row["folder_path"] == "plates" and row["rule_id"] != "same_file_later_step"
        ]
    assert pair["source_stage"] == "raw"
    assert pair["target_stage"] == "processed"
    assert pair["folder_path"] == "plates"


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
        outcome = (error.status, json.loads(error.read()))
        error.close()
        return outcome


def test_a_verdict_over_http_goes_through_the_one_writing_route(project: Project) -> None:
    candidate_id = one(project, PAIR)
    server = DashboardServer(project.path, port=0)
    server.start()
    try:
        status, body = _post(
            server, {"actor": "robin", "candidate_id": candidate_id, "verdict": "derived"}
        )
        assert status == 201, body
        assert body["target"] == candidate_target(candidate_id)
        assert body["kind"] == "confirm"

        status, body = _post(server, {"candidate_id": candidate_id, "verdict": "derived"})
        assert status == 400
        assert "actor" in body["error"]

        status, body = _post(
            server, {"actor": "robin", "candidate_id": "candidate_0000", "verdict": "derived"}
        )
        assert status == 409
    finally:
        server.stop()
    assert len(read_events(project.path)) == 1


# ---------------------------------------------------------------------------
# A sheet imports.
# ---------------------------------------------------------------------------


def test_a_stub_verdict_is_a_decision_about_the_question_the_finding_raised(
    project: Project, tmp_path: Path
) -> None:
    finding_id, entry_id, raised = one(
        project,
        "SELECT finding_id, subject, raised[1] FROM findings WHERE rule_id = 'content_unreadable'",
    )
    path = sheet(
        tmp_path / "stubs.csv",
        STUB_HEADERS,
        [[finding_id, entry_id, "stub.dat", "not_stub", "an empty log"]],
    )
    report = imported(project, path)
    assert (report.kind, report.written, report.in_run, sum(report.refused.values())) == (
        "finding",
        1,
        1,
        0,
    )
    (event,) = read_events(project.path)
    # The rule raises the way the content is out of reach, so one question covers every file out of
    # reach the same way, and a verdict is about that question.
    assert event.target == question_target("content_unreadable", raised)
    assert event.kind is RecordEventKind.REJECT
    said = payload_of(event)
    assert (said["verdict"], said["note"], said["finding_id"]) == (
        "not_stub",
        "an empty log",
        finding_id,
    )
    assert said["source"] == {"sheet": "stubs.csv", "rows": [1]}


def test_a_blank_row_decides_nothing_and_a_check_writes_nothing(
    project: Project, tmp_path: Path
) -> None:
    finding_id, entry_id = one(
        project, "SELECT finding_id, subject FROM findings WHERE rule_id = 'content_unreadable'"
    )
    path = sheet(tmp_path / "stubs.csv", STUB_HEADERS, [[finding_id, entry_id, "stub.dat", "", ""]])
    report = imported(project, path)
    assert (report.blank, report.written, report.in_run) == (1, 0, 1)

    filled = sheet(
        tmp_path / "filled.csv", STUB_HEADERS, [[finding_id, entry_id, "stub.dat", "stub", ""]]
    )
    checked = imported(project, filled, check=True)
    assert (checked.written, checked.check) == (1, True)
    assert read_events(project.path) == ()


def test_a_note_on_a_row_left_without_a_verdict_is_kept_and_displaces_nothing(
    project: Project, tmp_path: Path
) -> None:
    """A person who wrote on a row and chose no word said something, and it is kept.

    It is a ``note``: written once, found again as already recorded, never the decision in force, so
    a verdict written before it still stands. It is not counted as a blank and dropped.
    """
    finding_id, entry_id = one(
        project, "SELECT finding_id, subject FROM findings WHERE rule_id = 'content_unreadable'"
    )
    decided = sheet(
        tmp_path / "decided.csv", STUB_HEADERS, [[finding_id, entry_id, "stub.dat", "stub", ""]]
    )
    assert imported(project, decided).written == 1
    noted = sheet(
        tmp_path / "noted.csv",
        STUB_HEADERS,
        [[finding_id, entry_id, "stub.dat", "", "a tracking run that found nothing"]],
    )
    checked = imported(project, noted, check=True)
    assert (checked.noted, checked.blank, checked.written) == (1, 0, 0)
    assert len(read_events(project.path)) == 1

    report = imported(project, noted)
    assert (report.noted, report.blank, report.written) == (1, 0, 0)
    verdict, note = read_events(project.path)
    assert note.kind is RecordEventKind.NOTE
    assert payload_of(note)["note"] == "a tracking run that found nothing"
    assert payload_of(note)["source"] == {"sheet": "noted.csv", "rows": [1]}
    (in_force,) = latest_by_target(read_events(project.path)).values()
    assert in_force.event_id == verdict.event_id
    assert notes_by_target(read_events(project.path)) == {note.target: [note]}

    again = imported(project, noted)
    assert (again.noted, again.already) == (0, 1)
    assert len(read_events(project.path)) == 2


def test_a_stale_row_is_refused_and_counted(project: Project, tmp_path: Path) -> None:
    """A sheet drawn from another run or project cannot write (``decide.py``'s rule)."""
    path = sheet(
        tmp_path / "stubs.csv",
        STUB_HEADERS,
        [["finding_0000", "entry_0000", "gone.dat", "stub", ""]],
    )
    report = imported(project, path)
    assert report.written == 0
    assert report.refused == {"the current run does not hold this target": 1}
    assert report.in_run == 0
    assert read_events(project.path) == ()


def test_a_word_the_sheet_does_not_offer_is_refused(project: Project, tmp_path: Path) -> None:
    finding_id, entry_id = one(
        project, "SELECT finding_id, subject FROM findings WHERE rule_id = 'content_unreadable'"
    )
    path = sheet(
        tmp_path / "stubs.csv", STUB_HEADERS, [[finding_id, entry_id, "stub.dat", "derived", ""]]
    )
    report = imported(project, path)
    assert report.refused == {"the word is not one this sheet offers": 1}


def test_importing_again_writes_only_what_changed(project: Project, tmp_path: Path) -> None:
    finding_id, entry_id = one(
        project, "SELECT finding_id, subject FROM findings WHERE rule_id = 'content_unreadable'"
    )
    path = sheet(
        tmp_path / "stubs.csv", STUB_HEADERS, [[finding_id, entry_id, "stub.dat", "stub", ""]]
    )
    assert imported(project, path).written == 1
    again = imported(project, path)
    assert (again.written, again.already) == (0, 1)

    changed = sheet(
        tmp_path / "changed.csv",
        STUB_HEADERS,
        [[finding_id, entry_id, "stub.dat", "not_stub", ""]],
    )
    assert imported(project, changed).written == 1
    events = read_events(project.path)
    assert len(events) == 2
    (in_force,) = latest_by_target(events).values()
    assert payload_of(in_force)["verdict"] == "not_stub"


def test_a_pair_sheet_writes_candidate_events(project: Project, tmp_path: Path) -> None:
    candidate_id = one(project, PAIR)
    path = sheet(
        tmp_path / "pairs.csv", PAIR_HEADERS, [[candidate_id, "raw_plate_alpha.csv", "derived", ""]]
    )
    report = imported(project, path)
    assert (report.kind, report.written) == ("candidate", 1)
    (event,) = read_events(project.path)
    assert event.target == candidate_target(candidate_id)
    assert event.kind is RecordEventKind.CONFIRM


def test_a_stage_verdict_carries_the_stage_a_person_named(project: Project, tmp_path: Path) -> None:
    claim_id, rule_id = one(
        project,
        "SELECT c.claim_id, c.rule_id FROM claims c JOIN entries e ON e.entry_id = c.subject "
        "WHERE c.rule_id = 'pipeline_stage_from_name' AND e.name = 'raw_plate_alpha.csv'",
    )
    path = sheet(
        tmp_path / "stages.csv",
        STAGE_HEADERS,
        [[rule_id, claim_id, "raw_plate_alpha.csv", "wrong", "processed", ""]],
    )
    report = imported(project, path)
    assert report.written == 1
    (event,) = read_events(project.path)
    assert event.target == claim_target(claim_id)
    said = payload_of(event)
    assert (said["verdict"], said["correct_stage"], said["rule_id"]) == (
        "wrong",
        "processed",
        rule_id,
    )


def test_a_stage_row_naming_another_rule_is_refused(project: Project, tmp_path: Path) -> None:
    claim_id = one(
        project, "SELECT claim_id FROM claims WHERE rule_id = 'pipeline_stage_from_name' LIMIT 1"
    )
    path = sheet(
        tmp_path / "stages.csv",
        STAGE_HEADERS,
        [["acquisition_stage_from_format", claim_id, "x", "correct", "", ""]],
    )
    assert imported(project, path).refused == {"the row names a rule the claim is not from": 1}


def test_a_class_is_found_by_its_components_and_told_apart_by_rule(
    project: Project, tmp_path: Path
) -> None:
    key = one(
        project,
        "SELECT DISTINCT object_reference FROM claims WHERE rule_id = 'same_name_identity' "
        "AND object_reference LIKE '%plate%'",
    )
    path = sheet(
        tmp_path / "classes.csv",
        CLASS_HEADERS,
        [[spelled_key(key), "2", "plates", "one_thing"]],
    )
    report = imported(project, path, rule_id="same_name_identity")
    assert report.written == 1, report.refused
    (event,) = read_events(project.path)
    assert event.target == class_target("same_name_identity", key)
    assert payload_of(event)["key"] == key


def test_rows_disagreeing_about_one_target_are_all_refused(
    project: Project, tmp_path: Path
) -> None:
    candidate_id = one(project, PAIR)
    path = sheet(
        tmp_path / "pairs.csv",
        PAIR_HEADERS,
        [[candidate_id, "a", "derived", ""], [candidate_id, "a", "unrelated", ""]],
    )
    report = imported(project, path)
    assert report.refused == {"the sheet says two things about one target": 2}
    assert read_events(project.path) == ()


def test_a_term_answer_goes_through_the_review_view_s_own_write(
    project: Project, tmp_path: Path
) -> None:
    path = sheet(
        tmp_path / "terms.csv",
        TERM_HEADERS,
        [["sim", "2", "simulation [technique]"]],
    )
    report = imported(project, path)
    assert (report.kind, report.written) == ("term", 1), report.examples
    (event,) = read_events(project.path)
    # Revision 4 declares the ambiguity flag again naming the program defeater.
    assert event.target == question_target(
        "binding_is_ambiguous_outside_programs_and_domains", "sim"
    )
    assert event.kind is RecordEventKind.ASSIGN
    said = payload_of(event)
    assert (said["answer"], said["theme_kind"]) == ("simulation", "technique")


def test_a_term_defined_without_an_axis_is_refused_as_the_page_would_refuse_it(
    project: Project, tmp_path: Path
) -> None:
    path = sheet(tmp_path / "terms.csv", TERM_HEADERS, [["sim", "2", "spindle_density"]])
    report = imported(project, path)
    assert report.refused == {"the Questions page refuses this answer": 1}
    # The page's refusal names a meaning's missing kind in plain words: the axis is the kind a
    # meaning is written with, "meaning [kind]".
    assert "kind" in report.examples["the Questions page refuses this answer"]


# ---------------------------------------------------------------------------
# A term sheet, grouped by the decision.
# ---------------------------------------------------------------------------


def test_a_term_answered_alike_on_two_rows_is_one_decision(
    project: Project, tmp_path: Path
) -> None:
    path = sheet(
        tmp_path / "terms.csv",
        [*TERM_HEADERS, "note"],
        [
            ["sim", "2", "simulation [technique]", "from the plate reader"],
            ["sim", "2", "simulation [technique]", "and the kelt folder"],
        ],
    )
    report = imported(project, path)
    assert (report.written, report.already, dict(report.refused)) == (1, 0, {})
    (event,) = read_events(project.path)
    said = payload_of(event)
    assert said["answer"] == "simulation"
    assert said["note"] == "from the plate reader | and the kelt folder"
    assert said["source"]["rows"] == [1, 2]


def test_a_term_answered_two_ways_is_refused_before_anything_is_written(
    project: Project, tmp_path: Path
) -> None:
    rows = [
        ["sim", "2", "simulation [technique]"],
        ["sim", "2", "cannot_say"],
    ]
    path = sheet(tmp_path / "terms.csv", TERM_HEADERS, rows)
    checked = imported(project, path, check=True)
    report = imported(project, path)
    assert report.refused == {DISAGREES: 2}
    assert report.written == 0
    assert read_events(project.path) == ()
    assert (checked.written, dict(checked.refused)) == (report.written, dict(report.refused))


def test_a_sheet_with_a_conflict_writes_none_of_its_other_answers(
    project: Project, tmp_path: Path
) -> None:
    """All or nothing: a sheet contradicting itself on one term is not applied in part."""
    with open_catalog(project.path) as catalog:
        raised = sorted({str(question["raised"]) for question in current_questions(catalog)})
    others = [term for term in raised if term != "sim"]
    if not others:
        pytest.skip("the fixture raises one term only")
    rows = [
        ["sim", "2", "simulation [technique]"],
        ["sim", "2", "cannot_say"],
        [others[0], "1", "cannot_say"],
    ]
    report = imported(project, sheet(tmp_path / "terms.csv", TERM_HEADERS, rows))
    assert report.refused[DISAGREES] == 2
    assert report.written == 0
    assert read_events(project.path) == ()


def test_checking_a_term_sheet_refuses_exactly_what_writing_it_would(
    project: Project, tmp_path: Path
) -> None:
    """The check counted a definition with no axis as written; the write refused it."""
    path = sheet(
        tmp_path / "terms.csv",
        TERM_HEADERS,
        [["sim", "2", "spindle_density"], ["zib", "1", "cannot_say"]],
    )
    checked = imported(project, path, check=True)
    assert read_events(project.path) == ()
    report = imported(project, path)
    assert (checked.written, dict(checked.refused), checked.already) == (
        report.written,
        dict(report.refused),
        report.already,
    )
    assert checked.refused["the Questions page refuses this answer"] == 1


@pytest.fixture
def several(tmp_path: Path) -> Project:
    """A family with a link among its members, and folders raising one doubt word or two."""
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "vorn_brisa_raw.csv").write_text("a,b\n1,2\n")
    (pool / "kelt" / "vorn_brisa_processed.csv").write_text("a,b\n3,4\n")
    _written_in_turn(
        pool / "kelt" / "vorn_brisa_raw.csv", pool / "kelt" / "vorn_brisa_processed.csv"
    )
    try:
        (pool / "kelt" / "vorn_brisa.csv").symlink_to("/??/UNC/archive/vorn_brisa.csv")
    except OSError:
        pytest.skip("this platform cannot make a symbolic link without privileges")
    (pool / "New Folder untitled").mkdir()
    (pool / "New Folder untitled" / "a.csv").write_text("a\n")
    (pool / "untitled kelt").mkdir()
    (pool / "untitled kelt" / "b.csv").write_text("b\n")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    allow_pairs_in_one_folder(created.path)
    created = ask_every_tie(created.path)  # a tied word in a few names is still asked (0.4.0)
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFERENCE, started_at="t")
    return created


def test_a_class_published_as_conflicting_is_one_a_verdict_can_reach(
    several: Project, tmp_path: Path
) -> None:
    """A defeated class publishes as conflicting, and a person shown it can decide it.

    It is not refused as naming no class.
    """
    key = one(
        several,
        "SELECT object_reference FROM claims WHERE rule_id = 'name_family_without_stages' "
        "AND resolution_status = 'examined_conflicting' LIMIT 1",
    )
    path = sheet(tmp_path / "families.csv", CLASS_HEADERS, [[key, "3", "kelt", "one_thing"]])
    report = imported(several, path)
    assert (report.written, dict(report.refused)) == (1, {})
    (event,) = read_events(several.path)
    assert event.target == class_target("name_family_without_stages", key)


def test_a_row_about_a_finding_raising_several_values_decides_each_of_them(
    several: Project, tmp_path: Path
) -> None:
    """One row showing several raised values is one word about each.

    Each event says it was decided beside the others. Where two rows then say different things about
    one of the values, both are refused rather than one of them winning.
    """
    rows = {
        str(raised): (str(finding_id), str(subject))
        for finding_id, subject, raised in several_findings(several)
    }
    both = rows["new_folder,untitled"]
    alone = rows["untitled"]
    headers = ["finding_id", "rule", "path", "raised", "verdict (correct | wrong | cannot_say)"]
    agreeing = sheet(
        tmp_path / "doubt.csv",
        headers,
        [
            [
                both[0],
                "doubt_in_a_folder_name",
                "New Folder untitled",
                "new_folder, untitled",
                "correct",
            ]
        ],
    )
    report = imported(several, agreeing)
    assert (report.written, dict(report.refused)) == (2, {})
    events = read_events(several.path)
    assert {event.target for event in events} == {
        question_target("doubt_in_a_folder_name", "new_folder"),
        question_target("doubt_in_a_folder_name", "untitled"),
    }
    for event in events:
        assert payload_of(event)["raised_together"] == ["new_folder", "untitled"]

    disagreeing = sheet(
        tmp_path / "doubt-2.csv",
        [*headers, "note"],
        [
            [
                both[0],
                "doubt_in_a_folder_name",
                "New Folder untitled",
                "new_folder, untitled",
                "wrong",
                "",
            ],
            [
                alone[0],
                "doubt_in_a_folder_name",
                "untitled kelt",
                "untitled",
                "correct",
                "a real experiment, named in a hurry",
            ],
        ],
    )
    second = imported(several, disagreeing)
    assert second.refused == {DISAGREES: 2}
    assert second.written == 1  # new_folder, which only the first row reaches
    # **A note is never lost with its row's refused verdict**: the second row's words are kept.
    assert second.noted == 1
    (kept,) = notes_by_target(read_events(several.path))[
        question_target("doubt_in_a_folder_name", "untitled")
    ]
    assert payload_of(kept)["note"] == "a real experiment, named in a hurry"


def several_findings(project: Project) -> list[tuple[str, str, str]]:
    with open_catalog(project.path) as catalog:
        return [
            (finding_id, subject, ",".join(sorted(raised)))
            for finding_id, subject, raised in catalog.query(
                "SELECT finding_id, subject, raised FROM findings "
                "WHERE rule_id = 'doubt_in_a_folder_name'"
            ).fetchall()
        ]


def test_a_sheet_of_words_about_folders_is_read_as_words_and_never_as_verdicts(
    project: Project, tmp_path: Path
) -> None:
    """A finding sheet's words are annotations about the folders its findings were raised about.

    A row naming a finding the run does not hold is refused as stale. A finding sheet with neither a
    verdict nor words is refused.
    """
    path = sheet(
        tmp_path / "folders.csv",
        ["finding_id", "folder", "what this folder holds (your words)", "note"],
        [["finding_0000", "kelt", "plates from the second batch", ""]],
    )
    report = imported(project, path)
    assert (report.kind, report.written, report.refused) == ("finding", 0, {STALE: 1})
    assert read_events(project.path) == ()

    wordless = sheet(tmp_path / "wordless.csv", ["finding_id", "folder", "note"], [["f", "k", ""]])
    with pytest.raises(SheetRefusedError, match="no verdict column and no description"):
        imported(project, wordless)


def test_a_sheet_naming_nothing_a_row_can_be_about_is_refused_whole(
    project: Project, tmp_path: Path
) -> None:
    """An ``extension`` column makes an extension sheet.

    A sheet carrying no word about worth and no description decides nothing, so it is refused whole.
    """
    path = sheet(tmp_path / "extensions.csv", ["extension", "files"], [["dat", "4"]])
    with pytest.raises(SheetRefusedError, match="carries neither"):
        imported(project, path)
    nameless = sheet(tmp_path / "names.csv", ["name", "files"], [["stub.dat", "1"]])
    with pytest.raises(SheetRefusedError, match="none of the columns"):
        imported(project, nameless)


def test_the_record_verb_imports_a_sheet_and_says_what_became_of_every_row(
    project: Project, tmp_path: Path
) -> None:
    import subprocess
    import sys

    candidate_id = one(project, PAIR)
    path = sheet(
        tmp_path / "pairs.csv",
        PAIR_HEADERS,
        [[candidate_id, "a", "derived", ""], ["candidate_0000", "b", "unrelated", ""]],
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "ingence.cli.main", "record", "import", str(path), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    unattributed = run("-p", str(project.path))
    assert unattributed.returncode == 2
    assert read_events(project.path) == ()

    checked = run("-p", str(project.path), "--check")
    assert checked.returncode == 0, checked.stderr
    assert "would be written 1" in checked.stdout
    assert read_events(project.path) == ()

    written = run("-p", str(project.path), "--actor", "robin")
    assert written.returncode == 0, written.stderr
    assert "written 1" in written.stdout
    assert "1 refused: the current run does not hold this target" in written.stdout
    assert len(read_events(project.path)) == 1


def test_the_record_verb_records_as_the_name_set_on_this_machine(
    project: Project, tmp_path: Path
) -> None:
    """The record verb records as the name set on this machine: typed once (the Dashboard asks),
    said aloud, never guessed.
    """
    import subprocess
    import sys

    from ingence.api.decider import user_config_directory, write_decider

    candidate_id = one(project, PAIR)
    path = sheet(tmp_path / "pairs.csv", PAIR_HEADERS, [[candidate_id, "a", "derived", ""]])
    write_decider("kelt", set_at="2026-09-17T14:02:11Z", config_home=user_config_directory())
    written = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "record",
            "import",
            str(path),
            "-p",
            str(project.path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    assert "Saving answers as kelt, the name saved on this computer" in written.stdout
    assert [event.actor for event in read_events(project.path)] == ["kelt"]


# ---------------------------------------------------------------------------
# Teaching sheets, and a pre-filled sheet saying so.
# ---------------------------------------------------------------------------

EXTENSION_HEADERS = [
    "extension",
    "files",
    "what it is (your words)",
    "format / program / instrument it belongs to",
    "worth reading? (yes | no | never)",
]
FOLDER_HEADERS = ["finding_id", "folder", "raised", "what this folder holds (your words)", "note"]


@pytest.fixture
def taught(tmp_path: Path) -> Project:
    """A folder of words nobody taught the engine, which the shipped rule raises, and two files
    of kinds nobody named: one with an extension the Dictionary does not hold, one with none."""
    pool = tmp_path / "pool"
    (pool / "illegible").mkdir(parents=True)
    for index in range(1, 9):
        (pool / "illegible" / f"zorbex_quillan_vramp_{index}.csv").write_text("a,b\n1,2\n")
    (pool / "notes").mkdir()
    (pool / "notes" / "readme").write_text("words\n")
    (pool / "notes" / "trace.qzx").write_text("words\n")
    created = Project.create(tmp_path / "taught", created_at="t", roots=[pool])
    write_revision(created.path)
    allow_pairs_in_one_folder(created.path)
    created = ask_every_tie(created.path)  # a tied word in a few names is still asked (0.4.0)
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFERENCE, started_at="t")
    return created


def test_the_worth_words_and_the_two_new_targets_are_each_spelled_once() -> None:
    assert WORTH_READING == {
        "yes": RecordEventKind.CONFIRM,
        "no": RecordEventKind.REJECT,
        "never": RecordEventKind.ANNOTATE,
    }
    assert extension_target(".TIF") == extension_target("tif") == "extension:tif"
    assert extension_target("") == "extension:(none)"
    assert (extension_named_by("extension:tif"), extension_named_by("extension:(none)")) == (
        "tif",
        "",
    )
    assert extension_named_by(question_target("rule_a", "tif")) is None
    assert target_kind(extension_target("tif")) == "extension"
    assert target_kind(folder_target("entry_1")) == "folder"
    assert rule_named_by(extension_target("tif")) is None


def test_an_extension_s_worth_is_one_event_about_every_file_carrying_it(
    project: Project, tmp_path: Path
) -> None:
    path = sheet(
        tmp_path / "extensions.csv",
        EXTENSION_HEADERS,
        [
            ["csv", "4", "comma-separated tables", "a plate reader's export", "yes"],
            ["dat", "1", "an instrument's raw dump", "", "never"],
        ],
    )
    report = imported(project, path)
    assert (report.kind, report.written, report.in_run, sum(report.refused.values())) == (
        "extension",
        2,
        2,
        0,
    )
    events = {event.target: event for event in read_events(project.path)}
    table = events[extension_target("csv")]
    assert (table.kind, table.scope.kind) == (RecordEventKind.CONFIRM, ScopeKind.PROJECT)
    said = payload_of(table)
    assert (said["worth_reading"], said["described"], said["belongs_to"], said["extension"]) == (
        "yes",
        "comma-separated tables",
        "a plate reader's export",
        "csv",
    )
    assert said["as_shown"]["files"] == 4
    assert said["source"] == {"sheet": "extensions.csv", "rows": [1]}
    never = events[extension_target("dat")]
    assert never.kind is RecordEventKind.ANNOTATE
    assert payload_of(never)["worth_reading"] == "never"
    assert "belongs_to" not in payload_of(never)


def test_an_extension_no_file_carries_is_refused_and_one_spelled_twice_must_agree(
    project: Project, tmp_path: Path
) -> None:
    path = sheet(
        tmp_path / "extensions.csv",
        EXTENSION_HEADERS,
        [
            ["qzx", "0", "", "", "no"],
            ["CSV", "4", "", "", "yes"],
            [".csv", "4", "", "", "no"],
            ["dat", "1", "", "", "maybe"],
        ],
    )
    report = imported(project, path)
    assert report.refused == {STALE: 1, DISAGREES: 2, NOT_OFFERED: 1}
    assert read_events(project.path) == ()


def test_files_with_no_extension_are_one_kind_a_sheet_can_teach_and_a_description_alone_annotates(
    taught: Project, tmp_path: Path
) -> None:
    path = sheet(
        tmp_path / "extensions.csv",
        EXTENSION_HEADERS,
        [["", "1", "", "", "no"], ["qzx", "1", "a tracker's trace", "", ""]],
    )
    report = imported(taught, path)
    assert (report.written, sum(report.refused.values())) == (2, 0), report.refused
    events = {event.target: event for event in read_events(taught.path)}
    bare = events[extension_target("")]
    assert (bare.kind, payload_of(bare)["extension"]) == (RecordEventKind.REJECT, "")
    described = events[extension_target("qzx")]
    assert described.kind is RecordEventKind.ANNOTATE
    assert "worth_reading" not in payload_of(described)


def test_an_extension_sheet_imported_again_writes_only_what_changed(
    project: Project, tmp_path: Path
) -> None:
    path = sheet(tmp_path / "extensions.csv", EXTENSION_HEADERS, [["dat", "1", "", "", "no"]])
    assert imported(project, path).written == 1
    again = imported(project, path)
    assert (again.written, again.already) == (0, 1)
    changed = sheet(tmp_path / "changed.csv", EXTENSION_HEADERS, [["dat", "1", "", "", "yes"]])
    assert imported(project, changed).written == 1
    (in_force,) = latest_by_target(read_events(project.path)).values()
    assert (in_force.kind, payload_of(in_force)["worth_reading"]) == (
        RecordEventKind.CONFIRM,
        "yes",
    )


def test_a_folder_description_is_an_annotation_about_the_folder_its_finding_raised(
    taught: Project, tmp_path: Path
) -> None:
    finding_id, subject = one(
        taught,
        f"SELECT finding_id, subject FROM findings WHERE rule_id = '{UNREAD_IN_FOLDER}'",
    )
    entry_id = one(taught, "SELECT entry_id FROM entries WHERE name = 'illegible'")
    path = sheet(
        tmp_path / "folders.csv",
        FOLDER_HEADERS,
        [[finding_id, subject, "word_occurrences", "a second batch's invented words", ""]],
    )
    report = imported(taught, path, actor="wren", imported={"imported_by": "ines"})
    assert (report.kind, report.written, sum(report.refused.values())) == ("finding", 1, 0)
    (event,) = read_events(taught.path)
    assert event.target == subject == folder_target(entry_id)
    assert event.kind is RecordEventKind.ANNOTATE
    assert event.scope == Scope(ScopeKind.FOLDER, "illegible")
    said = payload_of(event)
    assert (said["described"], said["path"]) == ("a second batch's invented words", "illegible")
    assert [finding["rule_id"] for finding in said["findings"]] == [
        "vocabulary_unread_in_folder_outside_programs"
    ]
    assert (event.actor, said["source"]["imported_by"]) == ("wren", "ines")
    # A description is not a verdict on the rule that raised the folder, so no figure counts it.
    assert "rule_id" not in said
    with open_catalog(taught.path) as catalog:
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())
    assert "vocabulary_unread_in_folder_outside_programs" not in precision_by_rule(
        read_events(taught.path), versions
    )
    again = imported(taught, path, actor="wren", imported={"imported_by": "ines"})
    assert (again.written, again.already) == (0, 1)


def test_a_description_of_a_file_s_finding_or_naming_another_folder_is_refused(
    project: Project, taught: Project, tmp_path: Path
) -> None:
    stub_finding = one(
        project, "SELECT finding_id FROM findings WHERE rule_id = 'content_unreadable'"
    )
    about_a_file = sheet(
        tmp_path / "file.csv", FOLDER_HEADERS, [[stub_finding, "", "", "an empty log", ""]]
    )
    assert imported(project, about_a_file).refused == {NOT_A_FOLDER: 1}

    finding_id = one(
        taught,
        f"SELECT finding_id FROM findings WHERE rule_id = '{UNREAD_IN_FOLDER}'",
    )
    elsewhere = sheet(
        tmp_path / "elsewhere.csv",
        FOLDER_HEADERS,
        [[finding_id, "folder:entry_0000", "", "somewhere else", ""]],
    )
    assert imported(taught, elsewhere).refused == {OTHER_FOLDER: 1}
    assert read_events(project.path) == read_events(taught.path) == ()


def test_an_agent_s_sheet_says_so_on_every_event_and_the_figure_says_it_is_anchored(
    project: Project, tmp_path: Path
) -> None:
    """A figure from answers a person reviewed rather than gave is a different evidence class from
    one reached blind.

    The Record lets a reader tell them apart.
    """
    from ingence.record.imported import stated

    finding_id, entry_id = one(
        project, "SELECT finding_id, subject FROM findings WHERE rule_id = 'content_unreadable'"
    )
    stubs = sheet(
        tmp_path / "stubs.csv", STUB_HEADERS, [[finding_id, entry_id, "stub.dat", "not_stub", ""]]
    )
    terms = sheet(tmp_path / "terms.csv", TERM_HEADERS, [["sim", "2", "simulation"]])
    agent = stated("ines", agent_generated=True, reviewed=True)
    assert imported(project, stubs, actor=agent.author, imported=agent.source()).written == 1
    assert imported(project, terms, actor=agent.author, imported=agent.source()).written == 1
    for event in read_events(project.path):
        assert event.actor == "ines_agent"
        assert payload_of(event)["source"]["reviewed_by"] == "ines"

    with open_catalog(project.path) as catalog:
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())
    figure = precision_by_rule(read_events(project.path), versions)["content_unreadable"]
    assert (figure.rejected, figure.reviewed_in_file) == (1, 1)
    assert "1 of the 1 counted were reviewed in a filled-in file" in figure.sentence()


def test_a_stored_event_naming_who_pre_filled_it_still_counts_as_reviewed_in_a_file() -> None:
    """Records written before the import statement keep ``prefilled_by`` in their source; it
    reads as reviewed in a file, and its name is shown nowhere."""
    from ingence.record.precision import reviewed_in_file

    assert reviewed_in_file({"sheet": "a.csv", "prefilled_by": "tomas"})
    assert reviewed_in_file({"agent_generated": True, "reviewed_by": "ines"})
    assert not reviewed_in_file({"sheet": "a.csv", "imported_by": "ines"})
    assert not reviewed_in_file(None)


# ---------------------------------------------------------------------------
# Precision from the Record.
# ---------------------------------------------------------------------------


def test_the_wilson_interval_is_the_textbook_one() -> None:
    from ingence.record.precision import wilson_interval

    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(8, 10)  # type: ignore[misc]
    assert (round(low, 3), round(high, 3)) == (0.490, 0.943)
    low, high = wilson_interval(0, 10)  # type: ignore[misc]
    assert (low, round(high, 3)) == (0.0, 0.278)
    low, high = wilson_interval(10, 10)  # type: ignore[misc]
    assert (round(low, 3), high) == (0.722, 1.0)


def test_precision_counts_decisions_in_force_per_rule_at_its_version(project: Project) -> None:
    """Confirm, reject and annotate, per rule; a term's meaning is not a verdict on its rule."""
    from ingence.api.routes.decide import answer_question
    from ingence.record.precision import precision_by_rule

    candidate_id = one(project, PAIR)
    with open_catalog(project.path) as catalog:
        answer_candidate(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            candidate_id=candidate_id,
            verdict="unrelated",
        )
        answer_candidate(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            candidate_id=candidate_id,
            verdict="derived",
        )
        answer_question(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            rule_id="binding_is_ambiguous_outside_programs_and_domains",
            raised="sim",
            kind=RecordEventKind.ASSIGN,
            answer="simulation",
        )
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())

    figures = precision_by_rule(read_events(project.path), versions)
    assert set(figures) == {"derivation_within_name_identity"}
    figure = figures["derivation_within_name_identity"]
    assert (figure.confirmed, figure.rejected, figure.annotated) == (1, 0, 0)
    assert "1 confirmed, 0 rejected, 0 annotated" in figure.sentence()
    assert "Wilson 95% interval on the confirmed share of the 1 decided" in figure.sentence()

    moved = {**versions, "derivation_within_name_identity": "99"}
    apart = precision_by_rule(read_events(project.path), moved)["derivation_within_name_identity"]
    assert (apart.decided, apart.other_versions, apart.interval) == (0, 1, None)


def test_rule_show_prints_the_figure_with_its_method(project: Project, tmp_path: Path) -> None:
    import subprocess
    import sys

    candidate_id = one(project, PAIR)
    with open_catalog(project.path) as catalog:
        answer_candidate(
            catalog,
            project.path,
            actor="robin",
            occurred_at=MOMENT,
            candidate_id=candidate_id,
            verdict="reversed",
        )
    shown = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "rule", "show", "-p", str(project.path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert "derivation_within_name_identity v" in shown.stdout
    assert "0 confirmed, 1 rejected, 0 annotated" in shown.stdout
    assert "95% interval for the confirmed share" in " ".join(shown.stdout.split())
