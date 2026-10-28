"""The landing page driven in a real browser.

The tests run on a server started with no project, as ``ingence see`` starts one where none is
found, and on one started with a project that has not been read:

1. The first screen: two columns from 900 pixels, one below; the landing paragraph word for word;
   the header is the brand (the mark and "Ingence"), the version chip and a Documentation link to
   ingence.org, which is a link and never a fetch.
2. Each project a card, newest first, its name (or its folder's, "named after its folder" with
   Rename), a mono line of what it reads and where it is written, a line of runs and dates,
   Open; the count in the head; Rename changes the title and nothing else. No name is asked on
   this page (the Dashboard asks it at the first answer).
3. The card "Scan a data folder": Check, its note, name, the years, where saved, the reading depth
   (Quick, Standard by default, Deep) and the questions box, then one button: Start scan makes
   the project, writes the years to its settings file, and starts the census, in one call.
4. The page while it scans: four counters, in mono at 24 px, never a percentage; "Scanning <root>
   since <time>"; the Dashboard opens by itself at the end, at the years.
5. A machine with no project shows the paragraph and the card, and no section more.
6. A folder that cannot be read says why with a red dot and "Read it now" is off; a project
   already written at the place offers "Open it instead"; nothing is written.

And the keyboard path: Enter on Check checks; the fields run top to bottom; Enter on Start scan
scans.

Skipped where the ``browser`` extra or a browser is missing, as every browser test is; where
``INGENCE_BROWSER_REQUIRED`` is set each is a failure instead. Who decides is kept in a folder of
the test's own (``INGENCE_CONFIG_HOME``). Every name is invented.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from fixtures.dashboard_browser import (
    HANG_GUARD_MS,
    REPOSITORY,
    REQUIRED,
    launch_browser,
    need,
    start_dashboard,
    stop,
)
from ingence import __version__
from ingence.api.recent import remember_project
from ingence.api.routes import landing

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

#: The landing page's paragraph, word for word.
#: \u2019 is the apostrophe it is written with.
OPENING_PARAGRAPH = (
    "Ingence is a read-only indexing and inference engine for scientific filesystems, with a "
    "local Dashboard for exploring them. It reads files\u2019 names, metadata and contents where "
    "they already sit, and interprets them through two editable, versioned sources: a "
    "Dictionary, which defines what the words in names and files mean, and a Rulebook, the "
    "engine\u2019s interpretive logic, which turns that evidence into proposals about how files "
    "relate. The Dashboard lets you search and browse what is there, what could not be read, and "
    "what the engine concluded and why; where the rules cannot decide, it asks you."
)

#: The dash a figure not yet known is drawn as (never a 0).
DASH = "\u2013"

READING = 180_000


def _shots(page: Any, name: str) -> None:
    """Keep a screenshot where ``INGENCE_BROWSER_SCREENSHOTS`` names a folder (invented names)."""
    folder = os.environ.get("INGENCE_BROWSER_SCREENSHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _pool(base: Path) -> Path:
    pool = base / "imaging-drive"
    for folder in ("2024-03-05_HeLa", "2024-03-06_HeLa", "2024-03-07_HEK293"):
        (pool / folder).mkdir(parents=True)
        for index in (1, 2, 3):
            (pool / folder / f"DAPI_actin_{index:02d}.csv").write_text(f"a\n{index}\n")
    (pool / "western blot notes.txt").write_text("DMSO control\n")
    return pool


@pytest.fixture
def landed(tmp_path: Path) -> Iterator[tuple[Path, Path, Path, str]]:
    """A server with no project, in a process of its own: the temporary folder, the invented pool,
    the configuration folder (who decides, the list of projects) and the page's address."""
    pool = _pool(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    process, address = start_dashboard(None, config)
    try:
        yield tmp_path, pool, config, address
    finally:
        stop(process)


def _page(browser: Any, width: int = 1280, scheme: str = "light") -> Any:
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.problems = problems
    return page


def _fill_the_card(page: Any, pool: Path, where: Path, **given: Any) -> None:
    """Type what a person types: the folder, checked, then the rest."""
    page.fill("#ui-root", str(pool))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    if given.get("name"):
        page.fill("#ui-name", given["name"])
    page.fill("#ui-where", str(where))
    # A change event, as a person leaving the field sends: the page then asks where it would go.
    page.dispatch_event("#ui-where", "change")
    if given.get("years"):
        page.fill("#ui-first-year", str(given["years"][0]))
        page.fill("#ui-last-year", str(given["years"][1]))
    if given.get("depth"):
        page.check(f"#ui-depth-{given['depth']}")


# -- the first screen ----------------------------------------------------------------------------


def test_two_columns_from_900_one_below_and_the_opening_paragraph_is_word_for_word(
    browser, landed
) -> None:
    _, _, _, url = landed
    page = _page(browser, 1280)
    seen: list[str] = []
    page.on("request", lambda request: seen.append(request.url))
    page.goto(url)
    page.wait_for_selector("#ui-root")

    def columns() -> tuple[dict[str, float], dict[str, float]]:
        return page.locator(".ui-left").bounding_box(), page.locator(".ui-right").bounding_box()

    left, right = columns()
    assert right["x"] > left["x"] + left["width"], "at 1280 the card stands beside the paragraph"
    assert abs(right["y"] - left["y"]) < 4, "and level with it"
    _shots(page, "landing-01-1280")
    for width, two in ((900, True), (899, False), (800, False)):
        page.set_viewport_size({"width": width, "height": 900})
        left, right = columns()
        assert (right["x"] > left["x"] + left["width"] - 1) is two, f"{width} wide"
        if not two:
            assert right["y"] >= left["y"] + left["height"] - 1, f"{width}: one under the other"
            assert abs(right["x"] - left["x"]) < 2
        # Nothing runs off the side at any of the widths.
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    _shots(page, "landing-02-800")

    # The paragraph, word for word.
    assert page.inner_text(".ui-lead").replace("\n", " ") == OPENING_PARAGRAPH
    # The header: the mark and "Ingence", the version, and a link to ingence.org.
    assert page.inner_text("h1.ui-brand").strip() == "Ingence"
    assert page.locator("h1.ui-brand svg.ui-mark").count() == 1
    assert page.inner_text("#ui-version") == __version__
    docs = page.locator("#docs-link")
    assert docs.inner_text() == "Documentation"
    assert docs.get_attribute("href") == "https://ingence.org"
    # The mark follows the page's colors: its lines the ink, its made part the accent.
    colors = page.evaluate(
        """() => {
          const style = (selector, property) =>
            getComputedStyle(document.querySelector(selector))[property];
          return {
            ink: style('body', 'color'), files: style('.ui-mark .files', 'stroke'),
            accent: style('a', 'color'), made: style('.ui-mark .made', 'fill'),
          };
        }"""
    )
    assert colors["files"] == colors["ink"]
    assert colors["made"] == colors["accent"]
    # A link, never a fetch: no request left this computer, and no request went to ingence.org.
    assert all(address.startswith(url) for address in seen), seen
    assert page.problems == []
    page.context.close()


def test_the_page_follows_a_dark_system_and_has_no_switch_of_its_own(browser, landed) -> None:
    _, _, _, url = landed
    light, dark = _page(browser, 1280, "light"), _page(browser, 1280, "dark")
    try:
        for page in (light, dark):
            page.goto(url)
            page.wait_for_selector("#ui-root")
        ground = {
            "light": light.evaluate("getComputedStyle(document.body).backgroundColor"),
            "dark": dark.evaluate("getComputedStyle(document.body).backgroundColor"),
        }
        assert ground["light"] != ground["dark"]
        assert (
            light.locator("[role=switch]:not(.ui-switch), header [aria-label*=theme i]").count()
            == 0
        )
        _shots(dark, "landing-03-dark")
    finally:
        light.context.close()
        dark.context.close()


# -- no project ----------------------------------------------------------------------------------


def test_a_fresh_machine_shows_the_paragraph_and_the_card_and_nothing_more(browser, landed) -> None:
    _, _, _, url = landed
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-root")
    assert page.locator(".ui-lead").count() == 1
    assert page.locator("section.ui-form").count() == 1
    assert page.locator(".ui-recent, .ui-projects, .ui-deciding").count() == 0
    assert "Projects on this computer" not in page.inner_text("main")
    assert "No name is set" not in page.inner_text("main")
    # No name is asked here. The reading depth is, Standard chosen.
    assert page.locator("#ui-who").count() == 0
    assert page.is_checked("#ui-depth-standard")
    assert page.is_disabled("#ui-read"), "nothing to read until a folder is named"
    page.context.close()


# -- project cards -------------------------------------------------------------------------------


def test_projects_are_cards_newest_first_and_renaming_changes_the_title_and_nothing_else(
    browser, landed
) -> None:
    base, pool, config, url = landed
    named = landing.create_project(
        {
            "root": str(pool),
            "name": "Whitfield imaging",
            "project": str(base / "projects" / "whitfield"),
        },
        created_at="2026-10-01T10:00:00Z",
    )
    unnamed = landing.create_project(
        {"root": str(pool), "project": str(base / "projects" / "okafor")},
        created_at="2026-10-02T10:00:00Z",
    )
    remember_project(named, opened_at="2026-10-12T16:00:00Z", name=None, config_home=config)
    remember_project(unnamed, opened_at="2026-10-13T16:00:00Z", name=None, config_home=config)
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector(".ui-projects .ui-project")
    cards = page.locator(".ui-projects .ui-project")
    assert cards.count() == 2
    # The most recently opened first; the count in the section's head.
    assert [card.get_attribute("data-project") for card in cards.all()] == [
        str(unnamed.resolve()),
        str(named.resolve()),
    ]
    assert page.inner_text(".ui-section-head .ui-count") == "2 of 2"
    assert page.text_content(".ui-section-head h2") == "Projects on this computer"
    first, second = cards.nth(0), cards.nth(1)
    # A project with a name shows it; one without shows its folder's, and says so, with Rename.
    assert first.locator("h3").inner_text().startswith("okafor")
    assert "named after its folder" in first.locator("h3").inner_text()
    assert first.locator(".ui-rename-open").count() == 1
    assert second.locator("h3").inner_text().startswith("Whitfield imaging")
    assert "named after its folder" not in second.locator("h3").inner_text()
    assert second.locator(".ui-rename-open").count() == 0, (
        "Rename is for a project its folder names"
    )
    # Open stands at the left of the card, level with its middle.
    for card in (first, second):
        opener, card_box = card.locator(".ui-open").bounding_box(), card.bounding_box()
        title = card.locator("h3").bounding_box()
        assert opener["x"] < title["x"], "Open is left of the name"
        assert opener["x"] - card_box["x"] < 24, "and at the card's left edge"
        middle = opener["y"] + opener["height"] / 2
        assert abs(middle - (card_box["y"] + card_box["height"] / 2)) < 8, "level with its middle"
    # One mono line of what it reads and where it is written; one line of its runs and dates.
    reads = first.locator(".ui-reads")
    assert reads.inner_text().startswith(f"scans {pool.resolve()} · saved in ")
    assert str(unnamed.resolve()) in reads.inner_text()
    assert page.evaluate(
        "(node) => getComputedStyle(node).fontFamily", reads.element_handle()
    ).startswith('"IBM Plex Mono"')
    assert re.fullmatch(
        r"Not scanned yet · opened [A-Z][a-z]+ \d+(, \d{4})?",
        first.locator(".ui-runs").inner_text(),
    )
    assert first.locator(".ui-open").inner_text() == "Open"
    # No name is asked on this page: the Dashboard asks it at the first answer.
    assert page.locator(".ui-deciding").count() == 0
    assert "Set your name" not in page.inner_text("main")
    _shots(page, "landing-04-projects")

    before = {
        "reads": first.locator(".ui-reads").inner_text(),
        "runs": first.locator(".ui-runs").inner_text(),
        "open": first.locator(".ui-open").inner_text(),
        "second": second.locator("h3").inner_text(),
    }
    first.locator(".ui-rename-open").click()
    page.fill(".ui-project[data-project] .ui-rename-name >> visible=true", "Western blots")
    page.click(".ui-rename-save >> visible=true")
    page.wait_for_function(
        "document.querySelector('.ui-projects .ui-project h3')"
        ".innerText.startsWith('Western blots')"
    )
    renamed = page.locator(".ui-projects .ui-project").nth(0)
    assert "named after its folder" not in renamed.locator("h3").inner_text()
    assert renamed.locator(".ui-rename-open").count() == 0, "named now, so no Rename"
    assert renamed.locator(".ui-reads").inner_text() == before["reads"]
    assert renamed.locator(".ui-runs").inner_text() == before["runs"]
    assert renamed.locator(".ui-open").inner_text() == before["open"]
    assert (
        page.locator(".ui-projects .ui-project").nth(1).locator("h3").inner_text()
        == before["second"]
    )
    assert json.loads((unnamed / "project.json").read_text())["name"] == "Western blots"
    assert unnamed.name == "okafor", "the folder keeps its name"
    assert not (config / "decider.json").exists(), "no name was asked"
    assert page.problems == []
    page.context.close()


# -- scan a data folder, and the scan ------------------------------------------------------------


def test_read_it_now_makes_writes_three_things_follows_the_reading_and_opens_the_dashboard(
    browser, landed
) -> None:
    base, pool, config, url = landed
    where = base / "projects" / "hela-imaging"
    page = _page(browser)
    requests: list[tuple[str, str]] = []
    page.on("request", lambda r: requests.append((r.method, r.url.removeprefix(url))))
    page.goto(url)
    page.wait_for_selector("#ui-root")
    _fill_the_card(
        page,
        pool,
        where,
        name="HeLa imaging",
        years=(2000, 2020),
    )
    # The Check result as a note: a green dot, "Readable.", the folders at the top, and that the
    # files are not counted until the reading runs.
    note = page.inner_text("#ui-check .ui-note")
    assert note.startswith("Readable. 3 folders at the top. Ingence reads each one as an area.")
    assert "Ingence counts the files during the scan." in note
    assert page.locator("#ui-check .ui-dot.ui-ok").count() == 1
    # The card's parts.
    assert page.inner_text("section.ui-form h2") == "Scan a data folder"
    # The reading depth: three choices with one line each, Standard chosen.
    assert page.is_checked("#ui-depth-standard")
    assert page.inner_text("#ui-depth legend") == "Reading depth"
    for value, line in (
        ("quick", "Reads names, sizes and dates."),
        ("standard", "Also reads a sample of each file type\u2019s contents."),
        ("deep", "Reads every file whole, in the background, with a time limit you set."),
    ):
        assert page.inner_text(f"#ui-depth-{value}-help") == line
    assert page.inner_text("main").count("Ingence saves the project in this folder.") == 1
    page.check("#ui-depth-deep")
    assert not (where / "project.json").exists(), "nothing is made before the click"
    _shots(page, "landing-05-the-card")

    # One click: the project is made, the three land, and the census starts, in one call.
    with page.expect_response(lambda r: r.url.endswith("/api/landing/create")) as made:
        page.click("#ui-read")
    sent = json.loads(made.value.request.post_data)
    assert sent["years"] == [2000, 2020]
    # Deep opens files: the route takes the switch, and the depth rides along.
    assert sent["dive"] == {"enabled": True}
    assert sent["depth"] == "deep"
    assert "who" not in sent
    assert sent["read"] is True
    assert made.value.status == 201
    written = yaml.safe_load((where / "settings.yaml").read_text(encoding="utf-8"))
    assert written["dictionary"]["years"] == [2000, 2020]
    assert "dive" not in written, "opening files is kept in no settings file"
    assert not (config / "decider.json").exists(), "no name is asked on this page"
    assert json.loads((where / "project.json").read_text())["name"] == "HeLa imaging"
    # Exactly one write made the project and started the scan: no second call asked for it.
    posts = [path for method, path in requests if method == "POST"]
    assert posts == ["api/landing/create"], posts

    # While it scans: four counters in one row across the page, never a percentage. The
    # reading starts with the click (the worker needs seconds to start, and the page draws the
    # counters at once), so these are asserted always, never only when the page happened to be seen.
    page.wait_for_selector("#ui-counters .ui-counter", timeout=READING)
    reading = page.evaluate(
        """() => {
          const row = document.querySelector('#ui-counters');
          const card = document.querySelector('#ui-progress');
          const figure = getComputedStyle(row.querySelector('.ui-figure'));
          const cells = [...row.querySelectorAll('.ui-counter')]
            .map((n) => n.getBoundingClientRect());
          const main = document.querySelector('main').getBoundingClientRect();
          return {
            titles: [...row.querySelectorAll('.ui-counter .eyebrow')].map((n) => n.textContent),
            size: figure.fontSize, family: figure.fontFamily,
            text: row.innerText + ' ' + card.innerText,
            line: card.querySelector('.ui-reading-line').innerText,
            tops: cells.map((c) => Math.round(c.top)),
            across: cells[cells.length - 1].right - cells[0].left, wide: main.width,
          };
        }"""
    )
    assert reading["titles"] == ["Folders found", "Files found", "Total size", "Rules applied"]
    assert reading["size"] == "24px"
    assert reading["family"].startswith('"IBM Plex Mono"')
    assert "%" not in reading["text"]
    assert re.match(r"Scanning .*imaging-drive since \d{1,2}:\d{2} [AP]M", reading["line"]), reading
    assert len(set(reading["tops"])) == 1, f"the four stand level: {reading['tops']}"
    assert reading["across"] > 0.8 * reading["wide"], "and run across the page"
    _shots(page, "landing-06-while-it-reads")
    page.wait_for_selector("#pages", timeout=READING)
    assert page.url.endswith("#overview"), page.url
    assert (where / "CURRENT").exists()
    # The switch rode the request: the job that read the folder had a dive in it.
    job = page.evaluate("fetch('/api/landing/progress').then((r) => r.json())")
    assert job["operations"] == ["census", "dive", "inference"], job
    # The engine's own counts, nothing computed on the page: the census saw the folder it read and
    # the three in it, and the seven files.
    page.goto(url + "landing")
    page.wait_for_selector(".ui-projects .ui-project")
    card = page.locator(".ui-projects .ui-project").first
    assert card.locator("h3").inner_text().startswith("HeLa imaging")
    assert re.match(
        r"\d+ scans? · the last on [A-Z][a-z]+ \d+ at \d+:\d\d [AP]M",
        card.locator(".ui-runs").inner_text(),
    )
    # Open, at the right of the card, goes to its Dashboard.
    card.locator(".ui-open").click()
    page.wait_for_selector("#pages", timeout=READING)
    assert page.problems == []
    page.context.close()


def test_the_counters_grow_and_never_show_a_share(browser, landed) -> None:
    """The page's four figures from the engine's own, fed to it one second at a time: they grow,
    a count not yet known is a dash and never a 0, "Rules run" says "n of N" once inference knows
    its whole, and no percent appears at any point."""
    base, pool, _, url = landed
    project = landing.create_project(
        {"root": str(pool), "name": "Counting", "project": str(base / "counting")},
        created_at="2026-10-13T10:00:00Z",
    )
    steps = [
        {"phase": "census", "seen": None, "counters": {}},
        {
            "phase": "census",
            "counters": {"folders_found": 38, "files_found": 214, "bytes_seen": 2_048},
        },
        {
            "phase": "census",
            "counters": {"folders_found": 417, "files_found": 3_470, "bytes_seen": 2_470_000_000},
        },
        {
            "phase": "inferring",
            "counters": {
                "folders_found": 417,
                "files_found": 3_470,
                "bytes_seen": 2_470_000_000,
                "rules_run": 21,
                "rules_total": 48,
            },
        },
    ]
    state = {"at": 0}

    def progress(route: Any) -> None:
        step = steps[min(state["at"], len(steps) - 1)]
        state["at"] += 1
        route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "kind": "first_run",
                    "state": "running",
                    "operations": ["census", "inference"],
                    "started_at": "2026-10-13T19:07:00Z",
                    "job_seconds": 5,
                    **step,
                }
            ),
        )

    page = _page(browser)
    page.route("**/api/landing/progress", progress)
    page.route(
        "**/api/landing/state",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "version": __version__,
                    "recent": [],
                    "projects_parent": str(base),
                    "default_years": [1990, "present"],
                    "this_year": 2026,
                    "project": {
                        "path": str(project),
                        "name": "Counting",
                        "exists": True,
                        "roots": [{"path": str(pool)}],
                        "has_run": False,
                        "years": [1990, "present"],
                    },
                    "job": {
                        "kind": "first_run",
                        "state": "running",
                        "phase": "census",
                        "operations": ["census", "inference"],
                        "started_at": "2026-10-13T19:07:00Z",
                        "counters": {},
                    },
                }
            ),
        ),
    )
    page.goto(url)
    page.wait_for_selector("#ui-counters .ui-counter")

    def figures() -> dict[str, str]:
        return {
            name: page.inner_text(f"[data-counter={name}] .ui-figure")
            for name in ("folders_found", "files_found", "bytes_seen", "rules_run")
        }

    first = figures()
    assert first == dict.fromkeys(("folders_found", "files_found", "bytes_seen", "rules_run"), DASH)
    assert "after the file list" in page.inner_text("[data-counter=rules_run]")
    page.wait_for_function(
        "document.querySelector('[data-counter=files_found] .ui-figure').innerText === '214'"
    )
    middle = figures()
    assert middle["folders_found"] == "38"
    assert middle["bytes_seen"] == "2.0 kB"
    page.wait_for_function(
        "document.querySelector('[data-counter=files_found] .ui-figure').innerText === '3,470'"
    )
    page.wait_for_function(
        "document.querySelector('[data-counter=rules_run] .ui-figure').innerText.startsWith('21')"
    )
    last = figures()
    assert last["folders_found"] == "417"
    assert last["bytes_seen"] == "2.5 GB"
    assert re.fullmatch(r"21\s+of 48", last["rules_run"])
    assert "after the file list" not in page.inner_text("[data-counter=rules_run]")
    # Never a percent, never a bar.
    assert "%" not in page.inner_text("#ui-progress")
    assert page.locator("progress, meter, [role=progressbar]").count() == 0
    assert "Ingence knows the total at the end" not in page.inner_text("#ui-progress"), (
        "said while the census runs, not after it"
    )
    page.context.close()


