"""A visual lane: every Dashboard view, in light and in dark, on the sample collection's tier M.

The lane is skipped unless asked for, so the ordinary suite neither needs the project nor fails on
a font or a pixel. Ask with ``INGENCE_BROWSER_VISUAL=1`` and name a project made from tier M (the
commands that make one are in ``test_browser_budgets.py``) in ``INGENCE_VISUAL_PROJECT``.

The lane lists every view by its address (``SHOTS``): the six sections, every section of every
page, the landing page, and the variants a person reaches by acting (a card at each scale, the
Questions page with its bulk bar open, Ask's results, the quick checks). The Dashboard's own
navigation is read as well, and a section or page that is in the navigation and not in the list
fails the lane, as does a listed view that does not draw: a view cannot be added or lost without
the lane saying so.

Nothing is approved here. The lane takes a screenshot of every view in each scheme, at 1440 by
900 pixels, and writes it as a candidate to the folder ``INGENCE_VISUAL_OUT`` names (default:
``tests/visual/candidates/``, which git ignores), in a folder named for the browser
(``chromium/M-<light or dark>-<view>.png``; ``INGENCE_BROWSER`` picks the browser). A candidate
becomes a baseline, and from then on the lane compares the view with it, only when a person
copies it into the folder ``INGENCE_VISUAL_BASELINES`` names (default ``tests/visual/baselines/``)
under the same browser folder and name. A view with no baseline is not a failure: the lane reports
how many views it has no baseline for. A view with a baseline fails when more than
``MAX_DIFFERENT_SHARE`` of its pixels differ, and the lane then also writes the new picture as a
candidate, so the change can be looked at and approved or fixed.

What is held still: motion is switched off (reduced motion, no transitions or animations), the
fonts have loaded, the viewport and the color scheme are fixed, and the pages are read from a copy
of the project. What cannot be held still, such as a clock time a page prints, is why a few
differing pixels are allowed.

How to approve. Open the candidates and look at each: that it shows the view its file name
names, that nothing is cut off, and that the text reads in that scheme. Copy the ones approved into
the baselines folder under the same browser folder and name, and commit them if that folder is in
the repository; a changed view is approved the same way, its new candidate copied over the old
baseline. The baselines depend on the fonts of the machine that took them, so take and approve them
on the machine that runs the lane.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

try:
    import fcntl

    HAS_LOCK = True
except ImportError:  # Windows: no lock; two runs of the lane there must not overlap
    HAS_LOCK = False

from fixtures.dashboard_browser import (
    REPOSITORY,
    browser_name,
    launch_browser,
    need,
    png_rows,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

ASKED = os.environ.get("INGENCE_BROWSER_VISUAL") == "1"

pytestmark = pytest.mark.skipif(
    not ASKED,
    reason="the visual lane runs only when asked: set INGENCE_BROWSER_VISUAL=1 and "
    "INGENCE_VISUAL_PROJECT",
)

VISUAL = REPOSITORY / "tests" / "visual"
BASELINES = Path(os.environ.get("INGENCE_VISUAL_BASELINES") or VISUAL / "baselines")
CANDIDATES = Path(os.environ.get("INGENCE_VISUAL_OUT") or VISUAL / "candidates")

#: The copy of the project the lane serves, in a folder of a fixed name (removed before and after).
_TEMP = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
WORK = _TEMP / "ingence-visual-lane"
#: Held for the length of a run: the work folder has a fixed name, so two runs at once (two
#: worktrees, or workers splitting this file) would delete each other's project. A second run
#: waits, up to ``LOCK_WAIT_SECONDS``, for the first to end.
LOCK = _TEMP / "ingence-visual-lane.lock"
LOCK_WAIT_SECONDS = 1800

SCHEMES = ("light", "dark")
VIEWPORT = {"width": 1440, "height": 900}

#: The share of pixels that may differ from a baseline before a view fails.
MAX_DIFFERENT_SHARE = 0.005

#: Only bounds a page that never draws; every wait returns the moment its state is reached.
HANG_GUARD_MS = 600_000

#: No motion at all, so a picture does not depend on when it was taken. Added to every document
#: the page opens, including the ones a path opens.
_STILL = """
document.addEventListener('DOMContentLoaded', () => {
  const still = document.createElement('style');
  still.textContent = '*, *::before, *::after { animation: none !important;'
    + ' transition: none !important; }';
  document.head.append(still);
});
(() => {
  // The quick checks show once per project, so whichever view comes first would show
  // them. They read as seen, except for the one variant that asks for them (sessionStorage).
  const read = Storage.prototype.getItem;
  Storage.prototype.getItem = function (key) {
    const asked = read.call(window.sessionStorage, 'visual.quickchecks') === 'show';
    const quick = String(key).startsWith('ingence.quickchecks.v1.');
    return !asked && quick ? 'seen' : read.call(this, key);
  };
})();
"""


@dataclass(frozen=True)
class Shot:
    """One picture: how to reach the view, and the state that says it has drawn.

    ``kind`` is ``section`` (the address ``#<address>`` opens it), ``page`` (the page's tab is
    clicked from another page's section) or ``path`` (a page of its own beside the Dashboard, such
    as ``/landing``). ``act`` is run on a drawn section to reach a variant of it (a click, a key);
    it waits for the state the variant is in before it returns.
    """

    name: str
    kind: str
    address: str
    act: Callable[[Any], None] | None = None


#: The six sections, in the navigation's order (``nav#pages``).
PAGE_NAMES = ("overview", "overlap", "experiments", "checks", "history", "advanced")

#: Every section of every page (``nav.sections``), by its address.
SECTION_NAMES = (
    "overview",
    # Overlap (Connections and By year)
    "overlap",
    # Experiments
    "experiments",
    "topics",
    "checks",
    # History
    "changes",
    "manifest",
    # Advanced: Engine
    "rules",
    "dictionary",
    "settings",
    "ladders",
    # Advanced: Lists
    "inventory",
    "coverage",
    "classes",
    "candidates",
    "extensions",
    "programs",
    "claims",
    "review",
    "words",
    "found",
    # Advanced: Views
    "explorer",
    "graph",
    "map",
    "stability",
)


def _years_from_the_project(page: Any) -> None:
    page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    settled(page)


def _kicker(page: Any) -> str:
    return str(
        page.evaluate("(document.querySelector('#detail .ykicker') || {}).textContent || ''")
    )


def _card_of(page: Any, depth: int, kicker: str) -> None:
    """The Years view's card at one scale: go ``depth`` scales down the first row each time, then
    choose rows until the card's eyebrow begins with ``kicker``. Fails (and the view is listed as
    not drawn) where no row leads to such a card."""
    _years_from_the_project(page)
    for _ in range(depth):
        # A double click opens a row (Enter moves to its card).
        page.locator(".years .yrow").first.dblclick()
        settled(page)
    if kicker == "The project":
        # The project card is what the view opens on, with nothing chosen.
        page.wait_for_function(
            "(want) => (document.querySelector('#detail .ykicker') || {}).textContent"
            " && document.querySelector('#detail .ykicker').textContent.trim().startsWith(want)",
            arg=kicker,
            timeout=HANG_GUARD_MS,
        )
        return
    if kicker == "A theme":
        # An area's rows are its experiments under their lines. A heading opens one.
        page.locator(".years .yhead-row").first.click()
        settled(page)
        return
    if kicker == "A file":
        # An experiment opens on its story; one by one its rows are files.
        page.get_by_role("button", name="files, one by one").click()
        settled(page)
    rows = page.locator(".years .yrow")
    for index in range(min(rows.count(), 40)):
        rows.nth(index).click()
        settled(page)
        if _kicker(page).strip().startswith(kicker):
            return
    message = f"no row at depth {depth} opens a card beginning {kicker!r}"
    raise sync_api.Error(message)


def _years_card(depth: int, kicker: str) -> Callable[[Any], None]:
    def act(page: Any) -> None:
        _card_of(page, depth, kicker)

    return act


def _years_key(key: str, drawn: str) -> Callable[[Any], None]:
    """The Years view behind a key (W the Wheel, O Stability), once ``drawn`` is in the page."""

    def act(page: Any) -> None:
        _years_from_the_project(page)
        page.keyboard.press(key)
        page.wait_for_selector(drawn, timeout=HANG_GUARD_MS)

    return act


def _connections_line(page: Any) -> None:
    """Overlap's Connections, with the first line of its list opened."""
    page.evaluate("window.ingenceOverlap.setMode('connections'); window.ingenceOverlap.show()")
    page.wait_for_selector(".ov-canvas[data-nodes]", timeout=HANG_GUARD_MS)
    page.click(".ov-list summary")
    page.click(".ov-list tbody tr:first-child button")
    page.wait_for_selector("#detail .ov-evidence", timeout=HANG_GUARD_MS)
    settled(page)


