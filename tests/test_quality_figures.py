"""The quality figures every run carries, comparable across runs.

A small invented pool goes through the census, the dive and the inference, and every claim the
tests make about the stored figures is checked against a count taken another way: straight from
the stored Parquet tables with DuckDB, or by walking the pool itself. The figures are counts and
nothing else, so each must equal the independent count to the digit.

Every name is invented. The words are ones the Dictionary knows (cell lines, stains, techniques).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from ingence.api.routes.manifest import manifest
from ingence.api.v1.manifest import ManifestQuality
from ingence.cli.main import app
from ingence.core.outcomes import ScanOutcome
from ingence.core.project import Project
from ingence.core.schema import QualityState
from ingence.infer.run import rule_outcomes, run_inference
from ingence.quality import SUMMARIES
from ingence.quality.catalogue import catalogue
from ingence.quality.compare import figures_of
from ingence.quality.core import READ_FACET_OF, UNLISTED_OPERATIONS
from ingence.quality.publish import recompute
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
INFER = "run_20260101T000003Z_333333333333"
#: The sections the core summary writes. The registered summaries of other packages add their own.
CORE_SECTIONS = frozenset({"found", "read", "names", "conclusions", "cost"})
CENSUS_AGAIN = "run_20260101T000004Z_444444444444"

FILES = {
    "HeLa/imaging/HeLa_DAPI_001.csv": "a,b\n1,2\n",
    "HeLa/imaging/HeLa_DAPI_002.csv": "a,b\n3,4\n",
    "HeLa/imaging/HeLa_GFP_001.txt": "positions of the cells\n",
    "HeLa/analysis/HeLa_DAPI_001_results.csv": "cell,area\n1,40\n2,52\n",
    "notes/westernblot_protocol.txt": "block for one hour\n",
    ".hidden_cache": "hidden\n",
}


def _write_pool(pool: Path) -> None:
    for relative, text in FILES.items():
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _ids(prefix: str) -> dict[str, str]:
    return {"census": f"{prefix}1", "dive": f"{prefix}2", "infer": f"{prefix}3"}


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    base = tmp_path_factory.mktemp("quality")
    pool = base / "pool"
    _write_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    run_deepen(project, run_id=DIVE, started_at="t", budget_seconds=60)
    report = run_inference(project, run_id=INFER, started_at="t")
    return {"base": base, "pool": pool, "project": project, "report": report}


def _table(project: Project, run_id: str, table: str) -> str:
    """A run's stored table read straight from its Parquet file, for an independent count."""
    return (Path(project.path) / "runs" / run_id / table / "part-00000.parquet").as_posix()


def _count(project: Project, run_id: str, table: str, where: str = "true") -> int:
    path = Path(_table(project, run_id, table))
    if not path.is_file():  # a table with no rows has no file
        return 0
    row = (
        duckdb.connect()
        .execute(f"SELECT count(*) FROM read_parquet('{path}') WHERE {where}")
        .fetchone()
    )
    assert row is not None
    return int(row[0])


def _figures(project: Project, run_id: str) -> dict[tuple[str, str], Any]:
    return {(row.figure, row.facet): row for row in figures_of(project.path, run_id)}


def test_a_census_states_what_it_found_exactly_as_the_pool_is(made: dict[str, Any]) -> None:
    project, pool = made["project"], made["pool"]
    held = _figures(project, CENSUS)
    files = [path for path in pool.rglob("*") if path.is_file()]
    # The root folder is an entry of its own.
    folders = [pool, *(path for path in pool.rglob("*") if path.is_dir())]
    visible = [path for path in files if not path.name.startswith(".")]
    entries = held[("found.entries", "files")]
    assert (entries.count, entries.total) == (len(visible), len(visible) + len(folders))
    assert held[("found.entries", "folders")].count == len(folders)
    assert held[("found.entries", "links")].count == 0
    assert held[("found.bytes", "")].count == sum(path.stat().st_size for path in visible)
    # The one hidden file was left out and counted, out of every name the walk met.
    hidden = held[("found.left_out", "hidden")]
    assert (hidden.count, hidden.total) == (1, len(visible) + len(folders) + 1)
    assert held[("found.left_out", "exclude_glob")].count == 0
    # Every folder was listed, and each was counted.
    assert held[("found.folders_not_listed", "")].count == 0
    assert held[("found.folders_counted", "")].count == len(folders)
    assert held[("found.folders_counted", "")].total == len(folders)


