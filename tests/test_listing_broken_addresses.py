"""A listed file whose address is missing or broken.

A listing may state that a file's address answered *not found* or *moved*, or cannot be read as an
address, with when and by what reading. The census keeps the file as an entry with what the listing
declares and records the statement beside it. A remote reading that is answered the same way records
the same observation. The flag phase raises one finding per folder, in plain words, never one
question per file. A broken address says nothing about whether a file exists elsewhere, and the
words never claim it is lost.

Every archive, accession, path and address here is invented, and nothing reaches a server.
"""

from __future__ import annotations

import json
import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ingence.core.outcomes import IdentityScope, SourceScope
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.listing.format import (
    AddressState,
    DeclaredAddress,
    Listing,
    ListingError,
    ListingHeader,
    ListingRow,
    address_state_of,
    answered_state_of,
    parse_instant,
    read_listing,
    write_listing,
)
from ingence.listing.ingest import BROKEN_ADDRESS_KEY
from ingence.listing.remote import RemoteReading, publish_remote_readings
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

_URI = "listing:kelt:K-0034"
_HEADER = ListingHeader(
    archive="kelt",
    identifier="K-0034",
    fetched_at="2026-01-02T03:04:05Z",
    converter="test",
    converter_version="1",
)
_RULE = "broken_addresses_in_folder"


def _at(text: str) -> int:
    found = parse_instant(text)
    assert found is not None
    return found


def _broken(state: AddressState, status: int | None) -> DeclaredAddress:
    return DeclaredAddress(
        state=state, at_ns=_at("2026-01-02T03:00:00Z"), found_by="HEAD", status=status
    )


def _row(path: str, address: DeclaredAddress | None = None) -> ListingRow:
    return ListingRow(
        path=path,
        size_bytes=7,
        locator=f"https://example.invalid/K-0034/Files/{path}",
        address=address,
    )


def _project(tmp_path: Path, rows: list[ListingRow]) -> Project:
    project = Project.create(tmp_path / "project", created_at="t", listings=[_URI])
    write_listing(Listing(_HEADER, rows), Path(project.identity.roots[0].mount_path))
    assert run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    ).published
    return project


def _query(project: Project, sql: str) -> list[tuple[object, ...]]:
    with open_catalog(project.path) as catalog:
        return list(catalog.query(sql).fetchall())


# -- the format --------------------------------------------------------------------------------


def test_a_broken_address_round_trips_with_when_and_how(tmp_path: Path) -> None:
    rows = [
        _row("vorn/a.tif", _broken(AddressState.NOT_FOUND, 404)),
        _row("vorn/b.tif", _broken(AddressState.MOVED, 301)),
        ListingRow(
            path="vorn/c.tif",
            address=DeclaredAddress(
                state=AddressState.MALFORMED, at_ns=_at("2026-01-02T03:00:00Z"), found_by="check"
            ),
        ),
        _row("vorn/d.tif"),
    ]
    write_listing(Listing(_HEADER, rows), tmp_path / "listing")
    back = read_listing(tmp_path / "listing")
    assert back.rows == rows
    stated = json.loads((tmp_path / "listing" / "rows.jsonl").read_text("utf-8").splitlines()[0])
    assert stated["address"] == {
        "state": "not_found",
        "status": 404,
        "at": "2026-01-02T03:00:00Z",
        "found_by": "HEAD",
    }


def test_the_statuses_that_say_an_address_answers_no_file() -> None:
    assert address_state_of(404) is AddressState.NOT_FOUND
    assert address_state_of(410) is AddressState.NOT_FOUND
    assert address_state_of(301) is AddressState.MOVED
    assert address_state_of(308) is AddressState.MOVED
    # The engine's own readings follow a permanent redirect and record what its target
    # answers. So a redirect's status reaching one is a redirect the client gave up following,
    # never "moved". Only not found is read from a status there.
    assert answered_state_of(404) is AddressState.NOT_FOUND
    assert answered_state_of(410) is AddressState.NOT_FOUND
    for status in (301, 308, 302, 500, None):
        assert answered_state_of(status) is None
    # A temporary redirect is how many archives serve a file, and a server's own failure or a
    # refusal says nothing about the address: none is a broken address.
    for status in (200, 206, 302, 303, 307, 429, 500, 503, None):
        assert address_state_of(status) is None


