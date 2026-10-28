"""Topics driven in a real browser.

A topic is a person's name for a set of the engine's groupings (experiments, groups of shared
words, lines of work). It is the person's statement in the Record and a projection on the views,
never a claim. On the invented theme pool (``fixtures.topic_project``: six planted lines of work
over dated experiments of three invented people, every word invented), served by ``ingence see``
on a copy of the project, with who decides set before the page opens (the one dialog that asks is
tested elsewhere):

1. Making one: three stacks on Groupings are picked, "Add to topic..." makes a new topic by name,
   and Explore > Topics reads it: its members ("word group"), what each comes to, rename, take one
   member out, withdraw.
2. On the views: Years narrows to the topic's experiments (the lens) and names a row's topics
   ("also in" chips). The Wheel draws its ring and its key. The Map's legend switch draws its
   outline (as its own owner's) and "Show on the Map" opens the map fitted to it.
3. Add to topic from the other views: Years rows and the Map (shift-click and a box with Shift
   held), to a topic that exists and to a new one.
4. Export files (CSV) downloads the topic's files (the header, the byte order mark, a row for each
   file) and shows the file's notes beside the button.
5. The Graph workspace says topic where it names a person's own set.
6. Colors: a pick in progress is drawn in the accent, a recorded topic in the magenta family.
7. axe passes on Explore > Topics, in light and in dark, and with the chooser open.

Skipped where the ``browser`` extra, a browser or the themes extra is missing (a failure where
``INGENCE_BROWSER_REQUIRED=1``). Every served Dashboard keeps who decides in a folder of the test's
own. Every name is invented.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.request
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
need("sklearn", "themes need the themes extra")

STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
#: The first full read of a cold server draws the map's layout, which takes minutes when loaded.
SLOW = 300_000
ACTOR = "Dana Whitfield"
CSV_HEADER = "path,root,name,stage,stage_state,experiment,experiment_id,root_id"


def _axe_script() -> Path | None:
    named = os.environ.get("INGENCE_AXE_JS")
    for candidate in (
        Path(named) if named else None,
        REPOSITORY / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        launched = launch_browser(playwright)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Any:
    from fixtures.topic_project import build_topic_project

    return build_topic_project(tmp_path_factory.mktemp("topics") / "built")


@pytest.fixture
def served(built: Any, tmp_path: Path) -> Iterator[str]:
    """A Dashboard on a copy of the project, with who decides already named."""
    project = tmp_path / "project"
    shutil.copytree(built.project, project)
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    process, base = start_dashboard(project, config_home)
    try:
        _post(base, "api/decider", {"decider": ACTOR})
        yield base
    finally:
        stop(process)


# ---- helpers


def _post(base: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Ingence-Write": "1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as answer:
        return json.loads(answer.read())


def _get(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}{path}", timeout=300) as answer:
        return json.loads(answer.read())


def _topics(base: str) -> list[dict[str, Any]]:
    """Every topic as the engine reads it now (``GET /api/v1/topics``, the route's own numbers)."""
    return _get(base, "api/v1/topics?limit=0&suggestions=0&compare=none")["topics"]


def _topic(base: str, name: str) -> dict[str, Any]:
    (found,) = [topic for topic in _topics(base) if topic["name"] == name]
    return found


def _make_topic(base: str, name: str, members: list[dict[str, Any]]) -> str:
    """A topic written through the overlay's one batch, as the chooser writes it; its identifier."""
    written = _post(
        base,
        "api/review/answer",
        {"actor": ACTOR, "overlay": [{"kind": "topic", "name": name, "members": members}]},
    )
    return str(written["events"][0]["target"]).split(":")[2]


def _experiments(built: Any, theme: str) -> list[dict[str, str]]:
    """The planted theme's experiments as topic members."""
    return [
        {"kind": "experiment", "id": built.ids[unit]}
        for unit in sorted(built.ids)
        if built.plant.planted.get(unit) == theme
    ]


def _new_page(browser: Any, scheme: str = "light", width: int = 1440) -> Any:
    context = browser.new_context(viewport={"width": width, "height": 1100}, color_scheme=scheme)
    page = context.new_page()
    page.on("pageerror", lambda error: pytest.fail(f"the page raised: {error}"))
    return page


def _open(page: Any, base: str, address: str, ready: str) -> None:
    """Open a view and wait until what it draws is there and the page asks for nothing more."""
    page.goto(f"{base}{address}")
    page.wait_for_selector(ready, timeout=HANG_GUARD_MS)
    settled(page)


def _open_topics(page: Any, base: str) -> None:
    _open(page, base, "#topics", ".topic-card, .state-card")


def _open_years(page: Any, base: str, *, topics: bool = True) -> None:
    """Years at the project, its rows drawn and, where a topic exists, the topics read."""
    _open(page, base, "#years", ".yrow")
    if topics:
        page.wait_for_selector("select.ylens", timeout=HANG_GUARD_MS)


def _open_person(page: Any) -> None:
    """One scale down: the first area's experiments, under their lines."""
    page.locator(".yrow").first.dblclick()
    page.wait_for_selector(".yrow[data-pick]", timeout=HANG_GUARD_MS)
    settled(page)


def _open_map(page: Any, base: str, *, topics: bool = True) -> None:
    """The map drawn and, where a topic exists, the topics read (their switches in the legend). The
    layout is read once, patiently, so a later read is answered from the held one."""
    page.request.get(f"{base}api/v1/themes/map", timeout=SLOW)
    _open(page, base, "#map", ".map-dot")
    if topics:
        page.wait_for_selector(".map-topic-key", timeout=HANG_GUARD_MS)
    settled(page)


def _chooser(page: Any, *, new: str | None = None, existing: str | None = None) -> None:
    """Fill the "Add to a topic" chooser: a new topic by name, or the one that begins with
    ``existing``; and submit."""
    dialog = page.locator("dialog.topic-chooser")
    dialog.wait_for()
    if existing is not None:
        options = dialog.locator("#topic-choice option").all_inner_texts()
        label = next(option for option in options if option.startswith(existing))
        dialog.locator("#topic-choice").select_option(label=label)
    else:
        dialog.locator("#topic-new-name").fill(str(new))
    dialog.locator("button[type=submit]").click()
    page.wait_for_selector("dialog.topic-chooser", state="detached")


def _said(page: Any) -> str:
    """The quiet line a write leaves at the foot of the page, once it says something."""
    page.wait_for_function(
        "() => { const line = document.querySelector('.topic-say');"
        " return line && !line.hidden"
        " && /saved under|Nothing changed/.test(line.innerText); }",
        timeout=HANG_GUARD_MS,
    )
    return str(page.locator(".topic-say").inner_text())


def _color_of(page: Any, expression: str) -> str:
    """What a CSS color value (a variable, say) computes to, as ``rgb(...)``."""
    return str(
        page.evaluate(
            "value => { const probe = document.createElement('span'); probe.style.color = value;"
            " document.body.append(probe); const found = getComputedStyle(probe).color;"
            " probe.remove(); return found; }",
            expression,
        )
    )


def _hue(rgb: str) -> float:
    import colorsys

    red, green, blue = (int(part) / 255 for part in re.findall(r"\d+", rgb)[:3])
    return colorsys.rgb_to_hls(red, green, blue)[0] * 360


def _confirm(page: Any) -> None:
    """The primary button of the dialog that is open (Withdraw it, Take it out)."""
    dialog = page.locator("dialog[open]")
    dialog.wait_for()
    dialog.locator("button.primary").click()
    page.wait_for_selector("dialog[open]", state="detached")


# ---- 1. making one


def test_a_topic_is_made_from_three_stacks_and_read_on_the_topics_page(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    page.goto(f"{served}#classes")
    page.wait_for_selector(".cascade .topic-pick input", timeout=HANG_GUARD_MS)
    settled(page)
    stacks = page.locator(".cascade")
    words = [str(stacks.nth(number).get_attribute("data-terms")) for number in range(3)]
    assert len(set(words)) == 3
    for number in range(3):
        stacks.nth(number).locator(".topic-pick input").check()
    bar = page.locator(".topic-pickbar")
    assert "3 word groups picked" in bar.inner_text()
    assert page.locator(".cascade.topic-picked").count() == 3
    bar.get_by_role("button", name="Add to collection...").click()
    _chooser(page, new="Bench imaging")
    assert "Added 3 items to the collection Bench imaging" in _said(page)
    # What was added is no longer picked: the stacks are drawn again, with none ticked.
    page.wait_for_selector(".topic-pickbar", state="detached", timeout=HANG_GUARD_MS)
    settled(page)
    assert page.locator(".cascade.topic-picked").count() == 0

    # Experiments > Collections: the button stands after Experiments, and the page
    # reads what was made.
    page.locator('#pages button[data-page="experiments"]').click()
    page.locator('nav.sections button[data-view="topics"]').click()
    page.wait_for_selector(".topic-card")
    settled(page)
    labels = page.locator('nav.sections[data-sections-of="experiments"] button').all_inner_texts()
    assert labels == ["Experiments", "Collections"]
    assert page.locator('nav.sections button[data-view="topics"].active').count() == 1
    assert page.locator(".topic-row .topic-name").all_inner_texts() == ["Bench imaging"]
    served_topic = _topic(served, "Bench imaging")
    rows = page.locator(".topic-members tbody tr")
    assert rows.count() == 3
    for member in sorted(served_topic["members"], key=lambda m: m["name"]):
        assert member["kind"] == "core" and member["state"] == "resolved"
        row = page.locator(f'.topic-members tr[data-member="{member["key"]}"]')
        assert "word group" in row.inner_text()
        # What each comes to is the route's own number, said as the page says it.
        assert row.locator("td.topic-comes").inner_text().strip() == (
            f"{member['experiments']} experiment{'' if member['experiments'] == 1 else 's'}"
        )
    assert {tuple(m["terms"]) for m in served_topic["members"]} == {
        tuple(sorted(word.split())) for word in words
    }
    heading = page.locator(
        ".topic-card h3", has_text=re.compile("experiments \\(", re.I)
    ).inner_text()
    counted = (
        f"{served_topic['experiments_count']} of {served_topic['experiments_out_of']} in the scan"
    )
    assert counted in heading.lower()
    assert "You made this collection, and it is in your answer log" in (
        page.locator(".topic-card").inner_text()
    )
    page.context.close()


def test_a_topic_is_renamed_a_member_taken_out_and_the_topic_withdrawn(
    browser: Any, served: str, built: Any
) -> None:
    experiment = _experiments(built, "t1")[0]
    _make_topic(
        served,
        "Mixed kinds",
        [
            experiment,
            {"kind": "core", "terms": ["quolt", "sornel"]},
            {
                "kind": "line",
                "experiments": [m["id"] for m in _experiments(built, "t2")[:3]],
                "words": ["miravo", "pellit"],
                "label": "a line",
            },
        ],
    )
    page = _new_page(browser)
    _open_topics(page, served)
    assert page.locator(".topic-members tbody tr").count() == 3
    kinds = page.locator(".topic-members tbody tr td:first-child").all_inner_texts()
    assert sorted(kinds) == ["experiment", "theme", "word group"]

    # Rename: only the name changes, and every kind of member stays.
    page.locator('.topic-card [data-help="topics.rename"]').click()
    dialog = page.locator("dialog[open]")
    dialog.locator("input[type=text]").fill("Mixed kinds, renamed")
    dialog.locator("button[type=submit]").click()
    page.wait_for_selector(".topic-row .topic-name:text-is('Mixed kinds, renamed')")
    settled(page)
    renamed = _topic(served, "Mixed kinds, renamed")
    assert sorted(member["kind"] for member in renamed["members"]) == [
        "core",
        "experiment",
        "line",
    ]

    # Take one member out: the group of words leaves, and the others stay as they were.
    core = page.locator(".topic-members tbody tr", has_text="word group")
    core.locator('[data-help="topics.remove_member"]').click()
    _confirm(page)
    page.wait_for_function(
        "() => document.querySelectorAll('.topic-members tbody tr').length === 2",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    assert sorted(m["kind"] for m in _topic(served, "Mixed kinds, renamed")["members"]) == [
        "experiment",
        "line",
    ]
    assert not (page.locator(".topic-members tbody tr", has_text="word group").count()), (
        "the member that was taken out is gone"
    )
    # The Record kept what was said: the topic's earlier statements are counted on its card.
    assert "after 2 earlier changes" in page.locator(".topic-card").inner_text()

    # Withdraw: the topic stops being drawn; the page says there is none.
    page.get_by_role("button", name="Withdraw").click()
    _confirm(page)
    page.wait_for_selector(".state-card .state-sentence:text-is('No collections yet.')")
    assert _topics(served) == []
    assert page.locator(".topic-row").count() == 0
    page.context.close()


def test_the_last_member_of_a_topic_cannot_be_taken_out(
    browser: Any, served: str, built: Any
) -> None:
    _make_topic(served, "One only", _experiments(built, "t0")[:1])
    page = _new_page(browser)
    _open_topics(page, served)
    button = page.locator('.topic-members [data-help="topics.remove_member"]')
    assert button.is_disabled()
    assert "Withdraw the collection" in (button.get_attribute("title") or "")
    page.context.close()


# ---- 2. on the views


def test_years_narrows_to_a_topic_and_names_the_topics_of_a_row(
    browser: Any, served: str, built: Any
) -> None:
    held = _experiments(built, "t3")
    units = [u for u in sorted(built.ids) if built.plant.planted.get(u) == "t3"]
    people = {built.plant.people[unit] for unit in units}
    assert 1 <= len(people) < 3, "the planted line belongs to some of the people, not all"
    _make_topic(served, "Fourth line", held)
    page = _new_page(browser)
    _open_years(page, served)
    everyone = page.locator(".yrow").count()
    assert everyone == 3, "the project's rows are its three people"

    # A row names the topics its experiments are in, with how many of its experiments.
    chips = page.locator(".yrow .topic-chip")
    assert chips.count() == len(people)
    first = chips.first
    assert re.fullmatch(r"Fourth line \d+/\d+", first.inner_text().strip())
    assert page.locator(".yrow .topic-chips").first.inner_text().startswith("also in")

    # The lens: only the topic's experiments, at every level; the ladder is unchanged.
    select = page.locator("select.ylens")
    select.select_option(label=f"Fourth line ({len(held)})")
    page.wait_for_function(
        f"() => document.querySelectorAll('.yrow').length === {len(people)}",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    shown = page.locator(".yrow .ysub").all_inner_texts()
    assert all("experiment" in text for text in shown)
    assert sum(int(re.match(r"(\d+) experiment", text).group(1)) for text in shown) == len(held)
    # An area's rows are its experiments under their lines' headings.
    page.locator(".yrow").first.dblclick()
    page.wait_for_selector(".yrow[data-pick]")
    settled(page)
    lines = page.locator(".years .yhead-row")
    assert lines.count() == 1, "one line of work holds the topic's experiments"
    # One click clears the lens.
    page.get_by_role("button", name="Clear the collection").click()
    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-help='years.lens_clear']\").length === 0",
        timeout=HANG_GUARD_MS,
    )
    settled(page)
    assert page.locator("select.ylens").input_value() == ""
    assert page.locator(".years .yhead-row").count() >= 1
    page.context.close()


def test_the_wheel_draws_a_ring_for_a_topic_and_names_it_in_its_key(
    browser: Any, served: str, built: Any
) -> None:
    held = _experiments(built, "t2")
    _make_topic(served, "Third line", held)
    _make_topic(served, "Fifth line", _experiments(built, "t4"))
    page = _new_page(browser)
    _open_years(page, served)
    page.get_by_role("button", name="Wheel (W)").click()
    page.wait_for_selector(".ywheel path.wtopic", timeout=HANG_GUARD_MS)
    settled(page)
    rings = page.locator(".ywheel path.wtopic")
    ids = {rings.nth(n).get_attribute("data-topic") for n in range(rings.count())}
    assert ids == {topic["id"] for topic in _topics(served)}, "a ring for each topic"
    key = page.locator(".ywheeltopics")
    assert "Third line" in key.inner_text() and "Fifth line" in key.inner_text()
    assert f"{len(held)} experiments" in key.inner_text()
    title = rings.first.locator("title").text_content() or ""
    assert title.startswith("Your collection ") and " experiments here" in title
    # The rings stand outside the rim: each ring's outer radius (the first arc of its path) is
    # beyond the outer radius of every arc of the Wheel itself.
    reach = page.evaluate(
        "() => { const outer = (path) => Number(/A ([0-9.]+) /.exec(path.getAttribute('d'))[1]);"
        " const own = [...document.querySelectorAll('.ywheel path:not(.wtopic)')].map(outer);"
        " const band = [...document.querySelectorAll('.ywheel path.wtopic')].map(outer);"
        " return {rim: Math.max(...own), band: Math.min(...band)}; }"
    )
    assert reach["band"] > reach["rim"]
    page.context.close()


def test_the_map_outlines_a_topic_from_its_legend_and_opens_fitted_to_it(
    browser: Any, served: str, built: Any
) -> None:
    held = _experiments(built, "t1")
    _make_topic(served, "Second line", held)
    page = _new_page(browser)
    _open_map(page, served)
    switch = page.locator(".map-topic-key", has_text="Second line")
    assert re.search(r"· \d+ of 10 placed", switch.inner_text())
    assert page.locator('path.map-outline[data-owner="topics"]').count() == 0
    switch.locator("input").check()
    page.wait_for_selector('path.map-outline[data-owner="topics"]')
    assert page.locator('path.map-outline[data-owner="topics"]').count() == 1
    # A topic's outline is its owner's: the groups' legend line and card never list it.
    assert page.locator(".map-outline-legend").count() == 0
    assert page.locator(".map-groups").count() == 0
    switch.locator("input").uncheck()
    page.wait_for_function(
        "() => document.querySelectorAll('path.map-outline').length === 0", timeout=HANG_GUARD_MS
    )

    # From the topic's card: "Show on the Map" opens the map with the outline drawn.
    _open_topics(page, served)
    page.get_by_role("button", name="Show on the Map").click()
    page.wait_for_selector('path.map-outline[data-owner="topics"]', timeout=SLOW)
    settled(page)
    assert page.url.endswith("#map")
    assert page.locator(".map-topic-key", has_text="Second line").locator("input").is_checked()
    page.context.close()


# ---- 3. add from the views


def test_rows_picked_on_years_are_added_to_a_topic_that_exists(
    browser: Any, served: str, built: Any
) -> None:
    first = _experiments(built, "t0")[:2]
    _make_topic(served, "Already made", first)
    before = _topic(served, "Already made")
    page = _new_page(browser)
    _open_years(page, served)
    _open_person(page)
    page.get_by_role("button", name="Pick for a collection").click()
    boxes = page.locator(".ypickbox")
    assert boxes.count() >= 1
    # An area's rows are experiments: pick one the topic does not hold yet.
    picks = page.locator(".yrow[data-pick]").evaluate_all("rows => rows.map(r => r.dataset.pick)")
    held = {member["id"] for member in first}
    fresh = next(at for at, key in enumerate(picks) if key.split(":", 1)[1] not in held)
    boxes.nth(fresh).check()
    bar = page.locator(".topic-pickbar")
    assert "1 row picked" in bar.inner_text()
    bar.get_by_role("button", name="Add to collection...").click()
    _chooser(page, existing="Already made")
    assert "Added 1 item to the collection Already made" in _said(page)
    after = _topic(served, "Already made")
    assert len(after["members"]) == len(before["members"]) + 1
    assert "experiment" in {member["kind"] for member in after["members"]}
    assert {m["key"] for m in before["members"]} <= {m["key"] for m in after["members"]}, (
        "every member that was there stays"
    )
    assert page.locator(".topic-pickbar").count() == 0
    # Adding what the topic holds says so and changes nothing.
    page.locator(".ypickbox").nth(fresh).check()
    page.get_by_role("button", name="Add to collection...").click()
    _chooser(page, existing="Already made")
    page.wait_for_function(
        "() => /already in the collection/.test(document.querySelector('.topic-say').innerText)",
        timeout=HANG_GUARD_MS,
    )
    assert len(_topic(served, "Already made")["members"]) == len(after["members"])
    page.context.close()


def test_dots_picked_on_the_map_with_shift_are_added_to_a_new_topic(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_map(page, served, topics=False)
    # Two dots a pointer can reach (another dot may lie over a dot), well apart.
    reachable = page.evaluate(
        "() => [...document.querySelectorAll('.map-dot')].map((dot) => {"
        "   const b = dot.getBoundingClientRect();"
        "   const x = b.left + b.width / 2; const y = b.top + b.height / 2;"
        "   return {id: dot.dataset.id, x, y, hit: document.elementFromPoint(x, y) === dot}; })"
        "  .filter((dot) => dot.hit)"
    )
    first = reachable[0]
    second = next(d for d in reachable if abs(d["x"] - first["x"]) + abs(d["y"] - first["y"]) > 40)
    ids = [first["id"], second["id"]]
    page.keyboard.down("Shift")
    page.mouse.click(first["x"], first["y"])
    page.mouse.click(second["x"], second["y"])
    page.keyboard.up("Shift")
    page.wait_for_selector(".topic-pickbar")
    assert "2 dots picked" in page.locator(".topic-pickbar").inner_text()
    assert page.locator(".map-dot.topic-picked").count() == 2
    page.get_by_role("button", name="Add to collection...").click()
    _chooser(page, new="From the map")
    assert "Added 2 items to the collection From the map" in _said(page)
    held = _topic(served, "From the map")
    assert sorted(held["experiment_ids"]) == sorted(ids)
    assert {member["kind"] for member in held["members"]} == {"experiment"}
    assert page.locator(".map-dot.topic-picked").count() == 0
    # The legend gains the new topic's switch.
    page.wait_for_selector(".map-topic-key:has-text('From the map')")
    page.context.close()


def test_a_box_drawn_on_the_map_with_shift_picks_the_dots_in_it(browser: Any, served: str) -> None:
    page = _new_page(browser)
    _open_map(page, served, topics=False)
    box = page.evaluate(
        "() => { const plot = document.querySelector('.map-svg').getBoundingClientRect();"
        " const dots = [...document.querySelectorAll('.map-dot')].map((d) => {"
        "   const b = d.getBoundingClientRect(); return {id: d.dataset.id,"
        "     x: b.left + b.width / 2, y: b.top + b.height / 2}; });"
        " const x0 = plot.left + plot.width * 0.05; const y0 = plot.top + plot.height * 0.05;"
        " const x1 = plot.left + plot.width * 0.55; const y1 = plot.top + plot.height * 0.55;"
        " const within = (d, m) => d.x >= x0 + m && d.x <= x1 - m"
        "   && d.y >= y0 + m && d.y <= y1 - m;"
        " return {x0, y0, x1, y1,"
        "   inside: dots.filter((d) => within(d, 3)).map((d) => d.id),"
        "   reach: dots.filter((d) => within(d, -3)).map((d) => d.id)}; }"
    )
    assert box["inside"], "the box holds some dots"
    page.keyboard.down("Shift")
    page.mouse.move(box["x0"], box["y0"])
    page.mouse.down()
    page.mouse.move(box["x1"], box["y1"], steps=8)
    page.mouse.up()
    page.keyboard.up("Shift")
    page.wait_for_selector(".topic-pickbar")
    picked = page.locator(".map-dot.topic-picked")
    ids = sorted(str(picked.nth(n).get_attribute("data-id")) for n in range(picked.count()))
    # Every dot inside the box is picked and none outside it (a dot on the edge may go either way).
    assert set(box["inside"]) <= set(ids) <= set(box["reach"])
    # A plain box still zooms, and picks nothing more.
    assert page.locator(".topic-pickbar").inner_text().startswith(f"{len(ids)} dot")
    page.context.close()


# Ask lists experiments and picks none, so no test picks results in search for a topic.


# ---- 4. the CSV


def test_the_topics_files_download_as_csv_with_a_byte_order_mark_and_its_notes(
    browser: Any, served: str, built: Any
) -> None:
    held = _experiments(built, "t5")
    _make_topic(served, "Sixth line", held)
    page = _new_page(browser)
    _open_topics(page, served)
    assert page.locator(".topic-export-notes").is_hidden()
    with page.expect_download() as taken:
        page.get_by_role("button", name="Export files (CSV)").click()
    download = taken.value
    assert re.fullmatch(
        r"ingence-topic-sixth-line-\d{4}-\d{2}-\d{2}\.csv", download.suggested_filename
    )
    raw = Path(str(download.path())).read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "a byte order mark, so a spreadsheet reads the encoding"
    lines = raw[3:].decode("utf-8").splitlines()
    assert lines[0] == CSV_HEADER
    files = _topic(served, "Sixth line")["files_count"]
    assert len(lines) - 1 == files > 0
    assert all(line.split(",")[0] for line in lines[1:])
    notes = page.locator(".topic-export-notes")
    notes.wait_for()
    text = notes.inner_text()
    assert "about the file you saved" in text.lower()
    for said in ("leading apostrophe", "byte order mark", "root column"):
        assert said in text, said
    page.context.close()


# ---- 5. the Graph workspace


def test_the_graph_workspace_says_topic_where_it_names_a_persons_own_set(
    browser: Any, served: str, built: Any
) -> None:
    _make_topic(served, "Workspace topic", _experiments(built, "t0")[:2])
    page = _new_page(browser)
    page.goto(f"{served}#explorer")
    page.wait_for_selector(".ix-canvas-wrap", timeout=HANG_GUARD_MS)
    page.locator(".ix-canvas-wrap").focus()
    page.keyboard.press("t")
    table = page.locator("table.ix-table")
    table.wait_for(timeout=HANG_GUARD_MS)
    table.locator("button.ix-link").first.click()
    inspector = page.locator(".ix-inspector")
    inspector.locator(".ix-layer").wait_for(timeout=HANG_GUARD_MS)
    layer = inspector.locator(".ix-layer").inner_text()
    assert re.search(r"^Topics\b", layer, re.M | re.I)
    assert inspector.get_by_role("button", name="New topic…").count() == 1
    assert inspector.get_by_role("combobox").filter(has_text="Add to a topic…").count() == 1
    assert "Workspace topic" in inspector.locator("select").first.inner_text()
    everything = page.locator("body").inner_text() + " ".join(
        inspector.locator("[aria-label], [placeholder]").evaluate_all(
            "nodes => nodes.map(n => (n.getAttribute('aria-label') || '') + ' '"
            " + (n.getAttribute('placeholder') || ''))"
        )
    )
    for retired in ("New group", "Add to a group", "a new group's name", "Your group"):
        assert retired not in everything, retired
    inspector.get_by_role("button", name="New topic…").click()
    assert inspector.get_by_placeholder("a new topic's name").count() == 1
    page.context.close()


# ---- 6. colors


def test_a_pick_is_drawn_in_the_accent_and_a_recorded_topic_in_the_magenta_family(
    browser: Any, served: str
) -> None:
    page = _new_page(browser)
    _open_years(page, served, topics=False)
    _open_person(page)
    accent = _color_of(page, "var(--accent)")
    person = _color_of(page, "var(--person)")
    assert accent != person
    page.get_by_role("button", name="Pick for a collection").click()
    page.locator(".ypickbox").first.check()
    page.wait_for_selector(".topic-pickbar")
    bar = page.locator(".topic-pickbar")
    assert bar.evaluate("e => getComputedStyle(e).borderTopColor") == accent
    row = page.locator(".yrow.topic-picked").first
    assert accent in row.evaluate("e => getComputedStyle(e).boxShadow")
    assert person not in row.evaluate("e => getComputedStyle(e).boxShadow")
    assert (
        page.locator(".ypickbox").first.evaluate("e => getComputedStyle(e).accentColor") == accent
    )

    # Recorded, the topic is the person's: its chip and its dot are in the magenta family.
    bar.get_by_role("button", name="Add to collection...").click()
    _chooser(page, new="Colored by who said it")
    _said(page)
    page.wait_for_selector(".yrow .topic-chip")
    shade = _color_of(page, "var(--topic-1)")
    chip = page.locator(".yrow .topic-chip").first
    assert chip.evaluate("e => getComputedStyle(e).borderTopColor") == shade
    assert chip.locator(".topic-dot").evaluate("e => getComputedStyle(e).backgroundColor") == shade
    # The magenta family, from purple-magenta to rose (the sources' 290 to 340, less the rounding of
    # eight-bit channels).
    assert 288 <= _hue(shade) <= 342 and shade != accent
    for number in range(1, 7):
        assert 288 <= _hue(_color_of(page, f"var(--topic-{number})")) <= 342
    page.context.close()


# ---- 7. axe


def _serious(page: Any, where: str) -> list[str]:
    found = page.evaluate(
        "options => window.axe.run(document, options)",
        {"runOnly": {"type": "tag", "values": STANDARDS}},
    )
    return [
        f"{where}: {v['impact']} {v['id']}: {v['help']} "
        f"{json.dumps([n['html'][:100] for n in v['nodes'][:3]])}"
        for v in found["violations"]
        if v["impact"] in ("critical", "serious")
    ]


@pytest.mark.skipif(_axe_script() is None and not REQUIRED, reason="axe-core is not installed")
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_no_serious_violation_on_explore_topics(
    browser: Any, served: str, built: Any, scheme: str
) -> None:
    script = _axe_script()
    if script is None:
        pytest.fail("axe-core is not installed (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
    _make_topic(
        served,
        "Every kind",
        [
            _experiments(built, "t1")[0],
            {"kind": "core", "terms": ["quolt", "sornel"]},
            {"kind": "core", "terms": ["noword", "nothere"]},
            {
                "kind": "line",
                "experiments": [m["id"] for m in _experiments(built, "t2")[:3]],
                "words": ["miravo"],
                "label": "a line",
            },
        ],
    )
    _make_topic(served, "Second topic", _experiments(built, "t1")[:2])
    page = _new_page(browser, scheme)
    _open_topics(page, served)
    assert page.locator(".topic-members tbody tr.is-nothing").count() >= 1, (
        "a member that comes to nothing is drawn too"
    )
    page.add_script_tag(content=script.read_text(encoding="utf-8"))
    problems = _serious(page, "the topics page")
    page.locator(".topic-row", has_text="Second topic").click()
    page.wait_for_selector(".topic-card[data-topic]")
    settled(page)
    problems += _serious(page, "another topic")
    with page.expect_download():
        page.get_by_role("button", name="Export files (CSV)").click()
    page.locator(".topic-export-notes:not([hidden])").wait_for()
    problems += _serious(page, "with the file's notes shown")
    page.locator(".topic-card [data-help='topics.withdraw']").click()
    page.locator("dialog[open]").wait_for()
    problems += _serious(page, "with a confirmation open")
    page.locator("dialog[open] button:not(.primary)").click()
    assert not problems, "\n".join(problems)
    page.context.close()


@pytest.mark.skipif(_axe_script() is None and not REQUIRED, reason="axe-core is not installed")
def test_axe_finds_no_serious_violation_with_the_chooser_open_on_years(
    browser: Any, served: str, built: Any
) -> None:
    script = _axe_script()
    if script is None:
        pytest.fail("axe-core is not installed (INGENCE_BROWSER_REQUIRED is set)", pytrace=False)
    _make_topic(served, "Chooser topic", _experiments(built, "t0")[:2])
    page = _new_page(browser)
    _open_years(page, served)
    _open_person(page)
    page.get_by_role("button", name="Pick for a collection").click()
    page.locator(".ypickbox").first.check()
    page.add_script_tag(content=script.read_text(encoding="utf-8"))
    problems = _serious(page, "with rows picked")
    page.get_by_role("button", name="Add to collection...").click()
    page.locator("dialog.topic-chooser").wait_for()
    problems += _serious(page, "with the chooser open")
    assert not problems, "\n".join(problems)
    page.context.close()
