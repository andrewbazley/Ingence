"""Ask, and the assumptions list in Checks.

Every test reads what a person sees, in a real browser, on a small invented project with a run.
Ask's and the assumptions' routes answer the 0.4.0 contract. A test reads each route's own answer
and checks the page against it, so the tests hold whatever the run holds. Where a test needs a
part that this project's answer does not hold (filters, an unknown word, an empty answer), it takes
the route's answer in the browser and changes that part to invented values of its own.

Skipped where the ``browser`` extra or a browser is missing, as every browser test is.
"""

# ruff: noqa: E501  (the JavaScript each test runs in the page is kept whole, as the page reads it)

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any, NamedTuple

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
    from collections.abc import Iterator
    from pathlib import Path

sync_api = need("playwright.sync_api", "the browser extra is not installed")

STARTED = "2026-10-21T20:00:00Z"
#: The quick checks and the first-visit tour, both seen: the page opens at once.
SEEN = """(() => {
  const read = Storage.prototype.getItem;
  Storage.prototype.getItem = function (key) {
    return String(key).startsWith('ingence.quickchecks.v1.') ? 'seen' : read.call(this, key);
  };
  try { localStorage.setItem('ingence.tour.v1', 'seen'); } catch (error) { /* refused */ }
})();"""
#: The story component, standing in: each experiment opened is kept, and the component's own
#: script cannot replace it (its assignment is ignored), so the test reads what Ask asked of it.
STORY = """(() => {
  window.__stories = [];
  Object.defineProperty(window, 'ingenceStory', {configurable: true,
    get() { return {render(container, id) { window.__stories.push(id); container.textContent = 'The story of ' + id; return Promise.resolve(); }}; },
    set() {}});
})();"""
#: No story component at all, whatever is loaded: Ask opens the experiment as Experiments does.
NO_STORY = """(() => {
  Object.defineProperty(window, 'ingenceStory', {configurable: true, get() { return undefined; }, set() {}});
})();"""
#: Every dialog the page opens, kept: This is wrong must open none.
DIALOGS = """(() => {
  window.__dialogs = 0;
  const show = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function () { window.__dialogs += 1; return show.call(this); };
})();"""
#: The kinds of assumption in the order Checks groups them, with their headings.
KINDS = [
    ("file_type", "File types"),
    ("writer", "Writers"),
    ("domain", "Word meanings"),
    ("spelling", "Spellings"),
    ("area_kind", "Area kinds"),
    ("workflow", "Workflows"),
    ("stage", "Stages"),
    ("repeat", "Repeats"),
]


class Served(NamedTuple):
    address: str
    project: Path


def _build(base: Path) -> Path:
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "Example_Lab"
    people = {
        "alice": ["2024-03-15 HeLa DAPI", "2024-04-02 HeLa GFP"],
        "Scope_1": ["Plate_A titration"],
    }
    for person, experiments in people.items():
        for experiment in experiments:
            folder = pool / person / experiment
            folder.mkdir(parents=True)
            (folder / f"{experiment.replace(' ', '_')}_raw.csv").write_text("a,b\n1,2\n")
            (folder / f"{experiment.replace(' ', '_')}_analysis.csv").write_text("a,b\n1,2\n")
            (folder / "notes.txt").write_text("plate notes")
    project = Project.create(base / "project", created_at=STARTED, roots=[pool], name=None)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at=STARTED)
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at=STARTED)
    return project.path


def _get(address: str, path: str) -> Any:
    with urllib.request.urlopen(f"{address.rstrip('/')}{path}", timeout=30) as answer:
        return json.loads(answer.read())


