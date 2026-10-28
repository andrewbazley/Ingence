"""The six sections, the quick checks, the Overview, Checks and the header.

Every test reads what a person sees, in a real browser, on a small invented project with a run.
The Overview's routes answer the 0.4.0 contract. A test reads each route's own answer and checks
the page against it, so the tests hold when the routes read the run itself. Where a write records
nothing in this build (the stubs say so), the test answers the write in the browser and reads what
the page does with the answer.

Skipped where the ``browser`` extra or a browser is missing, as every browser test is.
"""

# ruff: noqa: E501  (the JavaScript each test runs in the page is kept whole, as the page reads it)

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import parse_qs, urlparse

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
PAGES = ["overview", "overlap", "experiments", "checks", "history", "advanced"]
PAGE_WORDS = ["Overview", "Overlap", "Experiments", "Checks", "History", "Advanced"]
#: Every storage key of the quick checks reads as seen: the Overview draws at once.
SEEN = """(() => {
  const read = Storage.prototype.getItem;
  Storage.prototype.getItem = function (key) {
    return String(key).startsWith('ingence.quickchecks.v1.') ? 'seen' : read.call(this, key);
  };
})();"""
#: A browser that keeps nothing: every use of localStorage throws.
REFUSED = """(() => {
  Object.defineProperty(window, 'localStorage', {get() { throw new Error('refused'); }});
})();"""


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
    base = tmp_path_factory.mktemp("d1overview")
    project = _build(base)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        _post(address, "/api/decider", {"decider": "alice"})
        yield Served(address, project)
    finally:
        stop(process)


#: The first-visit tour, seen: it starts by itself on a first Overview, over the page.
TOUR_SEEN = """(() => {
  try { localStorage.setItem('ingence.tour.v1', 'seen'); } catch (error) { /* refused */ }
})();"""


def _page(browser: Any, *, init: str | None = SEEN, width: int = 1360, tour: bool = False) -> Any:
    context = browser.new_context(viewport={"width": width, "height": 900})
    if not tour:
        context.add_init_script(TOUR_SEEN)
    if init:
        context.add_init_script(init)
    return context.new_page()


def _invented_check(number: int, *, guess: bool) -> dict[str, Any]:
    """A check of an invented question, with a guess or with none (``default`` null)."""
    options = [{"value": "correct", "text": "Right"}, {"value": "wrong", "text": "Wrong"}]
    return {
        "id": f"check:class:rule_{number}:v{number}",
        "question_id": f"class:rule_{number}:v{number}",
        "kind": "audit" if guess else "question",
        "title": f"Files read as kind {number}" if guess else f"A folder marked kind{number}",
        "text": f"{number} files read as kind {number}: right. For example: exp_A/log_{number}.txt",
        "default": {"value": "correct", "text": f"Read as kind {number}"} if guess else None,
        "settles_n": 10 - number,
        "settles_unit": "files",
        "options": options,
        "answer": {
            "route": "/api/review/answer",
            "body": {
                "question_id": f"class:rule_{number}:v{number}",
                "target": f"class:rule_{number}:v{number}",
            },
        },
    }


#: Four invented checks: the second has no guess, and the quick checks leave it out.
INVENTED = [
    _invented_check(1, guess=True),
    _invented_check(2, guess=False),
    _invented_check(3, guess=True),
    _invented_check(4, guess=True),
]
#: What the quick checks ask of them: the three with a guess.
INVENTED_QUICK = [check for check in INVENTED if check["default"]]


def _checks_are(page: Any, checks: list[dict[str, Any]]) -> None:
    """Answer the checks route in the browser with these checks (the project's own checks have no
    guess in the first five places, which the quick checks leave out)."""

    def answer(route: Any) -> None:
        asked = parse_qs(urlparse(route.request.url).query).get("limit", [str(len(checks))])
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "api_version": "1.0",
                    "schema_version": "x",
                    "run_id": "run_20260101T000002Z_222222222222",
                    "total": len(checks),
                    "checks": checks[: int(asked[0])],
                }
            ),
        )

    page.route("**/api/v1/checks*", answer)


def _overview(page: Any, address: str) -> None:
    page.goto(f"{address}#overview")
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    settled(page)


def _active(page: Any) -> list[str]:
    return list(
        page.evaluate("[document.querySelector('#pages button.active').dataset.page, state.view]")
    )


# ---------------------------------------------------------------------------
# 1. Six sections, old addresses, empty sections hidden, the count line gone.
# ---------------------------------------------------------------------------


def test_six_sections_in_order_and_the_overview_opens_first(browser: Any, served: Served) -> None:
    page = _page(browser)
    page.goto(served.address)
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    tabs = page.eval_on_selector_all(
        "#pages button", "nodes => nodes.map(n => [n.dataset.page, n.textContent.trim()])"
    )
    assert [tab[0] for tab in tabs] == PAGES
    assert [tab[1] for tab in tabs] == PAGE_WORDS
    assert _active(page) == ["overview", "overview"]
    assert page.locator("#summary").count() == 0
    # The keys 1 to 6 open the six pages, as their tabs would.
    page.locator("body").press("6")
    page.wait_for_function("state.view === 'rules'", timeout=HANG_GUARD_MS)
    page.locator("body").press("4")
    page.wait_for_function("state.view === 'checks'", timeout=HANG_GUARD_MS)
    page.context.close()


