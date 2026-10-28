"""A listing as a root: the one format, the BioStudies conversion, the polite client, the census
reading a listing, and the declared rules.

Every archive, accession, path, attribute and digest here is invented, and every network answer is
a stub: nothing in this file reaches a server.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from ingence.core.ids import CaseSensitivity
from ingence.core.outcomes import SourceScope
from ingence.core.project import Project, RootUnreachableError, is_listing_root
from ingence.core.settings import ListingSettings, Settings
from ingence.listing import http as listing_http
from ingence.listing.biostudies import convert
from ingence.listing.format import (
    ClockMeaning,
    DeclaredClock,
    DeclaredDigest,
    DeclaredLink,
    LinkRelation,
    Listing,
    ListingError,
    ListingHeader,
    ListingRow,
    parse_instant,
    parse_listing_uri,
    read_listing,
    root_uri_of,
    write_listing,
)
from ingence.listing.http import ArchiveRefusedError, PoliteClient, RangeRefusedError
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

_URI = "listing:kelt:K-0001"


def _header(archive: str = "kelt", identifier: str = "K-0001") -> ListingHeader:
    return ListingHeader(
        archive=archive,
        identifier=identifier,
        fetched_at="2026-01-02T03:04:05Z",
        converter="test",
        converter_version="1",
    )


def _at(text: str) -> int:
    found = parse_instant(text)
    assert found is not None
    return found


# -- the format --------------------------------------------------------------------------------


def test_a_listing_round_trips_in_path_order_and_says_which_root_it_is(tmp_path: Path) -> None:
    rows = [
        ListingRow(
            path="vorn/zib_02.tif",
            size_bytes=7,
            clocks=(DeclaredClock(ClockMeaning.UPLOAD, _at("2021-05-06T07:08:09Z"), "Uploaded"),),
            digests=(DeclaredDigest("md5", "a" * 32, "Sum"),),
            attributes=(("Stain", "kelt"), ("Stain", "brisa")),
            links=(DeclaredLink(LinkRelation.MADE_FROM, "vorn/zib_01.tif", "From"),),
            locator="https://example.invalid/vorn/zib_02.tif",
        ),
        ListingRow(path="vorn/zib_01.tif"),
    ]
    write_listing(Listing(_header(), rows), tmp_path)
    again = read_listing(tmp_path)
    assert again.header.root_uri == _URI
    assert [row.path for row in again.rows] == ["vorn/zib_01.tif", "vorn/zib_02.tif"]
    assert again.rows[1] == rows[0]
    assert again.rows[0].size_bytes is None  # a size nobody stated is absent
    written = (tmp_path / "rows.jsonl").read_bytes()
    write_listing(again, tmp_path)
    assert (tmp_path / "rows.jsonl").read_bytes() == written  # canonical: the same bytes


@pytest.mark.parametrize(
    "path", ["/vorn/a.tif", "vorn//a.tif", "vorn/../a.tif", "./a.tif", "vorn\\a.tif", ""]
)
def test_a_path_that_is_not_relative_and_plain_is_refused(tmp_path: Path, path: str) -> None:
    with pytest.raises(ListingError):
        write_listing(Listing(_header(), [ListingRow(path=path)]), tmp_path)


def test_a_path_listed_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ListingError, match="twice"):
        write_listing(Listing(_header(), [ListingRow(path="a"), ListingRow(path="a")]), tmp_path)


def test_a_digest_a_clock_and_a_link_are_read_only_in_their_own_shapes(tmp_path: Path) -> None:
    write_listing(Listing(_header(), []), tmp_path)
    for bad in (
        {"path": "a", "digests": [{"method": "md5", "value": "XYZ", "said_by": "s"}]},
        {"path": "a", "digests": [{"method": "md5", "value": "ab", "said_by": "s"}]},
        {"path": "a", "digests": [{"method": "crc", "value": "ab", "said_by": "s"}]},
        {"path": "a", "clocks": [{"meaning": "birth", "at": "2021-01-01T00:00:00Z"}]},
        {
            "path": "a",
            "clocks": [{"meaning": "upload", "at": "2021-01-01T00:00:00", "said_by": "s"}],
        },
        {"path": "a", "links": [{"relation": "related_to", "target": "b", "said_by": "s"}]},
        {"path": "a", "size": -1},
    ):
        (tmp_path / "rows.jsonl").write_text(json.dumps(bad) + "\n", "utf-8")
        with pytest.raises(ListingError):
            read_listing(tmp_path)


def test_a_listing_root_is_named_by_its_archive_and_identifier() -> None:
    assert parse_listing_uri("listing:biostudies:S-XX1") == ("biostudies", "S-XX1")
    assert root_uri_of("kelt", "K-0001") == _URI
    for other in ("file:///x", "listing:Kelt:K", "listing:kelt:", "listing:kelt:a/b", "listing"):
        assert parse_listing_uri(other) is None
    assert ClockMeaning.ARCHIVE_WRITE.kind.value == "copy"
    assert ClockMeaning.UPLOAD.kind.value == "copy"
    assert ClockMeaning.DEPOSITOR_MODIFIED.kind.value == "modification"


# -- the BioStudies conversion -----------------------------------------------------------------


def _study_raw(raw: Path) -> None:
    raw.mkdir(parents=True)
    (raw / "info.json").write_text(
        json.dumps(
            {
                "httpLink": "https://example.invalid/S-XX1",
                "released": 1_600_000_000_000,
                "modified": 1_600_000_100_000,
            }
        ),
        "utf-8",
    )
    (raw / "study.json").write_text(
        json.dumps(
            {
                "accno": "S-XX1",
                "attributes": [{"name": "Title", "value": "Vorn shapes"}],
                "section": {
                    "type": "Study",
                    "subsections": [
                        {
                            "type": "Annotations",
                            "attributes": [{"name": "File List", "value": "masks.json"}],
                        },
                        [
                            {
                                "type": "Study Component",
                                "attributes": [{"name": "File List", "value": "images.json"}],
                            }
                        ],
                        {
                            "type": "Extra",
                            "files": [[{"path": "notes.txt", "size": 3, "type": "file"}]],
                        },
                    ],
                },
            }
        ),
        "utf-8",
    )
    (raw / "filelist__images.json").write_text(
        json.dumps(
            [
                {
                    "path": "img/zib_1.zip",
                    "size": 100,
                    "type": "file",
                    "attributes": [{"name": "Channel 1", "value": "kelt"}],
                },
                {"path": "img/zib_2.zip", "size": 200, "type": "file"},
            ]
        ),
        "utf-8",
    )
    (raw / "filelist__masks.json").write_text(
        json.dumps(
            [
                {
                    "path": "zib_1_mask.zip",
                    "size": 10,
                    "type": "file",
                    "attributes": [{"name": "Source image association", "value": "zib_1.zip"}],
                },
                {
                    "path": "zib_9_mask.zip",
                    "size": 11,
                    "type": "file",
                    "attributes": [{"name": "Source image association", "value": "zib_9.zip"}],
                },
                {"path": "packed", "size": 5, "type": "directory"},
            ]
        ),
        "utf-8",
    )


def test_a_study_converts_with_its_attributes_links_and_nothing_invented(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _study_raw(raw)
    listing = convert("S-XX1", raw, fetched_at="2026-01-02T03:04:05Z")
    by_path = {row.path: row for row in listing.rows}
    assert set(by_path) == {
        "img/zib_1.zip",
        "img/zib_2.zip",
        "zib_1_mask.zip",
        "zib_9_mask.zip",
        "packed",
        "notes.txt",
    }
    assert listing.header.root_uri == "listing:biostudies:S-XX1"
    assert ("Title", "Vorn shapes") in listing.header.attributes
    assert ("Channel 1", "kelt") in by_path["img/zib_1.zip"].attributes
    # No clock, no digest: a file list states neither, and the ETag is not one.
    assert all(not row.clocks and not row.digests for row in listing.rows)
    # A source image named by its file name alone resolves to the one listed file of that name;
    # a name nothing listed stays as written.
    assert by_path["zib_1_mask.zip"].links == (
        DeclaredLink(LinkRelation.MADE_FROM, "img/zib_1.zip", "Source image association"),
    )
    assert by_path["zib_9_mask.zip"].links[0].target == "zib_9.zip"
    assert ("biostudies.type", "directory") in by_path["packed"].attributes
    assert by_path["img/zib_1.zip"].locator == "https://example.invalid/S-XX1/Files/img/zib_1.zip"


def test_the_archives_write_time_is_a_copy_clock_only_where_the_headers_were_asked(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    _study_raw(raw)
    (raw / "heads.json").write_text(
        json.dumps({"img/zib_1.zip": "Fri, 11 Aug 2023 12:09:35 GMT", "img/zib_2.zip": None}),
        "utf-8",
    )
    by_path = {row.path: row for row in convert("S-XX1", raw, fetched_at="t").rows}
    (clock,) = by_path["img/zib_1.zip"].clocks
    assert clock.meaning is ClockMeaning.ARCHIVE_WRITE
    assert clock.said_by == "Last-Modified"
    assert clock.at_ns == _at("2023-08-11T12:09:35Z")
    assert by_path["img/zib_2.zip"].clocks == ()


# -- the polite client -------------------------------------------------------------------------


class _Answer(io.BytesIO):
    def __init__(self, body: bytes, status: int, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.status = status
        self.headers = headers

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def test_the_client_pauses_names_itself_logs_every_request_and_stops_when_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, dict[str, str]]] = []
    answers = iter(
        [
            _Answer(b"hello", 200, {"ETag": '"e1"', "Last-Modified": "x", "Content-Length": "5"}),
            urllib.error.HTTPError("https://kelt.invalid/b", 503, "busy", {}, None),  # type: ignore[arg-type]
        ]
    )

    def opened(request: object, timeout: float) -> object:
        del timeout
        asked.append((request.full_url, dict(request.header_items())))  # type: ignore[attr-defined]
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    sleeps: list[float] = []
    monkeypatch.setattr(listing_http.urllib.request, "urlopen", opened)
    monkeypatch.setattr(listing_http.time, "sleep", sleeps.append)
    log: list[dict[str, object]] = []
    client = PoliteClient(user_agent="kelt agent", pause_seconds=0.5, log=log.append)
    first = client.request("https://kelt.invalid/a")
    assert first.body == b"hello" and first.etag == '"e1"'
    with pytest.raises(ArchiveRefusedError):
        client.request("https://kelt.invalid/b")
    with pytest.raises(ArchiveRefusedError, match="asked us to stop"):
        client.request("https://kelt.invalid/c")  # never sent
    assert [url for url, _ in asked] == ["https://kelt.invalid/a", "https://kelt.invalid/b"]
    assert all(headers.get("User-agent") == "kelt agent" for _, headers in asked)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 0.5
    assert [entry["status"] for entry in log] == [200, 503]
    assert log[0]["bytes"] == 5 and log[0]["etag"] == '"e1"' and log[1]["stopped"] is True


def test_a_range_answered_with_the_whole_file_is_refused_unread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read: list[int] = []

    class _Counted(_Answer):
        def read(self, size: int | None = -1) -> bytes:
            read.append(size or -1)
            return super().read(size)

    whole = _Counted(b"x" * 1000, 200, {})
    monkeypatch.setattr(listing_http.urllib.request, "urlopen", lambda request, timeout: whole)
    client = PoliteClient(user_agent="a", pause_seconds=0.0)
    with pytest.raises(RangeRefusedError):
        client.request("https://kelt.invalid/big", byte_range="0-9")
    assert read == []  # not a byte read
    assert whole.closed  # and the answer closed at once


# -- the census reads a listing ----------------------------------------------------------------


def _listing_project(tmp_path: Path, rows: list[ListingRow], **settings: object) -> Project:
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        listings=[_URI],
        settings=Settings(listing=ListingSettings(**settings)) if settings else None,  # type: ignore[arg-type]
    )
    write_listing(Listing(_header(), rows), Path(project.identity.roots[0].mount_path))
    return project


def _index(project: Project, suffix: str = "1", **options: object) -> None:
    report = run_census(
        project,
        run_id=f"run_20260101T00000{suffix}Z_{suffix * 12}",
        started_at="2026-01-01T00:00:00Z",
        **options,  # type: ignore[arg-type]
    )
    assert report.published


def test_a_listing_root_is_declared_in_the_project_and_needs_its_listing(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", listings=[_URI])
    (declaration,) = project.identity.roots
    assert is_listing_root(declaration)
    assert declaration.case_sensitivity is CaseSensitivity.SENSITIVE
    assert (
        Path(declaration.mount_path)
        == tmp_path.resolve() / "project" / "listings" / "kelt" / "K-0001"
    )
    with pytest.raises(RootUnreachableError, match="--fetch"):
        run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")


_ROWS = [
    ListingRow(
        path="vorn/zib_01.tif",
        size_bytes=4096,
        attributes=(("Genotype/Line", "kelt-1"),),
        links=(DeclaredLink(LinkRelation.MADE_FROM, "raw/zib_01.nd2", "From"),),
        digests=(DeclaredDigest("md5", "b" * 32, "Sum"),),
    ),
    ListingRow(path="raw/zib_01.nd2", size_bytes=900),
]


def test_the_census_reads_a_listing_with_its_statements_declared(tmp_path: Path) -> None:
    project = _listing_project(tmp_path, _ROWS)
    _index(project)
    with open_catalog(project.path) as catalog:
        entries = catalog.query(
            "SELECT relative_path, entry_type, size_bytes, modified_ns, mode, stat_fingerprint "
            "FROM entries ORDER BY relative_path"
        ).fetchall()
        declared = catalog.query(
            "SELECT key, source_scope, observer, value_text FROM observations "
            "WHERE starts_with(key, 'declared.') ORDER BY key"
        ).fetchall()
    assert [(path, kind) for path, kind, *_ in entries] == [
        (".", "directory"),
        ("raw", "directory"),
        ("raw/zib_01.nd2", "file"),
        ("vorn", "directory"),
        ("vorn/zib_01.tif", "file"),
    ]
    assert all(row[3:] == (None, None, None) for row in entries)
    assert entries[4][2] == 4096
    assert ("declared.attribute.genotype_line", "declared", "listing.kelt", "kelt-1") in declared
    assert ("declared.digest.md5", "declared", "listing.kelt", "b" * 32) in declared
    # The links are the answer key and are withheld by default.
    assert not [row for row in declared if row[0].startswith("declared.link.")]


def test_declared_links_enter_the_index_only_when_the_setting_records_them(
    tmp_path: Path,
) -> None:
    project = _listing_project(tmp_path, _ROWS, declared_links="recorded")
    _index(project)
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query(
            "SELECT value_structure FROM observations WHERE key = 'declared.link.made_from'"
        ).fetchall()
    assert json.loads(row[0])["said"][0]["target"] == "raw/zib_01.nd2"


def test_a_rebind_of_a_listing_keeps_its_statements_and_invents_nothing(tmp_path: Path) -> None:
    project = _listing_project(tmp_path, _ROWS)
    _index(project, "1")
    _index(project, "2", rebind=True)
    with open_catalog(project.path) as catalog:
        clocks = catalog.query(
            "SELECT count(*) FROM entries WHERE modified_ns IS NOT NULL OR mode IS NOT NULL"
        ).fetchone()
        kept = catalog.query(
            "SELECT count(*) FROM observations WHERE source_scope = ?",
            [SourceScope.DECLARED.value],
        ).fetchone()
    assert clocks == (0,)
    assert kept == (2,)


def test_a_listing_fetched_again_without_a_file_tombstones_it(tmp_path: Path) -> None:
    project = _listing_project(tmp_path, _ROWS)
    _index(project, "1")
    write_listing(Listing(_header(), _ROWS[:1]), Path(project.identity.roots[0].mount_path))
    _index(project, "2")
    with open_catalog(project.path) as catalog:
        gone = catalog.query(
            "SELECT relative_path FROM entries WHERE is_deleted ORDER BY relative_path"
        ).fetchall()
    assert gone == [("raw",), ("raw/zib_01.nd2",)]


def test_a_file_named_with_spaces_is_asked_for_by_its_encoded_name(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _study_raw(raw)
    (raw / "filelist__images.json").write_text(
        json.dumps([{"path": "img/zib one+two.zip", "size": 1, "type": "file"}]), "utf-8"
    )
    (row,) = [
        row for row in convert("S-XX1", raw, fetched_at="t").rows if row.path.startswith("img")
    ]
    assert row.locator == "https://example.invalid/S-XX1/Files/img/zib%20one%2Btwo.zip"


def test_one_listing_that_cannot_be_fetched_is_recorded_and_the_rest_are_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from http.client import InvalidURL

    from ingence.listing import fetch as listing_fetch

    fetched: list[str] = []

    class _Source:
        archive = "biostudies"
        version = "1"

        def fetch(self, identifier, directory, *, client, settings):  # type: ignore[no-untyped-def]
            del client, settings
            if identifier == "S-XX1":
                raise InvalidURL("a space in a path")
            fetched.append(identifier)
            listing = Listing(_header("biostudies", identifier), [ListingRow(path="a")])
            write_listing(listing, directory)
            return listing

    class _Sources:
        def __contains__(self, name: object) -> bool:
            return name == "biostudies"

        def get(self, name: str) -> _Source:
            del name
            return _Source()

    monkeypatch.setattr(listing_fetch, "LISTING_SOURCES", _Sources())
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        listings=["listing:biostudies:S-XX1", "listing:biostudies:S-XX2"],
    )
    outcome = listing_fetch.fetch_listing_roots(project)
    assert fetched == ["S-XX2"]
    assert outcome.fetched == [("listing:biostudies:S-XX2", 1)]
    assert outcome.failed[0][0] == "listing:biostudies:S-XX1"
    assert "InvalidURL" in outcome.failed[0][1]


def test_a_source_image_is_read_under_the_spellings_depositors_use(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _study_raw(raw)
    (raw / "filelist__images.json").write_text(
        json.dumps(
            [
                {"path": "img/zib_1.zip", "size": 1, "type": "file"},
                {
                    "path": "img/zib_1_cells.tif",
                    "size": 1,
                    "type": "file",
                    "attributes": [{"name": "source_image", "value": "img/zib_1.zip"}],
                },
                {
                    "path": "img/zib_1_nuclei.tif",
                    "size": 1,
                    "type": "file",
                    "attributes": [{"name": "Source Image", "value": "zib_1.zip"}],
                },
                {
                    "path": "img/zib_1_notes.txt",
                    "size": 1,
                    "type": "file",
                    "attributes": [{"name": "Source Name", "value": "zib_1.zip"}],
                },
            ]
        ),
        "utf-8",
    )
    by_path = {row.path: row for row in convert("S-XX1", raw, fetched_at="t").rows}
    assert by_path["img/zib_1_cells.tif"].links[0].target == "img/zib_1.zip"
    assert by_path["img/zib_1_nuclei.tif"].links[0].target == "img/zib_1.zip"
    assert by_path["img/zib_1_notes.txt"].links == ()  # a source's name is not a source image


def test_two_censuses_of_one_cached_listing_are_identical(tmp_path: Path) -> None:
    """A listing root is read the same way every time: one listing read twice gives one Index."""
    from ingence.storage.catalog import compare_runs, describe_differences

    project = _listing_project(tmp_path, _ROWS)
    _index(project, "1")
    _index(project, "2")
    first, second = "run_20260101T000001Z_111111111111", "run_20260101T000002Z_222222222222"
    assert compare_runs(project.path, first, second) == {}, describe_differences(
        project.path, first, second
    )


def test_a_path_listed_as_a_file_and_holding_files_is_a_folder(tmp_path: Path) -> None:
    rows = [
        ListingRow(path="vorn.zarr", size_bytes=10, attributes=(("Kind", "zarr"),)),
        ListingRow(path="vorn.zarr/0/0.0", size_bytes=3),
        ListingRow(path="vorn.zarr/.zattrs", size_bytes=2),
    ]
    project = _listing_project(tmp_path, rows)
    _index(project)
    with open_catalog(project.path) as catalog:
        kinds = dict(catalog.query("SELECT relative_path, entry_type FROM entries").fetchall())
        kept = catalog.query(
            "SELECT count(*) FROM observations WHERE key = 'declared.attribute.kind'"
        ).fetchone()
    assert kinds["vorn.zarr"] == "directory"
    assert kinds["vorn.zarr/0/0.0"] == "file" and kinds["vorn.zarr/.zattrs"] == "file"
    assert kept == (1,)  # what the listing said of the path is still recorded on it
