"""The landing page during a scan, in a real browser.

- Progress in words: the phase (the page's own word for every phase it knows, whatever
  ``phase_label`` says; ``phase_label`` only for a phase it does not know), "Step 2 of 5",
  "2,400 of 3,100", a bar only where the total is known, and the time left only where
  ``seconds_left`` is given. Never a percentage and never an internal table name.
- Stop: shown while ``can_stop``. It asks first, under the progress line, and says "What was read
  so far is kept." only where ``stop_keeps`` is true. Stop posts ``/api/workspace/stop`` with the
  job and ``keep: true``. Cancel and Escape close the question.
- The questions box: before Start scan, the lines are kept on the page and saved as goals
  (``POST /api/goals``) once the project exists. During the scan a line taken out is removed
  (``POST /api/goals/remove``). Each question's matches fill in by phase.
- The top folders: once the walk ends, one sentence from the most common guess and up to three
  names, and a list per folder with the guess chosen. A change posts ``/api/areas/kind``.
- Three panels: what to look at first, from the help catalog.
- Stop ends a real scan within 30 s. This needs the real stop route: while the route is a stub
  (it answers with ``controls.NOT_YET``), the test is skipped and says so.

The routes' answers are stubbed where a test reads a phase the scan of a small folder passes too
fast to see. Every name and number is invented.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    launch_browser,
    need,
    settled,
    start_dashboard,
    stop,
)
from ingence import __version__
from ingence.api.routes import landing

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: Every word that names an internal table, which the progress line never shows.
INTERNAL = ("bindings", "claims", "classes", "findings", "candidates", "questions")


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _pool(base: Path, files: int = 3) -> Path:
    pool = base / "plate-drive"
    for folder in ("Scope_1", "Scope_2", "Reader_A"):
        (pool / folder).mkdir(parents=True)
        for index in range(files):
            (pool / folder / f"2024-03-15 HeLa DAPI {index:04d}.csv").write_text(f"a\n{index}\n")
    return pool


@pytest.fixture
def unread(tmp_path: Path) -> Iterator[tuple[Path, Path, Path, str]]:
    """A server on a project made and not yet read: the writes have a project to go to."""
    pool = _pool(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    project = landing.create_project(
        {"root": str(pool), "name": "Plates", "project": str(tmp_path / "plates")},
        created_at="2026-10-21T10:00:00Z",
    )
    process, address = start_dashboard(project, config)
    try:
        yield tmp_path, pool, project, address
    finally:
        stop(process)


@pytest.fixture
def landed(tmp_path: Path) -> Iterator[tuple[Path, Path, Path, str]]:
    """A server with no project, as ``ingence see`` starts one where none is found."""
    pool = _pool(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    process, address = start_dashboard(None, config)
    try:
        yield tmp_path, pool, config, address
    finally:
        stop(process)


def _close(page: Any) -> None:
    """Close a page whose routes may still be answering: their answers are dropped first."""
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()


def _page(browser: Any, width: int = 1280) -> Any:
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.problems = problems
    return page


def _job(**given: Any) -> dict[str, Any]:
    """A first run as /api/landing/progress says it, running, with the current keys."""
    job: dict[str, Any] = {
        "job_id": "job_first_scan",
        "kind": "first_run",
        "state": "running",
        "phase": "dive",
        "phase_label": "",
        "operations": ["census", "dive", "inference"],
        "started_at": "2026-10-21T19:07:00Z",
        "counters": {"folders_found": 4, "files_found": 9, "bytes_seen": 2_048},
        "step": None,
        "steps": None,
        "done": None,
        "total": None,
        "rate_per_s": None,
        "seconds_left": None,
        "can_stop": False,
        "stop_keeps": False,
    }
    job.update(given)
    return job


def _scanning(page: Any, project: Path, pool: Path, progress: Callable[[], dict[str, Any]]) -> None:
    """The landing page as it is while a first run scans: the state and each second's progress
    answered by the test, everything else by the server."""
    state = {
        "version": __version__,
        "recent": [],
        "projects_parent": str(project.parent),
        "default_years": [1990, "present"],
        "this_year": 2026,
        "carry": [],
        "carried": None,
        "project": {
            "path": str(project),
            "name": "Plates",
            "exists": True,
            "roots": [{"path": str(pool)}],
            "has_run": False,
            "years": [1990, "present"],
        },
    }
    page.route(
        "**/api/landing/state",
        lambda route: route.fulfill(json={**state, "job": progress()}),
    )
    page.route("**/api/landing/progress", lambda route: route.fulfill(json=progress()))


# -- progress in words ----------------------------------------------------------------------------


def test_the_progress_names_the_phase_the_step_the_counts_and_the_time_left(
    browser, unread
) -> None:
    _, pool, project, url = unread
    now = {
        "job": _job(
            phase="dive",
            phase_label="Reading inside files",
            step=2,
            steps=5,
            done=2_400,
            total=3_100,
            seconds_left=240,
        )
    }
    page = _page(browser)
    _scanning(page, project, pool, lambda: now["job"])
    page.goto(url)
    page.wait_for_selector("#ui-progress-said .scan-progress")
    line = page.inner_text("#ui-progress-said .scan-progress-line")
    assert line == "Reading contents · Step 2 of 5 · 2,400 of 3,100 · About 4 minutes left"
    bar = page.locator("#ui-progress-said [role=progressbar]")
    assert bar.count() == 1
    assert bar.get_attribute("aria-valuenow") == "2400"
    assert bar.get_attribute("aria-valuemax") == "3100"
    assert bar.get_attribute("aria-valuetext") == "2,400 of 3,100"
    said = page.inner_text("#ui-progress")
    assert "%" not in said
    for word in INTERNAL:
        assert word not in said.lower(), word

    # No total: the count so far and no bar. No seconds left: no time. No label: the page's word.
    now["job"] = _job(phase="census", phase_label="", step=1, steps=3, done=812)
    page.wait_for_function(
        "document.querySelector('#ui-progress-said .scan-progress-line')"
        "?.innerText === 'Listing files · Step 1 of 3 · 812 so far'",
        timeout=HANG_GUARD_MS,
    )
    assert page.locator("#ui-progress-said [role=progressbar]").count() == 0
    assert "left" not in page.inner_text("#ui-progress-said")
    # The contract's phase names (``names``, ``rules``) read the same.
    now["job"] = _job(phase="rules", phase_label="", step=3, steps=3, done=21, total=48)
    page.wait_for_function(
        "document.querySelector('#ui-progress-said .scan-progress-line')"
        "?.innerText === 'Applying rules · Step 3 of 3 · 21 of 48'",
        timeout=HANG_GUARD_MS,
    )
    assert page.locator("#ui-progress-said [role=progressbar]").count() == 1
    # A phase the page does not know: the engine's phase_label.
    now["job"] = _job(phase="unknown_phase", phase_label="Checking copies", done=5, total=9)
    page.wait_for_function(
        "document.querySelector('#ui-progress-said .scan-progress-line')"
        "?.innerText === 'Checking copies · 5 of 9'",
        timeout=HANG_GUARD_MS,
    )
    assert "stop Ingence in its terminal" not in page.inner_text("main")
    assert page.problems == []
    _close(page)


# -- Stop -----------------------------------------------------------------------------------------


def test_stop_asks_first_and_says_only_what_is_true(browser, unread) -> None:
    _, pool, project, url = unread
    now = {"job": _job(can_stop=False)}
    page = _page(browser)
    _scanning(page, project, pool, lambda: now["job"])
    sent: list[dict[str, Any]] = []

    def stopped(route: Any) -> None:
        sent.append(json.loads(route.request.post_data))
        route.fulfill(
            status=201,
            json={
                "job_id": "job_first_scan",
                "keep": True,
                "requested": True,
                "state": "running",
                "stop_keeps": True,
                "said": "",
                "recorded": True,
            },
        )

    page.route("**/api/workspace/stop", stopped)
    page.goto(url)
    page.wait_for_selector("#ui-progress-said")
    # Stop only while the engine says the job can stop.
    assert not page.is_visible("#ui-stop")
    assert "Closing this page does not stop the scan." in page.inner_text("#ui-stop-row")
    now["job"] = _job(can_stop=True, stop_keeps=True)
    page.wait_for_selector("#ui-stop", state="visible", timeout=HANG_GUARD_MS)

    # Asked first, in a panel under the progress line; kept only where it is.
    page.click("#ui-stop")
    panel = page.locator("#stop-confirm")
    panel.wait_for()
    assert page.locator("dialog[open]").count() == 0, "never a pop-up"
    assert panel.locator(".stop-confirm-head").inner_text() == "Stop the scan?"
    assert panel.locator("[data-line=keeps]").inner_text() == "What was read so far is kept."
    assert panel.locator("[data-line=waits]").count() == 0, "no changes wait before a first scan"
    below = page.evaluate(
        "document.querySelector('#ui-stop-row').compareDocumentPosition("
        "document.querySelector('#stop-confirm')) & Node.DOCUMENT_POSITION_FOLLOWING"
    )
    assert below
    panel.get_by_role("button", name="Cancel").click()
    panel.wait_for(state="detached")
    assert sent == []

    # Escape is Cancel; and with stop_keeps false (read when Stop is pressed), the line is not said.
    now["job"] = _job(can_stop=True, stop_keeps=False)
    page.click("#ui-stop")
    panel.wait_for()
    assert panel.locator("[data-line=keeps]").count() == 0
    page.keyboard.press("Escape")
    panel.wait_for(state="detached")

    # Stop posts the stop route with the job and keep: true.
    page.click("#ui-stop")
    panel.get_by_role("button", name="Stop", exact=True).click()
    panel.wait_for(state="detached", timeout=HANG_GUARD_MS)
    assert sent == [{"job_id": "job_first_scan", "keep": True}]
    assert page.problems == []
    _close(page)


def test_a_stop_the_engine_does_not_take_says_what_it_said(browser, unread) -> None:
    """Through the server's own stop route: what it answers is said in the panel, which then
    closes on Close."""
    _, pool, project, url = unread
    page = _page(browser)
    _scanning(page, project, pool, lambda: _job(can_stop=True))
    answered: list[dict[str, Any]] = []

    def through(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        answered.append(body)
        # Said as the engine said it, whether or not it stopped.
        route.fulfill(
            response=response,
            json={**body, "requested": False, "said": "The engine did not stop it (d4)."},
        )

    page.route("**/api/workspace/stop", through)
    page.goto(url)
    page.click("#ui-stop")
    panel = page.locator("#stop-confirm")
    panel.get_by_role("button", name="Stop", exact=True).click()
    panel.locator(".stop-confirm-said").get_by_text("The engine did not stop it (d4).").wait_for()
    assert answered and answered[0]["job_id"] == "job_first_scan" and answered[0]["keep"] is True
    panel.get_by_role("button", name="Close").click()
    panel.wait_for(state="detached")
    assert page.problems == []
    _close(page)


def test_stop_ends_a_real_scan_within_30_seconds(browser, tmp_path: Path) -> None:
    """Needs the real stop route. A folder of 2,400 small files, scanned at Standard; Stop
    is pressed while it scans, and the job ends Stopped within 30 s. Skipped while the engine
    never says ``can_stop``."""
    pool = _pool(tmp_path, files=800)
    config = tmp_path / "config"
    config.mkdir()
    process, url = start_dashboard(None, config)
    page = _page(browser)
    try:
        page.goto(url)
        page.fill("#ui-root", str(pool))
        page.click("#ui-check-button")
        page.wait_for_selector("#ui-check .ui-note")
        page.fill("#ui-where", str(tmp_path / "projects" / "stopping"))
        page.dispatch_event("#ui-where", "change")
        page.click("#ui-read")
        page.wait_for_selector("#ui-progress, #pages", timeout=HANG_GUARD_MS)
        if _stop_route_is_a_stub(page):
            pytest.skip("needs the real stop route: /api/workspace/stop is a stub in this build")
        lines: list[str] = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = page.evaluate("fetch('/api/landing/progress').then((r) => r.json())")
            if job.get("state") not in ("queued", "running"):
                break
            if page.locator("#ui-progress-said").count():
                lines.append(page.inner_text("#ui-progress-said"))
            if job.get("can_stop") and page.is_visible("#ui-stop"):
                break
            page.wait_for_timeout(250)
        assert page.locator("#ui-stop").count() and page.is_visible("#ui-stop"), (
            f"Stop never showed while the scan ran (it ended {job.get('state')!r}); "
            "a real stop route says can_stop while a scan runs"
        )
        # The progress named its phase and its counts while it ran.
        labels = ("Listing files", "Reading contents", "Reading names", "Applying rules")
        assert any(label in line for line in lines for label in labels), lines
        assert any(re.search(r"\d+ (of \d+|so far)", line) for line in lines), lines
        page.click("#ui-stop")
        page.locator("#stop-confirm").get_by_role("button", name="Stop", exact=True).click()
        began = time.monotonic()
        while time.monotonic() - began < 30:
            job = page.evaluate("fetch('/api/landing/progress').then((r) => r.json())")
            if job.get("state") not in ("queued", "running"):
                break
            page.wait_for_timeout(250)
        assert job.get("state") == "cancelled", job
        assert time.monotonic() - began < 30
    finally:
        _close(page)
        stop(process)


def _stop_route_is_a_stub(page: Any) -> bool:
    """Whether the server's stop route is a stub: asked to stop a job no ledger holds, it
    answers with the stub's own sentence (``controls.NOT_YET``), which a real route does not say."""
    from ingence.api.routes import controls

    not_yet = getattr(controls, "NOT_YET", None)
    if not not_yet:
        return False
    answer = page.evaluate(
        """() => fetch('/api/workspace/stop', {method: 'POST',
             headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
             body: JSON.stringify({job_id: 'job_probe', keep: true})})
           .then((response) => response.json()).catch(() => ({}))"""
    )
    return answer.get("said") == not_yet


