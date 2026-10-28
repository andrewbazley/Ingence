"""The consolidated Dashboard driven in a real browser.

Beside ``test_browser.py`` (the Graph page and job recovery), and skipped the same way where the
``browser`` extra or a browser is missing. Every served Dashboard here keeps who decides in a
folder of the test's own (``INGENCE_CONFIG_HOME``), never in the person's configuration.

1. What is being looked at is kept: an entry named in the address is named under the header. The
   Graph page centres on it, Questions and History keep it, and a reload keeps it.
2. A dialog is labelled, keeps focus, closes on Escape and gives focus back.
3. A restore writes only after its preview is confirmed: Escape at the question writes nothing,
   and confirming makes every page read the earlier run.
4. A settled word is corrected from its page: the meaning is checked before it is sent, the
   person confirms, and the Record holds the person's meaning with its scope.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser, settled

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]

#: How long a wait for a request or a state gives up. This is a guard against a hang, not the time
#: the page is expected to take: every wait returns the moment its state is reached.
HANG_GUARD_MS = 120_000


def _shots(page: Any, name: str) -> None:
    """Keep a screenshot where ``INGENCE_BROWSER_SCREENSHOTS`` names a folder (invented names)."""
    folder = os.environ.get("INGENCE_BROWSER_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_dashboard")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt", "2024-03-07_vorn"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2, 3):
            (pool / folder / f"zib_glorbex_wd_{index:02d}.csv").write_text(f"a\n{index}\n")
        (pool / folder / "empty.txt").write_text("")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return project.path


@pytest.fixture
def served(built: Path, tmp_path: Path):
    import shutil

    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
    port = _free_port()
    process = _serve(project, port, config)
    try:
        yield project, f"http://127.0.0.1:{port}/", config
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serve(project: Path, port: int, config: Path) -> subprocess.Popen[bytes]:
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from ingence.api.server import DashboardServer\n"
        "server = DashboardServer(Path(sys.argv[1]), port=int(sys.argv[2]))\n"
        "server.start()\n"
        "print('serving', flush=True)\n"
        "while True:\n"
        "    time.sleep(60)\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY / "src"),
        "INGENCE_CONFIG_HOME": str(config),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(project), str(port)],
        env=environment,
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stdout.readline().startswith(b"serving")
    return process


def _entry_id(project: Path, name: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        found = catalog.query("SELECT entry_id FROM entries WHERE name = ?", [name]).fetchone()
    assert found is not None
    return str(found[0])


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


def _name_the_decider(page: Any, name: str) -> None:
    dialog = page.locator("dialog.ingence-dialog")
    dialog.get_by_label("Your name").fill(name)
    dialog.get_by_role("button", name="Use this name").click()


def test_a_page_left_while_its_list_loads_never_draws_over_the_next(browser, served) -> None:
    """The questions' rows, answered after a person moved on to History, are dropped rather than
    drawn over History's list (a race that appeared under load)."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    held: list[Any] = []
    try:
        page.goto(f"{base}#changes")
        page.locator("#list").get_by_text("Scans and their answers").wait_for()
        page.route("**/api/v1/questions/ranked*", lambda route: held.append(route))
        # Questions opens on what the engine concluded, read from the ranked questions.
        # Questions is a section of Advanced, which opens on Rules.
        page.locator('#pages button[data-page="advanced"]').click()
        page.wait_for_function("location.hash === '#rules'")
        with page.expect_request("**/api/v1/questions/ranked*", timeout=HANG_GUARD_MS):
            page.locator('nav.sections button[data-view="review"]').click()
        page.wait_for_function("location.hash === '#review'")
        assert held, "the ranked questions were asked for"
        page.locator('#pages button[data-page="history"]').click()
        page.locator("#list").get_by_text("Scans and their answers").wait_for()
        late = [route.request for route in held]
        for route in held:
            route.continue_()
        # The late answers have come and the page has had its turn with each, then it is quiet.
        for request in late:
            response = request.response()
            if response is not None:
                response.finished()
        settled(page)
        assert page.locator("#list").get_by_text("Scans and their answers").is_visible()
    finally:
        page.close()


