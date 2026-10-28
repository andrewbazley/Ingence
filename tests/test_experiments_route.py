"""The experiments the engine found, one list (Explore > Experiments).

``GET /api/v1/experiments`` is built on the one definition of an experiment
(``ingence.experiment_units``) and on the completeness figures, so it, the Years view, "No analysis
found" and an experiment's files cannot disagree about which experiments there are or how many
files each holds. It adds each experiment's files by stage and by kind, how well they were
characterized (each count over the experiment's files), and what a followed raw file rests on: a
pair a person confirmed, or a proposed pair the count takes as right.

The class: a list and its detail count the same things, and the pairing assumption is shown
as counts with their denominator. Every name here is invented: a pool of five dated experiments
and one without a date, two people's folders, and a recording outside every experiment.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"
LATER = "run_20260101T000003Z_333333333333"
FINAL = "run_20260101T000004Z_444444444444"
PERSON = "priya"

#: The experiments of the pool, by folder.
HELA = "dana/2024-02-05_hela_dapi"
HEK = "dana/2024-03-11_hek293_gfp"
PLAN = "sam/2024-04-02_actin_plan"
TUBULIN = "sam/2024-05-20_tubulin_dmso"
BLOT = "sam/2024-06-01_western_blot_summary"
UNDATED = "sam/pbs_untitled"
ALL = (HELA, HEK, PLAN, TUBULIN, BLOT, UNDATED)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_pool(root: Path) -> Path:
    """Six experiments of two people, and one recording outside them all.

    - ``HELA``: six recordings; a table named exactly as the first (a pair the engine proposes and
      the naming guess leaves out until a person confirms it); a table named for the second
      (followed by its name); a note. Listed under "No analysis found" (one of six followed).
    - ``HEK``: three recordings and nothing else (raw only).
    - ``PLAN``: a note (neither raw nor analysis found).
    - ``TUBULIN``: five recordings, each with a results table named for it, and a workbook (enough
      raw files to be judged, all followed).
    - ``BLOT``: two tables and no recording (analysis only).
    - ``UNDATED``: three recordings in a folder whose name holds no date (nothing dates it), two
      with a results table named for them (too few raw files to be judged).
    """
    first = root / HELA
    for number in range(1, 7):
        _write(first / "raw" / f"hela_dapi_{number:03d}.nd2")
    _write(first / "analysis" / "hela_dapi_001.csv", "t,x\n1,2\n")
    _write(first / "analysis" / "Traj_hela_dapi_002.csv", "t,x\n1,2\n")
    _write(first / "notes.txt", "plain words\n")
    for number in range(1, 4):
        _write(root / HEK / f"hek293_gfp_{number:03d}.nd2")
    _write(root / PLAN / "plan.txt", "plain words\n")
    for number in range(1, 6):
        _write(root / TUBULIN / f"tubulin_dmso_{number:03d}.nd2")
        _write(root / TUBULIN / f"tubulin_dmso_{number:03d}_results.csv", "a,b\n1,2\n")
    _write(root / TUBULIN / "tubulin_dmso_summary.xlsx")
    _write(root / BLOT / "blot_results.csv", "a,b\n1,2\n")
    _write(root / BLOT / "blot_tables.csv", "a,b\n1,2\n")
    for number in range(1, 4):
        _write(root / UNDATED / f"pbs_untitled_{number:03d}.nd2")
    for number in range(1, 3):
        _write(root / UNDATED / f"pbs_untitled_{number:03d}_results.csv", "a,b\n1,2\n")
    _write(root / "stray_dapi_001.nd2")
    return root


def ask(project: Path, path: str = "experiments", **query: Any) -> tuple[HTTPStatus, Any]:
    from ingence.api.v1 import context_for, dispatch

    context = context_for(project, settled=None, job_running=False)
    return dispatch(context, path, {name: [str(value)] for name, value in query.items()})


def listing(project: Path, **query: Any) -> dict[str, Any]:
    status, body = ask(project, **query)
    assert status == HTTPStatus.OK, body
    assert isinstance(body, dict)
    return body


def by_folder(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["folder"]: row for row in body["rows"]}


class World(NamedTuple):
    project: Project
    #: The route's whole list on the run before the person confirmed any pair.
    before: dict[str, Any]
    #: The list on the run after the person confirmed a pair the naming guess had left out.
    first: dict[str, Any]
    #: The two pairs confirmed, in the order: the recording and the table named exactly as it
    #: (not followed before), then a recording its name-matched table already followed.
    pairs: tuple[tuple[str, str], tuple[str, str]]


def _confirm(project: Project, recording: str, table: str) -> tuple[str, str]:
    """A person (invented) confirms that the table was made from the recording."""
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        proposed = catalog.query(
            "SELECT c.candidate_id, c.source_id, c.target_id FROM candidates c "
            "JOIN entries s ON s.entry_id = c.source_id JOIN entries t ON t.entry_id = c.target_id "
            "WHERE c.primitive = 'derives' AND s.name = ? AND t.name = ?",
            [recording, table],
        ).fetchone()
        assert proposed is not None, "the engine proposes the table as made from the recording"
        decide_target(
            DecisionContext(catalog, project.path),
            Decision(target=f"candidate:{proposed[0]}", word="derived"),
            actor=PERSON,
            occurred_at="2026-10-08T07:30:00Z",
        )
    return str(proposed[1]), str(proposed[2])


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    base = tmp_path_factory.mktemp("experiments_route")
    pool = write_pool(base / "pool")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    before = listing(created.path, limit=500)
    # The first answer: a pair the same-name guess left out, so a raw file is newly followed.
    new_pair = _confirm(created, "hela_dapi_001.nd2", "hela_dapi_001.csv")
    run_inference(created, run_id=LATER, started_at="t")
    first = listing(created.path, limit=500)
    # The second: a pair whose raw file was already followed through its name.
    followed_pair = _confirm(created, "hela_dapi_002.nd2", "Traj_hela_dapi_002.csv")
    run_inference(created, run_id=FINAL, started_at="t")
    return World(created, before, first, (new_pair, followed_pair))


# ---------------------------------------------------------------------------
# One definition: the same experiments, the same counts.
# ---------------------------------------------------------------------------


def test_the_experiments_are_exactly_the_ones_the_definition_and_completeness_count(
    world: World,
) -> None:
    from ingence.completeness import completeness_of_run
    from ingence.experiment_units import run_units
    from ingence.storage.catalog import open_catalog

    body = listing(world.project.path, limit=500)
    with open_catalog(world.project.path) as catalog:
        units = run_units(catalog)
    counted = {units.paths[unit] for unit in set(units.units.values())}
    assert set(by_folder(body)) == counted == set(ALL)
    found = completeness_of_run(world.project.path, FINAL)
    assert body["run_id"] == FINAL
    assert body["summary"]["experiments"] == body["matching"] == len(found.experiments) == 6
    for row in found.experiments:
        served = by_folder(body)[row.folder]
        assert served["id"] == row.experiment_id
        assert (served["files"], served["raw_acquisitions"]) == (row.files, row.raw_acquisitions)
        assert (served["analysis_files"], served["result_tables"]) == (
            row.analysis_files,
            row.result_tables,
        )
        # ``person`` is filled only where the area's folder stands for a person, which no answer
        # here says; the area itself is the folder.
        assert (served["derived_files"], served["person"], served["date"]) == (
            row.derived_files,
            None,
            row.date,
        )
        assert served["area"] == row.person and served["area_kind"] == "folder"
        assert served["followed"]["followed"] == row.followed
        assert served["followed"]["raw_found"] == row.raw_acquisitions
        assert served["followed"]["share"] == row.share
        assert served["has_analysis"] == row.has_analysis
    assert body["summary"]["files_outside"] == found.outside.files == 1


def test_raw_and_analysis_found_agree_with_no_analysis_found_for_each_experiment(
    world: World,
) -> None:
    body = listing(world.project.path, limit=500)
    status, standing = ask(world.project.path, "completeness/no_analysis_found")
    assert status == HTTPStatus.OK
    listed = {row["folder"] for row in standing["listed"]}
    assert listed == {HELA}
    judged_from = standing["settings"]["judged_from_acquisitions"]
    for folder, row in by_folder(body).items():
        assert (row["no_analysis_found"] == "listed") == (folder in listed), folder
        # Never both: a row on the list is never "Raw and analysis found" or "Analysis only".
        if row["no_analysis_found"]:
            assert row["state"] in ("raw_only", "neither"), folder
        # For an experiment the list judges, analysis found is the list's own opposite.
        if row["raw_acquisitions"] >= judged_from:
            assert (row["state"] == "both") == (row["no_analysis_found"] is None), folder
            assert row["state_why"].startswith(
                "Ingence used the rule of the No analysis found list"
            )
        else:
            assert row["state_why"].startswith("Ingence found too few raw files to judge"), folder
            assert f"judges experiments with {judged_from} or more" in row["state_why"]
        if folder in listed:
            held = next(item for item in standing["listed"] if item["folder"] == folder)
            assert row["raw_acquisitions"] == held["raw_acquisitions"]
            assert row["followed"]["followed"] == held["raw_followed"]
            assert row["analysis_files"] == held["analysis_files"]
            assert row["result_tables"] == held["result_tables"]
    assert body["summary"]["no_analysis_found"] == len(listed)
    assert body["summary"]["judged"] == standing["judged"] == 2
    states = {folder: row["state"] for folder, row in by_folder(body).items()}
    assert states == {
        HELA: "raw_only",  # judged and listed: two of six followed, no analysis of its own
        HEK: "raw_only",  # too few raw files to be judged, and nothing else found
        PLAN: "neither",
        TUBULIN: "both",  # judged: every raw file followed
        BLOT: "analysis_only",
        UNDATED: "both",  # too few to be judged: a results table and followed raw files were found
    }
    counts = {item["value"]: item["experiments"] for item in body["states"]}
    assert counts == {"both": 2, "raw_only": 2, "analysis_only": 1, "neither": 1}
    assert [item["label"] for item in body["states"]] == [
        "Raw and analysis found",
        "Raw found, no analysis found",
        "Analysis found, no raw found",
        "No raw files or analysis found",
    ]


def test_analysis_found_is_the_lists_own_opposite_for_an_experiment_it_judges() -> None:
    from ingence.api.v1.experiments import state_of
    from ingence.completeness import ExperimentFound
    from ingence.core.settings import CompletenessSettings

    settings = CompletenessSettings(
        judged_from_acquisitions=5,
        listed_below_share=0.5,
        own_analysis_files=4,
        own_result_tables=3,
    )

    def row(raw: int, followed: int, analysis: int = 0, tables: int = 0) -> ExperimentFound:
        return ExperimentFound(
            experiment_id="x",
            root_id="r",
            folder="f",
            files=raw + analysis + tables,
            raw_acquisitions=raw,
            followed=followed,
            derived_files=followed,
            analysis_files=analysis,
            result_tables=tables,
        )

    def state(found: ExperimentFound) -> str:
        from dataclasses import replace

        return state_of(replace(found, has_analysis=found.has_analysis_under(settings)), settings)[
            0
        ]

    # Judged (five raw files or more): the list lists a row below half followed with no analysis
    # of its own, so analysis is not found there, and is found everywhere else.
    assert state(row(6, 2)) == "raw_only"  # the list lists it
    assert state(row(6, 3)) == "both"  # at the share: not listed
    assert state(row(6, 0, analysis=4)) == "both"  # its own analysis, whatever the share
    assert state(row(6, 0, tables=3)) == "both"
    assert state(row(6, 1, analysis=3, tables=2)) == "raw_only"  # a little analysis is not enough
    # Too few raw files to judge: anything found counts.
    assert state(row(3, 0)) == "raw_only"
    assert state(row(3, 1)) == "both"
    assert state(row(3, 0, tables=1)) == "both"
    assert state(row(0, 0, tables=1)) == "analysis_only"
    assert state(row(0, 0)) == "neither"


def test_a_row_says_how_the_folder_was_found_its_dates_and_its_person(world: World) -> None:
    rows = by_folder(listing(world.project.path, limit=500))
    assert rows[PLAN]["found_by"] == "dated_name"
    assert rows[PLAN]["found_by_kind"] == "named"
    assert "dated name" in rows[PLAN]["found_by_said"]
    assert rows[HEK]["found_by"] == "structure"
    assert rows[HEK]["found_by_said"].startswith("What it holds")
    assert rows[HELA]["date"] == "2024-02-05"
    assert rows[HELA]["area"] == "dana" and rows[TUBULIN]["area"] == "sam"
    assert rows[HELA]["person"] is None, "a person is never guessed from a name alone (0.4.0)"
    # Nothing dates it: unknown, never a guess.
    assert rows[UNDATED]["date"] is None
    assert rows[UNDATED]["shown_name"] == "pbs untitled"
    assert rows[HELA]["name"] == "2024-02-05_hela_dapi"
    assert rows[HELA]["shown_name"] != rows[HELA]["name"]


# ---------------------------------------------------------------------------
# Files by stage and by kind, and how well they were characterized.
# ---------------------------------------------------------------------------


def test_each_rows_files_by_stage_and_by_kind_sum_to_its_files(world: World) -> None:
    body = listing(world.project.path, limit=500)
    assert body["rows"]
    for row in body["rows"]:
        assert sum(item["files"] for item in row["by_stage"]) == row["files"], row["folder"]
        assert sum(item["files"] for item in row["by_kind"]) == row["files"], row["folder"]
        stages = {item["key"]: item["files"] for item in row["by_stage"]}
        # The stage classes are the ones completeness counts.
        assert stages["raw"] == row["raw_acquisitions"]
        assert stages["analysis"] == row["analysis_files"]
        assert [item["key"] for item in row["by_stage"]] == [
            "raw",
            "analysis",
            "other",
            "conflicting",
            "none",
        ]
        assert [item["key"] for item in row["by_kind"]] == [
            "image",
            "table",
            "text",
            "archive",
            "array",
            "other",
            "conflicting",
            "unknown",
        ]
        assert all(item["label"] for item in row["by_stage"] + row["by_kind"])
    rows = by_folder(body)
    kinds = {item["key"]: item["files"] for item in rows[HEK]["by_kind"]}
    assert kinds["image"] == 3 and kinds["table"] == 0
    kinds = {item["key"]: item["files"] for item in rows[BLOT]["by_kind"]}
    assert kinds["table"] == 2
    stages = {item["key"]: item["files"] for item in rows[PLAN]["by_stage"]}
    assert stages["none"] == 1 and stages["raw"] == 0


def test_characterization_counts_carry_the_experiments_files_as_their_denominator(
    world: World,
) -> None:
    for row in listing(world.project.path, limit=500)["rows"]:
        held = row["characterized"]
        assert held["files"] == row["files"], row["folder"]
        for name in ("stage_read", "stage_conflicting", "role_read", "role_conflicting"):
            assert 0 <= held[name] <= held["files"], (row["folder"], name)
        assert held["stage_read"] + held["stage_conflicting"] <= held["files"]
        stages = {item["key"]: item["files"] for item in row["by_stage"]}
        # A stage read is a file in a raw, an analysis or another stage.
        assert held["stage_read"] == stages["raw"] + stages["analysis"] + stages["other"]
        assert held["stage_conflicting"] == stages["conflicting"]
    rows = by_folder(listing(world.project.path, limit=500))
    # Three recordings read at the raw stage; nothing else was in the folder.
    assert rows[HEK]["characterized"]["stage_read"] == 3
    # A note no rule read a stage of is not "unread": it is a file with no stage read.
    assert rows[PLAN]["characterized"]["stage_read"] == 0
    assert rows[PLAN]["characterized"]["files"] == 1


def test_stage_role_and_kind_read_equal_the_files_pages_counts_for_each_experiment(
    world: World,
) -> None:
    """The row and Years' card read a file's stage, role and kind alike: the number of files the
    files page shows with a stage (a role, a format) is what the row says was read."""
    rows = listing(world.project.path, limit=500)["rows"]
    assert rows
    for row in rows:
        status, page = ask(world.project.path, "experiments/files", experiment=row["id"], limit=500)
        assert status == HTTPStatus.OK, page
        shown = page["rows"]
        held = row["characterized"]
        assert len(shown) == row["files"], row["folder"]
        for kind in ("stage", "role"):
            with_one = [item for item in shown if item[kind]]
            settled = [item for item in with_one if any(not v["conflicting"] for v in item[kind])]
            assert len(with_one) == held[f"{kind}_read"] + held[f"{kind}_conflicting"], (
                row["folder"],
                kind,
            )
            assert len(settled) == held[f"{kind}_read"], (row["folder"], kind)
        kinds = {item["key"]: item["files"] for item in row["by_kind"]}
        assert len([item for item in shown if item["format"]]) == row["files"] - kinds["unknown"]
    # The pool holds roles and stages to compare, not only empty lists.
    assert any(row["characterized"]["role_read"] for row in rows)
    assert any(row["characterized"]["stage_read"] for row in rows)


PATTERNS = {
    "format": "classifies.format%",
    "stage": "classifies.%stage%",
    "role": "classifies.%role%",
}


def _hand_made_tally(reading: Any) -> Any:
    """The tally of one experiment over the engine's own two tables, made by hand."""
    import duckdb

    from ingence.api.v1.experiments import file_tallies
    from ingence.completeness.counting import Classes
    from ingence.experiment_units import RunUnits

    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE entries (entry_id VARCHAR, parent_entry_id VARCHAR, is_deleted BOOLEAN, "
        "entry_kind VARCHAR, entry_type VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE claims (subject VARCHAR, predicate VARCHAR, object_reference VARCHAR, "
        "resolution_status VARCHAR)"
    )
    files = {f"f{number}": "folder_a" for number in range(1, 8)} | {"stray": "folder_b"}
    for name, folder in files.items():
        connection.execute(
            "INSERT INTO entries VALUES (?, ?, false, 'filesystem', 'file')", [name, folder]
        )
    connection.execute(
        "INSERT INTO entries VALUES ('gone', 'folder_a', true, 'filesystem', 'file')"
    )
    connection.execute(
        "INSERT INTO entries VALUES ('subfolder', 'folder_a', false, 'filesystem', 'directory')"
    )
    claims = [
        ("f1", "classifies.stage_of", "acquisition", "resolved"),
        ("f1", "classifies.format", "image_format", "resolved"),
        ("f1", "classifies.file_role", "reference", "resolved"),
        ("f2", "classifies.stage_of", "acquisition", "resolved"),
        ("f2", "classifies.stage_of", "analysis", "resolved"),
        ("f2", "classifies.format", "tabular_format", "resolved"),
        ("f3", "classifies.stage_of", "an_unlisted_stage", "resolved"),
        ("f3", "classifies.format", "a_format_no_family_holds", "resolved"),
        ("f4", "classifies.stage_of", "acquisition", "examined_conflicting"),
        ("f4", "classifies.format", "image_format", "examined_conflicting"),
        ("f4", "classifies.file_role", "reference", "examined_conflicting"),
        ("f5", "classifies.stage_of", "acquisition", "resolved"),
        ("f5", "classifies.stage_of", "analysis", "examined_conflicting"),
        ("f5", "classifies.format", "image_format", "resolved"),
        ("f5", "classifies.format", "tabular_format", "resolved"),
        ("gone", "classifies.stage_of", "acquisition", "resolved"),
        ("stray", "classifies.stage_of", "acquisition", "resolved"),
        # A claim that is no reading of stage, format or role is not counted as one, nor is a
        # stage reading not yet examined.
        ("f6", "classifies.earliest_credible_time", "x", "resolved"),
        ("f6", "classifies.stage_of", "acquisition", "unexamined"),
        # Spelled for a role, but its value is no role of the Dictionary's.
        ("f6", "classifies.file_role", "a_value_no_dictionary_sense", "resolved"),
        # Rules whose names spell no stage, role or format, read by what their values are.
        ("f7", "classifies.pack_rule_for_steps", "a_stage_from_a_pack", "resolved"),
        ("f7", "classifies.pack_rule_for_positions", "a_role_from_a_pack", "resolved"),
        ("f7", "classifies.pack_rule_for_kinds", "image_format", "resolved"),
    ]
    for claim in claims:
        connection.execute("INSERT INTO claims VALUES (?, ?, ?, ?)", list(claim))

    class Stand:
        """The one thing the tally reads of a catalog: its connection."""

    stand = Stand()
    stand.connection = connection  # type: ignore[attr-defined]
    run = RunUnits(
        paths={},
        roots={},
        parents={},
        units={"folder_a": "experiment_a", "subfolder": "experiment_a"},
        programs=frozenset(),
    )
    classes = Classes(
        raw_acquisition_stages=("acquisition",),
        analysis_stages=("analysis", "summary"),
        not_result_stages=("acquisition", "raw"),
        result_table_formats=("tabular_format",),
    )
    families = {"image": ("image_format",), "table": ("tabular_format",)}
    tallies = file_tallies(stand, run, classes, families, reading)  # type: ignore[arg-type]
    assert set(tallies) == {"experiment_a"}
    return tallies["experiment_a"]


