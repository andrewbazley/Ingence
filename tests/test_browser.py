"""The browser harness: the Graph page and job recovery, driven in a real browser.

Needs the ``browser`` extra (Playwright for Python) and a browser Playwright can start: its own
Chromium (``playwright install chromium``) or, failing that, an installed Chrome.
``INGENCE_BROWSER`` names another (``firefox`` or ``webkit``; ``launch_browser`` reads it).
The tests are skipped, never failed, when either is missing, so the ordinary suite is unchanged.
CI's browser job installs both. Set ``INGENCE_BROWSER_SCREENSHOTS`` to a folder to keep screenshots
of the fixture's pages (invented names only).

1. The graph's reload-and-select path: a centre opened by its address draws. Choosing a line in
   the list shows its rule and version. The selection is in the address and survives a reload.
   Centring on another item and going back returns to the first centre. The arrow keys walk the
   list.
2. A job's recovery from the page: a definition starts a job, the server is killed while it runs,
   and a new server starts on the same project. The page must still draw the graph from a
   published run, and must say what became of the job. A job that died with its server is recorded
   in the durable job ledger, so the new server can report it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

#: Set in CI's browser job. There a missing extra or a browser that will not start is a failure,
#: so the job cannot pass having tested nothing. Elsewhere both are a skip.
REQUIRED = os.environ.get("INGENCE_BROWSER_REQUIRED") == "1"

if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="the browser extra is not installed"
    )

REPOSITORY = Path(__file__).resolve().parents[1]

#: How long a wait for a state gives up. This is a guard against a hang, not the time the page is
#: expected to take: every wait returns the moment its state is reached.
HANG_GUARD_MS = 120_000


def _shots(page: Any, name: str) -> None:
    folder = os.environ.get("INGENCE_BROWSER_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.graph_project import build_graph_project

    return build_graph_project(tmp_path_factory.mktemp("browser"))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(project: Path, port: int) -> subprocess.Popen[bytes]:
    """The Dashboard as a person runs it: its own process, so it can be killed mid-job."""
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from ingence.api.server import DashboardServer\n"
        "server = DashboardServer(Path(sys.argv[1]), port=int(sys.argv[2]))\n"
        "server.start()\n"
        "print('serving', flush=True)\n"
        "while True:\n"
        "    time.sleep(60)\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY / "src")}
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(project), str(port)],
        env=environment,
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stdout.readline().startswith(b"serving")
    return process


def _stop(process: subprocess.Popen[bytes], *, kill: bool = False) -> None:
    process.send_signal(signal.SIGKILL if kill else signal.SIGTERM)
    process.wait(timeout=30)
    if process.stdout is not None:
        process.stdout.close()


def test_the_graph_draws_by_its_address_and_keeps_the_selection_through_a_reload(browser, graph):
    port = _free_port()
    server = _serve(graph.project, port)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        base = f"http://127.0.0.1:{port}/"
        page.goto(f"{base}?center={graph.ids['trell']}#graph")
        page.wait_for_selector("table.graph-table")
        assert "trell" in page.locator('[data-graph="title"]').inner_text().lower()
        # The centre is selected first, with what the rules said about it (the typed entry route).
        inspector = page.locator('[data-graph="inspector"]')
        inspector.get_by_text("What the rules said about it").wait_for()
        _shots(page, "graph-folder-centre")

        page.locator("button.graph-pick", has_text="source of").first.click()
        inspector = page.locator('[data-graph="inspector"]')
        inspector.get_by_text("Published by this run").wait_for()
        assert graph.derivation_edge in page.url
        assert "version" in inspector.inner_text()
        _shots(page, "graph-derivation-selected")

        page.reload()
        page.wait_for_selector("table.graph-table")
        page.locator('[data-graph="inspector"]').get_by_text("Published by this run").wait_for()
        assert graph.derivation_edge in page.url, "the selection survives a reload"

        # Arrow keys walk the list's buttons.
        first = page.locator("button.graph-pick").first
        first.focus()
        page.keyboard.press("ArrowDown")
        focused = page.evaluate("document.activeElement && document.activeElement.className")
        assert "graph-pick" in focused

        # Centre on a child, then back: the first centre returns.
        page.locator('button.graph-pick[aria-label^="kelt_raw.csv,"]').click()
        page.get_by_role("button", name="Center the graph here").click()
        page.wait_for_function(f"location.search.includes('{graph.ids['kelt_raw.csv']}')")
        page.go_back()
        page.wait_for_function(f"location.search.includes('{graph.ids['trell']}')")
        page.wait_for_selector("table.graph-table")
        assert "trell" in page.locator('[data-graph="title"]').inner_text().lower()

        # Proposals on: the dashed, labelled lines appear in the list.
        page.goto(f"{base}?center={graph.ids['run_kelt.py']}&proposals=1#graph")
        page.wait_for_selector("table.graph-table")
        assert page.get_by_text("mentions (proposed)").count() == 2
        _shots(page, "graph-proposals")
    finally:
        page.close()
        _stop(server)


def test_the_graph_starts_by_choosing_a_centre_found_by_search(browser, graph):
    port = _free_port()
    server = _serve(graph.project, port)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"http://127.0.0.1:{port}/#graph")
        page.get_by_text("Choose a folder or file").first.wait_for()
        page.locator('[data-graph="choose"] input').fill("kelt")
        page.locator('[data-graph="choose"] input').press("Enter")
        page.locator(".graph-results button.graph-open").first.click()
        page.wait_for_selector("table.graph-table")
        assert "center=" in page.url
        _shots(page, "graph-chosen-by-search")
        # Any single file is reachable by its path, beside the search's experiments and folders.
        page.goto(f"http://127.0.0.1:{port}/#graph")
        page.locator('[data-graph="choose"] input').fill("zorn_scan")
        page.locator('[data-graph="choose"] input').press("Enter")
        page.locator(".graph-results button.graph-open", has_text="zorn/zorn_scan.bin").click()
        page.wait_for_selector("table.graph-table")
        assert "zorn_scan.bin" in page.locator('[data-graph="title"]').inner_text().lower()
    finally:
        page.close()
        _stop(server)


@pytest.fixture(scope="module")
def restarted(browser, graph, tmp_path_factory: pytest.TempPathFactory):
    """Start a job from the page, kill the server while it runs, and start a new server on the same
    project. The fixture yields what the reloaded page shows."""
    import shutil

    project = tmp_path_factory.mktemp("restart") / "project"
    shutil.copytree(graph.project, project)
    port = _free_port()
    server = _serve(project, port)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    base = f"http://127.0.0.1:{port}/"
    try:
        page.goto(f"{base}?center={graph.ids['trell']}#graph")
        page.wait_for_selector("table.graph-table")
        before = page.locator('[data-graph="status"]').inner_text()
        # A definition waits for Re-run: the page's answer, then its Re-run, starts a job.
        answered = page.evaluate(
            """async () => {
                const headers = {'Content-Type': 'application/json', 'X-Ingence-Write': '1'};
                const response = await fetch('/api/review/answer', {method: 'POST', headers,
                  body: JSON.stringify({actor: 'kelt', word: 'vorn', answer: 'vorn stain',
                    theme_kind: 'reagent'})});
                const rerun = await fetch('/api/workspace/rerun', {method: 'POST', headers,
                  body: '{}'});
                return [response.status, await response.json(), rerun.status];
            }"""
        )
        assert answered[0] in (200, 201) and answered[2] == 201, answered
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with urllib.request.urlopen(f"{base}api/applying") as response:
                phase = json.loads(response.read()).get("phase")
            # The server sends the contract's words (names, rules). The ledger's are in
            # phase_legacy.
            if phase in ("binding", "inferring", "names", "rules"):
                break
        _stop(server, kill=True)
        server = _serve(project, port)
        page.reload()
        page.wait_for_selector("table.graph-table")
        after = page.locator('[data-graph="status"]').inner_text()
        _shots(page, "graph-after-restart")
        banner = page.locator("#applying")
        notice = page.locator("#rerun-notice")
        # Wait for what the page says about the job (the banner or the opening notice, whichever
        # it says it in), not for five seconds. On a loaded machine the reloaded page may not have
        # asked the new server yet, and the fixture would go on with nothing to report. The guard
        # only bounds a page that never says anything. The wait returns the moment either has words.
        with contextlib.suppress(sync_api.TimeoutError):
            page.wait_for_function(
                "() => ['applying', 'rerun-notice'].some((id) => {"
                " const node = document.getElementById(id);"
                " return node && node.getClientRects().length > 0"
                " && node.textContent.trim() !== '';"
                " })",
                timeout=HANG_GUARD_MS,
            )
        yield {
            "before": before,
            "after": after,
            "banner_visible": banner.is_visible(),
            "banner": banner.inner_text().strip() if banner.is_visible() else "",
            # A job whose worker died with the server waits for a person's Re-run, and the
            # opening notice says it stopped.
            "notice": notice.inner_text().strip() if notice.is_visible() else "",
        }
    finally:
        page.close()
        _stop(server)


def test_the_graph_still_draws_from_a_published_run_after_the_server_is_killed_mid_job(
    restarted,
):
    # A run is named as History names it, "Scan 5", never by its identifier.
    assert re.search(r"\bScan \d+", restarted["before"])
    after = restarted["after"]
    assert re.search(r"\bScan \d+", after), "the reloaded page draws from a published run"


def test_the_page_says_what_became_of_a_job_the_killed_server_was_running(restarted):
    """Durable jobs and the page's banner reading the ledger: after a kill mid-job and a restart,
    the page says what became of the job."""
    assert (restarted["banner_visible"] and restarted["banner"]) or restarted["notice"].startswith(
        "A Re-run stopped when the Dashboard last closed."
    ), restarted
