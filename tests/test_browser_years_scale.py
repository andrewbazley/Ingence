"""The Years view's scale.

Each test runs in a real browser, on the invented theme pool (``fixtures.theme_pool``), on the same
plant written as an archive's listings (``fixtures.archive_pool``), and, where a test needs dozens
of lines of work, on the engine's routes served through Playwright from a larger project's answers
(``fixtures.many_lines``).

- The level names follow the root's kind, from the ladder the engine publishes: an archive's rows
  say "study" and "theme", a laboratory's "experiment" and "theme" ("line of work" is not used).
- Overlap draws a ring with chords from 13 lines up (as many chords as the overlap route serves
  pairs, and a table row for each). At 12 lines or fewer the matrix is the default.
- The Wheel labels only the arcs wider than 14 pixels, never two labels over each other, numbers
  the rest, and lists every line, with its number, its name and its count, beside it.
- The sums are the engine's: a person's and a line's experiments are what the completeness route
  sums.
- The Years keys work, and none fires while a field has focus.
- The first draw keeps an entry opened before it landed.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path as FilePath
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    launch_browser,
    need,
    serve_theme_project,
    start_dashboard,
    stop,
)
from fixtures.many_lines import serve_many_lines

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

WIDE = {"width": 1440, "height": 900}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "scale-lab")


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    from fixtures.archive_pool import build_archive_project

    base = tmp_path_factory.mktemp("scale-archive")
    project: Path = build_archive_project(base / "built")
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        yield address
    finally:
        stop(process)


def _get(base: str, route: str) -> Any:
    with urllib.request.urlopen(f"{base}api/v1/{route}", timeout=30) as answer:
        return json.loads(answer.read())


def _years(browser: Any, base: str, size: dict[str, int] | None = None) -> Any:
    page = browser.new_page(viewport=size or WIDE)
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .yrow .ylabel", timeout=HANG_GUARD_MS)
    return page


# -- the level names follow the root's kind -------------------------------------------------------

#: The words a row's own sentences must not carry in an archive: the laboratory's levels.
LABORATORY_WORDS = ("experiment", "line of work", "lines of work", "person", "people")
#: What the page reads of the rows' own sentences: the sentence under the title, each row's line.
ROW_TEXT = """() => [...document.querySelectorAll(
  '.years .yhint, .years .yrow .ysub, .years .yhead h2, .ycrumbs')]
  .map((n) => n.textContent.trim())"""


def test_an_archives_rows_say_study_and_theme_and_a_labs_say_experiment_and_line_of_work(
    browser: Any, lab: str, archive: str
) -> None:
    ladder = _get(archive, "ladder")
    assert (
        ladder["kind"] == "archive"
        and [root["kind"] for root in ladder["roots"]] == ["archive"] * 3
    )
    assert _get(lab, "ladder")["kind"] == "lab"

    page = _years(browser, archive)
    try:
        said = " | ".join(page.evaluate(ROW_TEXT))
        assert "6 themes over 66 studies" in said, said
        assert "10 studies" in said
        for word in LABORATORY_WORDS:
            assert word not in said.lower(), f"{word!r} in an archive's rows: {said}"
        assert "In no theme" in page.text_content(".years")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowRight")
        page.wait_for_selector(".ycrumbs .ysep")
        said = " | ".join(page.evaluate(ROW_TEXT))
        assert "10 studies. The mark is the study's date" in said, said
        for word in LABORATORY_WORDS:
            assert word not in said.lower(), f"{word!r} in an archive's rows: {said}"
    finally:
        page.close()

    page = _years(browser, lab)
    try:
        said = " | ".join(page.evaluate(ROW_TEXT))
        assert (
            "A row per area; bar height is experiments that year. 66 experiments in 3 areas."
            in said
        )
        assert "3 themes" in said or "2 themes" in said
        assert "stud" not in said.lower() and "line of work" not in said.lower(), said
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowRight")
        page.wait_for_selector(".ycrumbs .ysep")
        said = " | ".join(page.evaluate(ROW_TEXT))
        assert "stud" not in said.lower() and "line of work" not in said.lower(), said
        assert "experiments" in said
    finally:
        page.close()


def test_an_archives_wheel_overlap_and_narrow_link_use_its_words(
    browser: Any, lab: str, archive: str
) -> None:
    for base, broad, theme, study in (
        (archive, "broad themes", "theme", "stud"),
        # Plain words: a laboratory's coarser groups are broad themes too.
        (lab, "broad themes", "theme", "experiment"),
    ):
        page = _years(browser, base)
        try:
            page.keyboard.press("w")
            page.wait_for_selector(".ybig .ywheel svg")
            hint = page.text_content(".yhint")
            legend = page.text_content(".ybig")
            assert f"rings: {broad}," in hint and study in hint.lower(), hint
            assert f"Inner ring: {broad}" in legend, legend
            page.keyboard.press("o")
            page.wait_for_selector(".yoverlap", timeout=HANG_GUARD_MS)
            assert f"A {theme} below 0.80" in page.text_content(".yoverlap .yhint")
        finally:
            page.close()
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        page.goto(f"{archive}#years")
        page.wait_for_selector(".years.phone .ynarrow", timeout=HANG_GUARD_MS)
        links = page.eval_on_selector_all(
            ".ynarrow button.link", "nodes => nodes.map(n => n.textContent.trim())"
        )
        assert links[0] == "Search studies", links
    finally:
        page.close()


def test_the_ladders_page_names_the_levels_the_engine_publishes(browser: Any, archive: str) -> None:
    page = browser.new_page(viewport=WIDE)
    try:
        page.goto(f"{archive}#ladders")
        page.wait_for_selector(".yladders table", timeout=HANG_GUARD_MS)
        assert page.get_attribute(".yladders", "data-kind") == "archive"
        levels = page.eval_on_selector_all(
            ".yladders tbody tr td:first-child", "nodes => nodes.map(n => n.textContent)"
        )
        assert levels == ["Archive", "Theme", "Study", "File"]
    finally:
        page.close()


# -- the sums are the engine's --------------------------------------------------------------------


def test_the_counts_per_person_and_per_line_are_the_completeness_routes_sums(
    browser: Any, lab: str, archive: str
) -> None:
    completeness = _get(lab, "completeness")
    page = _years(browser, lab)
    try:
        rows = page.evaluate(
            """() => [...document.querySelectorAll('.years .yrow')].map((r) => [
              r.querySelector('.ylabel').textContent.trim(), r.querySelector('.ysub').textContent,
              r.querySelector('.yright').textContent])"""
        )
        served = {row["key"]: row for row in completeness["people"]}
        assert {name for name, _, _ in rows} == set(served)
        for name, sub, right in rows:
            assert sub.startswith(f"{served[name]['experiments']} experiments"), (name, sub)
            held = served[name]
            assert (
                f"{held['raw_followed']} of {held['raw_acquisitions']} raw files analyzed" in right
            )
    finally:
        page.close()

    # An archive has no people: its rows are lines, summed per theme.
    completeness = _get(archive, "completeness")
    themes = {t["theme"]: t for t in _get(archive, "themes")["themes"]}
    by_label = {t["label"]: t["theme"] for t in themes.values()}
    served_themes = {int(row["key"]): row for row in completeness["themes"]}
    page = _years(browser, archive)
    try:
        rows = page.evaluate(
            """() => [...document.querySelectorAll('.years .yrow')].map((r) => [
              r.querySelector('.ylabel').textContent.trim(), r.querySelector('.ysub').textContent,
              r.querySelector('.yright').textContent])"""
        )
        checked = 0
        for label, sub, right in rows:
            if label not in by_label:
                continue
            row = served_themes[by_label[label]]
            assert sub.startswith(f"{row['experiments']} studies"), (label, sub)
            assert f"{row['raw_followed']} of {row['raw_acquisitions']} raw files analyzed" in right
            checked += 1
        assert checked == len(served_themes) == 6
    finally:
        page.close()


# -- Overlap at many lines ------------------------------------------------------------------------


def _base(base: str) -> dict[str, dict[str, Any]]:
    return {
        "themes": _get(base, "themes"),
        "completeness": _get(base, "completeness"),
        "stability": _get(base, "themes/stability"),
        "overlap": _get(base, "themes/overlap"),
    }


def _many(browser: Any, lab: str, lines: int, pairs: int) -> tuple[Any, dict[str, Any]]:
    page = browser.new_page(viewport=WIDE)
    served = serve_many_lines(page, _base(lab), lines, pairs)
    page.goto(f"{lab}#years")
    page.wait_for_selector(".years .yrow .ylabel", timeout=HANG_GUARD_MS)
    return page, served


def test_at_39_lines_the_ring_draws_a_chord_for_each_pair_and_a_table_row_for_each(
    browser: Any, lab: str
) -> None:
    page, served = _many(browser, lab, 39, 27)
    try:
        page.keyboard.press("o")
        page.wait_for_selector(".yring", timeout=HANG_GUARD_MS)
        assert len(served["overlap"]["pairs"]) == 27
        counts = page.evaluate(
            """() => ({arcs: document.querySelectorAll('.yring .arc').length,
              dotted: document.querySelectorAll('.yring .arc.dotted').length,
              chords: document.querySelectorAll('.yring .chord').length,
              table: document.querySelectorAll('.yring-pairs tbody tr').length,
              matrix: document.querySelectorAll('.ymatrix').length,
              choice: document.querySelectorAll('.yoverlap .ybar').length,
              drawing: document.querySelector('.yoverlap').dataset.drawing,
              centre: [...document.querySelectorAll('.yring text.center')]
                .map((t) => t.textContent)})"""
        )
        assert counts["drawing"] == "ring"
        assert counts["arcs"] == 39 and counts["chords"] == 27 and counts["table"] == 27
        assert counts["matrix"] == 0 and counts["choice"] == 0, "no matrix at this count"
        assert counts["centre"] == ["39 themes", "27 pairs share a word"], counts["centre"]
        # The arcs of lines under 0.80 are dotted: the served stabilities say which.
        soft = [t["theme"] for t in served["stability"]["themes"] if t["stability"] < 0.8]
        assert counts["dotted"] == len(soft) == 4
        # A chord's width is the words shared over the smaller line's: wider where more is shared.
        pairs = {(p["theme_a"], p["theme_b"]): p for p in served["overlap"]["pairs"]}
        drawn = page.evaluate(
            """() => [...document.querySelectorAll('.yring .chord')].map((c) =>
              [Number(c.dataset.a), Number(c.dataset.b), Number(c.getAttribute('stroke-width'))])"""
        )
        for a, b, width in drawn:
            pair = pairs[(min(a, b), max(a, b))]
            assert width == pytest.approx(1 + 5 * pair["shared"] / pair["of_smaller"], abs=0.06)
        # The table repeats every pair: its words, and "shared of the smaller".
        table = page.eval_on_selector_all(
            ".yring-pairs tbody tr",
            """nodes => nodes.map(n => [n.dataset.pair, n.children[2].textContent,
              n.children[3].textContent])""",
        )
        assert {row[0] for row in table} == {f"{a}|{b}" for a, b in pairs}
        for key, words, share in table:
            pair = pairs[tuple(int(x) for x in key.split("|"))]
            assert words == ", ".join(pair["words"])
            assert share == f"{pair['shared']} of {pair['of_smaller']}"
        # Every line's stability is the shared axis; the soft ones carry the dotted dot.
        axes = page.evaluate(
            """() => ({all: document.querySelectorAll('.ystab .stability').length,
              below: document.querySelectorAll('.ystab .stability .stability-dot.below').length})"""
        )
        assert axes == {"all": 39, "below": 4}
        # A line has a name in full in the list, never only on the arc's hover.
        names = page.eval_on_selector_all(
            ".ystab button.link", "nodes => nodes.map(n => n.textContent)"
        )
        assert sorted(names) == sorted(t["label"] for t in served["themes"]["themes"])
    finally:
        page.close()


@pytest.mark.parametrize("width", [1024, 1241, 1280, 1440])
def test_the_ring_never_stands_over_a_number_of_the_stability_list(
    browser: Any, lab: str, width: int
) -> None:
    """At every width from 1024 to 1440, the ring and the list share a pane: no cell of a list row
    touches the ring's box, and the page does not scroll sideways."""
    page = browser.new_page(viewport={"width": width, "height": 900})
    serve_many_lines(page, _base(lab), 39, 27)
    try:
        page.goto(f"{lab}#years")
        page.wait_for_selector(".years .yrow .ylabel", timeout=HANG_GUARD_MS)
        page.keyboard.press("o")
        page.wait_for_selector(".yring", timeout=HANG_GUARD_MS)
        seen = page.evaluate(
            """() => {
              const ring = document.querySelector('.yring-box').getBoundingClientRect();
              const touching = [];
              let narrowest = Infinity;
              for (const row of document.querySelectorAll('.ystab:not(.head)')) {
                narrowest = Math.min(narrowest, row.getBoundingClientRect().width);
                for (const cell of [...row.children, ...row.querySelectorAll('.stability > *')]) {
                  const b = cell.getBoundingClientRect();
                  if (b.width === 0 || b.height === 0) continue;
                  const across = b.left < ring.right && b.right > ring.left;
                  const down = b.top < ring.bottom && b.bottom > ring.top;
                  if (across && down) touching.push(cell.textContent.trim().slice(0, 20));
                  const edge = row.getBoundingClientRect().right;
                  if (b.right > edge + 1) touching.push('past its row');
                }
              }
              const root = document.documentElement;
              return {touching, narrowest, ring: ring.width,
                sideways: root.scrollWidth - root.clientWidth};
            }"""
        )
        assert seen["touching"] == [], seen
        assert seen["narrowest"] >= 424, "a list row keeps the room its three columns need"
        assert seen["ring"] >= 250, "the ring is drawn at a size a person can read"
        assert seen["sideways"] <= 0, "nothing makes the page scroll sideways"
    finally:
        page.close()


