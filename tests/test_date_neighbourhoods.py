"""A name's date is a neighbourhood, descriptive, never over a file's clock.

A date in a folder title is a general "date" neighbourhood. It differs from the file's actual
date, and a date in a file or folder name is more descriptive than exact. General date
information (a folder's date, a file name's date code) is factored into a file's date. So:

- a date in a file's or a folder's name gives a neighbourhood claim, and two dates give a span;
- a file with no date in its name draws on the nearest dated folder above it;
- the earliest credible time is still the file's own content or filesystem clock, never the name's.

Every name is invented. Works at the rules' level over whatever dates the reader gives.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

#: A modification time well after every date in the names below: 2 January 2025, UTC.
MODIFIED_S = 1_735_776_000


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (MODIFIED_S, MODIFIED_S))


def _claims(project: Project, rule_id: str) -> dict[str, dict[str, str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c JOIN entries e "
            "ON e.entry_id = c.subject WHERE c.rule_id = ? AND c.resolution_status = 'resolved'",
            [rule_id],
        ).fetchall()
    return {path: json.loads(value) for path, value in rows}


def test_a_name_date_is_a_neighbourhood_a_span_or_the_folder_s_and_never_the_clock(
    tmp_path: Path,
) -> None:
    pool = tmp_path / "pool"
    _write(pool / "20240301 kelt vorn" / "kelt_vorn_01.csv")
    _write(pool / "20240301 kelt vorn" / "deeper" / "zib_tracks.csv")
    _write(pool / "20240301 kelt vorn" / "kelt_20240305_02.csv")
    _write(pool / "brisa" / "brisa_2024-04-01_to_2024-04-09.csv")
    _write(pool / "brisa" / "trell_notes.csv")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")

    files = _claims(created, "date_neighbourhood_of_a_file")
    folders = _claims(created, "date_neighbourhood_of_a_folder")
    assert folders["20240301 kelt vorn"] == {
        "from": "2024-03-01",
        "to": "2024-03-01",
        "source": "name",
    }
    inherited = {"from": "2024-03-01", "to": "2024-03-01", "source": "folder"}
    assert files["20240301 kelt vorn/kelt_vorn_01.csv"] == {
        **inherited,
        "folder": "20240301 kelt vorn",
    }
    assert files["20240301 kelt vorn/deeper/zib_tracks.csv"]["source"] == "folder"
    assert files["20240301 kelt vorn/kelt_20240305_02.csv"] == {
        "from": "2024-03-05",
        "to": "2024-03-05",
        "source": "name",
    }  # its own code is nearer than its folder's date
    assert files["brisa/brisa_2024-04-01_to_2024-04-09.csv"] == {
        "from": "2024-04-01",
        "to": "2024-04-09",
        "source": "name",
    }  # two dates: a span
    assert "brisa/trell_notes.csv" not in files  # no date near it

    earliest = _claims(created, "earliest_credible_time")
    for path in files:
        assert earliest[path]["source"] in {"birth", "modification", "modified_before_born"}
        assert earliest[path]["at"].startswith("2025-01-02") or earliest[path]["source"] == "birth"