def test_a_reading_published_as_conflicting_is_counted_apart_from_none_and_from_a_settled_one() -> (
    None
):
    """Unknown, unexamined and conflicting are three states: a file whose stage, format or role was
    published only as conflicting is counted as conflicting, a file no rule read as none or
    unknown, and a settled reading beats a conflicting one beside it. A claim is a reading of a
    kind by its value in the run's Dictionary, as the files page reads it, whatever its rule is
    called. Over the engine's own two tables, made by hand."""
    from ingence.api.v1.experiment_files import ClaimReading

    reading = ClaimReading(
        terms={
            "stage": frozenset(
                {"acquisition", "analysis", "an_unlisted_stage", "a_stage_from_a_pack"}
            ),
            "role": frozenset({"reference", "a_role_from_a_pack"}),
            "format": frozenset({"image_format", "tabular_format", "a_format_no_family_holds"}),
        },
        by_spelling=frozenset(),
        patterns=PATTERNS,
    )
    held = _hand_made_tally(reading)
    # Seven live files in the experiment: the deleted one, the folder and the stray are not counted.
    assert sum(held.stages.values()) == sum(held.kinds.values()) == 7
    assert dict(held.stages) == {
        "raw": 2,  # f1; f5, whose conflicting analysis reading does not unsettle its resolved one
        "analysis": 1,  # f2: an analysis stage beside an acquisition one is an analysis file
        "other": 2,  # f3; f7, by its value (its rule's name spells no stage)
        "conflicting": 1,  # f4
        "none": 1,  # f6: a stage reading not yet examined is no reading
    }
    assert dict(held.kinds) == {
        "image": 2,  # f1; f7, by its value
        "table": 1,  # f2
        "other": 1,  # f3: a known format no family holds
        "conflicting": 2,  # f4 published as conflicting; f5 read as two families
        "unknown": 1,  # f6: no format claim
    }
    assert dict(held.stage_state) == {"read": 5, "conflicting": 1, "none": 1}
    # f6's claim is spelled for a role but is no role of the Dictionary's: not a role read.
    assert dict(held.role_state) == {"read": 2, "conflicting": 1, "none": 4}


