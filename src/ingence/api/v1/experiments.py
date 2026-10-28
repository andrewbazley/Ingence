"""``GET /api/v1/experiments``: the experiments the engine found, how complete each is, and how
its files were tied to it (the Explore > Experiments tab).

The route builds one list on the one definition. An experiment is what ``ingence.experiment_units``
says: the folders the experiment rules concluded on, made one set by the clustering rule's policy,
with each file in the highest such folder above it and none beneath an installed program. Its
counts are :func:`~ingence.completeness.completeness_of_run`'s. So this list, the Years view, "No
analysis found" and an experiment's files (``/api/v1/experiments/files``) agree on which
experiments there are and how many files each holds.

What this route adds to those figures, each computed here from the same tables and nothing
invented:

- Its files by stage and by kind. A file's stage is the class ``completeness/classes.yaml``
  declares (a raw acquisition, an analysis file), another stage a rule read, stage readings in
  conflict, or no stage read. Its kind is the format family of the Graph workspace
  (``api/explorer_kinds.yaml``). Each list sums to the experiment's files.
- How well it is characterized: of its files, how many have a stage read and how many a role
  read, each over the experiment's files. A reading a rule published as conflicting is counted
  apart (unknown, unexamined and conflicting are three states).
- The pairing assumption, shown. Completeness counts a raw file as followed when a pair links
  it to an analysis file or a results table, whether a person confirmed the pair or the engine
  only proposed it. The split here says how many followed raw files rest on a pair a person
  confirmed on Questions > Proposed pairs and how many on pairs nobody answered, which the count
  takes as right.

The server filters, sorts and pages, so the page counts nothing. The filters are the person, the
line of work, the completeness state and a text on the name. Name, files, date and completeness
sort. A date or a share that is unknown sorts last in either direction (never as 0).
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.completeness import pinned_completeness, themes_not_computed
from ingence.api.v1.models import ApiModel, AreaKindName, Count, Identifier, register
from ingence.api.v1.names import name_fields
from ingence.api.v1.shaping import integer, one, pinned_catalog
from ingence.areas import area_id, area_kinds_of_run, area_of
from ingence.core.project import Project

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ingence.api.v1.experiment_files import ClaimReading
    from ingence.completeness import ExperimentFound
    from ingence.completeness.counting import Classes
    from ingence.core.settings import CompletenessSettings
    from ingence.experiment_units import RunUnits
    from ingence.storage.catalog import Catalog

__all__ = [
    "DEFAULT_LIMIT",
    "LIMIT",
    "PAIRING_ASSUMPTION",
    "SORTS",
    "STATES",
    "ExperimentRow",
    "ExperimentsResponse",
    "confirmed_followed",
    "experiments",
    "file_tallies",
]

#: How many experiments a page lists unless it asks, and the most it may ask for.
DEFAULT_LIMIT = 50
LIMIT = 500

#: The sorts a request may name, and the directions.
SORTS = ("name", "files", "date", "completeness")
ORDERS = ("asc", "desc")

#: The four completeness states a filter offers: whether raw files and whether analysis were
#: found in what was read (never whether they exist), with the words a page shows. Analysis found
#: is the No analysis found list's own judgment for an experiment with enough raw files
#: (:func:`state_of`), so a row is never "Raw and analysis found" and on that list.
STATES: dict[str, str] = {
    "both": "Raw and analysis found",
    "raw_only": "Raw found, no analysis found",
    "analysis_only": "Analysis found, no raw found",
    "neither": "No raw files or analysis found",
}

#: What the pairing does, in the words a page shows at the top of the tab.
PAIRING_ASSUMPTION = (
    "A raw file counts as analyzed when a pair or link joins it to an analysis file or "
    "results table. Ingence counts the pairs you confirmed in Checks > Suggested pairs. It also "
    "counts its own pairs and links that nobody has answered. These are suggested pairs, files "
    "made from a raw file, and files named after a raw file. Ingence assumes they are right, and "
    "this page does not check them."
)

#: Each stage bucket of a file and the words for it. Mutually exclusive, in this order, and
#: summing to the experiment's files.
STAGE_BUCKETS: tuple[tuple[str, str], ...] = (
    ("raw", "raw files"),
    ("analysis", "analysis files"),
    ("other", "another stage"),
    ("conflicting", "conflicting stages"),
    ("none", "no stage found"),
)

_UNITS_TABLE = "experiments_route_units"
_KINDS_TABLE = "experiments_route_kinds"
_TERMS_TABLE = "experiments_route_terms"


# ---------------------------------------------------------------------------
# The response models.
# ---------------------------------------------------------------------------


class ExperimentClassCount(ApiModel):
    """How many of an experiment's files fall in one class."""

    key: str = Field(description="The class, as a program names it.")
    label: str = Field(description="The class in words.")
    files: Count = Field(description="Files of the experiment in this class.")


class ExperimentCharacterized(ApiModel):
    """How well the engine characterized an experiment's files: each count over its files."""

    files: Count = Field(description="The experiment's files: the denominator of every count.")
    stage_read: Count = Field(description="Files with a stage read (a rule concluded one).")
    stage_conflicting: Count = Field(
        description="Files whose stage readings were published as conflicting and none settled."
    )
    role_read: Count = Field(description="Files with a role read (a rule concluded one).")
    role_conflicting: Count = Field(
        description="Files whose role readings were published as conflicting and none settled."
    )


