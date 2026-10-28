"""Engine > Rules as a table by group with a card, the Dictionary aligned with it, and Copy.

The tests run in a real browser, on an invented project (HeLa and DAPI names in dated folders;
Dana Whitfield teaches one word; one rule is switched off in the project's own rules). Skipped
where the ``browser`` extra or a browser is missing, and a failure where
``INGENCE_BROWSER_REQUIRED`` is set. Tests wait on the page's state, never on a fixed time. Who
decides is kept in a folder of the test's own.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    REPOSITORY,
    REQUIRED,
    launch_browser,
    need,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: An invented person's name, and the word that person teaches.
TEACHER = "Dana Whitfield"
SPELLING = "HeLaG"
TERM = "HeLaS3"

#: The rule the project switches off, chosen below from the shipped ones.
SETTINGS_RULE = "earliest_credible_time"


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _switched_off_rule() -> str:
    """A shipped rule with no settings, in force and not provisional: the one turned off."""
    from ingence.rulebook import shipped_rulebook

    for rule in shipped_rulebook().rules:
        if (
            not rule.parameters
            and rule.status.value != "provisional"
            and rule.effect.value == "classifies"
        ):
            return str(rule.rule_id)
    pytest.skip("every shipped rule has settings")


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str, Path]]:
    from ingence.api.routes import dictionary as dictionary_routes
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog
    from ingence.storage.layout import RULEBOOK_DIR

    base = tmp_path_factory.mktemp("browser_rules_table")
    pool = base / "pool"
    for folder in ("2024-03-05 HeLa DAPI", "2024-03-06 HeLa DAPI", "2024-03-07 HEK293 GFP actin"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2):
            (pool / folder / f"{folder.split()[1]}_{index:02d}.nd2").write_text("x")
            (pool / folder / f"results_{index:02d}.csv").write_text("a\n1\n")
    made = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(made, run_id="run_20260101T000001Z_111111111111", started_at="2026-01-01T00:00:01Z")
    run_inference(
        made, run_id="run_20260101T000002Z_222222222222", started_at="2026-01-01T00:00:02Z"
    )
    with open_catalog(made.path) as catalog:
        dictionary_routes.write(
            catalog,
            made.path,
            {
                "actor": TEACHER,
                "dictionary": "add",
                "spelling": SPELLING,
                "term": TERM,
                "theme_kind": "reagent",
            },
            occurred_at="2026-01-02T00:00:00Z",
        )
    folder = made.path / RULEBOOK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project-0001.yaml").write_text(
        "\n".join(
            [
                "project_revision: 1",
                "summary: Switched off by a person.",
                "changes:",
                f"  - disable: {_switched_off_rule()}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(made.path, config_home)
    try:
        yield address, address.rstrip("/"), made.path
    finally:
        stop(process)


def _page(browser: Any, address: str, hash_: str, *, width: int = 1400) -> Any:
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=address.rstrip("/"))
    page = context.new_page()
    page.goto(f"{address}#{hash_}")
    return page


def _close(page: Any) -> None:
    context = page.context
    page.close()
    context.close()


def _get(address: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{address}{path.lstrip('/')}", timeout=30) as answer:
        return json.loads(answer.read())


PERSON_COLOR = """() => {
  const probe = document.createElement('span');
  probe.style.color = 'var(--person)';
  document.body.append(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}"""

#: The visible rows of the Rules table, by the rule's name.
VISIBLE_ROWS = """() => [...document.querySelectorAll('#list tr.rules-row')]
  .filter((r) => r.getClientRects().length > 0 && !r.closest('details.rules-inactive'))
  .map((r) => r.dataset.rule)"""


def _rules_ready(page: Any) -> None:
    page.locator("#list tr.rules-row:visible").first.wait_for()


# ----------------------------------------------------------------------------------------------
# Rules as a table collapsible by group, with a card
# ----------------------------------------------------------------------------------------------


def test_a_group_folds_under_its_header_and_opens_again(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        data = _get(address, "api/rules")
        heading = next(
            section["heading"] for section in data["sections"] if section["effect"] == "classifies"
        )
        group = page.locator(".rules-group", has=page.get_by_role("button", name=heading))
        head = group.locator(".rules-group-head")
        page.get_by_role("button", name="Expand all").click()
        assert head.get_attribute("aria-expanded") == "true"
        rows = group.locator("tr.rules-row")
        assert rows.count() > 1
        assert rows.first.is_visible()
        # Collapse it: one header is left, its rows are gone.
        head.click()
        assert head.get_attribute("aria-expanded") == "false"
        assert group.locator("tr.rules-row:visible").count() == 0
        assert head.is_visible()
        head.click()
        assert group.locator("tr.rules-row:visible").count() == rows.count()
        # The header says the group's rules and what they concluded in this run.
        said = group.locator(".rules-group-counts").inner_text()
        assert said.startswith(f"{rows.count()} rules")
    finally:
        _close(page)


def test_a_group_s_counts_are_the_sums_of_its_rows_served_counts(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        data = _get(address, "api/rules")
        by_effect = {section["effect"]: section for section in data["sections"]}
        for effect in ("classifies", "flags"):
            section = by_effect.get(effect)
            if section is None:
                continue
            claims = sum(rule["claims"] for rule in section["rules"])
            findings = sum(rule["findings"] for rule in section["rules"])
            # A switched-off rule of the group still holds the counts the run gave it.
            claims += sum(
                rule.get("claims", 0)
                for rule in data["inactive"]
                if rule.get("effect") == effect and rule.get("off_kind") != "replaced"
            )
            said = page.locator(
                f'.rules-group[data-group="{effect}"] .rules-group-counts'
            ).inner_text()
            if claims:
                assert f"{claims:,} result" in said
            if findings:
                assert f"{findings:,} flag" in said
        # The title counts the rules and the groups, and how many ran.
        title = page.locator(".rules-title").inner_text()
        assert " rules in " in title and " groups" in title and "applied in Scan 2" in title
    finally:
        _close(page)


def test_search_keeps_the_groups_with_a_match_and_says_k_of_n(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        total = page.evaluate(
            "() => [...document.querySelectorAll('#list tr.rules-row')]"
            ".filter((r) => !r.closest('details.rules-inactive')).length"
        )
        assert page.locator(".rules-count").inner_text() == f"{total} of {total}"
        page.get_by_label("Find a rule").fill("time")
        shown = page.evaluate(VISIBLE_ROWS)
        assert shown
        assert page.locator(".rules-count").inner_text() == f"{len(shown)} of {total}"
        assert len(shown) < total
        # Only the groups holding a match have a header; each says how many of its rules answer.
        groups = page.locator("#list .rules-group:visible")
        assert 0 < groups.count() < page.locator("#list .rules-group").count()
        for header in groups.locator(".rules-group-counts").all_inner_texts():
            assert " of " in header
        # Every shown row says "time" in its name, sentence, statement or its group's heading.
        assert "earliest_credible_time" in shown
    finally:
        _close(page)


def test_the_card_of_a_rule_shows_its_version_and_its_settings(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        page.get_by_label("Find a rule").fill(SETTINGS_RULE)
        page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
        card = page.locator(f'#detail .rule-card[data-rule="{SETTINGS_RULE}"]')
        card.wait_for()
        rule = next(
            each
            for section in _get(address, "api/rules")["sections"]
            for each in section["rules"]
            if each["rule_id"] == SETTINGS_RULE
        )
        identity = card.locator(".card-id").inner_text()
        assert identity == f"{SETTINGS_RULE} · version {rule['version']} · in force"
        assert card.locator(".eyebrow").first.inner_text().lower() == "rule"
        assert card.locator(".rule-card-sentence").inner_text() == rule["plain"]
        for label in ("Decides", "Reads", "This scan"):
            assert card.locator("dt", has_text=label).count() == 1
        settings = card.locator(".rule-setting")
        assert settings.count() == len(rule["parameters"]) > 0
        for parameter in rule["parameters"]:
            assert card.locator(f'.rule-setting[data-setting="{parameter["name"]}"]').count() == 1
        for chip in ("Copy as a revision", "Switch off…", "Its results"):
            assert card.get_by_role("button", name=chip).count() == 1
        assert "The next scan reads it." in card.inner_text()
        # A long setting name wraps inside its column and never runs under its field.
        overlaps = card.evaluate(
            "(node) => [...node.querySelectorAll('.rule-setting')].filter((row) => {"
            " const name = [...row.querySelectorAll('.rule-setting-name code')]"
            ".map((code) => code.getBoundingClientRect().right);"
            " const field = row.querySelector('.rule-setting-value').getBoundingClientRect().left;"
            " return Math.max(...name) > field + 1; }).map((row) => row.dataset.setting)"
        )
        assert overlaps == []
    finally:
        _close(page)


def test_a_rule_a_person_switched_off_shows_off_in_magenta(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        named = _switched_off_rule()
        page.get_by_role("button", name="Expand all").click()
        row = page.locator(f'tr.rules-row[data-rule="{named}"]')
        row.wait_for()
        force = row.locator("td.rules-force")
        assert force.inner_text() == "off"
        person = page.evaluate(PERSON_COLOR)
        assert force.evaluate("(node) => getComputedStyle(node).color") == person
        # A rule in force is "on", in the ink, never magenta.
        on = page.locator("tr.rules-row:not(.off) td.rules-force").first
        assert on.inner_text() == "on"
        assert on.evaluate("(node) => getComputedStyle(node).color") != person
        # Its card says it is switched off, and the run shown still used it.
        row.click()
        card = page.locator(f'#detail .rule-card[data-rule="{named}"]')
        card.wait_for()
        assert card.locator(".card-id").inner_text().endswith("switched off")
        assert card.get_by_role("button", name="Switch off…").is_disabled()
    finally:
        _close(page)


def test_a_setting_typed_on_the_card_is_a_draft_that_waits_for_rerun(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        page.get_by_label("Find a rule").fill(SETTINGS_RULE)
        page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
        field = page.get_by_label(f"earliest_credible_year of {SETTINGS_RULE}")
        field.wait_for()
        original = field.input_value()
        field.fill("1995")
        page.locator(
            '.rule-setting[data-setting="earliest_credible_year"] .draft-note',
            has_text="draft, saved",
        ).wait_for()
        # Nothing ran: the draft is a draft, and the run shown still used the old value.
        shown = next(
            parameter
            for section in _get(address, "api/rules")["sections"]
            for each in section["rules"]
            if each["rule_id"] == SETTINGS_RULE
            for parameter in each["parameters"]
            if parameter["name"] == "earliest_credible_year"
        )
        assert shown["drafted"] is True and shown["draft"] == 1995
        assert str(shown["value"]) == original
        # Choosing another rule and coming back keeps what was typed.
        other = page.locator("tr.rules-row:visible").nth(1)
        other.click()
        page.locator(f'#detail .rule-card:not([data-rule="{SETTINGS_RULE}"])').wait_for()
        page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
        assert (
            page.get_by_label(f"earliest_credible_year of {SETTINGS_RULE}").input_value() == "1995"
        )
        # Put back, the draft goes away.
        page.get_by_label(f"earliest_credible_year of {SETTINGS_RULE}").fill(original)
        page.wait_for_function(
            "() => !document.querySelector('.rule-setting .draft-error')"
            " && document.querySelector('[data-setting=\"earliest_credible_year\"] input').value"
            f" === '{original}'"
        )
    finally:
        _close(page)


# ----------------------------------------------------------------------------------------------
# The Rules keyboard
# ----------------------------------------------------------------------------------------------


def test_enter_folds_a_header_and_opens_a_row_s_card_and_c_copies_the_revision(
    browser, served
) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        head = page.locator(".rules-group-head").first
        head.focus()
        expanded = head.get_attribute("aria-expanded")
        page.keyboard.press("Enter")
        assert head.get_attribute("aria-expanded") != expanded
        page.keyboard.press("Enter")
        assert head.get_attribute("aria-expanded") == expanded
        # Enter on a row opens its card.
        rows = page.locator("tr.rules-row:visible")
        target = rows.nth(1)
        name = target.get_attribute("data-rule")
        target.focus()
        page.keyboard.press("Enter")
        page.locator(f'#detail .rule-card[data-rule="{name}"]').wait_for()
        # C copies the card's revision text, as the button does.
        page.keyboard.press("c")
        page.wait_for_function(
            "document.querySelector('[data-copy=revision]').textContent === 'Copied'"
        )
        copied = _get(address, f"api/v1/rules/copy?rule={name}")
        assert page.evaluate("navigator.clipboard.readText()") == copied["revision"]
        # C does nothing while a field has focus.
        page.get_by_label("Find a rule").focus()
        page.keyboard.type("c")
        assert page.get_by_label("Find a rule").input_value() == "c"
    finally:
        _close(page)


# ----------------------------------------------------------------------------------------------
# Copy
# ----------------------------------------------------------------------------------------------


def _size(node: Any) -> tuple[float, float]:
    """The width and height of a node (where it stands in the window moves as the page scrolls)."""
    box = node.bounding_box()
    return (round(box["width"], 1), round(box["height"], 1))


def _watch_the_label(page: Any, kind: str, said: str) -> None:
    page.wait_for_function(
        "([kind, said]) => document.querySelector(`[data-copy=${kind}]`).textContent === said",
        arg=[kind, said],
    )


def test_copy_as_a_revision_writes_the_generator_s_text_and_says_copied_for_two_seconds(
    browser, served
) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        page.get_by_label("Find a rule").fill(SETTINGS_RULE)
        page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
        button = page.locator('[data-copy="revision"]')
        button.wait_for()
        before = _size(page.locator("#detail .rule-card"))
        width = _size(button)[0]
        button.click()
        _watch_the_label(page, "revision", "Copied")
        # Nothing else moves: the card and the button keep their size while it says Copied.
        assert _size(page.locator("#detail .rule-card")) == before
        # Within half a pixel: the text face's subpixel widths round differently by label.
        assert _size(button)[0] >= width - 0.5
        copied = _get(address, f"api/v1/rules/copy?rule={SETTINGS_RULE}")
        assert copied["values"] and copied["revision"] == copied["values"]
        assert page.evaluate("navigator.clipboard.readText()") == copied["revision"]
        # It says what it did before again, after two seconds (waited for by its own state).
        _watch_the_label(page, "revision", "Copy as a revision")
        assert _size(page.locator("#detail .rule-card")) == before
        assert abs(_size(button)[0] - width) <= 0.5
    finally:
        _close(page)


def test_the_text_a_rule_copies_is_the_website_s_text(served) -> None:
    """The route serves the functions the website's generator calls: compared with its output."""
    import html
    import re
    import sys

    tools = Path(__file__).resolve().parent.parent / "tools" / "docs"
    if not (tools / "generate_reference.py").is_file():
        pytest.skip("the documentation generators are not part of this install")
    address, _, _ = served
    sys.path.insert(0, str(tools))
    try:
        from generate_reference import _catalogue  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(tools))
    (page_text,) = (page.text for page in _catalogue([]))
    sources = {
        found.group(1): html.unescape(found.group(2))
        for found in re.finditer(
            r'<pre id="([^"]+)"[^>]*><code>(.*?)</code></pre>', page_text, re.S
        )
    }
    copied = _get(address, f"api/v1/rules/copy?rule={SETTINGS_RULE}")
    assert copied["values"] == sources[f"y-r-{SETTINGS_RULE}-set"]
    assert copied["switch_off"] == sources[f"y-r-{SETTINGS_RULE}-off"]
    word = _get(address, "api/v1/dictionary/copy?term=confocal")
    assert all(entry["text"] in sources.values() for entry in word["entries"])