def _post(address: str, route: str, body: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        address.rstrip("/") + route,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as answer:
        return json.loads(answer.read())


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    base = tmp_path_factory.mktemp("d7ask")
    project = _build(base)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        _post(address, "/api/decider", {"decider": "alice"})
        yield Served(address, project)
    finally:
        stop(process)


def _page(browser: Any, *inits: str, width: int = 1360) -> Any:
    context = browser.new_context(viewport={"width": width, "height": 900})
    for init in (SEEN, *inits):
        context.add_init_script(init)
    return context.new_page()


def _asked(question: str) -> str:
    return "/api/v1/ask?" + urllib.parse.urlencode({"q": question})


def _ask(page: Any, address: str, question: str) -> None:
    page.goto(f"{address}#ask?{urllib.parse.urlencode({'q': question})}")
    page.wait_for_selector("[data-ask-view]", timeout=HANG_GUARD_MS)
    settled(page)


def _words(words: list[Any]) -> str:
    return ", ".join(str(word) for word in words)


# ---------------------------------------------------------------------------
# 1. Ask's results
# ---------------------------------------------------------------------------


def test_ask_says_what_was_read_left_out_and_not_known_the_words_included_and_the_filters(
    browser: Any, served: Served
) -> None:
    question = "which HeLa timecourses with zorbit in 2024"
    changed: dict[str, Any] = {}

    def widened(route: Any) -> None:
        answer = route.fetch()
        held = answer.json()
        held["read"]["unknown"] = ["zorbit"]
        held["read"]["kept"] = [word for word in held["read"]["kept"] if word != "zorbit"]
        held["expanded"] = [
            *held["expanded"],
            {"word": "nuclear stain", "to": ["DAPI", "Hoechst"], "how": "a class"},
        ]
        held["filters"] = {"years": [2024], "types": ["TIFF image"], "areas": ["Scope_1"]}
        changed.update(held)
        route.fulfill(response=answer, json=held)

    page = _page(browser)
    page.route("**/api/v1/ask?*", widened)
    _ask(page, served.address, question)
    assert page.text_content("h2[data-ask-view]") == f"Ask: {question}"
    read = changed["read"]
    expected = f"Read: {_words(read['kept'])}."
    if read["dropped"]:
        expected += f" Left out: {_words(read['dropped'])}."
    expected += " Not known: zorbit."
    assert page.inner_text('[data-part="read"] [data-read]') == expected
    assert (
        page.inner_text('[data-part="read"] [data-filters]')
        == "Years: 2024. File types: TIFF image. Areas: Scope_1."
    )
    lines = page.locator('[data-part="read"] [data-expansion]')
    assert lines.count() == len(changed["expanded"])
    for index, expansion in enumerate(changed["expanded"]):
        assert lines.nth(index).inner_text() == (
            f"{expansion['word']}: {_words(expansion['to'])} ({expansion['how']})"
        )
    page.context.close()


def test_tiers_are_kept_apart_and_same_theme_is_listed_apart_as_suggestions(
    browser: Any, served: Served
) -> None:
    question = "HeLa"
    answer = _get(served.address, _asked(question))
    page = _page(browser)
    _ask(page, served.address, question)
    for tier, heading in (("named", "Named"), ("associated", "Associated")):
        ids = [
            item["experiment_id"] for item in answer["experiments"] if item["score_tier"] == tier
        ]
        group = page.locator(f'[data-part="experiments"] .ask-tier[data-tier="{tier}"]')
        if not ids:
            assert group.count() == 0, tier
            continue
        assert group.locator("h4").inner_text() == heading
        shown = group.locator("li[data-experiment]")
        assert [shown.nth(n).get_attribute("data-experiment") for n in range(shown.count())] == ids
        for item in [item for item in answer["experiments"] if item["score_tier"] == tier]:
            line = group.locator(f'li[data-experiment="{item["experiment_id"]}"]')
            assert line.locator("button.ask-open").inner_text() == item["label"]
            matched = line.locator(".ask-matched").all_inner_texts()
            for word in item["matched"]:
                assert any(word["word"] in text and word["how"] in text for text in matched)
            facts = line.locator(".ask-facts").inner_text()
            assert (item["dates"]["first"] or "Undated") in facts
            if item["completeness"] is None:
                assert "No raw files found" in facts
            else:
                assert f"Raw files analyzed: {round(item['completeness'] * 100)}%" in facts
    # Named comes before Associated, and the suggestions after both, in their own part.
    order = page.eval_on_selector_all(
        "[data-part='experiments'] .ask-tier, [data-part='same-theme']",
        "nodes => nodes.map(n => n.dataset.tier || n.dataset.part)",
    )
    # The suggestions: what the route tiers as same theme, then its same-theme list, each once.
    tiered = [i["experiment_id"] for i in answer["experiments"] if i["score_tier"] == "same_theme"]
    matched_ids = {item["experiment_id"] for item in answer["experiments"]}
    listed = [
        i["experiment_id"] for i in answer["same_theme"] if i["experiment_id"] not in matched_ids
    ]
    apart = list(dict.fromkeys(tiered + listed))
    tiers = [
        t
        for t in ("named", "associated")
        if any(i["score_tier"] == t for i in answer["experiments"])
    ]
    assert order == tiers + (["same-theme"] if apart else [])
    if apart:
        part = page.locator('[data-part="same-theme"]')
        assert part.locator("h3").inner_text() == "Same theme"
        shown = part.locator("li[data-experiment]")
        assert [
            shown.nth(n).get_attribute("data-experiment") for n in range(shown.count())
        ] == apart
        for item in answer["same_theme"]:
            if item["experiment_id"] in listed:
                line = part.locator(f'li[data-experiment="{item["experiment_id"]}"]')
                assert line.locator(".ask-suggested").inner_text() == (
                    f"Suggestion: shares the theme {item['via_theme']}."
                )
        # No suggestion is listed among the matches.
        assert (
            page.locator('[data-part="experiments"] li[data-experiment="' + apart[0] + '"]').count()
            == 0
        )
    page.context.close()


def test_save_as_goal_posts_the_question_and_says_what_the_engine_said(
    browser: Any, served: Served
) -> None:
    question = "Plate_A titrations"
    page = _page(browser)
    # The write is answered here, so the project this file shares keeps no goal.
    posted: list[Any] = []

    def goals(route: Any) -> None:
        if route.request.method != "POST":
            route.continue_()
            return
        posted.append((json.loads(route.request.post_data or "{}"), route.request.headers))
        route.fulfill(
            status=201,
            content_type="application/json",
            body=json.dumps(
                {"goal": {"id": "goal_ask", "text": question}, "said": "", "recorded": True}
            ),
        )

    page.route("**/api/goals", goals)
    _ask(page, served.address, question)
    button = page.locator('button[data-ask="save-goal"]')
    button.focus()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.querySelector('.ask-actions .ask-said').textContent !== ''",
        timeout=HANG_GUARD_MS,
    )
    assert [body for body, _ in posted] == [{"text": question}]
    assert posted[0][1].get("x-ingence-write") == "1"
    assert page.inner_text(".ask-actions .ask-said") == "Saved as a goal. The Overview shows it."
    # The pressed button keeps the focus, held: a second press posts nothing.
    assert button.get_attribute("aria-disabled") == "true"
    assert page.evaluate("document.activeElement.dataset.ask") == "save-goal"
    page.keyboard.press("Enter")
    settled(page)
    assert len(posted) == 1
    page.context.close()


