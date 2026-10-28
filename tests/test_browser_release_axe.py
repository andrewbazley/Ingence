"""An accessibility lane: axe-core on every Dashboard view, in light and in dark.

axe-core is a checker that reads a page the way the Web Content Accessibility Guidelines (WCAG 2.2,
levels A and AA) do: missing names on controls, text too faint to read, targets too small to press,
broken pairs of ARIA attributes.

axe-core is not shipped and not vendored. It is a development dependency named in
``package.json`` (a pinned version, installed by ``npm install`` into the git-ignored
``node_modules/``). This test reads ``node_modules/axe-core/axe.min.js``, or the file named by the
environment variable ``INGENCE_AXE_JS``, and is skipped where neither exists, as every browser
test is skipped where Playwright or a browser is missing. CI's browser job sets
``INGENCE_BROWSER_REQUIRED=1``, and there a missing axe-core, Playwright, browser or scikit-learn is
a failure, so the job cannot pass having tested nothing.

What is checked, each on the invented theme pool served by ``ingence see`` (who decides kept in a
folder of the test's own), and on the landing page served with no project:

1. Every page of the Dashboard and every section of every page, found from the page's own
   navigation so a section added later is covered without a change here.
2. The states between: the search box's list of words open, Ask's results, the Wheel and Overlap,
   a person drilled down to an experiment's files and one file, the Re-run summary, the help's
   pointing mode, and a dialog.
3. The landing page: as it opens, with a file typed where a folder belongs, and with a folder
   checked.

A page fails on any violation axe rates critical or serious. The message names the rule, how
many elements, and the first few. Lesser findings (moderate, minor) are not failures here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    REPOSITORY,
    REQUIRED,
    launch_browser,
    need,
    serve_theme_project,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


AXE_SCRIPT = _axe_script()
NO_AXE = "axe-core is not installed: run `npm install` in the repository, or set INGENCE_AXE_JS"
# Skipped where axe-core is missing, as every browser test is where its tool is. Where
# INGENCE_BROWSER_REQUIRED is set (CI's browser job) the tests run, and each fails on the fixture
# below instead.
pytestmark = pytest.mark.skipif(AXE_SCRIPT is None and not REQUIRED, reason=NO_AXE)


@pytest.fixture(autouse=True)
def _axe_is_installed() -> None:
    if AXE_SCRIPT is None:
        pytest.fail(f"{NO_AXE} (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)


#: What the guidelines call levels A and AA, up to WCAG 2.2.
STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
FAILING = ("critical", "serious")
SCHEMES = ("light", "dark")
#: Pages and sections the Dashboard must offer: found from its navigation, and these must be in it.
KNOWN_VIEWS = {
    # The Overview and Checks are sections, and Ask lists results. Overlap (Connections and By
    # year) holds the Years view. Stability is Advanced's.
    "overview",
    "checks",
    "overlap",
    "stability",
    "experiments",
    "topics",
    "found",
    "inventory",
    "explorer",
    "graph",
    "map",
    "words",
    "review",
    "classes",
    "claims",
    "candidates",
    "extensions",
    "programs",
    "coverage",
    "changes",
    "manifest",
    "rules",
    "dictionary",
    "settings",
    "ladders",
}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Dashboard on a copy of the invented theme project. The tests that go through the states
    set someone as deciding on it (a dialog asks), and so does the page-by-page pass for "someone
    deciding": it is not the server of any test that checks "nobody deciding". One answer waits
    for Re-run, so the badge and its summary are among the states checked."""
    yield from serve_theme_project(tmp_path_factory, "axe", waiting=True)


