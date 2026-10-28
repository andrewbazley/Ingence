"""What an area's folder stands for, read from its name.

One predicate, ``area_kind_from_folder_name``, serves the rule of the same name in revision 5.
A folder ``area_level`` levels below its root is read as standing for one of three kinds.

* A date, where the folder's name holds a year or a date.
* An instrument, where a whole word of the name is an instrument's spelling. The spellings are
  the ``instruments`` list of the shipped file-type table. A spelling that the table marks
  ``ambiguous`` counts only beside other words.
* A plain folder, otherwise.

The reading is :func:`ingence.areas.guess_area_kind`'s. The readouts and the ``/api/v1/areas``
route read a name with that one function, so a page before inference and a claim after it say
the same of the same name. A person's answer (``area:<entry_id>``) is read by the routes and
replaces the assumption. The rule itself reads the name alone. A folder-level classifying
predicate is handed the entry and not its children, so the pages read one instrument file type
that dominates a folder (``ingence.areas.area_rows``) where no claim exists yet, and this rule
never does. ``area_level`` is bound from ``themes.area_level`` at inference through the
project-override path (``rulebook.project_rulebook``), so one setting governs both.

The claim is at the abductive tier, the engine's heuristic tier: the best reading of the name, which
a later reading can retract. Its statement says that it assumed. The assumptions page lists each
class of such claims. Every example name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.core.schema import Entry, EntryType, RuleEffect
from ingence.rulebook.registry import Abstention, Concluded, Reads, declare

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ingence.rulebook.registry import Predicate

__all__ = ["AreaKindFromFolderName"]


class AreaKindFromFolderName:
    """Read what a folder at the area level stands for, from its name alone."""

    name = "area_kind_from_folder_name"
    version = "1"

    def __init__(self) -> None:
        self._instruments: frozenset[str] | None = None
        self._ambiguous: frozenset[str] = frozenset()

    def evaluate(self, subject: object, parameters: Mapping[str, object]) -> object:
        """Conclude the kind on a folder at the area level; None elsewhere."""
        from ingence.areas import (
            ambiguous_instrument_spellings,
            guess_area_kind,
            instrument_spellings,
        )

        entry = subject if isinstance(subject, Entry) else getattr(subject, "entry", None)
        if not isinstance(entry, Entry) or entry.entry_type is not EntryType.DIRECTORY:
            return None
        level = int(str(parameters.get("area_level", 1)))
        if entry.depth != level:
            return None
        if self._instruments is None:
            self._instruments = instrument_spellings()
            self._ambiguous = ambiguous_instrument_spellings()
        guess = guess_area_kind(
            entry.name, instruments=self._instruments, ambiguous=self._ambiguous
        )
        return Concluded(values=(guess.kind.value,), inputs=(entry.entry_id,))

    def explain_abstention(self, subject: object, parameters: Mapping[str, object]) -> Abstention:
        """Say that the folder is not at the area level."""
        level = parameters.get("area_level", 1)
        return Abstention(reason=f"not a folder {level} below its root")


@declare(
    "area_kind_from_folder_name",
    version="1",
    summary="Read what an area's folder stands for (a date, an instrument, a folder) from its name",
    effect=RuleEffect.CLASSIFIES,
    reads=Reads.ENTRY,
    parameters=("area_level",),
)
def _build_area_kind_from_folder_name() -> Predicate:
    return AreaKindFromFolderName()