# -- a folder that cannot be read ----------------------------------------------------------------


def test_a_folder_that_cannot_be_read_says_why_with_a_red_dot_and_writes_nothing(
    browser, landed
) -> None:
    base, pool, config, url = landed
    before = sorted(path.name for path in base.iterdir())
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-root")

    # A file where a folder belongs.
    page.fill("#ui-root", str(pool / "western blot notes.txt"))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-problem")
    said = page.inner_text("#ui-check")
    assert said == "Not readable: this is a file. Choose a data folder."
    assert page.is_disabled("#ui-read")
    # The dot is red (the page's problem color), beside the words, which are not red.
    colors = page.evaluate(
        """() => {
          const dot = getComputedStyle(document.querySelector('#ui-check .ui-dot'));
          const root = getComputedStyle(document.documentElement);
          const words = getComputedStyle(document.querySelector('#ui-check .ui-problem p'));
          const swatch = document.createElement('span');
          swatch.style.color = root.getPropertyValue('--problem');
          document.body.append(swatch);
          const problem = getComputedStyle(swatch).color;
          swatch.remove();
          return {dot: dot.backgroundColor, problem, words: words.color};
        }"""
    )
    assert colors["dot"] == colors["problem"]
    assert colors["words"] != colors["problem"]
    _shots(page, "landing-07-not-readable")

    # A folder that is not there.
    page.fill("#ui-root", str(base / "nowhere"))
    page.press("#ui-root", "Enter")
    page.wait_for_function(
        "document.querySelector('#ui-check').innerText.startsWith('Not readable: nothing is at')"
    )
    assert page.is_disabled("#ui-read")

    # A folder the person may not open: the engine's sentence names the folder.
    locked = base / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this account can open a folder with no permission")
        page.fill("#ui-root", str(locked))
        page.press("#ui-root", "Enter")
        page.wait_for_function(
            "document.querySelector('#ui-check').innerText.includes('permission denied on')"
        )
        assert (
            page.inner_text("#ui-check")
            == f"Not readable: permission denied on {locked.resolve()}."
        )
        assert page.is_disabled("#ui-read")
    finally:
        locked.chmod(0o755)

    # Typing a readable folder after turns it back on; nothing was written meanwhile.
    page.fill("#ui-root", str(pool))
    page.press("#ui-root", "Enter")
    page.wait_for_selector("#ui-check .ui-note")
    assert not page.is_disabled("#ui-read")
    assert sorted(path.name for path in base.iterdir()) == sorted([*before, "locked"])
    assert not (config / "decider.json").exists()
    assert not (config / "recent_projects.json").exists()
    page.context.close()


