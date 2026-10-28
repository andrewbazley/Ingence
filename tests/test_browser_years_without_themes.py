"""The Years view where themes cannot be drawn, in a real browser.

Beside ``test_browser_years.py`` and skipped the same way where the ``browser`` extra or a
browser is missing. The Dashboard is served on a copy of the invented theme pool by a process in
which scikit-learn and scipy cannot be imported (a ``sitecustomize.py`` in a folder of the test's
own hides them), the state of an installation without the themes packages.

1. Experiments are dated from their names on a time axis that matches the dates, never "undated"
   for a folder named with a date.
2. The page says what is missing in plain words and how to get it, with no component or package
   import name in it.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

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

    base = tmp_path_factory.mktemp("years_without_themes")
    project, _ = build_theme_project(base / "built")
    copy = base / "project"
    shutil.copytree(project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    port = _free_port()
    hidden = base / "hidden-packages"
    hidden.mkdir()
    (hidden / "sitecustomize.py").write_text(
        "import sys\nsys.modules['sklearn'] = None\nsys.modules['scipy'] = None\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "INGENCE_CONFIG_HOME": str(config_home),
        "PYTHONPATH": os.pathsep.join(
            [
                str(hidden),
                str(REPOSITORY / "src"),
                str(REPOSITORY / "tests"),
                os.environ.get("PYTHONPATH", ""),
            ]
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


def test_the_page_dates_experiments_and_says_what_is_missing(
    browser: Any, served: dict[str, Any]
) -> None:
    from fixtures.theme_pool import UNDATED

    base = served["base"]
    try:
        urllib.request.urlopen(f"{base}api/v1/themes", timeout=10)
    except urllib.error.HTTPError as refused:
        assert refused.code == 503
    else:
        pytest.fail("the themes route should refuse where scikit-learn and scipy are hidden")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    model = page.evaluate(
        "window.ingenceYears.load().then(m => ({computed: m.themesComputed, said: m.themesSaid,"
        " total: m.exps.length, undated: m.exps.filter(e => Number.isNaN(e.at)).length,"
        " years: m.years}))"
    )
    assert model["computed"] is False
    assert model["total"] > len(UNDATED)
    assert model["undated"] == len(UNDATED), "only the experiments no name dates are undated"
    assert model["years"], "the axis has years, from the names"
    said = model["said"]
    assert "scikit-learn and scipy" in said or "switched off" in said
    for developer_word in ("sklearn", "lsa_louvain", "theme_engine"):
        assert developer_word not in said
    rows = page.eval_on_selector_all(".yrow .ylabel", "nodes => nodes.map(n => n.textContent)")
    assert len(rows) == 3, "a row per invented person"
    facts = page.text_content("#detail")
    assert "not computed" in facts
    for developer_word in ("sklearn", "lsa_louvain", "theme_engine"):
        assert developer_word not in facts
    _shots(page, "36-years-without-themes-project")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        "document.querySelector('.years h2') "
        "&& !document.querySelector('.years h2').textContent.startsWith('The project')"
    )
    hint = page.text_content(".yhint")
    assert "Themes are not drawn" in hint
    assert "dated from their names" in hint
    assert hint.strip().endswith("scikit-learn scipy"), "no full stop is copied with the command"
    for developer_word in ("sklearn", "lsa_louvain", "theme_engine"):
        assert developer_word not in hint
    shown = page.eval_on_selector_all(".yrow .ysub", "nodes => nodes.map(n => n.textContent)")
    assert shown, "the person's experiments are rows"
    _shots(page, "36-years-without-themes-person")
    page.close()
