"""The partial rebind: an answer takes effect by reading only the names that could hold it.

An answer changes what one spelling means, so only the names that could hold it are read again:
every folder, and every file whose name, case folded and without separators, contains the
spelling. Every other name's reading is carried from the parent run. The result is checked to the
digit against the whole rebind, and the partial rebind is refused where carrying would mix two
tokenisers' readings in one run.

Every name here is invented.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.api.routes.decide import define_word
from ingence.core.project import Project
from ingence.scan.census import run_census
from ingence.storage.catalog import compare_runs, describe_differences, open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _pool(base: Path) -> Path:
    pool = base / "pool"
    for folder, names in {
        "kelt_series": ["vorn3q_raw.csv", "vorn3q_processed.csv", "brisa notes.txt"],
        "tamen": ["zeph_vorn3q_2024-03-01.tif", "pellan_raw.tif", "quolt-vorn3q-final.csv"],
        "other": ["unrelated_raw.tif", "unrelated_summary.csv"],
    }.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a,b\n1,2\n")
    return pool


def _run(project: Project, suffix: str, **options: object):
    report = run_census(
        project,
        run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
        started_at="2026-01-01T00:00:00Z",
        **options,
    )
    assert report.published
    return report


@pytest.fixture
def answered(tmp_path: Path) -> tuple[Project, Project]:
    """Two byte copies of one project, each holding one answer the census has not read yet."""
    pool = _pool(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _run(project, "1")
    with open_catalog(project.path) as catalog:
        define_word(
            catalog,
            project.path,
            actor="kelt",
            occurred_at="2026-01-01T00:00:00Z",
            word="vorn3q",
            answer="vorn three q",
            theme_kind="subject",
        )
    whole = tmp_path / "whole"
    partial = tmp_path / "partial"
    shutil.copytree(project.path, whole)
    shutil.copytree(project.path, partial)
    return Project.open(whole), Project.open(partial)


def test_a_partial_rebind_is_the_whole_rebind_to_the_digit(answered) -> None:
    whole, partial = answered
    full = _run(whole, "2", rebind=True)
    part = _run(partial, "2", rebind=True, spellings=("vorn3q",))
    # The two runs sit in two projects. Compare them in one.
    shutil.copytree(partial.path / "runs" / part.run_id, whole.path / "runs" / "partial-copy")
    differences = compare_runs(whole.path, full.run_id, "partial-copy")
    assert differences == {}, describe_differences(whole.path, full.run_id, "partial-copy")


def test_the_answer_reached_the_names_holding_it(answered) -> None:
    _, partial = answered
    _run(partial, "2", rebind=True, spellings=("vorn3q",))
    with open_catalog(partial.path) as catalog:
        bound = catalog.query(
            "SELECT count(DISTINCT b.entry_id) FROM dictionary_bindings b "
            "JOIN components c USING (component_id) "
            "WHERE c.normalized_value = 'vorn3q' AND b.resolution_status = 'resolved'"
        ).fetchone()[0]
    assert bound == 4


def test_the_run_record_names_the_spellings_it_read_again(answered) -> None:
    whole, partial = answered
    full = _run(whole, "2", rebind=True)
    part = _run(partial, "2", rebind=True, spellings=("vorn3q", "VORN3Q"))
    with open_catalog(whole.path, run_id=full.run_id) as catalog:
        assert catalog.query("SELECT rebind_spellings_json FROM run").fetchone()[0] == "null"
    with open_catalog(partial.path, run_id=part.run_id) as catalog:
        stated = catalog.query("SELECT rebind_spellings_json FROM run").fetchone()[0]
    assert stated == '["VORN3Q","vorn3q"]'


def test_a_partial_rebind_refuses_to_mix_two_tokenisers(answered) -> None:
    """Names read by another reading of glued runs are not carried into this one."""
    _, partial = answered
    settings = yaml.safe_load((partial.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {})["glued_runs"] = "whole"
    (partial.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    with pytest.raises(ValueError, match="re-bind them all"):
        run_census(
            Project.open(partial.path),
            run_id="run_20260101T000003Z_333333333333",
            started_at="t",
            rebind=True,
            spellings=("vorn3q",),
        )


def test_only_a_rebind_is_partial(answered) -> None:
    whole, _ = answered
    with pytest.raises(ValueError, match="only a rebind"):
        run_census(
            whole,
            run_id="run_20260101T000003Z_333333333333",
            started_at="t",
            spellings=("vorn3q",),
        )


def test_a_partial_rebind_refuses_names_read_with_another_vocabulary(answered, tmp_path) -> None:
    """Names carried from a census read before new formats and reagents shipped would keep the old
    readings. So anything but a person's answers changing the Dictionary refuses the partial
    rebind, and the whole rebind reads every name again."""
    from ingence.dictionary.laboratory_packs import install_pack

    _, partial = answered
    pack = tmp_path / "packs" / "zorn-lab"
    pack.mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        yaml.safe_dump({"name": "zorn-lab", "version": "1", "summary": "An invented laboratory."})
    )
    (pack / "assays.yaml").write_text(
        yaml.safe_dump(
            {
                "section": "zorn_assays",
                "summary": "Assays the invented laboratory runs.",
                "theme_kind": "technique",
                "entries": [{"term": "brisa_assay", "surfaces": ["brisa"]}],
            }
        )
    )
    install_pack(partial.path, pack)
    with pytest.raises(ValueError, match="another vocabulary"):
        run_census(
            Project.open(partial.path),
            run_id="run_20260101T000003Z_333333333333",
            started_at="t",
            rebind=True,
            spellings=("vorn3q",),
        )
    whole = run_census(
        Project.open(partial.path),
        run_id="run_20260101T000004Z_444444444444",
        started_at="t",
        rebind=True,
    )
    assert whole.published
