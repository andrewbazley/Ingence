"""A newcomer's Dashboard in a real browser.

The tests are skipped, as ``test_browser_dashboard.py`` is, where the ``browser`` extra or a
browser is missing, and their harness is copied from that file. Every served Dashboard keeps who
decides in a folder of the test's own (``INGENCE_CONFIG_HOME``), never in the person's
configuration. Projects are made as a newcomer makes one, with ``ingence init`` and nothing else.

1. No pack is asked about: every domain's words load from the first reading of the names, so the
   files two packs would read raise no pack question, and the Dictionary page says every word
   list is loaded.
2. The Groupings page says in words when nothing groups (the shipped Rulebook alone), and the
   lattice names no mode.

Every name is invented.
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


def test_no_pack_is_asked_about_and_every_word_list_is_loaded(
    browser, serving, tmp_path: Path
) -> None:
    project = _newcomer(tmp_path)
    base = serving(project)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#review")
        listed = page.locator("#list")
        listed.locator(".q-stands").wait_for()
        assert "Words for" not in listed.inner_text()
        ranked = page.evaluate(
            "async () => (await fetch('/api/v1/questions/ranked?answered=show&limit=1000')).json()"
        )
        assert ranked["questions"], "the newcomer is asked something"
        assert not [q for q in ranked["questions"] if q["kind"] == "pack"]
        assert not [
            q for q in ranked["questions"] if str(q.get("target") or "").startswith("pack:")
        ]
        _shots(page, "no-pack-question")
        page.goto(f"{base}#dictionary")
        packs = page.locator("section.dict-packs")
        packs.wait_for()
        assert "Ingence loads every domain word list." in packs.inner_text()
        for domain in ("biochemistry", "microscopy"):
            row = packs.locator("tr", has=page.get_by_text(domain, exact=True))
            assert "loaded" in row.inner_text() and "not loaded" not in row.inner_text()
    finally:
        page.close()


def test_the_groupings_page_says_when_the_run_proposes_no_group(
    browser, serving, tmp_path: Path
) -> None:
    # A run on a book whose rules group nothing here: revisions 0 to 3.
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.predicates import load_builtins
    from ingence.rulebook.resolve import effective_rulebook

    project = _newcomer(tmp_path)
    load_builtins()
    run_inference(
        Project.open(project),
        run_id="run_20260101T000009Z_999999999999",
        started_at="2026-01-01T00:00:09Z",
        rulebook=effective_rulebook(load_revisions(), at=3),
    )
    base = serving(project)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#classes")
        # Where nothing groups the page says so in words; the lattice
        # names no mode, so a run holding classes but no group rule says "This run proposes no
        # groups of experiments." in its section (``test_group_lattice_none.py``).
        page.get_by_text("no rule put two or more files or experiments together").wait_for()
        lattice = page.evaluate("async () => (await fetch('/api/v1/groups/lattice')).json()")
        assert (lattice["mode"], lattice["sets"]) == ("none", [])
        _shots(page, "groupings-no-group")
    finally:
        page.close()
