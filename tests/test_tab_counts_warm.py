"""The tabs' counts are counted from the server's start, and a page is told they are being counted.

On a large project, the first page's own reads can answer in under a second while the first count of
what waits on each review page takes well over a minute. Held here over the explorer fixture
(invented names), with the count slowed by hand:

- the server begins counting as it starts, on a thread of its own, and a request finds that count
  rather than starting another;
- a request waits a moment for a count being made, then answers that the counts are being counted;
- a count that fails is said to the request that finds it, and the next request counts again.
"""

from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from fixtures.explorer_project import ExplorerProject


def _get(base: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{base}api/waiting", timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


@pytest.fixture
def project(explorer_project: ExplorerProject, tmp_path: Path) -> Path:
    from ingence.api import server as server_module

    server_module.waiting_settled()
    server_module._WAITING.clear()
    copy = tmp_path / "project"
    shutil.copytree(explorer_project.project, copy)
    return copy


def test_a_slow_count_is_said_to_be_counting_and_then_answered_once(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api import server as server_module
    from ingence.api.routes import pages as page_routes

    calls: list[int] = []
    finished: list[int] = []
    begun = threading.Event()
    release = threading.Event()
    original = page_routes.waiting

    def slow(catalog: Any, project_path: Any) -> dict[str, int]:
        calls.append(1)
        begun.set()
        release.wait(30)
        finished.append(1)
        return original(catalog, project_path)

    monkeypatch.setattr(page_routes, "waiting", slow)
    monkeypatch.setattr(server_module, "WAITING_PATIENCE_SECONDS", 0.3)
    server = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        assert begun.wait(600), "the server began counting as it started"
        status, body = _get(server.url)
        assert status == 200 and body["counting"] is True
        # The answer came while the count was still held: the request did not wait for it. (A
        # clock would measure the machine's load as much as the request.)
        assert finished == []
        release.set()
        assert server_module.waiting_settled(30)
        status, body = _get(server.url)
        assert status == 200 and "counting" not in body
        assert body == _counted(project, original)
        assert len(calls) == 1, "the start's count and the requests shared one count"
    finally:
        release.set()
        server.stop()


def test_a_count_that_fails_is_said_and_the_next_request_counts_again(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.api import server as server_module
    from ingence.api.routes import pages as page_routes

    original = page_routes.waiting
    failing = [True]

    def flaky(catalog: Any, project_path: Any) -> dict[str, int]:
        if failing[0]:
            message = "the kelt page could not be built"
            raise OSError(message)
        return original(catalog, project_path)

    monkeypatch.setattr(page_routes, "waiting", flaky)
    server = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    try:
        assert server_module.waiting_settled(30)
        status, body = _get(server.url)
        if status == 200 and body.get("counting"):  # the start's failure was taken already
            assert server_module.waiting_settled(30)
            status, body = _get(server.url)
        assert status == 500 and "the kelt page could not be built" in body["error"]
        failing[0] = False
        # The request counts again. A count that outlasts the request's patience is answered
        # "counting" (the page asks again), so the test waits for the count to be made, never
        # for a time, and asks again.
        status, body = _asked_until_counted(server_module, server.url)
        assert status == 200 and body == _counted(project, original)
    finally:
        server.stop()


def _asked_until_counted(server_module: Any, base: str) -> tuple[int, dict[str, Any]]:
    """The answer to a request for the counts once they have been made.

    On a machine under load the first request can outlast the server's patience and be told
    that the counts are being counted. The count in progress ends in a state the server can
    report (``waiting_settled``), after which the next request is answered from it.
    """
    status, body = _get(base)
    for _ in range(20):
        if status != 200 or not body.get("counting"):
            break
        assert server_module.waiting_settled(120)
        status, body = _get(base)
    return status, body


class _Marked(set):  # type: ignore[type-arg]
    """The server's set of stopped counts, which tells the test when a stop has marked one."""

    def __init__(self, marked: threading.Event) -> None:
        super().__init__()
        self._marked = marked

    def update(self, *others: Any) -> None:
        super().update(*others)
        if self:
            self._marked.set()


class _SeenInterrupts:
    """A catalog's connection that says when it was first interrupted. Every other call behaves
    normally.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.interrupted = threading.Event()

    def interrupt(self) -> None:
        self.interrupted.set()
        self._connection.interrupt()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _counts_alive() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name == "tab-counts"]


def test_a_stop_while_the_count_opens_its_catalog_begins_no_query(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A count still opening its catalog has nothing to interrupt when the stop comes. It must
    find itself stopped and begin no query. The stop marks the count; the test lets the catalog
    open only after it has (an event, not a time), so the order is made, not hoped for."""
    from contextlib import contextmanager

    from ingence.api import server as server_module
    from ingence.api.routes import pages as page_routes
    from ingence.storage.catalog import open_catalog

    real_open = open_catalog
    opening, release, finish, marked = (threading.Event() for _ in range(4))
    queries: list[int] = []

    @contextmanager
    def slow_open(*args: Any, **kwargs: Any) -> Any:
        if threading.current_thread().name == "tab-counts":
            opening.set()
            release.wait(600)
        with real_open(*args, **kwargs) as catalog:
            yield catalog

    def counted(catalog: Any, project_path: Any) -> dict[str, int]:
        queries.append(1)
        finish.wait(600)
        return {}

    monkeypatch.setattr(server_module, "open_catalog", slow_open)
    monkeypatch.setattr(server_module, "_WAITING_STOPPED", _Marked(marked))
    monkeypatch.setattr(page_routes, "waiting", counted)
    server = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    stopper = threading.Thread(target=server.stop)
    try:
        assert opening.wait(600), "the count began opening its catalog"
        stopper.start()
        assert marked.wait(600), "the stop marked the count"
        release.set()
        stopper.join(600)
        assert not stopper.is_alive()
        assert queries == [], "a stopped count began its query"
        assert _counts_alive() == []
    finally:
        release.set()
        finish.set()
        stopper.join(60)


def test_a_stop_repeats_its_interrupt_when_the_first_comes_before_the_query(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DuckDB forgets an interrupt that arrives before a query begins. The count here is held
    between its catalog opening and its query; the test lets the query begin only after the stop
    has interrupted once, so that first interrupt is certainly lost, and the stop must interrupt
    again."""
    from ingence.api import server as server_module
    from ingence.api.routes import pages as page_routes

    parked, release = threading.Event(), threading.Event()
    seen: list[_SeenInterrupts] = []

    def endless(catalog: Any, project_path: Any) -> dict[str, int]:
        spy = _SeenInterrupts(catalog.connection)
        seen.append(spy)
        catalog.connection = spy
        parked.set()
        release.wait(600)
        catalog.connection.execute(f"SELECT sum(range) FROM range({10**12})").fetchone()
        return {}

    monkeypatch.setattr(page_routes, "waiting", endless)
    server = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    stopper = threading.Thread(target=server.stop)
    try:
        assert parked.wait(600), "the count was held before its query"
        stopper.start()
        assert seen[0].interrupted.wait(600), "the stop interrupted the count once"
        release.set()
        stopper.join(600)
        assert not stopper.is_alive()
        assert _counts_alive() == [], "the count was left running after the stop"
    finally:
        release.set()
        # A count the stop failed to end is ended here, so a failure does not leave it running.
        for _ in range(600):
            if not _counts_alive():
                break
            for spy in seen:
                spy.interrupt()
            threading.Event().wait(0.1)
        stopper.join(60)


def test_a_stop_while_the_starting_read_opens_its_catalog_leaves_no_count(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read the server makes as it starts begins the first count. When the stop comes while
    that read is still opening its catalog, the first stop of counts has already looked, so the
    stop must look again (and the read begins nothing once it finds itself stopped)."""
    from contextlib import contextmanager

    from ingence.api import server as server_module
    from ingence.api.routes import pages as page_routes
    from ingence.storage.catalog import open_catalog

    real_open = open_catalog
    real_stop_counting = server_module.stop_counting
    opening, release, looked, finish = (threading.Event() for _ in range(4))
    queries: list[int] = []

    @contextmanager
    def slow_open(*args: Any, **kwargs: Any) -> Any:
        if threading.current_thread().name == "record-read" and not opening.is_set():
            opening.set()
            release.wait(600)
        with real_open(*args, **kwargs) as catalog:
            yield catalog

    def stop_counting(*args: Any, **kwargs: Any) -> None:
        real_stop_counting(*args, **kwargs)
        looked.set()

    def counted(catalog: Any, project_path: Any) -> dict[str, int]:
        queries.append(1)
        finish.wait(600)
        return {}

    monkeypatch.setattr(server_module, "open_catalog", slow_open)
    monkeypatch.setattr(server_module, "stop_counting", stop_counting)
    monkeypatch.setattr(page_routes, "waiting", counted)
    server = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    stopper = threading.Thread(target=server.stop)
    try:
        assert opening.wait(600), "the starting read began opening its catalog"
        stopper.start()
        assert looked.wait(600), "the stop looked for counts once"
        release.set()
        stopper.join(600)
        assert not stopper.is_alive()
        assert _counts_alive() == [], "a count begun after the first look was left running"
    finally:
        release.set()
        finish.set()
        stopper.join(60)


def _counted(project: Path, count: Any) -> dict[str, int]:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        return dict(count(catalog, project))


def test_a_stopped_server_leaves_no_count_of_its_own_running(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A count still inside DuckDB as the process exits must not end it with an abort after every
    check has passed.

    Stopping a server interrupts its count and waits for it, and a count so ended is no failure: the
    next server counts again.
    """
    from ingence.api import server as server_module
    from ingence.api.routes import pages as page_routes

    original = page_routes.waiting
    reading = threading.Event()

    def endless(catalog: Any, project_path: Any) -> dict[str, int]:
        # One query that would run for a very long time unless interrupted.
        reading.set()
        catalog.connection.execute(f"SELECT sum(range) FROM range({10**12})").fetchone()
        return {}

    monkeypatch.setattr(page_routes, "waiting", endless)
    server = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    server.start()
    # Stopped when the count has reached its query, not after a time guessed to be enough: on a
    # loaded machine the count can still be opening its catalog after any fixed wait. This is the
    # stop that arrives mid-query; the next three tests make the stops that arrive earlier.
    assert reading.wait(600), "the count began"
    server.stop()
    alive = [t.name for t in threading.enumerate() if t.name in ("tab-counts", "record-read")]
    assert alive == []
    monkeypatch.setattr(page_routes, "waiting", original)
    again = server_module.DashboardServer(project, port=0, config_home=tmp_path / "config")
    again.start()
    try:
        assert server_module.waiting_settled(30)
        status, body = _get(again.url)
        assert status == 200 and body == _counted(project, original)
    finally:
        again.stop()
