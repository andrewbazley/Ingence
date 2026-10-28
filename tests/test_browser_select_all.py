"""The box in a table's header chooses every row shown, and a bar answers them together.

The bar answers with the buttons that the chosen rows share.

Skipped, never failed, where the ``browser`` extra or a browser is missing, and required where
``INGENCE_BROWSER_REQUIRED=1``. Each test serves a copy of one invented project on a port between
20000 and 29999, with who decides kept in a folder of the test's own (``INGENCE_CONFIG_HOME``).

What a person is promised:

1. The box has a name and a place: "Select all groups shown" in the header cell, reached by the
   keyboard and moved by Space.
2. One click chooses every row shown, and one button writes its word to each as its own decision,
   in one request. A row that stands for several classes writes a decision to each, and every row
   keeps its place with its answer.
3. The box is checked, mixed or clear as every, some or none of the shown rows are chosen. A click
   on the mixed box chooses the rest; a second click clears them.
4. A filter that hides rows leaves them unchosen, and the bar writes to exactly the rows chosen
   and in sight.
5. A table that pages chooses the rows on its page, says so, and leaves the other pages alone.

The same table draws Groupings, Conclusion samples, Proposed pairs and Extensions, so one box
serves them all (the Questions page's own tables are ``test_browser_questions_conclusions.py``'s).
Every name here is invented.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import REPOSITORY, REQUIRED, launch_browser, need

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: Where axe-core is read from, as the accessibility lane reads it.
NO_AXE = "axe-core is not installed: run `npm install` in the repository, or set INGENCE_AXE_JS"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every kind of row: questions, groupings, conclusion samples, pairs, extensions, a program."""
    from fixtures.one_folder_pairs import allow_pairs_in_one_folder
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from test_review_pages import _pool

    base = tmp_path_factory.mktemp("browser_select_all")
    project = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return project.path


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


@pytest.fixture
def served(built: Path, tmp_path: Path):
    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
    (config / "decider.json").write_text(json.dumps({"decider": "trell", "set_at": "t"}))
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
        [sys.executable, "-c", code, str(project), str(port)],
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


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project, withdrawn=True))


def _rows(base: str, kind: str, **filters: str) -> list[dict[str, Any]]:
    """The rows the page is sent for ``kind``, as the server counts them (one page of fifty)."""
    query = urllib.parse.urlencode({"limit": "50", "offset": "0", **filters})
    with urllib.request.urlopen(f"{base}api/pages/{kind}?{query}", timeout=30) as answer:
        return list(json.load(answer)["rows"])


def _targets_of(row: dict[str, Any]) -> list[str]:
    """Every target one row answers: its own, and each other rule's class beside it."""
    return [row["target"], *(other["target"] for other in row.get("also") or [])]


def _opened(browser: Any, base: str, view: str, width: int = 1400) -> Any:
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto(f"{base}#{view}")
    page.wait_for_function("() => (typeof me !== 'undefined') && me.actor === 'trell'")
    if view == "review":
        # Where no question leads, the rows are in the groups of the rest, each a chip that
        # opens the same table with the same header box; the first is opened.
        page.locator("#list .q-stands").wait_for()
        if page.locator('#list [data-section="leads"] tbody tr.row').count() == 0:
            page.locator("#list button.q-chip[data-title]").first.click()
    page.locator("#list tbody tr.row").first.wait_for()
    return page


def _header(page: Any) -> Any:
    return page.locator("#list thead input.select-all")


def _boxes(page: Any) -> Any:
    return page.locator("#list tbody input[type=checkbox]")


def _state(page: Any) -> tuple[bool, bool]:
    """The header box's ``(checked, indeterminate)``, as the browser holds them."""
    checked, mixed = page.evaluate(
        "() => { const box = document.querySelector('#list thead input.select-all');"
        " return [box.checked, box.indeterminate]; }"
    )
    return bool(checked), bool(mixed)


def _ticked(page: Any) -> int:
    return int(
        page.evaluate(
            "() => [...document.querySelectorAll('#list tbody input[type=checkbox]')]"
            ".filter((box) => box.checked).length"
        )
    )


def _box_of(page: Any, target: str) -> Any:
    return page.locator(f'#list tr.row[data-target="{target}"] input[type=checkbox]')