@pytest.mark.parametrize(("lines", "drawing"), [(12, "matrix"), (13, "ring")])
def test_the_ring_is_the_default_from_13_lines_and_the_matrix_up_to_12(
    browser: Any, lab: str, lines: int, drawing: str
) -> None:
    page, _ = _many(browser, lab, lines, min(lines, 9))
    try:
        page.keyboard.press("o")
        page.wait_for_selector(".yoverlap", timeout=HANG_GUARD_MS)
        assert page.get_attribute(".yoverlap", "data-drawing") == drawing
        if drawing == "matrix":
            assert page.eval_on_selector_all(".ymatrix td.cell", "n => n.length") == 144
            # The ring is one click away at this count.
            page.click(".yoverlap .ybar .ychip >> text=Ring")
            page.wait_for_selector(".yring", timeout=HANG_GUARD_MS)
            page.click(".yoverlap .ybar .ychip >> text=Matrix")
            page.wait_for_selector(".ymatrix", timeout=HANG_GUARD_MS)
        else:
            assert page.eval_on_selector_all(".ymatrix", "n => n.length") == 0
    finally:
        page.close()


def test_at_8_lines_the_matrix_is_the_default_and_a_ring_draws_the_pairs_served(
    browser: Any, lab: str
) -> None:
    page = _years(browser, lab)
    try:
        pairs = _get(lab, "themes/overlap")["pairs"]
        page.keyboard.press("o")
        page.wait_for_selector(".yoverlap", timeout=HANG_GUARD_MS)
        assert page.get_attribute(".yoverlap", "data-drawing") == "matrix"
        page.click(".yoverlap .ybar .ychip >> text=Ring")
        page.wait_for_selector(".yring", timeout=HANG_GUARD_MS)
        assert page.eval_on_selector_all(".yring .chord", "n => n.length") == len(pairs)
        assert page.eval_on_selector_all(".yring-pairs tbody tr", "n => n.length") == len(pairs)
    finally:
        page.close()


