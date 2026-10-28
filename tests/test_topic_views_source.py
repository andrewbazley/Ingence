"""Topics on the views, the pages' wiring.

Read from the sources, with no browser:

- the page loads the shared topic script and its styles before the views that draw a topic, and the
  Topics section after Years, and the typed scripts are in the type check;
- a topic's color is the person's: six tokens of the magenta family, in light and in dark;
- the Graph workspace's bundle says topic where a person's own set is named, and the engine's own
  groups keep their word;
- every control a topic script builds has a help entry in plain words, and none of the topic
  scripts' words is one the product has retired for topics ("collection", "hyper-group", "study");
- the picks are made in Groupings (stacks), Years (rows) and the Map (shift-click and a box), each
  through the one chooser.
"""

from __future__ import annotations

import colorsys
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
WEB = REPOSITORY / "src" / "ingence" / "web"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))["entries"]
TOPIC_SCRIPTS = ("topic_kit.js", "topics.js")


def _text(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_the_page_loads_the_kit_before_the_views_and_the_section_after_years() -> None:
    order = re.findall(r'<script src="/([\w.]+)"></script>', PAGE)
    assert order.index("topic_kit.js") < order.index("years.js") < order.index("topics.js")
    assert order.index("topic_kit.js") > order.index("app.js")
    assert order.index("topic_kit.js") < order.index("graph.js") < order.index("map.js")
    assert '<link rel="stylesheet" href="/topics.css">' in PAGE
    assert PAGE.index("/topics.css") < PAGE.index("/topic_kit.js")


def test_the_typed_scripts_are_in_the_type_check() -> None:
    config = json.loads((REPOSITORY / "tsconfig.json").read_text(encoding="utf-8"))
    files = config["files"]
    for named in ("topic_kit.js", "topics.js", "types/topics.d.ts"):
        assert f"src/ingence/web/{named}" in files
    for script in TOPIC_SCRIPTS:
        assert _text(script).startswith("// @ts-check")


def _hsl_tokens(css: str) -> list[tuple[int, int, int]]:
    return [
        (int(h), int(s_), int(l_))
        for h, s_, l_ in re.findall(r"--topic-\d: hsl\((\d+), (\d+)%, (\d+)%\)", css)
    ]


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """CIE Lab of an sRGB color (each channel 0 to 1), D65."""

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return math.dist(_lab(first), _lab(second))


def _from_hex(code: str) -> tuple[float, float, float]:
    return tuple(int(code[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _from_hsl(hue: int, saturation: int, lightness: int) -> tuple[float, float, float]:
    return colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)


def test_a_topic_s_color_is_the_person_s_six_shades_in_light_and_dark() -> None:
    css = _text("topics.css")
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    for number in range(1, 7):
        assert re.search(rf"--topic-{number}: hsl\(", light), number
        assert re.search(rf"--topic-{number}: hsl\(", dark), number
    assert len(_hsl_tokens(light)) == len(_hsl_tokens(dark)) == 6
    # Every shade is of the magenta family, from purple-magenta to rose: never the accent's blue,
    # the later-stage color's orange or the problem's red.
    for hue, _saturation, _lightness in _hsl_tokens(css):
        assert 290 <= hue <= 340, hue
    assert "colorOf" in _text("topic_kit.js")


def test_no_topic_color_reads_as_the_problem_red_in_light_or_dark() -> None:
    """A topic's chip carries a dot and a border, which is the problem marker's own form, so each
    shade keeps a distance from ``--problem`` at least that of the person's own magenta from it
    (``--person`` against ``--problem``, which the Dashboard already pairs), measured in CIE Lab."""
    app = _text("app.css")
    app_light, app_dark = app.split("@media (prefers-color-scheme: dark)", 1)
    topics = _text("topics.css")
    topics_light, topics_dark = topics.split("@media (prefers-color-scheme: dark)", 1)
    for mode, held, shades in (("light", app_light, topics_light), ("dark", app_dark, topics_dark)):
        problem = _from_hex(re.search(r"--problem: (#[0-9a-f]{6})", held).group(1))  # type: ignore[union-attr]
        person = _from_hex(re.search(r"--person: (#[0-9a-f]{6})", held).group(1))  # type: ignore[union-attr]
        least = _distance(person, problem)
        colors = [_from_hsl(*token) for token in _hsl_tokens(shades)]
        assert len(colors) == 6
        for number, color in enumerate(colors, 1):
            assert _distance(color, problem) >= least, (mode, number, _distance(color, problem))
        # Nor are two topics' shades one color.
        for first in range(6):
            for second in range(first + 1, 6):
                assert _distance(colors[first], colors[second]) >= 8, (mode, first + 1, second + 1)


def test_a_pick_in_progress_is_drawn_in_the_accent_and_only_a_topic_in_magenta() -> None:
    """Magenta is "a person said so": a topic. A row, dot, stack or result only picked is a
    selection, which is the accent's job, until it is recorded."""
    css = _text("topics.css")
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    picked = [
        (selector.strip(), body)
        for selector, body in rules
        if re.search(r"topic-pick|ypickbox|topic-picked", selector)
    ]
    for selector, body in picked:
        assert "--person" not in body, selector
    drawn = {selector for selector, body in picked if "--accent" in body}
    assert drawn >= {
        ".topic-pickbar",
        ".topic-pick input",
        ".topic-picked",
        ".years .ypickbox",
        ".years .yrow.topic-picked",
        ".map-svg .map-dot.topic-picked",
    }


def test_the_topics_are_read_again_after_a_rerun() -> None:
    source = _text("topic_kit.js")
    assert "onRefresh(" in source
    assert "held.run !== version.run_pinned" in source
    assert "changed()" in source


def test_the_topics_button_stands_in_the_experiments_nav_after_experiments() -> None:
    # Experiments is a page of its own, and a person reads "Collections" (view: topics).
    nav = re.search(r'data-sections-of="experiments".*?</nav>', PAGE, re.DOTALL)
    assert nav is not None
    assert re.findall(r'data-view="(\w+)"', nav.group(0)) == ["experiments", "topics"]
    assert 'data-view="topics" data-help="section.topics">Collections</button>' in nav.group(0)
    assert "  experiments: ['experiments', 'topics']," in _text("app.js")
    app = _text("app.js")
    assert "topics: () => notLoaded('Collections')" in app
    assert re.search(r"const OWN_DETAIL = new Set\(\[[^\]]*'topics'", app)
    source = _text("topics.js")
    assert "VIEWS.topics = draw" in source
    # Written in the page, not added to it at runtime: no insertion into the nav or the pages.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    for runtime in ("PAGES.", "PAGE_OF", "nav.sections", "splice(", ".after(button)"):
        assert runtime not in code, runtime


def test_the_topic_types_stand_alone_and_every_global_is_declared_once() -> None:
    topics = _text("types/topics.d.ts")
    declared = set(re.findall(r"^declare (?:const|function|let|var) (\w+)", topics, re.M))
    assert declared == {"topicKit"}, declared
    globals_ = _text("types/globals.d.ts")
    for name in (
        "api",
        "navigate",
        "state",
        "VIEWS",
        "detailPane",
        "OWN_DETAIL",
        "table",
        "onRefresh",
    ):
        assert len(re.findall(rf"^declare (?:const|function) {name}\b", globals_, re.M)) == 1, name


def test_a_topic_that_stands_is_edited_by_what_changed_never_by_its_whole_list() -> None:
    """The overlay's ``add`` and ``remove_members`` name what changes and carry the rest, so a group
    of words, a line of work or the mark of an experiment is never sent back as a plain entry."""
    kit = _text("topic_kit.js")
    assert "add: fresh" in kit
    assert "members: distinct([...before" not in kit and "membersOf" not in kit
    topics = _text("topics.js")
    assert "remove_members: [member.key]" in topics
    assert "topics.remove_member" in CATALOG
    human = (REPOSITORY / "explorer" / "src" / "human.ts").read_text(encoding="utf-8")
    assert "remove_members: taken" in human and "add: added.map" in human
    # A rename names no member; Revert says every kind again.
    assert "...(renamed ? {name: edit.name} : {})" in human
    assert (
        "membersSaid(said)" in human and "kind: 'core', terms" in human and "kind: 'line'" in human
    )
    assert "remove_members" in _text("explorer/explorer.js")


def test_a_topic_s_outlines_on_the_map_are_its_owner_s_not_a_key_prefix() -> None:
    source = _text("map.js")
    assert "const TOPICS = 'topics'" in source
    assert "owner: TOPICS" in source
    assert "window.ingenceMap.keys(TOPICS)" in source
    assert "startsWith('topic:')" not in source
    assert "{owner: TOPICS}" in source


def test_the_csv_reads_a_stage_through_the_experiments_lists_own_reading() -> None:
    route = (REPOSITORY / "src" / "ingence" / "api" / "v1" / "topic_export.py").read_text(
        encoding="utf-8"
    )
    assert "claim_reading" in route
    for private in ("_KINDS", "_dictionary_holds", "_kinds_of"):
        assert private not in route, private


def test_every_view_picks_through_the_one_chooser() -> None:
    for name, marker in (
        ("groups.js", "kind: 'core', terms"),
        ("years.js", "kind: 'experiment', id: e.id"),
        ("map.js", "kind: 'experiment', id}"),
        # Search results are not a place to pick for a topic.
    ):
        text = _text(name)
        assert marker in text, name
        assert "topicKit.addTo" in text, name
        assert "topicKit.pickBar" in text, name
    assert "kind: 'line'" in _text("years.js")
    assert "shiftKey" in _text("map.js")
    # The lens does not apply to what the Wheel draws, and it says so. (The seed pictures are in
    # Advanced's Stability section, which no lens narrows.)
    assert "data-lens-note': 'wheel'" in _text("years.js")
    assert "pickBox(" in _text("map.js")


def test_the_graph_workspace_says_topic_for_a_person_s_own_set() -> None:
    bundle = _text("explorer/explorer.js")
    for said in ("New topic…", "Add to a topic…", "a new topic's name", "Your topic "):
        assert said in bundle, said
    for retired in ("New group…", "Add to a group…", "a new group's name"):
        assert retired not in bundle, retired
    source = (REPOSITORY / "explorer" / "src" / "HumanUi.tsx").read_text(encoding="utf-8")
    assert "New topic…" in source
    assert "New group" not in source
    # The engine's own groups keep their word.
    assert "Draw the groups the files belong to" in (
        REPOSITORY / "explorer" / "src" / "Workspace.tsx"
    ).read_text(encoding="utf-8")


def test_every_help_key_the_topic_scripts_use_has_an_entry_in_plain_words() -> None:
    keys: set[str] = set()
    for name in ("topic_kit.js", "topics.js", "years.js", "groups.js", "map.js", "app.js"):
        text = _text(name)
        keys |= set(re.findall(r"'data-help': '((?:topics|section\.topics)[\w.]*)'", text))
    keys |= set(re.findall(r'data-help="(section\.topics)"', PAGE))
    keys |= {"groups.pick", "years.pick", "years.lens", "map.topics"}
    assert "section.topics" in keys and "topics.export" in keys
    for key in sorted(keys):
        entry = CATALOG.get(key)
        assert entry, key
        assert entry["title"].strip() and entry["text"].strip().endswith("."), key
        # 0.4.0 (the website's style guide): a person reads "collection" for a topic, so it may
        # appear.
        assert not re.search(r"\b(hyper-?group|study)\b", entry["text"], re.I), key


def test_no_word_the_product_retired_for_topics_is_in_the_topic_scripts() -> None:
    for name in (*TOPIC_SCRIPTS, "topics.css"):
        text = _text(name)
        # A word the product retired for topics, built from parts so that nothing here spells it.
        assert not re.search(r"\b" + "hyper" + "-?group" + r"\b", text, re.I), name
        # "study" is an archive's word for an experiment: it never names a topic. Since 0.4.0 a
        # person reads "collection" for a topic (the website's style guide), so that word may stand
        # here.
        assert not re.search(r"\bstud(y|ies)\b", text, re.I), name
        assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", text), name


def test_the_csv_is_served_by_a_route_and_saved_by_the_browser_not_written_by_the_engine() -> None:
    source = _text("topics.js")
    assert "/api/v1/topics/export?topic=" in source
    assert "URL.createObjectURL" in source and "download:" in source
    route = (REPOSITORY / "src" / "ingence" / "api" / "v1" / "topic_export.py").read_text(
        encoding="utf-8"
    )
    # The route makes text; it opens no file for writing.
    assert not re.search(r"open\([^)]*[\"'][wa]", route)
    assert "write_text" not in route and "write_bytes" not in route


def test_a_line_already_in_a_topic_is_counted_as_held_by_its_server_key() -> None:
    """ "Added N items" counts what the server adds: a line of work is held by the key the server
    makes of it, which the page computes (Node's runner, which is skipped where Node is absent)."""
    source = _text("topic_kit.js")
    assert "if (member.kind === 'line') return lineKey(member.experiments);" in source
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    if int(version.stdout.strip().lstrip("v").split(".")[0]) < 20:
        pytest.skip(f"Node {version.stdout.strip()} is older than 20, which the test needs")
    done = subprocess.run(
        [node, "--test", str(REPOSITORY / "tests" / "web_js" / "topic_line_key.test.mjs")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
