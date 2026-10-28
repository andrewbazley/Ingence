"""The footer: the version and a link to the documentation.

On the invented theme pool, served by ``ingence see`` on a copy. The footer says which Ingence this
is, from the engine's own answer (``/api/v1/version``), and links to the documentation site. The
link opens in a new tab and sends no referrer, so the address of this machine's Dashboard (it
carries the port, and the page's address names what is being looked at) is not handed to a site.
Skipped where the ``browser`` extra, a browser or the ``themes`` extra is missing, as every browser
test is.
"""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser, need, serve_theme_project

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

DOCS = "https://ingence.org"


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "footer")


def _version(address: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{address}api/v1/version", timeout=10) as answer:
        found: dict[str, Any] = json.loads(answer.read())
    return found


def _open(browser: Any, served: str, view: str = "years", width: int = 1440) -> Any:
    page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
    page.goto(f"{served}#{view}")
    page.wait_for_function(
        "document.querySelector('#footer-version').textContent.includes('schema')",
        timeout=HANG_GUARD_MS,
    )
    return page


def test_the_footer_names_the_version_and_the_schema_and_no_build(
    browser: Any, served: str
) -> None:
    answered = _version(served)
    page = _open(browser, served)
    shown = page.inner_text("#footer-version")
    page.context.close()
    assert (
        shown == f"ingence {answered['engine_version']} \u00b7 schema {answered['schema_version']}"
    )
    # The build is in the text of a problem report, never in the footer.
    assert "build" not in shown and (answered["engine_commit"] or "x") not in shown


def test_the_footer_links_to_the_documentation_in_a_new_tab(browser: Any, served: str) -> None:
    page = _open(browser, served)
    link = page.locator("#docs-link")
    assert link.inner_text() == "Documentation"
    assert link.get_attribute("href") == DOCS
    assert link.get_attribute("target") == "_blank"
    relation = (link.get_attribute("rel") or "").split()
    page.context.close()
    assert "noopener" in relation and "noreferrer" in relation


def test_the_footer_stays_on_every_page_and_fits_a_narrow_window(browser: Any, served: str) -> None:
    page = _open(browser, served, view="settings", width=1024)
    assert page.is_visible("#docs-link") and page.is_visible("#footer-version")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    page.context.close()
    assert overflow <= 0, "the footer made the page scroll sideways"


def test_the_footer_links_take_focus_and_show_it(browser: Any, served: str) -> None:
    page = _open(browser, served)
    page.focus("#docs-link")
    ring = page.evaluate(
        "(() => { const s = getComputedStyle(document.activeElement);"
        " return [document.activeElement.id, s.outlineStyle, parseFloat(s.outlineWidth)]; })()"
    )
    page.context.close()
    assert ring[0] == "docs-link"
    assert ring[1] != "none" and ring[2] > 0