# -- the Wheel's labels at many arcs --------------------------------------------------------------

BOXES = """() => [...document.querySelectorAll(
  '.ybig .ywheel svg text.wlabel, .ybig .ywheel svg text.wnum')]
  .map((t) => { const b = t.getBoundingClientRect();
    return {kind: t.getAttribute('class'), n: Number(t.dataset.n), left: b.left, right: b.right,
      top: b.top, bottom: b.bottom, text: t.textContent}; })"""


def test_no_two_wheel_labels_overlap_at_39_lines_and_every_arc_has_a_row_in_the_list(
    browser: Any, lab: str
) -> None:
    page, served = _many(browser, lab, 39, 27)
    try:
        page.keyboard.press("w")
        page.wait_for_selector(".ybig .ywheel svg text", timeout=HANG_GUARD_MS)
        boxes = page.evaluate(BOXES)
        assert boxes, "labels or numbers are drawn"
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                apart = (
                    a["right"] <= b["left"]
                    or b["right"] <= a["left"]
                    or a["bottom"] <= b["top"]
                    or b["bottom"] <= a["top"]
                )
                assert apart, f"{a['text']!r} and {b['text']!r} overlap"
        # A label stands only on an arc wider than 14 pixels; narrower arcs are numbered.
        arcs = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll(
              '.ybig .ywheel path[data-arc-px]')]
              .map((p) => [p.dataset.line, Number(p.dataset['arcPx'])]))"""
        )
        assert len(arcs) == 39
        labelled = {x["n"] for x in boxes if x["kind"] == "wlabel"}
        numbers_by_line = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll('.ykeyrow')]
              .map((r) => [r.dataset.n, r.dataset.line]))"""
        )
        for n in labelled:
            assert arcs[numbers_by_line[str(n)]] > 14, f"a label on a narrow arc (line {n})"
        assert any(x["kind"] == "wnum" for x in boxes), "narrow arcs are numbered"
        assert any(x["kind"] == "wlabel" for x in boxes), "wide arcs carry their names"
        # The key: a row for every line, in the order around the Wheel, with name and count.
        rows = page.eval_on_selector_all(
            ".ykeylist li .ykeyrow",
            """nodes => nodes.map(n => [n.querySelector('.ynumber').textContent,
              n.querySelector('.ylinelabel').textContent,
              n.querySelector('.ylinecount').textContent,
              n.dataset.line])""",
        )
        assert len(rows) == 39
        assert [int(r[0]) for r in rows] == list(range(1, 40))
        lines = {str(t["theme"]): t for t in served["themes"]["themes"]}
        assert {r[3] for r in rows} == set(lines) == set(arcs)
        for _number, label, count, line in rows:
            assert label == lines[line]["label"]
            assert count == f"{lines[line]['experiments']} experiments"
        # Pointing at a row finds its arc; the list is no hover-only place.
        page.hover(".ykeyrow >> nth=4")
        assert page.eval_on_selector_all(".ywheel path.hot", "n => n.length") == 1
    finally:
        page.close()


