"""The Words section of Questions driven in a real browser.

Words is where a person teaches the engine their own words, and the first line of questions. Each
test runs on an invented project, in a real browser. Skipped where the ``browser`` extra or a
browser is missing (a failure where ``INGENCE_BROWSER_REQUIRED=1``). Every test serves its own copy
of the project on a free port with who decides kept in a folder of the test's own, so what one
answers never reaches another.

The project is invented: three words no Dictionary holds, written in nine, eight and seven file
names (``zorbex``, ``qwuxel``, ``brevik``), each with one near spelling in a few names of its own
(``zorbec``, ``qwuxal``, ``brevic``), and a spelling a capital divides (``zibKelt``). The three
leading words are therefore the same on every run, whatever else the engine finds.

1. Words is a section of Advanced. The list is in files order with a bar against the first row's,
   and the meter says the engine's own counts.
2. Three words answered by keyboard (``1``, a meaning; ``S``, ``K`` and ``U`` on a spelling;
   ``Enter``): the request is the one the card describes, the list advances, focus stays in the
   card, the page keys do not switch tabs, and the Re-run badge counts what was written.
3. Show answered brings the answers back, with who gave them and when.
4. Withdraw asks first. An answer no run has read is cancelled and nothing waits. A page that is
   out of date is told so and reads the list again.
5. Re-run applies the answers and the meter's understood figure rises.
6. A glued spelling's card asks only how it reads.
7. The page digits stand aside inside the card and switch tabs outside it.
8. The Questions section's block of words, and Coverage's link to Words.
9. axe finds nothing serious on Words, in light and in dark.

Every name here is invented.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    REPOSITORY,
    REQUIRED,
    launch_browser,
    need,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

PERSON = "Dana Whitfield"
#: The words the invented project is built around, in the order the list must show them.
HEADS = ("zorbex", "qwuxel", "brevik")
#: The spelling each head has beside it, and the files each is written in.
NEAR = {"zorbex": "zorbec", "qwuxel": "qwuxal", "brevik": "brevic"}
FILES = {"zorbex": 9, "qwuxel": 8, "brevik": 7}

#: What axe rates failing, as the repository's accessibility lane has it.
STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
FAILING = ("critical", "serious")


@dataclass
class Served:
    project: Path
    url: str


# ------------------------------------------------------------------------------------ the fixtures


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The invented project: a census and an inference over the pool described above."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("words")
    pool = base / "plates"

    def put(folder: str, pattern: str, count: int, start: int = 1) -> None:
        (pool / folder).mkdir(parents=True, exist_ok=True)
        for index in range(start, start + count):
            (pool / folder / pattern.format(index)).write_text("a,b\n1,2\n")

    put("run1", "plate_zorbex_{}.csv", 6)
    put("run2", "plate_zorbex_{}.csv", 3, 7)
    put("run1", "plate_zorbec_{}.csv", 1)
    put("run2", "plate_zorbec_{}.csv", 1, 2)
    put("run1", "image_qwuxel_{}.tif", 5)
    put("run2", "image_qwuxel_{}.tif", 3, 6)
    put("run2", "image_qwuxal_{}.tif", 1)
    put("run3", "data_brevik_{}.txt", 7)
    put("run3", "data_brevic_{}.txt", 1, 8)
    put("run3", "image_zibKelt_{}.png", 2)
    # Two words a shipped domain section reads: every section loads, so they are understood.
    put("run3", "confocal_hela_{}.csv", 3)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return Path(project.path)


@pytest.fixture
def served(built: Path, tmp_path: Path) -> Iterator[Served]:
    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config-home"
    config.mkdir()
    (config / "decider.json").write_text(json.dumps({"decider": PERSON, "set_at": "t"}))
    process, address = start_dashboard(project, config)
    try:
        yield Served(project, address)
    finally:
        stop(process)


def _get(served: Served, path: str) -> Any:
    with urllib.request.urlopen(f"{served.url}{path}", timeout=300) as answer:
        return json.load(answer)


def _post(served: Served, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{served.url}api/review/answer", method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Ingence-Write", "1")
    try:
        with urllib.request.urlopen(request, timeout=300) as answer:
            return int(answer.status), json.load(answer)
    except urllib.error.HTTPError as error:
        try:
            return int(error.code), json.load(error)
        finally:
            error.close()


def _queue(served: Served, query: str = "") -> dict[str, Any]:
    suffix = f"?{query}" if query else ""
    found: dict[str, Any] = _get(served, f"api/v1/words/queue{suffix}")
    return found


def _answer(
    served: Served, word: str, meaning: str, kind: str = "reagent", actor: str = PERSON
) -> None:
    """Answer a word as the page would, for a test that starts from an answered word."""
    status, said = _post(
        served,
        {
            "actor": actor,
            "decisions": [
                {"target": f"term:{word}", "answer": meaning, "theme_kind": kind},
            ],
        },
    )
    assert status == 201, said
    assert said["refused"] == []


def _events(served: Served) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(served.project, held=False, withdrawn=True, moved=False))


def _page(browser: Any, served: Served, address: str, scheme: str = "light") -> Any:
    """A page on ``address``, the decider read, and what the address names drawn."""
    context = browser.new_context(viewport={"width": 1400, "height": 1000}, color_scheme=scheme)
    page = context.new_page()
    page.goto(f"{served.url}{address}")
    page.wait_for_function(
        "(who) => typeof me !== 'undefined' && me.actor === who", arg=PERSON, timeout=HANG_GUARD_MS
    )
    return page


def _words_page(browser: Any, served: Served, scheme: str = "light") -> Any:
    page = _page(browser, served, "#words", scheme)
    page.wait_for_selector("#detail .words-card h2 mark", timeout=HANG_GUARD_MS)
    settled(page)
    return page


def _open_word(page: Any) -> str:
    return str(page.locator("#detail .words-card h2 mark").inner_text())


def _wait_for_another_word(page: Any, was: str) -> None:
    page.wait_for_function(
        "(was) => { const mark = document.querySelector('#detail .words-card h2 mark');"
        " return Boolean(mark) && mark.textContent !== was; }",
        arg=was,
        timeout=HANG_GUARD_MS,
    )


def _recorded_requests(page: Any) -> list[dict[str, Any]]:
    """The bodies of the writes the page sends to the answer route, kept as they are sent."""
    sent: list[dict[str, Any]] = []

    def keep(request: Any) -> None:
        if request.method == "POST" and request.url.endswith("/api/review/answer"):
            sent.append(json.loads(request.post_data))

    page.on("request", keep)
    return sent


def _badge(page: Any) -> str:
    return str(page.evaluate("document.getElementById('rerun-badge').textContent.trim()"))


def _meter(page: Any) -> str:
    return str(page.locator(".words-meter-line").inner_text())


def _files_of(page: Any) -> list[tuple[str, int]]:
    rows = page.locator("tr.words-row")
    return [
        (
            str(rows.nth(at).get_attribute("data-word")),
            int(rows.nth(at).locator(".words-count").inner_text().replace(",", "")),
        )
        for at in range(rows.count())
    ]


# ----------------------------------------------------- 1. first, in files order, with a meter


def test_words_is_a_section_of_advanced_and_the_list_is_in_files_order(
    browser: Any, served: Served
) -> None:
    # Questions' sections are in Advanced. Words follows the full queue of questions.
    page = _page(browser, served, "#inventory")
    page.locator('nav.sections button[data-view="words"]').click()
    page.wait_for_function("location.hash === '#words'", timeout=HANG_GUARD_MS)
    page.wait_for_selector("#list tr.words-row", timeout=HANG_GUARD_MS)
    sections = page.eval_on_selector_all(
        'nav.sections[data-sections-of="advanced"] button[data-view]',
        "nodes => nodes.map((node) => node.dataset.view)",
    )
    assert sections.index("words") == sections.index("review") + 1, sections
    assert (
        page.evaluate("document.querySelector('nav.sections button.active').dataset.view")
        == "words"
    )
    queue = _queue(served)
    listed = _files_of(page)
    # The same words in the same order as the engine serves them, the most files first.
    assert [word for word, _ in listed] == [row["word"] for row in queue["rows"]]
    assert [word for word, _ in listed][:3] == list(HEADS)
    states = page.eval_on_selector_all(
        "tr.words-row", "nodes => nodes.map((node) => node.dataset.state)"
    )
    counts = [count for _, count in listed]
    # Files order, the most files first, then the word: one order for every state.
    assert listed == sorted(listed, key=lambda row: (-row[1], row[0]))
    assert counts[:3] == [FILES[word] for word in HEADS]
    # One dictionary: no row waits on a word list, and a word a domain section reads is understood.
    assert set(states) <= set(queue["by_state"]) and "pack" not in states
    assert not {"confocal", "hela"} & {word for word, _ in listed}
    # Each bar is drawn against the first row's files.
    widths = page.eval_on_selector_all(
        "tr.words-row .words-bar-fill", "nodes => nodes.map((node) => node.style.width)"
    )
    assert widths[0] == "100%"
    assert widths[1] == f"{round(100 * FILES['qwuxel'] / FILES['zorbex'])}%"
    assert widths[2] == f"{round(100 * FILES['brevik'] / FILES['zorbex'])}%"
    # The chips narrow by state and carry the engine's counts.
    chips = page.eval_on_selector_all(
        ".words-chip", "nodes => nodes.map((node) => node.textContent.trim())"
    )
    assert f"all · {sum(queue['by_state'].values())}" in chips
    for state, number in queue["by_state"].items():
        assert any(chip.endswith(f"· {number}") for chip in chips), (state, chips)
    page.context.close()


def test_the_meter_says_the_engines_counts_and_follows_the_open_word(
    browser: Any, served: Served
) -> None:
    queue = _queue(served)
    words = queue["reading"]["words"]
    read, understood = words["read"], words["understood"]
    page = _words_page(browser, served)
    first, second = queue["rows"][0], queue["rows"][1]
    assert _meter(page) == (
        f"{understood:,} of {read:,} word parts understood now. "
        f"If every word down to this one were answered: {understood + first['covered']:,} "
        f"of {read:,}."
    )
    page.locator(f'tr.words-row[data-word="{second["word"]}"]').click()
    page.wait_for_function(
        "(word) => { const mark = document.querySelector('#detail .words-card h2 mark');"
        " return Boolean(mark) && mark.textContent === word; }",
        arg=second["word"],
        timeout=HANG_GUARD_MS,
    )
    assert _meter(page).endswith(f"{understood + second['covered']:,} of {read:,}.")
    assert second["covered"] == first["covered"] + second["unknown_parts"]
    page.context.close()


# --------------------------------------------------------------- 2. three words by the keyboard


def test_three_words_answered_by_keyboard_advance_the_list_and_keep_focus_in_the_card(
    browser: Any, served: Served
) -> None:
    from ingence.workspace.state import pending_rerun

    queue = {row["word"]: row for row in _queue(served)["rows"]}
    unrelated = queue["brevik"]["variants"][0]["distinct_target"]
    page = _words_page(browser, served)
    sent = _recorded_requests(page)
    assert _open_word(page) == "zorbex"

    def answer(meaning: str, key: str, expected_next: str) -> None:
        word = _open_word(page)
        page.locator("#detail .words-card").focus()
        # ``1`` chooses "Something else": the page does not switch tabs.
        page.keyboard.press("1")
        assert page.evaluate("location.hash") == "#words"
        assert page.locator(".words-option.on").get_attribute("data-choice") == "own"
        page.locator(".define-term").fill(meaning)
        page.locator(".define-kind").select_option("reagent")
        # The spelling beside the word takes the answer's key.
        page.locator(".words-variant").first.focus()
        page.keyboard.press(key)
        page.wait_for_selector(".words-variant-answer.on", timeout=HANG_GUARD_MS)
        page.keyboard.press("Enter")
        _wait_for_another_word(page, word)
        assert _open_word(page) == expected_next
        # The list advanced: the answered word has left it, and focus is still in the card.
        assert page.locator(f'tr.words-row[data-word="{word}"]').count() == 0
        assert page.evaluate(
            "Boolean(document.activeElement && document.activeElement.closest('.words-card'))"
        )
        assert "Recorded" in page.locator("[data-advance=recorded]").inner_text()

    order = list(queue)
    after_brevik = [w for w in order[order.index("brevik") + 1 :] if w not in ("zorbec", "qwuxal")]
    answer("zorbex stain", "s", "qwuxel")
    answer("qwuxel stain", "k", "brevik")
    answer("brevik stain", "u", after_brevik[0])
    kind = {"theme_kind": "reagent"}
    assert sent == [
        {
            "actor": PERSON,
            "decisions": [
                {"target": "term:zorbex", "answer": "zorbex stain", **kind},
                {"target": "term:zorbec", "answer": "zorbex stain", **kind},
            ],
        },
        {
            "actor": PERSON,
            "decisions": [
                {"target": "term:qwuxel", "answer": "qwuxel stain", **kind},
                {"target": "term:qwuxal", "answer": "qwuxal", **kind},
            ],
        },
        {
            "actor": PERSON,
            "decisions": [
                {"target": "term:brevik", "answer": "brevik stain", **kind},
                {"target": unrelated, "word": "unrelated"},
            ],
        },
    ]
    targets = {event.target for event in _events(served)}
    assert targets == {
        "term:zorbex",
        "term:zorbec",
        "term:qwuxel",
        "term:qwuxal",
        "term:brevik",
        unrelated,
    }
    # The Re-run badge counts what was written (a pair called unrelated is read by no run).
    waiting = pending_rerun(served.project)
    assert waiting is not None
    page.wait_for_function(
        "(count) => { const badge = document.getElementById('rerun-badge');"
        " return !badge.hidden && badge.textContent.trim() === String(count); }",
        arg=waiting.count,
        timeout=HANG_GUARD_MS,
    )
    assert waiting.count == 5
    # The answered words and the spellings answered with them have left the list.
    left = {row["word"] for row in _queue(served)["rows"]}
    assert not left & {"zorbex", "zorbec", "qwuxel", "qwuxal", "brevik"}
    assert {word for word, _ in _files_of(page)} == left
    page.context.close()


def test_the_page_digits_switch_tabs_outside_the_card_and_choose_a_meaning_inside_it(
    browser: Any, served: Served
) -> None:
    page = _words_page(browser, served)
    page.locator("#detail .words-card").focus()
    page.keyboard.press("2")
    # Inside the card the digit chooses the second meaning ("Not a person's word"), nothing more.
    assert page.evaluate("location.hash") == "#words"
    assert page.locator(".words-option.on").get_attribute("data-choice") == "not_a_word"
    assert page.evaluate("document.querySelector('#pages button.active').dataset.page") == (
        "advanced"
    )
    # From a row of the list, outside the card, the page keys are the page's own.
    page.locator("tr.words-row").first.focus()
    page.keyboard.press("1")
    page.wait_for_function(
        "document.querySelector('#pages button.active').dataset.page === 'overview'",
        timeout=HANG_GUARD_MS,
    )
    page.context.close()


# ---------------------------------------------------------- 3. show answered, who gave it and when


def test_show_answered_brings_the_answers_back_with_who_gave_them_and_when(
    browser: Any, served: Served
) -> None:
    _answer(served, "zorbex", "zorbex stain")
    _answer(served, "qwuxel", "qwuxel stain", actor="Sam Okafor")
    page = _words_page(browser, served)
    assert {word for word, _ in _files_of(page)}.isdisjoint({"zorbex", "qwuxel"})
    page.get_by_label("Show answered").check()
    page.wait_for_selector("tr.words-row .words-answer", timeout=HANG_GUARD_MS)
    row = page.locator('tr.words-row[data-word="zorbex"]')
    row.wait_for(timeout=HANG_GUARD_MS)
    said = row.locator(".words-answer .tag.person")
    assert said.inner_text() == "means zorbex stain (reagent)"
    # Who gave it ("you" for the person at the page) and when: the day and the clock.
    when = r"\d{1,2} [A-Z][a-z]{2} \d{1,2}:\d{2} [AP]M"
    assert re.search(rf"· you, {when}$", said.get_attribute("title") or "")
    other = page.locator('tr.words-row[data-word="qwuxel"] .words-answer .tag.person')
    assert re.search(rf"· Sam Okafor, {when}$", other.get_attribute("title") or "")
    assert "waits for Re-run" in row.locator(".words-answer").inner_text()
    row.click()
    page.wait_for_selector(".words-answered", timeout=HANG_GUARD_MS)
    box = page.locator(".words-answered").inner_text()
    assert box.startswith("Answered: means zorbex stain (reagent)")
    assert "you," in box and "Waits for Re-run." in box
    # Show answered off again: the answered words leave.
    page.get_by_label("Show answered").uncheck()
    page.wait_for_function(
        "document.querySelector('tr.words-row[data-word=\"zorbex\"]') === null",
        timeout=HANG_GUARD_MS,
    )
    page.context.close()


# ------------------------------------------------------------------------------ 4. withdraw one


def _open_answered(page: Any, word: str) -> None:
    page.get_by_label("Show answered").check()
    page.locator(f'tr.words-row[data-word="{word}"] .words-withdraw').wait_for(
        timeout=HANG_GUARD_MS
    )
    page.locator(f'tr.words-row[data-word="{word}"]').click()
    page.wait_for_selector(".words-answered .words-withdraw", timeout=HANG_GUARD_MS)


def test_withdrawing_an_answer_no_run_has_read_asks_first_and_cancels_it(
    browser: Any, served: Served
) -> None:
    from ingence.core.schema import RecordEventKind
    from ingence.workspace.state import pending_rerun

    _answer(served, "zorbex", "zorbex stain")
    page = _words_page(browser, served)
    sent = _recorded_requests(page)
    _open_answered(page, "zorbex")
    page.locator(".words-answered .words-withdraw").click()
    page.wait_for_selector("dialog[open]", timeout=HANG_GUARD_MS)
    text = page.locator("dialog[open]").inner_text()
    assert 'withdraw the answer about "zorbex"?' in text.lower()
    assert "means zorbex stain (reagent)" in text
    assert (
        "No scan has read this answer, so withdrawing cancels it. Nothing waits for Re-run." in text
    )
    # Keeping it writes nothing.
    page.locator("dialog[open] button", has_text="Keep it").click()
    page.wait_for_selector("dialog[open]", state="detached", timeout=HANG_GUARD_MS)
    assert sent == []
    assert [event.kind for event in _events(served)] == [RecordEventKind.DEFINE]
    # Withdrawing does.
    page.locator(".words-answered .words-withdraw").click()
    page.wait_for_selector("dialog[open]", timeout=HANG_GUARD_MS)
    page.locator("dialog[open] button", has_text="Withdraw").click()
    page.wait_for_selector("[data-advance=recorded]", timeout=HANG_GUARD_MS)
    flash = page.locator("[data-advance=recorded]").inner_text()
    assert flash.startswith(f'Withdrawn by {PERSON}: the answer about "zorbex".')
    assert "Nothing waits for Re-run" in flash
    assert len(sent) == 1 and set(sent[0]) == {"actor", "withdraw"}
    kinds = [event.kind for event in _events(served)]
    assert kinds == [RecordEventKind.DEFINE, RecordEventKind.WITHDRAW]
    # The word is back with no answer, and nothing waits: the badge is gone.
    assert _open_word(page) == "zorbex"
    assert page.locator(".words-answered").count() == 0
    assert page.locator('tr.words-row[data-word="zorbex"] .words-answer').count() == 0
    assert pending_rerun(served.project) is None
    page.wait_for_function("document.getElementById('rerun-badge').hidden", timeout=HANG_GUARD_MS)
    page.get_by_label("Show answered").uncheck()
    page.wait_for_selector('tr.words-row[data-word="zorbex"]', timeout=HANG_GUARD_MS)
    page.context.close()


def test_withdrawing_on_a_page_that_is_out_of_date_says_so_and_reads_the_list_again(
    browser: Any, served: Served
) -> None:
    _answer(served, "zorbex", "zorbex stain")
    page = _words_page(browser, served)
    _open_answered(page, "zorbex")
    # Behind the page's back the same answer is withdrawn.
    held = _queue(served, "answered=only")
    answer = next(row for row in held["rows"] if row["word"] == "zorbex")["answered"]
    status, said = _post(
        served,
        {
            "actor": PERSON,
            "withdraw": {"sequence": answer["sequence"], "digest": answer["digest"]},
        },
    )
    assert status == 201, said
    page.locator(".words-answered .words-withdraw").click()
    page.wait_for_selector("dialog[open]", timeout=HANG_GUARD_MS)
    page.locator("dialog[open] button", has_text="Withdraw").click()
    page.wait_for_function(
        "document.querySelector('dialog[open]')"
        " && document.querySelector('dialog[open]').textContent.includes('could not withdraw')",
        timeout=HANG_GUARD_MS,
    )
    assert "already withdrawn" in page.locator("dialog[open]").inner_text()
    page.locator("dialog[open] button").first.click()
    # The list is read again: the word has no answer in it now.
    page.wait_for_function(
        "document.querySelector('tr.words-row[data-word=\"zorbex\"] .words-answer') === null"
        " && document.querySelector('tr.words-row[data-word=\"zorbex\"]') !== null",
        timeout=HANG_GUARD_MS,
    )
    page.context.close()


# ------------------------------------------------------------ 5. Re-run applies; the meter rises


def test_re_run_applies_the_answers_and_the_understood_figure_rises(
    browser: Any, served: Served
) -> None:
    before = _queue(served)
    understood = before["reading"]["words"]["understood"]
    occurrences = {row["word"]: row["occurrences"] for row in before["rows"]}
    _answer(served, "zorbex", "zorbex stain")
    _answer(served, "qwuxel", "qwuxel stain")
    page = _words_page(browser, served)
    assert _meter(page).startswith(f"{understood:,} of ")
    page.wait_for_function(
        "!document.getElementById('rerun-button').disabled", timeout=HANG_GUARD_MS
    )
    page.locator("#rerun-button").click()
    page.wait_for_selector("#applying.done", timeout=HANG_GUARD_MS)
    expected = understood + occurrences["zorbex"] + occurrences["qwuxel"]
    # The page reads the queue again by itself when the run ends: nothing here is touched.
    page.wait_for_function(
        "(figure) => document.querySelector('.words-meter-line')"
        " && document.querySelector('.words-meter-line').textContent.startsWith(figure)",
        arg=f"{expected:,} of ",
        timeout=HANG_GUARD_MS,
    )
    after = _queue(served)
    assert after["reading"]["words"]["understood"] == expected
    assert expected > understood
    # The answered words are understood now, so the engine is no longer unsure of them.
    assert {word for word, _ in _files_of(page)}.isdisjoint({"zorbex", "qwuxel"})
    assert _badge(page) == ""
    page.context.close()


# ------------------------------------------------------------------------------ 6. glued spelling


def test_a_glued_spellings_card_asks_only_how_it_reads(browser: Any, served: Served) -> None:
    page = _words_page(browser, served)
    sent = _recorded_requests(page)
    page.locator('.words-chip[data-state="glued"]').click()
    page.wait_for_selector('tr.words-row[data-state="glued"]', timeout=HANG_GUARD_MS)
    page.locator('tr.words-row[data-state="glued"]').click()
    page.wait_for_selector(".words-glued", timeout=HANG_GUARD_MS)
    assert page.locator("#detail .words-card h2").inner_text() == "Is zibkelt one word?"
    # No meaning is offered (the names hold its pieces), and the pieces are not asked again.
    assert page.locator('.words-options[aria-label="What it means"]').count() == 0
    assert (
        page.locator(".words-glued-note")
        .inner_text()
        .startswith(
            'A capital letter divides "zibkelt", so the names hold its pieces, zib and kelt'
        )
    )
    assert page.locator(".words-variant").count() == 0
    buttons = page.locator(".words-split strong").all_inner_texts()
    assert buttons == ["One word: zibKelt", "Separate words: zib, kelt"]
    page.locator(".words-split").first.click()
    assert page.locator(".words-split.on").count() == 1
    page.locator(".words-record").click()
    page.wait_for_function(
        "document.querySelector('[data-advance]') !== null", timeout=HANG_GUARD_MS
    )
    assert sent == [{"actor": PERSON, "decisions": [{"target": "split:zibkelt", "word": "whole"}]}]
    event = next(e for e in _events(served) if e.target == "split:zibkelt")
    assert json.loads(event.payload)["reading"] == "whole"
    page.context.close()


# ------------------------------------------------- 8. the Questions block, and Coverage's link


def test_the_questions_section_leads_with_a_block_of_the_words_and_a_link_into_words(
    browser: Any, served: Served
) -> None:
    queue = _queue(served, "limit=5")
    page = _page(browser, served, "#review")
    page.wait_for_selector(".q-words", timeout=HANG_GUARD_MS)
    shown = queue["rows"][:5]
    covered = shown[-1]["covered"]
    assert page.locator(".q-words h3").inner_text() == (
        f"{queue['total']} words the engine is unsure of. The first {len(shown)} words cover "
        f"{covered} of {queue['unknown_parts']} word parts it could not read."
    )
    assert page.locator(".words-q-list li").count() == len(shown)
    assert [item.get_attribute("data-word") for item in page.locator(".words-q-list li").all()] == [
        row["word"] for row in shown
    ]
    # The block comes before the questions that matter.
    order = page.evaluate(
        "[...document.querySelectorAll('.q-page [data-section]')]"
        ".map((node) => node.dataset.section)"
    )
    assert order.index("words") < order.index("leads")
    page.locator(".q-words a").click()
    page.wait_for_function("location.hash === '#words'", timeout=HANG_GUARD_MS)
    page.wait_for_selector("#detail .words-card", timeout=HANG_GUARD_MS)
    page.context.close()


def test_coverage_links_to_words_and_no_longer_lists_the_words(
    browser: Any, served: Served
) -> None:
    page = _page(browser, served, "#coverage")
    page.wait_for_selector('#detail a[href="#words"]', timeout=HANG_GUARD_MS)
    assert page.locator("#detail button", has_text="ask about it").count() == 0
    assert "Words that match nothing, by files" not in page.locator("#detail").inner_text()
    # Coverage keeps what it is about: the budget, the extensions and the clocks.
    text = (page.locator("#list").inner_text() + page.locator("#detail").inner_text()).lower()
    assert "what the time limit reached" in text and "why a file has no clock" in text
    page.locator('#detail a[href="#words"]').click()
    page.wait_for_function("location.hash === '#words'", timeout=HANG_GUARD_MS)
    page.wait_for_selector("#detail .words-card", timeout=HANG_GUARD_MS)
    page.context.close()


# ------------------------------------------------------------------------------------- 9. axe


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _violations(page: Any, where: str) -> list[str]:
    script = _axe_script()
    if script is None:
        message = "axe-core is not installed: set INGENCE_AXE_JS"
        if REQUIRED:
            pytest.fail(f"{message} (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
        pytest.skip(message)
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.add_script_tag(content=script.read_text(encoding="utf-8"))
    found = page.evaluate(
        "options => window.axe.run(document, options)",
        {"runOnly": {"type": "tag", "values": STANDARDS}},
    )
    said = []
    for violation in found["violations"]:
        if violation["impact"] not in FAILING:
            continue
        nodes = [
            f"{'/'.join(map(str, node['target']))}: {node['html'][:90]}"
            for node in violation["nodes"][:3]
        ]
        said.append(
            f"{where}: {violation['impact']} {violation['id']} on {len(violation['nodes'])} "
            f"element(s): {violation['help']}; first: {json.dumps(nodes)}"
        )
    return said


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_nothing_serious_on_words(browser: Any, served: Served, scheme: str) -> None:
    _answer(served, "brevik", "brevik stain")
    page = _words_page(browser, served, scheme)
    problems = _violations(page, f"{scheme} words, a word's card")
    # The states a person reaches by acting: answered words shown, a meaning chosen, a spelling
    # answered, a glued spelling's card, and the card with its folder choice open.
    page.get_by_label("Show answered").check()
    page.wait_for_selector("tr.words-row .words-answer", timeout=HANG_GUARD_MS)
    page.locator('tr.words-row[data-word="brevik"]').click()
    page.wait_for_selector(".words-answered .words-withdraw", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} words, an answered word")
    page.get_by_label("Show answered").uncheck()
    page.wait_for_selector('tr.words-row[data-word="zorbex"]', timeout=HANG_GUARD_MS)
    page.locator('tr.words-row[data-word="zorbex"]').click()
    page.wait_for_selector(".words-variant", timeout=HANG_GUARD_MS)
    page.locator(".words-option[data-number='1']").click()
    page.locator(".words-variant").first.focus()
    page.keyboard.press("s")
    page.wait_for_selector(".words-variant-answer.on", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} words, a meaning chosen and a spelling answered")
    page.locator('.words-chip[data-state="glued"]').click()
    page.locator('tr.words-row[data-state="glued"]').click()
    page.wait_for_selector(".words-glued", timeout=HANG_GUARD_MS)
    problems += _violations(page, f"{scheme} words, a glued spelling")
    page.context.close()
    assert problems == [], "\n".join(problems)