@pytest.mark.parametrize(
    "address",
    [
        {"state": "not_found", "status": 200, "at": "2026-01-02T03:00:00Z", "found_by": "HEAD"},
        {"state": "malformed", "status": 404, "at": "2026-01-02T03:00:00Z", "found_by": "HEAD"},
        {"state": "lost", "at": "2026-01-02T03:00:00Z", "found_by": "HEAD"},
        {"state": "not_found", "status": 404, "found_by": "HEAD"},
        {"state": "not_found", "status": 404, "at": "2026-01-02T03:00:00Z"},
    ],
)
def test_a_broken_address_that_contradicts_itself_or_omits_when_or_how_is_refused(
    tmp_path: Path, address: dict[str, object]
) -> None:
    directory = tmp_path / "listing"
    write_listing(Listing(_HEADER, []), directory)
    (directory / "rows.jsonl").write_text(
        json.dumps({"path": "vorn/a.tif", "address": address}) + "\n", "utf-8"
    )
    with pytest.raises(ListingError, match="address"):
        read_listing(directory)


# -- the census --------------------------------------------------------------------------------


def test_the_census_keeps_a_file_whose_address_is_broken_and_records_the_statement(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        [
            _row("vorn/a.tif", _broken(AddressState.NOT_FOUND, 404)),
            _row("vorn/b.tif", _broken(AddressState.NOT_FOUND, 404)),
            _row("vorn/c.tif", _broken(AddressState.MOVED, 301)),
            _row("vorn/d.tif"),
        ],
    )
    files = _query(project, "SELECT relative_path, size_bytes FROM entries WHERE entry_type='file'")
    assert sorted(files) == [(f"vorn/{name}.tif", 7) for name in "abcd"]
    said = _query(
        project,
        "SELECT e.relative_path, o.observer, o.source_scope, o.value_structure "
        f"FROM observations o JOIN entries e USING (entry_id) WHERE o.key = '{BROKEN_ADDRESS_KEY}' "
        "ORDER BY e.relative_path",
    )
    assert [(path, observer, scope) for path, observer, scope, _ in said] == [
        ("vorn/a.tif", "listing.kelt", SourceScope.DECLARED.value),
        ("vorn/b.tif", "listing.kelt", SourceScope.DECLARED.value),
        ("vorn/c.tif", "listing.kelt", SourceScope.DECLARED.value),
    ]
    moved = json.loads(str(said[2][3]))
    assert moved == {
        "state": "moved",
        "status": 301,
        "at": "2026-01-02T03:00:00Z",
        "found_by": "HEAD",
        "address": "https://example.invalid/K-0034/Files/vorn/c.tif",
    }


# -- a remote reading --------------------------------------------------------------------------


def test_a_remote_reading_answered_not_found_records_the_same_observation(tmp_path: Path) -> None:
    project = _project(tmp_path, [_row("vorn/a.tif"), _row("vorn/b.tif"), _row("vorn/c.tif")])
    ids = dict(_query(project, "SELECT relative_path, entry_id FROM entries"))
    readings = [
        RemoteReading(
            str(ids["vorn/a.tif"]),
            "blake3_quick",
            "1",
            IdentityScope.SAMPLED,
            None,
            failure="HTTP Error 404: Not Found",
            status=404,
        ),
        # A server's own failure says nothing about the address: an access error and no more.
        RemoteReading(
            str(ids["vorn/b.tif"]),
            "blake3_quick",
            "1",
            IdentityScope.SAMPLED,
            None,
            failure="HTTP Error 500: Internal Server Error",
            status=500,
        ),
        RemoteReading(
            str(ids["vorn/c.tif"]), "blake3_quick", "1", IdentityScope.SAMPLED, "f" * 32, 7
        ),
    ]
    publish_remote_readings(
        project, readings, run_id="run_20260101T000002Z_222222222222", started_at="t"
    )
    said = _query(
        project,
        "SELECT e.relative_path, o.observer, o.source_scope, o.value_structure "
        f"FROM observations o JOIN entries e USING (entry_id) WHERE o.key = '{BROKEN_ADDRESS_KEY}'",
    )
    assert [(path, observer, scope) for path, observer, scope, _ in said] == [
        ("vorn/a.tif", "remote.blake3_quick", SourceScope.DECLARED.value)
    ]
    structure = json.loads(str(said[0][3]))
    assert structure["state"] == "not_found" and structure["status"] == 404
    assert structure["found_by"] == "remote.blake3_quick"
    errors = _query(project, "SELECT relative_path, failure_class FROM scan_errors ORDER BY 1")
    assert errors == [("vorn/a.tif", "access"), ("vorn/b.tif", "access")]


