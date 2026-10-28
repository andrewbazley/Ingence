"""The dates a file draws from the folders above it.

A file's date takes in the dates in the names above it. A folder's name may hold the experiment's
date. A date in a name is descriptive and not exact. So each entry is handed the dates of the
nearest folder above it whose own name holds one, with that folder's path. The rule that reads them
(``name_date_neighbourhood``) says which it uses. The work is linear in the pool: each folder's own
dates are read once, and each entry's nearest dated folder is found by a walk up its parents,
remembered per folder.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ingence.core.schema import EntryType
from ingence.infer.subjects import name_dates

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ingence.infer.subjects import EntryRecords

__all__ = ["with_folder_dates"]


def with_folder_dates(records: Sequence[EntryRecords]) -> tuple[EntryRecords, ...]:
    """Hand every entry the dates of the nearest folder above it whose name holds one."""
    folders: dict[str, EntryRecords] = {
        held.entry.entry_id: held
        for held in records
        if held.entry.entry_type is EntryType.DIRECTORY
    }
    own = {entry_id: name_dates(held.components) for entry_id, held in folders.items()}
    nearest: dict[str | None, tuple[tuple[str, ...], str | None]] = {None: ((), None)}

    def above(folder: str | None) -> tuple[tuple[str, ...], str | None]:
        walked: list[str | None] = []
        cursor = folder
        found: tuple[tuple[str, ...], str | None] = ((), None)
        while cursor is not None:
            if cursor in nearest:
                found = nearest[cursor]
                break
            walked.append(cursor)
            held = folders.get(cursor)
            if held is None:
                break
            if own.get(cursor):
                found = (own[cursor], held.entry.relative_path)
                break
            cursor = held.entry.parent_entry_id
        for step in walked:
            nearest[step] = found
        return found

    result = []
    for held in records:
        dates, folder = above(held.entry.parent_entry_id)
        result.append(replace(held, folder_dates=dates, dated_folder=folder) if dates else held)
    return tuple(result)
