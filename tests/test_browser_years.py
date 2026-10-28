"""The Years view driven in a real browser, on the engine's data.

Beside ``test_browser_dashboard.py`` and skipped the same way where the ``browser`` extra or a
browser is missing. On the invented theme pool (``fixtures.theme_pool``: three invented people,
six planted lines of work, dated experiment folders), served by ``ingence see`` on a copy, with
who decides kept in a folder of the test's own.

1. The Years view is Overlap's By year mode: a row per area, the project's card beside it with its
   small Wheel. An area opens to its experiments under their lines of work, and a line's heading
   to that line. An experiment opens on its story and lists its files by folder and one by one. A
   file's card names its believed time and the rule.
2. A line of work is named through the Record: the name shows in magenta with the engine's words
   underneath, and survives a reload.
3. The Wheel opens behind its key, and O opens Stability (a section of Advanced). A year typed in
   the search box asks what was going on then. The arrow keys walk the rows and the right arrow
   opens one.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")
pytest.importorskip("sklearn", reason="themes need the themes extra")

REPOSITORY = Path(__file__).resolve().parents[1]
#: A page function: the inspector's kicker reads the given words.
KICKER_IS = (
    "document.querySelector('#detail .ykicker') "
    "&& document.querySelector('#detail .ykicker').textContent.trim() === '%s'"
)


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


def _free_port() -> int:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return int(held.getsockname()[1])


def _wait_for(url: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as answer:
                if answer.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    message = f"{url} did not answer in {seconds} s"
    raise AssertionError(message)


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    """A Dashboard on a copy of the theme project, who decides kept apart."""
    from fixtures.theme_pool import build_theme_project

    base = tmp_path_factory.mktemp("years")
    project, _ = build_theme_project(base / "built")
    copy = base / "project"
    shutil.copytree(project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    port = _free_port()
    environment = {
        **os.environ,
        "INGENCE_CONFIG_HOME": str(config_home),
        "PYTHONPATH": os.pathsep.join(
            [str(REPOSITORY / "src"), str(REPOSITORY / "tests"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "see",
            "-p",
            str(copy),
            "--port",
            str(port),
            "--no-open",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}/"
    try:
        _wait_for(f"{base_url}api/v1/version")
        yield {"base": base_url, "project": copy, "config_home": config_home}
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


def _decide(base: str, actor: str) -> None:
    body = json.dumps({"decider": actor}).encode()
    request = urllib.request.Request(
        f"{base}api/decider",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def _rows(page: Any) -> list[str]:
    return page.eval_on_selector_all(
        ".yrow .ylabel", "nodes => nodes.map(n => n.textContent.trim())"
    )


def test_down_the_scales(browser: Any, served: dict[str, Any]) -> None:
    base = served["base"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    # The Dashboard opens on the Overview. The Years view is Overlap's.
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    assert page.url.endswith("#overlap"), "#years opens Overlap"
    assert page.get_attribute(".ov-mode[data-mode='by_year']", "aria-pressed") == "true", (
        "in its By year mode, the Years view's place"
    )
    rows = _rows(page)
    assert len(rows) == 3, "a row per invented area"
    assert page.text_content("#detail .ykicker").strip() == "The project"
    assert page.query_selector("#detail .ywheel svg") is not None, (
        "the project card carries the small Wheel"
    )
    # The card's four rows, the last of them the experiments in no line.
    page.wait_for_selector("#detail .yc-kv", timeout=HANG_GUARD_MS)
    assert "In no theme" in page.text_content("#detail .yc-kv")
    _shots(page, "35-years-project")
    # The arrow down walks to the first row. The arrow right opens the person (Enter is the
    # inspector, and the right arrow opens one scale down).
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        "document.querySelector('.years h2') "
        "&& !document.querySelector('.years h2').textContent.startsWith('The project')"
    )
    assert page.text_content(".ycrumbs button[aria-current='location']").strip() == rows[0]
    assert page.locator(".years .yhead-row").count() >= 1, (
        "an area's experiments, under their lines"
    )
    assert _rows(page), "an area opens to its experiments"
    _shots(page, "35-years-person")
    # A line's heading opens that line.
    page.click(".years .yhead-row")
    page.wait_for_function("document.querySelectorAll('.ycrumbs .ysep').length >= 2")
    page.wait_for_function(KICKER_IS % "A theme")
    experiments = _rows(page)
    assert experiments
    _shots(page, "35-years-line")
    page.dblclick(".yrow")
    page.wait_for_selector(".years .ystory .st-story", timeout=HANG_GUARD_MS)
    assert page.text_content("#detail .ykicker").strip() == "An experiment"
    page.click(".years .ychip >> text=folders")
    page.wait_for_selector(".ybar input[type='search']", timeout=HANG_GUARD_MS)
    assert "Rows are folders" in page.text_content(".yhint")
    page.click("text=files, one by one")
    page.wait_for_selector(".yrow .ylabel.mono")
    page.click(".yrow")
    page.wait_for_function(KICKER_IS % "A file")
    assert "Believed time" in page.text_content("#detail .yc-lead")
    assert "earliest_credible_time" in page.text_content("#detail .yc-rules")
    _shots(page, "35-years-files")
    page.keyboard.press("Backspace")
    page.wait_for_selector("#detail .ykicker")
    page.close()


def test_a_line_is_named_through_the_record(browser: Any, served: dict[str, Any]) -> None:
    base = served["base"]
    _decide(base, "a. person")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
    first = page.evaluate(
        "window.ingenceYears.load().then(m => ({label: m.themes[0].label, id: m.themes[0].theme}))"
    )
    page.evaluate("id => window.ingenceYears.select({kind: 'theme', id})", first["id"])
    page.wait_for_function(KICKER_IS % "A theme")
    page.click("#detail >> text=Rename…")
    page.wait_for_selector("dialog[open] input")
    page.fill("dialog[open] input", "A line a person named")
    page.click("dialog[open] button.primary")
    page.wait_for_function(
        "document.querySelector('#detail .ytitle.human') !== null", timeout=HANG_GUARD_MS
    )
    assert page.text_content("#detail .ytitle").strip() == "A line a person named"
    assert first["label"] in page.text_content("#detail .yengine"), (
        "the engine's words stay underneath"
    )
    page.reload()
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    held = page.evaluate("window.ingenceYears.load().then(m => m.themes.map(t => t.person_label))")
    assert "A line a person named" in held, "the name survives a reload: it is in the Record"
    _shots(page, "35-years-named")
    page.close()


def test_wheel_overlap_and_a_year(browser: Any, served: dict[str, Any]) -> None:
    base = served["base"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
    page.click("text=Wheel (W)")
    page.wait_for_selector(".years .ywheel path")
    arcs = page.eval_on_selector_all(".years .ywheel path", "nodes => nodes.length")
    assert arcs > 60, "one arc per experiment and per line"
    _shots(page, "35-years-wheel")
    page.keyboard.press("o")
    page.wait_for_selector(".ystab", timeout=HANG_GUARD_MS)
    assert page.url.endswith("#stability"), "O opens Stability, off the main view"
    assert "Kept together each time" in page.text_content(".years")
    # The seeds' agreement and the resolutions tried are drawn, not printed bare.
    assert page.eval_on_selector_all(".years .yseedpic .yaxdot", "nodes => nodes.length") == 28
    assert page.eval_on_selector_all(".years .ysweepcol", "nodes => nodes.length") >= 7
    assert page.eval_on_selector_all(".years .ysweepcol.kept", "nodes => nodes.length") == 1
    assert page.eval_on_selector_all(
        ".years .ysweepbar.na", "nodes => nodes.every(n => n.getBoundingClientRect().height < 4)"
    ), "resolutions where the seeds were not compared are drawn empty, never full height"
    assert "agreement across seeds" not in page.text_content(".years")
    assert page.eval_on_selector_all(".years .ystab .stability", "nodes => nodes.length") >= 6
    assert "Stability" in page.text_content(".years .yseedpics")
    _shots(page, "35-years-overlap")
    # Back is By year as it was left, the Wheel open; Escape returns to the rows.
    page.go_back()
    page.wait_for_selector(".years .ywheel path", timeout=HANG_GUARD_MS)
    page.keyboard.press("Escape")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.fill("#search-box", "2021")
    page.press("#search-box", "Enter")
    page.wait_for_function(
        "document.querySelector('#detail .ytitle') "
        "&& document.querySelector('#detail .ytitle').textContent.includes('2021')",
        timeout=HANG_GUARD_MS,
    )
    assert "experiments dated 2021" in page.text_content("#detail .ysubline")
    _shots(page, "35-years-year")
    page.close()