def test_how_to_switch_off_shows_the_whole_numbered_file_and_copies_what_it_shows(
    browser, served
) -> None:
    address, _, project = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        page.get_by_label("Find a rule").fill(SETTINGS_RULE)
        page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
        page.get_by_role("button", name="Switch off…").click()
        dialog = page.get_by_role("dialog", name=f"How to switch off {SETTINGS_RULE}")
        dialog.wait_for()
        # The project already has revision 1 (the rule the fixture switched off), so the file the
        # box shows is number 2, with its name; it is shown whole and copied whole.
        offered = _get(address, f"api/v1/rules/copy?rule={SETTINGS_RULE}")
        assert (project / "rulebook" / "project-0001.yaml").is_file()
        assert offered["next_revision"] == 2 and offered["file_name"] == "project-0002.yaml"
        assert "project-0002.yaml" in dialog.inner_text()
        shown = dialog.locator("pre").evaluate("(node) => node.textContent")
        assert shown == offered["file_text"]
        assert shown.startswith("project_revision: 2\n")
        assert f"  - disable: {SETTINGS_RULE}" in shown
        dialog.get_by_role("button", name="Copy the file").click()
        _watch_the_label(page, "switch_off", "Copied")
        assert page.evaluate("navigator.clipboard.readText()") == shown
        dialog.get_by_role("button", name="Close").click()
        dialog.wait_for(state="detached")
        # Nothing was written: the box only shows how.
        assert not (project / "rulebook" / "project-0002.yaml").exists()
    finally:
        _close(page)