def test_a_project_already_written_at_the_place_offers_to_open_it_and_writes_nothing(
    browser, landed
) -> None:
    base, pool, config, url = landed
    existing = landing.create_project(
        {
            "root": str(pool),
            "name": "Whitfield imaging",
            "project": str(base / "projects" / "whitfield"),
        },
        created_at="2026-10-01T10:00:00Z",
    )
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-root")
    page.fill("#ui-root", str(pool))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    page.fill("#ui-where", str(existing))
    page.dispatch_event("#ui-where", "change")
    page.wait_for_selector("#ui-where-said .ui-problem")
    assert (
        page.inner_text("#ui-where-said").strip()
        == "A project is already saved here · Open it instead"
    )
    assert page.is_disabled("#ui-read")
    settings_before = (existing / "settings.yaml").read_text(encoding="utf-8")
    _shots(page, "landing-08-already-written")
    # Open it instead: the project opens, and nothing was made or read in it.
    page.click("#ui-where-said .ui-open-existing")
    page.wait_for_selector("#ui-setup")
    assert page.inner_text("#ui-setup h2") == "Whitfield imaging"
    assert (existing / "settings.yaml").read_text(encoding="utf-8") == settings_before
    assert not (existing / "CURRENT").exists()
    assert not (config / "decider.json").exists()
    page.context.close()


