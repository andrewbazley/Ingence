"""A narrow window keeps everything behind a chip, or says what still works.

At a phone's width (390 pixels) the Years table's year header and right-hand columns would be
clipped at the edge of the window. The Dashboard declares the window it is made for, 1024 pixels,
and below it nothing is hidden:

- 600 to 1023 pixels: the inspector is a drawer opened by a "Details" chip at the row bar's right
  (and by Enter on a row). The Wheel (W) stays as a chip that opens a full-width sheet over the
  rows, closed by Escape or Close. (Overlap's stability pictures are in Advanced's Stability
  section, which O opens.)
- Below 600 pixels: the page is the title, one sentence on what was found, and a box that says
  what still works, with two links.

At 1024 pixels and wider, on the invented theme pool (as ``test_browser_years.py``), what is
drawn must be readable, which these tests read in the browser and not in the markup:

1. No year label of the Years timeline is cut off.
2. Overlap's column of line names has a width and each name stands on one line, clear of its bar.
3. The Wheel is drawn at a size a person can read.
4. The table of words two lines share (in the Stability section) stands under the lines, inside
   the list, never past its edge.
5. The card of a row just clicked is in view, which it is not where the Years block keeps a
   minimum height of nearly the window's while the card stands under it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    launch_browser,
    need,
    serve_theme_project,
    settled,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

#: The narrowest window the Dashboard draws the rows beside the inspector in.
MINIMUM = 1024
PHONE = 600

#: A page function: whether the rows are drawn and their labels fit, and whether the page scrolls
#: sideways.
STATE = """() => {
  const shown = (el) => !!el && getComputedStyle(el).display !== 'none'
    && el.getBoundingClientRect().height > 0;
  const root = document.documentElement;
  return {
    note_gone: document.querySelector('#narrow-note') === null,
    rows_shown: shown(document.querySelector('.years .ylistbox')),
    row_count: document.querySelectorAll('.years .yrow').length,
    sideways: root.scrollWidth - root.clientWidth,
    // A year label is cut off where the text is wider than the box that holds it.
    labels: [...document.querySelectorAll('.years .ytick')]
      .filter((t) => t.textContent.trim())
      .map((t) => [t.textContent.trim(), t.clientWidth, t.scrollWidth]),
  };
}"""
#: The chips at the row bar: the Wheel's and Overlap's, and Details; are they there, and shown?
CHIPS = """() => {
  const shown = (el) => getComputedStyle(el).visibility !== 'hidden'
    && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
  const named = (pattern) => [...document.querySelectorAll('.years .ychip')]
    .filter((c) => pattern.test(c.textContent));
  const keys = document.querySelector('.years .ykeys');
  return {wheel: named(/Wheel [(]W[)]/).filter(shown).length,
    overlap: named(/Overlap [(]O[)]/).filter(shown).length,
    details: named(/Details/).filter(shown).length,
    keys_shown: !!keys && shown(keys)};
}"""
#: The card of the row just clicked: its kicker's place in the window.
CARD = """() => {
  const kicker = document.querySelector('#detail .ykicker');
  const box = kicker.getBoundingClientRect();
  return {text: kicker.textContent.trim(), top: box.top, bottom: box.bottom, window: innerHeight};
}"""
#: Overlap: the first line's name beside its bar, and the table of shared words under the lines.
OVERLAP = """() => {
  const box = document.querySelector('.yovlbox');
  const row = document.querySelectorAll('.ystab')[1];
  const shown = !!box && getComputedStyle(box).display !== 'none' && !!row
    && row.getBoundingClientRect().height > 0;
  if (!shown) return {shown: false, crumbs: document.querySelector('.ycrumbs').textContent};
  const name = row.children[0].getBoundingClientRect();
  const bar = row.children[1].getBoundingClientRect();
  const rows = [...document.querySelectorAll('.ystab')];
  const last = rows[rows.length - 1].getBoundingClientRect();
  const table = document.querySelector('.yovl table').getBoundingClientRect();
  const list = document.querySelector('#list').getBoundingClientRect();
  return {shown: true, name_width: name.width, name_height: name.height,
    clear_of_bar: name.right <= bar.left + 1,
    table_under_lines: table.top >= last.bottom - 1, table_right: table.right,
    list_right: list.right, table_width: table.width};
}"""
#: The Wheel: the large one behind W.
WHEEL = """() => {
  const svg = document.querySelector('.ybig .ywheel svg');
  const box = document.querySelector('.ybig');
  const shown = !!svg && !!box && getComputedStyle(box).display !== 'none'
    && svg.getBoundingClientRect().width > 0;
  return {shown, width: shown ? svg.getBoundingClientRect().width : 0};
}"""
#: Where the inspector stands: shown in the window, or kept out of it as a closed drawer.
DETAIL = """() => {
  const detail = document.querySelector('#detail');
  const style = getComputedStyle(detail);
  const box = detail.getBoundingClientRect();
  return {visible: style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0
    && box.right > 0 && box.left < innerWidth, position: style.position, width: box.width};
}"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "narrow")


