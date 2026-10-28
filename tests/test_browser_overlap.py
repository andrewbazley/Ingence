"""Overlap, the experiment's story and the workflow card in a real browser.

On the invented theme pool (``fixtures.theme_pool``: three invented areas, six planted themes,
dated experiment folders), served by ``ingence see`` on a copy with who decides kept in a folder of
the test's own. Skipped where the ``browser`` extra, a browser or scikit-learn is missing, as every
browser test is (a failure where ``INGENCE_BROWSER_REQUIRED=1``).

Every check reads the route the page reads (``/api/v1/overlap``, ``/api/v1/experiments/story``,
``/api/v1/workflows`` and the workflow controls) and compares the page with it, so a check holds
whatever the run holds. Where a check needs the run's own experiments behind a line (their files),
the test answers ``/api/v1/overlap`` itself with a payload of invented words over the run's real
experiment folders.

1. Connections draws one dot per experiment and a line of each kind the route gives.
2. A line opens to its evidence, then its files: the evidence in words first, and the files of
   both experiments only after Show the files. A dot clicked on the drawing opens its story.
3. The Overview's highlight: the experiments named in ``sessionStorage['ingence.highlight']``
   are highlighted until Clear highlight.
4. By year: areas, then an area's experiments under their themes, then one experiment's story.
5. ``#years`` opens By year, on a load and from a link while Connections is shown.
6. The workflow card: Turn off and Change post a dry run first and show its summary. Only the
   confirm button posts without ``dry_run``.
7. The wheel on the drawing: a plain wheel is left to the page, Ctrl + wheel zooms, and a sideways
   wheel pans.
8. Stability is a section of its own, off the main view.
"""

from __future__ import annotations

import json
import re
import urllib.request
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

#: The words of the invented lines the tests draw over the run's own experiments.
SHARED = ["shared_alpha", "shared_beta"]
STAGES = ["stage_raw", "stage_analysis"]

#: Dispatch one wheel event at the middle of the first element the selector finds; true where no
#: listener held the page still.
WHEEL = """([selector, init]) => {
  const target = document.querySelector(selector);
  if (!target) return 'missing';
  const box = target.getBoundingClientRect();
  return target.dispatchEvent(new WheelEvent('wheel', {bubbles: true, cancelable: true,
    composed: true, clientX: box.left + box.width / 2, clientY: box.top + box.height / 2,
    deltaMode: 0, ...init}));
}"""
FRAMES = (
    "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() =>"
    " setTimeout(done, 0))))"
)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "d3-overlap")


def _read(base: str, route: str) -> Any:
    with urllib.request.urlopen(f"{base}{route.lstrip('/')}", timeout=60) as answer:
        return json.loads(answer.read())


def _page(browser: Any) -> Any:
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    return page


def _drawn(page: Any) -> dict[str, Any]:
    page.wait_for_selector(".ov-canvas[data-nodes]", timeout=HANG_GUARD_MS)
    settled(page)
    return page.eval_on_selector(".ov-canvas", "e => Object.assign({}, e.dataset)")


def _invented_lines(base: str) -> dict[str, Any]:
    """A connections payload over three of the run's own experiments, its words invented: the
    first two share words and are repeats; the second and third follow the same workflow."""
    rows = _read(base, "/api/v1/experiments?limit=3&sort=name")["rows"]
    assert len(rows) == 3, "the theme pool has experiments"
    nodes = [
        {
            "experiment_id": row["id"],
            "label": row["shown_name"] or row["name"],
            "area": row["area"],
            "area_kind": "person",
            "theme": f"d3 theme {index % 2}",
            "start": row["date"],
            "end": row["date"],
            "completeness": 0.5,
            "repeats": 1,
        }
        for index, row in enumerate(rows)
    ]
    first, second, third = (row["id"] for row in rows)
    edges = [
        {
            "a": first,
            "b": second,
            "kinds": ["shared_words", "repeat_of"],
            "weight": 0.8,
            "evidence": [
                {"kind": "shared_words", "words": SHARED, "count": 2},
                {"kind": "repeat_of", "words": [], "count": None},
            ],
        },
        {
            "a": second,
            "b": third,
            "kinds": ["workflow"],
            "weight": 0.3,
            "evidence": [{"kind": "workflow", "words": STAGES, "count": 2}],
        },
    ]
    version = _read(base, "/api/v1/version")
    return {
        "api_version": "1.0",
        "schema_version": version["schema_version"],
        "run_id": version["run_pinned"],
        "mode": "connections",
        "connections": {"nodes": nodes, "edges": edges},
        "by_year": None,
    }