@pytest.fixture
def landed_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Path, Path, Path, str]]:
    """A server with no project whose home folder is the test's own, so the place it proposes for a
    project (under ``~/Ingence Projects``) is a folder the test made."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "config"
    config.mkdir()
    process, address = start_dashboard(None, config)
    try:
        yield tmp_path, home, config, address
    finally:
        stop(process)


def test_a_place_holding_another_folders_project_is_not_offered_to_be_opened(
    browser, landed_home
) -> None:
    """Two folders of one name, read by two projects: the second is proposed a place of its own,
    and the first's project is offered to be opened only where its folder is the one asked about."""
    base, home, _, url = landed_home
    first, second = (base / place / "imaging-drive" for place in ("lab-a", "lab-b"))
    for root in (first, second):
        root.mkdir(parents=True)
        (root / "DAPI_actin_01.csv").write_text("a\n1\n")
    parent = home / landing.PROJECTS_PARENT
    existing = landing.create_project(
        {"root": str(first), "project": str(parent / "imaging-drive")},
        created_at="2026-10-13T10:00:00Z",
    )
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-root")
    page.fill("#ui-root", str(second))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    # The place proposed is free, so nothing is said against it and Read it now is on.
    page.wait_for_function("document.querySelector('#ui-where').value.endsWith('imaging-drive 2')")
    assert page.locator("#ui-where-said .ui-problem").count() == 0
    assert not page.is_disabled("#ui-read")

    # By hand, the other folder's project: said, with another place, and no way to open it.
    page.fill("#ui-where", str(existing))
    page.dispatch_event("#ui-where", "change")
    page.wait_for_selector("#ui-where-said .ui-problem")
    said = page.inner_text("#ui-where-said")
    assert said.startswith(
        "A project for another data folder is already saved here. Choose another place."
    )
    assert page.locator(".ui-open-existing").count() == 0
    assert page.is_disabled("#ui-read")
    _shots(page, "landing-10-another-folders-project")
    page.click(".ui-use-another")
    page.wait_for_function("document.querySelector('#ui-where-said .ui-problem') === null")
    assert page.input_value("#ui-where").endswith("imaging-drive 2")
    assert not page.is_disabled("#ui-read")
    assert sorted(path.name for path in parent.iterdir()) == ["imaging-drive"], "nothing written"

    # For the folder that project does read, it is offered.
    page.fill("#ui-root", str(first))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    page.fill("#ui-where", str(existing))
    page.dispatch_event("#ui-where", "change")
    page.wait_for_selector("#ui-where-said .ui-open-existing")
    assert page.inner_text("#ui-where-said").strip() == (
        "A project is already saved here · Open it instead"
    )
    page.context.close()


