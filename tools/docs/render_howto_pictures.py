#!/usr/bin/env python3
"""Take the pictures that "How to read this" shows, from the demo, with the browser the tests use.

    PYTHONPATH=src python tools/docs/render_howto_pictures.py               # take them
    PYTHONPATH=src python tools/docs/render_howto_pictures.py --project DIR # a demo already made
    PYTHONPATH=src python tools/docs/render_howto_pictures.py --check       # no browser: agree?

It makes the sample laboratory in a temporary folder (the same steps as ``ingence demo``, about
four minutes), serves its Dashboard on the loopback with a settings folder of its own, opens three
views at a fixed window (1280 by 1800, so nothing scrolls under
the sticky header; device scale 1, light theme), and writes, for each:

- ``src/ingence/web/help/pictures/<name>.png``: the view, as a person sees it. The Dashboard shows
  it in its "How to read this" panel (the Groupings stacks, the Map of groups, the comparison),
  with the numbered marks drawn over it by the panel.
- ``src/ingence/web/help/pictures/positions.json``: where each mark goes, as fractions of the
  picture, measured from the page (the middle of the element each mark names), so a view that
  changes is marked right when the pictures are taken again, with nothing moved by hand.
- Where the guides' source is present: the same picture with the numbered marks drawn in, for the
  guide to the groupings, which has no script to draw them. Without that source this tool writes
  the first two files only.

What each mark says is in the help catalog (``catalog.json``, ``pictures``). The marks named below
are the ones the catalog words, and ``--check`` fails where the two differ, where a picture or its
place is missing, or where a file is not a PNG. Nothing here reaches an address but the
loopback, and the pictures show only the sample's invented words. Retake them whenever a view
they show changes.

Needs Playwright and its Chromium (the ``browser`` extra) and the themes extra (the map).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_PICTURES = REPOSITORY_ROOT / "src" / "ingence" / "web" / "help" / "pictures"
WEBSITE_SOURCE = REPOSITORY_ROOT / "site"
SITE_PICTURES = WEBSITE_SOURCE / "_static" / "groupings"
CATALOG = REPOSITORY_ROOT / "src" / "ingence" / "web" / "help" / "catalog.json"
POSITIONS = PACKAGE_PICTURES / "positions.json"
#: The window every picture is taken in.
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 1800
#: Seconds of content reading the demo's dive may use (the sample's smallest tier needs far fewer).
DIVE_SECONDS = 600
#: The demo's lattice, as the page itself asks for it.
LATTICE = "() => fetch('/api/v1/groups/lattice').then((response) => response.json())"
#: How many seconds a view may take to draw before the tool gives up.
WAIT_MS = 120_000


@dataclass(frozen=True)
class Mark:
    """One numbered mark: the catalog's id for it, the element it names and where in that element.

    ``at`` is the place inside the element's box as fractions of its width and height (the middle
    by default); ``selector`` is a Playwright selector, whose first match is used.
    """

    id: str
    selector: str
    at: tuple[float, float] = (0.5, 0.5)
    #: Where a selector cannot say which element (a dot has no name), a script that returns it; it
    #: runs in the page, and ``selector`` is then only a label for the message.
    script: str = ""


@dataclass(frozen=True)
class Picture:
    """One picture: where it is taken, what is cut out, and the marks on it."""

    name: str
    file: str
    address: str
    #: The element that is cut out of the page, and what must be drawn before it is taken.
    root: str
    ready: str
    marks: tuple[Mark, ...]
    #: What a person does first (opens a fold, ticks two sets): called with the page and the
    #: demo's lattice (the sets the Groupings page draws).
    prepare: Callable[[Any, dict[str, Any]], None] = field(default=lambda page, lattice: None)


# ---------------------------------------------------------------------------
# The sample laboratory
# ---------------------------------------------------------------------------


def make_demo(destination: Path) -> Path:
    """The sample laboratory in ``destination``, read the way ``ingence demo`` reads it; returns
    the project's folder. Nothing opens: the Dashboard is served by this tool."""
    from ingence.cli.main import app
    from ingence.evaluation.sample_lab import generate_sample_lab
    from ingence.evaluation.sample_lab_words import words

    lab = generate_sample_lab(destination, tier="S", seed=1)
    project = destination / "project"
    packs = [name for pack in words().pack_names() for name in ("--domain", pack)]
    steps = (
        ("init", str(project), "--root", str(lab.pool), "--label", "sample", *packs),
        ("index", "--project", str(project), "--quiet"),
        ("dive", "--project", str(project), "--budget", str(DIVE_SECONDS)),
        ("infer", "--project", str(project)),
    )
    for step in steps:
        report = io.StringIO()
        with contextlib.redirect_stdout(report):
            code = app(args=list(step), standalone_mode=False)
        if code:
            message = f"ingence {step[0]} stopped with status {code}:\n{report.getvalue()}"
            raise SystemExit(message)
    return project


