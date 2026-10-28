"""The Wheel as a person sees it, not as the page's markup reads it.

These tests catch a Wheel drawn as a wall of printed text while the other browser tests pass. If
the page makes its SVG elements in no namespace, the browser takes them for unknown elements and
prints each arc's tooltip as running text, and a test that looks for ``.ywheel path`` in the
markup still finds every path. These tests ask the browser what it laid out and then look at the
pixels.

On the invented theme pool (``fixtures.theme_pool``), served by ``ingence see`` on a copy, with who
decides kept in a folder of the test's own. Two Wheels are read: the large one behind W, and the
small one in the project's card. For each:

1. every element drawn is in the SVG namespace, and the root is a real SVG element;
2. the SVG has a size, its arcs have a size and lie inside it, and its words have a size;
3. no tooltip (a ``<title>``) is laid out as text, anywhere in the Wheel's block;
4. a screenshot of it carries color: the arcs are drawn, not typed.

Skipped where the ``browser`` extra, a browser or the ``themes`` extra is missing, as every browser
test is.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    coloured_share,
    launch_browser,
    need,
    serve_theme_project,
    settled,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

#: The namespace every drawn element must be in. Written here, in a test, not in the page: the
#: page's own files may name no address (``test_web_vendor.py`` reads every asset for one).
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
#: A page function. For the first SVG the selector finds: what the browser made of it.
WHAT_WAS_DRAWN = """(selector) => {
  const svg = document.querySelector(selector);
  const block = svg.closest('.ywheel');
  const asked = svg.getBoundingClientRect();
  const everything = [svg, ...svg.querySelectorAll('*')];
  const within = (box) => box.left >= asked.left - 1 && box.right <= asked.right + 1
    && box.top >= asked.top - 1 && box.bottom <= asked.bottom + 1;
  const shapes = [...svg.querySelectorAll('path')];
  const words = [...svg.querySelectorAll('text')];
  const titles = [...svg.querySelectorAll('title')];
  return {
    root_is_svg: svg instanceof SVGSVGElement,
    outside_namespace: everything.filter((e) => e.namespaceURI !== '__SVG__')
      .map((e) => e.localName),
    elements: everything.length,
    size: [asked.width, asked.height],
    shapes: shapes.length,
    shapes_with_size: shapes.filter((p) => typeof p.getBBox === 'function'
      && p.getBBox().width > 0 && p.getBBox().height > 0
      && within(p.getBoundingClientRect())).length,
    words: words.length,
    words_with_size: words.filter((t) => typeof t.getBBox === 'function'
      && t.getBBox().width > 0 && within(t.getBoundingClientRect())).length,
    titles: titles.length,
    titles_laid_out: titles.filter((t) => t.getClientRects().length > 0).length,
    titles_printed: titles.map((t) => t.textContent.trim())
      .filter((said) => said && block.innerText.includes(said)),
    centre: (svg.querySelector('text.center') || {textContent: ''}).textContent,
    count_size: svg.querySelector('text.center')
      ? getComputedStyle(svg.querySelector('text.center')).fontSize : '',
    sub_size: svg.querySelector('text.center.sub')
      ? getComputedStyle(svg.querySelector('text.center.sub')).fontSize : '',
  };
}""".replace("__SVG__", SVG_NAMESPACE)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Dashboard on a copy of the invented theme project."""
    yield from serve_theme_project(tmp_path_factory, "wheel")