def _open(browser: Any, base: str, width: int) -> Any:
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .ycrumbs, .years.phone", timeout=HANG_GUARD_MS)
    page.wait_for_selector(
        ".years .ylistbox, .years.phone", state="attached", timeout=HANG_GUARD_MS
    )
    return page


def _read(browser: Any, base: str, width: int) -> dict[str, Any]:
    """The rows' state, then Overlap's, then the Wheel's, in a window of the given width."""
    page = _open(browser, base, width)
    try:
        page.wait_for_selector(".yrow .ylabel", timeout=HANG_GUARD_MS)
        settled(page)
        seen: dict[str, Any] = page.evaluate(STATE)
        # A row clicked: its card is in view, not pushed below the fold.
        page.click(".yrow")
        page.wait_for_function(
            "document.querySelector('#detail .ykicker').textContent.trim() !== 'The project'"
        )
        seen["card"] = page.evaluate(CARD)
        page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
        page.wait_for_selector("#detail .ykicker", state="attached")
        seen["buttons"] = page.evaluate(CHIPS)
        # Overlap's stability rows and shared words are the Stability section's.
        page.keyboard.press("o")
        page.wait_for_selector(".yovl table", state="attached", timeout=HANG_GUARD_MS)
        settled(page)
        seen["overlap"] = page.evaluate(OVERLAP)
        page.go_back()
        page.wait_for_selector(".yrow .ylabel", timeout=HANG_GUARD_MS)
        page.click("text=Wheel (W)")
        page.wait_for_selector(".ybig .ywheel svg", state="attached", timeout=HANG_GUARD_MS)
        settled(page)
        seen["wheel"] = page.evaluate(WHEEL)
        return seen
    finally:
        page.close()


def test_at_a_phones_width_the_page_is_the_title_the_sentence_and_the_box_with_two_links(
    browser: Any, served: str
) -> None:
    """Below 600 pixels: the title, the completeness sentence and a box naming what still works
    with the links to it."""
    page = _open(browser, served, 390)
    try:
        page.wait_for_selector(".years.phone .ynarrow", timeout=HANG_GUARD_MS)
        seen = page.evaluate(STATE)
        assert seen["note_gone"], "the old note at the top is gone"
        assert seen["row_count"] == 0 and not seen["rows_shown"], "the rows are not drawn"
        assert seen["sideways"] <= 0, "nothing makes the page scroll sideways"
        parts = page.eval_on_selector_all(
            ".years.phone > *", "nodes => nodes.map(n => [n.className, n.textContent.trim()])"
        )
        assert [kind.split()[0] for kind, _ in parts] == ["yhead", "yhint", "ynarrow"], parts
        title, sentence, box = (text for _, text in parts)
        assert title.startswith("The project, ")
        assert "experiments in 3 areas" in sentence
        assert "raw acquisitions found are followed by an analysis file found" in sentence
        assert box.startswith(
            "The rows need a window at least 1024 px wide. On this screen you can search, read "
            "any card and answer questions."
        )
        links = page.eval_on_selector_all(
            ".years.phone .ynarrow button.link", "nodes => nodes.map(n => n.textContent.trim())"
        )
        assert len(links) == 2 and links[0] == "Search experiments", links
        assert links[1].startswith("Questions")
        assert not page.evaluate(DETAIL)["visible"], "no inspector below 600 pixels"
        # The links work: the first takes the search box, the second the Questions page.
        page.click(".ynarrow button.link >> nth=0")
        assert page.evaluate("document.activeElement.id") == "search-box"
        page.click(".ynarrow button.link >> nth=1")
        page.wait_for_function("location.hash === '#review'")  # #sheet is its older address
    finally:
        page.close()


