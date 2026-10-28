"""Re-run, Import and Export after an answer, in a real browser.

The tests are skipped, as ``test_browser_dashboard.py`` is, where the ``browser`` extra or a
browser is missing, and their harness is copied from ``test_browser_newcomer.py``. Every served
Dashboard keeps who decides in a folder of the test's own (``INGENCE_CONFIG_HOME``). Projects are
made as a newcomer makes one, with ``ingence init``, ``index`` and ``infer``.

1. After an answer the header's Re-run asks again: a question the rules read, once answered (a
   class of conclusions said wrong), makes Re-run enabled, with its badge's summary saying what
   it will apply, and with no reload.
2. The header's Export and Import mean the Dictionary's own, and a pack installed through the
   header waits for Re-run. The Dictionary opens its first word, as every split view does.
3. A burst of answers asks the server what waits once, not once per answer.

Every name is invented.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]

#: A microscope's files and a bench's, each named in its own words: two packs would read them.
_TREE = (
    "2026-01-12 brisa confocal/brisa_gfp_zstack_01.nd2",
    "2026-01-12 brisa confocal/brisa_gfp_zstack_02.nd2",
    "2026-01-12 brisa confocal/brisa_dapi_zstack_03.nd2",
    "2026-02-03 kelt widefield/kelt_mcherry_timelapse_01.nd2",
    "2026-02-03 kelt widefield/kelt_brightfield_02.nd2",
    "2026-03-09 zeph bench/zeph_elisa_plate_01.csv",
    "2026-03-09 zeph bench/zeph_hplc_trace_02.csv",
    "2026-03-09 zeph bench/zeph_qpcr_run_03.csv",
    "2026-03-09 zeph bench/zeph_sdspage_04.csv",
)


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


def _without_the_nd2_default(project: Path) -> None:
    """Several ND2 files accept the microscopy pack by default, so nothing proposes it. These
    tests pin the question that proposes a pack, so the default is switched off."""
    import yaml

    settings = project / "settings.yaml"
    held = yaml.safe_load(settings.read_text())
    held.setdefault("dictionary", {})["files_to_accept_a_pack"] = 0
    settings.write_text(yaml.safe_dump(held))


def _newcomer(base: Path, *extra: str) -> Path:
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
        ["init", str(project), "--root", str(pool), *extra],
        ["index", "-p", str(project), "--quiet"],
        ["infer", "-p", str(project)],
    ):
        done = runner.invoke(app, command)
        assert done.exit_code == 0, (command, done.output)
        if command[0] == "init":
            _without_the_nd2_default(project)
    return project


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


def _name_the_decider(page: Any, name: str) -> None:
    dialog = page.locator("dialog.ingence-dialog")
    dialog.get_by_label("Your name").fill(name)
    dialog.get_by_role("button", name="Use this name").click()


def _waiting_rows(page: Any) -> list[str]:
    """What Re-run will apply, as its badge's summary lists it."""
    page.wait_for_function("() => !document.getElementById('rerun-badge').hidden")
    page.locator("#rerun-badge").click()
    return page.locator("#rerun-summary li").all_text_contents()


def test_an_answer_the_rules_read_enables_rerun_with_no_reload(
    browser, serving, tmp_path: Path
) -> None:
    project = _newcomer(tmp_path)
    base = serving(project)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#review")
        rerun = page.locator("#rerun-button")
        page.wait_for_function("() => document.getElementById('rerun-button').title !== ''")
        assert rerun.is_disabled()
        listed = page.locator("#list")
        listed.locator('button.q-chip[data-title="Conclusions to check"]').click()
        listed.locator("tr.row", has_text='read as "acquisition"').first.click()
        detail = page.locator("#detail")
        detail.get_by_role("button", name="Wrong", exact=True).click()
        _name_the_decider(page, "trell")
        page.wait_for_function("() => !document.getElementById('rerun-button').disabled")
        assert [
            row
            for row in _waiting_rows(page)
            if row.startswith('every file read as "acquisition"') and row.endswith(": Wrong")
        ]
        assert page.evaluate("performance.getEntriesByType('navigation').length") == 1
        _shots(page, "rerun-after-answer")
    finally:
        page.close()


