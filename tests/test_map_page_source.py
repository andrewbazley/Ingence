"""The Map of groups in the Dashboard's source: what must be there and what must not.

The tests read the files, with no browser. The word cloud that the map replaced is gone whole: its
page, its styles, its help and its two routes. The Graph page's sections are the Explorer, the
Neighborhood and the Map of groups, in that order. The old address ``#words`` leads to the Words
section of Questions. ``words.js`` removes the map's alias, and the map's own line stays in its
source. Every control the map script builds names a help entry the catalog holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
WEB = REPOSITORY / "src" / "ingence" / "web"
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
MAP = (WEB / "map.js").read_text(encoding="utf-8")


def test_no_cloud_remains_in_the_dashboard_s_source() -> None:
    for name in ("wordcloud.js", "wordcloud.css"):
        assert not (WEB / name).exists(), name
    pattern = re.compile(r"wordcloud|word cloud|word_cloud", re.IGNORECASE)
    found = [
        str(path.relative_to(REPOSITORY))
        for path in WEB.rglob("*")
        if path.is_file()
        and path.suffix in {".js", ".css", ".html", ".json", ".ts", ".map"}
        and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not found, found
    # The Words section of Questions has help entries of its own (``words.*``,
    # ``section.words``). What this holds is that none is about a cloud, which the scan above reads.
    assert not [key for key in CATALOG["entries"] if "cloud" in key]


def test_the_cloud_s_routes_are_gone_and_nothing_asks_for_them() -> None:
    from ingence.api import v1

    v1.load_routes()
    assert "words/cloud" not in v1.DECLARED and "words/where" not in v1.DECLARED
    assert not (REPOSITORY / "src" / "ingence" / "api" / "v1" / "wordcloud.py").exists()
    asked = [
        p
        for p in WEB.glob("*.js")
        if re.search(r"v1/words/(cloud|where)", p.read_text(encoding="utf-8"))
    ]
    assert not asked, asked


def test_advanced_has_the_explorer_the_connections_and_the_map() -> None:
    # The Graph page's sections are Advanced's "Views", and Neighborhood is Connections.
    # The stability pictures, which are off the main view, close the group after the map.
    nav = re.search(r'aria-labelledby="sections-views">.*?</span>\s*</nav>', PAGE, re.DOTALL)
    assert nav is not None
    assert re.findall(r'data-view="(\w+)"', nav.group(0)) == [
        "explorer",
        "graph",
        "map",
        "stability",
    ]
    assert re.findall(r">([^<]+)</button>", nav.group(0)) == [
        "Explorer",
        "Connections",
        "Map of groups",
        "Stability",
    ]
    assert re.search(r"'explorer', 'graph', 'map'\],", APP)
    assert "VIEWS.map = showMap" in MAP
    assert "VIEW_ALIASES.words = 'map'" in MAP
    assert (
        '<script src="/map.js"></script>' in PAGE
        and '<link rel="stylesheet" href="/map.css">' in PAGE
    )


def test_every_control_the_map_names_has_words_in_the_catalog() -> None:
    keys = set(re.findall(r"'data-help': '([\w.]+)'", MAP))
    keys |= set(re.findall(r'data-help="(section\.map)"', PAGE))
    assert {"map.plot", "map.color_by", "map.legend", "map.card", "section.map"} <= keys
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])


def test_the_map_script_moves_merges_and_splits_nothing_and_draws_no_unplaced_point() -> None:
    assert "Nothing here can move, merge or split a point." in MAP
    # A point with no place is never given one: the dots are drawn from the placed points only.
    assert "for (const point of placedPoints())" in MAP
    assert "write(" not in MAP and "method: 'POST'" not in MAP


def test_every_selector_of_the_map_s_styles_is_scoped_to_the_map() -> None:
    """``map.css`` is linked on every page, so a rule on a bare ``.person`` or ``button.link`` would
    restyle the Dashboard's other pages."""
    text = re.sub(r"/\*.*?\*/", "", (WEB / "map.css").read_text(encoding="utf-8"), flags=re.DOTALL)
    heads = [head.strip() for head in re.findall(r"([^{};]+)\{", text)]
    selectors = [
        piece.strip()
        for head in heads
        if not head.startswith("@") and not re.match(r"^\d+%", head)
        for piece in head.split(",")
    ]
    loose = [selector for selector in selectors if selector != ":root" and "map-" not in selector]
    assert len(selectors) > 20 and not loose, loose
