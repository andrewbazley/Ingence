"""A listed file's bytes read where they are: the dive's own digests from range requests, a ZIP's
members from its tail, and the readings published as a run.

Served from memory by a stub client: nothing here reaches a server. Every name is invented.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from ingence.core.outcomes import IdentityScope
from ingence.core.project import Project
from ingence.listing import remote
from ingence.listing.format import Listing, ListingHeader, ListingRow, write_listing
from ingence.listing.http import Response
from ingence.listing.remote import (
    RemoteReading,
    publish_remote_readings,
    remote_quick_digest,
    remote_whole_digests,
    remote_zip_members,
)
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog


@dataclass
class _Served:
    """A client that serves one blob, as a server honouring ``Range`` would."""

    data: bytes
    asked: int = 0

    def request(self, url: str, *, byte_range: str | None = None, sink=None, method="GET"):  # type: ignore[no-untyped-def]
        del method
        self.asked += 1
        body = self.data
        if byte_range is not None:
            start, _, end = byte_range.partition("-")
            body = self.data[-int(end) :] if not start else self.data[int(start) : int(end) + 1]
        if sink is not None:
            sink(body)
            body = b""
        return Response(
            url=url,
            status=206 if byte_range else 200,
            body=body,
            etag=None,
            last_modified=None,
            content_length=len(body),
        )


@pytest.mark.parametrize("size", [0, 1, 1000, 70_000, 131_072, 131_073, 400_000])
def test_the_remote_sampled_digest_is_the_dives_own(size: int, tmp_path: Path) -> None:
    from ingence.extractors.base import ExtractionContext
    from ingence.identity.bytes import PROBE_BYTES, QuickDigest, WholeDigest

    assert remote.PROBE_BYTES == PROBE_BYTES
    blob = os.urandom(size)
    local = tmp_path / "zib.bin"
    local.write_bytes(blob)
    context = ExtractionContext(
        entry_id="entry_zib",
        path=local,
        relative_path="zib.bin",
        extensions=("bin",),
        size_bytes=size,
    )
    quick, read = remote_quick_digest(_Served(blob), "mem://zib", size)  # type: ignore[arg-type]
    assert quick == QuickDigest().compute(context).value  # type: ignore[union-attr]
    # The head alone up to twice the probe, as the dive reads it, and the tail beyond.
    assert read == min(size, PROBE_BYTES) + (PROBE_BYTES if size > 2 * PROBE_BYTES else 0)
    whole, served = remote_whole_digests(_Served(blob), "mem://zib")  # type: ignore[arg-type]
    assert whole["blake3_whole"] == WholeDigest().compute(context).value  # type: ignore[union-attr]
    assert whole["md5"] == hashlib.md5(blob, usedforsecurity=False).hexdigest()
    assert served == size


def test_a_remote_zip_lists_its_members_from_its_tail() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("vorn.zarr/.zattrs", b'{"kelt": 1}')
        archive.writestr("vorn.zarr/0/0.0", os.urandom(300_000))
        archive.writestr("vorn.zarr/0/0.1", b"")
    data = buffer.getvalue()
    members, _ = remote_zip_members(_Served(data), "mem://vorn.zip", len(data))  # type: ignore[arg-type]
    expected = {
        info.filename: (info.file_size, info.CRC)
        for info in zipfile.ZipFile(io.BytesIO(data)).infolist()
    }
    assert {member.name: (member.size, member.crc32) for member in members} == expected


def test_a_zip_whose_directory_is_in_its_tail_costs_one_request() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a", b"k")
    served = _Served(buffer.getvalue())
    remote_zip_members(served, "mem://a.zip", len(served.data))  # type: ignore[arg-type]
    assert served.asked == 1


def test_readings_are_published_as_a_run_the_digest_rules_read(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", listings=["listing:kelt:K-1"])
    header = ListingHeader(
        archive="kelt", identifier="K-1", fetched_at="t", converter="t", converter_version="1"
    )
    rows = [
        ListingRow(path="vorn/a.bin", size_bytes=3),
        ListingRow(path="vorn/b.bin", size_bytes=3),
    ]
    write_listing(Listing(header, rows), Path(project.identity.roots[0].mount_path))
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    with open_catalog(project.path) as catalog:
        ids = dict(catalog.query("SELECT relative_path, entry_id FROM entries").fetchall())
    readings = [
        RemoteReading(ids["vorn/a.bin"], "blake3_whole", "1", IdentityScope.WHOLE, "f" * 64, 3),
        RemoteReading(ids["vorn/b.bin"], "blake3_whole", "1", IdentityScope.WHOLE, "f" * 64, 3),
        RemoteReading(
            ids["vorn/b.bin"],
            "blake3_quick",
            "1",
            IdentityScope.SAMPLED,
            None,
            failure="the server said to stop",
        ),
    ]
    added = publish_remote_readings(
        project, readings, run_id="run_20260101T000002Z_222222222222", started_at="t"
    )
    assert added == 2
    with open_catalog(project.path) as catalog:
        rows_seen = catalog.query(
            "SELECT observer, identity_method, identity_scope, source_scope FROM observations "
            "WHERE key = 'identity.blake3_whole'"
        ).fetchall()
        errors = catalog.query("SELECT operation, failure_class FROM scan_errors").fetchall()
        profile = catalog.query("SELECT profile FROM run").fetchone()
    assert rows_seen == [("remote.blake3_whole", "blake3_whole", "whole", "content")] * 2
    assert errors == [("remote.blake3_quick", "access")]
    assert profile == ("remote",)
    assert json.loads(json.dumps(profile))  # the run is readable as any other


def test_a_remote_run_carries_what_the_census_left_out(tmp_path: Path) -> None:
    """Every later run carries the census's exclusions and recognised paths."""
    pool = tmp_path / "pool"
    (pool / ".hidden").mkdir(parents=True)
    (pool / "zib.txt").write_text("k")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    with open_catalog(project.path) as catalog:
        before = catalog.query("SELECT excluded_json FROM run").fetchone()
    publish_remote_readings(project, [], run_id="run_20260101T000002Z_222222222222", started_at="t")
    with open_catalog(project.path) as catalog:
        after = catalog.query("SELECT excluded_json, profile FROM run").fetchone()
    assert before[0] and after == (before[0], "remote")
