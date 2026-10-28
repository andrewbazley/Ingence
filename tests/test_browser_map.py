"""The Map of groups driven in a real browser.

Beside ``test_browser_years.py``, skipped the same way where the ``browser`` extra or a browser
is missing (a failure where ``INGENCE_BROWSER_REQUIRED=1``). On the invented theme pool
(``fixtures.theme_pool``: six planted lines of work, outliers that share no word with anyone),
served by ``ingence see`` on a copy, with who decides kept in a folder of the test's own.

1. Sections: the Graph page's sections are Explorer, Neighborhood and Map of groups. ``#words``
   opens the Words section of Questions (the map is ``#map``).
2. The plot: one dot for each placed experiment, an experiment with no place counted under the
   plot and listed on asking, never drawn. Three color-by chips whose legends never show two
   measures at once. Each theme is named at the middle of its experiments. A click on a name keeps
   its experiments. Hover names the dot and its line. A click opens the card with the theme's
   stability axis. Dragging zooms and a double click resets. The engine's sentence stands in the
   plot's corner. Nothing can move, merge or split a point. The keyboard walks the plot.
3. Loading: while the first read draws the layout, the plot's box says so, names the themes first,
   and shows no percentage. A second visit is immediate.
4. A project with no themes says so in a drawn state.
5. axe passes on the map, drawn and loading, in light and in dark.
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
    REQUIRED,
    launch_browser,
    need,
    serve_theme_project,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
SAID_NO_MOVE = "Nothing here can move, merge or split a point."


def _shots(page: Any, name: str) -> None:
    folder = os.environ.get("INGENCE_BROWSER_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


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
    yield from serve_theme_project(tmp_path_factory, "map")


@pytest.fixture(scope="module")
def cold(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Dashboard whose map nobody has read: the layout is drawn by the first full read."""
    yield from serve_theme_project(tmp_path_factory, "map38-cold")


@pytest.fixture(scope="module")
def no_themes(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Experiments that all read the same words: no theme (a group of identical readings is not a
    theme on its own)."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("map-none")
    pool = base / "pool"
    for day in ("20240301", "20240302", "20240303", "20240304", "20240305"):
        for number in (1, 2):
            path = pool / f"{day} kelt vorn" / f"kelt_vorn_{number:02d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project.path, config_home)
    try:
        yield address
    finally:
        stop(process)


def _new_page(browser: Any, scheme: str = "light") -> Any:
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme=scheme)
    page = context.new_page()
    page.on("pageerror", lambda error: pytest.fail(f"the page raised: {error}"))
    return page


#: The first full read of a cold server draws the layout, which takes minutes on a loaded machine.
SLOW = 300_000


def _warm(page: Any, base: str) -> None:
    """Read the whole map once, patiently, so a later read (the page's, or ``route.fetch``'s, which
    gives up after 30 seconds) is answered from the held layout and a test stands alone."""
    answer = page.request.get(f"{base}api/v1/themes/map", timeout=SLOW)
    assert answer.ok, answer.status


def _served(page: Any, base: str, path: str) -> Any:
    answer = page.request.get(f"{base}{path}", timeout=SLOW)
    assert answer.ok, (path, answer.status)
    return answer.json()


def _open_map(page: Any, base: str, address: str = "#map") -> None:
    page.goto(f"{base}{address}")
    page.wait_for_selector(".map-dot", timeout=HANG_GUARD_MS)
    page.wait_for_function(
        "() => !document.querySelector('.state-loading') && document.querySelector('.map-svg')",
        timeout=HANG_GUARD_MS,
    )
    _settled(page)


def _settled(page: Any) -> None:
    """Wait until the plot stops moving: the header's lines (who decides, what waits for Re-run)
    arrive after the page's first draw and push everything below them down, and a pointer aimed
    at a dot before that lands beside it."""
    page.evaluate(
        """() => new Promise((resolve) => {
          let last = ''; let since = performance.now();
          const tick = () => {
            const r = document.querySelector('.map-plot').getBoundingClientRect();
            const now = `${r.x},${r.y},${r.width},${r.height}`;
            if (now !== last) { last = now; since = performance.now(); }
            if (performance.now() - since > 600) resolve(true); else requestAnimationFrame(tick);
          };
          tick();
        })"""
    )