def test_what_is_looked_at_is_kept_across_pages_and_a_reload(browser, served) -> None:
    project, base, _ = served
    folder = _entry_id(project, "2024-03-06_kelt")
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}?entry={folder}#search")
        focus = page.locator("#focus")
        focus.get_by_text("2024-03-06_kelt").wait_for()
        # The inspector shows it beside the page, with the rule and version of each conclusion.
        page.locator("#detail").get_by_text("What the rules said about it").wait_for()
        _shots(page, "explore-focus-inspector")
        # The Explorer is a section of Advanced. It centers on what was looked at last, and the
        # one-hop page, Connections, on the same folder.
        page.locator('#pages button[data-page="advanced"]').click()
        page.wait_for_function("location.hash === '#rules'")
        page.locator('nav.sections button[data-view="explorer"]').click()
        page.wait_for_selector(".ix-root .ix-canvas-wrap")
        page.wait_for_function(f"location.search.includes('focus={folder}')")
        page.locator('nav.sections button[data-view="graph"]').click()
        page.wait_for_selector("table.graph-table")
        assert f"center={folder}" in page.url
        assert "2024-03-06_kelt" in page.locator('[data-graph="title"]').inner_text().lower()
        # What is looked at belongs to the page it was set on, so Checks and History do not name
        # it, and the page it was set on (the Overview, which holds Ask) does again on returning.
        page.locator('#pages button[data-page="checks"]').click()
        page.wait_for_function("location.hash.startsWith('#checks')")
        assert focus.is_hidden()
        assert f"entry={folder}" not in page.url
        page.locator('#pages button[data-page="history"]').click()
        page.locator("#list").get_by_text("Scans and their answers").wait_for()
        assert focus.is_hidden()
        page.locator('#pages button[data-page="overview"]').click()
        focus.get_by_text("2024-03-06_kelt").wait_for()
        assert f"entry={folder}" in page.url
        page.reload()
        focus.get_by_text("2024-03-06_kelt").wait_for()
        assert f"entry={folder}" in page.url
        # Put down, it leaves the address and the line.
        focus.get_by_role("button", name="put it down").click()
        page.wait_for_function("!location.search.includes('entry=')")
        assert focus.is_hidden()
    finally:
        page.close()


def test_a_dialog_is_labelled_closes_on_escape_and_gives_focus_back(browser, served) -> None:
    _, base, config = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#inventory")
        opener = page.locator("#decider").get_by_role("button", name="set your name")
        opener.click()
        dialog = page.get_by_role("dialog", name="What is your name?")
        dialog.wait_for()
        assert page.evaluate("document.activeElement.tagName") == "INPUT"
        # Checked before it is sent: nothing typed is refused beside the field.
        dialog.get_by_role("button", name="Use this name").click()
        dialog.get_by_role("alert").get_by_text("Write something first").wait_for()
        # Tab goes round the dialog's own controls and never leaves it.
        for _ in range(6):
            page.keyboard.press("Tab")
            assert page.evaluate("!!document.activeElement.closest('dialog')")
        page.keyboard.press("Escape")
        dialog.wait_for(state="detached")
        assert page.evaluate("document.activeElement.textContent") == "set your name"
        assert not list(config.iterdir()), "Escape wrote nothing"
    finally:
        page.close()


def test_a_restore_writes_only_after_its_preview_is_confirmed(browser, served) -> None:
    project, base, _ = served
    from ingence.storage.commit import read_pointer

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        # A decision made after the census, which going back to the census sets aside (a decision,
        # not a bare note: a restore never withdraws a note, which decides nothing).
        page.goto(f"{base}#inventory")
        written = page.evaluate(
            """async () => {
                const response = await fetch('/api/review/answer', {method: 'POST',
                  headers: {'Content-Type': 'application/json', 'X-Ingence-Write': '1'},
                  body: JSON.stringify({actor: 'vorn', target: 'question:content_unreadable:empty',
                    word: 'cannot_say', note: 'looked at them'})});
                return response.status;
            }"""
        )
        assert written == 201
        before = read_pointer(project)
        assert before is not None
        page.goto(f"{base}#changes")
        page.get_by_role("button", name="restore this scan...").first.click()
        detail = page.locator("#detail")
        detail.get_by_text("Going back to this scan").wait_for()
        detail.get_by_text("1 answer given since").wait_for()
        _shots(page, "history-restore-preview")
        detail.get_by_role("button", name="restore exactly this").click()
        question = page.get_by_role("dialog", name="Restore exactly this?")
        question.wait_for()
        page.keyboard.press("Escape")
        question.wait_for(state="detached")
        after_escape = read_pointer(project)
        assert after_escape is not None
        assert after_escape.run_id == before.run_id
        detail.get_by_role("button", name="restore exactly this").click()
        page.get_by_role("dialog", name="Restore exactly this?").get_by_role(
            "button", name="Restore this scan"
        ).click()
        _name_the_decider(page, "vorn")
        page.get_by_text("Restored: every page reads Scan 1 now").wait_for()
        restored = read_pointer(project)
        assert restored is not None
        assert restored.run_id == "run_20260101T000001Z_111111111111"
        assert _events(project)[-1].kind.value == "withdraw"
    finally:
        page.close()