# -- the Years keys -------------------------------------------------------------------------------

FOCUS = """() => { const a = document.activeElement;
  return {cls: a ? a.className : '', index: a && a.dataset ? a.dataset.index : null,
    id: a ? a.id : ''}; }"""


def test_the_rows_are_one_tab_stop_and_the_arrows_walk_them_with_a_ring(
    browser: Any, lab: str
) -> None:
    page = _years(browser, lab)
    try:
        stops = page.eval_on_selector_all(".yrow", "n => n.filter(r => r.tabIndex === 0).length")
        assert stops == 1, "the rows are one tab stop"
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.wait_for_function("document.activeElement.classList.contains('yrow')")
        assert page.evaluate(FOCUS)["index"] == "1", "the second ArrowDown is on the second row"
        ring = page.evaluate(
            """() => { const s = getComputedStyle(document.activeElement);
              return [s.outlineStyle, s.outlineWidth]; }"""
        )
        assert ring == ["solid", "2px"], "the focused row has the ring"
        page.keyboard.press("End")
        page.wait_for_function("document.activeElement.dataset.index === '2'")
        page.keyboard.press("Home")
        page.wait_for_function("document.activeElement.dataset.index === '0'")
        page.keyboard.press("ArrowUp")
        assert page.evaluate(FOCUS)["index"] == "0", "the walk stops at the first row"
    finally:
        page.close()


