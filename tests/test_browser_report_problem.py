"""What "Report a problem" states about settings, the version and what it reads.

The sheet's text states a setting only where its value is a switch, a number or one of the words the
Dashboard offers for it. A list, a text or a typed choice is counted and left out. It states the
version with its build where the engine says one. It reads nothing until the sheet is opened. The
sheet itself, the copy, the no-names rule and the failed route are ``test_browser_frame.py``'s.

Skipped where the ``browser`` extra, a browser or the ``themes`` extra is missing, as every browser
test is.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.request
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import urlsplit

import pytest

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser, need, start_dashboard, stop

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

#: A depth typed into settings.yaml as a person might type it, holding an invented name.
TYPED_DEPTH = "Example_Lab_Alice_pool"


class Served(NamedTuple):
    address: str
    base: Path
    project: Path


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    from fixtures.theme_pool import build_theme_project

    base = tmp_path_factory.mktemp("report")
    project, _ = build_theme_project(base / "built")
    copy = base / "project"
    shutil.copytree(project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield Served(address, base, copy)
    finally:
        stop(process)


@pytest.fixture(scope="module")
def served_with_a_typed_depth(
    served: Served, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """A second Dashboard, on a copy of the project whose ``settings.yaml`` types a ``depth`` that
    is none of the offered words. The settings route offers that word as written, so it is among
    the row's choices: the sheet must not state it."""
    base = tmp_path_factory.mktemp("typed-depth")
    copy = base / "project"
    shutil.copytree(served.project, copy)
    with (copy / "settings.yaml").open("a", encoding="utf-8") as written:
        written.write(f"\ndepth: {TYPED_DEPTH}\n")
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield address
    finally:
        stop(process)


def _get(address: str, path: str) -> Any:
    with urllib.request.urlopen(f"{address}{path}", timeout=10) as answer:
        return json.loads(answer.read())


def _go(page: Any, address: str, view: str = "years") -> None:
    page.goto(f"{address}#{view}")
    page.wait_for_selector("#report-link", timeout=HANG_GUARD_MS)
    page.wait_for_load_state("load")


def _text(page: Any) -> str:
    """The sheet's text, with the sheet opened from the footer's link."""
    page.click("#report-link")
    page.wait_for_selector("dialog.report-sheet[open] #report-text", timeout=HANG_GUARD_MS)
    return str(page.inner_text("#report-text"))


def _context(browser: Any) -> Any:
    return browser.new_context(viewport={"width": 1440, "height": 900}).new_page()


def test_the_text_states_the_version_the_build_the_counts_and_the_guide(
    browser: Any, served: Served
) -> None:
    answered = _get(served.address, "api/v1/version")
    project = _get(served.address, "api/project")
    page = _context(browser)
    _go(page, served.address)
    agent = page.evaluate("navigator.userAgent")
    shown = _text(page)
    page.context.close()
    head = f"ingence {answered['engine_version']} · schema {answered['schema_version']}"
    assert shown.startswith(head)
    if answered["engine_commit"]:
        assert f"· build {answered['engine_commit']}" in shown
    assert f"browser: {agent}" in shown
    assert re.search(r"^window: \d+ by \d+ pixels$", shown, re.MULTILINE)
    assert re.search(r"how much is read: depth = \w+; hash\.mode = \w+\.", shown)
    assert f"{project['counts']['entries']:,} files and folders" in shown
    assert f"{project['counts']['claims']:,} results" in shown
    assert f"{len(project['roots'])} data folder" in shown
    assert "How to report a problem without sharing your data: " in shown


def test_the_view_is_named_by_its_fixed_names(browser: Any, served: Served) -> None:
    page = _context(browser)
    _go(page, served.address, "settings")
    page.wait_for_selector("table.settings", timeout=HANG_GUARD_MS)
    shown = _text(page)
    page.context.close()
    assert re.search(r"^view: #settings$", shown, re.MULTILINE)


