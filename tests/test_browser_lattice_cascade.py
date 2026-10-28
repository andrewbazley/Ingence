"""The Groupings page's lattice drawn as a cascade, in a real browser.

The cascade shows a core set with its term variations stacked on top of it, so a person can
confirm a grouping from the stack. Each most general set is a stack: the core at its base, each
set adding to it a block stacked above what it adds to, as wide as its share of the core's
experiments. A set held by fewer experiments than the run asks from is listed apart, not asked,
with the number said.

1. The core stands at the base, asked, and each addition above it says what it adds.
2. Approving the core asks its additions in place, through the one answer route, and the stack's
   meter counts what is settled.
3. At a phone's width the stack keeps every block on the screen.

Skipped where the ``browser`` extra or a browser is missing. Every served Dashboard keeps who
decides in a folder of the test's own (``INGENCE_CONFIG_HOME``). Every name is invented.
"""

from __future__ import annotations

import os
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

#: Eight experiments share kelt and vorn. Four of them add brisa (one of those also adds trell),
#: and two add zeph. Three more share quolt and sorn, fewer than the five the run asks from.
_DAYS = (
    ("20240301", "kelt vorn"),
    ("20240305", "kelt vorn brisa"),
    ("20240308", "kelt vorn brisa"),
    ("20240312", "kelt vorn brisa trell"),
    ("20240315", "kelt vorn zeph"),
    ("20240319", "kelt vorn zeph"),
    ("20240322", "kelt vorn"),
    ("20240326", "kelt vorn brisa"),
    ("20240402", "quolt sorn"),
    ("20240409", "quolt sorn"),
    ("20240416", "quolt sorn"),
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


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_cascade")
    pool = base / "pool"
    for day, words in _DAYS:
        for number in (1, 2):
            path = pool / f"{day} {words}" / f"{words.replace(' ', '_')}_{number:03d}.nd2"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
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
        yield project, f"http://127.0.0.1:{port}/"
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


def _name_the_decider(page: Any, name: str) -> None:
    dialog = page.locator("dialog.ingence-dialog")
    dialog.get_by_label("Your name").fill(name)
    dialog.get_by_role("button", name="Use this name").click()


def _block(page: Any, terms: str) -> Any:
    return page.locator(f'.cascade-block[data-terms="{terms}"]')


def test_the_core_stands_at_the_base_and_each_addition_says_what_it_adds(browser, served) -> None:
    _, base = served
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        page.goto(f"{base}#classes")
        stack = page.locator('.cascade[data-terms="kelt vorn"]')
        stack.wait_for()
        # The core is the last row: the base the additions stand on, asked now.
        base_row = stack.locator(".cascade-row").last
        assert base_row.locator(".cascade-block").get_attribute("data-terms") == "kelt vorn"
        assert "state-asked" in (_block(page, "kelt vorn").get_attribute("class") or "")
        # What waits folds into one line above the question: only the core shows.
        fold = stack.locator(".cascade-fold > summary")
        assert fold.inner_text() == "3 more wait for this one"
        assert stack.locator(".cascade-block:visible").count() == 1
        fold.click()
        # Each addition says what it adds, strong, beside what it adds to.
        brisa = _block(page, "brisa kelt vorn")
        assert brisa.locator(".cascade-word.added").all_inner_texts() == ["+ brisa"]
        assert "waits for the group below" in brisa.inner_text()
        assert "4 of 8 experiments" in brisa.inner_text()
        # As wide as its share: four of eight experiments, narrower than the core.
        core_box = _block(page, "kelt vorn").bounding_box()
        brisa_box = brisa.bounding_box()
        assert core_box and brisa_box and brisa_box["width"] < core_box["width"]
        assert brisa_box["y"] < core_box["y"]  # stacked above
        # Pointing at an addition lights what it adds to.
        _block(page, "brisa kelt trell vorn").hover()
        assert "lit" in (brisa.get_attribute("class") or "")
        assert "lit" not in (_block(page, "kelt vorn").get_attribute("class") or "")
        # A set three experiments share is listed apart, not asked, with the number said.
        apart = page.locator(".cascade-unasked")
        assert "shared by fewer than 5 experiments" in apart.locator("summary").inner_text()
        apart.locator("summary").click()
        assert "quolt sorn" in apart.inner_text()
        assert page.locator('.cascade[data-terms="quolt sorn"]').count() == 0
        # The files three experiments each count alike are a series, listed under each of them.
        held = page.locator("details.told-group", has_text="20240305 kelt vorn brisa")
        held.locator("summary").click()
        assert "kelt_vorn_brisa_{001,002}.nd2" in held.inner_text()
        assert "in 3 experiments" in held.inner_text()
        _shots(page, "cascade-core-asked")
    finally:
        page.close()


def test_approving_the_core_asks_its_additions_in_place(browser, served) -> None:
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import read_events

    project, base = served
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        page.goto(f"{base}#classes")
        core = _block(page, "kelt vorn")
        core.get_by_role("button", name="One group", exact=True).click()
        _name_the_decider(page, "ines")
        page.wait_for_selector('.cascade-block.state-approved[data-terms="kelt vorn"]')
        for terms in ("brisa kelt vorn", "kelt vorn zeph"):
            assert "state-asked" in (_block(page, terms).get_attribute("class") or "")
        # trell adds to brisa's set, still waiting for it, folded above the questions.
        assert "state-waiting" in (
            _block(page, "brisa kelt trell vorn").get_attribute("class") or ""
        )
        fold = page.locator('.cascade[data-terms="kelt vorn"] .cascade-fold > summary')
        assert fold.inner_text() == "1 more wait for this one"
        score = page.locator('.cascade[data-terms="kelt vorn"] .cascade-score').inner_text()
        assert "1 of 4 settled, 2 asked now" in score
        _shots(page, "cascade-core-approved")
    finally:
        page.close()
    (event,) = [e for e in read_events(project) if e.kind is RecordEventKind.CONFIRM]
    assert event.actor == "ines"
    assert "kelt vorn" in event.target


def test_at_a_phones_width_every_block_stays_on_the_screen(browser, served) -> None:
    _, base = served
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        page.goto(f"{base}#classes")
        page.locator('.cascade[data-terms="kelt vorn"]').wait_for()
        width = page.evaluate("window.innerWidth")
        for box in page.locator(".cascade-block").evaluate_all(
            "nodes => nodes.map((n) => n.getBoundingClientRect().right)"
        ):
            assert box <= width
        assert page.evaluate("document.scrollingElement.scrollWidth") <= width
        _shots(page, "cascade-narrow")
    finally:
        page.close()