@pytest.mark.parametrize("width", [PHONE, 768, 1000, MINIMUM - 1])
def test_between_600_and_1023_the_three_chips_show_and_nothing_is_cut_off(
    browser: Any, served: str, width: int
) -> None:
    """At 1000 pixels the three chips are visible."""
    page = _open(browser, served, width)
    try:
        page.wait_for_selector(".yrow .ylabel", timeout=HANG_GUARD_MS)
        seen = page.evaluate(STATE)
        assert seen["note_gone"] and seen["rows_shown"] and seen["row_count"] == 3
        assert seen["sideways"] <= 0, "nothing makes the page scroll sideways"
        # No Overlap chip on the rows. O opens the Stability section.
        assert page.evaluate(CHIPS) == {
            "wheel": 1,
            "overlap": 0,
            "details": 1,
            "keys_shown": True,
        }
        assert not page.evaluate(DETAIL)["visible"], "the inspector waits in a closed drawer"
        cut = [label for label, held, needed in seen["labels"] if needed > held + 1]
        assert cut == [], f"year labels cut off at {width} px: {cut}"
    finally:
        page.close()


def test_the_details_chip_and_enter_open_the_drawer_and_escape_or_close_shut_it(
    browser: Any, served: str
) -> None:
    page = _open(browser, served, 1000)
    try:
        page.wait_for_selector(".yrow .ylabel", timeout=HANG_GUARD_MS)
        page.click(".ychip.details")
        page.wait_for_function("document.querySelector('main.drawer-open') !== null")
        assert page.evaluate(DETAIL)["visible"], "the drawer is open"
        assert page.evaluate(DETAIL)["width"] <= 450
        assert page.evaluate("document.activeElement.className") == "ydrawer-close"
        page.keyboard.press("Escape")
        page.wait_for_function("document.querySelector('main.drawer-open') === null")
        assert not page.evaluate(DETAIL)["visible"]
        # Enter on a row: the row is chosen and its card is in the drawer.
        page.focus('.yrow[tabindex="0"]')
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('main.drawer-open') !== null")
        page.wait_for_function(
            "(document.querySelector('#detail .ykicker') || {textContent: ''})"
            ".textContent.trim().startsWith('An area')"
        )
        page.click(".ydrawer-close")
        page.wait_for_function("document.querySelector('main.drawer-open') === null")
        # Resized to a wide window the inspector stands beside the rows, with no drawer.
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_function("document.querySelectorAll('.ychip.details').length === 0")
        page.wait_for_function(
            "() => { const d = document.querySelector('#detail');"
            " return getComputedStyle(d).visibility !== 'hidden'"
            " && d.getBoundingClientRect().width > 0; }"
        )
        assert page.evaluate(DETAIL)["visible"]
    finally:
        page.close()