class ExperimentFollowed(ApiModel):
    """The raw files found followed by an analysis file, and what that rests on."""

    raw_found: Count = Field(description="Raw files found in the experiment: the denominator.")
    followed: Count = Field(
        description="Raw files found followed by an analysis file or a results table found."
    )
    by_confirmed: Count = Field(
        description="Of those, the ones followed through a pair a person confirmed."
    )
    assumed: Count = Field(
        description=(
            "Of those, the ones followed only through the engine's own pairs and links, which "
            "nobody confirmed: pairs it proposed, derivations it published and files named for "
            "the raw file. They count as followed because the engine takes them as right."
        )
    )
    share: float | None = Field(
        description="followed over raw_found; null where no raw file was found (never 0)."
    )
    said: str = Field(description="The whole of it in one sentence, for a page to show.")
    n: Count = Field(description="The same number as followed, under the 0.4.0 pages' name.")
    of: Count = Field(description="The same number as raw_found: what n is out of.")


class ExperimentRepeats(ApiModel):
    """How many times an experiment was repeated, and the group of repeats it is in."""

    n: Count = Field(
        description="Experiments read as repeats of this one, itself included; 0 where none."
    )
    group: str | None = Field(
        description="The repeat group's identifier; null where it is in none."
    )


class ExperimentWorkflow(ApiModel):
    """The workflow an experiment follows, how far it got, and what the workflow expects."""

    id: str | None = Field(description="The workflow's identifier; null where none is known.")
    reached: str | None = Field(description="The last stage of the workflow its files reached.")
    expected: list[str] = Field(description="The workflow's stages, in order.")


class ExperimentTopic(ApiModel):
    """A topic an experiment is in: a person's own name for a set of the engine's groupings."""

    id: Identifier = Field(description="The topic's identifier.")
    name: str = Field(description="The name the person gave it.")


class ExperimentRow(ApiModel):
    """One experiment the engine found."""

    id: Identifier = Field(description="The experiment folder's entry.")
    folder: str = Field(description="The folder, relative to its root.")
    name: str = Field(description="The folder's own name, as written.")
    shown_name: str = Field(description="The name without the dates the tokenizer read in it.")
    root_id: Identifier
    person: str | None = Field(
        description="Whose it is, where a setting names a folder level for a person; else null."
    )
    area: str | None = Field(
        description="The area it is in: the folder at the area level (themes.area_level)."
    )
    area_kind: AreaKindName | None = Field(
        description="What the area's folder stands for; null where it is in no area."
    )
    repeats: ExperimentRepeats = Field(description="Its repeats, and the group they make.")
    workflow: ExperimentWorkflow | None = Field(
        description="The workflow it follows and how far it got; null where none is known."
    )
    date: str | None = Field(
        description=(
            "Its first date, as the Years view dates it (a date in a name, else the earliest "
            "believed time of a file in it); null where nothing dates it."
        )
    )
    last_file_time: str | None = Field(
        description="The last time a file in it was written, as the Years view has it; else null."
    )
    found_by: Literal["structure", "dated_name"] = Field(
        description="Which experiment rule concluded the folder: what it holds, or its dated name."
    )
    found_by_kind: str = Field(
        description="What the rule concluded: experiment, acquisitions, analysis or named."
    )
    found_by_said: str = Field(description="How the folder was found, in words.")
    files: Count = Field(description="Files found in it (none beneath an installed program).")
    by_stage: list[ExperimentClassCount] = Field(description="Its files by stage; sums to files.")
    by_kind: list[ExperimentClassCount] = Field(
        description="Its files by kind of format; sums to files."
    )
    raw_acquisitions: Count = Field(description="Raw files found in it.")
    analysis_files: Count = Field(description="Files found in it at an analysis stage.")
    result_tables: Count = Field(description="Results tables found in it.")
    derived_files: Count = Field(
        description="Files found derived from its raw files, wherever they lie."
    )
    has_analysis: bool = Field(
        description=(
            "Whether its analysis files or results tables count as an analysis of its own, "
            "under the completeness settings."
        )
    )
    state: Literal["both", "raw_only", "analysis_only", "neither"] = Field(
        description=(
            "Whether raw files and analysis were found: analysis is found when it holds an "
            "analysis file or a results table, or a raw file of it is followed."
        )
    )
    state_said: str
    state_why: str = Field(
        description=(
            "How the state was judged, in words: as the No analysis found list judges an "
            "experiment with enough raw files, or by what was found where it has too few."
        )
    )
    no_analysis_found: Literal["listed", "answered"] | None = Field(
        description=(
            "'listed': it is on the No analysis found list; 'answered': it would be, but a person "
            "said its analysis is elsewhere and this run read the answer; null: neither."
        )
    )
    followed: ExperimentFollowed
    characterized: ExperimentCharacterized
    theme: str | None = Field(
        description="Its line of work (a theme) where themes are computed; else null."
    )
    theme_label: str | None = Field(description="The words of its line of work; else null.")
    topics: list[ExperimentTopic] = Field(
        description=(
            "The topics a person made that hold it, read from the Record when asked (a topic is "
            "an annotation, never read by inference); empty where none does or none was read."
        )
    )


class ExperimentsTopic(ApiModel):
    """One topic and how it reads in this run: the experiments it holds, or that it was not read."""

    id: Identifier
    name: str
    experiments: Count | None = Field(
        description=(
            "Experiments of this run the topic holds; null where it was not read (a run that "
            "applied no rules, or a reading that is not available): unknown, never 0."
        )
    )
    read: bool = Field(description="Whether any member of the topic was read in this run.")
    complete: bool = Field(
        description="False where a member could not be read: the topic may hold more than this."
    )
    said: str = Field(description="What the topic holds in the run, or why it was not read.")


class ExperimentFacet(ApiModel):
    """One value a filter offers and how many experiments hold it."""

    value: str
    label: str
    experiments: Count


