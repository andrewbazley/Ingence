"""A small project for the tests of ``ingence fix``: two roots, a person's exclusion pattern, runs.

Every name here is invented, and the names are kept in one place so a test can say which of them
must never be printed. The runs have fixed identifiers, so what ``fix`` prints is the same on every
machine once the paths are masked (``masked``).

This module imports only what the engine had before ``fix --shareable``. The script that wrote
``tests/fixtures/fix_report_base/`` can then run it against an engine without that option.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.core.project import Project
from ingence.core.settings import ScanSettings, Settings
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from pathlib import Path

FIRST_ROOT = "Smith_Lab_Data"
SECOND_ROOT = "HeLa_screens"
PROJECT = "Priya_index_project"
#: The pattern a person wrote into the project's settings.
PATTERN = "*_Jane_*"
#: The folder it leaves out, and the file in that folder.
LEFT_OUT_FOLDER = "Notes_Jane_Doe"
LEFT_OUT_FILE = "draft_thesis_notes.txt"

#: Every file and folder name of the data, none of which may reach a shareable report.
NAMES = (
    FIRST_ROOT,
    SECOND_ROOT,
    PROJECT,
    LEFT_OUT_FOLDER,
    LEFT_OUT_FILE,
    "2031-03-14_Atlas_HeLa",
    "Atlas_HeLa_01.tif",
    "Atlas_HeLa_02.tif",
    "Atlas_summary.csv",
    "Okafor_plate_DAPI_03.tif",
    "Okafor_plate_DAPI_04.tif",
    "Jane_Doe",
    "Okafor",
    "Atlas",
)

RUNS = (
    "run_20310101T000001Z_111111111111",
    "run_20310101T000002Z_222222222222",
    "run_20310101T000003Z_333333333333",
)


@dataclass(frozen=True)
class FixProject:
    """The project a test asks ``ingence fix`` about, and what it was made from."""

    path: Path
    roots: tuple[Path, Path]
    runs: tuple[str, ...]


def build(tmp_path: Path, *, runs: int = 3, vary_last: bool = False) -> FixProject:
    """Make the two roots and the project, and publish ``runs`` censuses of them.

    With ``vary_last`` a file is added before the last census, so the two most recent runs differ
    (``fix --verify`` then has rows to say).
    """
    first = tmp_path / FIRST_ROOT
    second = tmp_path / SECOND_ROOT
    (first / "2031-03-14_Atlas_HeLa" / "raw").mkdir(parents=True)
    (first / "2031-03-14_Atlas_HeLa" / "analysis").mkdir()
    (first / "2031-03-14_Atlas_HeLa" / "raw" / "Atlas_HeLa_01.tif").write_bytes(b"a" * 32)
    (first / "2031-03-14_Atlas_HeLa" / "raw" / "Atlas_HeLa_02.tif").write_bytes(b"b" * 32)
    (first / "2031-03-14_Atlas_HeLa" / "analysis" / "Atlas_summary.csv").write_text("a,b\n1,2\n")
    (first / LEFT_OUT_FOLDER).mkdir()
    (first / LEFT_OUT_FOLDER / LEFT_OUT_FILE).write_text("notes")
    (first / ".DS_Store").write_text("hidden")
    (second / "$RECYCLE.BIN" / "deleted").mkdir(parents=True)
    (second / "$RECYCLE.BIN" / "deleted" / "Okafor_old.bin").write_bytes(b"c" * 16)
    (second / "Okafor_plate_DAPI_03.tif").write_bytes(b"d" * 64)

    settings = Settings(scan=ScanSettings(exclude_globs=(*ScanSettings().exclude_globs, PATTERN)))
    project = Project.create(
        tmp_path / PROJECT,
        created_at="2031-01-01T00:00:00Z",
        roots=[first, second],
        settings=settings,
    )
    for number, run_id in enumerate(RUNS[:runs], start=1):
        if vary_last and number == runs:
            (second / "Okafor_plate_DAPI_04.tif").write_bytes(b"e" * 64)
        report = run_census(
            Project.open(project.path),
            run_id=run_id,
            started_at=f"2031-01-01T00:00:0{number}Z",
        )
        assert report.published
    return FixProject(
        path=project.path, roots=(first.resolve(), second.resolve()), runs=RUNS[:runs]
    )


#: The abandoned staging directory of a run that never finished (``.tmp/<run>.partial``).
ABANDONED = "run_20310101T000000Z_999999999999"


def roots_away(made: FixProject) -> None:
    """Move both roots out of reach, as an unplugged disk is, and leave an interrupted run."""
    elsewhere = made.path.parent / "elsewhere"
    elsewhere.mkdir()
    for root in made.roots:
        root.rename(elsewhere / root.name)
    (made.path / ".tmp" / f"{ABANDONED}.partial").mkdir(parents=True)


#: The projects the tests ask about: whether the last census sees a file the one before did not,
#: and whether the roots are out of reach (with an interrupted run's folder left in the project).
PROJECTS = {"reachable": (True, False), "away": (False, True)}

#: What ``fix`` is asked with, and which project it is asked about. ``differing`` is the first
#: project's last two runs, which differ by one file; ``away`` has two that do not.
ASKED = {
    "reachable": ("reachable", ()),
    "differing": ("reachable", ("--verify",)),
    "away": ("away", ("--verify",)),
}


def project(name: str, tmp_path: Path) -> FixProject:
    """One of the projects, made in ``tmp_path``."""
    vary, away = PROJECTS[name]
    made = build(tmp_path, vary_last=vary)
    if away:
        roots_away(made)
    return made


def masked(text: str, made: FixProject) -> str:
    """The report with what differs between machines or engine builds written as a name.

    Paths, the engine's and the data format's versions, times, and the counts a build of the
    engine changes without the report's wording changing (the size of the shipped Dictionary
    among them). Run ``fix`` on a console as wide as ``WIDTH`` so no path is split over lines.
    """
    for token, value in (
        ("{PROJECT}", str(made.path)),
        ("{ROOT1}", str(made.roots[0])),
        ("{ROOT2}", str(made.roots[1])),
    ):
        text = text.replace(value, token)
    text = re.sub(
        r"data format \S+, created by \S+", "data format {SCHEMA}, created by {ENGINE}", text
    )
    text = re.sub(r"updated \d{4}-\d\d-\d\dT[\d:.]+Z?", "updated {TIME}", text)
    text = re.sub(r"(?m)^(    \w+): \d+$", r"\1: {N}", text)
    return re.sub(r"(?m)^    [\d,]+ items", "    {N} items", text)


#: The width of the console ``fix`` prints to, wide enough that no line is split.
WIDTH = 500
