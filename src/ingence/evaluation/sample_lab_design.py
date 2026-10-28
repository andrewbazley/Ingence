"""The design of each experiment of a sample collection, read from its key files.

The generator knows what each experiment was: its conditions and how many replicates of each it
did. It writes that into ``answer_key_unscored.json`` (section ``designs``), from its own choices
and never from anything the engine found. This module reads the section back, so a program that
measures a design grid has one place to look.

A cell is one condition: a tuple of levels, each level with the factor it belongs to. For
example ``control`` (one factor, treatment), ``nocodazole`` at ``10uM`` (treatment and dose) or
``control`` at ``24h`` (treatment and time). A cell has the number of replicates done and the
number of raw files made for it (none when the acquisitions are gone; a file an archiving
system replaced by a link is still counted).

    >>> designs = read_designs(Path("answer_key_unscored.json"))
    >>> designs[0].cells[0].levels
    (('treatment', 'control'),)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Cell", "Design", "read_designs"]


@dataclass(frozen=True)
class Cell:
    """One condition of an experiment with its replicates."""

    #: ``(("treatment", "nocodazole"), ("dose", "10uM"))``
    levels: tuple[tuple[str, str], ...]
    replicates: int
    raw_files: int

    @property
    def level_names(self) -> tuple[str, ...]:
        """The levels without their factors: ``("nocodazole", "10uM")``."""
        return tuple(level for _, level in self.levels)

    @property
    def factors(self) -> tuple[str, ...]:
        return tuple(factor for factor, _ in self.levels)


@dataclass(frozen=True)
class Design:
    """One experiment's cells."""

    #: The experiment's folder, relative to the pool.
    folder: str
    #: ``complete``, ``partial``, ``abandoned`` or ``analysis_only``.
    outcome: str
    cells: tuple[Cell, ...]

    def factors(self) -> tuple[str, ...]:
        """Every factor the experiment varies, in the order the cells first name them."""
        found: list[str] = []
        for cell in self.cells:
            found.extend(factor for factor in cell.factors if factor not in found)
        return tuple(found)

    def replicates(self) -> int:
        return sum(cell.replicates for cell in self.cells)


def read_designs(unscored: Path | dict[str, Any]) -> list[Design]:
    """The designs in an unscored key file (or in its loaded JSON), sorted by folder."""
    loaded = (
        json.loads(unscored.read_text(encoding="utf-8"))
        if not isinstance(unscored, dict)
        else unscored
    )
    entries = loaded["sections"]["designs"]["entries"]
    designs = [
        Design(
            folder=str(entry["folder"]),
            outcome=str(entry["outcome"]),
            cells=tuple(
                Cell(
                    levels=tuple((str(factor), str(level)) for factor, level in cell["levels"]),
                    replicates=int(cell["replicates"]),
                    raw_files=int(cell["raw_files"]),
                )
                for cell in entry["cells"]
            ),
        )
        for entry in entries
    ]
    return sorted(designs, key=lambda design: design.folder)
