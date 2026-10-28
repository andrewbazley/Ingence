"""Lineage readable at scale in the Graph workspace, in a real browser.

On the explorer fixture (``fixtures/explorer_project.py``, invented names), a copy in which every
experiment's raw files, masks and quantity tables are name families the way the engine's own rule
forms one. In the second experiment two of the four quantity tables are held in conflict by their
family. In the first, every mask was answered Wrong, so no family holds them.

1. Families draw above 40 files (or with no focus): one box for each name family with its words
   and its counts, the lines between them one edge to a kind, its width the lines. "Draw 2n"
   doubles the limit, and the sentence says what was left out.
2. A family opens as a Grid of its files (name, folder, lines in and out). A family answered Wrong
   is its files. A family with files in conflict says so, with a dashed red stroke.
3. The keyboard: the plot is one stop, Tab walks the nodes, Enter opens a family, F fits and D
   cycles Families, Files and Grid.
4. A lone file shows the sentence and the focus alone.

Needs the ``browser`` extra and a browser Playwright can start: skipped, never failed, where either
is missing, and required where ``INGENCE_BROWSER_REQUIRED=1``.
"""

from __future__ import annotations

import json
import os
import re
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

RULE = "name_family_without_stages"
STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
#: What a lineage with no focus is drawn with, and what the limit starts at.
FIRST = 8


def _key(*words: str) -> str:
    return json.dumps([["word", word] for word in sorted(words)], separators=(",", ":"))


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


class _Scene:
    def __init__(self, base: str, explorer: ExplorerProject) -> None:
        self.base = base
        self.explorer = explorer

    def stem(self, number: int) -> str:
        from fixtures.laboratory_pool import STEMS

        return STEMS[number]

    def files(self, number: int, kind: str) -> list[str]:
        folder = self.explorer.experiments[number]
        stem = self.stem(number)
        if kind == "raw":
            return [f"{folder}/raw/{stem}_{n:02d}.nd2" for n in range(1, 5)]
        if kind == "mask":
            return [f"{folder}/processed/{stem}_{n:02d}_mask.tif" for n in range(1, 5)]
        return [f"{folder}/processed/{stem}_{n:02d}_quant.csv" for n in range(1, 5)]


