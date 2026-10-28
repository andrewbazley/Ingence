"""Keyboard paths through every view.

A person who uses the keyboard alone meets the Dashboard through Tab and Escape. These tests press
the keys in a real browser, on the invented theme pool (``fixtures.theme_pool``) served by
``ingence see`` on a copy, and read what the browser did:

1. Tab through each view. For every page and section found from the page's own navigation (so a
   section added later is covered without a change here), and for the landing page: focus moves
   down the page in the order of the page (never back up, never twice to the same control), every
   control it stops at is visible, shows a ring (an outline or a shadow), and is not covered by
   the sticky header. The first sixty stops of a view are walked; some views hold hundreds.
2. The footer's two links follow each other, forward and back.
3. Escape closes what a key opened: the search box's list of words, the Re-run summary, the
   help's pointing mode, the help's tip, a dialog, the Wheel and Overlap, the Graph page's box for
   choosing another folder, and the Graph workspace's Arrange panel. A dialog gives focus back to
   what opened it, and keeps Tab and Shift+Tab inside itself while it is open.

Skipped where the ``browser`` extra, a browser or the ``themes`` extra is missing, as every browser
test is. Run a failing view alone before deciding it is load: the tests wait for each view to draw.
"""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    launch_browser,
    need,
    serve_theme_project,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

#: How many stops of a view are walked. A view with a table of hundreds of rows has hundreds.
MOST_STOPS = 60

#: A page function. Where focus is now: what it is, whether a person can see it and its ring, and
#: whether it comes after the control before it in the page. ``null`` once focus has left the page.
WHERE_FOCUS_IS = """() => {
  const e = document.activeElement;
  if (!e || e === document.body || e === document.documentElement) return null;
  const memory = (window.__stops = window.__stops || {previous: null, seen: new WeakSet()});
  const name = (e.getAttribute('aria-label') || e.textContent || e.id || e.tagName)
    .trim().slice(0, 50);
  const box = e.getBoundingClientRect();
  const style = getComputedStyle(e);
  const problems = [];
  const shown = box.width > 0 && box.height > 0
    && style.visibility !== 'hidden' && style.display !== 'none';
  if (!shown) problems.push('is not visible');
  const ring = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
  const shadow = style.boxShadow !== 'none';
  if (!ring && !shadow) problems.push('shows no ring (no outline and no shadow)');
  // The part of the control that is inside the window, and what is on top of its middle.
  const left = Math.max(box.left, 0), right = Math.min(box.right, innerWidth);
  const top = Math.max(box.top, 0), bottom = Math.min(box.bottom, innerHeight);
  if (right <= left || bottom <= top) {
    problems.push('is outside the window');
  } else {
    const hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
    if (!hit || !(e.contains(hit) || hit.contains(e))) {
      const by = hit ? hit.tagName.toLowerCase() + '.' + String(hit.className).slice(0, 30)
        : 'nothing';
      problems.push('is covered by ' + by);
    }
  }
  if (memory.seen.has(e)) problems.push('was already visited (focus went round)');
  memory.seen.add(e);
  const before = memory.previous;
  if (before && before.isConnected && before !== e
      && !(before.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING)) {
    const was = before.id || before.tagName.toLowerCase();
    problems.push('comes before the control focus was on (' + was + ')');
  }
  memory.previous = e;
  return {name, tag: e.tagName.toLowerCase(), problems};
}"""


#: A page function. Put the point Tab starts from at the start of the page: blurring a control
#: leaves it where the control was, so a view opened from another would be walked from the middle.
START_OF_PAGE = """() => {
  window.__stops = undefined;
  if (document.activeElement) document.activeElement.blur();
  document.body.tabIndex = -1;
  document.body.focus();
  document.body.removeAttribute('tabindex');
  window.scrollTo(0, 0);
}"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "keyboard", waiting=True)


@pytest.fixture(scope="module")
def landing(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The landing page, served with no project."""
    config_home: Path = tmp_path_factory.mktemp("keyboard-landing") / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(None, config_home)
    try:
        yield address
    finally:
        stop(process)


