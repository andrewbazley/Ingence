"""An experiment folder's name read into parts, and shown without its dates.

The page does not parse a name. No number or word on a page is computed anywhere the engine did
not compute it. The census read every folder's name with the tokenizer and recorded where each
part stood (``name_shapes``) and what it was (``components``). This module reads those records,
as the design grid reads a file's (:func:`ingence.design.read.name_parts_of`), and nothing else:

- A part is a date where the tokenizer read it as one (``20260304``, ``2026-03-04``,
  ``04Mar2026``, a number glued to a month's word), within the years the run read dates in. A
  number that the tokenizer did not read as a date stays a number, wherever it stands.
- The shown name is built from the parts that are left and the separators the tokenizer recorded
  between them. Where two parts stood glued together (``zibKelt``) they stay glued. Anywhere
  else, or where a date stood between them, one space joins them. A dot, a bracket or a hyphen
  that the name wrote around a part never reaches it. The shown name is what a row says. The full
  name is always one glance away.

Where the run recorded no layout for a name (``dictionary.record_name_shapes`` off, or a name read
before layouts were kept) there are no parts, the shown name is the name as written, and
``read`` says so: unexamined, never a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ingence.design.read import name_parts_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.storage.catalog import Catalog

__all__ = ["ExperimentName", "NamePiece", "experiment_names", "shown_name"]

#: The kinds of part a shown name leaves out.
HIDDEN_KINDS = frozenset({"date"})


@dataclass(frozen=True, slots=True)
class NamePiece:
    """One part of a name, as the tokenizer read it."""

    #: The part as the name writes it.
    text: str
    #: ``word``, ``date``, ``clock``, ``number``, ``quantity``, ``version``, ``indexed``,
    #: ``identifier``: the tokenizer's kind, with a number inside a date or a clock read as that.
    kind: str
    #: The kind of the sense the Dictionary bound the part to (``reagent``, ``technique``), or None.
    sense: str | None
    #: Whether the shown name leaves it out.
    hidden: bool


@dataclass(frozen=True, slots=True)
class ExperimentName:
    """An experiment folder's name, its parts, and the name shown on a row."""

    #: The folder's own name, as written.
    name: str
    #: The name without its dates. The name itself where nothing is left or no layout was kept.
    shown: str
    parts: tuple[NamePiece, ...]
    #: Whether the run recorded a layout for this name. False: no parts, shown is the name.
    read: bool


def shown_name(name: str, pieces: Sequence[tuple[str, str, bool]]) -> str:
    """The name a row shows, from the parts of ``name`` in order.

    Each piece is ``(separator, text, hidden)``: the separator the tokenizer recorded before the
    part (empty for a part glued to the one before it), the part as written, and whether it is
    left out. Kept parts that stood glued together stay glued. Any other two kept parts, and two
    with a hidden part between, are joined by one space. The name as written where nothing is
    kept (or there is nothing to build from).
    """
    shown = ""
    joined = False
    for separator, text, hidden in pieces:
        if hidden:
            joined = False
            continue
        if shown:
            shown += "" if joined and not separator else " "
        shown += text
        joined = True
    return shown or name


def experiment_names(catalog: Catalog, ids: Sequence[str]) -> dict[str, ExperimentName]:
    """The name of each folder in ``ids``, read from the run's recorded layouts.

    Three reads for the whole list (names, layouts, components), however long it is. An
    identifier the run does not hold is left out of the answer.
    """
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    names = {
        str(entry): str(name)
        for entry, name in catalog.query(
            "SELECT entry_id, name FROM entries WHERE entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [wanted],
        ).fetchall()
    }
    layouts = {
        str(entry): json.loads(str(slots))
        for entry, slots in catalog.query(
            "SELECT entry_id, slots_json FROM name_shapes WHERE source_scope = 'name' "
            "AND entry_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY entry_id",
            [wanted],
        ).fetchall()
        if slots
    }
    components = {
        str(row[0]): row[1:]
        for row in catalog.query(
            "SELECT component_id, component_type, normalized_value, raw_value, magnitude, "
            "dimension FROM components WHERE source_scope = 'name' "
            "AND entry_id IN (SELECT unnest(?::VARCHAR[]))",
            [wanted],
        ).fetchall()
    }
    found: dict[str, ExperimentName] = {}
    for entry, name in names.items():
        slots = layouts.get(entry)
        parts = name_parts_of(slots, components, name) if slots else ()
        if not slots or not parts:
            found[entry] = ExperimentName(name=name, shown=name, parts=(), read=False)
            continue
        ordered = sorted(slots, key=lambda item: int(str(item["ordinal"])))
        pieces: list[NamePiece] = []
        built: list[tuple[str, str, bool]] = []
        for slot, part in zip(ordered, parts, strict=True):
            hidden = part.kind in HIDDEN_KINDS
            pieces.append(
                NamePiece(text=part.shown, kind=part.kind, sense=part.sense, hidden=hidden)
            )
            built.append((str(slot.get("separator") or ""), part.shown, hidden))
        found[entry] = ExperimentName(
            name=name, shown=shown_name(name, built), parts=tuple(pieces), read=True
        )
    return found
