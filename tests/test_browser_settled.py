"""What ``settled`` waits for.

The browser tests wait for a quiet page with ``fixtures.dashboard_browser.settled``. It must hold
for two things: a request a timer makes a moment after the wait is called (a check that nothing is
sent depends on it), and a response whose headers have come and whose body is still arriving.
Each test serves a tiny page of its own from a thread; no Dashboard is involved.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import launch_browser, need, settled

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: The page: one request whose body takes a while. A test starts a timer on it afterwards.
_PAGE = b"""<!doctype html><meta charset="utf-8"><title>settled</title>
<script>
window.late = null; window.slow = null;
fetch('/slow').then((r) => r.text()).then((t) => { window.slow = t; });
</script><p>page</p>"""

#: A key's handler that sends a request a moment later: run in the page a test has left quiet.
_LATER = (
    "(delay) => setTimeout(() => fetch('/late').then((r) => r.text())"
    ".then((t) => { window.late = t; }), delay)"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/":
            self._send(_PAGE, "text/html")
        elif self.path == "/late":
            self._send(b"late", "text/plain")
        elif self.path == "/slow":
            # The headers go out at once; the body arrives in two parts, a while apart.
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "8")
            self.end_headers()
            self.wfile.write(b"slow")
            self.wfile.flush()
            time.sleep(1.2)
            self.wfile.write(b"down")
        else:
            self.send_error(404)

    def _send(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def address() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _quiet_page(browser: Any, address: str) -> Any:
    """A page that loaded, finished its one request and has been quiet for a long while."""
    page = browser.new_page()
    page.goto(address)
    page.wait_for_function("window.slow !== null")
    page.evaluate("window.__ingenceQuiet.last = performance.now() - 10000")
    return page


def test_settled_waits_for_a_request_a_timer_makes_after_it_is_called(
    browser: Any, address: str
) -> None:
    page = _quiet_page(browser, address)
    try:
        page.evaluate(_LATER, 250)
        settled(page)
        assert page.evaluate("window.late") == "late"
    finally:
        page.close()


def test_settled_waits_for_a_body_still_arriving_not_only_its_headers(
    browser: Any, address: str
) -> None:
    page = browser.new_page()
    try:
        page.goto(address)
        # The headers of /slow came at once; its body takes more than a second.
        assert page.evaluate("window.slow") is None
        settled(page, quiet_ms=300)
        assert page.evaluate("window.slow") == "slowdown"
    finally:
        page.close()


def test_a_wait_counted_from_the_pages_last_request_returns_before_a_late_timer(
    browser: Any, address: str
) -> None:
    """The weaker form, kept for a page that has long been quiet, says so in its name."""
    page = _quiet_page(browser, address)
    try:
        page.evaluate(_LATER, 1500)
        settled(page, quiet_ms=300, from_now=False)
        assert page.evaluate("window.late") is None
    finally:
        page.close()