def _dots(page: Any) -> list[dict[str, Any]]:
    return page.eval_on_selector_all(
        ".map-dot",
        "nodes => nodes.map(n => ({id: n.dataset.id, cx: +n.getAttribute('cx'),"
        " cy: +n.getAttribute('cy'), r: +n.getAttribute('r'), faded: n.classList.contains('faded'),"
        " fill: n.style.fill, unknown: n.classList.contains('unknown')}))",
    )


def _alone_matching(page: Any, served_points: dict[str, Any], *, in_theme: bool) -> str:
    alone = page.evaluate(
        """() => {
          const dots = [...document.querySelectorAll('.map-dot')].map(n => ({id: n.dataset.id,
            x: +n.getAttribute('cx'), y: +n.getAttribute('cy')}));
          // Alone, and not under a theme's name: a pointer there reaches the dot itself.
          return dots.filter(d => dots.every(o => o === d || Math.hypot(o.x - d.x, o.y - d.y) > 14))
            .filter((d) => {
              const node = document.querySelector(`.map-dot[data-id="${d.id}"]`);
              const box = node.getBoundingClientRect();
              const at = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
              return at === node;
            }).map(d => d.id);
        }"""
    )
    for identifier in alone:
        point = served_points[identifier]
        if (point["theme"] is not None) == in_theme:
            return str(identifier)
    said = "no isolated dot of that kind on this layout"
    if REQUIRED:
        pytest.fail(f"{said} (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
    pytest.skip(said)


def _center(page: Any, selector: str) -> tuple[float, float]:
    box = page.locator(selector).bounding_box()
    assert box is not None, selector
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


# ----------------------------------------------------------------------------------------- sections


def test_the_graph_page_offers_the_map_and_never_the_words(browser: Any, served: str) -> None:
    # The Graph page's sections are Advanced's "Views". Neighborhood is Connections.
    page = _new_page(browser)
    page.goto(f"{served}#graph")
    page.wait_for_selector(
        'nav.sections[data-sections-of="advanced"]:not([hidden])', timeout=HANG_GUARD_MS
    )
    buttons = page.eval_on_selector_all(
        'nav.sections[data-sections-of="advanced"] [aria-labelledby="sections-views"] button',
        "nodes => nodes.map(n => n.textContent.trim())",
    )
    assert buttons == ["Explorer", "Connections", "Map of groups", "Stability"]
    # `#words` opens the Words section of Questions, which is a page of its own. It does not
    # open the map.
    page.goto(f"{served}#words")
    page.wait_for_function("() => state.view === 'words'", timeout=HANG_GUARD_MS)
    assert page.evaluate("location.hash") == "#words"
    assert page.locator(".map-dot").count() == 0
    page.goto(f"{served}#map")
    page.wait_for_selector(".map-dot", timeout=HANG_GUARD_MS)
    assert page.evaluate("location.hash") == "#map"
    assert (
        page.evaluate("document.querySelector('nav.sections button.active').dataset.view") == "map"
    )
    page.close()


# ----------------------------------------------------------------------------------------- the plot


def test_one_dot_for_each_placed_experiment_and_those_with_no_place_are_listed_never_drawn(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    placed = [p for p in whole["experiments"] if p["x"] is not None]
    unplaced = [p for p in whole["experiments"] if p["x"] is None]
    assert placed and unplaced, "the invented pool has experiments with and without a place"
    dots = _dots(page)
    assert {d["id"] for d in dots} == {p["id"] for p in placed}
    assert len(dots) == len(placed) == whole["placed"]
    # Never at the corner: no dot sits at 0,0, and none of the unplaced is drawn.
    assert not any(abs(d["cx"]) < 1 and abs(d["cy"]) < 1 for d in dots)
    assert not {p["id"] for p in unplaced} & {d["id"] for d in dots}
    # The heading and the count under the plot.
    assert page.inner_text(".map-title") == (
        f"{whole['placed']} of {whole['count']} experiments placed by the words their themes read"
    )
    said = page.inner_text(".map-unplaced")
    assert said.startswith(
        f"{len(unplaced)} not placed: none of their words are among the words the themes read"
    )
    assert page.locator(".map-unplaced-list").is_hidden()
    page.click("text=list them")
    assert page.locator(".map-unplaced-list li").count() == len(unplaced)
    listed = page.inner_text(".map-unplaced-list")
    assert all(p["folder"] in listed for p in unplaced)
    # A dot is 7 to 10 pixels across.
    assert all(3.5 <= d["r"] <= 5.0 for d in dots)
    page.close()


def test_a_dot_is_7_to_10_pixels_across_by_its_files_on_a_log_scale(
    browser: Any, served: str
) -> None:
    """The fewest files draw the smallest dot, the most the largest, the others between by the
    logarithm. (The invented pool gives every experiment the same files, so the answer is altered
    on its way to the page.)"""
    import math

    def altered(route: Any, _request: Any = None) -> None:
        answer = route.fetch(timeout=SLOW)
        body = answer.json()
        for index, point in enumerate(body["experiments"]):
            point["files"] = (1, 10, 100, 1000)[index % 4]
        route.fulfill(response=answer, json=body)

    page = _new_page(browser)
    _warm(page, served)
    page.route("**/api/v1/themes/map", altered)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    files_of = {p["id"]: (1, 10, 100, 1000)[i % 4] for i, p in enumerate(whole["experiments"])}
    by_size: dict[int, set[float]] = {}
    for dot in _dots(page):
        by_size.setdefault(files_of[dot["id"]], set()).add(dot["r"])
    assert {files: min(radii) for files, radii in by_size.items()}[1] == 3.5
    assert max(by_size[1000]) == 5.0
    for files, radii in by_size.items():
        assert len(radii) == 1, (files, radii)
        wanted = 3.5 + 1.5 * (math.log1p(files) - math.log1p(1)) / (
            math.log1p(1000) - math.log1p(1)
        )
        assert abs(next(iter(radii)) - wanted) < 0.01, (files, radii)
    page.close()


def test_the_chips_change_the_legend_one_measure_at_a_time(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    chips = page.eval_on_selector_all(".map-chip", "nodes => nodes.map(n => n.textContent.trim())")
    assert chips == ["theme", "raw data analyzed", "year"]
    pressed = "document.querySelector('.map-chip[aria-pressed=true]').dataset.measure"

    def keys() -> dict[str, int]:
        return {
            "lines": page.locator(".map-legend .map-key[data-theme]").count(),
            "levels": page.locator(".map-legend [data-level]").count(),
            "ramp": page.locator(".map-legend .map-ramp").count(),
        }

    assert page.evaluate(pressed) == "line"
    assert keys() == {"lines": len(whole["themes"]), "levels": 0, "ramp": 0}
    fills = {d["fill"] for d in _dots(page) if not d["unknown"]}
    assert fills and all("--map-c" in fill or "--map-other" in fill for fill in fills)

    # The count beside a line's name is not stuck to it.
    count_box = page.locator(".map-key[data-theme] .map-key-count").first
    assert float(count_box.evaluate("n => getComputedStyle(n).marginLeft.replace('px', '')")) >= 4

    page.click('.map-chip[data-measure="raw"]')
    assert page.evaluate(pressed) == "raw"
    assert keys() == {"lines": 0, "levels": 5, "ramp": 0}
    # Hatching means unknown: "an experiment" is never keyed by it where the values are known.
    assert page.locator(".map-legend-line .map-swatch.unknown").count() == 0
    shown = _dots(page)
    # The completeness ramp is never drawn with the palette of lines.
    assert not any("--map-c" in d["fill"] or "--map-other" in d["fill"] for d in shown)
    assert any(
        "--map-all" in d["fill"]
        or "--map-most" in d["fill"]
        or "--map-none" in d["fill"]
        or "--map-some" in d["fill"]
        for d in shown
    ) or all(d["unknown"] for d in shown)

    page.click('.map-chip[data-measure="year"]')
    assert page.evaluate(pressed) == "year"
    assert keys()["lines"] == 0 and keys()["levels"] == 0 and keys()["ramp"] == 1
    assert page.locator(".map-legend-line .map-swatch.unknown").count() == 0
    shown = _dots(page)
    year_of = {p["id"]: p["year"] for p in whole["experiments"]}
    undated = [d for d in shown if year_of[d["id"]] is None]
    dated = [d for d in shown if year_of[d["id"]] is not None]
    assert dated and all("color-mix" in d["fill"] for d in dated)
    assert all(d["unknown"] for d in undated)

    page.click('.map-chip[data-measure="line"]')
    assert keys()["lines"] == len(whole["themes"])
    page.close()


def test_where_the_standing_could_not_be_counted_no_dot_takes_a_level(
    browser: Any, served: str
) -> None:
    """Unknown is not "none": the engine says what was found could not be counted, so the ramp
    colors nothing, and says that instead of its five words."""
    said = (
        "What was found of raw data and analysis could not be counted for this run, so no "
        "standing is shown."
    )

    def altered(route: Any, _request: Any = None) -> None:
        answer = route.fetch(timeout=SLOW)
        body = answer.json()
        body["standing_computed"] = False
        body["standing_said"] = said
        for point in body["experiments"]:
            point["standing"] = None
        route.fulfill(response=answer, json=body)

    page = _new_page(browser)
    _warm(page, served)
    page.route("**/api/v1/themes/map", altered)
    _open_map(page, served)
    page.click('.map-chip[data-measure="raw"]')
    assert _dots(page) and all(d["unknown"] for d in _dots(page))
    assert said in page.inner_text(".map-legend")
    assert page.locator(".map-legend [data-level]").count() == 0
    page.click('.map-chip[data-measure="line"]')
    assert any(not d["unknown"] for d in _dots(page))
    page.close()


def test_a_theme_is_named_at_its_center_and_a_click_on_its_name_keeps_its_experiments(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    centered = [t for t in whole["themes"] if t["x"] is not None]
    assert centered
    names = page.locator(".map-theme")
    assert names.count() == len(centered)
    dots = {d["id"]: d for d in _dots(page)}
    in_theme: dict[int, list[dict[str, Any]]] = {}
    for point in whole["experiments"]:
        if point["theme"] is not None and point["id"] in dots:
            in_theme.setdefault(point["theme"], []).append(dots[point["id"]])
    for theme in centered:
        ring = page.locator(f'.map-theme[data-theme="{theme["theme"]}"] .map-center')
        x = float(ring.get_attribute("cx"))
        y = float(ring.get_attribute("cy"))
        members = in_theme[theme["theme"]]
        assert abs(x - sum(d["cx"] for d in members) / len(members)) < 1.5
        assert abs(y - sum(d["cy"] for d in members) / len(members)) < 1.5
        label = theme["person_label"] or theme["label"]
        assert (
            page.locator(f'.map-theme[data-theme="{theme["theme"]}"] text').text_content() == label
        )
    chosen = max(centered, key=lambda t: t["experiments"])
    page.locator(f'.map-theme[data-theme="{chosen["theme"]}"] .map-ground').click()
    faded = {d["id"] for d in _dots(page) if d["faded"]}
    kept = {p["id"] for p in whole["experiments"] if p["theme"] == chosen["theme"]}
    assert faded == {i for i in dots if i not in kept}
    assert (
        page.locator(f'.map-theme[data-theme="{chosen["theme"]}"]').get_attribute("aria-pressed")
        == "true"
    )
    assert "Showing only" in page.inner_text(".map-kept")
    # The legend's button for the line does what its name does.
    page.locator(f'.map-key[data-theme="{chosen["theme"]}"]').click()
    assert not any(d["faded"] for d in _dots(page))
    page.locator(f'.map-key[data-theme="{chosen["theme"]}"]').click()
    assert any(d["faded"] for d in _dots(page))
    page.click(".map-kept button")
    assert not any(d["faded"] for d in _dots(page))
    page.close()


def test_hover_names_a_dot_and_its_line_and_a_click_opens_its_card(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    points = {p["id"]: p for p in whole["experiments"]}
    themes = {t["theme"]: t for t in whole["themes"]}
    identifier = _alone_matching(page, points, in_theme=True)
    point = points[identifier]
    theme = themes[point["theme"]]
    x, y = _center(page, f'.map-dot[data-id="{identifier}"]')
    page.mouse.move(x, y)
    page.wait_for_selector(".map-tip:not([hidden])")
    tip = page.inner_text(".map-tip")
    assert point["label"] in tip and (theme["person_label"] or theme["label"]) in tip
    page.mouse.click(x, y)
    card = page.locator(".map-card")
    assert card.locator(".map-name").inner_text() == point["label"]
    assert card.locator(".map-folder").inner_text() == point["folder"]
    facts = card.locator(".map-facts").inner_text()
    assert theme["label"] in facts
    assert card.locator(".map-facts .map-swatch").count() == 1
    assert str(point["year"]) in facts if point["year"] is not None else "undated" in facts
    assert f"{point['files']:,}" in facts
    assert point["standing"]["said"] in facts
    assert f"{point['at_this_place']} experiment" in facts
    # The theme's stability, drawn on its axis, with the sentence that says whose it is.
    axis = card.locator(".stability")
    assert axis.count() == 1
    assert axis.locator(".stability-value").inner_text() == f"{theme['stability']:.2f}"
    assert axis.locator(".stability-track").count() == 1
    assert "Per-experiment stability is not served; the theme's is shown." in card.inner_text()
    ring = page.locator(".map-chosen")
    assert (
        abs(float(ring.get_attribute("cx")) - (x - page.locator(".map-svg").bounding_box()["x"]))
        < 2
    )
    # Nothing here can move, merge or split a point.
    assert SAID_NO_MOVE in card.inner_text()
    buttons = page.eval_on_selector_all(
        ".map-page button", "nodes => nodes.map(n => n.textContent.toLowerCase())"
    )
    assert not [b for b in buttons if any(w in b for w in ("move", "merge", "split"))]
    # An experiment in no theme says so and draws no axis.
    lone = _alone_matching(page, points, in_theme=False)
    x, y = _center(page, f'.map-dot[data-id="{lone}"]')
    page.mouse.click(x, y)
    assert "in no theme" in page.inner_text(".map-card .map-facts")
    assert page.locator(".map-card .stability").count() == 0
    _shots(page, "map-card")
    page.close()


def test_the_open_card_follows_the_color_by_chip(browser: Any, served: str) -> None:
    """One measure at a time: the line's swatch is in the card only while coloring by line, and a
    card opened from the unplaced list (it belongs to no dot) stays open when the chip changes."""
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    points = {p["id"]: p for p in whole["experiments"]}
    identifier = _alone_matching(page, points, in_theme=True)
    x, y = _center(page, f'.map-dot[data-id="{identifier}"]')
    page.mouse.click(x, y)
    name = points[identifier]["label"]
    assert page.locator(".map-card .map-name").inner_text() == name
    swatches = page.locator(".map-card .map-swatch")
    assert swatches.count() == 1
    for measure in ("raw", "year"):
        page.click(f'.map-chip[data-measure="{measure}"]')
        assert page.locator(".map-card .map-name").inner_text() == name
        assert swatches.count() == 0, f"the line's swatch stayed in the card under {measure}"
    page.click('.map-chip[data-measure="line"]')
    assert swatches.count() == 1
    # A card opened from the list of experiments with no place.
    unplaced = [p for p in whole["experiments"] if p["x"] is None]
    page.click("text=list them")
    page.locator(".map-unplaced-list li button").first.click()
    shown = page.locator(".map-card .map-name").inner_text()
    assert shown in {p["label"] for p in unplaced}
    page.click('.map-chip[data-measure="raw"]')
    assert page.locator(".map-card .map-name").inner_text() == shown
    assert "not placed" in page.inner_text(".map-card .map-facts")
    page.close()


def test_at_1280_by_900_the_legend_and_the_method_show_and_the_chips_stand_beside_the_heading(
    browser: Any, served: str
) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.on("pageerror", lambda error: pytest.fail(f"the page raised: {error}"))
    _open_map(page, served)
    plot = page.locator(".map-plot").bounding_box()
    assert plot is not None and 480 <= plot["height"] <= 560, plot
    # What the page shows without scrolling: the engine's sentence and the legend's first line.
    for selector in (".map-method", ".map-legend-line"):
        box = page.locator(selector).bounding_box()
        assert box is not None and box["y"] + box["height"] <= 900, selector
    title = page.locator(".map-title").bounding_box()
    chips = page.locator(".map-chips").bounding_box()
    assert title is not None and chips is not None
    assert chips["y"] < title["y"] + title["height"], "the chips wrapped under the heading"
    assert chips["x"] > title["x"] + 200
    _shots(page, "map-1280")
    context.close()


def test_the_card_opens_the_experiment_in_years(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    points = {p["id"]: p for p in whole["experiments"]}
    identifier = _alone_matching(page, points, in_theme=True)
    x, y = _center(page, f'.map-dot[data-id="{identifier}"]')
    page.mouse.click(x, y)
    page.click("text=Open in By year")
    # By year, at #overlap, opens the experiment on its story.
    page.wait_for_function("location.hash === '#overlap'")
    page.wait_for_selector(".years .ystory", timeout=HANG_GUARD_MS)
    assert points[identifier]["label"].split(" ")[0] in page.inner_text("#detail")
    page.close()


def test_dragging_zooms_and_a_double_click_resets(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    before = {d["id"]: d for d in _dots(page)}
    box = page.locator(".map-svg").bounding_box()
    assert box is not None
    left, top = box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.1
    page.mouse.move(left, top)
    page.mouse.down()
    page.mouse.move(left + box["width"] * 0.3, top + box["height"] * 0.3, steps=6)
    page.mouse.up()
    after = {d["id"]: d for d in _dots(page)}
    moved = [i for i in before if abs(after[i]["cx"] - before[i]["cx"]) > 20]
    assert moved, "the drag did not zoom"
    # A zoom moves the dots; it does not make them larger.
    assert all(after[i]["r"] == before[i]["r"] for i in before)
    # The drag did not click a dot: nothing was selected.
    assert page.locator(".map-chosen").get_attribute("visibility") == "hidden"
    page.dblclick(".map-svg", position={"x": box["width"] * 0.5, "y": box["height"] * 0.9})
    again = {d["id"]: d for d in _dots(page)}
    assert all(abs(again[i]["cx"] - before[i]["cx"]) < 0.2 for i in before)
    assert all(abs(again[i]["cy"] - before[i]["cy"]) < 0.2 for i in before)
    page.close()


def test_the_engine_s_sentence_stands_in_the_plot_s_corner(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    assert page.inner_text(".map-method") == whole["method_said"]
    plot = page.locator(".map-plot").bounding_box()
    method = page.locator(".map-method").bounding_box()
    assert plot is not None and method is not None
    assert method["y"] > plot["y"] + plot["height"] / 2
    assert (
        method["x"] >= plot["x"] and method["x"] + method["width"] <= plot["x"] + plot["width"] + 1
    )
    page.close()


def test_the_keyboard_walks_the_plot(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served)
    # The plot is one tab stop: no dot takes focus by itself.
    assert page.evaluate("document.querySelectorAll('.map-dot[tabindex]').length") == 0
    assert page.evaluate("document.querySelector('.map-svg').getAttribute('tabindex')") == "0"
    page.focus(".map-svg")
    page.keyboard.press("ArrowRight")
    first = page.inner_text(".map-live")
    assert first
    cursor = page.locator(".map-cursor")
    assert cursor.get_attribute("visibility") == "visible"
    x0 = float(cursor.get_attribute("cx"))
    page.keyboard.press("ArrowRight")
    x1 = float(cursor.get_attribute("cx"))
    assert x1 > x0 or page.inner_text(".map-live") == first
    page.keyboard.press("ArrowLeft")
    assert float(cursor.get_attribute("cx")) <= x1
    # Enter opens the card of the dot the cursor is on.
    page.keyboard.press("Enter")
    name = page.inner_text(".map-card .map-name")
    assert page.inner_text(".map-live").startswith(name)
    # + and - zoom, 0 resets, L toggles the names.
    before = {d["id"]: d["cx"] for d in _dots(page)}
    page.keyboard.press("+")
    zoomed = {d["id"]: d["cx"] for d in _dots(page)}
    assert any(abs(zoomed[i] - before[i]) > 5 for i in before)
    page.keyboard.press("-")
    page.keyboard.press("0")
    reset = {d["id"]: d["cx"] for d in _dots(page)}
    assert all(abs(reset[i] - before[i]) < 0.2 for i in before)
    assert page.locator(".map-names.hidden").count() == 0
    page.keyboard.press("l")
    assert page.locator(".map-names.hidden").count() == 1
    page.keyboard.press("L")
    assert page.locator(".map-names.hidden").count() == 0
    # Escape puts the card down.
    page.keyboard.press("Escape")
    assert "Click a point" in page.inner_text(".map-card")
    page.close()


# ---------------------------------------------------------------------------- loading


def test_while_the_layout_is_drawn_the_box_says_so_and_names_the_themes_first(
    browser: Any, cold: str
) -> None:
    page = _new_page(browser)
    asked: list[str] = []
    held: list[Any] = []
    page.on("request", lambda request: asked.append(request.url))
    # The full answer waits until the test lets it go; the themes alone are the server's.
    page.route("**/api/v1/themes/map", lambda route: held.append(route))
    page.add_init_script(
        "window.__sawLoading = false; new MutationObserver(() => {"
        " if (document.querySelector('.map-loading-card')) window.__sawLoading = true; })"
        ".observe(document, {childList: true, subtree: true});"
    )
    page.goto(f"{cold}#map")
    page.wait_for_selector(".map-loading-card", timeout=HANG_GUARD_MS)
    only = _served(page, cold, "api/v1/themes/map?themes_only=true")
    assert only["themes_only"] is True and only["layout_ready"] is False
    assert only["experiments"] == [] and all(t["x"] is None for t in only["themes"])
    box = page.inner_text(".map-loading")
    assert f"Placing {only['count']} experiments…" in box
    assert (
        "Ingence builds the map once after each scan and keeps it. "
        "The rows and the Wheel are ready meanwhile."
    ) in box
    # No theme is placed until the layout is, so the box does not say they are.
    assert "placed first" not in box
    assert page.locator(".map-dots-loading span").count() == 3
    # The themes are named before the experiments are placed, and no percentage is shown.
    named = page.eval_on_selector_all(
        ".map-themes-first li", "nodes => nodes.map(n => n.textContent)"
    )
    assert len(named) == len(only["themes"])
    for theme in only["themes"]:
        assert any((theme["person_label"] or theme["label"]) in line for line in named)
    assert "%" not in box and "percent" not in box.lower()
    assert page.locator(".map-dot").count() == 0
    assert page.locator('.state-card[data-state="loading"]').count() == 1
    # The themes were asked for before the whole map.
    first = [u for u in asked if "/api/v1/themes/map" in u]
    assert first[0].endswith("themes_only=true")
    _shots(page, "map-loading")
    # Let the whole answer go: the map draws in the box's place.
    assert held, "the full answer was never asked for"
    held[0].continue_()
    page.wait_for_selector(".map-dot", timeout=120000)
    assert page.locator(".map-loading-card").count() == 0
    assert page.locator(".map-dot").count() == _served(page, cold, "api/v1/themes/map")["placed"]
    page.close()

    # A second visit is immediate: the layout is held, so the box never says it is placing.
    again = _new_page(browser)
    again.add_init_script(
        "window.__sawLoading = false; new MutationObserver(() => {"
        " if (document.querySelector('.map-loading-card')) window.__sawLoading = true; })"
        ".observe(document, {childList: true, subtree: true});"
    )
    again.goto(f"{cold}#map")
    again.wait_for_selector(".map-dot", timeout=HANG_GUARD_MS)
    assert again.evaluate("window.__sawLoading") is False
    held_answer = _served(again, cold, "api/v1/themes/map?themes_only=true")
    assert held_answer["layout_ready"] is True and held_answer["experiments"] == []
    assert any(t["x"] is not None for t in held_answer["themes"])
    again.close()


# ---------------------------------------------------------------------------------------- no themes


def test_a_project_with_no_themes_says_so(browser: Any, no_themes: str) -> None:
    page = _new_page(browser)
    page.goto(f"{no_themes}#map")
    page.wait_for_selector('.state-card[data-state="empty"]', timeout=HANG_GUARD_MS)
    card = page.locator(".state-card")
    asked = _served(page, no_themes, "api/v1/themes/map?themes_only=true")
    assert asked["themes"] == [] and asked["count"] >= 1
    noun = "experiment" if asked["count"] == 1 else "experiments"
    assert card.locator(".state-sentence").inner_text() == (
        f"No themes were found among {asked['count']} {noun}."
    )
    assert page.locator(".map-dot").count() == 0
    assert page.locator(".map-chip").count() == 0
    assert "Map of groups" in (card.locator(".eyebrow").text_content() or "")
    page.close()


# --------------------------------------------------------------------------------------------- axe


@pytest.mark.skipif(_axe_script() is None and not REQUIRED, reason="axe-core is not installed")
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_no_serious_violation_on_the_map(browser: Any, served: str, scheme: str) -> None:
    script = _axe_script()
    if script is None:
        pytest.fail("axe-core is not installed (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
    page = _new_page(browser, scheme)
    _open_map(page, served)
    whole = _served(page, served, "api/v1/themes/map")
    points = {p["id"]: p for p in whole["experiments"]}
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
    identifier = _alone_matching(page, points, in_theme=True)
    x, y = _center(page, f'.map-dot[data-id="{identifier}"]')
    page.mouse.click(x, y)
    problems += serious("with a card")
    for measure in ("raw", "year"):
        page.click(f'.map-chip[data-measure="{measure}"]')
        problems += serious(f"colored by {measure}")
    page.click('.map-chip[data-measure="line"]')
    page.click("text=list them")
    problems += serious("with the unplaced listed")
    assert not problems, "\n".join(problems)
    page.close()


@pytest.mark.skipif(_axe_script() is None and not REQUIRED, reason="axe-core is not installed")
def test_axe_finds_no_serious_violation_while_the_map_loads(
    browser: Any, tmp_path_factory: pytest.TempPathFactory
) -> None:
    script = _axe_script()
    if script is None:
        pytest.fail("axe-core is not installed (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
    from fixtures.theme_pool import build_theme_project

    base = tmp_path_factory.mktemp("map-axe-cold")
    project, _ = build_theme_project(base / "built")
    copy = base / "project"
    shutil.copytree(project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        held: list[Any] = []
        for scheme in ("light", "dark"):
            page = _new_page(browser, scheme)

            def hold(route: Any, _request: Any = None, *, kept: list[Any] = held) -> None:
                kept.append(route)

            def cold(route: Any, _request: Any = None) -> None:
                # The server warms the run's readouts when it opens, so by the second scheme the
                # layout may be drawn and the page would skip the loading state. The themes' answer
                # is sent as a cold server's, with the layout not yet drawn.
                response = route.fetch()
                route.fulfill(response=response, json={**response.json(), "layout_ready": False})

            page.route("**/api/v1/themes/map", hold)
            page.route(lambda url: "/api/v1/themes/map?themes_only=" in url, cold)
            page.goto(f"{address}#map")
            page.wait_for_selector(".map-loading-card", timeout=HANG_GUARD_MS)
            page.add_script_tag(content=script.read_text(encoding="utf-8"))
            found = page.evaluate(
                "options => window.axe.run(document, options)",
                {"runOnly": {"type": "tag", "values": STANDARDS}},
            )
            serious = [
                f"{v['id']}: {v['help']}"
                for v in found["violations"]
                if v["impact"] in ("critical", "serious")
            ]
            assert not serious, (scheme, serious)
            for route in held:
                route.abort()
            held.clear()
            page.close()
    finally:
        stop(process)