def _bulk_bar_open(page: Any) -> None:
    """The Questions page with rows chosen: the bulk bar offers the answers together."""
    boxes = page.locator('[data-section="leads"] tbody input[type=checkbox]')
    boxes.first.wait_for(timeout=HANG_GUARD_MS)
    for index in range(min(boxes.count(), 3)):
        boxes.nth(index).check()
    page.wait_for_selector('[data-section="leads"] .bulk-bar:not([hidden])', timeout=HANG_GUARD_MS)


def _search_for_a_word(page: Any) -> None:
    """Ask's results, with a word the sample collection's names hold."""
    box = page.locator("#search-box")
    box.fill("confocal")
    box.press("Enter")
    page.wait_for_selector("[data-ask-view]", timeout=HANG_GUARD_MS)


def _quick_checks(page: Any) -> None:
    """The quick checks a new project shows before the Overview: asked for, then the page again."""
    page.evaluate("sessionStorage.setItem('visual.quickchecks', 'show')")
    page.evaluate(
        "Object.keys(localStorage).filter(k => k.startsWith('ingence.quickchecks.'))"
        ".forEach(k => localStorage.removeItem(k))"
    )
    page.reload()
    page.wait_for_selector('[data-view="quick-checks"]', timeout=HANG_GUARD_MS)
    page.evaluate("sessionStorage.removeItem('visual.quickchecks')")


