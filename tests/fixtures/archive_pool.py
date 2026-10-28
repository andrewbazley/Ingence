"""An invented archive read from listings, the Years view's second kind of root.

The theme pool's plant (``fixtures.theme_pool``: six planted lines of work over dated experiment
folders) written as three listing roots, one study each, instead of folders: the first folder below
a listing root is no person's, so the project climbs the archive's ladder (archive, theme, study,
file). Every archive, accession, path and word is invented, and nothing here reaches a network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fixtures.theme_pool import ENGINE_SURFACES, theme_documents

#: The three studies, one listing root each; the plant's three invented people choose the study.
STUDIES = {"kipper": "S-0001", "tovald": "S-0002", "zandri": "S-0003"}
ARCHIVE_CENSUS_RUN = "run_20260101T000001Z_aaaaaaaaaaaa"
ARCHIVE_INFER_RUN = "run_20260101T000002Z_bbbbbbbbbbbb"


def build_archive_project(base: Path) -> Path:
    """The plant as three listings, indexed and inferred under revision 4 (a census and an
    inference, no dive): the project's path."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.listing.format import (
        ClockMeaning,
        DeclaredClock,
        Listing,
        ListingHeader,
        ListingRow,
        parse_instant,
        write_listing,
    )
    from ingence.scan.census import run_census

    plant = theme_documents()
    uris = [f"listing:bia:{identifier}" for identifier in STUDIES.values()]
    project = Project.create(base / "project", created_at="t", listings=uris)
    rows: dict[str, list[ListingRow]] = {person: [] for person in STUDIES}
    for order, (unit, written) in enumerate(sorted(plant.documents.items())):
        words = [ENGINE_SURFACES.get(word, word) for word in written]
        year = plant.years[unit]
        number = int(unit.rsplit("_", 1)[1]) if unit[-1].isdigit() else 0
        day = 1 + order % 28
        title_words = " ".join(words[:2])
        if year is None:
            folder = f"{title_words} {unit.replace('_', '')}"
        else:
            folder = f"{year}{(number % 12) + 1:02d}{day:02d} {title_words}"
        stem = "_".join(words[2:] or words[:1])
        moment = dt.datetime(year or 1985, (number % 12) + 1, day, 10, tzinfo=dt.UTC)
        stamp = parse_instant(moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
        clocks = (DeclaredClock(ClockMeaning.UPLOAD, stamp or 0, "Uploaded"),)
        for name in [f"{stem}_{index:02d}.nd2" for index in range(1, 4)] + [f"{stem}_results.csv"]:
            rows[plant.people[unit]].append(
                ListingRow(path=f"{folder}/{name}", size_bytes=5, clocks=clocks)
            )
    for declaration, (person, identifier) in zip(
        project.identity.roots, STUDIES.items(), strict=True
    ):
        header = ListingHeader(
            archive="bia",
            identifier=identifier,
            fetched_at="2026-01-02T03:04:05Z",
            converter="test",
            converter_version="1",
        )
        write_listing(Listing(header, rows[person]), Path(declaration.mount_path))
    write_revision(project.path)
    run_census(project, run_id=ARCHIVE_CENSUS_RUN, started_at="t")
    run_inference(project, run_id=ARCHIVE_INFER_RUN, started_at="t")
    return project.path
