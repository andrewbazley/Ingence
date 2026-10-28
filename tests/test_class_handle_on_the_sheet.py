"""More rules for the class handle and the question sheet.

- The sheet's cut-offs are the project's settings as the Dashboard commits them too.
- A claim outside its class (a file inside an installed program) says so on its handle, and why.
- What waits for Re-run says a class answer in plain words, never a rule's identifier.
- A proven class on the sheet says an answer cannot change it.

Every name is invented except the installed program's, which the shipped Dictionary declares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from test_claim_class_handle import _build
from test_class_handle_membership import _ask

if TYPE_CHECKING:
    from pathlib import Path


def test_a_cut_off_committed_on_the_dashboard_reaches_the_sheet(tmp_path: Path) -> None:
    from ingence.core.project import Project
    from ingence.core.settings import DASHBOARD_SETTINGS_FILE

    project = _build(tmp_path)
    assert len(_ask(project, "questions/sheet")["questions"]) > 1
    (project.path / DASHBOARD_SETTINGS_FILE).write_text("questions:\n  sheet_budget: 1\n")
    assert Project.open(project.path).settings.questions.sheet_budget == 1
    assert len(_ask(project, "questions/sheet")["questions"]) == 1


def test_a_claim_outside_its_class_says_so_on_its_handle(tmp_path: Path) -> None:
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from test_class_handle_membership import _csv, _write

    pool = tmp_path / "pool"
    app = pool / "Fiji" / "Fiji.app"
    for number in range(4):
        _write(app / "jars" / f"vorn-library-{number}.jar", "PK")
    _write(app / "plugins" / "brisa_plugin.jar", "PK")
    _write(app / "luts" / "fire.lut", "lut")
    for number in range(30):
        _write(app / "macros" / f"table_{number:04d}.csv", "a,b\n1,2\n")
    for number in range(6):
        _write(pool / "20240301 kelt" / f"vorn_{number:03d}.csv", "a,b\n1,2\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_000000000002", started_at="t")

    inside = _ask(project, "claims/class", claim=_csv(project, "Fiji/")[0]["claim_id"])
    assert inside["member"] is False
    assert "installed program" in inside["outside"]
    assert inside["size"] == 6
    member = _ask(project, "claims/class", claim=_csv(project)[0]["claim_id"])
    assert member["member"] is True and member["outside"] is None


def test_what_waits_for_rerun_says_a_class_answer_in_plain_words(tmp_path: Path) -> None:
    from ingence.workspace.state import answers_a_run_reads
    from test_claim_class_handle import FORMAT_RULE, _answer, _handle
    from test_class_handle_membership import _csv

    project = _build(tmp_path)
    handle = _handle(project, _csv(project)[0]["claim_id"])
    _answer(project, handle["handle"], "wrong")
    said = " ".join(row for change in answers_a_run_reads(project.path) for row in change.rows)
    assert FORMAT_RULE not in said and handle["value"] not in said, said
    assert handle["value"].replace("_", " ") in said and "Wrong" in said


def test_a_proven_class_on_the_sheet_says_an_answer_cannot_change_it(
    tmp_path: Path, monkeypatch: object
) -> None:
    import pytest

    from ingence.review import question_sheet

    project = _build(tmp_path)
    audits = [q for q in _ask(project, "questions/sheet")["questions"] if q["kind"] == "audit"]
    assert audits and all(question["retractable"] is True for question in audits)
    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    # Every rule read as proving its reading: each audit says so.
    monkeypatch.setattr(question_sheet, "_retractable_rules", lambda catalog: frozenset())
    proven = [q for q in _ask(project, "questions/sheet")["questions"] if q["kind"] == "audit"]
    assert proven and all(question["retractable"] is False for question in proven)
    assert all("cannot change" in question["meaning"] for question in proven)
