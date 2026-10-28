"""The vendored graph library travels with the engine and reaches for nothing.

Cytoscape.js is shipped under ``web/vendor/cytoscape/`` so the Graph page draws with no network, as
the engine runs only on the user's machine. A wheel that dropped it would serve a page that cannot
draw, silently. A vendored file that changed without its record would be a library nobody chose.
Both are checked here, and every web asset, nested or not, is held to the rule that it names no
scheme and calls no network interface. The vendored file's only ``http://`` strings are in its
bundled libraries' licence comments, which are read out before the check. The one exception is two
addresses a person opens by clicking a link (the documentation and the reporting guide), each let
through only where it is written in the footer's link and in ``report.js``.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import zipfile
from pathlib import Path

import pytest

from ingence.api.server import WEB_ROOT

REPOSITORY = Path(__file__).resolve().parents[1]
VENDOR = WEB_ROOT / "vendor" / "cytoscape"
#: Browser interfaces that open a connection. None of the Dashboard's assets may use one; its
#: own requests go through ``fetch`` to a path on the loopback server, which names no scheme.
NETWORK_INTERFACES = ("XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts")
SCHEMES = ("http://", "https://", "//cdn", "@import url(")
#: What a bundled framework may spell that opens nothing: the XML namespaces React DOM names SVG and
#: MathML elements by, which a browser never fetches, and the prefix of the documentation link
#: React's minified errors print. Nothing else with a scheme passes.
NAMESPACES = (
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/1998/Math/MathML",
    "https://react.dev/errors/",
)


#: Addresses a person opens by clicking: the footer's link to the documentation and the reporting
#: guide that "Report a problem" names in its email. The page opens neither, fetches nothing from
#: either, and loads no script, style or image from them. They are navigation. Each is let through
#: only in the one place it is written: the footer link's ``href`` attribute and the guide's
#: constant in ``report.js``. The same address anywhere else (a ``fetch``, an image, a stylesheet, a
#: longer address at the site, a lookalike host) is a scheme like any other and fails.
USES_OF_LINKS_A_PERSON_OPENS = (
    'href="https://ingence.org"',
    "const GUIDE = 'https://ingence.org/guides/reporting-a-problem.html';",
)


def without_the_links_a_person_opens(text: str) -> str:
    """``text`` with the two places a person-opened address is written taken out."""
    for use in USES_OF_LINKS_A_PERSON_OPENS:
        text = text.replace(use, "")
    return text


def reaches_out(text: str) -> list[str]:
    """What ``text`` names that opens a connection: a scheme or a network interface."""
    return [name for name in (*SCHEMES, *NETWORK_INTERFACES) if name in text]


def _artifact_globs() -> list[str]:
    manifest = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    return list(manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"])


def _matches(glob: str, path: str) -> bool:
    """Whether a wheel artifact glob names a path, as gitignore-style globs do: ``*`` stays in
    one folder."""
    pattern = "".join(
        "[^/]*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", glob)
    )
    return re.fullmatch(pattern, path) is not None


def _assets() -> list[Path]:
    return sorted(
        path
        for path in WEB_ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.name != "__init__.py"
        # The TypeScript declarations are for the type check, not for the page.
        and "types" not in path.relative_to(WEB_ROOT).parts
    )


def _declared(name: str) -> str:
    """A digest as the vendor record's table states it."""
    record = (VENDOR / "VENDOR.md").read_text(encoding="utf-8")
    found = re.search(rf"\| `{re.escape(name)}` SHA-256 \| `([0-9a-f]{{64}})` \|", record)
    assert found, f"VENDOR.md states no SHA-256 for {name}"
    return found.group(1)


def test_the_vendored_library_is_the_pinned_release_with_its_licence() -> None:
    library = VENDOR / "cytoscape.min.js"
    assert hashlib.sha256(library.read_bytes()).hexdigest() == _declared("cytoscape.min.js")
    licence = VENDOR / "LICENSE"
    assert hashlib.sha256(licence.read_bytes()).hexdigest() == _declared("LICENSE")
    assert "Permission is hereby granted" in licence.read_text(encoding="utf-8")
    record = (VENDOR / "VENDOR.md").read_text(encoding="utf-8")
    assert "| Version | 3.34.3 (pinned) |" in record


def test_every_web_asset_nested_or_not_is_carried_by_the_wheel() -> None:
    globs = _artifact_globs()
    for asset in _assets():
        relative = asset.relative_to(REPOSITORY).as_posix()
        assert any(_matches(glob, relative) for glob in globs), (
            f"the wheel would drop {relative}; add a glob to pyproject.toml's artifacts"
        )


