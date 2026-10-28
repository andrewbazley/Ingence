"""Explore > Experiments driven in a real browser.

Skipped where the ``browser`` extra, a browser, scikit-learn or axe-core is missing, as every
browser test is (a failure where ``INGENCE_BROWSER_REQUIRED=1``). Each Dashboard keeps who decides
in a folder of the test's own (``INGENCE_CONFIG_HOME`` is never the person's).

The project is invented: the theme pool of ``fixtures/theme_pool.py`` (three invented people,
six planted lines of work, dated experiment folders and two undated ones: more experiments than a
page lists) with six more experiments of two other invented people beside them, written for what a
row says:

- ``dana/2024-02-05_hela_dapi``: six recordings, a table named for one of them and a table named
  exactly as another (raw files followed by the engine's own pairs, judged and listed under No
  analysis found);
- ``dana/2024-03-11_hek293_gfp``: three recordings and nothing else;
- ``sam/2024-04-02_actin_plan``: a note (no raw file found, nothing to follow);
- ``sam/2024-05-20_tubulin_dmso``: five recordings, each with a table named for it (all followed);
- ``sam/2024-06-01_western_blot_summary``: two tables and no recording;
- ``sam/pbs_untitled``: three recordings in a folder whose name holds no date.

The topics tests make two topics of the person's own over these through the overlay, as the Topics
page does, before the Dashboard starts; every other test asks of the same Dashboard and writes
nothing. The checks are what a person sees: the headings and counts, the filters and the counter,
the sort headers, opening a row's story and By year, the chips and the topic filter, the states,
the width of the table, and axe-core on the tab.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
need("sklearn", "themes need the themes extra")

CENSUS = "run_20260101T000001Z_aaaaaaaaaaaa"
INFER = "run_20260101T000002Z_bbbbbbbbbbbb"
ACTOR = "a. tester"
SAID_AT = "2026-06-01T10:00:00Z"

HELA = "dana/2024-02-05_hela_dapi"
HEK = "dana/2024-03-11_hek293_gfp"
PLAN = "sam/2024-04-02_actin_plan"
TUBULIN = "sam/2024-05-20_tubulin_dmso"
BLOT = "sam/2024-06-01_western_blot_summary"
UNDATED = "sam/pbs_untitled"
EXTRAS = (HELA, HEK, PLAN, TUBULIN, BLOT, UNDATED)
#: The shown name of each (the name without the dates read in it; underscores are spaces).
SHOWN = {
    HELA: "hela dapi",
    HEK: "hek293 gfp",
    PLAN: "actin plan",
    TUBULIN: "tubulin dmso",
    BLOT: "western_blot summary",
    UNDATED: "pbs untitled",
}
FIRST_TOPIC, SECOND_TOPIC = "Cell lines", "Controls"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_extras(root: Path) -> None:
    """The six experiments of two people beside the theme pool's."""
    for number in range(1, 7):
        _write(root / HELA / "raw" / f"hela_dapi_{number:03d}.nd2")
    _write(root / HELA / "analysis" / "hela_dapi_001.csv", "t,x\n1,2\n")
    _write(root / HELA / "analysis" / "Traj_hela_dapi_002.csv", "t,x\n1,2\n")
    _write(root / HELA / "notes.txt", "plain words\n")
    for number in range(1, 4):
        _write(root / HEK / f"hek293_gfp_{number:03d}.nd2")
    _write(root / PLAN / "plan.txt", "plain words\n")
    for number in range(1, 6):
        _write(root / TUBULIN / f"tubulin_dmso_{number:03d}.nd2")
        _write(root / TUBULIN / f"tubulin_dmso_{number:03d}_results.csv", "a,b\n1,2\n")
    _write(root / TUBULIN / "tubulin_dmso_summary.xlsx")
    _write(root / BLOT / "blot_results.csv", "a,b\n1,2\n")
    _write(root / BLOT / "blot_tables.csv", "a,b\n1,2\n")
    for number in range(1, 4):
        _write(root / UNDATED / f"pbs_untitled_{number:03d}.nd2")
    for number in range(1, 3):
        _write(root / UNDATED / f"pbs_untitled_{number:03d}_results.csv", "a,b\n1,2\n")


def build_project(base: Path) -> Path:
    """The theme pool with the extras, read and inferred (a census and an inference, no dive)."""
    from fixtures.revision_4 import write_revision
    from fixtures.theme_pool import ENGINE_SURFACES, write_theme_pool
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "pool"
    pool.mkdir(parents=True)
    write_theme_pool(pool, surfaces=ENGINE_SURFACES)
    write_extras(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id=CENSUS, started_at="t")
    run_inference(project, run_id=INFER, started_at="t")
    return project.path


def build_census_only(base: Path) -> Path:
    """A project that was read and never inferred."""
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    pool = base / "pool"
    pool.mkdir(parents=True)
    write_extras(pool)
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="t")
    return project.path


def _entry_ids(project: Path, *folders: str) -> dict[str, str]:
    from ingence.storage.catalog import open_catalog

    found: dict[str, str] = {}
    with open_catalog(project) as catalog:
        for folder in folders:
            row = catalog.query(
                "SELECT entry_id FROM entries WHERE relative_path = ?", [folder]
            ).fetchone()
            assert row is not None, folder
            found[folder] = str(row[0])
    return found