def test_the_box_numbers_the_file_afresh_each_time_it_opens(browser, served) -> None:
    """A Commit writes a revision file and takes the next number. A box opened again on the same
    page, after one, must show the number after that, or the file saved from it is refused."""
    address, _, project = served
    taken = project / "rulebook" / "project-0002.yaml"
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        page.get_by_label("Find a rule").fill(SETTINGS_RULE)
        page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
        opener = page.get_by_role("button", name="Switch off…")
        opener.click()
        dialog = page.get_by_role("dialog", name=f"How to switch off {SETTINGS_RULE}")
        dialog.wait_for()
        assert "project-0002.yaml" in dialog.inner_text()
        dialog.get_by_role("button", name="Close").click()
        dialog.wait_for(state="detached")
        # What a Commit of a setting writes, while the page stays as it is.
        taken.write_text(
            "project_revision: 2\nsummary: A setting.\nchanges:\n"
            f"  - override: {SETTINGS_RULE}\n    parameters:\n      earliest_credible_year: 2001\n",
            encoding="utf-8",
        )
        opener.click()
        dialog.wait_for()
        assert "project-0003.yaml" in dialog.inner_text()
        shown = dialog.locator("pre").evaluate("(node) => node.textContent")
        assert shown.startswith("project_revision: 3\n")
        dialog.get_by_role("button", name="Close").click()
    finally:
        taken.unlink(missing_ok=True)
        _close(page)


