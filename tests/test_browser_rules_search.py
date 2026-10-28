"""The Rules page finds a rule by what it says.

The page already holds every rule, so the search is made in the page and asks the server nothing.
The behaviour: a word typed must appear in the rule's name, its plain sentence, its statement or
its group's heading. A rule that does not answer is hidden, and a group with none left is hidden.
The count says how many of how many, and the groups that answer open. Clearing the box shows every
rule again, and a rule opened from another page is never hidden by an old search. Every word is
invented (``fixtures.theme_pool``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import launch_browser, need, serve_theme_project

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")
need("sklearn", "themes need the themes extra")

#: Every rule row with whether it is shown, by the rule's name. A row of a group that is closed
#: is in the page and not shown, so a search is read through what the groups open.
RULES = """() => [...document.querySelectorAll('#list tr.rules-row')].map((r) =>
  [r.dataset.rule, !r.hidden && !r.closest('[hidden]')
    && !(r.closest('details') && !r.closest('details').open)])"""
#: The pure function the page uses, on invented rules.
ANSWERS = """([text, rule, heading]) => ruleAnswers(ruleSearchWords(text), rule, heading)"""


@pytest.fixture(scope="module")
def address(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from serve_theme_project(tmp_path_factory, "rules_search")


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _shown(page: Any) -> list[str]:
    return [name for name, shown in page.evaluate(RULES) if shown]


def _open_every_group(page: Any) -> None:
    page.get_by_role("button", name="Expand all").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('.rules-group-head')]"
        ".every((h) => h.getAttribute('aria-expanded') === 'true')"
    )


def test_a_search_answers_by_name_sentence_statement_and_heading(browser, address) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{address}#rules")
        page.locator("#list tr.rules-row:visible").first.wait_for()
        _open_every_group(page)
        everything = _shown(page)
        assert len(everything) > 5
        box = page.get_by_label("Find a rule")
        # A rule's own name finds it, in any case.
        name = everything[0]
        box.fill(name.upper())
        assert name in _shown(page)
        assert len(_shown(page)) < len(everything)
        # Two words must both appear, in any order.
        row = page.evaluate(
            "() => { const r = document.querySelector('#list tr.rules-row td.rule-plain');"
            " return r.textContent; }"
        )
        words = [w for w in row.split() if len(w) > 3][:2]
        box.fill(" ".join(reversed(words)))
        assert _shown(page)
        # Nothing answers, and the page says so.
        box.fill("zzzz nothing says this")
        assert _shown(page) == []
        assert (
            "No rule matches your search." in page.locator("#list .rules-search-note").inner_text()
        )
        assert page.locator("#list .rules-group:visible").count() == 0
        # Clearing the box shows every rule again, with the groups' own counts.
        box.fill("")
        assert _shown(page) == everything
        counts = page.locator("#list .rules-group-counts").all_inner_texts()
        assert all(" of " not in count for count in counts)
    finally:
        page.close()


def test_the_count_says_how_many_of_how_many_and_the_groups_that_answer_open(
    browser, address
) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{address}#rules")
        page.locator("#list tr.rules-row:visible").first.wait_for()
        total = page.evaluate(
            "() => [...document.querySelectorAll('#list tr.rules-row')]"
            ".filter((r) => !r.closest('details.rules-inactive')).length"
        )
        assert page.locator(".rules-count").inner_text() == f"{total} of {total}"
        _open_every_group(page)
        name = _shown(page)[0]
        page.get_by_label("Find a rule").fill(name)
        kept = len(_shown(page))
        assert page.locator(".rules-count").inner_text() == f"{kept} of {total}"
        # Only the groups with a match are left, each saying how many of its rules answer.
        heads = page.locator("#list .rules-group:visible .rules-group-counts").all_inner_texts()
        assert heads
        assert all(" of " in head for head in heads)
        assert sum(int(head.split(" of ")[0]) for head in heads) == kept
        # A group closed before the search is opened by it.
        page.get_by_label("Find a rule").fill("")
        page.get_by_role("button", name="Collapse all").click()
        assert _shown(page) == []
        page.get_by_label("Find a rule").fill(name)
        assert name in _shown(page)
    finally:
        page.close()


def test_a_rule_opened_from_another_page_is_not_hidden_by_an_old_search(browser, address) -> None:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    try:
        page.goto(f"{address}#rules")
        page.locator("#list tr.rules-row:visible").first.wait_for()
        _open_every_group(page)
        name = _shown(page)[0]
        page.get_by_label("Find a rule").fill("zzzz nothing says this")
        assert _shown(page) == []
        page.evaluate("(name) => openRule(name)", name)
        page.locator(f'[id="rule-{name}"]').wait_for()
        assert name in _shown(page)
        assert page.get_by_label("Find a rule").input_value() == ""
        # Its card is open beside the table.
        page.locator(f'#detail .rule-card[data-rule="{name}"]').wait_for()
    finally:
        page.close()


@pytest.mark.parametrize(
    ("text", "answers"),
    [
        ("", True),
        ("  ", True),
        ("SAME_comp", True),
        ("composition ZED", False),
        ("sentence statement", True),
        ("heading", True),
        ("heading nowhere", False),
    ],
)
def test_the_rule_search_function_on_invented_rules(browser, address, text, answers) -> None:
    rule = {
        "rule_id": "same_composition",
        "plain": "Files with the same sentence sit together.",
        "statement": "Group files whose statement is equal.",
    }
    page = browser.new_page()
    try:
        page.goto(f"{address}#rules")
        page.locator("#list tr.rules-row:visible").first.wait_for()
        assert page.evaluate(ANSWERS, [text, rule, "A heading"]) is answers
    finally:
        page.close()
