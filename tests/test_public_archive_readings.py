"""Four classes of reading found on public archives, each as its minimal synthetic case.

Each class was measured on stored projects, read only, and is pinned here as a test that must
pass. A short date that two field orders allow is not a date on its own. A name date after the
reading is not a clock. A key-value label is one indexed component. A compressed format's file is
that format. Every name is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.dictionary.normalize import AtomKind, Normaliser
from ingence.dictionary.store import DictionaryStore

READER = Normaliser(DictionaryStore.builtin())


def _dates(name: str) -> list[str]:
    return [atom.normalised for atom in READER.atoms(name) if atom.kind is AtomKind.DATE]


@pytest.mark.parametrize("name", ["22-06-07_kelt_zib_01.nd2", "vorn_31-12-22.tif"])
def test_a_short_date_two_orders_allow_is_no_date(name: str) -> None:
    assert _dates(name) == []


def test_a_name_date_after_the_reading_is_no_clock(tmp_path: Path) -> None:
    from ingence.core.project import Project
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "12-19-31_Take2 vorn.tif").write_bytes(b"k")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    with open_catalog(project.path) as catalog:
        clocks = catalog.query(
            "SELECT event_time_ns FROM entries WHERE name = '12-19-31_Take2 vorn.tif'"
        ).fetchall()
    assert clocks == [(None,)]


def test_a_key_value_label_is_one_indexed_component() -> None:
    kinds = {(atom.text, atom.kind) for atom in READER.atoms("sub-01_task-zib_run-02_bold")}
    assert ("run-02", AtomKind.INDEXED) in kinds


def test_a_compressed_formats_file_is_that_format(tmp_path: Path) -> None:
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "zib_kelt.nii.gz").write_bytes(b"k")
    (pool / "zib_kelt.nii").write_bytes(b"k")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    with open_catalog(project.path) as catalog:
        formats = dict(
            catalog.query(
                "SELECT e.name, c.object_reference FROM claims c JOIN entries e "
                "ON e.entry_id = c.subject WHERE c.rule_id IN "
                "('format_from_extension', 'format_from_extension_unless_contradicted')"
            ).fetchall()
        )
    assert formats["zib_kelt.nii.gz"] == formats["zib_kelt.nii"]


@pytest.mark.parametrize("name", ["WD1_15-02_WT_kelt", "vorn_28.02_zib", "kelt 3-11 brisa"])
def test_a_day_and_a_month_with_no_year_is_no_date(name: str) -> None:
    """A collection may write a day and a month with no year (``15-02``). That is not a date, and
    the missing year is never guessed."""
    assert _dates(name) == []
