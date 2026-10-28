"""The Years view draws what the engine serves and works none of it out.

Driven in a real browser on the invented theme pool, served by ``ingence see`` on a copy, with who
decides kept in a folder of the test's own. Three things that the page does not compute itself are
checked against the engine's own answer to the same request:

1. A name is the engine's: no row shows a date the tokenizer read in the folder's name.
2. The busiest days are the engine's: one experiment is given more than twenty files written in
   two sittings and a few months apart. The file scale opens on the sittings, and the chip and the
   hint say what ``busy_days`` says.
3. The words two lines share are the engine's: each pair ``themes/overlap`` lists is named in the
   Overlap table's cell for it, and no cell names a word the route does not.

Skipped where the ``browser`` extra or a browser is missing, as every browser test is.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from fixtures.dashboard_browser import HANG_GUARD_MS, launch_browser

sync_api = pytest.importorskip("playwright.sync_api", reason="the browser extra is not installed")
pytest.importorskip("sklearn", reason="themes need the themes extra")

REPOSITORY = Path(__file__).resolve().parents[1]
#: The sittings given to one experiment: (month, day, hour, files).
SITTINGS = ((3, 2, 10, 22), (3, 3, 14, 6), (7, 15, 9, 4))


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


def _free_port() -> int:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return int(held.getsockname()[1])


def _wait_for(url: str, seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as answer:
                if answer.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    message = f"{url} did not answer in {seconds} s"
    raise AssertionError(message)


def _get(base: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base}{path}", timeout=60) as answer:
        return json.loads(answer.read())


def _build(base: Path) -> tuple[Path, str]:
    """The theme pool, with one experiment's folder given files written in three sittings."""
    from fixtures.revision_4 import write_revision
    from fixtures.theme_pool import ENGINE_SURFACES, write_theme_pool
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    pool = base / "pool"
    pool.mkdir(parents=True)
    plant = write_theme_pool(pool, surfaces=ENGINE_SURFACES)
    dated = sorted(
        folder for unit, folder in plant.folders.items() if plant.years[unit] is not None
    )
    chosen = dated[0]
    index = 0
    for month, day, hour, count in SITTINGS:
        moment = dt.datetime(2019, month, day, hour, tzinfo=dt.UTC).timestamp()
        for _ in range(count):
            index += 1
            path = pool / chosen / f"sitting_{index:03d}.nd2"
            path.write_text(f"{chosen} {index}\n")
            os.utime(path, (moment, moment))
    project = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(project.path)
    run_census(project, run_id="run_20260101T000001Z_aaaaaaaaaaaa", started_at="t")
    run_inference(project, run_id="run_20260101T000002Z_bbbbbbbbbbbb", started_at="t")
    return project.path, chosen


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("serve")
    built, chosen = _build(base / "built")
    copy = base / "project"
    shutil.copytree(built, copy)
    config_home = base / "config-home"
    config_home.mkdir()
    port = _free_port()
    environment = {
        **os.environ,
        "INGENCE_CONFIG_HOME": str(config_home),
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
            str(copy),
            "--port",
            str(port),
            "--no-open",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}/"
    try:
        _wait_for(f"{base_url}api/v1/version")
        yield {"base": base_url, "chosen": chosen}
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


def _rows(page: Any) -> list[str]:
    return page.eval_on_selector_all(
        ".yrow .ylabel", "nodes => nodes.map(n => n.textContent.trim())"
    )


def _open(page: Any, label: str, depth: int) -> None:
    """Open the row of this name, one scale down, and wait until the page is there."""
    page.locator(".yrow .ylabel", has_text=label).first.dblclick()
    page.wait_for_function(f"document.querySelectorAll('.ycrumbs .ysep').length >= {depth}")


def test_names_busy_days_and_shared_words_are_what_the_engine_serves(
    browser: Any, served: dict[str, Any]
) -> None:
    base, chosen = served["base"], served["chosen"]
    themes = _get(base, "api/v1/themes")
    row = next(item for item in themes["experiments"] if item["folder"] == chosen)
    line = next(item for item in themes["themes"] if item["theme"] == row["theme"])
    assert row["name_read"] is True and not row["shown_name"][:8].isdigit()
    assert row["name"] != row["shown_name"], "the planted name carries a date the engine hides"
    files = _get(base, f"api/v1/experiments/files?experiment={row['id']}&limit=1")
    busy = files["busy_days"]
    assert busy is not None and busy["said"] == "2 to 3 Mar 2019"
    assert busy["files"] >= 28 and busy["percent"] >= 50

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.goto(f"{base}#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)

    # Down the scales to the experiment: the area, whose rows are its experiments under their
    # lines of work, then the folder itself: its story, and its files a chip away.
    _open(page, chosen.split("/")[0], 1)
    heads = page.eval_on_selector_all(
        ".years .yhead-row strong", "ns => ns.map(n => n.textContent)"
    )
    assert (line["person_label"] or line["label"]) in heads
    labels = _rows(page)
    assert not any(label[:8].isdigit() for label in labels), labels
    assert row["shown_name"] in labels
    _open(page, row["shown_name"], 3)
    page.wait_for_selector(".years .ystory", timeout=HANG_GUARD_MS)
    page.click(".years .ychip >> text=folders")
    page.wait_for_selector(".ybar input[type='search']", timeout=HANG_GUARD_MS)
    chip = page.locator(".ybar .ychip", has_text="Show the whole span")
    chip.wait_for(timeout=HANG_GUARD_MS)
    assert f"{busy['said']} holds {busy['percent']}% of files" in chip.inner_text()
    assert f"Opened on {busy['said']}, which holds {busy['percent']}% of the files" in (
        page.text_content(".yhint") or ""
    )

    # Overlap: each pair the route lists is named in both its cells, and no cell names a word the
    # route does not.
    overlap = _get(base, "api/v1/themes/overlap")
    page.goto(base + "#years")
    page.wait_for_selector(".years .yrow", timeout=HANG_GUARD_MS)
    page.keyboard.press("o")
    page.wait_for_selector(".yovl table", timeout=HANG_GUARD_MS)
    titles = page.eval_on_selector_all(".yovl td.cell", "nodes => nodes.map(n => n.title)")
    said = [title for title in titles if " share: " in title]
    assert len(said) == 2 * len(overlap["pairs"])
    shown = {item["theme"]: item["person_label"] or item["label"] for item in themes["themes"]}
    for pair in overlap["pairs"]:
        a, b = shown[pair["theme_a"]], shown[pair["theme_b"]]
        words = ", ".join(pair["words"])
        # Both orders of the pair name the same words, as the engine lists them.
        assert f"{a} and {b} share: {words}" in said, pair
        assert f"{b} and {a} share: {words}" in said, pair
    assert all(title in ("", "no word in common") or " share: " in title for title in titles)
    assert problems == []
    page.close()