def test_right_opens_left_and_backspace_go_up_and_enter_is_the_inspector(
    browser: Any, lab: str
) -> None:
    page = _years(browser, lab)
    try:
        page.keyboard.press("ArrowDown")
        page.wait_for_function("document.activeElement.classList.contains('yrow')")
        name = page.text_content(".yrow.selected .ylabel").strip()
        page.keyboard.press("ArrowRight")
        page.wait_for_function("document.querySelectorAll('.ycrumbs .ysep').length === 1")
        assert page.text_content(".ycrumbs button[aria-current='location']").strip() == name
        page.keyboard.press("ArrowLeft")
        page.wait_for_function("document.querySelectorAll('.ycrumbs .ysep').length === 0")
        page.keyboard.press("ArrowRight")
        page.wait_for_function("document.querySelectorAll('.ycrumbs .ysep').length === 1")
        page.keyboard.press("Backspace")
        page.wait_for_function("document.querySelectorAll('.ycrumbs .ysep').length === 0")
        # Enter is the inspector for the row: it takes the focus (it does not open the row).
        page.keyboard.press("ArrowDown")
        page.wait_for_function("document.activeElement.classList.contains('yrow')")
        page.keyboard.press("Enter")
        page.wait_for_function("document.activeElement.id === 'detail'")
        assert page.evaluate("document.querySelectorAll('.ycrumbs .ysep').length") == 0
    finally:
        page.close()


