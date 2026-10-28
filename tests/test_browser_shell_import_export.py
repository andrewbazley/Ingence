"""The shell, Import and Export by page, Rules and Settings, in a browser.

The tests run in a real browser beside ``test_browser_dashboard.py``, whose harness this file
copies. They are skipped the same way where the ``browser`` extra or a browser is missing. Every
served Dashboard keeps who decides in a folder of the test's own (``INGENCE_CONFIG_HOME``). Every
name here is invented.

1. Advanced is the last tab. It holds Rules, Dictionary and Settings, and every old address lands.
2. The Questions tab carries a dot, not a number, while anything waits. The number is in its
   tooltip and its accessible name, and nothing is shown when nothing waits.
3. What is looked at belongs to the page it was set on: the header's line is hidden on another
   page and comes back on returning.
4. One way to show a rule: plain words, no name in brackets, and a toggle labelled with the name
   and version that opens the rule as written with its settings.
5. Settings: every field is pre-filled with the value in force, and a changed value is marked.
6. Import and Export by page, with Import asking what the file is and who wrote it.
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

from fixtures.dashboard_browser import launch_browser

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
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("browser_shell_import_export")
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


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


def _entry_id(project: Path, name: str) -> str:
    from ingence.storage.catalog import open_catalog

    with open_catalog(project) as catalog:
        found = catalog.query("SELECT entry_id FROM entries WHERE name = ?", [name]).fetchone()
    assert found is not None
    return str(found[0])


def _name_the_decider(page: Any, name: str) -> None:
    dialog = page.locator("dialog.ingence-dialog")
    dialog.get_by_label("Your name").fill(name)
    dialog.get_by_role("button", name="Use this name").click()


# -- 1. the tabs --------------------------------------------------------------------------------


def test_advanced_is_last_and_holds_rules_dictionary_and_settings(browser, served) -> None:
    # Engine's sections are the first group of Advanced, the last of six tabs.
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#changes")
        tabs = page.locator("#pages button")
        tabs.first.wait_for()
        assert [name.strip() for name in tabs.all_inner_texts()][-1] == "Advanced"
        assert [tab.get_attribute("data-page") for tab in tabs.all()] == [
            "overview",
            "overlap",
            "experiments",
            "checks",
            "history",
            "advanced",
        ]
        page.locator('#pages button[data-page="advanced"]').click()
        sections = page.locator('nav.sections[data-sections-of="advanced"]')
        sections.wait_for(state="visible")
        assert sections.locator('[aria-labelledby="sections-engine"] button').all_inner_texts() == [
            "Rules",
            "Dictionary",
            "Settings",
            "Folder levels",
        ]
        page.locator("#list h2").get_by_text("Rules", exact=True).wait_for()
        sections.get_by_role("button", name="Dictionary").click()
        # The section itself, whichever page fills it.
        page.wait_for_function("location.hash === '#dictionary'")
        sections.locator('button[data-view="dictionary"].active').wait_for()
        page.locator("#list > *").first.wait_for()
        assert page.locator("#list .state-failed").count() == 0
    finally:
        page.close()


@pytest.mark.parametrize(
    ("old", "view", "heading"),
    [("parameters", "rules", "Rules"), ("rules", "rules", "Rules"), ("settings", "settings", None)],
)
def test_an_old_address_lands_on_the_advanced_page(browser, served, old, view, heading) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#{old}")
        page.wait_for_function(f"location.hash === '#{view}'")
        engine = page.locator('#pages button[data-page="advanced"]')
        assert "active" in (engine.get_attribute("class") or "")
        assert page.locator(f'nav.sections button[data-view="{view}"].active').count() == 1
        if heading:
            page.locator("#list h2").get_by_text(heading, exact=True).wait_for()
        else:
            page.locator("#list table.settings").first.wait_for()
    finally:
        page.close()


# -- 2. the dot of the tab that holds the review sections (Advanced) ----------------------------


def _waiting_as(counts: dict[str, Any]) -> Any:
    return lambda _found: counts


def test_the_questions_tab_carries_a_dot_saying_how_many_wait(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    counts = {"questions": 5, "classes": 2, "claims": 0, "candidates": 0, "extensions": 0}
    _answered_with(page, "**/api/waiting", _waiting_as({**counts, "programs": 0}))
    try:
        page.goto(f"{base}#inventory")
        tab = page.locator('#pages button[data-page="advanced"]')
        tab.locator(".dot").wait_for()
        # No number on the tab itself; its number is in its name and its tooltip.
        assert tab.inner_text().strip() == "Advanced"
        assert tab.get_attribute("aria-label") == "Advanced, 7 waiting"
        assert tab.get_attribute("title") == "7 waiting"
        # The sections under it keep their numbers.
        page.locator('#pages button[data-page="advanced"]').click()
        section = page.locator('nav.sections button[data-waiting="classes"]')
        page.wait_for_function(
            "document.querySelector('nav.sections button[data-waiting=\"classes\"]')"
            ".textContent.includes('(2)')"
        )
        assert "(2)" in section.inner_text()
        _shots(page, "questions-dot")
    finally:
        page.close()


def test_the_questions_tab_carries_nothing_when_nothing_waits(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    counts = dict.fromkeys(("questions", "classes", "claims", "candidates"), 0)
    _answered_with(page, "**/api/waiting", _waiting_as({**counts, "extensions": 0, "programs": 0}))
    try:
        page.goto(f"{base}#inventory")
        tab = page.locator('#pages button[data-page="advanced"]')
        page.wait_for_function(
            "document.querySelector('#pages button[data-page=\"advanced\"]').title !== ''"
        )
        assert tab.locator(".dot").count() == 0
        assert tab.get_attribute("title") == "Nothing waiting"
        assert tab.inner_text().strip() == "Advanced"
    finally:
        page.close()


def test_while_counting_the_dot_is_quiet_and_says_so(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _answered_with(page, "**/api/waiting", _waiting_as({"counting": True}))
    try:
        page.goto(f"{base}#inventory")
        tab = page.locator('#pages button[data-page="advanced"]')
        tab.locator(".dot.counting").wait_for()
        assert tab.get_attribute("aria-label") == "Advanced, counting what waits"
    finally:
        page.close()


# -- 3. what is looked at belongs to its page ---------------------------------------------------


def _a_question_row(page: Any) -> Any:
    """The first question listed. None of this project's questions leads (its unknown words are
    asked on Words, and every domain's words load), so the rows are in the groups of "the rest",
    each a chip that opens its table: the first is opened."""
    page.locator("#list .q-stands").wait_for()
    page.locator("#list button.q-chip[data-title]").first.click()
    row = page.locator("#list table tr.row").first
    row.wait_for()
    return row


def test_what_is_looked_at_belongs_to_the_page_it_was_set_on(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#review")
        _a_question_row(page).click()
        line = page.locator("#focus")
        line.get_by_text("Looking at the question").wait_for()
        said = line.inner_text()
        # Questions is a section of Advanced. The other five pages each keep their own.
        for other in ("history", "overview", "overlap", "experiments", "checks"):
            page.locator(f'#pages button[data-page="{other}"]').click()
            page.wait_for_function(
                '(page) => document.querySelector(`#pages button[data-page="${page}"]`)'
                ".classList.contains('active')",
                arg=other,
            )
            assert line.is_hidden(), other
            assert "question=" not in page.url, other
        page.locator('#pages button[data-page="advanced"]').click()
        line.get_by_text("Looking at the question").wait_for()
        assert line.inner_text() == said
        assert "question=" in page.url
        # A reload on another page keeps each page's own.
        page.locator('#pages button[data-page="history"]').click()
        page.wait_for_function("state.view === 'changes'")
        page.reload()
        page.wait_for_function("state.view === 'changes'")
        assert line.is_hidden()
        page.locator('#pages button[data-page="advanced"]').click()
        line.get_by_text("Looking at the question").wait_for()
    finally:
        page.close()


def test_a_focus_saved_on_the_old_parameters_page_is_kept_on_engine(browser, served) -> None:
    project, base, _ = served
    folder = _entry_id(project, "2024-03-06_kelt")
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#inventory")
        page.locator("#pages button").first.wait_for()
        page.evaluate(
            "(id) => sessionStorage.setItem('ingence.focus', JSON.stringify("
            "{pages: {parameters: {kind: 'entry', id, label: '2024-03-06_kelt'}}}))",
            folder,
        )
        page.goto(f"{base}#rules")
        page.reload()
        page.locator("#focus").get_by_text("2024-03-06_kelt").wait_for()
    finally:
        page.close()


# -- 4. one way to show a rule ------------------------------------------------------------------


def test_a_rule_shows_its_plain_words_and_opens_by_its_name_and_version(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#rules")
        page.locator("#list tr.rules-row:visible").first.wait_for()
        page.get_by_role("button", name="Expand all").click()
        listed = page.locator("#list").inner_text()
        # No lead paragraph, and no rule's name in brackets after its words.
        assert "Every conclusion comes from one of these rules" not in listed
        for plain in page.locator("#list td.rule-plain").all_inner_texts():
            assert not plain.rstrip(".").endswith(")"), plain
        # A row is the rule's words, with its name and version beside them; the card, opened by the
        # row, holds the rule as written and its settings.
        row = page.locator("#rule-same_composition")
        assert "same_composition" in row.inner_text()
        row.click()
        card = page.locator('#detail .rule-card[data-rule="same_composition"]')
        card.wait_for()
        assert card.locator(".card-id").inner_text().startswith("same_composition \u00b7 version ")
        card.locator(".rule-settings").wait_for()
        # A rule's settings shown as the Settings page shows its own: the name, what it is for, the
        # value as a field, no shipped-default column.
        assert card.locator(".rule-setting").count() > 0
        assert card.locator(".rule-setting [data-draft^='rule_parameters:']").count() > 0
        _shots(page, "rules-one-way")
    finally:
        page.close()


# -- 5. the Settings page -----------------------------------------------------------------------


def test_every_setting_is_pre_filled_and_a_changed_one_is_marked(browser, served) -> None:
    project, base, _ = served
    (project / "settings.dashboard.yaml").write_text("scan:\n  workers: 3\n", encoding="utf-8")
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#settings")
        page.locator("#list table.settings").first.wait_for()
        # Grouped under plain headings, each row with its purpose.
        assert "Listing files" in page.locator("#list h3").all_inner_texts()
        purposes = page.locator("#list table.settings .purpose")
        assert purposes.count() > 80
        # Every field holds the value in force: numbers, words, lists and choices alike.
        empty = page.evaluate(
            "() => [...document.querySelectorAll('#list [data-draft^=\"settings:\"]')]"
            ".filter((field) => field.type !== 'checkbox' && field.value === ''"
            " && field.dataset.original !== 'null' && field.dataset.original !== '[]')"
            ".map((field) => field.dataset.draft)"
        )
        assert empty == []
        workers = page.locator('tr[data-setting="scan.workers"]')
        assert workers.locator("input").input_value() == "3"
        # The years have one editor, the block at the top.
        assert page.locator('tr[data-setting="dictionary.years"]').count() == 0
        globs = page.locator('tr[data-setting="scan.exclude_globs"] input')
        assert ".git" in globs.input_value()
        assert (
            page.locator('tr[data-setting="hash.mode"] select').evaluate("(s) => s.value")
            == '"need"'
        )
        # Changed from its default: marked, the default on hover, and a way back to it.
        mark = workers.locator(".tag.changed")
        assert mark.get_attribute("title") == "The default is 8"
        assert "Shipped default" not in page.locator("#list thead").first.inner_text()
        workers.get_by_role("button", name="use the default").click()
        assert workers.locator("input").input_value() == "8"
        workers.get_by_text("draft, saved").wait_for()
        # Where it comes from opens the file.
        workers.get_by_role("button", name="settings.dashboard.yaml").click()
        dialog = page.locator("dialog.settings-file")
        dialog.locator("pre").get_by_text("workers: 3").wait_for()
        _shots(page, "settings-file")
        dialog.get_by_role("button", name="Close").click()
        _shots(page, "settings")
    finally:
        page.close()


# -- 6. Import and Export by page ---------------------------------------------------------------

_TRANSFER = {
    # view: (export enabled, import enabled, words the export's tooltip holds)
    "settings": (True, True, "settings file"),
    "review": (True, True, "questions as one file"),
    "claims": (True, True, "questions as one file"),
    "classes": (True, True, "group answers"),
    "changes": (True, False, "every scan"),
    "rules": (False, False, "Nothing on this page"),
    "inventory": (False, False, "Nothing on this page"),
}


@pytest.mark.parametrize("view", sorted(_TRANSFER))
def test_import_and_export_say_what_they_do_on_each_page(browser, served, view) -> None:
    _, base, _ = served
    exports, imports, words = _TRANSFER[view]
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#{view}")
        page.wait_for_function(
            "(view) => document.getElementById('export-button').dataset.view === view", arg=view
        )
        export = page.locator("#export-button")
        imported = page.locator("#import-button")
        assert export.is_enabled() is exports and imported.is_enabled() is imports
        assert words in (export.get_attribute("title") or "")
        # A disabled button still says why.
        assert imported.get_attribute("title")
    finally:
        page.close()


def _returned_file(page: Any) -> tuple[str, list[str]]:
    pack = page.evaluate("async () => (await fetch('/api/v1/questions/pack')).json()")
    # Questions of the run's own, not a pack's (answered accept or refuse).
    targets = [
        question["target"]
        for question in pack["questions"]
        if not question["target"].startswith("pack:")
    ][:2]
    answers = dict(pack["answers"])
    for target in targets:
        answers[target] = {"word": "empty_output", "basis": "empty beside outputs"}
    return json.dumps({**pack, "answers": answers}), targets


def _import(page: Any, name: str, mime: str, text: str) -> Any:
    """Import through the header's box: the file, what it is (the page's own), who wrote it."""
    page.locator("#import-button").click()
    dialog = page.locator("dialog.import-dialog")
    dialog.locator("#import-file").set_input_files(
        files=[{"name": name, "mimeType": mime, "buffer": text.encode()}]
    )
    dialog.get_by_role("button", name="Import").click()
    return dialog


def test_a_question_file_s_answers_are_written_as_the_person_s_when_imported(
    browser, served
) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#review")
        _a_question_row(page)
        returned, targets = _returned_file(page)
        before = len(_events(project))
        _import(page, "back.json", "application/json", returned)
        _name_the_decider(page, "kelt")
        line = page.locator("#imported")
        line.get_by_text("Imported back.json").wait_for()
        assert f"{len(targets)} answer" in line.inner_text()
        assert "saved as kelt's" in line.inner_text()
        _shots(page, "question-file-imported")
        written = _events(project)[before:]
        assert sorted(event.target for event in written) == sorted(targets)
        for event in written:
            source = json.loads(event.payload)["source"]
            assert event.actor == "kelt"
            assert source["imported_from"] == "back.json"
            assert "imported_by" not in source
    finally:
        page.close()


def test_a_settings_file_is_previewed_and_taken_as_drafts(browser, served) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#settings")
        page.locator("#list table.settings").first.wait_for()
        text = "scan:\n  workers: 5\n  nothing_by_this_name: 1\n"
        box = _import(page, "s.yaml", "application/yaml", text)
        # A settings file is drafted by whoever commits it: the box does not ask who wrote it.
        assert box.count() == 0 or not box.locator("#import-author").is_visible()
        dialog = page.locator("dialog.settings-import")
        dialog.get_by_text("scan.nothing_by_this_name").wait_for()
        assert "there is no setting of this name" in dialog.inner_text()
        _shots(page, "settings-import")
        # Nothing is drafted or written until taken.
        assert not (project / "settings.dashboard.yaml").exists()
        drafts = page.evaluate("async () => (await fetch('/api/workspace/state')).json()")
        assert not drafts["uncommitted"].get("settings")
        dialog.get_by_role("button", name="Take 1 as drafts").click()
        page.locator('tr[data-setting="scan.workers"]').get_by_text("draft, saved").wait_for()
        assert page.locator('tr[data-setting="scan.workers"] input').input_value() == "5"
        assert not (project / "settings.dashboard.yaml").exists()
        assert "workers" not in (project / "settings.yaml").read_text(encoding="utf-8")
    finally:
        page.close()


def test_a_groupings_sheet_s_answers_are_written_as_the_author_named(browser, served) -> None:
    from ingence.record.verdicts import class_index, spelled_key
    from ingence.storage.catalog import open_catalog

    project, base, _ = served
    with open_catalog(project) as catalog:
        index = class_index(catalog)
    spelled = next(
        spelled_key(key)
        for key, rules in sorted(index.by_key.items())
        if len(rules) == 1 and len(index.by_spelling.get(spelled_key(key), ())) == 1
    )
    text = (
        "class key,members,folder(s),verdict (one_thing | mixed),note\n"
        f'"{spelled}",3,x,mixed,two batches\n'
    )
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#classes")
        # The page drawn first, as it is long before a person has chosen a file.
        page.locator("#list h2").first.wait_for()
        page.wait_for_function("!document.querySelector('#detail .state-loading')")
        page.wait_for_function(
            "document.getElementById('import-button').dataset.view === 'classes'"
        )
        before = len(_events(project))
        _import(page, "groups.csv", "text/csv", text)
        _name_the_decider(page, "vorn")
        page.locator("#imported").get_by_text("Imported groups.csv").wait_for()
        _shots(page, "groupings-import")
        written = _events(project)[before:]
        assert len(written) == 1 and written[0].actor == "vorn"
        assert json.loads(written[0].payload)["source"]["imported_from"] == "groups.csv"
    finally:
        page.close()


@pytest.mark.parametrize(
    ("view", "named", "holds"),
    [
        ("settings", "ingence-settings-", "include_hidden: true"),
        ("changes", "ingence-history-", '"record"'),
        ("classes", "ingence-groupings-", "decision,when,who,about,kind,answer,note"),
    ],
)
def test_export_saves_what_the_page_holds(browser, served, view, named, holds) -> None:
    project, base, _ = served
    (project / "settings.dashboard.yaml").write_text(
        "scan:\n  include_hidden: true\n", encoding="utf-8"
    )
    page = browser.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    try:
        page.goto(f"{base}#{view}")
        page.wait_for_function(
            "(view) => document.getElementById('export-button').dataset.view === view", arg=view
        )
        with page.expect_download() as waited:
            page.locator("#export-button").click()
        download = waited.value
        assert download.suggested_filename.startswith(named)
        assert holds in Path(download.path()).read_text(encoding="utf-8")
    finally:
        page.close()


# -- 7. the header's words ----------------------------------------------------------------------


def test_the_header_has_no_count_line(browser, served) -> None:
    # The header's count line is removed. The Overview says what the scan found.
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#inventory")
        page.locator("#pages button").first.wait_for()
        assert page.locator("#summary").count() == 0
        header = page.locator("header").inner_text()
        for words in ("files and folders", "conclusions", "pairs suggested"):
            assert words not in header, words
    finally:
        page.close()


@pytest.mark.parametrize(
    ("status", "said"),
    [
        (
            {"phase": "failed", "spellings": ["kelt"], "error": "the worker stopped"},
            "is kept in the answer log",
        ),
        (
            {"phase": "failed", "rerun": ["inference"], "error": "the worker stopped"},
            "Re-run applies them again",
        ),
    ],
)
def test_a_failed_job_says_what_to_do_on_the_page(browser, served, status, said) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _answered_with(page, "**/api/applying", lambda _found: status)
    try:
        page.goto(f"{base}#inventory")
        banner = page.locator("#applying")
        banner.get_by_text(said).wait_for()
        # The commands a person could type are behind "by hand", never the sentence itself.
        sentence = banner.evaluate(
            "(node) => [...node.childNodes].filter((part) => part.nodeType === 3)"
            ".map((part) => part.textContent).join('')"
        )
        assert "ingence" not in sentence
        assert "ingence index --rebind" in (banner.locator("details").text_content() or "")
    finally:
        page.close()


# -- settings shown and typed in words ----------------------------------------------------------


def test_a_choice_is_offered_in_words_with_its_stored_value_on_hover(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#settings")
        select = page.locator('tr[data-setting="hash.skip_remote_filesystems"] select')
        select.wait_for()
        options = select.locator("option")
        assert options.all_inner_texts() == [
            "left to the depth",
            "hash network drives too",
            "never hash on a network drive",
        ]
        assert options.nth(2).get_attribute("title") == "written as always"
    finally:
        page.close()


def test_a_list_of_words_keeps_what_was_typed(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{base}#settings")
        row = page.locator('tr[data-setting="hash.include_extensions"]')
        row.locator("input").fill("001, tif")
        row.get_by_text("draft, saved").wait_for()
        drafted = page.evaluate(
            "async () => (await (await fetch('/api/workspace/settings')).json()).settings"
            ".find((row) => row.name === 'hash.include_extensions').draft"
        )
        assert drafted == ["001", "tif"]
    finally:
        page.close()
