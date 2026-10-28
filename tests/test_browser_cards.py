"""The inspector's cards at each scale of Years, and What changed, in a real browser.

Skipped, never failed, where the ``browser`` extra or a browser is missing; required where
``INGENCE_BROWSER_REQUIRED=1``. Every project is invented: one whose lines of work blur into one
another (``fixtures/blurred_pool.py``, a dive and a later census), one of invented experiments with
designs (``fixtures/design_pool.py``) and one of unrelated files. Each is served by ``ingence see``
on a copy, on a port between 20000 and 29999, with who decides and everything else the Dashboard
writes kept in a folder of the test's own (``INGENCE_CONFIG_HOME``).

The behaviour: a card draws what its routes serve, in a fixed order, with every stability on its
one axis. Nothing on it is worked out on the page that the engine did not work out. Where the
engine did not serve a thing (a clock nobody read, a table never opened, an experiment with no
design) the card says so or leaves the section out, and never draws a zero.

The cards are: the project card; a line of work with where the other seeds split it; an
experiment; its design grid; the one stability axis; a file and the tables with its columns; each
figure beside the previous run's; and the empty Years view.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import launch_browser

REQUIRED = os.environ.get("INGENCE_BROWSER_REQUIRED") == "1"
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="the browser extra is not installed"
    )
pytest.importorskip("sklearn", reason="themes need the themes extra")

REPOSITORY = Path(__file__).resolve().parents[1]
WEB = REPOSITORY / "src" / "ingence" / "web"
PROJECT_NAME = "Whitfield bench pool"


# ---------------------------------------------------------------------------
# The browser and the servers.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


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


def _wait_for(url: str, seconds: float = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as answer:
                if answer.status == 200:
                    return
        except Exception:
            time.sleep(0.3)
    message = f"{url} did not answer in {seconds} s"
    raise AssertionError(message)


def _serve(project: Path, base: Path):
    """Serve ``project`` and yield its address; stop the server after."""
    config = base / "config-home"
    config.mkdir(exist_ok=True)
    port = _port()
    environment = {
        **os.environ,
        "INGENCE_CONFIG_HOME": str(config),
        "PYTHONPATH": os.pathsep.join(
            [str(REPOSITORY / "src"), str(REPOSITORY / "tests"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ingence.cli.main",
            "see",
            "-p",
            str(project),
            "--port",
            str(port),
            "--no-open",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    address = f"http://127.0.0.1:{port}/"
    try:
        _wait_for(f"{address}api/v1/version")
        yield address
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def blurred(tmp_path_factory: pytest.TempPathFactory):
    """The blurred project, served on a copy, who decides set."""
    from fixtures.blurred_pool import build_blurred_project
    from ingence.core.project import Project

    base = tmp_path_factory.mktemp("cards_blurred")
    built = build_blurred_project(base / "built")
    copy = base / "project"
    shutil.copytree(built, copy)
    Project.open(copy).rename(PROJECT_NAME)
    for address in _serve(copy, base):
        _decide(address, "a. person")
        yield {"base": address, "project": copy}


@pytest.fixture(scope="module")
def designed(tmp_path_factory: pytest.TempPathFactory):
    """A pool of experiments with designs, the grid switched on, and one with twelve columns."""
    from fixtures.design_pool import project_with_the_grid_on, write_pool
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("cards_designed")
    pool = write_pool(base / "pool")
    wide = pool / "20261105_HeLa_time_course"
    wide.mkdir()
    for drug in ("DMSO", "taxol"):
        for hours in range(1, 13):
            for number in (1, 2):
                (wide / f"20261105_HeLa_{drug}_{hours}h_{number:03d}.tif").write_text("x")
    project = project_with_the_grid_on(base / "project", [pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    for address in _serve(project.path, base):
        yield {"base": address, "project": project.path}


@pytest.fixture(scope="module")
def unrelated(tmp_path_factory: pytest.TempPathFactory):
    """A folder of unrelated files: no experiment is found in it."""
    from fixtures.revision_4 import write_revision
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    base = tmp_path_factory.mktemp("cards_unrelated")
    pool = base / "pool"
    for folder, names in {"notes": ("readme.txt", "todo.txt"), "misc": ("list.txt",)}.items():
        (pool / folder).mkdir(parents=True)
        for name in names:
            (pool / folder / name).write_text("a few words\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    for address in _serve(project.path, base):
        yield {"base": address, "project": project.path}


def _decide(base: str, actor: str) -> None:
    import json

    request = urllib.request.Request(
        f"{base}api/decider",
        data=json.dumps({"decider": actor}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def _get(page: Any, path: str) -> Any:
    return page.evaluate("async (path) => (await fetch(path)).json()", path)


def _open_years(browser: Any, base: str, *, scheme: str = "light", width: int = 1440) -> Any:
    page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page.goto(f"{base}#years")
    page.wait_for_function("window.ingenceYears !== undefined", timeout=120000)
    page.wait_for_selector("#detail .ycard, .state-card", timeout=120000)
    return page


def _select(page: Any, selection: str) -> None:
    page.evaluate(
        "async (selection) => { await window.ingenceYears.load();"
        " window.ingenceYears.select(JSON.parse(selection)); }",
        selection,
    )


def _themes(page: Any) -> dict[str, Any]:
    return _get(page, "/api/v1/themes")


def _stability(page: Any) -> dict[str, Any]:
    return _get(page, "/api/v1/themes/stability")


# ---------------------------------------------------------------------------
# The project card.
# ---------------------------------------------------------------------------


def test_the_project_card_shows_its_name_wheel_and_counts(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    page.wait_for_function(
        "document.querySelector('#detail .ytitle') && "
        f"document.querySelector('#detail .ytitle').textContent === {PROJECT_NAME!r}",
        timeout=60000,
    )
    card = page.locator("#detail .ycard[data-card='project']")
    assert card.locator(".ykicker").text_content().strip() == "The project"
    mono = card.locator(".yc-monoline").inner_text()
    assert mono.startswith("pool"), (
        "the folder it reads (not the project's own), then people, years"
    )
    assert "3 areas" in mono and "2023" in mono
    # The small Wheel, 120 px, beside the sentence; the large Wheel stays behind W.
    assert card.locator(".ywheel svg").count() == 1
    assert card.locator(".yc-small svg").get_attribute("width") == "120"
    completeness = _get(page, "/api/v1/completeness")
    raw = sum(e["raw_acquisitions"] for e in completeness["experiments"])
    followed = sum(e["raw_followed"] for e in completeness["experiments"])
    assert card.locator(".yc-lead").inner_text().strip() == (
        f"{followed:,} of {raw:,} raw acquisitions found are followed by an analysis file found."
    )
    # Four rows, in a fixed order, each a served count or a sum of served counts.
    assert card.locator(".yc-kv dt").all_inner_texts() == [
        "Experiments",
        "Files in them",
        "Themes",
        "In no theme",
    ]
    themes = _themes(page)
    in_them = sum(e["files"] for e in themes["experiments"])
    rows = card.locator(".yc-kv dd").all_inner_texts()
    assert rows[0] == f"{len(themes['experiments'])} in 3 areas"
    assert rows[1] == f"{in_them:,} of {in_them + completeness['outside']['files']:,} read"
    assert rows[2].startswith(f"{len(themes['themes'])}")
    # How far the seeds agree is in Advanced's Stability section, not in the main view.
    assert card.locator(".stability").count() == 0
    # Lines of work: four rows, then "and n more", which opens the rest in a list that scrolls.
    lines = card.locator(".yc-lines .yc-line")
    assert lines.count() == min(4, len(themes["themes"]))
    more = card.locator(".yc-more")
    assert more.inner_text() == f"and {len(themes['themes']) - 4} more"
    # The note, and the three chips (the map by its address).
    assert "never a result" in card.locator(".notice-note").inner_text()
    assert card.locator(".notice-note .card-id").inner_text() == "themes · completeness"
    chips = card.locator(".yactions .yact")
    assert chips.all_inner_texts() == ["Inventory", "Questions", "Map of groups"]
    assert [chips.nth(i).get_attribute("href") for i in range(3)] == [
        "#inventory",
        "#review",
        "#map",
    ]
    # At 1440 x 900 the whole card, to the bottom of its chips, is inside the window: it fits
    # without scrolling (the lines list below is the one thing that scrolls).
    page.evaluate("window.scrollTo(0, 0)")
    reach = page.evaluate(
        "() => ({"
        "chips: document.querySelector('#detail .ycard .yactions').getBoundingClientRect().bottom,"
        " window: window.innerHeight})"
    )
    assert reach["chips"] <= reach["window"], reach
    more.click()
    assert card.locator(".yc-lines").evaluate("n => n.classList.contains('open')")
    assert card.locator(".yc-lines .yc-line").count() == len(themes["themes"])
    assert card.locator(".yc-lines").evaluate("n => getComputedStyle(n).overflowY") == "auto"
    page.close()


def test_the_wheel_link_opens_the_large_wheel_and_no_card_holds_it(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    page.locator("#detail .yc-wheel-link").click()
    page.wait_for_selector(".years .ybig .ywheel svg")
    assert page.locator(".years .ybig .ywheel svg").get_attribute("width") != "120"
    assert page.locator("#detail .ywheel svg").get_attribute("width") == "120"
    page.close()


# ---------------------------------------------------------------------------
# The one stability axis.
# ---------------------------------------------------------------------------


def test_a_value_of_exactly_point_eight_is_drawn_filled(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    classes = page.evaluate(
        "() => [0.79, 0.8, 0.81, 1].map("
        "v => stabilityAxis(v).querySelector('.stability-dot').className)"
    )
    assert classes == [
        "stability-dot below",
        "stability-dot",
        "stability-dot",
        "stability-dot",
    ], "filled at 0.80 and above, dotted below it"
    page.close()


# ---------------------------------------------------------------------------
# A line of work.
# ---------------------------------------------------------------------------


def _stability_rows(page: Any) -> dict[str, Any]:
    """Advanced's Stability section shows the lines' stability: each line's name and its axis, by
    name."""
    page.evaluate("navigate('stability')")
    page.wait_for_selector(".ystability .ystab:not(.head)", timeout=120000)
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('.ystability .ystab:not(.head)')]
          .map((row) => [row.querySelector('button').textContent,
            {below: row.querySelectorAll('.stability-dot.below').length,
             dots: row.querySelectorAll('.stability-dot').length}]))"""
    )