#: The views a person reaches by acting on a section, and the pages beside the Dashboard. A card
#: at each scale of By year (project, area, theme, experiment, file), its Wheel, Stability
#: behind O, Overlap's Connections with a line opened, and a search with results.
VARIANTS: tuple[Shot, ...] = (
    Shot("landing", "path", "landing"),
    Shot("card-project", "section", "years", _years_card(0, "The project")),
    Shot("card-person", "section", "years", _years_card(0, "An area")),
    Shot("card-line-of-work", "section", "years", _years_card(1, "A theme")),
    Shot("card-experiment", "section", "years", _years_card(1, "An experiment")),
    Shot("card-file", "section", "years", _years_card(2, "A file")),
    Shot("years-wheel", "section", "years", _years_key("w", ".ybig .ywheel svg")),
    Shot("years-overlap", "section", "years", _years_key("o", ".ystab")),
    Shot("overlap-connections", "section", "overlap", _connections_line),
    Shot("ask-results", "section", "overview", _search_for_a_word),
    Shot("questions-bulk-bar", "section", "review", _bulk_bar_open),
    # Last: the quick checks stay on the Overview for the rest of the page's life once asked for.
    Shot("quick-checks", "section", "overview", _quick_checks),
)

SHOTS: tuple[Shot, ...] = (
    *(Shot(f"page-{name}", "page", name) for name in PAGE_NAMES),
    *(Shot(name, "section", name) for name in SECTION_NAMES),
    *VARIANTS,
)


#: ``INGENCE_VISUAL_ONLY`` names views (comma-separated, by file-name part) to take alone, while
#: one is being worked on. The lane's own completeness check is the whole list.
ONLY = tuple(each for each in os.environ.get("INGENCE_VISUAL_ONLY", "").split(",") if each)