def test_w_and_o_open_the_wheel_and_overlap_n_goes_to_the_graph_and_plus_minus_zero_zoom(
    browser: Any, lab: str
) -> None:
    page = _years(browser, lab)
    try:
        page.keyboard.press("w")
        page.wait_for_selector(".ybig .ywheel svg")
        page.keyboard.press("Escape")
        page.wait_for_selector(".years .yrow")
        # O opens Stability, a section of its own. Back returns to the rows.
        page.keyboard.press("o")
        page.wait_for_selector(".yoverlap")
        page.go_back()
        page.wait_for_selector(".years .yrow")
        # Zoom the axis: plus narrows it (a Reset zoom button appears), zero resets it.
        assert page.eval_on_selector_all(".yreset", "n => n.length") == 0
        before = page.text_content(".yticks")
        page.keyboard.press("+")
        page.wait_for_selector(".yreset")
        assert page.text_content(".yticks") != before, "the axis shows a narrower span"
        page.keyboard.press("-")
        page.keyboard.press("0")
        page.wait_for_function("document.querySelectorAll('.yreset').length === 0")
        # N: the graph of the chosen experiment's relations. An area's rows are its experiments,
        # so the arrow down from an area's first row chooses one.
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowRight")
        page.wait_for_selector(".ycrumbs .ysep")
        page.keyboard.press("ArrowDown")
        page.wait_for_function("document.activeElement.classList.contains('yrow')")
        page.keyboard.press("n")
        page.wait_for_function(
            "location.hash.startsWith('#graph') || location.hash.startsWith('#explorer')"
        )
    finally:
        page.close()


def test_no_key_fires_while_a_field_has_focus(browser: Any, lab: str) -> None:
    page = _years(browser, lab)
    try:
        before = page.url
        page.focus("#search-box")
        page.keyboard.type("wo+-0n")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("End")
        page.keyboard.press("Backspace")
        assert page.input_value("#search-box") == "wo+-0"
        assert page.eval_on_selector_all(".ybig", "n => n.length") == 0, "W opened nothing"
        assert page.eval_on_selector_all(".yoverlap", "n => n.length") == 0, "O opened nothing"
        assert page.eval_on_selector_all(".ycrumbs .ysep", "n => n.length") == 0
        assert page.eval_on_selector_all(".yrow.focused", "n => n.length") == 0
        assert page.url == before
    finally:
        page.close()


# -- the first draw -------------------------------------------------------------------------------