# -- the finding -------------------------------------------------------------------------------


def test_broken_addresses_are_one_finding_for_their_folder_in_plain_words(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        [
            _row("vorn/a.tif", _broken(AddressState.NOT_FOUND, 404)),
            _row("vorn/b.tif", _broken(AddressState.NOT_FOUND, 404)),
            _row("vorn/c.tif", _broken(AddressState.MOVED, 301)),
            _row("vorn/d.tif"),
            _row("vorn/e.tif"),
            _row("zib/f.tif"),
        ],
    )
    ids = dict(_query(project, "SELECT relative_path, entry_id FROM entries"))
    publish_remote_readings(
        project,
        [
            RemoteReading(
                str(ids["vorn/d.tif"]),
                "blake3_quick",
                "1",
                IdentityScope.SAMPLED,
                None,
                failure="HTTP Error 404: Not Found",
                status=404,
            )
        ],
        run_id="run_20260101T000002Z_222222222222",
        started_at="2026-01-01T00:00:02Z",
    )
    run_inference(project, run_id="run_20260101T000003Z_333333333333", started_at="t")
    # Every listed file is kept, and four of them carry the observation.
    files = _query(project, "SELECT count(*) FROM entries WHERE entry_type = 'file'")
    assert files == [(6,)]
    said = _query(
        project,
        f"SELECT count(DISTINCT entry_id) FROM observations WHERE key = '{BROKEN_ADDRESS_KEY}'",
    )
    assert said == [(4,)]
    raised = _query(
        project,
        "SELECT e.relative_path, f.raised, f.reason, f.inputs FROM findings f JOIN entries e "
        f"ON e.entry_id = f.subject WHERE f.rule_id = '{_RULE}'",
    )
    assert len(raised) == 1
    path, values, reason, inputs = raised[0]
    assert path == "vorn"
    assert list(values) == ["address moved", "address not found"]
    assert list(inputs) == [ids["vorn"]]
    assert str(reason) == (
        "The archive lists 4 files directly in this folder whose addresses are broken: 3 answered "
        "not found and 1 answered that it had moved. They were not read, and a broken address "
        "does not say whether a file exists elsewhere."
    )
    for claim in ("lost", "deleted", "does not exist", "missing"):
        assert claim not in str(reason)


def test_one_kind_of_broken_address_is_said_in_one_sentence(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        [
            _row("a.tif", _broken(AddressState.NOT_FOUND, 404)),
            _row("b.tif"),
        ],
    )
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    raised = _query(project, f"SELECT reason FROM findings WHERE rule_id = '{_RULE}'")
    assert raised == [
        (
            "The archive lists 1 file directly in this folder whose address answered not found. "
            "It was not read, and a broken address does not say whether a file exists elsewhere.",
        )
    ]


def test_a_listing_with_no_broken_address_raises_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path, [_row("vorn/a.tif"), _row("vorn/b.tif"), _row("zib/c.tif")])
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    assert _query(project, f"SELECT count(*) FROM findings WHERE rule_id = '{_RULE}'") == [(0,)]
    assert _query(
        project, f"SELECT count(*) FROM observations WHERE key = '{BROKEN_ADDRESS_KEY}'"
    ) == [(0,)]


# -- the wording -------------------------------------------------------------------------------


def test_an_address_that_could_not_be_read_is_said_as_one_never_as_no_address() -> None:
    from ingence.rulebook.predicates.declared import _broken_sentence

    one = _broken_sentence((("malformed", 1),), 1)
    assert one.startswith(
        "The archive lists 1 file directly in this folder with an address that could not be "
        "read as one. It was not read"
    )
    mixed = _broken_sentence((("not_found", 2), ("malformed", 1)), 3)
    assert "2 answered not found and 1 had an address that could not be read as one." in mixed
    for words in (one, mixed):
        assert "no address" not in words and "missing" not in words


# -- the BioStudies conversion -----------------------------------------------------------------


