"""The Dashboard's frame: header, footer, states, Report a problem and keyboard.

Every test reads what a person sees, in a real browser, on a small invented project. The
project's root path and its names hold distinctive invented words (``Marrowby``, ``quillfern``),
and one word is taught through the Record, so a test can look for any of them in what "Report a
problem" shows. Nothing here opens an address a button points at; the address is read from the
attribute. The one root that is "unplugged" is a temporary folder the test moves away and back,
never a mount.

The tests, by what they check:

- the brand: ``test_the_header_brand_is_the_mark_and_the_word``, ``test_the_two_faces...``
- the header chips: ``test_the_header_is_one_row_at_1280...``,
  ``test_the_header_controls_are_32_px...``,
  ``test_every_control_in_the_header_and_footer_shows_a_two_pixel_accent_ring``
- the footer: ``test_the_footer_is_one_row_at_1280...``,
  ``test_the_footer_has_the_one_outbound_link...``
- Report a problem: ``test_report_a_problem_opens_a_sheet...``, ``test_copy_the_text_instead...``,
  ``test_the_report_holds_no_path_folder_name_or_taught_word``, the public-repository test
- the keyboard: the skip link, the keys of every page, ``R`` with nothing waiting, the ``?``
  sheet, typing in a field, a modifier held
- the states: census only, a root that cannot be reached, a failed route, an empty search
- the project's name and Rename: ``test_the_header_names_the_project_and_rename...``

Skipped where the ``browser`` extra or a browser is missing, as every browser test is.
"""

# ruff: noqa: E501  (the JavaScript each test runs in the page is kept whole, as the page reads it)

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    REPOSITORY,
    launch_browser,
    need,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

STARTED = "2026-10-13T20:00:00Z"
CONTACT = "hello@ingence.org"
GUIDE = "https://ingence.org/guides/reporting-a-problem.html"
#: Words no part of the engine's own text holds: any of them in a report is a leak.
DISTINCT = ("Marrowby", "quillfern", "Whitfield", "Okafor", "Dana_Whitfield", "zorblat")
NO_AXE = "axe-core is not installed: run `npm install` in the repository, or set INGENCE_AXE_JS"


class Served(NamedTuple):
    address: str
    base: Path
    project: Path
    pool: Path


def _build(base: Path, *, infer: bool) -> tuple[Path, Path]:
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "Marrowby_Pool"
    people = {
        "Dana_Whitfield": ["HeLa_DAPI_actin_2024-03-11", "HEK293_GFP_2024-05-02"],
        "Sam_Okafor": ["western_blot_2023-11-20"],
    }
    for person, experiments in people.items():
        for experiment in experiments:
            folder = pool / person / experiment
            folder.mkdir(parents=True)
            (folder / f"quillfern_{experiment}_raw.csv").write_text("a,b\n1,2\n")
            (folder / f"{experiment}_analysis.csv").write_text("a,b\n1,2\n")
            (folder / "notes.txt").write_text("hello")
    project = Project.create(base / "project", created_at=STARTED, roots=[pool], name=None)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at=STARTED)
    if infer:
        run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at=STARTED)
    return project.path, pool


