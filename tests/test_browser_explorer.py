"""The Graph workspace in a real browser: the Wheel by keyboard, address and table.

Needs the ``browser`` extra and a browser Playwright can start. The tests are skipped, never
failed, when either is missing, as ``test_browser.py`` is, and required in CI's browser job. Set
``INGENCE_BROWSER_SCREENSHOTS`` to a folder to keep screenshots of the fixture's Wheel (invented
names only): each mode's overview and detail, light and dark, narrow and wide.

1. The Wheel draws, and is walked by keyboard: arrows move between folders, Space selects one
   (the inspector names it, the address holds it), Enter centres on it, back returns to the
   root and a reload keeps what was chosen.
2. The table says what the drawing says, and centres the Wheel from a row.
3. A measure is chosen from the toolbar and kept in the address; the legend follows it.
4. Nothing leaves the machine: every request the page makes is to the loopback server, and
   the page logs no error, in either theme and with motion reduced.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser, settled

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_browser import _free_port, _serve, _shots, _stop

REQUIRED = os.environ.get("INGENCE_BROWSER_REQUIRED") == "1"
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="the browser extra is not installed"
    )


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _settled(page: Any) -> None:
    """Wait until the Wheel is drawn, no new one is on its way and the page is asking for nothing
    more. The pages open with reduced motion, so the Wheel has no sweep to wait out."""
    page.wait_for_selector(".ix-root:not([data-loading]) .ix-canvas-wrap")
    settled(page)


@pytest.fixture(scope="module")
def explorer(explorer_project):
    return explorer_project


@pytest.fixture(scope="module")
def served(explorer):
    port = _free_port()
    process = _serve(explorer.project, port)
    yield f"http://127.0.0.1:{port}/"
    _stop(process)


def _open(
    browser: Any, base: str, address: str, **options: Any
) -> tuple[Any, list[str], list[str]]:
    options.setdefault("reduced_motion", "reduce")
    page = browser.new_page(
        viewport=options.pop("viewport", {"width": 1400, "height": 900}), **options
    )
    errors: list[str] = []
    requests: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource")
            else None
        ),
    )
    page.on("request", lambda request: requests.append(request.url))
    # A browser asks every site for its icon; the Dashboard has none, and that 404 is the only
    # failure allowed. Any other failed response is an error.
    page.on(
        "response",
        lambda response: (
            errors.append(f"{response.status} {response.url}")
            if response.status >= 400 and not response.url.endswith("/favicon.ico")
            else None
        ),
    )
    page.goto(f"{base}{address}")
    _settled(page)
    return page, errors, requests


def test_the_wheel_is_walked_by_keyboard_and_kept_in_the_address(browser, served, explorer) -> None:
    page, errors, _ = _open(browser, served, "#explorer")
    try:
        wheel = page.locator(".ix-canvas-wrap")
        label = wheel.get_attribute("aria-label") or ""
        assert "file" in label
        _shots(page, "explorer-wheel-overview-light-wide")
        wheel.focus()
        page.keyboard.press("ArrowDown")
        page.keyboard.press(" ")
        page.wait_for_function("new URLSearchParams(location.search).get('sel')")
        selected = page.evaluate("new URLSearchParams(location.search).get('sel')")
        inspector = page.locator(".ix-inspector")
        inspector.get_by_role("button", name="Center the Wheel here").wait_for()
        name = inspector.locator("h2").inner_text()
        assert name.strip()
        page.keyboard.press("Enter")
        page.wait_for_function(
            f"new URLSearchParams(location.search).get('focus') === '{selected}'"
        )
        _settled(page)
        # The crumbs name the new center once its data has come.
        page.locator(".ix-crumbs").filter(has_text=name.strip()).wait_for()
        _shots(page, "explorer-wheel-detail-light-wide")
        page.go_back()
        page.wait_for_function("!new URLSearchParams(location.search).get('focus')")
        page.go_forward()
        page.wait_for_function(
            f"new URLSearchParams(location.search).get('focus') === '{selected}'"
        )
        page.reload()
        _settled(page)
        # The crumbs name the new center once its data has come.
        page.locator(".ix-crumbs").filter(has_text=name.strip()).wait_for()
        assert not errors, errors
    finally:
        page.close()


def test_the_table_says_what_the_wheel_draws_and_centres_from_a_row(
    browser, served, explorer
) -> None:
    page, errors, _ = _open(browser, served, "#explorer")
    try:
        page.locator(".ix-canvas-wrap").focus()
        page.keyboard.press("t")
        table = page.locator("table.ix-table")
        table.wait_for()
        rows = table.locator("tbody tr")
        assert rows.count() > 3
        first = explorer.experiments[0]
        row = rows.filter(has=page.get_by_role("button", name=first, exact=True))
        assert row.count() == 1
        row.get_by_role("button", name="Center here").click()
        page.wait_for_function(
            f"new URLSearchParams(location.search).get('focus') === '{explorer.ids[first]}'"
        )
        table.locator("tbody tr").filter(has_text="raw").first.wait_for()
        _shots(page, "explorer-wheel-table-light-wide")
        assert not errors, errors
    finally:
        page.close()


def test_a_measure_is_chosen_and_the_legend_follows(browser, served) -> None:
    page, errors, _ = _open(browser, served, "#explorer")
    try:
        page.locator(".ix-toolbar").get_by_role("button", name="Provenance").click()
        page.wait_for_function("new URLSearchParams(location.search).get('m') === 'prov'")
        legend = page.locator(".ix-legend")
        legend.get_by_text("published derivation").wait_for()
        legend.get_by_text("proposed only").wait_for()
        page.reload()
        try:
            page.wait_for_selector(".ix-legend")
        except sync_api.TimeoutError:
            # Said with what the page showed, since a legend that never comes back is seen rarely.
            shown = page.inner_text("body")[:400].replace("\n", " | ")
            pytest.fail(f"no legend after the reload at {page.url}; errors {errors}; page: {shown}")
        assert (
            page.locator(".ix-toolbar button[aria-pressed=true]", has_text="Provenance").count()
            == 1
        )
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize(
    ("scheme", "width"),
    [("dark", 1400), ("light", 420), ("dark", 420)],
    ids=["dark-wide", "light-narrow", "dark-narrow"],
)
def test_nothing_leaves_the_machine_in_either_theme_or_width(
    browser, served, scheme, width
) -> None:
    page, errors, requests = _open(
        browser,
        served,
        "#explorer",
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
    )
    try:
        _shots(page, f"explorer-wheel-overview-{scheme}-{'narrow' if width < 900 else 'wide'}")
        foreign = [url for url in requests if not url.startswith(served)]
        assert not foreign, foreign
        assert not errors, errors
        # Nothing wider than the page: no sideways scroll at a phone's width.
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1
    finally:
        page.close()


if os.environ.get("INGENCE_BROWSER_SCREENSHOTS"):
    Path(os.environ["INGENCE_BROWSER_SCREENSHOTS"]).mkdir(parents=True, exist_ok=True)


def _lineage_settled(page: Any) -> None:
    page.wait_for_selector(".ix-root:not([data-loading]) .ix-lineage svg g rect")
    settled(page)


def test_lineage_draws_forks_and_joins_and_is_walked_by_keyboard(browser, served, explorer) -> None:
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    focus = explorer.ids[summary]
    page, errors, requests = _open_lineage(browser, served, f"?mode=lineage&focus={focus}#explorer")
    try:
        assert any("explorer-worker" in url for url in requests), "the layout ran off the worker"
        _shots(page, "explorer-lineage-overview-light-wide")
        view = page.locator(".ix-lineage")
        view.focus()
        page.keyboard.press("ArrowUp")
        page.wait_for_function(
            f"(new URLSearchParams(location.search).get('sel') || '') !== '{focus}'"
        )
        upstream = page.evaluate("new URLSearchParams(location.search).get('sel')")
        assert upstream and upstream != focus
        page.keyboard.press("Enter")
        page.wait_for_function(
            f"new URLSearchParams(location.search).get('focus') === '{upstream}'"
        )
        _lineage_settled(page)
        _shots(page, "explorer-lineage-detail-light-wide")
        page.go_back()
        page.wait_for_function(f"new URLSearchParams(location.search).get('focus') === '{focus}'")
        page.keyboard.press("t")
        table = page.locator("table.ix-table").last
        table.get_by_text("produced from (published)").first.wait_for()
        assert not errors, errors
    finally:
        page.close()


def test_lineage_is_laid_out_where_its_worker_fails(browser, served, explorer) -> None:
    """A worker that fails does not leave Lineage waiting with no word. Its waiting layouts are
    laid out in the page instead."""
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(
        "**/*layout.worker.js",
        lambda route: route.fulfill(
            status=200,
            content_type="text/javascript",
            body="throw new Error('a worker that cannot start');",
        ),
    )
    try:
        page.goto(f"{served}?mode=lineage&focus={explorer.ids[summary]}#explorer")
        _lineage_settled(page)
        assert page.locator(".ix-lineage svg g rect").count() > 0
        assert not page.get_by_text("Laying out the lineage").is_visible()
    finally:
        page.close()


def test_lineage_proposals_are_asked_for_and_a_mode_switch_keeps_the_selection(
    browser, served, explorer
) -> None:
    raw = explorer.mentions[0][1]
    page, errors, _ = _open(browser, served, f"?sel={explorer.ids[raw]}#explorer")
    try:
        page.locator(".ix-toolbar").get_by_role("button", name="Lineage").click()
        page.wait_for_function(
            f"new URLSearchParams(location.search).get('focus') === '{explorer.ids[raw]}'"
        )
        _lineage_settled(page)
        page.locator(".ix-toolbar").get_by_role("button", name="Proposals").click()
        page.wait_for_function("new URLSearchParams(location.search).get('prop') === '1'")
        _lineage_settled(page)
        page.locator(".ix-toolbar").get_by_role("button", name="Table").click()
        page.locator("table.ix-table").last.get_by_text("mentions (proposed)").first.wait_for()
        page.locator(".ix-toolbar").get_by_role("button", name="Wheel").click()
        page.wait_for_selector(".ix-root:not([data-loading]) .ix-table")
        assert page.evaluate("new URLSearchParams(location.search).get('sel')") == explorer.ids[raw]
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize(
    ("scheme", "width"), [("dark", 1400), ("light", 420)], ids=["dark-wide", "light-narrow"]
)
def test_lineage_in_either_theme_or_width(browser, served, explorer, scheme, width) -> None:
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    page, errors, requests = _open_lineage(
        browser,
        served,
        f"?mode=lineage&focus={explorer.ids[summary]}&groups=1&prop=1#explorer",
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
    )
    try:
        _shots(page, f"explorer-lineage-overview-{scheme}-{'narrow' if width < 900 else 'wide'}")
        assert not [url for url in requests if not url.startswith(served)]
        assert not errors, errors
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1
    finally:
        page.close()


def _open_lineage(
    browser: Any, base: str, address: str, **options: Any
) -> tuple[Any, list[str], list[str]]:
    options.setdefault("reduced_motion", "reduce")
    page = browser.new_page(
        viewport=options.pop("viewport", {"width": 1400, "height": 900}), **options
    )
    errors: list[str] = []
    requests: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.on(
        "response",
        lambda response: (
            errors.append(f"{response.status} {response.url}")
            if response.status >= 400 and not response.url.endswith("/favicon.ico")
            else None
        ),
    )
    page.goto(f"{base}{address}")
    _lineage_settled(page)
    return page, errors, requests


def _timeline_settled(page: Any) -> None:
    page.wait_for_selector(".ix-root:not([data-loading]) .ix-timeline svg")
    settled(page)


def test_timeline_activity_by_keyboard_lists_a_period_s_files_and_a_window_is_chosen(
    browser, served
) -> None:
    page, errors, _ = _open_lineage_like(
        browser, served, "?mode=timeline#explorer", _timeline_settled
    )
    try:
        _shots(page, "explorer-timeline-activity-light-wide")
        view = page.locator(".ix-timeline")
        view.focus()
        page.keyboard.press("ArrowDown")
        page.wait_for_function(
            "(new URLSearchParams(location.search).get('sel') || '').startsWith('tbin:')"
        )
        page.locator(".ix-inspector").get_by_text("A period").wait_for()
        page.locator(".ix-inspector .ix-list li").first.wait_for()
        box = view.bounding_box()
        assert box is not None
        page.keyboard.down("Shift")
        page.mouse.move(box["x"] + box["width"] * 0.35, box["y"] + box["height"] * 0.5)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5, steps=6)
        page.mouse.up()
        page.keyboard.up("Shift")
        page.wait_for_function("new URLSearchParams(location.search).get('from') !== null")
        _timeline_settled(page)
        page.locator('.ix-toolbar [title="Clear the window chosen"]').click()
        page.wait_for_function("new URLSearchParams(location.search).get('from') === null")
        assert not errors, errors
    finally:
        page.close()


def test_timeline_thread_from_lineage_places_files_and_names_sources(
    browser, served, explorer
) -> None:
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    focus = explorer.ids[summary]
    page, errors, _ = _open_lineage(browser, served, f"?mode=lineage&focus={focus}#explorer")
    try:
        page.locator(".ix-toolbar").get_by_role("button", name="Timeline").click()
        page.wait_for_function("new URLSearchParams(location.search).get('reading') === 'thread'")
        _timeline_settled(page)
        _shots(page, "explorer-timeline-thread-light-wide")
        select = page.locator(".ix-toolbar select")
        select.select_option("event")
        page.wait_for_function("new URLSearchParams(location.search).get('src') === 'event'")
        _timeline_settled(page)
        page.locator(".ix-toolbar").get_by_role("button", name="Table").click()
        page.locator("table.ix-table").get_by_text("undated under this source").first.wait_for()
        assert not errors, errors
    finally:
        page.close()


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _period_in_utc(edge: str, unit: str) -> str:
    """A bin's period as the page names it, read from the edge's own text in UTC."""
    import datetime as dt

    moment = dt.datetime.fromisoformat(edge.replace("Z", "+00:00")).astimezone(dt.UTC)
    day = f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"
    return {
        "hour": f"{day} {moment.hour:02d}:00",
        "day": day,
        "week": f"week of {day}",
        "month": f"{_MONTHS[moment.month - 1]} {moment.year}",
        "quarter": f"Q{(moment.month - 1) // 3 + 1} {moment.year}",
        "year": str(moment.year),
    }[unit]


def test_a_period_is_named_in_utc_for_a_reader_west_of_it(browser, served) -> None:
    """Bins are UTC periods. Named in a reader's zone, each would be a day early for a reader in
    New York ("31 Mar" for April). The page names a period in UTC, from the edge's own text, and
    says so."""
    page, errors, requests = _open_lineage_like(
        browser,
        served,
        "?mode=timeline#explorer",
        _timeline_settled,
        timezone_id="America/New_York",
    )
    try:
        asked = [url for url in requests if "/api/v1/explorer/timeline" in url][-1]
        body = json.loads(urllib.request.urlopen(asked, timeout=30).read())
        edges, unit = body["activity"]["bins"]["edges"], body["activity"]["bins"]["unit"]
        page.locator(".ix-timeline svg text", has_text="UTC").first.wait_for()
        page.locator(".ix-timeline").focus()
        page.keyboard.press("ArrowDown")
        page.wait_for_function(
            "(new URLSearchParams(location.search).get('sel') || '').startsWith('tbin:')"
        )
        chosen = page.evaluate("new URLSearchParams(location.search).get('sel')")
        bin_index = int(chosen.split(":")[2])
        said = page.locator(".ix-inspector .ix-note").first.inner_text()
        assert _period_in_utc(edges[bin_index], unit) in said, (said, edges[bin_index])
        assert "UTC" in said
        # The table names each row's periods the same way.
        page.locator(".ix-toolbar").get_by_role("button", name="Table").click()
        table = page.locator("table.ix-table")
        table.wait_for()
        track = next(
            index
            for index, row in enumerate(body["activity"]["tracks"])
            if sum(body["activity"]["counts"][body["source"]][index]) and row["files"]
        )
        counts = body["activity"]["counts"][body["source"]][track]
        first = next(index for index, value in enumerate(counts) if value)
        assert _period_in_utc(edges[first], unit) in table.inner_text()
        assert not errors, errors
    finally:
        page.close()


def test_a_file_card_names_its_times_in_utc_beside_the_timeline(browser, served, explorer) -> None:
    """The card shows an instant in UTC and says so. A card in the reader's zone, unnamed, beside
    a Timeline in UTC would show a reader in New York another hour, or another day."""
    import datetime as dt

    raw = explorer.mentions[0][1]
    card = json.loads(
        urllib.request.urlopen(
            f"{served}api/v1/explorer/entry?id={explorer.ids[raw]}", timeout=30
        ).read()
    )
    modified = next(time for time in card["times"] if time["kind"] == "modified")
    moment = dt.datetime.fromisoformat(modified["at"].replace("Z", "+00:00")).astimezone(dt.UTC)
    expected = (
        f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year} "
        f"{moment.hour:02d}:{moment.minute:02d} UTC"
    )
    page, errors, _ = _open(
        browser, served, f"?sel={explorer.ids[raw]}#explorer", timezone_id="America/New_York"
    )
    try:
        page.locator(".ix-inspector").get_by_text(expected).first.wait_for()
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize(
    ("scheme", "width"), [("dark", 1400), ("light", 420)], ids=["dark-wide", "light-narrow"]
)
def test_timeline_in_either_theme_or_width(browser, served, scheme, width) -> None:
    page, errors, requests = _open_lineage_like(
        browser,
        served,
        "?mode=timeline#explorer",
        _timeline_settled,
        viewport={"width": width, "height": 900},
        color_scheme=scheme,
        reduced_motion="reduce",
    )
    try:
        _shots(page, f"explorer-timeline-activity-{scheme}-{'narrow' if width < 900 else 'wide'}")
        assert not [url for url in requests if not url.startswith(served)]
        assert not errors, errors
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1
    finally:
        page.close()


def _open_lineage_like(
    browser: Any, base: str, address: str, settle: Any, **options: Any
) -> tuple[Any, list[str], list[str]]:
    options.setdefault("reduced_motion", "reduce")
    page = browser.new_page(
        viewport=options.pop("viewport", {"width": 1400, "height": 900}), **options
    )
    errors: list[str] = []
    requests: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.on(
        "response",
        lambda response: (
            errors.append(f"{response.status} {response.url}")
            if response.status >= 400 and not response.url.endswith("/favicon.ico")
            else None
        ),
    )
    page.goto(f"{base}{address}")
    settle(page)
    return page, errors, requests


@pytest.fixture
def served_copy(explorer, tmp_path):
    """A copy of the fixture served on its own, for a test that writes to the Record."""
    import shutil

    copy = tmp_path / "project"
    shutil.copytree(explorer.project, copy)
    port = _free_port()
    process = _serve(copy, port)
    yield f"http://127.0.0.1:{port}/", copy
    _stop(process)


def test_a_label_shows_at_once_survives_modes_undoes_and_saves_through_a_review(
    browser, served_copy, explorer
) -> None:
    base, _ = served_copy
    summary = next(t for _, t in explorer.derivations if t.endswith("_summary.csv"))
    focus = explorer.ids[summary]
    writes: list[str] = []
    page, errors, _ = _open_lineage(
        browser, base, f"?mode=lineage&focus={focus}&sel={focus}#explorer"
    )
    page.on(
        "request", lambda request: writes.append(request.url) if request.method == "POST" else None
    )
    try:
        inspector = page.locator(".ix-inspector")
        inspector.get_by_role("button", name="Add a label").click()
        field = inspector.get_by_role("textbox", name=f"Label for {summary.rsplit('/', 1)[-1]}")
        field.fill("the kelt summary")
        field.press("Enter")
        # At once, in the drawing, marked unsaved, and nothing written yet.
        page.locator(".ix-lineage svg text", has_text="the kelt summary").first.wait_for()
        assert page.locator(".ix-toolbar", has_text="1 unsaved edit").count() == 1
        assert not writes
        _shots(page, "explorer-label-unsaved-lineage")
        # The same label in another mode.
        page.locator(".ix-toolbar").get_by_role("button", name="Timeline").click()
        _timeline_settled(page)
        page.locator(".ix-timeline svg text", has_text="the kelt summary").first.wait_for()
        # Undone and done again, from the keyboard.
        page.locator(".ix-timeline").focus()
        page.keyboard.press("Control+z")
        page.wait_for_function("!document.querySelector('.ix-toolbar .ix-human-pill')")
        page.keyboard.press("Control+Shift+z")
        page.locator(".ix-toolbar .ix-human-pill").wait_for()
        # Reviewed and saved: the one write, under a name given once.
        page.locator(".ix-toolbar .ix-human-pill").click()
        review = page.locator(".ix-review")
        review.get_by_text("the kelt summary").wait_for()
        review.get_by_role("textbox").fill("a. person")
        review.get_by_role("button", name="Save 1 label to the Record").click()
        review.get_by_text("1 statement written to the Record under a. person").wait_for()
        assert any(url.endswith("/api/review/answer") for url in writes)
        page.reload()
        _timeline_settled(page)
        page.locator(".ix-timeline svg text", has_text="the kelt summary").first.wait_for()
        assert page.locator(".ix-toolbar .ix-human-pill").count() == 0
        assert not errors, errors
    finally:
        page.close()
    card = json.loads(
        urllib.request.urlopen(f"{base}api/v1/explorer/entry?id={focus}", timeout=30).read()
    )
    assert card["name"] == summary.rsplit("/", 1)[-1]


def test_a_staged_move_is_a_plan_with_a_diff_and_writes_nothing(
    browser, served_copy, explorer
) -> None:
    base, _ = served_copy
    raw = explorer.mentions[0][1]
    focus = explorer.ids[raw]
    writes: list[str] = []
    page, errors, _ = _open_lineage(
        browser, base, f"?mode=lineage&focus={focus}&sel={focus}&depth=2#explorer"
    )
    page.on(
        "request", lambda request: writes.append(request.url) if request.method == "POST" else None
    )
    try:
        chooser = page.locator(".ix-inspector").get_by_role("combobox", name="Move in the plan to")
        options = chooser.locator("option").all_inner_texts()
        target = next(option for option in options if option.startswith("move to"))
        chooser.select_option(label=target)
        page.locator(".ix-lineage svg text", has_text="in the plan, from").first.wait_for()
        page.locator(".ix-toolbar .ix-human-pill", has_text="1 in the plan").click()
        review = page.locator(".ix-review")
        review.get_by_text("A plan only").wait_for()
        review.locator("ins", has_text=target.removeprefix("move to ")).wait_for()
        _shots(page, "explorer-plan-review")
        assert not writes, writes
        # Kept as a plan in the workspace and checked: the one write is the plan's own file.
        review.get_by_role("textbox", name="Who is deciding on this machine").fill("a. person")
        review.get_by_role("button", name="Keep and check the plan").click()
        review.get_by_text("Kept as").wait_for()
        assert review.get_by_role("button", name="Apply (not available)").is_disabled()
        assert "Nothing was moved" in review.inner_text()
        assert all(url.endswith(("/api/workspace/plan", "/api/decider")) for url in writes), writes
        writes.clear()
        page.keyboard.press("Escape")
        page.locator(".ix-lineage").focus()
        page.keyboard.press("Control+z")
        page.wait_for_function("!document.querySelector('.ix-toolbar .ix-human-pill')")
        assert not writes, writes
        assert not errors, errors
    finally:
        page.close()


def test_the_wheel_is_arranged_by_what_names_say_and_drilled_into(browser, served) -> None:
    """Arrange by technique from the panel (key A), read a value's spellings, look inside it."""
    page, errors, requests = _open(browser, served, "#explorer")
    try:
        page.locator(".ix-canvas-wrap").focus()
        page.keyboard.press("a")
        panel = page.get_by_role("dialog", name="Arrange the Wheel")
        panel.wait_for()
        panel.get_by_role("button", name="Technique, then condition").click()
        _settled(page)
        assert (
            "arr=t.technique%2Ct.condition" in page.url or "arr=t.technique,t.condition" in page.url
        )
        page.keyboard.press("Escape")
        page.keyboard.press("t")
        table = page.locator("table.ix-table")
        table.wait_for()
        table.get_by_role("button", name="confocal", exact=True).click()
        inspector = page.locator(".ix-inspector")
        inspector.get_by_text("Written in names as").wait_for()
        assert inspector.get_by_text("lsm", exact=True).is_visible()
        assert "One sense written 2 ways" in inspector.inner_text()
        inspector.get_by_role("button", name="Look inside").click()
        page.wait_for_url("**w=t.technique%3Dconfocal**")
        page.wait_for_selector(".ix-root:not([data-loading]) table.ix-table")
        crumbs = page.locator(".ix-crumbs")
        assert crumbs.get_by_role("button", name="confocal").is_visible()
        assert "wild type" in page.locator("table.ix-table").inner_text()
        # Removing the filter returns every file; back returns to the arrangement.
        page.go_back()
        page.wait_for_selector(".ix-root:not([data-loading]) table.ix-table")
        assert "w=" not in page.url
        assert "no technique named" in page.locator("table.ix-table").inner_text()
        assert not errors, errors
        assert all(url.startswith(served) or url.startswith("data:") for url in requests)
    finally:
        page.close()