def test_the_names_figures_are_the_stored_components_counted_by_what_the_dictionary_made(
    made: dict[str, Any],
) -> None:
    project = made["project"]
    held = _figures(project, CENSUS)
    components = _table(project, CENSUS, "components")
    bindings = _table(project, CENSUS, "dictionary_bindings")
    connection = duckdb.connect()
    words = dict(
        connection.execute(
            f"""SELECT coalesce(b.resolution_status, 'unexamined'), sum(c.occurrences)
            FROM read_parquet('{components}') c
            LEFT JOIN read_parquet('{bindings}') b USING (component_id)
            WHERE c.source_scope = 'name' AND c.component_type = 'word' GROUP BY 1"""
        ).fetchall()
    )
    read = sum(int(number) for number in words.values())
    assert read > 0
    assert held[("names.words", "understood")].count == int(words.get("resolved", 0))
    assert held[("names.words", "unknown")].count == int(words.get("examined_no_evidence", 0))
    assert held[("names.words", "contested")].count == int(words.get("examined_conflicting", 0))
    assert held[("names.words", "understood")].total == read
    # The parts of names are the words and the other kinds, and add up to their whole.
    parts = [row for (figure, _), row in held.items() if figure == "names.parts"]
    assert sum(row.count for row in parts) == parts[0].total
    assert held[("names.parts", "word")].count == read


def test_a_figure_the_pass_does_not_reach_is_not_run_and_never_zero(made: dict[str, Any]) -> None:
    project = made["project"]
    census = _figures(project, CENSUS)
    for figure in ("read.files_read", "conclusions.claims", "conclusions.questions", "found.parts"):
        row = census[(figure, "")]
        assert row.state is QualityState.NOT_RUN
        assert (row.count, row.total, row.duration_ns) == (None, None, None)
    # The dive's own budget is not a figure of a census or an inference.
    assert census[("cost.dive_budget", "")].state is QualityState.NOT_RUN
    assert _figures(project, INFER)[("cost.dive_budget", "")].state is QualityState.NOT_RUN
    # A dive has read files and has drawn no conclusion.
    dive = _figures(project, DIVE)
    assert dive[("read.files_read", "")].state is QualityState.COUNTED
    assert dive[("conclusions.claims", "")].state is QualityState.NOT_RUN
    # The engine's own peak memory is read as each pass ends, and is a figure of its own.
    for run_id in (CENSUS, DIVE, INFER):
        row = _figures(project, run_id)[("cost.peak_memory", "")]
        assert row.state is QualityState.COUNTED
        assert row.peak_bytes is not None and row.peak_bytes > 1_000_000
        assert (row.count, row.duration_ns) == (None, None)


def test_a_dive_states_what_it_read_and_how_each_file_was_digested(made: dict[str, Any]) -> None:
    project = made["project"]
    held = _figures(project, DIVE)
    files = _count(project, DIVE, "entries", "entry_kind = 'filesystem' AND entry_type = 'file'")
    read = _count(
        project,
        DIVE,
        "entries",
        "entry_kind = 'filesystem' AND entry_type = 'file' "
        "AND scan_outcome IN ('extracted', 'extracted_with_fallback')",
    )
    assert (held[("read.files_read", "")].count, held[("read.files_read", "")].total) == (
        read,
        files,
    )
    # Every file is accounted for once: read, or not read for one stated reason.
    parts = [row for (figure, _), row in held.items() if figure == "read.files_not_read"]
    assert read + sum(row.count for row in parts) == files
    # Whole, sampled and no digest also account for every file once.
    digests = {facet: held[("read.digests", facet)].count for facet in ("whole", "sampled", "none")}
    assert sum(digests.values()) == files
    whole = (
        duckdb.connect()
        .execute(
            f"""SELECT count(DISTINCT entry_id)
        FROM read_parquet('{_table(project, DIVE, "observations")}')
        WHERE identity_method = 'blake3_whole'
        AND entry_id IN (SELECT entry_id FROM read_parquet('{_table(project, DIVE, "entries")}')
                         WHERE entry_kind = 'filesystem' AND entry_type = 'file')"""
        )
        .fetchone()
    )
    assert whole is not None
    assert digests["whole"] == int(whole[0])
    spent = held[("cost.dive_budget", "spent")]
    granted = held[("cost.dive_budget", "granted")]
    assert granted.duration_ns == 60 * 1_000_000_000
    assert spent.duration_ns is not None
    assert spent.duration_ns <= granted.duration_ns


