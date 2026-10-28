"""The Questions pages driven in a real browser.

The tests run beside ``test_browser_dashboard.py``, whose harness this file copies, and are
skipped the same way where the ``browser`` extra or a browser is missing. Every served Dashboard
keeps who decides in a folder of the test's own (``INGENCE_CONFIG_HOME``). They check these
behaviours:

1. Auto-advance in every split view: opening a section fills the pane with its first waiting row.
   After a choice the next waiting row fills it at once. At the end, one line says so.
2. The note box has no label and says "notes/context".
3. A note's chips: a term the engine recognises in a note is written only on a click.
4. The shown words: "Can't say", "Empty or blank output file", and no subtext under a label.
5. The copies answers: "Copies", "Unrelated" and "Can't say". The answer "Related, not copies"
   does not exist.
6. Claim samples: one answer for every file a value was concluded about, from its list.
7. Proposed pairs: "Related".

Every name here is invented.
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
#: 2020-09-13, one instant shared by three copies of one file.
SHARED_INSTANT = 1_600_000_000


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
    """Every kind of row: questions, a class of copies, claims, pairs, extensions, a program."""
    from fixtures.one_folder_pairs import allow_pairs_in_one_folder
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from test_review_pages import _pool

    base = tmp_path_factory.mktemp("questions_pages")
    pool = _pool(base)
    for folder in ("2024-03-05_kelt", "2024-03-06_vorn", "2024-03-07_zeph"):
        copy = pool / "runs" / folder / "brisa_19_quant.csv"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("a,b\n1,2\n3,4\n")
        os.utime(copy, (SHARED_INSTANT, SHARED_INSTANT))
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    # The review pages' pairs lie in one folder, which the identity leaves as siblings.
    allow_pairs_in_one_folder(project.path)
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
    (config / "decider.json").write_text(json.dumps({"decider": "trell", "set_at": "t"}))
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


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


def _decider_named(page: Any) -> None:
    """Who decides is set in the test's own configuration folder; the page reads it on load."""
    page.wait_for_function("() => (typeof me !== 'undefined') && me.actor === 'trell'")


def _opened(page: Any, base: str, view: str) -> Any:
    page.goto(f"{base}#{view}")
    _decider_named(page)
    detail = page.locator("#detail")
    return detail


# ---------------------------------------------------------------------------
# 1. Auto-advance, on every split view.
# ---------------------------------------------------------------------------

#: Each page of questions but the first, and a word its rows all offer (the Questions page has
#: its own cards: ``test_browser_questions_conclusions.py``).
SHEETS = (
    ("classes", "cannot_say"),
    ("claims", "cannot_say"),
    ("candidates", "cannot_say"),
    ("extensions", "yes"),
)


@pytest.mark.parametrize(("view", "word"), SHEETS)
def test_each_sheet_opens_on_its_first_waiting_row_and_advances_after_a_choice(
    browser, served, view: str, word: str
) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, view)
        # Opening the section fills the pane with its first waiting row, marked in the list.
        selected = page.locator("#list .row.selected")
        selected.first.wait_for()
        first = selected.first.get_attribute("data-target")
        assert first == page.locator("#list tr.row").first.get_attribute("data-target")
        detail.locator("h2.question").wait_for()
        before = len(_events(project))
        rows = page.locator("#list tr.row").count()
        # A word only this row's kind offers is chosen where the row offers it.
        button = detail.locator(f'button.answer[data-word="{word}"]')
        if not button.count():
            button = detail.locator(".answers button.answer[data-word]").first
        button.first.click()
        detail.locator('[data-advance="recorded"], [data-advance="done"]').first.wait_for()
        assert len(_events(project)) == before + 1
        if rows > 1:
            # The next waiting row fills the pane at once, and the answered one stays where it was
            # with its answer in the person's color.
            detail.locator('[data-advance="recorded"]').wait_for()
            detail.locator("h2.question").wait_for()
            now = page.locator("#list .row.selected").first.get_attribute("data-target")
            assert now and now != first
            answered_row = page.locator(f'#list tr.row[data-target="{first}"]')
            assert answered_row.count() == 1
            assert answered_row.locator(".tag.person").count() == 1
            # The keyboard stays on the list: the open row holds the focus.
            assert page.evaluate("document.activeElement.dataset.target") == now
        else:
            detail.locator('[data-advance="done"]').wait_for()
        _shots(page, f"advance-{view}")
    finally:
        page.close()


