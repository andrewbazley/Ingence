"""``GET /api/v1/overview``: the run in one screen.

What a person sees first: how many experiments, files and areas the run found and the span of
their dates; each area with what its folder stands for; the file types by share; how complete the
experiments are (raw files, raw files with analysis, experiments complete, partial, raw only,
analysis only, not read); how many experiments were repeated; the leading themes; the goals and
how many experiments each matches; how many assumptions and checks wait; and how far the reading
reached: files whose contents were read, files described from a read file of the same series,
and files neither read nor described.

The route reads the stored readout and computes nothing. The readouts step builds the payload once,
after inference (``ingence.readouts.overview_readout``), and keeps it under the run's own Dictionary
fingerprint. A run with no readout (made before the readouts step existed, or by ``index`` alone)
has it built on the first request, once. A few fields stay at their empty values, and the readout
names them (``stubs``). The invented example in :mod:`ingence.api.v1.examples` stays for the tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, AreaKindName, Count, Identifier, register
from ingence.api.v1.shaping import run_required

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = [
    "FileTypeCategory",
    "OverviewArea",
    "OverviewCompleteness",
    "OverviewCounts",
    "OverviewCoverage",
    "OverviewDateRange",
    "OverviewFileType",
    "OverviewGoal",
    "OverviewRepeats",
    "OverviewResponse",
    "OverviewTheme",
]

#: What a file type is, broadly: an instrument's own format, a general data format, a program's
#: file, a container, an archive or a document.
FileTypeCategory = Literal["instrument", "general", "program", "container", "archive", "document"]


class OverviewCounts(ApiModel):
    """The run's three counts."""

    experiments: Count = Field(description="Experiments the run found.")
    files: Count = Field(description="Files the run listed, in every root.")
    areas: Count = Field(description="Areas the run found: the folders at the area level.")


class OverviewDateRange(ApiModel):
    """The span of the run's experiments in time."""

    first: str | None = Field(description="The earliest date, YYYY-MM-DD; null where none.")
    last: str | None = Field(description="The latest date, YYYY-MM-DD; null where none.")
    basis: str = Field(description="What the dates were read from, in words.")


class OverviewArea(ApiModel):
    """One area: a folder at the area level, and what the folder stands for."""

    id: Identifier = Field(description="The area's identifier.")
    name: str = Field(description="The folder's name, as written.")
    kind: AreaKindName = Field(description="What the folder stands for.")
    kind_assumed: bool = Field(description="True while the kind is the engine's own reading.")
    experiments: Count = Field(description="Experiments found in it.")
    files: Count = Field(description="Files found in it.")


class OverviewFileType(ApiModel):
    """One file type and its share of the run's files."""

    type_id: str = Field(description="The type, as a program names it (an extension or a format).")
    label: str = Field(description="The type in words.")
    category: FileTypeCategory = Field(description="What kind of type it is, broadly.")
    files: Count = Field(description="Files of this type.")
    share: float = Field(description="Its share of the run's files, 0 to 1.")


class OverviewCompleteness(ApiModel):
    """How complete the experiments are, as the engine found them."""

    raw_files: Count = Field(description="Raw files found.")
    raw_with_analysis: Count = Field(description="Raw files found followed by an analysis.")
    experiments_complete: Count = Field(description="Experiments with raw files and analysis.")
    experiments_partial: Count = Field(description="Experiments with some raw files followed.")
    experiments_raw_only: Count = Field(description="Experiments with raw files and no analysis.")
    experiments_analysis_only: Count = Field(description="Experiments with analysis and no raw.")
    not_read: Count = Field(description="Files whose contents were not read.")


class OverviewRepeats(ApiModel):
    """How many experiments were done once, and how many again."""

    seen_once: Count = Field(description="Experiments with no repeat.")
    repeated: Count = Field(description="Experiments in a group of repeats.")
    groups: Count = Field(description="Groups of repeats.")


class OverviewTheme(ApiModel):
    """One of the leading themes."""

    id: Identifier = Field(description="The theme's identifier.")
    label: str = Field(description="The theme's words.")
    experiments: Count = Field(description="Experiments in it.")


