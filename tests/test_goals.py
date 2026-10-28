"""Goals: a sentence a person wrote, kept in the workspace and never read by inference.

A goal is answered every run as Ask answers a question, lowered to "at least n of m". The
experiments route filters by goal and by ids. Goals typed before a project exists are written into
the new project. Every name is invented."""

from __future__ import annotations

import json

import pytest

from ingence.api.v1 import context_for, dispatch
from ingence.goals import GOALS_FILE, lower_goal, read_goals, remove_goal, write_goal


def test_a_goal_is_written_removed_and_lowered_in_the_workspace(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert read_goals(project) == []
    first = write_goal(project, "  kelt   vorn with analysis ", actor="a. person")
    second = write_goal(project, "brisa titrations")
    assert first.id == "goal_1" and first.text == "kelt vorn with analysis"
    assert first.actor == "a. person" and second.actor is None and second.id == "goal_2"
    assert [goal.id for goal in read_goals(project)] == ["goal_1", "goal_2"]
    assert (project / GOALS_FILE).is_file()
    # A projection: the Record is not written.
    assert not (project / "record").exists()
    lowered = lower_goal(project, "goal_1", 2)
    assert lowered.at_least == 2 and read_goals(project)[0].at_least == 2
    assert remove_goal(project, "goal_2") is True and remove_goal(project, "goal_2") is False
    assert [goal.id for goal in read_goals(project)] == ["goal_1"]
    # Identifiers are never reused.
    assert write_goal(project, "again").id == "goal_3"
    with pytest.raises(ValueError, match="sentence"):
        write_goal(project, "   ")
    with pytest.raises(ValueError, match="500"):
        write_goal(project, "x" * 501)
    with pytest.raises(ValueError, match="identifier"):
        remove_goal(project, "nonsense")
    with pytest.raises(LookupError):
        lower_goal(project, "goal_2", 1)


def test_a_damaged_line_costs_one_goal_never_the_page(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "workspace").mkdir(parents=True)
    (project / GOALS_FILE).write_text(
        "not json\n" + json.dumps({"act": "write", "id": "goal_1", "text": "kelt"}) + "\n"
    )
    assert [goal.text for goal in read_goals(project)] == ["kelt"]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("goals"))


def _carrying(built, *words: str) -> set[str]:
    from fixtures.theme_pool import ENGINE_SURFACES

    wanted = {ENGINE_SURFACES.get(word, word) for word in words}
    return {
        built.ids[unit]
        for unit, written in built.plant.documents.items()
        if unit in built.ids and wanted <= {ENGINE_SURFACES.get(w, w) for w in written}
    }


def test_goals_are_answered_from_the_run_with_matches_why_and_a_summary(built) -> None:
    goal = write_goal(built.project, "keltan and vornish experiments", actor="a. person")
    context = context_for(built.project, settled=None, job_running=False)
    status, body = dispatch(context, "goals", {})
    assert status == 200, body
    assert body["phase"] is None
    (item,) = [row for row in body["goals"] if row["id"] == goal.id]
    assert item["text"] == goal.text
    assert item["read"]["kept"] == ["keltan", "vornish"]
    assert set(item["read"]["dropped"]) == {"and", "experiments"}
    named = {row["experiment_id"] for row in item["matches"] if row["tier"] == "named"}
    assert named == _carrying(built, "keltan", "vornish")
    for row in item["matches"]:
        assert row["why"] and row["label"]
    summary = item["summary"]
    assert summary["named"] == len(named)
    assert (
        summary["experiments"] == summary["named"] + summary["associated"] + summary["same_theme"]
    )
    assert summary["of"] >= summary["raw_with_analysis"]
    assert summary["first"] is None or len(summary["first"]) == 10
    # Lowered to at least one of two words: the named tier widens to either word.
    lower_goal(built.project, goal.id, 1)
    status, body = dispatch(context, "goals", {})
    (item,) = [row for row in body["goals"] if row["id"] == goal.id]
    named_now = {row["experiment_id"] for row in item["matches"] if row["tier"] == "named"}
    assert named_now == _carrying(built, "keltan") | _carrying(built, "vornish")
    partial = [row for row in item["matches"] if row["tier"] == "named" and "1 of 2" in row["why"]]
    assert partial
    remove_goal(built.project, goal.id)


