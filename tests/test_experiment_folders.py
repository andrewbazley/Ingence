"""Where an experiment's folder is: by what it holds first, by its name second.

Experiment folders are identified first by the structure of their contents: whether a folder
holds raw data and the files made from that data. A folder's name is read only after its
contents.

The classes, over the grammar of names and declared formats, every name invented:

- a folder holding an instrument's acquisitions and files named for them is an experiment; one
  holding only acquisitions is one session's acquisitions; one holding only files named for
  acquisitions kept elsewhere is an analysis;
- a reanalysis that copied the movies into a subfolder, and a subfolder of other movies beside the
  experiment's own, are parts of the experiment above them;
- one experiment split into a raw folder and a tracks folder is bound at the folder holding both;
- a year or month folder of separate experiments is not one, and one stray movie at the top of many
  experiments does not make the top an experiment;
- the name rule takes the shallowest folder whose name holds a date and a word, as the tokeniser
  read them, and never a folder inside a dated one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import each_deviation, write_revision
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType, RecordEventKind
from ingence.grouping.experiment_links import experiment_units, propose_groups
from ingence.infer.folder_contents import name_parts
from ingence.infer.run import run_inference
from ingence.infer.subjects import FolderContents
from ingence.record.log import append_event
from ingence.record.questions import question_target
from ingence.rulebook.predicates.experiments import ExperimentFromContents, bound_above, bound_here
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pool(base: Path) -> Path:
    pool = base / "pool"
    dated = pool / "20190304 kelt droplet"
    for name in (
        "kelt_vorn_001.nd2",
        "kelt_vorn_002.nd2",
        "Drift_kelt_vorn_001.tif",
        "kelt_vorn_001_results.csv",
        "Drift_kelt_vorn_002.tif",
    ):
        _write(dated / name)
    _write(dated / "reanalysis" / "kelt_vorn_001.nd2")
    _write(dated / "reanalysis" / "Drift_kelt_vorn_001.tif")
    _write(dated / "beads" / "zib_beads_001.nd2")
    _write(dated / "20190305 second look" / "kelt_vorn_later_001.nd2")
    month = pool / "2019" / "Mar2019"
    _write(month / "brisa session" / "brisa_quolt_01.nd2")
    _write(month / "brisa session" / "Traj_brisa_quolt_01.nd2.csv")
    _write(month / "tamen run" / "tamen_sorn_001.nd2")
    _write(month / "tamen run" / "tamen_sorn_002.nd2")
    _write(month / "tamen run" / "01.nd2")
    _write(pool / "split study" / "raw" / "vorn_split_001.nd2")
    _write(pool / "split study" / "tracks" / "Traj_vorn_split_001.csv")
    _write(pool / "analysis only" / "Traj_tamen_sorn_001.csv")
    _write(pool / "notes" / "plan.txt")
    _write(pool / "stray_movie_zzz.nd2")
    repeat = pool / "20190311 kelt droplet repeat"
    for name in ("kelt_vorn_001.nd2", "kelt_vorn_002.nd2", "Drift_kelt_vorn_001.tif"):
        _write(repeat / name)
    for name in (
        "kelt_10mM-Ca_0mM-Mg_001.nd2",
        "kelt_10mM-Ca_0mM-Mg_002.nd2",
        "kelt_0mM-Ca_10mM-Mg_001.nd2",
        "kelt_10mM-Ca_0mM-Mg_001.csv",
    ):
        _write(pool / "kelt salts" / name)
    return pool


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("experiment-folders")
    pool = _pool(base)
    created = Project.create(base / "project", created_at="t", roots=[pool])
    # The clustering's own questions, in the ``each_deviation`` mode. Under the lattice, a
    # proposal whose core the lattice asks is asked once, by the lattice
    # (``test_group_lattice.py``).
    each_deviation(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


@pytest.fixture(scope="module")
def concluded(project: Project) -> dict[str, dict[str, str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.rule_id, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject WHERE c.rule_id IN "
            "('experiment_folder_from_contents', 'experiment_folder_from_dated_name') "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
    found: dict[str, dict[str, str]] = {"structure": {}, "name": {}}
    for path, rule, value in rows:
        found["structure" if rule.endswith("contents") else "name"][path] = value
    return found


def test_raw_and_what_is_named_for_it_in_one_folder_is_an_experiment(concluded: dict) -> None:
    assert concluded["structure"]["20190304 kelt droplet"] == "experiment"
    assert concluded["structure"]["2019/Mar2019/brisa session"] == "experiment"


def test_a_folder_of_acquisitions_alone_is_one_session_s_acquisitions(concluded: dict) -> None:
    assert concluded["structure"]["2019/Mar2019/tamen run"] == "acquisitions"


def test_files_named_for_acquisitions_kept_elsewhere_are_an_analysis(concluded: dict) -> None:
    assert concluded["structure"]["analysis only"] == "analysis"


def test_a_reanalysis_copy_and_a_subfolder_beside_the_raw_are_parts(concluded: dict) -> None:
    structure = concluded["structure"]
    for part in ("reanalysis", "beads", "20190305 second look"):
        assert f"20190304 kelt droplet/{part}" not in structure


def test_one_experiment_split_into_raw_and_tracks_is_bound_where_both_are(concluded: dict) -> None:
    structure = concluded["structure"]
    assert structure["split study"] == "experiment"
    assert "split study/raw" not in structure
    assert "split study/tracks" not in structure


def test_calendar_folders_and_a_stray_movie_at_the_top_make_no_experiment(concluded: dict) -> None:
    structure = concluded["structure"]
    for folder in (".", "2019", "2019/Mar2019", "notes"):
        assert folder not in structure


def test_the_name_rule_takes_the_shallowest_dated_folder_only(concluded: dict) -> None:
    names = concluded["name"]
    assert names == {
        "20190304 kelt droplet": "experiment",
        "20190311 kelt droplet repeat": "experiment",
    }


def test_a_repeat_sharing_rare_words_and_file_names_is_proposed_as_one_group(
    project: Project,
) -> None:
    with open_catalog(project.path) as catalog:
        raised = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e "
            "ON f.subject LIKE '%' || e.entry_id || '%' "
            "WHERE f.rule_id = 'experiments_may_belong_together' AND e.entry_type = 'directory'"
        ).fetchall()
    groups: dict[str, set[str]] = {}
    for folder, values in raised:
        assert len(values) == 1  # each member folder raises its one group
        groups.setdefault(values[0], set()).add(folder)
    members = sorted(sorted(group) for group in groups.values())
    # The repeat shares its files and rare words; an analysis of the raw session goes with it.
    assert ["20190304 kelt droplet", "20190311 kelt droplet repeat"] in members
    assert ["2019/Mar2019/tamen run", "analysis only"] in members


def test_replicates_of_one_condition_differ_by_a_counter_and_keep_quantities_in_place(
    project: Project,
) -> None:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'replicates_of_one_condition' AND c.resolution_status = 'resolved'"
        ).fetchall()
    key = dict(rows)
    salts, tamen, kelt = (
        "kelt salts/kelt_",
        "2019/Mar2019/tamen run/tamen_sorn_",
        "20190304 kelt droplet/",
    )
    assert key[f"{salts}10mM-Ca_0mM-Mg_001.nd2"] == key[f"{salts}10mM-Ca_0mM-Mg_002.nd2"]
    assert f"{salts}0mM-Ca_10mM-Mg_001.nd2" not in key  # a condition of one movie groups nothing
    assert f"{salts}10mM-Ca_0mM-Mg_001.csv" not in key  # its table is a series of its own
    assert key[f"{tamen}001.nd2"] == key[f"{tamen}002.nd2"]
    assert key[f"{kelt}kelt_vorn_001.nd2"] == key[f"{kelt}kelt_vorn_002.nd2"]
    assert key[f"{kelt}kelt_vorn_001.nd2"] != key[f"{kelt}Drift_kelt_vorn_001.tif"]


def _approved_group_keys(project: Project) -> dict[str, str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'experiments_approved_together' "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
    return dict(rows)


def test_an_approved_group_is_claimed_for_every_file_beneath_its_experiments(
    tmp_path: Path,
) -> None:
    """Approval at the group's level is inherited beneath: one yes groups every file."""
    pool = _pool(tmp_path)
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    each_deviation(write_revision(created.path))  # the clustering's question
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    first = "run_20260101T000002Z_222222222222"
    run_inference(created, run_id=first, started_at="t")
    assert _approved_group_keys(created) == {}  # the clustering alone groups nothing
    with open_catalog(created.path) as catalog:
        raised = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e "
            "ON f.subject LIKE '%' || e.entry_id || '%' "
            "WHERE f.rule_id = 'experiments_may_belong_together' AND e.entry_type = 'directory'"
        ).fetchall()
    value = {folder: values[0] for folder, values in raised}["20190304 kelt droplet"]
    append_event(
        created.path,
        kind=RecordEventKind.CONFIRM,
        actor="a person",
        occurred_at="2026-01-01T00:00:03Z",
        target=question_target("experiments_may_belong_together", value),
        against_run_id=first,
        payload={"raised": [value]},
    )
    run_inference(created, run_id="run_20260101T000004Z_444444444444", started_at="t")
    key = _approved_group_keys(created)
    beneath = {path for path in key if path.startswith(("20190304 kelt", "20190311 kelt"))}
    assert "20190304 kelt droplet/reanalysis/kelt_vorn_001.nd2" in beneath  # inherited below
    assert "20190311 kelt droplet repeat/kelt_vorn_002.nd2" in beneath
    assert len(set(key.values())) == 1
    assert set(key) == beneath  # nothing outside the approved group's experiments


