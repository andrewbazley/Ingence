"""A placeholder in a listing's declared-link field is never a link.

A depositor writes ``-``, ``n/a``, ``none``, ``null`` or nothing in a source-image column to say
there is no source image. Such a value is not a link to a file named ``-``. So the BioStudies
converter sets them aside and says how many in the listing's notes, and the census's ingest
refuses any converter's placeholder link and counts it on the run record. A real file name in the
same column still links.

Every archive, accession, path and attribute here is invented, and nothing reaches a server.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.core.schema import RunRecord
from ingence.core.settings import ListingSettings, Settings
from ingence.listing.biostudies import convert
from ingence.listing.format import (
    DeclaredLink,
    LinkRelation,
    Listing,
    ListingHeader,
    ListingRow,
    is_placeholder,
    write_listing,
)
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

#: Every spelling of "there is none" that the engine treats as a placeholder, blank among them.
PLACEHOLDERS = (
    "-",
    "n/a",
    "N/A",
    "na",
    "none",
    "None",
    "null",
    "NULL",
    "nan",
    "NaN",
    "#N/A",
    "",
    "   ",
)


@pytest.mark.parametrize("written", PLACEHOLDERS)
def test_each_placeholder_is_one(written: str) -> None:
    assert is_placeholder(written)


@pytest.mark.parametrize("written", ["zib_1.zip", "img/vorn_02.tif", "nan_03.tif", "-zib.tif"])
def test_a_file_name_is_no_placeholder(written: str) -> None:
    assert not is_placeholder(written)


def _study(raw: Path, sources: list[str]) -> None:
    raw.mkdir(parents=True)
    (raw / "info.json").write_text(json.dumps({"httpLink": "https://example.invalid/S-XX7"}))
    (raw / "study.json").write_text(
        json.dumps(
            {
                "accno": "S-XX7",
                "section": {
                    "type": "Study",
                    "attributes": [{"name": "File List", "value": "masks.json"}],
                },
            }
        )
    )
    rows: list[dict[str, object]] = [{"path": "img/zib_1.zip", "size": 9, "type": "file"}]
    for number, source in enumerate(sources):
        rows.append(
            {
                "path": f"masks/zib_{number:02d}_mask.tif",
                "size": 3,
                "type": "file",
                "attributes": [{"name": "Source image association", "value": source}],
            }
        )
    (raw / "filelist__masks.json").write_text(json.dumps(rows))


def test_a_biostudies_placeholder_source_image_is_no_link_and_is_counted(tmp_path: Path) -> None:
    _study(tmp_path / "raw", [*PLACEHOLDERS, "zib_1.zip"])
    listing = convert("S-XX7", tmp_path / "raw", fetched_at="2026-01-02T03:04:05Z")
    linked = {row.path: row.links for row in listing.rows if row.links}
    real = f"masks/zib_{len(PLACEHOLDERS):02d}_mask.tif"
    assert linked == {
        real: (DeclaredLink(LinkRelation.MADE_FROM, "img/zib_1.zip", "Source image association"),)
    }
    # The value as written stays an attribute. Only the link is refused, and the notes say so.
    assert any(
        f"{len(PLACEHOLDERS)} values in a link's column were placeholders" in note
        for note in listing.header.notes
    )


def _header() -> ListingHeader:
    return ListingHeader(
        archive="kelt",
        identifier="K-0007",
        fetched_at="2026-01-02T03:04:05Z",
        converter="test",
        converter_version="1",
    )


def test_the_census_refuses_any_converter_s_placeholder_link_and_counts_it(
    tmp_path: Path,
) -> None:
    rows = [
        ListingRow(path="raw/zib_01.nd2", size_bytes=9),
        ListingRow(
            path="vorn/zib_01.tif",
            size_bytes=4,
            links=(DeclaredLink(LinkRelation.MADE_FROM, "raw/zib_01.nd2", "From"),),
        ),
        *(
            ListingRow(
                path=f"vorn/zib_{number + 2:02d}.tif",
                size_bytes=4,
                links=(DeclaredLink(LinkRelation.MADE_FROM, written, "From"),),
            )
            for number, written in enumerate(("-", "N/A", "null", " "))
        ),
    ]
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        listings=["listing:kelt:K-0007"],
        settings=Settings(listing=ListingSettings(declared_links="recorded")),
    )
    write_listing(Listing(_header(), rows), tmp_path / "project" / "listings" / "kelt" / "K-0007")
    report = run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:00Z"
    )
    assert report.published
    with open_catalog(project.path) as catalog:
        said = [
            json.loads(value)["said"][0]["target"]
            for (value,) in catalog.query(
                "SELECT value_structure FROM observations WHERE key = 'declared.link.made_from'"
            ).fetchall()
        ]
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    assert said == ["raw/zib_01.nd2"]
    assert dict(record_from_row(row, RunRecord).counts)["declared_link_placeholders"] == 4


def test_a_listed_file_named_like_a_placeholder_still_links(tmp_path: Path) -> None:
    """A value is a placeholder only where it names no listed path."""
    raw = tmp_path / "raw"
    _study(raw, ["NA"])
    rows = json.loads((raw / "filelist__masks.json").read_text())
    rows.append({"path": "img/NA", "size": 9, "type": "file"})
    (raw / "filelist__masks.json").write_text(json.dumps(rows))
    listing = convert("S-XX7", raw, fetched_at="2026-01-02T03:04:05Z")
    linked = {row.path: row.links for row in listing.rows if row.links}
    assert linked["masks/zib_00_mask.tif"][0].target == "img/NA"