@pytest.mark.parametrize(
    ("address", "page_name", "view"),
    [
        # #years opens Overlap's own view (its By year mode). Stability is Advanced's.
        ("years", "overlap", "overlap"),
        ("overlap", "overlap", "overlap"),
        ("stability", "advanced", "stability"),
        ("experiments", "experiments", "experiments"),
        ("topics", "experiments", "topics"),
        ("review", "advanced", "review"),
        ("classes", "advanced", "classes"),
        ("explorer", "advanced", "explorer"),
        ("graph", "advanced", "graph"),
        ("rules", "advanced", "rules"),
        ("programs", "advanced", "programs"),
        ("words", "advanced", "words"),
        ("ladders", "advanced", "ladders"),
        ("engine", "advanced", "rules"),
        ("parameters", "advanced", "rules"),
        ("search", "overview", "ask"),
        ("manifest", "history", "manifest"),
    ],
)
def test_old_addresses_open_their_views_on_their_new_pages(
    browser: Any, served: Served, address: str, page_name: str, view: str
) -> None:
    page = _page(browser)
    page.goto(f"{served.address}#{address}")
    page.wait_for_function(
        "([p, v]) => state.view === v && document.querySelector('#pages button.active')?.dataset.page === p",
        arg=[page_name, view],
        timeout=HANG_GUARD_MS,
    )
    assert _active(page) == [page_name, view]
    page.context.close()


def test_empty_sections_are_hidden_and_an_old_address_still_opens_one(
    browser: Any, served: Served
) -> None:
    waiting = _get(served.address, "/api/waiting")
    page = _page(browser)
    page.goto(f"{served.address}#rules")
    page.wait_for_function(
        "() => [...document.querySelectorAll('nav.sections button[data-waiting]')].every(b => b.querySelector('.count') && !/counting/.test(b.textContent))",
        timeout=HANG_GUARD_MS,
    )
    shown = page.eval_on_selector_all(
        'nav.sections[data-sections-of="advanced"] button[data-waiting]',
        "nodes => Object.fromEntries(nodes.map(n => [n.dataset.waiting, !n.hidden && n.offsetParent !== null]))",
    )
    for kind, visible in shown.items():
        assert visible == (int(waiting.get(kind, 0)) > 0), (kind, waiting.get(kind))
    quiet = [kind for kind, visible in shown.items() if not visible]
    assert quiet, "the invented project has no section with nothing waiting"
    # Advanced keeps its sections that count nothing, so the page itself shows.
    assert page.is_visible('#pages button[data-page="advanced"]')
    views = {"questions": "review"}
    kind = quiet[0]
    view = views.get(kind, kind)
    page.goto(f"{served.address}#{view}")
    page.wait_for_function("v => state.view === v", arg=view, timeout=HANG_GUARD_MS)
    assert page.is_visible(f'nav.sections button[data-view="{view}"]')
    page.click('nav.sections button[data-view="rules"]')
    page.wait_for_function("state.view === 'rules'", timeout=HANG_GUARD_MS)
    assert not page.is_visible(f'nav.sections button[data-view="{view}"]')
    page.context.close()


# ---------------------------------------------------------------------------
# 2. The quick checks.
# ---------------------------------------------------------------------------


def _answers(page: Any) -> list[dict[str, Any]]:
    """Every answer the page posts, answered in the browser so nothing reaches the Record."""
    posted: list[dict[str, Any]] = []

    def answer(route: Any) -> None:
        if route.request.method != "POST":
            route.continue_()
            return
        body = json.loads(route.request.post_data or "{}")
        posted.append(body)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "event_id": "evt_check",
                    "sequence": 1,
                    "kind": "confirm",
                    "actor": body.get("actor"),
                    "occurred_at": STARTED,
                    "target": body.get("target"),
                    "payload": {},
                }
            ),
        )

    page.route("**/api/review/answer", answer)
    return posted