def _answer_with(page: Any, payload: dict[str, Any]) -> None:
    page.route(
        re.compile(r"/api/v1/overlap\?mode=connections$"),
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)
        ),
    )


def test_connections_draws_a_dot_per_experiment_and_each_kind_of_line(
    browser: Any, served: str
) -> None:
    # A payload under the 400-line cap, so every pair is drawn (the run's own is over it).
    payload = _invented_lines(served)
    stub = payload["connections"]
    kinds: dict[str, int] = {}
    for edge in stub["edges"]:
        for kind in edge["kinds"]:
            kinds[kind] = kinds.get(kind, 0) + 1
    page = _page(browser)
    try:
        _answer_with(page, payload)
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        assert page.get_attribute(".ov-mode[data-mode='connections']", "aria-pressed") == "true"
        assert int(drawn["nodes"]) == len(stub["nodes"]), "a dot per experiment"
        assert int(drawn["pairs"]) == len(stub["edges"]), "a line per pair that overlaps"
        assert json.loads(drawn["kinds"]) == kinds, "each kind of line the route gives"
        legend = page.text_content(".ov-legend")
        for words in ("Shared words", "Same workflow", "Same instrument", "Repeat of", "Copy"):
            assert words in legend, f"the legend names {words}"
        assert "Made from" in legend
        page.click(".ov-list summary")
        assert page.locator(".ov-list tbody tr").count() == len(stub["edges"]), (
            "the list under the drawing holds the same lines"
        )
        # A kind unticked leaves the drawing.
        first = next(iter(kinds))
        page.uncheck(f".ov-kinds input[data-kind='{first}']")
        page.evaluate(FRAMES)
        after = json.loads(page.get_attribute(".ov-canvas", "data-kinds"))
        assert first not in after
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_the_run_s_own_connections_draw_a_dot_per_experiment_and_at_most_400_lines(
    browser: Any, served: str
) -> None:
    real = _read(served, "/api/v1/overlap?mode=connections")["connections"]
    # A kind that joins more than half of all pairs starts unticked and is not drawn.
    every = len(real["nodes"]) * (len(real["nodes"]) - 1) / 2
    joined: dict[str, int] = {}
    for edge in real["edges"]:
        for kind in edge["kinds"]:
            joined[kind] = joined.get(kind, 0) + 1
    total = sum(count for count in joined.values() if count <= every / 2)
    page = _page(browser)
    try:
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        assert int(drawn["nodes"]) == len(real["nodes"]), "a dot per experiment"
        assert int(drawn["lines"]) == min(total, 400), "every line, or the 400 strongest"
        assert sum(json.loads(drawn["kinds"]).values()) == int(drawn["lines"])
        if total > 400:
            assert f"Showing the 400 strongest of {total:,} lines" in page.text_content(
                ".ov-caption"
            )
        assert not page.errors, page.errors
    finally:
        page.context.close()


def _crowded_lines(base: str) -> dict[str, Any]:
    """Five invented experiments, ten pairs: one workflow joins eight pairs and one instrument all
    ten; shared words, a repeat, a copy and a made-from each join one or two."""
    version = _read(base, "/api/v1/version")
    nodes = [
        {
            "experiment_id": f"crowd_{index}",
            "label": f"d3 crowd {index}",
            "area": "d3 area",
            "area_kind": "folder",
            "theme": "d3 theme",
            "start": None,
            "end": None,
            "completeness": None,
            "repeats": 1,
        }
        for index in range(5)
    ]
    wanted = {"shared_words": 2, "repeat_of": 1, "copy": 1, "made_from": 1}
    edges = []
    number = 0
    for a in range(5):
        for b in range(a + 1, 5):
            kinds = ["instrument"]
            if number < 8:
                kinds.append("workflow")
            for kind, count in wanted.items():
                if number < count:
                    kinds.append(kind)
            edges.append(
                {
                    "a": f"crowd_{a}",
                    "b": f"crowd_{b}",
                    "kinds": kinds,
                    "weight": round(1 - number / 11, 6),
                    "evidence": [{"kind": kind, "words": ["d3word"], "count": 1} for kind in kinds],
                }
            )
            number += 1
    return {
        "api_version": "1.0",
        "schema_version": version["schema_version"],
        "run_id": version["run_pinned"],
        "mode": "connections",
        "connections": {"nodes": nodes, "edges": edges},
        "by_year": None,
    }