# O opens the Stability section, so the Wheel is the one sheet over the rows.
@pytest.mark.parametrize("key,sheet", [("w", "The Wheel")])
def test_the_wheel_opens_as_a_full_width_sheet_closed_by_escape_or_close(
    browser: Any, served: str, key: str, sheet: str
) -> None:
    page = _open(browser, served, 1000)
    try:
        page.wait_for_selector(".yrow .ylabel", timeout=HANG_GUARD_MS)
        page.keyboard.press(key)
        page.wait_for_selector(".ysheet", timeout=HANG_GUARD_MS)
        if key == "w":
            page.wait_for_selector(".ysheet .ywheel svg", timeout=HANG_GUARD_MS)
        else:
            page.wait_for_selector(".ysheet .ystab", timeout=HANG_GUARD_MS)
        seen = page.evaluate(
            """() => { const s = document.querySelector('.ysheet').getBoundingClientRect();
              return {left: s.left, right: s.right, width: innerWidth, title:
              document.querySelector('.ysheet h2').textContent,
              rows_under: document.querySelectorAll('.yrow').length}; }"""
        )
        assert seen["left"] == 0 and seen["right"] == seen["width"], "a sheet as wide as the window"
        assert seen["title"].startswith(sheet)
        assert seen["rows_under"] == 3, "the rows are under the sheet"
        page.keyboard.press("Escape")
        page.wait_for_function("document.querySelector('.ysheet') === null")
        # The chip opens it too, and its Close button shuts it.
        page.click(f"text={'Wheel (W)' if key == 'w' else 'Overlap (O)'}")
        page.wait_for_selector(".ysheet")
        page.click(".ysheet-close")
        page.wait_for_function("document.querySelector('.ysheet') === null")
    finally:
        page.close()


@pytest.mark.parametrize("width", [MINIMUM, 1101, 1200, 1240, 1241, 1441, 1700, 1920])
def test_at_the_declared_width_and_wider_what_is_drawn_is_readable(
    browser: Any, served: str, width: int
) -> None:
    seen = _read(browser, served, width)
    assert seen["note_gone"]
    assert seen["rows_shown"] and seen["row_count"] == 3, "a row per invented person"
    assert seen["sideways"] <= 0, "nothing makes the page scroll sideways"
    # 1. Every year label of the timeline has the room its text needs.
    assert len(seen["labels"]) >= 3, "the timeline's year labels were found"
    cut = [label for label, held, needed in seen["labels"] if needed > held + 1]
    assert cut == [], f"year labels cut off at {width} px: {cut}"
    # 2. Overlap: the names have a column, each on one line, clear of its bar.
    overlap = seen["overlap"]
    assert overlap["shown"], "Overlap is drawn"
    assert overlap["name_width"] >= 120, f"the names' column is {overlap['name_width']} px wide"
    assert overlap["name_height"] <= 30, "a name stands on one line, not a word to a line"
    assert overlap["clear_of_bar"], "a name does not run under its bar"
    # 3. The Wheel is as large as the page makes it, not a few pixels.
    assert seen["wheel"]["shown"] and seen["wheel"]["width"] >= 300, seen["wheel"]
    # 4. The table of shared words stands under the lines and ends inside the list.
    assert overlap["table_under_lines"], "the table of shared words is under the lines"
    assert overlap["table_right"] <= overlap["list_right"] + 1, (
        f"the table ends at {overlap['table_right']} px, past the list's edge at "
        f"{overlap['list_right']} px"
    )
    # 5. The card of a clicked row is in view.
    card = seen["card"]
    assert card["text"] != "The project", "a row was clicked and its card is shown"
    assert card["top"] >= 0 and card["bottom"] <= card["window"], (
        f"the card's kicker is at {card['top']:.0f}-{card['bottom']:.0f} px in a window "
        f"{card['window']} px tall"
    )
    # The chip that opens the Wheel and the line of keys are shown (there is no Overlap chip),
    # and there is no Details chip: the inspector stands beside or under the rows.
    assert seen["buttons"] == {"wheel": 1, "overlap": 0, "details": 0, "keys_shown": True}, seen[
        "buttons"
    ]