def test_quick_checks_one_at_a_time_keep_and_skip_keep_every_guess(
    browser: Any, served: Served
) -> None:
    checks = INVENTED_QUICK
    page = _page(browser, init=None)
    _checks_are(page, INVENTED)
    posted = _answers(page)
    page.goto(served.address)
    page.wait_for_selector('[data-view="quick-checks"]', timeout=HANG_GUARD_MS)
    assert page.locator(".ck-card").count() == 1
    assert page.locator(".ov-page").count() == 0
    # The tour waits for the quick checks: they carry an attribute, and say when they close.
    assert page.locator("[data-quick-checks]").count() == 1
    page.evaluate(
        "window.__done = 0; document.addEventListener('ingence:checks-done', () => { window.__done += 1; })"
    )
    assert page.text_content(".ck-count") == f"Check 1 of {len(checks)}"
    assert page.inner_text(".ck-title") == checks[0]["title"]
    first = checks[0]
    keep = page.locator('[data-quick="keep"]')
    assert keep.inner_text() == ("Keep" if first["default"] else "Next")
    if first["default"]:
        assert first["default"]["text"] in page.inner_text(".ck-guess")
    keep.click()
    page.wait_for_function(
        "n => document.querySelector('.ck-count')?.textContent === n",
        arg=f"Check 2 of {len(checks)}",
        timeout=HANG_GUARD_MS,
    )
    assert page.inner_text(".ck-title") == checks[1]["title"]
    assert page.locator(".ck-card").count() == 1
    # Only a check with a guess is asked here: the one with none is left out, and every card keeps.
    assert INVENTED[1]["title"] not in page.inner_text(".ck-card")
    assert "Ingence has no guess here" not in page.inner_text(".ck-card")
    assert "Each check already has a guess" in page.inner_text(".ck-note")
    assert page.inner_text('[data-quick="keep"]') == "Keep"
    assert page.evaluate("window.__done") == 0
    page.click('[data-quick="skip"]')
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    settled(page)
    assert posted == []
    assert page.evaluate("window.__done") == 1
    assert page.locator("[data-quick-checks]").count() == 0
    # Shown once for the project: a reload opens the Overview itself.
    page.reload()
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    assert page.locator('[data-view="quick-checks"]').count() == 0
    page.context.close()


def test_quick_check_change_answers_through_the_answer_route(browser: Any, served: Served) -> None:
    checks = INVENTED_QUICK
    page = _page(browser, init=None)
    _checks_are(page, INVENTED)
    posted = _answers(page)
    page.goto(served.address)
    page.wait_for_selector('[data-view="quick-checks"]', timeout=HANG_GUARD_MS)
    page.click('[data-quick="change"]')
    option = checks[0]["options"][-1]
    page.click(f'.ck-options button[data-option="{option["value"]}"]')
    page.wait_for_function(
        "n => document.querySelector('.ck-count')?.textContent === n",
        arg=f"Check 2 of {len(checks)}",
        timeout=HANG_GUARD_MS,
    )
    assert len(posted) == 1
    assert posted[0]["word"] == option["value"]
    assert posted[0]["target"] == checks[0]["question_id"]
    assert posted[0]["actor"] == "alice"
    assert "waits for Re-run" in page.inner_text(".ck-said")
    page.context.close()


def test_quick_checks_show_once_per_page_load_where_storage_is_refused(
    browser: Any, served: Served
) -> None:
    page = _page(browser, init=REFUSED)
    _checks_are(page, INVENTED)
    page.goto(served.address)
    page.wait_for_selector('[data-view="quick-checks"]', timeout=HANG_GUARD_MS)
    page.click('[data-quick="skip"]')
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    page.click('#pages button[data-page="history"]')
    page.wait_for_function("state.view === 'changes'", timeout=HANG_GUARD_MS)
    page.click('#pages button[data-page="overview"]')
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    assert page.locator('[data-view="quick-checks"]').count() == 0
    page.reload()
    page.wait_for_selector('[data-view="quick-checks"]', timeout=HANG_GUARD_MS)
    page.context.close()


# ---------------------------------------------------------------------------
# 2b. Where Ingence has no guess.
# ---------------------------------------------------------------------------


def test_b2_quick_checks_are_skipped_where_no_check_has_a_guess(
    browser: Any, served: Served
) -> None:
    page = _page(browser, init=None)
    _checks_are(page, [_invented_check(2, guess=False), _invented_check(5, guess=False)])
    page.goto(served.address)
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    assert page.locator('[data-view="quick-checks"]').count() == 0
    page.context.close()


def test_b2_a_guessed_check_ranked_sixth_is_still_a_quick_check(
    browser: Any, served: Served
) -> None:
    unguessed = [_invented_check(number, guess=False) for number in range(10, 15)]
    page = _page(browser, init=None)
    _checks_are(page, [*unguessed, _invented_check(6, guess=True)])
    page.goto(served.address)
    page.wait_for_selector('[data-view="quick-checks"]', timeout=HANG_GUARD_MS)
    assert page.text_content(".ck-count") == "Check 1 of 1"
    assert "Files read as kind 6" in page.inner_text(".ck-title")
    page.context.close()


def test_b2_the_overview_lists_a_check_with_no_guess_with_its_question_and_options(
    browser: Any, served: Served
) -> None:
    page = _page(browser)
    _checks_are(page, INVENTED)
    _overview(page, served.address)
    quiet = page.locator(".ov-check-list li[data-no-guess]")
    assert quiet.count() == 1
    item = quiet.first.inner_text()
    assert INVENTED[1]["title"] in item and INVENTED[1]["text"] in item
    assert "Ingence has no guess here" in item
    assert quiet.first.locator("button[data-option]").count() == len(INVENTED[1]["options"])
    # A check with a guess keeps its one line.
    assert page.locator(".ov-check-list li:not([data-no-guess]) .ck-guess").count() == 0
    assert "guess: Read as kind 1" in page.locator(".ov-check-list li").first.inner_text()
    page.context.close()


