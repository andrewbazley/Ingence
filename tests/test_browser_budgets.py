"""Time budgets for the Dashboard on the sample collection's two larger tiers.

The tests are skipped unless asked for. The ordinary suite never runs this file: a slow machine
must not fail it. Ask with ``INGENCE_BROWSER_BUDGETS=1`` and name the projects to measure:

- ``INGENCE_BUDGET_PROJECT_M``: a project made from the sample collection's tier M (about ten
  thousand files);
- ``INGENCE_BUDGET_PROJECT_L``: the same from tier L (about a hundred thousand files).

A tier whose variable is not set is skipped on its own. To make a project (``sample`` is any new
folder; use ``tier='L'`` for the large one)::

    python -c "from pathlib import Path; from ingence.evaluation.sample_lab import \\
        generate_sample_lab; generate_sample_lab(Path('sample'), tier='M', seed=1)"
    ingence init sample/project --root sample/pool --label sample \\
        --domain microscopy --domain biochemistry --domain computational --domain sequencing
    ingence index -p sample/project --quiet
    ingence dive -p sample/project --budget 1500
    ingence infer -p sample/project

The ``--domain`` options are the packs ``ingence demo`` loads (``words().pack_names()`` in
``ingence.evaluation.sample_lab_words`` lists them); the budgets and the pictures of the visual lane
were measured on a project made with them. The test serves a copy of the project, so what it was
given is not written to.

What is measured, in the browser, from the moment the person acts to the moment the page has
drawn what they asked for and the browser has painted twice since:

- ``first_paint``: opening the Dashboard on the Years view, until its first row is drawn (time since
  the page began to load);
- ``view_switch``: from the Years view to the Inventory section, until its first bar is drawn;
- ``scale_change``: from the project to its first row's person in the Years view, until the page
  names that person and has drawn their rows.

``first_paint_cold`` is the first of these on a server that has just started, which reads
everything for the first time. The others are taken ``RUNS`` times after it in new pages on the
same server, and their median is compared with the budget, so one slow moment on a shared machine
does not fail the test and a page that is slow every time does.

How the budgets were set. Each measurement was taken five times in a row, at one load, each
time with the load written beside it (``INGENCE_BUDGET_RECORD`` keeps every figure with its load as
a line of JSON). Five servers were started one after another for the cold one. A budget is the
slowest of the five, doubled, rounded up to the next half second; ``BUDGETS`` says the load and the
date of each tier's five. A figure taken while the machine's one-minute load average is above
``MAX_LOAD_TO_SET_A_BUDGET`` is not used to set a budget: other work on the laptop moves a
page's time by more than the margin. A budget is a limit the page should stay inside on the
laptop it was measured on at about that load, not a promise about another machine. To change one,
measure again with ``INGENCE_BUDGET_PRINT=1`` (the figures are printed with their loads and
nothing is asserted) and write the new figure with its load.

To set a budget again, on a quiet machine (one-minute load average of at most
``MAX_LOAD_TO_SET_A_BUDGET``; ``uptime`` says), take five measurements and let the rule read them::

    for run in 1 2 3 4 5; do
        INGENCE_BROWSER_BUDGETS=1 INGENCE_BUDGET_PRINT=1 INGENCE_BUDGET_RECORD=record.jsonl \
        INGENCE_BUDGET_PROJECT_M=sample/project \
        python -m pytest tests/test_browser_budgets.py -k medium -q -s
    done
    PYTHONPATH=tests python -m fixtures.budget_rule record.jsonl

It prints each tier's budgets with the slowest figure each came from and the loads; it refuses
figures taken above the limit. Write them into ``BUDGETS`` with their loads and the date.
(For tier L use ``INGENCE_BUDGET_PROJECT_L`` and ``-k large``.)

When the machine is busy the test is skipped, with the load in its reason, rather than failed:
above ``MAX_LOAD_TO_JUDGE`` a slow page says nothing about the page. Set
``INGENCE_BUDGET_ANY_LOAD=1`` to judge anyway.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from fixtures.dashboard_browser import (
    browser_name,
    launch_browser,
    need,
    settled,
    start_dashboard,
    stop,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

sync_api = need("playwright.sync_api", "the browser extra is not installed")

ASKED = os.environ.get("INGENCE_BROWSER_BUDGETS") == "1"
PRINT_ONLY = os.environ.get("INGENCE_BUDGET_PRINT") == "1"
#: A file to which each tier's figures are appended, one line of JSON each, with their loads.
RECORD = os.environ.get("INGENCE_BUDGET_RECORD")
ANY_LOAD = os.environ.get("INGENCE_BUDGET_ANY_LOAD") == "1"

#: The one-minute load average above which a figure is not used to set a budget.
MAX_LOAD_TO_SET_A_BUDGET = 4.0
#: The load average above which the test is skipped rather than judged: a budget set at a load
#: of at most 4 carries a margin of two, and a machine busier than twice that proves nothing.
MAX_LOAD_TO_JUDGE = 8.0

pytestmark = pytest.mark.skipif(
    not ASKED,
    reason="time budgets run only when asked: set INGENCE_BROWSER_BUDGETS=1 and "
    "INGENCE_BUDGET_PROJECT_M / INGENCE_BUDGET_PROJECT_L",
)

#: Seconds a page may take, by tier. Set from five measurements each (see the module's note), taken
#: one after another on 14 October 2026 on an 8-core laptop, while a training job held about two
#: cores. So the load was above ``MAX_LOAD_TO_SET_A_BUDGET``: the limit was raised for these, with
#: ``--max-load``, and each figure's load is written here. The build is the one before the Years
#: inspector cards were added. Re-take them on a quiet machine with the recipe in the module's
#: note.
#:
#: M, load 3.2 to 7.2: first paint cold 15.3 to 16.4 s, first paint 1.43 to 1.60 s, view switch
#: 0.112 to 0.115 s and scale change 0.034 to 0.049 s.
#:
#: L, load 3.8 to 8.2: first paint cold 98.8 to 114.5 s, first paint 9.5 to 11.8 s, view switch
#: 0.21 to 0.27 s and scale change 0.089 to 0.104 s.
#:
#: A budget is the slowest of the five, doubled, rounded up to the next half second.
BUDGETS: dict[str, dict[str, float]] = {
    "M": {
        "first_paint_cold": 33.0,
        "first_paint": 3.5,
        "view_switch": 0.5,
        "scale_change": 0.5,
    },
    "L": {
        "first_paint_cold": 229.5,
        "first_paint": 24.0,
        "view_switch": 1.0,
        "scale_change": 0.5,
    },
}

#: How many times each measurement is taken before the median is compared.
RUNS = 3

#: Only bounds a page that never draws. Every wait returns the moment its state is reached.
HANG_GUARD_MS = 600_000


def _served(tier: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    named = os.environ.get(f"INGENCE_BUDGET_PROJECT_{tier}")
    if not named:
        pytest.skip(f"INGENCE_BUDGET_PROJECT_{tier} names no project")
    base = tmp_path_factory.mktemp(f"budget_{tier}")
    project = base / "project"
    shutil.copytree(Path(named), project)
    config_home = base / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(project, config_home)
    try:
        yield address
    finally:
        stop(process)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def served_m(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _served("M", tmp_path_factory)


@pytest.fixture(scope="module")
def served_l(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _served("L", tmp_path_factory)


#: Run in the page: act, then wait until a selector the action makes appear is in the page and the
#: browser has painted twice. Returns the milliseconds from the act to then.
_ACT_AND_WAIT = """
async ([act, done]) => {
  const started = performance.now();
  if (act) document.querySelector(act).click();
  await new Promise((resolve) => {
    const seen = () => document.querySelector(done) !== null;
    if (seen()) { resolve(); return; }
    const watcher = new MutationObserver(() => {
      if (seen()) { watcher.disconnect(); resolve(); }
    });
    watcher.observe(document, {subtree: true, childList: true, attributes: true});
  });
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return performance.now() - started;
}
"""

#: The same for a page that begins loading: the time since it began, when the first row is drawn.
_FIRST_ROW = """
async () => {
  await new Promise((resolve) => {
    const seen = () => document.querySelector('.years .yrow') !== null;
    if (seen()) { resolve(); return; }
    const watcher = new MutationObserver(() => {
      if (seen()) { watcher.disconnect(); resolve(); }
    });
    watcher.observe(document, {subtree: true, childList: true, attributes: true});
  });
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return performance.now();
}
"""


def measure(browser: Any, base: str) -> dict[str, float]:
    """One run of the three measurements, in seconds, in a new page."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(f"{base}#years")
        first_paint = page.evaluate(_FIRST_ROW) / 1000
        # The project's card names the project, and the rows are the people.
        page.wait_for_function(
            "document.querySelector('#detail .ykicker')"
            " && document.querySelector('#detail .ykicker').textContent.trim() === 'The project'",
            timeout=HANG_GUARD_MS,
        )
        # One scale down: the right arrow on the first row opens its person (Enter is the
        # inspector). The keys act on the list, so it is given the focus once the page is quiet:
        # a key pressed while the rows are still being drawn is lost, and the wait below would
        # never end.
        settled(page)
        page.focus(".ylist")
        was = page.evaluate("document.querySelector('.years h2').textContent")
        page.keyboard.press("ArrowDown")
        started = page.evaluate("performance.now()")
        page.keyboard.press("ArrowRight")
        page.wait_for_function(
            "(was) => { const heading = document.querySelector('.years h2');"
            " return heading && heading.textContent !== was"
            " && document.querySelector('.years .yrow') !== null; }",
            arg=was,
            timeout=HANG_GUARD_MS,
        )
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        scale_change = (page.evaluate("performance.now()") - started) / 1000
        # A new section: the Inventory's first bar.
        view_switch = (
            page.evaluate(
                _ACT_AND_WAIT,
                ['nav.sections button[data-view="inventory"]', "#detail .bar-label"],
            )
            / 1000
        )
        return {
            "first_paint": first_paint,
            "view_switch": view_switch,
            "scale_change": scale_change,
        }
    finally:
        page.close()