def test_a_question_nothing_matches_says_so_and_what_to_try(browser: Any, served: Served) -> None:
    def nothing(route: Any) -> None:
        answer = route.fetch()
        held = answer.json()
        held["experiments"] = []
        held["same_theme"] = []
        route.fulfill(response=answer, json=held)

    page = _page(browser)
    page.route("**/api/v1/ask?*", nothing)
    _ask(page, served.address, "glorbex")
    card = page.locator('#list .state-card[data-state="empty"]')
    card.wait_for(timeout=HANG_GUARD_MS)
    assert card.locator(".state-sentence").inner_text() == "No experiment matches these words."
    assert "Try a broader word, or check the spelling." in card.inner_text()
    assert page.locator('[data-part="same-theme"]').count() == 0
    page.context.close()


def test_opening_an_experiment_opens_its_story_beside_the_list(
    browser: Any, served: Served
) -> None:
    # A word the project's names hold, so the route answers with its own experiments.
    question = "HeLa"
    answer = _get(served.address, _asked(question))
    assert answer["experiments"], "the route finds the project's HeLa experiments"
    first = answer["experiments"][0]
    page = _page(browser, STORY)
    _ask(page, served.address, question)
    page.click(
        f'[data-part="experiments"] li[data-experiment="{first["experiment_id"]}"] button.ask-open'
    )
    page.wait_for_function("window.__stories.length === 1", timeout=HANG_GUARD_MS)
    assert page.evaluate("window.__stories") == [first["experiment_id"]]
    assert page.text_content("#detail .ask-story-title") == first["label"]
    assert page.inner_text("#detail .ask-story") == f"The story of {first['experiment_id']}"
    assert page.evaluate("state.view") == "ask"
    page.context.close()


