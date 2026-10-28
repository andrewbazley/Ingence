"""The same-columns, design-grid and themes summaries, registered with the quality figures.

Three summaries are registered with the figures every run carries. Each has its own catalogue
file. The tests hold, for each summary, that

1. its figures are in the run's ``quality_figures``, in the manifest's comparison and in
   ``ingence export quality``;
2. each figure equals the value the summary function gives for the published run;
3. a figure the run's pass does not reach is not run, and one that cannot be counted (a component
   switched off, a number below what a figure can hold) is not recorded with the reason, never
   zero;
4. the closed breakdowns in the catalogues are the reasons the components define.

Every name is invented (the pools are those of the three summaries' own tests).
"""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from fixtures.design_pool import project_with_the_grid_on
from fixtures.design_pool import write_pool as write_design_pool
from fixtures.same_columns_pool import DIVE as COLUMNS_DIVE
from fixtures.same_columns_pool import INFERENCE as COLUMNS_INFERENCE
from fixtures.same_columns_pool import build_project as build_columns_project
from fixtures.theme_pool import THEME_INFER_RUN, build_theme_project
from ingence.api.routes.manifest import manifest
from ingence.cli.main import app
from ingence.columns import COLUMN_INDEXES, same_column_summary
from ingence.core.project import Project
from ingence.core.schema import QualityState
from ingence.design import DESIGN_ENGINES, design_summary
from ingence.design.grid import REASONS as GRID_REASONS
from ingence.design.quality import MEDIAN_FILL_WHOLE
from ingence.infer.run import run_inference
from ingence.quality.catalogue import catalogue, load_catalogue
from ingence.quality.compare import figures_of
from ingence.quality.publish import recompute
from ingence.quality.registry import SUMMARIES, catalogue_paths
from ingence.rulebook.predicates.series import REASONS as SERIES_REASONS
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.themes import THEME_ENGINES
from ingence.themes.projection import themes_projection
from ingence.themes.quality import POINTS, REASON_CODES, theme_quality

if TYPE_CHECKING:
    from pathlib import Path

DESIGN_CENSUS = "run_20260101T000001Z_111111111111"
DESIGN_FIRST = "run_20260101T000002Z_222222222222"
DESIGN_SECOND = "run_20260101T000003Z_333333333333"
THEMES_SECOND = "run_20260101T000009Z_cccccccccccc"
SEED_NUMBERS = frozenset({"themes.shown_seed", "themes.medoid_seed"})


# ---------------------------------------------------------------------------
# The projects: one for each summary, each built once.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def columns(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """Census, dive, a later census and an inference over tables, one header in several places."""
    project = build_columns_project(tmp_path_factory.mktemp("registered_columns"))
    # A second inference, so the manifest has a run of its own kind to set this one beside.
    run_inference(project, run_id="run_20260101T000005Z_555555555555", started_at="t")
    return project