def _post(address: str, route: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        address.rstrip("/") + route,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as answer:
        found: dict[str, Any] = json.loads(answer.read())
    return found


def _get(address: str, path: str) -> Any:
    with urllib.request.urlopen(f"{address.rstrip('/')}{path}", timeout=30) as answer:
        return json.loads(answer.read())


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """An inferred project, one word taught through the Record (so one change waits for Re-run and
    the badge shows), a root and names holding distinctive invented words."""
    base = tmp_path_factory.mktemp("shell")
    project, pool = _build(base, infer=True)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        _post(
            address,
            "/api/review/answer",
            {"actor": "dana", "word": "quillfern", "answer": "quill fern", "theme_kind": "subject"},
        )
        yield Served(address, base, project, pool)
    finally:
        stop(process)


@pytest.fixture(scope="module")
def census_only(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """A project that was read and never inferred, as ``ingence index`` alone leaves it."""
    base = tmp_path_factory.mktemp("census")
    project, pool = _build(base, infer=False)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        yield Served(address, base, project, pool)
    finally:
        stop(process)


@pytest.fixture(scope="module")
def census_job(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """A second project read and never inferred, whose rules one test really starts: the shared
    census-only project must stay as it is for the tests that look at its card."""
    base = tmp_path_factory.mktemp("job")
    project, pool = _build(base, infer=False)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        yield Served(address, base, project, pool)
    finally:
        stop(process)


def _page(browser: Any, width: int = 1280, height: int = 800, **options: Any) -> Any:
    return browser.new_context(viewport={"width": width, "height": height}, **options).new_page()


def _open(browser: Any, served: Served, view: str = "years", width: int = 1280) -> Any:
    page = _page(browser, width)
    page.goto(f"{served.address}#{view}")
    page.wait_for_selector("#project-name:not([hidden])", timeout=HANG_GUARD_MS)
    page.wait_for_function(
        "document.querySelector('#footer-version').textContent.includes('schema')",
        timeout=HANG_GUARD_MS,
    )
    # The faces are loaded before anything is measured (a face that arrives late moves the layout).
    page.evaluate("document.fonts.ready.then(() => true)")
    return page


def _drawn(page: Any) -> None:
    """The view has drawn: nothing in the list pane says it is still reading."""
    page.wait_for_function(
        "(() => { const list = document.getElementById('list');"
        " return list.children.length > 0 && !list.querySelector('.state-loading'); })()",
        timeout=HANG_GUARD_MS,
    )


def _box(page: Any, selector: str) -> dict[str, float]:
    found = page.locator(selector).first.bounding_box()
    assert found is not None, f"{selector} is not on the page"
    return found


# ------------------------------------------------------------------------- the brand


@pytest.mark.parametrize("width", [1280, 1024])
def test_the_header_brand_is_the_mark_and_the_word(
    browser: Any, served: Served, width: int
) -> None:
    page = _open(browser, served, width=width)
    facts = page.evaluate(
        """() => {
          const mark = document.querySelector('h1.brand svg.brand-mark');
          const word = document.querySelector('h1.brand .brand-word');
          const files = mark.querySelector('.files');
          const made = mark.querySelector('path.made');
          const root = getComputedStyle(document.documentElement);
          const probe = document.createElement('i');
          probe.style.color = root.getPropertyValue('--accent');
          document.body.append(probe);
          const accent = getComputedStyle(probe).color;
          probe.remove();
          const style = getComputedStyle(word);
          return {
            width: mark.getBoundingClientRect().width, height: mark.getBoundingClientRect().height,
            wordSize: style.fontSize, wordWeight: style.fontWeight, wordText: word.textContent,
            transform: style.textTransform,
            filesStroke: getComputedStyle(files).stroke, ink: getComputedStyle(word).color,
            madeFill: getComputedStyle(made).fill, accent,
            metadata: mark.querySelectorAll('metadata').length,
          };
        }"""
    )
    page.context.close()
    assert (facts["width"], facts["height"]) == (26, 26)
    assert facts["wordText"] == "Ingence" and facts["transform"] == "none"
    assert facts["wordSize"] == "18px" and int(facts["wordWeight"]) >= 700
    assert facts["filesStroke"] == facts["ink"], "the files take the text color"
    assert facts["madeFill"] == facts["accent"], "the made file takes the accent"
    assert facts["metadata"] == 0, "the mark carries no metadata"


def test_the_two_faces_are_the_ones_the_page_draws_in(browser: Any, served: Served) -> None:
    page = _open(browser, served)
    facts = page.evaluate(
        """async () => {
          await document.fonts.ready;
          return {text: document.fonts.check('16px "Atkinson Hyperlegible"'),
            mono: document.fonts.check('13px "IBM Plex Mono"'),
            body: getComputedStyle(document.body).fontFamily,
            version: getComputedStyle(document.getElementById('footer-version')).fontFamily};
        }"""
    )
    page.context.close()
    assert facts["text"] and facts["mono"]
    assert facts["body"].startswith('"Atkinson Hyperlegible"')
    assert facts["version"].startswith('"IBM Plex Mono"')


# ------------------------------------------------------------------------ the header chips


def test_the_header_is_one_row_at_1280_with_brand_search_chips_and_name(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, width=1280)
    brand, search = _box(page, ".top .brand"), _box(page, "#search-slot")
    controls, name = _box(page, "#header-controls"), _box(page, "#project-name")
    top = _box(page, ".top")
    page.context.close()
    assert top["height"] < 50, f"the header's row wraps: {top['height']} px tall"
    centers = [part["y"] + part["height"] / 2 for part in (brand, search, controls, name)]
    assert max(centers) - min(centers) < 6
    assert brand["x"] < search["x"] < controls["x"] < name["x"]


def test_the_header_controls_are_32_px_chips_with_equal_gaps_badge_or_not(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, width=1280)
    page.wait_for_selector("#rerun-badge:not([hidden])", timeout=HANG_GUARD_MS)

    def gaps() -> tuple[list[float], list[float]]:
        found = page.evaluate(
            """() => [...document.querySelectorAll('#header-controls > .chip-control, '
              + '#header-controls #workspace-slot > .chip-control, '
              + '#header-controls .chip-badge-wrap > .chip-control')]
              .map((node) => { const b = node.getBoundingClientRect();
                return [b.x, b.width, b.height]; })
              .sort((a, b) => a[0] - b[0])"""
        )
        heights = [chip[2] for chip in found]
        between = [found[i + 1][0] - (found[i][0] + found[i][1]) for i in range(len(found) - 1)]
        return heights, between

    heights_with, between_with = gaps()
    # The badge off: the gaps are the same, because the badge sits on the corner of Re-run.
    page.evaluate("document.getElementById('rerun-badge').hidden = true")
    heights_without, between_without = gaps()
    labels = page.evaluate(
        "[...document.querySelectorAll('#header-controls .chip-control')].map(n => n.textContent.trim())"
    )
    dot = page.evaluate(
        "(() => { const s = getComputedStyle(document.getElementById('rerun-badge'));"
        " return [s.borderRadius, s.position]; })()"
    )
    page.context.close()
    assert labels == ["Committed", "Re-run", "Import", "Export", "Open another project", "?"]
    assert set(heights_with) == {32} == set(heights_without)
    assert max(between_with) - min(between_with) <= 1, between_with
    assert between_with == between_without
    assert dot[1] == "absolute" and dot[0] != "0px"


def test_every_control_in_the_header_and_footer_shows_a_two_pixel_accent_ring(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, width=1280)
    page.keyboard.press("Tab")  # the skip link first
    seen: list[tuple[str, str, str, str]] = []
    # The walk ends at the footer's last link. It is bounded by how many controls the page has, plus
    # a few the count misses, and not by a fixed number that falls short each time a section is
    # added to a page.
    reachable = page.evaluate(
        """() => document.querySelectorAll(
          'a[href], button:not([disabled]), input:not([type=hidden]), select, textarea, summary,'
          + ' [tabindex]:not([tabindex="-1"])').length"""
    )
    for _ in range(reachable + 5):
        facts = page.evaluate(
            """() => { const e = document.activeElement; const s = getComputedStyle(e);
              const probe = document.createElement('i');
              probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent');
              document.body.append(probe); const accent = getComputedStyle(probe).color; probe.remove();
              return [e.id || e.textContent.trim().slice(0, 20), s.outlineWidth, s.outlineOffset,
                s.outlineColor === accent ? 'accent' : s.outlineColor, s.outlineStyle,
                !!e.closest('header, footer, .skip-link') || e.classList.contains('skip-link')]; }"""
        )
        if facts[5]:
            seen.append((facts[0], facts[1], facts[2], f"{facts[3]} {facts[4]}"))
        if facts[0] in {"report-link"}:
            break
        page.keyboard.press("Tab")
    page.context.close()
    names = [each[0] for each in seen]
    for expected in ("skip-link", "search-box", "help-button", "docs-link", "report-link"):
        assert expected in names, f"Tab never reached {expected}: {names}"
    for found in seen:
        assert found[1:] == ("2px", "2px", "accent solid"), found


# ------------------------------------------------------------------------------ footer


def _version_said() -> tuple[str, str]:
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY / "src")}
    said = subprocess.run(
        [sys.executable, "-m", "ingence.cli.main", "--version"],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
        timeout=120,
    ).stdout.strip()
    found = re.match(r"^ingence (\S+) \(schema ([^,)\s]+)", said)
    assert found, said
    return found.group(1), found.group(2)


def _footer_boxes(page: Any) -> tuple[dict[str, float], dict[str, float]]:
    """The sentence's box and the links' box, taken in one step so a late shift cannot fall between."""
    found = page.evaluate(
        """() => ['footer .footer-line', 'footer .footer-links'].map((s) => {
          const b = document.querySelector(s).getBoundingClientRect();
          return {x: b.x, y: b.y, height: b.height}; })"""
    )
    return found[0], found[1]


def test_the_footer_is_one_row_at_1280_two_rows_at_800_and_says_the_version(
    browser: Any, served: Served
) -> None:
    version, schema = _version_said()
    wide = _open(browser, served, width=1280)
    line, links = _footer_boxes(wide)
    foot = wide.evaluate(
        """() => { const f = document.querySelector('footer'); const s = getComputedStyle(f);
          return {size: s.fontSize, line: s.borderTopWidth, ruled: s.borderTopStyle,
            text: f.innerText, right: document.getElementById('footer-version').textContent,
            colored: s.color, soft: getComputedStyle(document.documentElement).getPropertyValue('--ink-soft')}; }"""
    )
    wide.context.close()
    assert abs((line["y"] + line["height"] / 2) - (links["y"] + links["height"] / 2)) < 8
    assert line["x"] < links["x"], "the sentence is at the left and the links at the right"
    assert (foot["size"], foot["line"], foot["ruled"]) == ("13px", "1px", "solid")
    assert foot["right"] == f"ingence {version} \u00b7 schema {schema}"
    assert "build" not in foot["text"] and not re.search(r"[0-9a-f]{7,}", foot["right"])
    narrow = _open(browser, served, width=800)
    line2, links2 = _footer_boxes(narrow)
    narrow.context.close()
    assert links2["y"] >= line2["y"] + line2["height"] - 1, (
        "below 900 px the footer wraps to two rows"
    )


def test_the_footer_has_the_one_outbound_link_and_the_report_link(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served)
    outbound = page.evaluate(
        "[...document.querySelectorAll('a[href^=\"http\"]')].map(a => [a.id, a.href, a.target, a.rel])"
    )
    footer_words = page.inner_text("footer .footer-links")
    page.context.close()
    assert [row[0] for row in outbound] == ["docs-link"], outbound
    assert outbound[0][1].rstrip("/") == "https://ingence.org" and outbound[0][2] == "_blank"
    assert "noopener" in outbound[0][3] and "noreferrer" in outbound[0][3]
    assert "Documentation" in footer_words and "Report a problem" in footer_words


# ----------------------------------------------------------------- Report a problem


def _email_only(page: Any) -> None:
    """Answer ``/api/v1/reporting`` with no new-issue address, so the sheet offers the email
    whatever metadata the installed package carries."""

    def serve(route: Any) -> None:
        answered = route.fetch()
        route.fulfill(response=answered, json={**answered.json(), "new_issue": None})

    page.route("**/api/v1/reporting", serve)


def _sheet(page: Any) -> Any:
    page.click("#report-link")
    page.wait_for_selector("dialog.report-sheet[open] #report-text", timeout=HANG_GUARD_MS)
    return page.locator("dialog.report-sheet")


def _decoded(address: str) -> tuple[str, str, str]:
    parts = urlsplit(address)
    query = parse_qs(parts.query, keep_blank_values=True)
    return unquote(parts.path), query["subject"][0], query["body"][0].replace("\r\n", "\n")


def test_report_a_problem_opens_a_sheet_that_shows_the_text_before_it_leaves(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served)
    _email_only(page)
    sheet = _sheet(page)
    eyebrow = sheet.locator(".eyebrow").inner_text()
    title = sheet.locator("h2").inner_text()
    shown = sheet.locator("#report-text").inner_text()
    font = page.evaluate("getComputedStyle(document.getElementById('report-text')).fontFamily")
    primary = sheet.locator("#report-open")
    address = str(primary.get_attribute("href"))
    note = sheet.inner_text()
    guide = sheet.locator("#report-guide").get_attribute("href")
    names = sheet.locator("button, a.sheet-button").all_inner_texts()
    page.context.close()
    assert eyebrow.lower() == "report a problem"
    assert title == f"An email to {CONTACT}, with nothing from your data"
    assert font.startswith('"IBM Plex Mono"')
    who, subject, body = _decoded(address)
    assert who == CONTACT and subject.startswith("Ingence problem report ")
    assert body.strip() == shown.strip(), "the box shows the text the email carries"
    assert "ingence teach shapes" in note and "never holds a name, a path or a word" in note
    assert guide == GUIDE
    assert "Open the email" in names and "Copy the text instead" in names
    assert len(address) <= 2000
    # While the repository is not public the sheet does not say it opens an issue page.
    assert "new-issue page" not in note


def test_copy_the_text_instead_copies_what_the_box_shows(browser: Any, served: Served) -> None:
    page = _page(browser)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.goto(f"{served.address}#years")
    page.wait_for_selector("#report-link", timeout=HANG_GUARD_MS)
    sheet = _sheet(page)
    shown = sheet.locator("#report-text").inner_text()
    sheet.locator("#report-copy").click()
    page.wait_for_function(
        "document.getElementById('report-copy').textContent === 'Copied'", timeout=10000
    )
    copied = page.evaluate("navigator.clipboard.readText()")
    page.context.close()
    assert copied.strip() == shown.strip() and copied.strip()


def test_the_report_holds_no_path_folder_name_or_taught_word(browser: Any, served: Served) -> None:
    project = _get(served.address, "/api/project")
    version = _get(served.address, "/api/v1/version")
    page = _open(browser, served)
    _email_only(page)
    sheet = _sheet(page)
    shown = sheet.locator("#report-text").inner_text()
    address = str(sheet.locator("#report-open").get_attribute("href"))
    page.context.close()
    who, subject, body = _decoded(address)
    everything = f"{shown}\n{address}\n{subject}\n{body}\n{who}"
    lowered = everything.lower()
    for word in DISTINCT:
        assert word.lower() not in lowered, f"the report holds {word!r}"
    places = {str(served.base), str(served.project), str(served.pool), str(served.base.parent)}
    places.update(str(root["root_uri"]) for root in project["roots"])
    for place in places:
        assert place not in everything and place.replace("/", "%2F") not in everything, place
    for run in filter(None, {version["run_pinned"], version["run_current"]}):
        assert run not in everything
    assert not re.search(r"/Users/|/home/|/private/|/var/folders|C:\\\\", everything)
    for part in served.pool.rglob("*"):
        for found in re.findall(r"[A-Za-z]{6,}", part.name):
            assert found.lower() not in lowered or found.lower() in {"python"}, found
    # What it does state: the version with its build, the computer, counts with a number each, view.
    head = f"ingence {version['engine_version']} \u00b7 schema {version['schema_version']}"
    assert shown.startswith(head), shown.splitlines()[0]
    if version["engine_commit"]:
        assert f"\u00b7 build {version['engine_commit']}" in shown.splitlines()[0]
    assert re.search(r"^computer: .+ \u00b7 Python \d", shown, re.MULTILINE)
    assert re.search(r"^project: [\d,]+ files and folders", shown, re.MULTILINE)
    # #years opens Overlap (its By year mode), and the address says #overlap.
    assert re.search(r"^view: #overlap$", shown, re.MULTILINE)


def test_the_report_names_a_failed_route_and_time_and_nothing_a_person_typed(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, view="inventory")
    clean = page.evaluate(
        "[window.ingenceReport.safeRoute('/api/v1/themes/map?q=quillfern'),"
        " window.ingenceReport.safeRoute('/api/entry/abc123def'),"
        " window.ingenceReport.safeRoute('/api/folder/HeLa Marrowby'),"
        " window.ingenceReport.safeRoute('/api/dictionary/quillfern/actin'),"
        " window.ingenceReport.safeRoute('/Users/dana/pool'),"
        " window.ingenceReport.safeRoute('/api/search/stream?q=HEK293T+brefeldin')]"
    )
    line = page.evaluate(
        "window.ingenceReport.failureLine({method: 'GET', route: '/api/v1/themes/map?q=zorblat',"
        " status: 500, at: new Date(2026, 9, 13, 9, 14)})"
    )
    page.context.close()
    assert clean == [
        "/api/v1/themes/map",
        "/api/entry/:id",
        "/api/folder/:id",
        "/api/dictionary/:id",
        "/:id",
        "/api/search/:id",
    ]
    assert line == "error: GET /api/v1/themes/map returned 500 at 9:14 AM"
    assert "zorblat" not in line


def test_the_builder_states_nothing_it_is_not_meant_to(browser: Any, served: Served) -> None:
    """Hostile input given straight to the builder: a version that is a path, a count that is not a
    whole number, a commit that is a name, a computer that is a name, an issue address that is not
    https, a status that is text."""
    page = _open(browser, served)
    hostile = "/Users/someone/Example_Lab_Data"
    text = page.evaluate(
        "hostile => window.ingenceReport.bodyText({"
        " version: {engine_version: hostile, engine_commit: hostile, schema_version: hostile},"
        " counts: {entries: hostile, claims: -3, findings: 1.5, candidates: null},"
        " roots: hostile, agent: 'Browser 1', window: [1, 2], page: null,"
        " reporting: {computer: hostile, python: hostile, new_issue: hostile},"
        " failure: {method: hostile, route: hostile, status: hostile, at: hostile},"
        " settings: [{name: hostile, kind: 'boolean', value: true, changed: true},"
        "   {name: 'a.b', kind: 'number', value: hostile, changed: true}]})",
        hostile,
    )
    issue = page.evaluate(
        "hostile => window.ingenceReport.reportFor({reporting: {new_issue: hostile}}).issue",
        hostile,
    )
    page.context.close()
    assert "someone" not in text and "Example_Lab" not in text
    assert "ingence: the version could not be read." in text
    assert "project: nothing read yet, or not readable." in text
    assert "computer:" not in text
    assert issue is None


def test_the_address_is_cut_to_two_thousand_characters_with_the_count_of_what_is_left_out(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served)
    report = page.evaluate(
        """() => {
          const settings = [];
          for (let i = 0; i < 120; i += 1) {
            settings.push({name: 'section.' + 'a_long_setting_name_'.repeat(2) + i, kind: 'boolean',
              value: true, changed: true});
          }
          return window.ingenceReport.reportFor({
            version: {engine_version: '0.3.1', engine_commit: 'a'.repeat(40) + '+dirty',
              schema_version: '1.23'},
            counts: {entries: 99999999, claims: 99999999, findings: 99999999,
              candidates: 99999999, rule_failures: 99999999},
            roots: 12, settings, agent: 'Mozilla/5.0 '.repeat(40), window: [3840, 2160],
            page: 'candidates', reporting: {computer: 'macOS 14.6 (arm64)', python: '3.12.6',
              new_issue: 'https://example.invalid/owner/ingence/issues/new'},
          });
        }"""
    )
    page.context.close()
    assert len(report["email"]) <= 2000, len(report["email"])
    assert re.search(r"and \d+ more", report["text"]) or "not named here" in report["text"]
    assert "Settings changed from their defaults (120)" in report["text"]
    # One text for the box, the copy, the email and the issue page.
    assert _decoded(report["email"])[2].strip() == report["text"].strip()
    assert report["issue"].startswith("https://example.invalid/owner/ingence/issues/new?title=")
    assert unquote(report["issue"].split("&body=")[1]).strip() == report["text"].strip()


def test_when_the_repository_is_public_the_primary_opens_its_issue_page(
    browser: Any, served: Served
) -> None:
    """The route says the repository's issue tracker (the package's own metadata names one only once
    the repository is public): the sheet says so in one line, and the button's address is the
    new-issue page carrying the same text. The address is read, never opened."""
    page = _open(browser, served)

    def public(route: Any) -> None:
        answered = route.fetch()
        route.fulfill(
            response=answered,
            json={
                **answered.json(),
                "new_issue": "https://example.invalid/owner/ingence/issues/new",
            },
        )

    page.route("**/api/v1/reporting", public)
    sheet = _sheet(page)
    shown = sheet.locator("#report-text").inner_text()
    primary = sheet.locator("#report-open")
    address = str(primary.get_attribute("href"))
    label = primary.inner_text()
    target = primary.get_attribute("target")
    said = sheet.locator("#report-repository").inner_text()
    email = str(sheet.locator("#report-email").get_attribute("href"))
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()
    assert address.startswith("https://example.invalid/owner/ingence/issues/new?title=")
    assert parse_qs(urlsplit(address).query)["title"][0] == _decoded(email)[1]
    assert unquote(address.split("&body=")[1]).strip() == shown.strip()
    assert label == "Open the issue page" and target == "_blank"
    assert "new-issue page" in said
    assert email.startswith(f"mailto:{CONTACT}?")


def test_the_report_sheet_closes_with_escape_and_gives_focus_back(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served)
    page.focus("#report-link")
    page.keyboard.press("Enter")
    page.wait_for_selector("dialog.report-sheet[open]", timeout=HANG_GUARD_MS)
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('dialog.report-sheet')", timeout=10000)
    held = page.evaluate("document.activeElement.id")
    page.context.close()
    assert held == "report-link"


# ----------------------------------------------------------------------------- keyboard


def test_a_skip_link_is_the_first_focus_stop_and_leaves_the_address_alone(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, view="inventory")
    page.keyboard.press("Tab")
    first = page.evaluate("document.activeElement.id")
    shown = _box(page, "#skip-link")
    page.keyboard.press("Enter")
    after = page.evaluate("[document.activeElement.tagName, location.hash]")
    page.keyboard.press("Tab")
    next_in_page = page.evaluate("!!document.activeElement.closest('main')")
    page.context.close()
    assert first == "skip-link" and shown["y"] >= 0, "the link is on screen once it has focus"
    assert after == ["MAIN", "#inventory"], after
    assert next_in_page, "the next Tab is inside the page, past the header"


def _active_page(page: Any) -> str:
    return str(page.evaluate("document.querySelector('#pages button.active').dataset.page"))


def test_the_keys_of_every_page_work(browser: Any, served: Served) -> None:
    page = _open(browser, served, view="years")
    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
    page.keyboard.press("/")
    on_search = page.evaluate("document.activeElement.id")
    page.keyboard.press("Escape")  # leave the box without typing: nothing to close
    page.evaluate("document.activeElement.blur()")
    opened = []
    # Six sections, keys 1 to 6.
    pages = ["overview", "overlap", "experiments", "checks", "history", "advanced"]
    for key in "123456":
        page.keyboard.press(key)
        page.wait_for_function(
            "k => document.querySelector('#pages button.active').dataset.page === k",
            arg=pages[int(key) - 1],
            timeout=HANG_GUARD_MS,
        )
        opened.append(_active_page(page))
    page.keyboard.press("R")
    on_rerun = page.evaluate("document.activeElement.id")
    # The check is that nothing is sent: the page has asked for nothing and is quiet.
    settled(page)
    page.context.close()
    assert on_search == "search-box"
    assert opened == pages
    assert on_rerun == "rerun-button", "R focuses Re-run where a change waits"
    assert not any("/api/workspace/rerun" in url for url in posted), "R never runs anything"


def test_r_says_so_where_nothing_waits_for_rerun(browser: Any, census_only: Served) -> None:
    page = _open(browser, census_only, view="inventory")
    assert page.evaluate("document.getElementById('rerun-button').disabled")
    page.keyboard.press("R")
    page.wait_for_function(
        "document.getElementById('keys-status') && document.getElementById('keys-status').textContent",
        timeout=10000,
    )
    said = page.inner_text("#keys-status")
    page.context.close()
    assert "Nothing waits for Re-run" in said


def test_the_question_mark_lists_every_shortcut_and_escape_closes_it(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served)
    page.keyboard.press("?")
    page.wait_for_selector("dialog.shortcuts-sheet[open]", timeout=10000)
    sheet = page.locator("dialog.shortcuts-sheet")
    text = sheet.inner_text()
    rows = sheet.locator("tr").count()
    captions = sheet.locator("caption").all_inner_texts()
    keys = {key.strip() for key in sheet.locator("th kbd").all_inner_texts()}
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('dialog.shortcuts-sheet')", timeout=10000)
    page.context.close()
    # The Words section has a table of its own, and it is in Advanced.
    assert captions[0] == "Every page" and len(captions) == 9
    assert "Advanced \u203a Words" in captions
    # Every key of every view.
    wanted = {
        "/",
        "1",
        "2",
        "3",
        "4",
        "5",
        "R",
        "?",
        "Esc",
        "\u2191",
        "\u2193",
        "\u2192",
        "\u2190",
        "Backspace",
        "Enter",
        "W",
        "O",
        "N",
        "Home",
        "End",
        "+",
        "-",
        "0",
        "T",
        "Space",
        "Shift",
        "A",
        "L",
        "Tab",
        "F",
        "D",
        "C",
        "S",
        "K",
        "U",
    }
    assert wanted <= keys, wanted - keys
    assert rows >= 30
    for phrase in (
        "Focus the Ask box",
        "never starts",
        "The Wheel",
        "Overlap",
        "Teach a word",
        "Copy the card",
        "Clear the choice",
        "Center on the item",
    ):
        assert phrase in text or phrase.replace("never starts", "never start") in text, phrase


def test_the_header_question_mark_opens_the_same_sheet_and_offers_to_point_out_controls(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served)
    page.click("#help-button")
    page.wait_for_selector("dialog.shortcuts-sheet[open]", timeout=10000)
    page.click("#shortcuts-point")
    page.wait_for_selector(".help-marker", timeout=15000)
    markers = page.locator(".help-marker").count()
    page.keyboard.press("Escape")
    left = page.locator(".help-marker").count()
    page.context.close()
    assert markers > 5 and left == 0


def test_no_key_fires_while_a_text_field_has_focus(browser: Any, served: Served) -> None:
    page = _open(browser, served, view="years")
    page.click("#search-box")
    page.keyboard.type("/12345R? abc")
    value = page.input_value("#search-box")
    active = _active_page(page)
    opened = page.evaluate("!!document.querySelector('dialog[open]')")
    held = page.evaluate("document.activeElement.id")
    page.context.close()
    assert value == "/12345R? abc"
    assert active == "overlap" and not opened and held == "search-box"


def test_a_key_with_a_modifier_is_the_browsers_not_the_pages(browser: Any, served: Served) -> None:
    page = _open(browser, served, view="years")
    page.keyboard.press("Control+1")
    page.keyboard.press("Alt+3")
    # The check is that nothing happens: the page is quiet.
    settled(page)
    active = _active_page(page)
    page.context.close()
    assert active == "overlap"


# -------------------------------------------------------------------------------- the states


def test_a_project_read_and_never_inferred_says_so_on_every_tab_but_the_inventory_and_search(
    browser: Any, census_only: Served
) -> None:
    shell = _get(census_only.address, "/api/v1/shell")
    assert shell["inferred"] is False and shell["census"]["files"] > 0
    files, folders = shell["census"]["files"], shell["census"]["folders"]
    sentence = (
        f"Ingence listed {files:,} files in {folders:,} folders. No rule has run, so there are "
        "no experiments, themes or results yet."
    )
    page = _open(browser, census_only, view="years")
    # Every view the page's own navigation names, but the two that read the census alone and the four
    # that work without a rule: each must show the card (generously waited for, never skipped).
    named = page.evaluate(
        "[...document.querySelectorAll('nav.sections button[data-view]')].map(b => b.dataset.view)"
    )
    # Words works from the census alone too (a word is taught from the names, whether or
    # not a rule has run), so it keeps its own list with the state above it, as the Dictionary does.
    working = {"inventory", "rules", "dictionary", "settings", "changes", "words"}
    views = [view for view in named if view not in working]
    assert {"overlap", "map", "review", "experiments", "topics"} <= set(views), views
    for view in views:
        page.goto(f"{census_only.address}#{view}")
        page.reload()
        page.wait_for_selector('#list .state-card[data-state="census"]', timeout=90000)
        card = page.locator('#list .state-card[data-state="census"]')
        assert (
            card.locator(".state-sentence").inner_text()
            == "Files listed. The rules have not run yet."
        ), view
        assert sentence in card.inner_text(), view
        buttons = set(card.locator("button").all_inner_texts())
        assert {"Run the rules now", "Browse the Inventory"} <= buttons, view
    # The Inventory works from the census alone: no state is drawn over it.
    page.goto(f"{census_only.address}#inventory")
    page.reload()
    _drawn(page)
    assert page.locator('.state-card[data-state="census"]').count() == 0
    # Ask answers from a run's experiments, so it shows the state too (an old #search
    # address opens Ask).
    for address in ("#ask?q=keltan", "#search"):
        page.goto(f"{census_only.address}{address}")
        page.reload()
        page.wait_for_selector('#list .state-card[data-state="census"]', timeout=90000)
    # The views that work without a rule keep working, with the same state above them. Words draws
    # its own list from the census (the meter and the words), the card above and none in the list.
    page.goto(f"{census_only.address}#words")
    page.reload()
    page.wait_for_selector('#run-state .state-card[data-state="census"]', timeout=HANG_GUARD_MS)
    page.wait_for_selector("#list .words-toggles", timeout=HANG_GUARD_MS)
    assert page.locator('#list .state-card[data-state="census"]').count() == 0
    assert sentence in page.locator("#run-state").inner_text()
    page.goto(f"{census_only.address}#settings")
    page.reload()
    page.wait_for_selector('#run-state .state-card[data-state="census"]', timeout=HANG_GUARD_MS)
    page.wait_for_selector("table.settings", timeout=HANG_GUARD_MS)
    page.click("#run-state >> text=Browse the Inventory")
    page.wait_for_function("location.hash === '#inventory'", timeout=10000)
    page.context.close()


def test_run_the_rules_now_queues_inference_only_where_none_has_run(tmp_path: Path) -> None:
    """The write behind "Run the rules now": refused where a rule has run, and where it is allowed
    it queues inference alone (the project's own job, never a walk of a root)."""
    from ingence.api.routes import workspace as workspace_routes

    class Applier:
        def __init__(self, busy: bool = False) -> None:
            self.asked: list[list[str]] = []
            self.busy = busy

        def job_busy(self) -> bool:
            return self.busy

        def stopped(self) -> bool:
            return False

        def status(self) -> dict[str, str]:
            return {"phase": "inferring" if self.busy else "idle"}

        def submit_rerun(self, steps: list[str]) -> dict[str, str]:
            self.asked.append(list(steps))
            return {"phase": "queued"}

    census_project, _ = _build(tmp_path / "census", infer=False)
    applier = Applier()
    answer = workspace_routes.write(census_project, "/api/workspace/infer", {}, applier=applier)
    assert applier.asked == [["inference"]] and answer["started"] == ["inference"]
    # While a job is queued or running, nothing is queued: the answer is the applier's status.
    busy = Applier(busy=True)
    held = workspace_routes.write(census_project, "/api/workspace/infer", {}, applier=busy)
    assert busy.asked == [] and held["started"] == [] and held["applying"]["phase"] == "inferring"
    inferred_project, _ = _build(tmp_path / "inferred", infer=True)
    with pytest.raises(ValueError, match="rules have run in this project already"):
        workspace_routes.write(inferred_project, "/api/workspace/infer", {}, applier=Applier())
    with pytest.raises(ValueError, match="no job can be started"):
        workspace_routes.write(census_project, "/api/workspace/infer", {}, applier=None)


def test_a_root_that_cannot_be_reached_is_said_above_the_rows_and_the_last_run_still_reads(
    browser: Any, served: Served
) -> None:
    away = served.pool.with_name("Marrowby_Pool.away")
    page = _open(browser, served, view="years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    assert page.locator("#root-state").is_hidden()
    served.pool.rename(away)
    try:
        page.reload()
        page.wait_for_selector(
            '#root-state .state-card[data-state="problem"]', timeout=HANG_GUARD_MS
        )
        card = page.locator("#root-state .state-card")
        said = card.inner_text()
        rows_after = page.locator(".years .yrow").count()
        dot = page.evaluate(
            "(() => { const s = getComputedStyle(document.querySelector('#root-state .state-dot'));"
            " return [s.backgroundColor, s.borderTopWidth, s.borderTopColor]; })()"
        )
        above = _box(page, "#root-state")["y"] < _box(page, "main")["y"]
        # Show the last scan: the notice folds to a line, the rows keep their room.
        card.get_by_role("button", name="Show the last scan").click()
        page.wait_for_selector("#root-state .root-strip", timeout=10000)
        strip = page.inner_text("#root-state .root-strip")
        page.locator("#root-state").get_by_role("button", name="Details").click()
        page.wait_for_selector('#root-state .state-card[data-state="problem"]', timeout=10000)
    finally:
        away.rename(served.pool)
    page.locator("#root-state").get_by_role("button", name="Check again").click()
    page.wait_for_function("document.getElementById('root-state').hidden", timeout=HANG_GUARD_MS)
    page.context.close()
    assert f"The folder it reads is not reachable: {served.pool.resolve()}." in said or (
        f"The folder it reads is not reachable: {served.pool}." in said
    )
    assert re.search(r"The last scan is shown as it was published on [A-Z][a-z]+ \d{1,2}\.", said)
    assert (
        "Ingence can read nothing again until the drive is back. Reading contents is paused."
        in said
    )
    assert "Show the last scan" in said and "Check again" in said
    assert rows_after > 0, "the last run still reads"
    assert above, "the banner sits above the rows"
    # A red border and a white (page-colored) fill: never a red fill.
    assert dot[1] == "2px" and dot[0] != dot[2]
    assert "not reachable" in strip


def test_a_failed_route_says_what_failed_and_offers_a_report_with_the_route_and_time(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, view="years")
    _email_only(page)
    failing = {"on": True}

    def fail(route: Any) -> None:
        if failing["on"]:
            route.fulfill(status=500, json={"error": "ValueError: the table could not be read"})
        else:
            route.continue_()

    page.route("**/api/inventory**", fail)
    page.goto(f"{served.address}#inventory")
    page.reload()
    page.wait_for_selector("#list .state-failed", timeout=HANG_GUARD_MS)
    card = page.locator("#list .state-failed")
    said = card.inner_text()
    role = card.get_attribute("role")
    card.get_by_role("button", name="Report a problem").click()
    page.wait_for_selector("dialog.report-sheet[open] #report-text", timeout=HANG_GUARD_MS)
    shown = page.locator("#report-text").inner_text()
    address = str(page.locator("#report-open").get_attribute("href"))
    page.keyboard.press("Escape")
    failing["on"] = False
    page.locator("#list .state-failed").get_by_role("button", name="Try again").click()
    page.wait_for_function("!document.querySelector('#list .state-failed')", timeout=HANG_GUARD_MS)
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()
    assert "Ingence could not answer." in said
    assert re.search(r"GET /api/inventory returned 500 at \d{1,2}:\d{2} [AP]M\.", said)
    assert "The scan is unchanged, and nothing was saved." in said
    assert "Try again" in said and "Report a problem" in said
    assert role == "alert"
    assert re.search(
        r"^error: GET /api/inventory returned 500 at \d{1,2}:\d{2} [AP]M$", shown, re.MULTILINE
    )
    assert _decoded(address)[2].strip() == shown.strip()
    for word in DISTINCT:
        assert word.lower() not in shown.lower()


# ------------------------------------------------------------- "Run the rules now", asked once


def _infer_posts(posts: list[str]) -> int:
    return len([url for url in posts if url.endswith("/api/workspace/infer")])


def test_opening_a_census_only_page_posts_nothing_and_a_click_posts_once(
    browser: Any, census_job: Served
) -> None:
    """Nothing starts by itself; a click asks once (a double click too); a reload while the job
    runs does not offer the button again and asks nothing; when the job ends the view shows."""
    page = _page(browser)
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    page.goto(f"{census_job.address}#years")
    page.wait_for_selector('#list .state-card[data-state="census"]', timeout=90000)
    for view in ("sheet", "settings", "inventory", "years"):
        page.goto(f"{census_job.address}#{view}")
        page.reload()
        _drawn(page)
    assert posts == [], "opening and browsing a census-only project posts nothing"
    page.goto(f"{census_job.address}#years")
    page.reload()
    page.wait_for_selector('#list .state-card[data-state="census"]', timeout=90000)
    page.get_by_role("button", name="Run the rules now").dblclick()
    page.wait_for_selector('#list .state-card[data-state="loading"]', timeout=HANG_GUARD_MS)
    said = page.locator('#list .state-card[data-state="loading"]').inner_text()
    assert _infer_posts(posts) == 1, "a double click asks once"
    assert page.get_by_role("button", name="Run the rules now").count() == 0
    # A reload while the engine says a job is busy: the card says so, and nothing new is asked.
    page.route("**/api/applying", lambda r: r.fulfill(json={"phase": "inferring"}))
    page.reload()
    page.wait_for_selector('#list .state-card[data-state="loading"]', timeout=HANG_GUARD_MS)
    assert page.get_by_role("button", name="Run the rules now").count() == 0
    assert _infer_posts(posts) == 1, "a reload while the job runs asks nothing"
    # The job is the real one: when the engine says it is over, the view shows (not the card).
    page.unroute_all(behavior="ignoreErrors")
    page.wait_for_selector(".years .yrow", timeout=300000)
    shell = _get(census_job.address, "/api/v1/shell")
    page.context.close()
    assert "Applying the rules" in said
    assert shell["inferred"] is True and _infer_posts(posts) == 1


def test_a_census_only_page_found_with_a_job_running_says_so_and_a_failed_job_is_said(
    browser: Any, census_only: Served
) -> None:
    """Through the engine's own words about its job (``/api/applying``): busy on a reload says
    "Applying the rules…" with no button, and a job that fails is said on the card with the button
    back. Nothing is posted by looking."""
    page = _page(browser)
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    state = {"phase": "queued"}
    page.route("**/api/applying", lambda r: r.fulfill(json=dict(state)))
    page.goto(f"{census_only.address}#years")
    page.wait_for_selector('#list .state-card[data-state="loading"]', timeout=90000)
    assert page.get_by_role("button", name="Run the rules now").count() == 0
    state.update(phase="failed", error="OSError: the disk is full")
    page.wait_for_selector('#list .state-card[data-state="census"]', timeout=HANG_GUARD_MS)
    said = page.locator('#list .state-card[data-state="census"]').inner_text()
    enabled = page.get_by_role("button", name="Run the rules now").is_enabled()
    page.unroute_all(behavior="ignoreErrors")
    page.context.close()
    assert "The last attempt did not finish: OSError: the disk is full." in said
    assert enabled and posts == []


# ----------------------------------------------------------------- the Graph workspace's keys


def test_the_page_keys_stand_aside_while_focus_is_inside_the_graph_workspace(
    browser: Any, served: Served
) -> None:
    page = _open(browser, served, view="explorer")
    page.wait_for_selector(".ix-host button", timeout=90000)
    page.locator(".ix-host button").first.focus()
    inside = page.evaluate("!!document.activeElement.closest('.ix-host')")
    for key in ("2", "5", "R", "/", "?"):
        page.keyboard.press(key)
    # The check is that the page keys did nothing: the page is quiet.
    settled(page)
    active = _active_page(page)
    held = page.evaluate("document.activeElement.id")
    opened = page.evaluate("!!document.querySelector('dialog[open]')")
    still_inside = page.evaluate("!!document.activeElement.closest('.ix-host')")
    # Out of the workspace, the same keys are the page's again.
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("5")
    page.wait_for_function(
        "document.querySelector('#pages button.active').dataset.page === 'history'",
        timeout=HANG_GUARD_MS,
    )
    page.context.close()
    assert inside and still_inside
    assert active == "advanced" and held != "search-box" and not opened


# ------------------------------------------------------------------------- the project's name


def test_the_header_names_the_project_and_rename_changes_the_name_alone(
    browser: Any, census_only: Served
) -> None:
    page = _open(browser, census_only, view="inventory")
    first = page.evaluate(
        "(() => { const t = document.getElementById('project-name-text'); const s = getComputedStyle(t);"
        " return [t.textContent, t.classList.contains('given'), s.color]; })()"
    )
    page.click("#rename-button")
    page.wait_for_selector("dialog[open] input", timeout=10000)
    page.fill("dialog[open] input", "Western blot pool")
    page.click("dialog[open] button[type=submit]")
    page.wait_for_function(
        "document.getElementById('project-name-text').textContent === 'Western blot pool'",
        timeout=HANG_GUARD_MS,
    )
    after = page.evaluate(
        "(() => { const t = document.getElementById('project-name-text'); const s = getComputedStyle(t);"
        " const probe = document.createElement('i');"
        " probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--person');"
        " document.body.append(probe); const person = getComputedStyle(probe).color; probe.remove();"
        " return [t.classList.contains('given'), s.color === person]; })()"
    )
    shell = _get(census_only.address, "/api/v1/shell")
    page.context.close()
    assert first[0] == census_only.project.name and first[1] is False
    assert after == [True, True], "a name a person gave is shown in the person's color"
    assert shell["project_name"] == "Western blot pool" and shell["name_given"] is True
    assert census_only.project.is_dir() and census_only.pool.is_dir(), "only the name changed"


# ----------------------------------------------------------------------------------- accessibility


def _axe_source() -> str | None:
    named = os.environ.get("INGENCE_AXE_JS")
    if named and Path(named).is_file():
        return Path(named).read_text(encoding="utf-8")
    return None


def _violations(page: Any, where: str) -> list[str]:
    found = page.evaluate(
        """async () => { const r = await axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag22aa', 'best-practice']});
          return r.violations.filter(v => ['serious', 'critical'].includes(v.impact))
            .map(v => v.id + ': ' + v.nodes.slice(0, 2).map(n => n.target.join(' ')).join(' | ')); }"""
    )
    return [f"{where}: {each}" for each in found]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_shell_has_no_serious_axe_violation(
    browser: Any, served: Served, census_only: Served, scheme: str
) -> None:
    source = _axe_source()
    if source is None:
        if os.environ.get("INGENCE_BROWSER_REQUIRED") == "1":
            pytest.fail(NO_AXE, pytrace=False)
        pytest.skip(NO_AXE)
    problems: list[str] = []
    for label, project, view in (
        ("the page", served, "years"),
        ("census only", census_only, "sheet"),
    ):
        page = _page(browser, color_scheme=scheme)
        page.goto(f"{project.address}#{view}")
        page.wait_for_selector("#project-name:not([hidden])", timeout=HANG_GUARD_MS)
        page.wait_for_function(
            "document.querySelector('#footer-version').textContent.includes('schema')",
            timeout=HANG_GUARD_MS,
        )
        page.evaluate("document.fonts.ready.then(() => true)")
        if project is served:
            page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
        else:
            page.wait_for_selector("#list .state-card", timeout=HANG_GUARD_MS)
        page.add_script_tag(content=source)
        problems += _violations(page, f"{scheme} {label}")
        if project is served:
            page.click("#report-link")
            page.wait_for_selector("dialog.report-sheet[open] #report-text", timeout=HANG_GUARD_MS)
            problems += _violations(page, f"{scheme} the report sheet")
            page.keyboard.press("Escape")
            page.keyboard.press("?")
            page.wait_for_selector("dialog.shortcuts-sheet[open]", timeout=10000)
            problems += _violations(page, f"{scheme} the shortcuts sheet")
        page.context.close()
    assert problems == [], "\n".join(problems)