def test_the_views_asked_for_alone_are_all_listed() -> None:
    """``INGENCE_VISUAL_ONLY`` that names a view the lane does not list takes no pictures, and a
    run of no pictures must not pass."""
    listed = {shot.name for shot in SHOTS}
    unknown = [name for name in ONLY if name not in listed]
    assert not unknown, (
        f"INGENCE_VISUAL_ONLY names no listed view: {unknown}; listed: {sorted(listed)}"
    )


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@contextlib.contextmanager
def _exclusive() -> Iterator[None]:
    """One run of the lane at a time on this machine (see ``LOCK``)."""
    if not HAS_LOCK:
        yield
        return
    with LOCK.open("w") as held:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail(
                        f"another run of the visual lane held {WORK} for {LOCK_WAIT_SECONDS} s"
                    )
                time.sleep(1)
        try:
            yield
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    named = os.environ.get("INGENCE_VISUAL_PROJECT")
    if not named:
        pytest.skip("INGENCE_VISUAL_PROJECT names no project")
    with _exclusive():
        # A folder of a fixed name: the landing page prints where the project is, and a picture
        # must not carry the name of a temporary folder or of the person who ran it.
        base = WORK
        if base.exists():
            shutil.rmtree(base)
        project = base / "project"
        shutil.copytree(Path(named), project)
        config_home = base / "config-home"
        config_home.mkdir()
        # The landing page offers a folder in the person's home to write projects to: the server
        # sees a home of the lane's own, so no picture carries a person's name.
        home = base / "home"
        home.mkdir()
        real_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            process, address = start_dashboard(project, config_home)
        finally:
            if real_home is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = real_home
        try:
            yield address
        finally:
            stop(process)
            shutil.rmtree(base, ignore_errors=True)


def _drawn(page: Any) -> None:
    """Wait until the page has drawn what it was asked for and asks for nothing more."""
    page.wait_for_function(
        "() => { const list = document.querySelector('#list');"
        " return list && list.children.length > 0 && !document.querySelector("
        "'.state-loading, .state-card[data-state=\"loading\"]'); }",
        timeout=HANG_GUARD_MS,
    )
    # Everything the view is still asking for has come: the page draws nothing more by itself.
    settled(page)
    page.evaluate("document.fonts.ready.then(() => true)")


def _on_the_dashboard(page: Any, base: str) -> None:
    """Make sure the Dashboard itself is open: a new page shows nothing, and the landing page is
    a page beside it."""
    if page.url.startswith(base) and page.locator("nav.sections").count() > 0:
        return
    page.goto(f"{base}#years")
    page.wait_for_selector("nav.sections", state="attached", timeout=HANG_GUARD_MS)


def _explorer_drawn(page: Any) -> None:
    """The Explorer is drawn: its Wheel is laid out (a worker does that, which the quiet wait cannot
    see), the crumbs name the center, the legend is up and the canvas holds pixels."""
    page.wait_for_selector(".ix-root:not([data-loading]) .ix-canvas-wrap", timeout=HANG_GUARD_MS)
    page.wait_for_function(
        "() => { const crumbs = document.querySelector('.ix-crumbs');"
        " return crumbs && crumbs.textContent.trim().length > 0; }",
        timeout=HANG_GUARD_MS,
    )
    page.wait_for_selector(".ix-legend", state="visible", timeout=HANG_GUARD_MS)
    page.wait_for_function(
        "() => { const canvas = document.querySelector('.ix-canvas-wrap canvas');"
        " if (!canvas || !canvas.width || !canvas.height) return false;"
        " const context = canvas.getContext('2d');"
        " const data = context.getImageData(0, 0, canvas.width, canvas.height).data;"
        " for (let at = 3; at < data.length; at += 4) { if (data[at] > 0) return true; }"
        " return false; }",
        timeout=HANG_GUARD_MS,
    )


def _open_section(page: Any, base: str, view: str) -> None:
    """Show a section by its address and wait until it has drawn something."""
    _on_the_dashboard(page, base)
    page.evaluate("view => { location.hash = '#' + view; }", view)
    # #years opens Overlap in its By year mode, and Overlap's tab is the one shown.
    page.wait_for_function(
        "view => { const on = document.querySelector('nav.sections button.active');"
        " return on && on.dataset.view === view; }",
        arg={"years": "overlap"}.get(view, view),
        timeout=HANG_GUARD_MS,
    )
    if view == "explorer":
        _explorer_drawn(page)
    if view == "map":
        page.wait_for_selector(".map-dot", timeout=HANG_GUARD_MS)
    _drawn(page)


def _open_page(page: Any, base: str, name: str) -> None:
    """Show a page by clicking its tab, from a section of another page."""
    other = "advanced" if name != "advanced" else "experiments"
    first = {"advanced": "rules", "experiments": "experiments"}[other]  # a section of another page
    _open_section(page, base, first)
    page.locator(f'nav#pages button[data-page="{name}"]').click()
    page.wait_for_function(
        'name => { const nav = document.querySelector(`nav.sections[data-sections-of="${name}"]`);'
        " return nav && !nav.hidden; }",
        arg=name,
        timeout=HANG_GUARD_MS,
    )
    _drawn(page)


