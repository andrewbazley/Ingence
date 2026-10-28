"""The Rules page of a project with an old rules file says what changed, in words.

The engine's own rules apply to every project. A project still holding its old rules file is told
once that the file is no longer read, with the choice it made there that still applies. A rule out
of force says why in words, never "superseded at revision 4". The tests are skipped, like their
neighbours, where the ``browser`` extra or a browser is missing. Every served Dashboard keeps who
decides in a folder of the test's own (``INGENCE_CONFIG_HOME``). Every name is invented.
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


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project made the old way: its rules file holds each deviation, chosen by a person."""
    from fixtures.revision_4 import retired_form, write_retired
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_old_rules_file")
    pool = base / "pool"
    for folder in ("2024-03-05 kelt vorn", "2024-03-06 kelt vorn"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"kelt_vorn_{index:02d}.nd2").write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    chosen = retired_form()
    for rule in chosen["rules"]:
        for parameter in rule.get("parameters", ()):
            if parameter["name"] == "derivations":
                parameter["default"] = "each_deviation"
    write_retired(project.path, chosen)
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return project.path


@pytest.fixture(scope="module")
def served(built: Path, tmp_path_factory: pytest.TempPathFactory):
    config = tmp_path_factory.mktemp("old_rules_file_config")
    port = _free_port()
    process = _serve(built, port, config)
    try:
        yield built, f"http://127.0.0.1:{port}/"
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


def test_the_rules_page_says_the_old_file_is_no_longer_read_and_why_rules_are_out(
    browser: Any, served: Any
) -> None:
    _, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#rules")
        page.locator("#list h2", has_text="Rules").wait_for()
        note = page.locator("#list p.note", has_text="no longer read")
        note.wait_for()
        said = note.inner_text()
        assert "now part of Ingence" in said
        assert "Your choices in it still apply" in said
        assert "each deviation" in said
        page.locator("details.rules-inactive > summary").click()
        inactive = page.locator("details.rules-inactive").inner_text()
        assert "replaced by a newer rule" in inactive
        text = page.locator("main").inner_text()
        assert "revision 4" not in text.replace("01-revision-4.yaml", "")
        assert "project revision" not in text
    finally:
        page.close()