def test_an_inference_states_its_conclusions_and_keeps_the_three_abstentions_apart(
    made: dict[str, Any],
) -> None:
    project, report = made["project"], made["report"]
    held = _figures(project, INFER)
    # Claims the rules concluded: the stored claims, leaving out the census's own counts.
    concluded = _count(
        project,
        INFER,
        "claims",
        "rule_id NOT LIKE 'census.%' AND rule_id NOT LIKE 'cascade.%' "
        "AND predicate NOT LIKE 'closed_world.%'",
    )
    assert held[("conclusions.claims", "")].count == concluded
    by_kind = [
        row for (figure, facet), row in held.items() if figure == "conclusions.claims" and facet
    ]
    assert sum(row.count for row in by_kind) == concluded
    assert all("/" in row.facet for row in by_kind)
    # Standing: the claims a defeater left conflicting, apart from the resolved ones.
    conflicting = _count(
        project,
        INFER,
        "claims",
        "resolution_status = 'examined_conflicting' AND predicate NOT LIKE 'closed_world.%'",
    )
    assert held[("conclusions.claims_standing", "conflicting")].count == conflicting
    # The pairs proposed.
    assert held[("conclusions.candidates", "")].count == _count(project, INFER, "candidates")
    # The findings, by what each is about, and the questions they raise.
    findings = _count(project, INFER, "findings")
    assert (
        sum(held[("conclusions.findings", facet)].count for facet in ("entry", "group", "scope"))
        == findings
    )
    # The abstentions are the pass's own counts, in four states and never merged.
    counts = rule_outcomes(report)
    states = ("concluded", "unknown", "unexamined", "conflicting", "starved")
    for facet in states:
        row = held[("conclusions.abstentions", facet)]
        assert (row.count, row.total) == (
            counts[facet if facet != "concluded" else "concluded"],
            counts["evaluated"],
        )
    assert (
        sum(held[("conclusions.abstentions", facet)].count for facet in states)
        == counts["evaluated"]
    )
    # The pass's counts are the ones its journal wrote, taken another way.
    path = _table(project, INFER, "journal")
    detail = (
        duckdb.connect()
        .execute(
            f"SELECT detail FROM read_parquet('{path}') "
            "WHERE event IN ('rule.evaluated', 'rule.flagged')"
        )
        .fetchall()
    )
    journal = dict.fromkeys(("evaluated", "no_evidence", "unexamined", "conflicting", "starved"), 0)
    for (text,) in detail:
        for name in journal:
            journal[name] += int(json.loads(text).get(name, 0))
    assert journal["evaluated"] == counts["evaluated"]
    assert journal["no_evidence"] == counts["unknown"]
    assert journal["unexamined"] == counts["unexamined"]
    assert journal["conflicting"] == counts["conflicting"]


def test_the_stored_figures_are_the_ones_the_tables_give_again(made: dict[str, Any]) -> None:
    project = made["project"]
    for run_id, phase in ((CENSUS, "census"), (DIVE, "dive")):
        again = recompute(project.path, run_id, phase=phase)
        # Memory is read as a pass ends and the time of each summary is taken as it runs. No table
        # holds either, so a run stated again from its tables does not give them back. They are
        # not recorded there.
        held_by_no_table = ("cost.peak_memory", "cost.quality_time")
        assert [row.model_dump() for row in again if row.figure not in held_by_no_table] == [
            row.model_dump()
            for row in figures_of(project.path, run_id)
            if row.figure not in held_by_no_table
        ]
        (peak,) = [row for row in again if row.figure == "cost.peak_memory"]
        assert peak.state is QualityState.NOT_RECORDED


def test_every_figure_is_in_the_catalogue_with_a_method_and_the_core_states_all_of_them(
    made: dict[str, Any],
) -> None:
    project = made["project"]
    stated = {figure for figure, _ in _figures(project, INFER)}
    # The one figure the collector writes only for a summary it could not read has no row here.
    assert stated == set(catalogue().figures) - {"quality.summaries_not_recorded"}
    for definition in catalogue().figures.values():
        assert definition.method.endswith((".", ")"))
        assert definition.label and definition.unit
        assert definition.section in catalogue().sections
    # A facet a closed breakdown names is stated, and nothing else is.
    held = _figures(project, INFER)
    for name, definition in catalogue().figures.items():
        rows = [row for (figure, _), row in held.items() if figure == name]
        if definition.facets and all(row.state is QualityState.COUNTED for row in rows):
            assert {row.facet for row in rows} == set(definition.facets)


def test_each_stored_outcome_of_a_reading_is_counted_somewhere() -> None:
    assert set(READ_FACET_OF) == set(ScanOutcome)
    from ingence.scan.census import _UNLISTED_OPERATIONS

    assert set(UNLISTED_OPERATIONS) == set(_UNLISTED_OPERATIONS)