# -- the questions box ----------------------------------------------------------------------------


def test_the_questions_typed_before_the_project_are_saved_as_goals_once_it_exists(
    browser, landed
) -> None:
    base, pool, _, url = landed
    page = _page(browser)
    posted: list[tuple[str, dict[str, Any]]] = []
    page.on(
        "request",
        lambda request: (
            posted.append((request.url.removeprefix(url), json.loads(request.post_data or "{}")))
            if request.method == "POST"
            else None
        ),
    )
    page.goto(url)
    page.wait_for_selector("#ui-goals")
    assert page.inner_text("label[for=ui-goals]") == (
        "What do you want to find out from this data?"
    )
    assert page.inner_text("#ui-goals-help") == (
        "Write one question per line. You can leave this empty."
    )
    page.fill("#ui-root", str(pool))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    page.fill("#ui-where", str(base / "projects" / "plates"))
    page.dispatch_event("#ui-where", "change")
    page.fill(
        "#ui-goals", "HeLa DAPI timecourses\n\n  Plate reader titrations  \nHeLa DAPI timecourses"
    )
    # Nothing is saved before the project exists.
    assert not [path for path, _ in posted if path.startswith("api/goals")]
    with page.expect_response(
        lambda response: response.url.endswith("/api/landing/create"), timeout=HANG_GUARD_MS
    ) as created:
        page.click("#ui-read")
    made = created.value.json()
    # The questions go with the project: once each, blank lines and spaces left out.
    create = next(body for path, body in posted if path == "api/landing/create")
    assert create["goals"] == ["HeLa DAPI timecourses", "Plate reader titrations"]
    # The route saves them as the project's goals and answers each with its id ...
    assert [goal["text"] for goal in made["goals"]] == create["goals"]
    assert all(goal["id"] for goal in made["goals"])
    served = url if made["url"] in ("", "/") else made["url"]
    with urllib.request.urlopen(f"{served.rstrip('/')}/api/v1/goals", timeout=30) as answer:
        kept = json.loads(answer.read())["goals"]
    assert sorted(goal["text"] for goal in kept) == sorted(create["goals"])
    # ... so the page posts none of them again.
    settled(page)
    assert not [path for path, _ in posted if path.startswith("api/goals")], posted
    assert page.problems == []
    _close(page)