def test_where_the_dictionary_holds_no_sense_of_a_kind_its_claims_are_read_by_spelling() -> None:
    """The files page's fallback: a run whose Dictionary holds no role at all reads a role by the
    spelling of the predicate, and then only that."""
    from ingence.api.v1.experiment_files import ClaimReading

    reading = ClaimReading(
        terms={
            "stage": frozenset({"acquisition", "analysis"}),
            "role": frozenset(),
            "format": frozenset({"image_format", "tabular_format"}),
        },
        by_spelling=frozenset({"role"}),
        patterns=PATTERNS,
    )
    held = _hand_made_tally(reading)
    # f1 and f6 (spelled for a role, resolved) are read; f4's is published as conflicting; f7's
    # rule spells no role and nothing says its value is one.
    assert dict(held.role_state) == {"read": 2, "conflicting": 1, "none": 4}
    # The stage is still read by value only (the Dictionary holds stages here): f3's and f7's
    # values are not among them, though f3's rule spells stage; f1, f2 and f5 are read by theirs.
    assert dict(held.stage_state) == {"read": 3, "conflicting": 1, "none": 3}


# ---------------------------------------------------------------------------
# The pairing assumption, shown.
# ---------------------------------------------------------------------------


def test_followed_splits_into_pairs_a_person_confirmed_and_the_engines_pairs_assumed_right(
    world: World,
) -> None:
    before = by_folder(world.before)[HELA]["followed"]
    first = by_folder(world.first)[HELA]["followed"]
    last = by_folder(listing(world.project.path, limit=500))[HELA]["followed"]
    # Before any answer: the table named for the second recording follows it by its name, and the
    # table named exactly as the first is the naming guess left out. Nobody has confirmed a pair.
    assert (before["raw_found"], before["followed"]) == (6, 1)
    assert (before["by_confirmed"], before["assumed"]) == (0, 1)
    assert before["share"] == pytest.approx(1 / 6)
    assert before["said"] == (
        "1 / 6 raw files analyzed (0 by pairs you confirmed, 1 by Ingence's own pairs and links)."
    )
    # An answer on a pair the guess had left out: the first recording is followed now, through a
    # confirmed pair. Followed and confirmed rise by one; the assumed one stays.
    assert (first["raw_found"], first["followed"]) == (6, 2)
    assert (first["by_confirmed"], first["assumed"]) == (1, 1)
    assert first["said"] == (
        "2 / 6 raw files analyzed (1 by pairs you confirmed, 1 by Ingence's own pairs and links)."
    )
    # An answer on a pair whose raw file was already followed (through its name): the count
    # moves from assumed to confirmed and followed stays the same.
    assert (last["raw_found"], last["followed"]) == (6, first["followed"])
    assert (last["by_confirmed"], last["assumed"]) == (
        first["by_confirmed"] + 1,
        first["assumed"] - 1,
    )
    assert (last["by_confirmed"], last["assumed"]) == (2, 0)
    assert last["said"] == (
        "2 / 6 raw files analyzed (2 by pairs you confirmed, 0 by Ingence's own pairs and links)."
    )
    for held in (before, first, last):
        assert held["by_confirmed"] + held["assumed"] == held["followed"]