def test_no_web_asset_names_a_scheme_or_opens_a_connection() -> None:
    checked = []
    for asset in _assets():
        if asset.suffix not in {".html", ".css", ".js"}:
            continue
        text = asset.read_text(encoding="utf-8")
        if "vendor" in asset.relative_to(WEB_ROOT).parts:
            # A bundled library's licence comments carry its authors' addresses; the code does not.
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        if "explorer" in asset.relative_to(WEB_ROOT).parts:
            for namespace in NAMESPACES:
                text = text.replace(namespace, "")
        text = without_the_links_a_person_opens(text)
        for scheme in SCHEMES:
            assert scheme not in text, f"{asset.relative_to(WEB_ROOT)} reaches out with {scheme!r}"
        for interface in NETWORK_INTERFACES:
            assert interface not in text, f"{asset.relative_to(WEB_ROOT)} uses {interface}"
        checked.append(asset.relative_to(WEB_ROOT).as_posix())
    assert "vendor/cytoscape/cytoscape.min.js" in checked
    assert "graph.js" in checked
    assert "explorer/explorer.js" in checked


def test_the_built_wheel_holds_the_vendored_library(tmp_path: Path) -> None:
    """Where the build backend is installed, build the wheel and read its file list."""
    wheel_builder = pytest.importorskip("hatchling.builders.wheel")
    builder = wheel_builder.WheelBuilder(str(REPOSITORY))
    built = next(iter(builder.build(directory=str(tmp_path), versions=["standard"])))
    with zipfile.ZipFile(built) as wheel:
        names = set(wheel.namelist())
    assert "ingence/web/vendor/cytoscape/cytoscape.min.js" in names
    assert "ingence/web/vendor/cytoscape/LICENSE" in names
    assert "ingence/web/graph.js" in names
    assert "ingence/web/explorer/explorer.js" in names
    assert "ingence/web/explorer/THIRD_PARTY_NOTICES.txt" in names


def test_the_allowed_links_pass_only_where_they_are_written() -> None:
    """The footer's link and the guide's constant, as the page writes them, pass; a request to the
    same site, a lookalike host, a longer address and an image or stylesheet from it do not."""
    passes = [
        '<a id="docs-link" href="https://ingence.org" target="_blank">Documentation</a>',
        "  const GUIDE = 'https://ingence.org/guides/reporting-a-problem.html';",
    ]
    for text in passes:
        assert reaches_out(without_the_links_a_person_opens(text)) == [], text
    planted = [
        "fetch('https://ingence.org/collect')",
        "fetch('https://ingence.org')",
        'fetch("https://ingence.org/guides/reporting-a-problem.html")',
        '<img src="https://ingence.org.example.net/pixel.gif">',
        '<link rel="stylesheet" href="https://ingence.org/site.css">',
        '<a href="https://ingence.org/elsewhere">',
        "x = 'https://ingence.org' + '/collect'",
        "const url = `https://ingence.org/a`",
        "@import url(https://ingence.org/site.css);",
        "import('https://ingence.org/module.js')",
        "navigator.sendBeacon('https://ingence.org/guides/reporting-a-problem.html')",
    ]
    for text in planted:
        assert reaches_out(without_the_links_a_person_opens(text)), f"passed: {text}"


FONTS = {
    "atkinson-hyperlegible": (
        "AtkinsonHyperlegible-Regular.woff2",
        "AtkinsonHyperlegible-Italic.woff2",
        "AtkinsonHyperlegible-Bold.woff2",
    ),
    "ibm-plex-mono": ("IBMPlexMono-Regular.woff2", "IBMPlexMono-Medium.woff2"),
}


@pytest.mark.parametrize("folder", sorted(FONTS))
def test_each_vendored_font_is_the_recorded_file_with_its_license(folder: str) -> None:
    """Both faces ship in the package, each file the one its record names, with its license."""
    base = WEB_ROOT / "vendor" / folder
    record = (base / "VENDOR.md").read_text(encoding="utf-8")
    for name in (*FONTS[folder], "LICENSE"):
        found = re.search(rf"\| `{re.escape(name)}` SHA-256 \| `([0-9a-f]{{64}})` \|", record)
        assert found, f"{folder}/VENDOR.md states no SHA-256 for {name}"
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == found.group(1)
    assert "SIL Open Font License" in (base / "LICENSE").read_text(encoding="utf-8")
    assert (base / FONTS[folder][0]).read_bytes()[:4] == b"wOF2"
    served = (WEB_ROOT / "fonts.css").read_text(encoding="utf-8")
    for name in FONTS[folder]:
        assert f"/vendor/{folder}/{name}" in served


def test_the_fonts_stylesheet_declares_every_face_outside_its_comments() -> None:
    """A comment holding ``*/`` ends early and swallows the next rule; every face must survive."""
    served = (WEB_ROOT / "fonts.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", served, flags=re.DOTALL)
    statements = [part.strip() for part in rules.split("}") if part.strip()]
    assert len(statements) == sum(len(names) for names in FONTS.values())
    assert all(statement.startswith("@font-face {") for statement in statements)