def test_the_last_answer_says_so_in_one_line(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, "candidates")
        detail.locator("h2.question").wait_for()
        for _ in range(page.locator("#list tr.row").count()):
            was = page.locator("#list .row.selected").first.get_attribute("data-target")
            detail.locator('button.answer[data-word="unrelated"]').click()
            # The next pair opens, or the last line is said.
            page.wait_for_function(
                "(was) => document.querySelector('#detail [data-advance=done]')"
                " || ((document.querySelector('#list .row.selected') || {}).dataset || {}).target"
                " !== was",
                arg=was,
            )
            if detail.locator('[data-advance="done"]').count():
                break
        done = detail.locator('[data-advance="done"]')
        done.wait_for()
        assert "every suggested pair" in done.inner_text()
        _shots(page, "advance-done")
    finally:
        page.close()


def test_programs_and_words_advance_too(browser, served) -> None:
    """Programs advance the same way. The words that bind to nothing are in the Words section, not
    in Coverage. The promise is the same as for every split view: an answer opens the next word
    at once, and the last one says so. The Words section keeps it, and its card says "not a
    person's word" with what it is to a computer."""
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, "programs")
        detail.locator("h2.question").wait_for()
        assert page.locator("#list .program.row.selected").count() == 1
        page.goto(f"{base}#words")
        _decider_named(page)
        page.wait_for_selector(".words-card h2 mark")
        words = page.locator("#list tr.words-row").count()
        first = detail.locator("h2 mark").inner_text()
        page.locator('.words-option[data-choice="not_a_word"]').click()
        before = len(_events(project))
        detail.locator(".words-computer .answers button.answer").filter(
            has_text="just letters"
        ).first.click()
        if words > 1:
            detail.locator('[data-advance="recorded"]').wait_for()
            page.wait_for_function(
                "(was) => document.querySelector('#detail .words-card h2 mark')"
                " && document.querySelector('#detail .words-card h2 mark').textContent !== was",
                arg=first,
            )
        else:
            detail.locator('[data-advance="done"]').wait_for()
        assert len(_events(project)) == before + 1
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 2 and 3. The note box, and what it recognises.
# ---------------------------------------------------------------------------


def test_the_note_box_has_no_label_and_says_notes_context(browser, served) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        for view in ("classes", "claims", "candidates", "extensions", "programs"):
            detail = _opened(page, base, view)
            box = detail.locator("input.note-input")
            box.wait_for()
            assert box.get_attribute("placeholder") == "notes/context"
            assert detail.locator(".note-field label").count() == 0
            assert detail.get_by_text("decides nothing, and is kept beside the row").count() == 0
    finally:
        page.close()


def test_a_note_s_term_is_kept_only_when_its_chip_is_clicked(browser, served) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, "classes")
        detail.locator("h2.question").wait_for()
        target = page.locator("#list .row.selected").first.get_attribute("data-target")
        before = len(_events(project))
        detail.locator("input.note-input").type("raw plates, from vorn", delay=20)
        chip = detail.locator(".note-chips .note-chip.bound").first
        chip.wait_for()
        detail.locator(".note-chips .note-chip.unbound", has_text="vorn").wait_for()
        assert "does not use it yet" in (chip.get_attribute("title") or "")
        # Typing wrote nothing.
        assert len(_events(project)) == before
        _shots(page, "note-chips")
        chip.click()
        detail.locator(".note-chips .note-chip.kept").wait_for()
        events = _events(project)
        assert len(events) == before + 1
        event = events[-1]
        said = json.loads(event.payload)
        assert (event.kind.value, event.target, event.actor) == ("note", target, "trell")
        assert said["observed"][0]["term"]
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 4 to 7. The words shown, and the new answers.
# ---------------------------------------------------------------------------


def _row_of(page: Any, text: str, *also: str) -> Any:
    rows = page.locator("#list tr.row", has_text=text)
    for more in also:
        rows = rows.filter(has_text=more)
    row = rows.first
    row.wait_for()
    row.click()
    return page.locator("#detail")