def test_b2_checks_says_each_has_a_guess_only_where_each_has_one(
    browser: Any, served: Served
) -> None:
    page = _page(browser)
    _checks_are(page, INVENTED)
    page.goto(f"{served.address}#checks?check={INVENTED[1]['id']}")
    page.wait_for_selector(".ck-card", timeout=HANG_GUARD_MS)
    settled(page)
    assert "Each already has a guess" not in page.inner_text(".ck-page .lead")
    card = page.inner_text(".ck-card")
    assert "Ingence has no guess here" in card and INVENTED[1]["text"] in card
    assert page.locator(".ck-card .ck-options button[data-option]").first.is_visible()
    page.context.close()
    everyone = _page(browser)
    _checks_are(everyone, INVENTED_QUICK)
    everyone.goto(f"{served.address}#checks")
    everyone.wait_for_selector(".ck-card", timeout=HANG_GUARD_MS)
    assert "Each already has a guess" in everyone.inner_text(".ck-page .lead")
    everyone.context.close()


def test_b3_an_assumption_is_one_sentence_with_the_rule_behind_why(
    browser: Any, served: Served
) -> None:
    page = _page(browser)

    def answer(route: Any) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "api_version": "1.0",
                    "schema_version": "x",
                    "run_id": "run_20260101T000002Z_222222222222",
                    "total": 1,
                    "assumptions": [
                        {
                            "id": "class:rule_9:log",
                            "kind": "stage",
                            "text": "Read as log: 5 files. Ingence placed these by the workflow.\n\nWhy: The full rule statement.",
                            "count": 5,
                            "examples": [],
                            "reject": {
                                "route": "/api/assumptions/reject",
                                "body": {"id": "class:rule_9:log"},
                            },
                            "tier": "abductive",
                        }
                    ],
                }
            ),
        )

    page.route("**/api/v1/assumptions", answer)
    _overview(page, served.address)
    line = page.locator(".ov-assumption").first
    assert line.locator(".ov-assumption-text").inner_text() == (
        "Read as log: 5 files. Ingence placed these by the workflow."
    )
    why = line.locator("details.ov-why")
    assert why.get_attribute("open") is None
    assert "The full rule statement." not in line.locator(".ov-assumption-text").inner_text()
    why.locator("summary").click()
    assert "The full rule statement." in why.inner_text()
    page.context.close()


def test_b5_a_tour_step_shows_only_while_its_control_is_on_the_page(
    browser: Any, served: Served
) -> None:
    page = _page(browser, tour=True)
    _overview(page, served.address)
    assert page.evaluate("window.ingenceTour('first_visit')") is True
    page.wait_for_selector(".help-tour", timeout=HANG_GUARD_MS)
    assert page.locator(".help-tour").count() == 1
    # Another page is shown: the tour does not float over it.
    page.click('#pages button[data-page="checks"]')
    page.wait_for_function("state.view === 'checks'", timeout=HANG_GUARD_MS)
    page.wait_for_function("!document.querySelector('.help-tour')", timeout=HANG_GUARD_MS)
    assert page.locator(".help-callout, .help-tour").count() == 0
    # And back on the Overview it does not resume by itself.
    page.click('#pages button[data-page="overview"]')
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.locator(".help-tour").count() == 0
    page.context.close()


def test_b5_a_step_whose_control_goes_moves_to_the_next_that_is_on_the_page(
    browser: Any, served: Served
) -> None:
    page = _page(browser, tour=True)
    _overview(page, served.address)
    assert page.evaluate("window.ingenceTour('first_visit')") is True
    page.wait_for_selector(".help-tour", timeout=HANG_GUARD_MS)
    first = page.get_attribute(".help-tour", "aria-label")
    assert first is not None and "step 1 of" in first
    page.evaluate("document.querySelector('[data-help=\"overview.found\"]').hidden = true")
    page.wait_for_function(
        "n => (document.querySelector('.help-tour')?.getAttribute('aria-label') || '') !== n",
        arg=first,
        timeout=HANG_GUARD_MS,
    )
    shown = page.evaluate("document.querySelector('.help-tour')?.getAttribute('aria-label') || ''")
    assert shown == "" or "overview.found" not in shown
    page.context.close()


# ---------------------------------------------------------------------------
# 3. The Overview against its routes.
# ---------------------------------------------------------------------------