class ExperimentsSummary(ApiModel):
    """Totals over every experiment of the run, whatever the filters keep."""

    experiments: Count
    files: Count = Field(description="Files in all the experiments (none outside one).")
    files_outside: Count = Field(description="Files found in no experiment.")
    raw_acquisitions: Count
    followed: Count
    no_analysis_found: Count = Field(description="Experiments on the No analysis found list.")
    judged: Count = Field(
        description="Experiments judged for that list (enough raw files found): its denominator."
    )


@register("experiments")
class ExperimentsResponse(ApiModel):
    """The experiments the engine found, one page of them, with what the filters offer."""

    schema_version: str
    run_id: Identifier
    counted_as: str = Field(description="What an experiment is in this run, in sentences.")
    pairing_assumption: str = Field(description="What a followed raw file rests on, in words.")
    summary: ExperimentsSummary
    matching: Count = Field(description="Experiments the filters keep.")
    offset: Count
    limit: Count
    sort: str
    order: str
    rows: list[ExperimentRow]
    people: list[ExperimentFacet]
    lines: list[ExperimentFacet]
    states: list[ExperimentFacet]
    themes_computed: bool
    themes_said: str
    topics: list[ExperimentsTopic] = Field(
        description="Every topic a person made, and how each reads in this run."
    )
    topics_read: bool = Field(
        description=(
            "False where the topics could not be read at all (the component is not available): "
            "the rows then carry none, and ``topics_said`` says why."
        )
    )
    topics_said: str = Field(
        description=(
            "Why topics were not read or not counted, in words; empty where they were. A run "
            "that applied no rules says its topics were not counted."
        )
    )
    topic_filter_said: str | None = Field(
        description=(
            "With ``topic``: what that topic holds in the run, why it was not read, or that it no "
            "longer stands."
        )
    )


# ---------------------------------------------------------------------------
# What this route counts that the completeness figures do not.
# ---------------------------------------------------------------------------

_TALLY_SQL = f"""
WITH
live AS (
    SELECT e.entry_id, u.experiment_id
    FROM entries e JOIN {_UNITS_TABLE} u ON u.folder_id = e.parent_entry_id
    WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
),
claimed AS (
    SELECT c.subject AS entry_id,
           c.resolution_status = 'resolved' AS resolved,
           -- Completeness' own spelling, for the raw and analysis buckets only.
           c.predicate LIKE 'classifies.%stage%' AS spelled_stage,
           -- The files page's reading of a claim's kind: the value is a sense of that kind in the
           -- run's Dictionary, or, for a kind the Dictionary holds no sense of, the spelling.
           (coalesce(t.stage, false) OR ($stage_by_spelling AND c.predicate LIKE $stage_pattern))
               AS is_stage,
           (coalesce(t.role, false) OR ($role_by_spelling AND c.predicate LIKE $role_pattern))
               AS is_role,
           (coalesce(t.format, false) OR ($format_by_spelling AND c.predicate LIKE $format_pattern))
               AS is_format,
           c.object_reference AS value,
           coalesce(k.family, 'other') AS family
    FROM claims c
    LEFT JOIN {_TERMS_TABLE} t ON t.term = c.object_reference
    LEFT JOIN {_KINDS_TABLE} k ON k.term = c.object_reference
    WHERE c.predicate LIKE 'classifies.%' AND c.object_reference IS NOT NULL
      AND c.resolution_status IN ('resolved', 'examined_conflicting')
      AND c.subject IN (SELECT entry_id FROM live)
      AND (t.term IS NOT NULL OR c.predicate LIKE 'classifies.%stage%'
           OR c.predicate LIKE $role_pattern OR c.predicate LIKE $format_pattern)
),
per_file AS (
    SELECT entry_id,
           bool_or(spelled_stage AND resolved AND list_contains($raw, value)) AS at_acquisition,
           bool_or(spelled_stage AND resolved AND list_contains($analysis, value)) AS at_analysis,
           bool_or(is_stage AND resolved) AS stage_read,
           bool_or(is_stage AND NOT resolved) AS stage_conflicting,
           bool_or(is_role AND resolved) AS role_read,
           bool_or(is_role AND NOT resolved) AS role_conflicting,
           count(DISTINCT family) FILTER (WHERE is_format AND resolved) AS families,
           min(family) FILTER (WHERE is_format AND resolved) AS family,
           bool_or(is_format AND NOT resolved) AS format_conflicting
    FROM claimed GROUP BY entry_id
),
bucketed AS (
    SELECT l.experiment_id,
           CASE WHEN p.at_analysis THEN 'analysis'
                WHEN p.at_acquisition THEN 'raw'
                WHEN p.stage_read THEN 'other'
                WHEN p.stage_conflicting THEN 'conflicting'
                ELSE 'none' END AS stage_bucket,
           -- A raw or an analysis stage is a stage read, whatever spelled it.
           CASE WHEN p.stage_read OR p.at_acquisition OR p.at_analysis THEN 'read'
                WHEN p.stage_conflicting THEN 'conflicting'
                ELSE 'none' END AS stage_state,
           CASE WHEN p.role_read THEN 'read'
                WHEN p.role_conflicting THEN 'conflicting'
                ELSE 'none' END AS role_state,
           CASE WHEN p.families >= 2 THEN 'conflicting'
                WHEN p.families = 1 THEN p.family
                WHEN p.format_conflicting THEN 'conflicting'
                ELSE 'unknown' END AS kind_bucket
    FROM live l LEFT JOIN per_file p ON p.entry_id = l.entry_id
)
SELECT experiment_id, 'stage' AS axis, stage_bucket AS bucket, count(*) AS files
FROM bucketed GROUP BY 1, 2, 3
UNION ALL
SELECT experiment_id, 'kind', kind_bucket, count(*) FROM bucketed GROUP BY 1, 2, 3
UNION ALL
SELECT experiment_id, 'stage_state', stage_state, count(*) FROM bucketed GROUP BY 1, 2, 3
UNION ALL
SELECT experiment_id, 'role_state', role_state, count(*) FROM bucketed GROUP BY 1, 2, 3
"""  # noqa: S608 -- the only names spelled in are this module's constants