def test_without_the_story_an_experiment_opens_as_experiments_opens_it(
    browser: Any, served: Served
) -> None:
    # An experiment By year does not list opens Experiments: the answer names one this project lacks.
    question = "HeLa"
    answer = _get(served.address, _asked(question))
    assert answer["experiments"], "the route finds the project's HeLa experiments"
    answer["experiments"][0] = {
        **answer["experiments"][0],
        "experiment_id": "entry_not_in_this_project",
        "label": "Plate_Z invented",
    }
    page = _page(browser, NO_STORY)
    page.route(
        "**/api/v1/ask?*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(answer)
        ),
    )
    _ask(page, served.address, question)
    page.locator(
        '[data-part="experiments"] li[data-experiment="entry_not_in_this_project"] button.ask-open'
    ).click()
    page.wait_for_function("state.view === 'experiments'", timeout=HANG_GUARD_MS)
    page.context.close()


def test_without_the_story_a_listed_experiment_opens_in_overlaps_by_year(
    browser: Any, served: Served
) -> None:
    """With an experiment of this project, the fallback opens Overlap's By year at it."""
    question = "HeLa"
    rows = _get(served.address, "/api/v1/experiments?limit=50")["rows"]
    answer = _get(served.address, _asked(question))
    if not rows or not answer["experiments"]:
        pytest.skip("the project needs an experiment and the route an answer")
    real = rows[0]
    answer["experiments"][0] = {
        **answer["experiments"][0],
        "experiment_id": real["id"],
        "label": real.get("shown_name") or real["name"],
    }
    page = _page(browser, NO_STORY)
    page.route(
        "**/api/v1/ask?*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(answer)
        ),
    )
    _ask(page, served.address, question)
    page.locator(
        f'[data-part="experiments"] li[data-experiment="{real["id"]}"] button.ask-open'
    ).click()
    page.wait_for_function("state.view === 'overlap'", timeout=HANG_GUARD_MS)
    assert page.evaluate("window.ingenceOverlap.mode()") == "by_year"
    page.context.close()


def test_the_header_box_and_an_old_search_address_open_ask(browser: Any, served: Served) -> None:
    page = _page(browser)
    page.goto(f"{served.address}#search")
    page.wait_for_function("state.view === 'ask'", timeout=HANG_GUARD_MS)
    page.wait_for_selector('.ask-page[data-view="ask"]', timeout=HANG_GUARD_MS)
    assert page.text_content(".ask-page h2") == "Ask"
    box = page.locator("#search-box")
    box.fill("HeLa DAPI")
    box.press("Enter")
    page.wait_for_function("location.hash === '#ask?q=HeLa+DAPI'", timeout=HANG_GUARD_MS)
    page.wait_for_selector("[data-ask-view]", timeout=HANG_GUARD_MS)
    assert page.text_content("h2[data-ask-view]") == "Ask: HeLa DAPI"
    assert page.locator('[data-part="experiments"], #list .state-card').count() >= 1
    page.context.close()


# ---------------------------------------------------------------------------
# 2. The assumptions, in Checks
# ---------------------------------------------------------------------------


def _with_real_examples(served: Served) -> tuple[dict[str, Any], dict[str, str]]:
    """The assumptions route's answer, its examples pointed at this project's own experiments, so
    the inspector can read them."""
    rows = _get(served.address, "/api/v1/experiments?limit=50")["rows"]
    assert rows, "the invented project has experiments"
    answer = _get(served.address, "/api/v1/assumptions")
    names: dict[str, str] = {}
    for index, item in enumerate(answer["assumptions"]):
        row = rows[index % len(rows)]
        item["examples"] = [{"entry_id": row["id"], "label": f"Example_Lab/{row['name']}"}]
        names[row["id"]] = str(row["name"])
    return answer, names


def test_checks_lists_every_assumption_grouped_by_kind_most_files_first(
    browser: Any, served: Served
) -> None:
    items = _get(served.address, "/api/v1/assumptions")["assumptions"]
    page = _page(browser)
    page.goto(f"{served.address}#checks?show=assumptions")
    page.wait_for_selector('[data-part="all-assumptions"]', timeout=HANG_GUARD_MS)
    whole = page.locator('[data-part="all-assumptions"]')
    present = [(kind, words) for kind, words in KINDS if any(i["kind"] == kind for i in items)]
    groups = whole.locator(".ov-kind-group")
    assert [groups.nth(n).get_attribute("data-kind") for n in range(groups.count())] == [
        kind for kind, _ in present
    ]
    assert whole.locator(".ov-kind-heading").all_inner_texts() == [words for _, words in present]
    for kind, _ in present:
        expected = sorted((i for i in items if i["kind"] == kind), key=lambda i: -int(i["count"]))
        lines = whole.locator(f'.ov-kind-group[data-kind="{kind}"] .ov-assumption')
        assert [lines.nth(n).get_attribute("data-assumption") for n in range(lines.count())] == [
            i["id"] for i in expected
        ]
    assert whole.locator(".ov-assumption").count() == len(items)
    page.context.close()