def test_the_shown_words_are_cannot_say_and_empty_or_blank_output_file_with_no_subtext(
    browser, served
) -> None:
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    # The first read of a busy machine is slow: the page's state is waited for.
    page.set_default_timeout(180_000)
    try:
        _opened(page, base, "review")
        # A question that does not lead is one chip away.
        page.locator("button.q-chip", has_text="Content unreadable").click()
        detail = _row_of(page, "empty")
        detail.locator(".q-card-title", has_text="An empty file").wait_for()
        buttons = detail.locator("button.answer[data-word]")
        shown = [text.strip() for text in buttons.all_inner_texts()]
        assert "Empty or blank output file" in shown
        assert "Can't say" in shown and "Other" not in shown
        assert detail.locator("button.answer[data-word] .kind").count() == 0
        # The rule that asks it is named, with its version, one click away.
        detail.locator("details.q-why summary").click()
        assert "content_unreadable" in detail.locator("details.q-why").inner_text()
        _shots(page, "question-content-unreadable")
    finally:
        page.close()


def test_a_class_of_copies_is_answered_as_a_group_or_not(browser, served) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _opened(page, base, "classes")
        detail = _row_of(page, "brisa_19_quant")
        detail.locator("h2.question", has_text="Copies of one file?").wait_for()
        shown = [
            text.strip()
            for text in detail.locator(".answers button.answer[data-word]").all_inner_texts()
        ]
        assert shown == ["Copies", "Unrelated", "Can't say"]
        _shots(page, "classes-copies")
        assert detail.locator('button.answer[data-word="related"]').count() == 0
        detail.locator('button.answer[data-word="not_copies"]').click()
        detail.locator('[data-advance="recorded"], [data-advance="done"]').first.wait_for()
        said = json.loads(_events(project)[-1].payload)
        assert said["verdict"] == "not_copies"
    finally:
        page.close()


def test_a_claim_sample_is_answered_for_every_file_like_it_at_once(browser, served) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, "claims")
        detail.locator("h2.question").wait_for()
        more = detail.locator("p.asks", has_text="more like it")
        if not more.count():
            # The raw plate's stage, shared with nine other files; its time is its own, and its row
            # comes first because the plate's pair is written an hour apart.
            _row_of(page, "raw_plate", "pipeline_stage_from_name")
        detail.locator("p.asks", has_text="more like it").wait_for()
        # The answer for all is offered only once the list is on the page.
        assert detail.locator('button[data-word="correct-for-all"]').count() == 0
        detail.locator('button.answer[data-word="list-for-all"]').click()
        names = detail.locator(".for-all-list ul.names li")
        names.first.wait_for()
        listed = names.count()
        assert listed >= 2
        _shots(page, "claims-for-all")
        before = len(_events(project))
        detail.locator('button.answer[data-word="correct-for-all"]').click()
        detail.locator(
            '[data-advance="recorded"], [data-advance="done"]', has_text="correct for all"
        ).first.wait_for()
        written = _events(project)[before:]
        assert len(written) == listed
        assert all(json.loads(event.payload)["verdict"] == "correct" for event in written)
        assert all(
            json.loads(event.payload)["source"]["answered_together"] == listed for event in written
        )
    finally:
        page.close()


def test_a_proposed_pair_may_be_related(browser, served) -> None:
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, "candidates")
        detail.locator("h2.question", has_text="made from").wait_for()
        assert "tier" not in detail.inner_text()
        related = detail.locator('button.answer[data-word="related"]')
        assert related.inner_text().startswith("Related")
        _shots(page, "pairs-related")
        related.click()
        detail.locator('[data-advance="recorded"], [data-advance="done"]').first.wait_for()
        said = json.loads(_events(project)[-1].payload)
        assert (said["verdict"], said["relation"]) == ("related", "concerns")
    finally:
        page.close()


