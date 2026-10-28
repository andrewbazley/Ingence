"""Groupings explained and compared, driven in a real browser.

Beside ``test_browser_map.py``, skipped the same way where the ``browser`` extra or a browser is
missing (a failure where ``INGENCE_BROWSER_REQUIRED=1``). On the invented theme pool
(``fixtures.theme_pool``: six planted lines of work, sets of words that many experiments share),
served by ``ingence see`` on a copy, with who decides kept in a folder of the test's own. Nothing
here answers a question or writes to the Record: ticking, comparing and outlining read only.

1. How to read this on the stacks, the Map and the comparison: closed until asked, opens in place
   (no dialog, no new page), a picture with numbered marks and a list that says each, in the
   catalog's words.
2. Show on the map from a stack, and Show the stack back.
3. Compare two sets from Groupings: ticks, the tray, the panel's words, experiments and files
   against what the engine served, and Show on the map with lettered outlines and the overlap
   shaded.
4. Compare lines of work from the Map (two to four), and close it.
5. A line's Show on the map from Years.
6. Topic outlines (owner ``topics``) survive Clear outlines, a comparison and a stack's Show on
   the map.
7. axe passes on Groupings: drawn, with the panel open, and with a comparison, in light and
   dark.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
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
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
#: The first full read of a cold server draws the map's layout, which takes minutes on a loaded
#: machine. It is read once, patiently, so every test stands alone.
SLOW = 300_000


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
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    for base in serve_theme_project(tmp_path_factory, "groupings"):
        for route in ("api/v1/themes/map", "api/v1/groups/lattice"):
            with urllib.request.urlopen(base + route, timeout=SLOW) as answer:
                answer.read()
        yield base


def _new_page(browser: Any, scheme: str = "light") -> Any:
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme=scheme)
    page = context.new_page()
    page.on("pageerror", lambda error: pytest.fail(f"the page raised: {error}"))
    return page


def _get(page: Any, base: str, route: str, groups: list[str] | None = None) -> Any:
    query = urllib.parse.urlencode([("group", group) for group in groups or []])
    answer = page.request.get(f"{base}{route}" + (f"?{query}" if query else ""), timeout=SLOW)
    assert answer.ok, (route, answer.status)
    return answer.json()


def _open_groupings(page: Any, base: str) -> None:
    page.goto(f"{base}#classes")
    page.wait_for_selector(".group-lattice .cascade", timeout=HANG_GUARD_MS)
    settled(page)


def _open_map(page: Any, base: str) -> None:
    page.goto(f"{base}#map")
    page.wait_for_selector(".map-dot", timeout=HANG_GUARD_MS)
    page.wait_for_function(
        "() => !document.querySelector('.state-loading') && document.querySelector('.map-svg')",
        timeout=HANG_GUARD_MS,
    )
    settled(page)


def _plural(count: int, one: str) -> str:
    return f"{count:,} {one}" + ("" if count == 1 else "s")


def _of(count: int, whole: int) -> str:
    return f"{count:,} of {whole:,}"


def _spec(terms: list[str]) -> str:
    return "words:" + "|".join(terms)


# ------------------------------------------------------------------------------- the lattice's sets


def _overlapping_sets(page: Any, base: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two sets of the lattice that share some experiments and not all of either's, both listed on
    the page, and the shared experiments among them have a place on the map."""
    lattice = _get(page, base, "api/v1/groups/lattice")
    points = {p["folder"]: p for p in _get(page, base, "api/v1/themes/map")["experiments"]}
    sets = lattice["sets"]
    for first in sets:
        for second in sets:
            shared = set(first["experiments"]) & set(second["experiments"])
            if (
                first["terms"] < second["terms"]
                and shared
                and shared != set(first["experiments"])
                and shared != set(second["experiments"])
                and any((points.get(folder) or {}).get("x") is not None for folder in shared)
                and all(_listed(page, found["terms"]) for found in (first, second))
            ):
                return first, second
    pytest.fail("the invented pool should hold two listed sets of words that overlap in part")


def _listed(page: Any, terms: list[str]) -> int:
    wanted = " ".join(terms)
    return page.locator(
        f'.cascade-block[data-terms="{wanted}"], .cascade-unasked li[data-terms="{wanted}"]'
    ).count()