class _Tally:
    """One experiment's files counted by stage, by kind, and by how well they were read."""

    __slots__ = ("kinds", "role_state", "stage_state", "stages")

    def __init__(self) -> None:
        self.stages: Counter[str] = Counter()
        self.kinds: Counter[str] = Counter()
        self.stage_state: Counter[str] = Counter()
        self.role_state: Counter[str] = Counter()


def file_tallies(
    catalog: Catalog,
    run: RunUnits,
    classes: Classes,
    families: Mapping[str, Sequence[str]],
    reading: ClaimReading,
) -> dict[str, _Tally]:
    """Every experiment's files by stage, by kind of format, and how well each was read.

    A file's stage, role and format are read as the files page reads them (``reading``, from
    :func:`~ingence.api.v1.experiment_files.claim_reading`). A reading is a classifying claim whose
    value is a sense of that kind in the run's Dictionary. For a kind that the Dictionary holds no
    sense of, it is a claim whose predicate is spelled for that kind. A file's kind is the format
    read, mapped through ``families`` (the Graph workspace's display families; a known format
    outside them is ``other``). Only the raw and analysis buckets keep completeness' own spelling
    (``classes``), so they equal what completeness counts. A raw or an analysis stage is also a
    stage read. Only resolved claims settle a reading. A claim published as conflicting is counted
    as conflicting where nothing else settles it, and a file no claim touches is ``none`` or
    ``unknown``. The files counted are exactly completeness' (the filesystem's own, none beneath a
    program, by ``run.units``).
    """
    import pyarrow as pa

    from ingence.api.v1.explorer.categories import KIND

    units = run.units
    terms: dict[str, str] = {}
    for family, listed in families.items():
        for term in listed:
            terms.setdefault(term, family)
    read_as: dict[str, dict[str, bool]] = {}
    for kind, found in reading.terms.items():
        for term in found:
            read_as.setdefault(term, {})[kind] = True
    catalog.connection.register(
        _UNITS_TABLE,
        pa.table(
            {
                "folder_id": pa.array(list(units), type=pa.string()),
                "experiment_id": pa.array(list(units.values()), type=pa.string()),
            }
        ),
    )
    catalog.connection.register(
        _KINDS_TABLE,
        pa.table(
            {
                "term": pa.array(list(terms), type=pa.string()),
                "family": pa.array(list(terms.values()), type=pa.string()),
            }
        ),
    )
    catalog.connection.register(
        _TERMS_TABLE,
        pa.table(
            {
                "term": pa.array(list(read_as), type=pa.string()),
                **{
                    kind: pa.array(
                        [held.get(kind, False) for held in read_as.values()], type=pa.bool_()
                    )
                    for kind in ("stage", "role", "format")
                },
            }
        ),
    )
    try:
        rows = catalog.connection.execute(
            _TALLY_SQL,
            {
                "raw": list(classes.raw_acquisition_stages),
                "analysis": list(classes.analysis_stages),
                **{
                    f"{kind}_by_spelling": kind in reading.by_spelling
                    for kind in ("stage", "role", "format")
                },
                **{
                    f"{kind}_pattern": reading.patterns[kind]
                    for kind in ("stage", "role", "format")
                },
            },
        ).fetchall()
    finally:
        catalog.connection.unregister(_UNITS_TABLE)
        catalog.connection.unregister(_KINDS_TABLE)
        catalog.connection.unregister(_TERMS_TABLE)
    tallies: dict[str, _Tally] = {}
    shown_kinds = set(KIND.category_keys())
    for experiment, axis, bucket, files in rows:
        held = tallies.setdefault(str(experiment), _Tally())
        if axis == "kind" and bucket not in shown_kinds:
            bucket = "other"  # a family a later edit of the display families adds
        target = {
            "stage": held.stages,
            "kind": held.kinds,
            "stage_state": held.stage_state,
            "role_state": held.role_state,
        }[str(axis)]
        target[str(bucket)] += int(files)
    return tallies


_FLAGS_SQL = """
WITH ids AS (SELECT unnest($ids::VARCHAR[]) AS entry_id)
SELECT e.entry_id, e.parent_entry_id,
       coalesce(bool_or(c.predicate LIKE 'classifies.%stage%' AND c.resolution_status = 'resolved'
                        AND list_contains($raw, c.object_reference)), false) AS at_acquisition,
       coalesce(bool_or(c.predicate LIKE 'classifies.%stage%' AND c.resolution_status = 'resolved'
                        AND list_contains($analysis, c.object_reference)), false) AS at_analysis,
       coalesce(bool_or(c.predicate LIKE 'classifies.%stage%' AND c.resolution_status = 'resolved'
                        AND list_contains($not_result, c.object_reference)), false) AS at_raw_stage,
       coalesce(bool_or(c.predicate LIKE 'classifies.format%' AND c.resolution_status = 'resolved'
                        AND list_contains($tables, c.object_reference)), false) AS tabled
FROM entries e
JOIN ids ON ids.entry_id = e.entry_id
LEFT JOIN claims c ON c.subject = e.entry_id AND c.resolution_status = 'resolved'
                  AND (c.predicate LIKE 'classifies.%stage%'
                       OR c.predicate LIKE 'classifies.format%')
WHERE NOT e.is_deleted AND e.entry_kind = 'filesystem' AND e.entry_type <> 'directory'
GROUP BY e.entry_id, e.parent_entry_id
"""


