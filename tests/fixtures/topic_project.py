"""A project with experiments, cores and lines of work to follow, for the topics.

The themes' invented pool (``theme_pool``: six planted lines of work over dated experiments of
three invented people, every word invented), indexed and inferred by the engine, with the
experiments' identifiers by planted unit, so a test names ``t2_03`` and gets the experiment's
folder identifier. :func:`rerun` writes a second census and inference over the same pool after
files were added, so a test can read what a re-run changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fixtures.theme_pool import (
    THEME_CENSUS_RUN,
    THEME_INFER_RUN,
    ThemeDocuments,
    build_theme_project,
)

if TYPE_CHECKING:
    from pathlib import Path

#: The second census and inference :func:`rerun` writes.
SECOND_CENSUS_RUN = "run_20260101T000003Z_cccccccccccc"
SECOND_INFER_RUN = "run_20260101T000004Z_dddddddddddd"


@dataclass(frozen=True)
class TopicProject:
    project: Path
    pool: Path
    plant: ThemeDocuments
    census: str = THEME_CENSUS_RUN
    infer: str = THEME_INFER_RUN
    #: The experiment's folder identifier by planted unit (``t2_03``), as the themes read it.
    ids: dict[str, str] = field(default_factory=dict)
    #: The experiment folder below the pool by planted unit.
    folders: dict[str, str] = field(default_factory=dict)


def build_topic_project(base: Path) -> TopicProject:
    """Build the project under ``base`` and name its experiments."""
    from ingence.api.v1 import context_for, dispatch

    project, plant = build_theme_project(base)
    status, body = dispatch(context_for(project, settled=None, job_running=False), "themes", {})
    assert status == 200, body
    by_folder = {row["folder"]: row["id"] for row in body["experiments"]}
    ids = {unit: by_folder[folder] for unit, folder in plant.folders.items() if folder in by_folder}
    return TopicProject(
        project=project, pool=base / "pool", plant=plant, ids=ids, folders=dict(plant.folders)
    )


def rerun(built: TopicProject, additions: dict[str, list[str]]) -> str:
    """Write files into experiment folders (``{unit: [file names]}``) and run a second census and
    inference over the pool; return the new inference's run identifier.

    A new acquisition file named with words adds those words to its experiment's terms (a new
    movie with a new word changes its experiment's set), and nothing else about the experiment.
    """
    from ingence.core.project import Project
    from ingence.infer.run import run_inference
    from ingence.scan.census import run_census

    for unit, names in additions.items():
        folder = built.pool / built.folders[unit]
        for name in names:
            path = folder / name
            path.write_text(f"{unit} {name}\n")
            os.utime(path, (1.7e9, 1.7e9))
    opened = Project.open(built.project)
    run_census(opened, run_id=SECOND_CENSUS_RUN, started_at="t")
    run_inference(opened, run_id=SECOND_INFER_RUN, started_at="t")
    return SECOND_INFER_RUN
