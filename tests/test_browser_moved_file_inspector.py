"""A moved file's inspectors say where it came from, and a copy says it was one.

Driven in a real browser, skipped where the ``browser`` extra or a browser is missing, as
``test_browser_dashboard.py`` is (its harness, copied). The served Dashboard keeps who decides in
a folder of the test's own (``INGENCE_CONFIG_HOME``). A copy that keeps its original's name, size
and time, with the original removed, is read as a move. The Dashboard's inspector and the Graph
workspace's show one row, "Moved here", in the same words. Every name is invented.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]
COPIED = "as a copy of kelt_2024/zib_trell_02.csv, the original removed"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _walked(project: Any, nonce: str) -> None:
    """A walk stamped with the moment it began, a second after anything before it."""
    from ingence.core.ids import run_id as make_run_id
    from ingence.scan.census import run_census

    time.sleep(1.1)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_census(project, run_id=make_run_id(started_at=started, nonce=nonce), started_at=started)


@pytest.fixture
def served(tmp_path: Path):
    from ingence.core.project import Project
    from ingence.storage.catalog import open_catalog

    pool = tmp_path / "pool"
    for folder in ("kelt_2024", "vorn_2024"):
        (pool / folder).mkdir(parents=True)
    (pool / "kelt_2024" / "zib_trell_02.csv").write_text("a,b\n3,4\n" * 9, encoding="utf-8")
    (pool / "kelt_2024" / "brisa_notes.txt").write_text("three replicates\n", encoding="utf-8")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    _walked(project, "one")
    shutil.copy2(pool / "kelt_2024" / "zib_trell_02.csv", pool / "vorn_2024" / "zib_trell_02.csv")
    (pool / "kelt_2024" / "zib_trell_02.csv").unlink()
    _walked(project, "two")
    # The rules run once: a project only read, never inferred, shows the census card in
    # place of the Graph workspace, and this test reads the workspace's inspector.
    from ingence.core.ids import run_id as make_run_id
    from ingence.infer.run import run_inference

    time.sleep(1.1)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rules_run = make_run_id(started_at=started, nonce="rules")
    run_inference(project, run_id=rules_run, started_at=started)
    with open_catalog(project.path) as catalog:
        found = catalog.query(
            "SELECT entry_id FROM entries WHERE relative_path = 'vorn_2024/zib_trell_02.csv'"
        ).fetchone()
    assert found is not None
    config = tmp_path / "config"
    config.mkdir()
    port = int(os.environ.get("INGENCE_BROWSER_PORT", "0")) or _free_port()
    process = _serve(project.path, port, config)
    try:
        yield str(found[0]), f"http://127.0.0.1:{port}/"
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(project: Path, port: int, config: Path) -> subprocess.Popen[bytes]:
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
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY / "src"),
        "INGENCE_CONFIG_HOME": str(config),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(project), str(port)],
        env=environment,
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stdout.readline().startswith(b"serving")
    return process


def test_both_inspectors_say_a_copy_was_moved_here_as_a_copy(browser, served) -> None:
    copy_id, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        # The Dashboard's inspector, beside the page, opened on what is looked at.
        page.goto(f"{base}?entry={copy_id}#search")
        detail = page.locator("#detail")
        detail.get_by_text("Moved here", exact=True).wait_for()
        assert detail.get_by_text(COPIED, exact=True).count() == 1
        # Its modification time says its zone, as a run's name does (the machine's own).
        modified = detail.locator("dl.inspector-facts dt:text-is('Modified') + dd").inner_text()
        expected = page.evaluate(
            "() => new Intl.DateTimeFormat(undefined, {timeZoneName: 'short'})"
            ".formatToParts(new Date()).find((part) => part.type === 'timeZoneName').value"
        )
        assert modified.endswith(expected), (modified, expected)
        # The Graph workspace's inspector, the file selected: the same row, the same words.
        page.goto(f"{base}?sel={copy_id}#explorer")
        card = page.locator(".ix-inspector")
        card.get_by_text("Moved here", exact=True).first.wait_for()
        assert card.get_by_text(COPIED, exact=True).count() >= 1
    finally:
        page.close()