def test_a_replaced_rule_offers_no_copy_and_says_why(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        replaced = next(
            rule
            for rule in _get(address, "api/rules")["inactive"]
            if rule["off_kind"] == "replaced"
        )
        page.get_by_role("button", name="Expand all").click()
        page.locator(f'tr.rules-row[data-rule="{replaced["rule_id"]}"]').click()
        card = page.locator(f'#detail .rule-card[data-rule="{replaced["rule_id"]}"]')
        card.wait_for()
        button = card.get_by_role("button", name="Copy as a revision")
        # C does nothing here. A copy (or the box that says it cannot be made) would follow the key
        # within moments, so the page is watched for one and none may come.
        page.locator("body").click(position={"x": 5, "y": 5})
        page.evaluate("navigator.clipboard.writeText('nothing copied yet')")
        page.keyboard.press("c")
        with pytest.raises(sync_api.TimeoutError):
            page.wait_for_function(
                "() => document.querySelector('dialog')"
                " || document.querySelector('[data-copy=revision]').dataset.copied",
                timeout=2000,
            )
        assert page.evaluate("navigator.clipboard.readText()") == "nothing copied yet"
        assert button.inner_text() == "Copy as a revision"
        assert button.is_disabled()
        assert "A newer rule replaced this one" in card.locator(".rules-copy-why").inner_text()
        # The route offers nothing for it either.
        offered = _get(address, f"api/v1/rules/copy?rule={replaced['rule_id']}")
        assert offered["replaced"] is True and offered["revision"] is None
    finally:
        _close(page)


def test_its_conclusions_opens_the_conclusion_samples_of_that_rule(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "rules")
    try:
        _rules_ready(page)
        data = _get(address, "api/rules")
        rules = [
            rule for section in data["sections"] for rule in section["rules"] if rule["claims"]
        ]
        sampled = next(rule for rule in rules if rule["effect"] != "enumerates")
        walked = next(rule for rule in rules if rule["effect"] == "enumerates")
        # The walk's own facts are not sampled on the Questions page: the chip says so.
        page.get_by_label("Find a rule").fill(walked["rule_id"])
        page.locator(f'tr.rules-row[data-rule="{walked["rule_id"]}"]').click()
        assert page.get_by_role("button", name="Its results").is_disabled()
        page.get_by_label("Find a rule").fill(sampled["rule_id"])
        page.locator(f'tr.rules-row[data-rule="{sampled["rule_id"]}"]').click()
        page.get_by_role("button", name="Its results").click()
        page.wait_for_function("() => window.location.hash === '#claims'")
        page.get_by_label("Rule", exact=True).wait_for()
        assert page.get_by_label("Rule", exact=True).input_value() == sampled["rule_id"]
    finally:
        _close(page)


# ----------------------------------------------------------------------------------------------
# The Dictionary, aligned with the Rules
# ----------------------------------------------------------------------------------------------


def test_the_dictionary_rows_say_who_taught_each_word_with_a_magenta_dot(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "dictionary")
    try:
        page.locator("table.dictionary tbody tr.row").first.wait_for()
        heads = page.locator("table.dictionary thead th").all_inner_texts()
        assert heads == ["Word", "Meaning", "Kind of meaning", "Who taught it", "Names matched"]
        page.locator("input.dict-search").fill(SPELLING)
        taught = page.locator(f'table.dictionary tr.row[data-term="{TERM}"]')
        taught.wait_for()
        cell = taught.locator("td.taught")
        assert TEACHER in cell.inner_text()
        # The dot is magenta: the website's person color, the one job that color has.
        person = page.evaluate(PERSON_COLOR)
        dot = cell.locator(".person-dot")
        assert dot.count() == 1
        assert (
            dot.evaluate("(node) => getComputedStyle(node, '::before').backgroundColor") == person
        )
        # A word that ships with Ingence has no dot and says where it comes from.
        page.locator("input.dict-search").fill("confocal")
        shipped = page.locator('table.dictionary tr.row[data-term="confocal"]')
        shipped.wait_for()
        assert shipped.locator(".person-dot").count() == 0
        assert shipped.locator("td.taught .tag").count() >= 1
    finally:
        _close(page)


def test_the_word_card_shows_the_magenta_dot_and_who_taught_it(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "dictionary")
    try:
        page.locator("table.dictionary tbody tr.row").first.wait_for()
        page.locator("input.dict-search").fill(SPELLING)
        page.locator(f'table.dictionary tr.row[data-term="{TERM}"]').click()
        card = page.locator(f'#detail .word-card[data-term="{TERM}"]')
        card.wait_for()
        assert card.locator(".eyebrow").first.inner_text().lower() == "dictionary word"
        assert card.locator("h2").inner_text() == TERM
        for label in ("Kind of meaning", "Who taught it"):
            assert card.locator("dt", has_text=label).count() == 1
        who = card.locator("dd", has=page.locator(".person-dot"))
        assert TEACHER in who.inner_text()
        assert who.locator(".person-dot").evaluate(
            "(node) => getComputedStyle(node, '::before').backgroundColor"
        ) == page.evaluate(PERSON_COLOR)
        # The spellings, the Record and the controls the page had stay on the card.
        assert card.locator("code.spelling", has_text=SPELLING).count() == 1
        assert card.get_by_label("Another spelling").count() == 1
        assert "taught by" in card.locator("ol.word-meanings").inner_text()
    finally:
        _close(page)


def test_copy_as_yaml_copies_the_entry_a_pack_accepts(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "dictionary")
    try:
        page.locator("table.dictionary tbody tr.row").first.wait_for()
        page.locator("input.dict-search").fill("confocal")
        page.locator('table.dictionary tr.row[data-term="confocal"]').click()
        card = page.locator('#detail .word-card[data-term="confocal"]')
        card.wait_for()
        card.get_by_role("button", name="Copy as YAML").click()
        _watch_the_label(page, "yaml", "Copied")
        copied = _get(address, "api/v1/dictionary/copy?term=confocal")
        assert page.evaluate("navigator.clipboard.readText()") == copied["text"]
        assert copied["text"].lstrip().startswith("- {term: confocal")
        _watch_the_label(page, "yaml", "Copy as YAML")
    finally:
        _close(page)


def test_the_copy_of_a_taught_word_holds_only_vocabulary(browser, served) -> None:
    address, _, _ = served
    page = _page(browser, address, "dictionary")
    try:
        page.locator("table.dictionary tbody tr.row").first.wait_for()
        page.locator("input.dict-search").fill(SPELLING)
        page.locator(f'table.dictionary tr.row[data-term="{TERM}"]').click()
        card = page.locator(f'#detail .word-card[data-term="{TERM}"]')
        card.wait_for()
        card.get_by_role("button", name="Copy as YAML").click()
        _watch_the_label(page, "yaml", "Copied")
        text = page.evaluate("navigator.clipboard.readText()")
        assert SPELLING in text
        assert "raised_by" not in text and "defined" not in text
    finally:
        _close(page)


# ----------------------------------------------------------------------------------------------
# Accessibility: axe-core on both pages, with a card open, in light and in dark
# ----------------------------------------------------------------------------------------------


def _axe_source() -> str | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("view", ["rules", "dictionary"])
def test_the_rules_and_the_dictionary_have_no_serious_violation(
    browser, served, view: str, scheme: str
) -> None:
    source = _axe_source()
    if source is None:
        message = "axe-core is not installed: run `npm install`, or set INGENCE_AXE_JS"
        if REQUIRED:
            pytest.fail(f"{message} (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
        pytest.skip(message)
    address, _, _ = served
    context = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    try:
        page.goto(f"{address}#{view}")
        if view == "rules":
            _rules_ready(page)
            page.get_by_role("button", name="Expand all").click()
            page.locator(f'tr.rules-row[data-rule="{SETTINGS_RULE}"]').click()
            page.locator(f'#detail .rule-card[data-rule="{SETTINGS_RULE}"]').wait_for()
        else:
            page.locator("table.dictionary tbody tr.row").first.wait_for()
            page.locator("input.dict-search").fill(SPELLING)
            page.locator(f'table.dictionary tr.row[data-term="{TERM}"]').click()
            page.locator(f'#detail .word-card[data-term="{TERM}"]').wait_for()
        page.add_script_tag(content=source)
        found = page.evaluate(
            "options => window.axe.run(document, options)",
            {
                "runOnly": {
                    "type": "tag",
                    "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
                }
            },
        )
        serious = [
            f"{item['impact']} {item['id']} on {len(item['nodes'])} element(s): "
            f"{[node['html'][:100] for node in item['nodes'][:3]]}"
            for item in found["violations"]
            if item["impact"] in ("critical", "serious")
        ]
        assert serious == [], f"{scheme} {view}: {serious}"
    finally:
        context.close()