def test_a_second_census_with_one_file_added_shows_the_change_in_the_manifest(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    _write_pool(pool)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    (pool / "HeLa" / "imaging" / "HeLa_DAPI_003.csv").write_text("a,b\n5,6\n")
    run_census(project, run_id=CENSUS_AGAIN, started_at="t")
    found = manifest(project.path, CENSUS_AGAIN, viewer="a person")
    quality = found["quality"]
    assert quality["available"] is True
    assert found["compared_run_id"] == CENSUS
    said = quality["said"]
    sentences = [sentence for sentence in said if sentence.startswith("Entries found (files)")]
    assert len(sentences) == 1
    before = _figures(project, CENSUS)[("found.entries", "files")]
    assert before.count is not None and before.total is not None
    assert sentences[0] == (
        f"Entries found (files): {before.count + 1:,} of {before.total + 1:,} entries, "
        f"up 1 from {before.count:,} of {before.total:,} entries."
    )
    assert any(
        sentence.startswith("Bytes found: ") and "up 8 from" in sentence for sentence in said
    )
    # A figure that did not move says nothing, and the rows hold every figure beside the last.
    rows = {(row["figure"], row["facet"]): row for row in quality["rows"]}
    still = rows[("found.entries", "links")]
    assert (still["count"], still["was_count"], still["changed"]) == (0, 0, False)
    assert not any(row["said"] in said for row in quality["rows"] if not row["changed"])
    # The pass that wrote neither run reached conclusions: not run, and not a zero beside a zero.
    claims = rows[("conclusions.claims", "")]
    assert (claims["state"], claims["count"], claims["was_state"]) == ("not_run", None, "not_run")
    # Two censuses by one engine over one vocabulary: the versions are the same, so the pool is
    # what moved the figures.
    assert quality["versions_said"].startswith("The same versions as the run before it")
    assert {item["name"] for item in quality["versions"]} == set(catalogue().versions)
    # What the API serves validates, so the page can rely on it.
    assert ManifestQuality.model_validate(quality).rows


def test_a_run_from_an_engine_that_kept_no_figures_says_so(
    made: dict[str, Any], tmp_path: Path
) -> None:
    import shutil

    base = tmp_path / "older"
    pool = base / "pool"
    _write_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    run_census(project, run_id=CENSUS_AGAIN, started_at="t")
    # Written by an engine that kept none: the table is not in the run.
    shutil.rmtree(project.path / "runs" / CENSUS / "quality_figures")
    found = manifest(project.path, CENSUS_AGAIN, viewer="a person")["quality"]
    assert found["available"] is True
    assert "kept no quality figures" in found["why_not"]
    assert all(row["was_count"] is None for row in found["rows"])
    first = manifest(project.path, CENSUS, viewer="a person")["quality"]
    assert first["available"] is False
    assert "kept no quality figures" in first["why_not"]


def test_a_summary_that_fails_never_stops_a_run_being_published(tmp_path: Path) -> None:
    base = tmp_path / "failing"
    pool = base / "pool"
    _write_pool(pool)
    catalogue_file = base / "toy.yaml"
    catalogue_file.write_text(
        "figures:\n"
        "  toy.broken:\n"
        "    section: found\n"
        "    label: Broken figure\n"
        "    unit: things\n"
        "    known_from: census\n"
        "    method: Raises when asked.\n"
    )

    @SUMMARIES.register(
        "toy_failing",
        version="1",
        summary="a summary that raises",
        attributes={"catalogue": str(catalogue_file)},
    )
    def _failing() -> Any:
        def summarise(context: Any) -> Any:
            message = "no"
            raise RuntimeError(message)

        return summarise

    catalogue.cache_clear()
    try:
        project = Project.create(base / "project", created_at="t", roots=[pool])
        report = run_census(project, run_id=CENSUS, started_at="t")
        assert report.published
        held = _figures(project, CENSUS)
        assert held[("toy.broken", "")].state is QualityState.NOT_RECORDED
        assert "RuntimeError" in held[("toy.broken", "")].note
        # The core's figures are there all the same.
        assert held[("found.entries", "files")].state is QualityState.COUNTED
    finally:
        SUMMARIES.unregister("toy_failing")
        catalogue.cache_clear()


def test_another_module_registers_a_summary_with_its_own_catalogue(tmp_path: Path) -> None:
    base = tmp_path / "registered"
    pool = base / "pool"
    _write_pool(pool)
    catalogue_file = base / "toy.yaml"
    catalogue_file.write_text(
        "figures:\n"
        "  toy.folders_with_files:\n"
        "    section: found\n"
        "    label: Folders holding files\n"
        "    unit: folders\n"
        "    known_from: census\n"
        "    total: folders found\n"
        "    method: Folders with at least one file directly inside, out of the folders found.\n"
    )

    @SUMMARIES.register(
        "toy_folders",
        version="1",
        summary="folders holding files",
        attributes={"catalogue": str(catalogue_file)},
    )
    def _toy() -> Any:
        def summarise(context: Any) -> Any:
            row = context.catalog.query(
                "SELECT count(DISTINCT parent_entry_id) FROM entries WHERE entry_type = 'file' "
                "AND entry_kind = 'filesystem'"
            ).fetchone()
            folders = context.catalog.query(
                "SELECT count(*) FROM entries WHERE entry_type = 'directory'"
            ).fetchone()
            yield context.counted(
                "toy.folders_with_files", count=int(row[0]), total=int(folders[0])
            )

        return summarise

    catalogue.cache_clear()
    try:
        project = Project.create(base / "project", created_at="t", roots=[pool])
        run_census(project, run_id=CENSUS, started_at="t")
        run_census(project, run_id=CENSUS_AGAIN, started_at="t")
        row = _figures(project, CENSUS)[("toy.folders_with_files", "")]
        assert (row.count, row.total) == (3, 5)
        # Its method comes from its catalogue, and the manifest compares it like any other.
        said = manifest(project.path, CENSUS_AGAIN, viewer="a person")["quality"]["rows"]
        toy = next(item for item in said if item["figure"] == "toy.folders_with_files")
        assert toy["method"].startswith("Folders with at least one file")
        assert (toy["count"], toy["was_count"], toy["changed"]) == (3, 3, False)
    finally:
        SUMMARIES.unregister("toy_folders")
        catalogue.cache_clear()


# -------------------------------------------------------------------- the export


def _export(*arguments: str) -> Any:
    return CliRunner().invoke(app, ["export", "quality", *arguments])


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_the_export_writes_the_figures_with_their_methods_for_one_run_and_for_all(
    made: dict[str, Any], tmp_path: Path
) -> None:
    project = made["project"]
    out = tmp_path / "one"
    done = _export("--project", str(project.path), "--run", INFER, "--out", str(out))
    assert done.exit_code == 0, done.output
    rows = _read_csv(out / "quality.csv")
    stored = figures_of(project.path, INFER)
    assert len(rows) == len(stored)
    assert set(rows[0]) == {
        "project",
        "run",
        "phase",
        "section",
        "figure",
        "facet",
        "label",
        "unit",
        "state",
        "count",
        "total",
        "seconds",
        "peak_bytes",
        "method",
        "note",
    }
    assert {row["run"] for row in rows} == {INFER}
    assert all(row["method"] for row in rows)
    files = next(row for row in rows if (row["figure"], row["facet"]) == ("found.entries", "files"))
    assert (files["count"], files["total"]) == ("5", "10")
    # A figure not run keeps its row with its state and no count, never a zero.
    unrun = next(row for row in rows if row["figure"] == "cost.dive_budget")
    assert (unrun["state"], unrun["count"], unrun["total"]) == ("not_run", "", "")
    document = json.loads((out / "quality.json").read_text())
    assert len(document["figures"]) == len(rows)
    assert document["runs"][0]["engine_version"]
    versions = _read_csv(out / "quality_runs.csv")
    assert versions[0]["run"] == INFER and versions[0]["rulebook_hash"]
    # An inference reads no names, so its record states no name reader. The one its components
    # carry is named all the same.
    assert versions[0]["normalizer_version"]
    # Every published run, oldest first.
    every = tmp_path / "every"
    done = _export("--project", str(project.path), "--all", "--out", str(every))
    assert done.exit_code == 0, done.output
    runs = [row["run"] for row in _read_csv(every / "quality_runs.csv")]
    assert runs[:3] == [CENSUS, DIVE, INFER]


def test_the_export_refuses_a_place_inside_a_root_and_writes_nothing(
    made: dict[str, Any],
) -> None:
    project, pool = made["project"], made["pool"]
    before = sorted(pool.rglob("*"))
    inside = pool / "HeLa" / "exported"
    done = _export("--project", str(project.path), "--out", str(inside))
    assert done.exit_code == 2
    assert "never writes into the folders it reads" in " ".join(done.output.split())
    assert sorted(pool.rglob("*")) == before
    assert not inside.exists()
    # Nor inside the project, outside its exports folder.
    done = _export("--project", str(project.path), "--out", str(project.path / "runs" / "x"))
    assert done.exit_code == 2
    # By default it writes into the project's exports folder.
    done = _export("--project", str(project.path))
    assert done.exit_code == 0, done.output
    assert (project.path / "projections" / "exports" / "quality" / INFER / "quality.csv").is_file()


def test_two_projects_exports_join_into_one_table(made: dict[str, Any], tmp_path: Path) -> None:
    base = tmp_path / "second"
    pool = base / "pool"
    _write_pool(pool)
    (pool / "HeLa" / "imaging" / "HeLa_DAPI_004.csv").write_text("a,b\n9,9\n")
    second = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(second, run_id=CENSUS, started_at="t")
    first_out, second_out = tmp_path / "a", tmp_path / "b"
    assert (
        _export(
            "--project",
            str(made["project"].path),
            "--run",
            CENSUS,
            "--out",
            str(first_out),
            "--project-name",
            "first lab",
        ).exit_code
        == 0
    )
    assert (
        _export(
            "--project",
            str(second.path),
            "--run",
            CENSUS,
            "--out",
            str(second_out),
            "--label",  # the earlier spelling still works
            "second lab",
        ).exit_code
        == 0
    )
    header = (first_out / "quality.csv").read_text().splitlines()[0]
    assert header == (second_out / "quality.csv").read_text().splitlines()[0]
    assert header.startswith("project,run,")
    joined = _read_csv(first_out / "quality.csv") + _read_csv(second_out / "quality.csv")
    assert {row["project"] for row in joined} == {"first lab", "second lab"}
    files = {
        row["project"]: row["count"]
        for row in joined
        if (row["figure"], row["facet"]) == ("found.entries", "files")
    }
    assert int(files["second lab"]) == int(files["first lab"]) + 1


def test_the_export_names_the_project_only_when_asked_and_is_neutral_otherwise(
    made: dict[str, Any], tmp_path: Path
) -> None:
    base = tmp_path / "vornfolder"
    pool = base / "pool"
    _write_pool(pool)
    project = Project.create(base / "vornproject", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    neutral = tmp_path / "neutral"
    done = _export("--project", str(project.path), "--run", CENSUS, "--out", str(neutral))
    assert done.exit_code == 0, done.output
    rows = _read_csv(neutral / "quality.csv")
    assert {row["project"] for row in rows} == {"project"}
    assert {row["project"] for row in _read_csv(neutral / "quality_runs.csv")} == {"project"}
    # Nothing of the folder's own name is in any of the three files.
    for written in neutral.iterdir():
        assert "vorn" not in written.read_text(encoding="utf-8")
    named = tmp_path / "named"
    done = _export(
        "--project",
        str(project.path),
        "--run",
        CENSUS,
        "--out",
        str(named),
        "--project-name",
        "zib",
    )
    assert done.exit_code == 0, done.output
    assert {row["project"] for row in _read_csv(named / "quality.csv")} == {"zib"}


def test_the_export_names_a_run_that_is_not_there(made: dict[str, Any], tmp_path: Path) -> None:
    done = _export(
        "--project", str(made["project"].path), "--run", "run_nothing", "--out", str(tmp_path / "x")
    )
    assert done.exit_code == 2
    assert not (tmp_path / "x").exists()
    both = _export("--project", str(made["project"].path), "--run", INFER, "--all")
    assert both.exit_code == 2


def test_the_stored_table_is_in_every_runs_manifest_and_its_rows_count_what_the_manifest_says(
    made: dict[str, Any],
) -> None:
    project = made["project"]
    for run_id in (CENSUS, DIVE, INFER):
        with open_catalog(project.path, run_id=run_id) as catalog:
            assert catalog.count("quality_figures") == len(figures_of(project.path, run_id))
        document = json.loads((project.path / "runs" / run_id / "manifest.json").read_text())
        assert any(item["path"].startswith("quality_figures/") for item in document["files"])


def test_two_runs_made_by_different_versions_say_which_differ() -> None:
    from ingence.quality.compare import version_changes

    same = dict.fromkeys(catalogue().versions, "1")
    rows, said = version_changes(same, dict(same))
    assert said.startswith("The same versions as the run before it")
    changed = {**same, "rulebook_hash": "2", "engine_commit": "2"}
    rows, said = version_changes(changed, same)
    assert said == (
        "Not the same versions as the run before it: the engine's commit, the Rulebook's digest."
    )
    assert {row["name"]: (row["now"], row["was"]) for row in rows}["rulebook_hash"] == ("2", "1")


# ------------------------------------------------------- the comparison, in its corner cases


def _fig(figure: str, facet: str = "", **held: Any) -> Any:
    from ingence.core.schema import QualityFigure

    return QualityFigure(run_id="", phase="infer", figure=figure, facet=facet, **held)


def test_a_breakdown_value_one_run_lacks_is_zero_out_of_that_run_s_own_whole() -> None:
    from ingence.quality.compare import compare_quality

    claims = "conclusions.claims"
    now = [_fig(claims, count=8), _fig(claims, "classifies/defeasible", count=8, total=8)]
    was = [
        _fig(claims, count=9),
        _fig(claims, "classifies/defeasible", count=8, total=9),
        _fig(claims, "identical/entailed", count=1, total=9),
    ]
    rows = {row.facet: row for row in compare_quality(now, was)}
    gone = rows["identical/entailed"]
    assert (gone.count, gone.total, gone.was_count, gone.was_total) == (0, 8, 1, 9)
    assert gone.said == (
        "Claims concluded by the rules (identical/entailed): 0 of 8 claims, "
        "down 1 from 1 of 9 claims."
    )


def test_a_change_of_the_whole_alone_has_no_signed_zero_and_says_the_whole_moved() -> None:
    from ingence.quality.compare import compare_quality

    was = [_fig("read.files_read", count=5, total=5)]
    now = [_fig("read.files_read", count=5, total=6)]
    (row,) = compare_quality(now, was)
    assert row.changed and row.change == 0
    assert row.said == "Files a dive opened and read: 5 of 6 files, from 5 of 5 files."


def test_a_figure_that_goes_from_counted_to_not_recorded_gets_a_sentence_and_not_run_none() -> None:
    from ingence.core.schema import QualityState
    from ingence.quality.compare import compare_quality

    was = [_fig("read.files_read", count=5, total=5), _fig("found.bytes", count=7)]
    now = [
        _fig("read.files_read", state=QualityState.NOT_RUN),
        _fig("found.bytes", state=QualityState.NOT_RECORDED, note="the sizes were not kept"),
    ]
    rows = {row.figure: row for row in compare_quality(now, was)}
    # A figure the pass does not reach is no change and has no sentence.
    assert (rows["read.files_read"].changed, rows["read.files_read"].said) == (False, "")
    assert rows["read.files_read"].state == "not_run"
    assert rows["found.bytes"].changed
    assert rows["found.bytes"].said == (
        "Bytes found: not recorded (the sizes were not kept), though the run before it counted "
        "7 bytes."
    )
    # The reason travels with the row, so a page can say it instead of a fixed phrase.
    assert rows["found.bytes"].note == "the sizes were not kept"
    # And it reaches the API's row.
    from ingence.api.v1.manifest import ManifestQualityRow
    from ingence.quality.compare import asdict

    assert ManifestQualityRow.model_validate(asdict(rows["found.bytes"])).note.startswith(
        "the sizes"
    )


def test_a_figure_that_cannot_be_counted_is_only_itself_not_recorded(
    made: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.quality import core

    # A stored outcome the reader does not know: the reading figures fail, the rest stand.
    monkeypatch.setattr(core, "READ_FACET_OF", {})
    again = {
        (row.figure, row.facet): row for row in recompute(made["project"].path, DIVE, phase="dive")
    }
    read = again[("read.files_read", "")]
    assert read.state is QualityState.NOT_RECORDED
    assert "KeyError" in read.note
    assert again[("found.entries", "files")].state is QualityState.COUNTED
    assert again[("names.words", "unknown")].state is QualityState.COUNTED
    assert again[("read.digests", "whole")].state is QualityState.COUNTED


def test_a_summary_whose_catalogue_is_missing_is_not_recorded_and_the_run_publishes(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    _write_pool(pool)

    @SUMMARIES.register(
        "toy_lost",
        version="1",
        summary="a summary whose catalogue is gone",
        attributes={"catalogue": str(tmp_path / "gone.yaml")},
    )
    def _lost() -> Any:
        return lambda context: []

    catalogue.cache_clear()
    try:
        project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
        report = run_census(project, run_id=CENSUS, started_at="t")
        assert report.published
        row = _figures(project, CENSUS)[("quality.summaries_not_recorded", "toy_lost")]
        assert row.state is QualityState.NOT_RECORDED
        assert "could not be read" in row.note
        assert _figures(project, CENSUS)[("found.entries", "files")].count == 5
    finally:
        SUMMARIES.unregister("toy_lost")
        catalogue.cache_clear()


def test_peak_memory_is_volatile_and_not_recorded_where_the_platform_cannot_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.quality import publish
    from ingence.storage.catalog import VOLATILE_COLUMNS, compare_runs

    assert {"duration_ns", "peak_bytes"} <= VOLATILE_COLUMNS
    pool = tmp_path / "pool"
    _write_pool(pool)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    run_census(project, run_id=CENSUS_AGAIN, started_at="t")
    # Two censuses of one unchanged pool differ in time and memory only, which a comparison skips.
    assert "quality_figures" not in compare_runs(project.path, CENSUS, CENSUS_AGAIN)
    monkeypatch.setattr(publish, "_resident_peak", lambda: None)
    run_census(project, run_id="run_20260101T000005Z_555555555555", started_at="t")
    row = _figures(project, "run_20260101T000005Z_555555555555")[("cost.peak_memory", "")]
    assert row.state is QualityState.NOT_RECORDED
    assert "platform cannot read" in row.note


def _sentences_of(project: Project, run_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    quality = manifest(project.path, run_id, viewer="a person")["quality"]
    return quality["said"], quality["rows"]


def test_a_pass_that_does_not_reach_a_breakdown_says_nothing_of_it_on_any_ordinary_run(
    tmp_path: Path,
) -> None:
    """Census, dive, inference, then census again: no sentence for a figure a pass simply does
    not reach, and nothing is called not recorded."""
    pool = tmp_path / "pool"
    _write_pool(pool)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    ids = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 5)]
    began = [f"2026-01-01T00:00:0{n}Z" for n in range(1, 5)]  # stamps a wall time reads
    run_census(project, run_id=ids[0], started_at=began[0])
    run_deepen(project, run_id=ids[1], started_at=began[1], budget_seconds=60)
    run_inference(project, run_id=ids[2], started_at=began[2])
    run_census(project, run_id=ids[3], started_at=began[3])
    for run_id in ids:
        said, rows = _sentences_of(project, run_id)
        assert not [sentence for sentence in said if "though the run before it counted" in sentence]
        assert not [sentence for sentence in said if "not recorded" in sentence]
        # What the pass does not reach is not run in every row, never not recorded. (The core's
        # rows. A pool with no experiments gives the registered summaries of the design grid and
        # the themes nothing to take a median or a seed agreement of, and they say so.)
        assert {row["state"] for row in rows if row["section"] in CORE_SECTIONS} <= {
            "counted",
            "not_run",
        }
        # A figure of the machine and the moment is in the table, not in the sentences.
        assert not [s for s in said if s.startswith(("Wall time", "Peak resident", "Dive budget"))]
    _, rows = _sentences_of(project, ids[2])
    budget = [row for row in rows if row["figure"] == "cost.dive_budget"]
    assert [row["state"] for row in budget] == ["not_run"]
    _, rows = _sentences_of(project, ids[3])
    unread = [row for row in rows if row["figure"] == "read.files_not_read"]
    assert {row["state"] for row in unread} == {"not_run"}
    assert any(row["volatile"] and row["state"] == "counted" for row in rows)


def test_peak_memory_is_said_in_mebibytes() -> None:
    from ingence.quality.compare import compare_quality

    mebibyte = 1024 * 1024
    was = [_fig("cost.peak_memory", peak_bytes=200 * mebibyte)]
    now = [_fig("cost.peak_memory", peak_bytes=212 * mebibyte)]
    (row,) = compare_quality(now, was)
    assert row.volatile and row.changed
    assert row.said == "Peak resident memory: 212 MiB, up 12 MiB from 200 MiB."


def test_a_catalogue_that_defines_a_figure_another_defines_is_refused_whole(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    _write_pool(pool)
    twice = tmp_path / "twice.yaml"
    twice.write_text(
        "figures:\n"
        "  found.bytes:\n"
        "    section: found\n    label: Another count\n    unit: things\n"
        "    known_from: census\n    method: Counts something else.\n"
        "  toy.fine:\n"
        "    section: found\n    label: Fine\n    unit: things\n"
        "    known_from: census\n    method: Counts.\n"
    )

    @SUMMARIES.register(
        "zz_twice",
        version="1",
        summary="defines a figure twice",
        attributes={"catalogue": str(twice)},
    )
    def _twice() -> Any:
        return lambda context: [context.counted("toy.fine", count=1)]

    catalogue.cache_clear()
    try:
        held = catalogue()
        assert "found.bytes" in held.figures and "toy.fine" not in held.figures
        assert "found.bytes" in held.problems["zz_twice"]
        assert held.figures["found.bytes"].label == "Bytes found"
        project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
        assert run_census(project, run_id=CENSUS, started_at="t").published
        row = _figures(project, CENSUS)[("quality.summaries_not_recorded", "zz_twice")]
        assert row.state is QualityState.NOT_RECORDED and "found.bytes" in row.note
        assert ("toy.fine", "") not in _figures(project, CENSUS)
    finally:
        SUMMARIES.unregister("zz_twice")
        catalogue.cache_clear()


def test_no_label_has_parentheses_inside_parentheses() -> None:
    from ingence.quality.compare import _heading

    for name, definition in catalogue().figures.items():
        for facet in definition.facets or {""}:
            assert _heading(name, facet).count("(") <= 1, _heading(name, facet)
