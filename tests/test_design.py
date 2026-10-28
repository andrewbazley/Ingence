"""The design grid of each experiment, read from a run, and the series rule.

An invented pool of experiments, one design in each, every name invented: a crossed drug-by-time
design with one combination absent, a nested one, a dose ladder, two parts that always go together
(with an exception), series of replicates, a tie, a series whose names differ in two parts, files
the grouping gives no condition class, and an empty output. A date stands in every name.

The grid's factors, levels, cells, states, crossing and merge are what the names say. A date is
never a factor. The rule that orders replicates pairs only neighbours of a class whose one
counting part varies, and abstains whole on a tie, two varying parts or a number that does not
parse. A dose ladder is never a series. The same run gives the same grid byte for byte.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.design_pool import _write, project_with_the_grid_on, write_pool
from ingence.core.project import Project
from ingence.core.registry import ComponentNotAvailableError
from ingence.design import DESIGN_ENGINES, as_json, design_of_run, design_summary
from ingence.infer.run import run_inference
from ingence.rulebook.predicates.series import REASONS
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.design.grid import ExperimentDesign

CENSUS = "run_20260101T000001Z_111111111111"
FIRST = "run_20260101T000002Z_222222222222"
SECOND = "run_20260101T000003Z_333333333333"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("design")
    pool = write_pool(base / "pool")
    created = project_with_the_grid_on(base / "project", [pool])
    _turn_the_series_rule_on(created)
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=FIRST, started_at="t")
    return created


def _turn_the_series_rule_on(project: Project) -> None:
    """The rule is shipped off; a project turns it on with a change file in its rulebook folder."""
    folder = project.path / "rulebook"
    folder.mkdir(exist_ok=True)
    (folder / "project-0001.yaml").write_text(
        "project_revision: 1\nsummary: Replicate numbers are an order.\n"
        "changes:\n  - enable: succeeds_by_series_index\n"
    )


def _by_folder(project: Project, run_id: str = FIRST) -> dict[str, ExperimentDesign]:
    return {design.folder: design for design in design_of_run(project.path, run_id).experiments}


def _levels(design: ExperimentDesign) -> list[list[str]]:
    return [[level.shown for level in factor.levels] for factor in design.factors]


def test_a_crossed_design_with_one_combination_absent(project: Project) -> None:
    design = _by_folder(project)["20261012_HeLa_drug_time"]
    assert _levels(design) == [["DMSO", "taxol"], ["0h", "2h"]]
    assert design.factors[1].order == "numeric"
    states = {cell.levels: (cell.state, cell.files, cell.replicates) for cell in design.cells}
    assert states == {
        (0, 0): ("occupied", 3, 3),
        (0, 1): ("occupied", 3, 3),
        (1, 0): ("occupied", 3, 3),
        (1, 1): ("not found", 0, 0),
    }
    assert [pair.relation for pair in design.pairs] == ["partly crossed"]
    (finding,) = design.findings
    assert finding.combination == ("taxol", "2h")
    assert "missing" not in finding.question.casefold()
    assert design.coverage.files == design.coverage.in_grid == 9


def test_a_date_is_never_a_factor_and_the_counter_is_the_replicate_axis(project: Project) -> None:
    for design in _by_folder(project).values():
        assert all(factor.kind != "date" for factor in design.factors)
        assert all(1 not in factor.positions for factor in design.factors)
    crossed = _by_folder(project)["20261012_HeLa_drug_time"]
    assert crossed.replicate_positions == (5,)


def test_a_nested_design_is_nested(project: Project) -> None:
    design = _by_folder(project)["20261013_HeLa_nested"]
    assert _levels(design) == [["nocodazole", "taxol"], ["1uM", "5uM", "10uM", "50uM"]]
    (pair,) = design.pairs
    assert (pair.relation, pair.nested_in) == ("nested", design.factors[0].factor_id)
    assert design.not_found_listed is False


def test_a_dose_ladder_labels_the_levels(project: Project) -> None:
    design = _by_folder(project)["20261014_HeLa_dose"]
    (dose,) = design.factors
    assert [level.shown for level in dose.levels] == ["1uM", "10uM", "100uM"]
    assert dose.ladder is not None
    assert (dose.ladder.kind, round(dose.ladder.step)) == ("ratio", 10)


def test_parts_that_go_together_are_merged_with_the_exception_counted(project: Project) -> None:
    design = _by_folder(project)["20261015_HeLa_channels"]
    (factor,) = design.factors
    assert [level.shown for level in factor.levels] == ["GFP + 488", "mCherry + 561"]
    (note,) = factor.merged
    assert (note.observations, note.exceptions) == (14, 1)
    assert note.said == "one exception in 14"
    assert design.merged_parts == 1


def test_names_without_a_condition_class_are_left_out_unless_the_setting_draws_them(
    project: Project,
) -> None:
    # Five movies counted t01 to t05: each is the only file of its condition, so none has a class.
    design = _by_folder(project)["20261018_HeLa_live"]
    assert not design.has_grid
    assert dict(design.coverage.left_out) == {"no_condition_class": 5}
    held = Project.open(project.path).settings.design
    drawn = design_of_run(project.path, FIRST, held.model_copy(update={"include_unclassed": True}))
    live = next(d for d in drawn.experiments if d.folder == "20261018_HeLa_live")
    # t01 to t05 count with one another, so they are the replicate axis and no condition.
    assert not live.has_grid
    assert live.reason_code == "no_factor"


def test_a_clock_in_a_name_is_no_condition(project: Project) -> None:
    design = _by_folder(project)["20261020_HeLa_clock"]
    assert _levels(design) == [["DMSO", "taxol"]]
    assert [factor.positions for factor in design.factors] == [(3,)]
    # The three numbers of the clock and the counter that follows are not conditions.
    assert design.cells[0].replicates == 3


def test_a_condition_with_one_file_fills_its_cell_and_raises_no_question(project: Project) -> None:
    design = _by_folder(project)["20261023_HeLa_single"]
    assert _levels(design) == [["DMSO", "taxol"], ["0h", "2h"]]
    states = {cell.levels: (cell.state, cell.files) for cell in design.cells}
    assert states[(1, 1)] == ("occupied", 1)
    assert design.findings == ()
    assert dict(design.coverage.left_out) == {}


def test_numbers_that_are_conditions_are_factors_not_an_order(project: Project) -> None:
    waves = _by_folder(project)["20261021_HeLa_wavelengths"]
    assert _levels(waves) == [["488", "561", "640"]]
    assert waves.factors[0].order == "numeric"
    doses = _by_folder(project)["20261022_HeLa_unitless"]
    assert {level.shown for level in doses.factors[-1].levels} == {
        "0",
        "1",
        "10",
        "30",
        "60",
        "100",
    }
    pairs = _succeeds(project)
    assert not [pair for pair in pairs if "20261021" in pair[0] or "20261022" in pair[0]]


def test_a_day_beside_a_month_and_labels_that_count_repeats_are_no_conditions(
    project: Project,
) -> None:
    for folder in ("20261024_HeLa_months", "20261025_HeLa_labels", "20261026_HeLa_samples"):
        design = _by_folder(project)[folder]
        assert _levels(design) == [["DMSO", "taxol"]], (folder, _levels(design))
        assert design.cells[0].replicates >= 2


def test_a_counter_after_a_date_word_and_an_underscore_stays_the_replicate_axis(
    project: Project,
) -> None:
    for folder in ("20261027_HeLa_weekday", "20261028_HeLa_monthword"):
        design = _by_folder(project)[folder]
        assert _levels(design) == [["DMSO", "taxol"]], folder
        assert design.replicate_positions, folder
        assert [cell.replicates for cell in design.cells] == [2, 2], folder


def test_a_label_read_as_a_time_point_is_a_factor_in_numeric_order(project: Project) -> None:
    design = _by_folder(project)["20261029_HeLa_timepoints"]
    assert _levels(design) == [["DMSO", "taxol"], ["tp1", "tp2", "tp3"]]
    assert design.factors[1].order == "numeric"
    assert [cell.replicates for cell in design.cells] == [2] * 6


def test_an_empty_output_is_a_state_of_its_cell(project: Project) -> None:
    design = _by_folder(project)["20261019_HeLa_empty"]
    states = {cell.levels: cell.state for cell in design.cells}
    assert states[(1, 0)] == "empty output"
    assert [state for levels, state in states.items() if levels != (1, 0)] == ["occupied"] * 3


def _succeeds(project: Project) -> list[tuple[str, str]]:
    """Every proposed succession, as (earlier name, later name)."""
    with open_catalog(project.path, run_id=FIRST) as catalog:
        rows = catalog.query(
            "SELECT s.name, t.name FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.primitive = 'succeeds' ORDER BY 1, 2"
        ).fetchall()
    return [(str(first), str(second)) for first, second in rows]


def test_replicates_are_ordered_by_neighbours_only(project: Project) -> None:
    pairs = _succeeds(project)
    series = [pair for pair in pairs if "20261016" in pair[0]]
    assert series == [
        (f"20261016_HeLa_DMSO_{n:03d}.tif", f"20261016_HeLa_DMSO_{n + 1:03d}.tif")
        for n in range(1, 5)
    ]
    crossed = [pair for pair in pairs if "20261012" in pair[0]]
    # Three classes of three replicates: two neighbouring pairs each, never first to third.
    assert len(crossed) == 6
    assert all(int(second[-7:-4]) == int(first[-7:-4]) + 1 for first, second in crossed)
    assert all(first.rsplit("_", 1)[0] == second.rsplit("_", 1)[0] for first, second in crossed)


def test_a_dose_ladder_is_never_a_series(project: Project) -> None:
    doses = [pair for pair in _succeeds(project) if "20261014" in pair[0]]
    # Replicates of one dose follow each other; no dose follows another.
    assert len(doses) == 3
    for first, second in doses:
        assert first.split("-")[1].split("_")[0] == second.split("-")[1].split("_")[0]


def test_a_class_abstains_whole_on_a_tie_or_two_varying_parts(project: Project) -> None:
    pairs = _succeeds(project)
    assert not [pair for pair in pairs if "20261017" in pair[0]]  # 1 and 001 tie
    # The class of GFP movies holds 488 and the one off the pairing, 561: two parts vary. The
    # mCherry movies all say 561, so only their counter varies and they are ordered.
    assert not [pair for pair in pairs if "20261015" in pair[0] and "GFP" in pair[0]]
    assert len([pair for pair in pairs if "20261015" in pair[0] and "mCherry" in pair[0]]) == 5
    assert not [pair for pair in pairs if "20261018" in pair[0]]  # each is a class of one


def test_the_summary_counts_with_denominators(project: Project) -> None:
    summary = design_summary(project.path, FIRST)
    assert summary.experiments == 19
    assert summary.with_grid == 15
    assert dict(summary.factors_per_grid) == {1: 9, 2: 6}
    assert summary.pairs == 6
    assert summary.crossed_pairs == 3
    assert summary.crossed_share == pytest.approx(3 / 6)
    assert summary.merged_parts == 1
    assert summary.grids_with_merge == 1
    assert summary.findings == 1
    # The five t01 to t05 movies and the lone file beside the empty ones have no class (and so
    # nothing to draw from); the series and the tie are classes whose names vary in no condition.
    assert dict(summary.abstentions) == {"no_condition_class": 2, "no_factor": 2}
    assert summary.files_in_grid <= summary.files
    assert summary.as_dict()["median_fill"] == summary.median_fill


def test_the_summary_counts_the_classes_the_series_rule_did_not_order_by_reason(
    project: Project,
) -> None:
    summary = design_summary(project.path, FIRST)
    refused = dict(summary.series_not_ordered)
    assert summary.series_classes == summary.series_ordered + sum(refused.values())
    assert refused["not_counting"] == 3  # 488 561 640; 1 10 100; 0 30 60
    assert refused["tie"] == 1  # 1 and 001
    assert refused["many_parts"] == 5  # the GFP movies, and the days beside a month
    assert summary.series_ordered == 45
    assert set(refused) <= set(REASONS)
    assert summary.as_dict()["series_not_ordered"]


def test_two_inferences_of_one_project_give_the_same_design_byte_for_byte(
    project: Project,
) -> None:
    run_inference(project, run_id=SECOND, started_at="t")
    first = design_of_run(project.path, FIRST)
    second = design_of_run(project.path, SECOND)
    assert first.run_id != second.run_id
    assert as_json(first) == as_json(second)
    assert as_json(first) == as_json(design_of_run(project.path, FIRST))


def test_one_experiment_alone_is_the_same_grid(project: Project) -> None:
    whole = design_of_run(project.path, FIRST)
    chosen = next(d for d in whole.experiments if d.folder == "20261012_HeLa_drug_time")
    alone = design_of_run(project.path, FIRST, experiment=chosen.experiment_id)
    assert alone.experiments == (chosen,)


def test_the_setting_switches_the_grid_off(project: Project) -> None:
    held = Project.open(project.path).settings.design
    off = held.model_copy(update={"enabled": False})
    run = design_of_run(project.path, FIRST, off)
    assert run.enabled is False
    assert run.experiments == ()
    merging_off = held.model_copy(update={"merge_parts": False})
    unmerged = {d.folder: d for d in design_of_run(project.path, FIRST, merging_off).experiments}[
        "20261015_HeLa_channels"
    ]
    assert len(unmerged.factors) == 2


def test_the_component_is_reached_only_through_the_registry(project: Project) -> None:
    DESIGN_ENGINES.set_enabled("name_grid", enabled=False)
    try:
        with pytest.raises(ComponentNotAvailableError):
            design_of_run(project.path, FIRST)
    finally:
        DESIGN_ENGINES.set_enabled("name_grid", enabled=True)


# ---------------------------------------------------------------------------
# The routes.
# ---------------------------------------------------------------------------


def _get(project: Project, path: str, **query: str) -> tuple[int, dict[str, object]]:
    from ingence.api import v1

    context = v1.context_for(project.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, path, {key: [value] for key, value in query.items()})
    return status, payload


def test_the_overview_route_answers_its_model_with_the_summary(project: Project) -> None:
    from ingence.api import v1
    from ingence.api.v1.models import RESPONSE_MODELS

    status, payload = _get(project, "design")
    assert status == 200, payload
    v1.DECLARED["design"].model_validate(payload)
    assert v1.DECLARED["design"] in set(RESPONSE_MODELS.values())
    assert payload["enabled"] is True
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert summary["experiments"] == 19
    assert summary["with_grid"] == 15
    rows = {row["folder"]: row for row in payload["experiments"]}  # type: ignore[attr-defined]
    assert rows["20261012_HeLa_drug_time"]["factors"] == 2
    assert rows["20261012_HeLa_drug_time"]["cells"] == 3
    assert rows["20261016_HeLa_series"]["has_grid"] is False


def test_one_experiment_s_route_answers_the_grid_whole(project: Project) -> None:
    from ingence.api import v1

    overview = _get(project, "design")[1]
    chosen = next(
        row
        for row in overview["experiments"]  # type: ignore[attr-defined]
        if row["folder"] == "20261012_HeLa_drug_time"
    )
    status, payload = _get(project, "experiments/design", experiment=chosen["id"])
    assert status == 200, payload
    v1.DECLARED["experiments/design"].model_validate(payload)
    experiment = payload["experiment"]
    assert isinstance(experiment, dict)
    assert [factor["name"] for factor in experiment["factors"]] == ["factor", "time"]
    assert [cell["state"] for cell in experiment["cells"]].count("not found") == 1
    assert experiment["findings"][0]["question"].startswith("Combination not found")
    assert experiment["coverage"] == {"files": 9, "in_grid": 9, "left_out": []}
    assert experiment["other_layouts"] == []
    assert "missing" not in str(payload).casefold()


def test_the_routes_refuse_in_the_one_shape(project: Project, tmp_path: Path) -> None:
    from ingence.api import v1

    assert _get(project, "experiments/design")[0] == 400
    status, payload = _get(project, "experiments/design", experiment="entry_nothing")
    assert status == 404
    assert payload["error"]["code"] == "not_found"  # type: ignore[index]
    bare = Project.create(tmp_path / "bare", created_at="t", roots=[tmp_path])
    context = v1.context_for(bare.path, settled=None, job_running=False)
    status, payload = v1.dispatch(context, "design", {})
    assert status == 404
    assert payload["error"]["code"] == "no_published_run"  # type: ignore[index]


def test_shipped_off_the_series_rule_proposes_nothing(tmp_path: Path) -> None:
    pool = tmp_path / "pool" / "20261012_HeLa_series"
    for number in range(1, 5):
        _write(pool / f"20261012_HeLa_DMSO_{number:03d}.tif")
    created = project_with_the_grid_on(tmp_path / "project", [tmp_path / "pool"])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=FIRST, started_at="t")
    with open_catalog(created.path, run_id=FIRST) as catalog:
        count = catalog.query("SELECT count(*) FROM candidates WHERE primitive = 'succeeds'")
        assert count.fetchone() == (0,)
    # What the rule would do is still counted: one class of four, put in order.
    summary = design_summary(created.path, FIRST)
    assert (summary.series_classes, summary.series_ordered) == (1, 1)


def test_the_grid_is_off_by_default_and_a_project_turns_it_on_in_its_settings(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool" / "20261012_HeLa_series"
    for number in range(1, 3):
        _write(pool / f"20261012_HeLa_DMSO_{number:03d}.tif")
    created = Project.create(tmp_path / "project", created_at="t", roots=[tmp_path / "pool"])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=FIRST, started_at="t")
    assert design_of_run(created.path, FIRST).enabled is False
    _, payload = _get(created, "design")
    assert payload["enabled"] is False and payload["experiments"] == []
    on = created.settings
    created.save_settings(
        on.model_copy(update={"design": on.design.model_copy(update={"enabled": True})})
    )
    assert design_of_run(created.path, FIRST).enabled is True