def test_the_overview_draws_every_part_from_its_routes(browser: Any, served: Served) -> None:
    found = _get(served.address, "/api/v1/overview")
    assumptions = _get(served.address, "/api/v1/assumptions")
    checks = _get(served.address, "/api/v1/checks?limit=5")
    goals = _get(served.address, "/api/v1/goals")
    page = _page(browser)
    _overview(page, served.address)
    order = page.eval_on_selector_all(
        ".ov-page > *", "nodes => nodes.map(n => n.dataset.part || n.className.split(' ')[0])"
    )
    # The Ask box first, then the goals, then the sentence.
    assert order[:2] == ["ov-title", "ov-ask"]
    if goals["goals"]:
        assert order[2] == "goals"
    assert page.get_attribute(".ov-ask input", "aria-label") == "Ask"
    sentence = page.inner_text(".ov-sentence")
    counts = found["counts"]
    assert sentence.startswith(
        f"{counts['experiments']:,} experiments in {counts['areas']:,} areas"
    )
    assert f"{counts['files']:,} files" in sentence
    assert found["file_types"][0]["label"] in sentence
    # Areas, each with its kind; an assumed kind says so and offers Change.
    areas = page.locator(".ov-area")
    assert areas.count() == len(found["areas"])
    for index, area in enumerate(found["areas"]):
        row = areas.nth(index)
        assert area["name"] in row.inner_text() and area["kind"] in row.inner_text()
        assert (row.locator(".ov-assumed").count() == 1) == area["kind_assumed"]
    completeness = found["completeness"]
    analyzed = page.inner_text(".ov-analyzed")
    assert (
        analyzed
        == f"Raw files analyzed: {completeness['raw_with_analysis']:,} of {completeness['raw_files']:,}"
    )
    assert f"{completeness['not_read']:,} files not read yet" in page.inner_text(
        '[data-part="completeness"]'
    )
    repeats = page.inner_text('[data-part="repeats"]')
    assert f"Seen once: {found['repeats']['seen_once']:,}" in repeats
    assert page.locator(".ov-theme").count() == len(found["themes_top"])
    lines = page.locator('[data-part="assumptions"] .ov-assumption')
    assert lines.count() == min(8, len(assumptions["assumptions"]))
    assert lines.nth(0).locator("button", has_text="This is wrong").count() == 1
    assert page.locator(".ov-check-list li").count() == len(checks["checks"])
    cards = page.locator(".ov-goal")
    assert cards.count() == len(goals["goals"])
    for index, goal in enumerate(goals["goals"]):
        card = cards.nth(index)
        summary = goal["summary"]
        line = card.locator(".ov-goal-line").inner_text()
        assert line.startswith(f"{summary['experiments']:,} experiment")
        assert (
            f"({summary['named']:,} named, {summary['associated']:,} associated, {summary['same_theme']:,} same theme)"
            in line
        )
        assert "Seen once means one experiment supports it: observational." in card.inner_text()
        tiers = card.locator(".ov-tier").evaluate_all("nodes => nodes.map(n => n.dataset.tier)")
        assert tiers == [
            t
            for t in ("named", "associated", "same_theme")
            if any(m["tier"] == t for m in goal["matches"])
        ]
    page.context.close()


def test_show_these_opens_the_goals_experiments_and_keeps_them_for_overlap(
    browser: Any, served: Served
) -> None:
    goals = _get(served.address, "/api/v1/goals")["goals"]
    if not goals:
        pytest.skip("the routes answer no goal for this project")
    page = _page(browser)
    _overview(page, served.address)
    page.locator(".ov-goal").first.locator("button", has_text="Show these").click()
    page.wait_for_function("state.view === 'experiments'", timeout=HANG_GUARD_MS)
    assert page.evaluate("location.hash") == f"#experiments?goal={goals[0]['id']}"
    kept = json.loads(page.evaluate("sessionStorage.getItem('ingence.highlight')"))
    assert kept["kind"] == "goal" and kept["id"] == goals[0]["id"]
    assert kept["experiments"] == [match["experiment_id"] for match in goals[0]["matches"]]
    page.wait_for_selector(
        f'.ov-goal-panel[data-goal="{goals[0]["id"]}"] .ov-tier', timeout=HANG_GUARD_MS
    )
    listed = page.eval_on_selector_all(
        ".ov-goal-panel li[data-experiment]", "nodes => nodes.map(n => n.dataset.experiment)"
    )
    assert sorted(listed) == sorted(match["experiment_id"] for match in goals[0]["matches"])
    page.context.close()


def test_this_is_wrong_posts_the_reject_route_and_lights_rerun(
    browser: Any, served: Served
) -> None:
    item = _get(served.address, "/api/v1/assumptions")["assumptions"][0]
    page = _page(browser)
    posted: list[dict[str, Any]] = []
    state = {"rejected": False}

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
        # What waits is this test's own: nothing before the press, one correction after, whatever
        # another test of the file wrote to the shared project.
        answer = route.fetch()
        held = answer.json()
        held["pending_rerun"] = (
            {
                "count": 1,
                "banner": "1 correction waits for Re-run.",
                "steps": [{"words": "apply the rules again"}],
                "kinds": [],
            }
            if state["rejected"]
            else None
        )
        route.fulfill(response=answer, json=held)

    page.route("**/api/assumptions/reject", reject)
    page.route("**/api/workspace/state", workspace)
    _overview(page, served.address)
    assert page.is_hidden("#rerun-badge")
    line = page.locator(f'[data-part="assumptions"] .ov-assumption[data-assumption="{item["id"]}"]')
    wrong = line.locator("button[data-wrong]")
    wrong.focus()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "id => document.querySelector(`.ov-assumption[data-assumption=\"${id}\"] .ov-said`)?.textContent === 'Waits for Re-run'",
        arg=item["id"],
        timeout=HANG_GUARD_MS,
    )
    # The route records an answer, so the page adds the name it was given.
    assert posted == [{**item["reject"]["body"], "actor": "alice"}]
    # The pressed button keeps the focus, held: a second press posts nothing.
    assert page.evaluate("document.activeElement.hasAttribute('data-wrong')")
    assert wrong.get_attribute("aria-disabled") == "true"
    page.keyboard.press("Enter")
    settled(page)
    assert len(posted) == 1
    page.wait_for_selector("#rerun-badge:not([hidden])", timeout=HANG_GUARD_MS)
    assert page.inner_text("#rerun-badge") == "1"
    page.context.close()


