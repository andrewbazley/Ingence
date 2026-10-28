"""The listing route's answers, built offline from saved listings.

- A declared SHA-256 is identity entailed. Another declared method stays one tier below. The
  engine's own whole reading of a listed file computes SHA-256 beside BLAKE3 and MD5, so a
  declaration can be checked where the engine has the bytes.
- An attribute that a listing states is asked about, one question per attribute's name. No
  attribute is packed into a vocabulary, so users can clarify terms themselves.
- A folder whose declared copy times span more than the copy window (a day) is raised, with the
  span.

Every name is invented.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.listing.format import (
    ClockMeaning,
    DeclaredClock,
    DeclaredDigest,
    Listing,
    ListingHeader,
    ListingRow,
    parse_instant,
    write_listing,
)
from ingence.listing.remote import remote_whole_digests
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

_URI = "listing:kelt:K-0002"
_HEADER = ListingHeader(
    archive="kelt",
    identifier="K-0002",
    fetched_at="2026-01-02T03:04:05Z",
    converter="test",
    converter_version="1",
)
_DAY_NS = 86_400 * 1_000_000_000


def _project(tmp_path: Path, rows: list[ListingRow]) -> Project:
    project = Project.create(tmp_path / "project", created_at="t", listings=[_URI])
    write_listing(Listing(_HEADER, rows), Path(project.identity.roots[0].mount_path))
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return project


def _query(project: Project, sql: str) -> list[tuple[object, ...]]:
    with open_catalog(project.path) as catalog:
        return list(catalog.query(sql).fetchall())


def test_a_declared_sha256_groups_as_identical_naming_what_would_retract_it(
    tmp_path: Path,
) -> None:
    same = DeclaredDigest("sha256", "a" * 64, "Checksum")
    md5 = DeclaredDigest("md5", "b" * 32, "Sum")
    rows = [
        ListingRow(path="vorn/a.tif", size_bytes=5, digests=(same,)),
        ListingRow(path="vorn/b.tif", size_bytes=5, digests=(same,)),
        ListingRow(path="vorn/c.tif", size_bytes=5, digests=(md5,)),
        ListingRow(path="vorn/d.tif", size_bytes=5, digests=(md5,)),
    ]
    project = _project(tmp_path, rows)
    tiers = dict(
        _query(
            project,
            "SELECT rule_id, min(inference_tier) FROM claims WHERE rule_id IN "
            "('identical_by_declared_sha256', 'identical_by_declared_digest') GROUP BY rule_id",
        )
    )
    # Defeasible, naming its contradiction, until the engine observes SHA-256 itself.
    assert tiers == {
        "identical_by_declared_sha256": "defeasible",
        "identical_by_declared_digest": "defeasible",
    }


class _Served:
    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    def request(self, url: str, *, sink: object = None, **_: object) -> None:
        del url
        sink(self._blob)  # type: ignore[operator]


def test_the_engines_own_whole_reading_computes_sha256_beside_the_others() -> None:
    blob = b"kelt vorn zib " * 1000
    whole, served = remote_whole_digests(_Served(blob), "mem://kelt")  # type: ignore[arg-type]
    assert whole["sha256"] == hashlib.sha256(blob).hexdigest()
    assert whole["md5"] == hashlib.md5(blob, usedforsecurity=False).hexdigest()
    assert served == len(blob)


def test_an_attribute_a_listing_states_is_asked_about_never_packed_in(tmp_path: Path) -> None:
    rows = [
        ListingRow(path="vorn/a.tif", size_bytes=5, attributes=(("Genotype/Line", "zib"),)),
        ListingRow(path="vorn/b.tif", size_bytes=5, attributes=(("Genotype/Line", "brisa"),)),
        ListingRow(path="vorn/c.tif", size_bytes=5, attributes=(("Imaging method", "trell"),)),
    ]
    project = _project(tmp_path, rows)
    raised = _query(
        project,
        "SELECT u.value, count(*) FROM findings f, unnest(f.raised) AS u(value) "
        "WHERE f.rule_id = 'declared_attribute_to_clarify' GROUP BY u.value",
    )
    assert dict(raised) == {
        "what the attribute genotype_line says": 2,
        "what the attribute imaging_method says": 1,
    }


def test_declared_copies_spanning_more_than_the_window_are_flagged(tmp_path: Path) -> None:
    moment = parse_instant("2023-08-11T12:00:00Z")
    assert moment is not None

    def row(path: str, at: int) -> ListingRow:
        return ListingRow(
            path=path,
            size_bytes=5,
            clocks=(DeclaredClock(ClockMeaning.ARCHIVE_WRITE, at, "Last-Modified"),),
        )

    rows = [
        row("vorn/a.tif", moment),
        row("vorn/b.tif", moment + 3 * _DAY_NS),  # three days apart: the window fails
        row("zib/a.tif", moment),
        row("zib/b.tif", moment + 60 * 1_000_000_000),  # a minute apart: one copy
    ]
    project = _project(tmp_path, rows)
    raised = _query(
        project,
        "SELECT e.relative_path, f.raised, f.reason FROM findings f JOIN entries e "
        "ON e.entry_id = f.subject WHERE f.rule_id = 'declared_copy_window_exceeded'",
    )
    assert [(path, list(values)) for path, values, _ in raised] == [
        ("vorn", ["declared copies span more than the window"])
    ]
    assert "2023-08-11" in str(raised[0][2]) and "2023-08-14" in str(raised[0][2])
    copied = [
        json.loads(str(value))
        for (value,) in _query(
            project,
            "SELECT object_reference FROM claims "
            "WHERE rule_id = 'folder_copied_by_declared_clocks'",
        )
    ]
    assert [value["files"] for value in copied] == [2]  # zib is one copy, vorn is not