def test_a_settled_word_is_corrected_from_its_page(browser, served) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}?word=wd#review")
        detail = page.locator("#detail")
        detail.get_by_text("It means working distance").wait_for()
        detail.get_by_role("button", name="record this meaning").click()
        detail.get_by_text("Write what the word means first.").wait_for()
        detail.get_by_label("means").fill("trell disc")
        detail.get_by_label("a kind of").select_option("condition")
        detail.get_by_label("where").select_option("2024-03-05_kelt")
        detail.get_by_role("button", name="record this meaning").click()
        _shots(page, "word-correction-confirm")
        page.get_by_role("dialog", name='Give "wd" your own meaning?').get_by_role(
            "button", name="Record it"
        ).click()
        _name_the_decider(page, "kelt")
        detail.get_by_text("Saved as answer").wait_for()
        event = _events(project)[-1]
        payload = json.loads(event.payload)
        # A correction below a folder is kept on a target of its own (one per word and place).
        assert (event.kind.value, event.target, event.actor) == (
            "define",
            "term:wd below 2024-03-05_kelt",
            "kelt",
        )
        assert event.scope.token == "folder:2024-03-05_kelt"
        assert payload["corrects"]["term"] == "working_distance"
    finally:
        page.close()


def test_view_in_graph_from_explore_and_the_file_inspector_survives_a_reload(
    browser, served
) -> None:
    """The graph's first acceptance point: a link from the inspector opens the exact selected
    centre, for a folder and for a file (the file itself), and the centre survives a reload. The
    folder opens in the inspector beside Ask, as a link to it does."""
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        folder = _entry_id(project, "2024-03-05_kelt")
        page.goto(f"{base}?entry={folder}#ask")
        detail = page.locator("#detail")
        detail.get_by_text("What the rules said about it").wait_for()
        detail.get_by_role("button", name="Show connections").click()
        page.wait_for_selector("table.graph-table")
        assert page.evaluate("new URLSearchParams(location.search).get('center')") == folder
        title = page.locator('[data-graph="title"]').inner_text().lower()
        page.reload()
        page.wait_for_selector("table.graph-table")
        assert page.evaluate("new URLSearchParams(location.search).get('center')") == folder
        assert page.locator('[data-graph="title"]').inner_text().lower() == title

        # From the file inspector: the file's own identifier is the centre (an old #search
        # address opens Ask, with the inspector beside it).
        file_id = _entry_id(project, "zib_glorbex_wd_02.csv")
        page.goto(f"{base}?entry={file_id}#search")
        detail = page.locator("#detail")
        detail.get_by_text("The words of its name").wait_for()
        detail.get_by_role("button", name="Show connections").click()
        page.wait_for_selector("table.graph-table")
        assert page.evaluate("new URLSearchParams(location.search).get('center')") == file_id
        assert "zib_glorbex_wd_02.csv" in page.locator('[data-graph="title"]').inner_text().lower()
        page.reload()
        page.wait_for_selector("table.graph-table")
        assert page.evaluate("new URLSearchParams(location.search).get('center')") == file_id
    finally:
        page.close()