def test_an_entry_opened_before_the_first_draw_stays_open(browser: Any, lab: str) -> None:
    """The page opens an entry in the first fraction of a second; the rows land later (their route
    is held back), and the draw that lands them leaves the entry alone."""
    entry = _get(lab, "completeness")["experiments"][0]
    page = browser.new_page(viewport=WIDE)
    try:
        release: list[Any] = []

        def hold(route: Any) -> None:
            release.append(route)

        page.route("**/api/v1/themes", hold)
        # The Dashboard opens on the Overview. The Years view is opened by its address.
        page.goto(f"{lab}#years")
        page.wait_for_function("() => typeof inspector !== 'undefined'")
        page.evaluate("(id) => inspector.showEntry(id)", entry["id"])
        page.wait_for_function(
            "(id) => [...document.querySelectorAll('#detail code')]"
            ".some((c) => c.textContent === id)",
            arg=entry["id"],
        )
        assert page.eval_on_selector_all(".years .yrow", "n => n.length") == 0, "rows not drawn yet"
        for _ in range(HANG_GUARD_MS // 50):
            if release:
                break
            page.wait_for_timeout(50)
        assert release, "the rows' route was asked for"
        release[0].continue_()
        page.wait_for_selector(".years .yrow .ylabel", timeout=HANG_GUARD_MS)
        assert page.eval_on_selector_all(
            "#detail code", "(nodes, id) => nodes.some((c) => c.textContent === id)", entry["id"]
        ), "the first draw kept the entry"
        assert page.eval_on_selector_all("#detail .ykicker", "n => n.length") == 0
        # A later choice still draws its card as ever.
        page.click(".yrow")
        page.wait_for_selector("#detail .ykicker")
    finally:
        page.close()


def test_typing_in_the_file_filter_keeps_the_focus_and_updates_its_count(
    browser: Any, lab: str
) -> None:
    """A redraw of the rows while a person types in the filter must not take the focus, and the keys
    stay quiet while the field has it."""
    entry = _get(lab, "completeness")["experiments"][0]
    page = _years(browser, lab)
    try:
        page.evaluate("(id) => window.ingenceYears.open({kind: 'exp', id})", entry["id"])
        # An experiment opens on its story. Its files are one chip away.
        page.wait_for_selector(".years .ystory", timeout=HANG_GUARD_MS)
        page.click("text=files, one by one")
        page.wait_for_selector(".ybar input[type='search']", timeout=HANG_GUARD_MS)
        page.wait_for_selector(".yrow .ylabel.mono", timeout=HANG_GUARD_MS)
        total = page.text_content(".ybar .ycount")
        page.click(".ybar input[type='search']")
        page.keyboard.type("csv")
        assert page.input_value(".ybar input[type='search']") == "csv"
        assert page.evaluate("document.activeElement.tagName") == "INPUT"
        page.wait_for_function(
            "(total) => document.querySelector('.ybar .ycount').textContent !== total", arg=total
        )
        assert " of " in page.text_content(".ybar .ycount")
    finally:
        page.close()


# -- the views, read by axe-core ------------------------------------------------------------------

#: The standards the release lane reads (``test_browser_release_axe.py``).
STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _axe_script() -> FilePath | None:
    named = os.environ.get("INGENCE_AXE_JS")
    root = FilePath(__file__).resolve().parents[1]
    for candidate in (
        FilePath(named) if named else None,
        root / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _serious(page: Any, where: str) -> list[str]:
    script = _axe_script()
    if script is None:
        message = "axe-core is not installed: run `npm install`, or set INGENCE_AXE_JS"
        if os.environ.get("INGENCE_BROWSER_REQUIRED") == "1":
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.add_script_tag(content=script.read_text(encoding="utf-8"))
    found = page.evaluate(
        "options => window.axe.run(document, options)",
        {"runOnly": {"type": "tag", "values": STANDARDS}},
    )
    return [
        f"{where}: {v['impact']} {v['id']}: {v['help']}; {[n['html'][:80] for n in v['nodes'][:2]]}"
        for v in found["violations"]
        if v["impact"] in ("critical", "serious")
    ]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_ring_the_wheel_and_the_narrow_views_have_no_serious_accessibility_findings(
    browser: Any, lab: str, scheme: str
) -> None:
    found: list[str] = []
    #: Each step: the key (or the Details chip) and what the page then shows.
    for width, steps in (
        # O opens Stability (the ring), a section of its own, so it comes last.
        (1440, (("w", ".ybig .ywheel svg"), ("o", ".yring"))),
        (
            1000,
            (
                ("w", ".ysheet .ywheel svg"),
                ("Escape", "!.ysheet"),
                ("details", "main.drawer-open"),
            ),
        ),
        (390, ()),
    ):
        page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
        try:
            serve_many_lines(page, _base(lab), 39, 27)
            page.goto(f"{lab}#years")
            page.wait_for_selector(
                ".years .yrow .ylabel, .years.phone .ynarrow", timeout=HANG_GUARD_MS
            )
            found += _serious(page, f"{scheme} {width} rows")
            for step, shown in steps:
                if step == "details":
                    page.click(".ychip.details")
                else:
                    page.keyboard.press(step)
                if shown.startswith("!"):
                    page.wait_for_function(f"document.querySelector('{shown[1:]}') === null")
                else:
                    page.wait_for_selector(shown, timeout=HANG_GUARD_MS)
                found += _serious(page, f"{scheme} {width} after {step}")
        finally:
            page.close()
    assert found == [], "\n".join(found)
