"""Import says who wrote the file, in a browser.

Import opens a box asking for the file, what it is, and who wrote it: the people who have answered
in this project, and "Agent-generated". An agent's file asks the person to confirm they reviewed
it and is recorded as ``<their name>_agent``. Cancelling writes nothing. Any other choice records
that person, the event naming who imported it.

The harness is ``test_browser_shell_import_export.py``'s. The tests are skipped the same way where
the ``browser`` extra or a browser is missing, and every served Dashboard keeps who decides in a
folder of the test's own. The project runs the shipped rules. Every name is invented.
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
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_import_author")
    pool = base / "pool"
    for folder in ("2024-03-05_kelt", "2024-03-06_kelt"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"zib_glorbex_{index:02d}.csv").write_text(f"a\n{index}\n")
        (pool / folder / "trace.qzx").write_text("x")
    project = Project.create(base / "project", created_at="t", roots=[pool])
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

    from ingence.api.decider import write_decider
    from ingence.core.ids import PROJECT_SCOPE
    from ingence.core.schema import RecordEventKind
    from ingence.record.log import append_event

    project = tmp_path / "project"
    shutil.copytree(built, project)
    # Somebody else has answered here already, so Import offers them as an author.
    append_event(
        project,
        kind=RecordEventKind.NOTE,
        actor="wren",
        occurred_at="2026-09-30T20:00:00Z",
        target="extension:csv",
        against_run_id="run_20260101T000002Z_222222222222",
        payload={"note": "tables"},
        scope=PROJECT_SCOPE,
    )
    config = tmp_path / "config"
    config.mkdir()
    write_decider("kelt", set_at="2026-09-30T20:00:00Z", config_home=config)
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


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


#: A filled answers file about two extensions the invented pool holds.
ANSWERS = json.dumps(
    {
        "answers": {
            "extension:qzx": {"word": "never", "basis": "a tracker's trace, one per day"},
            "extension:csv": {"word": "yes", "basis": "tables of values"},
        }
    }
)


def _open_import(page: Any, base: str) -> Any:
    page.goto(f"{base}#review")
    page.wait_for_function("document.getElementById('import-button').dataset.view === 'review'")
    page.wait_for_function("!document.getElementById('import-button').disabled")
    page.locator("#import-button").click()
    dialog = page.locator("dialog.import-dialog")
    dialog.wait_for()
    return dialog


def _choose(dialog: Any, author: str) -> None:
    dialog.locator("#import-file").set_input_files(
        files=[{"name": "answers.json", "mimeType": "application/json", "buffer": ANSWERS.encode()}]
    )
    dialog.locator("#import-author").select_option(label=author)
    dialog.get_by_role("button", name="Import").click()


def test_import_asks_for_the_file_what_it_is_and_who_wrote_it(browser, served) -> None:
    _project, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        dialog = _open_import(page, base)
        assert dialog.locator("#import-kind").input_value() == "answers"
        authors = dialog.locator("#import-author option").all_inner_texts()
        assert authors == ["kelt (you)", "wren", "Agent-generated"]
        _shots(page, "import-dialog")
        # A settings file is drafted by whoever commits it: the box does not ask who wrote it.
        dialog.locator("#import-kind").select_option("settings")
        assert not dialog.locator("#import-author").is_visible()
        # Nothing chosen: the box says so and stays open.
        dialog.get_by_role("button", name="Import").click()
        dialog.get_by_text("Choose a file first.").wait_for()
        dialog.get_by_role("button", name="Cancel").click()
        assert page.locator("dialog.import-dialog").count() == 0
    finally:
        page.close()


def test_an_agent_s_file_is_recorded_as_the_person_s_agent_once_they_confirm(
    browser, served
) -> None:
    project, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _choose(_open_import(page, base), "Agent-generated")
        confirm = page.locator("dialog.ingence-dialog")
        confirm.get_by_text("kelt_agent").wait_for()
        _shots(page, "agent-confirmation")
        assert len(_events(project)) == 1
        confirm.get_by_role("button", name="I reviewed them").click()
        line = page.locator("#imported")
        line.get_by_text("2 answers saved as kelt_agent's").wait_for()
        _shots(page, "agent-imported")
        written = _events(project)[1:]
        assert sorted(event.target for event in written) == ["extension:csv", "extension:qzx"]
        for event in written:
            source = json.loads(event.payload)["source"]
            assert event.actor == "kelt_agent"
            assert (source["agent_generated"], source["reviewed_by"]) == (True, "kelt")
    finally:
        page.close()


def test_cancelling_the_confirmation_writes_nothing(browser, served) -> None:
    project, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _choose(_open_import(page, base), "Agent-generated")
        confirm = page.locator("dialog.ingence-dialog")
        confirm.get_by_text("kelt_agent").wait_for()
        confirm.get_by_role("button", name="Cancel").click()
        page.wait_for_function("!document.querySelector('dialog')")
        assert len(_events(project)) == 1
        # ``init`` makes the project's folders, ``imports/`` among them: nothing is kept in it.
        assert not [path for path in (project / "imports").glob("**/*") if path.is_file()]
    finally:
        page.close()


def test_another_person_s_file_is_theirs_and_names_who_imported_it(browser, served) -> None:
    project, base = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _choose(_open_import(page, base), "wren")
        line = page.locator("#imported")
        try:
            line.get_by_text("Imported answers.json").wait_for()
        except sync_api.TimeoutError:
            # Said with what the page showed: this wait has failed rarely, and never alone.
            dialogs = page.locator("dialog").all_inner_texts()
            pytest.fail(
                f"no import line; the import line says {line.inner_text()!r} (hidden: "
                f"{line.is_hidden()}); dialogs open: {dialogs}; events written: "
                f"{len(_events(project))}"
            )
        assert "2 answers saved as wren's" in line.inner_text()
        # The page goes on to a waiting question that leads, or, where none leads (this project:
        # its questions are all in the groups of the rest), says to choose one. The line is
        # where advancing leaves it.
        page.locator("#detail .q-card, #detail p.empty").first.wait_for()
        # The page has read the next question and the header what waits, and is quiet.
        settled(page)
        assert "2 answers saved as wren's" in line.inner_text()
        assert line.is_visible()
        # No confirmation is asked for a person's file.
        written = _events(project)[1:]
        assert {event.actor for event in written} == {"wren"}
        for event in written:
            source = json.loads(event.payload)["source"]
            assert (source["imported_by"], source["imported_from"]) == ("kelt", "answers.json")
            assert "agent_generated" not in source
    finally:
        page.close()


def test_a_whole_exported_file_returned_filled_imports_however_many_questions_it_asks(
    browser, served
) -> None:
    """The whole export comes back filled and is larger than a write. Only its answers are sent,
    so a file of two hundred questions imports as a file of two does."""
    project, base = served
    questions = [
        {
            "target": f"question:binding_is_ambiguous:zorbex{index:03d}",
            "question": f"What does zorbex{index:03d} mean here?",
            "words": [],
            "evidence": {
                "names": [f"zib_glorbex_zorbex{index:03d}_{name:02d}.csv" for name in range(15)],
                "folders": [f"2024-03-{1 + name % 28:02d}_kelt" for name in range(15)],
                "engine_reading": {},
            },
        }
        for index in range(200)
    ]
    whole = json.dumps(
        {"format": "ingence.questions/1", "questions": questions, **json.loads(ANSWERS)}
    )
    assert len(whole) > 60 * 1024
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        dialog = _open_import(page, base)
        dialog.locator("#import-file").set_input_files(
            files=[{"name": "whole.json", "mimeType": "application/json", "buffer": whole.encode()}]
        )
        dialog.get_by_role("button", name="Import").click()
        line = page.locator("#imported")
        line.get_by_text("Imported whole.json").wait_for()
        assert "2 answers saved as kelt's" in line.inner_text()
        assert len(_events(project)) == 3
    finally:
        page.close()