def test_words_are_suggested_as_typed_and_enter_asks_the_question(browser, served) -> None:
    """The words the names use appear under the box as a person types, and one is chosen by
    keyboard. Enter opens Ask with the words typed."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    asked: list[str] = []
    page.on(
        "request",
        lambda request: asked.append(request.url) if "/api/" in request.url else None,
    )
    try:
        page.goto(f"{base}#inventory")
        box = page.locator("#search-box")
        box.press_sequentially("glor", delay=40)
        suggestion = page.locator("#search-suggest li").first
        suggestion.wait_for()
        assert "glorbex" in suggestion.inner_text()
        box.press("ArrowDown")
        box.press("Enter")
        assert box.input_value() == "glorbex "
        box.press("Enter")
        page.wait_for_function("location.hash === '#ask?q=glorbex'")
        page.wait_for_selector("h2[data-ask-view]")
        assert page.text_content("h2[data-ask-view]") == "Ask: glorbex"
        assert any("/api/search/suggest?prefix=glor" in url for url in asked)
        assert any("/api/v1/ask?q=glorbex" in url for url in asked)
        # Nothing asks for the search results any more, before Enter or after it.
        assert not any("/api/search/stream" in url for url in asked)
    finally:
        page.close()


@pytest.fixture(scope="module")
def experiments_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The experiment rules' own invented pool, where groups of experiments are proposed."""
    from fixtures.revision_4 import each_deviation, write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from test_experiment_folders import _pool

    base = tmp_path_factory.mktemp("browser_groupings")
    created = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    # The clustering's proposals, each asked on its own (``derivations: each_deviation``). Under
    # the lattice, the default, a proposal whose core the lattice asks is deferred to the
    # lattice's own question.
    each_deviation(write_revision(created.path))
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created.path


def test_a_proposed_group_s_words_are_approved_and_shown_as_a_core(
    browser, experiments_built: Path, tmp_path: Path
) -> None:
    import shutil

    project = tmp_path / "project"
    shutil.copytree(experiments_built, project)
    config = tmp_path / "config"
    config.mkdir()
    port = _free_port()
    process = _serve(project, port, config)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"http://127.0.0.1:{port}/#classes")
        asked = page.locator("#list tr.row", has=page.locator(".tag.now", has_text="experiments"))
        asked.first.click()
        detail = page.locator("#detail")
        detail.get_by_text("Or approve the words they share as a group").wait_for()
        boxes = detail.locator(".approve-core input[type=checkbox]")
        words = sorted(boxes.evaluate_all("(all) => all.map((box) => box.value)"))
        assert len(words) >= 2
        _shots(page, "groupings-approve-core")
        detail.get_by_role("button", name="approve these words as a group").click()
        _name_the_decider(page, "trell")
        page.locator("#detail").get_by_text("Saved as answer").wait_for()
        event = _events(project)[-1]
        assert (event.kind.value, event.actor) == ("confirm", "trell")
        assert event.target == "group:" + " ".join(words)
        # The Groupings page lists the approved core, and says the run has not resolved it.
        page.locator("#list .resolutions").get_by_text(" ".join(words)).wait_for()
        _shots(page, "groupings-approved-core-shown")
    finally:
        page.close()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


# -- API additions, shown where the pages meet them ------------------------------------------------
# Each route is answered with an invented payload, so the page's words are tested apart from
# what makes the state (a silent worker, a settings filter, a move a dive undid), which the
# engine's own tests hold.


def _answered_with(page: Any, pattern: str, change: Any) -> None:
    """Answer ``pattern`` with the server's own answer, changed by ``change`` (a dict in, out)."""

    def handle(route: Any) -> None:
        found = route.fetch()
        body = change(json.loads(found.text()) if found.ok else {})
        route.fulfill(
            status=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=json.dumps(body),
        )

    page.route(pattern, handle)


def test_the_job_banner_says_what_a_silence_will_lead_to_and_who_holds_a_job_back(
    browser, served
) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    said = (
        "Its worker has not reported for 75 s. A long step can keep it quiet; if it "
        "stays silent for 900 s it will be stopped and run again (jobs.stop_silent_seconds)."
    )
    _answered_with(
        page,
        "**/api/applying",
        lambda _: {
            "phase": "binding",
            "state": "running",
            "spellings": ["kelt"],
            "elapsed_seconds": 80,
            "silent_seconds": 75.0,
            "silent_stops_at_seconds": 900,
            "silent_said": said,
        },
    )
    try:
        page.goto(f"{base}#inventory")
        banner = page.locator("#applying")
        banner.get_by_text("it will be stopped and run again").wait_for()
        page.unroute("**/api/applying")
        held = "Waiting: ingence infer (process 4242) is publishing runs in this project"
        _answered_with(
            page,
            "**/api/applying",
            lambda _: {
                "phase": "queued",
                "state": "queued",
                "queued": ["kelt"],
                "held_by": {"command": "ingence infer", "pid": 4242},
                "held_said": held + ", and a job started now would publish over it.",
            },
        )
        page.goto(f"{base}?held=1#inventory")
        page.locator("#applying").get_by_text(held).wait_for()
    finally:
        page.close()