def test_a_question_taken_out_during_the_scan_is_removed(browser, unread) -> None:
    _, pool, project, url = unread
    page = _page(browser)
    _scanning(page, project, pool, lambda: _job(phase="census"))
    saved: list[str] = []
    removed: list[str] = []

    def goal(route: Any) -> None:
        text = json.loads(route.request.post_data)["text"]
        saved.append(text)
        route.fulfill(
            status=201,
            json={
                "goal": {"id": f"goal_scan_{len(saved)}", "text": text},
                "said": "",
                "recorded": True,
            },
        )

    def remove(route: Any) -> None:
        removed.append(json.loads(route.request.post_data)["id"])
        route.fulfill(
            status=201, json={"id": removed[-1], "removed": True, "said": "", "recorded": True}
        )

    page.route("**/api/goals", goal)
    page.route("**/api/goals/remove", remove)
    page.goto(url)
    page.wait_for_selector("#ui-goals-live")
    page.fill("#ui-goals-live", "Western blot actin\nConfocal z-stacks")
    page.dispatch_event("#ui-goals-live", "change")
    deadline = time.monotonic() + 30
    while len(saved) < 2 and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert saved == ["Western blot actin", "Confocal z-stacks"]
    page.fill("#ui-goals-live", "Confocal z-stacks")
    page.dispatch_event("#ui-goals-live", "change")
    deadline = time.monotonic() + 30
    while not removed and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert removed == ["goal_scan_1"]
    assert saved == ["Western blot actin", "Confocal z-stacks"], "nothing saved twice"
    assert page.problems == []
    _close(page)