def _with_settings(page: Any, rows: list[dict[str, Any]]) -> None:
    """Answer the settings route with the engine's own rows plus these."""

    def serve(route: Any) -> None:
        answered = route.fetch()
        found = answered.json()
        found["settings"] = [*found["settings"], *rows]
        route.fulfill(response=answered, json=found)

    page.route("**/api/workspace/settings", serve)


def test_changed_settings_are_stated_as_switch_states_and_numbers_only(
    browser: Any, served: Served
) -> None:
    hostile = "/Users/someone/Example_Lab_Data/private_pool"
    page = _context(browser)
    _with_settings(
        page,
        [
            # A switch, a number and a choice that changed: stated.
            {
                "name": "x.switch",
                "kind": "boolean",
                "value": True,
                "default": False,
                "changed": True,
            },
            {"name": "x.number", "kind": "number", "value": 37, "default": 4, "changed": True},
            {
                "name": "x.choice",
                "kind": "choice",
                "value": "deep",
                "default": "standard",
                "changed": True,
                "choices": ["standard", "deep"],
            },
            # A list, a text and a choice outside its offered words: left out, never read in.
            {"name": "x.list", "kind": "list", "value": [hostile], "default": [], "changed": True},
            {"name": "x.text", "kind": "text", "value": hostile, "default": "", "changed": True},
            {
                "name": "x.outside",
                "kind": "choice",
                "value": hostile,
                "default": "a",
                "changed": True,
                "choices": ["a", "b"],
            },
            # Unchanged: not stated.
            {"name": "x.same", "kind": "boolean", "value": True, "default": True, "changed": False},
        ],
    )
    _go(page, served.address)
    shown = _text(page)
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()
    assert "x.switch = on" in shown and "x.number = 37" in shown and "x.choice = deep" in shown
    assert "3 changed settings hold lists or text and are left out" in shown
    assert "x.same" not in shown and "x.list" not in shown and "x.text" not in shown
    assert "x.outside" not in shown
    assert hostile not in shown and "someone" not in shown and "Example_Lab" not in shown


def test_a_depth_typed_into_the_settings_file_never_reaches_the_text(
    browser: Any, served_with_a_typed_depth: str
) -> None:
    """Through the real route. ``depth`` is text in the settings, so a person can type anything
    there, a name included; the server then adds the word to the row's choices, marked "(as
    written)". The text says it is not one of the offered words, and never the word."""
    row = next(
        found
        for found in _get(served_with_a_typed_depth, "api/workspace/settings")["settings"]
        if found["name"] == "depth"
    )
    # What the text relies on, stated here so a change in the route fails this test, not silently.
    assert row["value"] == TYPED_DEPTH
    assert TYPED_DEPTH in row["choices"], "the route no longer adds the typed word to the choices"
    assert row["choice_words"][json.dumps(TYPED_DEPTH)].endswith("(as written)")
    page = _context(browser)
    _go(page, served_with_a_typed_depth)
    shown = _text(page)
    page.context.close()
    assert "depth = not one of the offered words" in shown
    assert TYPED_DEPTH not in shown and "Example_Lab" not in shown and "Alice" not in shown


def test_a_changed_choice_typed_as_written_is_not_stated(browser: Any, served: Served) -> None:
    """The same rule for any choice the server marks "(as written)", given to the builder."""
    page = _context(browser)
    _go(page, served.address)
    body = page.evaluate(
        "typed => window.ingenceReport.bodyText({settings: [{name: 'x.kind', kind: 'choice',"
        " value: typed, changed: true, choices: ['a', 'b', typed],"
        " choice_words: {[JSON.stringify(typed)]: typed + ' (as written)'}}]})",
        TYPED_DEPTH,
    )
    page.context.close()
    assert "x.kind = not one of the offered words" in body
    assert TYPED_DEPTH not in body