class _Archive:
    """A stub archive: a study's info, submission and one file list, and each file's headers.

    A file's ``HEAD`` answers as ``answers`` says (a time, a status the client raises as an error,
    or ``ValueError`` raised once a server was asked), or, where ``through`` is a real client, as
    a real server answers it.
    """

    def __init__(
        self,
        answers: dict[str, object],
        *,
        link: str = "https://example.invalid/S-XX34",
        through: object = None,
    ) -> None:
        from ingence.listing.biostudies import API

        files = [{"path": path, "size": 7, "type": "file"} for path in sorted(answers)]
        self.bodies = {
            f"{API}/S-XX34/info": json.dumps({"httpLink": link}),
            f"{API}/S-XX34": json.dumps(
                {
                    "accno": "S-XX34",
                    "section": {
                        "type": "Study",
                        "attributes": [{"name": "File List", "value": "files.json"}],
                    },
                }
            ),
            f"{link}/Files/files.json": json.dumps(files),
        }
        self.answers = {f"{link}/Files/{path}": answer for path, answer in answers.items()}
        self.through = through
        self.heads = 0

    def request(self, url: str, *, method: str = "GET", **_: object) -> object:
        from email.message import Message

        from ingence.listing.http import Response

        if method == "HEAD":
            self.heads += 1
            if self.through is not None:
                return self.through.request(url, method="HEAD")  # type: ignore[attr-defined]
            answer = self.answers[url]
            if answer is ValueError:
                message = f"nonnumeric port in a redirect from {url!r}"
                raise ValueError(message)
            if isinstance(answer, int):
                raise urllib.error.HTTPError(url, answer, "stub", Message(), None)
            return Response(
                url=url,
                status=200,
                body=b"",
                etag=None,
                last_modified=str(answer),
                content_length=7,
            )
        return Response(
            url=url,
            status=200,
            body=self.bodies[url].encode("utf-8"),
            etag=None,
            last_modified=None,
            content_length=None,
        )


def _fetch(archive: _Archive, directory: Path) -> object:
    from ingence.core.settings import ListingSettings
    from ingence.listing.biostudies import BioStudiesSource

    return BioStudiesSource().fetch(
        "S-XX34",
        directory,
        client=archive,  # type: ignore[arg-type]
        settings=ListingSettings(read_archive_clocks=True),
    )


#: What the local server holds, and what it redirects where (permanently).
_PRESENT = {"/S-XX34/Files/vorn/a.tif"}
_REDIRECTS = {
    "/S-XX34/Files/vorn/c.tif": "/S-XX34/Files/vorn/a.tif",
    "/S-XX34/Files/vorn/d.tif": "/S-XX34/Files/vorn/gone.tif",
    "/S-XX34/Files/vorn/e.tif": "/S-XX34/Files/vorn/e.tif",
}


