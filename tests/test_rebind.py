"""Re-binding without re-walking: ``ingence index --rebind``.

An answer changes what a name means and nothing on disk, so the loop from an answer to its
effect needs no walk. A rebind replays the stored census through the census's own walker and
must be indistinguishable from a walk of the unchanged tree, to the digit, while touching no
root at all.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.scan.census import run_census
from ingence.storage.catalog import compare_runs, describe_differences, open_catalog


def _pool(base):
    pool = base / "pool"
    (pool / "exp1_ctrl" / "raw").mkdir(parents=True)
    (pool / "exp1_ctrl" / "analysis").mkdir(parents=True)
    (pool / "exp2_treated").mkdir(parents=True)
    (pool / "exp1_ctrl" / "raw" / "plate_01_zorbex_2024-03-01.nd2").write_bytes(b"a" * 100)
    (pool / "exp1_ctrl" / "raw" / "plate_02_zorbex.tif").write_bytes(b"b" * 200)
    (pool / "exp1_ctrl" / "analysis" / "zorbex_summary.csv").write_bytes(b"c" * 50)
    (pool / "exp2_treated" / "notes zorbex.txt").write_bytes(b"d" * 10)
    (pool / "stack.ome.tif").write_bytes(b"e" * 5)
    if sys.platform != "win32":
        # A link inside the root and an archive's link to another server: a rebind reads the
        # second's place from its stored text, as a walk does.
        (pool / "exp2_treated" / "inside.txt").symlink_to(
            pool / "exp2_treated" / "notes zorbex.txt"
        )
        (pool / "exp1_ctrl" / "raw" / "plate_03_zorbex.nd2").symlink_to("/??/UNC/archive/p.nd2")
    return pool


def _run(project, suffix: str, **options):
    report = run_census(
        project,
        run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
        started_at="2026-01-01T00:00:00Z",
        **options,
    )
    assert report.published
    return report


def _profile(project, run_id: str) -> tuple[str, str | None]:
    with open_catalog(project.path, run_id=run_id) as catalog:
        row = catalog.query("SELECT profile, parent_run_id FROM run").fetchone()
    return row[0], row[1]


def test_a_rebind_under_an_unchanged_vocabulary_is_the_census_to_the_digit(tmp_path) -> None:
    """Every table but the run's own record agrees with a walk of the unchanged tree."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _run(project, "1")
    walked = _run(project, "2")
    rebound = _run(project, "3", rebind=True)
    assert compare_runs(project.path, walked.run_id, rebound.run_id) == {}, describe_differences(
        project.path, walked.run_id, rebound.run_id
    )
    assert _profile(project, rebound.run_id) == ("rebind", walked.run_id)
    assert rebound.counts.entries == walked.counts.entries


def test_a_rebind_touches_no_root(tmp_path) -> None:
    """With the pool gone from its path, a walk fails and a rebind does not."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    walked = _run(project, "1")
    shutil.move(pool, tmp_path / "unmounted")
    rebound = _run(project, "2", rebind=True)
    assert rebound.counts.entries == walked.counts.entries
    assert rebound.counts.errors == 0
    assert compare_runs(project.path, walked.run_id, rebound.run_id) == {}


def test_a_rebind_after_an_answer_is_what_a_walk_after_it_would_find(tmp_path) -> None:
    """The loop's whole claim: the answer's effect, without the walk.

    Two identical projects answer the same question. One walks again and one re-binds, and
    every binding agrees. The label is one the Dictionary places nowhere, so the question is
    real (``test_answers.py``'s fixture, made twice).
    """
    from fixtures.revision_4 import write_revision
    from ingence.api.routes.decide import answer_question
    from ingence.infer.run import run_inference

    pool = tmp_path / "pool"
    for folder in ("first", "second"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"{folder}_plate_qq{index}.csv").write_text("a,b\n1,2\n")
    reports = {}
    for name, rebind in (("walked", False), ("rebound", True)):
        project = Project.create(tmp_path / name, created_at="t", roots=[pool])
        write_revision(project.path)
        _run(project, "1")
        run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
        with open_catalog(project.path) as catalog:
            answer_question(
                catalog,
                project.path,
                actor="robin",
                occurred_at="2026-09-18T15:00:00Z",
                rule_id="indexed_label_may_mark_replicates",
                raised="qq",
                kind=RecordEventKind.DEFINE,
                answer="biological_replicate",
                theme_kind="replicate",
            )
        reports[name] = (project, _run(project, "3", rebind=rebind))
    query = (
        "SELECT e.relative_path, c.normalized_value, b.resolution_status, b.term, b.sense_origin "
        "FROM dictionary_bindings b JOIN components c USING (component_id) "
        "JOIN entries e ON e.entry_id = b.entry_id ORDER BY ALL"
    )
    found = {}
    for name, (project, report) in reports.items():
        with open_catalog(project.path, run_id=report.run_id) as catalog:
            found[name] = catalog.query(query).fetchall()
    assert found["walked"] == found["rebound"]
    assert any(row[3] == "biological_replicate" for row in found["rebound"]), "it took effect"


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission bits do not deny the owner here",
)
def test_a_rebind_carries_what_the_census_could_not_list(tmp_path) -> None:
    """The stored tree is the one the census saw, errors and all."""
    pool = _pool(tmp_path)
    locked = pool / "exp2_treated" / "locked"
    locked.mkdir()
    (locked / "unseen.csv").write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    locked.chmod(0o000)
    try:
        walked = _run(project, "1")
        walked_again = _run(project, "2")
    finally:
        locked.chmod(0o755)
    rebound = _run(project, "3", rebind=True)
    assert walked.counts.errors == rebound.counts.errors == 1
    assert compare_runs(project.path, walked_again.run_id, rebound.run_id) == {}


def test_a_rebind_keeps_what_a_dive_read_and_reads_only_the_names_again(tmp_path) -> None:
    """The bytes are unchanged, so a dive's readings stand.

    A rebind that dropped them would turn the loop from an answer to its effect into a loss of
    every instrument clock and mention a dive had read, which a walk loses too and a rebind has
    no reason to.
    """
    from ingence.scan.deepen import run_deepen

    pool = _pool(tmp_path)
    (pool / "exp2_treated" / "protocol zorbex.txt").write_text("step one\nstep two\n" * 20)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _run(project, "1")
    dive = run_deepen(
        project,
        run_id="run_20260101T000002Z_222222222222",
        started_at="t",
        budget_seconds=60.0,
    )
    assert dive.examined > 0
    rebound = _run(project, "3", rebind=True)

    query_readings = (
        "SELECT entry_id, observer, key, value_text, value_integer FROM observations "
        "WHERE observer NOT IN ('census', 'census.organisation') ORDER BY ALL"
    )
    query_outcomes = "SELECT entry_id, scan_outcome, event_time_ns FROM entries ORDER BY ALL"
    with open_catalog(project.path, run_id="run_20260101T000002Z_222222222222") as catalog:
        readings = catalog.query(query_readings).fetchall()
        outcomes = catalog.query(query_outcomes).fetchall()
    with open_catalog(project.path, run_id=rebound.run_id) as catalog:
        assert catalog.query(query_readings).fetchall() == readings
        assert catalog.query(query_outcomes).fetchall() == outcomes
    assert readings, "the dive read something to carry"
    # And the names were read again: the rebind's bindings are the census's.
    census = "run_20260101T000001Z_111111111111"
    differences = compare_runs(project.path, census, rebound.run_id)
    assert "dictionary_bindings" not in differences
    assert "components" not in differences
