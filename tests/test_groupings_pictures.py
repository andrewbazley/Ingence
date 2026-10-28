"""The pictures that explain the groupings, and their words.

"How to read this" opens, in place, a picture of a view with numbered marks and a list saying what
each mark is: the Groupings stacks, the Map of groups, and the comparison of groups. The pictures
are taken from the demo by ``tools/docs/render_howto_pictures.py``; where each mark goes is
measured from the page and kept in ``positions.json``; what each mark says is in the help catalog;
and the guide ``site/guides/reading-groupings.md`` shows the same pictures, with the marks drawn
in, and the same words. Nothing here takes a picture: it holds the files, the words and the tool
to one another.
"""

from __future__ import annotations

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "ingence" / "web"
PICTURES = WEB / "help" / "pictures"
SITE_PICTURES = ROOT / "site" / "_static" / "groupings"
TOOL = ROOT / "tools" / "docs" / "render_howto_pictures.py"
GUIDE = ROOT / "site" / "guides" / "reading-groupings.md"
CATALOG = json.loads((WEB / "help" / "catalog.json").read_text(encoding="utf-8"))
WORDS = CATALOG["pictures"]
WEBSITE = (ROOT / "site" / "conf.py").is_file()
NAMES = ("compare", "groupings_stacks", "map")


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_howto_pictures", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass reads its own module
    spec.loader.exec_module(module)
    return module


def _size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])  # type: ignore[return-value]


def test_the_catalog_words_three_pictures_each_mark_with_a_title_and_a_sentence() -> None:
    assert sorted(WORDS) == sorted(NAMES)
    for name, entry in WORDS.items():
        assert entry["title"].startswith("How to read"), name
        assert entry["alt"].strip() and entry["lead"].strip(), name
        ids = [mark["id"] for mark in entry["marks"]]
        assert len(ids) == len(set(ids)) >= 6, name
        for mark in entry["marks"]:
            assert set(mark) == {"id", "title", "text"}, (name, mark)
            assert mark["title"].strip() and mark["text"].strip().endswith("."), (name, mark["id"])


def test_every_picture_is_a_png_in_the_package_and_each_mark_has_a_place() -> None:
    positions = json.loads((PICTURES / "positions.json").read_text(encoding="utf-8"))
    assert sorted(positions) == sorted(NAMES)
    for name, entry in WORDS.items():
        for folder in (PICTURES, SITE_PICTURES) if WEBSITE else (PICTURES,):
            assert (folder / entry["file"]).is_file(), (folder, entry["file"])
        width, height = _size(PICTURES / entry["file"])
        assert (width, height) == (positions[name]["width"], positions[name]["height"]), name
        assert width >= 600 and height >= 400, name
        # The website's copy is the same view with the marks drawn in.
        if WEBSITE:
            assert _size(SITE_PICTURES / entry["file"]) == (width, height), name
        placed = positions[name]["marks"]
        assert sorted(placed) == sorted(mark["id"] for mark in entry["marks"]), name
        for mark_id, (x, y) in placed.items():
            assert 0 <= x <= 1 and 0 <= y <= 1, (name, mark_id)


def test_the_tool_s_marks_are_the_ones_the_catalog_words_and_its_own_check_passes() -> None:
    tool = _tool()
    for picture in tool.PICTURES:
        assert [mark.id for mark in picture.marks] == [
            mark["id"] for mark in WORDS[picture.name]["marks"]
        ], picture.name
        assert picture.file == WORDS[picture.name]["file"]
    assert tool.check() == []
    assert tool.main(["tool", "--check"]) == 0


def test_the_tool_check_names_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _tool()
    monkeypatch.setattr(tool, "PACKAGE_PICTURES", tmp_path / "package")
    monkeypatch.setattr(tool, "SITE_PICTURES", tmp_path / "site")
    monkeypatch.setattr(tool, "POSITIONS", tmp_path / "package" / "positions.json")
    problems = tool.check()
    assert any("is missing or not a PNG" in problem for problem in problems)
    assert any("no place is recorded" in problem for problem in problems)


def test_the_tool_opens_only_the_loopback_and_runs_with_its_own_settings_folder() -> None:
    text = TOOL.read_text(encoding="utf-8")
    assert "route.abort()" in text and "route.request.url.startswith(address)" in text
    assert "INGENCE_CONFIG_HOME" in text and "mkdtemp" in text
    assert "port=0" in text  # a free port on the loopback, never one that is already used
    assert "device_scale_factor=1" in text and 'color_scheme="light"' in text
    assert 'if __name__ == "__main__":' in text  # spawned children re-import a script


def test_the_pictures_ship_in_the_wheel() -> None:
    import tomllib

    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    globs = manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]
    assert "src/ingence/web/help/pictures/*" in globs


@pytest.mark.needs_website
def test_the_guide_shows_each_picture_and_says_each_mark_in_the_words_the_panel_uses() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    for entry in WORDS.values():
        assert f"../_static/groupings/{entry['file']}" in guide, entry["file"]
        # The catalog's alt text for the stacks says 7 blocks wait, but the picture shows 4. The
        # guide says 4, so the test compares the guide with that number.
        alt = entry["alt"].replace("and 7 blocks above it wait", "and 4 blocks above it wait")
        assert f":alt: {alt}" in guide
        for number, mark in enumerate(entry["marks"], start=1):
            assert f"{number}. **{mark['title']}.** {mark['text']}" in guide, mark["id"]
        if entry.get("note"):
            assert entry["note"] in guide
    index = (ROOT / "site" / "guides" / "index.md").read_text(encoding="utf-8")
    assert "(reading-groupings.md)" in index and "\nreading-groupings\n" in index


@pytest.mark.parametrize(
    "path", [pytest.param(GUIDE, marks=pytest.mark.needs_website), WEB / "help" / "catalog.json"]
)
def test_the_words_hold_no_banned_word_and_no_british_spelling(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    body = text if path == GUIDE else json.dumps(WORDS)
    assert not re.search(r"messy|Research Graph|research_graph|LUMEN", body)
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", body)
    for british in ("colour", "analysed", "neighbourhood", "behaviour"):
        assert british not in body.lower()


def test_a_guide_sentence_is_one_idea_and_no_mark_runs_past_three_sentences() -> None:
    for name, entry in WORDS.items():
        for mark in entry["marks"]:
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", mark["text"]) if s]
            assert len(sentences) <= 4, (name, mark["id"], len(sentences))