class _Files(BaseHTTPRequestHandler):
    def _answer(self, *, body: bool) -> None:
        if self.path in _REDIRECTS:
            self.send_response(301)
            self.send_header("Location", _REDIRECTS[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path in _PRESENT:
            self.send_response(200)
            self.send_header("Last-Modified", "Fri, 11 Aug 2023 12:09:35 GMT")
            self.send_header("Content-Length", "7")
            self.end_headers()
            if body:
                self.wfile.write(b"kkkkkkk")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_HEAD(self) -> None:
        self._answer(body=False)

    def do_GET(self) -> None:
        # After a redirect, urllib asks again with GET.
        self._answer(body=True)

    def log_message(self, *_: object) -> None:
        return


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A web server on this machine's loopback, holding one file and three redirects."""
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/S-XX34"
    finally:
        server.shutdown()
        server.server_close()


def _client() -> object:
    from ingence.listing.http import PoliteClient

    return PoliteClient(user_agent="test", pause_seconds=0.0)


def test_a_study_whose_file_answers_not_found_is_listed_with_its_address_broken(
    tmp_path: Path,
) -> None:
    """One file's ``HEAD`` answering 404 does not stop the study's fetch. The study is listed and
    the file's address is stated broken, with when and by what reading."""
    archive = _Archive({"vorn/a.tif": "Fri, 11 Aug 2023 12:09:35 GMT", "vorn/b.tif": 404})
    listing = _fetch(archive, tmp_path / "listing")
    by_path = {row.path: row for row in listing.rows}  # type: ignore[attr-defined]
    assert set(by_path) == {"vorn/a.tif", "vorn/b.tif"}
    assert by_path["vorn/a.tif"].address is None and by_path["vorn/a.tif"].clocks
    missing = by_path["vorn/b.tif"].address
    assert missing is not None
    assert (missing.state, missing.status, missing.found_by) == (
        AddressState.NOT_FOUND,
        404,
        "HEAD",
    )
    # The conversion reads what was kept, so a second one offline says the same.
    assert read_listing(tmp_path / "listing").rows == listing.rows  # type: ignore[attr-defined]


def test_a_permanent_redirect_is_followed_and_recorded_by_what_its_target_answers(
    tmp_path: Path, served: str
) -> None:
    """Against a real server: a file moved for good that still answers is not a broken address, and
    one moved to nothing answers not found, as its target does."""
    names = ("vorn/a.tif", "vorn/b.tif", "vorn/c.tif", "vorn/d.tif")
    archive = _Archive(dict.fromkeys(names, None), link=served, through=_client())
    listing = _fetch(archive, tmp_path / "listing")
    by_path = {row.path: row for row in listing.rows}  # type: ignore[attr-defined]
    assert by_path["vorn/a.tif"].address is None and by_path["vorn/a.tif"].clocks
    assert by_path["vorn/c.tif"].address is None and by_path["vorn/c.tif"].clocks
    for name in ("vorn/b.tif", "vorn/d.tif"):
        stated = by_path[name].address
        assert stated is not None
        assert (stated.state, stated.status) == (AddressState.NOT_FOUND, 404)
    assert not any(
        row.address is not None and row.address.state is AddressState.MOVED
        for row in listing.rows  # type: ignore[attr-defined]
    )


def test_a_redirect_loop_stops_the_study_as_the_client_raises_it_never_as_moved(
    tmp_path: Path, served: str
) -> None:
    """A redirect the client gives up following is the client's failure, said as it said it."""
    archive = _Archive({"vorn/a.tif": None, "vorn/e.tif": None}, link=served, through=_client())
    with pytest.raises(urllib.error.HTTPError, match="infinite loop") as raised:
        _fetch(archive, tmp_path / "listing")
    assert raised.value.code == 301
    raised.value.close()
    assert not (tmp_path / "listing" / "raw" / "addresses.json").exists()


def test_an_address_that_cannot_be_read_as_one_is_stated_so_and_nothing_is_asked(
    tmp_path: Path,
) -> None:
    archive = _Archive({"vorn/a.tif": None}, link="https://example.invalid:port/S-XX34")
    listing = _fetch(archive, tmp_path / "listing")
    (row,) = listing.rows  # type: ignore[attr-defined]
    assert row.address is not None
    assert (row.address.state, row.address.status) == (AddressState.MALFORMED, None)
    assert archive.heads == 0


def test_a_value_error_once_a_server_was_asked_is_no_unreadable_address(tmp_path: Path) -> None:
    """A ``ValueError`` raised after a server was asked (a redirect naming a port that is no
    number) is a failure reaching the file, recorded as the fetch's failure, never as malformed."""
    archive = _Archive({"vorn/a.tif": ValueError})
    with pytest.raises(ValueError, match="nonnumeric port"):
        _fetch(archive, tmp_path / "listing")


def test_a_server_failure_on_a_files_headers_still_stops_the_study(tmp_path: Path) -> None:
    """A server's own failure says nothing about the address, and is not recorded as one."""
    archive = _Archive({"vorn/a.tif": 500})
    with pytest.raises(urllib.error.HTTPError):
        _fetch(archive, tmp_path / "listing")


def test_a_kept_status_that_contradicts_its_state_is_not_read_as_a_broken_address() -> None:
    from ingence.listing.biostudies import _address

    at = "2026-01-02T03:00:00Z"
    assert _address({"state": "not_found", "status": 301, "at": at, "found_by": "HEAD"}) is None
    assert _address({"state": "malformed", "status": 404, "at": at, "found_by": "HEAD"}) is None
    kept = _address({"state": "not_found", "status": 404, "at": at, "found_by": "HEAD"})
    assert kept is not None and kept.state is AddressState.NOT_FOUND
