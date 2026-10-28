"""The Dashboard's job line, its Stop and the first-visit tour, in a real browser.

- The job line (``#applying``) says the phase, the step, the counts and the time left from the
  fields of ``/api/applying``, with a bar only where the total is known. It never shows an
  internal table name, a time predicted from an earlier job, or the Record.
- The header's Re-run reads Stop while the job can stop (``can_stop``), and Re-run again after.
  While a job runs, a line over the readout says the views show the last finished scan.
- Stop asks first, under the job line, never in a pop-up: "What was read so far is kept." only
  where ``stop_keeps`` is true, and "Your changes still wait for Re-run." only where changes wait.
  Stop posts ``/api/workspace/stop`` with the job and ``keep: true``. ``window.ingenceStop`` opens
  the same question from any page.
- A stopped job reads Stopped, in the job line and in History, never "failed".
- The tour starts once, after the quick checks, has Skip, Back, Next and Done, jumps without
  motion under reduced motion, and runs again from the "?" sheet. The test does not rely on the
  Overview's own page. It puts the four parts the tour points at on the page itself, by their
  ``data-help`` keys.

The routes' answers are stubbed: a job on a small project ends before its phases can be read.
Every name and number is invented.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser, need, serve_theme_project

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: Words that name an internal table or a predicted time, which the job line never shows.
NEVER = ("bindings", "claims", "classes", "findings", "candidates", "last time", "Record")

#: The parts of the Overview the tour points at, in its order (the Overview carries these keys).
TOUR_KEYS = ("overview.found", "overview.completeness", "overview.ask", "overview.assumptions")


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "d4jobs")


def _applying(**given: Any) -> dict[str, Any]:
    """A re-run as /api/applying says it while it runs, with the current keys and the older ones
    the line must never show."""
    state: dict[str, Any] = {
        "job_id": "job_rerun",
        "state": "running",
        "phase": "inferring",
        # An older label for the phase. The page says its own word for a phase it knows, whatever
        # ``phase_label`` says.
        "phase_label": "Applying the rules",
        "rerun": ["inference"],
        "spellings": [],
        "queued": [],
        "operations": ["inference"],
        "retries": 0,
        "attempts": 1,
        "waiting_for": ["claims", "classes", "findings", "questions", "candidates"],
        "expected_seconds": 99,
        "elapsed_seconds": 12,
        "step": 1,
        "steps": 1,
        "done": 21,
        "total": 48,
        "rate_per_s": 2.0,
        "seconds_left": 150,
        "can_stop": False,
        "stop_keeps": False,
    }
    state.update(given)
    return state


WAITING = {
    "count": 3,
    "banner": "3 changes wait for Re-run.",
    "steps": [{"words": "apply the rules again", "reads_the_share": False}],
    "summary": [],
    "commits": [],
    "answers": [],
}


def _stubbed(page: Any, now: dict[str, Any]) -> None:
    """/api/applying answers ``now["job"]``; /api/workspace/state carries the same job, and
    ``now["waiting"]`` as what waits for Re-run."""
    page.route("**/api/applying", lambda route: route.fulfill(json=now["job"]))

    def workspace(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        body["applying"] = now["job"]
        body["pending_rerun"] = now.get("waiting")
        route.fulfill(response=response, json=body)

    page.route("**/api/workspace/state", workspace)


def _close(page: Any) -> None:
    """Close a page whose routes may still be answering: their answers are dropped first."""
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()


def _open(browser: Any, served: str, view: str = "inventory", **context: Any) -> Any:
    page = browser.new_context(viewport={"width": 1400, "height": 900}, **context).new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.problems = problems
    page.goto(f"{served}#{view}")
    return page


def _until(page: Any, check: Callable[[], bool], what: str) -> None:
    """Wait for a state the page reaches by itself (each turn of its own polling)."""
    for _ in range(HANG_GUARD_MS // 100):
        if check():
            return
        page.wait_for_timeout(100)
    raise AssertionError(what)


# -- the job line ---------------------------------------------------------------------------------


def test_the_job_line_says_the_phase_step_counts_and_time_left(browser, served) -> None:
    now: dict[str, Any] = {"job": _applying(step=2, steps=2)}
    page = _open(browser, served)
    _stubbed(page, now)
    page.reload()
    line = page.locator("#applying")
    line.get_by_text("Applying rules · Step 2 of 2 · 21 of 48 · About 3 minutes left").wait_for(
        timeout=HANG_GUARD_MS
    )
    said = line.inner_text()
    assert said.startswith(
        "Changes: Applying rules · Step 2 of 2 · 21 of 48 · About 3 minutes left."
    )
    for word in NEVER:
        assert word not in said, word
    bar = line.locator("[role=progressbar]")
    assert bar.get_attribute("aria-valuenow") == "21"
    assert bar.get_attribute("aria-valuemax") == "48"
    assert "%" not in said

    # An answer's job: its words, then the phase; no total, so no bar and the count so far.
    now["job"] = _applying(
        rerun=[],
        spellings=["kelt"],
        phase="binding",
        phase_label="",
        step=1,
        steps=2,
        done=300,
        total=None,
        seconds_left=None,
    )
    line.get_by_text('"kelt": Reading names · Step 1 of 2 · 300 so far.').wait_for(
        timeout=HANG_GUARD_MS
    )
    assert line.locator("[role=progressbar]").count() == 0
    assert "left" not in line.inner_text()
    assert page.problems == []
    _close(page)


# -- Stop in the header, and the line over the readout --------------------------------------------


def test_re_run_reads_stop_while_a_job_can_stop_and_the_readout_says_it_is_the_last(
    browser, served
) -> None:
    now: dict[str, Any] = {"job": _applying(can_stop=True)}
    page = _open(browser, served)
    _stubbed(page, now)
    page.reload()
    button = page.locator("#rerun-button")
    _until(page, lambda: button.inner_text() == "Stop", "Re-run reads Stop")
    assert button.is_enabled()
    assert button.get_attribute("data-help") == "workspace.stop"
    banner = page.locator("#scan-not-ready")
    banner.wait_for(timeout=HANG_GUARD_MS)
    assert banner.inner_text() == "This is the last finished scan. The new one is not ready yet."
    # Over the readout: before the page's main region.
    assert page.evaluate(
        "Boolean(document.querySelector('#scan-not-ready').compareDocumentPosition("
        "document.querySelector('#main')) & Node.DOCUMENT_POSITION_FOLLOWING)"
    )

    # The job ends: Re-run again, and the line goes.
    now["job"] = {
        **_applying(),
        "state": "done",
        "phase": "done",
        "phase_label": "Done",
        "can_stop": False,
        "done": None,
        "total": None,
        "seconds_left": None,
    }
    _until(page, lambda: button.inner_text() == "Re-run", "Stop reads Re-run again")
    assert button.get_attribute("data-help") == "workspace.rerun"
    _until(page, lambda: not banner.is_visible(), "the line over the readout goes")
    assert page.problems == []
    _close(page)


def test_stop_asks_first_with_only_the_lines_that_are_true(browser, served) -> None:
    now: dict[str, Any] = {"job": _applying(can_stop=True, stop_keeps=True), "waiting": WAITING}
    page = _open(browser, served)
    _stubbed(page, now)
    sent: list[dict[str, Any]] = []

    def stopped(route: Any) -> None:
        sent.append(json.loads(route.request.post_data))
        assert route.request.headers.get("x-ingence-write") == "1"
        route.fulfill(
            status=201,
            json={
                "job_id": "job_rerun",
                "keep": True,
                "requested": True,
                "state": "running",
                "stop_keeps": True,
                "said": "",
                "recorded": True,
            },
        )

    page.route("**/api/workspace/stop", stopped)
    page.reload()
    button = page.locator("#rerun-button")
    _until(page, lambda: button.inner_text() == "Stop", "Re-run reads Stop")
    button.click()
    panel = page.locator("#stop-confirm")
    panel.wait_for()
    assert page.locator("dialog[open]").count() == 0, "never a pop-up"
    assert panel.locator(".stop-confirm-head").inner_text() == "Stop the scan?"
    assert panel.locator("[data-line=keeps]").inner_text() == "What was read so far is kept."
    assert panel.locator("[data-line=waits]").inner_text() == "Your changes still wait for Re-run."
    # Under the job line.
    assert (
        page.evaluate("document.querySelector('#applying').nextElementSibling.id") == "stop-confirm"
    )
    panel.get_by_role("button", name="Cancel").click()
    panel.wait_for(state="detached")

    # Each line on its own: kept and nothing waiting, then waiting and nothing kept.
    for keeps, waiting, lines in ((True, None, ["keeps"]), (False, WAITING, ["waits"])):
        now["job"] = _applying(can_stop=True, stop_keeps=keeps)
        now["waiting"] = waiting
        button.click()
        panel.wait_for()
        shown = [node.get_attribute("data-line") for node in panel.locator("[data-line]").all()]
        assert shown == lines, (keeps, waiting, shown)
        panel.get_by_role("button", name="Cancel").click()
        panel.wait_for(state="detached")

    # Nothing kept and nothing waiting: the question alone. window.ingenceStop opens it too.
    now["job"] = _applying(can_stop=True, stop_keeps=False)
    now["waiting"] = None
    page.evaluate("() => { window.__d4 = window.ingenceStop(); }")
    panel.wait_for()
    assert panel.locator("[data-line]").count() == 0
    panel.get_by_role("button", name="Stop", exact=True).click()
    panel.wait_for(state="detached", timeout=HANG_GUARD_MS)
    assert sent == [{"job_id": "job_rerun", "keep": True}]
    assert page.evaluate("window.__d4.then((answer) => answer.requested)") is True
    assert page.problems == []
    _close(page)


def test_a_stopped_job_reads_stopped_never_failed(browser, served) -> None:
    now: dict[str, Any] = {"job": _applying(can_stop=True), "waiting": WAITING}
    page = _open(browser, served)
    _stubbed(page, now)
    jobs = {
        "api_version": "1.0",
        "run_pinned": None,
        "run_current": None,
        "job_running": False,
        "answered_at": "2026-10-21T20:00:00Z",
        "jobs": [
            {
                "job_id": "job_rerun",
                "kind": "rerun",
                "operations": ["inference"],
                "spellings": [],
                "state": "cancelled",
                "phase": None,
                "retries": 0,
                "attempts": 1,
                "record_sequence": 4,
                "base_run": None,
                "bound_run": None,
                "produced_run": None,
                "error": None,
                "heartbeat_at": None,
                "created_at": "2026-10-21T19:58:00Z",
                "started_at": "2026-10-21T19:58:01Z",
                "ended_at": "2026-10-21T19:59:00Z",
                "events": [
                    {
                        "at": "2026-10-21T19:59:00Z",
                        "state": "cancelled",
                        "phase": None,
                        "message": "stopped by a person",
                    }
                ],
            }
        ],
    }
    page.route(lambda url: "/api/v1/jobs" in url, lambda route: route.fulfill(json=jobs))
    page.reload()
    line = page.locator("#applying")
    line.get_by_text("Applying rules").wait_for(timeout=HANG_GUARD_MS)
    now["job"] = {
        **_applying(),
        "state": "cancelled",
        "phase": "cancelled",
        "phase_label": "Stopped",
        "done": None,
        "total": None,
        "seconds_left": None,
    }
    line.get_by_text("Changes: stopped. Your changes still wait for Re-run.").wait_for(
        timeout=HANG_GUARD_MS
    )
    assert "fail" not in line.inner_text().lower()
    page.goto(f"{served}#changes")
    tag = page.locator(".jobs .run-head .tag").first
    tag.wait_for(timeout=HANG_GUARD_MS)
    assert tag.inner_text() == "Stopped"
    assert "fail" not in page.inner_text(".jobs").lower()
    assert page.problems == []
    _close(page)


# -- the tour -------------------------------------------------------------------------------------

_PLACE_THE_OVERVIEW = """(keys) => {
  for (const key of [...keys].reverse()) {
    const part = document.createElement('div');
    part.dataset.help = key;
    part.className = 'd4-overview-part';
    part.textContent = key;
    part.style.cssText = 'height: 90px; margin: 8px; border: 1px solid;';
    document.getElementById('main').prepend(part);
  }
}"""

_RECORD_SCROLLS = """
  window.__d4Scrolls = [];
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (options) {
    if (this.classList && this.classList.contains('d4-overview-part')) {
      window.__d4Scrolls.push(options && options.behavior);
    }
    return original.call(this, options);
  };
