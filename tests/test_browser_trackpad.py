"""A trackpad scrolls the page, and only a pinch zooms a drawing.

A two-finger scroll on a trackpad must not zoom the timelines, so that a person can scroll past
them. This holds on the Years rows (the axis and each row's time cell), on the Explorer's
Timeline and Lineage, and on the Graph page's drawing:

1. A plain upright wheel is left to the page: the event is not default-prevented and the
   drawing's zoom and pan do not move.
2. Ctrl + wheel zooms: a trackpad pinch arrives as a wheel event with Ctrl held, and a mouse
   user holds Ctrl. The page is held still.
3. A mostly sideways wheel pans, and keeps the zoom.

Each wheel is a ``WheelEvent`` dispatched on the drawing from the page, so the result does not
depend on where a real pointer lands. One test also sends real input (Playwright's wheel, with
Control held, then without) and sees the axis zoom and the page scroll. What each drawing shows is
read from what it says of itself: the Years axis and the Timeline say their span in
``data-start``/``data-end``, the Lineage its transform, the Graph ``data-zoom``/``data-pan``.

On a copy of the Graph workspace's invented fixture (``fixtures/explorer_project.py``), served by
``ingence see`` with who decides kept in a folder of the test's own. Skipped where the ``browser``
extra or a browser is missing, as every browser test is (a failure where
``INGENCE_BROWSER_REQUIRED=1``).
"""

from __future__ import annotations

import re
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    launch_browser,
    need,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: Dispatch one wheel event at the middle of the first element the selector finds. Returns true
#: where no listener held the page still (``dispatchEvent`` is false once one called
#: ``preventDefault``), or "missing" where nothing matched.
WHEEL = """([selector, init]) => {
  const target = document.querySelector(selector);
  if (!target) return 'missing';
  const box = target.getBoundingClientRect();
  return target.dispatchEvent(new WheelEvent('wheel', {bubbles: true, cancelable: true,
    composed: true, clientX: box.left + box.width / 2, clientY: box.top + box.height / 2,
    deltaMode: 0, ...init}));
}"""
#: Two painted frames and a task after them: any update a wheel scheduled has been drawn.
FRAMES = (
    "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() =>"
    " setTimeout(done, 0))))"
)