def _decide(base: str, actor: str) -> None:
    request = urllib.request.Request(
        f"{base}api/decider",
        data=json.dumps({"decider": actor}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def _new_page(browser: Any) -> Any:
    return browser.new_context(viewport={"width": 1440, "height": 900}).new_page()


def _views(browser: Any, served: str) -> list[str]:
    """Every section of every page, from the navigation."""
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    views: list[str] = page.eval_on_selector_all(
        "nav.sections button[data-view]", "nodes => nodes.map(n => n.dataset.view)"
    )
    page.context.close()
    return views


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


def _tab_through(page: Any, where: str) -> list[str]:
    """Press Tab from the start of the page, up to ``MOST_STOPS`` times or until focus leaves the
    page; the problems found at each stop, in words."""
    page.evaluate(START_OF_PAGE)
    problems: list[str] = []
    stops = 0
    for _ in range(MOST_STOPS):
        page.keyboard.press("Tab")
        stop_at = page.evaluate(WHERE_FOCUS_IS)
        if stop_at is None:
            break
        stops += 1
        problems += [
            f"{where}, stop {stops}, {stop_at['tag']} '{stop_at['name']}' {problem}"
            for problem in stop_at["problems"]
        ]
    if stops < 5:
        problems.append(f"{where}: only {stops} stops; the view is out of reach of the keyboard")
    return problems


def test_the_navigation_holds_every_section_this_test_walks(browser: Any, served: str) -> None:
    views = _views(browser, served)
    assert len(views) >= 20, f"the navigation lost sections: {views}"
    # Overlap holds the Years view (its By year mode). Stability is its own section.
    walked = {"overlap", "explorer", "map", "review", "changes", "settings", "stability"}
    assert walked <= set(views)


def test_tab_walks_every_view_in_page_order_with_a_visible_ring(browser: Any, served: str) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    problems: list[str] = []
    for view in _views(browser, served):
        _open_view(page, view)
        problems += _tab_through(page, view)
    page.context.close()
    assert problems == [], "\n".join(problems)


def test_tab_walks_the_landing_page_in_page_order_with_a_visible_ring(
    browser: Any, landing: str
) -> None:
    page = _new_page(browser)
    page.goto(landing)
    page.wait_for_selector("#ui-root", timeout=HANG_GUARD_MS)
    settled(page)
    problems = _tab_through(page, "landing")
    page.context.close()
    assert problems == [], "\n".join(problems)


def test_the_footer_links_follow_each_other_at_the_end_of_the_page(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.focus("#docs-link")
    page.keyboard.press("Tab")
    after_docs = page.evaluate("document.activeElement.id")
    page.keyboard.press("Shift+Tab")
    back = page.evaluate("document.activeElement.id")
    page.context.close()
    assert after_docs == "report-link"
    assert back == "docs-link"


# --------------------------------------------------------------------------- Escape


def _focus_is_on(page: Any) -> str:
    return str(
        page.evaluate(
            "(() => { const e = document.activeElement;"
            " return e ? e.id || e.getAttribute('aria-label') || e.textContent.trim().slice(0, 40)"
            " : ''; })()"
        )
    )


def test_escape_closes_the_search_boxs_list_of_words(browser: Any, served: str) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    box = page.locator("#search-box")
    box.focus()
    box.type("ke")
    listing = box.get_attribute("aria-controls")
    page.wait_for_selector(f"#{listing}:not([hidden]) [role=option]", timeout=15000)
    assert box.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    closed = box.get_attribute("aria-expanded") == "false"
    held = _focus_is_on(page)
    page.context.close()
    assert closed, "Escape left the list of words open"
    assert held == "search-box", "Escape took focus away from the search box"


def test_escape_closes_the_rerun_summary_and_focus_stays_on_its_button(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    badge = page.locator("#rerun-badge")
    badge.wait_for(state="visible", timeout=HANG_GUARD_MS)
    badge.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#rerun-summary:not([hidden])", timeout=15000)
    page.keyboard.press("Escape")
    hidden = page.evaluate("document.querySelector('#rerun-summary').hidden")
    expanded = badge.get_attribute("aria-expanded")
    held = _focus_is_on(page)
    page.context.close()
    assert hidden and expanded == "false", "Escape left the Re-run summary open"
    assert held == "rerun-badge"


def test_escape_stops_the_helps_pointing_and_its_tip(browser: Any, served: str) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    button = page.locator("#help-button")
    button.focus()
    page.keyboard.press("Enter")
    # The "?" opens the sheet of shortcuts, which offers the pointing.
    page.wait_for_selector("dialog.shortcuts-sheet[open]", timeout=15000)
    page.click("#shortcuts-point")
    page.wait_for_selector(".help-marker", timeout=15000)
    page.keyboard.press("Escape")
    markers = page.locator(".help-marker").count()
    held = _focus_is_on(page)
    # The tip that appears when a control takes focus.
    for _ in range(20):
        page.locator("#pages button[data-page=advanced]").focus()
        try:
            page.wait_for_selector("#help-tip:not([hidden])", timeout=1000)
            break
        except sync_api.TimeoutError:
            # The focus came before the page could show its tip: take it away and give it again.
            page.evaluate("document.activeElement.blur()")
    assert page.evaluate("!!document.querySelector('#help-tip:not([hidden])')"), "no tip appeared"
    page.keyboard.press("Escape")
    tip_open = page.evaluate("!!document.querySelector('#help-tip:not([hidden])')")
    page.context.close()
    assert markers == 0, "Escape left the help pointing at every control"
    assert held == "help-button"
    assert not tip_open, "Escape left the tip open"


def test_escape_closes_a_dialog_and_gives_focus_back_to_what_opened_it(
    browser: Any, served: str
) -> None:
    _decide(served, "a. person")
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    settled(page)
    first = page.evaluate("window.ingenceYears.load().then(m => m.themes[0].theme)")
    page.evaluate("id => window.ingenceYears.select({kind: 'theme', id})", first)
    opener = page.locator("#detail >> text=Rename…")
    opener.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("dialog[open]", timeout=15000)
    in_dialog = "document.querySelector('dialog[open]').contains(document.activeElement)"
    inside = page.evaluate(in_dialog)
    # Tab and Shift+Tab stay inside the dialog while it is open.
    strayed = []
    for key in ["Tab"] * 8 + ["Shift+Tab"] * 8:
        page.keyboard.press(key)
        if not page.evaluate(in_dialog):
            strayed.append(key)
    page.keyboard.press("Escape")
    still_open = page.locator("dialog[open]").count()
    back = page.evaluate("document.activeElement.textContent.trim()")
    page.context.close()
    assert inside, "the dialog did not take focus"
    assert strayed == [], f"focus left the open dialog on {strayed}"
    assert still_open == 0, "Escape left the dialog open"
    assert back == "Rename…", "focus did not return to the control that opened the dialog"


# O opens Stability, a section of its own, so only the Wheel opens over the rows.
@pytest.mark.parametrize(("key", "opens"), [("w", ".ybig .ywheel")])
def test_escape_returns_from_the_wheel_to_the_rows(
    browser: Any, served: str, key: str, opens: str
) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.focus(".ylist")
    page.keyboard.press(key)
    page.wait_for_selector(opens, timeout=HANG_GUARD_MS)
    page.keyboard.press("Escape")
    page.wait_for_selector(".years .yrow", timeout=15000)
    left = page.locator(opens).count()
    page.context.close()
    assert left == 0, f"Escape left {opens} open"


def test_escape_closes_the_graph_pages_box_for_choosing_a_folder(browser: Any, served: str) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#graph")
    _open_view(page, "graph")
    summary = page.locator("details[data-graph=choose] summary")
    summary.focus()
    # With nothing chosen the box is open already; with something chosen it is closed.
    if not page.evaluate("document.querySelector('details[data-graph=choose]').open"):
        page.keyboard.press("Enter")
    assert page.evaluate("document.querySelector('details[data-graph=choose]').open")
    page.locator("details[data-graph=choose] input").focus()
    page.keyboard.press("Escape")
    is_open = page.evaluate("document.querySelector('details[data-graph=choose]').open")
    back = page.evaluate("document.activeElement.tagName.toLowerCase()")
    page.context.close()
    assert not is_open, "Escape left the box open"
    assert back == "summary", "focus did not return to the line that opened the box"


def test_escape_closes_the_graph_workspaces_arrange_panel(browser: Any, served: str) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#explorer")
    _open_view(page, "explorer")
    opener = page.locator("button:has-text('Arrange')").first
    opener.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".ix-popover", timeout=15000)
    page.keyboard.press("Escape")
    left = page.locator(".ix-popover").count()
    page.context.close()
    assert left == 0, "Escape left the Arrange panel open"


PADDING = "parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0"


def test_focus_is_kept_clear_of_the_sticky_header(browser: Any, served: str) -> None:
    """A control focused from below, in a view long enough to scroll, is scrolled to just under the
    header and not behind it. The scroll padding that does it is there while Tab is down."""
    page = _new_page(browser)
    page.goto(f"{served}#years")
    _open_view(page, "rules")
    page.evaluate(START_OF_PAGE)
    page.keyboard.press("Shift+Tab")  # the last control of the page
    page.keyboard.down("Shift")
    page.keyboard.down("Tab")
    padding = page.evaluate(PADDING)
    page.keyboard.up("Tab")
    page.keyboard.up("Shift")
    header = page.evaluate("document.querySelector('body > header').getBoundingClientRect().height")
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
    covered = page.evaluate(
        "(() => { const e = document.activeElement; const r = e.getBoundingClientRect();"
        " const hit = document.elementFromPoint((r.left + r.right) / 2,"
        " Math.max((r.top + r.bottom) / 2, 0));"
        " return !(hit && (e.contains(hit) || hit.contains(e))); })()"
    )
    page.context.close()
    assert padding >= header, (
        "the page's scroll padding is shorter than its header while Tab is down"
    )
    assert not covered, "focus is behind the sticky header"


def test_the_scroll_padding_is_held_only_while_the_keyboard_moves_focus(
    browser: Any, served: str
) -> None:
    """A page-wide padding moved where the browser scrolls a control a mouse was about to click
    (a click on the section bar was blocked by the help's tip). It is released when the key comes
    up and when the pointer is pressed, and a click on a control under the header goes through."""
    page = _new_page(browser)
    page.goto(f"{served}#years")
    _open_view(page, "rules")
    assert page.evaluate(PADDING) == 0, "padding before any key"
    page.keyboard.down("Tab")
    assert page.evaluate(PADDING) > 0
    page.keyboard.up("Tab")
    assert page.evaluate(PADDING) == 0, "padding left on after the key came up"
    page.keyboard.down("Tab")
    page.mouse.down()
    held_after_pointer = page.evaluate(PADDING)
    page.mouse.up()
    page.keyboard.up("Tab")
    page.mouse.move(700, 500)
    page.locator("#pages button[data-page=advanced]").click()
    page.wait_for_function(
        "document.querySelector('nav.sections:not([hidden]) button[data-view=graph]')",
        timeout=15000,
    )
    # The section bar of the page just opened takes a click while the help's tip is up.
    page.locator("nav.sections:not([hidden]) button[data-view=graph]").click(timeout=10000)
    page.context.close()
    assert held_after_pointer == 0, "padding left on after the pointer was pressed"


def test_enter_on_a_button_in_the_years_view_does_not_also_open_the_row(
    browser: Any, served: str
) -> None:
    """The Years view's keys act on its focused row. Enter on a button is the button's own."""
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    settled(page)
    page.focus(".ylist")
    page.keyboard.press("ArrowDown")
    before = page.inner_text(".ycrumbs")
    page.locator("button.ychip:has-text('Wheel')").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".ybig .ywheel", timeout=HANG_GUARD_MS)
    after = page.inner_text(".ycrumbs")
    page.context.close()
    assert "The Wheel" in after
    assert before.strip() == "Project"
    assert "kipper" not in after and "tovald" not in after and "zandri" not in after, after
