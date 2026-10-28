"""Many ladders in Graph > Neighborhood, in a real browser.

On the explorer fixture (``fixtures/explorer_project.py``, invented names), a copy that publishes
one line from a raw file to a file of another experiment: the raw file's neighborhood stands on
two ladders.

1. Two ladders. Two bands and two cards; a node's band is its ``ladder``. The card names its rungs
   and says the line of work was not asked for. Turning Color by ladder off removes the bands and
   keeps the cards. Turning Lines of work on asks the engine again. Past the cap the list says how
   many ladders are not drawn.
2. Each row of the Items table names its ladder.
3. A file no line touches shows the sentence and its one action, and draws the center.
4. The keyboard. The plot is one stop: Tab and Shift Tab walk the items, an arrow follows a line,
   Enter centers, F fits.
5. Open in By year opens the Years view; axe finds nothing serious, in either color scheme.

Needs the ``browser`` extra and a browser Playwright can start: skipped, never failed, where either
is missing, and required where ``INGENCE_BROWSER_REQUIRED=1``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    REPOSITORY,
    launch_browser,
    need,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fixtures.explorer_project import ExplorerProject

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: The words the page uses, written as escapes so the source holds no look-alike character.
ARROW = "\u203a"
CENTERS = "the center\u2019s"
STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def explorer(explorer_project: ExplorerProject) -> ExplorerProject:
    return explorer_project


def _raw(explorer: ExplorerProject) -> str:
    from fixtures.laboratory_pool import STEMS

    return f"{explorer.experiments[0]}/raw/{STEMS[0]}_01.nd2"


def _serve_lines(
    explorer: ExplorerProject, tmp_path_factory: pytest.TempPathFactory, name: str, others: int
) -> Iterator[tuple[str, ExplorerProject]]:
    """A Dashboard on a copy whose raw file has a line to a file of ``others`` other experiments."""
    from fixtures.explorer_project import with_published
    from fixtures.laboratory_pool import STEMS

    base = tmp_path_factory.mktemp(name)
    lines = tuple(
        (
            _raw(explorer),
            f"{explorer.experiments[number]}/processed/{STEMS[number]}_01_mask.tif",
            "derives",
        )
        for number in range(1, 1 + others)
    )
    project = with_published(explorer, base / "built", lines=lines)
    copy = base / "project"
    shutil.copytree(project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield address, explorer
    finally:
        stop(process)


@pytest.fixture(scope="module")
def served(
    explorer: ExplorerProject, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, ExplorerProject]]:
    """A Dashboard on a copy whose raw file has a line to a file of another experiment."""
    yield from _serve_lines(explorer, tmp_path_factory, "neighborhood-38", 1)


@pytest.fixture(scope="module")
def served_three(
    explorer: ExplorerProject, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, ExplorerProject]]:
    """The same with lines to both other experiments: the raw file's neighborhood has three
    ladders."""
    yield from _serve_lines(explorer, tmp_path_factory, "neighborhood-38-three", 2)


def _page(browser: Any, scheme: str = "light") -> Any:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            page.errors.append(message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource")
            else None
        ),
    )
    return page


def _open(page: Any, base: str, explorer: ExplorerProject, path: str, extra: str = "") -> None:
    page.goto(f"{base}?center={explorer.ids[path]}{extra}#graph")
    page.wait_for_selector("table.graph-table", timeout=180000)
    page.wait_for_function(
        "() => document.querySelector('[data-graph=\"canvas\"]').dataset.bands !== undefined",
        timeout=180000,
    )
    # The center is selected first and the inspector fills: the page has drawn once.
    page.locator('[data-graph="inspector"]').get_by_text("What the rules said about it").wait_for(
        timeout=180000
    )


def _cards(page: Any) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = page.eval_on_selector_all(
        ".graph-ladder",
        "cards => cards.map(card => ({id: card.dataset.ladder, text: card.innerText,"
        " edge: getComputedStyle(card).borderLeftColor,"
        " palette: getComputedStyle(card).getPropertyValue('--ladder').trim()}))",
    )
    return cards


def _band_nodes(page: Any) -> dict[str, str]:
    held: dict[str, str] = json.loads(
        page.get_attribute('[data-graph="canvas"]', "data-band-nodes")
    )
    return held


# ------------------------------------------------------------------ two ladders


def test_two_ladders_show_two_bands_and_two_cards_and_a_node_stands_in_its_own_band(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        assert page.get_attribute('[data-graph="canvas"]', "data-bands") == "2"
        cards = _cards(page)
        assert len(cards) == 2
        head = page.locator(".graph-ladders .eyebrow").inner_text()
        assert head.strip().lower() == "folder levels in these connections \u00b7 2 of 2"
        # The center's ladder is first and says so; each card names root and experiment and says
        # the line of work was not asked for, in the engine's words.
        assert CENTERS in cards[0]["text"]
        assert CENTERS not in cards[1]["text"]
        for card in cards:
            assert f"data folder {ARROW} experiment" in card["text"]
            assert "theme: not asked for" in card["text"]
            assert "Open in By year" in card["text"]
            assert "member" in card["text"]
        assert cards[0]["text"].splitlines()[0].startswith(f"pool {ARROW} ")
        # Each ladder has its own color, along the card's left edge.
        assert cards[0]["edge"] != cards[1]["edge"]
        palette = [
            page.evaluate(
                "name => getComputedStyle(document.documentElement).getPropertyValue(name).trim()",
                f"--ladder-{number}",
            )
            for number in (1, 2)
        ]
        assert palette[0] != palette[1]
        assert [card["palette"] for card in cards] == palette
        # A node's band is its ladder: the drawing's, the Items table's and the cards' agree.
        by_node = _band_nodes(page)
        rows = page.eval_on_selector_all(
            ".graph-table tr[data-id] td.graph-ladder-cell",
            "cells => cells.map(cell => [cell.parentElement.dataset.id, cell.dataset.ladder])",
        )
        assert rows
        for node_id, ladder in rows:
            assert by_node[node_id] == ladder
        assert {card["id"] for card in cards} == set(by_node.values())
        assert len(set(by_node.values())) == 2
        # The center is in the first card's ladder, the other ladder's node in the second's.
        assert by_node[explorer.ids[_raw(explorer)]] == cards[0]["id"]
        assert not page.errors
    finally:
        page.context.close()


def test_the_items_table_names_each_rows_ladder_with_its_dot(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        headers = page.eval_on_selector_all(
            ".graph-table thead th", "heads => heads.map(head => head.innerText)"
        )
        assert "Levels" in headers
        cards = _cards(page)
        titles = [card["text"].splitlines()[0].replace(f" \u00b7 {CENTERS}", "") for card in cards]
        cells = page.eval_on_selector_all(
            "td.graph-ladder-cell",
            "cells => cells.map(cell => ({ladder: cell.dataset.ladder, text: cell.innerText,"
            " dot: cell.querySelector('.ladder-dot') !== null}))",
        )
        assert cells
        by_id = {card["id"]: title for card, title in zip(cards, titles, strict=True)}
        for cell in cells:
            assert cell["text"].strip() == by_id[cell["ladder"]]
            assert cell["dot"]
        assert len({cell["ladder"] for cell in cells}) == 2
    finally:
        page.context.close()


def test_color_by_ladder_off_removes_the_bands_and_keeps_the_cards(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        box = page.locator('[data-graph="color"] input')
        assert box.is_checked()
        box.uncheck()
        page.wait_for_function(
            "() => document.querySelector('[data-graph=\"canvas\"]').dataset.bands === '0'"
        )
        assert _band_nodes(page) == {}
        assert "color=0" in page.url
        # The cards stay, with a neutral edge; the table keeps the names without a colored dot.
        cards = _cards(page)
        assert len(cards) == 2
        assert all(card["palette"] == "" for card in cards)
        assert page.locator("td.graph-ladder-cell .ladder-dot").count() > 0
        page.reload()
        page.wait_for_selector("table.graph-table", timeout=180000)
        page.wait_for_function(
            "() => document.querySelector('[data-graph=\"canvas\"]').dataset.bands === '0'"
        )
        assert not page.locator('[data-graph="color"] input').is_checked()
        page.locator('[data-graph="color"] input').check()
        page.wait_for_function(
            "() => document.querySelector('[data-graph=\"canvas\"]').dataset.bands === '2'"
        )
        assert "color=0" not in page.url
        assert not page.errors
    finally:
        page.context.close()


def test_lines_of_work_on_the_rungs_asks_again_and_the_card_stops_saying_not_asked_for(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    asked: list[str] = []
    page.on(
        "request",
        lambda request: asked.append(request.url) if "graph/neighborhood" in request.url else None,
    )
    try:
        _open(page, base, explorer, _raw(explorer))
        assert "lines_of_work=false" in asked[-1]
        note = page.locator('[data-graph="lines-note"]').inner_text()
        assert note == "Off by default. Turning it on builds the themes once."
        assert not page.locator('[data-graph="lines"] input').is_checked()
        page.locator('[data-graph="lines"] input').check()
        page.wait_for_function(
            "() => { const card = document.querySelector('.graph-ladder');"
            " return card !== null && !card.innerText.includes('not asked for'); }",
            timeout=120000,
        )
        assert "lines_of_work=true" in asked[-1]
        assert "lines=1" in page.url
        text = _cards(page)[0]["text"]
        # Found, or said not to be: the line is asked for, so it is never "not asked for".
        assert "theme: " in text
        assert not page.errors
    finally:
        page.context.close()


def test_past_the_cap_the_list_counts_the_ladders_not_drawn(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer), "&max_ladders=1")
        head = page.locator(".graph-ladders .eyebrow").inner_text()
        assert head.strip().lower() == "folder levels in these connections \u00b7 1 of 2"
        assert page.locator(".graph-ladders-more").inner_text() == (
            "and 1 more chain of levels, not drawn"
        )
        assert len(_cards(page)) == 1
        assert page.get_attribute('[data-graph="canvas"]', "data-bands") == "1"
        caps = page.locator('[data-graph="caps"]').inner_text()
        assert "2 ladders here; up to 1 are drawn, the rest counted." in caps
        # The node on the ladder left out is in no band, and the table says so.
        cells = page.eval_on_selector_all("td.graph-ladder-cell", "c => c.map(x => x.innerText)")
        assert any("on levels not drawn" in cell for cell in cells)
    finally:
        page.context.close()


def _with_many_ladders(body: dict[str, Any], listed: int = 24, left_out: int = 7) -> dict[str, Any]:
    """The real answer with more ladders than the cap lists: ``listed`` cards, ``left_out`` more
    counted, each extra ladder holding two nodes that stand on it."""
    template = next(node for node in body["nodes"] if not node["is_center"])
    for number in range(listed - len(body["ladders"])):
        ladder_id = f"ladder:{number:016x}"
        members = [f"entry_extra_{number}_{part}" for part in range(2)]
        body["ladders"].append(
            {
                "id": ladder_id,
                "rungs": [
                    {"level": "root", "id": "root_extra", "name": "pool", "path": None},
                    {
                        "level": "experiment",
                        "id": f"entry_extra_exp_{number}",
                        "name": f"run_{number:02d}",
                        "path": f"run_{number:02d}",
                    },
                ],
                "members": members,
                "is_center_ladder": False,
                "line_of_work": "not_computed",
                "line_of_work_reason": "lines of work were not asked for",
            }
        )
        body["nodes"].extend(
            {
                **template,
                "id": member,
                "name": f"extra_{number:02d}_{part}.csv",
                "path": f"run_{number:02d}/extra_{number:02d}_{part}.csv",
                "placement": "related",
                "ladder": ladder_id,
            }
            for part, member in enumerate(members)
        )
    body["ladders_total"] = len(body["ladders"]) + left_out
    body["ladders_left_out"] = left_out
    return body


def test_beyond_24_ladders_the_rest_are_one_row_and_the_page_still_draws(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)

    def answer(route: Any) -> None:
        response = route.fetch()
        route.fulfill(response=response, json=_with_many_ladders(response.json()))

    page.route("**/api/v1/graph/neighborhood*", answer)
    try:
        _open(page, base, explorer, _raw(explorer))
        assert len(_cards(page)) == 24
        head = page.locator(".graph-ladders .eyebrow").inner_text()
        assert head.strip().lower() == "folder levels in these connections \u00b7 24 of 31"
        assert (
            page.locator(".graph-ladders-more").inner_text()
            == "and 7 more chains of levels, not drawn"
        )
        assert page.get_attribute('[data-graph="canvas"]', "data-bands") == "24"
        # More than the palette holds: colors repeat, and every card still says which ladder it is.
        titles = [card["text"].splitlines()[0] for card in _cards(page)]
        assert len(set(titles)) == 24
        caps = page.locator('[data-graph="caps"]').inner_text()
        assert "31 ladders here; up to 24 are drawn, the rest counted." in caps
        assert not page.errors
    finally:
        page.context.close()


def test_several_ladders_stand_side_by_side_and_stay_legible_at_1440(
    browser: Any, served_three: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served_three
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        assert len(_cards(page)) == 3
        page.wait_for_function(
            "() => document.querySelector('[data-graph=\"canvas\"]').dataset.bandBoxes",
            timeout=HANG_GUARD_MS,
        )
        canvas = page.locator('[data-graph="canvas"]')
        # Labels are 13 px, so a zoom of 0.85 or more keeps them about 11 px or larger.
        assert float(canvas.get_attribute("data-zoom") or "0") >= 0.85
        boxes = json.loads(canvas.get_attribute("data-band-boxes") or "[]")
        assert len(boxes) == 3
        size = canvas.bounding_box()
        assert size is not None
        # The bands are side by side on the page, none over another, each whole in the plot.
        for first in range(3):
            box = boxes[first]
            assert box["x1"] >= 0 and box["x2"] <= size["width"], box
            assert box["y1"] >= 0 and box["y2"] <= size["height"], box
            for second in range(first + 1, 3):
                other = boxes[second]
                apart = box["x2"] <= other["x1"] or other["x2"] <= box["x1"]
                apart = apart or box["y2"] <= other["y1"] or other["y2"] <= box["y1"]
                assert apart, (box, other)
        # Side by side: the three bands share one row of the plot.
        assert max(box["y1"] for box in boxes) - min(box["y1"] for box in boxes) < 60
        # The Ladders list starts level with the plot, not below the title above it.
        plot_top = canvas.bounding_box()["y"]
        list_top = page.locator(".graph-ladders").bounding_box()["y"]
        assert abs(plot_top - list_top) <= 2, (plot_top, list_top)
        assert not page.errors
    finally:
        page.context.close()


# ------------------------------------------------------- a file no line touches


def test_a_file_no_line_touches_shows_the_sentence_and_the_center_alone(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        # With every relation on, as the page opens: the file's folder is not drawn beside it.
        _open(page, base, explorer, "notes/brisa_notes.txt")
        lone = page.locator('[data-graph="lone"]')
        page.wait_for_selector('[data-graph="lone"][data-lone="yes"]', timeout=HANG_GUARD_MS)
        said = lone.inner_text()
        assert "No line touches this file in Scan " in said
        assert "A line that is not drawn does not prove there is none." in said
        assert lone.get_by_role("button", name="Show suggested pairs").count() == 1
        rows = page.eval_on_selector_all(
            ".graph-table tr[data-id]", "rows => rows.map(row => row.dataset.id)"
        )
        center = explorer.ids["notes/brisa_notes.txt"]
        assert rows == [center]
        assert list(_band_nodes(page)) == [center]
        assert "drawing of 1 items" in page.get_attribute('[data-graph="canvas"]', "aria-label")
        assert page.locator('[data-graph="relations"] input[value="contains"]').is_checked()
        assert len(_cards(page)) == 1
        assert "1 member: " in _cards(page)[0]["text"]
        # The action turns the proposals on and asks again.
        lone.get_by_role("button", name="Show suggested pairs").click()
        page.wait_for_function("() => location.search.includes('proposals=1')")
        page.wait_for_selector("table.graph-table")
        assert not page.errors
    finally:
        page.context.close()


def test_a_file_with_a_line_shows_no_such_sentence(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        page.wait_for_selector(
            '[data-graph="lone"][data-lone="no"]', state="attached", timeout=HANG_GUARD_MS
        )
        assert page.locator('[data-graph="lone"]').inner_text().strip() == ""
    finally:
        page.context.close()


# ----------------------------------------------------------------- the keyboard


def _inspected(page: Any) -> str:
    return str(page.locator('[data-graph="inspector"] h3').first.inner_text())


def test_the_plot_is_one_stop_tab_walks_the_items_arrows_follow_lines_enter_centers_f_fits(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        plot = page.locator('[data-graph="canvas"]')
        assert plot.get_attribute("tabindex") == "0"
        center_name = _inspected(page)
        plot.focus()
        page.keyboard.press("Tab")
        page.wait_for_function("() => new URLSearchParams(location.search).get('selected')")
        second = page.evaluate("new URLSearchParams(location.search).get('selected')")
        assert second != explorer.ids[_raw(explorer)]
        assert _inspected(page) != center_name
        # Still in the plot: Shift Tab walks back to the center rather than leaving it.
        assert page.evaluate("document.activeElement.dataset.graph") == "canvas"
        page.keyboard.press("Shift+Tab")
        page.wait_for_function(
            "(id) => new URLSearchParams(location.search).get('selected') === id",
            arg=explorer.ids[_raw(explorer)],
        )
        assert _inspected(page) == center_name
        # An arrow follows a line: some arrow leads from the center to a joined item.
        moved = ""
        for arrow in ("ArrowDown", "ArrowRight", "ArrowLeft", "ArrowUp"):
            page.keyboard.press(arrow)
            now = page.evaluate("new URLSearchParams(location.search).get('selected')")
            if now and now != explorer.ids[_raw(explorer)]:
                moved = now
                break
        assert moved
        # F fits the drawing; it changes nothing chosen.
        page.keyboard.press("f")
        assert page.evaluate("new URLSearchParams(location.search).get('selected')") == moved
        # Enter centers the graph on the item, and the plot keeps the keyboard.
        page.keyboard.press("Enter")
        page.wait_for_function(
            "(id) => new URLSearchParams(location.search).get('center') === id", arg=moved
        )
        page.wait_for_selector("table.graph-table")
        page.wait_for_function("() => document.activeElement.dataset.graph === 'canvas'")
        assert not page.errors
    finally:
        page.context.close()


# -------------------------------------------------------------- Open in By year


def test_open_in_years_opens_the_years_view(
    browser: Any, served: tuple[str, ExplorerProject]
) -> None:
    base, explorer = served
    page = _page(browser)
    try:
        _open(page, base, explorer, _raw(explorer))
        # The second card is the other experiment's: Years opens that experiment, not the project.
        other = explorer.experiments[1]
        page.locator(".graph-ladder").nth(1).get_by_role("button", name="Open in By year").click()
        # The Years view is Overlap's By year mode, at the address #overlap.
        page.wait_for_function("() => location.hash === '#overlap'", timeout=HANG_GUARD_MS)
        page.wait_for_function(
            "name => document.body.innerText.includes(name)",
            arg=other,
            timeout=180000,
        )
        # At the experiment's own scale: its story, and its folders one chip away.
        page.wait_for_selector(".years .ystory .st-story", timeout=180000)
        page.click(".years .ychip >> text=folders")
        page.wait_for_function(
            "() => ['raw', 'processed', 'analysis'].every("
            "word => document.querySelector('#list').innerText.includes(word))",
            timeout=180000,
        )
        assert not page.errors
    finally:
        page.context.close()


# -------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_neighborhood_has_no_serious_accessibility_violation(
    browser: Any, served: tuple[str, ExplorerProject], scheme: str
) -> None:
    script = _axe_script()
    if script is None:
        pytest.skip("axe-core is not installed: set INGENCE_AXE_JS")
    base, explorer = served
    page = _page(browser, scheme)
    try:
        _open(page, base, explorer, _raw(explorer))
        page.add_script_tag(content=script.read_text(encoding="utf-8"))
        found = page.evaluate(
            "options => window.axe.run(document.querySelector('.graph-page'), options)",
            {"runOnly": {"type": "tag", "values": STANDARDS}},
        )
        bad = [
            f"{item['id']}: {item['help']} {[n['html'][:90] for n in item['nodes'][:3]]}"
            for item in found["violations"]
            if item["impact"] in ("critical", "serious")
        ]
        assert not bad, bad
    finally:
        page.context.close()
