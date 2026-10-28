"""The class handle and the question sheet: one membership, and the rules around it.

- One membership for a class of claims. The sheet, the handle and inference count the same
  files (resolved claims outside installed programs). So twelve data files beside three
  thousand of a program's are a class of twelve everywhere, and an answer reaches those twelve.
- An answered class leaves the sheet; a value that is a structure is never an audit; a class the
  rule proves is recorded and never shown as waiting; a file joining a class after the answer says
  so; the sheet speaks in plain words; the listing marks a conflicting value; an answer on a class
  is written from the Questions page; the sheet's cut-offs are the project's settings.

Every name is invented except the installed program's, which the shipped Dictionary declares.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.log import read_events
from ingence.scan.census import run_census
from test_claim_class_handle import (
    AGAIN,
    FOLDER,
    FORMAT_RULE,
    _answer,
    _build,
    _claims,
    _handle,
)

if TYPE_CHECKING:
    from pathlib import Path

LATER = "run_20260101T000004Z_000000000004"
CENSUS2 = "run_20260101T000005Z_000000000005"
DATA_FILES = 12
PROGRAM_FILES = 3_000


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _ask(project: Project, path: str, **query: Any) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project.path, settled=None, job_running=False)
    status, body = dispatch(context, path, {k: [str(v)] for k, v in query.items()})
    assert status == HTTPStatus.OK, body
    return body


def _csv(project: Project, where: str = FOLDER) -> list[dict[str, Any]]:
    with_paths = []
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        paths = dict(catalog.query("SELECT entry_id, relative_path FROM entries").fetchall())
        subjects = dict(
            catalog.query(
                "SELECT claim_id, subject FROM claims WHERE rule_id = ?", [FORMAT_RULE]
            ).fetchall()
        )
    for claim in _claims(project, FORMAT_RULE):
        path = str(paths.get(subjects[claim["claim_id"]], ""))
        if claim["name"].endswith(".csv") and path.startswith(where):
            with_paths.append({**claim, "path": path})
    return with_paths


def test_twelve_data_files_beside_a_program_s_three_thousand_are_a_class_of_twelve(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    app = pool / "Fiji" / "Fiji.app"
    for number in range(4):
        _write(app / "jars" / f"vorn-library-{number}.jar", "PK")
    _write(app / "plugins" / "brisa_plugin.jar", "PK")
    _write(app / "luts" / "fire.lut", "lut")
    for number in range(PROGRAM_FILES):
        _write(app / "macros" / f"table_{number:04d}.csv", "a,b\n1,2\n")
    for number in range(DATA_FILES):
        _write(pool / FOLDER / f"vorn_{number:03d}.csv", "a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_000000000002", started_at="t")

    data = _csv(project)
    assert len(data) == DATA_FILES
    assert len(_csv(project, "Fiji/")) == PROGRAM_FILES
    handle = _handle(project, data[0]["claim_id"])
    assert handle["size"] == DATA_FILES
    assert all(example.startswith(FOLDER) for example in handle["examples"])
    sheet = _ask(project, "questions/sheet")
    audit = next(q for q in sheet["questions"] if q.get("target") == handle["handle"])
    assert audit["size"] == audit["settles_n"] == DATA_FILES

    _answer(project, handle["handle"], "wrong")
    said = json.loads(read_events(project.path)[-1].payload)
    assert said["size"] == DATA_FILES and len(said["members"]) == DATA_FILES
    assert said["examples"] == handle["examples"]
    assert said["source"]["page"] == "questions"
    run_inference(project, run_id=AGAIN, started_at="t")
    assert {claim["status"] for claim in _csv(project)} == {"examined_conflicting"}
    assert {claim["status"] for claim in _csv(project, "Fiji/")} == {"resolved"}
    assert _handle(project, data[0]["claim_id"])["size"] == DATA_FILES


def test_an_answered_class_stays_until_the_run_reads_it_and_structures_are_never_audits(
    tmp_path: Path,
) -> None:
    project = _build(tmp_path)
    handle = _handle(project, _csv(project)[0]["claim_id"])
    before = _ask(project, "questions/sheet")
    assert any(q.get("target") == handle["handle"] for q in before["questions"])
    for question in before["questions"]:
        if question["kind"] == "audit":
            try:
                parsed = json.loads(str(question["value"]))
            except ValueError:
                parsed = None
            assert not isinstance(parsed, (dict, list)), question["value"]
    _answer(project, handle["handle"], "correct")
    # The sheet keeps what was answered, with the answer, until a run reads it.
    after = _ask(project, "questions/sheet")
    [kept] = [q for q in after["questions"] if q.get("target") == handle["handle"]]
    assert kept["answer"]["word"] == "correct" and kept["answer"]["waits_for_rerun"] is True
    run_inference(project, run_id=LATER, started_at="t")
    read = _ask(project, "questions/sheet")
    assert not any(q.get("target") == handle["handle"] for q in read["questions"])
    # Answered on the Questions page, so never in what Groupings exports.
    from ingence.api.routes.workspace import _groupings_export

    assert json.loads(read_events(project.path)[-1].payload)["source"]["page"] == "questions"
    assert handle["handle"] not in _groupings_export(project.path)["text"]


def test_a_file_joining_after_the_answer_is_defeated_and_says_so(tmp_path: Path) -> None:
    project = _build(tmp_path)
    handle = _handle(project, _csv(project)[0]["claim_id"])
    _answer(project, handle["handle"], "wrong")
    (tmp_path / "pool" / FOLDER / "vorn_999.csv").write_text("a,b\n3,4\n")
    run_census(project, run_id=CENSUS2, started_at="t")
    run_inference(project, run_id=LATER, started_at="t")
    claims = {claim["name"]: claim for claim in _csv(project)}
    joined = claims["vorn_999.csv"]
    assert joined["status"] == "examined_conflicting"
    reasons = [d["reason"] for d in json.loads(joined["defeaters_json"]) if d["fired"]]
    assert any("joined" in reason and "after the answer" in reason for reason in reasons)
    shown = [d["reason"] for d in json.loads(claims["vorn_001.csv"]["defeaters_json"])]
    assert not any("joined" in reason for reason in shown)
    # The listing says the same of each claim, and marks the value as conflicting.
    with_rows = _ask(project, "experiments/files", experiment=_experiment(project), limit=500)
    rows = {row["name"]: row for row in with_rows["rows"]}
    late = next(c for c in rows["vorn_999.csv"]["claims"] if c["rule_id"] == FORMAT_RULE)
    early = next(c for c in rows["vorn_001.csv"]["claims"] if c["rule_id"] == FORMAT_RULE)
    assert late["class_answer"]["word"] == "wrong" and late["class_answer"]["joined_after"]
    assert early["class_answer"]["joined_after"] is False
    assert rows["vorn_001.csv"]["format"][0]["conflicting"] is True


def _experiment(project: Project) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        found = catalog.query(
            "SELECT subject FROM claims WHERE rule_id LIKE 'experiment_folder%' LIMIT 1"
        ).fetchone()
    assert found is not None
    return str(found[0])


def test_the_handle_says_whether_the_reading_can_be_retracted() -> None:
    from ingence.api.v1.claim_class import reads_for
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEvent, RecordEventKind
    from ingence.record.answers import inference_reads

    assert "proven" in reads_for(retractable=False)
    assert "not changed" in reads_for(retractable=False)
    assert "conflicting" in reads_for(retractable=True)
    proven = RecordEvent(
        event_id="evt_a",
        sequence=1,
        kind=RecordEventKind.REJECT,
        actor="zib",
        occurred_at="2026-10-08T00:00:00Z",
        scope=PROJECT_SCOPE,
        target="class:kelt_rule:abc",
        against_run_id="run_20260101T000002Z_000000000002",
        payload=json.dumps({"verdict": "wrong", "of": "claims", "retractable": False}),
    )
    assert inference_reads(proven) is False
    assert inference_reads(
        proven.model_copy(update={"payload": json.dumps({"verdict": "wrong", "of": "claims"})})
    )


def test_the_sheet_speaks_plain_words(tmp_path: Path) -> None:
    project = _build(tmp_path)
    sheet = _ask(project, "questions/sheet")
    audits = [q for q in sheet["questions"] if q["kind"] == "audit"]
    assert audits
    for question in audits:
        assert question["rule_id"] not in question["question"]
        assert "_" not in question["question"], question["question"]
        assert question["meaning"]
    from ingence.review.question_sheet import TEMPLATE_MEANING

    assert "[source]" in TEMPLATE_MEANING and "#" in TEMPLATE_MEANING
    assert "{src}" not in TEMPLATE_MEANING


def test_the_sheet_s_cut_offs_are_the_project_s_settings(tmp_path: Path) -> None:
    from ingence.core.settings import Settings

    held = Settings().questions
    assert (held.sheet_budget, held.sheet_tied_words, held.audit_first) == (50, 10, 20)
    project = _build(tmp_path)
    project = project.save_settings(
        project.settings.model_copy(
            update={"questions": project.settings.questions.model_copy(update={"sheet_budget": 1})}
        )
    )
    assert len(_ask(project, "questions/sheet")["questions"]) == 1