def _goals_answer(phase: str | None) -> dict[str, Any]:
    """An invented answer of /api/v1/goals: one question, two matches."""
    return {
        "api_version": "1.0",
        "schema_version": "1.24",
        "run_id": "run_scan",
        "phase": phase,
        "goals": [
            {
                "id": "goal_scan_1",
                "text": "nuclear stain timecourses",
                "read": {"kept": ["nuclear", "stain"], "dropped": [], "unknown": []},
                "expanded": [{"word": "stain", "to": ["DAPI", "Hoechst"], "how": "a meaning"}],
                "matches": [
                    {
                        "experiment_id": "entry_d4a",
                        "label": "2024-03-15 HeLa DAPI",
                        "tier": "named",
                        "why": "its name holds dapi",
                    },
                    {
                        "experiment_id": "entry_d4b",
                        "label": "2024-03-22 HeLa DAPI rep2",
                        "tier": "associated",
                        "why": "a repeat of a named match",
                    },
                ],
                "summary": {
                    "experiments": 2,
                    "named": 1,
                    "associated": 1,
                    "same_theme": 0,
                    "first": "2024-03-15",
                    "last": "2024-03-22",
                    "raw_with_analysis": 1,
                    "of": 2,
                    "repeated_groups": 1,
                    "seen_once": 0,
                },
            }
        ],
    }