class OverviewGoal(ApiModel):
    """One goal a person wrote, and how many experiments match it."""

    id: Identifier = Field(description="The goal's identifier.")
    text: str = Field(description="The goal as the person wrote it.")
    matches: Count = Field(description="Experiments matching it, at any tier.")


class OverviewCoverage(ApiModel):
    """How far the reading reached."""

    read: Count = Field(description="Files whose contents were read.")
    represented: Count = Field(description="Files described from a read file of the same series.")
    not_reached: Count = Field(description="Files neither read nor described.")


@register("overview")
class OverviewResponse(ApiModel):
    """``GET /api/v1/overview``: the run in one screen."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered.")
    counts: OverviewCounts = Field(description="Experiments, files and areas.")
    date_range: OverviewDateRange = Field(description="The span of dates, and its basis.")
    areas: list[OverviewArea] = Field(description="Every area, most experiments first.")
    file_types: list[OverviewFileType] = Field(description="File types, largest share first.")
    completeness: OverviewCompleteness = Field(description="How complete the experiments are.")
    repeats: OverviewRepeats = Field(description="Experiments done once, and again.")
    themes_top: list[OverviewTheme] = Field(description="The leading themes, largest first.")
    goals: list[OverviewGoal] = Field(description="The goals, each with its matches.")
    assumptions_count: Count = Field(description="Assumptions the run rests on.")
    checks_count: Count = Field(description="Checks waiting for an answer.")
    coverage: OverviewCoverage = Field(description="How far the reading reached.")


@route("overview", model=OverviewResponse)
def overview(context: V1Context, query: Mapping[str, Sequence[str]]) -> OverviewResponse:
    """The pinned run in one screen, from its stored readout.

    The readout holds the repeats and the coverage.
    """
    del query
    from ingence.readouts import readout, with_area_answers

    run_id = run_required(context)
    stored = readout(context.project, run_id, "overview")
    return OverviewResponse.model_validate(with_area_answers(context.project, stored))


#: The outcomes that say a file's contents were read.
_READ_OUTCOMES = ("extracted", "extracted_with_fallback")


def coverage_of(project: Path, run_id: str) -> OverviewCoverage:
    """How far one run's reading reached.

    The result counts the files read, the files described from a read file of the same series, and
    the files neither read nor described.

    A described file is one left unread because another file of its series was read. The file counts
    as represented by that file, by the series key the file list recorded for both. Every other
    unread file is not reached: a ``metadata_only`` file without a read file of its series, and a
    ``deferred`` file, among them one not reached because its root stopped answering. Files whose
    reading failed, changed under the reading, or were skipped by policy or by the settings are in
    none of the three. They were reached, and the run's quality figures and the checks count them
    under their own names.
    """
    from ingence.storage.catalog import open_catalog

    def counted(catalog: object, statement: str, parameters: list[str]) -> int:
        row = catalog.query(statement, parameters).fetchone()  # type: ignore[attr-defined]
        return int(row[0]) if row else 0

    files_sql = (
        "FROM entries e WHERE e.entry_type = 'file' AND NOT e.is_deleted "
        "AND e.entry_kind = 'filesystem'"
    )
    with open_catalog(project, run_id=run_id) as catalog:
        read = counted(
            catalog,
            f"SELECT count(*) {files_sql} AND e.scan_outcome IN (?, ?)",
            list(_READ_OUTCOMES),
        )
        represented = counted(
            catalog,
            "SELECT count(*) FROM entries e JOIN observations o USING (entry_id) "
            "WHERE e.entry_type = 'file' AND NOT e.is_deleted AND e.entry_kind = 'filesystem' "
            "AND e.scan_outcome = 'metadata_only' AND o.key = 'series.key' "
            "AND o.value_text IN ("
            "  SELECT r.value_text FROM observations r JOIN entries re USING (entry_id) "
            "  WHERE r.key = 'series.key' AND re.scan_outcome IN (?, ?))",
            list(_READ_OUTCOMES),
        )
        unread = counted(
            catalog,
            f"SELECT count(*) {files_sql} AND e.scan_outcome IN ('metadata_only', 'deferred')",
            [],
        )
    return OverviewCoverage(
        read=read, represented=represented, not_reached=max(0, unread - represented)
    )
