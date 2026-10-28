"""The pages show plain words, never the engine's stored ones.

Where a chart's category is a stored value (``examined_no_evidence``, ``not_a_link``,
``metadata_only``), the page shows a plain label from one table and keeps the stored word in the
label's tooltip. A run is named by its name, with its identifier in the tooltip. No developer
terms appear on a page. The tests are skipped, like their neighbours, where the ``browser`` extra
or a browser is missing. Every served Dashboard keeps who decides in a folder of the test's own
(``INGENCE_CONFIG_HOME``).
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")

REPOSITORY = Path(__file__).resolve().parents[1]

#: How long an ``expect`` waits before it gives up. This is a guard against a hang, not the time
#: the page is expected to take: every wait returns the moment its state is reached. Playwright's
#: own 5 s for ``expect`` is shorter than a loaded machine takes to draw a page.
HANG_GUARD_MS = 120_000

#: Stored words a page of Explore, Graph or History must not show as text (a tooltip may).
RAW_WORDS = (
    "examined_no_evidence",
    "examined_conflicting",
    "unexamined",
    "not_a_link",
    "metadata_only",
    "skipped_by_settings",
    "outside_root",
)
#: A run's identifier as the engine writes it. A page names the run instead.
RUN_ID = re.compile(r"run_\d{8}T\d{6}Z_[0-9a-f]+")


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

    base = tmp_path_factory.mktemp("browser_plain_words")
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


@pytest.fixture(scope="module")
def served(built: Path, tmp_path_factory: pytest.TempPathFactory):
    config = tmp_path_factory.mktemp("plain_words_config")
    port = _free_port()
    process = _serve(built, port, config)
    try:
        yield built, f"http://127.0.0.1:{port}/"
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


def _no_raw_words(text: str, where: str) -> None:
    shown = [word for word in RAW_WORDS if re.search(rf"\b{word}\b", text)]
    assert not shown, f"{where} shows the stored words {shown}"


def test_the_inventory_names_every_state_in_plain_words(browser: Any, served: Any) -> None:
    _, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#inventory")
        detail = page.locator("#detail")
        detail.locator(".bar-label").first.wait_for()
        page.wait_for_function("document.querySelectorAll('#detail .bars').length >= 5")
        # The logical context is appended last, when its read arrives.
        page.wait_for_load_state("networkidle")
        _no_raw_words(page.locator("main").inner_text(), "the Inventory")
        labels = page.locator("main .bar-label")
        for index in range(labels.count()):
            shown = labels.nth(index).inner_text()
            assert "_" not in shown, f"a bar is labelled {shown!r}"
        # The stored word stays in the tooltip, so nothing is hidden from someone who needs it.
        titles = page.eval_on_selector_all(
            "main .bar-label[title]", "nodes => nodes.map((node) => node.title)"
        )
        assert "examined_no_evidence" in titles or "unexamined" in titles
        assert "not_a_link" in titles
        # The entries' chart counts whether their questions are settled, and its heading says so:
        # "not examined" under a bare "Files and folders" read as never scanned.
        headings = page.eval_on_selector_all(
            "#detail h3", "nodes => nodes.map((n) => n.textContent)"
        )
        assert "Questions about files and folders" in headings
        # One meaning per word on the page: the scan's own outcomes never borrow the
        # words of the section "Left out and set aside", which counts other things.
        for stored in ("skipped_by_policy", "skipped_by_settings", "deferred"):
            plain = page.evaluate(f"plainValue('{stored}')")
            assert not re.search(r"set aside|left out", plain), f"{stored} reads {plain!r}"
    finally:
        page.close()


def test_history_names_runs_and_never_shows_their_identifiers(browser: Any, served: Any) -> None:
    _, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#changes")
        page.locator("ol.timeline li.run").first.wait_for()
        page.locator("#detail h2").first.wait_for()
        text = page.locator("main").inner_text()
        assert not RUN_ID.search(text), "History shows a run's identifier as text"
        _no_raw_words(text, "History")
        # The identifier is kept where a person who needs it can find it: the name's tooltip.
        assert page.locator("ol.timeline [title^='run_']").count() >= 1
        page.get_by_role("button", name="restore this scan...").first.click()
        page.locator("#detail").get_by_text("Going back to this scan").wait_for()
        preview = page.locator("#detail").inner_text()
        assert not RUN_ID.search(preview)
        # The watermark is a position in the Record, never a count of decisions.
        assert re.search(r"Answer log read up to\s+number \d", preview), preview
        assert "Decisions it read" not in preview
    finally:
        page.close()


def test_a_restore_preview_opened_from_its_address_names_both_runs(
    browser: Any, served: Any
) -> None:
    """A reload or a shared address opens the preview with no name in hand. It names the run gone
    back to and the current one, from the runs History lists."""
    _, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}?run=run_20260101T000001Z_111111111111#changes")
        detail = page.locator("#detail")
        # Wait for the preview to say both names, not for its heading and then read what is there:
        # the pane can show the line saying the preview is being read until the runs are listed.
        sync_api.expect(detail).to_contain_text(
            re.compile(r"Going back to\s+Scan 1\b"), use_inner_text=True, timeout=HANG_GUARD_MS
        )
        sync_api.expect(detail).to_contain_text(
            re.compile(r"Now showing\s+Scan 2\b"), use_inner_text=True, timeout=HANG_GUARD_MS
        )
        assert not RUN_ID.search(detail.inner_text()), detail.inner_text()
    finally:
        page.close()


#: Records the text of the right-hand pane each time it changes, so a test reads what the pane
#: showed and in what order, not only what it shows when the test looks.
_RECORD_THE_DETAIL = """
window.__detailSeen = [];
new MutationObserver(() => {
  const pane = document.getElementById('detail');
  if (!pane) return;
  const text = pane.innerText.replace(/\\s+/g, ' ').trim();
  const last = window.__detailSeen[window.__detailSeen.length - 1];
  if (text && text !== last) window.__detailSeen.push(text);
}).observe(document, {subtree: true, childList: true, characterData: true});
"""


def test_a_restore_preview_opened_from_its_address_is_not_redrawn_as_loading(
    browser: Any, served: Any
) -> None:
    """Opening History on a run named in the address asks for the preview once. If it asked twice,
    once for the address and once when the runs were listed, and the preview was drawn before the
    list came, the second ask would replace it with the line saying it was being read. A person
    (or a test) reading in between would see that line under the heading's place.

    The order that would show this is made here, not waited for: the runs' list is held until the
    preview is drawn."""
    _, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(_RECORD_THE_DETAIL)
    held: list[Any] = []
    # The preview's own read of the runs goes first, History's list second (the page's order, and
    # the assertion below that the list is not drawn yet fails if that order ever changes).
    page.route("**/api/timeline", lambda route: held.append(route))
    try:
        page.goto(f"{base}?run=run_20260101T000001Z_111111111111#changes")
        # Both reads of the runs are held (a bounded wait for that state, never an open loop).
        for _ in range(HANG_GUARD_MS // 50):
            if len(held) >= 2:
                break
            page.wait_for_timeout(50)
        assert len(held) == 2, f"the page asked for the runs {len(held)} times, not twice"
        held[0].continue_()
        page.locator("#detail").get_by_text("Going back to this scan").wait_for()
        assert page.locator("ol.timeline li.run").count() == 0, "the list came first"
        held[1].continue_()
        page.locator("ol.timeline li.run").first.wait_for()
        seen = page.evaluate("window.__detailSeen")
        drawn = next(i for i, text in enumerate(seen) if text.startswith("GOING BACK TO THIS SCAN"))
        later = [text for text in seen[drawn:] if not text.startswith("GOING BACK TO THIS SCAN")]
        assert later == [], f"the preview was replaced by {later}"
    finally:
        page.close()


def test_the_neighbourhood_says_a_file_s_state_in_plain_words(browser: Any, served: Any) -> None:
    project, base = served
    folder = _entry_id(project, "2024-03-06_kelt")
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}?center={folder}#graph")
        inspector = page.locator('[data-graph="inspector"]')
        inspector.get_by_text("What the rules said about it").wait_for()
        shown = page.locator("main").inner_text()
        _no_raw_words(shown, "the Neighbourhood")
        # The run it was read from is named, not identified (the status line says which).
        assert not RUN_ID.search(shown), "the Neighbourhood shows a run's identifier as text"
        # Each state in plain words, its stored word in the tooltip: not the stored word unspaced.
        # The tooltips exist only where the plain words do, so a page without them fails here.
        assert inspector.locator("dd[title]").count() >= 2
        questions = page.evaluate(
            """() => [...document.querySelectorAll('[data-graph="inspector"] dt')]
              .find((dt) => dt.textContent === 'Its questions').nextElementSibling.textContent"""
        )
        assert questions in ("not examined", "no evidence found", "conflicting evidence", "settled")
        for stored in page.eval_on_selector_all(
            '[data-graph="inspector"] dd[title]',
            "nodes => nodes.map((node) => [node.title, node.textContent])",
        ):
            raw, shown = stored
            assert shown != raw.replace("_", " "), f"{raw} is shown as {shown!r}"
    finally:
        page.close()


def test_the_wheel_names_no_setting_by_its_code_name(browser: Any, served: Any) -> None:
    _, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#explorer")
        caps = page.locator(".ix-inspector .ix-caps")
        caps.wait_for()
        said = caps.inner_text()
        assert said.strip()
        assert "max_bins" not in said and "'more'" not in said
        shown = page.locator("main").inner_text()
        _no_raw_words(shown, "the Wheel")
        assert not RUN_ID.search(shown), "the Wheel shows a run's identifier as text"
    finally:
        page.close()