def test_a_line_below_the_boundary_is_dotted_in_stability_and_its_card_holds_no_axis(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    route = _stability(page)
    themes = {t["theme"]: t for t in _themes(page)["themes"]}
    below = [t for t in route["themes"] if t["stability"] < route["uncertain_below"]]
    assert below, "the blurred pool has a line the seeds split"
    line = min(below, key=lambda t: t["stability"])
    _select(page, f'{{"kind": "theme", "id": {line["theme"]}}}')
    card = page.locator("#detail .ycard[data-card='theme']")
    card.wait_for(timeout=60000)
    assert card.locator(".ykicker").text_content().strip() == "A theme"
    assert card.locator(".stability").count() == 0, "the card's axis left the main view"
    rows = _stability_rows(page)
    label = themes[line["theme"]].get("person_label") or themes[line["theme"]]["label"]
    assert rows[label] == {"below": 1, "dots": 1}, "the dotted dot below 0.80, in Stability"
    page.close()


def test_a_line_at_or_above_the_boundary_has_a_filled_dot_in_stability(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    route = _stability(page)
    themes = {t["theme"]: t for t in _themes(page)["themes"]}
    firm = max(route["themes"], key=lambda t: t["stability"])
    assert firm["stability"] >= route["uncertain_below"]
    rows = _stability_rows(page)
    label = themes[firm["theme"]].get("person_label") or themes[firm["theme"]]["label"]
    assert rows[label] == {"below": 0, "dots": 1}
    page.close()


def test_a_line_names_the_words_it_shares_and_where_it_stands(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    overlap = _get(page, "/api/v1/themes/overlap")
    themes = {t["theme"]: t for t in _themes(page)["themes"]}
    pair = max(overlap["pairs"], key=lambda p: (p["shared"], -p["theme_a"]))
    _select(page, f'{{"kind": "theme", "id": {pair["theme_a"]}}}')
    card = page.locator("#detail .ycard[data-card='theme']")
    card.locator(".yc-shares .yc-soft").first.wait_for(timeout=60000)
    mine = card.locator(".yc-shares .yc-soft").all_inner_texts()
    other = themes[pair["theme_b"]]["label"]
    expected = (
        f"Shares {', '.join(pair['words'])} with {other} "
        f"({pair['shared']} of {len(themes[pair['theme_a']]['words'])} of its words)."
    )
    assert expected in mine
    assert card.locator(".yc-overlap-link").inner_text() == "Stability (O)"
    keys = card.locator(".yc-kv dt").all_inner_texts()
    assert keys[:2] == ["Dates", "Files"] and "No analysis found" in keys
    assert " first · " in card.locator(".yc-kv dd").first.inner_text()
    page.close()


def test_a_name_a_person_gives_is_magenta_with_the_engine_s_words_underneath(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    line = _themes(page)["themes"][0]
    _select(page, f'{{"kind": "theme", "id": {line["theme"]}}}')
    card = page.locator("#detail .ycard[data-card='theme']")
    card.locator(".yc-yourname").wait_for()
    assert card.locator(".ytitle.human").count() == 0, "magenta only where a person said so"
    assert "None. A name you give is shown in magenta" in card.locator(".yc-yourname").inner_text()
    card.locator(".yc-rename").click()
    page.wait_for_selector("dialog[open] input")
    page.fill("dialog[open] input", "Dana's spindle line")
    page.click("dialog[open] button.primary")
    page.wait_for_function(
        "document.querySelector('#detail .ytitle.human') !== null", timeout=60000
    )
    assert page.locator("#detail .ytitle").inner_text().strip() == "Dana's spindle line"
    assert line["label"] in page.locator("#detail .yengine").inner_text()
    assert page.locator("#detail .yc-given strong.human").inner_text() == "Dana's spindle line"
    page.close()


# ---------------------------------------------------------------------------
# An experiment.
# ---------------------------------------------------------------------------


def _experiment(page: Any, where: str) -> dict[str, Any]:
    """The completeness row of the first experiment for which the JS ``where`` holds."""
    return page.evaluate(
        "async (where) => { const c = await fetch('/api/v1/completeness').then(r => r.json());"
        " return c.experiments.find(new Function('e', 'return ' + where)); }",
        where,
    )


def test_an_experiment_card_follows_the_board_and_spells_analyzed_with_a_z(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    row = _experiment(page, "e.raw_acquisitions > 0 && e.analysis_files === 0")
    assert row is not None, "an experiment with raw data and no analysis"
    _select(page, f'{{"kind": "exp", "id": "{row["id"]}"}}')
    card = page.locator("#detail .ycard[data-card='exp']")
    card.locator(".yc-path").wait_for()
    assert card.locator(".ykicker").text_content().strip() == "An experiment"
    assert card.locator(".yc-path").text_content() == row["folder"]
    assert (
        card.locator(".yc-lead")
        .inner_text()
        .startswith(f"{row['raw_followed']} of {row['raw_acquisitions']} raw")
    )
    assert card.locator(".yc-kv dt").all_inner_texts() == [
        "Date",
        "Files found",
        "Theme",
        "Raw · analysis · results",
    ]
    values = card.locator(".yc-kv dd").all_inner_texts()
    assert values[0].endswith("from the folder's name")
    assert values[3] == (
        f"{row['raw_acquisitions']} · {row['analysis_files']} · {row['result_tables']}"
    )
    files = _get(page, f"/api/v1/experiments/files?experiment={row['id']}&limit=1")
    page.wait_for_function(
        "document.querySelector('#detail .yc-kv dd:nth-of-type(2)').textContent.includes('opened')",
        timeout=60000,
    )
    assert card.locator(".yc-kv dd").nth(1).inner_text() == (
        f"{files['files']} · {files['opened_files']} opened"
    )
    chips = card.locator(".yactions .yact").all_inner_texts()
    assert chips == ["Open its files", "Analyzed elsewhere…", "Relations (N)"]
    assert "nalysed" not in page.locator("#detail").inner_text()
    page.wait_for_function(
        "/experiment_folder_from_\\w+ v\\d/.test("
        "document.querySelector('#detail .notice-note .card-id').textContent)",
        timeout=60000,
    )
    note = card.locator(".notice-note").inner_text()
    assert re.search(r"experiment_folder_from_\w+ v\d", note), "the note names its rule and version"
    page.close()


def test_the_british_spelling_is_gone_from_the_files_the_cards_live_in() -> None:
    """``grep -ri analysed`` finds only the engine's field name in the files the cards live in."""
    for name in ("years.js", "inspector.js", "manifest.js", "years.css"):
        text = (WEB / name).read_text(encoding="utf-8")
        found = re.findall(r"analysed\w*", text, flags=re.IGNORECASE)
        assert set(found) <= {"analysed_elsewhere"}, (name, sorted(set(found)))


# ---------------------------------------------------------------------------
# The design grid in the card.
# ---------------------------------------------------------------------------


def _design_id(page: Any, folder: str) -> str:
    return page.evaluate(
        "async (folder) => (await fetch('/api/v1/design').then(r => r.json()))"
        ".experiments.find(e => e.folder === folder).id",
        folder,
    )


def _settled_frames(page: Any) -> None:
    page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def test_the_card_draws_a_compact_grid_with_the_full_grids_counts(
    browser: Any, designed: dict[str, Any]
) -> None:
    page = _open_years(browser, designed["base"])
    identifier = _design_id(page, "20261012_HeLa_drug_time")
    _select(page, f'{{"kind": "exp", "id": "{identifier}"}}')
    grid = page.locator("#detail .yc-gridblock")
    grid.wait_for(timeout=60000)
    assert "Design grid" in grid.locator("h3").text_content()
    numbers = grid.locator(".yc-cell:not(.notfound):not(.none)").all_inner_texts()
    assert numbers == ["3", "3", "3"]
    assert grid.locator(".yc-cell.notfound").count() == 1, (
        "hatched where a combination was not found"
    )
    assert "gradient" in grid.locator(".yc-cell.notfound").evaluate(
        "n => getComputedStyle(n).backgroundImage"
    )
    key = grid.locator(".yc-gridkey").inner_text()
    assert "darker is more replicates" in key and "hatched: combination not found" in key
    assert grid.locator(".yc-cell").first.evaluate("n => n.getBoundingClientRect().height") == 22
    grid.get_by_text("Open the grid").click()
    page.wait_for_selector(".ydesign .dgrid .dnum")
    full = page.eval_on_selector_all(
        ".ydesign .dgrid .dnum", "nodes => nodes.map(n => n.textContent)"
    )
    assert full == numbers, "the card's grid and the full grid show the same counts"
    page.close()


def test_a_grid_of_more_than_nine_columns_shows_nine_and_says_how_many_more(
    browser: Any, designed: dict[str, Any]
) -> None:
    page = _open_years(browser, designed["base"])
    identifier = _design_id(page, "20261105_HeLa_time_course")
    _select(page, f'{{"kind": "exp", "id": "{identifier}"}}')
    grid = page.locator("#detail .yc-gridblock")
    grid.wait_for(timeout=60000)
    assert grid.locator(".yc-gcol").count() == 9
    assert grid.get_by_text("Open the grid (3 more columns)").count() == 1
    page.close()


def test_an_experiment_with_no_design_has_no_grid_section(
    browser: Any, designed: dict[str, Any], blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, designed["base"])
    identifier = _design_id(page, "20261016_HeLa_series")
    with page.expect_response(lambda r: "experiments/design" in r.url):
        _select(page, f'{{"kind": "exp", "id": "{identifier}"}}')
    page.locator("#detail .ycard[data-card='exp']").wait_for()
    _settled_frames(page)
    assert page.locator("#detail .yc-gridblock").count() == 0
    assert "Design grid" not in page.locator("#detail").inner_text()
    page.close()
    # Where the grid is switched off (as it ships), no experiment shows one.
    page = _open_years(browser, blurred["base"])
    first = _themes(page)["experiments"][0]["id"]
    with page.expect_response(lambda r: "experiments/design" in r.url):
        _select(page, f'{{"kind": "exp", "id": "{first}"}}')
    page.locator("#detail .ycard[data-card='exp']").wait_for()
    _settled_frames(page)
    assert page.locator("#detail .yc-gridblock").count() == 0
    page.close()


# ---------------------------------------------------------------------------
# A file, and the tables with the same columns.
# ---------------------------------------------------------------------------


def _busy_files(page: Any) -> tuple[str, dict[str, dict[str, Any]]]:
    """The experiment that holds several tables, and its files by name."""
    experiment = page.evaluate(
        "async () => (await fetch('/api/v1/themes').then(r => r.json())).experiments"
        ".find(e => e.folder.startsWith('dana_whitfield/20230101')).id"
    )
    body = _get(page, f"/api/v1/experiments/files?experiment={experiment}&limit=500")
    return experiment, {row["name"]: row for row in body["rows"]}


def _open_file(page: Any, experiment: str, row: dict[str, Any]) -> Any:
    page.evaluate(
        "async ([experiment, row]) => { await window.ingenceYears.load();"
        " window.ingenceYears.open({kind: 'exp', id: experiment});"
        " window.ingenceYears.select("
        "{kind: 'file', id: row.id, exp: experiment, name: row.name}); }",
        [experiment, row],
    )
    card = page.locator("#detail .ycard[data-card='file']")
    card.wait_for(timeout=60000)
    # The page starts a read of the experiment's files for each draw made before they arrive, and
    # draws again as each finishes: a person's click finds the page after the last of them.
    page.wait_for_load_state("networkidle")
    card.wait_for(timeout=60000)
    return card


def test_a_file_card_names_its_clocks_and_never_merges_them(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    experiment, files = _busy_files(page)
    late = files["late_results.csv"]
    card = _open_file(page, experiment, late)
    assert card.locator(".ykicker").text_content().strip() == "A file"
    title = card.locator(".ytitle")
    assert title.inner_text() == "late_results.csv"
    assert "mono" in title.get_attribute("class") or ""
    assert "Plex" in title.evaluate("n => getComputedStyle(n).fontFamily")
    assert title.evaluate("n => getComputedStyle(n).fontSize") == "16px"
    assert card.locator(".ysubline").inner_text().startswith("table · ")
    believed = card.locator(".yc-lead").first.inner_text()
    assert (
        believed.startswith("Believed time ")
        and ", from its " in believed
        and " clock." in believed
    )
    assert "it does not say when the file was made" in believed
    assert card.locator(".yc-clocks dt").all_inner_texts() == [
        "modified",
        "birth",
        "inside the file",
        "name (folder)",
    ]
    inside = card.locator(".yc-clocks dd").nth(2)
    assert inside.inner_text().strip() == "not read: no file was opened"
    assert inside.locator(".yc-hatch").count() == 1, "hatched: it is not in the data"
    page.close()


def test_the_tables_with_the_same_columns_are_boxed_with_where_they_lie(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    experiment, files = _busy_files(page)
    two = next(row for name, row in files.items() if name.endswith("_plate_two.csv"))
    columns = _get(page, f"/api/v1/columns?id={two['id']}&limit=500")
    here = columns["same_columns_in_folder"]
    elsewhere = columns["same_columns_elsewhere"]
    assert here >= 1 and elsewhere > 20, "more files than the route names unless asked"
    assert here + elsewhere == columns["same_columns_total"]
    card = _open_file(page, experiment, two)
    box = card.locator(".yc-columns[data-columns='twins']")
    box.wait_for(timeout=60000)
    assert box.locator("div").first.inner_text() == (
        f"Same columns as {here} files in this folder and {elsewhere} others."
    )
    assert box.locator(".card-id").inner_text() == "well · cell_line · treatment · cell_count"
    assert "Read from the first line of each table that was opened." in box.inner_text()
    # See the n opens the files one by one, kept to the ones with those columns.
    box.get_by_text(f"See the {here}").click()
    page.wait_for_selector(".years .yrow")
    names = page.eval_on_selector_all(
        ".years .yrow .ylabel", "nodes => nodes.map(n => n.textContent)"
    )
    assert len(names) == len(columns["same_columns_here"]) + 1 == here + 1, (
        "the files the route names in this folder, and the file itself"
    )
    assert (
        page.locator(".ybar input[type='search']").input_value() == "same columns as " + two["name"]
    )
    page.close()


def test_a_table_no_dive_opened_shows_no_box_and_one_with_no_twin_says_so(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    experiment, files = _busy_files(page)
    late = files["late_results.csv"]
    card = _open_file(page, experiment, late)
    _settled_frames(page)
    assert card.locator(".yc-columns").count() == 0, "a table that was not opened shows no box"
    alone = next(row for name, row in files.items() if name.endswith("_viability.csv"))
    card = _open_file(page, experiment, alone)
    box = card.locator(".yc-columns[data-columns='none']")
    box.wait_for(timeout=60000)
    assert "No other table read has these columns." in box.inner_text()
    page.close()


def test_a_rule_s_name_is_never_cut_short_at_380_pixels(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    experiment, files = _busy_files(page)
    some = next(row for name, row in files.items() if name.endswith("_results.csv"))
    card = _open_file(page, experiment, some)
    card.locator(".yc-rulename").first.wait_for()
    page.evaluate("document.querySelector('#detail .ycard').style.width = '380px'")
    names = card.locator(".yc-rulename")
    claims = {
        (c["rule_id"], c["rule_version"])
        for c in some["claims"]
        if c["predicate"].startswith("classifies.") and c["value"] is not None
    }
    shown = {text.strip() for text in names.all_inner_texts()}
    for rule, version in claims:
        assert f"{rule} v{version}" in shown, "the whole name and its version are in the card"
    for index in range(names.count()):
        measure = names.nth(index).evaluate(
            "n => ({ellipsis: getComputedStyle(n).textOverflow === 'ellipsis', "
            "fits: n.scrollWidth <= n.clientWidth + 1})"
        )
        assert measure == {"ellipsis": False, "fits": True}
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # A row opens its rule, version and evidence.
    row = card.locator(".yc-rulerow").first
    detail = card.locator(".yc-ruledetail").first
    assert not detail.is_visible()
    row.click()
    assert detail.is_visible() and "version" in detail.inner_text()
    page.close()


def test_teach_a_word_opens_the_parts_of_the_files_name(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    experiment, files = _busy_files(page)
    some = next(row for name, row in files.items() if name.endswith("_results.csv"))
    card = _open_file(page, experiment, some)
    assert card.locator(".yactions .yact").all_inner_texts() == [
        "Its record",
        "Relations (N)",
        "Teach a word…",
    ]
    teach = card.locator(".yact[data-teach]")
    assert teach.count() == 1 and teach.inner_text() == "Teach a word…", "the T key finds the chip"
    teach.click()
    card.locator(".yc-teach .yword").first.wait_for(timeout=60000)
    assert card.locator(".yc-teach .yword").count() >= 1
    page.close()


# ---------------------------------------------------------------------------
# What changed, each figure beside the previous run's.
# ---------------------------------------------------------------------------


def _figures_of(manifest: dict[str, Any], *, beside: bool) -> list[dict[str, Any]]:
    return [
        row
        for row in manifest["quality"]["rows"]
        if row["state"] == "counted" and (not beside or row["was_state"] == "counted")
    ]


def test_what_changed_sets_each_run_s_figures_beside_the_previous_run_s(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{blurred['base']}#manifest")
    page.locator(".ui-run").first.wait_for(timeout=120000)
    listed = _get(page, "/api/runs")["runs"]
    manifests = {r["run_id"]: _get(page, f"/api/v1/manifest?run={r['run_id']}") for r in listed}
    later = next(
        m
        for m in manifests.values()
        if m["compared_label"] and m["quality"]["available"] and _figures_of(m, beside=True)
    )
    first = next(
        m
        for m in manifests.values()
        if not m["compared_label"] and m["quality"]["available"] and _figures_of(m, beside=False)
    )
    # A run with a previous run: this run's figure and the previous run's, side by side.
    block = page.locator(f".ui-run[data-run='{later['run_id']}']")
    if block.get_attribute("open") is None:
        block.locator("summary").first.click()
    block.locator(".ui-quality-all summary").wait_for(timeout=120000)
    block.locator(".ui-quality-all summary").click()
    table = block.locator("table.ui-quality-runs").first
    table.wait_for()
    heads = table.locator("thead th").all_inner_texts()
    assert [h.split("\n")[0] for h in heads] == ["Figure", "This scan", "Previous scan"]
    assert later["compared_label"] in heads[2]
    row = _figures_of(later, beside=True)[0]
    line = table.locator("tbody tr").filter(has=page.locator(f"th:text-is({row['label']!r})")).first
    assert f"{row['was_count']:,}" in line.locator("td.ui-previous").inner_text()
    assert f"{row['count']:,}" in line.locator("td.ui-quality-now").inner_text()
    # The change is words only: no color of its own says better or worse.
    for change in block.locator(".ui-quality-runs .ui-change").all():
        assert change.evaluate(
            "n => getComputedStyle(n).color === getComputedStyle(n.closest('td')).color"
        )
    # The first run has no previous run, and says so in the third column.
    start = page.locator(f".ui-run[data-run='{first['run_id']}']")
    if start.get_attribute("open") is None:
        start.locator("summary").first.click()
    start.locator(".ui-quality-all summary").wait_for(timeout=120000)
    start.locator(".ui-quality-all summary").click()
    plain = start.locator("table.ui-quality-runs").first
    plain.wait_for()
    texts = plain.locator("td.ui-previous").all_inner_texts()
    assert texts and set(texts) == {"no previous scan"}
    page.close()


# ---------------------------------------------------------------------------
# The Years view with nothing in it.
# ---------------------------------------------------------------------------


def test_a_folder_of_unrelated_files_shows_the_sentence_and_not_an_empty_table(
    browser: Any, unrelated: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{unrelated['base']}#years")
    state = page.locator(".state-card[data-state='empty']")
    state.wait_for(timeout=120000)
    assert (
        state.locator(".state-sentence").inner_text()
        == "No experiments were found in what was read."
    )
    text = state.inner_text()
    assert "experiment_folder_from_contents" in text and "experiment_folder_from_dated_name" in text
    link = state.get_by_role("link", name="Open the Inventory")
    assert link.get_attribute("href") == "#inventory"
    rules = state.get_by_role("link", name="Which rules read experiments")
    assert rules.get_attribute("href") == "#rules"
    # The Years view is Overlap's By year mode.
    assert state.locator(".eyebrow").inner_text().strip().lower() == "overlap · by year · empty"
    assert page.locator(".years .yrow").count() == 0
    assert page.locator(".years table").count() == 0
    assert page.locator("#detail .ycard[data-card='project']").count() == 1, "the pane is not blank"
    page.close()


# ---------------------------------------------------------------------------
# Further cases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "said"),
    [
        ("instrument_clock", "from its instrument clock."),
        ("document_clock", "from its document clock."),
        ("document_last_saved", "from its last-save clock."),
        ("birth", "from its birth clock."),
        ("modification", "from its modified clock."),
    ],
)
def test_the_believed_time_names_the_clock_the_rule_chose_in_the_rule_s_own_words(
    browser: Any, blurred: dict[str, Any], source: str, said: str
) -> None:
    """The engine sends the rule's words (``instrument_clock``); the card never says clock twice."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    def chosen(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        for row in body["rows"]:
            if row["name"] == "late_results.csv" and row["believed"] is not None:
                row["believed"]["source"] = source
        route.fulfill(response=response, json=body)

    page.route("**/api/v1/experiments/files**", chosen)
    page.goto(f"{blurred['base']}#years")
    page.wait_for_function("window.ingenceYears !== undefined", timeout=120000)
    page.wait_for_selector("#detail .ycard", timeout=120000)
    experiment, files = _busy_files(page)
    card = _open_file(page, experiment, files["late_results.csv"])
    believed = card.locator(".yc-lead").first.inner_text()
    assert said in believed, believed
    assert "clock clock" not in believed
    rule_row = card.locator(".yc-rulerow", has_text="earliest credible time").first
    assert "clock clock" not in rule_row.inner_text()
    page.close()


def test_the_t_key_opens_the_list_of_words_from_a_file_card(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = _open_years(browser, blurred["base"])
    experiment, files = _busy_files(page)
    some = next(row for name, row in files.items() if name.endswith("_results.csv"))
    card = _open_file(page, experiment, some)
    assert card.locator(".yc-teach").is_hidden()
    page.locator("body").press("t")
    card.locator(".yc-teach .yword").first.wait_for(timeout=60000)
    assert card.locator(".yc-teach").is_visible()
    # Typing the letter into the search box does not open it.
    page.locator("#search-box").fill("t")
    assert card.locator(".yc-teach").is_visible()
    page.close()


def test_a_years_view_with_no_line_draws_no_wheel_and_no_lines_heading(
    browser: Any, unrelated: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{unrelated['base']}#years")
    page.locator("#detail .ycard[data-card='project']").wait_for(timeout=120000)
    card = page.locator("#detail .ycard[data-card='project']")
    assert card.locator(".ywheel").count() == 0, "no Wheel where there is nothing to draw"
    assert card.locator(".yc-wheel-link").count() == 0
    assert card.locator(".yc-lines").count() == 0
    assert "Themes" not in card.locator("h3").all_text_contents()
    assert "1" not in card.locator(".ywheel text").all_text_contents()
    page.close()


def test_this_run_and_the_previous_run_stand_side_by_side_at_1440(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{blurred['base']}#manifest")
    page.locator(".ui-run").first.wait_for(timeout=120000)
    listed = _get(page, "/api/runs")["runs"]
    manifests = {r["run_id"]: _get(page, f"/api/v1/manifest?run={r['run_id']}") for r in listed}
    later = next(
        m
        for m in manifests.values()
        if m["compared_label"] and m["quality"]["available"] and _figures_of(m, beside=True)
    )
    block = page.locator(f".ui-run[data-run='{later['run_id']}']")
    if block.get_attribute("open") is None:
        block.locator("summary").first.click()
    block.locator(".ui-quality-all summary").wait_for(timeout=120000)
    block.locator(".ui-quality-all summary").click()
    table = block.locator("table.ui-quality-runs").first
    table.wait_for()
    pane = page.locator("#list")
    fits = pane.evaluate("n => n.scrollWidth <= n.clientWidth + 1")
    assert fits, "nothing scrolls sideways"
    box = table.bounding_box()
    previous = table.locator("thead th").nth(2).bounding_box()
    assert box is not None and previous is not None
    pane_box = pane.bounding_box()
    assert pane_box is not None
    assert previous["x"] + previous["width"] <= pane_box["x"] + pane_box["width"] + 1
    assert previous["x"] + previous["width"] <= 1440
    # The figures' names wrap inside their column rather than widening the table.
    assert table.evaluate("t => t.scrollWidth <= t.clientWidth + 1")
    page.close()


def test_see_them_stands_beside_a_count_only_where_there_is_something_to_see(
    browser: Any, blurred: dict[str, Any]
) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    listed: dict[str, int] = {}

    def some_lines_have_one(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        # The first line has experiments with no analysis found; the others have none.
        body["themes"][0]["no_analysis_found"] = 2
        body["themes"][0]["experiments_with_raw"] = max(
            2, body["themes"][0]["experiments_with_raw"]
        )
        for row in body["themes"][1:]:
            row["no_analysis_found"] = 0
        listed["with"] = int(body["themes"][0]["key"])
        route.fulfill(response=response, json=body)

    page.route("**/api/v1/completeness", some_lines_have_one)
    page.goto(f"{blurred['base']}#years")
    page.wait_for_function("window.ingenceYears !== undefined", timeout=120000)
    page.wait_for_selector("#detail .ycard", timeout=120000)
    themes = _themes(page)["themes"]
    seen = {"none": 0, "some": 0}
    for theme in themes:
        _select(page, f'{{"kind": "theme", "id": {theme["theme"]}}}')
        page.locator("#detail .ycard[data-card='theme'] .ytitle").wait_for()
        label = page.locator("#detail .yc-kv dt", has_text="No analysis found")
        dd = label.locator("xpath=following-sibling::dd[1]")
        text = dd.inner_text()
        if text.startswith("0 of"):
            assert dd.locator(".link").count() == 0, text
            seen["none"] += 1
        else:
            assert theme["theme"] == listed["with"], text
            assert dd.locator(".link").count() == 1, text
            seen["some"] += 1
    assert seen["some"] == 1 and seen["none"] == len(themes) - 1
    page.close()


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory):
    from fixtures.archive_pool import build_archive_project

    base = tmp_path_factory.mktemp("cards_archive")
    built = build_archive_project(base / "built")
    for address in _serve(built, base):
        yield {"base": address, "project": built}


def test_an_archive_s_cards_say_theme_and_study(browser: Any, archive: dict[str, Any]) -> None:
    page = _open_years(browser, archive["base"])
    project = page.locator("#detail .ycard[data-card='project']")
    keys = project.locator(".yc-kv dt").all_inner_texts()
    assert keys[0] == "Studies" and "Themes" in keys and "In no theme" in keys, keys
    first = _themes(page)["themes"][0]["theme"]
    _select(page, f'{{"kind": "theme", "id": {first}}}')
    theme = page.locator("#detail .ycard[data-card='theme']")
    theme.wait_for()
    assert theme.locator(".ykicker").text_content().strip() == "A theme"
    under = theme.locator(".ysubline").inner_text()
    assert "stud" in under, under
    seen = [theme.inner_text().lower()]
    experiment = _themes(page)["experiments"][0]["id"]
    _select(page, f'{{"kind": "exp", "id": "{experiment}"}}')
    card = page.locator("#detail .ycard[data-card='exp']")
    card.wait_for()
    assert card.locator(".ykicker").text_content().strip() == "A study"
    assert "Theme" in card.locator(".yc-kv dt").all_inner_texts()
    seen.append(card.inner_text().lower())
    for said in seen:
        assert "line of work" not in said and "lines of work" not in said, said
    page.close()


# ---------------------------------------------------------------------------
# The accessibility checker, in light and dark.
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


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_card_passes_the_accessibility_checker(
    browser: Any, blurred: dict[str, Any], scheme: str
) -> None:
    script = _axe_script()
    if script is None:
        if REQUIRED:
            pytest.fail("axe-core is not installed: set INGENCE_AXE_JS")
        pytest.skip("axe-core is not installed: run `npm install`, or set INGENCE_AXE_JS")
    page = _open_years(browser, blurred["base"], scheme=scheme)
    page.add_script_tag(content=script.read_text(encoding="utf-8"))
    experiment, files = _busy_files(page)
    some = next(row for name, row in files.items() if name.endswith("_plate_two.csv"))
    line = _stability(page)["themes"][0]["theme"]
    checks = {
        "project": "window.ingenceYears.select(null)",
        "theme": f"window.ingenceYears.select({{kind: 'theme', id: {line}}})",
        "exp": f"window.ingenceYears.select({{kind: 'exp', id: '{experiment}'}})",
    }
    for kind, command in checks.items():
        page.evaluate(command)
        page.locator(f"#detail .ycard[data-card='{kind}']").wait_for(timeout=60000)
        _settled_frames(page)
        found = page.evaluate(
            "async () => (await axe.run(document.querySelector('#detail'), "
            "{runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']})).violations"
            ".filter(v => ['critical', 'serious'].includes(v.impact))"
            ".map(v => v.id + ': ' + v.nodes.length)"
        )
        assert found == [], (kind, found)
    _open_file(page, experiment, some).locator(".yc-columns").wait_for(timeout=60000)
    found = page.evaluate(
        "async () => (await axe.run(document.querySelector('#detail'), "
        "{runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']})).violations"
        ".filter(v => ['critical', 'serious'].includes(v.impact))"
        ".map(v => v.id + ': ' + v.nodes.length)"
    )
    assert found == [], ("file", found)
    page.close()