def test_this_is_wrong_says_the_engines_words_when_nothing_is_recorded(
    browser: Any, served: Served
) -> None:
    item = _get(served.address, "/api/v1/assumptions")["assumptions"][0]
    page = _page(browser)
    # The write is answered here, so the project the file shares records nothing.
    nothing = {
        "id": item["id"],
        "kind": None,
        "answer": None,
        "waits_for_rerun": True,
        "said": "Not in this build yet; nothing was kept.",
        "recorded": False,
    }
    page.route(
        "**/api/assumptions/reject",
        lambda route: route.fulfill(
            status=201, content_type="application/json", body=json.dumps(nothing)
        ),
    )
    _overview(page, served.address)
    # The line's examples are buttons too: This is wrong is named.
    page.locator(f'.ov-assumption[data-assumption="{item["id"]}"] button[data-wrong]').first.click()
    said = page.locator(
        f'[data-part="assumptions"] .ov-assumption[data-assumption="{item["id"]}"] .ov-said'
    )
    page.wait_for_function(
        "el => el.textContent !== ''", arg=said.element_handle(), timeout=HANG_GUARD_MS
    )
    assert said.inner_text() == nothing["said"]
    page.context.close()


def test_an_assumed_area_kind_posts_the_area_route(browser: Any, served: Served) -> None:
    areas = [
        area for area in _get(served.address, "/api/v1/overview")["areas"] if area["kind_assumed"]
    ]
    if not areas:
        pytest.skip("no area kind is assumed")
    area = areas[0]
    page = _page(browser)
    posted: list[dict[str, Any]] = []

    def kind(route: Any) -> None:
        posted.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=201,
            content_type="application/json",
            body=json.dumps(
                {**posted[-1], "answer_target": f"area:{area['id']}", "said": "", "recorded": True}
            ),
        )

    page.route("**/api/areas/kind", kind)
    _overview(page, served.address)
    row = page.locator(f'.ov-area[data-area="{area["id"]}"]')
    row.locator("button", has_text="Change").click()
    other = next(
        kind
        for kind in ("person", "instrument", "project", "date", "folder")
        if kind != area["kind"]
    )
    # Choosing posts nothing (the arrow keys choose on some systems). Save posts.
    row.locator("select").select_option(other)
    settled(page)
    assert posted == []
    save = row.locator("button[data-area-save]")
    save.click()
    page.wait_for_function(
        "el => el.textContent === 'Waits for Re-run'",
        arg=row.locator(".ov-said").element_handle(),
        timeout=HANG_GUARD_MS,
    )
    assert posted == [{"area_id": area["id"], "kind": other, "actor": "alice"}]
    assert save.get_attribute("aria-disabled") == "true"
    assert page.evaluate("document.activeElement.hasAttribute('data-area-save')")
    page.context.close()


# ---------------------------------------------------------------------------
# 4. Checks.
# ---------------------------------------------------------------------------


def test_checks_shows_one_question_at_a_time_and_the_words_that_matter(
    browser: Any, served: Served
) -> None:
    checks = _get(served.address, "/api/v1/checks?limit=50")["checks"]
    if len(checks) < 2:
        pytest.skip("the routes answer fewer than the 2 checks this test walks")
    assumptions = _get(served.address, "/api/v1/assumptions")["assumptions"]
    page = _page(browser)
    _overview(page, served.address)
    page.locator(".ov-check-list li").nth(1).locator("button.link").click()
    page.wait_for_function("state.view === 'checks'", timeout=HANG_GUARD_MS)
    page.wait_for_selector(".ck-card", timeout=HANG_GUARD_MS)
    assert page.locator(".ck-card").count() == 1
    assert page.text_content(".ck-count") == f"Check 2 of {len(checks)}"
    assert page.inner_text(".ck-title") == checks[1]["title"]
    page.click(".ck-card button:has-text('Previous')")
    page.wait_for_function(
        "n => document.querySelector('.ck-count').textContent === n",
        arg=f"Check 1 of {len(checks)}",
        timeout=HANG_GUARD_MS,
    )
    assert page.locator(".ck-card button", has_text="Change").count() == 1
    words = [item for item in assumptions if item["kind"] in ("spelling", "domain")]
    assert page.locator('[data-part="words"] .ov-assumption').count() == len(words)
    assert page.locator('[data-part="all-assumptions"] .ov-assumption').count() == len(assumptions)
    page.click("button:has-text('All questions')")
    page.wait_for_function("state.view === 'review'", timeout=HANG_GUARD_MS)
    assert _active(page) == ["advanced", "review"]
    page.context.close()


def test_all_assumptions_opens_the_full_list_in_checks(browser: Any, served: Served) -> None:
    page = _page(browser)
    _overview(page, served.address)
    page.click('[data-part="assumptions"] button:has-text("All assumptions")')
    page.wait_for_function(
        "document.activeElement && document.activeElement.id === 'all-assumptions'",
        timeout=HANG_GUARD_MS,
    )
    assert page.evaluate("location.hash") == "#checks?show=assumptions"
    page.context.close()


