"""Areas (0.4.0): each experiment's area and what the area's folder stands for, read from the name
by one rule as an assumption, answered by a person on ``area:<entry_id>``, and carried by the areas,
experiments, themes, overlap and ladder routes. Every name is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.api.v1 import V1Context, dispatch
from ingence.areas import (
    AREA_RULE_ID,
    area_of,
    area_rows,
    claimed_area_kinds,
    dominant_instrument_type,
    guess_area_kind,
    leading_kind,
    write_area_kind,
)
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.readouts import build_readouts
from ingence.record.targets import area_named_by, area_target, target_kind
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

CENSUS_RUN = "run_20260101T000001Z_aaaaaaaaaaaa"
INFER_RUN = "run_20260101T000002Z_bbbbbbbbbbbb"
INSTRUMENTS = frozenset({"quorlin", "brevane", "axon"})
AMBIGUOUS = frozenset({"axon"})


def _build(base: Path) -> Project:
    pool = base / "pool"
    for folder, names in {
        "Quenby/2021-03-04 drift": ["drift_01.csv", "drift_02.csv", "drift_summary.xlsx"],
        "Quenby/2021-06-10 drift repeat": ["drift_01.csv", "notes.txt"],
        "Quorlin room/2020-02-02 calibration": ["cal_01.tif", "cal_02.tif"],
        "2019/old plates": ["plate_a.csv"],
        "shared/scripts": ["measure.py"],
    }.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a,b\n1,2\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    run_inference(Project.open(project.path), run_id=INFER_RUN, started_at="t")
    build_readouts(project.path, INFER_RUN)
    return Project.open(project.path)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    """The project read with invented instrument spellings in place of the shipped table's,
    for the rule (through the predicate) and the pages alike."""
    import ingence.areas as areas_module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(areas_module, "instrument_spellings", lambda: INSTRUMENTS)
        patch.setattr(areas_module, "ambiguous_instrument_spellings", lambda: AMBIGUOUS)
        yield _build(tmp_path_factory.mktemp("areas"))


def _context(project: Path, run_id: str = INFER_RUN) -> V1Context:
    return V1Context(project=project, run_current=run_id, run_pinned=run_id, job_running=False)


def test_a_name_is_read_in_the_order_the_kinds_are_decided() -> None:
    assert guess_area_kind("2019", instruments=INSTRUMENTS).kind == "date"
    assert guess_area_kind("2021-03 drift", instruments=INSTRUMENTS).kind == "date"
    assert guess_area_kind("20210305_plates", instruments=INSTRUMENTS).kind == "date"
    assert guess_area_kind("Quorlin room", instruments=INSTRUMENTS).kind == "instrument"
    assert (
        guess_area_kind("brevane 2", instruments=INSTRUMENTS, ambiguous=AMBIGUOUS).kind
        == "instrument"
    )
    # Whole words only: a spelling inside a longer word is no match.
    assert (
        guess_area_kind("brevane2", instruments=INSTRUMENTS, ambiguous=AMBIGUOUS).kind == "folder"
    )
    assert (
        guess_area_kind("rebrevane", instruments=INSTRUMENTS, ambiguous=AMBIGUOUS).kind == "folder"
    )
    # A spelling that is also an ordinary word matches only beside other words.
    assert guess_area_kind("axon", instruments=INSTRUMENTS, ambiguous=AMBIGUOUS).kind == "folder"
    assert (
        guess_area_kind("axon rig", instruments=INSTRUMENTS, ambiguous=AMBIGUOUS).kind
        == "instrument"
    )
    assert guess_area_kind("Quenby", instruments=INSTRUMENTS).kind == "folder"
    assert guess_area_kind("Quenby", instruments=INSTRUMENTS, actors=["quenby"]).kind == "person"
    assert (
        guess_area_kind("plates", instruments=INSTRUMENTS, dominant_instrument_type="ND2").kind
        == "instrument"
    )
    # A date wins over an instrument's word, and nothing is ever guessed as a project.
    assert guess_area_kind("Quorlin 2019", instruments=INSTRUMENTS).kind == "date"
    assert all(guess.assumed for guess in [guess_area_kind("x", instruments=INSTRUMENTS)])
    assert dominant_instrument_type({"ND2": 6, "": 4}) == "ND2"
    assert dominant_instrument_type({"ND2": 4, "": 6}) is None
    assert dominant_instrument_type({"": 0}) is None
    assert leading_kind(["folder", "instrument", "instrument"]) == "instrument"
    assert leading_kind([]) is None


def test_the_area_is_the_folder_at_the_level_and_the_target_is_spelled_once() -> None:
    assert area_of("Quenby/2021-03-04 drift", 1) == "Quenby"
    assert area_of("Quenby/2021-03-04 drift", 2) == "2021-03-04 drift"
    assert area_of("Quenby", 1) == "Quenby"
    assert area_of(".", 1) is None and area_of("Quenby", None) is None
    assert area_target("entry_1") == "area:entry_1"
    assert area_named_by("area:entry_1") == "entry_1" and area_named_by("folder:x") is None
    assert target_kind("area:entry_1") == "area"


def test_the_rule_assumes_each_area_s_kind_at_the_abductive_tier(built: Project) -> None:
    with open_catalog(built.path, run_id=INFER_RUN) as catalog:
        claimed = claimed_area_kinds(catalog)
        tier = catalog.query(
            "SELECT DISTINCT inference_tier FROM claims WHERE rule_id = ?", [AREA_RULE_ID]
        ).fetchall()
        names = {
            str(name): str(entry)
            for entry, name in catalog.query(
                "SELECT entry_id, name FROM entries WHERE depth = 1 AND entry_type = 'directory'"
            ).fetchall()
        }
    assert [str(row[0]) for row in tier] == ["abductive"]
    assert claimed[names["Quorlin room"]] == "instrument"
    assert claimed[names["2019"]] == "date"
    assert claimed[names["Quenby"]] == "folder" and claimed[names["shared"]] == "folder"


def test_the_assumptions_list_holds_the_area_classes(built: Project) -> None:
    status, payload = dispatch(_context(built.path), "assumptions", {})
    assert status == 200, payload
    kinds = {item["id"]: item for item in payload["assumptions"] if item["kind"] == "area_kind"}
    assert len(kinds) == 3, sorted(kinds)
    for item in kinds.values():
        assert item["tier"] == "abductive" and item["id"].startswith(f"class:{AREA_RULE_ID}:")
        assert "assumed" in item["text"] and item["examples"]


def test_the_areas_route_reads_the_claims_then_a_person_s_answer(
    built: Project, tmp_path: Path
) -> None:
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    built = Project.open(copy)
    status, payload = dispatch(_context(built.path), "areas", {})
    assert status == 200, payload
    by_name = {row["name"]: row for row in payload["areas"]}
    assert (
        by_name["Quorlin room"]["kind"] == "instrument" and by_name["Quorlin room"]["kind_assumed"]
    )
    assert by_name["2019"]["kind"] == "date" and by_name["Quenby"]["kind"] == "folder"
    assert sum(row["files"] for row in payload["areas"]) == 9
    with open_catalog(built.path, run_id=INFER_RUN) as catalog:
        entry = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE name = 'Quenby' AND depth = 1"
            ).fetchone()[0]
        )
        event = write_area_kind(built.path, entry, "person", actor="Quenby")
        assert event.target == area_target(entry)
        rows = {
            row.name: row
            for row in area_rows(
                catalog, built.path, Project.open(built.path).settings.themes.area_level
            )
        }
    assert rows["Quenby"].kind == "person" and rows["Quenby"].kind_assumed is False
    assert rows["Quenby"].reason == "a person said so"
    status, payload = dispatch(_context(built.path), "areas", {})
    by_name = {row["name"]: row for row in payload["areas"]}
    assert by_name["Quenby"]["kind"] == "person" and not by_name["Quenby"]["kind_assumed"]
    with pytest.raises(ValueError, match="kind is one of"):
        write_area_kind(built.path, entry, "robot", actor="Quenby")


def test_experiments_themes_overlap_and_the_ladder_carry_the_area(built: Project) -> None:
    status, payload = dispatch(_context(built.path), "experiments", {"limit": ["50"]})
    assert status == 200, payload
    rows = {row["folder"]: row for row in payload["rows"]}
    assert rows, "the invented folders are experiments"
    for folder, row in rows.items():
        assert row["area"] == area_of(folder, 1)
        assert row["area_kind"] in ("person", "instrument", "date", "project", "folder")
        if row["area_kind"] != "person":
            assert row["person"] is None
    status, payload = dispatch(_context(built.path), "overlap", {"mode": ["by_year"]})
    assert status == 200, payload
    by_year = payload["by_year"]
    assert {row["name"] for row in by_year["areas"]} >= {"Quorlin room", "2019", "Quenby"}
    assert by_year["experiments"] and all("area_kind" in row for row in by_year["experiments"])
    assert payload["connections"] is None
    status, payload = dispatch(_context(built.path), "ladder", {})
    assert status == 200, payload
    words = {level["kind"]: level["name"] for level in payload["levels"]}
    # The areas here are mostly plain folders, so the level's word is the folder's.
    assert words["person"] == "folder"
    status, payload = dispatch(_context(built.path), "themes", {"limit": ["50"]})
    assert status == 200, payload
    for row in payload["experiments"]:
        assert row["area"] == area_of(row["folder"], 1)


def test_an_answer_after_the_readouts_reaches_every_route(built: Project, tmp_path: Path) -> None:
    """A person's answer on an area's kind is read over the stored kinds by the overview, the
    experiments, the themes, the overlap and the ladder, as /areas reads it."""
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    with open_catalog(copy, run_id=INFER_RUN) as catalog:
        entry = str(
            catalog.query(
                "SELECT entry_id FROM entries WHERE name = 'Quenby' AND depth = 1"
            ).fetchone()[0]
        )
    status, before = dispatch(_context(copy), "overview", {})
    assert status == 200 and {a["name"]: a["kind"] for a in before["areas"]}["Quenby"] == "folder"
    write_area_kind(copy, entry, "person", actor="Quenby")
    status, areas = dispatch(_context(copy), "areas", {})
    assert {a["name"]: a["kind"] for a in areas["areas"]}["Quenby"] == "person"
    status, overview = dispatch(_context(copy), "overview", {})
    assert status == 200, overview
    quenby = {a["name"]: a for a in overview["areas"]}["Quenby"]
    assert quenby["kind"] == "person" and quenby["kind_assumed"] is False
    status, experiments = dispatch(_context(copy), "experiments", {"limit": ["50"]})
    rows = [row for row in experiments["rows"] if row["area"] == "Quenby"]
    assert rows and all(row["area_kind"] == "person" and row["person"] == "Quenby" for row in rows)
    status, overlap = dispatch(_context(copy), "overlap", {"mode": ["by_year"]})
    assert {a["name"]: a["kind"] for a in overlap["by_year"]["areas"]}["Quenby"] == "person"
    status, themes = dispatch(_context(copy), "themes", {"limit": ["50"]})
    assert status == 200, themes
    assert all(
        row["area_kind"] == "person" for row in themes["experiments"] if row["area"] == "Quenby"
    )


def test_a_refused_area_class_is_a_plain_folder_not_guessed_again(
    built: Project, tmp_path: Path
) -> None:
    """A person's "wrong" on the class of area-rule claims that read a folder as an instrument
    leaves the folder a plain folder, not assumed, on the next reading."""
    import shutil

    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event, now
    from ingence.review.claim_classes import claim_class_target

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    append_event(
        copy,
        kind=RecordEventKind.REJECT,
        actor="Quenby",
        occurred_at=now(),
        target=claim_class_target(AREA_RULE_ID, "instrument"),
        against_run_id=INFER_RUN,
        payload={"of": "claims", "verdict": "wrong"},
    )
    with open_catalog(copy, run_id=INFER_RUN) as catalog:
        rows = {
            row.name: row
            for row in area_rows(catalog, copy, Project.open(copy).settings.themes.area_level)
        }
    assert rows["Quorlin room"].kind == "folder" and rows["Quorlin room"].kind_assumed is False
    assert rows["Quorlin room"].reason == "a person refused the reading"


def test_the_area_level_setting_binds_the_rule_s_parameter(built: Project, tmp_path: Path) -> None:
    """themes.area_level governs the rule's area_level through the project override path.

    A project at the default binds nothing and keeps the shipped digest."""
    import shutil

    from ingence.rulebook import project_rulebook, shipped_rulebook
    from ingence.rulebook.render import value_in_force

    copy = tmp_path / "project"
    shutil.copytree(built.path, copy)
    assert project_rulebook(copy).digest == shipped_rulebook().digest
    opened = Project.open(copy)
    themes = opened.settings.themes.model_copy(update={"area_level": 2, "person_level": 2})
    opened.save_settings(opened.settings.model_copy(update={"themes": themes}))
    bound = project_rulebook(copy)
    rule = bound.by_id(AREA_RULE_ID)
    assert rule is not None
    assert {p.name: value_in_force(p) for p in rule.parameters}["area_level"] == 2
    assert bound.digest != shipped_rulebook().digest