def test_a_person_s_layer_is_drafted_undone_saved_and_read_back_with_its_state(
    browser, served_copy, explorer
) -> None:
    """A group, an exclusion and a proposed relation from the inspector; each a draft marked so
    until saved in one reviewed write, then read back from the Record with its state; a
    relation's other end chosen in the drawing."""
    base, _ = served_copy
    first, second, _ = explorer.experiments
    writes: list[str] = []
    page, errors, _ = _open(browser, base, "#explorer")
    page.on(
        "request", lambda request: writes.append(request.url) if request.method == "POST" else None
    )
    try:
        page.locator(".ix-canvas-wrap").focus()
        page.keyboard.press("t")
        table = page.locator("table.ix-table")
        table.wait_for()
        table.get_by_role("button", name=first, exact=True).click()
        inspector = page.locator(".ix-inspector")
        inspector.get_by_role("button", name="New topic…").click()
        name = inspector.get_by_role("textbox", name="A new topic's name")
        name.fill("an invented series")
        name.press("Enter")
        inspector.locator(".ix-human-chip", has_text="an invented series").wait_for()
        assert inspector.locator(".ix-state-draft").count() >= 1
        inspector.get_by_role("button", name="Set aside…").click()
        inspector.get_by_role("button", name="a duplicate").click()
        inspector.get_by_text("a duplicate").first.wait_for()
        inspector.get_by_role("combobox").filter(has_text="Propose a relation").select_option(
            "same_experiment"
        )
        page.locator(".ix-relating").wait_for()
        table.get_by_role("button", name=second, exact=True).click()
        page.wait_for_function("!document.querySelector('.ix-relating')")
        assert page.locator(".ix-toolbar", has_text="3 unsaved edits").count() == 1
        assert not writes
        # Undone and done again.
        page.locator("table.ix-table").focus()
        page.keyboard.press("Control+z")
        page.locator(".ix-toolbar", has_text="2 unsaved edits").wait_for()
        page.keyboard.press("Control+Shift+z")
        page.locator(".ix-toolbar", has_text="3 unsaved edits").wait_for()
        page.locator(".ix-toolbar .ix-human-pill").click()
        review = page.locator(".ix-review")
        review.get_by_role("textbox").fill("a. person")
        review.get_by_role("button", name="Save to the Record").click()
        review.get_by_text("3 statements written to the Record under a. person").wait_for()
        review.get_by_text("In the Record (3)").wait_for()
        assert review.locator(".ix-state-projected").count() == 3
        assert len([url for url in writes if url.endswith("/api/review/answer")]) == 1
        overlay = json.loads(
            urllib.request.urlopen(f"{base}api/v1/explorer/overlay", timeout=30).read()
        )
        kinds = sorted(statement["kind"] for statement in overlay["statements"])
        assert kinds == ["exclude", "group", "relate"]
        assert all(statement["actor"] == "a. person" for statement in overlay["statements"])
        assert not errors, errors
    finally:
        page.close()