def test_every_row_s_split_adds_up_and_a_raw_file_none_follows_says_so(world: World) -> None:
    for row in listing(world.project.path, limit=500)["rows"]:
        held = row["followed"]
        assert held["by_confirmed"] + held["assumed"] == held["followed"], row["folder"]
        assert held["followed"] <= held["raw_found"], row["folder"]
    rows = by_folder(listing(world.project.path, limit=500))
    # Files named for a raw file are followed, and no person confirmed them: assumed right.
    assert rows[TUBULIN]["followed"]["followed"] == 5
    assert rows[TUBULIN]["followed"]["by_confirmed"] == 0
    assert rows[TUBULIN]["followed"]["assumed"] == 5
    # Nothing follows a recording: zero, over its denominator, never a missing number.
    assert rows[HEK]["followed"]["followed"] == 0
    assert rows[HEK]["followed"]["share"] == 0.0
    assert rows[HEK]["followed"]["said"].startswith("0 / 3 raw files analyzed (0 by pairs")


def test_no_raw_file_found_is_an_unknown_share_and_is_said_so_never_zero(world: World) -> None:
    rows = by_folder(listing(world.project.path, limit=500))
    for folder in (PLAN, BLOT):
        held = rows[folder]["followed"]
        assert held["raw_found"] == 0
        assert held["share"] is None
        assert held["said"] == "No raw files found in this experiment."
    body = listing(world.project.path)
    assert "Ingence assumes they are right" in body["pairing_assumption"]
    assert "Suggested pairs" in body["pairing_assumption"]
    # The words name what "assumed" takes in, not only proposed pairs.
    for taken in ("suggested pairs", "files made from a raw file", "files named after a raw file"):
        assert taken in body["pairing_assumption"]


