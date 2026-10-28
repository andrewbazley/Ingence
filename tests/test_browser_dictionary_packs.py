"""The Dictionary's packs driven in a real browser.

Skipped where the ``browser`` extra or a browser is missing, as ``test_browser_dashboard.py`` is,
whose harness this copies. Who decides is kept in a folder of the test's own
(``INGENCE_CONFIG_HOME``). Every name here is invented.

The Packs part lists the spellings that the core and a loaded pack share, folded away, and a
spelling is a search. Every shipped section stands level with the core, so such a spelling is a
tie, read by its folder's files. No pack is asked about, not even one that reads nothing here. A
refusal in the answer log still stops loading it, and the Dictionary page says so.
"""

from __future__ import annotations

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
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_dictionary_packs")
    pool = base / "pool"
    (pool / "kelt").mkdir(parents=True)
    for index in (1, 2):
        (pool / "kelt" / f"brisa_fov_{index:02d}.csv").write_text(f"a\n{index}\n")
    # Every shipped pack loads, as a project made before packs were named does. The rules in
    # force are the engine's own, the pack rule among them.
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return project.path


@pytest.fixture
def served(built: Path, tmp_path: Path):
    import shutil

    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
    port = _port()
    process = _serve(project, port, config)
    try:
        yield project, f"http://127.0.0.1:{port}/", config
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def _port() -> int:
    """Any free loopback port. A fixed port fails when two of this file's tests run side by side,
    because the second server cannot listen."""
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


def test_the_packs_part_lists_the_spellings_the_core_and_a_pack_share(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#dictionary")
        shared = page.locator("details.dict-collisions")
        shared.wait_for()
        assert (
            "spellings a word list shares with the core or your list"
            in shared.locator("summary").inner_text()
        )
        shared.locator("summary").click()
        row = shared.locator("li", has=page.get_by_role("button", name="fov", exact=True))
        row.wait_for()
        # The core's meaning and the microscopy pack's stand level: neither is bold. It is a tie.
        assert "position" in row.inner_text() and "field of view" in row.inner_text()
        assert row.locator("strong").count() == 0
        assert row.locator(".tag.now").inner_text() == "tie"
        assert row.locator(".tag.origin-domain").inner_text() == "microscopy"
        _shots(page, "dictionary-shared-spellings")
        row.get_by_role("button", name="fov", exact=True).click()
        page.wait_for_function("document.querySelector('input.dict-search').value === 'fov'")
    finally:
        page.close()


def test_a_pack_reading_nothing_is_not_asked_about_and_a_refusal_stops_loading_it(
    browser, served
) -> None:
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event, now
    from ingence.storage.commit import read_pointer

    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#review")
        listed = page.locator("#list")
        listed.locator(".q-stands").wait_for()
        # No pack is asked about, a pack that reads nothing here included.
        assert "Words for" not in listed.inner_text()
        # A refusal in the answer log (an older project's, or one written by hand) stops loading it.
        pointer = read_pointer(project)
        assert pointer is not None
        append_event(
            project,
            kind=RecordEventKind.REJECT,
            actor="wren",
            occurred_at=now(),
            target="pack:clinical",
            against_run_id=pointer.run_id,
        )

        page.goto(f"{base}#dictionary")
        row = page.locator("section.dict-packs tr", has=page.get_by_text("clinical", exact=True))
        row.wait_for()
        assert "not loaded" in row.inner_text()
        assert "refused by wren" in row.inner_text()
        assert "except those refused" in page.locator("section.dict-packs").inner_text()
        _shots(page, "keep-pack-refused")
    finally:
        page.close()
