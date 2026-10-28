"""Coverage on the overview: the counts.

The overview says how far the reading reached, in three counts that add up. They are files read,
files described from a read file of the same series (by the series key the file list recorded for
both), and files neither read nor described, among them a file whose root stopped answering.
Every name is invented.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

from ingence.api import v1
from ingence.api.v1.overview import coverage_of
from ingence.core.project import Project
from ingence.core.settings import ExtractionSettings, Settings, SpendOrder
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.commit import read_pointer


def _png(width: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, 8, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk + b"\x00" * 200


def test_read_represented_and_not_reached_add_up_to_the_files(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    (pool / "stacks").mkdir(parents=True)
    for number in range(1, 7):
        (pool / "stacks" / f"frame_{number:02d}.png").write_bytes(_png(64))
    (pool / "notes.txt").write_text("a note about the stacks\n")
    gone = pool / "vanished.csv"
    gone.write_text("well,value\nA1,1\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            extraction=ExtractionSettings(enabled=True, spend_order=SpendOrder.SERIES_FIRST)
        ),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    os.remove(gone)  # out of reach by the time the pass opens it: not reached
    report = run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=120.0
    )
    assert report.left_to_series == 4
    assert report.not_reached == 1
    pointer = read_pointer(project.path)
    assert pointer is not None

    coverage = coverage_of(project.path, pointer.run_id)
    assert coverage.read == 3, "two frames of the series, and the note"
    assert coverage.represented == 4, "the four frames left to the two that were read"
    assert coverage.not_reached == 1, "the file that vanished"
    assert coverage.read + coverage.represented + coverage.not_reached == 8

    context = v1.V1Context(
        project=project.path,
        run_current=pointer.run_id,
        run_pinned=pointer.run_id,
        job_running=False,
    )
    status, payload = v1.dispatch(context, "overview", {})
    assert status == 200
    assert payload["coverage"] == {"read": 3, "represented": 4, "not_reached": 1}
    assert payload["run_id"] == pointer.run_id


def test_a_failed_and_a_skipped_file_are_in_none_of_the_three_counts(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "notes.txt").write_text("a note\n")
    (pool / "empty.csv").write_bytes(b"")  # skipped by policy: no bytes to read
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    report = run_deepen(
        project, run_id="run_20260101T000002Z_222222222222", started_at="t", budget_seconds=120.0
    )
    assert report.skipped == 1
    pointer = read_pointer(project.path)
    assert pointer is not None
    coverage = coverage_of(project.path, pointer.run_id)
    assert (coverage.read, coverage.represented, coverage.not_reached) == (1, 0, 0)
