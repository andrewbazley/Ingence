"""The question sheet, answered by the engine.

The sheet, the manifest and the completeness rows say in the engine's words what the page would
otherwise rewrite or fetch again:

- a question answered since the run stays on the sheet with its answer until Re-run reads it, and
  leaves once the run has (so the sheet does not change under the person answering it);
- a pack answer (not asked as a question since 0.4.0) takes "Can't say" as a note, which decides
  nothing;
- each row names its rule's version; a tied word's examples are paths relative to the root and its
  row carries its meanings; a pattern's sentence has no marks;
- the manifest says where a decision was about in place words, a reading's value as words, and a
  rule whose name holds a word no sentence carries by its plain words and version;
- a completeness row names its theme's words beside the number.

Every name here is invented (the fixtures' own).
"""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from fixtures.theme_pool import build_theme_project
from ingence.api.routes.decide import Decision, DecisionContext, decide_target
from ingence.api.routes.manifest import _cited, _place_words, _shown
from ingence.core.project import Project
from ingence.record.log import append_event, latest_by_target, read_events
from ingence.record.verdicts import shown_word
from ingence.review.question_sheet import (
    NO_STEM_SAID,
    TEMPLATE_MEANING,
    name_template,
    readable_template,
)
from ingence.storage.catalog import open_catalog

PERSON = "a. tester"
MOMENT = "2026-10-13T12:00:00Z"


def _sheet(project: Project | Path) -> dict[str, Any]:
    from ingence.api.v1 import context_for, dispatch

    path = project.path if isinstance(project, Project) else project
    context = context_for(path, settled=None, job_running=False)
    status, body = dispatch(context, "questions/sheet", {})
    assert status == HTTPStatus.OK, body
    return body


def _answer(project: Path, target: str, word: str, note: str | None = None) -> Any:
    with open_catalog(project) as catalog:
        return decide_target(
            DecisionContext(catalog, project),
            Decision(target=target, word=word, note=note),
            actor=PERSON,
            occurred_at=MOMENT,
        )


# ---------------------------------------------------------------------------
# On a project with a vocabulary pack to take, audits, and a word standing between two senses.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def standing(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from test_browser_standing_views import build_standing_project

    return build_standing_project(tmp_path_factory.mktemp("sheet"))