def _box(page: Any, terms: list[str]) -> Any:
    """A set's Compare box wherever the page lists it, opening the folds that hide it."""
    wanted = " ".join(terms)
    inside = f'[data-terms="{wanted}"]'
    for fold in page.locator("details.cascade-fold, details.cascade-unasked").all():
        if fold.locator(inside).count() and not fold.evaluate("node => node.open"):
            fold.locator("summary").first.click()
    where = f'.cascade-block[data-terms="{wanted}"], .cascade-unasked li[data-terms="{wanted}"]'
    return page.locator(where).first.locator(".compare-box input").first


def _tick(page: Any, terms: list[str]) -> None:
    _box(page, terms).check()


def _compare_two(page: Any, base: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Tick two overlapping sets, press Compare, and return the two sets and what the engine says
    of them."""
    first, second = _overlapping_sets(page, base)
    _tick(page, first["terms"])
    _tick(page, second["terms"])
    page.click(".compare-tray button.answer")
    page.wait_for_selector(".compare-slot .compare-key", timeout=HANG_GUARD_MS)
    served_answer = _get(
        page, base, "api/v1/groups/compare", [_spec(first["terms"]), _spec(second["terms"])]
    )
    return first, second, served_answer


def _marks_on(page: Any, panel: str) -> list[str]:
    """The numbers of the marks drawn over a panel's picture."""
    return page.eval_on_selector_all(
        f"{panel} .howto-mark", "nodes => nodes.map(n => n.textContent.trim())"
    )


# -------------------------------------------------------------------------- 1. how to read this


def _check_how_to_read(page: Any, base: str, toggle: str, panel: str, name: str, file: str) -> None:
    catalog = _get(page, base, "help/catalog.json")["pictures"][name]
    # Closed until asked: the link says so, and the panel is hidden.
    assert page.get_attribute(toggle, "aria-expanded") == "false"
    assert page.locator(panel).is_hidden()
    assert page.locator(".howto:visible").count() == 0
    page.click(toggle)
    page.wait_for_function(
        "(panel) => document.querySelectorAll(panel + ' .howto-list li').length > 0",
        arg=panel,
        timeout=HANG_GUARD_MS,
    )
    assert page.get_attribute(toggle, "aria-expanded") == "true"
    assert page.locator(panel).is_visible()
    # In place: the same page, no dialog.
    assert page.locator("dialog[open], [role='dialog'], [aria-modal='true']").count() == 0
    page.wait_for_function(
        "(panel) => { const i = document.querySelector(panel + ' .howto-figure img');"
        " return i && i.complete && i.naturalWidth > 0; }",
        arg=panel,
        timeout=HANG_GUARD_MS,
    )
    image = page.locator(f"{panel} .howto-figure img")
    assert image.get_attribute("src") == f"/help/pictures/{file}"
    assert (image.get_attribute("alt") or "").strip() == catalog["alt"]
    # The list says each mark in the catalog's words, numbered in order.
    items = page.eval_on_selector_all(
        f"{panel} .howto-list li", "nodes => nodes.map(n => n.textContent.trim())"
    )
    assert len(items) == len(catalog["marks"])
    for item, mark in zip(items, catalog["marks"], strict=True):
        assert item == f"{mark['title']}. {mark['text']}"
    # The marks are over the picture, numbered 1 to n, each at a place inside it.
    assert _marks_on(page, panel) == [str(number) for number in range(1, len(items) + 1)]
    places = page.eval_on_selector_all(
        f"{panel} .howto-mark",
        "nodes => nodes.map(n => [parseFloat(n.style.left), parseFloat(n.style.top)])",
    )
    assert all(0 <= x <= 100 and 0 <= y <= 100 for x, y in places), places
    # Closed again by the same link.
    page.click(toggle)
    assert page.get_attribute(toggle, "aria-expanded") == "false"
    assert page.locator(panel).is_hidden()


def test_how_to_read_the_stacks_is_closed_opens_in_place_and_says_each_mark(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    assert page.locator(".howto-toggle").first.inner_text() == "How to read this"
    _check_how_to_read(
        page,
        served,
        ".group-lattice .groups-head .howto-toggle",
        ".group-lattice > .howto",
        "groupings_stacks",
        "groupings-stacks.png",
    )
    assert page.evaluate("location.hash") == "#classes"
    page.close()


def test_how_to_read_the_map_is_closed_opens_in_place_and_says_each_mark(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    _check_how_to_read(page, served, ".map-head .howto-toggle", "#howto-map", "map", "map.png")
    assert page.evaluate("location.hash") == "#map"
    page.close()


def test_how_to_read_the_comparison_is_closed_opens_in_place_and_says_each_mark(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    _compare_two(page, served)
    assert page.locator(".compare-slot .howto:visible").count() == 0
    _check_how_to_read(
        page,
        served,
        ".compare-slot .howto-toggle",
        ".compare-slot .howto",
        "compare",
        "compare.png",
    )
    page.close()


def test_nothing_of_the_three_panels_is_open_when_a_view_is_drawn(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    assert page.locator(".howto:visible").count() == 0
    assert page.locator(".compare-slot").is_hidden()
    assert page.locator(".compare-tray").is_hidden()
    _open_map(page, served)
    assert page.locator(".howto:visible").count() == 0
    assert page.locator(".map-outline").count() == 0
    assert page.get_attribute(".map-head .howto-toggle", "aria-expanded") == "false"
    page.close()


# ----------------------------------------------------------------- 2. show on the map, and back


def test_a_stack_shows_on_the_map_outlined_and_the_map_shows_the_stack_back(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    core = page.locator(".group-lattice .cascade").first
    terms = core.get_attribute("data-terms")
    assert terms
    held = {
        " ".join(found["terms"]): found
        for found in _get(page, served, "api/v1/groups/lattice")["sets"]
    }[terms]
    core.locator(".cascade-tools button", has_text="Show on the map").click()
    page.wait_for_selector(".map-outline", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.evaluate("location.hash") == "#map"
    # One outline, the stack's, named by its words; no letter for a single group.
    assert page.locator(".map-outline").count() == 1
    assert page.get_attribute(".map-outline", "data-key") == _spec(held["terms"])
    assert page.locator(".map-outline-tag").count() == 0
    # The legend and the card say how many of its experiments have a place, out of all of them.
    total = len(held["experiments"])
    legend = page.inner_text(".map-outline-legend")
    assert ", ".join(held["terms"]) in legend
    placed = re.search(r"(\d[\d,]*) of (\d[\d,]*) placed", legend)
    assert placed and int(placed.group(2).replace(",", "")) == total, legend
    assert 0 < int(placed.group(1).replace(",", "")) <= total
    card = page.inner_text(".map-groups")
    assert f"of {_plural(total, 'experiment')} placed on the map." in card
    # The map is fitted to the group: its outline fills much of the plot, where the whole map would
    # draw it small.
    share = page.evaluate(
        "() => { const box = document.querySelector('.map-outline').getBBox();"
        " const plot = document.querySelector('.map-svg');"
        " return Math.max(box.width / plot.clientWidth, box.height / plot.clientHeight); }"
    )
    assert share > 0.4, share
    # Back: the Groupings page opens with that stack found and marked.
    page.click(".map-groups >> text=Show the word group")
    page.wait_for_selector(".group-lattice .cascade.revealed", timeout=HANG_GUARD_MS)
    assert page.evaluate("location.hash") == "#classes"
    assert page.get_attribute(".group-lattice .cascade.revealed", "data-terms") == terms
    page.close()


def test_a_block_inside_a_fold_is_found_and_lit_by_show_the_stack(
    browser: Any, served: str
) -> None:
    """A set that is only a block of a stack, in a closed fold, is reached from the Map too."""
    page = _new_page(browser)
    _open_groupings(page, served)
    sets = _get(page, served, "api/v1/groups/lattice")["sets"]
    block = next(
        (found for found in sets if found["parents"] and _listed(page, found["terms"])), None
    )
    if block is None:
        pytest.skip("the invented pool holds no block that adds to another set")
    wanted = " ".join(block["terms"])
    # What the Map's "Show the stack" sets, then the Groupings page is drawn again.
    page.evaluate(
        "terms => { window.ingenceGroups.pendingStack = terms; return navigate('classes'); }",
        wanted,
    )
    page.wait_for_selector(".cascade-block.revealed", timeout=HANG_GUARD_MS)
    assert page.get_attribute(".cascade-block.revealed", "data-terms") == wanted
    assert page.locator(".cascade-block.revealed").is_visible()
    page.close()


# ------------------------------------------------------------------ 3. compare two on Groupings


def test_ticking_two_sets_opens_the_tray_and_the_panel_says_what_the_engine_served(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    # Nothing ticked: no tray, no panel.
    assert page.locator(".compare-tray").is_hidden()
    first, second = _overlapping_sets(page, served)
    _tick(page, first["terms"])
    assert page.locator(".compare-tray").is_visible()
    # One ticked: the button says how many more.
    assert page.inner_text(".compare-tray button.answer") == "Tick 1 more to compare"
    assert page.locator(".compare-tray button.answer").is_disabled()
    _tick(page, second["terms"])
    assert page.inner_text(".compare-tray button.answer") == "Compare 2 groups"
    chips = page.eval_on_selector_all(
        ".compare-tray .compare-chip", "nodes => nodes.map(n => n.textContent.trim())"
    )
    assert [chip[0] for chip in chips] == ["A", "B"]
    assert ", ".join(first["terms"]) in chips[0] and ", ".join(second["terms"]) in chips[1]
    page.click(".compare-tray button.answer")
    page.wait_for_selector(".compare-slot .compare-key", timeout=HANG_GUARD_MS)
    answer = _get(
        page, served, "api/v1/groups/compare", [_spec(first["terms"]), _spec(second["terms"])]
    )
    panel = page.inner_text(".compare-slot .compare-body")
    # The groups, with their letters, their words, and their sizes.
    keys = page.eval_on_selector_all(
        ".compare-slot .compare-key", "nodes => nodes.map(n => n.innerText)"
    )
    for key, group in zip(keys, answer["groups"], strict=True):
        assert key.startswith(group["tag"])
        assert group["label"] in key
        assert f"{_plural(len(group['experiments']), 'experiment')}, " in key
        assert _plural(group["files"], "file") in key
    # Words, experiments and files, each count with what it is out of.
    assert f"In all 2: {_of(len(answer['words_in_all']), answer['words_in_either'])} words" in panel
    assert (
        f"In all 2: {_of(len(answer['experiments_in_all']), answer['experiments_in_either'])} "
        "experiments in any of them." in panel
    )
    files = _of(answer["files_in_all"], answer["files_in_either"])
    assert f"In the experiments in all 2: {files} files" in panel
    for group in answer["groups"]:
        assert (
            f"{group['tag']}: {_plural(group['files'], 'file')} in "
            f"{_plural(len(group['experiments']), 'experiment')}; "
            f"{_of(group['files_only'], group['files'])} are in experiments only it holds."
        ) in panel
        assert (
            f"{_of(len(group['experiments_only']), len(group['experiments']))} only here" in panel
        )
    in_all = page.eval_on_selector_all(
        ".compare-slot .compare-section >> nth=0 >> p >> nth=0 >> .compare-word",
        "nodes => nodes.map(n => n.textContent)",
    )
    assert in_all == answer["words_in_all"]
    # One bar for each group, and a list of the experiments in all, closed until opened.
    assert page.locator(".compare-slot .compare-bar").count() == 2
    lists = page.locator(".compare-slot .compare-list")
    assert lists.count() == 3
    assert not lists.first.evaluate("node => node.open")
    lists.first.locator("summary").click()
    items = lists.first.locator("li code").all_inner_texts()
    assert items == [e["label"] for e in answer["experiments_in_all"]]
    page.close()


def test_the_comparison_shows_on_the_map_with_lettered_outlines_and_the_overlap_shaded(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    _, _, answer = _compare_two(page, served)
    page.click(".compare-actions button:has-text('Show on the map')")
    page.wait_for_selector(".map-outline", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.evaluate("location.hash") == "#map"
    assert page.locator(".map-outline").count() == 2
    assert page.eval_on_selector_all(
        ".map-outline-tag text", "nodes => nodes.map(n => n.textContent)"
    ) == ["A", "B"]
    keys = page.eval_on_selector_all(".map-outline", "nodes => nodes.map(n => n.dataset.key)")
    assert keys == [group["key"] for group in answer["groups"]]
    # The overlap of the two shapes is drawn and shaded, and the legend names it.
    assert page.locator(".map-overlap").count() >= 1
    shade = page.eval_on_selector(
        ".map-overlap",
        "n => { const s = getComputedStyle(n); return [s.fill, n.getAttribute('d')]; }",
    )
    assert shade[1], "the overlap has a shape"
    assert shade[0] not in ("none", "rgba(0, 0, 0, 0)"), shade
    legend = page.inner_text(".map-outline-legend")
    assert "where outlines overlap" in legend
    for group in answer["groups"]:
        assert group["label"] in legend
        assert f"of {len(group['experiments'])} placed" in legend
    # The card holds the comparison (the same counts), with the way to close it.
    card = page.inner_text(".map-card")
    assert (
        f"In all 2: {_of(len(answer['experiments_in_all']), answer['experiments_in_either'])}"
        in card
    )
    assert page.locator(".map-card .compare-key").count() == 2
    page.click(".map-card >> text=Close the comparison")
    page.wait_for_function(
        "() => document.querySelectorAll('.map-outline').length === 0", timeout=HANG_GUARD_MS
    )
    assert "Click a point to read its card." in page.inner_text(".map-card")
    assert page.locator(".map-outline-legend").count() == 0
    page.close()


def test_a_fifth_tick_is_refused_in_words_and_clear_empties_the_tray(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_groupings(page, served)
    sets = _get(page, served, "api/v1/groups/lattice")["sets"]
    listed = [found for found in sets if _listed(page, found["terms"])][:5]
    assert len(listed) == 5, "the invented pool lists five sets"
    for found in listed[:4]:
        _tick(page, found["terms"])
    assert page.locator(".compare-tray .compare-chip").count() == 4
    fifth = _box(page, listed[4]["terms"])
    fifth.click()
    assert (
        page.inner_text(".compare-tray .compare-said")
        == "Compare 4 groups at most. Remove one first."
    )
    assert not fifth.is_checked()
    assert page.locator(".compare-tray .compare-chip").count() == 4
    page.click(".compare-tray button:has-text('Clear')")
    assert page.locator(".compare-tray").is_hidden()
    assert page.locator(".compare-box input:checked").count() == 0
    page.close()


# --------------------------------------------------------------- 4. compare lines on the Map


def test_lines_of_work_are_compared_from_the_map_and_closed(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    page.click(".map-compare > summary")
    boxes = page.locator(".map-compare-line input")
    themes = _get(page, served, "api/v1/themes/map?themes_only=true")["themes"]
    assert boxes.count() == len(themes)
    go = page.locator(".map-compare-go button")
    assert go.is_disabled() and go.inner_text() == "Tick 2 to 4 themes"
    chosen = [boxes.nth(index).get_attribute("value") for index in range(3)]
    for index in range(3):
        boxes.nth(index).check()
    assert go.inner_text() == "Compare 3 themes" and go.is_enabled()
    go.click()
    page.wait_for_selector(".map-card .compare-key", timeout=HANG_GUARD_MS)
    page.wait_for_function(
        "() => document.querySelectorAll('.map-outline').length === 3", timeout=HANG_GUARD_MS
    )
    answer = _get(page, served, "api/v1/groups/compare", [f"line:{theme}" for theme in chosen])
    assert page.locator(".map-card .compare-key").count() == 3
    assert page.eval_on_selector_all(
        ".map-outline-tag text", "nodes => nodes.map(n => n.textContent)"
    ) == ["A", "B", "C"]
    assert page.eval_on_selector_all(".map-outline", "nodes => nodes.map(n => n.dataset.key)") == [
        f"line:{theme}" for theme in chosen
    ]
    card = page.inner_text(".map-card")
    assert (
        f"In all 3: {_of(len(answer['experiments_in_all']), answer['experiments_in_either'])}"
        in card
    )
    # Three groups: each two are listed with what they share.
    pairs = page.locator(".map-card .compare-section", has_text="Each two").locator("li")
    assert pairs.count() == 3 == len(answer["pairs"])
    assert pairs.first.inner_text().startswith("A and B:")
    # A fourth is allowed, a fifth is refused in words.
    boxes.nth(3).check()
    boxes.nth(4).click()
    assert not boxes.nth(4).is_checked()
    assert page.inner_text(".map-compare-go .note") == "4 themes at most. Untick one first."
    page.click(".map-card >> text=Close the comparison")
    page.wait_for_function(
        "() => document.querySelectorAll('.map-outline').length === 0", timeout=HANG_GUARD_MS
    )
    page.close()


# -------------------------------------------------------------- 5. a line's show on the map


def test_a_lines_show_on_the_map_from_years_outlines_its_experiments(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#years")
    page.wait_for_function(
        "() => window.ingenceYears && typeof window.ingenceYears.select === 'function'",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    themes = _get(page, served, "api/v1/themes")
    line = max(themes["themes"], key=lambda t: (t["experiments"], -t["theme"]))
    page.evaluate(
        "id => window.ingenceYears.load()"
        ".then(() => window.ingenceYears.select({kind: 'theme', id}))",
        line["theme"],
    )
    button = page.locator("button", has_text="Show on the map")
    button.first.wait_for(timeout=HANG_GUARD_MS)
    assert button.count() == 1
    button.click()
    page.wait_for_selector(".map-outline", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.evaluate("location.hash") == "#map"
    assert page.locator(".map-outline").count() == 1
    assert page.get_attribute(".map-outline", "data-key") == f"line:{line['theme']}"
    legend = page.inner_text(".map-outline-legend")
    placed = re.search(r"(\d[\d,]*) of (\d[\d,]*) placed", legend)
    assert placed and int(placed.group(2).replace(",", "")) == line["experiments"], legend
    assert 0 < int(placed.group(1).replace(",", "")) <= line["experiments"]
    page.close()


# ------------------------------------------------------------------------ 6. topics' outlines

#: A topic is the person's: it is written to the Record as the Topics section writes it, and its
#: outline is drawn by the Map's own legend switch under the owner ``topics``. The topic lands in
#: the module's own copy of the invented project, which no other test reads a topic from.
_MAKE_TOPIC = """async ([ids, name]) => {
  await write('/api/review/answer', {actor: 'Dana Whitfield', overlay: [
    {kind: 'topic', name, members: ids.map((id) => ({kind: 'experiment', id}))}]});
  topicKit.changed();
}"""
TOPIC_NAME = "An example topic"


def _topic_outline(page: Any, base: str) -> str:
    """Make a topic of one line's experiments and switch its outline on, as a person does; returns
    the key the outline is held under."""
    points = _get(page, base, "api/v1/themes/map")["experiments"]
    ids = [p["id"] for p in points if p["theme"] == 3 and p["x"] is not None]
    assert ids
    page.evaluate(_MAKE_TOPIC, [ids, TOPIC_NAME])
    switch = page.locator(f'input[aria-label="Outline the collection {TOPIC_NAME}"]')
    switch.wait_for(timeout=HANG_GUARD_MS)
    switch.check()
    page.wait_for_selector('.map-outline[data-owner="topics"]', timeout=HANG_GUARD_MS)
    (key,) = page.evaluate("() => window.ingenceMap.keys('topics')")
    assert key.startswith("topic:")
    return str(key)


def _owners(page: Any) -> dict[str, list[str]]:
    return page.evaluate(
        "() => ({groups: window.ingenceMap.keys('groups'),"
        " topics: window.ingenceMap.keys('topics')})"
    )


def test_a_topics_outline_survives_clear_outlines_a_comparison_and_a_stack_shown(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    key = _topic_outline(page, served)
    # The groups' legend line and card never list a topic's outline.
    assert page.locator(".map-outline-legend").count() == 0
    assert page.locator(".map-groups").count() == 0
    # A comparison of two lines draws and clears its own outlines and leaves the topic.
    page.click(".map-compare > summary")
    boxes = page.locator(".map-compare-line input")
    boxes.nth(0).check()
    boxes.nth(1).check()
    page.click(".map-compare-go button")
    page.wait_for_function(
        "() => document.querySelectorAll('.map-outline[data-owner=\"groups\"]').length === 2",
        timeout=HANG_GUARD_MS,
    )
    assert _owners(page)["topics"] == [key]
    assert page.locator('.map-outline[data-owner="topics"]').count() == 1
    page.click(".map-card >> text=Close the comparison")
    page.wait_for_function(
        "() => document.querySelectorAll('.map-outline[data-owner=\"groups\"]').length === 0",
        timeout=HANG_GUARD_MS,
    )
    assert _owners(page) == {"groups": [], "topics": [key]}
    # A stack's Show on the map replaces the groups' own outlines and leaves the topic.
    page.evaluate("navigate('classes')")
    page.wait_for_selector(".group-lattice .cascade", timeout=HANG_GUARD_MS)
    page.locator(
        ".group-lattice .cascade .cascade-tools button", has_text="Show on the map"
    ).first.click()
    page.wait_for_selector('.map-outline[data-owner="groups"]', timeout=HANG_GUARD_MS)
    settled(page)
    owners = _owners(page)
    assert len(owners["groups"]) == 1 and owners["topics"] == [key]
    assert page.locator('.map-outline[data-owner="topics"]').count() == 1
    # The groups' legend line lists only the group; Clear outlines clears only the group.
    assert TOPIC_NAME not in page.inner_text(".map-outline-legend")
    page.click(".map-outline-legend >> text=Clear outlines")
    page.wait_for_function(
        "() => document.querySelectorAll('.map-outline[data-owner=\"groups\"]').length === 0",
        timeout=HANG_GUARD_MS,
    )
    assert _owners(page) == {"groups": [], "topics": [key]}
    assert page.locator('.map-outline[data-owner="topics"]').count() == 1
    # The Outlines button hides the groups' outlines only: with one drawn again, hide it.
    page.evaluate("navigate('classes')")
    page.wait_for_selector(".group-lattice .cascade", timeout=HANG_GUARD_MS)
    page.locator(
        ".group-lattice .cascade .cascade-tools button", has_text="Show on the map"
    ).first.click()
    page.wait_for_selector('.map-outline[data-owner="groups"]', timeout=HANG_GUARD_MS)
    page.click(".map-outline-legend .map-tool")
    assert page.is_hidden('.map-outline[data-owner="groups"]')
    assert page.is_visible('.map-outline[data-owner="topics"]')
    page.close()


# --------------------------------------------------------------------------------------------- axe


@pytest.mark.skipif(_axe_script() is None and not REQUIRED, reason="axe-core is not installed")
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_no_serious_violation_on_groupings(
    browser: Any, served: str, scheme: str
) -> None:
    script = _axe_script()
    if script is None:
        pytest.fail("axe-core is not installed (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
    page = _new_page(browser, scheme)
    _open_groupings(page, served)
    page.add_script_tag(content=script.read_text(encoding="utf-8"))

    def serious(where: str) -> list[str]:
        found = page.evaluate(
            "options => window.axe.run(document, options)",
            {"runOnly": {"type": "tag", "values": STANDARDS}},
        )
        return [
            f"{where}: {v['impact']} {v['id']}: {v['help']} "
            f"{json.dumps([n['html'][:100] for n in v['nodes'][:3]])}"
            for v in found["violations"]
            if v["impact"] in ("critical", "serious")
        ]

    problems = serious("drawn")
    page.click(".group-lattice .groups-head .howto-toggle")
    page.wait_for_selector(".group-lattice > .howto .howto-list li", timeout=HANG_GUARD_MS)
    problems += serious("with the explanation open")
    page.click(".group-lattice .groups-head .howto-toggle")
    _compare_two(page, served)
    problems += serious("with a comparison")
    page.click(".compare-slot .howto-toggle")
    page.wait_for_selector(".compare-slot .howto .howto-list li", timeout=HANG_GUARD_MS)
    problems += serious("with the comparison explained")
    assert not problems, "\n".join(problems)
    page.close()
