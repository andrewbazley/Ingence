"""``/api/v1/ladder``: the levels a project's rows climb, and the word for each.

A root is a folder (a laboratory's: ``lab``) or an archive's listing (``archive``). The Years view
draws one component over either, a row per thing at the current level, and each sentence there
that names a level (``A row per person``, ``12 studies``) reads the word from this route and
carries none of its own. The words come from ``api/plain_words.yaml`` (``levels``):

- a lab's ladder is project, person, line of work, experiment, file;
- an archive's is archive, theme, study, file, with no person level: the first folder below
  an archive's root is not a person's. A lab has none either where the setting that says which
  folder is a person's (``themes.person_level``) is blank.

The project's ladder is the archive's only where every root is a listing; a project that reads a
listing beside a folder is read as a lab (each root's own kind is listed beside it).

Nothing here reads a file, writes anything or draws a conclusion: it is a description of the
levels, read from the project's roots and one setting.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.plain_words import plain_words
from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, Identifier, register
from ingence.api.v1.shaping import pinned_catalog
from ingence.listing.format import parse_listing_uri

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["LadderResponse", "ladder"]

type RootKind = Literal["lab", "archive"]
type LevelKind = Literal["root", "person", "theme", "experiment", "file"]

#: The levels of each kind of root, top first, as the Years view climbs them. A kind with no
#: person level is an archive's.
_LEVELS: dict[str, tuple[LevelKind, ...]] = {
    "lab": ("root", "person", "theme", "experiment", "file"),
    "archive": ("root", "theme", "experiment", "file"),
}
#: What the page draws for each level: where its rows come from and which axis they stand on.
_SOURCE: dict[LevelKind, str] = {
    "root": "/api/v1/themes (experiments, themes, people), /api/v1/completeness (sums)",
    "person": "/api/v1/themes (a projection recomputed when read; never a conclusion)",
    "theme": "/api/v1/themes, /api/v1/completeness",
    "experiment": "/api/v1/experiments/files (each file with what the engine found about it)",
    "file": "/api/v1/entry, /api/v1/experiments/files",
}
_MEASURES: dict[LevelKind, list[str]] = {
    "root": ["count", "raw followed by analysis"],
    "person": ["raw followed by analysis", "a person said so"],
    "theme": ["raw followed by analysis", "a person said so"],
    "experiment": ["stage", "kind"],
    "file": [],
}


class LadderLevel(ApiModel):
    """One level: its word, what a row is there, and where the rows come from."""

    kind: LevelKind = Field(
        description="Which level: the root, a person, a theme, an experiment or a file."
    )
    name: str = Field(description="The word for one of these, in this root's kind: ``study``.")
    plural: str = Field(description="The word for several: ``studies``.")
    rows: str = Field(description="What a row is at this level, in words, naming the level below.")
    source: str = Field(description="Which routes supply the rows.")
    axis: str = Field(description="The time axis the rows are drawn on.")
    measures: list[str] = Field(description="The measures that color a row at this level.")
    broad: str | None = Field(
        default=None,
        description="The word for the coarser groups of these, where any: ``broad lines``.",
    )


class LadderRoot(ApiModel):
    """One root of the project and the kind of ladder it climbs."""

    root_id: Identifier
    kind: Literal["lab", "archive"] = Field(
        description="``archive`` for an archive's listing, ``lab`` for a folder."
    )


@register("ladder")
class LadderResponse(ApiModel):
    """``GET /api/v1/ladder``: the project's ladder and the kind of each of its roots."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str
    run_id: Identifier
    kind: Literal["lab", "archive"] = Field(
        description="The project's ladder: ``archive`` where every root is a listing, else ``lab``."
    )
    levels: list[LadderLevel] = Field(description="The project's levels, the root first.")
    roots: list[LadderRoot]


def _kind_of(uri: str) -> RootKind:
    """A root's kind: an archive where the engine reads the address as a listing's (the check
    the project's roots use, :func:`ingence.core.project.is_listing_root`), else a folder."""
    return "archive" if parse_listing_uri(uri) is not None else "lab"


def ladder_kind(kinds: Sequence[RootKind]) -> RootKind:
    """The project's kind: an archive's where there is a root and every root is a listing."""
    return "archive" if kinds and all(kind == "archive" for kind in kinds) else "lab"


def _levels(
    kind: RootKind, *, people: bool, area_word: tuple[str, str] | None = None
) -> list[LadderLevel]:
    """The levels of ``kind``, each with its words; the person level only where ``people``, and
    its word the areas' kind's ("instrument", "year", "folder") where
    ``area_word`` gives one."""
    words = plain_words()
    singular: Mapping[str, str] = words.levels.get(kind, {})
    many: Mapping[str, str] = words.level_plurals.get(kind, {})
    kinds = [level for level in _LEVELS[kind] if people or level != "person"]
    named = {level: (singular[level], many[level]) for level in kinds}
    if "person" in named and area_word is not None:
        named["person"] = area_word
    broad = "broad themes" if kind == "lab" else f"broad {named['theme'][1]}"
    below = dict(pairwise(kinds))
    rows: dict[LevelKind, str] = {
        "root": (
            f"{named['person'][1]}, where the first folder below a root is read as a person's; "
            f"otherwise {named['theme'][1]}"
            if "person" in named
            else f"{named['theme'][1]}, under their {broad}"
        ),
        "person": f"{named['theme'][1]}, under their {broad}",
        "theme": named[below["theme"]][1] if "theme" in below else "",
        "experiment": "files, grouped by folder, stage or kind, or one by one",
        "file": (
            "its record: the parts of its name, its clocks, every conclusion with its rule, "
            "what it was made from, its copies"
        ),
    }
    axis: dict[LevelKind, str] = {
        "root": f"the year of each {named['experiment'][0]}",
        "person": f"the date of each {named['experiment'][0]}",
        "theme": f"the {named['experiment'][0]}'s date, a line to the last file written",
        "experiment": (
            "the believed time of each file (the earliest credible time rule), else its "
            "modification"
        ),
        "file": "its own clocks, sources never merged",
    }
    return [
        LadderLevel(
            kind=level,
            name=named[level][0],
            plural=named[level][1],
            rows=rows[level],
            source=_SOURCE[level],
            axis=axis[level],
            measures=_MEASURES[level],
            broad=broad if level == "theme" else None,
        )
        for level in kinds
    ]


@route("ladder", model=LadderResponse)
def ladder(context: V1Context, query: Mapping[str, Sequence[str]]) -> LadderResponse:
    """The pinned run's project ladder, with the word for each level."""
    del query
    from ingence.core.project import Project

    with pinned_catalog(context) as (catalog, run_id):
        found = catalog.query("SELECT root_id, root_uri FROM roots ORDER BY root_id").fetchall()
    roots = [LadderRoot(root_id=str(row[0]), kind=_kind_of(str(row[1]))) for row in found]
    kind = ladder_kind([root.kind for root in roots])
    people = (
        kind == "lab" and Project.open(context.project).settings.themes.person_level is not None
    )
    area_word = None
    if people:
        # The level's word comes from the areas' kind: what most of the folders at the area level
        # stand for, read from the run's areas.
        from ingence.areas import KIND_WORDS, area_kinds_of_run, leading_kind

        leading = leading_kind(
            kind for kind, _ in area_kinds_of_run(context.project, run_id).values()
        )
        if leading is not None and leading != "person":
            area_word = KIND_WORDS[leading]
    return LadderResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        kind=kind,
        levels=_levels(kind, people=people, area_word=area_word),
        roots=roots,
    )