# ---------------------------------------------------------------------------
# What a person does before each picture is taken
# ---------------------------------------------------------------------------


def _groups_to_compare(lattice: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two sets of the demo's lattice that share some experiments and not all of either's: the
    biggest set and the first other set of three or more experiments that overlaps it in part.
    Chosen by their experiments, never by their words."""
    found = [s for s in lattice["sets"] if len(s["experiments"]) >= 3]
    found.sort(key=lambda s: (-len(s["experiments"]), s["terms"]))
    for first in found:
        for second in found:
            shared = set(first["experiments"]) & set(second["experiments"])
            if (
                first is not second
                and shared
                and shared != set(first["experiments"])
                and shared != set(second["experiments"])
            ):
                return first, second
    message = "the demo's lattice holds no two sets that overlap in part"
    raise SystemExit(message)


def _tick(page: Any, terms: list[str]) -> None:
    """Tick one set's Compare box wherever the page lists it (a block of a stack, or a set listed
    apart), opening the folds that hide it."""
    wanted = " ".join(terms)
    where = f'.cascade-block[data-terms="{wanted}"], .cascade-unasked li[data-terms="{wanted}"]'
    for fold in page.locator("details.cascade-fold, details.cascade-unasked").all():
        inside = f'[data-terms="{wanted}"]'
        if fold.locator(inside).count() and not fold.evaluate("(node) => node.open"):
            fold.locator("summary").first.click()
    page.locator(where).first.locator(".compare-box input").first.check()


def prepare_stacks(page: Any, lattice: dict[str, Any]) -> None:
    del lattice
    page.locator(".cascade-fold > summary").first.click()
    page.wait_for_timeout(300)


def prepare_compare(page: Any, lattice: dict[str, Any]) -> None:
    first, second = _groups_to_compare(lattice)
    _tick(page, first["terms"])
    _tick(page, second["terms"])
    page.locator(".compare-tray button.answer").click()
    page.wait_for_selector(".compare-slot .compare-key", timeout=WAIT_MS)


def prepare_map(page: Any, lattice: dict[str, Any]) -> None:
    """The map with two groups outlined, as "Show on the map" draws them (no zoom, so the whole map
    is seen with its names)."""
    first, second = _groups_to_compare(lattice)
    page.evaluate(
        """([a, b]) => window.ingenceMap.show({fit: false, outlines: [
            {key: 'a', label: (a.terms_shown || a.terms).join(', '), tag: 'A',
             folders: a.experiments, stack: a.terms,
             color: window.ingenceMap.outlineColor(0)},
            {key: 'b', label: (b.terms_shown || b.terms).join(', '), tag: 'B',
             folders: b.experiments, stack: b.terms,
             color: window.ingenceMap.outlineColor(1)}]})""",
        [first, second],
    )
    page.wait_for_selector(".map-outline", timeout=WAIT_MS)
    page.wait_for_selector(".map-overlap", timeout=WAIT_MS)
    page.wait_for_timeout(500)


#: The first block above the core, in the row that stands on it (the fold holds it, opened first).
ABOVE = ".cascade-fold .cascade-row:last-child .cascade-block"

#: The dot farthest left on the map: the same dot in every demo, whatever its identifier.
LEFTMOST_DOT = (
    "() => [...document.querySelectorAll('.map-dot')]"
    ".sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x)[0]"
)

PICTURES: tuple[Picture, ...] = (
    Picture(
        name="groupings_stacks",
        file="groupings-stacks.png",
        address="#classes",
        root=".group-lattice .cascades > li",
        ready=".cascade",
        prepare=prepare_stacks,
        marks=(
            Mark("stack", ".cascade", (0.99, 0.35)),
            Mark("base", ".cascade-fold ~ .cascade-row .cascade-block", (0.0, 0.5)),
            Mark("block", ABOVE, (0.0, 0.5)),
            Mark("strong_word", ".cascade-fold .cascade-word.added", (1.2, 0.5)),
            Mark("faint_word", ".cascade-fold .cascade-word:not(.added)", (-0.4, 0.3)),
            Mark("state", ".cascade-fold .tag", (1.12, 0.5)),
            Mark("experiments", ".cascade-fold .lattice-experiments summary", (1.15, 0.5)),
            Mark("fold", ".cascade-fold > summary", (0.72, 0.5)),
            Mark("answers", ".answers", (1.05, 0.5)),
            Mark("meter", ".cascade-meter", (0.0, 0.5)),
            Mark("compare", ".cascade-fold ~ .cascade-row .compare-box", (1.18, 0.5)),
            Mark("map", ".cascade-tools button", (1.25, 0.5)),
        ),
    ),
    Picture(
        name="map",
        file="map.png",
        address="#map",
        root=".map-page",
        ready=".map-dot",
        prepare=prepare_map,
        marks=(
            Mark("dot", "the leftmost dot", (1.0, 0.0), script=LEFTMOST_DOT),
            Mark("name", ".map-theme >> nth=1 >> .map-ground", (0.0, 0.5)),
            Mark("ring", ".map-theme >> nth=1 >> .map-center", (1.0, 0.0)),
            Mark("outline", ".map-outline >> nth=0", (1.0, 0.7)),
            Mark("letter", ".map-outline-tag >> nth=0", (-0.9, 0.5)),
            Mark("overlap", ".map-overlap >> nth=0", (0.5, 0.5)),
            Mark("colors", ".map-keys", (0.93, 0.3)),
            Mark("outline_legend", ".map-outline-legend", (0.93, 0.5)),
            Mark("measure", ".map-chips", (1.0, 0.0)),
            Mark("tools", ".map-tools", (1.0, 0.0)),
            Mark("sentence", ".map-method", (0.99, 0.5)),
            Mark("card", ".map-group", (0.9, 0.25)),
        ),
    ),
    Picture(
        name="compare",
        file="compare.png",
        address="#classes",
        root=".compare-slot",
        ready=".cascade",
        prepare=prepare_compare,
        marks=(
            Mark("keys", ".compare-key >> nth=0", (1.0, 0.0)),
            Mark("words_all", ".compare-section >> nth=0 >> p >> nth=0", (0.99, 0.5)),
            Mark("words_only", ".compare-section >> nth=0 >> p >> nth=1", (0.99, 0.5)),
            Mark("experiments_all", ".compare-section >> nth=1 >> p >> nth=0", (0.99, 0.5)),
            Mark("bars", ".compare-bar >> nth=0", (0.99, 0.5)),
            Mark("lists", ".compare-list >> nth=0 >> summary", (0.99, 0.5)),
            Mark("files", ".compare-section >> nth=2 >> p >> nth=0", (0.99, 0.5)),
            Mark("map", ".compare-actions button", (1.0, 0.5)),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Taking them
# ---------------------------------------------------------------------------


def _place(page: Any, picture: Picture, root: Any) -> dict[str, list[float]]:
    """Where each mark goes, as fractions of the cut-out picture, read from the page now."""
    box = root.bounding_box()
    if box is None:
        message = f"{picture.name}: the view drew nothing to cut out ({picture.root})"
        raise SystemExit(message)
    places: dict[str, list[float]] = {}
    for mark in picture.marks:
        if mark.script:
            found = page.evaluate_handle(mark.script).as_element()
            inner = found.bounding_box() if found is not None else None
            missing = found is None
        else:
            target = page.locator(mark.selector).first
            missing = target.count() == 0
            inner = None if missing else target.bounding_box()
        if missing:
            message = (
                f"{picture.name}: the view shows nothing for the mark {mark.id!r} "
                f"({mark.selector}); change the view or the mark"
            )
            raise SystemExit(message)
        if inner is None:
            message = f"{picture.name}: the mark {mark.id!r} names an element with no box"
            raise SystemExit(message)
        x = (inner["x"] + inner["width"] * mark.at[0] - box["x"]) / box["width"]
        y = (inner["y"] + inner["height"] * mark.at[1] - box["y"]) / box["height"]
        places[mark.id] = [round(min(max(x, 0.0), 1.0), 4), round(min(max(y, 0.0), 1.0), 4)]
    return places


def _burn_marks(root: Any, marks: list[tuple[int, list[float]]]) -> None:
    """Draw the numbered marks into the page, inside the cut-out element, for the guide's copy."""
    root.evaluate(
        """(node, marks) => {
            if (getComputedStyle(node).position === 'static') node.style.position = 'relative';
            for (const [number, at] of marks) {
                const dot = document.createElement('span');
                dot.textContent = String(number);
                dot.setAttribute('data-burned-mark', '');
                dot.style.cssText = 'position:absolute;z-index:9;width:22px;height:22px;'
                    + 'border-radius:50%;display:flex;align-items:center;justify-content:center;'
                    + 'background:#16181d;color:#fff;font:700 13px/1 system-ui,sans-serif;'
                    + 'box-shadow:0 0 0 2px #fff;transform:translate(-50%,-50%);'
                    + `left:${at[0] * 100}%;top:${at[1] * 100}%`;
                node.appendChild(dot);
            }
        }""",
        marks,
    )