@pytest.fixture(scope="module")
def design(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("registered_design")
    pool = write_design_pool(base / "pool")
    project = project_with_the_grid_on(base / "project", [pool])
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    run_inference(project, run_id=DESIGN_FIRST, started_at="t")
    run_inference(project, run_id=DESIGN_SECOND, started_at="t")
    return project


@pytest.fixture(scope="module")
def themes(tmp_path_factory: pytest.TempPathFactory) -> Project:
    pytest.importorskip("sklearn")
    path, _plant = build_theme_project(tmp_path_factory.mktemp("registered_themes"))
    project = Project.open(path)
    run_inference(project, run_id=THEMES_SECOND, started_at="t")
    return project


def _rows(project: Project, run_id: str, prefix: str) -> dict[tuple[str, str], Any]:
    return {
        (row.figure, row.facet): row
        for row in figures_of(project.path, run_id)
        if row.figure.startswith(f"{prefix}.")
    }


def _counted(
    rows: dict[tuple[str, str], Any], figure: str, facet: str = ""
) -> tuple[int, int | None]:
    row = rows[(figure, facet)]
    assert row.state is QualityState.COUNTED, (figure, facet, row.note)
    return row.count, row.total


# ---------------------------------------------------------------------------
# Same columns.
# ---------------------------------------------------------------------------


def test_the_same_columns_figures_equal_the_summary_functions_values(columns: Project) -> None:
    summary = same_column_summary(columns.path, COLUMNS_INFERENCE)
    rows = _rows(columns, COLUMNS_INFERENCE, "columns")
    assert _counted(rows, "columns.tables_counted") == (
        summary.tables_counted,
        summary.tables_in_run,
    )
    layouts = _counted(rows, "columns.layouts")
    assert layouts[1] == summary.tables_counted
    assert _counted(rows, "columns.classes") == (summary.classes, layouts[0])
    assert _counted(rows, "columns.tables_in_classes") == (
        summary.tables_in_classes,
        summary.tables_counted,
    )
    assert _counted(rows, "columns.classes_spanning_folders") == (
        summary.classes_spanning_folders,
        summary.classes,
    )
    # The pool's numbers, so the figures are not equal to themselves only: the tables of one header
    # in three places, the late table no dive read, and the file counted among the run's tables.
    assert summary.classes == 1
    assert summary.tables_counted < summary.tables_in_run


def test_a_pass_that_reads_no_columns_does_not_reach_the_figures(columns: Project) -> None:
    census = _rows(columns, "run_20260101T000001Z_111111111111", "columns")
    assert census
    assert {row.state for row in census.values()} == {QualityState.NOT_RUN}
    assert all(row.count is None and row.total is None for row in census.values())
    dive = _rows(columns, COLUMNS_DIVE, "columns")
    assert {row.state for row in dive.values()} == {QualityState.COUNTED}


def test_the_same_columns_figures_are_the_stored_digests_not_the_rules_claims(
    columns: Project,
) -> None:
    """Switching the grouping rule off leaves the figures as they were (a dive's digests)."""
    folder = columns.path / "rulebook"
    folder.mkdir(exist_ok=True)
    (folder / "project-0001.yaml").write_text(
        "project_revision: 1\nsummary: The columns rule is off.\n"
        "changes:\n  - disable: same_column_schema\n"
    )
    try:
        run = "run_20260101T000007Z_777777777777"
        run_inference(columns, run_id=run, started_at="t")
        # The rule groups tables in the run before and no longer does in this one.
        grouped = "SELECT count(*) FROM claims WHERE rule_id = 'same_column_schema'"
        with open_catalog(columns.path, run_id=COLUMNS_INFERENCE) as catalog:
            assert (catalog.query(grouped).fetchone() or (0,))[0] > 0
        with open_catalog(columns.path, run_id=run) as catalog:
            assert (catalog.query(grouped).fetchone() or (None,))[0] == 0
        assert {
            key: (row.count, row.total) for key, row in _rows(columns, run, "columns").items()
        } == {
            key: (row.count, row.total)
            for key, row in _rows(columns, COLUMNS_INFERENCE, "columns").items()
        }
    finally:
        (folder / "project-0001.yaml").unlink()


def test_the_same_columns_figures_are_in_the_manifest_beside_the_run_before(
    columns: Project,
) -> None:
    run = "run_20260101T000005Z_555555555555"
    found = manifest(columns.path, run, viewer="a person")
    assert found["compared_run_id"] == COLUMNS_INFERENCE
    held = {
        (row["figure"], row["facet"]): row
        for row in found["quality"]["rows"]
        if row["figure"].startswith("columns.")
    }
    stored = _rows(columns, run, "columns")
    before = _rows(columns, COLUMNS_INFERENCE, "columns")
    assert set(held) == set(stored)
    for key, row in held.items():
        assert (row["count"], row["total"]) == (stored[key].count, stored[key].total)
        assert (row["was_count"], row["was_total"]) == (before[key].count, before[key].total)
        assert row["changed"] is False
        assert row["method"] == catalogue().figures[key[0]].method


# ---------------------------------------------------------------------------
# The design grid.
# ---------------------------------------------------------------------------


def test_the_design_figures_equal_the_summary_functions_values(design: Project) -> None:
    summary = design_summary(design.path, DESIGN_FIRST).as_dict()
    rows = _rows(design, DESIGN_FIRST, "design")
    assert _counted(rows, "design.with_grid") == (summary["with_grid"], summary["experiments"])
    assert ("design.factors_per_grid", "") not in rows
    for factors, grids in summary["factors_per_grid"]:
        assert _counted(rows, "design.factors_per_grid", str(factors)) == (
            grids,
            summary["with_grid"],
        )
    assert _counted(rows, "design.grids_with_fill") == (summary["fill_grids"], summary["with_grid"])
    assert _counted(rows, "design.median_fill") == (
        round(summary["median_fill"] * MEDIAN_FILL_WHOLE),
        MEDIAN_FILL_WHOLE,
    )
    assert _counted(rows, "design.crossed_pairs") == (summary["crossed_pairs"], summary["pairs"])
    assert _counted(rows, "design.files_in_grid") == (summary["files_in_grid"], summary["files"])
    left = dict(map(tuple, summary["left_out"]))
    for reason in catalogue().figures["design.files_left_out"].facets:
        assert _counted(rows, "design.files_left_out", reason) == (
            left.get(reason, 0),
            summary["files"],
        )
    absent = dict(map(tuple, summary["abstentions"]))
    for reason in catalogue().figures["design.no_grid"].facets:
        assert _counted(rows, "design.no_grid", reason) == (
            absent.get(reason, 0),
            summary["experiments"],
        )
    assert _counted(rows, "design.grids_with_merge") == (
        summary["grids_with_merge"],
        summary["with_grid"],
    )
    assert _counted(rows, "design.series_ordered") == (
        summary["series_ordered"],
        summary["series_classes"],
    )
    refused = dict(map(tuple, summary["series_not_ordered"]))
    for reason in catalogue().figures["design.series_not_ordered"].facets:
        assert _counted(rows, "design.series_not_ordered", reason) == (
            refused.get(reason, 0),
            summary["series_classes"],
        )
    # The pool's own numbers (test_design.py states the same): nineteen experiments, fifteen with
    # a grid, three of six pairs crossed. The breakdowns add up to what they divide.
    assert (summary["experiments"], summary["with_grid"]) == (19, 15)
    assert (summary["crossed_pairs"], summary["pairs"]) == (3, 6)
    assert sum(left.values()) == summary["files"] - summary["files_in_grid"]
    assert sum(absent.values()) == summary["experiments"] - summary["with_grid"]
    assert sum(refused.values()) + summary["series_ordered"] == summary["series_classes"]


def test_a_median_fill_is_a_count_of_one_thousand_and_a_grid_of_no_fill_states_none(
    design: Project,
) -> None:
    summary = design_summary(design.path, DESIGN_FIRST)
    assert summary.median_fill is not None
    row = _rows(design, DESIGN_FIRST, "design")[("design.median_fill", "")]
    assert (row.count, row.total) == (round(summary.median_fill * 1000), 1000)
    assert row.count / row.total == pytest.approx(summary.median_fill, abs=0.0005)
    # Nothing to take a median of is not a median of nothing counted as zero.
    census = _rows(design, DESIGN_CENSUS, "design")
    assert {row.state for row in census.values()} == {QualityState.NOT_RUN}


def test_the_design_figures_are_in_the_manifest_and_the_export(
    design: Project, tmp_path: Path
) -> None:
    found = manifest(design.path, DESIGN_SECOND, viewer="a person")
    assert found["compared_run_id"] == DESIGN_FIRST
    held = {
        (row["figure"], row["facet"]): row
        for row in found["quality"]["rows"]
        if row["figure"].startswith("design.")
    }
    stored = _rows(design, DESIGN_SECOND, "design")
    before = _rows(design, DESIGN_FIRST, "design")
    assert set(held) == set(stored)
    for key, row in held.items():
        assert (row["count"], row["total"]) == (stored[key].count, stored[key].total)
        assert (row["was_count"], row["was_total"]) == (before[key].count, before[key].total)
        assert row["changed"] is False
    assert _exported(design, DESIGN_SECOND, tmp_path, "design") == {
        key: (row.state.value, row.count, row.total) for key, row in stored.items()
    }


def test_a_pool_with_no_grid_states_the_median_not_recorded_with_the_reason(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    (pool / "notes").mkdir(parents=True)
    (pool / "notes" / "protocol.txt").write_text("block for one hour\n")
    project = project_with_the_grid_on(tmp_path / "project", [pool])
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    run_inference(project, run_id=DESIGN_FIRST, started_at="t")
    rows = _rows(project, DESIGN_FIRST, "design")
    median = rows[("design.median_fill", "")]
    assert median.state is QualityState.NOT_RECORDED
    assert median.count is None and "no grid has a fill" in median.note
    # Experiments counted as none are zero out of none, which is a count and not a missing figure.
    assert _counted(rows, "design.with_grid") == (0, summary_experiments(project))
    # No grid, so no number of factors to break down: said, and not a count of nothing.
    assert rows[("design.factors_per_grid", "")].state is QualityState.NOT_RECORDED


def summary_experiments(project: Project) -> int:
    return int(design_summary(project.path, DESIGN_FIRST).experiments)


def test_the_design_grid_is_off_by_default_and_its_figures_say_so(tmp_path: Path) -> None:
    pool = write_design_pool(tmp_path / "pool")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert project.settings.design.enabled is False
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    run_inference(project, run_id=DESIGN_FIRST, started_at="t")
    rows = _rows(project, DESIGN_FIRST, "design")
    assert {row.state for row in rows.values()} == {QualityState.NOT_RECORDED}
    assert all(row.count is None and "the design grid is off" in row.note for row in rows.values())


def test_the_design_figures_say_so_when_the_grid_or_its_component_is_switched_off(
    tmp_path: Path,
) -> None:
    pool = write_design_pool(tmp_path / "pool")
    project = project_with_the_grid_on(tmp_path / "project", [pool])
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    settings = project.settings
    project.save_settings(
        settings.model_copy(
            update={"design": settings.design.model_copy(update={"enabled": False})}
        )
    )
    run_inference(project, run_id=DESIGN_FIRST, started_at="t")
    DESIGN_ENGINES.set_enabled("name_grid", enabled=False)
    try:
        run_inference(project, run_id=DESIGN_SECOND, started_at="t")
    finally:
        DESIGN_ENGINES.set_enabled("name_grid", enabled=True)
    for run in (DESIGN_FIRST, DESIGN_SECOND):
        rows = _rows(project, run, "design")
        assert {figure for figure, _ in rows} == {
            name for name in catalogue().figures if name.startswith("design.")
        }
        assert {row.state for row in rows.values()} == {QualityState.NOT_RECORDED}
        assert all(
            row.count is None and ("switched off" in row.note or "grid is off" in row.note)
            for row in rows.values()
        )
        # The run published, and the other summaries' figures are there.
        assert any(
            row.state is QualityState.COUNTED
            for row in figures_of(project.path, run)
            if row.figure.startswith("found.")
        )


# ---------------------------------------------------------------------------
# Themes.
# ---------------------------------------------------------------------------


def test_the_themes_figures_equal_the_quality_of_the_published_projection(themes: Project) -> None:
    quality = theme_quality(themes_projection(themes.path, THEME_INFER_RUN))
    rows = _rows(themes, THEME_INFER_RUN, "themes")
    placement = quality["placement"]
    assert _counted(rows, "themes.placed") == (placement["placed"], placement["experiments"])
    reasons = placement["unplaced_reasons"]
    coded = {REASON_CODES.get(reason, reason): number for reason, number in reasons.items()}
    for facet in catalogue().figures["themes.unplaced"].facets:
        assert _counted(rows, "themes.unplaced", facet) == (
            coded.get(facet, 0),
            placement["experiments"],
        )
    assert sum(coded.values()) == placement["unplaced"]
    pairs = quality["seed_agreement"]["pairs"]
    assert pairs["count"] > 0
    assert _counted(rows, "themes.seed_pairs_identical") == (
        quality["seed_agreement"]["identical_pairs"],
        pairs["count"],
    )
    assert _counted(rows, "themes.seed_agreement_least") == (
        round(pairs["minimum"] * POINTS),
        POINTS,
    )
    assert _counted(rows, "themes.seed_agreement_median") == (
        round(pairs["median"] * POINTS),
        POINTS,
    )
    # A seed's number names a seed: a value with no whole, so History draws no bar for it.
    assert _counted(rows, "themes.shown_seed") == (quality["shown_seed"], None)
    assert _counted(rows, "themes.medoid_seed") == (quality["medoid_seed"], None)
    connected = quality["connectivity"]
    assert _counted(rows, "themes.disconnected") == (
        connected["disconnected_themes"],
        connected["themes"],
    )
    assert _counted(rows, "themes.broad_disconnected") == (
        connected["disconnected_broad_themes"],
        connected["broad_themes"],
    )
    chance = quality["chance"]
    for facet in ("between", "within", "placement"):
        cutoff = chance[f"cutoff_{facet}"]
        if cutoff is None:
            row = rows[("themes.cutoffs", facet)]
            assert row.state is QualityState.NOT_RECORDED
            # Shuffled documents were measured. The cutoff within a theme is unset by default.
            assert chance["measured"] is True
            assert row.note == "the settings set no cutoff of this kind"
        else:
            assert _counted(rows, "themes.cutoffs", facet) == (round(cutoff * POINTS), POINTS)


def _again_equals_kept(project: Project, run: str, phase: str, prefix: str) -> None:
    again = [
        row for row in recompute(project.path, run, phase=phase) if row.figure.startswith(prefix)
    ]
    kept = [row for row in figures_of(project.path, run) if row.figure.startswith(prefix)]
    assert again
    assert [row.model_dump() for row in again] == [row.model_dump() for row in kept]


def test_the_stored_columns_and_design_figures_are_the_ones_the_tables_give_again(
    columns: Project, design: Project
) -> None:
    _again_equals_kept(columns, COLUMNS_INFERENCE, "infer", "columns")
    _again_equals_kept(columns, COLUMNS_DIVE, "dive", "columns")
    _again_equals_kept(design, DESIGN_FIRST, "infer", "design")


def test_the_stored_themes_figures_are_the_ones_the_tables_give_again(themes: Project) -> None:
    _again_equals_kept(themes, THEME_INFER_RUN, "infer", "themes")


def test_two_inferences_give_the_same_themes_figures(themes: Project) -> None:
    first = _rows(themes, THEME_INFER_RUN, "themes")
    second = _rows(themes, THEMES_SECOND, "themes")
    assert {key: (row.state, row.count, row.total) for key, row in first.items()} == {
        key: (row.state, row.count, row.total) for key, row in second.items()
    }


def test_the_themes_figures_are_in_the_manifest_and_the_export(
    themes: Project, tmp_path: Path
) -> None:
    found = manifest(themes.path, THEMES_SECOND, viewer="a person")
    assert found["compared_run_id"] == THEME_INFER_RUN
    held = {
        (row["figure"], row["facet"]): row
        for row in found["quality"]["rows"]
        if row["figure"].startswith("themes.")
    }
    stored = _rows(themes, THEMES_SECOND, "themes")
    before = _rows(themes, THEME_INFER_RUN, "themes")
    assert set(held) == set(stored)
    for key, row in held.items():
        assert (row["count"], row["total"]) == (stored[key].count, stored[key].total)
        assert (row["was_count"], row["was_total"]) == (before[key].count, before[key].total)
    assert _exported(themes, THEMES_SECOND, tmp_path, "themes") == {
        key: (row.state.value, row.count, row.total) for key, row in stored.items()
    }


def test_the_themes_figures_say_so_when_the_component_is_switched_off(tmp_path: Path) -> None:
    path, _plant = build_theme_project(tmp_path / "off")
    project = Project.open(path)
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        run_inference(project, run_id=THEMES_SECOND, started_at="t")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    rows = _rows(project, THEMES_SECOND, "themes")
    assert rows
    assert {row.state for row in rows.values()} == {QualityState.NOT_RECORDED}
    assert all(row.count is None and "switched off" in row.note for row in rows.values())
    assert any(
        row.state is QualityState.COUNTED and row.figure.startswith("found.")
        for row in figures_of(project.path, THEMES_SECOND)
    )


def test_the_same_columns_figures_say_so_when_the_component_is_switched_off(
    tmp_path: Path,
) -> None:
    project = build_columns_project(tmp_path, late=False)
    COLUMN_INDEXES.set_enabled("stored_columns", enabled=False)
    try:
        run_inference(project, run_id="run_20260101T000008Z_888888888888", started_at="t")
    finally:
        COLUMN_INDEXES.set_enabled("stored_columns", enabled=True)
    rows = _rows(project, "run_20260101T000008Z_888888888888", "columns")
    assert {row.state for row in rows.values()} == {QualityState.NOT_RECORDED}
    assert all("switched off" in row.note for row in rows.values())


# ---------------------------------------------------------------------------
# The catalogues.
# ---------------------------------------------------------------------------


def test_each_summary_is_registered_with_a_catalogue_in_the_package_that_wrote_it() -> None:
    paths = catalogue_paths()
    for name in ("columns", "design", "themes"):
        assert name in SUMMARIES
        assert paths[name].name == "quality.yaml"
        assert paths[name].parent.name == name
        figures = load_catalogue(paths[name]).figures
        assert figures
        assert all(figure.startswith(f"{name}.") for figure in figures)
        for definition in figures.values():
            assert definition.method.endswith(".") and "\n" not in definition.method
            # A denominator for every count (a share is a count and its whole). The two seed
            # numbers name a seed and count nothing, and say so in their unit.
            if definition.figure in SEED_NUMBERS:
                assert "not a count" in definition.unit and definition.total is None
            else:
                assert definition.total, definition.figure
            # A figure is as the run stood when it was written.
            assert "when the run was written" in definition.method, definition.figure
    assert list(catalogue().sections)[:5] == ["found", "read", "names", "conclusions", "cost"]
    assert {"columns", "design", "themes"} <= set(catalogue().sections)


def _all_stated(project: Project, run: str, prefix: str) -> None:
    stated = {figure for figure, _ in _rows(project, run, prefix)}
    assert stated == {name for name in catalogue().figures if name.startswith(f"{prefix}.")}


def test_every_columns_and_design_figure_has_a_row_in_a_run_that_reaches_it(
    columns: Project, design: Project
) -> None:
    _all_stated(columns, COLUMNS_INFERENCE, "columns")
    _all_stated(design, DESIGN_FIRST, "design")


def test_every_themes_figure_has_a_row_in_a_run_that_reaches_it(themes: Project) -> None:
    _all_stated(themes, THEME_INFER_RUN, "themes")


def test_the_closed_breakdowns_are_the_reasons_the_components_define() -> None:
    figures = catalogue().figures
    assert set(figures["design.no_grid"].facets) == set(GRID_REASONS)
    assert set(figures["design.files_left_out"].facets) == set(GRID_REASONS) - {"no_files"}
    assert set(figures["design.series_not_ordered"].facets) == set(SERIES_REASONS)
    assert set(figures["themes.unplaced"].facets) == set(REASON_CODES.values())
    from ingence.themes import core

    assert set(REASON_CODES) == {
        core.NO_WORDS,
        core.TOO_SMALL,
        core.NOT_CLOSE,
        core.NOT_CLOSE_TO_GROUP,
        core.REPEATED_TEXT,
    }


def test_no_catalogue_sentence_holds_a_decision_number_a_session_or_a_product_name() -> None:
    import re

    # The names are built from parts: nothing that ships spells them out.
    names = ["Cl" + "aude", "assis" + "tant", r"\b" + "A" + r"I\b"]
    pattern = re.compile(r"\bD\d{3}\b|session|" + "|".join(names), re.IGNORECASE)
    for name in ("columns", "design", "themes"):
        figures = load_catalogue(catalogue_paths()[name]).figures
        for definition in figures.values():
            for text in (
                definition.label,
                definition.unit,
                definition.total or "",
                definition.method,
            ):
                assert not pattern.search(text), (definition.figure, text)
            for words in definition.facets.values():
                assert not pattern.search(words), (definition.figure, words)


# ---------------------------------------------------------------------------
# Robustness: a missing extra, a stale catalogue cache, the cutoffs' reasons, the cost.
# ---------------------------------------------------------------------------


def test_a_base_install_without_the_themes_extra_says_so_in_every_themes_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extra is faked missing: the summary stays registered and gives the reason."""
    import importlib.util

    real = importlib.util.find_spec

    def find_spec(name: str, *arguments: Any, **keywords: Any) -> Any:
        return None if name in {"sklearn", "scipy"} else real(name, *arguments, **keywords)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    # The engine's cached instance and any failure recorded for it are the real ones' to keep.
    monkeypatch.setattr(THEME_ENGINES, "_instances", {})
    monkeypatch.setattr(THEME_ENGINES, "_failures", {})
    assert not THEME_ENGINES.available("lsa_louvain")
    assert "themes" in SUMMARIES.names(enabled_only=True)
    assert SUMMARIES.available("themes")
    pool = write_design_pool(tmp_path / "pool")
    project = project_with_the_grid_on(tmp_path / "project", [pool])
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    run_inference(project, run_id=DESIGN_FIRST, started_at="t")
    rows = _rows(project, DESIGN_FIRST, "themes")
    assert {figure for figure, _ in rows} == {
        name for name in catalogue().figures if name.startswith("themes.")
    }
    assert {row.state for row in rows.values()} == {QualityState.NOT_RECORDED}
    assert all("extra is not installed" in row.note for row in rows.values())
    # The other summaries are not touched.
    assert _rows(project, DESIGN_FIRST, "design")[("design.with_grid", "")].count == 15


def test_a_summary_switched_on_or_off_after_the_catalogue_was_read_never_fails_a_publish(
    tmp_path: Path,
) -> None:
    pool = write_design_pool(tmp_path / "pool")
    project = project_with_the_grid_on(tmp_path / "project", [pool])
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    catalogue.cache_clear()
    catalogue()
    SUMMARIES.set_enabled("design", enabled=False)
    try:
        run_inference(project, run_id=DESIGN_FIRST, started_at="t")
        catalogue.cache_clear()
        catalogue()
    finally:
        SUMMARIES.set_enabled("design", enabled=True)
    try:
        # The cache was read without the design summary and the summary is enabled again.
        run_inference(project, run_id=DESIGN_SECOND, started_at="t")
    finally:
        catalogue.cache_clear()
    for run in (DESIGN_FIRST, DESIGN_SECOND):
        assert any(
            row.state is QualityState.COUNTED and row.figure.startswith("found.")
            for row in figures_of(project.path, run)
        )


def test_the_cutoffs_have_three_distinct_reasons_for_a_missing_value(columns: Project) -> None:
    from ingence.quality.context import QualityContext
    from ingence.themes.quality import _cutoff

    measured = {
        "measured": True,
        "quantile_within": None,
        "cutoff_within": None,
        "quantile_between": 0.99,
        "cutoff_between": -0.2,
        "quantile_placement": 0.99,
        "cutoff_placement": 0.5,
    }
    with open_catalog(columns.path, run_id=COLUMNS_INFERENCE) as catalog:
        context = QualityContext(catalog, COLUMNS_INFERENCE, "infer")
        nothing = _cutoff(context, {**measured, "measured": False}, "placement")
        unset = _cutoff(context, measured, "within")
        negative = _cutoff(context, measured, "between")
        fine = _cutoff(context, measured, "placement")
        absent = _cutoff(context, None, "placement")
    assert nothing.note == absent.note == "no shuffled documents were measured"
    assert unset.note == "the settings set no cutoff of this kind"
    assert negative.note == "the cutoff is below 0, which this figure cannot hold"
    assert len({nothing.note, unset.note, negative.note}) == 3
    assert all(row.state is QualityState.NOT_RECORDED for row in (nothing, unset, negative, absent))
    assert (fine.state, fine.count, fine.total) == (QualityState.COUNTED, 500, POINTS)


def test_each_run_states_what_its_summaries_cost_and_reads_memory_after_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingence.quality.publish as publish

    order: list[str] = []
    real_collect = publish.collect

    def collected(*arguments: Any, **keywords: Any) -> Any:
        found = real_collect(*arguments, **keywords)
        order.append("summaries")
        return found

    def peak() -> int:
        order.append("memory")
        return 123_456_789

    monkeypatch.setattr(publish, "collect", collected)
    monkeypatch.setattr(publish, "_resident_peak", peak)
    pool = write_design_pool(tmp_path / "pool")
    project = project_with_the_grid_on(tmp_path / "project", [pool])
    run_census(project, run_id=DESIGN_CENSUS, started_at="t")
    assert order == ["summaries", "memory"]
    rows = {(row.figure, row.facet): row for row in figures_of(project.path, DESIGN_CENSUS)}
    assert rows[("cost.peak_memory", "")].peak_bytes == 123_456_789
    assert rows[("cost.peak_memory", "")].state is QualityState.COUNTED
    timed = {facet: row for (figure, facet), row in rows.items() if figure == "cost.quality_time"}
    assert set(timed) == set(SUMMARIES.names(enabled_only=True))
    assert all(
        row.state is QualityState.COUNTED and (row.duration_ns or 0) > 0 for row in timed.values()
    )
    # Figures stated again from the tables are not timed, and say so.
    again = list(recompute(project.path, DESIGN_CENSUS, phase="census"))
    (untimed,) = [row for row in again if row.figure == "cost.quality_time"]
    assert untimed.state is QualityState.NOT_RECORDED and "not timed" in untimed.note
    # The time is volatile: the History page leaves it out of the sentences.
    found = manifest(project.path, DESIGN_CENSUS, viewer="a person")
    assert not [s for s in found["quality"]["said"] if s.startswith("Time to state")]


# ---------------------------------------------------------------------------
# The export.
# ---------------------------------------------------------------------------


def _exported(
    project: Project, run: str, tmp_path: Path, prefix: str
) -> dict[tuple[str, str], tuple[str, int | None, int | None]]:
    out = tmp_path / f"export_{prefix}"
    done = CliRunner().invoke(
        app, ["export", "quality", "--project", str(project.path), "--run", run, "--out", str(out)]
    )
    assert done.exit_code == 0, done.output
    with (out / "quality.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["figure"].startswith(f"{prefix}.")]
    assert rows and all(row["method"] for row in rows)
    assert all(row["section"] == prefix for row in rows)
    return {
        (row["figure"], row["facet"]): (
            row["state"],
            int(row["count"]) if row["count"] else None,
            int(row["total"]) if row["total"] else None,
        )
        for row in rows
    }


def test_the_same_columns_figures_are_in_the_export(columns: Project, tmp_path: Path) -> None:
    stored = _rows(columns, COLUMNS_INFERENCE, "columns")
    assert _exported(columns, COLUMNS_INFERENCE, tmp_path, "columns") == {
        key: (row.state.value, row.count, row.total) for key, row in stored.items()
    }
