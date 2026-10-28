"""The design grid drawn on an experiment's page, in a real browser.

Beside ``test_browser_years.py`` and skipped the same way where the ``browser`` extra or a
browser is missing. On the invented pool of ``fixtures.design_pool``, served by ``ingence see`` on
a copy with who decides kept in a folder of the test's own.

The behaviour: the experiment page draws the grid its route serves: levels on the axes, a cell
shaded by its replicates, hatched where the combination was not found, outlined where the output
is empty. The question for a person is shown. An experiment with no grid says why. Nothing leaves
the page wider than the window.
"""

from __future__ import annotations

import os
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

REPOSITORY = Path(__file__).resolve().parents[1]
CENSUS = "run_20260101T000001Z_111111111111"
INFER = "run_20260101T000002Z_222222222222"


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
    from fixtures.design_pool import project_with_the_grid_on, write_pool
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("design_browser")
    pool = write_pool(base / "pool")
    project = project_with_the_grid_on(base / "project", [pool])
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFER, started_at="t")
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
            str(project.path),
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
        yield {"base": base_url, "project": project.path}
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


def _open(page: Any, base: str, folder: str) -> None:
    page.goto(f"{base}#years")
    page.wait_for_function("window.ingenceYears !== undefined", timeout=HANG_GUARD_MS)
    found = page.evaluate(
        """async (folder) => {
            const answer = await fetch('/api/v1/design').then(r => r.json());
            return answer.experiments.find(e => e.folder === folder).id;
        }""",
        folder,
    )
    page.evaluate(
        "async (id) => { await window.ingenceYears.load();"
        " window.ingenceYears.go([{kind: 'project'}, {kind: 'exp', id}]); }",
        found,
    )
    page.wait_for_selector(".ybar .ychip", timeout=HANG_GUARD_MS)
    page.click(".ybar .ychip[data-help='years.design']")
    page.wait_for_selector(".ydesign", timeout=HANG_GUARD_MS)


def test_the_grid_is_drawn_with_its_levels_and_its_states(
    browser: Any, served: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))
    _open(page, served["base"], "20261012_HeLa_drug_time")
    page.wait_for_selector(".dgrid .dcell")
    heads = page.eval_on_selector_all(".dgrid .dhead", "nodes => nodes.map(n => n.textContent)")
    assert {"DMSO", "taxol", "0h", "2h"} <= set(heads), heads
    states = page.eval_on_selector_all(".dgrid .dcell", "nodes => nodes.map(n => n.dataset.state)")
    assert sorted(states) == ["not found", "occupied", "occupied", "occupied"]
    missing = page.query_selector(".dgrid .dcell.dnotfound")
    assert missing is not None
    assert "gradient" in missing.evaluate("n => getComputedStyle(n).backgroundImage")
    numbers = page.eval_on_selector_all(".dgrid .dnum", "nodes => nodes.map(n => n.textContent)")
    assert numbers == ["3", "3", "3"], "each combination was repeated three times"
    assert "Combination not found: taxol / 2h." in page.text_content(".dfinding")
    text = page.text_content(".ydesign") or ""
    assert "missing" not in text.casefold()
    assert "partly crossed" in text
    assert "A factor is a part of the file names" in text
    assert "word" not in text.casefold().replace("words", "")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not problems, problems
    _shots(page, "36-design-crossed")
    page.close()


def test_a_nested_design_lists_no_combination_as_not_found(
    browser: Any, served: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _open(page, served["base"], "20261013_HeLa_nested")
    page.wait_for_selector(".dgrid .dcell")
    assert page.query_selector(".dgrid .dcell.dnotfound") is None
    assert "nested" in (page.text_content(".ydesign") or "")
    _shots(page, "36-design-nested")
    page.close()


def test_an_empty_output_is_outlined_and_a_ladder_and_a_merge_are_said(
    browser: Any, served: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _open(page, served["base"], "20261019_HeLa_empty")
    page.wait_for_selector(".dgrid .dcell.dempty")
    outlined = page.query_selector(".dgrid .dcell.dempty")
    assert outlined is not None
    assert outlined.evaluate("n => getComputedStyle(n).boxShadow") != "none"
    page.evaluate(
        "async () => { const m = await window.ingenceYears.load();"
        " const a = await fetch('/api/v1/design').then(r => r.json());"
        " const id = a.experiments.find(e => e.folder === '20261014_HeLa_dose').id;"
        " window.ingenceYears.go([{kind: 'project'}, {kind: 'exp', id}]); }"
    )
    page.wait_for_function(
        "document.querySelector('.ydesign') && document.body.textContent.includes('a ladder')"
    )
    assert "each level is 10 times the one before" in (page.text_content(".ydesign") or "")
    page.evaluate(
        "async () => { const a = await fetch('/api/v1/design').then(r => r.json());"
        " const id = a.experiments.find(e => e.folder === '20261015_HeLa_channels').id;"
        " window.ingenceYears.go([{kind: 'project'}, {kind: 'exp', id}]); }"
    )
    page.wait_for_function("document.body.textContent.includes('one exception in 14')")
    _shots(page, "36-design-merged")
    page.close()


def test_an_experiment_with_no_grid_says_why(browser: Any, served: dict[str, Any]) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _open(page, served["base"], "20261016_HeLa_series")
    page.wait_for_selector(".ydesign[data-grid='none']")
    assert "No grid is drawn" in (page.text_content(".ydesign") or "")
    assert page.query_selector(".dgrid") is None
    page.close()


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_grid_passes_the_accessibility_checker_in_light_and_dark(
    browser: Any, served: dict[str, Any], scheme: str
) -> None:
    script = _axe_script()
    if script is None:
        pytest.skip("axe-core is not installed: run `npm install`, or set INGENCE_AXE_JS")
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
    _open(page, served["base"], "20261012_HeLa_drug_time")
    page.wait_for_selector(".dgrid .dcell")
    page.add_script_tag(content=script.read_text(encoding="utf-8"))
    result = page.evaluate(
        "async () => (await axe.run(document.querySelector('.ydesign'), "
        "{runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']})).violations"
        ".filter(v => ['critical', 'serious'].includes(v.impact))"
        ".map(v => v.id + ': ' + v.nodes.length)"
    )
    assert result == [], result
    page.close()
