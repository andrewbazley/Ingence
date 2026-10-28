"""The six sections and the help catalog.

Every view the Dashboard had before is a section of one of its pages, so nothing a page did is
lost, and every control the shell names has words in the help catalog, so "?" never points at
something it cannot explain. The Dashboard has six sections (Overview, Overlap, Experiments,
Checks, History, Advanced). The file keeps the name from when there were five pages.
"""

from __future__ import annotations

import json
import re

from ingence.api.server import WEB_ROOT

PAGE = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
APP = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
CATALOG = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))

#: The views of the Dashboard before the merge, each of which must still be reachable.
_BEFORE = {
    "inventory",
    "review",
    "classes",
    "claims",
    "candidates",
    "extensions",
    "programs",
    "changes",
    "coverage",
    "rules",
}


def test_there_are_six_sections() -> None:
    pages = re.findall(r'<button data-page="(\w+)"', PAGE)
    # The Overview first and the experiments next; Advanced holds the rest.
    assert pages == ["overview", "overlap", "experiments", "checks", "history", "advanced"]
    words = re.findall(r'<button data-page="\w+"[^>]*>([^<]+)</button>', PAGE)
    assert words == ["Overview", "Overlap", "Experiments", "Checks", "History", "Advanced"]


def test_the_overview_opens_first_and_old_page_names_join_their_new_pages() -> None:
    assert "const named = viewNamed(parts.view) || 'overview';" in APP
    aliases = re.search(r"const PAGE_ALIASES = \{(.*?)\};", APP, re.DOTALL)
    assert aliases is not None
    for old, new in (
        ("explore", "experiments"),
        ("graph", "advanced"),
        ("questions", "advanced"),
        ("engine", "advanced"),
        ("parameters", "advanced"),
    ):
        assert f"{old}: '{new}'" in aliases.group(1), old


def test_every_view_of_before_is_a_section_of_a_page() -> None:
    sections = set(re.findall(r'<button data-view="(\w+)"', PAGE))
    assert sections >= _BEFORE
    for view in _BEFORE:
        assert f"{view}:" in APP or f"'{view}'" in APP, view


def test_every_page_lists_its_views_and_no_view_is_on_two_pages() -> None:
    listed = re.search(r"const PAGES = \{(.*?)\n\};", APP, re.DOTALL)
    assert listed is not None
    views = re.findall(r"'(\w+)'", listed.group(1))
    assert len(views) == len(set(views))
    assert set(views) >= _BEFORE


def test_every_control_the_pages_name_has_words_in_the_catalog() -> None:
    keys = set(re.findall(r'data-help="([\w.]+)"', PAGE))
    for script in ("app.js", "review.js", "workspace.js", "overview.js"):
        keys |= set(
            re.findall(r"'data-help': '([\w.]+)'", (WEB_ROOT / script).read_text(encoding="utf-8"))
        )
    assert keys
    assert keys <= set(CATALOG["entries"]), keys - set(CATALOG["entries"])
    for rule in CATALOG["auto"]:
        assert rule["key"] in CATALOG["entries"], rule["key"]
    for tour in CATALOG["tours"].values():
        assert set(tour) <= set(CATALOG["entries"])


def test_every_entry_has_a_title_and_a_plain_sentence_and_names_no_address() -> None:
    for key, entry in CATALOG["entries"].items():
        assert entry["title"].strip() and entry["text"].strip(), key
        assert "http" not in entry["text"], key


def test_the_help_assets_fetch_nothing_from_anywhere() -> None:
    # The help folder holds the pictures "How to read this" shows: a picture is not
    # read as text; the folder's scripts, catalog and the places of the marks are.
    for asset in sorted((WEB_ROOT / "help").rglob("*")):
        if not asset.is_file() or asset.suffix == ".png":
            continue
        text = asset.read_text(encoding="utf-8")
        for scheme in ("http://", "https://", "//cdn", "@import url("):
            assert scheme not in text, f"{asset.name} reaches out with {scheme!r}"


def test_the_waiting_counts_still_have_their_sections() -> None:
    for kind in ("questions", "classes", "claims", "candidates", "extensions", "programs"):
        assert f'data-waiting="{kind}"' in PAGE, kind
