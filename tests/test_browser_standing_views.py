"""The standing views driven in a real browser.

Skipped where the ``browser`` extra or a browser is missing, as every browser test is. Each served
Dashboard keeps who decides in a folder of the test's own (``INGENCE_CONFIG_HOME``). The project is
invented: the theme pool of ``fixtures/theme_pool.py`` with every experiment given six raw
acquisitions, one experiment of one planted line left without its results table (it looks
forgotten) and every experiment of another planted line left without them (a line that stopped).

1. The question sheet is part of Questions; its checks are in
   ``test_browser_questions_conclusions.py``.
2. No analysis found says its header sentence with its denominator. "Analyzed elsewhere" is
   written to the experiment's own target and the row waits for Re-run.
3. What changed lists the runs newest first, the current one open with its sentences.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]

#: The invented person deciding; never a real name.
PERSON = "a. tester"

CENSUS = "run_20260101T000001Z_aaaaaaaaaaaa"
INFER = "run_20260101T000002Z_bbbbbbbbbbbb"


def _shots(page: Any, name: str) -> None:
    """Keep a screenshot where ``INGENCE_BROWSER_SCREENSHOTS`` names a folder (invented names)."""
    folder = os.environ.get("INGENCE_BROWSER_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


def write_standing_pool(root: Path) -> dict[str, list[str]]:
    """The theme pool with six raw acquisitions per experiment. One planted line's first
    experiment and every experiment of another line have no results table. Returns the folders
    left without one, by the planted line."""
    from fixtures.theme_pool import ENGINE_SURFACES, write_theme_pool

    plant = write_theme_pool(root, surfaces=ENGINE_SURFACES)
    by_line: dict[str, list[str]] = {}
    for unit, folder in sorted(plant.folders.items()):
        by_line.setdefault(plant.planted[unit], []).append(folder)
    for folder in plant.folders.values():
        place = root / folder
        first = sorted(place.glob("*_01.nd2"))[0]
        stem = first.name[: -len("_01.nd2")]
        moment = first.stat().st_mtime
        for index in range(4, 7):
            made = place / f"{stem}_{index:02d}.nd2"
            made.write_text(f"{folder} {index}\n")
            os.utime(made, (moment, moment))
    lines = sorted(by_line, key=lambda line: (-len(by_line[line]), line))
    forgotten, stopped = lines[0], lines[1]
    without = {"forgotten": by_line[forgotten][:1], "stopped": list(by_line[stopped])}
    for folders in without.values():
        for folder in folders:
            for table in (root / folder).glob("*_results.csv"):
                table.unlink()
    # A word standing between two senses (as in ``test_review_pages.py``), in names apart from
    # the experiments: the sheet's tied word.
    for folder in ("kelt", "quolt", "zeph"):
        for index in range(3):
            for step in ("raw", "processed"):
                made = root / "notes" / folder / f"vorn_sim_{step}_{index}.csv"
                made.parent.mkdir(parents=True, exist_ok=True)
                made.write_text("a,b\n1,2\n")
    return without


def build_standing_project(base: Path) -> Path:
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "pool"
    pool.mkdir(parents=True)
    write_standing_pool(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFER, started_at="t")
    return project.path


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_standing_project(tmp_path_factory.mktemp("standing"))


@pytest.fixture
def served(built: Path, tmp_path: Path):
    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
    port = _free_port()
    process = _serve(project, port, config)
    try:
        _answered_once(f"http://127.0.0.1:{port}/")
        yield project, f"http://127.0.0.1:{port}/", config
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


#: What a page of this file reads first, and the server computes the first time it is asked.
_FIRST_READS = (
    "api/v1/questions/ranked?leads=true&answered=show&limit=1000",
    "api/v1/completeness/no_analysis_found",
    f"api/v1/manifest?run={INFER}",
    "api/timeline",
)


def _answered_once(url: str) -> None:
    """Wait until the server has answered each page's first read.

    A freshly started server computes these on the first ask (seconds on an idle machine, and a
    multiple of that on a loaded one), and the test's page waits for the first thing it draws for
    thirty seconds. That time belongs to the server's work and not to the page being tested, so the
    work is waited for here, by its answer, before the page opens. The timeout is a guard against
    a server that never answers.
    """
    for path in _FIRST_READS:
        with urllib.request.urlopen(url + path, timeout=900) as answer:
            answer.read()


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


def _page(browser: Any, url: str) -> tuple[Any, list[str]]:
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    return page, errors


def _decide_as(page: Any) -> None:
    """Answer the one question every first decision asks: who is deciding (invented)."""
    dialog = page.locator("dialog[open]")
    dialog.wait_for()
    dialog.locator("input").fill(PERSON)
    dialog.locator("button", has_text="Use this name").click()


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project))


def test_no_analysis_found_says_its_denominator_and_takes_analyzed_elsewhere(
    browser, served
) -> None:
    project, url, _ = served
    page, errors = _page(browser, url + "#found")
    page.locator(".ui-found-row").first.wait_for()
    lead = page.locator(".ui-lead").inner_text()
    assert (
        " of " in lead
        and "experiments with raw data: no analysis found in the files Ingence read." in lead
    )
    listed, judged = lead.split(" experiments")[0].split(" of ")
    assert 0 < int(listed) <= int(judged)
    assert "never analyzed" not in page.locator("#list").inner_text().lower()
    thresholds = page.locator(".ui-thresholds").inner_text()
    assert "completeness.judged_from_acquisitions" in thresholds
    _shots(page, "standing-found")

    row = page.locator(".ui-found-row").first
    button = row.locator("button.ui-word")
    assert button.inner_text() == "Analyzed elsewhere"
    row.locator("input.ui-note").fill("on another drive")
    button.click()
    _decide_as(page)
    row = page.locator(".ui-found-row.ui-answered").first
    row.wait_for()
    assert row.locator(".ui-said").inner_text().startswith("Analyzed elsewhere · waits for Re-run")
    _shots(page, "standing-found-answered")
    (event,) = [e for e in _events(project) if e.target.startswith("analysed_elsewhere:")]
    payload = json.loads(event.payload)
    assert (payload["verdict"], payload["note"], event.actor) == (
        "elsewhere",
        "on another drive",
        PERSON,
    )

    # Read again, the run still lists it (nothing runs by itself) and it still waits.
    page.reload()
    page.locator(".ui-found-row.ui-answered").first.wait_for()
    # Clicking a row opens its experiment in the inspector beside the list.
    page.locator(".ui-found-row .ui-folder").first.click()
    page.locator("#detail").get_by_text("Nothing selected.").wait_for(state="detached")
    assert errors == []
    page.close()


def test_what_changed_lists_the_runs_and_the_current_run_s_sentences(browser, served) -> None:
    _, url, _ = served
    page, errors = _page(browser, url + "#manifest")
    page.locator(".ui-run").first.wait_for()
    runs = page.locator(".ui-run")
    assert runs.count() == 2
    # Newest first, the current one marked and open, its sentences read.
    assert runs.nth(0).get_attribute("data-run") == INFER
    assert runs.nth(0).get_attribute("open") is not None
    assert "current" in runs.nth(0).locator("summary").inner_text()
    runs.nth(0).locator(".ui-said-list li").first.wait_for()
    assert runs.nth(0).locator(".ui-said-list li").count() >= 1
    # The earlier run is read only on opening.
    assert runs.nth(1).locator(".ui-said-list").count() == 0
    runs.nth(1).locator("summary").click()
    runs.nth(1).locator(".ui-said-list li").first.wait_for()
    assert "first run" in runs.nth(1).locator(".ui-said-list").inner_text()
    # The run's quality figures sit under its sentences: every figure beside the run before it,
    # a share as a bar beside its count and its whole, and never a bare percentage.
    quality = runs.nth(0).locator(".ui-quality-all")
    quality.wait_for()
    quality.locator("summary").click()
    quality.locator("table.ui-quality").first.wait_for()
    assert quality.locator("table.ui-quality caption").first.inner_text() == "Found"
    assert quality.locator(".ui-bar").count() > 0
    assert "%" not in quality.inner_text()
    assert "of" in quality.locator("td.ui-quality-now").first.inner_text()
    _shots(page, "standing-manifest")
    assert errors == []
    page.close()