def _bar(page: Any) -> Any:
    return page.locator("#list .bulk-bar")


def _count(page: Any) -> str:
    return str(_bar(page).locator(".bulk-count").inner_text())


def _answered(page: Any) -> int:
    """How many rows show an answer in the person's color."""
    return int(page.locator("#list tbody tr.row .tag.person").count())


def _rule_filter(page: Any) -> Any:
    return page.locator('#list select[aria-label="Rule"]')


def _wait_for_rows(page: Any, count: int) -> None:
    page.wait_for_function(
        "(count) => document.querySelectorAll('#list tbody tr.row').length === count", arg=count
    )


# ---------------------------------------------------------------------------
# 1. The box has a name and a place, and the keyboard reaches it.
# ---------------------------------------------------------------------------


def test_the_header_box_is_named_sits_in_the_header_and_the_keyboard_moves_it(
    browser, served
) -> None:
    _, base = served
    page = _opened(browser, base, "classes")
    try:
        header = page.get_by_role("checkbox", name="Select all groups shown")
        assert header.count() == 1
        assert page.locator("#list thead th input.select-all").count() == 1
        total = page.locator("#list tbody tr.row").count()
        assert _boxes(page).count() == total > 1
        # From the heading above, the keyboard arrives at the box, and Space chooses.
        page.locator("#list h2").first.click()
        for _ in range(80):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.classList.contains('select-all')"):
                break
        assert page.evaluate("document.activeElement.classList.contains('select-all')")
        page.keyboard.press("Space")
        assert _state(page) == (True, False)
        assert _ticked(page) == total
        assert _count(page) == f"{total} of {total} chosen"
        page.keyboard.press("Space")
        assert _state(page) == (False, False)
        assert _ticked(page) == 0
        assert _bar(page).is_hidden()
    finally:
        page.close()


def test_the_pages_of_every_kind_name_what_the_box_chooses(browser, served) -> None:
    _, base = served
    names = {
        "classes": "groups",
        "claims": "files",
        "candidates": "pairs",
        "extensions": "extensions",
    }
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        for view, noun in names.items():
            page.goto(f"{base}#{view}")
            page.locator("#list tbody tr.row").first.wait_for()
            assert page.get_by_role("checkbox", name=f"Select all {noun} shown").count() == 1
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 2. One click chooses every row shown, and the bar writes to each.
# ---------------------------------------------------------------------------


def test_the_header_box_chooses_every_conclusion_shown_and_one_press_answers_them_all(
    browser, served
) -> None:
    project, base = served
    rows = [row for row in _rows(base, "claims") if not row.get("grouped_by")]
    assert len(rows) >= 3
    page = _opened(browser, base, "claims")
    try:
        before = len(_events(project))
        _header(page).check()
        assert _ticked(page) == len(rows)
        assert _state(page) == (True, False)
        bar = _bar(page)
        assert _count(page) == f"{len(rows)} of {len(rows)} chosen"
        assert bar.locator(".bulk-buttons button").all_inner_texts() == [
            "Correct",
            "Wrong",
            "Can't say",
        ]
        bar.locator("input.bulk-note").fill("checked together")
        sent: list[Any] = []
        page.on(
            "request",
            lambda request: (
                sent.append(request)
                if request.method == "POST" and request.url.endswith("/api/review/answer")
                else None
            ),
        )
        bar.get_by_role("button", name="Correct").click()
        page.wait_for_function(
            "(count) => document.querySelectorAll("
            "'#list tbody tr.row .tag.person').length === count",
            arg=len(rows),
        )
        # One request, one decision for each row, the same note, and the bar closed.
        assert len(sent) == 1
        written = _events(project)[before:]
        assert sorted(event.target for event in written) == sorted(row["target"] for row in rows)
        assert {event.actor for event in written} == {"trell"}
        assert {json.loads(event.payload)["note"] for event in written} == {"checked together"}
        assert {json.loads(event.payload)["verdict"] for event in written} == {"correct"}
        assert bar.is_hidden()
        # Every row keeps its place and shows its answer; none left the list.
        assert page.locator("#list tbody tr.row").count() == len(rows)
        assert {
            page.locator("#list tbody tr.row .tag.person").nth(i).inner_text()
            for i in range(len(rows))
        } == {"Correct"}
    finally:
        page.close()


