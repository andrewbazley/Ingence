"""Streaming where a whole table or a whole file would be held.

Three places could hold a whole table or a whole file. The dive carries forward every observation,
error, edge and claim of the run it continues, as Python dictionaries. A manifest digests each file
by reading it whole and writing it out as hex. The reorganisation comparison lists every matched
pair before counting anything. Each reads in bounded pieces and gives exactly the result of reading
in one piece: the tests here compare the bounded reading at its smallest piece with the reading in
one piece.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ingence.core.ids import digest_file, digest_text
from ingence.core.project import Project
from ingence.scan.census import run_census
from ingence.storage.catalog import compare_runs
from ingence.storage.commit import run_path

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"
INFERENCE = "run_20260101T000003Z_333333333333"


@pytest.mark.parametrize("size", [0, 1, 7, 8, 9, 64, 1000])
def test_a_file_digested_in_pieces_is_the_digest_of_it_read_whole(tmp_path: Path, size) -> None:
    path = tmp_path / "part-00000.parquet"
    path.write_bytes(bytes(range(256)) * (size // 256) + bytes(range(size % 256)))
    whole = digest_text(path.read_bytes().hex())
    assert digest_file(path, chunk_bytes=8) == whole
    assert digest_file(path) == whole


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    (pool / "2024-03-05_zib_kelt" / "analysis").mkdir(parents=True)
    for index in range(1, 4):
        (pool / "2024-03-05_zib_kelt" / f"zib_kelt_0{index}.csv").write_text(
            f"replicate,value\n{index},3.{index}\n"
        )
    (pool / "2024-03-05_zib_kelt" / "analysis" / "measure_zib.py").write_text(
        "# reads zib_kelt_01.csv and writes plot_zib_kelt.csv\n"
    )
    (pool / "2024-03-05_zib_kelt" / "notes.txt").write_text("vorn buffer, three replicates\n")
    (pool / "brisa.md").write_text("# brisa\n1. mix\n")
    (pool / "brisa_copy.md").write_text("# brisa\n1. mix\n")
    return pool


def test_a_dive_carrying_in_pieces_publishes_what_it_did_in_one(tmp_path, monkeypatch) -> None:
    from ingence.infer.run import run_inference
    from ingence.scan import deepen
    from ingence.storage import catalog as catalog_module

    pool = _pool(tmp_path)
    first = Project.create(tmp_path / "one", created_at="t", roots=[pool])
    run_census(first, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    run_inference(first, run_id=DIVE.replace("2Z", "4Z"), started_at="2026-01-01T00:00:02Z")
    second_path = tmp_path / "two"
    shutil.copytree(first.path, second_path)
    second = Project.open(second_path)

    deepen.run_deepen(first, run_id=DIVE, started_at="2026-01-01T00:00:03Z", budget_seconds=60)
    monkeypatch.setattr(catalog_module, "BATCH_ROWS", 1)
    deepen.run_deepen(second, run_id=DIVE, started_at="2026-01-01T00:00:03Z", budget_seconds=60)

    shutil.copytree(run_path(second.path, DIVE), run_path(first.path, INFERENCE))
    assert compare_runs(first.path, DIVE, INFERENCE) == {}


def test_the_reorganisation_measured_in_pieces_is_the_one_measured_whole(
    tmp_path, monkeypatch
) -> None:
    from ingence.compare import structure
    from ingence.compare.match import Side
    from ingence.compare.report import measure

    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    (pool / "2024-03-05_zib_kelt").rename(pool / "kelt_moved")
    (pool / "brisa.md").rename(pool / "kelt_moved" / "brisa.md")
    run_census(project, run_id=DIVE, started_at="2026-01-01T00:00:02Z")
    sides = Side(project=project.path, run_id=CENSUS), Side(project=project.path, run_id=DIVE)

    whole = measure(*sides)
    monkeypatch.setattr(structure, "PAIRS_BATCH_ROWS", 1)
    pieces = measure(*sides)
    assert whole.structure == pieces.structure
    assert whole.basis == pieces.basis
    assert whole.summary == pieces.summary
    assert whole.structure.matched == whole.summary.matched_total > 0