def test_coverage_says_what_the_settings_left_unread_and_why(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})

    def settings_filter(found: dict[str, Any]) -> dict[str, Any]:
        found["reached"]["set_aside_by_settings"] = 3
        found["reached"]["set_aside_by_settings_because"] = {
            "the settings exclude this extension": 2,
            "above the settings' size limit": 1,
        }
        return found

    _answered_with(page, "**/api/coverage*", settings_filter)
    try:
        page.goto(f"{base}#coverage")
        line = page.locator("#list .set-aside-by-settings")
        line.wait_for()
        text = line.inner_text()
        assert "3 left unread by this project's extraction settings" in text
        assert "2 because the settings exclude this extension" in text
        assert "1 because above the settings' size limit" in text
    finally:
        page.close()


def test_a_restore_preview_lists_the_rule_settings_it_changes_back(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})

    def reverted(found: dict[str, Any]) -> dict[str, Any]:
        found["rules_reverted"] = {"kelt_rule": {"minimum_files": [5, 3]}}
        return found

    _answered_with(page, "**/api/v1/restore/preview*", reverted)
    try:
        page.goto(f"{base}#changes")
        page.get_by_role("button", name="restore this scan...").first.click()
        detail = page.locator("#detail")
        detail.get_by_text("Rule settings changed back").wait_for()
        detail.get_by_text("kelt_rule, minimum_files: 5 → 3").wait_for()
    finally:
        page.close()


def test_history_lists_the_moves_a_dive_undid_under_the_notice(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _answered_with(
        page,
        "**/api/reorganisation*",
        lambda _: {
            "available": True,
            "noticed": True,
            "notice": "A dive found one file a walk took for moved had other bytes.",
            "compared": {"before": "Run 1", "after": "Run 2"},
            "matched_without_size": 0,
            "states_no_sizes": {"before": False, "after": False},
            "folder_lines": {},
            "moves_undone": [
                {"path": "vorn/zib_glorbex_01.csv", "taken_for": "kelt/zib_glorbex_01.csv"}
            ],
        },
    )
    try:
        page.goto(f"{base}#changes")
        undone = page.locator("#list details.moves-undone")
        undone.wait_for()
        undone.locator("summary").click()
        assert "1 move undone by reading contents" in undone.inner_text()
        assert "vorn/zib_glorbex_01.csv: not kelt/zib_glorbex_01.csv" in undone.inner_text()
    finally:
        page.close()


def test_words_suggested_late_are_not_drawn_over_the_results(browser, served) -> None:
    """The suggestion asked for while typing came back after Enter and was drawn over the
    results. Enter stops it (the results are Ask's)."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    held: list[Any] = []
    # The suggestions are held back until the results are drawn, then let through.
    page.route("**/api/search/suggest*", lambda route: held.append(route))
    try:
        page.goto(f"{base}#inventory")
        # The box listens once the page has started: letters typed before that ask for nothing.
        try:
            page.locator("main .bar-label").first.wait_for()
        except sync_api.TimeoutError:
            # Said with what the page showed: this wait has failed rarely.
            shown = page.inner_text("main")[:300].replace("\n", " | ")
            pytest.fail(f"the Inventory never drew; the page says: {shown}")
        settled(page)
        box = page.locator("#search-box")
        with page.expect_request("**/api/search/suggest*", timeout=HANG_GUARD_MS):
            box.press_sequentially("glorbex", delay=30)
        box.press("Enter")
        page.wait_for_selector("h2[data-ask-view]")
        late = [route.request for route in held]
        for route in held:
            route.continue_()
        for request in late:
            response = request.response()
            if response is not None:
                response.finished()
        settled(page)
        listed = page.locator("#search-suggest")
        assert listed.count() == 0 or not listed.is_visible()
    finally:
        page.close()