# -- an older answer never overwrites a newer one ------------------------------------------------


def test_a_slow_answer_about_an_older_name_does_not_overwrite_the_place_for_the_newer_one(
    browser, landed_home
) -> None:
    """Each keystroke in the name asks where the project would go. The answer to the first is held
    back here until the second has been answered; the place shown stays the second's."""
    base, _, _, url = landed_home
    pool = _pool(base)
    page = _page(browser)
    page.add_init_script(
        """(() => {
          const real = window.fetch;
          window.fetch = async (input, init) => {
            const address = typeof input === 'string' ? input : input.url;
            const response = await real(input, init);
            if (address.includes('project-folder') && address.includes('name=W&')) {
              await new Promise((done) => setTimeout(done, 1500));
              window.__lateAnswerDelivered = true;
            }
            return response;
          };
        })()"""
    )
    page.goto(url)
    page.wait_for_selector("#ui-root")
    page.fill("#ui-root", str(pool))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    page.fill("#ui-name", "W")
    page.fill("#ui-name", "Whitfield imaging")
    page.wait_for_function(
        "document.querySelector('#ui-where').value.endsWith('Whitfield imaging')"
    )
    # The held answer (about "W") arrives now; the page has had it before the next frames are drawn.
    page.wait_for_function("window.__lateAnswerDelivered === true", timeout=HANG_GUARD_MS)
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    assert page.input_value("#ui-where").endswith("/Ingence Projects/Whitfield imaging")
    page.context.close()


