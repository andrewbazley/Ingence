"""A mention's shape placed against the pool, and a run's tally of them."""

from __future__ import annotations

import json

from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings, Settings
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.scan.mention_shapes import PoolRoot, placed, shapes_of, tally
from ingence.storage.catalog import open_catalog

ROOTS = (
    PoolRoot(path="/Volumes/Lab", case_insensitive=True),
    PoolRoot(path="/data/strict", case_insensitive=False),
)


def test_a_rooted_mention_under_a_root_of_the_pool_is_local_and_any_other_is_foreign():
    assert placed("/Volumes/Lab/plates/p1.csv", "absolute", ROOTS) == "absolute_local"
    assert placed("/volumes/lab/plates/p1.csv", "absolute", ROOTS) == "absolute_local"
    assert placed("/data/strict/x.csv", "absolute", ROOTS) == "absolute_local"
    assert placed("/DATA/strict/x.csv", "absolute", ROOTS) == "absolute_foreign"
    assert placed("/Volumes/LabOther/x.csv", "absolute", ROOTS) == "absolute_foreign"
    assert placed("C:\\Users\\x\\p1.nd2", "absolute", ROOTS) == "absolute_foreign"
    # Recorded without its leading separator, which the pattern cannot take unquoted.
    assert placed("Volumes/Lab/plates/p1.csv", "absolute", ROOTS) == "absolute_local"


def test_shapes_that_are_not_rooted_pass_through_and_a_reference_without_one_is_spelled():
    assert placed("out/fig1.png", "relative", ROOTS) == "relative"
    assert placed("lib/core.h", "code_include", ROOTS) == "code_include"
    # An extractor that read no shapes: the spelling decides.
    assert shapes_of(["/Volumes/Lab/a.csv", "b/c.txt", "d.txt"], [], ROOTS) == {
        "/Volumes/Lab/a.csv": "absolute_local",
        "b/c.txt": "relative",
        "d.txt": "bare",
    }


def test_a_tally_counts_paths_by_extension_and_shape_and_says_what_it_cannot_classify():
    mentions = [
        (
            "e1",
            "text",
            json.dumps(["a/b.csv", "c/d.h"]),
            json.dumps({"a/b.csv": "relative", "c/d.h": "code_include"}),
        ),
        ("e2", "text", json.dumps(["a/b.csv"]), json.dumps({"a/b.csv": "relative"})),
        # Projects saved by an earlier version hold paths and no shapes.
        ("e3", "text", json.dumps(["x/y.txt"]), None),
    ]
    extension_of = {"e1": "py", "e2": "py", "e3": "m"}
    counted = {
        (row.extension, row.shape): (row.mentions, row.files)
        for row in tally(mentions, extension_of)
    }
    assert counted == {
        ("m", "unclassified"): (1, 1),
        ("py", "code_include"): (1, 1),
        ("py", "relative"): (2, 2),
    }


def test_a_dive_records_each_mention_s_shape_and_tallies_them_in_its_run_record(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    local = (pool / "plates" / "p1.csv").as_posix()
    (pool / "plates").mkdir()
    (pool / "plates" / "p1.csv").write_text("a,b\n1,2\n")
    (pool / "analyse.py").write_text(
        "import os\n"
        "from lib import util  # lib/util.py\n"
        f'table = read("{local}")\n'
        'other = read("/somewhere/else/q.csv")\n'
        "figure = save(out/fig1.png)\n"
    )
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True, adapters=("text",))),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )
    with open_catalog(project.path) as catalog:
        (shapes,) = catalog.query(
            "SELECT o.value_structure FROM observations o JOIN entries e USING (entry_id) "
            "WHERE o.key = 'references.shapes' AND e.name = 'analyse.py'"
        ).fetchone()
        (mentions,) = catalog.query("SELECT mentions_json FROM run").fetchone()
    assert json.loads(shapes) == {
        local: "absolute_local",
        "/somewhere/else/q.csv": "absolute_foreign",
        "lib/util.py": "code_include",
        "out/fig1.png": "relative",
    }
    counted = {(row["extension"], row["shape"]): row["mentions"] for row in json.loads(mentions)}
    assert counted == {
        ("py", "absolute_foreign"): 1,
        ("py", "absolute_local"): 1,
        ("py", "code_include"): 1,
        ("py", "relative"): 1,
    }


def test_an_inference_carries_the_tally_of_the_mentions_it_carries(tmp_path):
    from ingence.infer.run import run_inference

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "notes.txt").write_text("see results/summary.csv\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True, adapters=("text",))),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=60.0
    )
    with open_catalog(project.path) as catalog:
        (dived,) = catalog.query("SELECT mentions_json FROM run").fetchone()
    run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    with open_catalog(project.path) as catalog:
        (inferred,) = catalog.query("SELECT mentions_json FROM run").fetchone()
    assert json.loads(inferred) == json.loads(dived) != []
