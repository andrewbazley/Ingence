"""The fetch guard: a study too large to read whole is refused, in words.

Two limits, both settings: the bytes a response may weigh and the files a study may list. Either
is a failed fetch that names the limit and the setting, and neither leaves a listing behind.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from ingence.core.settings import ListingSettings
from ingence.listing import http as listing_http
from ingence.listing.biostudies import API, BioStudiesSource
from ingence.listing.http import PoliteClient, Response, ResponseTooLargeError

if TYPE_CHECKING:
    from pathlib import Path

_LINK = "https://example.invalid/S-XX9"


class _Answer(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.status = 200
        self.headers = headers

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _Study:
    """A stub archive holding one study with ``count`` files in one file list."""

    def __init__(self, count: int) -> None:
        files = [{"path": f"f{index}.tif", "size": 1, "type": "file"} for index in range(count)]
        self.bodies = {
            f"{API}/S-XX9/info": json.dumps({"httpLink": _LINK}),
            f"{API}/S-XX9": json.dumps(
                {
                    "accno": "S-XX9",
                    "section": {
                        "type": "Study",
                        "attributes": [{"name": "File List", "value": "files.json"}],
                    },
                }
            ),
            f"{_LINK}/Files/files.json": json.dumps(files),
        }
        self.limits: list[int | None] = []

    def request(
        self, url: str, *, method: str = "GET", max_bytes: int | None = None, **_: object
    ) -> Response:
        self.limits.append(max_bytes)
        return Response(
            url=url,
            status=200,
            body=self.bodies[url].encode("utf-8"),
            etag=None,
            last_modified=None,
            content_length=None,
        )


def _fetch(archive: object, directory: Path, **settings: object) -> object:
    return BioStudiesSource().fetch(
        "S-XX9",
        directory,
        client=archive,  # type: ignore[arg-type]
        settings=ListingSettings(**settings),  # type: ignore[arg-type]
    )


def test_a_study_listing_more_files_than_allowed_is_refused_in_words(tmp_path: Path) -> None:
    archive = _Study(6)
    with pytest.raises(ResponseTooLargeError) as raised:
        _fetch(archive, tmp_path / "listing", max_listing_files=5)
    words = str(raised.value)
    assert "S-XX9 lists 6 files, more than the 5 allowed" in words
    assert "listing.max_listing_files" in words
    # No listing was built, and the guard's limit was handed to every request.
    assert not (tmp_path / "listing" / "listing.json").exists()
    assert set(archive.limits) == {ListingSettings().max_listing_bytes}


def test_a_study_at_the_limit_is_read_whole(tmp_path: Path) -> None:
    listing = _fetch(_Study(5), tmp_path / "listing", max_listing_files=5)
    assert len(listing.rows) == 5  # type: ignore[attr-defined]


def test_a_response_stating_more_bytes_than_allowed_is_refused_unread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Counted(_Answer):
        reads = 0

        def read(self, size: int | None = -1) -> bytes:
            type(self).reads += 1
            return super().read(size)

    big = _Counted(b"x" * 100, {"Content-Length": "100"})
    monkeypatch.setattr(listing_http.urllib.request, "urlopen", lambda request, timeout: big)
    client = PoliteClient(user_agent="a", pause_seconds=0.0)
    with pytest.raises(ResponseTooLargeError, match="states 100 bytes, more than the 50"):
        client.request("https://example.invalid/a", max_bytes=50)
    assert _Counted.reads == 0 and big.closed


def test_a_response_that_grows_past_the_limit_is_stopped_mid_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(listing_http, "_CHUNK", 10)
    sizes: list[int] = []

    class _Sized(_Answer):
        def read(self, size: int | None = -1) -> bytes:
            block = super().read(size)
            sizes.append(len(block))
            return block

    body = _Sized(b"x" * 100, {})
    monkeypatch.setattr(listing_http.urllib.request, "urlopen", lambda request, timeout: body)
    log: list[dict[str, object]] = []
    client = PoliteClient(user_agent="a", pause_seconds=0.0, log=log.append)
    with pytest.raises(ResponseTooLargeError, match="more than the 25 bytes allowed"):
        client.request("https://example.invalid/a", max_bytes=25)
    assert sizes == [10, 10, 10]  # three blocks read, the rest left
    assert log[0]["too_large"] is True


def test_a_study_whose_file_list_is_too_heavy_names_the_setting(tmp_path: Path) -> None:
    class _Heavy(_Study):
        def request(self, url: str, **_: object) -> Response:  # type: ignore[override]
            message = f"{url}: the server states 9 bytes, more than the 3 allowed; nothing was read"
            raise ResponseTooLargeError(message)

    with pytest.raises(
        ResponseTooLargeError, match=r"S-XX9 is too large to read whole.*max_listing_bytes"
    ):
        _fetch(_Heavy(1), tmp_path / "listing", max_listing_bytes=3)
