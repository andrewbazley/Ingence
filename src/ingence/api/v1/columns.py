"""``GET /api/v1/columns?id=``: a table's columns, and the tables they relate it to.

The group is labeled "same columns", never "same experiment". Tables with the same columns may come
from one export, one analysis, or unrelated work that used one program. This route says only what
the column names say. It gives three lists, each with the whole count beside the files it names:

- ``same_columns``: tables whose column names, order and delimiter are the same;
- ``contained_in``: tables whose columns include this table's, each with the columns it adds
  (an empty list of added columns means the same names written in another order or delimiter);
- ``contains``: tables whose columns this table's include, each with the columns this one adds
  beside them (``missing`` on the file, the columns the other table lacks).

The route is a projection of one published run. It reads after inference and writes nothing. It
reads the digests and names a dive stored, not the rule's claims, so the lines appear when the rule
is switched off. The inclusion lists are empty, with ``inclusion_on`` false, where the setting
``columns.inclusion`` is off. A file that is not a table of the index is answered with the reason in
words (``why_not``), never left blank. Each list names as many files as the setting
``columns.files_listed`` says (20 unless changed). The parameter ``limit=`` asks for more, up to
500, to open them. The count beside each list is always the whole. ``same_columns_in_folder`` and
``same_columns_elsewhere`` count the tables with the same columns that lie in this table's own
folder and elsewhere. ``same_columns_here`` names those in the folder.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from pydantic import Field

from ingence import SCHEMA_VERSION
from ingence.api.v1 import ApiError, V1Context, route
from ingence.api.v1.models import ApiModel, Count, Identifier, register
from ingence.api.v1.shaping import integer, one, run_named
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.columns.stored import Related

__all__ = ["ColumnFile", "ColumnsResponse", "RelatedColumnFile"]

#: What the page reads the group as, and what it does not mean: sent so every client says it alike.
GROUP_LABEL = "same columns"
#: The most files a request may ask each list to name (the setting's default is far fewer).
LIST_LIMIT = 500
NOTES = (
    "Tables with the same columns may come from one export, one analysis or unrelated work that "
    "used one program. These lists compare column names only.",
    "Column names are compared in lower case with extra spaces removed. The kinds of values in "
    "the columns are not compared.",
)


class ColumnFile(ApiModel):
    """A table named in a list."""

    id: Identifier
    path: str
    columns: Count | None


class RelatedColumnFile(ApiModel):
    """A table whose columns contain this one's, or that this one's contain."""

    id: Identifier
    path: str
    columns: Count | None
    #: Columns the other table has and this one lacks, as the other wrote them (container lists).
    added: list[str]
    #: Columns this table has and the other lacks, as this one wrote them (contained lists).
    missing: list[str]
    #: True where the names are the same and only their order or delimiter differs.
    same_names: bool


@register("columns")
class ColumnsResponse(ApiModel):
    """``GET /api/v1/columns?id=``: one table's columns and the tables they relate it to."""

    schema_version: str
    run_id: Identifier
    id: Identifier
    path: str
    #: What the group is called: "same columns".
    group_label: str
    #: Whether the file is a table whose columns were read and reach the floor.
    read: bool
    #: In words, why it is not, where it is not.
    why_not: str | None
    #: The column names as the table wrote them.
    column_names: list[str]
    column_count: Count | None
    delimiter: str | None
    same_columns_total: Count
    #: Of those, how many lie in this table's own folder and how many elsewhere (the whole counts,
    #: whatever ``limit`` says): "same columns as {n} files in this folder and {k} others".
    same_columns_in_folder: Count = 0
    same_columns_elsewhere: Count = 0
    #: The tables with the same columns that lie in this table's own folder, up to the limit: the
    #: files "See the {n}" opens, so the count and what opens are one answer.
    same_columns_here: list[ColumnFile] = Field(default_factory=list)
    same_columns: list[ColumnFile]
    #: Whether the inclusion index is on (the setting ``columns.inclusion``).
    inclusion_on: bool
    contained_in_total: Count
    contained_in: list[RelatedColumnFile]
    contains_total: Count
    contains: list[RelatedColumnFile]
    notes: list[str]


def _file(related: Related) -> ColumnFile:
    return ColumnFile(
        id=related.table.entry_id, path=related.table.path, columns=related.table.count
    )


def _related(related: Related) -> RelatedColumnFile:
    return RelatedColumnFile(
        id=related.table.entry_id,
        path=related.table.path,
        columns=related.table.count,
        added=list(related.added),
        missing=list(related.missing),
        same_names=related.same_names,
    )


@route("columns", model=ColumnsResponse)
def columns_route(context: V1Context, query: Mapping[str, Sequence[str]]) -> ColumnsResponse:
    from ingence.columns import columns_of_file
    from ingence.core.project import Project
    from ingence.core.registry import ComponentNotAvailableError

    identifier = (one(query, "id") or "").strip()
    if not identifier:
        raise ApiError("id names the table", parameter="id")
    run_id, _ = run_named(context, query)
    if run_id is None:
        message = "this project has no published run yet; index it first"
        raise ApiError(message, code="no_published_run", status=HTTPStatus.NOT_FOUND)
    # How many files each list names: the setting's, unless the page asks for more (to open them).
    limit = (
        integer(query, "limit", default=0, low=0, high=LIST_LIMIT) if one(query, "limit") else None
    )
    with open_catalog(context.project, run_id=run_id) as catalog:
        row = catalog.query(
            "SELECT relative_path FROM entries WHERE entry_id = ?", [identifier]
        ).fetchone()
    if row is None:
        message = f"run {run_id} holds no entry {identifier}"
        raise ApiError(message, code="not_found", status=HTTPStatus.NOT_FOUND, parameter="id")
    settings = Project.open(context.project).settings.columns
    try:
        found = columns_of_file(context.project, run_id, identifier, settings, limit)
    except ComponentNotAvailableError as error:
        message = f"the columns index is not available here: {error}"
        raise ApiError(
            message, code="unavailable", status=HTTPStatus.SERVICE_UNAVAILABLE
        ) from error
    return ColumnsResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        id=identifier,
        path=found.path or str(row[0] or ""),
        group_label=GROUP_LABEL,
        read=found.read,
        why_not=found.why_not,
        column_names=list(found.names),
        column_count=found.count,
        delimiter=found.delimiter,
        same_columns_total=found.same_columns_total,
        same_columns_in_folder=found.same_columns_in_folder,
        same_columns_elsewhere=found.same_columns_elsewhere,
        same_columns_here=[_file(item) for item in found.same_columns_here],
        same_columns=[_file(item) for item in found.same_columns],
        inclusion_on=found.inclusion_on,
        contained_in_total=found.contained_in_total,
        contained_in=[_related(item) for item in found.contained_in],
        contains_total=found.contains_total,
        contains=[_related(item) for item in found.contains],
        notes=list(NOTES),
    )