def test_the_confirmed_count_is_the_one_completeness_applies_to_a_pair(world: World) -> None:
    from ingence.api.v1.experiments import confirmed_followed
    from ingence.completeness import completeness_of_run, confirmed_derivations
    from ingence.completeness.counting import classes
    from ingence.completeness.elsewhere import run_watermark
    from ingence.experiment_units import run_units
    from ingence.storage.catalog import open_catalog

    with open_catalog(world.project.path) as catalog:
        pairs = confirmed_derivations(world.project.path, run_watermark(catalog))
        units = run_units(catalog)
        found = confirmed_followed(catalog, pairs, units, classes())
        hela = next(unit for unit in set(units.units.values()) if units.paths[unit] == HELA)
    assert pairs == sorted(world.pairs)
    assert found == {hela: 2}
    # Never more than completeness counts followed, so a split cannot overshoot.
    rows = {row.folder: row for row in completeness_of_run(world.project.path, FINAL).experiments}
    assert found[hela] <= rows[HELA].followed
    # A pair whose later file is another recording, or the same file twice, follows nothing.
    with open_catalog(world.project.path) as catalog:
        assert confirmed_followed(catalog, [], units, classes()) == {}
        recording, table = world.pairs[0]
        assert confirmed_followed(catalog, [(table, recording)], units, classes()) == {}
        assert confirmed_followed(catalog, [(recording, recording)], units, classes()) == {}


# ---------------------------------------------------------------------------
# Filters, sorts and pages.
# ---------------------------------------------------------------------------


def test_the_filters_keep_the_experiments_asked_for_and_say_how_many(world: World) -> None:
    project = world.project.path
    people = listing(project, person="dana", limit=500)
    assert {row["folder"] for row in people["rows"]} == {HELA, HEK}
    assert people["matching"] == 2 and people["summary"]["experiments"] == 6
    assert [(item["value"], item["experiments"]) for item in people["people"]] == [
        ("dana", 2),
        ("sam", 4),
    ]
    both = listing(project, state="both", limit=500)
    assert {row["folder"] for row in both["rows"]} == {TUBULIN, UNDATED}
    named = listing(project, q="TUBULIN", limit=500)
    assert [row["folder"] for row in named["rows"]] == [TUBULIN]
    # The text is matched against the name as written, the shown name and the folder.
    assert [row["folder"] for row in listing(project, q="2024-02-05")["rows"]] == [HELA]
    assert [row["folder"] for row in listing(project, q="sam/pbs")["rows"]] == [UNDATED]
    combined = listing(project, person="sam", state="both", q="pbs")
    assert [row["folder"] for row in combined["rows"]] == [UNDATED]
    nothing = listing(project, person="dana", state="neither")
    assert nothing["rows"] == [] and nothing["matching"] == 0
    assert nothing["summary"]["experiments"] == 6


def test_each_sort_orders_the_rows_and_an_unknown_date_or_share_is_last_either_way(
    world: World,
) -> None:
    project = world.project.path

    def folders(**query: Any) -> list[str]:
        return [row["folder"] for row in listing(project, limit=500, **query)["rows"]]

    names = folders(sort="name")
    assert names == sorted(names, key=lambda folder: _shown(project, folder))
    assert folders(sort="name", order="desc") == list(reversed(names))
    ascending = folders(sort="files")
    sizes = [by_folder(listing(project, limit=500))[folder]["files"] for folder in ascending]
    assert sizes == sorted(sizes)
    assert folders(sort="files", order="desc")[0] == ascending[-1]
    # Dates: the dated ones in date order; the undated one last whichever way it runs.
    assert folders(sort="date") == [HELA, HEK, PLAN, TUBULIN, BLOT, UNDATED]
    assert folders(sort="date", order="desc") == [BLOT, TUBULIN, PLAN, HEK, HELA, UNDATED]
    # Completeness: the share of raw files followed; no raw file found (unknown) comes last.
    rising = folders(sort="completeness")
    # HEK 0 of 3, HELA 2 of 6, UNDATED 2 of 3, TUBULIN 5 of 5; the two unknown in folder order.
    assert rising == [HEK, HELA, UNDATED, TUBULIN, PLAN, BLOT]
    falling = folders(sort="completeness", order="desc")
    assert falling == [TUBULIN, UNDATED, HELA, HEK, PLAN, BLOT]
    # The same run always lists alike.
    assert folders(sort="completeness") == rising


def _shown(project: Path, folder: str) -> tuple[str, str]:
    row = by_folder(listing(project, limit=500))[folder]
    return (row["shown_name"].casefold(), folder.casefold())


def test_the_list_is_paged_and_every_experiment_is_counted(world: World) -> None:
    project = world.project.path
    first = listing(project, limit=4, offset=0, sort="date")
    rest = listing(project, limit=4, offset=4, sort="date")
    assert len(first["rows"]) == 4 and len(rest["rows"]) == 2
    assert first["matching"] == rest["matching"] == first["summary"]["experiments"] == 6
    assert [row["folder"] for row in first["rows"] + rest["rows"]] == [
        row["folder"] for row in listing(project, sort="date", limit=500)["rows"]
    ]
    assert (first["offset"], first["limit"]) == (0, 4)
    assert listing(project, offset=50)["rows"] == []
    assert listing(project)["limit"] == 50