# ---------------------------------------------------------------------------
# 5. The header's Ask, and the Overview's job line (Re-run reads Stop; see workspace.js).
# ---------------------------------------------------------------------------


def test_the_ask_box_opens_the_ask_view_with_the_question(browser: Any, served: Served) -> None:
    page = _page(browser)
    _overview(page, served.address)
    box = page.locator("#search-box")
    assert box.get_attribute("aria-label") == "Ask"
    assert box.get_attribute("data-help") == "ask.box"
    assert (box.get_attribute("placeholder") or "").startswith("Ask: for example")
    box.fill("HeLa")
    box.press("Enter")
    page.wait_for_function("state.view === 'ask'", timeout=HANG_GUARD_MS)
    assert page.evaluate("location.hash") == "#ask?q=HeLa"
    page.wait_for_selector("[data-ask-view]", timeout=HANG_GUARD_MS)
    assert page.text_content("h2[data-ask-view]") == "Ask: HeLa"
    # The Overview's own box asks the same way.
    page.click('#pages button[data-page="overview"]')
    page.wait_for_selector(".ov-ask input", timeout=HANG_GUARD_MS)
    page.fill(".ov-ask input", "Plate_A")
    page.press(".ov-ask input", "Enter")
    page.wait_for_function("location.hash === '#ask?q=Plate_A'", timeout=HANG_GUARD_MS)
    page.context.close()


#: The stop dialog, standing in: each call is kept, and the dialog's own script cannot
#: replace it (its assignment is ignored), so the test reads what the Overview asked of it.
STOP_DIALOG = """(() => {
  window.__stops = [];
  Object.defineProperty(window, 'ingenceStop', {configurable: true,
    get() { return (jobId) => { window.__stops.push(jobId); return Promise.resolve(); }; },
    set() {}});
})();"""


def test_the_overviews_job_line_follows_the_job_and_stop_asks_the_stop_dialog(
    browser: Any, served: Served
) -> None:
    page = _page(browser)
    page.add_init_script(STOP_DIALOG)
    status = {
        "phase": "binding",
        "state": "running",
        "job_id": "job_overview_long",
        "spellings": [],
        "rerun": [],
        "operations": ["partial_rebind"],
        "queued": [],
        "phase_label": "Reading the names",
        "step": 1,
        "steps": 2,
        "done": 3,
        "total": 9,
        "rate_per_s": None,
        "seconds_left": 40,
        "can_stop": True,
        "stop_keeps": True,
    }
    page.route(
        "**/api/applying",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(status)
        ),
    )
    page.goto(f"{served.address}#overview")
    page.wait_for_selector(".ov-job:not([hidden])", timeout=HANG_GUARD_MS)
    assert page.inner_text(".ov-job-line span") == "Reading the names: step 1 of 2, 3 of 9."
    stop = page.locator(".ov-job-line button")
    assert stop.inner_text() == "Stop" and stop.is_enabled()
    # Each reading of the job brings the words up to date; the line is not drawn again.
    page.evaluate("document.querySelector('.ov-job-line button').dataset.kept = 'yes'")
    status["done"] = 5
    page.wait_for_function(
        "() => document.querySelector('.ov-job-line span').textContent.includes('5 of 9')",
        timeout=HANG_GUARD_MS,
    )
    assert page.get_attribute(".ov-job-line button", "data-kept") == "yes"
    stop.click()
    page.wait_for_function("window.__stops.length === 1", timeout=HANG_GUARD_MS)
    assert page.evaluate("window.__stops") == ["job_overview_long"]
    # The job over, the line goes.
    status.update({"phase": "done", "state": "done"})
    page.wait_for_selector(".ov-job", state="hidden", timeout=HANG_GUARD_MS)
    page.context.close()


def test_the_overviews_stop_is_off_where_the_engine_says_the_job_cannot_stop(
    browser: Any, served: Served
) -> None:
    page = _page(browser)
    page.add_init_script(STOP_DIALOG)
    status = {
        "phase": "inferring",
        "state": "running",
        "job_id": "job_overview_fixed",
        "spellings": [],
        "rerun": [],
        "operations": ["inference"],
        "queued": [],
        "phase_label": "Applying the rules",
        "step": 1,
        "steps": 1,
        "done": None,
        "total": None,
        "can_stop": False,
        "stop_keeps": False,
    }
    page.route(
        "**/api/applying",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(status)
        ),
    )
    page.goto(f"{served.address}#overview")
    page.wait_for_selector(".ov-job:not([hidden])", timeout=HANG_GUARD_MS)
    assert page.inner_text(".ov-job-line span") == "Applying the rules: step 1 of 1."
    assert page.is_disabled(".ov-job-line button")
    assert page.evaluate("window.__stops") == []
    page.context.close()