@pytest.mark.parametrize(
    ("phase", "said"),
    [
        ("listing", "Ingence will look for: nuclear, stain, DAPI, Hoechst"),
        ("names", "Matches by name: 2024-03-15 HeLa DAPI"),
        (None, "2 experiments match: 1 by name, 1 by related words, 0 in the same theme."),
    ],
)
def test_each_question_fills_in_by_phase(browser, unread, phase, said) -> None:
    _, pool, project, url = unread
    page = _page(browser)
    job_phase = {"listing": "census", "names": "binding"}.get(phase or "", "views")
    _scanning(page, project, pool, lambda: _job(phase=job_phase))
    page.route("**/api/v1/goals", lambda route: route.fulfill(json=_goals_answer(phase)))
    page.route(
        "**/api/goals",
        lambda route: route.fulfill(
            status=201,
            json={"goal": {"id": "goal_scan_1", "text": "x"}, "said": "", "recorded": True},
        ),
    )
    page.goto(url)
    page.wait_for_selector("#ui-goals-live")
    page.fill("#ui-goals-live", "nuclear stain timecourses")
    page.dispatch_event("#ui-goals-live", "change")
    page.locator("#ui-goal-results").get_by_text(said).wait_for(timeout=HANG_GUARD_MS)
    assert page.inner_text("#ui-goal-results .ui-goal-text") == "nuclear stain timecourses"
    if phase is None:
        card = page.inner_text("#ui-goal-results")
        assert "From 2024-03-15 to 2024-03-22." in card
        assert "Raw files analyzed: 1 of 2 experiments." in card
        assert "2024-03-22 HeLa DAPI rep2 · by related words" in card
    assert page.problems == []
    _close(page)


# -- the top folders ------------------------------------------------------------------------------