def test_a_bad_parameter_is_refused_in_the_one_shape(world: World) -> None:
    for query in (
        {"sort": "size"},
        {"order": "up"},
        {"state": "whole"},
        {"limit": "0"},
        {"limit": "501"},
        {"offset": "-1"},
    ):
        status, body = ask(world.project.path, **query)
        assert status == HTTPStatus.BAD_REQUEST, query
        assert body["error"]["code"] == "bad_parameter", query
        assert body["error"]["message"], query


def test_the_response_says_what_an_experiment_is_and_that_themes_were_not_asked_for_here(
    world: World,
) -> None:
    body = listing(world.project.path)
    assert body["counted_as"].startswith("An experiment is a folder the experiment rules")
    assert "A folder inside another experiment is part of it." in body["counted_as"]
    # The pool is too small for lines of work: said in words, with no line on any row.
    assert body["lines"] == [] or all(item["label"] for item in body["lines"])
    for row in body["rows"]:
        assert row["theme"] is None or row["theme_label"]


# ---------------------------------------------------------------------------
# The files page and the list give a file to the same experiment.
# ---------------------------------------------------------------------------


def test_the_files_page_and_the_list_place_a_file_alike_where_nearest_and_highest_differ(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder the policy keeps as an experiment inside another one (a later rule of a project's
    own, or a policy that keeps both): the files page gives its files to the outer experiment,
    the highest, as completeness and the themes do, and not to the inner folder, the nearest."""
    from ingence import experiment_units
    from ingence.api.routes import groupings
    from ingence.api.routes.groupings import experiment_folders
    from ingence.storage.catalog import open_catalog

    real = experiment_units.policy_experiments
    with open_catalog(world.project.path) as catalog:
        inner_id, inner_path = catalog.query(
            "SELECT entry_id, relative_path FROM entries WHERE relative_path = ? "
            "AND entry_type = 'directory'",
            [f"{HELA}/analysis"],
        ).fetchone()
        outer_id = experiment_units.run_units(catalog)
        held_outer = next(
            unit for unit in set(outer_id.units.values()) if outer_id.paths[unit] == HELA
        )

    def nested(catalog: Any) -> dict[str, dict[str, str]]:
        return {
            **real(catalog),
            str(inner_id): {
                "entry_id": str(inner_id),
                "path": str(inner_path),
                "name": "analysis",
                "kind": "analysis",
            },
        }

    monkeypatch.setattr(experiment_units, "policy_experiments", nested)
    # The groupings' reading imported the function by name.
    monkeypatch.setattr(groupings, "policy_experiments", nested)
    with open_catalog(world.project.path) as catalog:
        nearest = experiment_folders(catalog)
        # The groupings' reading is the nearest: the inner folder holds its files.
        assert nearest.of(f"{HELA}/analysis") == str(inner_id)
        assert nearest.path_of(str(inner_id)) == inner_path
        run = experiment_units.run_units(catalog)
        # The shared rule is the highest: the outer experiment holds them.
        assert run.units[str(inner_id)] == held_outer
    body = listing(world.project.path, limit=500)
    assert set(by_folder(body)) == set(ALL), "the inner folder is no experiment of the list"
    outer_row = by_folder(body)[HELA]
    status, files = ask(world.project.path, "experiments/files", experiment=held_outer, limit=500)
    assert status == HTTPStatus.OK, files
    # The page lists every file of the outer experiment, the inner folder's among them.
    assert files["files"] == outer_row["files"] == 9
    paths = {row["path"] for row in files["rows"]}
    assert {
        f"{HELA}/analysis/hela_dapi_001.csv",
        f"{HELA}/analysis/Traj_hela_dapi_002.csv",
    } <= paths
    # The inner folder by itself is refused in words, naming the experiment whose files these are.
    status, refused = ask(world.project.path, "experiments/files", experiment=inner_id)
    assert status == HTTPStatus.NOT_FOUND
    assert refused["error"]["code"] == "not_found"
    assert HELA in refused["error"]["message"]


def test_the_files_page_counts_the_files_the_list_counts_for_every_experiment(
    world: World,
) -> None:
    for row in listing(world.project.path, limit=500)["rows"]:
        status, files = ask(world.project.path, "experiments/files", experiment=row["id"])
        assert status == HTTPStatus.OK, files
        assert files["files"] == row["files"], row["folder"]
        assert files["experiment"]["folder"] == row["folder"]


def test_files_beneath_an_installed_program_are_in_neither_the_list_nor_the_files_page(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    experiment = pool / "dana" / "2024-07-08_hela_gfp"
    for number in range(1, 4):
        _write(experiment / f"hela_gfp_{number:03d}.nd2")
    _write(experiment / "hela_gfp_001_results.csv", "a,b\n1,2\n")
    program = experiment / "viewer_tool"
    _write(program / "bin" / "viewer_tool")
    _write(program / "lib" / "sample_hela_001.nd2")
    _write(program / "lib" / "sample_hela_001_results.csv", "a,b\n1,2\n")
    _write(program / "README")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    run_inference(created, run_id=INFER, started_at="t")
    body = listing(created.path)
    (row,) = body["rows"]
    assert row["folder"] == "dana/2024-07-08_hela_gfp"
    assert (row["files"], row["raw_acquisitions"], row["result_tables"]) == (4, 3, 1)
    status, files = ask(created.path, "experiments/files", experiment=row["id"], limit=500)
    assert status == HTTPStatus.OK, files
    # The files page does not list the program's files as the experiment's: they are no one's.
    assert files["files"] == row["files"] == 4
    assert not [item for item in files["rows"] if "/viewer_tool/" in item["path"]]


# ---------------------------------------------------------------------------
# Lines of work: the list agrees with the themes the Years view draws.
# ---------------------------------------------------------------------------


def test_the_line_of_work_filter_and_the_list_agree_with_the_themes_the_years_view_draws(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sklearn")
    from fixtures.theme_pool import build_theme_project

    themed, _ = build_theme_project(tmp_path)
    status, drawn = ask(themed, "themes")
    assert status == HTTPStatus.OK, drawn
    years_ids = {row["id"] for row in drawn["experiments"]}
    body = listing(themed, limit=500)
    assert body["themes_computed"] is True
    listed_ids = {row["id"] for row in body["rows"]}
    # Years lists the experiments the themes route does, up to the ones that route did not list.
    assert years_ids <= listed_ids
    assert body["summary"]["experiments"] == body["matching"] == len(listed_ids)
    lines = body["lines"]
    assert lines
    assert sum(item["experiments"] for item in lines) <= body["summary"]["experiments"]
    chosen = lines[0]
    narrowed = listing(themed, line=chosen["value"], limit=500)
    assert narrowed["matching"] == chosen["experiments"] > 0
    assert {row["theme"] for row in narrowed["rows"]} == {chosen["value"]}
    assert {row["theme_label"] for row in narrowed["rows"]} == {chosen["label"]}
    each = {row["id"]: row["theme"] for row in body["rows"]}
    for row in drawn["experiments"]:
        assert each[row["id"]] == (None if row["theme"] is None else str(row["theme"])), row["id"]


# ---------------------------------------------------------------------------
# Topics: a person's named sets, read from the Record when asked, never by inference.
# ---------------------------------------------------------------------------

SAID_AT = "2026-06-01T10:00:00Z"
TOPIC_ACTOR = "a. person"


def _topic_copy(world: World, tmp_path: Path) -> Path:
    """A copy of the project, so a topic a test writes reaches no other test."""
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(world.project.path, copy)
    return copy


def _make_topics(project: Path, *topics: dict[str, Any]) -> None:
    from ingence.api.routes.overlay import prepare_overlay, write_prepared

    edits = [{"kind": "topic", **topic} for topic in topics]
    prepared = prepare_overlay(project, actor=TOPIC_ACTOR, occurred_at=SAID_AT, edits=edits)
    write_prepared(project, prepared, actor=TOPIC_ACTOR, occurred_at=SAID_AT)


def _member(experiment: str) -> dict[str, str]:
    return {"kind": "experiment", "id": experiment}


def test_without_a_topic_each_row_carries_none_and_the_response_says_nothing_of_them(
    world: World,
) -> None:
    body = listing(world.project.path, limit=500)
    assert body["topics"] == [] and body["topics_read"] is True and body["topics_said"] == ""
    assert body["topic_filter_said"] is None
    assert all(row["topics"] == [] for row in body["rows"])


def test_a_topic_over_two_experiments_is_on_each_of_them_and_filters_the_list_to_them(
    world: World, tmp_path: Path
) -> None:
    from ingence.api.v1.models import RESPONSE_MODELS
    from ingence.record.log import read_events

    project = _topic_copy(world, tmp_path)
    rows = by_folder(listing(project, limit=500))
    hela, tubulin, hek = rows[HELA]["id"], rows[TUBULIN]["id"], rows[HEK]["id"]
    _make_topics(
        project,
        {"name": "Cell lines", "members": [_member(hela), _member(tubulin)]},
        {"name": "Controls", "members": [_member(hela), _member(hek)]},
    )
    before = len(read_events(project, held=False))
    body = listing(project, limit=500)
    RESPONSE_MODELS["experiments"].model_validate(body)
    assert len(read_events(project, held=False)) == before, "asking writes nothing"
    # What the topics package says, by the same experiment identifiers this list serves.
    status, topics = ask(project, "topics", suggestions="0")
    assert status == HTTPStatus.OK
    said = {topic["name"]: topic for topic in topics["topics"]}
    assert set(said["Cell lines"]["experiment_ids"]) == {hela, tubulin}
    assert set(said["Controls"]["experiment_ids"]) == {hela, hek}
    ids = {name: topic["id"] for name, topic in said.items()}
    assert body["topics_read"] is True and body["topics_said"] == ""
    # Every row names the topics that hold it, by name and identifier, in name order.
    by_name = {
        folder: [(each["name"], each["id"]) for each in row["topics"]]
        for folder, row in by_folder(body).items()
    }
    assert by_name[HELA] == [("Cell lines", ids["Cell lines"]), ("Controls", ids["Controls"])]
    assert by_name[TUBULIN] == [("Cell lines", ids["Cell lines"])]
    assert by_name[HEK] == [("Controls", ids["Controls"])]
    assert all(by_name[folder] == [] for folder in (PLAN, BLOT, UNDATED))
    # The facets count each topic's experiments out of the run's, and say it in words.
    facets = {facet["name"]: facet for facet in body["topics"]}
    assert [facet["name"] for facet in body["topics"]] == ["Cell lines", "Controls"]
    for name in ("Cell lines", "Controls"):
        assert facets[name]["id"] == ids[name]
        assert facets[name]["experiments"] == 2 and facets[name]["read"] is True
        assert facets[name]["complete"] is True
        assert facets[name]["said"] == said[name]["said"]
        assert facets[name]["experiments"] == said[name]["experiments_count"]
    # The filter keeps the topic's experiments, and every count stays over the whole run.
    cell = listing(project, topic=ids["Cell lines"], limit=500)
    assert {row["folder"] for row in cell["rows"]} == {HELA, TUBULIN}
    assert cell["matching"] == 2 and cell["summary"]["experiments"] == 6
    assert cell["topic_filter_said"] == f"\u201cCell lines\u201d: {said['Cell lines']['said']}"
    assert [facet["experiments"] for facet in cell["topics"]] == [2, 2]
    # It combines with the other filters.
    both = listing(project, topic=ids["Cell lines"], state="both")
    assert [row["folder"] for row in both["rows"]] == [TUBULIN]
    controls = listing(project, topic=ids["Controls"], sort="date", limit=500)
    assert [row["folder"] for row in controls["rows"]] == [HELA, HEK]
    # A topic that does not stand is not an error the page cannot leave: no rows, and it says so.
    gone = listing(project, topic="nosuchtopic", limit=500)
    assert gone["rows"] == [] and gone["matching"] == 0 and gone["summary"]["experiments"] == 6
    assert gone["topic_filter_said"].startswith("This collection no longer exists")
    assert gone["topics_read"] is True and len(gone["topics"]) == 2


def test_a_topic_deleted_while_the_page_kept_its_filter_holds_nothing_and_says_it_no_longer_stands(
    world: World, tmp_path: Path
) -> None:
    project = _topic_copy(world, tmp_path)
    rows = by_folder(listing(project, limit=500))
    _make_topics(project, {"name": "Cell lines", "members": [_member(rows[HELA]["id"])]})
    [made] = listing(project)["topics"]
    assert [row["folder"] for row in listing(project, topic=made["id"])["rows"]] == [HELA]
    # The person deletes the topic (the Topics page does this through the overlay); the page asks
    # again with the filter it kept.
    _make_topics(project, {"id": made["id"], "remove": True})
    after = listing(project, topic=made["id"], limit=500)
    assert after["topics"] == [] and after["topics_read"] is True
    assert after["rows"] == [] and after["matching"] == 0
    assert after["summary"]["experiments"] == 6
    assert after["topic_filter_said"].startswith("This collection no longer exists")
    assert "deleted it or merged it" in after["topic_filter_said"]
    # Without the filter every experiment is listed, and none carries the topic.
    plain = listing(project, limit=500)
    assert len(plain["rows"]) == 6 and all(row["topics"] == [] for row in plain["rows"])


def test_a_topic_that_holds_nothing_is_said_so_and_is_not_an_unread_one(
    world: World, tmp_path: Path
) -> None:
    project = _topic_copy(world, tmp_path)
    rows = by_folder(listing(project, limit=500))
    # The person's own folder is no experiment: the member resolves to nothing, which is a
    # reading, not a failure to read.
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        person_folder = str(
            catalog.query("SELECT entry_id FROM entries WHERE relative_path = 'dana'").fetchone()[0]
        )
    _make_topics(
        project,
        {"name": "Mostly empty", "members": [_member(person_folder)]},
        {"name": "Some", "members": [_member(person_folder), _member(rows[HELA]["id"])]},
    )
    body = listing(project, limit=500)
    facets = {facet["name"]: facet for facet in body["topics"]}
    empty = facets["Mostly empty"]
    assert empty["read"] is True and empty["experiments"] == 0
    assert "resolves to no experiment" in empty["said"]
    assert facets["Some"]["experiments"] == 1 and facets["Some"]["complete"] is True
    narrowed = listing(project, topic=empty["id"])
    assert narrowed["rows"] == [] and narrowed["matching"] == 0
    assert narrowed["topic_filter_said"] == f"\u201cMostly empty\u201d: {empty['said']}"
    # Every experiment still counts: the topic keeps none out.
    assert narrowed["summary"]["experiments"] == 6


def test_topics_that_cannot_be_read_are_said_so_and_never_dropped_silently(
    world: World, tmp_path: Path
) -> None:
    from ingence.topics import TOPIC_ENGINES

    project = _topic_copy(world, tmp_path)
    hela = by_folder(listing(project, limit=500))[HELA]["id"]
    _make_topics(project, {"name": "Cell lines", "members": [_member(hela)]})
    topic_id = listing(project)["topics"][0]["id"]
    TOPIC_ENGINES.set_enabled("record_projection", enabled=False)
    try:
        body = listing(project, limit=500)
        asked = listing(project, topic=topic_id, limit=500)
        unknown = listing(project, topic="whatever", limit=500)
    finally:
        TOPIC_ENGINES.set_enabled("record_projection", enabled=True)
    # The list is served, the topics are said not read, and no row claims to hold one.
    assert body["topics_read"] is False and body["topics"] == []
    assert body["topics_said"].startswith("Ingence could not read your collections: ")
    assert len(body["rows"]) == 6 and all(row["topics"] == [] for row in body["rows"])
    # Filtering by a topic that cannot be read keeps nothing and says why: never the whole list.
    for narrowed in (asked, unknown):
        assert narrowed["rows"] == [] and narrowed["matching"] == 0
        assert narrowed["topic_filter_said"] == body["topics_said"]
        assert narrowed["summary"]["experiments"] == 6
    # And once the component is back, the same request reads the topic again.
    assert [row["folder"] for row in listing(project, topic=topic_id)["rows"]] == [HELA]


def test_a_run_that_applied_no_rules_says_its_topics_were_not_counted(tmp_path: Path) -> None:
    from ingence.storage.catalog import open_catalog

    pool = write_pool(tmp_path / "pool")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=CENSUS, started_at="t")
    with open_catalog(created.path) as catalog:
        folder = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE relative_path = ?", [HELA]
            ).fetchone()[0]
        )
    _make_topics(created.path, {"name": "Over a census", "members": [_member(folder)]})
    body = listing(created.path)
    # A census holds no experiments yet: the list is empty, and the topic's count is unknown.
    assert body["summary"]["experiments"] == 0 and body["rows"] == []
    assert body["topics_read"] is True
    assert body["topics_said"] == (
        "Collections are not counted yet. This scan has not applied the rules, so it has no "
        "experiments yet."
    )
    [facet] = body["topics"]
    assert facet["name"] == "Over a census"
    assert facet["experiments"] is None and facet["read"] is False and facet["complete"] is False
    assert facet["said"].startswith("Not read: this run applied no rules")
    narrowed = listing(created.path, topic=facet["id"])
    assert narrowed["rows"] == []
    assert narrowed["topic_filter_said"] == f"\u201cOver a census\u201d: {facet['said']}"


def test_a_topic_not_read_is_named_beside_read_ones_and_an_incomplete_one_is_marked(
    world: World, tmp_path: Path
) -> None:
    """With the lines of work not computed, a topic made only of a line could not be read, and one
    that adds a line to an experiment is read but may hold more: both say so, beside a topic read
    whole. No row says "none" for the unread one."""
    from ingence.themes import THEME_ENGINES

    project = _topic_copy(world, tmp_path)
    rows = by_folder(listing(project, limit=500))
    line = {"kind": "line", "experiments": [rows[HEK]["id"]], "words": ["hek293"]}
    _make_topics(
        project,
        {
            "name": "Cell lines",
            "members": [_member(rows[HELA]["id"]), _member(rows[TUBULIN]["id"])],
        },
        {"name": "Only a line", "members": [line]},
        {"name": "One and a line", "members": [_member(rows[HELA]["id"]), line]},
    )
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        body = listing(project, limit=500)
        unread = listing(
            project, topic=next(t["id"] for t in body["topics"] if t["name"] == "Only a line")
        )
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    facets = {facet["name"]: facet for facet in body["topics"]}
    assert body["topics_read"] is True and body["topics_said"] == ""
    # Read whole: a count, and complete.
    assert (facets["Cell lines"]["experiments"], facets["Cell lines"]["complete"]) == (2, True)
    assert facets["Cell lines"]["read"] is True
    # Read, but a member could not be: the count is "at least", and the sentence says so.
    assert (facets["One and a line"]["experiments"], facets["One and a line"]["read"]) == (1, True)
    assert facets["One and a line"]["complete"] is False
    assert "could not be read" in facets["One and a line"]["said"]
    # Not read at all: no count (never 0), and the engine's sentence.
    only = facets["Only a line"]
    assert only["read"] is False and only["experiments"] is None and only["complete"] is False
    assert only["said"].startswith("Not read: ")
    # It holds no row here, and the filter says why instead of listing none quietly.
    assert unread["rows"] == [] and unread["summary"]["experiments"] == 6
    assert unread["topic_filter_said"] == f"\u201cOnly a line\u201d: {only['said']}"
    # The rows the read topics hold still carry them; the unread one is on none.
    on_rows = {
        folder: [t["name"] for t in row["topics"]] for folder, row in by_folder(body).items()
    }
    assert on_rows[HELA] == ["Cell lines", "One and a line"]
    assert on_rows[TUBULIN] == ["Cell lines"]
    assert on_rows[HEK] == []