# -- the keyboard --------------------------------------------------------------------------------


def test_enter_on_check_checks_and_enter_on_the_last_field_reads(browser, landed) -> None:
    base, pool, config, url = landed
    where = base / "projects" / "hek"
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-root")
    page.fill("#ui-root", str(pool))
    # Enter on the Check button checks (the button's own activation).
    page.focus("#ui-check-button")
    page.keyboard.press("Enter")
    page.wait_for_selector("#ui-check .ui-note")
    page.fill("#ui-where", str(where))
    page.dispatch_event("#ui-where", "change")
    # The fields run top to bottom: the depth, then the questions, then Start scan.
    order = page.evaluate(
        """() => [...document.querySelectorAll(
            'section.ui-form input, section.ui-form textarea, section.ui-form button')]
          .filter((n) => n.offsetParent !== null).map((n) => n.id)"""
    )
    assert order.index("ui-root") < order.index("ui-check-button") < order.index("ui-name")
    assert order.index("ui-where") < order.index("ui-depth-quick") < order.index("ui-depth-deep")
    assert order.index("ui-depth-deep") < order.index("ui-goals") < order.index("ui-read")
    # Enter on Start scan scans.
    assert not (where / "project.json").exists()
    page.focus("#ui-read")
    page.keyboard.press("Enter")
    page.wait_for_selector("#ui-progress, #pages", timeout=READING)
    assert (where / "project.json").exists()
    assert not (config / "decider.json").exists()
    page.wait_for_selector("#pages", timeout=READING)
    assert page.problems == []
    page.context.close()


