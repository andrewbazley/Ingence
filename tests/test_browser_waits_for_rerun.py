"""What waits for Re-run, in a real browser.

Nothing runs by itself. Opening a project whose changes no run has followed says so, and offers a
re-run now or later. A badge near Re-run sums up the changes that will take effect, at summary
level unless a person asks for the lines.

1. One notice when a session opens, with Re-run now and Later. Later keeps the notice closed on
   reloads and in new tabs of that browser (the choice is kept in localStorage) until something
   else waits. A browser that never chose Later asks. While the notice is open, it counts a change
   made meanwhile.
2. The badge counts what waits and opens a summary by kind, each line opening its rows.
3. Re-run now starts the run, and the badge goes when the run is done.

The tests are skipped, as ``test_browser_dashboard.py`` is, where the ``browser`` extra or a
browser is missing. Their harness is copied from ``test_browser_rerun_after_answer.py``. Every
served Dashboard keeps who decides in a folder of the test's own (``INGENCE_CONFIG_HOME``).
Every name is invented.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser, settled

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]

_TREE = (
    "2026-01-12 brisa confocal/brisa_gfp_zstack_01.nd2",
    "2026-01-12 brisa confocal/brisa_dapi_zstack_02.nd2",
    "2026-02-03 kelt widefield/kelt_mcherry_timelapse_01.nd2",
    "2026-03-09 zeph bench/zeph_elisa_plate_01.csv",
    "2026-03-09 zeph bench/zeph_qpcr_run_02.csv",
)

PERSON = "wren"


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


def _newcomer(base: Path) -> Path:
    """A project made as a newcomer makes one: ``init``, ``index`` and ``infer``, nothing else."""
    from typer.testing import CliRunner

    from ingence.cli.main import app

    pool = base / "pool"
    for relative in _TREE:
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 16)
    project = base / "project"
    runner = CliRunner()
    for command in (
        ["init", str(project), "--root", str(pool)],
        ["index", "-p", str(project), "--quiet"],
        ["infer", "-p", str(project)],
    ):
        done = runner.invoke(app, command)
        assert done.exit_code == 0, (command, done.output)
        if command[0] == "init":
            # The ND2 default is off, so only a person's answers wait for the badge.
            settings = project / "settings.yaml"
            held = yaml.safe_load(settings.read_text())
            held.setdefault("dictionary", {})["files_to_accept_a_pack"] = 0
            settings.write_text(yaml.safe_dump(held))
    return project


def _later_than_the_runs() -> str:
    """Now, a second on, so a run started next begins after it (a run in the same second waits)."""
    time.sleep(1.1)
    stamp = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    time.sleep(1.1)
    return stamp


def _answer(project: Path, *targets: str) -> None:
    """A person's accepts, written through the Record's one way in, after the runs.

    A pack is refused, never accepted: every domain loads, so accepting a pack changes nothing
    and waits for nothing, while refusing one is a veto, the one answer on a pack that changes how
    the names are read (a Dictionary change)."""
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event
    from ingence.storage.commit import read_pointer

    pointer = read_pointer(project)
    assert pointer is not None
    moment = _later_than_the_runs()
    for target in targets:
        append_event(
            project,
            kind=RecordEventKind.REJECT if target.startswith("pack:") else RecordEventKind.CONFIRM,
            actor=PERSON,
            occurred_at=moment,
            target=target,
            against_run_id=pointer.run_id,
        )


def _free_port() -> int:
    """A loopback port: ``INGENCE_BROWSER_PORT`` where a person names one, else any free one."""
    named = os.environ.get("INGENCE_BROWSER_PORT")
    if named:
        return int(named)
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


@pytest.fixture
def serving(tmp_path: Path):
    """Serve a project the test makes, and stop it afterwards."""
    running: list[subprocess.Popen[bytes]] = []

    def start(project: Path) -> str:
        config = tmp_path / "config"
        config.mkdir(exist_ok=True)
        port = _free_port()
        running.append(_serve(project, port, config))
        return f"http://127.0.0.1:{port}/"

    yield start
    for process in running:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        if process.stdout is not None:
            process.stdout.close()


def _badge_shown(page: Any) -> None:
    page.wait_for_function("() => !document.getElementById('rerun-badge').hidden")


def test_one_notice_when_a_session_opens_and_later_keeps_it_closed_until_something_else_waits(
    browser, serving, tmp_path: Path
) -> None:
    project = _newcomer(tmp_path)
    _answer(project, "pack:microscopy")
    base = serving(project)
    session = browser.new_context(viewport={"width": 1400, "height": 900})
    page = session.new_page()
    try:
        page.goto(f"{base}#inventory")
        notice = page.locator("#rerun-notice")
        notice.wait_for()
        assert notice.inner_text().startswith("1 Dictionary change since the last scan.")
        assert notice.get_by_role("button", name="Re-run now").is_visible()
        _shots(page, "notice-at-opening")
        # Nothing ran by itself: the change still waits, and no job was asked for.
        assert page.evaluate("fetch('/api/applying').then((r) => r.json())")["phase"] == "idle"
        notice.get_by_role("button", name="Later").click()
        assert notice.is_hidden()
        page.reload()
        # The notice is decided in the same turn of the page as the badge, from the first reading of
        # what waits: once the badge is drawn and the page is quiet, the decision has been made.
        _badge_shown(page)
        settled(page)
        assert notice.is_hidden(), "Later holds on a reload"
        assert page.locator("#rerun-badge").inner_text() == "1"
        # Later is kept in the browser, so a new tab does not ask again.
        tab = session.new_page()
        tab.goto(f"{base}#inventory")
        _badge_shown(tab)
        settled(tab)
        assert tab.locator("#rerun-notice").is_hidden(), "Later holds in a new tab"
        tab.close()
        # Something else waits now, so the next page asks again.
        _answer(project, "group:brisa zstack")
        tab = session.new_page()
        tab.goto(f"{base}#inventory")
        tab.locator("#rerun-notice").wait_for()
        assert tab.locator("#rerun-badge").inner_text() == "2"
    finally:
        session.close()
    # A browser that never chose Later asks.
    again = browser.new_context(viewport={"width": 1400, "height": 900})
    try:
        page = again.new_page()
        page.goto(f"{base}#inventory")
        page.locator("#rerun-notice").wait_for()
    finally:
        again.close()


def test_an_open_notice_counts_a_change_made_meanwhile(browser, serving, tmp_path: Path) -> None:
    project = _newcomer(tmp_path)
    _answer(project, "pack:microscopy")
    base = serving(project)
    session = browser.new_context(viewport={"width": 1400, "height": 900})
    page = session.new_page()
    try:
        page.goto(f"{base}#inventory")
        notice = page.locator("#rerun-notice")
        notice.wait_for()
        # A setting committed while the notice is open (the Settings page's own two writes).
        page.evaluate(
            """async () => {
                await write('/api/workspace/draft', {kind: 'settings',
                  values: {dictionary: {tokenise_names: false}}});
                await write('/api/workspace/commit', {actor: 'wren', message: 'names off'});
                await refreshUncommitted();
            }"""
        )
        page.wait_for_function(
            "() => document.getElementById('rerun-notice').innerText"
            ".startsWith('1 Dictionary change and 1 setting since the last scan.')"
        )
        assert page.locator("#rerun-badge").inner_text() == "2"
    finally:
        session.close()


def test_the_badge_counts_what_waits_and_opens_a_summary_by_kind(
    browser, serving, tmp_path: Path
) -> None:
    project = _newcomer(tmp_path)
    _answer(project, "pack:microscopy", "group:brisa zstack")
    base = serving(project)
    session = browser.new_context(viewport={"width": 1400, "height": 900})
    page = session.new_page()
    try:
        page.goto(f"{base}#inventory")
        page.locator("#rerun-notice").get_by_role("button", name="Later").click()
        badge = page.locator("#rerun-badge")
        _badge_shown(page)
        assert badge.inner_text() == "2"
        assert page.locator("#rerun-summary").is_hidden()
        badge.click()
        summary = page.locator("#rerun-summary")
        summary.wait_for()
        assert badge.get_attribute("aria-expanded") == "true"
        lines = summary.locator("details > summary")
        assert lines.all_inner_texts() == ["1 answer", "1 Dictionary change"]
        # Summary level first: a line's rows open only when asked.
        row = summary.get_by_text("the group brisa zstack accepted")
        assert row.is_hidden()
        lines.first.click()
        assert row.is_visible()
        _shots(page, "badge-summary")
        # A kind opened stays open when the header reads what waits again.
        page.evaluate("() => refreshUncommitted()")  # returns when the reading has been drawn
        assert row.is_visible()
        badge.click()
        assert summary.is_hidden()
        # And Escape closes it.
        badge.click()
        summary.wait_for()
        page.keyboard.press("Escape")
        assert summary.is_hidden()
    finally:
        session.close()


def test_rerun_now_starts_the_run_and_the_badge_goes_when_it_is_done(
    browser, serving, tmp_path: Path
) -> None:
    project = _newcomer(tmp_path)
    _answer(project, "group:brisa zstack")
    base = serving(project)
    session = browser.new_context(viewport={"width": 1400, "height": 900})
    page = session.new_page()
    try:
        page.goto(f"{base}#inventory")
        notice = page.locator("#rerun-notice")
        notice.get_by_role("button", name="Re-run now").click()
        assert notice.is_hidden()
        page.locator("#applying.done").wait_for(timeout=120_000)
        page.wait_for_function(
            "() => document.getElementById('rerun-badge').hidden", timeout=HANG_GUARD_MS
        )
        assert page.locator("#rerun-button").is_disabled()
        _shots(page, "after-rerun")
    finally:
        session.close()


def test_a_rerun_a_close_stopped_is_offered_again_and_starts_only_on_a_click(
    browser, serving, tmp_path: Path
) -> None:
    """A Re-run left queued when the Dashboard closed waits for a person."""
    from ingence.jobs.ledger import JobLedger
    from ingence.storage.commit import read_pointer

    project = _newcomer(tmp_path)
    _answer(project, "group:brisa zstack")
    pointer = read_pointer(project)
    assert pointer is not None
    JobLedger(project).enqueue(steps=("inference",), record_sequence=1, base_run=pointer.run_id)
    base = serving(project)
    session = browser.new_context(viewport={"width": 1400, "height": 900})
    page = session.new_page()
    try:
        page.goto(f"{base}#inventory")
        notice = page.locator("#rerun-notice")
        notice.wait_for()
        assert notice.inner_text().startswith(
            "A Re-run stopped when the Dashboard last closed. 1 answer since the last scan."
        )
        # Nothing started by itself: once the page has asked for all it asks for, the re-run is
        # still stopped.
        settled(page)
        assert page.evaluate("fetch('/api/applying').then((r) => r.json())")["phase"] == "stopped"
        _shots(page, "stopped-notice")
        notice.get_by_role("button", name="Re-run now").click()
        page.locator("#applying.done").wait_for(timeout=120_000)
        page.wait_for_function(
            "() => document.getElementById('rerun-badge').hidden", timeout=HANG_GUARD_MS
        )
    finally:
        session.close()


def test_rerun_applies_the_rest_and_keeps_the_line_about_the_share(
    browser, serving, tmp_path: Path
) -> None:
    """A waiting dive holds nothing else back, and the line saying it is yours to start stays
    while the rest is applied."""
    project = _newcomer(tmp_path)
    _answer(project, "group:brisa zstack", "extension:tif")
    base = serving(project)
    session = browser.new_context(viewport={"width": 1400, "height": 900})
    page = session.new_page()
    try:
        page.goto(f"{base}#inventory")
        page.locator("#rerun-notice").get_by_role("button", name="Later").click()
        _badge_shown(page)
        page.locator("#rerun-button").click()
        share = page.locator("#rerun-summary .rerun-share")
        share.wait_for()
        assert share.inner_text() == (
            "Start what needs the data folders yourself, in a terminal: ingence dive, then "
            "ingence infer."
        )
        page.locator("#applying.done").wait_for(timeout=120_000)
        # The badge says 1 (the dive's answer still waits) once the header has read what waits
        # again after the re-run: that reading is what the line must outlive.
        page.wait_for_function(
            "() => { const badge = document.getElementById('rerun-badge');"
            " return !badge.hidden && badge.textContent === '1'; }",
            timeout=HANG_GUARD_MS,
        )
        assert share.is_visible(), "the line outlives the header's refresh"
        _shots(page, "share-line")
    finally:
        session.close()
