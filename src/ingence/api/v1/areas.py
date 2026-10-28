"""``GET /api/v1/areas``: the areas of the project.

An area is a folder at the area level (``themes.area_level``), and its kind is what the folder
stands for: a person, an instrument, a date, a project or a plain folder. The engine reads the kind
from the names and assumes it until a person says otherwise (``POST /api/areas/kind``).

The route answers during a scan. A first run lists the areas before it reads anything, so the
route takes the current run where the pin is not yet set. It refuses only where there is no run at
all.

The route reads the folders at the area level and the files beneath each. The kind comes first from
a person's answer (``area:<entry_id>`` in the answer log), then from the area rule's claim where
the run holds one, and otherwise from the name (``ingence.areas.area_rows``). The invented example
in :mod:`ingence.api.v1.examples` stays for the tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ingence.api.v1 import V1Context, route
from ingence.api.v1.models import ApiModel, AreaKindName, Count, Identifier, register
from ingence.api.v1.shaping import run_required

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["AreaItem", "AreasResponse"]


class AreaItem(ApiModel):
    """One area."""

    id: Identifier = Field(description="The area's identifier.")
    name: str = Field(description="The folder's name, as written.")
    kind: AreaKindName = Field(description="What the folder stands for.")
    kind_assumed: bool = Field(description="True while the kind is the engine's own reading.")
    files: Count = Field(description="Files found in it.")


@register("areas")
class AreasResponse(ApiModel):
    """``GET /api/v1/areas``: every area and its kind."""

    api_version: Literal["1.0"] = "1.0"
    schema_version: str = Field(description="The record contract the engine writes.")
    run_id: Identifier = Field(description="The run answered: the current one during a scan.")
    areas: list[AreaItem] = Field(description="The areas, most files first.")


@route("areas", model=AreasResponse)
def areas(context: V1Context, query: Mapping[str, Sequence[str]]) -> AreasResponse:
    """The areas and their kinds, from the current run during a scan."""
    del query
    from ingence import SCHEMA_VERSION
    from ingence.areas import area_rows
    from ingence.core.project import Project
    from ingence.storage.catalog import open_catalog

    run_id = run_required(context, current_too=True)
    level = Project.open(context.project).settings.themes.area_level
    with open_catalog(context.project, run_id=run_id) as catalog:
        rows = area_rows(catalog, context.project, level)
    return AreasResponse(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        areas=[
            AreaItem(
                id=row.id,
                name=row.name,
                kind=row.kind,
                kind_assumed=row.kind_assumed,
                files=row.files,
            )
            for row in rows
        ],
    )