def test_a_kind_of_line_joining_most_pairs_starts_unticked_with_its_count(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    try:
        _answer_with(page, _crowded_lines(served))
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        ticked = {
            kind: page.is_checked(f".ov-kinds input[data-kind='{kind}']")
            for kind in (
                "shared_words",
                "workflow",
                "instrument",
                "repeat_of",
                "made_from",
                "copy",
            )
        }
        assert ticked == {
            "shared_words": True,
            "workflow": False,
            "instrument": False,
            "repeat_of": True,
            "made_from": True,
            "copy": True,
        }
        legend = page.inner_text(".ov-kinds")
        assert "Same workflow (8), joins most pairs" in legend
        assert "Same instrument (10), joins almost every pair" in legend
        assert "Shared words (2)" in legend and "joins" not in legend.split("Shared words")[1][:20]
        assert set(json.loads(drawn["kinds"])) == {"shared_words", "repeat_of", "made_from", "copy"}
        # A tick draws the kind.
        page.check(".ov-kinds input[data-kind='workflow']")
        page.evaluate(FRAMES)
        assert json.loads(page.get_attribute(".ov-canvas", "data-kinds"))["workflow"] == 8
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_a_line_opens_to_its_evidence_and_then_its_files(browser: Any, served: str) -> None:
    payload = _invented_lines(served)
    first, second = payload["connections"]["edges"][0]["a"], payload["connections"]["edges"][0]["b"]
    files = {
        experiment: _read(served, f"/api/v1/experiments/files?experiment={experiment}&limit=40")
        for experiment in (first, second)
    }
    page = _page(browser)
    try:
        _answer_with(page, payload)
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        assert int(drawn["nodes"]) == 3 and int(drawn["pairs"]) == 2
        page.click(".ov-list summary")
        page.click(".ov-list tbody tr:first-child button")
        page.wait_for_selector("#detail .ov-card[data-card='pair']", timeout=HANG_GUARD_MS)
        evidence = page.text_content("#detail .ov-evidence")
        assert f"Shared words: {', '.join(SHARED)}" in evidence
        assert "Repeat of" in evidence
        assert page.locator("#detail .ov-filelist li").count() == 0, (
            "the files wait until they are asked for"
        )
        page.click("#detail button[data-help='overlap.files']")
        page.wait_for_selector("#detail .ov-filegroup", timeout=HANG_GUARD_MS)
        settled(page)
        for experiment, page_of_files in files.items():
            group = page.locator(f"#detail .ov-filegroup[data-experiment='{experiment}']")
            names = sorted(group.locator(".ov-filelist button").all_text_contents())
            assert names == sorted(row["name"] for row in page_of_files["rows"]), (
                "the files of each experiment, from the files route"
            )
        # A file opens its record, with the way back to the line.
        page.locator("#detail .ov-filelist button").first.click()
        page.wait_for_selector("#detail >> text=back to the overlap", timeout=HANG_GUARD_MS)
        # The workflow line's evidence is its stages in order.
        page.click(".ov-list tbody tr:nth-child(2) button")
        page.wait_for_selector("#detail .ov-evidence", timeout=HANG_GUARD_MS)
        assert f"Same workflow: {', then '.join(STAGES)}" in page.text_content(
            "#detail .ov-evidence"
        )
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_a_dot_clicked_on_the_drawing_opens_the_experiment_s_story(
    browser: Any, served: str
) -> None:
    payload = _invented_lines(served)
    node = payload["connections"]["nodes"][2]
    story = _read(served, f"/api/v1/experiments/story?id={node['experiment_id']}")
    page = _page(browser)
    try:
        _answer_with(page, payload)
        page.goto(f"{served}#overlap")
        _drawn(page)
        page.locator(".ov-canvas").scroll_into_view_if_needed()
        at = page.evaluate(
            """id => {
              const box = document.querySelector('.ov-canvas').getBoundingClientRect();
              const where = window.ingenceOverlap.drawing().getElementById(id).renderedPosition();
              return {x: box.left + where.x, y: box.top + where.y};
            }""",
            node["experiment_id"],
        )
        page.mouse.click(at["x"], at["y"])
        page.wait_for_selector(
            f"#detail .ov-card[data-experiment='{node['experiment_id']}'] .st-story",
            timeout=HANG_GUARD_MS,
        )
        assert page.text_content("#detail .ol-title").strip() == node["label"]
        assert page.locator("#detail .st-dayline").count() == len(story["days"])
        assert page.locator("#detail .st-day").count() == len(story["days"]), "the day strip"
        times = len(story["repeats"]) + 1
        said = "Seen once" if times == 1 else f"Repeated {times} times"
        assert page.text_content("#detail .st-repeat-count").strip() == said
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_the_overview_s_highlight_dims_the_rest_until_cleared(browser: Any, served: str) -> None:
    stub = _read(served, "/api/v1/overlap?mode=connections")["connections"]
    named = [stub["nodes"][0]["experiment_id"]]
    page = _page(browser)
    try:
        page.goto(f"{served}#experiments")
        # As the Overview's Show these keeps it (overview.js): {kind, id, label, experiments}.
        page.evaluate(
            "ids => sessionStorage.setItem('ingence.highlight', JSON.stringify("
            "{kind: 'goal', id: 'd3-goal', label: 'd3 goal words', experiments: ids}))",
            named,
        )
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        assert drawn["highlighted"] == "1"
        said = page.text_content(".ov-highlight")
        assert "Highlighted: 1 experiment from the Overview, for d3 goal words" in said
        page.click(".ov-highlight button")
        page.wait_for_function(
            "() => !document.querySelector('.ov-highlight')"
            " && document.querySelector('.ov-canvas[data-highlighted]')"
            " && document.querySelector('.ov-canvas').dataset.highlighted === '0'",
            timeout=HANG_GUARD_MS,
        )
        assert page.evaluate("sessionStorage.getItem('ingence.highlight')") is None
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_by_year_runs_from_areas_to_experiments_to_one_story(browser: Any, served: str) -> None:
    areas = sorted({row["person"] for row in _read(served, "/api/v1/completeness")["experiments"]})
    page = _page(browser)
    try:
        page.goto(f"{served}#years")
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        page.evaluate("window.ingenceYears.go([{kind: 'project'}])")
        assert page.get_attribute(".ov-mode[data-mode='by_year']", "aria-pressed") == "true"
        labels = page.eval_on_selector_all(
            ".yrow .ylabel", "ns => ns.map(n => n.textContent.trim())"
        )
        assert sorted(labels) == areas, "a row per area"
        assert "A row per area" in page.text_content(".years .yhint")
        page.click(".yrow")
        page.wait_for_function(
            "document.querySelector('#detail .ykicker')"
            " && document.querySelector('#detail .ykicker').textContent.startsWith('An area')"
        )
        page.dblclick(".yrow")
        page.wait_for_selector(".years .yhead-row", timeout=HANG_GUARD_MS)
        assert page.text_content(".ycrumbs button[aria-current='location']").strip() == labels[0]
        assert page.locator(".years .yrow").count() > 0, "an area opens to its experiments"
        assert "A row per experiment" in page.text_content(".years .yhint")
        experiment = page.get_attribute(".years .yrow", "data-index")
        assert experiment == "0"
        page.dblclick(".years .yrow")
        page.wait_for_selector(".years .ystory .st-story", timeout=HANG_GUARD_MS)
        story_id = page.get_attribute(".years .ystory .st-story", "data-story")
        story = _read(served, f"/api/v1/experiments/story?id={story_id}")
        assert page.locator(".years .st-dayline").count() == len(story["days"])
        # The files are one choice away, from the chips above the story.
        page.click(".years .ychip >> text=folders")
        page.wait_for_selector(".years .ybar input[type='search']", timeout=HANG_GUARD_MS)
        assert "Rows are folders" in page.text_content(".years .yhint")
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_years_opens_by_year_on_a_load_and_from_a_link(browser: Any, served: str) -> None:
    page = _page(browser)
    try:
        page.goto(f"{served}#years")
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        assert page.url.endswith("#overlap"), "the address names the view, Overlap"
        assert page.get_attribute(".ov-mode[data-mode='by_year']", "aria-pressed") == "true"
        page.click(".ov-mode[data-mode='connections']")
        _drawn(page)
        page.evaluate("location.hash = '#years'")
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        assert page.get_attribute(".ov-mode[data-mode='by_year']", "aria-pressed") == "true"
        # The mode is kept for the tab: a reload of #overlap stays in By year.
        page.reload()
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        assert not page.errors, page.errors
    finally:
        page.context.close()


def _name(base: str) -> None:
    """Set the name every write is saved under, as a person does at their first answer."""
    request = urllib.request.Request(
        f"{base}api/decider",
        data=json.dumps({"decider": "alice"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


def _posts(page: Any, path: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    page.on(
        "request",
        lambda request: (
            seen.append(json.loads(request.post_data or "{}"))
            if request.method == "POST" and request.url.endswith(path)
            else None
        ),
    )
    return seen


def test_turn_off_shows_the_dry_run_then_posts_the_confirm(browser: Any, served: str) -> None:
    workflows = _read(served, "/api/v1/workflows")["workflows"]
    assert workflows, "the route lists a workflow"
    chosen = workflows[0]
    _name(served)
    page = _page(browser)
    try:
        bodies = _posts(page, "/api/workflows/withdraw")
        page.goto(f"{served}#experiments")
        card = f".exp-workflows .wf-card[data-workflow='{chosen['id']}']"
        page.wait_for_selector(card, timeout=HANG_GUARD_MS)
        page.click(f"{card} button[data-help='workflow.turn_off']")
        page.wait_for_selector(f"{card} .wf-preview", timeout=HANG_GUARD_MS)
        assert bodies == [{"id": chosen["id"], "dry_run": True}], "the first post is a dry run"
        assert "would change" in page.text_content(f"{card} .wf-preview")
        page.click(f"{card} .wf-preview button[data-help='workflow.turn_off_confirm']")
        page.wait_for_selector(f"{card} .wf-done", timeout=HANG_GUARD_MS)
        assert bodies[1] == {"id": chosen["id"], "actor": "alice"}, (
            "the confirm posts without dry_run, with the name"
        )
        assert "This change waits for Re-run." in page.text_content(f"{card} .wf-done")
        assert len(bodies) == 2
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_change_edits_the_types_then_shows_the_dry_run_then_posts(
    browser: Any, served: str
) -> None:
    workflows = _read(served, "/api/v1/workflows")["workflows"]
    chosen = next(workflow for workflow in workflows if len(workflow["stages"]) >= 2)
    stages = [stage["stage"] for stage in chosen["stages"]]
    _name(served)
    page = _page(browser)
    try:
        bodies = _posts(page, "/api/workflows")
        page.goto(f"{served}#experiments")
        card = f".exp-workflows .wf-card[data-workflow='{chosen['id']}']"
        page.wait_for_selector(card, timeout=HANG_GUARD_MS)
        assert page.text_content(f"{card} .wf-sentence").startswith(
            "Suggested workflow:" if chosen["origin"] == "suggested" else "Your workflow:"
        )
        page.click(f"{card} button[data-help='workflow.change']")
        page.wait_for_selector(f"{card} .wf-form", timeout=HANG_GUARD_MS)
        # The second stage moves up; the first type of the (new) first stage is removed.
        page.click(f"{card} .wf-stage[data-stage='{stages[1]}'] button[data-help='workflow.move']")
        page.locator(f"{card} .wf-stage").first.locator(".wf-x").first.click()
        page.click(f"{card} .wf-form button[type='submit']")
        page.wait_for_selector(f"{card} .wf-preview", timeout=HANG_GUARD_MS)
        assert len(bodies) == 1 and bodies[0]["dry_run"] is True
        sent = bodies[0]["workflow"]
        assert [stage["stage"] for stage in sent["stages"]][:2] == [stages[1], stages[0]]
        assert sent["stages"][0]["types"] == chosen["stages"][1]["types"][1:]
        assert sent["name"] == chosen["name"]
        page.click(f"{card} .wf-preview button[data-help='workflow.change_confirm']")
        page.wait_for_selector(f"{card} .wf-done", timeout=HANG_GUARD_MS)
        assert len(bodies) == 2 and "dry_run" not in bodies[1]
        assert bodies[1]["workflow"] == sent
        assert bodies[1]["actor"] == "alice"
        assert "This change waits for Re-run." in page.text_content(f"{card} .wf-done")
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_an_experiments_row_opens_its_story_under_it(browser: Any, served: str) -> None:
    page = _page(browser)
    try:
        page.goto(f"{served}#experiments")
        page.wait_for_selector(".exp-row .exp-open", timeout=HANG_GUARD_MS)
        row = page.get_attribute(".exp-row", "data-id")
        page.click(".exp-row .exp-open")
        page.wait_for_selector(
            f".exp-story-row[data-story-of='{row}'] .st-story", timeout=HANG_GUARD_MS
        )
        assert page.get_attribute(".exp-row .exp-open", "aria-expanded") == "true"
        assert page.locator(".exp-row").count() > 1, "the table stays"
        page.click(".exp-row .exp-open")
        assert page.locator(".exp-story-row").count() == 0, "a second click closes it"
        assert not page.errors, page.errors
    finally:
        page.context.close()


def _sight(page: Any) -> tuple[float, float, float]:
    zoom, pan = page.eval_on_selector(".ov-canvas", "e => [e.dataset.zoom, e.dataset.pan]")
    x, y = (float(part) for part in pan.split())
    return float(zoom), x, y


def test_a_plain_wheel_scrolls_the_page_and_only_ctrl_zooms_the_drawing(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    try:
        page.goto(f"{served}#overlap")
        _drawn(page)
        before = _sight(page)
        upright = page.evaluate(WHEEL, [".ov-canvas", {"deltaX": 0, "deltaY": 120}])
        page.evaluate(FRAMES)
        assert upright is True, "a plain wheel is left to the page"
        assert _sight(page) == before, "and the drawing does not move"
        pinch = page.evaluate(WHEEL, [".ov-canvas", {"deltaX": 0, "deltaY": 120, "ctrlKey": True}])
        page.evaluate(FRAMES)
        assert pinch is False, "Ctrl + wheel holds the page still"
        zoomed = _sight(page)
        assert zoomed[0] < before[0], "Ctrl + wheel down zooms out"
        sideways = page.evaluate(WHEEL, [".ov-canvas", {"deltaX": 120, "deltaY": 4}])
        page.evaluate(FRAMES)
        assert sideways is False
        panned = _sight(page)
        assert panned[0] == pytest.approx(zoomed[0]) and panned[1] != zoomed[1]
        assert panned[2] == zoomed[2], "a sideways wheel pans sideways only"
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_stability_is_a_section_of_its_own(browser: Any, served: str) -> None:
    page = _page(browser)
    try:
        page.goto(f"{served}#years")
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        assert page.locator(".years .yseedpics, .years .ystab").count() == 0, (
            "the seed pictures left the main view"
        )
        assert "Seeds agree" not in page.text_content("#detail")
        page.keyboard.press("o")
        page.wait_for_selector(".ystability .ystab", timeout=HANG_GUARD_MS)
        assert page.url.endswith("#stability")
        assert page.locator(".ystability .yseedpic").count() >= 1
        assert not page.errors, page.errors
    finally:
        page.context.close()


# -- more cases ------------------------------------------------------------------------------------


def _many_lines(base: str, pairs: int) -> dict[str, Any]:
    """An invented payload of 40 experiments in a ring of ``pairs`` pairs, two kinds of line each,
    and one experiment with no line at all."""
    version = _read(base, "/api/v1/version")
    nodes = [
        {
            "experiment_id": f"node_{index:03d}",
            "label": f"d3 node {index:03d}",
            "area": "d3 area",
            "area_kind": "folder",
            "theme": f"d3 theme {index % 3}",
            "start": None,
            "end": None,
            "completeness": None,
            "repeats": 1,
        }
        for index in range(41)
    ]
    edges = []
    for number in range(pairs):
        a, b = number % 40, (number + 1 + number // 40) % 40
        edges.append(
            {
                "a": f"node_{a:03d}",
                "b": f"node_{b:03d}",
                "kinds": ["shared_words", "workflow"],
                "weight": round(1 - number / (pairs + 1), 6),
                "evidence": [
                    {"kind": "shared_words", "words": ["d3word"], "count": 1},
                    {"kind": "workflow", "words": STAGES, "count": 2},
                ],
            }
        )
    return {
        "api_version": "1.0",
        "schema_version": version["schema_version"],
        "run_id": version["run_pinned"],
        "mode": "connections",
        "connections": {"nodes": nodes, "edges": edges},
        "by_year": None,
    }


def test_the_cap_counts_the_lines_drawn_and_an_experiment_with_no_line_is_listed(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    try:
        _answer_with(page, _many_lines(served, 300))
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        assert drawn["lines"] == "400", "at most 400 lines are drawn, one per kind of a pair"
        assert json.loads(drawn["kinds"]) == {"shared_words": 200, "workflow": 200}
        assert drawn["pairs"] == "200", "the strongest pairs, each with both its lines"
        assert "Showing the 400 strongest of 600 lines" in page.text_content(".ov-caption")
        assert page.get_attribute(".ov-canvas", "role") == "img"
        page.click(".ov-list summary")
        alone = page.locator(".ol-alone button")
        assert alone.all_text_contents() == ["d3 node 040"], "the experiment with no line"
        alone.first.click()
        page.wait_for_selector(
            "#detail .ov-card[data-experiment='node_040']", timeout=HANG_GUARD_MS
        )
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_a_theme_the_overview_sends_sets_the_filter_once(browser: Any, served: str) -> None:
    stub = _read(served, "/api/v1/overlap?mode=connections")["connections"]
    theme = stub["nodes"][0]["theme"]
    count = sum(1 for node in stub["nodes"] if node["theme"] == theme)
    page = _page(browser)
    try:
        page.goto(f"{served}#experiments")
        page.evaluate("t => sessionStorage.setItem('ingence.overlap.theme', t)", theme)
        page.goto(f"{served}#overlap")
        drawn = _drawn(page)
        assert drawn["nodes"] == str(count), "only the theme's experiments"
        assert page.input_value("select[data-help='overlap.filter_theme']") == theme
        assert page.evaluate("sessionStorage.getItem('ingence.overlap.theme')") is None
        page.evaluate("window.ingenceOverlap.setTheme('')")
        page.wait_for_function(
            "n => document.querySelector('.ov-canvas[data-nodes]')"
            " && document.querySelector('.ov-canvas').dataset.nodes === n",
            arg=str(len(stub["nodes"])),
            timeout=HANG_GUARD_MS,
        )
        assert not page.errors, page.errors
    finally:
        page.context.close()


def test_a_control_that_redraws_its_part_keeps_the_focus(browser: Any, served: str) -> None:
    stub = _read(served, "/api/v1/overlap?mode=connections")["connections"]
    page = _page(browser)
    try:
        page.goto(f"{served}#experiments")
        page.evaluate(
            "ids => sessionStorage.setItem('ingence.highlight',"
            " JSON.stringify({experiments: ids}))",
            [stub["nodes"][0]["experiment_id"]],
        )
        page.goto(f"{served}#overlap")
        _drawn(page)
        # Clear highlight: the bar goes, the focus goes to the line that says what is drawn.
        page.focus(".ov-highlight button")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.activeElement"
            " && document.activeElement.classList.contains('ov-caption')",
            timeout=HANG_GUARD_MS,
        )
        # A mode button: the bar is drawn again, the same button keeps the focus.
        page.focus(".ov-mode[data-mode='by_year']")
        page.keyboard.press("Enter")
        page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        assert page.evaluate("document.activeElement.dataset.mode") == "by_year"
        # The workflow form: Down moves the stage, and its Up keeps the focus at the bottom.
        workflows = _read(served, "/api/v1/workflows")["workflows"]
        chosen = next(workflow for workflow in workflows if len(workflow["stages"]) >= 2)
        first = chosen["stages"][0]["stage"]
        page.goto(f"{served}#experiments")
        card = f".exp-workflows .wf-card[data-workflow='{chosen['id']}']"
        page.wait_for_selector(card, timeout=HANG_GUARD_MS)
        page.click(f"{card} button[data-help='workflow.change']")
        page.wait_for_selector(f"{card} .wf-form", timeout=HANG_GUARD_MS)
        page.focus(f"{card} [data-focus='down:{first}']")
        page.keyboard.press("Enter")
        held = page.evaluate("document.activeElement.dataset.focus")
        assert held in {f"down:{first}", f"up:{first}"}, held
        assert not page.errors, page.errors
    finally:
        page.context.close()
