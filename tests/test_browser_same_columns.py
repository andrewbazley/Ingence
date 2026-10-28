"""A table's page in the Dashboard says which files have the same columns.

Skipped, never failed, where the ``browser`` extra or a browser is missing, and required where
``INGENCE_BROWSER_REQUIRED=1``. The project is invented (``fixtures/same_columns_pool.py``). The
Dashboard keeps who decides, and everything else it writes, in a folder of the test's own
(``INGENCE_CONFIG_HOME``) on a port between 20000 and 29999.

1. The lines appear on a table's page: "Same columns as N files" and "Its columns are contained
   in M files", with the added column named.
2. The group is called same columns, and the page never says it is the same experiment.
3. A file that is not a table has no such lines, and a table whose columns were not read says why.
"""

from __future__ import annotations

import os
import random
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser, settled
from fixtures.same_columns_pool import LATE, build_project

REQUIRED = os.environ.get("INGENCE_BROWSER_REQUIRED") == "1"
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="the browser extra is not installed"
    )

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _port() -> int:
    """A free port between 20000 and 29999, picked at random."""
    for _ in range(200):
        port = random.randint(20000, 29999)
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.fail("no free port between 20000 and 29999")


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("browser_same_columns")
    project = build_project(base)
    config = base / "config-home"
    config.mkdir()
    port = _port()
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
        [sys.executable, "-c", code, str(project.path), str(port)],
        env=environment,
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stdout.readline().startswith(b"serving")
    try:
        yield project, f"http://127.0.0.1:{port}/"
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        process.stdout.close()


def _identifiers(project: Any) -> dict[str, str]:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project.path) as catalog:
        rows = catalog.query("SELECT relative_path, entry_id FROM entries").fetchall()
    return {str(path): str(identifier) for path, identifier in rows}


def _open(browser: Any, base: str, identifier: str) -> Any:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{base}#years")
    # After the page's first draw. The inspector exists as soon as its script has run, and a
    # page opened on it before the first view finishes drawing has it cleared by that draw. On a
    # busy machine the line this test waits for then never comes. The Years view's rows and its
    # project card say the first draw is done. #years opens Overlap's By year mode (view
    # `overlap`).
    page.wait_for_function(
        "() => typeof inspector !== 'undefined' && ['years', 'overlap'].includes(state.view)"
        " && document.querySelector('#list > *')"
        " && !document.querySelector('#list .state-loading')",
        timeout=120_000,
    )
    settled(page)
    page.evaluate("(id) => inspector.showEntry(id)", identifier)
    return page


def test_a_tables_page_says_how_many_files_have_the_same_columns_and_contain_its_own(
    browser, served
) -> None:
    project, base = served
    page = _open(browser, base, _identifiers(project)["plate_one/HeLa_DMSO_quant.csv"])
    try:
        same = page.locator('[data-columns-line="same"]')
        same.wait_for()
        assert same.inner_text() == "Same columns as 3 files"
        contained = page.locator('[data-columns-line="contained"]')
        contained.wait_for()
        assert contained.inner_text() == "Its columns are contained in 3 files"
        block = page.locator(".inspector-columns")
        text = block.inner_text()
        assert "adds mean_area" in text
        assert "the same names in another order or delimiter" in text
        # The three files with the same columns are named, each a way to its own page.
        assert "plate_two/HeLa_taxol_quant.csv" in text
        assert "templates/HeLa_quant_template.csv" in text
    finally:
        page.close()


def test_the_larger_table_says_how_many_files_have_columns_inside_its_own(browser, served) -> None:
    project, base = served
    page = _open(browser, base, _identifiers(project)["plate_three/HeLa_nocodazole_quant.csv"])
    try:
        page.locator('[data-columns-line="same"]').wait_for()
        assert page.locator('[data-columns-line="same"]').inner_text() == "Same columns as 0 files"
        assert (
            page.locator('[data-columns-line="contained"]').inner_text()
            == "Its columns are contained in 0 files"
        )
        contains = page.locator('[data-columns-line="contains"]')
        assert contains.inner_text() == "6 files have columns contained in its own"
        assert "lacks mean_area" in page.locator(".inspector-columns").inner_text()
    finally:
        page.close()


def test_the_group_is_called_same_columns_and_never_the_same_experiment(browser, served) -> None:
    project, base = served
    page = _open(browser, base, _identifiers(project)["plate_two/HeLa_taxol_quant.csv"])
    try:
        page.locator('[data-columns-line="same"]').wait_for()
        text = page.locator(".inspector-columns").inner_text()
        assert "Same columns as 3 files" in text
        # The note says what the lists compare; no line claims an experiment.
        assert "compare column names only" in text
        assert "same experiment" not in text.casefold()
    finally:
        page.close()


def test_a_table_not_read_says_why_and_a_file_that_is_no_table_has_no_such_lines(
    browser, served
) -> None:
    project, base = served
    ids = _identifiers(project)
    page = _open(browser, base, ids[LATE])
    try:
        page.locator(".inspector-columns").get_by_text("Not compared with other tables").wait_for()
        assert page.locator('[data-columns-line="same"]').count() == 0
    finally:
        page.close()
    page = _open(browser, base, ids["stacks/HeLa_DMSO_mask.npy"])
    try:
        page.get_by_text("What the rules said about it").wait_for()
        assert page.locator(".inspector-columns").count() == 0
    finally:
        page.close()