# -- a project made and not yet read -------------------------------------------------------------


@pytest.fixture
def unread(tmp_path: Path) -> Iterator[tuple[Path, Path, Path, str]]:
    """A server on a project ``ingence init`` made and nobody has read (the years chosen), as
    ``ingence see`` finds it."""
    pool = _pool(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    project = landing.create_project(
        {
            "root": str(pool),
            "name": "Counting",
            "project": str(tmp_path / "counting"),
            "years": [2005, 2015],
        },
        created_at="2026-10-13T10:00:00Z",
    )
    process, address = start_dashboard(project, config)
    try:
        yield tmp_path, pool, project, address
    finally:
        stop(process)


def test_a_project_made_and_not_read_shows_the_same_fields_and_reads_on_the_click(
    browser, unread
) -> None:
    _, _, project, url = unread
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-setup")
    assert page.inner_text("#ui-setup h2") == "Counting"
    assert page.input_value("#ui-setup-first-year") == "2005"
    assert page.input_value("#ui-setup-last-year") == "2015"
    # Nothing about the depth is kept in a project, and none has been read: Standard is chosen.
    assert page.is_checked("#ui-setup-depth-standard")
    assert page.locator("#ui-setup-who").count() == 0
    assert page.locator("#ui-setup").inner_text().count("scans ") == 1
    page.fill("#ui-setup-first-year", "2001")
    page.check("#ui-setup-depth-deep")
    page.click("#ui-setup-read")
    page.wait_for_selector("#ui-counters .ui-counter", timeout=READING)
    page.wait_for_selector("#pages", timeout=READING)
    assert page.url.endswith("#overview")
    written = yaml.safe_load((project / "settings.yaml").read_text(encoding="utf-8"))
    assert written["dictionary"]["years"] == [2001, 2015]
    assert "dive" not in written, "opening files is kept in no settings file"
    # It rode the request: the reading that followed opened files too.
    job = page.evaluate("fetch('/api/landing/progress').then((r) => r.json())")
    assert job["operations"] == ["census", "dive", "inference"], job
    page.context.close()


def test_a_reading_that_cannot_start_says_what_happened_and_what_to_do(browser, unread) -> None:
    base, pool, project, url = unread
    page = _page(browser)
    page.goto(url)
    page.wait_for_selector("#ui-setup")
    # The folder goes away between making the project and reading it.
    pool.rename(base / "moved-pool")
    page.check("#ui-setup-depth-deep")
    page.click("#ui-setup-read")
    page.wait_for_selector("text=did not finish", timeout=READING)
    said = page.inner_text("#ui-progress")
    assert "What happened: A walk reads every declared root" in said
    assert "RootUnreachableError" not in said.split("Why")[0]
    commands = page.inner_text(".ui-commands")
    assert f"ingence index -p {project}" in commands
    assert "ingence infer -p" in commands
    # And it can be tried again from the same page, with the depth as the scan asked for it.
    assert page.is_visible("#ui-setup-read")
    assert page.is_checked("#ui-setup-depth-deep")
    _shots(page, "landing-09-failed")
    page.context.close()


# -- every state is readable by a screen reader, in light and in dark ----------------------------


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _serious(page: Any, where: str) -> list[str]:
    """What axe finds on the page as it stands, critical or serious, in words."""
    script = _axe_script()
    if script is None:
        if REQUIRED:
            pytest.fail(
                "axe-core is not installed (INGENCE_BROWSER_REQUIRED is set)", pytrace=False
            )
        pytest.skip("axe-core is not installed: run `npm install`, or set INGENCE_AXE_JS")
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
    return [
        f"{where}: {item['impact']} {item['id']} on {len(item['nodes'])} element(s): "
        f"{item['help']}; first: {item['nodes'][0]['html'][:100]}"
        for item in found["violations"]
        if item["impact"] in ("critical", "serious")
    ]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_landing_states_have_no_serious_accessibility_violation(
    browser, landed, scheme: str
) -> None:
    """The page as it opens on a machine with projects, with a rename open, a note for each kind of
    Check, a project already written at the place, and the page while it reads."""
    base, pool, config, url = landed
    named = landing.create_project(
        {
            "root": str(pool),
            "name": "Whitfield imaging",
            "project": str(base / "projects" / "whitfield"),
        },
        created_at="2026-10-01T10:00:00Z",
    )
    unnamed = landing.create_project(
        {"root": str(pool), "project": str(base / "projects" / "okafor")},
        created_at="2026-10-02T10:00:00Z",
    )
    remember_project(named, opened_at="2026-10-12T16:00:00Z", name=None, config_home=config)
    remember_project(unnamed, opened_at="2026-10-13T16:00:00Z", name=None, config_home=config)
    page = _page(browser, 1280, scheme)
    problems: list[str] = []
    page.goto(url)
    page.wait_for_selector(".ui-projects .ui-project")
    problems += _serious(page, f"{scheme}: projects and the card")
    page.locator(".ui-rename-open").first.click()
    problems += _serious(page, f"{scheme}: a rename open")
    page.fill("#ui-root", str(pool / "western blot notes.txt"))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-problem")
    problems += _serious(page, f"{scheme}: a folder that cannot be read")
    page.fill("#ui-root", str(pool))
    page.click("#ui-check-button")
    page.wait_for_selector("#ui-check .ui-note")
    page.fill("#ui-where", str(named))
    page.dispatch_event("#ui-where", "change")
    page.wait_for_selector("#ui-where-said .ui-problem")
    problems += _serious(page, f"{scheme}: a project already written there")
    page.context.close()
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_while_it_reads_and_before_the_first_run_has_no_serious_violation(
    browser, unread, scheme: str
) -> None:
    _, _, project, url = unread
    page = _page(browser, 1280, scheme)
    page.goto(url)
    page.wait_for_selector("#ui-setup")
    problems = _serious(page, f"{scheme}: a project not yet read")
    page.route(
        "**/api/landing/progress",
        lambda route: route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "kind": "first_run",
                    "state": "running",
                    "phase": "inferring",
                    "operations": ["census", "inference"],
                    "started_at": "2026-10-13T19:07:00Z",
                    "counters": {
                        "folders_found": 417,
                        "files_found": 3_470,
                        "bytes_seen": 2_470_000_000,
                        "rules_run": 21,
                        "rules_total": 48,
                    },
                }
            ),
        ),
    )
    page.click("#ui-setup-read")
    page.wait_for_selector("#ui-counters .ui-counter")
    page.wait_for_function(
        "document.querySelector('[data-counter=rules_run] .ui-figure').innerText.startsWith('21')"
    )
    problems += _serious(page, f"{scheme}: while it reads")
    assert project.exists()
    page.context.close()
    assert problems == [], "\n".join(problems)
