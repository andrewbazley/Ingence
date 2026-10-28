"""The Dashboard's icon is the site's mark.

The browser tab shows the mark the documentation site uses, as a drawing (``favicon.svg``) and as a
32-pixel picture (``favicon-32.png``) for a browser that draws no icon from a drawing. Both ship in
the package beside the page, and both pages (the Dashboard and the landing page) name them. This
needs no browser: it asks the running server for each file and reads the two pages' markup.
"""

from __future__ import annotations

import struct
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fixtures.dashboard_browser import start_dashboard, stop
from ingence.api.server import WEB_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator

SITE_MARK = Path(__file__).resolve().parents[1] / "site" / "_static" / "favicon.svg"


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The landing page's server: no project is needed to ask for an icon."""
    config_home = tmp_path_factory.mktemp("favicon") / "config-home"
    config_home.mkdir()
    process, address = start_dashboard(None, config_home)
    try:
        yield address
    finally:
        stop(process)


def _get(address: str, path: str) -> tuple[int, str, bytes]:
    with urllib.request.urlopen(f"{address}{path.lstrip('/')}", timeout=10) as answer:
        return answer.status, answer.headers.get("Content-Type", ""), answer.read()


def test_the_drawing_is_served_as_an_svg_picture(served: str) -> None:
    status, kind, body = _get(served, "favicon.svg")
    assert status == 200
    assert kind.split(";")[0] == "image/svg+xml"
    root = ElementTree.fromstring(body)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 16 16"


def test_the_picture_is_served_as_a_32_pixel_png(served: str) -> None:
    status, kind, body = _get(served, "favicon-32.png")
    assert status == 200
    assert kind.split(";")[0] == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", body[16:24])
    assert (width, height) == (32, 32)


@pytest.mark.needs_website
def test_the_icon_is_the_sites_mark() -> None:
    """The same drawing the site's pages carry: one mark, not a second one that drifts."""
    assert (WEB_ROOT / "favicon.svg").read_bytes() == SITE_MARK.read_bytes()
    site_png = SITE_MARK.with_name("favicon-32.png")
    assert (WEB_ROOT / "favicon-32.png").read_bytes() == site_png.read_bytes()


@pytest.mark.parametrize("page", ["index.html", "landing.html"])
def test_each_page_names_the_icon(page: str) -> None:
    head = (WEB_ROOT / page).read_text(encoding="utf-8").split("</head>")[0]
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in head
    assert '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">' in head
