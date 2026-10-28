"""The reading figure says what it counts.

Wherever the engine states how much of the names it read (the census's closing lines,
``ingence teach shapes``, the Dashboard's Coverage page), it says it in counts that name their
unit: of the word parts, how many were understood (a Dictionary sense), how many are unknown and
how many contested. Beside them it counts the parts read as something other than a word (numbers,
replicate or index labels, dates, extensions and the rest), which are neither understood nor
unknown. Every count is exact, and they sum to the parts read.

Every name here is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.project import Project
from ingence.scan.census import run_census
from ingence.scan.reading_figure import reading_figure, reading_said
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

#: Words: zorbit and kelt unknown (3 times), image and notes understood, sim contested between two
#: packs. Also: two numbers (005, 007), one index label (pos12), one date, three extensions.
NAMES = ("zorbit_image_2024-03-05_005.tif", "kelt_sim_pos12_007.csv", "zorbit_notes.txt")


def _project(tmp_path: Path) -> Project:
    pool = tmp_path / "zorbit"  # the root's own name is read too: one more unknown word
    pool.mkdir()
    for name in NAMES:
        (pool / name).write_text("x")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    settings = yaml.safe_load((project.path / "settings.yaml").read_text())
    settings.setdefault("dictionary", {}).update(domain_packs="all")
    (project.path / "settings.yaml").write_text(yaml.safe_dump(settings))
    project = Project.open(project.path)
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    return project


def test_the_figure_counts_each_kind_exactly_and_they_sum_to_the_parts_read(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        figure = reading_figure(catalog)
    assert figure["words"] == {
        "read": 7,
        "understood": 2,
        "unknown": 4,
        "contested": 1,
        "not_looked_up": 0,
    }
    assert figure["also"] == {"number": 2, "indexed": 1, "date": 1, "extension": 3}
    assert figure["parts"] == 14
    assert figure["parts"] == figure["words"]["read"] + sum(figure["also"].values())
    words = figure["words"]
    assert words["read"] == sum(value for key, value in words.items() if key != "read")


def test_the_figure_is_said_in_plain_words_with_its_units() -> None:
    said = reading_said(
        {
            "parts": 18_850,
            "words": {
                "read": 12_400,
                "understood": 4_170,
                "unknown": 7_900,
                "contested": 330,
                "not_looked_up": 0,
            },
            "also": {"number": 3_100, "indexed": 900, "date": 1_050, "extension": 1_400},
        }
    )
    assert said == (
        "Words: 12,400 read; 4,170 understood (a Dictionary meaning), 7,900 unknown, 330 "
        "tied. Also read: 3,100 numbers, 900 replicate or index labels, 1,050 dates, "
        "1,400 extensions. 18,850 parts of names in all."
    )


def test_the_census_closing_lines_and_the_coverage_page_state_it(tmp_path: Path) -> None:
    from ingence.api.routes.coverage import coverage

    project = _project(tmp_path)
    with open_catalog(project.path) as catalog:
        page = coverage(catalog, project.path)
        expected = reading_said(reading_figure(catalog))
    assert page["reading_said"] == expected
    assert expected.startswith("Words: 7 read; 2 understood (a Dictionary meaning), 4 unknown")
    rebound = CliRunner().invoke(app, ["index", "--rebind", "-p", str(project.path), "-q"])
    assert rebound.exit_code == 0, rebound.output
    assert expected in " ".join(rebound.output.split())
    shapes = CliRunner().invoke(app, ["teach", "shapes", "-p", str(project.path)])
    assert expected in " ".join(shapes.output.split())
    assert "Unbound:" not in shapes.output  # one word count, not two side by side