def confirmed_followed(
    catalog: Catalog, pairs: Sequence[tuple[str, str]], run: RunUnits, classes: Classes
) -> dict[str, int]:
    """For each experiment, the raw files followed through a pair a person confirmed.

    ``pairs`` are the derivations a person confirmed, as completeness reads them from the Record
    (acquisition, follower). The test is the one completeness applies to every pair
    (``completeness/counting.py``). The earlier file is a raw acquisition inside an experiment.
    The later one is not raw and is an analysis file or a results table. Both are live
    filesystem files, and the later one lies beneath no installed program. A confirmed pair
    counts whatever the two names say. A raw file with several confirmed followers is counted
    once. This function computes the count from the same claims, because completeness serves the
    total only.
    """
    if not pairs:
        return {}
    ids = sorted({entry for pair in pairs for entry in pair})
    rows = catalog.connection.execute(
        _FLAGS_SQL,
        {
            "ids": ids,
            "raw": list(classes.raw_acquisition_stages),
            "analysis": list(classes.analysis_stages),
            "not_result": list(classes.not_result_stages),
            "tables": list(classes.result_table_formats),
        },
    ).fetchall()
    parent: dict[str, str | None] = {}
    is_raw: dict[str, bool] = {}
    follows: dict[str, bool] = {}
    for entry, folder, acquisition, analysis, raw_stage, tabled in rows:
        name = str(entry)
        parent[name] = None if folder is None else str(folder)
        is_raw[name] = bool(acquisition) and not bool(analysis)
        results_table = bool(tabled) and (bool(analysis) or not bool(raw_stage))
        follows[name] = not is_raw[name] and (bool(analysis) or results_table)
    found: dict[str, set[str]] = {}
    for acquisition_id, follower_id in pairs:
        if acquisition_id == follower_id:
            continue
        if not (is_raw.get(acquisition_id) and follows.get(follower_id)):
            continue
        experiment = run.units.get(parent[acquisition_id] or "")
        if experiment is None or (parent[follower_id] or "") in run.programs:
            continue
        found.setdefault(experiment, set()).add(acquisition_id)
    return {experiment: len(held) for experiment, held in found.items()}


# ---------------------------------------------------------------------------
# Words.
# ---------------------------------------------------------------------------

#: What each kind the structure rule concludes means, in the words a row shows.
_FOUND_BY_WORDS = {
    "named": "Its dated name: the folder's name holds a date and a word.",
    "experiment": "What it holds: raw files and the files named for them, kept together.",
    "acquisitions": "What it holds: raw files, with their analysis files kept elsewhere.",
    "analysis": "What it holds: analysis files named for raw files that lie elsewhere.",
}


def found_by_said(kind: str) -> str:
    """How a folder was found to be an experiment, in words."""
    return _FOUND_BY_WORDS.get(
        kind, f"The experiment rules read it as {kind.replace('_', ' ') or 'one'}."
    )


def _files_of(count: int) -> str:
    return f"{count} raw {'file' if count == 1 else 'files'}"


def followed_said(raw: int, followed: int, confirmed: int, assumed: int) -> str:
    """The followed count with its denominator and what it rests on, in one sentence."""
    if not raw:
        return "No raw files found in this experiment."
    return (
        f"{followed} / {_files_of(raw)} analyzed ({confirmed} by pairs you confirmed, "
        f"{assumed} by Ingence's own pairs and links)."
    )


def state_of(
    row: ExperimentFound, settings: CompletenessSettings
) -> tuple[Literal["both", "raw_only", "analysis_only", "neither"], str]:
    """Whether raw files and analysis were found, and how that was judged, in words.

    An experiment that the No analysis found list judges (``completeness.judged_from_acquisitions``
    raw files or more) has analysis found exactly where the list does not list it. It holds an
    analysis of its own (``has_analysis``), or its followed share is at or above
    ``completeness.listed_below_share``, the values completeness computes. So no judged row is
    "Raw and analysis found" and listed, or answered. Only where there are too few raw files to
    judge is analysis found by what was found: any analysis file, results table or followed raw
    file. Analysis found means found in what was read, never whether it exists.
    """
    raw = row.raw_acquisitions > 0
    judged = row.raw_acquisitions >= settings.judged_from_acquisitions
    if judged:
        share = row.share
        analysis = row.has_analysis or (share is not None and share >= settings.listed_below_share)
        said = (
            "Ingence used the rule of the No analysis found list. This experiment holds its own "
            "analysis, or enough of its raw files are analyzed."
            if analysis
            else "Ingence used the rule of the No analysis found list. Too few of its raw files "
            "are analyzed. Ingence also found too few analysis files or results tables for its "
            "own analysis."
        )
    else:
        analysis = row.analysis_files > 0 or row.result_tables > 0 or row.followed > 0
        said = (
            "Ingence found too few raw files to judge this experiment. The No analysis found list "
            f"judges experiments with {settings.judged_from_acquisitions} or more. Here, analysis "
            "counts as found when Ingence finds any analysis file, results table or analyzed raw "
            "file."
        )
    if raw and analysis:
        return "both", said
    if raw:
        return "raw_only", said
    return ("analysis_only" if analysis else "neither"), said


# ---------------------------------------------------------------------------
# The route.
# ---------------------------------------------------------------------------


class _Topics:
    """What the topics package says of every topic against the run: read when asked."""

    __slots__ = ("by_experiment", "facets", "held", "read", "said")

    def __init__(self) -> None:
        #: False where the topics could not be read at all.
        self.read = True
        #: Why they were not read or counted, in words; empty where all was read.
        self.said = ""
        self.facets: list[ExperimentsTopic] = []
        #: Each topic's experiments, by topic identifier (empty for a topic not read).
        self.held: dict[str, frozenset[str]] = {}
        self.by_experiment: dict[str, list[ExperimentTopic]] = {}


