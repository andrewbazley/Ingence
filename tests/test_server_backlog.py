"""A page's opening burst of requests is answered, not reset.

``socketserver`` listens with a queue of five, so a page that asks for a dozen routes at once can
have some of its connections reset by the operating system, and the page says the engine did not
answer. The Dashboard's server listens with a queue of 128.
"""

from __future__ import annotations

import concurrent.futures
import urllib.request
from pathlib import Path

from ingence.api.server import DashboardServer, _DashboardHTTPServer


def test_the_server_listens_with_room_for_a_burst() -> None:
    assert _DashboardHTTPServer.request_queue_size >= 128


def test_a_burst_of_simultaneous_requests_is_answered_every_time(tmp_path: Path) -> None:
    server = DashboardServer(None, port=0, config_home=tmp_path / "config")
    assert isinstance(server._http, _DashboardHTTPServer)
    server.start()
    try:
        address = f"http://127.0.0.1:{server._http.server_address[1]}/api/landing/state"

        def ask(_: int) -> int | str:
            try:
                with urllib.request.urlopen(address, timeout=60) as answer:
                    return int(answer.status)
            except OSError as refused:
                return type(refused).__name__

        with concurrent.futures.ThreadPoolExecutor(64) as pool:
            answers = list(pool.map(ask, range(192)))
    finally:
        server.stop()
    assert [answer for answer in answers if answer != 200] == []