def _screenshot_folder(page: Any, selector: str, name: str) -> bytes:
    shot = cast("bytes", page.locator(selector).screenshot())
    folder = os.environ.get("INGENCE_BROWSER_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / f"{name}.png").write_bytes(shot)
    return shot


def _check_drawn(seen: dict[str, Any], shot: bytes, where: str) -> None:
    assert seen["root_is_svg"], f"{where}: the root is not an SVG element in the browser's eyes"
    assert seen["outside_namespace"] == [], (
        f"{where}: elements made outside the SVG namespace, which the browser prints as text: "
        f"{sorted(set(seen['outside_namespace']))}"
    )
    width, height = seen["size"]
    assert width > 100 and height > 100, f"{where}: the SVG has no size ({width} x {height})"
    assert seen["shapes"] > 60, f"{where}: an arc per experiment and per line: {seen['shapes']}"
    assert seen["shapes_with_size"] == seen["shapes"], (
        f"{where}: only {seen['shapes_with_size']} of {seen['shapes']} arcs have a size in the SVG"
    )
    assert seen["words"] >= 1 and seen["words_with_size"] == seen["words"], (
        f"{where}: the words in the Wheel are not all laid out ({seen['words_with_size']} of "
        f"{seen['words']})"
    )
    assert seen["titles"] == seen["shapes"], f"{where}: every arc carries its tooltip"
    assert seen["titles_laid_out"] == 0, f"{where}: {seen['titles_laid_out']} tooltips are laid out"
    assert seen["titles_printed"] == [], (
        f"{where}: tooltip texts are printed on the page: {seen['titles_printed'][:2]}"
    )
    share = coloured_share(shot)
    assert share > 0.15, f"{where}: only {share:.1%} of the picture is colored: arcs not drawn"


def test_the_large_wheel_is_drawn(browser: Any, served: str) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
    page.click("text=Wheel (W)")
    page.wait_for_selector(".ybig .ywheel svg path")
    seen = page.evaluate(WHAT_WAS_DRAWN, ".ybig .ywheel svg")
    shot = _screenshot_folder(page, ".ybig .ywheel svg", "release-wheel-large")
    _check_drawn(seen, shot, "the Wheel")
    assert seen["centre"].endswith("experiments"), "the count sits in the middle"
    # Six lines of work are planted: each is named beside its arcs, those near the left and right
    # edges shortened to the room the drawing leaves them, and the count makes seven words.
    assert seen["words"] == 7, f"{seen['words']} words in the Wheel; six lines and the count"
    assert seen["size"][0] >= 360, "the large Wheel is large"
    page.close()


def test_the_small_wheel_in_the_project_card_is_drawn(browser: Any, served: str) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.wait_for_selector("#detail .ywheel svg path")
    seen = page.evaluate(WHAT_WAS_DRAWN, "#detail .ywheel svg")
    shot = _screenshot_folder(page, "#detail .ywheel svg", "release-wheel-small")
    _check_drawn(seen, shot, "the project card's small Wheel")
    # The card's Wheel is 120 px. Its hole holds the count alone.
    assert seen["words"] == 1, "the small Wheel names its count, and no more"
    assert seen["count_size"] == "12px", "the count keeps its size"
    assert seen["size"][0] < 300, "the card's Wheel is small"
    page.close()


def test_no_drawing_on_any_page_is_made_outside_the_svg_namespace(
    browser: Any, served: str
) -> None:
    """Every drawing the pages make (the Graph workspace's, the legends', the Wheel's) is read
    for the same fault."""
    drawn = (
        "() => { const out = {n: 0, bad: []};"
        " for (const svg of document.querySelectorAll('svg')) { out.n += 1;"
        " for (const e of [svg, ...svg.querySelectorAll('*')])"
        " if (e.namespaceURI !== '__SVG__') out.bad.push(e.localName); } return out; }"
    ).replace("__SVG__", SVG_NAMESPACE)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    views = page.eval_on_selector_all(
        "nav.sections button[data-view]", "nodes => nodes.map(n => n.dataset.view)"
    )
    assert len(views) >= 20
    stray: list[str] = []
    drawings = 0
    # Last, Overlap in its Connections mode (its legend's line samples are drawings).
    for view in [*views, "connections"]:
        if view == "connections":
            page.evaluate("window.ingenceOverlap.setMode('connections')")
            view = "overlap"
        page.evaluate("view => { location.hash = '#' + view; }", view)
        page.wait_for_function(
            "view => { const on = document.querySelector('nav.sections button.active');"
            " return on && on.dataset.view === view; }",
            arg=view,
            timeout=HANG_GUARD_MS,
        )
        settled(page)
        seen = page.evaluate(drawn)
        drawings += seen["n"]
        stray += [f"{view}: {name}" for name in seen["bad"]]
    page.close()
    assert drawings > 5, "the pages' drawings were found"
    assert stray == [], stray