def _take(page: Any, base: str, shot: Shot) -> bytes:
    if shot.kind == "path":
        page.goto(f"{base}{shot.address}")
        settled(page)
        page.evaluate("document.fonts.ready.then(() => true)")
    else:
        if shot.kind == "page":
            _open_page(page, base, shot.address)
        else:
            _open_section(page, base, shot.address)
        if shot.act is not None:
            shot.act(page)
            _drawn(page)
    # No hover or focus help over the picture: the pointer in the empty corner, nothing focused.
    page.mouse.move(1, 1)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    settled(page)
    return bytes(page.screenshot(animations="disabled", caret="hide"))


def _different_share(taken: bytes, baseline: bytes) -> float:
    """The share of pixels that differ between two screenshots (1.0 when their sizes differ)."""
    width, height, step, rows = png_rows(taken)
    other_width, other_height, other_step, other_rows = png_rows(baseline)
    if (width, height, step) != (other_width, other_height, other_step):
        return 1.0
    different = 0
    for mine, theirs in zip(rows, other_rows, strict=True):
        if mine == theirs:
            continue
        for column in range(width):
            if (
                mine[column * step : (column + 1) * step]
                != theirs[column * step : (column + 1) * step]
            ):
                different += 1
    return different / (width * height)


def test_the_lane_lists_every_page_and_section_the_dashboard_has(browser: Any, served: str) -> None:
    """A page or section added to the navigation, or taken out of it, fails here by name."""
    context = browser.new_context(viewport=VIEWPORT)
    page = context.new_page()
    try:
        page.goto(f"{served}#years")
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        sections = page.eval_on_selector_all(
            "nav.sections button[data-view]", "nodes => nodes.map(n => n.dataset.view)"
        )
        pages = page.eval_on_selector_all(
            "nav#pages button[data-page]", "nodes => nodes.map(n => n.dataset.page)"
        )
    finally:
        context.close()
    assert sorted(pages) == sorted(PAGE_NAMES), (
        f"pages the lane does not list: {sorted(set(pages) - set(PAGE_NAMES))}; "
        f"listed and not in the navigation: {sorted(set(PAGE_NAMES) - set(pages))}"
    )
    assert sorted(sections) == sorted(SECTION_NAMES), (
        f"sections the lane does not list: {sorted(set(sections) - set(SECTION_NAMES))}; "
        f"listed and not in the navigation: {sorted(set(SECTION_NAMES) - set(sections))}"
    )
    names = [shot.name for shot in SHOTS]
    assert len(names) == len(set(names)), "two views share a file name"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_every_view_looks_as_approved(browser: Any, served: str, scheme: str) -> None:
    context = browser.new_context(viewport=VIEWPORT, color_scheme=scheme, reduced_motion="reduce")
    context.add_init_script(_STILL)
    page = context.new_page()
    out = CANDIDATES / browser_name()
    approved = BASELINES / browser_name()
    out.mkdir(parents=True, exist_ok=True)
    unapproved: list[str] = []
    changed: list[str] = []
    missing: list[str] = []
    taken_count = 0
    try:
        wanted = [shot for shot in SHOTS if not ONLY or shot.name in ONLY]
        assert wanted, "no view to take"
        for shot in wanted:
            try:
                taken = _take(page, served, shot)
            except sync_api.Error as error:
                missing.append(f"{shot.name} ({shot.kind} {shot.address}): {str(error)[:120]}")
                continue
            taken_count += 1
            name = f"M-{scheme}-{shot.name}.png"
            baseline = approved / name
            if not baseline.is_file():
                (out / name).write_bytes(taken)
                unapproved.append(name)
                continue
            share = _different_share(taken, baseline.read_bytes())
            if share > MAX_DIFFERENT_SHARE:
                (out / name).write_bytes(taken)
                changed.append(f"{name} ({share:.1%} of its pixels differ)")
    finally:
        context.close()
    print(f"\n{scheme}: {taken_count} of {len(wanted)} views taken, in {out}")
    print(f"{len(unapproved)} with no baseline, {len(changed)} changed, {len(missing)} not drawn")
    assert missing == [], "views that did not draw: " + "; ".join(missing)
    assert taken_count == len(wanted)
    assert changed == [], "views that no longer look as approved: " + "; ".join(changed)