def test_the_header_box_chooses_all_the_groupings_and_a_word_is_written_to_each_target(
    browser, served
) -> None:
    project, base = served
    rows = _rows(base, "classes")
    expected = sorted(target for row in rows for target in _targets_of(row))
    assert len(rows) > 10 and len(expected) > len(rows)
    page = _opened(browser, base, "classes")
    try:
        before = len(_events(project))
        _header(page).check()
        assert _ticked(page) == len(rows)
        bar = _bar(page)
        assert _count(page) == f"{len(rows)} of {len(rows)} chosen"
        bar.get_by_role("button", name="Not one group").click()
        page.wait_for_function(
            "(count) => document.querySelectorAll("
            "'#list tbody tr.row .tag.person').length === count",
            arg=len(rows),
        )
        written = _events(project)[before:]
        assert sorted(event.target for event in written) == expected
        assert {json.loads(event.payload)["verdict"] for event in written} == {"mixed"}
        # Every row is answered and stays where it was, so the table and its header box remain.
        assert _header(page).count() == 1
        assert page.locator("#list tbody tr.row").count() == len(rows)
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 3. Checked, mixed or clear.
# ---------------------------------------------------------------------------


def test_the_box_is_mixed_when_some_rows_are_chosen_and_a_click_chooses_the_rest(
    browser, served
) -> None:
    _, base = served
    page = _opened(browser, base, "classes")
    try:
        total = _boxes(page).count()
        assert _state(page) == (False, False)
        # One row chosen: mixed.
        _boxes(page).first.check()
        assert _state(page) == (False, True)
        # Every row chosen one by one: checked, and the same as one click.
        for index in range(1, total):
            _boxes(page).nth(index).check()
        assert _state(page) == (True, False)
        assert _ticked(page) == total
        # One row cleared: mixed, and the bar counts the rest.
        _boxes(page).nth(3).uncheck()
        assert _state(page) == (False, True)
        assert _count(page) == f"{total - 1} of {total} chosen"
        # A click on the mixed box chooses the rest.
        _header(page).click()
        assert _state(page) == (True, False)
        assert _ticked(page) == total
        assert _count(page) == f"{total} of {total} chosen"
        # A second click clears every row shown, and the bar goes.
        _header(page).click()
        assert _state(page) == (False, False)
        assert _ticked(page) == 0
        assert _bar(page).is_hidden()
        # The last chosen row cleared by hand leaves the box clear, not mixed.
        _boxes(page).nth(2).check()
        assert _state(page) == (False, True)
        _boxes(page).nth(2).uncheck()
        assert _state(page) == (False, False)
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 4. A filter that hides rows leaves them unchosen.
# ---------------------------------------------------------------------------


def test_a_filter_that_hides_rows_leaves_them_unchosen_and_the_bar_writes_to_the_chosen_only(
    browser, served
) -> None:
    project, base = served
    narrowed = _rows(base, "classes", rule="replicates_of_one_condition")
    everything = _rows(base, "classes")
    assert 0 < len(narrowed) < len(everything)
    inside = {row["target"] for row in narrowed}
    page = _opened(browser, base, "classes")
    try:
        # A row the filter will hide is chosen first.
        outside = next(row for row in everything if row["target"] not in inside)
        _box_of(page, outside["target"]).check()
        assert _state(page) == (False, True)
        # The filter changes the box's state: of the rows now shown, none is chosen.
        _rule_filter(page).select_option("replicates_of_one_condition")
        _wait_for_rows(page, len(narrowed))
        assert _state(page) == (False, False)
        assert _ticked(page) == 0
        assert _bar(page).is_hidden()
        # The box chooses the rows shown, and only those.
        _header(page).check()
        assert _ticked(page) == len(narrowed)
        assert _count(page) == f"{len(narrowed)} of {len(narrowed)} chosen"
        # With the filter off, the rows that were hidden are unchosen, and the box is mixed.
        _rule_filter(page).select_option("")
        _wait_for_rows(page, len(everything))
        assert _state(page) == (False, True)
        assert _ticked(page) == len(narrowed)
        assert _box_of(page, outside["target"]).is_checked() is False
        # The bar writes to the chosen rows and to no other.
        before = len(_events(project))
        bar = _bar(page)
        bar.get_by_role("button", name="Not one group").click()
        page.wait_for_function(
            "(count) => document.querySelectorAll("
            "'#list tbody tr.row .tag.person').length === count",
            arg=len(narrowed),
        )
        written = _events(project)[before:]
        assert sorted(event.target for event in written) == sorted(
            target for row in narrowed for target in _targets_of(row)
        )
        assert outside["target"] not in {event.target for event in written}
        # Every row, answered or not, is still in the list.
        _wait_for_rows(page, len(everything))
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 5. A table that pages.
# ---------------------------------------------------------------------------


