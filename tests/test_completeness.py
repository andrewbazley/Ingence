"""Completeness per experiment: what the engine found, never what exists.

A lack of raw data or analysis files does not mean they do not exist. It means the engine did not
find them. So every count here is what the engine found in what was read. The share of raw
acquisitions followed by an analysis file is null, never 0, where no raw acquisition was found:
unknown is not absent.

The classes, over the grammar of names and declared formats, every name invented:

- a raw acquisition is a file a rule read at the acquisition stage and no rule read at an
  analysis stage; an analysis file is one a rule read at an analysis stage; a results table is a
  table format read at no raw stage;
- a raw acquisition is followed when a derivation the engine proposed or published, or a file
  named for it, comes from it, wherever that file lies, unless the follower is itself a raw
  acquisition (a copy of a recording follows nothing);
- an experiment is a folder a rule classed as one (the highest such above a file); raw data in no
  experiment is counted apart, as found outside every experiment;
- per person (the first folder level below a root) and per theme (from
  a theme provider; absent, the themes are said not computed, never guessed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.workspace.rerun import Change

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


#: The invented pool: two people's folders, each holding dated experiments.
#:
#: - ``vorn/2021-03-01_kelt_zeph``: two acquisitions, one followed by a track table named for it
#:   (share 0.5), one analysis file, one results table;
#: - ``vorn/2021-04-02_kelt_brisa``: six acquisitions and nothing else (share 0.0);
#: - ``tamen/2022-05-10_quolt_plan``: a dated folder of notes, no acquisition (share null);
#: - ``tamen/2022-06-11_quolt_sorn``: three acquisitions, all followed by results named for them,
#:   and a summary workbook;
#: - ``stray_sorn_001.nd2`` at the root, beside the people's folders: raw data in no experiment.
def write_pool(root: Path) -> Path:
    first = root / "vorn" / "2021-03-01_kelt_zeph"
    _write(first / "raw" / "kelt_zeph_001.nd2")
    _write(first / "raw" / "kelt_zeph_002.nd2")
    _write(first / "analysis" / "Traj_kelt_zeph_001.csv", "t,x\n1,2\n")
    unread = root / "vorn" / "2021-04-02_kelt_brisa"
    for number in range(1, 7):
        _write(unread / f"kelt_brisa_{number:03d}.nd2")
    notes = root / "tamen" / "2022-05-10_quolt_plan"
    _write(notes / "plan.txt", "plain words\n")
    whole = root / "tamen" / "2022-06-11_quolt_sorn"
    for number in range(1, 4):
        _write(whole / f"quolt_sorn_{number:03d}.nd2")
        _write(whole / f"quolt_sorn_{number:03d}_results.csv", "a,b\n1,2\n")
    _write(whole / "quolt_sorn_summary.xlsx")
    _write(root / "stray_sorn_001.nd2")
    return root


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("completeness")
    pool = write_pool(base / "pool")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _by_folder(project: Project) -> dict[str, object]:
    from ingence.completeness import completeness_of_run

    found = completeness_of_run(project.path, INFER)
    return {row.folder: row for row in found.experiments}


def test_each_experiment_counts_what_the_engine_found(project: Project) -> None:
    rows = _by_folder(project)
    zeph = rows["vorn/2021-03-01_kelt_zeph"]
    assert zeph.raw_acquisitions == 2
    assert zeph.followed == 1
    assert zeph.derived_files == 1
    assert zeph.analysis_files == 1
    assert zeph.result_tables == 1
    assert zeph.share == 0.5
    brisa = rows["vorn/2021-04-02_kelt_brisa"]
    assert (brisa.raw_acquisitions, brisa.followed, brisa.share) == (6, 0, 0.0)
    assert brisa.files == 6
    sorn = rows["tamen/2022-06-11_quolt_sorn"]
    assert (sorn.raw_acquisitions, sorn.followed, sorn.share) == (3, 3, 1.0)
    assert sorn.result_tables == 4


def test_no_raw_acquisition_found_is_a_null_share_never_zero(project: Project) -> None:
    plan = _by_folder(project)["tamen/2022-05-10_quolt_plan"]
    assert plan.raw_acquisitions == 0
    assert plan.share is None
    assert plan.has_analysis is False


def test_raw_data_in_no_experiment_is_counted_apart(project: Project) -> None:
    from ingence.completeness import completeness_of_run

    found = completeness_of_run(project.path, INFER)
    assert "stray_sorn_001.nd2" not in {row.folder for row in found.experiments}
    assert found.outside.raw_acquisitions == 1
    assert found.outside.followed == 0


def test_an_experiment_inside_another_is_part_of_it_and_a_folder_in_none_has_none() -> None:
    from ingence.experiment_units import highest_experiment

    parents = {
        "root": None,
        "vorn": "root",
        "kelt_study": "vorn",
        "kelt_study/raw": "kelt_study",
        "kelt_study/raw/day2": "kelt_study/raw",
        "notes": "root",
    }
    units = highest_experiment(parents, frozenset({"kelt_study", "kelt_study/raw/day2"}))
    # The highest experiment above a folder is its experiment; one inside it is part of it.
    assert units == {
        "kelt_study": "kelt_study",
        "kelt_study/raw": "kelt_study",
        "kelt_study/raw/day2": "kelt_study",
    }


def test_an_analysis_of_its_own_is_enough_analysis_files_or_results_tables() -> None:
    from ingence.completeness import ExperimentFound
    from ingence.core.settings import CompletenessSettings

    settings = CompletenessSettings()
    base = {
        "experiment_id": "entry_a",
        "root_id": "root_a",
        "folder": "zeph",
        "files": 40,
        "raw_acquisitions": 20,
        "followed": 0,
        "derived_files": 0,
    }
    assert not ExperimentFound(**base, analysis_files=4, result_tables=2).has_analysis_under(
        settings
    )
    assert ExperimentFound(**base, analysis_files=5, result_tables=0).has_analysis_under(settings)
    assert ExperimentFound(**base, analysis_files=0, result_tables=3).has_analysis_under(settings)


def test_a_person_is_the_folder_person_level_below_a_root(project: Project) -> None:
    from ingence.completeness import completeness_of_run
    from ingence.experiment_units import person_of

    # The one definition completeness and themes share (themes.person_level, default 1): the
    # folder that many levels below the root, above the experiment; none for an experiment at or
    # above that level, and none where the setting is blank.
    assert person_of("vorn/2021-03-01_kelt_zeph", 1) == "vorn"
    assert person_of("vorn/brisa/2021-03-01_kelt_zeph", 2) == "brisa"
    assert person_of("kelt_zeph", 1) is None
    assert person_of(".", 1) is None
    assert person_of("vorn/2021-03-01_kelt_zeph", None) is None
    found = completeness_of_run(project.path, INFER)
    people = {group.key: group for group in found.people}
    assert set(people) == {"vorn", "tamen"}
    assert (people["vorn"].experiments, people["vorn"].raw_acquisitions) == (2, 8)
    assert people["vorn"].followed == 1
    assert people["vorn"].share == 1 / 8
    assert people["tamen"].share == 1.0
    assert people["tamen"].experiments_with_raw == 1


def test_per_theme_comes_from_a_provider_and_is_never_guessed(project: Project) -> None:
    from ingence.completeness import completeness_of_run

    absent = completeness_of_run(project.path, INFER, themes=lambda project, run_id: None)
    assert absent.themes_computed is False
    assert absent.themes == []
    rows = _by_folder(project)
    themed = {
        rows["vorn/2021-03-01_kelt_zeph"].experiment_id: "kelt",
        rows["vorn/2021-04-02_kelt_brisa"].experiment_id: "kelt",
        rows["tamen/2022-06-11_quolt_sorn"].experiment_id: "quolt",
    }
    found = completeness_of_run(project.path, INFER, themes=lambda project, run_id: themed)
    assert found.themes_computed is True
    by_theme = {group.key: group for group in found.themes}
    assert (by_theme["kelt"].experiments, by_theme["kelt"].raw_acquisitions) == (2, 8)
    assert by_theme["quolt"].share == 1.0
    # An experiment the provider gives no theme is in no theme's sum, and says so.
    plan = {row.folder: row for row in found.experiments}["tamen/2022-05-10_quolt_plan"]
    assert plan.theme is None


def _fake_themes(monkeypatch: pytest.MonkeyPatch, function: object | None) -> None:
    """Stand in for ``ingence.themes``: absent (None), or with ``theme_assignments``."""
    import sys
    import types

    from ingence.api import v1

    # The route modules import the themes route, which imports the real package: loaded before
    # the stand-in replaces it, so a dispatch inside the test never imports through the fake.
    v1.load_routes()
    if function is None:
        monkeypatch.setitem(sys.modules, "ingence.themes", None)  # an import of it fails
        return
    fake = types.ModuleType("ingence.themes")
    fake.theme_assignments = function
    monkeypatch.setitem(sys.modules, "ingence.themes", fake)


def test_without_the_themes_module_the_themes_are_not_computed_and_say_why(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.completeness import completeness_of_run

    _fake_themes(monkeypatch, None)
    found = completeness_of_run(project.path, INFER)
    assert found.themes_computed is False
    assert "no theme assignments" in found.themes_said


def test_a_failing_theme_engine_says_themes_not_computed_with_its_reason(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api import v1
    from ingence.completeness import completeness_of_run
    from ingence.core.registry import ComponentNotAvailableError

    def raising(project: Path, run_id: str) -> dict[str, object]:
        message = "theme engine 'vornish' is unavailable: a dependency is missing"
        raise ComponentNotAvailableError(message)

    _fake_themes(monkeypatch, raising)
    found = completeness_of_run(project.path, INFER)
    assert found.themes_computed is False
    assert "a dependency is missing" in found.themes_said
    assert "ComponentNotAvailableError" not in found.themes_said  # plain words, no class name
    assert found.experiments  # the counts stand without themes
    context = v1.context_for(project.path, settled=None, job_running=False)
    for route in ("completeness", "completeness/no_analysis_found"):
        status, payload = v1.dispatch(context, route, {})
        assert status == 200, payload
        assert "a dependency is missing" in payload["themes_said"]


def test_the_themes_module_s_shape_is_read_and_an_unplaced_experiment_has_no_theme(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.completeness import completeness_of_run

    rows = _by_folder(project)
    zeph = rows["vorn/2021-03-01_kelt_zeph"].experiment_id
    brisa = rows["vorn/2021-04-02_kelt_brisa"].experiment_id

    def shaped(project: Path, run_id: str) -> dict[str, dict[str, object]]:
        return {
            zeph: {"theme_id": 3, "broad_id": 1, "label": "kelt · zeph"},
            brisa: {"theme_id": None, "broad_id": None, "unplaced_reason": "too far"},
        }

    _fake_themes(monkeypatch, shaped)
    found = completeness_of_run(project.path, INFER)
    assert found.themes_computed is True
    themes = {row.folder: row.theme for row in found.experiments}
    assert themes["vorn/2021-03-01_kelt_zeph"] == "3"
    assert themes["vorn/2021-04-02_kelt_brisa"] is None
    assert [(group.key, group.experiments) for group in found.themes] == [("3", 1)]


def test_the_route_serves_the_pinned_run_and_its_words_say_found(project: Project) -> None:
    from ingence.api import v1

    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "completeness", {})
    assert status == 200, payload
    assert payload["run_id"] == INFER
    rows = {row["folder"]: row for row in payload["experiments"]}
    plan = rows["tamen/2022-05-10_quolt_plan"]
    assert plan["share_of_raw_followed_by_analysis"] is None
    assert "no raw acquisition found" in plan["said"].lower()
    brisa = rows["vorn/2021-04-02_kelt_brisa"]
    assert brisa["raw_acquisitions"] == 6
    assert "found" in brisa["said"]
    schema = v1.DECLARED["completeness"].model_json_schema()
    row = schema["$defs"]["ExperimentCompleteness"]["properties"]
    for name in ("raw_acquisitions", "derived_files", "analysis_files", "result_tables"):
        assert "found" in row[name]["description"], name
    assert "null" in row["share_of_raw_followed_by_analysis"]["description"]


# ---------------------------------------------------------------------------
# "No analysis found", with its two reasons (design request 4).
# ---------------------------------------------------------------------------


def _found(
    name: str,
    *,
    raw: int,
    followed: int,
    theme: str | None = "kelt",
    analysis_files: int = 0,
    result_tables: int = 0,
) -> object:
    from ingence.completeness import ExperimentFound

    return ExperimentFound(
        experiment_id=f"entry_{name}",
        root_id="root_a",
        folder=f"vorn/{name}",
        files=raw + analysis_files + result_tables,
        raw_acquisitions=raw,
        followed=followed,
        derived_files=followed,
        analysis_files=analysis_files,
        result_tables=result_tables,
        theme=theme,
    )


def _listed(experiments: list[object], **overrides: object) -> dict[str, object]:
    from ingence.completeness import no_analysis_found
    from ingence.core.settings import CompletenessSettings

    settings = CompletenessSettings(**overrides)  # type: ignore[arg-type]
    found = no_analysis_found(experiments, settings, themes_computed=True)  # type: ignore[arg-type]
    return {item.experiment.folder: item for item in found.listed}


def test_listed_when_under_half_followed_with_no_analysis_of_its_own() -> None:
    listed = _listed(
        [
            _found("zeph_a", raw=10, followed=4),
            _found("zeph_b", raw=10, followed=5),
            _found("zeph_c", raw=4, followed=0),
            _found("zeph_d", raw=10, followed=0, analysis_files=5),
            _found("zeph_e", raw=10, followed=0, result_tables=3),
        ]
    )
    # Under half followed: listed. Half: not. Fewer than five raw acquisitions found: not judged.
    # An analysis of its own (five analysis files, or three results tables): not listed.
    assert set(listed) == {"vorn/zeph_a"}


def test_forgotten_when_the_rest_of_its_theme_is_at_least_half_followed() -> None:
    listed = _listed(
        [
            _found("kelt_a", raw=6, followed=0),
            _found("kelt_b", raw=10, followed=5),
            _found("kelt_c", raw=10, followed=5),
        ]
    )
    item = listed["vorn/kelt_a"]
    assert item.theme_rest == 0.5
    assert item.reason == "forgotten"
    assert "forgotten" in item.reason_said


def test_a_line_that_stopped_when_the_rest_of_its_theme_is_mostly_not_followed() -> None:
    listed = _listed(
        [
            _found("kelt_a", raw=6, followed=0),
            _found("kelt_b", raw=10, followed=1),
            _found("kelt_c", raw=10, followed=2),
        ]
    )
    item = listed["vorn/kelt_a"]
    assert item.theme_rest == 3 / 20
    assert item.reason == "stopped"
    # The others are listed too, and each reads the rest of the theme without itself.
    assert listed["vorn/kelt_b"].theme_rest == 2 / 16


def test_between_the_bounds_no_reason_is_given_and_it_is_still_listed() -> None:
    listed = _listed(
        [
            _found("kelt_a", raw=6, followed=0),
            _found("kelt_b", raw=10, followed=3),
            _found("kelt_c", raw=10, followed=4),
        ]
    )
    item = listed["vorn/kelt_a"]
    assert item.theme_rest == 0.35
    assert item.reason is None
    assert "no analysis found" in item.said.lower()


def test_the_rest_of_a_theme_counts_only_experiments_judged_and_never_another_theme() -> None:
    listed = _listed(
        [
            _found("kelt_a", raw=6, followed=0),
            _found("kelt_small", raw=4, followed=4),  # too few to be judged: not in the rest
            _found("vorn_b", raw=10, followed=10, theme="vorn"),
        ]
    )
    item = listed["vorn/kelt_a"]
    assert item.theme_rest is None
    assert item.reason is None
    assert "no other experiment of its theme" in item.reason_said


def test_without_themes_no_reason_is_given_and_the_words_say_so() -> None:
    from ingence.completeness import no_analysis_found
    from ingence.core.settings import CompletenessSettings

    found = no_analysis_found(
        [_found("kelt_a", raw=6, followed=0, theme=None), _found("kelt_b", raw=9, followed=9)],  # type: ignore[list-item]
        CompletenessSettings(),
        themes_computed=False,
    )
    (item,) = found.listed
    assert item.reason is None
    assert "themes not computed" in item.reason_said.lower()


def test_every_threshold_is_a_setting() -> None:
    experiments = [
        _found("kelt_a", raw=6, followed=2),
        _found("kelt_b", raw=10, followed=6),
    ]
    assert set(_listed(experiments)) == {"vorn/kelt_a"}
    assert set(_listed(experiments, listed_below_share=0.3)) == set()
    assert set(_listed(experiments, judged_from_acquisitions=7)) == set()
    assert _listed(experiments)["vorn/kelt_a"].reason == "forgotten"
    assert _listed(experiments, forgotten_at_least=0.7)["vorn/kelt_a"].reason is None
    assert (
        _listed(experiments, forgotten_at_least=0.8, stopped_below=0.7)["vorn/kelt_a"].reason
        == "stopped"
    )
    with pytest.raises(ValueError, match="stopped_below"):
        _listed(experiments, forgotten_at_least=0.3, stopped_below=0.4)


def test_listed_by_share_then_most_raw_data_first() -> None:
    from ingence.completeness import no_analysis_found
    from ingence.core.settings import CompletenessSettings

    found = no_analysis_found(
        [
            _found("kelt_a", raw=6, followed=2),
            _found("kelt_b", raw=20, followed=0),
            _found("kelt_c", raw=8, followed=0),
        ],  # type: ignore[list-item]
        CompletenessSettings(),
        themes_computed=True,
    )
    assert [item.experiment.folder for item in found.listed] == [
        "vorn/kelt_b",
        "vorn/kelt_c",
        "vorn/kelt_a",
    ]
    assert found.judged == 3


def test_the_no_analysis_route_lists_what_was_found_and_says_found(project: Project) -> None:
    from ingence.api import v1

    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "completeness/no_analysis_found", {})
    assert status == 200, payload
    listed = {row["folder"]: row for row in payload["listed"]}
    # Six raw acquisitions found, none followed, no analysis of its own; the others are not.
    assert set(listed) == {"vorn/2021-04-02_kelt_brisa"}
    brisa = listed["vorn/2021-04-02_kelt_brisa"]
    assert brisa["share_of_raw_followed_by_analysis"] == 0.0
    assert "no analysis found in what was read" in brisa["said"].lower()
    assert payload["judged"] == 1
    if not payload["themes_computed"]:
        assert brisa["reason"] is None
        assert "themes not computed" in brisa["reason_said"].lower()
    status, whole = v1.dispatch(context, "completeness", {})
    people = {row["key"]: row for row in whole["people"]}
    assert people["vorn"]["no_analysis_found"] == 1
    assert people["tamen"]["no_analysis_found"] == 0


# ---------------------------------------------------------------------------
# "Analysed elsewhere": a person's answer (design request 11).
# ---------------------------------------------------------------------------

#: The invented person answering; never a real name.
PERSON = "a. person"
LATER = "run_20260101T000003Z_333333333333"
LATEST = "run_20260101T000004Z_444444444444"


@pytest.fixture
def answered_project(tmp_path: Path) -> Project:
    """A project of its own: these tests write to its Record and run it again."""
    pool = write_pool(tmp_path / "pool")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def _experiment(project: Project, run_id: str, folder: str) -> str:
    from ingence.completeness import completeness_of_run

    found = completeness_of_run(project.path, run_id)
    return next(row.experiment_id for row in found.experiments if row.folder == folder)


BRISA = "vorn/2021-04-02_kelt_brisa"


def _say(project: Project, experiment_id: str, word: str, *, actor: str = PERSON) -> object:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.record.targets import analysed_elsewhere_target
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        return decide_target(
            DecisionContext(catalog, project.path),
            Decision(target=analysed_elsewhere_target(experiment_id), word=word),
            actor=actor,
            occurred_at="2026-10-08T01:00:00Z",
        )


def test_a_person_says_the_analysis_is_elsewhere_through_the_one_answer_route(
    answered_project: Project,
) -> None:
    import json

    from ingence.api.routes.decide import AnswerRefusedError
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_events

    brisa = _experiment(answered_project, INFER, BRISA)
    event = _say(answered_project, brisa, "elsewhere")
    assert event.kind is RecordEventKind.CONFIRM
    assert event.actor == PERSON
    assert event.target == f"analysed_elsewhere:{brisa}"
    payload = json.loads(event.payload)
    assert payload["verdict"] == "elsewhere"
    assert payload["path"] == BRISA
    assert [e.event_id for e in read_events(answered_project.path)] == [event.event_id]
    # Refused: nobody, a word not offered, a folder no rule classed an experiment, no folder.
    with pytest.raises(AnswerRefusedError, match="who made it"):
        _say(answered_project, brisa, "elsewhere", actor=" ")
    with pytest.raises(AnswerRefusedError, match="not an answer here"):
        _say(answered_project, brisa, "analysed")
    from ingence.storage.catalog import open_catalog

    with open_catalog(answered_project.path) as catalog:
        (person_folder,) = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'vorn'"
        ).fetchone()
    with pytest.raises(AnswerRefusedError, match="no experiment"):
        _say(answered_project, str(person_folder), "elsewhere")
    with pytest.raises(AnswerRefusedError, match="no experiment"):
        _say(answered_project, "entry_nothing", "elsewhere")


def test_it_leaves_the_list_at_the_next_run_and_is_shown_as_answered(
    answered_project: Project,
) -> None:
    from ingence.completeness import completeness_of_run

    brisa = _experiment(answered_project, INFER, BRISA)
    _say(answered_project, brisa, "elsewhere")
    # The Dashboard never runs anything by itself: the run read before the answer still lists it,
    # and the flag says the answer waits for the next re-run.
    before = completeness_of_run(answered_project.path, INFER)
    assert [item.experiment.folder for item in before.no_analysis.listed] == [BRISA]
    flagged = before.no_analysis.listed[0].experiment.elsewhere
    assert flagged is not None
    assert (flagged.read_by_run, flagged.said_now, flagged.actor) == (False, True, PERSON)
    assert "next re-run" in flagged.said
    # The next run reads it: the experiment leaves the list and is shown as answered.
    run_inference(answered_project, run_id=LATER, started_at="t")
    after = completeness_of_run(answered_project.path, LATER)
    assert after.no_analysis.listed == []
    assert [item.experiment.folder for item in after.no_analysis.answered] == [BRISA]
    row = {row.folder: row for row in after.experiments}[BRISA]
    assert row.elsewhere is not None
    assert (row.elsewhere.read_by_run, row.elsewhere.said_now) == (True, True)
    # Counted per person as answered, not as having no analysis found.
    vorn = {group.key: group for group in after.people}["vorn"]
    assert (vorn.no_analysis_found, vorn.analysed_elsewhere) == (0, 1)


def test_taken_back_it_returns_to_the_list_at_the_next_run(answered_project: Project) -> None:
    from ingence.completeness import completeness_of_run

    brisa = _experiment(answered_project, INFER, BRISA)
    _say(answered_project, brisa, "elsewhere")
    run_inference(answered_project, run_id=LATER, started_at="t")
    _say(answered_project, brisa, "not_elsewhere")
    waiting = completeness_of_run(answered_project.path, LATER)
    (item,) = waiting.no_analysis.answered
    assert item.experiment.elsewhere is not None
    assert (item.experiment.elsewhere.read_by_run, item.experiment.elsewhere.said_now) == (
        True,
        False,
    )
    assert "taken back" in item.experiment.elsewhere.said
    run_inference(answered_project, run_id=LATEST, started_at="t")
    again = completeness_of_run(answered_project.path, LATEST)
    assert [listed.experiment.folder for listed in again.no_analysis.listed] == [BRISA]
    assert again.no_analysis.listed[0].experiment.elsewhere is None


def test_a_withdrawal_undoes_it_like_any_decision(answered_project: Project) -> None:
    from ingence.completeness import completeness_of_run
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.record.targets import restore_target

    brisa = _experiment(answered_project, INFER, BRISA)
    said = _say(answered_project, brisa, "elsewhere")
    run_inference(answered_project, run_id=LATER, started_at="t")
    # Going back to the first run withdraws what was decided since (schema 1.22), this too.
    append_event(
        answered_project.path,
        kind=RecordEventKind.WITHDRAW,
        actor=PERSON,
        occurred_at="2026-10-08T01:05:00Z",
        target=restore_target(INFER),
        against_run_id=LATER,
        payload={"withdrawn": [said.event_id], "restored_to": INFER, "previewed": "digest"},
    )
    run_inference(answered_project, run_id=LATEST, started_at="t")
    again = completeness_of_run(answered_project.path, LATEST)
    assert [item.experiment.folder for item in again.no_analysis.listed] == [BRISA]
    assert again.no_analysis.answered == []


def test_the_routes_show_the_answer_and_the_list_without_it(answered_project: Project) -> None:
    from ingence.api import v1

    brisa = _experiment(answered_project, INFER, BRISA)
    _say(answered_project, brisa, "elsewhere")
    run_inference(answered_project, run_id=LATER, started_at="t")
    context = v1.context_for(answered_project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "completeness/no_analysis_found", {})
    assert status == 200, payload
    assert payload["listed"] == []
    (answered,) = payload["answered"]
    assert answered["folder"] == BRISA
    assert answered["analysed_elsewhere"]["actor"] == PERSON
    assert answered["analysed_elsewhere"]["read_by_run"] is True
    assert answered["answer_target"] == f"analysed_elsewhere:{brisa}"
    status, whole = v1.dispatch(context, "completeness", {})
    rows = {row["folder"]: row for row in whole["experiments"]}
    assert rows[BRISA]["analysed_elsewhere"]["said_now"] is True
    assert rows["vorn/2021-03-01_kelt_zeph"]["analysed_elsewhere"] is None
    assert {word["word"] for word in whole["answer_words"]} == {"elsewhere", "not_elsewhere"}


# ---------------------------------------------------------------------------
# Nested experiments, copies in another format, the Re-run list, installed programs.
# ---------------------------------------------------------------------------


def _built(base: Path, pool: Path) -> Project:
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    return created


def test_only_the_highest_experiment_above_each_file_is_an_experiment(tmp_path: Path) -> None:
    from ingence.completeness import completeness_of_run

    pool = tmp_path / "pool"
    top = pool / "vorn" / "2021-03-01_kelt_zeph"
    for number in range(1, 7):
        _write(top / "raw" / f"kelt_zeph_{number:03d}.nd2")
    _write(top / "analysis" / "Traj_kelt_zeph_001.csv", "t,x\n1,2\n")
    _write(top / "analysis" / "Traj_kelt_zeph_002.csv", "t,x\n1,2\n")
    _write(top / "analysis" / "kelt_zeph_summary.xlsx")
    inner = top / "day2_kelt_zeph"
    for number in (101, 102):
        _write(inner / f"kelt_zeph_{number}.nd2")
    _write(inner / "kelt_zeph_101_results.csv", "a,b\n1,2\n")
    series = pool / "tamen" / "quolt_series"
    for number in range(1, 6):
        _write(series / f"quolt_sorn_{number:03d}.nd2")
    tracks = pool / "tamen" / "quolt_analysis"
    _write(tracks / "quolt_sorn_001_tracks.csv", "a,b\n1,2\n")
    _write(tracks / "quolt_sorn_002_tracks.csv", "a,b\n1,2\n")
    _write(tracks / "summary_quolt.xlsx")
    _write(pool / "stray_brisa_001.nd2")
    created = _built(tmp_path, pool)
    found = completeness_of_run(created.path, INFER)
    # The folders classed inside an experiment (its acquisitions, its analysis, a later day) are
    # part of it, never experiments of their own.
    rows = {row.folder: row for row in found.experiments}
    assert sorted(rows) == ["tamen", "vorn/2021-03-01_kelt_zeph"]
    zeph = rows["vorn/2021-03-01_kelt_zeph"]
    assert (zeph.files, zeph.raw_acquisitions, zeph.followed, zeph.derived_files) == (12, 8, 3, 3)
    assert (zeph.analysis_files, zeph.result_tables, zeph.share) == (4, 4, 0.375)
    quolt = rows["tamen"]
    assert (quolt.files, quolt.raw_acquisitions, quolt.followed, quolt.share) == (8, 5, 2, 0.4)
    # ``tamen`` is an experiment at the first level, so it belongs to no person (person_level 1).
    assert {group.key: group.experiments for group in found.people} == {"vorn": 1}
    assert (found.outside.files, found.outside.raw_acquisitions) == (1, 1)


def test_the_same_recording_in_another_format_is_no_analysis_of_it(tmp_path: Path) -> None:
    from ingence.completeness import completeness_of_run

    # ``raw/zeph_007.nd2`` is followed by a tracks table named for it, not by
    # ``raw/zeph_007.tif``.
    pool = tmp_path / "pool"
    experiment = pool / "vorn" / "2021-05-04_zeph_brisa"
    for number in range(1, 7):
        _write(experiment / "raw" / f"zeph_{number:03d}.nd2")
        _write(experiment / "raw" / f"zeph_{number:03d}.tif")
    _write(experiment / "tracks" / "tracks_zeph_007.nd2 - C=0-1.csv", "t,x\n1,2\n")
    _write(experiment / "raw" / "zeph_007.nd2")
    _write(experiment / "raw" / "zeph_007.tif")
    created = _built(tmp_path, pool)
    found = completeness_of_run(created.path, INFER)
    (row,) = found.experiments
    assert row.raw_acquisitions == 7
    assert (row.followed, row.derived_files) == (1, 1)
    assert [item.experiment.folder for item in found.no_analysis.listed] == [row.folder]


def test_files_of_an_installed_program_inside_an_experiment_are_not_counted(
    tmp_path: Path,
) -> None:
    from ingence.completeness import completeness_of_run

    pool = tmp_path / "pool"
    experiment = pool / "vorn" / "2021-06-07_kelt_quolt"
    for number in range(1, 4):
        _write(experiment / f"kelt_quolt_{number:03d}.nd2")
    _write(experiment / "kelt_quolt_001_results.csv", "a,b\n1,2\n")
    program = experiment / "quoltview"
    _write(program / "bin" / "quoltview")
    _write(program / "lib" / "sample_quolt_001.nd2")
    _write(program / "lib" / "sample_quolt_001_results.csv", "a,b\n1,2\n")
    _write(program / "README")
    created = _built(tmp_path, pool)
    from ingence.storage.catalog import open_catalog

    with open_catalog(created.path) as catalog:
        flagged = catalog.query(
            "SELECT count(*) FROM findings WHERE rule_id LIKE '%installed_software%'"
        ).fetchone()
    assert flagged is not None
    assert flagged[0] >= 1, "the engine reads the folder as an installed program"
    with open_catalog(created.path) as catalog:
        # The one definition leaves a program's folders out, so themes and completeness agree.
        from ingence.experiment_units import run_units

        units = run_units(catalog)
        program_folders = {
            folder
            for folder, path in units.paths.items()
            if path.endswith("/quoltview") or "/quoltview/" in path
        }
        assert program_folders, "the program's folders are entries of the run"
        assert program_folders <= units.programs
        assert not program_folders & set(units.units)
        assert {units.paths[unit] for unit in units.units.values()} == {
            "vorn/2021-06-07_kelt_quolt"
        }
    found = completeness_of_run(created.path, INFER)
    (row,) = found.experiments
    assert (row.files, row.raw_acquisitions, row.result_tables) == (4, 3, 1)


def _answers_waiting(project: Project) -> list[Change]:
    """The changes waiting for a re-run other than a pack's.

    The pool's ND2 files leave the microscopy pack, accepted by default, waiting for the names
    to be read again. These tests ask only about the answers.
    """
    from ingence.workspace.state import pending_rerun

    waiting = pending_rerun(project.path)
    return (
        [] if waiting is None else [change for change in waiting.changes if change.kind != "pack"]
    )


def test_an_answer_and_its_taking_back_each_wait_on_the_re_run_list(
    answered_project: Project,
) -> None:
    from ingence.workspace.rerun import Step

    assert _answers_waiting(answered_project) == []
    brisa = _experiment(answered_project, INFER, BRISA)
    _say(answered_project, brisa, "elsewhere")
    (change,) = _answers_waiting(answered_project)
    assert change.needs == (Step.INFERENCE,)
    assert any("marked as analyzed elsewhere" in row for row in change.rows), change.rows
    assert any(BRISA in row for row in change.rows), change.rows
    run_inference(answered_project, run_id=LATER, started_at="t")
    assert _answers_waiting(answered_project) == []
    _say(answered_project, brisa, "not_elsewhere")
    (change,) = _answers_waiting(answered_project)
    assert any("no longer" in row for row in change.rows), change.rows


def test_a_derivation_a_person_confirmed_counts_whatever_its_names_say(tmp_path: Path) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.completeness import completeness_of_run
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    experiment = pool / "vorn" / "2021-07-08_zeph_sorn"
    for number in range(1, 7):
        _write(experiment / "raw" / f"zeph_sorn_{number:03d}.nd2")
    _write(experiment / "analysis" / "zeph_sorn_001.csv", "t,x\n1,2\n")
    created = _built(tmp_path, pool)
    with open_catalog(created.path) as catalog:
        proposed = catalog.query(
            "SELECT c.candidate_id FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id WHERE c.primitive = 'derives' "
            "AND s.name = 'zeph_sorn_001.nd2' AND t.name = 'zeph_sorn_001.csv'"
        ).fetchone()
    assert proposed is not None, "the engine proposes the table as made from the recording"
    # Proposed only, a table named exactly as the recording is the naming guess left out.
    (row,) = completeness_of_run(created.path, INFER).experiments
    assert row.followed == 0
    with open_catalog(created.path) as catalog:
        decide_target(
            DecisionContext(catalog, created.path),
            Decision(target=f"candidate:{proposed[0]}", word="derived"),
            actor=PERSON,
            occurred_at="2026-10-08T07:30:00Z",
        )
    # A person's answer outranks the naming guess, from the next run.
    run_inference(created, run_id=LATER, started_at="t")
    (row,) = completeness_of_run(created.path, LATER).experiments
    assert (row.followed, row.derived_files) == (1, 1)
