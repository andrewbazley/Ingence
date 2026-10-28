"""Asking an archive, politely: one request at a time, paused, named, logged, and stopped when told.

An archive is somebody else's server. The engine uses the network only during indexing and file
exploration. A listing root's fetch and a remote digest are both such uses, and this is how the
engine asks. Four rules apply, each a parameter or a record rather than a habit:

- one request at a time, with a pause between (``ListingSettings.pause_seconds``);
- a user agent that says what this is (``ListingSettings.user_agent``);
- every request is logged: the URL, the moment, the status, the bytes, and the ``ETag`` and
  ``Last-Modified`` the server sent, through the ``log`` callable a caller hands in;
- a 429 or a 503 stops that server for the rest of the run: recorded, raised as
  :class:`ArchiveRefusedError`, and never retried in a loop. Any other failure is raised to the
  caller as it came, which records it as data (a failed fetch is a fact about the archive).

A response larger than the limit a caller states (``max_bytes``) is refused the same way: by its
stated length before a byte is read, or as soon as more than the limit has arrived, and
:class:`ResponseTooLargeError` raised with the sizes in words.

A range request answered with the whole file is refused, never read: the response is closed at once
and :class:`RangeRefusedError` raised, so a server that ignores ``Range`` costs one status line and
not a download.

Standard library only (``urllib``).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ArchiveRefusedError",
    "PoliteClient",
    "RangeRefusedError",
    "Response",
    "ResponseTooLargeError",
]

#: The HTTP statuses that mean "stop asking": too many requests, and service unavailable.
STOP_STATUSES = frozenset({429, 503})
_CHUNK = 1 << 20


class ArchiveRefusedError(RuntimeError):
    """The server said to stop (429 or 503). Nothing more is asked of it in this run."""


class RangeRefusedError(RuntimeError):
    """A server answered a range request with the whole file. Nothing more was read."""


class ResponseTooLargeError(ValueError):
    """An answer is larger than the limit the caller set. The rest of it was not read."""


@dataclass(frozen=True, slots=True)
class Response:
    """What one request returned: its status, the headers worth keeping, and its body."""

    url: str
    status: int
    body: bytes
    etag: str | None
    last_modified: str | None
    content_length: int | None


def _moment() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class PoliteClient:
    """The one way the engine asks an archive for anything."""

    user_agent: str
    pause_seconds: float = 0.5
    #: Receives one dictionary per request. None keeps no log, which only a test should do.
    log: Callable[[dict[str, Any]], None] | None = None
    timeout_seconds: float = 120.0
    #: The servers that said to stop, by host. Nothing more is asked of them in this run.
    stopped: set[str] = field(default_factory=set)
    requests: int = 0
    bytes_received: int = 0
    _last_request: float = field(default=0.0, repr=False)

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.pause_seconds:
            time.sleep(self.pause_seconds - elapsed)

    def _record(self, entry: dict[str, Any]) -> None:
        if self.log is not None:
            self.log(entry)

    def _refuse_if_stopped(self, url: str) -> str:
        host = urlsplit(url).netloc
        if host in self.stopped:
            message = f"{host} asked us to stop earlier in this run; {url} was not requested"
            raise ArchiveRefusedError(message)
        return host

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        byte_range: str | None = None,
        sink: Callable[[bytes], None] | None = None,
        max_bytes: int | None = None,
    ) -> Response:
        """Ask once. ``byte_range`` is a ``Range`` value's part after ``bytes=``. ``sink``
        receives the body in pieces instead of keeping it (a streamed digest keeps no bytes).
        ``max_bytes`` refuses an answer larger than it, unread past the limit."""
        host = self._refuse_if_stopped(url)
        self._wait()
        headers = {"User-Agent": self.user_agent}
        if byte_range is not None:
            headers["Range"] = f"bytes={byte_range}"
        request = urllib.request.Request(url, headers=headers, method=method)  # noqa: S310
        started = time.monotonic()
        entry: dict[str, Any] = {"url": url, "method": method, "moment": _moment()}
        if byte_range is not None:
            entry["range"] = byte_range
        self.requests += 1
        try:
            opened = urllib.request.urlopen(request, timeout=self.timeout_seconds)  # noqa: S310
            with opened as answer:
                status = int(answer.status)
                etag = answer.headers.get("ETag")
                modified = answer.headers.get("Last-Modified")
                length_header = answer.headers.get("Content-Length")
                length = int(length_header) if length_header and length_header.isdigit() else None
                entry.update(status=status, etag=etag, last_modified=modified, length=length)
                if byte_range is not None and status != 206:
                    entry.update(bytes=0, refused_range=True)
                    message = (
                        f"{url}: asked for bytes={byte_range} and the server offered the whole "
                        f"file (status {status}); nothing was read"
                    )
                    raise RangeRefusedError(message)
                if (
                    method != "HEAD"
                    and max_bytes is not None
                    and length is not None
                    and length > max_bytes
                ):
                    entry.update(bytes=0, too_large=True)
                    message = (
                        f"{url}: the server states {length:,} bytes, more than the "
                        f"{max_bytes:,} allowed; nothing was read"
                    )
                    raise ResponseTooLargeError(message)
                kept: list[bytes] = []
                received = 0
                if method != "HEAD":
                    while True:
                        block = answer.read(_CHUNK)
                        if not block:
                            break
                        received += len(block)
                        if max_bytes is not None and received > max_bytes:
                            entry.update(bytes=received, too_large=True)
                            self.bytes_received += received
                            message = (
                                f"{url}: more than the {max_bytes:,} bytes allowed "
                                "had arrived; the rest was not read"
                            )
                            raise ResponseTooLargeError(message)
                        if sink is None:
                            kept.append(block)
                        else:
                            sink(block)
                entry["bytes"] = received
                self.bytes_received += received
                return Response(
                    url=url,
                    status=status,
                    body=b"".join(kept),
                    etag=etag,
                    last_modified=modified,
                    content_length=length,
                )
        except urllib.error.HTTPError as error:
            entry.update(status=error.code, bytes=0, error=str(error.reason))
            if error.code in STOP_STATUSES:
                self.stopped.add(host)
                entry["stopped"] = True
                message = f"{host} answered {error.code} for {url}; nothing more is asked of it"
                raise ArchiveRefusedError(message) from error
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            entry.update(status=None, bytes=entry.get("bytes", 0), error=repr(error))
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - started, 3)
            self._last_request = time.monotonic()
            self._record(entry)
