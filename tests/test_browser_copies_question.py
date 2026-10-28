"""The copies question driven in a real browser.

Beside ``test_browser_dashboard.py``, whose harness this copies, and skipped the same way where
the ``browser`` extra or a browser is missing. The served Dashboard keeps who decides in a folder
of the test's own (``INGENCE_CONFIG_HOME``). The copies question states its reason plainly, first,
and its answers are "Copies", "Unrelated" and "Can't say".

Every name here is invented.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]
#: The ports this file's servers may use.
PORTS = range(9040, 9044)


def _shots(page: Any, name: str) -> None:
    """Keep a screenshot where ``INGENCE_BROWSER_SCREENSHOTS`` names a folder (invented names)."""
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
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three copies of one table agreeing on name, size and last change, and a pair beside."""
    from test_copies_answers import _project

    return _project(tmp_path_factory.mktemp("browser_copies_question")).path


@pytest.fixture
def served(built: Path, tmp_path: Path):
    import shutil

    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
    (config / "decider.json").write_text(json.dumps({"decider": "wren", "set_at": "t"}))
    port = _free_port()
    process = _serve(project, port, config)
    try:
        yield project, f"http://127.0.0.1:{port}/", config
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def _free_port() -> int:
    """The first of this file's ports that nothing holds."""
    for port in PORTS:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("every port this file may use is held")
    return 0


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


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


def test_the_copies_question_leads_with_its_reason_and_offers_three_answers(
    browser, served
) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#classes")
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor === 'wren'")
        row = page.locator("#list tr.row", has_text="brisa_19_quant").first
        row.wait_for()
        row.click()
        detail = page.locator("#detail")
        detail.locator("h2.question", has_text="Copies of one file?").wait_for()
        lead = detail.locator("p.asks").first.inner_text().strip()
        assert lead.startswith("These 3 files have the same name, size and last change, in ")
        # The pattern under it does not say "the same name" again.
        pattern = detail.locator("p", has=page.locator("code")).first.inner_text()
        assert "same name" not in pattern
        shown = [
            text.strip()
            for text in detail.locator(".answers button.answer[data-word]").all_inner_texts()
        ]
        assert shown == ["Copies", "Unrelated", "Can't say"]
        _shots(page, "copies-question")
        detail.locator('button.answer[data-word="not_copies"]').click()
        detail.locator('[data-advance="recorded"], [data-advance="done"]').first.wait_for()
        said = json.loads(_events(project)[-1].payload)
        assert said["verdict"] == "not_copies"
    finally:
        page.close()


def test_a_set_asked_again_because_a_file_joined_says_what_was_answered(browser, tmp_path) -> None:
    """The pane says what was answered and about how many files, not "A rule doubts"."""
    from ingence.scan.census import run_census
    from test_copies_answers import (
        SHARED_INSTANT,
        _project,
        _write,
        answer_row,
        copies_row,
        infer_again,
    )

    project = _project(tmp_path)
    answer_row(project, copies_row(project), "not_copies")
    newcomer = tmp_path / "pool" / "runs" / "2024-03-08_trell" / "brisa_19_quant.csv"
    _write(newcomer, "a,b\n1,2\n3,4\n" * 40)
    os.utime(newcomer, (SHARED_INSTANT, SHARED_INSTANT))
    run_census(project, run_id="run_20260101T000007Z_777777777777", started_at="t")
    infer_again(project, tmp_path, run_id="run_20260101T000008Z_888888888888")
    config = tmp_path / "config"
    config.mkdir()
    (config / "decider.json").write_text(json.dumps({"decider": "wren", "set_at": "t"}))
    port = _free_port()
    process = _serve(project.path, port, config)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"http://127.0.0.1:{port}/#classes")
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor === 'wren'")
        row = page.locator("#list tr.row", has_text="brisa_19_quant").first
        row.wait_for()
        row.click()
        detail = page.locator("#detail")
        detail.locator("h2.question", has_text="Copies of one file?").wait_for()
        text = detail.inner_text()
        assert "Answered “Unrelated” when it held 3 files." in text
        assert "A rule doubts this group." not in text
        _shots(page, "copies-asked-again")
    finally:
        page.close()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()
