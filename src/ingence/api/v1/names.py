"""An experiment's name as the engine read it, for the routes that list experiments.

``name``, ``shown_name``, ``name_parts`` and ``name_read`` are served beside an experiment's
folder by ``/api/v1/themes`` and ``/api/v1/completeness``, from the layouts the census recorded
for each name (:mod:`ingence.experiment_names`). A page shows ``shown_name`` and never reads a
name itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from ingence.api.v1.models import ApiModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.storage.catalog import Catalog

__all__ = ["NamePartItem", "name_fields"]


class NamePartItem(ApiModel):
    """One part of an experiment folder's name, as the tokenizer read it."""

    text: str = Field(description="The part as the name writes it.")
    kind: str = Field(
        description=(
            "What the tokenizer read it as: word, date, clock, number, quantity, version, "
            "indexed or identifier. A number inside a date or a clock is read as that."
        )
    )
    sense: str | None = Field(
        description=(
            "The kind of the sense the Dictionary bound the part to (reagent, technique and so "
            "on), or null where it bound none."
        )
    )
    hidden: bool = Field(description="Whether the shown name leaves the part out (a date).")


def name_fields(catalog: Catalog, folders: Sequence[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """For each ``(identifier, folder path)``, the four name fields of its response row.

    The folder's own name is the last piece of its path; where the run holds no entry under the
    identifier, that last piece stands in as the name, with no parts and ``name_read`` false.
    """
    from ingence.experiment_names import experiment_names

    read = experiment_names(catalog, [identifier for identifier, _ in folders])
    fields: dict[str, dict[str, Any]] = {}
    for identifier, folder in folders:
        held = read.get(identifier)
        if held is None:
            last = folder.rsplit("/", 1)[-1]
            fields[identifier] = {
                "name": last,
                "shown_name": last,
                "name_parts": [],
                "name_read": False,
            }
            continue
        fields[identifier] = {
            "name": held.name,
            "shown_name": held.shown,
            "name_parts": [
                NamePartItem(text=item.text, kind=item.kind, sense=item.sense, hidden=item.hidden)
                for item in held.parts
            ],
            "name_read": held.read,
        }
    return fields