"""


def _tour_ready(page: Any) -> None:
    page.wait_for_function("typeof window.ingenceTour === 'function'", timeout=HANG_GUARD_MS)
    page.wait_for_selector("#main")


def test_the_tour_starts_once_after_the_checks_and_skip_ends_it(browser, served) -> None:
    page = _open(browser, served)
    page.add_init_script(_RECORD_SCROLLS)
    page.reload()
    _tour_ready(page)
    # The quick checks are open: the tour waits for them.
    page.evaluate(
        "() => { const checks = document.createElement('div'); checks.id = 'quick-checks';"
        " checks.dataset.quickChecks = ''; checks.textContent = 'checks';"
        " document.body.prepend(checks); }"
    )
    page.evaluate(_PLACE_THE_OVERVIEW, list(TOUR_KEYS))
    page.wait_for_timeout(600)
    assert page.locator(".help-tour").count() == 0, "not over the quick checks"
    page.evaluate(
        "() => { document.getElementById('quick-checks').hidden = true;"
        " document.dispatchEvent(new Event('ingence:checks-done')); }"
    )
    tour = page.locator(".help-tour")
    tour.wait_for(timeout=HANG_GUARD_MS)
    assert tour.locator(".help-tour-count").inner_text() == "Step 1 of 4"
    assert tour.locator("strong").inner_text() == "1. What was found"
    assert tour.get_attribute("role") == "dialog"
    assert page.evaluate("localStorage.getItem('ingence.tour.v1')") is not None
    assert tour.locator("[data-tour=back]").is_disabled()
    assert page.evaluate("document.activeElement.dataset.tour") == "next"
    tour.locator("[data-tour=next]").click()
    assert page.locator(".help-tour .help-tour-count").inner_text() == "Step 2 of 4"
    page.locator(".help-tour [data-tour=back]").click()
    assert page.locator(".help-tour .help-tour-count").inner_text() == "Step 1 of 4"
    page.locator(".help-tour [data-tour=skip]").click()
    assert page.locator(".help-tour, .help-arrow").count() == 0
    # Without reduced motion, each step scrolls smoothly.
    assert set(page.evaluate("window.__d4Scrolls")) == {"smooth"}

    # Once: a reload with the Overview on the page starts nothing.
    page.reload()
    _tour_ready(page)
    page.evaluate(_PLACE_THE_OVERVIEW, list(TOUR_KEYS))
    page.wait_for_timeout(800)
    assert page.locator(".help-tour").count() == 0

    # "Show the tour" on the "?" sheet runs it again, to Done.
    page.click("#help-button")
    page.get_by_role("button", name="Show the tour").click()
    page.locator(".help-tour").wait_for(timeout=HANG_GUARD_MS)
    for step in (1, 2, 3):
        assert page.locator(".help-tour .help-tour-count").inner_text() == f"Step {step} of 4"
        page.locator(".help-tour [data-tour=next]").click()
    assert page.locator(".help-tour strong").inner_text() == "4. Assumptions and checks"
    page.locator(".help-tour [data-tour=done]").click()
    assert page.locator(".help-tour").count() == 0
    assert page.problems == []
    _close(page)


def test_the_tour_jumps_without_motion_and_escape_skips_it(browser, served) -> None:
    page = _open(browser, served, reduced_motion="reduce")
    page.add_init_script(_RECORD_SCROLLS)
    page.reload()
    _tour_ready(page)
    page.evaluate(_PLACE_THE_OVERVIEW, list(TOUR_KEYS))
    tour = page.locator(".help-tour")
    tour.wait_for(timeout=HANG_GUARD_MS)
    tour.locator("[data-tour=next]").click()
    assert set(page.evaluate("window.__d4Scrolls")) == {"auto"}
    page.keyboard.press("Escape")
    assert page.locator(".help-tour").count() == 0
    assert page.problems == []
    _close(page)