def test_the_header_exports_and_installs_the_dictionary_s_packs(
    browser, serving, tmp_path: Path
) -> None:
    project = _newcomer(tmp_path)
    base = serving(project)
    page = browser.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    try:
        page.goto(f"{base}#dictionary")
        page.wait_for_function(
            "() => document.getElementById('export-button').dataset.view === 'dictionary'"
        )
        # A split view is never left empty: the first word opens beside the list.
        first = page.locator("table.dictionary tbody tr.row").first
        first.wait_for()
        term = first.get_attribute("data-term")
        page.locator("#detail h2", has_text=term).wait_for()
        assert "selected" in (first.get_attribute("class") or "")
        export = page.locator("#export-button")
        imported = page.locator("#import-button")
        assert export.is_enabled() and imported.is_enabled()
        assert "Export yours" in (export.get_attribute("title") or "")
        assert "Install dictionary words" in (imported.get_attribute("title") or "")
        # A pack installed through the header is the page's own install, and waits for Re-run.
        pack = tmp_path / "zeph_words.yaml"
        pack.write_text("section: zeph_words\nentries:\n  - {term: zeph_bench, surfaces: [zeph]}\n")
        # Import asks what the file is in one box, the page's own kind chosen. A pack is not asked
        # who wrote it.
        imported.click()
        dialog = page.locator("dialog.import-dialog")
        dialog.wait_for()
        dialog.locator("#import-file").set_input_files(str(pack))
        dialog.get_by_role("button", name="Import").click()
        _name_the_decider(page, "trell")
        page.get_by_text("zeph_words installed: 1 words.").wait_for()
        page.wait_for_function("() => !document.getElementById('rerun-button').disabled")
        assert "words from zeph_words added" in _waiting_rows(page)
        with page.expect_download() as waiting:
            export.click()
        assert waiting.value.suggested_filename.endswith(".yaml")
    finally:
        page.close()


#: Counts the page's asks for what waits, where they are made, so a test reads the number at the
#: moment it matters rather than watching the network for a while.
_COUNT_THE_ASKS = """
window.__waitingAsks = 0;
const askedFetch = window.fetch;
window.fetch = function (input) {
  const address = typeof input === 'string' ? input : input.url;
  if (address.includes('/api/waiting')) window.__waitingAsks += 1;
  return askedFetch.apply(this, arguments);
};
"""


def test_a_burst_of_writes_asks_what_waits_once(browser, serving, tmp_path: Path) -> None:
    """Every answer asks the server what waits, and each ask starts a count there, so "take all"
    over two hundred answers would start two hundred counts. A burst of writes asks once, a
    moment after its last write.

    The test uses no clock but the page's own, and that one holds still. A test that waited 2.5 s
    for the page to go quiet and 1.5 s for the ask failed on a loaded machine either way. The
    server's first count can outlast the wait, and the page then asks again every 2 s by itself.
    A page that was slow to run its timer missed the 1.5 s. So the server's answer is fixed here
    (that loop is not what is tested), the page's timers are the browser's fake clock, and the
    asks are counted at the moment each is made.
    """
    project = _newcomer(tmp_path)
    base = serving(project)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(_COUNT_THE_ASKS)
    nothing = dict.fromkeys(
        ("questions", "classes", "claims", "candidates", "extensions", "programs"), 0
    )
    page.route(
        "**/api/waiting",
        lambda route: route.fulfill(
            status=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=json.dumps(nothing),
        ),
    )
    try:
        # On the Inventory only the page's own ask sets the Questions tab's words, so they say
        # that ask has been answered.
        page.goto(f"{base}#inventory")
        page.wait_for_function(
            "() => document.querySelector('#pages button[data-page=\"advanced\"]').title"
            " === 'Nothing waiting'"
        )
        before = page.evaluate("window.__waitingAsks")
        assert before >= 1
        # From here the page's timers move only when the test moves them.
        page.clock.install(time=datetime(2026, 1, 1, tzinfo=UTC))
        page.clock.pause_at(datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))
        page.evaluate("() => { for (let i = 0; i < 20; i++) refreshAfterWrites(); }")
        assert page.evaluate("window.__waitingAsks") == before, "a write asked at once"
        page.clock.run_for(100)
        assert page.evaluate("window.__waitingAsks") == before, "the ask did not wait"
        page.clock.run_for(5000)
        assert page.evaluate("window.__waitingAsks") == before + 1
    finally:
        page.close()