def test_the_experiments_route_filters_by_a_goal_and_by_ids(built) -> None:
    goal = write_goal(built.project, "quolt tamen")
    context = context_for(built.project, settled=None, job_running=False)
    status, body = dispatch(context, "experiments", {"goal": [goal.id], "limit": ["500"]})
    assert status == 200, body
    listed = {row["id"] for row in body["rows"]}
    status, goals = dispatch(context, "goals", {})
    (item,) = [row for row in goals["goals"] if row["id"] == goal.id]
    matched = {row["experiment_id"] for row in item["matches"] if row["tier"] != "same_theme"}
    assert listed == matched and body["matching"] == len(matched)
    two = sorted(listed)[:2]
    status, body = dispatch(context, "experiments", {"ids": [",".join(two)]})
    assert status == 200 and {row["id"] for row in body["rows"]} == set(two)
    status, body = dispatch(context, "experiments", {"goal": ["goal_none"]})
    assert status == 200 and body["matching"] == 0
    remove_goal(built.project, goal.id)


def test_goals_typed_before_a_project_exists_are_written_into_it(tmp_path) -> None:
    from ingence.api.routes.landing import LandingRefusedError, write_goals

    project = tmp_path / "project"
    project.mkdir()
    assert write_goals(project, None) is None
    written = write_goals(project, ["kelt vorn", "  ", "brisa"])
    assert written == [{"id": "goal_1", "text": "kelt vorn"}, {"id": "goal_2", "text": "brisa"}]
    assert [goal.text for goal in read_goals(project)] == ["kelt vorn", "brisa"]
    with pytest.raises(LandingRefusedError):
        write_goals(project, "kelt")
    with pytest.raises(LandingRefusedError):
        write_goals(project, ["x" * 501])


def test_goals_answer_their_words_alone_before_any_run_with_the_job_s_phase(tmp_path) -> None:
    """A project with a goal and no run answers 200, each goal's words widened by the Dictionary
    alone, run_id null; while a job runs, phase says where it is."""
    from ingence.core.project import Project
    from ingence.jobs.ledger import JobLedger

    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    (pool / "kelt" / "notes.txt").write_text("kelt")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_goal(project.path, "which dapi images of kelt?")
    status, body = dispatch(context_for(project.path, settled=None, job_running=False), "goals", {})
    assert status == 200, body
    assert body["run_id"] is None and body["phase"] is None
    (goal,) = body["goals"]
    # Before a run, a file-role word is no filter and is dropped as a how-word.
    assert goal["read"]["kept"] == ["dapi", "kelt"]
    assert goal["read"]["dropped"] == ["which", "images", "of"]
    assert goal["matches"] == [] and goal["summary"]["experiments"] == 0
    assert any(item["word"] == "dapi" and "hoechst" in item["to"] for item in goal["expanded"])
    # A running job: its phase in the contract's words.
    ledger = JobLedger(project.path)
    queued = ledger.enqueue(record_sequence=None, base_run=None)
    claimed = ledger.claim_next(pinned=None)
    assert claimed is not None and claimed.job_id == queued.job_id
    ledger.heartbeat(claimed.job_id, attempt=claimed.attempts, phase="census")
    status, body = dispatch(context_for(project.path, settled=None, job_running=True), "goals", {})
    assert status == 200 and body["phase"] == "listing" and body["run_id"] is None


def test_the_ledger_s_phase_is_said_in_the_contract_s_words() -> None:
    from ingence.api.v1.goals import PHASE_WORDS

    assert PHASE_WORDS == {
        "census": "listing",
        "dive": "reading",
        "binding": "names",
        "inferring": "rules",
        "projections": "views",
    }