def test_an_assumptions_example_opens_the_file_in_the_inspector(
    browser: Any, served: Served
) -> None:
    answer, names = _with_real_examples(served)
    page = _page(browser)
    page.route(
        "**/api/v1/assumptions",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(answer)
        ),
    )
    page.goto(f"{served.address}#checks")
    page.wait_for_selector('[data-part="all-assumptions"] .ov-example', timeout=HANG_GUARD_MS)
    item = answer["assumptions"][0]
    example = item["examples"][0]
    line = page.locator(
        f'[data-part="all-assumptions"] .ov-assumption[data-assumption="{item["id"]}"]'
    )
    button = line.locator(f'.ov-example[data-entry="{example["entry_id"]}"]')
    assert button.inner_text() == example["label"]
    button.click()
    box = line.locator(".ov-inspect")
    box.get_by_text("What the rules said about it").wait_for(timeout=HANG_GUARD_MS)
    assert names[example["entry_id"]] in box.inner_text()
    assert button.get_attribute("aria-expanded") == "true"
    # The address keeps what the page looks at; an example opened under a line is not it.
    assert "entry=" not in page.evaluate("location.search")
    # The same example again closes it.
    button.click()
    assert box.is_hidden()
    assert button.get_attribute("aria-expanded") == "false"
    page.context.close()


def test_this_is_wrong_in_checks_posts_the_reject_body_lights_rerun_and_opens_no_dialog(
    browser: Any, served: Served
) -> None:
    items = _get(served.address, "/api/v1/assumptions")["assumptions"]
    item = items[-1]
    page = _page(browser, DIALOGS)
    posted: list[dict[str, Any]] = []
    state = {"rejected": False}
    native: list[str] = []
    page.on("dialog", lambda dialog: (native.append(dialog.type), dialog.dismiss()))

    def reject(route: Any) -> None:
        posted.append(json.loads(route.request.post_data or "{}"))
        state["rejected"] = True
        route.fulfill(
            status=201,
            content_type="application/json",
            body=json.dumps(
                {
                    "id": posted[-1].get("id"),
                    "kind": item["kind"],
                    "answer": None,
                    "waits_for_rerun": True,
                    "said": "",
                    "recorded": True,
                }
            ),
        )

    def workspace(route: Any) -> None:
        answer = route.fetch()
        held = answer.json()
        if state["rejected"]:
            held["pending_rerun"] = {
                "count": 1,
                "banner": "1 correction waits for Re-run.",
                "steps": [{"words": "apply the rules again"}],
                "kinds": [],
            }
        route.fulfill(response=answer, json=held)

    page.route("**/api/assumptions/reject", reject)
    page.route("**/api/workspace/state", workspace)
    page.goto(f"{served.address}#checks")
    page.wait_for_selector('[data-part="all-assumptions"] .ov-assumption', timeout=HANG_GUARD_MS)
    settled(page)
    assert page.is_hidden("#rerun-badge")
    line = page.locator(
        f'[data-part="all-assumptions"] .ov-assumption[data-assumption="{item["id"]}"]'
    )
    line.locator("button[data-wrong]").click()
    page.wait_for_function(
        "id => [...document.querySelectorAll(`[data-part='all-assumptions'] .ov-assumption[data-assumption=\"${id}\"] .ov-said`)].some(s => s.textContent === 'Waits for Re-run')",
        arg=item["id"],
        timeout=HANG_GUARD_MS,
    )
    # The route records an answer, so the page adds the name it was given.
    assert posted == [{**item["reject"]["body"], "actor": "alice"}]
    assert line.locator("button[data-wrong]").is_disabled()
    page.wait_for_selector("#rerun-badge:not([hidden])", timeout=HANG_GUARD_MS)
    assert page.inner_text("#rerun-badge") == "1"
    assert page.evaluate("window.__dialogs") == 0
    assert page.locator("dialog[open]").count() == 0
    assert native == []
    page.context.close()
