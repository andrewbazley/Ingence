"""The Dictionary page driven in a real browser.

The tests are skipped where the ``browser`` extra or a browser is missing, as
``test_browser_dashboard.py`` is, whose harness this file copies. Who decides is kept in a folder
of the test's own (``INGENCE_CONFIG_HOME``). Every name here is invented.

The page lists the vocabulary and answers a search as it is typed. A word opens beside the list.
A spelling is added, and one is inactivated and brought back, each written to the Record under the
name set on this machine. The person's own dictionary is exported as a file.
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

from fixtures.dashboard_browser import launch_browser, settled

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]
UNBOUND = "zorbexin"


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
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_dictionary")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_vorn"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"plate_{UNBOUND}_{index:02d}.csv").write_text(f"a\n{index}\n")
        (pool / folder / "plates_brisa.csv").write_text("a\n1\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    return project.path


@pytest.fixture
def served(built: Path, tmp_path: Path):
    import shutil

    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
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


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


def _name_the_decider(page: Any, name: str) -> None:
    dialog = page.locator("dialog.ingence-dialog")
    dialog.get_by_label("Your name").fill(name)
    dialog.get_by_role("button", name="Use this name").click()


def test_the_dictionary_lists_searches_opens_adds_inactivates_and_exports(
    browser, served, tmp_path
) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    try:
        page.goto(f"{base}#dictionary")
        # The section sits under Parameters and lists the vocabulary, fifty rows a page.
        page.locator('nav.sections button[data-view="dictionary"]').wait_for()
        rows = page.locator("table.dictionary tbody tr.row")
        rows.first.wait_for()
        assert rows.count() == 50
        page.locator(".pager").get_by_text("next 50").wait_for()
        _shots(page, "dictionary-list")

        # A search answers as it is typed, by a spelling.
        page.locator("input.dict-search").fill("plates")
        page.wait_for_selector('table.dictionary tr.row[data-term="plate"]')
        page.wait_for_function(
            "document.querySelector('table.dictionary tbody tr.row').dataset.term === 'plate'"
        )

        # A word opens beside the list, with how many names it binds.
        page.locator('table.dictionary tr.row[data-term="plate"]').click()
        detail = page.locator("#detail")
        detail.locator("h2", has_text="plate").wait_for()
        detail.get_by_text("names in this scan").wait_for()
        assert int(detail.locator(".dict-names strong").inner_text()) >= 4
        # The search had already opened this word, and the click asks for it again. The later
        # answer redraws the pane, and a spelling typed in the meantime is emptied out of its box
        # (the page's behavior, seen once in 52 runs at a high load). Type once the page is quiet.
        settled(page)

        # A spelling is added; the first write asks who decides, once.
        detail.get_by_label("Another spelling").fill(UNBOUND)
        detail.get_by_role("button", name="Add").click()
        _name_the_decider(page, "kelt")
        page.wait_for_function(
            f"[...document.querySelectorAll('#detail code.spelling')]"
            f".some((node) => node.textContent === '{UNBOUND}')"
        )
        added = [event for event in _events(project) if event.target == f"term:{UNBOUND}"]
        assert [(event.kind.value, event.actor) for event in added] == [("assign", "kelt")]
        assert json.loads(added[0].payload)["answer"] == "plate"
        _shots(page, "dictionary-added")

        # The same spelling added to another word is said first: one answer per spelling.
        page.locator("input.dict-search").fill("template")
        page.locator('table.dictionary tr.row[data-term="template"]').click()
        page.wait_for_function("document.querySelector('#detail h2').textContent === 'template'")
        detail.get_by_label("Another spelling").fill(UNBOUND)
        detail.get_by_role("button", name="Add").click()
        asked = page.get_by_role("dialog", name="Replace an answer?")
        asked.get_by_text(f"{UNBOUND} already means plate (kelt's answer of").wait_for()
        asked.get_by_role("button", name="Cancel").click()
        asked.wait_for(state="detached")
        assert len([e for e in _events(project) if e.target == f"term:{UNBOUND}"]) == 1
        page.locator("input.dict-search").fill("plates")
        page.locator('table.dictionary tr.row[data-term="plate"]').click()
        page.wait_for_function("document.querySelector('#detail h2').textContent === 'plate'")
        detail.locator("code.spelling", has_text=UNBOUND).wait_for()

        # A spelling is inactivated: greyed, with who and when, and a way back.
        row = detail.locator(".dict-spellings li", has=page.locator("code", has_text="plates"))
        row.get_by_role("button", name="inactivate").click()
        detail.locator(".dict-spellings li.inactive", has_text="plates").wait_for()
        detail.get_by_text("inactive since").first.wait_for()
        assert "by kelt" in detail.locator(".dict-spellings li.inactive").inner_text()
        _shots(page, "dictionary-inactive")
        detail.locator(".dict-spellings li.inactive").get_by_role(
            "button", name="bring back"
        ).click()
        page.wait_for_function("!document.querySelector('#detail .dict-spellings li.inactive')")
        kinds = [
            event.kind.value for event in _events(project) if event.target == "surface:plate:plates"
        ]
        assert kinds == ["reject", "confirm"]

        # The person's own dictionary is exported as a pack section file.
        with page.expect_download() as waiting:
            page.get_by_role("button", name="Export yours").click()
        download = waiting.value
        saved = tmp_path / download.suggested_filename
        download.save_as(saved)
        text = saved.read_text(encoding="utf-8")
        assert saved.suffix == ".yaml"
        assert "term: plate" in text and UNBOUND in text
    finally:
        page.close()