@pytest.fixture(scope="module")
def tied_pool(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # The standing pool's planted word is read by its folder's domain, never tied. The review pages'
    # pool keeps a word that two domains spell differently, which is asked.
    from test_review_pages import _project

    return _project(tmp_path_factory.mktemp("sheet_tied")).path


def test_a_tied_word_s_examples_are_relative_and_its_row_carries_its_meanings(
    tied_pool: Path,
) -> None:
    tied = [q for q in _sheet(tied_pool)["questions"] if q["kind"] == "tied word"]
    assert tied, "the fixture plants a word standing between two senses"
    for question in tied:
        assert question["examples"], question
        for example in question["examples"]:
            assert not example.startswith("/") and "vorn_sim" in example, example
        assert len(question["senses"]) >= 2
        for sense in question["senses"]:
            assert sense["term"] and "theme_kind" in sense
        # The sentence offers exactly the meanings the row carries, in the same order.
        offered = ", ".join(s["term"] for s in question["senses"][:-1])
        assert question["question"].endswith(f"{offered} or {question['senses'][-1]['term']}?")
    other = next(q for q in _sheet(tied_pool)["questions"] if q["kind"] != "tied word")
    assert other["senses"] is None


def test_every_row_that_rests_on_a_rule_names_the_rule_s_version_in_the_run(
    standing: Path,
) -> None:
    body = _sheet(standing)
    with open_catalog(standing) as catalog:
        versions = dict(catalog.query("SELECT rule_id, version FROM rules").fetchall())
    ruled = [q for q in body["questions"] if q["rule_id"]]
    assert ruled
    for question in ruled:
        assert question["rule_version"] == str(versions[question["rule_id"]]), question["key"]
    for question in body["questions"]:
        if not question["rule_id"]:
            assert question["rule_version"] is None


def test_an_answered_question_stays_with_its_answer_until_the_run_reads_it(
    standing: Path, tmp_path: Path
) -> None:
    import shutil

    from ingence.infer.run import run_inference

    copy = tmp_path / "project"
    shutil.copytree(standing, copy)
    before = _sheet(copy)
    audits = [q for q in before["questions"] if q["kind"] == "audit" and q["target"]]
    assert audits and before["summary"]["answered_waiting"] == 0
    chosen = audits[0]
    assert chosen["answer"] is None
    event = _answer(copy, chosen["target"], "correct", note="checked by hand")
    after = _sheet(copy)
    # The sheet is the sheet it was: nothing leaves, nothing takes the place of what was answered.
    assert [q["key"] for q in after["questions"]] == [q["key"] for q in before["questions"]]
    answered = next(q for q in after["questions"] if q["key"] == chosen["key"])
    assert answered["answer"] == {
        "word": "correct",
        "label": "Correct",
        "actor": PERSON,
        "occurred_at": MOMENT,
        "sequence": event.sequence,
        "note": "checked by hand",
        "waits_for_rerun": True,
    }
    assert [q["key"] for q in after["questions"] if q["answer"]] == [chosen["key"]]
    assert after["summary"]["answered_waiting"] == 1
    # The next run reads the answer, and the question leaves the sheet as it always did.
    run_inference(Project.open(copy), run_id="run_20261013T120000Z_cccccccccccc", started_at="t")
    read = _sheet(copy)
    assert chosen["key"] not in [q["key"] for q in read["questions"]]
    assert read["summary"]["answered_waiting"] == 0


def test_a_later_answer_on_the_same_class_is_the_one_the_sheet_carries(
    standing: Path, tmp_path: Path
) -> None:
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(standing, copy)
    audit = next(q for q in _sheet(copy)["questions"] if q["kind"] == "audit" and q["target"])
    _answer(copy, audit["target"], "wrong")
    assert next(q for q in _sheet(copy)["questions"] if q["key"] == audit["key"])["answer"]
    # A later answer on the same class is the one the sheet carries.
    _answer(copy, audit["target"], "correct")
    held = next(q for q in _sheet(copy)["questions"] if q["key"] == audit["key"])
    assert held["answer"]["word"] == "correct"


def test_cannot_say_on_a_tied_word_is_served_as_cannot_say_with_its_label(
    tied_pool: Path, tmp_path: Path
) -> None:
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(tied_pool, copy)
    asked = {q["kind"]: q for q in _sheet(copy)["questions"] if q["target"]}
    for kind in ("tied word", "audit"):
        question = asked[kind]
        _answer(copy, question["target"], "cannot_say", note="not sure")
        held = next(q for q in _sheet(copy)["questions"] if q["key"] == question["key"])
        # The word a person said and the label a button shows, never the event's own kind.
        assert held["answer"]["word"] == "cannot_say", (kind, held["answer"])
        assert held["answer"]["label"] == "Can't say", (kind, held["answer"])
        assert held["answer"]["waits_for_rerun"] is True and held["answer"]["note"] == "not sure"


def test_a_run_that_recorded_no_watermark_drops_every_answered_question_as_before(
    standing: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import ingence.completeness.elsewhere as elsewhere

    copy = tmp_path / "project"
    shutil.copytree(standing, copy)
    audit = next(q for q in _sheet(copy)["questions"] if q["kind"] == "audit" and q["target"])
    _answer(copy, audit["target"], "correct")
    # With no watermark the engine cannot say the answer came after the run: it is the run's.
    monkeypatch.setattr(elsewhere, "run_watermark", lambda catalog: None)
    gone = _sheet(copy)
    assert audit["key"] not in [q["key"] for q in gone["questions"]]
    assert gone["summary"]["answered_waiting"] == 0


# ---------------------------------------------------------------------------
# A pack: examples, "Can't say", the rule's version.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_packs(tmp_path_factory: pytest.TempPathFactory) -> Project:
    from test_question_sheet import _build

    return _build(tmp_path_factory.mktemp("pack"))


@pytest.fixture
def packs(built_packs: Project, tmp_path: Path) -> Project:
    """**A copy of its own for each test**: the tests write answers into the Record, and one
    test's answer must never be in force in another's."""
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built_packs.path, copy)
    return Project.open(copy)


def test_no_pack_is_asked_on_the_sheet(packs: Project) -> None:
    # Every domain section loads, so a pool of instrument files is asked about no pack.
    questions = _sheet(packs)["questions"]
    assert questions
    assert not [q for q in questions if q["kind"] == "pack"]
    assert not [q for q in questions if str(q["target"] or "").startswith("pack:")]


def test_a_pack_takes_cannot_say_as_a_note_that_decides_nothing(packs: Project) -> None:
    before = _sheet(packs)
    event = _answer(packs.path, "pack:microscopy", "cannot_say", note="ask the group")
    assert event.kind.value == "note"
    payload = json.loads(event.payload)
    assert payload["note"] == "Can't say. ask the group" and "verdict" not in payload
    plain = _answer(packs.path, "pack:microscopy", "cannot_say")
    assert json.loads(plain.payload)["note"] == "Can't say"
    # It decided nothing: no decision is in force, and the sheet is the sheet it was (a pack is not
    # a question on it).
    assert "pack:microscopy" not in latest_by_target(read_events(packs.path))
    after = _sheet(packs)
    assert [q["key"] for q in after["questions"]] == [q["key"] for q in before["questions"]]


def test_a_pack_still_takes_accept_and_refuses_what_it_is_not_offered(packs: Project) -> None:
    from ingence.api.routes.decide import AnswerRefusedError

    with pytest.raises(AnswerRefusedError):
        _answer(packs.path, "pack:microscopy", "correct")
    event = _answer(packs.path, "pack:microscopy", "accept")
    # An accepted pack is a prior for its domain, recorded, and asked about on no sheet.
    assert event.kind.value == "confirm"
    assert "pack:microscopy" in latest_by_target(read_events(packs.path))


# ---------------------------------------------------------------------------
# The patterns' sentences.
# ---------------------------------------------------------------------------


def test_a_pattern_s_sentence_has_no_marks_and_says_when_names_hold_no_source(
    explorer_project: Any,
) -> None:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(explorer_project.project, settled=None, job_running=False)
    status, body = dispatch(context, "questions/sheet", {})
    assert status == HTTPStatus.OK, body
    marked = [q for q in body["questions"] if q["kind"] in ("derivation pattern", "atypical pair")]
    assert marked
    for question in marked:
        for text in (question["question"], question["meaning"], *question["examples"]):
            assert "{src}" not in text and "(no stem)" not in text, text
        name, holds = readable_template(question["template"])
        assert f'"{name[:60]}"' in question["question"]
        assert ("[source]" in name) or not holds
        assert question["question"].endswith(NO_STEM_SAID) == (not holds)
        assert "[source]" in question["meaning"]


def test_a_name_pattern_is_read_for_a_person() -> None:
    assert readable_template("tracks_{src}.csv") == ("tracks_[source].csv", True)
    assert readable_template("(no stem) summary_#.xlsx") == ("summary_#.xlsx", False)
    assert name_template("zeph_007.nd2", "tracks_zeph_007.csv") == "tracks_{src}.csv"
    assert name_template("zeph_007.nd2", "summary_2.xlsx") == "(no stem) summary_#.xlsx"
    assert "[source]" in TEMPLATE_MEANING and "{src}" not in TEMPLATE_MEANING


# ---------------------------------------------------------------------------
# The words a label and a description use.
# ---------------------------------------------------------------------------


def test_the_elsewhere_label_is_in_american_spelling() -> None:
    from ingence.completeness.elsewhere import ELSEWHERE_WORDS

    assert shown_word("elsewhere") == "Analyzed elsewhere"
    assert [w["label"] for w in ELSEWHERE_WORDS] == ["Analyzed elsewhere", "Not elsewhere"]


def test_the_answer_target_names_the_one_answer_route() -> None:
    from ingence.api.v1.completeness import ExperimentCompleteness

    said = ExperimentCompleteness.model_fields["answer_target"].description or ""
    assert "/api/review/answer" in said and "/api/decide" not in said


# ---------------------------------------------------------------------------
# The manifest's words.
# ---------------------------------------------------------------------------


def test_a_value_the_dictionary_spells_with_underscores_is_said_as_words() -> None:
    assert _shown("image_format") == "image format"
    assert _shown("image_format (in doubt)") == "image format (in doubt)"
    assert _shown(None) is None and _shown('{"a": 1}') is None


def test_a_rule_with_a_word_no_sentence_carries_is_cited_by_its_plain_words_and_version() -> None:
    held = _cited("group_resolution_deviation", "2")
    assert "resolution" not in held.split("(")[-1] and "deviation" not in held.split("(")[-1]
    assert held.startswith("the rule “") and held.endswith("(version 2)")
    # A rule whose name holds none keeps its name in the sentence.
    assert _cited("pipeline_stage_from_name", "2").endswith("(pipeline stage from name, version 2)")
    # And every shipped rule is cited without a word the Dashboard's sentences never carry.
    from ingence.api.plain_words import plain_words

    never = plain_words().never_shown
    assert {"resolution", "deviation", "candidate", "token"} <= never
    for rule_id in plain_words().rules:
        cited = _cited(rule_id, "1")
        named = cited[cited.rindex("”") + 1 :]
        assert not never & set(named.replace(",", " ").replace("(", " ").split()), cited


def test_a_decision_that_names_no_folder_is_placed_in_words(tmp_path: Path) -> None:
    from ingence.core.schema import RecordEventKind

    project = _small(tmp_path)
    with open_catalog(project.path) as catalog:
        folder = catalog.query(
            "SELECT entry_id, relative_path FROM entries WHERE entry_type = 'directory' "
            "AND relative_path <> '' ORDER BY relative_path LIMIT 1"
        ).fetchone()
        assert folder is not None
        elsewhere = append_event(
            project.path,
            kind=RecordEventKind.CONFIRM,
            actor=PERSON,
            occurred_at=MOMENT,
            target=f"analysed_elsewhere:{folder[0]}",
            against_run_id="run_20261013T000002Z_bbbbbbbbbbbb",
            payload={"verdict": "elsewhere"},
        )
        assert _place_words(catalog, elsewhere) == f"the analysis of {folder[1]}"
        gone = elsewhere.model_copy(update={"target": "analysed_elsewhere:entry_nowhere"})
        assert _place_words(catalog, gone) == "an experiment's analysis"
        klass = elsewhere.model_copy(
            update={
                "target": "class:format_from_extension:0a1b2c",
                "payload": json.dumps({"of": "claims", "value": "image_format"}),
            }
        )
        assert _place_words(catalog, klass) == (
            "every conclusion read as “image format” by one rule"
        )
        bare = klass.model_copy(update={"payload": "{}"})
        assert _place_words(catalog, bare) == "every conclusion of one class"
        other = elsewhere.model_copy(update={"target": "extension:csv"})
        assert _place_words(catalog, other) == "extension csv"


def _small(base: Path) -> Project:
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "pool"
    (pool / "2024-03-05 kelt").mkdir(parents=True)
    (pool / "2024-03-05 kelt" / "vorn_01.csv").write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20261013T000001Z_aaaaaaaaaaaa", started_at="t")
    run_inference(project, run_id="run_20261013T000002Z_bbbbbbbbbbbb", started_at="t")
    return project


# ---------------------------------------------------------------------------
# A theme's words beside its number.
# ---------------------------------------------------------------------------


def test_completeness_names_each_experiment_s_theme_in_words(tmp_path: Path) -> None:
    from ingence.api.v1 import context_for, dispatch

    path, plant = build_theme_project(tmp_path)
    context = context_for(path, settled=None, job_running=False)
    _, themes = dispatch(context, "themes", {})
    _, rows = dispatch(context, "completeness", {})
    _, none = dispatch(context, "completeness/no_analysis_found", {})
    label_of = {row["id"]: row["theme_label"] for row in themes["experiments"]}
    assert any(label_of.values())
    for row in rows["experiments"]:
        assert row["theme_label"] == label_of[row["id"]]
        assert (row["theme"] is None) == (row["theme_label"] is None)
    for row in [*none["listed"], *none["answered"]]:
        assert row["theme_label"] == label_of[row["id"]]
    assert len(plant.documents) == len(rows["experiments"])
