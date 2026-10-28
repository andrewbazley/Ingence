"""The sample collection read into a project, for the agent interface's tests.

The shipped sample collection (tier S, seed 1: invented members, invented folders) is written
into a temporary folder, listed and inferred over, so the ``/api/v1`` routes answer from a real
run. Its files' contents are not read (no dive): the tests here are about what leaves a project,
not about what the reading finds.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ingence.agent.shape import Vocabulary
from ingence.core.project import Project
from ingence.evaluation.sample_lab import generate_sample_lab
from ingence.infer.run import run_inference
from ingence.scan.census import run_census

__all__ = [
    "SampleProject",
    "build_sample_project",
    "checkable_names",
    "names_of_the_pool",
    "survivors",
]

CENSUS_RUN = "run_20260101T000001Z_111111111111"
INFER_RUN = "run_20260101T000002Z_222222222222"
_WORD = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*")


@dataclass(frozen=True, slots=True)
class SampleProject:
    """The project, the pool it reads, and the key the generator wrote beside the pool."""

    project: Project
    pool: Path
    unscored_path: Path


def build_sample_project(base: Path) -> SampleProject:
    """Write the sample collection under ``base`` and read it into a project beside it."""
    lab = generate_sample_lab(base / "lab", tier="S", seed=1)
    project = Project.create(base / "project", created_at="t", roots=[lab.pool])
    run_census(project, run_id=CENSUS_RUN, started_at="t")
    run_inference(project, run_id=INFER_RUN, started_at="t")
    _make_topic(project.path)
    return SampleProject(project=project, pool=lab.pool, unscored_path=lab.unscored_path)


#: The topic the fixture makes: the person's own name for it.
TOPIC_NAME = "My own topic"


def _make_topic(project: Path) -> None:
    """Make a topic with three members: an experiment, an entry and a core.

    The entry is the experiment's first file. The core's terms are two words of the experiment
    folder's own name. A test can then see whether a member's identifier, path or discovered term
    leaves the project.
    """
    from ingence.api import v1 as api_v1
    from ingence.api.routes.overlay import prepare_overlay, write_prepared

    context = api_v1.context_for(project, settled=None, job_running=False)
    _, listed = api_v1.dispatch(
        context, "experiments", {"limit": ["1"], "sort": ["date"], "order": ["asc"]}
    )
    experiment = listed["rows"][0]
    _, files = api_v1.dispatch(
        context, "experiments/files", {"experiment": [experiment["id"]], "limit": ["1"]}
    )
    terms = [word for word in _WORD.findall(experiment["name"]) if word.isalpha()][:2]
    members = [
        {"kind": "experiment", "id": experiment["id"]},
        {"kind": "entry", "id": files["rows"][0]["id"]},
        {"kind": "core", "terms": terms},
    ]
    edits = [{"kind": "topic", "name": TOPIC_NAME, "members": members}]
    when = "2026-06-01T10:00:00Z"
    prepared = prepare_overlay(project, actor="a. person", occurred_at=when, edits=edits)
    write_prepared(project, prepared, actor="a. person", occurred_at=when)


def names_of_the_pool(sample: SampleProject) -> tuple[set[str], set[str]]:
    """Return the names of the pool and the names of its members.

    The first set holds every file and folder name of the pool, with and without its extension.
    The second set holds every member's name, whole and in parts, and their folder's name.
    """
    names: set[str] = set()
    for _, folders, files in os.walk(sample.pool):
        for name in (*folders, *files):
            names.add(name)
            names.add(os.path.splitext(name)[0])
    people: set[str] = set()
    key = json.loads(sample.unscored_path.read_text(encoding="utf-8"))
    for member in key["sections"]["members"]["entries"]:
        people.add(member["name"])
        people.update(_WORD.findall(member["name"]))
        people.add(Path(member["folder"]).name)
    return names, people


def checkable_names(names: set[str], vocabulary: Vocabulary) -> set[str]:
    """The names worth checking for: at least three characters, and not one public word.

    A folder called ``raw`` or ``2021`` is a word that the engine itself uses. It is not a name
    from the data.
    """
    kept: set[str] = set()
    for name in names:
        words = _WORD.findall(name)
        if len(name) < 3 or not words:
            continue
        if len(words) == 1 and vocabulary.knows(words[0]):
            continue
        kept.add(name)
    return kept


def survivors(text: str, names: set[str]) -> list[str]:
    """The names found in a text.

    A single plain word counts only as a whole word (``Red`` is not in ``credible``). Any other
    name counts anywhere in the text.
    """
    folded = text.casefold()
    found = []
    for name in names:
        needle = name.casefold()
        if name.isalpha():
            if re.search(rf"(?<![^\W_]){re.escape(needle)}(?![^\W_])", folded):
                found.append(name)
        elif needle in folded:
            found.append(name)
    return sorted(found)
