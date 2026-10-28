"""Questions opens on conclusions, the few that matter, with the rest one click away.

Each behaviour is checked in a real browser. Skipped where the ``browser`` extra or a browser is
missing, and required where ``INGENCE_BROWSER_REQUIRED=1``. Each test serves a copy of an invented
project on a port between 20000 and 29999, with who decides kept in a folder of the test's own
(``INGENCE_CONFIG_HOME``).

Two projects, both invented: the standing project (``test_browser_standing_views``: three invented
people's experiments in six planted lines of work, packs to take, a word between two senses,
classes of conclusions, patterns of derivations) and the group pool
(``test_question_sheet``: a vocabulary pack and thirty groups of experiments, every one
answerable on this page). Every domain loads, so the standing project asks no pack and its planted
word is read by its folder's domain. The tied word's tests use a third, ``test_review_pages``'
pool under the shipped Rulebook, where a word two domains spell differently is tied and leads.

1. The tabs: seven items (Words first) where nothing waits on Extensions. The word "sheet"
   appears nowhere a person or the page's own markup can read it.
2. Conclusions first: the sentence comes before any table.
3. The leads table holds the route's leads, each reason verbatim, each linking to its address,
   each with its rule and version.
4. Every other question is one chip away, fifty rows a page, and the counts add up.
5. Choose three, press Correct: one request, three answers in magenta, the badge up by three.
   Escape clears.
6. One card for every kind, with relative examples. "Can't say" on a card is counted and decides
   nothing. A tied word's buttons are the route's ``senses``.
7. An answer stays with its row, and with its card, across a reload.
8. When every question is answered, the page says so under the sentence.
9. The keyboard: Space, Shift with the arrows, A, Enter and Escape.

Every name here is invented.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser, need
from test_browser_standing_views import _free_port, _serve, build_standing_project

sync_api = need("playwright.sync_api", "the browser extra is not installed")

PERSON = "Dana Whitfield"

#: The word a person reading the page must never meet on it.
SHEET = re.compile(r"sheet", re.IGNORECASE)


def _shots(page: Any, name: str) -> None:
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
def standing_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_standing_project(tmp_path_factory.mktemp("question_standing"))


@pytest.fixture(scope="module")
def groups_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from test_question_sheet import _build

    return Path(_build(tmp_path_factory.mktemp("question_groups")).path)


def _serving(built: Path, tmp_path: Path):
    project = tmp_path / "project"
    shutil.copytree(built, project)
    config = tmp_path / "config"
    config.mkdir()
    (config / "decider.json").write_text(json.dumps({"decider": PERSON, "set_at": "t"}))
    port = _free_port()
    process = _serve(project, port, config)
    url = f"http://127.0.0.1:{port}/"
    # What the page reads first is computed on the first ask: wait for it here, by its answer.
    for path in (
        "api/v1/questions/ranked?leads=true&answered=show&limit=1000",
        "api/v1/themes",
        "api/v1/themes/overlap",
        "api/v1/themes/stability",
        "api/waiting",
    ):
        with urllib.request.urlopen(url + path, timeout=900) as answer:
            answer.read()
    return process, project, url


def _stop(process: Any) -> None:
    process.send_signal(signal.SIGTERM)
    try:
        # A busy machine is slow to say goodbye: what is waited for is the process, not a number.
        process.wait(timeout=240)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=60)
    if process.stdout is not None:
        process.stdout.close()


@pytest.fixture
def standing(standing_built: Path, tmp_path: Path):
    process, project, url = _serving(standing_built, tmp_path)
    try:
        yield project, url
    finally:
        _stop(process)


@pytest.fixture
def groups(groups_built: Path, tmp_path: Path):
    process, project, url = _serving(groups_built, tmp_path)
    try:
        yield project, url
    finally:
        _stop(process)


def _get(url: str, path: str, **query: str) -> Any:
    suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
    with urllib.request.urlopen(f"{url}{path}{suffix}", timeout=300) as answer:
        return json.load(answer)


def _ranked(url: str, **query: str) -> dict[str, Any]:
    return dict(
        _get(url, "api/v1/questions/ranked", **{"answered": "show", "limit": "1000", **query})
    )


def _events(project: Path) -> list[Any]:
    from ingence.record.log import read_events

    return list(read_events(project))


def _opened(
    browser: Any, url: str, address: str = "#review", prepare: Any = None
) -> tuple[Any, list[str]]:
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    # A busy machine answers slowly: the page's state is waited for, not a number of seconds.
    page.set_default_timeout(180_000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if prepare is not None:
        prepare(page)
    page.goto(url + address)
    page.wait_for_function("() => (typeof me !== 'undefined') && me.actor !== ''")
    page.locator(".q-stands").wait_for(timeout=300_000)
    # The tabs' counts are read beside the page: wait until each tab has said its own.
    page.wait_for_function(
        "() => [...document.querySelectorAll('nav.sections button[data-waiting]')]"
        ".every((b) => { const c = b.querySelector('.count');"
        " return c && !c.textContent.includes('counting'); })",
        timeout=300_000,
    )
    return page, errors


def _badge(page: Any) -> int:
    text = page.evaluate(
        "() => { const b = document.getElementById('rerun-badge');"
        " return b && !b.hidden ? b.textContent : '0'; }"
    )
    return int(text)


def _person_color(page: Any) -> str:
    """The computed color of the person token, as the browser reads it."""
    return str(
        page.evaluate(
            "() => { const probe = document.createElement('span');"
            " probe.style.color = 'var(--person)'; document.body.append(probe);"
            " const color = getComputedStyle(probe).color; probe.remove(); return color; }"
        )
    )


def _lead_rows(page: Any) -> Any:
    return page.locator('[data-section="leads"] tbody tr.q-row')


def _row_for(page: Any, key: str) -> Any:
    return page.locator(f'tr.q-row[data-key="{key}"]')


# ---------------------------------------------------------------------------
# The tabs, and the conclusions first.
# ---------------------------------------------------------------------------


def test_the_tabs_are_seven_and_the_word_sheet_is_nowhere_on_the_page(browser, standing) -> None:
    _, url = standing
    page, errors = _opened(browser, url)
    waiting = _get(url, "api/waiting")
    tabs = page.locator('nav[data-sections-of="advanced"] button:visible').all_inner_texts()
    names = [re.sub(r"\s*\(.*\)$", "", tab).strip() for tab in tabs]

    # Questions' sections are Advanced's. A section that counts what waits on it shows only while
    # something does (the open one shows always).
    def shown(kind: str, words: str) -> list[str]:
        return [words] if waiting[kind] or kind == "questions" else []

    expected = ["Rules", "Dictionary", "Settings", "Folder levels", "Inventory", "Coverage"]
    expected += shown("classes", "Word groups") + shown("candidates", "Suggested pairs")
    expected += shown("extensions", "Extensions") + shown("programs", "Programs")
    expected += shown("claims", "Conclusion samples") + shown("questions", "Questions")
    expected += [
        "Words",
        "No analysis found",
        "Explorer",
        "Connections",
        "Map of groups",
        "Stability",
    ]
    assert names == expected
    if not waiting["extensions"]:
        # No extensions waiting: the Extensions tab is not there to be reached.
        assert page.locator(
            'nav[data-sections-of="advanced"] button[data-view="extensions"]'
        ).is_hidden()
    # The word appears nowhere in what a person reads, nor in any attribute the page's own
    # markup sets (a stylesheet link's own name is the browser's).
    assert not SHEET.search(page.inner_text("body"))
    holders = page.evaluate(
        """() => [...document.querySelectorAll('body *')].flatMap((node) =>
            [...node.attributes].filter((a) => !(node.tagName === 'LINK' && a.name === 'rel')
              && /sheet/i.test(a.value)).map((a) => node.tagName + '[' + a.name + ']'))"""
    )
    assert holders == []
    comments = page.evaluate(
        "() => { const found = [];"
        " const walker = document.createTreeWalker(document, NodeFilter.SHOW_COMMENT);"
        " while (walker.nextNode()) {"
        "   const text = walker.currentNode.nodeValue;"
        "   if (/sheet/i.test(text)) found.push(text.slice(0, 60)); }"
        " return found; }"
    )
    assert comments == []
    # An address the Dashboard used before lands on Questions.
    for old in ("#sheet", "#questions"):
        page.goto(url + old)
        page.locator(".q-stands").wait_for()
        assert page.evaluate("() => state.view") == "review"
    # The red dot stays while questions lead.
    assert page.locator('#pages button[data-page="advanced"] .dot').count() == 1
    assert errors == []
    page.close()


def test_the_page_opens_on_the_sentence_not_on_a_table_and_has_no_cards(browser, standing) -> None:
    # The four cards (lines of work, shared words, groupings, pairs) are not on the page: the
    # Overview and Overlap say those.
    _, url = standing
    body = _ranked(url, leads="true")
    summary = body["summary"]
    page, errors = _opened(browser, url)
    heading = page.locator(".q-stands").inner_text()
    remain = summary["questions"] - summary["answered"]
    lead = summary["leading"] - summary["leading_answered"]
    assert re.fullmatch(
        r"Scan \d+ stands as read\. [\d,]+ questions? remain(s)?; [\d,]+ of them leads?\.", heading
    )
    assert f"{remain} question" in heading and f"{lead} of them lead" in heading
    assert page.locator("#list .q-cards, #list .q-concl").count() == 0
    # The sentence comes before any table, by their places on the page.
    stands = page.locator(".q-stands").bounding_box()
    table = page.locator("#list table").first.bounding_box()
    assert stands is not None and table is not None
    assert stands["y"] < table["y"]
    _shots(page, "q38-conclusions-first")
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# The few that matter.
# ---------------------------------------------------------------------------


def test_the_leads_table_is_the_route_s_leads_with_each_reason_and_address(
    browser, standing
) -> None:
    _, url = standing
    body = _ranked(url, leads="true")
    leads = body["questions"]
    assert leads and all(question["leads"] for question in leads)
    page, errors = _opened(browser, url)
    rows = _lead_rows(page)
    assert rows.count() == min(len(leads), 50)
    shown = leads[:50]
    assert [rows.nth(i).get_attribute("data-key") for i in range(rows.count())] == [
        question["key"] for question in shown
    ]
    for index, question in enumerate(shown):
        row = rows.nth(index)
        # The reason is the route's, word for word.
        assert row.locator(".q-reason").inner_text() == question["reason"]
        # The link is the route's address, and the rule and version are named.
        assert row.locator("a.q-text").get_attribute("data-address") == question["address"]
        assert question["address"].startswith("?question=") or question["address"] == "#candidates"
        if question["rule_id"]:
            rule = row.locator(".q-rule").inner_text()
            assert question["rule_id"] in rule and f"v{question['rule_version']}" in rule
        # The answers its kind takes, in the route's words.
        if question["answers"]:
            assert row.locator("td.q-answer button.answer").all_inner_texts() == [
                answer["label"] for answer in question["answers"]
            ]
    head = page.locator("#q-lead-head").inner_text()
    assert (
        head == f"{body['summary']['leading'] - body['summary']['leading_answered']} lead: "
        "a rule asks them first, or a grouping waits on them"
    )
    # "How the ranking works" opens the tuple in the engine's words.
    page.get_by_role("button", name="how the ranking works").click()
    ranking = page.locator(".q-ranking")
    assert ranking.is_visible()
    assert ranking.locator("li").all_inner_texts() == body["summary"]["ranked_by"]
    assert "score" in ranking.inner_text()
    # A lead row opens its card, and the question named in the address opens it too.
    first = shown[0]
    rows.first.locator("a.q-text").click()
    page.locator(".q-card").wait_for()
    assert (
        page.locator(".q-card-title")
        .inner_text()
        .startswith(first["question"].rstrip("?").split(":")[0])
    )
    page.goto(url + first["address"])
    page.locator(".q-card").wait_for()
    assert page.locator(".q-card").get_attribute("data-key") == first["key"]
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# The rest, one click away.
# ---------------------------------------------------------------------------


def test_every_other_question_is_one_chip_away_fifty_a_page_and_the_counts_add_up(
    browser, standing
) -> None:
    _, url = standing
    whole = _ranked(url)
    leads = [q for q in whole["questions"] if q["leads"]]
    rest = [q for q in whole["questions"] if not q["leads"]]
    page, errors = _opened(browser, url)
    chips = page.locator("button.q-chip")
    titles = {
        t["title"]: t["questions"] - t["leading"]
        for t in whole["titles"]
        if t["questions"] > t["leading"]
    }
    shown = {
        chip.get_attribute("data-title"): int(chip.inner_text().rsplit("·", 1)[1].replace(",", ""))
        for chip in (chips.nth(i) for i in range(chips.count()))
    }
    assert shown == titles
    # Nothing is hidden, and the counts add up: the leads and every chip are the route's total.
    assert (
        len(leads) + sum(shown.values()) == whole["summary"]["questions"] == len(whole["questions"])
    )
    assert sum(shown.values()) == len(rest)
    # What sits in the other tabs is named in a last chip, from the route's own counts.
    elsewhere = page.locator(".q-elsewhere")
    said = " ".join(elsewhere.inner_text().split())
    for held in whole["summary"]["elsewhere"]:
        assert f"{held['label']} {held['waiting']:,}" in said
    # A chip opens its group as the same table, fifty rows a page, with its filters.
    biggest = max(titles, key=lambda title: titles[title])
    chip = page.locator(f'button.q-chip[data-title="{biggest}"]')
    chip.click()
    group = page.locator(f'.q-group[data-title="{biggest}"]')
    group.locator("tbody tr.q-row").first.wait_for()
    assert chip.get_attribute("aria-expanded") == "true"
    assert group.locator("tbody tr.q-row").count() == min(titles[biggest], 50)
    assert group.locator("thead input.select-all").count() == 1
    assert group.locator('select[aria-label="Top-level folder"]').count() == 1
    assert group.locator('select[aria-label="Rule"]').count() == 1
    expected = [q["key"] for q in rest if q["title"] == biggest]
    drawn = [
        group.locator("tbody tr.q-row").nth(i).get_attribute("data-key")
        for i in range(group.locator("tbody tr.q-row").count())
    ]
    assert drawn == expected[:50]
    if titles[biggest] > 50:
        pager = group.locator(".pager")
        assert f"rows 1 to 50 of {titles[biggest]:,}" in pager.inner_text()
        pager.get_by_role("button", name="next 50").click()
        group.locator(".pager").get_by_text("rows 51 to").wait_for()
        second = [
            group.locator("tbody tr.q-row").nth(i).get_attribute("data-key")
            for i in range(group.locator("tbody tr.q-row").count())
        ]
        assert second == expected[50:100]
    # A filter narrows the table to what the route says it keeps.
    options = group.locator('select[aria-label="Rule"] option').all_inner_texts()
    if len(options) > 2:
        rule = options[1].rsplit(" (", 1)[0]
        group.locator('select[aria-label="Rule"]').select_option(label=options[1])
        page.wait_for_function(
            "(rule) => [...document.querySelectorAll('.q-group:not([hidden]) td.q-rule')]"
            ".every((cell) => cell.innerText.includes(rule))",
            arg=rule,
        )
    chip.click()
    assert group.is_hidden()
    _shots(page, "q38-the-rest")
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# Bulk answering.
# ---------------------------------------------------------------------------


def _answer_requests(page: Any) -> list[Any]:
    sent: list[Any] = []
    page.on(
        "request",
        lambda request: (
            sent.append(request)
            if request.method == "POST" and request.url.endswith("/api/review/answer")
            else None
        ),
    )
    return sent


def test_choose_three_press_correct_and_three_rows_say_so_in_one_request(browser, groups) -> None:
    project, url = groups
    body = _ranked(url, leads="true")
    correct = [q for q in body["questions"] if any(a["word"] == "correct" for a in q["answers"])]
    assert len(correct) >= 4
    page, errors = _opened(browser, url)
    sent = _answer_requests(page)
    before = _badge(page)
    before_events = len(_events(project))
    # The header box: clear, then mixed when some rows are chosen.
    header = page.locator('[data-section="leads"] thead input.select-all')
    bar = page.locator('[data-section="leads"] .bulk-bar')
    assert bar.is_hidden() and not header.is_checked()
    chosen = correct[:3]
    for question in chosen:
        _row_for(page, question["key"]).locator("input[type=checkbox]").check()
    assert page.evaluate(
        "() => document.querySelector("
        "'[data-section=\"leads\"] thead input.select-all').indeterminate"
    )
    assert bar.is_visible()
    total = _lead_rows(page).count()
    assert bar.locator(".bulk-count").inner_text() == f"3 of {total} chosen"
    assert "answer them together:" in bar.inner_text()
    assert bar.locator(".bulk-buttons button").all_inner_texts() == [
        "Correct",
        "Wrong",
        "Can't say",
    ]
    assert "recorded under your name" in bar.inner_text() and "waits for Re-run" in bar.inner_text()
    bar.locator("input.bulk-note").fill("checked together")
    _shots(page, "q38-bulk-bar")
    bar.locator("button[data-word=correct]").click()
    for question in chosen:
        said = _row_for(page, question["key"]).locator(".q-said")
        said.wait_for()
        assert said.inner_text() == "Correct"
        assert said.evaluate("(node) => getComputedStyle(node).color") == _person_color(page)
    # One request wrote the three, and the bar closed with the choice cleared.
    assert len(sent) == 1
    sent_body = json.loads(sent[0].post_data)
    assert sent_body["targets"] == [q["target"] for q in chosen]
    assert (sent_body["word"], sent_body["note"]) == ("correct", "checked together")
    assert bar.is_hidden()
    assert not header.is_checked()
    # The Re-run badge counts three more, and the Record holds three events with one note.
    page.wait_for_function(
        "(before) => Number(document.getElementById('rerun-badge').textContent) >= before + 3",
        arg=before,
    )
    assert _badge(page) == before + 3
    events = _events(project)[before_events:]
    assert len(events) == 3
    assert {e.actor for e in events} == {PERSON}
    assert {json.loads(e.payload)["note"] for e in events} == {"checked together"}
    # The heading says three fewer remain.
    summary = body["summary"]
    assert (
        page.locator(".q-stands").inner_text().endswith(f"{summary['leading'] - 3} of them lead.")
    )
    assert errors == []
    page.close()


def _an_audit_leading(url: str) -> tuple[dict[str, Any], Any]:
    """A conclusion to check, and a step that makes it lead beside the route's own leads.

    No invented project here leads with two kinds of question: no pack questions are asked
    (every domain loads) and the review pool leads with its tied word alone. So the page is sent
    its leads with one of its own audits among them, the route's row as it is, and nothing is
    written."""
    audit = next(q for q in _ranked(url)["questions"] if q["kind"] == "audit")

    def prepare(page: Any) -> None:
        def handle(route: Any) -> None:
            response = route.fetch()
            body = response.json()
            asked = route.request.url
            held = {q["key"] for q in body["questions"]}
            if "leads=true" in asked and "key=" not in asked and audit["key"] not in held:
                body["questions"].append({**audit, "leads": True})
            route.fulfill(response=response, json=body)

        page.route("**/api/v1/questions/ranked?*", handle)

    return audit, prepare


def test_rows_of_different_kinds_share_only_cannot_say_and_escape_clears_the_choice(
    browser, tied
) -> None:
    _, url = tied
    body = _ranked(url, leads="true")
    word = next(q for q in body["questions"] if q["kind"] == "word" and q["senses"])
    other, prepare = _an_audit_leading(url)
    page, errors = _opened(browser, url, prepare=prepare)
    bar = page.locator('[data-section="leads"] .bulk-bar')
    _row_for(page, word["key"]).locator("input[type=checkbox]").check()
    # A tied word's own answers: its meanings, then Can't say.
    offered = bar.locator(".bulk-buttons button").all_inner_texts()
    assert len(offered) == len(word["answers"]) and offered[-1] == "Can't say", offered
    _row_for(page, other["key"]).locator("input[type=checkbox]").check()
    # Their kinds differ: only Can't say, and the note, are common to both.
    assert bar.locator(".bulk-buttons button").all_inner_texts() == ["Can't say"]
    assert bar.locator("input.bulk-note").is_visible()
    assert bar.locator(".bulk-count").inner_text().startswith("2 of ")
    # Escape clears the choice, and the boxes with it.
    page.keyboard.press("Escape")
    assert bar.is_hidden()
    assert page.locator('[data-section="leads"] tbody input[type=checkbox]:checked').count() == 0
    # The header box chooses every row shown, and clears them again.
    header = page.locator('[data-section="leads"] thead input.select-all')
    header.check()
    assert page.locator('[data-section="leads"] tbody input[type=checkbox]:checked').count() == min(
        len([q for q in [*body["questions"], other] if q["answers"]]), 50
    )
    header.uncheck()
    assert bar.is_hidden()
    assert errors == []
    page.close()


def test_cannot_say_on_several_rows_is_a_note_each_and_decides_nothing(browser, standing) -> None:
    project, url = standing
    body = _ranked(url, leads="true")
    # Two of the questions that lead.
    packs = [q for q in body["questions"] if q["answers"]][:2]
    assert len(packs) == 2
    page, errors = _opened(browser, url)
    sent = _answer_requests(page)
    before_events = len(_events(project))
    for pack in packs:
        _row_for(page, pack["key"]).locator("input[type=checkbox]").check()
    page.locator('[data-section="leads"] .bulk-bar input.bulk-note').fill("not sure yet")
    page.locator('[data-section="leads"] .bulk-bar button[data-word=cannot_say]').click()
    page.wait_for_function(
        "() => document.querySelector('.bulk-said').textContent.startsWith('Saved')"
    )
    assert len(sent) == 1
    events = _events(project)[before_events:]
    assert len(events) == 2
    for event in events:
        said = json.loads(event.payload)
        # On a question Can't say is an annotation that names itself, with the note as written. It
        # decides nothing.
        assert event.kind.value == "annotate"
        assert said["note"] == "not sure yet"
        assert said.get("verdict") in (None, "", "cannot_say")
    # Nothing was decided: on a question Can't say is counted as its answer and decides nothing (no
    # confirm or reject is written), and each row says Can't say.
    again = {q["key"]: q for q in _ranked(url)["questions"]}
    for pack in packs:
        answer = again[pack["key"]]["answer"] or {}
        assert again[pack["key"]]["answered"] and answer.get("word") == "cannot_say", answer
    assert {event.kind.value for event in events} == {"annotate"}
    for pack in packs:
        said = _row_for(page, pack["key"]).locator('.q-said[data-said="cannot_say"]')
        said.wait_for()
        assert said.inner_text() == "Can't say"
    assert errors == []
    page.close()


@pytest.fixture(scope="module")
def review_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One of every kind of row: questions, groupings, conclusion samples, pairs, extensions and an
    installed program (``test_review_pages``' invented pool)."""
    from fixtures.one_folder_pairs import allow_pairs_in_one_folder
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from test_review_pages import _pool

    base = tmp_path_factory.mktemp("question_review")
    project = Project.create(base / "project", created_at="t", roots=[_pool(base)])
    write_revision(project.path)
    allow_pairs_in_one_folder(project.path)
    run_census(
        project, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z"
    )
    run_inference(
        project, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    return Path(project.path)


@pytest.fixture(scope="module")
def tied_built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``test_review_pages``' pool under the shipped Rulebook, where a word two domains spell
    differently is tied and leads. The standing pool's planted word is read by its folder's
    domain, so it is never tied."""
    from test_review_pages import _project

    return Path(_project(tmp_path_factory.mktemp("question_tied")).path)


@pytest.fixture
def tied(tied_built: Path, tmp_path: Path):
    process, project, url = _serving(tied_built, tmp_path)
    try:
        yield project, url
    finally:
        _stop(process)


@pytest.fixture
def review(review_built: Path, tmp_path: Path):
    process, project, url = _serving(review_built, tmp_path)
    try:
        yield project, url
    finally:
        _stop(process)


@pytest.mark.parametrize("view", ["classes", "claims", "candidates", "extensions", "programs"])
def test_every_other_table_of_questions_takes_the_header_box_and_the_bar(
    browser, review, view: str
) -> None:
    """Groupings, Conclusion samples, Proposed pairs, Extensions and Programs: the header box, and
    a bar offering the answers the chosen rows have in common, with "Can't say" last."""
    _, url = review
    if view == "programs":
        rows = _get(url, "api/programs", answered="show")["programs"]
        rows = [row for row in rows if row.get("target")]
    else:
        rows = _get(url, f"api/pages/{view}", limit="50", answered="show")["rows"]
    assert rows
    shared = [word["word"] for word in rows[0]["words"] if word["word"] != "cannot_say"]
    for row in rows:
        shared = [w for w in shared if any(o["word"] == w for o in row["words"])]
    labels = {word["word"]: word["label"] for word in rows[0]["words"]}
    expected = [labels[word] for word in shared] + ["Can't say"]
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"{url}#{view}")
        page.locator("#list tbody tr.row, #list .program.row").first.wait_for(timeout=300_000)
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor !== ''")
        header = page.locator("#list input.select-all")
        assert header.count() == 1
        bar = page.locator("#list .bulk-bar")
        assert bar.is_hidden()
        header.check()
        assert bar.is_visible()
        assert bar.locator(".bulk-buttons button").all_inner_texts() == expected
        assert bar.locator("input.bulk-note").is_visible()
        assert "recorded under your name · waits for Re-run" in bar.inner_text()
        page.keyboard.press("Escape")
        assert bar.is_hidden() and not header.is_checked()
        assert errors == []
    finally:
        page.close()


# ---------------------------------------------------------------------------
# One card for every kind.
# ---------------------------------------------------------------------------


def test_a_tied_word_s_card_takes_its_buttons_from_senses_and_its_examples_are_relative(
    browser, tied
) -> None:
    project, url = tied
    whole = _ranked(url)["questions"]
    tied = next(q for q in whole if q["kind"] == "word" and q["senses"])
    page, errors = _opened(
        browser, url, f"?question={urllib.parse.quote(tied['key'], safe='')}#review"
    )
    card = page.locator(".q-card")
    card.wait_for()
    assert card.get_attribute("data-key") == tied["key"]
    # The question, with its meanings named from the route's senses; the kind and rule version.
    title = card.locator(".q-card-title").inner_text()
    terms = [s["term"].replace("_", " ") for s in tied["senses"]]
    assert title.endswith(
        (", ".join(terms[:-1]) + " or " + terms[-1] if len(terms) > 2 else " or ".join(terms)) + "?"
    )
    assert card.locator(".q-kind").inner_text() == f"word · v{tied['rule_version']}"
    # One sentence of what answering settles, and where it applies.
    sentence = card.locator(".q-card-sentence").inner_text()
    assert sentence.startswith(f"Answering settles {tied['settles_n']} files")
    assert "applies to this root" in sentence
    # Three examples at most, relative to the root, in mono, with the count and the caption.
    examples = card.locator(".q-examples code").all_inner_texts()
    assert examples == tied["examples"][:3] and 1 <= len(examples) <= 3
    assert all(
        not example.startswith(("/", "~")) and "/Users" not in example for example in examples
    )
    caption = card.locator(".q-small", has_text="relative to the data folder").inner_text()
    assert caption.startswith(
        f"{['none', 'one', 'two', 'three'][len(examples)]} of {tied['examples_of']}"
    )
    # The answers: the route's senses (term, then kind in mono), then Can't say.
    buttons = card.locator("button.answer")
    texts = buttons.all_inner_texts()
    assert [t.split("\n")[0] for t in texts] == [
        s["term"].replace("_", " ") for s in tied["senses"]
    ] + ["Can't say"]
    assert [buttons.nth(i).get_attribute("data-word") for i in range(buttons.count())] == [
        a["word"] for a in tied["answers"]
    ]
    assert (
        card.locator("input.q-note").get_attribute("placeholder")
        == "a note saved with the answer (optional)"
    )
    # "Why this is asked" names the rule and its version.
    card.locator(".q-why summary").click()
    assert (
        tied["rule_id"].replace("_", " ") in card.locator(".q-why").inner_text()
        or tied["rule_id"] in card.locator(".q-why").inner_text()
    )
    assert f"v{tied['rule_version']}" in card.locator(".q-why").inner_text()
    # "Where the word stands" opens the Ask view on the word (Ask keeps the question in
    # ``askState``).
    card.get_by_role("button", name=re.compile("stands")).click()
    page.wait_for_function("() => state.view === 'ask'")
    assert page.evaluate("() => askState.query") == tied["value"]
    assert page.evaluate("() => new URLSearchParams(state.hashQuery).get('q')") == tied["value"]
    # Answering writes the sense on the question, with the note, once; the card shows the answer.
    page.goto(url + tied["address"])
    card = page.locator(".q-card")
    card.wait_for()
    before_events = len(_events(project))
    card.locator("input.q-note").fill("it is the imaging technique")
    first = card.locator("button.answer").first
    sense = first.get_attribute("data-word")
    first.click()
    answered = page.locator(".q-answered")
    answered.wait_for()
    assert "Answered:" in answered.inner_text() and "you" in answered.inner_text()
    assert "Waits for Re-run" in answered.inner_text()
    assert card.locator("button.answer").count() == 0
    (event,) = _events(project)[before_events:]
    said = json.loads(event.payload)
    assert event.actor == PERSON and sense in event.payload
    assert said["note"] == "it is the imaging technique"
    assert errors == []
    page.close()


def test_can_t_say_on_a_card_is_counted_and_decides_nothing(browser, tied) -> None:
    """No pack is asked, because every domain loads, so the card is the tied word's. Can't say
    is its answer, with the note: an annotation that decides nothing and waits for no Re-run."""
    from ingence.workspace.state import changes_since

    project, url = tied
    word = next(q for q in _ranked(url)["questions"] if q["kind"] == "word" and q["senses"])
    page, errors = _opened(
        browser, url, f"?question={urllib.parse.quote(word['key'], safe='')}#review"
    )
    card = page.locator(".q-card")
    card.wait_for()
    # The card says what Can't say does, under the buttons.
    assert "Can't say is saved as a note and decides nothing." in card.inner_text()
    before_events = len(_events(project))
    card.locator("input.q-note").fill("ask the person who set up the scope")
    card.locator("button.answer", has_text="Can't say").click()
    page.locator(".q-answered").wait_for()
    assert "Can't say" in page.locator(".q-answered").inner_text()
    events = _events(project)[before_events:]
    assert [event.kind.value for event in events] == ["annotate"]
    said = json.loads(events[0].payload)
    assert said["note"] == "ask the person who set up the scope"
    # Nothing is decided: no confirm or reject is written, and nothing waits for Re-run.
    assert not [e for e in _events(project) if e.kind.value in ("confirm", "reject")]
    assert changes_since(project) == []
    assert not [q for q in _ranked(url)["questions"] if q["kind"] == "pack"]
    assert errors == []
    page.close()


def test_every_card_shows_at_most_three_relative_examples_with_the_count(browser, standing) -> None:
    _, url = standing
    whole = _ranked(url)["questions"]
    page, errors = _opened(browser, url)
    kinds_seen: set[str] = set()
    for question in whole:
        if question["kind"] in kinds_seen and len(kinds_seen) < 5:
            continue
        kinds_seen.add(question["kind"])
        page.goto(
            url + question["address"] if question["address"].startswith("?") else url + "#review"
        )
        if not question["address"].startswith("?"):
            continue
        card = page.locator(".q-card")
        card.wait_for()
        assert card.get_attribute("data-key") == question["key"]
        shown = card.locator(".q-examples code").all_inner_texts()
        assert len(shown) <= 3
        assert all(not text.startswith(("/", "~")) and "/Users" not in text for text in shown)
        if shown:
            assert f"of {question['examples_of']:,}" in card.inner_text()
        assert card.locator(".q-kind").count() == 1
        assert card.locator(".q-why").count() == 1
    # No pack is asked, and this pool's word is read by its folder's domain, so the kinds are the
    # group questions and the conclusions to check (the tied word's card is
    # test_a_tied_word_s_card_takes_its_buttons_from_senses_and_its_examples_are_relative's).
    assert {"question", "audit"} <= kinds_seen
    assert "pack" not in kinds_seen
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# An answered row stays with its answer.
# ---------------------------------------------------------------------------


def test_an_answered_row_keeps_its_place_with_its_answer_across_a_reload(browser, groups) -> None:
    project, url = groups
    body = _ranked(url, leads="true")
    target = next(q for q in body["questions"] if any(a["word"] == "correct" for a in q["answers"]))
    order = [q["key"] for q in body["questions"]]
    page, errors = _opened(browser, url)
    before_events = len(_events(project))
    row = _row_for(page, target["key"])
    row.locator("button[data-word=wrong]").click()
    row.locator(".q-said").wait_for()
    assert row.locator(".q-said").inner_text() == "Wrong"
    # In place: the rows keep their order, and the heading counts one fewer.
    assert [_lead_rows(page).nth(i).get_attribute("data-key") for i in range(len(order))] == order
    # Reload: the row is still there with its answer, and its card says it waits for Re-run.
    page.reload()
    page.locator(".q-stands").wait_for()
    row = _row_for(page, target["key"])
    assert row.locator(".q-said").inner_text() == "Wrong"
    assert [_lead_rows(page).nth(i).get_attribute("data-key") for i in range(len(order))] == order
    row.locator("a.q-text").click()
    answered = page.locator(".q-answered")
    answered.wait_for()
    assert re.search(
        r"Answered: Wrong · you, \d{1,2} [A-Z][a-z]{2} \d{1,2}:\d{2} (AM|PM)", answered.inner_text()
    )
    assert (
        "The row stays here with its answer until the next scan reads it." in answered.inner_text()
    )
    assert page.locator(".q-card").evaluate(
        "(node) => getComputedStyle(node.querySelector('.q-answered')).borderTopColor"
    ) == _person_color(page)
    # Change: the buttons come back, and a new answer replaces the one shown.
    answered.get_by_role("button", name="Change").click()
    page.locator(".q-card button.answer").first.wait_for()
    page.locator(".q-card button.answer", has_text="Correct").click()
    page.locator(".q-answered", has_text="Answered: Correct").wait_for()
    assert row.locator(".q-said").inner_text() == "Correct"
    events = _events(project)[before_events:]
    assert [json.loads(e.payload)["verdict"] for e in events] == ["wrong", "correct"]
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# Nothing left.
# ---------------------------------------------------------------------------


def test_with_every_question_answered_the_page_says_so_under_the_sentence(browser, groups) -> None:
    _, url = groups
    whole = _ranked(url)["questions"]
    assert all(q["answers"] for q in whole), "the group pool asks nothing answered pair by pair"
    # Answer every question through the one route, as the page's own batches do.
    decisions = [
        {"target": q["target"], "word": next(a["word"] for a in q["answers"] if a["word"])}
        for q in whole
    ]
    request = urllib.request.Request(
        url + "api/review/answer",
        method="POST",
        data=json.dumps({"actor": PERSON, "decisions": decisions}).encode(),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    with urllib.request.urlopen(request, timeout=300) as answer:
        assert answer.status == 201
    page, errors = _opened(browser, url)
    state = page.locator("#q-nothing .state-card")
    state.wait_for()
    assert (
        state.locator(".state-sentence").inner_text()
        == "Every question is answered or waits for Re-run."
    )
    assert state.get_attribute("data-state") == "empty"
    page.wait_for_function("() => !document.getElementById('rerun-badge').hidden")
    count = _badge(page)
    assert count >= 1
    assert f"{count} changes wait for Re-run." in state.inner_text()
    # It stands under the sentence and above the leads, whose rows are still there with answers.
    stands = page.locator(".q-stands").bounding_box()
    nothing = state.bounding_box()
    leads = page.locator('[data-section="leads"]').bounding_box()
    assert stands is not None and nothing is not None and leads is not None
    assert stands["y"] < nothing["y"] < leads["y"]
    assert page.locator(".q-stands").inner_text().startswith("Scan ")
    assert "0 questions remain; 0 of them lead." in page.locator(".q-stands").inner_text()
    assert _lead_rows(page).count() >= 1
    assert page.locator('[data-section="leads"] .q-said').count() == _lead_rows(page).count()
    _shots(page, "q38-nothing-left")
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# The keyboard.
# ---------------------------------------------------------------------------


def test_the_questions_keyboard_space_shift_arrows_a_enter_and_escape(browser, groups) -> None:
    _, url = groups
    body = _ranked(url, leads="true")
    keys = [q["key"] for q in body["questions"] if q["answers"]][:4]
    page, errors = _opened(browser, url)
    bar = page.locator('[data-section="leads"] .bulk-bar')
    first = _row_for(page, keys[0])
    first.focus()
    # Space toggles the focused row's box.
    page.keyboard.press("Space")
    assert first.locator("input[type=checkbox]").is_checked()
    page.keyboard.press("Space")
    assert not first.locator("input[type=checkbox]").is_checked()
    # Shift with the arrows extends the choice to the rows passed over.
    page.keyboard.press("Shift+ArrowDown")
    page.keyboard.press("Shift+ArrowDown")
    assert bar.locator(".bulk-count").inner_text().startswith("3 of ")
    chosen = page.locator('[data-section="leads"] tbody input[type=checkbox]:checked').count()
    assert chosen == 3
    # A puts the focus on the focused row's first answer button, Escape clears the choice.
    focused = page.evaluate("() => document.activeElement.dataset.key")
    assert focused == keys[2]
    page.keyboard.press("a")
    assert page.evaluate(
        "() => document.activeElement.tagName + '/' + document.activeElement.className"
    ).startswith("BUTTON/answer")
    assert page.evaluate("() => document.activeElement.closest('tr').dataset.key") == keys[2]
    page.keyboard.press("Escape")
    assert bar.is_hidden()
    # Enter opens the row's card.
    _row_for(page, keys[1]).focus()
    page.keyboard.press("Enter")
    page.locator(".q-card").wait_for()
    assert page.locator(".q-card").get_attribute("data-key") == keys[1]
    assert errors == []
    page.close()


# ---------------------------------------------------------------------------
# More cases: every table presses, a note reads alike after a reload, an answer leaves once the
# run has read it, and the page fits a 1280-wide window.
# ---------------------------------------------------------------------------


def _rows_of_view(url: str, view: str) -> list[dict[str, Any]]:
    if view == "programs":
        found = _get(url, "api/programs", answered="show")["programs"]
        return [row for row in found if row.get("target")]
    return list(_get(url, f"api/pages/{view}", limit="50", answered="show")["rows"])


def _answered_tags(page: Any, view: str) -> Any:
    place = (
        "#list .program-answer .tag.person"
        if view == "programs"
        else "#list tbody tr.row .tag.person"
    )
    return page.locator(place)


@pytest.mark.parametrize("view", ["classes", "claims", "candidates", "programs"])
def test_the_bar_answers_every_chosen_row_of_each_other_table_in_one_request(
    browser, review, view: str
) -> None:
    project, url = review
    rows = _rows_of_view(url, view)
    if view == "claims":
        rows = [row for row in rows if not row.get("grouped_by")]
    assert rows
    targets = sorted(
        {t for row in rows for t in [row["target"], *(o["target"] for o in row.get("also") or [])]}
    )
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(300_000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sent = _answer_requests(page)
    try:
        page.goto(f"{url}#{view}")
        page.locator("#list tbody tr.row, #list .program.row").first.wait_for()
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor !== ''")
        before = len(_events(project))
        page.locator("#list input.select-all").check()
        bar = page.locator("#list .bulk-bar")
        words = bar.locator(".bulk-buttons button").all_inner_texts()
        assert words[-1] == "Can't say" and len(words) >= 2, words
        stored = bar.locator(".bulk-buttons button").first.get_attribute("data-word")
        bar.locator("input.bulk-note").fill("pressed for all")
        bar.locator(".bulk-buttons button").first.click()
        page.wait_for_function(
            "(count) => document.querySelectorAll("
            "'#list tbody tr.row .tag.person, #list .program-answer .tag.person').length === count",
            arg=len(rows),
        )
        assert len(sent) == 1, "the whole table is one request"
        written = _events(project)[before:]
        assert sorted(event.target for event in written) == targets
        assert {json.loads(event.payload).get("note") for event in written} == {"pressed for all"}
        assert {json.loads(event.payload)["verdict"] for event in written} == {stored}
        assert bar.is_hidden()
        assert errors == []
    finally:
        page.close()


def test_cannot_say_chosen_for_many_rows_reads_the_same_before_and_after_a_reload(
    browser, review
) -> None:
    _, url = review
    rows = [row for row in _rows_of_view(url, "claims") if not row.get("grouped_by")]
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(300_000)
    try:
        page.goto(f"{url}#claims")
        page.locator("#list tbody tr.row").first.wait_for()
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor !== ''")
        page.locator("#list input.select-all").check()
        page.locator("#list .bulk-bar button[data-word=cannot_say]").click()
        tags = _answered_tags(page, "claims")
        page.wait_for_function(
            "(count) => document.querySelectorAll("
            "'#list tbody tr.row .tag.person').length === count",
            arg=len(rows),
        )
        at_once = sorted(tags.all_inner_texts())
        assert set(at_once) == {"Can't say"}
        page.reload()
        page.locator("#list tbody tr.row .tag.person").first.wait_for()
        assert sorted(_answered_tags(page, "claims").all_inner_texts()) == at_once
    finally:
        page.close()


def test_an_answered_row_is_gone_once_the_run_has_read_its_answer(browser, tied) -> None:
    from ingence.core.project import Project
    from ingence.infer.run import run_inference

    project, url = tied
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.set_default_timeout(300_000)
    try:
        # A conclusion sample answered with its row's own button, and a tied word on the Questions
        # page.
        page.goto(f"{url}#claims")
        page.locator("#list tbody tr.row").first.wait_for()
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor !== ''")
        claim = page.locator("#list tbody tr.row").first.get_attribute("data-target")
        page.locator(f'#list tr.row[data-target="{claim}"] input[type=checkbox]').check()
        page.locator("#list .bulk-bar button[data-word=correct]").click()
        page.locator(f'#list tr.row[data-target="{claim}"] .tag.person').wait_for()
        pack = next(q for q in _ranked(url, leads="true")["questions"] if q["kind"] == "word")
        sense = pack["answers"][0]["word"]
        page.goto(f"{url}#review")
        page.locator(f'tr.q-row[data-key="{pack["key"]}"]').wait_for()
        page.locator(f'tr.q-row[data-key="{pack["key"]}"] button[data-word="{sense}"]').click()
        page.locator(f'tr.q-row[data-key="{pack["key"]}"] .q-said').wait_for()
        # While the answers wait for Re-run, a reload keeps both rows with their answers.
        page.reload()
        page.locator(f'tr.q-row[data-key="{pack["key"]}"] .q-said').wait_for()
        page.goto(f"{url}#claims")
        page.locator(f'#list tr.row[data-target="{claim}"] .tag.person').wait_for()
        # The rules run again and read them: neither row is on its page any more.
        run_inference(
            Project.open(project),
            run_id="run_20260101T000003Z_333333333333",
            started_at="2026-01-01T00:00:03Z",
        )
        page.reload()
        page.wait_for_function("() => (typeof me !== 'undefined') && me.actor !== ''")
        page.locator("#list").wait_for()
        page.wait_for_function(
            "(target) => !document.querySelector(`#list tr.row[data-target='${target}']`)"
            " && !document.querySelector('#list .state-loading')",
            arg=claim,
        )
        page.goto(f"{url}#review")
        page.locator(".q-stands").wait_for()
        assert page.locator(f'tr.q-row[data-key="{pack["key"]}"]').count() == 0
    finally:
        page.close()


def test_at_1280_a_rows_answers_sit_side_by_side(browser, standing) -> None:
    _, url = standing
    body = _ranked(url, leads="true")
    three = next(q for q in body["questions"] if len(q["answers"]) == 3)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(300_000)
    try:
        page.goto(url + "#review")
        page.locator(".q-stands").wait_for()
        row = _row_for(page, three["key"])
        row.scroll_into_view_if_needed()
        buttons = row.locator("td.q-answer button.answer")
        heights = [buttons.nth(i).bounding_box()["y"] for i in range(buttons.count())]
        assert len(heights) == 3 and max(heights) - min(heights) < 2, heights
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        page.close()