@pytest.fixture(scope="module")
def served(
    explorer_project: ExplorerProject, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[_Scene]:
    from fixtures.explorer_project import AddedClaim, with_published
    from ingence.storage.catalog import open_catalog

    explorer = explorer_project
    with open_catalog(explorer.project) as catalog:
        row = catalog.query(
            "SELECT subject, predicate FROM claims "
            "WHERE predicate LIKE 'groups.same_name_identity%' ORDER BY claim_id LIMIT 1"
        ).fetchone()
    assert row is not None
    paths = {entry: path for path, entry in explorer.ids.items()}
    donor, predicate = paths[str(row[0])], str(row[1])
    scene = _Scene("", explorer)
    claims: list[AddedClaim] = []
    for number in range(len(explorer.experiments)):
        stem = scene.stem(number)
        # The first experiment's masks were answered Wrong: the family holds none of them. The
        # second experiment's last two quantity tables are held in conflict by their family.
        groups: tuple[tuple[list[str], tuple[str, str], list[str]], ...] = (
            (scene.files(number, "raw"), (stem, "acquisition"), []),
            (
                scene.files(number, "mask"),
                (stem, "mask"),
                scene.files(0, "mask") if number == 0 else [],
            ),
            (scene.files(number, "quant"), (stem, "table"), scene.files(1, "quant")[2:]),
        )
        for members, words, conflicting in groups:
            claims.extend(
                AddedClaim(
                    donor,
                    predicate,
                    value=_key(*words),
                    onto=member,
                    group_rule=RULE,
                    conflicting=member in conflicting,
                )
                for member in members
            )
    base = tmp_path_factory.mktemp("families")
    project = with_published(explorer, base / "built", claims=tuple(claims))
    copy = base / "project"
    shutil.copytree(project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield _Scene(address, explorer)
    finally:
        stop(process)


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


def _settled(page: Any) -> None:
    page.wait_for_selector(".ix-root:not([data-loading])", timeout=180000)
    page.wait_for_function(
        "() => !document.querySelector('.ix-state[role=\"status\"]')", timeout=180000
    )


def _open(page: Any, scene: _Scene, query: str = "", wait: str = '[data-draw="families"]') -> None:
    page.goto(f"{scene.base}?mode=lineage{query}#explorer")
    page.wait_for_selector(wait, timeout=180000)
    _settled(page)


def _shown(page: Any) -> dict[str, Any]:
    """What the drawing holds: each node and edge with what it says."""
    shown: dict[str, Any] = page.evaluate(
        """() => ({
          nodes: Array.from(document.querySelectorAll('g[data-node]')).map(g => {
            const rect = g.querySelector('rect').getBoundingClientRect();
            return {id: g.dataset.node, kind: g.dataset.kind, members: Number(g.dataset.members),
              conflict: Number(g.dataset.conflict), text: g.textContent,
              dash: g.querySelector('rect').getAttribute('stroke-dasharray'),
              stroke: Number(g.querySelector('rect').getAttribute('stroke-width')),
              box: [rect.left, rect.top, rect.right, rect.bottom]};
          }),
          edges: Array.from(document.querySelectorAll('g[data-edge]')).map(g => {
            const path = g.querySelector('path');
            return {id: g.dataset.edge, lines: Number(g.dataset.lines), kind: g.dataset.kind,
              width: Number(path.getAttribute('stroke-width')),
              dash: path.getAttribute('stroke-dasharray')};
          }),
        })"""
    )
    return shown


#: What the sentence above the plot says where some of what is drawn are files in no family: the
#: families, then the files, each with its total, then what the limit left out.
SENTENCE = re.compile(
    r"^([\d,]+) of ([\d,]+) famil(?:y|ies) and ([\d,]+) of ([\d,]+) files? in no family drawn; "
    r"([\d,]+) more families and files hold ([\d,]+) lines? to these and ([\d,]+) among "
    r"themselves\.$"
)


def _truth(page: Any, limit: int) -> dict[str, Any]:
    """The engine's own answer for the same request, for the counts the page must repeat."""
    body: dict[str, Any] = page.evaluate(
        "async n => (await fetch('/api/v1/explorer/lineage/families?proposals=true&limit=' + n))"
        ".json()",
        limit,
    )
    return body


def _sentence(page: Any) -> str:
    return str(page.locator('[data-families="sentence"]').inner_text())


# --------------------------------------------------------------------- families


def test_families_draw_with_no_focus_eight_boxes_legible_and_draw_2n_doubles_the_limit(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1")
        draw = page.locator('[role="group"][aria-label="Draw"]')
        pressed = draw.locator('button[aria-pressed="true"]').all_inner_texts()
        assert pressed == ["Families"]
        assert draw.locator("button").all_inner_texts() == ["Families", "Files", "Grid"]
        shown = _shown(page)
        assert len(shown["nodes"]) == FIRST
        assert shown["edges"]
        # Legible at 1440: every box on the page, none over another, none shrunk past reading.
        plot = page.locator(".ix-families").bounding_box()
        assert plot is not None
        for node in shown["nodes"]:
            left, top, right, bottom = node["box"]
            assert plot["x"] - 1 <= left and right <= plot["x"] + plot["width"] + 1
            assert plot["y"] - 1 <= top and bottom <= plot["y"] + plot["height"] + 1
            assert right - left >= 100, node["text"]
        boxes = [node["box"] for node in shown["nodes"]]
        for first in range(len(boxes)):
            for second in range(first + 1, len(boxes)):
                a, b = boxes[first], boxes[second]
                overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                assert not overlap, (shown["nodes"][first]["text"], shown["nodes"][second]["text"])
        # The sentence counts what was drawn and what the limit left out, in the engine's own
        # totals, and names a family and a file in no family apart: a file is never a family.
        truth = _truth(page, FIRST)
        cut = truth["truncation"]
        assert cut["families_total"] > 0 and cut["files_total"] > 0
        said = SENTENCE.match(_sentence(page))
        assert said, _sentence(page)
        families, of_families, files, of_files, more, lines_to, among = (
            int(part.replace(",", "")) for part in said.groups()
        )
        kinds = [node["kind"] for node in shown["nodes"]]
        assert (families, files) == (kinds.count("family"), kinds.count("file"))
        assert families + files == FIRST
        assert (of_families, of_files) == (cut["families_total"], cut["files_total"])
        assert more == cut["nodes_left_out"] == cut["nodes_total"] - FIRST
        assert (lines_to, among) == (
            cut["lines_to_nodes_not_drawn"],
            cut["lines_among_nodes_not_drawn"],
        )
        total = cut["nodes_total"]
        assert total > FIRST
        more_link = page.locator('[data-families="more"]')
        assert more_link.inner_text() == f"Draw {2 * FIRST}"
        assert page.locator('[data-families="table"]').inner_text() == "Table"
        more_link.click()
        page.wait_for_function(
            "(n) => document.querySelectorAll('g[data-node]').length === n",
            arg=min(2 * FIRST, total),
        )
        assert "drawn=16" in page.url
        assert total > 2 * FIRST
        doubled = _truth(page, 2 * FIRST)["truncation"]
        again = SENTENCE.match(_sentence(page))
        assert again, _sentence(page)
        counts = [int(part.replace(",", "")) for part in again.groups()]
        assert counts[1] == doubled["families_total"] and counts[3] == doubled["files_total"]
        assert counts[0] + counts[2] == min(2 * FIRST, total)
        assert not page.errors
    finally:
        page.context.close()


def test_a_box_says_its_words_its_files_and_lines_and_a_conflict_is_a_dashed_red_stroke(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1&drawn=60")
        shown = _shown(page)
        by_text = {node["text"]: node for node in shown["nodes"]}
        second = served.stem(1)
        tables = next(node for text, node in by_text.items() if text.startswith(f"{second} table"))
        # Two of its four tables are held in conflict by the family: counted in words beside it.
        assert tables["members"] == 2
        assert tables["conflict"] == 2
        assert "2 in conflict" in tables["text"]
        assert tables["dash"]
        assert " files · " in tables["text"] and " lines" in tables["text"]
        # A family with nothing in conflict has a solid stroke and no such words.
        clean = next(node for text, node in by_text.items() if text.startswith(f"{second} mask"))
        assert clean["conflict"] == 0 and not clean["dash"]
        assert "in conflict" not in clean["text"]
        # A box's stroke is heavier where more of its files have a line.
        assert all(1 <= node["stroke"] <= 4 for node in shown["nodes"])
        # An edge's width is its lines, 1 to 6 px, and never narrower for more lines.
        edges = sorted(shown["edges"], key=lambda edge: edge["lines"])
        assert all(1 <= edge["width"] <= 6 for edge in edges)
        assert [edge["width"] for edge in edges] == sorted(edge["width"] for edge in edges)
        # One edge to a kind between two nodes, and a proposed one is dashed.
        proposed = [edge for edge in edges if edge["kind"].startswith("proposed")]
        assert proposed and all(edge["dash"] for edge in proposed)
        published = [edge for edge in edges if edge["kind"] == "published_derivation"]
        assert published and all(not edge["dash"] for edge in published)
    finally:
        page.context.close()


def test_a_family_opens_as_a_grid_of_its_files_with_lines_in_and_out(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1&drawn=60")
        second = served.stem(1)
        node = page.locator("g[data-node]", has_text=f"{second} table")
        node.click()
        page.wait_for_selector('[data-draw="grid"][data-family]', timeout=HANG_GUARD_MS)
        _settled(page)
        assert "draw=grid" in page.url and "family=family" in page.url
        pressed = page.locator('[role="group"][aria-label="Draw"] button[aria-pressed="true"]')
        assert pressed.all_inner_texts() == ["Grid"]
        headers = page.locator('table[data-table="family-members"] thead th').all_inner_texts()
        assert [header.strip().lower() for header in headers] == [
            "name",
            "folder",
            "lines in",
            "lines out",
        ]
        rows = page.locator('table[data-table="family-members"] tbody tr')
        assert rows.count() == 2
        names = page.locator('table[data-table="family-members"] tbody th').all_inner_texts()
        assert names == sorted(Path(path).name for path in served.files(1, "quant")[:2])
        counts = page.locator('[data-grid="counts"]').inner_text()
        assert "Files 1 to 2 of 2" in counts
        assert "2 more in conflict with the family" in counts
        # The rest is counted, a hundred at a time: nothing past this page, so both are off.
        assert page.get_by_role("button", name="Earlier 100").is_disabled()
        assert page.get_by_role("button", name="Next 100").is_disabled()
        # Back to every family.
        page.get_by_role("button", name="All families").click()
        page.wait_for_selector('[data-draw="families"]', timeout=HANG_GUARD_MS)
        assert "family=" not in page.url
        assert not page.errors
    finally:
        page.context.close()


def test_a_family_answered_wrong_is_its_files_and_the_page_says_it_un_collapses(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1&drawn=60")
        texts = [node["text"] for node in _shown(page)["nodes"]]
        first, second = served.stem(0), served.stem(1)
        # The first experiment's masks were answered Wrong: no family holds them. The second's do.
        assert not any(text.startswith(f"{first} mask") for text in texts)
        assert any(text.startswith(f"{second} mask") for text in texts)
        masks = [Path(path).name for path in served.files(0, "mask")]
        shown = [node for node in _shown(page)["nodes"] if node["kind"] == "file"]
        stood_alone = [node for node in shown if any(name in node["text"] for name in masks)]
        assert stood_alone, "the masks answered Wrong draw as their own files"
        assert all(node["members"] == 1 for node in stood_alone)
        legend = page.locator('[data-families="wrong"]').inner_text()
        assert legend == 'A family answered "Wrong" un-collapses into its files.'
    finally:
        page.context.close()


def test_table_and_grid_list_the_same_families_the_drawing_holds(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1")
        drawn = [node["id"] for node in _shown(page)["nodes"]]
        page.locator('[data-families="table"]').click()
        page.wait_for_selector('table[data-table="families"]', timeout=HANG_GUARD_MS)
        rows = page.eval_on_selector_all(
            'table[data-table="families"] tbody tr', "rows => rows.map(row => row.dataset.node)"
        )
        assert rows == drawn
        assert page.locator('table[data-table="family-lines"] tbody tr').count() > 0
        # D leads to Grid, which with no family open lists the families to open one from.
        page.locator('table[data-table="families"]').locator("button").first.focus()
        page.keyboard.press("d")
        page.wait_for_function("() => location.search.includes('draw=')")
        assert not page.errors
    finally:
        page.context.close()


# ----------------------------------------------------------------- the keyboard


def test_the_plot_is_one_stop_tab_walks_nodes_enter_opens_a_family_and_f_fits(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1")
        plot = page.locator(".ix-families")
        assert plot.get_attribute("tabindex") == "0"
        assert "Tab" in (plot.get_attribute("aria-label") or "")
        plot.focus()
        page.keyboard.press("Tab")
        page.wait_for_function("() => new URLSearchParams(location.search).get('sel')")
        first = page.evaluate("new URLSearchParams(location.search).get('sel')")
        page.keyboard.press("Tab")
        page.wait_for_function(
            "(was) => new URLSearchParams(location.search).get('sel') !== was", arg=first
        )
        # Still in the plot: Shift Tab walks back one node rather than leaving it.
        assert page.evaluate("document.activeElement.classList.contains('ix-families')")
        page.keyboard.press("Shift+Tab")
        page.wait_for_function(
            "(was) => new URLSearchParams(location.search).get('sel') === was", arg=first
        )
        # F fits the drawing and changes nothing chosen.
        page.keyboard.press("f")
        assert page.evaluate("new URLSearchParams(location.search).get('sel')") == first
        # An arrow follows an edge from the node chosen, to a node it joins.
        moved = False
        for arrow in ("ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"):
            page.keyboard.press(arrow)
            if page.evaluate("new URLSearchParams(location.search).get('sel')") != first:
                moved = True
                break
        assert moved
        # Enter opens a family as a Grid: a family chosen, the plot focused, Enter.
        body = page.evaluate(
            "async () => (await fetch('/api/v1/explorer/lineage/families"
            "?proposals=true&limit=8')).json()"
        )
        family_id = next(node["id"] for node in body["nodes"] if node["kind"] == "family")
        _open(page, served, f"&prop=1&sel={family_id}")
        page.locator(".ix-families").focus()
        page.keyboard.press("Enter")
        page.wait_for_selector('[data-draw="grid"][data-family]', timeout=HANG_GUARD_MS)
        assert not page.errors
    finally:
        page.context.close()


def test_d_cycles_families_files_grid_and_keeps_working(browser: Any, served: _Scene) -> None:
    page = _page(browser)
    try:
        focus = served.explorer.ids[served.explorer.experiments[0]]
        _open(page, served, f"&focus={focus}&draw=families")
        draw = page.locator('[role="group"][aria-label="Draw"]')
        seen = []
        page.locator(".ix-families").focus()
        for _ in range(4):
            seen.append(draw.locator('button[aria-pressed="true"]').inner_text())
            page.keyboard.press("d")
            page.wait_for_function(
                "(was) => document.querySelector('[role=group][aria-label=Draw] "
                "button[aria-pressed=true]').innerText !== was",
                arg=seen[-1],
            )
            _settled(page)
        order = ["Families", "Files", "Grid"]
        assert seen == [order[step % 3] for step in range(4)]
        assert not page.errors
    finally:
        page.context.close()


def test_a_file_in_no_family_is_drawn_as_a_file_and_never_called_a_family(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1&drawn=60")
        shown = _shown(page)
        files = [node for node in shown["nodes"] if node["kind"] == "file"]
        families = [node for node in shown["nodes"] if node["kind"] == "family"]
        assert files and families
        widest_file = max(node["box"][2] - node["box"][0] for node in files)
        narrowest_family = min(node["box"][2] - node["box"][0] for node in families)
        assert widest_file < narrowest_family
        for node in files:
            assert "in no family" in node["text"]
            assert "files \u00b7" not in node["text"]
            # A thin stroke, not the heavy ink of a family's box.
            assert node["stroke"] <= 1.3 and not node["dash"]
        # Said in the counts too: the whole is drawn, and the sentence names both kinds.
        said = _sentence(page)
        assert " families and " in said and " files in no family drawn." in said
        assert "families drawn" not in said
        legend = page.locator(".ix-legend").inner_text()
        assert "file in no family" in legend
    finally:
        page.context.close()


def test_a_file_in_focus_opens_as_files_and_never_as_an_empty_plot(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        raw = served.explorer.ids[served.files(0, "raw")[0]]
        _open(page, served, f"&focus={raw}", wait=".ix-lineage")
        pressed = page.locator('[role="group"][aria-label="Draw"] button[aria-pressed="true"]')
        assert pressed.all_inner_texts() == ["Files"]
        assert page.locator("g[data-node]").count() == 0
        assert page.locator(".ix-lineage svg g rect").count() > 1
        assert "families" not in page.locator(".ix-crumbs").inner_text().lower()
        # Asked for as Families, its lines are not cut at its folder: the whole run's families.
        page.locator('[role="group"][aria-label="Draw"]').get_by_role(
            "button", name="Families"
        ).click()
        page.wait_for_selector('[data-draw="families"]', timeout=HANG_GUARD_MS)
        _settled(page)
        assert page.locator("g[data-node]").count() > 0
        assert page.locator('[data-lone="families-empty"]').count() == 0
        assert not page.errors
    finally:
        page.context.close()


def test_the_first_tab_chooses_the_largest_family(browser: Any, served: _Scene) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1")
        body = page.evaluate(
            "async () => (await fetch('/api/v1/explorer/lineage/families?proposals=true&limit=8'))"
            ".json()"
        )
        largest = body["nodes"][0]["id"]
        page.locator(".ix-families").focus()
        page.keyboard.press("Tab")
        page.wait_for_function("() => new URLSearchParams(location.search).get('sel')")
        assert page.evaluate("new URLSearchParams(location.search).get('sel')") == largest
    finally:
        page.context.close()


def test_red_is_a_border_or_a_dot_beside_a_word_never_the_words_color(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1&drawn=60")
        ink = page.evaluate("getComputedStyle(document.querySelector('.ix-root')).color")
        # In the drawing: the words "n in conflict" are ink, a red dot stands beside them, and the
        # box's stroke is the red border.
        box = page.locator("g[data-node]", has_text="in conflict").first
        assert box.locator("text", has_text="in conflict").get_attribute("fill") == "var(--ix-ink)"
        assert box.locator("circle").get_attribute("fill") == "var(--ix-problem)"
        assert box.locator("rect").get_attribute("stroke") == "var(--ix-problem)"
        # In the table and the inspector: ink with a red dot.
        page.locator('[data-families="table"]').click()
        page.wait_for_selector('table[data-table="families"]', timeout=HANG_GUARD_MS)
        cell = page.locator(
            'table[data-table="families"] td.ix-num', has=page.locator(".ix-dot-problem")
        )
        assert cell.count() >= 1
        assert page.evaluate("el => getComputedStyle(el).color", cell.first.element_handle()) == ink
        node = page.locator('table[data-table="families"] tr:has(.ix-dot-problem) button').first
        node.click()
        page.wait_for_selector('[data-draw="grid"][data-family]', timeout=HANG_GUARD_MS)
        value = page.locator(".ix-inspector dd", has=page.locator(".ix-dot-problem")).first
        assert page.evaluate("el => getComputedStyle(el).color", value.element_handle()) == ink
        assert not page.errors
    finally:
        page.context.close()


def test_the_workspaces_keys_do_nothing_while_the_focus_is_outside_it(
    browser: Any, served: _Scene
) -> None:
    page = _page(browser)
    try:
        _open(page, served, "&prop=1")
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("d")
        page.keyboard.press("t")
        page.keyboard.press("Control+z")
        assert "draw=" not in page.url and "table=1" not in page.url
        # Inside it D works, and goes on working after it replaced the view that had the focus.
        page.locator(".ix-families").focus()
        pressed = "[role=group][aria-label=Draw] button[aria-pressed=true]"
        for _ in range(2):
            was = page.locator(pressed).inner_text()
            page.keyboard.press("d")
            page.wait_for_function(
                "([selector, was]) => document.querySelector(selector).innerText !== was",
                arg=[pressed, was],
            )
            _settled(page)
        assert "draw=" in page.url
        assert page.evaluate("document.querySelector('.ix-root').contains(document.activeElement)")
    finally:
        page.context.close()


# ------------------------------------------------------------------ a lone file


def test_a_lone_file_shows_the_sentence_and_its_node_alone(browser: Any, served: _Scene) -> None:
    page = _page(browser)
    try:
        lone = served.explorer.ids["notes/brisa_notes.txt"]
        _open(page, served, f"&draw=files&focus={lone}", wait='[data-lone="yes"]')
        said = page.locator(".ix-lone").inner_text()
        assert "No line touches this file in Scan " in said
        assert "A line not drawn is not evidence of absence." in said
        assert (
            page.locator(".ix-lone").get_by_role("button", name="Show proposed pairs").count() == 1
        )
        # The focus alone: one item, no lines.
        assert "1 items · 0 lines" in page.locator(".ix-footer .ix-caps").inner_text()
        page.get_by_role("button", name="Show proposed pairs").click()
        page.wait_for_function("() => location.search.includes('prop=1')")
        assert not page.errors
    finally:
        page.context.close()


def test_a_file_with_a_line_shows_no_such_sentence(browser: Any, served: _Scene) -> None:
    page = _page(browser)
    try:
        # The first experiment's first raw file is made into a mask by a published line.
        raw = served.files(0, "raw")[0]
        _open(
            page,
            served,
            f"&draw=files&focus={served.explorer.ids[raw]}",
            wait=".ix-lineage",
        )
        _settled(page)
        assert page.locator(".ix-lone").count() == 0
    finally:
        page.context.close()


# -------------------------------------------------------------------------- axe


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_families_have_no_serious_accessibility_violation(
    browser: Any, served: _Scene, scheme: str
) -> None:
    script = _axe_script()
    if script is None:
        pytest.skip("axe-core is not installed: set INGENCE_AXE_JS")
    page = _page(browser, scheme)
    try:
        _open(page, served, "&prop=1")
        page.add_script_tag(content=script.read_text(encoding="utf-8"))
        found = page.evaluate(
            "options => window.axe.run(document.querySelector('.ix-root'), options)",
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