def test_the_last_check_kept_closes_the_quick_checks_and_says_so(
    browser: Any, served: Served
) -> None:
    checks = INVENTED_QUICK
    page = _page(browser, init=None)
    _checks_are(page, INVENTED)
    page.goto(served.address)
    page.wait_for_selector("[data-quick-checks]", timeout=HANG_GUARD_MS)
    page.evaluate(
        "window.__done = 0; document.addEventListener('ingence:checks-done', () => { window.__done += 1; })"
    )
    for index in range(1, len(checks) + 1):
        assert page.text_content(".ck-count") == f"Check {index} of {len(checks)}"
        page.click('[data-quick="keep"]')
    page.wait_for_selector(".ov-page", timeout=HANG_GUARD_MS)
    assert page.evaluate("window.__done") == 1
    assert page.evaluate("document.activeElement.id") == "overview-title"
    page.context.close()


def test_the_first_visit_tour_waits_for_the_quick_checks_to_close(
    browser: Any, served: Served
) -> None:
    page = _page(browser, init=None, tour=True)
    _checks_are(page, INVENTED)
    page.goto(served.address)
    page.wait_for_selector("[data-quick-checks]", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.locator(".help-tour").count() == 0, "the tour waits while the checks are open"
    page.click('[data-quick="skip"]')
    page.wait_for_selector(".help-tour", timeout=HANG_GUARD_MS)
    first = json.loads(page.evaluate("fetch('/help/catalog.json').then((r) => r.text())"))["tours"][
        "first_visit"
    ][0]
    assert page.locator(f'.ov-page [data-help="{first}"]').count() >= 1
    page.context.close()


def _goals_naming(address: str, ids: list[str]) -> dict[str, Any]:
    """The goals route's answer, its first goal's matches pointed at this project's own experiments
    (the stub names invented ones), each tier kept as the route gave it."""
    goals = _get(address, "/api/v1/goals")
    goal = goals["goals"][0]
    goal["matches"] = [
        {**match, "experiment_id": ids[index % len(ids)]}
        for index, match in enumerate(goal["matches"][: len(ids)])
    ]
    return goals


def test_show_these_lists_only_the_goals_experiments_and_each_opens_its_story(
    browser: Any, served: Served
) -> None:
    rows = _get(served.address, "/api/v1/experiments?limit=50")["rows"]
    if len(rows) < 2 or not _get(served.address, "/api/v1/goals")["goals"]:
        pytest.skip("the project needs 2 experiments and the routes a goal")
    ids = [row["id"] for row in rows[:2]]
    goals = _goals_naming(served.address, ids)
    page = _page(browser)
    page.route(
        "**/api/v1/goals",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(goals)
        ),
    )
    _overview(page, served.address)
    page.locator(".ov-goal").first.locator("button", has_text="Show these").click()
    page.wait_for_selector(".exp-goal-line", timeout=HANG_GUARD_MS)
    shown = page.eval_on_selector_all(
        ".exp-table tr.exp-row", "rows => rows.map(r => r.dataset.id)"
    )
    assert sorted(shown) == sorted(ids)
    assert page.text_content(".exp-goal-line").startswith(
        f"Showing the {len(ids)} experiments of this goal."
    )
    # Each of the goal's experiments opens its story under it, and again closes it.
    first = page.locator(f'.ov-goal-panel li[data-experiment="{ids[0]}"] button.ov-goal-open')
    first.click()
    page.wait_for_function(
        "id => { const box = document.getElementById(`goal-story-${id}`);"
        " return box && !box.hidden && box.textContent.trim() !== ''; }",
        arg=ids[0],
        timeout=HANG_GUARD_MS,
    )
    assert first.get_attribute("aria-expanded") == "true"
    first.click()
    assert first.get_attribute("aria-expanded") == "false"
    # Show all lists every experiment again.
    page.click(".exp-goal-line button")
    page.wait_for_function("location.hash === '#experiments'", timeout=HANG_GUARD_MS)
    page.wait_for_selector(".exp-table tr.exp-row", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.locator(".exp-goal-line").count() == 0
    assert page.locator(".exp-table tr.exp-row").count() == len(rows)
    page.context.close()


def test_a_theme_opens_overlaps_connections_with_its_filter_and_keeps_a_goals_highlight(
    browser: Any, served: Served
) -> None:
    themes = _get(served.address, "/api/v1/overview")["themes_top"]
    if not themes:
        pytest.skip("the routes answer no theme")
    page = _page(browser)
    _overview(page, served.address)
    kept = json.dumps({"kind": "goal", "id": "goal_x", "label": "a goal", "experiments": ["e1"]})
    page.evaluate("text => sessionStorage.setItem('ingence.highlight', text)", kept)
    page.evaluate("sessionStorage.setItem('ingence.overlap.mode', 'by_year')")
    page.locator(".ov-theme").first.click()
    page.wait_for_function("state.view === 'overlap'", timeout=HANG_GUARD_MS)
    assert page.evaluate("sessionStorage.getItem('ingence.overlap.theme')") == themes[0]["label"]
    assert (
        page.evaluate(
            "window.ingenceOverlap ? window.ingenceOverlap.mode() : sessionStorage.getItem('ingence.overlap.mode')"
        )
        == "connections"
    )
    assert page.evaluate("sessionStorage.getItem('ingence.highlight')") == kept
    page.context.close()
