"""The Map's outline function: its geometry, and where it sits.

``window.ingenceMap.outline(ids, color, options)`` draws the outline of a set of experiments under
the map's dots. The compare overlay, "Show on the map" and a person's topics all draw through it.
Its pure functions (hull, padded outline, overlap) and its owners are tested by
``tests/web_js/map_outline.test.mjs``; ``tests/web_js/map_owner.test.mjs`` and
``tests/web_js/groups_words.test.mjs`` run the real drawings of the Map and of the Groupings page
on a fake page. All three use Node's own runner, which this module runs when Node is installed
(and skips, in words, when it is not). The rest reads the source: the layer is under the dots and
out of the way of a click, the legend toggles it, and the colors are the engine's own in both
themes (never the magenta of a person's words).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "ingence" / "web"
MAP = (WEB / "map.js").read_text(encoding="utf-8")
CSS = (WEB / "map.css").read_text(encoding="utf-8")
NODE_TEST = ROOT / "tests" / "web_js" / "map_outline.test.mjs"
OWNER_TEST = ROOT / "tests" / "web_js" / "map_owner.test.mjs"
WORDS_TEST = ROOT / "tests" / "web_js" / "groups_words.test.mjs"


def test_the_geometry_passes_its_node_tests() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    version = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 20:
        pytest.skip(f"Node {version} is older than 20, which the test needs")
    done = subprocess.run(
        [node, "--test", str(NODE_TEST), str(OWNER_TEST), str(WORDS_TEST)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1000:]


def test_the_script_names_the_outline_function_and_says_how_to_call_it() -> None:
    assert "window.ingenceMap = {" in MAP
    assert "function outline(ids, color, options = {})" in MAP
    assert "window.ingenceMap.outline(ids, color, {key, label, tag, owner})" in MAP
    for name in (
        "clear: clearOutline",
        "keys: outlineKeys",
        "outlineColor",
        "show,",
        "onDraw",
        "fit: fitTo",
    ):
        assert name in MAP, name


def test_the_outlines_lie_under_the_dots_and_never_take_a_click() -> None:
    assert "svg.append(pattern(), outlines, dots, names, chosen, cursor);" in MAP
    assert re.search(r"\.map-outlines \{ pointer-events: none; \}", CSS)
    # The Outlines button hides the groups' outlines and no other owner's.
    assert '.map-outlines.hide-groups [data-owner="groups"] { display: none; }' in CSS
    assert "classList.toggle('hide-groups', !view.outlinesShown)" in MAP
    # The legend carries what the layer says, since the layer is hidden from a screen reader.
    assert "'map-outlines', 'aria-hidden': 'true'" in MAP
    assert "'Outlines'" in MAP and "'Clear outlines'" in MAP


def test_the_outline_colors_are_the_engines_in_both_themes_and_none_is_magenta() -> None:
    light = CSS[: CSS.index("@media (prefers-color-scheme: dark)")]
    dark = CSS[CSS.index("@media (prefers-color-scheme: dark)") :]
    dark = dark[: dark.index("main.single-layout")]
    for name in ("--map-o0", "--map-o1", "--map-o2", "--map-o3"):
        assert name + ":" in light and name + ":" in dark, name
    app = (WEB / "app.css").read_text(encoding="utf-8")
    magentas = set(re.findall(r"--person:\s*(#[0-9a-fA-F]{6})", app))
    used = set(re.findall(r"--map-o\d:\s*(#[0-9a-fA-F]{6})", CSS))
    assert len(used) == 8 and not (used & magentas)


def test_the_groups_code_acts_only_on_the_groups_own_outlines() -> None:
    """Another owner's outlines (topics) are drawn here and never cleared, listed or relabelled by
    the compare overlay, "Show on the map", "Clear outlines", the card or the legend line."""
    assert "{key, label, tag, owner})" in MAP and "const GROUPS = 'groups';" in MAP
    assert "const owner = String(options.owner || GROUPS);" in MAP
    # No code outside the one helper empties the held outlines.
    assert "view.outlines.clear()" not in MAP
    assert MAP.count("dropOutlines(GROUPS)") == 3  # compared, closed, replaced by a request
    assert "if (request.replace !== false) dropOutlines(GROUPS);" in MAP
    assert "const own = ownedBy(GROUPS);" in MAP and MAP.count("ownedBy(GROUPS)") == 2
    assert "onclick: () => clearOutline() }" not in MAP and "clearOutline()" in MAP
    assert "function clearOutline(key, {owner = GROUPS} = {})" in MAP
    # An overlap is shaded between outlines of one owner only.
    assert "drawn[first].held.owner !== drawn[second].held.owner" in MAP


def test_a_default_key_cannot_collide_and_a_listener_can_be_removed() -> None:
    assert "outlineSerial += 1;" in MAP and "`${owner}-outline-${outlineSerial}`" in MAP
    assert "view.outlines.get(key).owner !== owner" in MAP
    assert "view.listeners.splice(at, 1)" in MAP


def test_the_panel_script_is_loaded_by_a_load_of_the_maps_own_that_gives_up_in_words() -> None:
    body = MAP[MAP.index("function whenGroups()") : MAP.index('/* "How to read this" beside')]
    assert "const GROUPS_WAIT_MS = 15000;" in MAP
    assert "document.querySelector('script[src=\"/groups.js\"]')" not in body
    assert (
        "setTimeout(fail, GROUPS_WAIT_MS)" in body
        and "script.addEventListener('error', fail)" in body
    )
    assert "The panel did not load. Reload the page and try again." in body


def test_a_tag_keeps_clear_of_the_names_and_a_group_keeps_its_size() -> None:
    assert "function nameBoxes()" in MAP and "function tagPlace(polygon, avoid)" in MAP
    # The names are placed before the outlines, so the tags see where the names are.
    assert "placeNames();\n    placeOutlines();" in MAP
    # A folder name that matches no dot is counted in the group's size, not dropped.
    assert "missing: named.missing" in MAP and "held.ids.length + held.missing" in MAP


def test_the_fourth_outline_s_key_boxes_draw_the_map_s_dash_and_dot() -> None:
    groups = (WEB / "groups.css").read_text(encoding="utf-8")
    assert "10 4 2 4" in CSS  # the map's dash
    pattern = "var(--o) 0 10px, transparent 10px 14px, var(--o) 14px 16px, transparent 16px 20px"
    assert CSS.count(pattern) == 4 and groups.count(pattern) == 4
    assert "dashed double" not in CSS and "dashed double" not in groups


def test_an_outline_opened_through_show_keeps_the_owner_the_entry_names() -> None:
    assert "owner: entry.owner, stack: entry.stack, missing: named.missing, quiet: true" in MAP
    assert "{outlines: [{key, label, tag, ids | folders, color, stack, owner}], fit: true}" in MAP
    # The legend line is added only where there is one: a missing part would be written as "null".
    assert "if (outlines) holder.append(outlines);" in MAP
    assert "...items, outlineLegend());" not in MAP
