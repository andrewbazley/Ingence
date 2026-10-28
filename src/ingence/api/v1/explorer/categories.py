"""What each measure the Graph workspace colors by means, category by category.

One measure is colored at a time. Each states its question, its denominator and its categories, so
a person never has to guess what a filled wedge claims. The categories are computed once per run by
:mod:`~ingence.api.v1.explorer.projection` and never re-derived in the page. The page receives
counts and these definitions, and draws.

There are four measures, each over the files beneath a folder. A folder is counted by what it
holds. A member promoted from inside an archive or container is not a file of the folder hierarchy
and is left out of every denominator.

- ``exam``: was its content read in this run? The answer comes from the scan's own outcome. A link
  to an archive outside the root is ``elsewhere``: its bytes are not here to read, which is neither
  "read" nor "not read".
- ``stage``: what stage do the rules read it as? The answer comes from the stage claims of the run.
  A stage is a reading of a name or a format, never evidence that a file was analyzed. Two stage
  families on one file are ``conflicting``, not a choice.
- ``prov``: is it joined by published provenance? A file at either end of a published ``derives``
  edge counts, else of a published ``references`` edge, else of a proposed pair only. A published
  line and a proposal are never counted together.
- ``kind``: what format family is it? The answer comes from the run's format claim, by the display
  families of ``api/explorer_kinds.yaml``. A known format outside the families is ``other``. A
  file with no format claim is ``unknown``. A format claim published as conflicting (its defeater
  fired), or two families read for one file, is ``conflicting``, never the settled kind.

Each category carries a level that the page draws it with: ``yes``, ``partial``, ``problem``,
``elsewhere``, ``no`` or ``unknown`` for the three state measures, and ``kind`` for the kind
families. So every measure reads the same way: the darker the fill, the more the run holds. A
problem is marked, and what is unknown is hatched, never blank.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Literal

import yaml

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

__all__ = [
    "EXAM",
    "KIND",
    "MEASURES",
    "PROV",
    "STAGE",
    "Category",
    "Measure",
    "kind_families",
    "stage_families",
]

Level = Literal["yes", "partial", "problem", "elsewhere", "no", "unknown", "kind"]


@dataclass(frozen=True, slots=True)
class Category:
    key: str
    label: str
    level: Level
    meaning: str


@dataclass(frozen=True, slots=True)
class Measure:
    key: str
    label: str
    question: str
    denominator: str
    categories: tuple[Category, ...]
    #: The category that stands for "the run could not say", counted and shown, never dropped.
    unknown: str

    def category_keys(self) -> tuple[str, ...]:
        return tuple(category.key for category in self.categories)


_FILES = "files beneath this folder, not counting those read from inside an archive or container"

EXAM = Measure(
    key="exam",
    label="Examined",
    question="Was the file's content read in this run?",
    denominator=_FILES,
    categories=(
        Category(
            "read",
            "content read",
            "yes",
            "a reader opened the file and recorded what it "
            "found (extracted, or extracted with a fallback reader)",
        ),
        Category(
            "set_aside",
            "set aside",
            "partial",
            "left unread by a person's answer, a "
            "policy or the dive's budget (skipped by policy, or deferred)",
        ),
        Category(
            "by_settings",
            "left out by the settings",
            "partial",
            "left unread by the project's extraction settings (the extensions to read or leave "
            "out, or the largest size read); the Coverage view says which",
        ),
        Category(
            "failed",
            "reading failed",
            "problem",
            "a reader tried and failed, or the file changed while it was read",
        ),
        Category(
            "elsewhere",
            "archive link",
            "elsewhere",
            "a link to an archive outside the root: its bytes are not here to read",
        ),
        Category(
            "named",
            "name and metadata only",
            "no",
            "only its name, size and times were recorded; its content was not opened",
        ),
        Category(
            "unknown",
            "not stated",
            "unknown",
            "the run records no scan outcome this workspace knows",
        ),
    ),
    unknown="unknown",
)

STAGE = Measure(
    key="stage",
    label="Stage reading",
    question="What stage do the run's rules read the file as, from its name or format?",
    denominator=_FILES,
    categories=(
        Category("acquisition", "acquisition", "yes", "read as an acquisition or raw data"),
        Category("processing", "processing", "yes", "read as processed, exported or converted"),
        Category(
            "analysis",
            "analysis",
            "yes",
            "read as analysis from its name: a reading, not evidence that anything was analyzed",
        ),
        Category("summary", "summary and figures", "yes", "read as a summary, report or figure"),
        Category("other", "another stage", "partial", "a stage value outside the four families"),
        Category(
            "conflicting",
            "readings conflict",
            "problem",
            "two stage families were read "
            "for the file, or a stage conclusion was published as conflicting",
        ),
        Category("none", "no stage read", "no", "no stage rule concluded on the file"),
    ),
    unknown="none",
)

PROV = Measure(
    key="prov",
    label="Provenance",
    question="Is the file joined to another by provenance this run published?",
    denominator=_FILES,
    categories=(
        Category(
            "derivation",
            "published derivation",
            "yes",
            "at either end of a published 'produced from' edge",
        ),
        Category(
            "reference",
            "published mention",
            "partial",
            "at either end of a published "
            "'mentions' edge, and of no derivation: a mention is never 'made from'",
        ),
        Category(
            "proposed",
            "proposed only",
            "elsewhere",
            "named only by a proposed pair nobody has asserted",
        ),
        Category(
            "none",
            "none recorded",
            "no",
            "no published or proposed provenance touches it: not evidence that it has none",
        ),
    ),
    unknown="none",
)

KIND = Measure(
    key="kind",
    label="Kind",
    question="What family of format is the file, as the run concluded it?",
    denominator=_FILES,
    categories=(
        Category("image", "images and video", "kind", "an image, image stack or video format"),
        Category("table", "tables", "kind", "a table or a table-holding database"),
        Category("text", "text and documents", "kind", "text, documents, logs, markup"),
        Category("archive", "archives and containers", "kind", "an archive or container"),
        Category("array", "arrays, volumes and meshes", "kind", "arrays, volumes, meshes"),
        Category("other", "another known format", "kind", "a format outside the five families"),
        Category(
            "conflicting",
            "format readings conflict",
            "problem",
            "its format conclusion was published as conflicting (a rule that withdraws it "
            "fired), or two format families were read for it: the run does not settle its kind",
        ),
        Category(
            "unknown", "format not concluded", "unknown", "the run holds no format conclusion"
        ),
    ),
    unknown="unknown",
)

#: Every measure, in the order the page offers them.
MEASURES: tuple[Measure, ...] = (EXAM, STAGE, PROV, KIND)


def _kinds_file() -> Traversable:
    """The display families' file, as installed."""
    return resources.files("ingence.api").joinpath("explorer_kinds.yaml")