def _check(browser: Any, base: str, tier: str) -> None:
    # The first run on a server that has just started reads everything cold: it is a measurement of
    # its own (a person's first opening), and the runs after it are the warm ones. The load average
    # is read just before each run and kept beside its figures.
    runs: list[tuple[float, dict[str, float]]] = []
    for _ in range(1 + RUNS):
        load = os.getloadavg()[0]
        runs.append((load, measure(browser, base)))
    (cold_load, cold), warm = runs[0], runs[1:]
    seen = {name: statistics.median(run[name] for _, run in warm) for name in warm[0][1]}
    seen["first_paint_cold"] = cold["first_paint"]
    # A figure's load is the busiest of the runs it comes from.
    loads = {name: max(load for load, _ in warm) for name in warm[0][1]}
    loads["first_paint_cold"] = cold_load
    busiest = max(loads.values())
    print(f"\ntier {tier} (warm ones the median of {RUNS}), seconds at one-minute load:")
    for name, value in seen.items():
        print(f"  {name}: {value:.3f} s at load {loads[name]:.1f}")
    if RECORD:
        line = {
            "tier": tier,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "browser": browser_name(),
            "figures": {
                name: {"seconds": round(seen[name], 4), "load": loads[name]} for name in seen
            },
            "runs": [
                {"load": load, **{name: round(value, 4) for name, value in run.items()}}
                for load, run in runs
            ],
        }
        with Path(RECORD).open("a", encoding="utf-8") as kept:
            kept.write(json.dumps(line) + "\n")
    if PRINT_ONLY:
        if busiest > MAX_LOAD_TO_SET_A_BUDGET:
            print(
                f"  NOT FOR A BUDGET: the load reached {busiest:.1f}, above "
                f"{MAX_LOAD_TO_SET_A_BUDGET:g}; measure again when the machine is quiet"
            )
        return
    assert tier in BUDGETS, f"no budget is set for tier {tier}: measure with INGENCE_BUDGET_PRINT=1"
    over = {
        name: (round(value, 2), BUDGETS[tier][name])
        for name, value in seen.items()
        if value > BUDGETS[tier][name]
    }
    if over and busiest > MAX_LOAD_TO_JUDGE and not ANY_LOAD:
        pytest.skip(
            f"over budget (measured, budget) {over}, but the machine was busy (load "
            f"{busiest:.1f}, above {MAX_LOAD_TO_JUDGE:g}): that says nothing about the page; "
            "run again when it is quiet"
        )
    assert not over, f"over budget (measured, budget) on tier {tier} at load {busiest:.1f}: {over}"


def test_the_budgets_hold_on_the_medium_tier(browser: Any, served_m: str) -> None:
    _check(browser, served_m, "M")


def test_the_budgets_hold_on_the_large_tier(browser: Any, served_l: str) -> None:
    _check(browser, served_l, "L")