def _png_size(path: Path) -> tuple[int, int]:
    """A PNG's width and height in pixels, read from its header."""
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return int(width), int(height)


def take(project: Path, only: list[str] | None = None) -> list[str]:
    """Take the pictures of ``project``'s Dashboard and write the places of their marks; returns
    the names of the pictures taken."""
    from playwright.sync_api import sync_playwright

    from ingence.api.server import DashboardServer

    PACKAGE_PICTURES.mkdir(parents=True, exist_ok=True)
    for_site = WEBSITE_SOURCE.is_dir()
    if for_site:
        SITE_PICTURES.mkdir(parents=True, exist_ok=True)
    positions: dict[str, Any] = (
        json.loads(POSITIONS.read_text(encoding="utf-8")) if POSITIONS.is_file() else {}
    )
    taken: list[str] = []
    with DashboardServer(project, port=0) as server, sync_playwright() as playwright:
        address = server.url.rstrip("/")
        browser = playwright.chromium.launch()
        try:
            for picture in PICTURES:
                if only and picture.name not in only:
                    continue
                context = browser.new_context(
                    viewport={"width": WINDOW_WIDTH, "height": WINDOW_HEIGHT},
                    device_scale_factor=1,
                    color_scheme="light",
                    reduced_motion="reduce",
                )
                page = context.new_page()
                # Only the loopback is ever opened.
                page.route(
                    "**/*",
                    lambda route: (
                        route.continue_()
                        if route.request.url.startswith(address)
                        else route.abort()
                    ),
                )
                page.goto(f"{address}/{picture.address}")
                page.wait_for_selector(picture.ready, timeout=WAIT_MS)
                lattice = page.evaluate(
                    "() => fetch('/api/v1/groups/lattice').then((r) => r.json())"
                )
                picture.prepare(page, lattice)
                # The pointer rests where it lights nothing and no help is showing.
                page.mouse.move(2, 2)
                page.wait_for_timeout(700)
                root = page.locator(picture.root).first
                root.screenshot(path=str(PACKAGE_PICTURES / picture.file))
                places = _place(page, picture, root)
                width, height = _png_size(PACKAGE_PICTURES / picture.file)
                positions[picture.name] = {"width": width, "height": height, "marks": places}
                if for_site:
                    _burn_marks(
                        root, [(n, places[m.id]) for n, m in enumerate(picture.marks, start=1)]
                    )
                    root.screenshot(path=str(SITE_PICTURES / picture.file))
                context.close()
                taken.append(picture.name)
        finally:
            browser.close()
    ordered = {name: positions[name] for name in sorted(positions)}
    POSITIONS.write_text(json.dumps(ordered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return taken


# ---------------------------------------------------------------------------
# Checking them (no browser)
# ---------------------------------------------------------------------------


def _shown(path: Path) -> Path:
    """A path as the repository names it, where it is inside it."""
    return path.relative_to(REPOSITORY_ROOT) if path.is_relative_to(REPOSITORY_ROOT) else path


def check() -> list[str]:
    """What is wrong with the pictures as committed: empty where all is well."""
    problems: list[str] = []
    catalog = json.loads(CATALOG.read_text(encoding="utf-8")).get("pictures", {})
    positions = json.loads(POSITIONS.read_text(encoding="utf-8")) if POSITIONS.is_file() else {}
    for picture in PICTURES:
        words = catalog.get(picture.name)
        if words is None:
            problems.append(f"catalog.json has no pictures.{picture.name}")
            continue
        if words.get("file") != picture.file:
            problems.append(
                f"{picture.name}: the catalog names {words.get('file')}, not {picture.file}"
            )
        said = [mark["id"] for mark in words.get("marks", [])]
        made = [mark.id for mark in picture.marks]
        if said != made:
            problems.append(f"{picture.name}: the catalog words {said} but the tool marks {made}")
        placed = positions.get(picture.name, {}).get("marks", {})
        for mark_id in made:
            if mark_id not in placed:
                problems.append(f"{picture.name}: no place is recorded for the mark {mark_id}")
        folders = (
            (PACKAGE_PICTURES, SITE_PICTURES) if WEBSITE_SOURCE.is_dir() else (PACKAGE_PICTURES,)
        )
        for folder in folders:
            path = folder / picture.file
            if not path.is_file() or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                problems.append(f"{_shown(path)} is missing or not a PNG")
    for name in catalog:
        if name not in {picture.name for picture in PICTURES}:
            problems.append(f"catalog.json words a picture the tool does not take: {name}")
    return problems


@contextlib.contextmanager
def _own_settings_folder() -> Iterator[Path]:
    """A settings folder of the tool's own, so nothing of a person's is read or written."""
    folder = Path(tempfile.mkdtemp(prefix="howto-config-"))
    previous = os.environ.get("INGENCE_CONFIG_HOME")
    os.environ["INGENCE_CONFIG_HOME"] = str(folder)
    try:
        yield folder
    finally:
        if previous is None:
            os.environ.pop("INGENCE_CONFIG_HOME", None)
        else:
            os.environ["INGENCE_CONFIG_HOME"] = previous
        shutil.rmtree(folder, ignore_errors=True)


def main(argv: list[str]) -> int:
    """Take the pictures, or check them."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if a picture, place or word is missing"
    )
    parser.add_argument(
        "--project", type=Path, help="a demo project already made (skips the five steps)"
    )
    parser.add_argument("--only", action="append", help="take only this picture (by name)")
    args = parser.parse_args(argv[1:])
    if args.check:
        problems = check()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    with _own_settings_folder():
        if args.project:
            taken = take(args.project.resolve(), args.only)
        else:
            work = Path(tempfile.mkdtemp(prefix="howto-demo-")) / "demo"
            try:
                taken = take(make_demo(work), args.only)
            finally:
                shutil.rmtree(work.parent, ignore_errors=True)
    for name in taken:
        print(f"took {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
