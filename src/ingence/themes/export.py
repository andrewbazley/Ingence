"""The themes projection as two CSV tables: one row per experiment, one per theme.

The export is written where the person says, and never into a data pool or a run. Lists are
joined by `` · `` (as a label's words are), a year-by-year count is ``year:count`` pairs joined
by ``; ``, and a missing value is an empty cell. Columns are in a fixed order, and rows are in the
projection's order.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingence.core.destinations import (
    Refusal,
    joined,
    refuse_existing,
    refuse_read_only,
    replacing,
    where_refused,
    write_replacing,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "EXPERIMENT_COLUMNS",
    "THEME_COLUMNS",
    "ExportRefusedError",
    "check_destination",
    "table_paths",
    "write_tables",
]

EXPERIMENTS_FILE = "themes_experiments.csv"
THEMES_FILE = "themes.csv"
#: Written beside the tables, so a later export knows the folder is its own to write again.
MARKER_FILE = ".ingence-export"


class ExportRefusedError(ValueError):
    """An export was asked to write where it must not; the message says why, in words."""


def table_paths(directory: Path) -> tuple[Path, Path, Path]:
    """The three files an export writes into ``directory``: its marker and its two tables. Named
    here once, so a command asks about every one before it writes any."""
    return directory / MARKER_FILE, directory / EXPERIMENTS_FILE, directory / THEMES_FILE


def check_destination(
    directory: Path, *, project: Path, roots: Sequence[Path], overwrite: bool = False
) -> None:
    """Refuse a folder inside a declared root (source data is read-only), anywhere inside the
    project but its ``projections/exports``, a file, or a folder holding files an export did not
    write (one that is empty, missing, or marked as an export's is taken). A folder outside the
    project where an export's files already are is refused unless ``overwrite``
    (``core.destinations.refuse_existing``). Every one is asked before any is written.

    The function decides by identity, by the walk that every command that writes where a person
    says shares (:func:`ingence.core.destinations.where_refused`). The walk compares the target's
    nearest existing folder and each folder above it, and the folder it reaches on disk (through
    any symbolic link) and each above that, with each root, the exports folder and the project by
    ``os.path.samefile``. Along each, the first that is one of them decides, and either refusing
    refuses.
    """
    # As typed, and never normalized: the system resolves a ``..`` after a link, not a spelling.
    target = joined(directory)
    if target.exists() and not target.is_dir():
        message = f"refused: {directory} is a file, not a folder"
        raise ExportRefusedError(message)
    # The exports folder is made while the question is asked, so it is compared by identity like
    # every other folder, and removed again where the destination is refused (an export that
    # writes nothing makes nothing). A first export into it is taken.
    refusal = where_refused(directory, project=project, roots=roots)
    if refusal is not None:
        raise ExportRefusedError(_refusal_words(refusal, directory))
    if target.is_dir() and any(target.iterdir()) and not (target / MARKER_FILE).is_file():
        message = (
            f"refused: {directory} holds files an export did not write; "
            "name an empty folder or a new one"
        )
        raise ExportRefusedError(message)
    refuse_existing(table_paths(directory), project=project, overwrite=overwrite)


def _refusal_words(refusal: Refusal, directory: Path) -> str:
    """Why an export into ``directory`` is refused, in the words an export has always used."""
    if refusal.kind == "root":
        return f"refused: {directory} is inside the data root {refusal.root}, never written"
    return (
        f"refused: {directory} is inside the project, whose folders an export never "
        f"writes; name a folder outside it, or one under {refusal.exports}"
    )


EXPERIMENT_COLUMNS = (
    "id",
    "folder",
    "root",
    "date",
    "last_file_time",
    "year",
    "files",
    "bytes",
    "theme",
    "theme_label",
    "broad_theme",
    "broad_label",
    "unplaced_reason",
    "person",
    "collection",
    "words",
)
THEME_COLUMNS = (
    "theme",
    "label",
    "words",
    "senses",
    "broad_theme",
    "broad",
    "experiments",
    "stability",
    "core_experiments",
    "first",
    "peak",
    "last",
    "going",
    "direction",
    "undated",
    "by_year",
    "specific_words",
    "succession",
    "examples",
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "; ".join(f"{key}:{value[key]}" for key in value)
    if isinstance(value, list):
        return " · ".join(
            str(item["word"]) if isinstance(item, dict) and "word" in item else str(item)
            for item in value
        )
    return str(value)


def write_tables(built: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    """Write the two tables into ``directory`` (made if missing); return their paths.

    A file there that is not writable is refused (``ReadOnlyFileError``) before any is written,
    so no export is left half done.
    """
    refuse_read_only(table_paths(directory))
    directory.mkdir(parents=True, exist_ok=True)
    write_replacing(
        directory / MARKER_FILE,
        "Written by ingence export; the next export rewrites its tables here only when given "
        "--overwrite, or without it in the project's own projections/exports.\n",
    )
    written: list[Path] = []
    for name, columns, rows in (
        (EXPERIMENTS_FILE, EXPERIMENT_COLUMNS, built["experiments"]),
        (THEMES_FILE, THEME_COLUMNS, built["themes"]),
    ):
        path = directory / name
        with replacing(path, newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_cell(row.get(column)) for column in columns])
        written.append(path)
    return written[0], written[1]