def _areas_answer() -> dict[str, Any]:
    rows = [
        ("area_scan_1", "Scope_1", "instrument", 120),
        ("area_scan_2", "alice", "person", 90),
        ("area_scan_3", "Scope_2", "instrument", 60),
        ("area_scan_4", "Reader_A", "instrument", 30),
        ("area_scan_5", "Scope_3", "instrument", 10),
    ]
    return {
        "api_version": "1.0",
        "schema_version": "1.24",
        "run_id": "run_scan",
        "areas": [
            {"id": area_id, "name": name, "kind": kind, "kind_assumed": True, "files": files}
            for area_id, name, kind, files in rows
        ],
    }


def test_once_the_walk_ends_the_page_asks_what_the_top_folders_are(browser, unread) -> None:
    _, pool, project, url = unread
    now = {"job": _job(phase="census")}
    page = _page(browser)
    _scanning(page, project, pool, lambda: now["job"])
    asked: list[str] = []

    def areas(route: Any) -> None:
        asked.append(route.request.url)
        route.fulfill(json=_areas_answer())

    kinds: list[dict[str, Any]] = []

    def kind(route: Any) -> None:
        kinds.append(json.loads(route.request.post_data))
        route.fulfill(
            status=201, json={**kinds[-1], "answer_target": "area:x", "said": "", "recorded": True}
        )

    page.route("**/api/v1/areas", areas)
    page.route("**/api/areas/kind", kind)
    page.goto(url)
    page.wait_for_selector("#ui-progress-said")
    # While the walk lists the files, nothing is asked.
    page.evaluate("new Promise((done) => setTimeout(done, 3500))")
    assert not page.is_visible("#ui-areas")
    assert asked == []
    now["job"] = _job(phase="dive")
    page.wait_for_selector("#ui-areas .ui-areas-guess", timeout=HANG_GUARD_MS)
    assert page.inner_text("#ui-areas .ui-areas-guess") == (
        "Your top folders look like instruments (Scope_1, Scope_2, Reader_A). Change if wrong."
    )
    assert page.input_value("select[data-area=area_scan_1]") == "instrument"
    assert page.input_value("select[data-area=area_scan_2]") == "person"
    assert page.inner_text("label[for=ui-area-1]") == "alice"
    page.select_option("select[data-area=area_scan_2]", "instrument")
    page.locator("[data-area-row=area_scan_2] .ui-area-said").get_by_text("Saved.").wait_for()
    assert kinds == [{"area_id": "area_scan_2", "kind": "instrument"}]
    # Asked once: the lists are never drawn again under a person's hand.
    page.evaluate("new Promise((done) => setTimeout(done, 3500))")
    assert len(asked) == 1
    assert page.problems == []
    _close(page)


# -- what to look at first ------------------------------------------------------------------------


def test_three_panels_say_what_to_look_at_first(browser, unread) -> None:
    _, pool, project, url = unread
    page = _page(browser)
    _scanning(page, project, pool, lambda: _job())
    page.goto(url)
    page.wait_for_selector("#ui-first .ui-first-card h3")
    catalog = page.evaluate("fetch('/help/catalog.json').then((r) => r.json())")
    titles = page.locator("#ui-first .ui-first-card h3").all_inner_texts()
    assert titles == ["Overview", "Overlap", "Experiments"]
    texts = page.locator("#ui-first .ui-first-card p").all_inner_texts()
    keys = ("landing.first.overview", "landing.first.overlap", "landing.first.experiments")
    assert texts == [catalog["entries"][key]["text"] for key in keys]
    assert page.inner_text("#ui-first-head") == "What to look at first"
    assert page.problems == []
    _close(page)


# -- the reading depth ----------------------------------------------------------------------------


def test_a_project_that_reads_names_only_offers_quick_alone(browser, unread) -> None:
    *_, url = unread
    page = _page(browser)

    def state(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        body["project"]["dive_opens_files"] = False
        route.fulfill(response=response, json=body)

    page.route("**/api/landing/state", state)
    page.goto(url)
    page.wait_for_selector("#ui-setup")
    assert page.is_checked("#ui-setup-depth-quick")
    assert page.is_disabled("#ui-setup-depth-standard")
    assert page.is_disabled("#ui-setup-depth-deep")
    assert "This project reads names only, so Quick is the only choice." in page.inner_text(
        "#ui-setup"
    )
    with page.expect_request("**/api/landing/first-run") as asked:
        page.click("#ui-setup-read")
    sent = json.loads(asked.value.post_data)
    assert sent["dive"] is False
    assert sent["depth"] == "quick"
    _close(page)