def test_two_files_of_one_name_are_asked_about_by_their_paths(browser, served) -> None:
    """A pair of one name in sibling folders, proposed as related, names each file by its path,
    never "Do a.csv and a.csv belong together?"."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _opened(page, base, "candidates")
        row = (
            page.locator("#list tr.row", has_text="vorn_sim_raw_0")
            .filter(has_not_text="vorn_sim_processed_0")
            .first
        )
        row.wait_for()
        row.click()
        question = page.locator("#detail h2.question", has_text="belong together")
        question.wait_for()
        said = question.inner_text()
        named = [code.inner_text() for code in question.locator("code").all()]
        assert len(named) == 2 and named[0] != named[1], said
        assert all(name.endswith("/vorn_sim_raw_0.csv") for name in named), said
    finally:
        page.close()


def test_a_pair_proposed_as_related_is_asked_whether_the_two_belong_together(
    browser, served
) -> None:
    """A pair proposed as related has no direction. Its question asks whether the two belong
    together, naming both, and its line says where, never which came earlier."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _opened(page, base, "candidates")
        detail = _row_of(page, "vorn_sim_raw_0", "vorn_sim_processed_0")
        question = detail.locator("h2.question", has_text="belong together")
        question.wait_for()
        said = question.inner_text()
        assert said.startswith("Do ") and said.endswith(" belong together?")
        assert "vorn_sim_raw_0.csv" in said and "vorn_sim_processed_0.csv" in said
        assert "made from" not in said
        asks = detail.locator("p.asks").first.inner_text()
        assert asks.startswith("In ")
        assert "Earlier" not in asks and "later" not in asks
        _shots(page, "pairs-related-question")
    finally:
        page.close()


# ---------------------------------------------------------------------------
# More cases: sets, lists and reloads.
# ---------------------------------------------------------------------------


def test_the_selected_rows_bar_answers_a_set_on_every_rule_and_it_leaves(browser, served) -> None:
    """The bar answers a set that several rules formed on every rule, not on its first rule only."""
    project, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        _opened(page, base, "classes")
        row = page.locator("#list tr.row", has_text="plate_alpha").filter(has_text="more").first
        row.wait_for()
        target = row.get_attribute("data-target")
        rules = 1 + int(row.locator("td").nth(3).inner_text().split("and ")[1].split()[0])
        before = len(_events(project))
        row.locator("input[type=checkbox]").check()
        bar = page.locator("#list .bulk-bar")
        bar.get_by_role("button", name="Not one group").click()
        # The row stays where it was, with its answer.
        page.wait_for_function(
            "(t) => !!document.querySelector(`#list tr.row[data-target='${t}'] .tag.person`)",
            arg=target,
        )
        written = _events(project)[before:]
        assert len(written) == rules >= 2
        assert all(json.loads(event.payload)["verdict"] == "mixed" for event in written)
    finally:
        page.close()


def test_a_cut_list_is_not_answered_for_all(browser, served) -> None:
    """A list naming fewer files than the value holds is never answered as "all"."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})

    def cut(route: Any) -> None:
        found = route.fetch()
        body = json.loads(found.text())
        body["files"] = int(body["listed"]) + 10
        route.fulfill(
            status=200,
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=json.dumps(body),
        )

    page.route("**/api/claim-value*", cut)
    try:
        detail = _opened(page, base, "claims")
        detail.locator("h2.question").wait_for()
        if not detail.locator("p.asks", has_text="more like it").count():
            # The raw plate's stage, shared with nine other files; its time is its own, and its row
            # comes first because the plate's pair is written an hour apart.
            _row_of(page, "raw_plate", "pipeline_stage_from_name")
        detail.locator('button.answer[data-word="list-for-all"]').click()
        detail.locator('[data-for-all="cut"]').wait_for()
        assert detail.locator('button[data-word="correct-for-all"]').count() == 0
        assert "listed" in detail.locator('[data-for-all="cut"]').inner_text()
    finally:
        page.close()


def test_a_kept_term_stays_kept_when_the_row_is_opened_again(browser, served) -> None:
    """After a reload, a kept term is not offered again, and the Record never says "null"."""
    _, base, _ = served
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        detail = _opened(page, base, "classes")
        detail.locator("h2.question").wait_for()
        detail.locator("input.note-input").type("raw plates", delay=20)
        chip = detail.locator(".note-chips .note-chip.bound").first
        chip.wait_for()
        term = chip.get_attribute("data-term")
        chip.click()
        detail.locator(".note-chips .note-chip.kept").wait_for()
        page.reload()
        _decider_named(page)
        detail.locator("h2.question").wait_for()
        record = detail.locator("ul.record").inner_text()
        assert "kept: " in record and "null" not in record
        detail.locator("input.note-input").type("raw plates", delay=20)
        detail.locator(f'.note-chips .note-chip.kept[data-term="{term}"]').wait_for()
    finally:
        page.close()