def read_topics(project: Path, run_id: str) -> _Topics:
    """Every topic against the run, read from the Record as it stands, never by inference.

    A topic is a person's annotation (``ingence.topics``). What its members resolve to is computed
    here when asked. A reading that is not available, or that fails, is said in words (``read``
    false) and never dropped. A topic none of whose members could be read says so (``read`` false
    on its entry and no experiments). A run that applied no rules says its topics were not counted.
    Nothing is written.
    """
    from ingence.api.routes.overlay import topics_standing
    from ingence.core.registry import ComponentNotAvailableError
    from ingence.topics import resolve
    from ingence.topics.said import topic_said

    found = _Topics()
    try:
        if not topics_standing(project):
            return found
        resolved = resolve(project, run_id, compared=None, suggestions=False)
    except ComponentNotAvailableError as error:
        found.read = False
        found.said = f"Ingence could not read your collections: {error}"
        return found
    except Exception as error:  # said in words, never a failed page for a view of the topics
        found.read = False
        found.said = f"Ingence could not read your collections: {error}"
        return found
    if resolved.run_experiments is None:
        found.said = (
            "Collections are not counted yet. This scan has not applied the rules, so it has no "
            "experiments yet."
        )
    for reading in resolved.topics:
        topic = reading.topic
        found.held[topic.topic_id] = frozenset(reading.experiments)
        found.facets.append(
            ExperimentsTopic(
                id=topic.topic_id,
                name=topic.name,
                experiments=len(reading.experiments) if reading.read else None,
                read=reading.read,
                complete=reading.complete,
                said=topic_said(reading, resolved.run_experiments),
            )
        )
        for experiment in reading.experiments:
            found.by_experiment.setdefault(experiment, []).append(
                ExperimentTopic(id=topic.topic_id, name=topic.name)
            )
    found.facets.sort(key=lambda facet: (facet.name.casefold(), facet.id))
    for held in found.by_experiment.values():
        held.sort(key=lambda each: (each.name.casefold(), each.id))
    return found


class _Held:
    """One experiment's figures before the filters, the sort and the page."""

    __slots__ = (
        "area",
        "area_kind",
        "confirmed",
        "date",
        "found",
        "kind",
        "last",
        "listed",
        "names",
        "repeats",
        "state",
        "state_why",
        "tally",
        "workflow",
    )

    def __init__(
        self,
        found: ExperimentFound,
        names: Mapping[str, object],
        kind: str,
        tally: _Tally,
        confirmed: int,
        dates: tuple[str | None, str | None],
        listed: Literal["listed", "answered"] | None,
        settings: CompletenessSettings,
        repeats: ExperimentRepeats | None = None,
        workflow: ExperimentWorkflow | None = None,
    ) -> None:
        self.found = found
        self.names = names
        self.kind = kind
        self.tally = tally
        self.confirmed = confirmed
        #: The area it is in and what the area's folder stands for.
        self.area: str | None = None
        self.area_kind: str | None = None
        self.date, self.last = dates
        self.listed = listed
        self.state, self.state_why = state_of(found, settings)
        # Its repeats and the step it reached, two projections over the run.
        self.repeats = repeats if repeats is not None else ExperimentRepeats(n=0, group=None)
        self.workflow = workflow

    @property
    def shown(self) -> str:
        return str(self.names["shown_name"])

    def base_key(self) -> tuple[str, str, str]:
        return (self.found.root_id, self.found.folder, self.found.experiment_id)

    def name_key(self) -> tuple[str, str, str]:
        return (self.shown.casefold(), self.found.folder.casefold(), self.found.experiment_id)


def _pick(
    query: Mapping[str, Sequence[str]], name: str, allowed: Sequence[str], default: str
) -> str:
    chosen = (one(query, name) or "").strip() or default
    if chosen not in allowed:
        message = f"{name} is one of {', '.join(allowed)}; {chosen!r} is not"
        raise ApiError(message, parameter=name)
    return chosen


def _filtered(
    held: Sequence[_Held],
    *,
    person: str,
    line: str,
    state: str,
    text: str,
    topic: frozenset[str] | None = None,
) -> list[_Held]:
    """The experiments the filters keep (a filter left blank keeps every experiment). ``topic``
    is the experiments a topic holds: a topic not read holds none, and the response says so."""
    kept = list(held)
    if topic is not None:
        kept = [item for item in kept if item.found.experiment_id in topic]
    if person:
        # The ``person`` filter names an area (the facet's value), whatever its kind.
        kept = [item for item in kept if item.area == person]
    if line:
        kept = [item for item in kept if item.found.theme == line]
    if state:
        kept = [item for item in kept if item.state == state]
    if text:
        needle = text.casefold()
        kept = [
            item
            for item in kept
            if needle in str(item.names["name"]).casefold()
            or needle in item.shown.casefold()
            or needle in item.found.folder.casefold()
        ]
    return kept


def _ordered(held: Sequence[_Held], sort: str, order: str) -> list[_Held]:
    """The experiments in the sort's order.

    An unknown date or share is last in either direction, never first as if it were 0. Ties break
    by root, folder and identifier (the name sort: by shown name, folder and identifier), so the
    same run always lists alike.
    """
    descending = order == "desc"
    base = sorted(held, key=_Held.base_key)
    if sort == "name":
        return sorted(base, key=_Held.name_key, reverse=descending)
    if sort == "files":
        return sorted(base, key=lambda item: item.found.files, reverse=descending)
    if sort == "date":
        known = [item for item in base if item.date is not None]
        unknown = [item for item in base if item.date is None]
        known.sort(key=lambda item: str(item.date), reverse=descending)
        return known + unknown
    # completeness: the share of raw files followed, then the raw files found.
    known = [item for item in base if item.found.share is not None]
    unknown = [item for item in base if item.found.share is None]
    known.sort(
        key=lambda item: (item.found.share or 0.0, item.found.raw_acquisitions), reverse=descending
    )
    return known + unknown