def make_topics(project: Path, *topics: dict[str, Any]) -> None:
    """Topics of the person's own, written to the Record as the overlay writes them."""
    from ingence.api.routes.overlay import prepare_overlay, write_prepared

    edits = [{"kind": "topic", **topic} for topic in topics]
    prepared = prepare_overlay(project, actor=ACTOR, occurred_at=SAID_AT, edits=edits)
    write_prepared(project, prepared, actor=ACTOR, occurred_at=SAID_AT)


def add_two_topics(project: Path) -> None:
    ids = _entry_ids(project, HELA, HEK, TUBULIN)

    def member(folder: str) -> dict[str, str]:
        return {"kind": "experiment", "id": ids[folder]}

    make_topics(
        project,
        {"name": FIRST_TOPIC, "members": [member(HELA), member(TUBULIN)]},
        {"name": SECOND_TOPIC, "members": [member(HELA), member(HEK)]},
    )


# ---------------------------------------------------------------------------
# The browser and the Dashboards.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The invented project with its two topics made; copied for every Dashboard."""
    project = build_project(tmp_path_factory.mktemp("experiments-built"))
    add_two_topics(project)
    return project


def _serve(built_project: Path, base: Path) -> Iterator[str]:
    copy = base / "project"
    shutil.copytree(built_project, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield address
    finally:
        stop(process)


@pytest.fixture(scope="module")
def served(built: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One Dashboard for every test that only looks."""
    yield from _serve(built, tmp_path_factory.mktemp("experiments-served"))


@pytest.fixture
def served_alone(built: Path, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A Dashboard of its own, for a test that changes the Record. Yields the address and the
    project folder the Dashboard reads."""
    base = tmp_path
    copy = base / "project"
    shutil.copytree(built, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(copy, config_home)
    try:
        yield address, copy
    finally:
        stop(process)


@pytest.fixture(scope="module")
def served_census(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Dashboard on a project that was read and never inferred."""
    base = tmp_path_factory.mktemp("experiments-census")
    project = build_census_only(base)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        yield address
    finally:
        stop(process)


def _page(browser: Any, *, width: int = 1440, height: int = 900, scheme: str = "light") -> Any:
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    return context.new_page()


def _open(page: Any, base: str) -> None:
    """The Experiments tab, drawn: its table is there and the page has asked for nothing more."""
    page.goto(f"{base}#experiments")
    page.wait_for_selector(".exp-table", timeout=HANG_GUARD_MS)
    settled(page)


def _names(page: Any) -> list[str]:
    """The shown names of the rows listed, in order."""
    return list(
        page.eval_on_selector_all(
            "tr.exp-row td.exp-name button.exp-open", "els => els.map(e => e.textContent.trim())"
        )
    )


def _folders(page: Any) -> list[str]:
    return list(
        page.eval_on_selector_all("tr.exp-row p.exp-folder", "els => els.map(e => e.textContent)")
    )


def _counter(page: Any) -> str:
    return str(page.inner_text(".exp-pager span")).strip()


def _row(page: Any, folder: str) -> Any:
    return page.locator("tr.exp-row", has=page.locator(f'p.exp-folder[title="{folder}"]'))


def _choose(page: Any, name: str, label: str | None = None, value: str | None = None) -> None:
    """Choose an option of a filter and wait until the list answers for it."""
    page.select_option(f'select[data-filter="{name}"]', label=label, value=value)
    settled(page)


def _wait_names(page: Any, names: list[str]) -> None:
    """Wait until the rows listed are exactly these, by shown name, in this order."""
    page.wait_for_function(
        "names => JSON.stringify([...document.querySelectorAll("
        "'tr.exp-row td.exp-name button.exp-open')].map(e => e.textContent.trim()))"
        " === JSON.stringify(names)",
        arg=names,
        timeout=HANG_GUARD_MS,
    )
    settled(page)


def _narrow(page: Any, folder: str) -> Any:
    """Narrow the list to one experiment by its folder (the name filter matches folders) and
    return its row. The list holds more than a page, so a row is found this way."""
    page.fill(".exp-search", folder)
    page.wait_for_function(
        "folder => { const rows = [...document.querySelectorAll('tr.exp-row p.exp-folder')];"
        " return rows.length === 1 && rows[0].title === folder; }",
        arg=folder,
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    return _row(page, folder)


def _dates_over_the_pages(page: Any) -> list[tuple[str, bool]]:
    """Each row's date cell as ``(words, is unknown)``, over every page in the order listed. The
    page is left on its first page."""
    cells: list[tuple[str, bool]] = []
    while True:
        cells += [
            (text, bool(unknown))
            for text, unknown in page.eval_on_selector_all(
                "tr.exp-row td.exp-dates",
                "els => els.map(e => [e.textContent.trim(), e.classList.contains('exp-unknown')])",
            )
        ]
        following = page.locator(".exp-pager button", has_text="Next")
        if following.is_disabled():
            break
        before = _counter(page)
        following.click()
        page.wait_for_function(
            "before => document.querySelector('.exp-pager span').textContent.trim() !== before",
            arg=before,
            timeout=HANG_GUARD_MS,
        )
        settled(page)
    while not page.locator(".exp-pager button", has_text="Previous").is_disabled():
        before = _counter(page)
        page.click(".exp-pager button >> text=Previous")
        page.wait_for_function(
            "before => document.querySelector('.exp-pager span').textContent.trim() !== before",
            arg=before,
            timeout=HANG_GUARD_MS,
        )
        settled(page)
    return cells


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _matching(counter: str) -> tuple[int, int]:
    """``(matching, all)`` from "Showing 1 to 50 of 24 matching, of 72 experiments in all." or
    "Showing 1 to 50 of 72 experiments."."""
    found = re.search(r"of ([\d,]+) matching, of ([\d,]+) experiment", counter)
    if found:
        return _number(found.group(1)), _number(found.group(2))
    whole = re.search(r"of ([\d,]+) experiment", counter)
    assert whole, counter
    return _number(whole.group(1)), _number(whole.group(1))


# ---------------------------------------------------------------------------
# 1. The tab's place and its heading.
# ---------------------------------------------------------------------------


def test_experiments_is_the_first_section_of_its_own_page(browser: Any, served: str) -> None:
    # Experiments is a page of its own. Its first section is the table.
    page = _page(browser)
    page.goto(f"{served}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.click('#pages button[data-page="experiments"]')
    page.wait_for_selector(".exp-table", timeout=HANG_GUARD_MS)
    sections = page.eval_on_selector_all(
        'nav.sections[data-sections-of="experiments"] button[data-view]',
        "els => els.map(e => e.dataset.view)",
    )
    assert sections == ["experiments", "topics"], sections
    assert page.url.endswith("#experiments")
    active = page.eval_on_selector(
        "nav.sections button.active", "e => [e.dataset.view, e.textContent.trim()]"
    )
    assert active == ["experiments", "Experiments"]
    assert (page.text_content(".exp-page h2") or "").strip() == "Experiments"
    assert page.eval_on_selector(
        '#pages button[data-page="experiments"]', "e => e.classList.contains('active')"
    )
    page.context.close()


def test_the_headings_counts_equal_years_and_no_analysis_found(browser: Any, served: str) -> None:
    page = _page(browser)
    _open(page, served)
    lead = page.inner_text(".exp-page .ui-lead")
    found = re.match(
        r"([\d,]+) experiments found, holding ([\d,]+) files\. ([\d,]+) files? found lies?", lead
    )
    assert found, lead
    experiments, files = _number(found.group(1)), _number(found.group(2))
    standing = page.inner_text(".exp-page p:has(button:text('See that list'))")
    judged = re.search(
        r"([\d,]+) of ([\d,]+) experiments with enough raw files show no analysis found", standing
    )
    assert judged, standing
    listed, judged_total = _number(judged.group(1)), _number(judged.group(2))
    # The Years view (Overlap's By year mode) counts the same experiments and the same files.
    page.evaluate("location.hash = '#years'")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    assert page.evaluate("window.ingenceYears.load().then((m) => m.exps.length)") == experiments
    assert page.evaluate("window.ingenceYears.load().then((m) => m.files)") == files
    # No analysis found says the same of the same experiments.
    page.click('#pages button[data-page="advanced"]')  # No analysis found is there
    page.click('nav.sections button[data-view="found"]')
    page.wait_for_selector(".ui-lead", timeout=HANG_GUARD_MS)
    header = page.inner_text(".ui-lead")
    said = re.match(r"([\d,]+) of ([\d,]+) experiments? with raw data: no analysis found", header)
    assert said, header
    assert (_number(said.group(1)), _number(said.group(2))) == (listed, judged_total)
    assert listed >= 1, "the invented pool has an experiment on the list"
    page.context.close()


def test_the_pairing_note_says_what_the_engine_assumes_and_links_to_proposed_pairs(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    note = page.inner_text(".exp-assumption")
    assert "Ingence assumes they are right" in note
    for taken in ("suggested pairs", "files made from a raw file", "files named after a raw file"):
        assert taken in note
    assert "this page does not check them" in note
    page.click(".exp-assumption >> text=Open Suggested pairs")
    page.wait_for_function(
        "() => document.querySelector('nav.sections button.active').dataset.view === 'candidates'",
        timeout=HANG_GUARD_MS,
    )
    assert page.url.endswith("#candidates")
    page.context.close()


# ---------------------------------------------------------------------------
# 2. A row.
# ---------------------------------------------------------------------------


def test_a_row_says_how_many_raw_files_are_analyzed_with_its_bar_and_unknown_is_not_zero(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    hela = _narrow(page, HELA)
    cell = hela.locator("td.exp-followed")
    assert cell.locator(".exp-ratio").inner_text() == "1 / 6"
    # The bar: the assumed part has a width, nothing was confirmed by a person.
    widths = cell.locator(".exp-bar").evaluate("bar => [...bar.children].map(c => c.style.width)")
    assert float(widths[0].rstrip("%")) == 0 and float(widths[1].rstrip("%")) > 0
    assert cell.locator(".exp-bar").get_attribute("role") == "img"
    assert cell.locator(".exp-bar").get_attribute("aria-label") == (
        "1 of 6 raw files analyzed: 0 confirmed by you, 1 assumed"
    )
    # The engine's sentence is behind Why, closed until asked; the keyboard opens it.
    said = cell.locator("details.exp-why")
    assert said.count() == 1 and not said.evaluate("d => d.open")
    assert not cell.locator(".exp-said").is_visible()
    said.locator("summary").focus()
    page.keyboard.press("Enter")
    assert said.evaluate("d => d.open")
    assert cell.locator(".exp-said").inner_text() == (
        "1 / 6 raw files analyzed (0 by pairs you confirmed, 1 by Ingence's own pairs and links)."
    )
    # The why and the flag of the state, while this row is the one listed: how it was judged,
    # behind a disclosure so the column stays narrow.
    why = hela.locator("td.exp-state details.exp-why")
    assert why.count() == 1 and not why.evaluate("d => d.open")
    why.locator("summary").click()
    assert "Ingence used the rule of the No analysis found list" in why.inner_text()
    assert hela.locator("td.exp-state .exp-flag").inner_text().strip() == (
        "On the No analysis found list"
    )
    # Every raw file analyzed fills the bar; behind Why, the pairs are linked to Proposed pairs.
    tubulin = _narrow(page, TUBULIN)
    cell = tubulin.locator("td.exp-followed")
    assert cell.locator(".exp-ratio").inner_text() == "5 / 5"
    full = cell.locator(".exp-bar").evaluate("bar => [...bar.children].map(c => c.style.width)")
    assert float(full[1].rstrip("%")) == 100.0
    cell.locator("details.exp-why summary").click()
    assert (
        cell.locator(".exp-said")
        .inner_text()
        .startswith("5 / 5 raw files analyzed (0 by pairs you confirmed, 5 by Ingence's own pairs")
    )
    assert cell.locator(".exp-pairs button").inner_text() == "See the suggested pairs"
    # No raw file found: unknown, drawn as unknown (no bar, no zero, no count).
    plan = _narrow(page, PLAN)
    cell = plan.locator("td.exp-followed")
    assert cell.locator(".exp-none").inner_text().strip() == "No raw files found"
    assert "exp-unknown" in (cell.locator(".exp-none").get_attribute("class") or "")
    assert cell.locator(".exp-bar").count() == 0 and cell.locator(".exp-ratio").count() == 0
    cell.locator("details.exp-why summary").click()
    assert cell.locator(".exp-said").inner_text() == ("No raw files found in this experiment.")
    # An undated experiment says so, in the same drawn unknown.
    undated = _narrow(page, UNDATED)
    assert undated.locator("td.exp-dates").inner_text().strip() == "undated"
    assert "exp-unknown" in (undated.locator("td.exp-dates").get_attribute("class") or "")
    page.context.close()


def test_the_bar_is_the_second_column_with_its_counts_beside_it_and_one_legend_above(
    browser: Any, served: str
) -> None:
    """People read the bar, so it comes first after the name, the full width of its cell and 14
    to 16 px tall, the counts in bold beside it. One legend above the table names its two shades,
    and no row carries one."""
    page = _page(browser)
    _open(page, served)
    heads = page.eval_on_selector_all(
        ".exp-table thead th", "els => els.map(e => e.textContent.trim())"
    )
    assert heads[1] == "Raw files analyzed", heads
    assert not [head for head in heads if "followed" in head.lower()], heads
    seconds = page.eval_on_selector_all(
        "tr.exp-row > td:nth-child(2)", "els => els.map(e => e.className)"
    )
    assert seconds and all("exp-followed" in name for name in seconds), seconds
    tubulin = _narrow(page, TUBULIN)
    measured = tubulin.locator("td.exp-followed").evaluate(
        """cell => {
            const bar = cell.querySelector('.exp-bar').getBoundingClientRect();
            const ratio = cell.querySelector('.exp-ratio');
            const box = ratio.getBoundingClientRect();
            const style = getComputedStyle(cell);
            const inner = cell.getBoundingClientRect().width - parseFloat(style.paddingLeft)
              - parseFloat(style.paddingRight);
            return {height: bar.height, bar: bar.width, ratio: box.width, inner,
              weight: Number(getComputedStyle(ratio).fontWeight), barRight: bar.right,
              ratioLeft: box.left,
              apart: Math.abs((bar.top + bar.bottom) / 2 - (box.top + box.bottom) / 2)};
        }"""
    )
    assert 14 <= measured["height"] <= 16, measured
    assert measured["weight"] >= 700, measured
    # Beside the bar, on its line, and the two fill the cell (a small gap between them).
    assert measured["barRight"] <= measured["ratioLeft"] and measured["apart"] < 4, measured
    assert measured["bar"] + measured["ratio"] >= measured["inner"] - 12, measured
    # One legend, above the table, in the bar's own two shades; none in a row.
    legend = page.locator(".exp-legend")
    assert legend.count() == 1
    assert legend.locator(".exp-key").all_inner_texts() == [
        "Confirmed: a pair you confirmed",
        "Assumed: a pair or link Ingence found",
    ]
    placed = page.evaluate(
        """() => {
            const legend = document.querySelector('.exp-legend').getBoundingClientRect();
            const table = document.querySelector('.exp-tablewrap').getBoundingClientRect();
            const shade = (selector) => getComputedStyle(document.querySelector(selector))
              .backgroundColor;
            return {above: legend.bottom <= table.top,
              confirmed: shade('.exp-swatch.confirmed') === shade('.exp-bar-confirmed'),
              assumed: shade('.exp-swatch.assumed') === shade('.exp-bar-assumed')};
        }"""
    )
    assert placed == {"above": True, "confirmed": True, "assumed": True}, placed
    assert page.locator("tr.exp-row .exp-legend, tr.exp-row .exp-key").count() == 0
    page.context.close()


def test_the_files_disclosure_lists_the_experiments_files_by_stage_and_by_kind_summing_to_it(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    checked = 0
    for folder in (HELA, TUBULIN, HEK, BLOT):
        row = _narrow(page, folder)
        details = row.locator("td.exp-files details")
        summary = details.locator("summary").inner_text()
        files = _number(summary.split()[0])
        details.locator("summary").click()
        assert details.evaluate("d => d.open")
        heads = details.locator(".exp-breakdown-head").all_inner_texts()
        assert len(heads) == 2
        assert heads[0].startswith("By stage") and heads[1].startswith("By kind")
        for head in heads:
            assert head.endswith(f"of {files} files"), (folder, head)
        sums = details.evaluate(
            "d => [...d.querySelectorAll('.exp-breakdown')].map(b =>"
            " [...b.querySelectorAll('li .exp-n')]"
            ".reduce((n, e) => n + Number(e.textContent.replace(/,/g, '')), 0))"
        )
        assert sums == [files, files], (folder, files, sums)
        checked += 1
    assert checked == 4
    # The line under the count says the raw files, analysis files and results tables found.
    assert _narrow(page, TUBULIN).locator("td.exp-files .exp-counts").inner_text() == (
        "5 raw · 6 analysis · 6 results tables"
    )
    page.context.close()


# ---------------------------------------------------------------------------
# 3. Filters, the counter, the no-match card and Clear.
# ---------------------------------------------------------------------------


def test_the_name_filter_narrows_the_list_and_the_counter_says_how_many_of_how_many(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    matching, total = _matching(_counter(page))
    assert matching == total >= 72
    assert len(_names(page)) == 50, "a page lists fifty"
    assert _counter(page).startswith("Showing 1 to 50 of ")
    page.fill(".exp-search", "dapi")
    _wait_names(page, [SHOWN[HELA]])
    assert _counter(page) == f"Showing 1 of 1 matching, of {total} experiments in all."
    # The text matches the folder as well as the name.
    page.fill(".exp-search", "sam/pbs")
    _wait_names(page, [SHOWN[UNDATED]])
    assert _counter(page) == f"Showing 1 of 1 matching, of {total} experiments in all."
    page.context.close()


def test_the_person_and_completeness_filters_keep_what_their_options_count(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    options = page.eval_on_selector_all(
        'select[data-filter="person"] option', "els => els.map(e => e.textContent)"
    )
    assert options[0] == "every area"
    dana = next(option for option in options if option.startswith("dana ("))
    expected = _number(re.search(r"\((\d+)\)", dana).group(1))  # type: ignore[union-attr]
    assert expected == 2
    _choose(page, "person", label=dana)
    assert sorted(_names(page)) == sorted([SHOWN[HELA], SHOWN[HEK]])
    assert _matching(_counter(page))[0] == expected
    _choose(page, "person", value="")
    # Completeness: the option's count is the counter's, and every row carries that state.
    states = page.eval_on_selector_all(
        'select[data-filter="state"] option', "els => els.map(e => [e.value, e.textContent])"
    )
    assert [value for value, _ in states] == ["", "both", "raw_only", "analysis_only", "neither"]
    labels = dict(states)
    assert labels["both"].startswith("Raw and analysis found (")
    assert labels["raw_only"].startswith("Raw found, no analysis found (")
    for value in ("both", "raw_only", "analysis_only", "neither"):
        counted = _number(re.search(r"\(([\d,]+)\)", labels[value]).group(1))  # type: ignore[union-attr]
        _choose(page, "state", value=value)
        if counted:
            assert _matching(_counter(page))[0] == counted, value
            held = page.eval_on_selector_all("tr.exp-row", "rows => rows.map(r => r.dataset.state)")
            assert set(held) == {value}, (value, set(held))
        else:
            assert page.locator("tr.exp-row").count() == 0
    # The judged experiment on the list is "Raw found, no analysis found", never "both".
    _choose(page, "state", value="raw_only")
    page.fill(".exp-search", "dapi")
    _wait_names(page, [SHOWN[HELA]])
    _choose(page, "state", value="both")
    assert page.locator("tr.exp-row").count() == 0
    page.context.close()


def test_the_line_of_work_filter_keeps_the_experiments_of_one_line(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    options = page.eval_on_selector_all(
        'select[data-filter="line"] option', "els => els.map(e => [e.value, e.textContent])"
    )
    assert options[0][1] == "every theme" and len(options) >= 3, options
    value, label = options[1]
    counted = _number(re.search(r"\(([\d,]+)\)\s*$", label).group(1))  # type: ignore[union-attr]
    _choose(page, "line", value=value)
    assert _matching(_counter(page))[0] == counted
    # Every row names that line of work.
    line_label = label.rsplit(" (", 1)[0]
    shown = page.eval_on_selector_all(
        "tr.exp-row td.exp-name p.faint", "els => els.map(e => e.textContent)"
    )
    assert shown and all(f"theme {line_label}" in text for text in shown), (
        line_label,
        shown[:2],
    )
    page.context.close()


def test_a_filter_that_matches_nothing_draws_the_no_match_card_and_clear_brings_the_list_back(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    _, total = _matching(_counter(page))
    page.fill(".exp-search", "zzz-nothing-is-named-this")
    page.wait_for_selector(".exp-results .state-card", timeout=HANG_GUARD_MS)
    card = page.inner_text(".exp-results .state-card")
    assert "No experiment matches these filters." in card
    assert f"{total} experiments were found in all." in card
    assert page.locator("tr.exp-row").count() == 0
    page.click(".exp-results .state-card >> text=Clear the filters")
    page.wait_for_selector(".exp-table", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.input_value(".exp-search") == ""
    assert _matching(_counter(page)) == (total, total)
    # The Clear control above the list does the same from a filtered list.
    _choose(page, "state", value="neither")
    page.click(".exp-clear")
    page.wait_for_function(
        "() => { const s = document.querySelector('select[data-filter=\"state\"]');"
        " return s !== null && s.value === ''; }",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    assert _matching(_counter(page)) == (total, total)
    page.context.close()


def test_the_pager_moves_through_the_pages_and_every_experiment_is_counted(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    _, total = _matching(_counter(page))
    first = _folders(page)
    assert page.locator(".exp-pager button", has_text="Previous").is_disabled()
    page.click(".exp-pager button >> text=Next")
    page.wait_for_function(
        "() => document.querySelector('.exp-pager span').textContent.startsWith('Showing 51 to ')",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    second = _folders(page)
    assert len(first) == 50 and len(first) + len(second) == total
    assert not set(first) & set(second), "no experiment is listed twice"
    assert page.locator(".exp-pager button", has_text="Next").is_disabled()
    page.click(".exp-pager button >> text=Previous")
    page.wait_for_function(
        "() => document.querySelector('.exp-pager span').textContent.startsWith('Showing 1 to ')",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    assert _folders(page) == first
    page.context.close()


# ---------------------------------------------------------------------------
# 4. Sort.
# ---------------------------------------------------------------------------


def test_the_sort_headers_order_the_rows_and_an_undated_experiment_is_last_both_ways(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    assert page.get_attribute('th[aria-sort="ascending"]', "aria-sort") == "ascending"
    assert "Experiment" in (page.text_content('th[aria-sort="ascending"]') or "")
    names = [name.casefold() for name in _names(page)]
    assert names == sorted(names)
    # Dates: dated ones in order, the undated last, whichever way it runs.
    for order in ("ascending", "descending"):
        page.click("button.exp-sort >> text=Dates")
        page.wait_for_function(
            "order => { const th = document.querySelector('th[aria-sort=\"' + order + '\"]');"
            " return th && th.textContent.includes('Dates'); }",
            arg=order,
            timeout=HANG_GUARD_MS,
        )
        settled(page)
        cells = _dates_over_the_pages(page)
        flags = [unknown for _, unknown in cells]
        assert any(flags), "the list holds undated experiments"
        assert flags == sorted(flags), f"an undated experiment is last ({order})"
        assert len(cells) >= 72, "every page was read"
        years = [
            int(found.group(0))
            for text, unknown in cells
            if not unknown
            if (found := re.search(r"\d{4}", text))
        ]
        assert years == sorted(years, reverse=order == "descending"), (order, years)
    # Files: the largest first when chosen, and the select and the header agree.
    page.click("button.exp-sort >> text=/^Files/")
    settled(page)
    assert page.input_value('select[data-filter="sort"]') == "files"
    assert page.input_value('select[data-filter="order"]') == "desc"
    sizes = [
        _number(text.split()[0])
        for text in page.eval_on_selector_all(
            "tr.exp-row td.exp-files summary", "els => els.map(e => e.textContent)"
        )
    ]
    assert sizes == sorted(sizes, reverse=True)
    page.click("button.exp-sort >> text=/^Files/")
    settled(page)
    assert page.input_value('select[data-filter="order"]') == "asc"
    # Completeness: experiments with no raw file found (unknown) come last, whichever way.
    page.click("button.exp-sort >> text=Raw files analyzed")
    settled(page)
    texts = page.eval_on_selector_all(
        "tr.exp-row td.exp-followed",
        "els => els.map(e => e.querySelector('.exp-none') !== null)",
    )
    assert texts == sorted(texts)
    page.click("button.exp-sort >> text=Raw files analyzed")
    settled(page)
    texts = page.eval_on_selector_all(
        "tr.exp-row td.exp-followed",
        "els => els.map(e => e.querySelector('.exp-none') !== null)",
    )
    assert texts == sorted(texts)
    page.context.close()


# ---------------------------------------------------------------------------
# 5. A row opens its story, and from it By year.
# ---------------------------------------------------------------------------


def test_a_row_opens_its_story_then_by_year_at_its_card_and_returning_keeps_the_filters(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    _choose(page, "state", value="raw_only")
    page.fill(".exp-search", "hek")
    _wait_names(page, [SHOWN[HEK]])
    page.click(f'tr.exp-row:has(p.exp-folder[title="{HEK}"]) button.exp-open')
    # The row opens its story under it. By year is one choice from there.
    page.wait_for_selector(".exp-story-row .st-story", timeout=HANG_GUARD_MS)
    page.click(".exp-story-row button[data-help='experiments.by_year']")
    page.wait_for_function(
        "() => document.querySelector('nav.sections button.active').dataset.view === 'overlap'",
        timeout=HANG_GUARD_MS,
    )
    page.wait_for_selector('.ycard[data-card="exp"]', timeout=HANG_GUARD_MS)
    card = page.text_content('.ycard[data-card="exp"]') or ""
    assert "An experiment" in card and SHOWN[HEK] in card
    assert HEK in card, "the card names the folder"
    # Its story is drawn in the view beside it (the scale is the experiment's).
    page.wait_for_selector(".years .ystory .st-story", timeout=HANG_GUARD_MS)
    # Back: the filters are as they were left.
    page.click('#pages button[data-page="experiments"]')
    page.wait_for_selector(".exp-table", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.input_value(".exp-search") == "hek"
    assert page.input_value('select[data-filter="state"]') == "raw_only"
    assert _names(page) == [SHOWN[HEK]]
    page.context.close()


# ---------------------------------------------------------------------------
# 6. Topics.
# ---------------------------------------------------------------------------


def test_the_topics_column_draws_a_chip_for_each_topic_and_says_none_where_there_is_none(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    plan = _narrow(page, PLAN).locator("td.exp-topics")
    assert plan.inner_text().strip() == "none"
    assert plan.locator(".exp-chip").count() == 0
    hek = _narrow(page, HEK)
    assert hek.locator("td.exp-topics .exp-chip").all_inner_texts() == [SECOND_TOPIC]
    tubulin = _narrow(page, TUBULIN)
    assert tubulin.locator("td.exp-topics .exp-chip").all_inner_texts() == [FIRST_TOPIC]
    hela = _narrow(page, HELA)
    assert hela.locator("td.exp-topics .exp-chip").all_inner_texts() == [FIRST_TOPIC, SECOND_TOPIC]
    # A chip is the person's own, in the person's color (magenta), as the page's token says.
    chip = hela.locator("td.exp-topics .exp-chip").first
    color = chip.evaluate("e => getComputedStyle(e).color")
    token = page.evaluate(
        "() => { const probe = document.createElement('span');"
        " probe.style.color = 'var(--person)'; document.body.append(probe);"
        " const c = getComputedStyle(probe).color; probe.remove(); return c; }"
    )
    assert color == token
    options = page.eval_on_selector_all(
        'select[data-filter="topic"] option', "els => els.map(e => e.textContent)"
    )
    assert options == ["every collection", f"{FIRST_TOPIC} (2)", f"{SECOND_TOPIC} (2)"]
    page.context.close()


def test_a_topic_chip_filters_to_its_topic_and_the_topic_filter_gives_the_same(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    _open(page, served)
    _, total = _matching(_counter(page))
    hela = _narrow(page, HELA)
    hela.locator(".exp-chip", has_text=FIRST_TOPIC).click()
    page.wait_for_function(
        "() => document.querySelector('select[data-filter=\"topic\"]').value !== ''",
        timeout=HANG_GUARD_MS,
    )
    # The chip filters; the name filter that found the row is cleared to see the whole topic.
    page.fill(".exp-search", "")
    _wait_names(page, [SHOWN[HELA], SHOWN[TUBULIN]])
    assert _counter(page) == f"Showing 1 to 2 of 2 matching, of {total} experiments in all."
    assert (
        page.eval_on_selector(
            'select[data-filter="topic"]', "e => e.options[e.selectedIndex].textContent"
        )
        == f"{FIRST_TOPIC} (2)"
    )
    # Every row listed carries the chip.
    for texts in page.eval_on_selector_all(
        "tr.exp-row td.exp-topics",
        "els => els.map(e => [...e.querySelectorAll('.exp-chip')].map(c => c.textContent))",
    ):
        assert FIRST_TOPIC in texts
    # The filter, chosen by hand, keeps the other topic's experiments.
    _choose(page, "topic", label=f"{SECOND_TOPIC} (2)")
    assert sorted(_names(page)) == sorted([SHOWN[HELA], SHOWN[HEK]])
    # It combines with the other filters: the topic's experiments that are raw only.
    _choose(page, "state", value="raw_only")
    assert sorted(_names(page)) == sorted([SHOWN[HELA], SHOWN[HEK]])
    _choose(page, "state", value="both")
    assert page.locator("tr.exp-row").count() == 0
    card = page.inner_text(".exp-results .state-card")
    assert "No experiment matches these filters." in card
    # The sentence the engine served for the topic is on the card.
    assert f"“{SECOND_TOPIC}”: 2 of " in card
    page.context.close()


def test_a_topic_deleted_while_the_filter_was_kept_says_it_no_longer_stands(
    browser: Any, served_alone: tuple[str, Path]
) -> None:
    served, project = served_alone
    page = _page(browser)
    _open(page, served)
    _choose(page, "topic", label=f"{FIRST_TOPIC} (2)")
    assert sorted(_names(page)) == sorted([SHOWN[HELA], SHOWN[TUBULIN]])
    topic_id = page.input_value('select[data-filter="topic"]')
    # The person leaves for another section and deletes the topic (the overlay's own edit).
    page.evaluate("location.hash = '#years'")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    make_topics(project, {"id": topic_id, "remove": True})
    page.click('#pages button[data-page="experiments"]')
    page.wait_for_selector(".exp-filters", timeout=HANG_GUARD_MS)
    page.wait_for_selector(".exp-results .state-card", timeout=HANG_GUARD_MS)
    card = page.inner_text(".exp-results .state-card")
    assert "This collection no longer exists." in card
    assert "Someone may have deleted it or merged it with another." in card
    # The tab is not stuck on an error: the filters are there, the filter shows what it holds, and
    # choosing every topic lists the experiments again.
    assert page.locator(".exp-filters").count() == 1
    assert (
        page.eval_on_selector(
            'select[data-filter="topic"]', "e => e.options[e.selectedIndex].textContent"
        )
        == "a collection that no longer exists"
    )
    _choose(page, "topic", value="")
    assert page.locator("tr.exp-row").count() >= 1
    # The other topic still stands.
    options = page.eval_on_selector_all(
        'select[data-filter="topic"] option', "els => els.map(e => e.textContent)"
    )
    assert options == ["every collection", f"{SECOND_TOPIC} (2)"]
    page.context.close()


# ---------------------------------------------------------------------------
# 7. States.
# ---------------------------------------------------------------------------


def test_a_project_read_and_never_inferred_draws_the_census_only_state(
    browser: Any, served_census: str
) -> None:
    page = _page(browser)
    page.goto(f"{served_census}#experiments")
    page.wait_for_selector("#list .state-card", timeout=HANG_GUARD_MS)
    card = page.inner_text("#list .state-card")
    assert "Files listed. The rules have not run yet." in card
    assert "no rule has run" in card.lower() or "No rule has run" in card
    assert "Run the rules now" in card
    assert page.locator(".exp-table").count() == 0
    assert page.get_attribute("#list .state-card", "data-state") == "census"
    page.context.close()


def test_a_failed_read_draws_the_error_state_and_try_again_reads_it_whole(
    browser: Any, served: str
) -> None:
    page = _page(browser)
    failing = {"on": True}

    def answer(route: Any) -> None:
        if failing["on"]:
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps(
                    {"error": {"code": "server_error", "message": "an invented failure"}}
                ),
            )
        else:
            route.continue_()

    page.route("**/api/v1/experiments?*", answer)
    page.goto(f"{served}#experiments")
    page.wait_for_selector("#list .state-failed", timeout=HANG_GUARD_MS)
    card = page.inner_text("#list .state-failed")
    assert "Ingence could not answer." in card
    assert "GET /api/v1/experiments returned 500" in card
    assert "an invented failure" in card
    assert "The scan is unchanged" in card
    assert page.locator(".exp-table").count() == 0
    failing["on"] = False
    page.click("#list .state-failed >> text=Try again")
    page.wait_for_selector(".exp-table", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.locator("tr.exp-row").count() == 50
    page.context.close()


# ---------------------------------------------------------------------------
# 8. The table's width.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280])
def test_the_table_has_no_sideways_overflow(browser: Any, served: str, width: int) -> None:
    """Every column fits: the wrapper does not scroll sideways and neither does the page, with a
    row's explanation and a files disclosure open, at the two widths a laboratory's screens have."""
    page = _page(browser, width=width, height=900)
    _open(page, served)
    page.locator("tr.exp-row td.exp-state details.exp-why summary").first.click()
    page.locator("tr.exp-row td.exp-followed details.exp-why summary").first.click()
    page.locator("tr.exp-row td.exp-files details summary").first.click()
    measured = page.evaluate(
        "() => { const wrap = document.querySelector('.exp-tablewrap');"
        " const table = wrap.querySelector('table');"
        " const last = wrap.querySelector('thead th:last-child').getBoundingClientRect();"
        " return {wrapScroll: wrap.scrollWidth, wrapClient: wrap.clientWidth,"
        " tableWidth: table.getBoundingClientRect().width, lastRight: last.right,"
        " wrapRight: wrap.getBoundingClientRect().right,"
        " pageScroll: document.documentElement.scrollWidth, inner: window.innerWidth}; }"
    )
    assert measured["wrapScroll"] <= measured["wrapClient"], measured
    assert measured["lastRight"] <= measured["wrapRight"] + 1, measured
    assert measured["pageScroll"] <= measured["inner"], measured
    page.context.close()


# ---------------------------------------------------------------------------
# 9. axe-core.
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


def _violations(page: Any, where: str) -> list[str]:
    """What axe finds on the page as it stands, serious or worse, in words."""
    script = _axe_script()
    if script is None:
        pytest.fail("axe-core is not installed: set INGENCE_AXE_JS", pytrace=False)
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.add_script_tag(content=script.read_text(encoding="utf-8"))
    found = page.evaluate(
        "options => window.axe.run(document, options)",
        {
            "runOnly": {
                "type": "tag",
                "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
            }
        },
    )
    said: list[str] = []
    for violation in found["violations"]:
        if violation["impact"] not in ("critical", "serious"):
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
def test_the_tab_has_no_serious_axe_violation(browser: Any, served: str, scheme: str) -> None:
    """The table with its chips, a files disclosure and a Why disclosure open; the no-match card;
    and the page narrower than a table needs."""
    page = _page(browser, scheme=scheme)
    _open(page, served)
    page.locator("tr.exp-row td.exp-files details summary").first.click()
    page.locator("tr.exp-row td.exp-state details.exp-why summary").first.click()
    page.locator("tr.exp-row td.exp-followed details.exp-why summary").first.click()
    problems = _violations(page, f"{scheme} the table")
    page.fill(".exp-search", "zzz-nothing-is-named-this")
    page.wait_for_selector(".exp-results .state-card", timeout=HANG_GUARD_MS)
    settled(page)
    problems += _violations(page, f"{scheme} the no-match card")
    page.click(".exp-results .state-card >> text=Clear the filters")
    page.wait_for_selector(".exp-table", timeout=HANG_GUARD_MS)
    page.set_viewport_size({"width": 800, "height": 900})
    settled(page)
    problems += _violations(page, f"{scheme} the table at 800 px")
    page.context.close()
    assert problems == [], "\n".join(problems)