def _families() -> dict[str, dict[str, dict[str, object]]]:
    """The display families, read again whenever their file changes on disk, so an edit shows
    on the next request with no restart (a projection built with other families is rebuilt)."""
    source = _kinds_file()
    try:
        stamp = os.stat(str(source)).st_mtime_ns
    except OSError:
        stamp = 0  # installed inside an archive: it cannot be edited, and is read once
    return _families_as_of(str(source), stamp)


@lru_cache(maxsize=4)
def _families_as_of(where: str, stamp: int) -> dict[str, dict[str, dict[str, object]]]:
    del where, stamp  # the cache's key: the file and when it last changed
    loaded = yaml.safe_load(_kinds_file().read_text("utf-8"))
    return {"kinds": dict(loaded["kinds"]), "stages": dict(loaded["stages"])}


def kind_families() -> dict[str, tuple[str, ...]]:
    """Each kind family and the format terms it holds, in color order."""
    return {
        family: tuple(str(term) for term in held.get("formats", ()))  # type: ignore[attr-defined]
        for family, held in _families()["kinds"].items()
    }


def stage_families() -> dict[str, tuple[str, ...]]:
    """Each stage family and the stage values a rule writes that it holds."""
    return {
        family: tuple(str(value) for value in held.get("values", ()))  # type: ignore[attr-defined]
        for family, held in _families()["stages"].items()
    }