def test_a_workbook_in_an_approved_experiment_is_its_member_and_its_parts_are_not(
    tmp_path: Path,
) -> None:
    """A dive promotes a workbook's parts, which share its path; the workbook is the experiment's
    file, and its charts and properties are evidence about it, never members."""
    from fixtures.office_packages import workbook
    from ingence.scan.deepen import run_deepen

    pool = _pool(tmp_path)
    workbook(
        pool / "20190304 kelt droplet" / "kelt_plan.xlsx",
        created="2019-03-04T09:00:00Z",
        modified="2019-03-05T10:00:00Z",
    )
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    each_deviation(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    first = "run_20260101T000003Z_333333333333"
    run_inference(created, run_id=first, started_at="t")
    with open_catalog(created.path) as catalog:
        parts = catalog.query(
            "SELECT count(*) FROM entries WHERE entry_kind = 'derived'"
        ).fetchone()[0]
        raised = catalog.query(
            "SELECT e.relative_path, f.raised FROM findings f JOIN entries e "
            "ON f.subject LIKE '%' || e.entry_id || '%' "
            "WHERE f.rule_id = 'experiments_may_belong_together' AND e.entry_type = 'directory'"
        ).fetchall()
    assert parts > 0  # the dive promoted the workbook's parts
    value = {folder: values[0] for folder, values in raised}["20190304 kelt droplet"]
    append_event(
        created.path,
        kind=RecordEventKind.CONFIRM,
        actor="a person",
        occurred_at="2026-01-01T00:00:04Z",
        target=question_target("experiments_may_belong_together", value),
        against_run_id=first,
        payload={"raised": [value]},
    )
    run_inference(created, run_id="run_20260101T000005Z_555555555555", started_at="t")
    with open_catalog(created.path) as catalog:
        members = catalog.query(
            "SELECT e.name, e.entry_kind FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'experiments_approved_together' "
            "AND c.resolution_status = 'resolved' AND e.relative_path LIKE '%kelt_plan.xlsx'"
        ).fetchall()
    assert members == [("kelt_plan.xlsx", "filesystem")]


def test_one_shared_word_links_nothing_and_two_repeats_group() -> None:
    rows = {
        "a": frozenset({"f:kelt", "w:vorn", "n:keltvorn001", "w:tif"}),
        "b": frozenset({"f:kelt", "w:vorn", "n:keltvorn001", "w:tif"}),
        "c": frozenset({"f:testing", "w:zib", "w:tif"}),
        "d": frozenset({"f:testing", "w:quolt", "w:tif"}),
        "e": frozenset({"w:tif", "w:sorn"}),
    }
    groups, beyond = propose_groups(rows)
    assert [group.members for group in groups] == [("a", "b")]
    assert beyond == 0


def test_no_cap_by_default_so_every_proposed_group_is_raised() -> None:
    """No cap applies by default, so every proposed group is raised.

    The Dashboard shows 50 at a time."""
    rows = {}
    for number in range(60):
        shared = {f"w:pair{number}", f"n:pairfile{number}"}
        rows[f"x{number}"] = frozenset(shared | {"w:common"})
        rows[f"y{number}"] = frozenset(shared | {"w:common"})
    groups, beyond = propose_groups(rows)
    assert len(groups) == 60
    assert beyond == 0


def test_the_cap_counts_the_groups_it_does_not_raise() -> None:
    rows = {}
    for number in range(6):
        shared = {f"w:pair{number}", f"n:pairfile{number}"}
        rows[f"x{number}"] = frozenset(shared | {"w:common"})
        rows[f"y{number}"] = frozenset(shared | {"w:common"})
    groups, beyond = propose_groups(rows, maximum_groups=4)
    assert len(groups) == 4
    assert beyond == 2


def test_the_outermost_evidence_policy_binds_condition_folders_under_a_dated_title() -> None:
    structure = {
        "d/dmso": "acquisitions",
        "d/drug": "acquisitions",
        "m/e1": "experiment",
        "b/x": "experiment",
        "b/y": "experiment",
    }
    named = ["d", "b"]
    assert experiment_units(structure, named, policy="outermost_evidence") == [
        "b/x",
        "b/y",
        "d",
        "m/e1",
    ]
    # A name comes first: the dated backup is one experiment.
    assert experiment_units(structure, named) == ["b", "d", "m/e1"]
    assert experiment_units(structure, named, policy="structure_then_name") == [
        "b/x",
        "b/y",
        "d/dmso",
        "d/drug",
        "m/e1",
    ]


def test_name_first_keeps_every_named_folder_and_structure_only_where_no_name_is() -> None:
    structure = {
        "dated kelt/raw": "acquisitions",
        "dated kelt/tracks": "analysis",
        "undated/brisa": "experiment",
        "undated/brisa/part": "acquisitions",
    }
    units = experiment_units(structure, ["dated kelt"], policy="name_first")
    assert units == ["dated kelt", "undated/brisa"]


def _contents(**counts: int) -> FolderContents:
    entry = Entry(
        entry_id="entry_" + "0" * 32,
        root_id="root_" + "0" * 32,
        relative_path="f",
        name="f",
        depth=1,
        entry_type=EntryType.DIRECTORY,
        first_seen_run="run_20260101T000001Z_111111111111",
        last_seen_run="run_20260101T000001Z_111111111111",
    )
    return FolderContents(entry=entry, **counts)


def test_a_folder_holding_fewer_of_its_own_movies_than_material_subfolders_holds_none() -> None:
    top = _contents(acquisitions_here=1, holding_children=5, linked_children=1, whole_children=5)
    assert not bound_here(top)
    kept = _contents(acquisitions_here=5, holding_children=1, linked_children=1)
    assert bound_here(kept)


def test_a_folder_of_complete_experiments_sharing_names_is_no_split_experiment() -> None:
    """Years of complete experiments share common movie names; that does not make one experiment."""
    years = _contents(holding_children=6, linked_children=6, whole_children=5)
    assert not bound_here(years)
    split = _contents(holding_children=3, linked_children=3, whole_children=0)
    assert bound_here(split)


def test_a_subfolder_is_a_part_where_its_parent_holds_the_material_directly() -> None:
    part = _contents(
        acquisitions_here=3,
        acquisitions_beneath=3,
        parent_material_here=50,
        parent_holding_children=1,
        parent_linked_children=1,
    )
    assert bound_above(part)
    assert ExperimentFromContents().evaluate(part, {}) is None
    separate = _contents(
        acquisitions_here=3,
        acquisitions_beneath=3,
        parent_material_here=1,
        parent_holding_children=40,
        parent_linked_children=2,
        parent_whole_children=30,
    )
    assert not bound_above(separate)


def test_a_name_is_cut_into_parts_at_every_separator_with_its_extension_left_off() -> None:
    assert name_parts("Traj_A_DMSO1hr001.nd2.csv", "csv") == ("traj", "a", "dmso1hr001", "nd2")
    assert name_parts("Mask-BG_Drift_kelt 1mM-ATP_001.tif", "tif") == (
        "mask",
        "bg",
        "drift",
        "kelt",
        "1mm",
        "atp",
        "001",
    )


def _item_pool(base: Path, *, raw_beside: bool) -> Path:
    """One run's outputs, one subfolder per movie, each written from one template."""
    pool = base / "pool"
    items = [f"keltmovie{number:02d}" for number in range(1, 25)]
    raw = pool / "brisa run" / "raw" if raw_beside else pool / "acquired"
    for item in items:
        _write(raw / f"{item}.nd2")
        _write(pool / "brisa run" / "movies" / item / f"Traj_{item}_tracks.csv")
        _write(pool / "brisa run" / "movies" / item / f"Plot_{item}_tracks.png")
    return pool


def _structure(base: Path, pool: Path) -> dict[str, str]:
    created = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(created.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'experiment_folder_from_contents' "
            "AND c.resolution_status = 'resolved'"
        ).fetchall()
    return dict(rows)


def test_one_subfolder_per_movie_from_one_template_is_one_unit_not_one_per_movie(
    tmp_path: Path,
) -> None:
    """A run's per-item outputs are its parent's, not one analysis per item."""
    elsewhere = _structure(tmp_path / "a", _item_pool(tmp_path / "a", raw_beside=False))
    assert not any(path.startswith("brisa run/movies/") for path in elsewhere)
    # The run's folder is itself bound further up (its outputs link to the raw beside it), so the
    # run's output folder stays its own unit.
    assert elsewhere.get("brisa run/movies") == "analysis"
    beside = _structure(tmp_path / "b", _item_pool(tmp_path / "b", raw_beside=True))
    assert not any(path.startswith("brisa run/movies") for path in beside)
    assert beside.get("brisa run") == "experiment"


def test_three_replicate_days_in_a_small_pool_are_proposed_together() -> None:
    """Three days of one condition are proposed together in a small pool.

    A feature that three of nine experiments share is kept, as it is at thirty-six: the
    common-word cut does not drop it because the pool is small."""
    days = {
        f"2019031{day} kelt vorn": frozenset(
            {"f:kelt", "f:vorn", "w:kelt", "w:vorn", "n:keltvorn001", "w:results", "p:"}
        )
        for day in (0, 1, 2)
    }
    others = {
        f"2019040{number} {word}": frozenset({f"f:{word}", f"w:{word}", "w:results", "p:"})
        for number, word in enumerate(("brisa", "zib", "trell", "quolt", "sorn", "dask"))
    }
    groups, _ = propose_groups({**days, **others})
    assert [group.members for group in groups] == [tuple(sorted(days))]


def test_a_word_every_experiment_holds_still_links_nothing_in_a_small_pool() -> None:
    """The cut keeps its purpose: a word nearly all of a small pool holds is no link."""
    rows = {
        f"e{number}": frozenset({"f:kelt", "w:kelt", f"f:own{number}", "p:"}) for number in range(9)
    }
    groups, _ = propose_groups(rows)
    assert groups == []


def test_stage_folders_and_a_shared_year_link_no_experiments(tmp_path: Path) -> None:
    """Two unrelated experiments under one year's folder, each split into raw, processed and
    analysis folders, are not proposed as one group named by a stage word.

    A stage word and the folder they sit in are not a link on their own."""
    pool = tmp_path / "pool"
    for folder, words in (("20200221 zib kelt", "zib_kelt"), ("20201123 vorn brisa", "vorn_brisa")):
        for number in (1, 2):
            base = pool / "2020" / folder
            for path in (
                base / "raw" / f"{words}_{number:03d}.nd2",
                base / "processed" / f"{words}_{number:03d}_processed.tif",
                base / "analysis" / f"{words}_{number:03d}_results.csv",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
    for number, word in enumerate(("trell", "quolt", "sorn", "dask", "pell", "morv", "gisk")):
        path = pool / f"2021040{number} {word} run" / f"{word}_001.nd2"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    each_deviation(write_revision(created.path))  # the clustering's own questions
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(created.path) as catalog:
        raised = catalog.query(
            "SELECT DISTINCT u.value FROM findings f, unnest(f.raised) AS u(value) "
            "WHERE f.rule_id = 'experiments_may_belong_together'"
        ).fetchall()
    assert raised == []
