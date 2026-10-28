"""The Graph page's promises that hold in its served files.

The browser harness (``test_browser.py``, the ``browser`` extra) drives the page itself; these
read what the server serves, so the promises hold in the ordinary suite too: the page is in the
shell and loads its own script and the vendored library; the address carries the centre, the
filters and the selection and is read back on a reload and on back and forward; the same content
is a keyboard-reachable list; reduced motion is honoured; every help key has words; and the typed
boundary's declarations are current and cover the page's script.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from ingence.api.server import WEB_ROOT

REPOSITORY = Path(__file__).resolve().parents[1]
PAGE = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
GRAPH = (WEB_ROOT / "graph.js").read_text(encoding="utf-8")
APP = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
CATALOG = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))


def test_the_shell_holds_the_graph_regions_and_loads_the_local_library_before_the_page() -> None:
    assert '<template id="graph-template">' in PAGE
    regions = ("tools", "relations", "proposals", "caps", "choose", "canvas", "list", "inspector")
    for region in regions:
        assert f'data-graph="{region}"' in PAGE, region
    library = PAGE.index('<script src="/vendor/cytoscape/cytoscape.min.js"></script>')
    page = PAGE.index('<script src="/graph.js"></script>')
    assert library < page
    assert '<link rel="stylesheet" href="/graph.css">' in PAGE


def test_app_js_hands_the_graph_view_to_graph_js_and_nothing_else_of_it() -> None:
    body = re.search(r"async function showGraph\(\) \{(.*?)\n\}", APP, re.DOTALL)
    assert body is not None
    assert body.group(1).strip() == "await window.showGraphPage();"


def test_the_address_holds_the_centre_the_filters_and_the_selection() -> None:
    for name in ("center", "relations", "proposals", "selected", "max_nodes", "max_edges"):
        assert f"query.get('{name}')" in GRAPH, f"a reload would lose {name}"
        assert f"query.set('{name}'" in GRAPH, f"a change of {name} would not reach the address"
    assert "history.pushState" in GRAPH and "history.replaceState" in GRAPH
    assert "window.addEventListener('popstate'" in GRAPH, "back and forward redraw"


def test_the_same_content_is_a_list_a_keyboard_and_a_screen_reader_reach() -> None:
    assert "el('caption'" in GRAPH and "scope: 'col'" in GRAPH
    assert "class: 'graph-pick', type: 'button'" in GRAPH, "each row is a button"
    assert "'aria-label'" in GRAPH
    assert "ArrowDown" in GRAPH and "ArrowUp" in GRAPH
    assert 'aria-live="polite"' in PAGE and 'role="application"' in PAGE
    assert 'tabindex="0"' in PAGE, "the plot is one stop for the keyboard"


def test_proposed_lines_are_told_apart_by_words_and_line_style_not_colour_alone() -> None:
    assert "'line-style': 'dashed'" in GRAPH
    assert "dashed line: suggested" in PAGE
    assert "thin line: contains" in PAGE
    assert "thick arrowed line: published by the scan" in PAGE


def test_reduced_motion_is_honoured() -> None:
    assert "prefers-reduced-motion: reduce" in GRAPH
    assert "animate: false" in GRAPH
    assert "prefers-reduced-motion" in (WEB_ROOT / "graph.css").read_text(encoding="utf-8")


def test_the_graph_writes_nothing_and_takes_a_proposal_to_the_existing_review_page() -> None:
    assert "method: 'POST'" not in GRAPH and "write(" not in GRAPH
    assert "showView(review.view)" in GRAPH


def test_every_help_key_the_graph_names_has_words_and_says_distance_is_not_evidence() -> None:
    keys = set(re.findall(r"'data-help': '([\w.]+)'", GRAPH)) | set(
        re.findall(r'data-help="(graph\.[\w.]+)"', PAGE)
    )
    assert keys
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])
    assert "distance on the page is not evidence" in CATALOG["entries"]["graph.layout"]["text"]


def test_the_page_script_is_type_checked_against_the_generated_declarations() -> None:
    assert GRAPH.startswith("// @ts-check")
    assert "import('./types/api')" in GRAPH
    config = json.loads((REPOSITORY / "tsconfig.json").read_text(encoding="utf-8"))
    assert config["compilerOptions"]["checkJs"] is True
    assert config["compilerOptions"]["strict"] is True
    assert "src/ingence/web/graph.js" in config["files"]
    assert "src/ingence/web/types/api.d.ts" in config["files"]


def test_the_typescript_declarations_are_current() -> None:
    checked = subprocess.run(
        [sys.executable, str(REPOSITORY / "tools" / "generate_web_types.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_the_declarations_name_every_graph_response_field() -> None:
    declared = (WEB_ROOT / "types" / "api.d.ts").read_text(encoding="utf-8")
    for name in ("NeighborhoodResponse", "GraphNode", "GraphEdge", "EdgeDetail", "Truncation"):
        assert f"export interface {name} {{" in declared, name
    assert 'kind: "indexed_containment" | "published_assertion" | "proposed_candidate";' in declared