@pytest.fixture(scope="module")
def served_undecided(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Dashboard of its own, on which no test ever sets anyone as deciding: the project as a
    newcomer opens it, whatever order the tests run in."""
    yield from serve_theme_project(tmp_path_factory, "axe-undecided")


@pytest.fixture(scope="module")
def landing(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """The landing page, served with no project, and an invented folder to check."""
    base = tmp_path_factory.mktemp("axe-landing")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt", "2024-03-07_vorn"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2, 3):
            (pool / folder / f"zib_glorbex_wd_{index:02d}.csv").write_text(f"a\n{index}\n")
    (pool / "tamen notes.txt").write_text("brisa\n")
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(None, config_home)
    try:
        yield address, pool
    finally:
        stop(process)


def _new_page(browser: Any, scheme: str) -> Any:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    page.add_init_script("window.__problems = []")
    return page


def _violations(page: Any, where: str) -> list[str]:
    """What axe finds on the page as it stands, serious or worse, in words; empty when none."""
    assert AXE_SCRIPT is not None
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.add_script_tag(content=AXE_SCRIPT.read_text(encoding="utf-8"))
    found = page.evaluate(
        "options => window.axe.run(document, options)",
        {"runOnly": {"type": "tag", "values": STANDARDS}},
    )
    said: list[str] = []
    for violation in found["violations"]:
        if violation["impact"] not in FAILING:
            continue
        nodes = []
        for node in violation["nodes"][:3]:
            colors = ""
            for check in node["any"] + node["all"] + node["none"]:
                data = check.get("data") or {}
                if isinstance(data, dict) and "contrastRatio" in data:
                    colors = (
                        f" ({data.get('fgColor')} on {data.get('bgColor')}, "
                        f"{data.get('contrastRatio')} to 1, "
                        f"needs {data.get('expectedContrastRatio')})"
                    )
            reason = " ".join(
                str(check["message"])[:160] for check in (node["any"] + node["all"] + node["none"])
            )
            nodes.append(
                f"{'/'.join(map(str, node['target']))}{colors}: {node['html'][:90]} [{reason}]"
            )
        said.append(
            f"{where}: {violation['impact']} {violation['id']} on {len(violation['nodes'])} "
            f"element(s): {violation['help']}; first: {json.dumps(nodes)}"
        )
    return said


def _open_view(page: Any, view: str) -> None:
    """Show a view by its address and wait until it has drawn something."""
    page.evaluate("view => { location.hash = '#' + view; }", view)
    page.wait_for_function(
        "view => { const on = document.querySelector('nav.sections button.active');"
        " return on && on.dataset.view === view; }",
        arg=view,
        timeout=HANG_GUARD_MS,
    )
    if view == "explorer":
        page.wait_for_selector(".ix-root canvas", timeout=HANG_GUARD_MS)
    else:
        page.wait_for_function(
            "() => { const list = document.querySelector('#list');"
            " return list && list.children.length > 0 && !list.querySelector('.state-loading'); }",
            timeout=HANG_GUARD_MS,
        )
    settled(page)


def _decide(base: str, actor: str) -> None:
    import urllib.request

    request = urllib.request.Request(
        f"{base}api/decider",
        data=json.dumps({"decider": actor}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


@pytest.mark.parametrize(
    ("scheme", "decided"),
    [("light", False), ("dark", False), ("light", True), ("dark", True)],
    ids=["light", "dark", "light-someone-deciding", "dark-someone-deciding"],
)
def test_every_page_and_section_has_no_serious_violation(
    browser: Any, request: pytest.FixtureRequest, scheme: str, decided: bool
) -> None:
    """Every page, with nobody set as deciding (the project as a newcomer opens it) and with
    someone set (the line about who decides changes, and so do the answers' buttons). Each case has
    a server of its own kind, so the order the tests run in cannot change what a case checks."""
    served: str = request.getfixturevalue("served" if decided else "served_undecided")
    if decided:
        _decide(served, "a. person")
    page = _new_page(browser, scheme)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    if not decided:
        page.wait_for_function(
            "document.querySelector('#decider').textContent.includes('No name is set')",
            timeout=HANG_GUARD_MS,
        )
    views = page.eval_on_selector_all(
        "nav.sections button[data-view]", "nodes => nodes.map(n => n.dataset.view)"
    )
    assert set(views) >= KNOWN_VIEWS, (
        f"the navigation lost views: {sorted(KNOWN_VIEWS - set(views))}"
    )
    pages = page.eval_on_selector_all("#pages button", "nodes => nodes.map(n => n.dataset.page)")
    assert pages == ["overview", "overlap", "experiments", "checks", "history", "advanced"]
    problems: list[str] = []
    for view in views:
        _open_view(page, view)
        problems += _violations(page, f"{scheme} {view}{' (someone deciding)' if decided else ''}")
    if decided:
        assert "a. person" in page.inner_text("#decider"), "someone is set as deciding"
    page.context.close()
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_states_between_have_no_serious_violation(
    browser: Any, served: str, scheme: str
) -> None:
    _decide(served, "a. person")
    page = _new_page(browser, scheme)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    problems: list[str] = []

    # 1. The search box's list of words, open: the combobox pattern, whole.
    box = page.locator("#search-box")
    assert box.get_attribute("role") == "combobox"
    assert box.get_attribute("aria-expanded") == "false", "closed until words are suggested"
    controlled = box.get_attribute("aria-controls")
    assert controlled and page.query_selector(f"#{controlled}") is not None, (
        "the list the box controls exists whenever it is named"
    )
    box.click()
    box.type("ke")
    page.wait_for_selector(f"#{controlled}:not([hidden]) [role=option]", timeout=15000)
    assert box.get_attribute("aria-expanded") == "true", "open while words are shown"
    problems += _violations(page, f"{scheme} the list of words open")
    box.press("ArrowDown")
    assert box.get_attribute("aria-activedescendant"), "the chosen word is named"
    box.press("Escape")
    assert box.get_attribute("aria-expanded") == "false", "closed again by Escape"
    assert box.get_attribute("aria-activedescendant") is None

    # 2. Ask's results.
    box.fill("keltan")
    box.press("Enter")
    page.wait_for_selector("[data-ask-view]", timeout=HANG_GUARD_MS)
    settled(page)
    problems += _violations(page, f"{scheme} Ask's results")

    # 3. The Wheel, with the project's card beside it. O opens Stability.
    page.evaluate("location.hash = '#years'")
    page.wait_for_selector(".years .yrow")
    page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
    page.click("text=Wheel (W)")
    page.wait_for_selector(".ybig .ywheel path")
    problems += _violations(page, f"{scheme} the Wheel")
    page.keyboard.press("o")
    page.wait_for_selector(".ystab", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} Stability")
    # Overlap's Connections, and a line opened to its evidence.
    page.evaluate("window.ingenceOverlap.setMode('connections'); location.hash = '#overlap'")
    page.wait_for_selector(".ov-canvas[data-nodes]", timeout=HANG_GUARD_MS)
    settled(page)
    problems += _violations(page, f"{scheme} Connections")
    page.click(".ov-list summary")
    page.click(".ov-list tbody tr:first-child button")
    page.wait_for_selector("#detail .ov-evidence", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} a line's evidence")
    page.click(".ov-mode[data-mode='by_year']")
    page.wait_for_selector(".years .ywheel, .years .yrow", timeout=HANG_GUARD_MS)
    page.keyboard.press("Escape")
    page.wait_for_selector(".years .yrow")

    # 4. Down the scales: an area, its experiments, an experiment's story, its files, one file.
    page.dblclick(".yrow")
    page.wait_for_function("document.querySelectorAll('.ycrumbs .ysep').length >= 1")
    problems += _violations(page, f"{scheme} an area")
    page.dblclick(".yrow")
    page.wait_for_selector(".years .ystory .st-story", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} an experiment's story")
    # Each repeat links to an experiment by its id, never to an object written as text.
    links = page.locator(".st-repeats .st-link")
    assert links.count() >= 1, "the first area's first experiment has repeats"
    for i in range(links.count()):
        named = links.nth(i).get_attribute("data-experiment") or ""
        assert named and "[object" not in named + links.nth(i).inner_text(), named
    page.click(".years .ychip >> text=folders")
    page.wait_for_selector(".ybar input[type='search']", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} an experiment")
    page.click("text=files, one by one")
    page.wait_for_selector(".yrow .ylabel.mono")
    page.click(".yrow")
    page.wait_for_selector("#detail .yc-rules")
    problems += _violations(page, f"{scheme} a file")

    # 5. A dialog: naming a line of work.
    page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
    first = page.evaluate("window.ingenceYears.load().then(m => m.themes[0].theme)")
    page.evaluate("id => window.ingenceYears.select({kind: 'theme', id})", first)
    page.click("#detail >> text=Rename…")
    page.wait_for_selector("dialog[open] input")
    problems += _violations(page, f"{scheme} a dialog")
    page.keyboard.press("Escape")

    # 6. What waits for Re-run, opened from its badge.
    badge = page.locator("#rerun-badge")
    assert badge.is_visible(), "the invented project has a change waiting, so the badge shows"
    box_size = badge.bounding_box()
    assert box_size and box_size["width"] >= 24 and box_size["height"] >= 24
    badge.click()
    page.wait_for_selector("#rerun-summary:not([hidden])")
    problems += _violations(page, f"{scheme} the Re-run summary")
    badge.click()

    # 7. The help, pointing at every control.
    page.click("#help-button")
    page.wait_for_selector("dialog.shortcuts-sheet[open]", timeout=15000)
    problems += _violations(page, f"{scheme} the shortcuts sheet")
    page.click("#shortcuts-point")
    page.wait_for_selector(".help-marker", timeout=15000)
    problems += _violations(page, f"{scheme} the help pointing")
    page.context.close()
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_landing_page_has_no_serious_violation(
    browser: Any, landing: tuple[str, Path], scheme: str
) -> None:
    address, pool = landing
    page = _new_page(browser, scheme)
    page.goto(address)
    page.wait_for_selector("#ui-root")
    problems = _violations(page, f"{scheme} the landing page")
    # A file where a folder belongs: refused in words.
    page.fill("#ui-root", str(pool / "tamen notes.txt"))
    page.click("#ui-check-button")
    page.wait_for_selector(".ui-problem")
    problems += _violations(page, f"{scheme} the landing page, a file refused")
    # A folder, checked.
    page.fill("#ui-root", str(pool))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    problems += _violations(page, f"{scheme} the landing page, a folder checked")
    page.context.close()
    assert problems == [], "\n".join(problems)