def _row(
    item: _Held,
    stage_buckets: Sequence[tuple[str, str]],
    kind_buckets: Sequence[tuple[str, str]],
    topics: Sequence[ExperimentTopic] = (),
) -> ExperimentRow:
    found = item.found
    tally = item.tally
    names = item.names
    confirmed = item.confirmed
    area, area_kind = item.area, item.area_kind
    if confirmed > found.followed:
        # Never hidden: the two counts disagree, which is a defect, so it is said in the log and
        # the confirmed count is served as counted (assumed cannot be below nothing).
        logging.getLogger(__name__).warning(
            "experiment %s: %d raw files followed through confirmed pairs, %d followed in all",
            found.experiment_id,
            confirmed,
            found.followed,
        )
    assumed = max(0, found.followed - confirmed)
    state_words = STATES[item.state]
    return ExperimentRow(
        id=found.experiment_id,
        folder=found.folder,
        name=str(names["name"]),
        shown_name=item.shown,
        root_id=found.root_id,
        # The area and its kind: the folder at the area level, read with the run's areas.
        # ``person`` is filled only where the area's kind is a person.
        person=found.person if area_kind == "person" else None,
        area=area,
        area_kind=area_kind,
        repeats=item.repeats,
        workflow=item.workflow,
        date=item.date,
        last_file_time=item.last,
        found_by="dated_name" if item.kind == "named" else "structure",
        found_by_kind=item.kind,
        found_by_said=found_by_said(item.kind),
        files=found.files,
        by_stage=[
            ExperimentClassCount(key=key, label=label, files=tally.stages.get(key, 0))
            for key, label in stage_buckets
        ],
        by_kind=[
            ExperimentClassCount(key=key, label=label, files=tally.kinds.get(key, 0))
            for key, label in kind_buckets
        ],
        raw_acquisitions=found.raw_acquisitions,
        analysis_files=found.analysis_files,
        result_tables=found.result_tables,
        derived_files=found.derived_files,
        has_analysis=found.has_analysis,
        state=item.state,
        state_said=state_words,
        state_why=item.state_why,
        no_analysis_found=item.listed,
        followed=ExperimentFollowed(
            raw_found=found.raw_acquisitions,
            followed=found.followed,
            by_confirmed=confirmed,
            assumed=assumed,
            share=found.share,
            said=followed_said(found.raw_acquisitions, found.followed, confirmed, assumed),
            n=found.followed,
            of=found.raw_acquisitions,
        ),
        characterized=ExperimentCharacterized(
            files=found.files,
            stage_read=tally.stage_state.get("read", 0),
            stage_conflicting=tally.stage_state.get("conflicting", 0),
            role_read=tally.role_state.get("read", 0),
            role_conflicting=tally.role_state.get("conflicting", 0),
        ),
        theme=found.theme,
        theme_label=found.theme_label,
        topics=list(topics),
    )


def _goal_matches(context: V1Context, goal_id: str) -> frozenset[str]:
    """The experiments a goal matches (named and associated), read as the Goals route reads it;
    a goal that does not stand matches none."""
    from ingence.api.v1.asking import ask_in_run, run_experiments
    from ingence.goals import read_goals

    goal = next((item for item in read_goals(context.project) if item.id == goal_id), None)
    if goal is None:
        return frozenset()
    with pinned_catalog(context) as (catalog, run_id):
        known = run_experiments(context, catalog, run_id)
        reading = ask_in_run(context, catalog, goal.text, known.asked, at_least=goal.at_least)
    return frozenset(item.experiment.experiment_id for item in reading.experiments)