def test_nothing_is_read_for_the_report_until_its_sheet_is_opened(
    browser: Any, served: Served
) -> None:
    """The footer adds no load while the page is drawn. On the Inventory, which asks for neither the
    settings nor the computer itself: the reads are counted before and after the sheet opens, and
    the version is read once for the footer and the sheet together."""
    page = _context(browser)
    asked: list[str] = []
    page.on("request", lambda request: asked.append(urlsplit(request.url).path))
    _go(page, served.address, "inventory")
    page.wait_for_function(
        "document.querySelector('#footer-version').textContent.includes('schema')",
        timeout=HANG_GUARD_MS,
    )
    page.wait_for_load_state("networkidle")
    before = list(asked)
    assert "/api/workspace/settings" not in before, "the settings were read before the sheet"
    assert "/api/v1/reporting" not in before
    projects_before = before.count("/api/project")
    _text(page)
    page.context.close()
    assert asked.count("/api/workspace/settings") == 1
    assert asked.count("/api/v1/reporting") == 1
    assert asked.count("/api/project") == projects_before + 1
    assert asked.count("/api/v1/version") == 1, "the footer and the sheet read the version twice"


def _email_only(page: Any) -> None:
    """Answer ``/api/v1/reporting`` with no new-issue address, so the sheet offers the email
    whatever metadata the installed package carries."""

    def serve(route: Any) -> None:
        answered = route.fetch()
        route.fulfill(response=answered, json={**answered.json(), "new_issue": None})

    page.route("**/api/v1/reporting", serve)


def _routed_version(page: Any, change: dict[str, Any] | None, status: int = 200) -> None:
    """Answer ``/api/v1/version`` with the engine's own answer changed, or with an error."""

    def serve(route: Any) -> None:
        if change is None:
            route.fulfill(status=status, json={"error": "not available"})
            return
        answered = route.fetch()
        route.fulfill(response=answered, json={**answered.json(), **change})

    page.route("**/api/v1/version", serve)


def test_a_version_with_no_commit_is_stated_without_a_build(browser: Any, served: Served) -> None:
    page = _context(browser)
    _email_only(page)
    _routed_version(page, {"engine_version": "0.3.0", "engine_commit": None})
    _go(page, served.address, "settings")
    page.wait_for_function(
        "document.querySelector('#footer-version').textContent.startsWith('ingence 0.3.0')",
        timeout=HANG_GUARD_MS,
    )
    footer = page.inner_text("#footer-version")
    shown = _text(page)
    address = str(page.get_attribute("#report-open", "href"))
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()
    assert footer.startswith("ingence 0.3.0 · schema ")
    assert shown.startswith("ingence 0.3.0 · schema ") and "build" not in shown
    assert "subject=Ingence%20problem%20report%200.3.0" in address


def test_a_version_that_cannot_be_read_is_said_so_and_nothing_else(
    browser: Any, served: Served
) -> None:
    page = _context(browser)
    _email_only(page)
    _routed_version(page, None, status=500)
    # The footer reads the version once the browser is idle: wait for that answer, not for a time.
    with page.expect_response("**/api/v1/version", timeout=HANG_GUARD_MS):
        _go(page, served.address, "settings")
    footer = page.inner_text("#footer-version")
    shown = _text(page)
    address = str(page.get_attribute("#report-open", "href"))
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()
    assert footer == "ingence"
    assert shown.startswith("ingence: the version could not be read.")
    assert "subject=Ingence%20problem%20report&" in address


def test_one_of_a_count_is_not_plural(browser: Any, served: Served) -> None:
    page = _context(browser)
    _go(page, served.address)
    one, many = page.evaluate(
        """() => ['1', '2'].map((n) => window.ingenceReport.bodyText({
          counts: {entries: +n, claims: +n, findings: +n, candidates: +n, rule_failures: +n},
          roots: +n}))"""
    )
    page.context.close()
    assert (
        "project: 1 file or folder \u00b7 1 result \u00b7 1 flag \u00b7 1 pair suggested "
        "\u00b7 1 rule that could not decide \u00b7 1 data folder"
    ) in one
    assert (
        "project: 2 files and folders \u00b7 2 results \u00b7 2 flags \u00b7 2 pairs suggested "
        "\u00b7 2 rules that could not decide \u00b7 2 data folders"
    ) in many