def test_a_table_that_pages_chooses_the_rows_on_its_page_and_leaves_the_others(
    browser, served
) -> None:
    project, base = served
    everything = _rows(base, "classes")
    size = 6
    assert len(everything) > 2 * size
    page = browser.new_page(viewport={"width": 1400, "height": 900})

    def smaller_pages(route: Any) -> None:
        """The page script with six rows to a page, so the invented project has more than one."""
        found = route.fetch()
        script = found.text().replace("const PAGE_SIZE = 50;", f"const PAGE_SIZE = {size};")
        assert f"PAGE_SIZE = {size}" in script
        route.fulfill(
            status=200,
            headers={"Content-Type": "text/javascript; charset=utf-8"},
            body=script,
        )

    page.route("**/review.js", smaller_pages)
    try:
        page.goto(f"{base}#classes")
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor === 'trell'")
        _wait_for_rows(page, size)
        first_page = [
            page.locator("#list tbody tr.row").nth(index).get_attribute("data-target")
            for index in range(size)
        ]
        # The box says what it chooses.
        title = _header(page).get_attribute("title") or ""
        more = len(everything) - size
        assert title == f"Selects the {size} rows on this page. {more} more are on other pages."
        _header(page).check()
        assert _ticked(page) == size
        assert _count(page) == f"{size} of {size} chosen"
        # The next page starts with nothing chosen: the box and the bar say so.
        page.get_by_role("button", name="next 50 →").click()
        page.wait_for_function(
            "(was) => document.querySelector('#list tbody tr.row')?.dataset.target !== was",
            arg=first_page[0],
        )
        _wait_for_rows(page, size)
        assert _state(page) == (False, False)
        assert _bar(page).is_hidden()
        second_page = [
            page.locator("#list tbody tr.row").nth(index).get_attribute("data-target")
            for index in range(size)
        ]
        assert not set(second_page) & set(first_page)
        # The box chooses this page's rows, and a word is written to them alone.
        before = len(_events(project))
        _header(page).check()
        bar = _bar(page)
        assert _count(page) == f"{size} of {size} chosen"
        bar.get_by_role("button", name="Not one group").click()
        page.wait_for_function(
            "(count) => document.querySelectorAll("
            "'#list tbody tr.row .tag.person').length === count",
            arg=size,
        )
        written = {event.target for event in _events(project)[before:]}
        by_target = {row["target"]: row for row in everything}
        assert written == {
            target for answered in second_page for target in _targets_of(by_target[answered])
        }
        # None of the first page was written.
        assert not written & set(first_page)
    finally:
        page.close()


# ---------------------------------------------------------------------------
# What the box does to the page's accessibility.
# ---------------------------------------------------------------------------


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


@pytest.mark.parametrize("view", ["review", "classes", "extensions"])
def test_the_header_box_adds_no_serious_accessibility_violation(browser, served, view: str) -> None:
    axe = _axe_script()
    if axe is None:
        if REQUIRED:
            pytest.fail(f"{NO_AXE} (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
        pytest.skip(NO_AXE)
    _, base = served
    page = _opened(browser, base, view)
    try:
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.add_script_tag(content=axe.read_text(encoding="utf-8"))
            found = page.evaluate(
                "(options) => window.axe.run(document.querySelector('#list'), options)",
                {"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21aa"]}},
            )
            serious = [
                f"{violation['id']}: {[node['html'][:100] for node in violation['nodes'][:3]]}"
                for violation in found["violations"]
                if violation["impact"] in ("critical", "serious")
            ]
            assert not serious, f"{view} {scheme}: {serious}"
            # The box is read as a checkbox with its name by the checker's own rules.
            assert not [
                violation
                for violation in found["violations"]
                if any("select-all" in node["html"] for node in violation["nodes"])
            ]
    finally:
        page.close()