@route("experiments", model=ExperimentsResponse)
def experiments(context: V1Context, query: Mapping[str, Sequence[str]]) -> ExperimentsResponse:
    """The pinned run's experiments: filtered, sorted and paged, with the counts a row shows."""
    from ingence.api.v1.experiment_files import claim_reading
    from ingence.api.v1.explorer.categories import KIND, kind_families
    from ingence.completeness import confirmed_derivations
    from ingence.completeness.counting import classes as completeness_classes
    from ingence.completeness.elsewhere import run_watermark
    from ingence.completeness.workflow_steps import steps_reached
    from ingence.experiment_units import counted_as, experiment_times, policy_experiments, run_units
    from ingence.grouping.repeats import repeat_groups

    sort = _pick(query, "sort", SORTS, "name")
    order = _pick(query, "order", ORDERS, "asc")
    state = (one(query, "state") or "").strip()
    if state and state not in STATES:
        message = f"state is one of {', '.join(STATES)}; {state!r} is not"
        raise ApiError(message, parameter="state")
    person = (one(query, "person") or "").strip()
    line = (one(query, "line") or "").strip()
    text = (one(query, "q") or "").strip()[:200]
    topic = (one(query, "topic") or "").strip()
    # Two optional filters: ``goal=<goal id>`` keeps a goal's matched experiments (named and
    # associated), and ``ids=`` keeps a comma-separated list of experiment ids.
    goal = (one(query, "goal") or "").strip()
    ids_typed = (one(query, "ids") or "").strip()
    only_ids: frozenset[str] | None = (
        frozenset(part.strip() for part in ids_typed.split(",") if part.strip())
        if ids_typed
        else None
    )
    offset = integer(query, "offset", default=0, low=0, high=10_000_000)
    limit = integer(query, "limit", default=DEFAULT_LIMIT, low=1, high=LIMIT)

    found = pinned_completeness(context)
    classes = completeness_classes()
    with pinned_catalog(context) as (catalog, run_id):
        units = run_units(catalog)
        kinds = {unit: item["kind"] for unit, item in policy_experiments(catalog).items()}
        times = experiment_times(catalog, units)
        tallies = file_tallies(catalog, units, classes, kind_families(), claim_reading(catalog))
        confirmed = confirmed_followed(
            catalog, confirmed_derivations(context.project, run_watermark(catalog)), units, classes
        )
        names = _names(catalog, found.experiments)
        counted = counted_as(catalog)
        groups = repeat_groups(catalog, units)
        steps = steps_reached(catalog, units)
    topics = read_topics(context.project, run_id)
    areas_known = area_kinds_of_run(context.project, run_id)
    area_level = Project.open(context.project).settings.themes.area_level
    listed: dict[str, Literal["listed", "answered"]] = {
        item.experiment.experiment_id: "listed" for item in found.no_analysis.listed
    }
    listed.update(
        {item.experiment.experiment_id: "answered" for item in found.no_analysis.answered}
    )
    held = [
        _Held(
            row,
            names[row.experiment_id],
            kinds.get(row.experiment_id, "named"),
            tallies.get(row.experiment_id, _Tally()),
            confirmed.get(row.experiment_id, 0),
            times.get(row.experiment_id, (row.date, None)),
            listed.get(row.experiment_id),
            found.settings,
            repeats=(
                ExperimentRepeats(
                    n=len(groups[row.experiment_id].members), group=groups[row.experiment_id].group
                )
                if row.experiment_id in groups
                else None
            ),
            workflow=(
                ExperimentWorkflow(
                    id=steps[row.experiment_id].workflow_id,
                    reached=steps[row.experiment_id].reached,
                    expected=list(steps[row.experiment_id].expected),
                )
                if row.experiment_id in steps
                else None
            ),
        )
        for row in found.experiments
    ]
    for item in held:
        name = area_of(item.found.folder, area_level)
        if name is not None:
            item.area = name
            item.area_kind = areas_known.get(area_id(item.found.root_id, name), ("folder", True))[0]
    # A topic that was not read, or that no longer stands (a person deleted or merged it while the
    # page kept the filter), holds nothing here, and ``topic_filter_said`` says why: never an error
    # the page cannot leave, and never the whole list.
    kept = _filtered(
        held,
        person=person,
        line=line,
        state=state,
        text=text,
        topic=(topics.held.get(topic, frozenset()) if topic else None),
    )
    if goal:
        matched = _goal_matches(context, goal)
        kept = [item for item in kept if item.found.experiment_id in matched]
    if only_ids is not None:
        kept = [item for item in kept if item.found.experiment_id in only_ids]
    chosen = next((facet for facet in topics.facets if facet.id == topic), None)
    if not topic:
        filter_said = None
    elif chosen is not None:
        filter_said = f"“{chosen.name}”: {chosen.said}"
    elif topics.read:
        filter_said = (
            "This collection no longer exists in the answer log. "
            "Someone may have deleted it or merged it with another."
        )
    else:
        filter_said = topics.said
    page = _ordered(kept, sort, order)[offset : offset + limit]
    kind_buckets = [(category.key, category.label) for category in KIND.categories]
    # The ``people`` facet lists the areas: the folders at the area level, whatever each stands
    # for. A row's ``person`` is filled only where its area's kind is a person.
    people = Counter(item.area for item in held if item.area)
    line_labels: dict[str, str] = {}
    line_counts: Counter[str] = Counter()
    for item in held:
        if item.found.theme is not None:
            line_counts[item.found.theme] += 1
            line_labels.setdefault(item.found.theme, item.found.theme_label or item.found.theme)
    state_counts: Counter[str] = Counter(item.state for item in held)
    return ExperimentsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        counted_as=counted,
        pairing_assumption=PAIRING_ASSUMPTION,
        summary=ExperimentsSummary(
            experiments=len(held),
            files=sum(item.found.files for item in held),
            files_outside=found.outside.files,
            raw_acquisitions=sum(item.found.raw_acquisitions for item in held),
            followed=sum(item.found.followed for item in held),
            no_analysis_found=len(found.no_analysis.listed),
            judged=found.no_analysis.judged,
        ),
        matching=len(kept),
        offset=offset,
        limit=limit,
        sort=sort,
        order=order,
        rows=[
            _row(
                item,
                STAGE_BUCKETS,
                kind_buckets,
                topics.by_experiment.get(item.found.experiment_id, ()),
            )
            for item in page
        ],
        people=[
            ExperimentFacet(value=name, label=name, experiments=n)
            for name, n in sorted(people.items())
        ],
        lines=[
            ExperimentFacet(value=theme, label=line_labels[theme], experiments=n)
            for theme, n in sorted(
                line_counts.items(), key=lambda pair: (line_labels[pair[0]], pair[0])
            )
        ],
        states=[
            ExperimentFacet(value=key, label=label, experiments=state_counts.get(key, 0))
            for key, label in STATES.items()
        ],
        themes_computed=found.themes_computed,
        themes_said="" if found.themes_computed else themes_not_computed(found.themes_said),
        topics=topics.facets,
        topics_read=topics.read,
        topics_said=topics.said,
        topic_filter_said=filter_said,
    )


def _names(catalog: Catalog, rows: Sequence[ExperimentFound]) -> dict[str, dict[str, object]]:
    """Each experiment's name fields, as the other routes that list experiments serve them."""
    fields = name_fields(catalog, [(row.experiment_id, row.folder) for row in rows])
    return {identifier: dict(held) for identifier, held in fields.items()}
