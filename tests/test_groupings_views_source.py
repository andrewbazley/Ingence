"""The Groupings stacks, the Map and the comparison, read from the Dashboard's source.

What must be there, and what must not, with no browser: a "How to read this" panel on each of the
three views that is closed until asked and never a dialog; a tick to compare two to four sets; "Show
on the map" from a stack and from a line of work, and "Show the stack" back; the dead call to the
word cloud gone; an archive's root called an archive and its experiments studies. Every control the
scripts build names a help entry the catalog holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "ingence" / "web"
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))
GROUPS = (WEB / "groups.js").read_text(encoding="utf-8")
MAP = (WEB / "map.js").read_text(encoding="utf-8")
YEARS = (WEB / "years.js").read_text(encoding="utf-8")
REVIEW = (WEB / "review.js").read_text(encoding="utf-8")


def _help_keys(script: str) -> set[str]:
    """Every help key a script names: a literal ``data-help`` and the key a panel is given."""
    keys = set(re.findall(r"'data-help': '([\w.]+)'", script))
    keys |= set(re.findall(r"howTo\('\w+', '([\w.]+)'\)", script))
    keys |= set(re.findall(r"undefined, '([\w.]+)'\)", script))
    return keys


def test_every_help_key_the_three_scripts_name_is_in_the_catalog() -> None:
    for script in (GROUPS, MAP, YEARS):
        keys = _help_keys(script)
        assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])
    named = _help_keys(GROUPS) | _help_keys(MAP) | _help_keys(YEARS)
    for key in (
        "groupings.howto",
        "map.howto",
        "compare.howto",
        "groupings.compare_box",
        "groupings.compare_tray",
        "groupings.show_on_map",
        "compare.show_on_map",
        "map.compare",
        "map.outlines",
        "map.groups",
        "map.show_stack",
        "years.show_on_map",
    ):
        assert key in named, key
        assert CATALOG["entries"][key]["text"].strip(), key


def test_each_view_has_a_how_to_read_link_that_opens_its_panel_in_place_and_only_when_asked() -> (
    None
):
    for script in (GROUPS, MAP):
        assert "'aria-expanded': 'false'" in script
        assert "panel.hidden = !open;" in script
        assert "text: 'How to read this'" in script
        # Never a dialog: nothing takes the page's focus or covers it.
        assert "showModal" not in script and "role: 'dialog'" not in script
        assert "alertdialog" not in script and "dialogs." not in script
    assert "howTo('groupings_stacks', 'groupings.howto')" in GROUPS
    assert "howTo('compare', 'compare.howto')" in GROUPS
    assert "fillHowTo(panel, 'map')" in MAP
    # A panel is hidden when made: it opens by the link's click, never by itself.
    assert "hidden: '', 'aria-label': 'How to read this'" in GROUPS
    assert "hidden: '', 'aria-label': 'How to read the map'" in MAP
    assert not re.search(r"toggle\.click\(\)|panel\.hidden = false", GROUPS + MAP)


def test_the_panel_draws_marks_where_the_tool_measured_them_and_says_each_in_the_catalog() -> None:
    assert "api('/help/pictures/positions.json')" in GROUPS
    assert "api('/help/catalog.json')" in GROUPS
    assert "catalog.pictures" in GROUPS
    assert "(100 * mark.at[0]).toFixed(2)" in GROUPS and "(100 * mark.at[1]).toFixed(2)" in GROUPS
    # The picture opens at full size on a click, in its own tab; a mark is for the eye only.
    assert "'aria-hidden': 'true'" in GROUPS and "'Open the picture at full size'" in GROUPS


def test_a_set_is_ticked_to_compare_two_to_four_and_the_comparison_writes_nothing() -> None:
    assert "const LEAST = 2;" in GROUPS and "const MOST = 4;" in GROUPS
    assert "api(`/api/v1/groups/compare?" in GROUPS
    assert "encodeURIComponent(spec)" in GROUPS
    assert "const BAR = '|';" in GROUPS  # a term may hold a space
    body = GROUPS[GROUPS.index("/* A tick on a set") : GROUPS.index("const plural =")]
    assert "write(" not in body
    # The one write in the file is still the answer route.
    assert set(re.findall(r"write\('([^']+)'", GROUPS)) == {"/api/review/answer"}
    assert "Tick ${LEAST - picked.length} more to compare" in GROUPS


def test_a_stack_shows_on_the_map_and_the_map_shows_the_stack_back() -> None:
    assert "'data-help': 'groupings.show_on_map', text: 'Show on the map'" in GROUPS
    assert "folders: core.experiments" in GROUPS and "stack: core.terms" in GROUPS
    assert "window.ingenceMap.show({fit: true" in GROUPS
    assert "text: 'Show the word group'" in MAP or "'Show the word group'" in MAP
    assert "navigate('classes')" in MAP
    # The way back is one name in both scripts: the Map sets it, the stacks read it once.
    assert "ingenceGroups.pendingStack = terms.join(' ')" in MAP.replace("window.", "")
    assert "shared.pendingStack" in GROUPS and "shared.pendingStack = null;" in GROUPS


def test_a_line_of_work_shows_on_the_map_from_its_card() -> None:
    assert "function showLineOnMap(t)" in YEARS
    assert "window.ingenceMap.show({fit: true, outlines: [{key: `line:${t.theme}`" in YEARS
    assert "ids: t.exps.map((e) => e.id)" in YEARS
    assert "act('Show on the map', () => showLineOnMap(t), undefined, 'years.show_on_map')" in YEARS


def test_the_word_cloud_s_dead_call_is_gone() -> None:
    for path in WEB.rglob("*.js"):
        assert "seeAsWords" not in path.read_text(encoding="utf-8"), path.name
    assert (
        "const section = el('div', {class: 'group-sections'}, [latticeHook(), holder]);" in REVIEW
    )


def test_an_archive_s_root_says_archive_and_its_experiments_say_study() -> None:
    assert "facts.push(['Study'" not in YEARS
    assert "facts.push([capFirst(word('root')), e.rootName])" in YEARS
    assert "archive: {root: ['archive', 'archives']" in YEARS
    assert "experiment: ['study', 'studies']" in YEARS
    from ingence.api.plain_words import plain_words

    words = plain_words()
    assert words.levels["archive"]["root"] == "archive"
    assert words.levels["archive"]["experiment"] == "study"
    assert words.levels["lab"]["root"] != words.levels["archive"]["experiment"]


def test_the_compare_panel_and_the_map_keep_their_styles_inside_their_own_names() -> None:
    css = re.sub(
        r"/\*.*?\*/", "", (WEB / "groups.css").read_text(encoding="utf-8"), flags=re.DOTALL
    )
    # Colors are the engine's: the outline tokens, never the magenta of a person's words.
    assert "var(--person)" not in css
    for token in ("--map-o0", "--map-o1", "--map-o2", "--map-o3"):
        assert token in css


def test_only_a_stack_s_own_base_row_is_drawn_as_the_base() -> None:
    """A block in the opened fold is the last row of the fold, and must not look like the base."""
    css = re.sub(
        r"/\*.*?\*/", "", (WEB / "groups.css").read_text(encoding="utf-8"), flags=re.DOTALL
    )
    rules = [head.strip() for head in re.findall(r"([^{};]+)\{", css)]
    last_row = [rule for rule in rules if ":last-child" in rule and "cascade" in rule]
    assert len(last_row) == 2, last_row
    for rule in last_row:
        assert rule.startswith(".cascade-blocks > .cascade-row:last-child"), rule


def test_the_compare_panel_counts_a_group_s_files_out_of_its_own() -> None:
    assert "outOf(group.files_only, group.files)" in GROUPS
    assert "outOf(group.files_only, data.files_in_either)" not in GROUPS
    # The screen-reader label of a bar says only the counts the engine served.
    assert (
        "in some"
        not in GROUPS[GROUPS.index("function overlapBar") : GROUPS.index("const experimentItems")]
    )


def test_the_lines_of_work_are_read_when_the_tray_first_appears_not_with_the_page() -> None:
    tray = GROUPS[GROUPS.index("function compareTray") : GROUPS.index("const plural =")]
    assert tray.count("themes/map?themes_only=true") == 1
    assert tray.index("if (!asked) {") < tray.index("themes/map?themes_only=true")
    # Not asked before the tray is first drawn with something ticked.
    assert tray.index("if (!chosen.size) return;") < tray.index("themes/map?themes_only=true")
    before = tray[: tray.index("const draw = () =>")]
    assert "themes/map" not in before


def test_the_written_words_are_read_before_any_stack_is_built() -> None:
    """The first drawing of the page must show the files' spellings: the map of them is filled
    before the stacks are made through it, not after."""
    render = GROUPS[GROUPS.index("function render(holder)") :]
    assert render.index("written.clear();") < render.index("const stacks = el('ul'")
    assert render.index("written.clear();") < render.index(
        "stack(core, children, holder, answerable)"
    )
    assert render.count("written.clear();") == 1