#: An upright scroll; Ctrl + wheel up, which zooms in (fingers spread on a trackpad), and down,
#: which zooms out; and a mostly sideways swipe.
UPRIGHT = {"deltaX": 0, "deltaY": 120}
ZOOM_IN = {"deltaX": 0, "deltaY": -120, "ctrlKey": True}
ZOOM_OUT = {"deltaX": 0, "deltaY": 120, "ctrlKey": True}
SIDEWAYS = {"deltaX": 120, "deltaY": 6}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(
    explorer_project: Any, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, Any]]:
    """A Dashboard on a copy of the explorer fixture; yields its address and the fixture."""
    base = tmp_path_factory.mktemp("trackpad")
    copy = base / "project"
    shutil.copytree(explorer_project.project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield address, explorer_project
    finally:
        stop(process)


def _page(browser: Any, *, height: int = 900) -> Any:
    context = browser.new_context(
        viewport={"width": 1400, "height": height}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    return page


def _wheel(page: Any, selector: str, init: dict[str, Any]) -> bool:
    """Send one wheel event; true where the page was left to scroll. Waits until any update the
    wheel scheduled is drawn."""
    left_to_page = page.evaluate(WHEEL, [selector, init])
    assert left_to_page != "missing", f"nothing on the page matches {selector}"
    page.evaluate(FRAMES)
    return bool(left_to_page)


def _check_three_wheels(
    page: Any,
    selector: str,
    read: Callable[[], tuple[float, float, float]],
    *,
    zoom: dict[str, Any],
) -> None:
    """The three wheels on one drawing, whose state ``read`` gives as ``(zoom, pan, other)``: a
    magnification (or a span), a sideways position, and what a sideways wheel must not move."""
    before = read()
    assert _wheel(page, selector, UPRIGHT), f"{selector}: an upright scroll is left to the page"
    assert read() == before, f"{selector}: an upright scroll moved nothing"
    assert not _wheel(page, selector, zoom), f"{selector}: Ctrl + wheel holds the page still"
    zoomed = read()
    assert zoomed[0] != pytest.approx(before[0]), f"{selector}: Ctrl + wheel zooms"
    assert not _wheel(page, selector, SIDEWAYS), f"{selector}: a sideways swipe holds the page"
    panned = read()
    # The spans are said in whole milliseconds, so a pan may round them by one.
    same = {"rel": 1e-6, "abs": 1e-6}
    assert panned[0] == pytest.approx(zoomed[0], **same), f"{selector}: a pan keeps the zoom"
    assert panned[2] == pytest.approx(zoomed[2], **same), f"{selector}: a pan is sideways only"
    assert panned[1] != pytest.approx(zoomed[1]), f"{selector}: a sideways swipe pans"


def _years_span(page: Any) -> tuple[float, float, float]:
    """The Years axis: (its span, its start, 0), from what the axis says of itself."""
    start, end = page.eval_on_selector(".yticks", "e => [e.dataset.start, e.dataset.end]")
    assert start is not None and end is not None, "the axis says the span it shows"
    return float(end) - float(start), float(start), 0.0


@pytest.mark.parametrize("selector", [".yticks", ".yrow .ytime"], ids=["axis", "time-cell"])
def test_years_rows_scroll_the_page_and_zoom_only_with_ctrl(
    browser: Any, served: tuple[str, Any], selector: str
) -> None:
    base, _ = served
    page = _page(browser)
    try:
        page.goto(f"{base}#years")
        page.wait_for_selector(".years .yrow .ytime", timeout=HANG_GUARD_MS)
        settled(page)
        before = _years_span(page)
        _check_three_wheels(page, selector, lambda: _years_span(page), zoom=ZOOM_IN)
        assert _years_span(page)[0] < before[0], "Ctrl + wheel up narrows the span"
        assert page.locator(".yreset").count() == 1, "zoomed in, Reset zoom is offered"
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_real_input_on_a_years_row_zooms_with_control_and_scrolls_the_page_without(
    browser: Any, served: tuple[str, Any]
) -> None:
    """Playwright's own wheel, as a person's hand sends it: Control held zooms the axis, and the
    page stays where it was; then a plain scroll over the same row scrolls the page."""
    base, _ = served
    page = _page(browser, height=600)
    try:
        page.goto(f"{base}#years")
        page.wait_for_selector(".years .yrow .ytime", timeout=HANG_GUARD_MS)
        settled(page)
        assert page.evaluate("() => document.scrollingElement.scrollHeight > innerHeight"), (
            "the page is taller than the window, so it can scroll"
        )
        box = page.locator(".yrow .ytime").first.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        span, start, _ = _years_span(page)
        page.keyboard.down("Control")
        try:
            page.mouse.wheel(0, -120)
        finally:
            page.keyboard.up("Control")
        page.wait_for_function(
            "start => document.querySelector('.yticks').dataset.start !== start",
            arg=str(round(start)),
            timeout=HANG_GUARD_MS,
        )
        assert _years_span(page)[0] < span, "Control and the wheel zoomed in"
        assert page.evaluate("() => document.scrollingElement.scrollTop") == 0
        zoomed = _years_span(page)
        page.mouse.wheel(0, 200)
        page.wait_for_function(
            "() => document.scrollingElement.scrollTop > 0", timeout=HANG_GUARD_MS
        )
        page.evaluate(FRAMES)
        assert _years_span(page) == zoomed, "the plain scroll moved the page, not the axis"
        assert not page.errors, page.errors
    finally:
        page.context.close()


def _timeline_span(page: Any) -> tuple[float, float, float]:
    """The Explorer's Timeline: (its span, its start, 0)."""
    start, end = page.eval_on_selector(".ix-timeline", "e => [e.dataset.start, e.dataset.end]")
    assert start is not None and end is not None, "the Timeline says the span it shows"
    return float(end) - float(start), float(start), 0.0


def test_explorer_timeline_scrolls_the_page_and_zooms_only_with_ctrl(
    browser: Any, served: tuple[str, Any]
) -> None:
    base, _ = served
    page = _page(browser)
    try:
        page.goto(f"{base}?mode=timeline#explorer")
        page.wait_for_selector(
            ".ix-root:not([data-loading]) .ix-timeline svg", timeout=HANG_GUARD_MS
        )
        settled(page)
        before = _timeline_span(page)
        _check_three_wheels(page, ".ix-timeline", lambda: _timeline_span(page), zoom=ZOOM_IN)
        assert _timeline_span(page)[0] < before[0], "Ctrl + wheel up narrows the span"
        assert not page.errors, page.errors
    finally:
        page.context.close()


_TRANSFORM = re.compile(r"translate\(([-\d.e]+)[ ,]+([-\d.e]+)\)\s*scale\(([-\d.e]+)\)")


def _lineage_view(page: Any) -> tuple[float, float, float]:
    """The Explorer's Lineage: (its scale, its sideways shift, its upright shift)."""
    transform = page.eval_on_selector(
        ".ix-lineage > svg > g[transform]", "g => g.getAttribute('transform')"
    )
    found = _TRANSFORM.search(transform or "")
    assert found, transform
    x, y, k = (float(part) for part in found.groups())
    return k, x, y


def test_explorer_lineage_scrolls_the_page_and_zooms_only_with_ctrl(
    browser: Any, served: tuple[str, Any]
) -> None:
    base, explorer = served
    summary = next(target for _, target in explorer.derivations if target.endswith("_summary.csv"))
    page = _page(browser)
    try:
        page.goto(f"{base}?mode=lineage&focus={explorer.ids[summary]}#explorer")
        page.wait_for_selector(
            ".ix-root:not([data-loading]) .ix-lineage svg g rect", timeout=HANG_GUARD_MS
        )
        settled(page)
        before = _lineage_view(page)
        _check_three_wheels(page, ".ix-lineage", lambda: _lineage_view(page), zoom=ZOOM_IN)
        assert _lineage_view(page)[0] > before[0], "Ctrl + wheel up magnifies"
        assert not page.errors, page.errors
    finally:
        page.context.close()


def _graph_view(page: Any) -> tuple[float, float, float]:
    """The Graph page's drawing: (its zoom, its sideways pan, its upright pan)."""
    zoom, pan = page.eval_on_selector(
        '[data-graph="canvas"]', "e => [e.dataset.zoom, e.dataset.pan]"
    )
    assert zoom is not None and pan is not None, "the drawing says its zoom and pan"
    x, y = (float(part) for part in pan.split())
    return float(zoom), x, y


def test_the_graph_scrolls_the_page_and_zooms_only_with_ctrl(
    browser: Any, served: tuple[str, Any]
) -> None:
    base, explorer = served
    from fixtures.laboratory_pool import STEMS

    raw = f"{explorer.experiments[0]}/raw/{STEMS[0]}_01.nd2"
    page = _page(browser)
    try:
        page.goto(f"{base}?center={explorer.ids[raw]}#graph")
        page.wait_for_selector("table.graph-table", timeout=HANG_GUARD_MS)
        page.wait_for_function(
            "() => document.querySelector('[data-graph=\"canvas\"]').dataset.pan !== undefined",
            timeout=HANG_GUARD_MS,
        )
        settled(page)
        before = _graph_view(page)
        # A small neighborhood is drawn at the largest zoom the page allows, so this zooms out.
        _check_three_wheels(page, '[data-graph="canvas"]', lambda: _graph_view(page), zoom=ZOOM_OUT)
        assert _graph_view(page)[0] < before[0], "Ctrl + wheel down zooms out"
        assert not page.errors, page.errors
    finally:
        page.context.close()
