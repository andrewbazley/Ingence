"""Tables with the same columns, and tables whose columns contain another's.

Everything here is a projection and never a claim. The package recomputes it from a published
run and the settings. Nothing becomes a Record or an Index row, and no question is raised about
one. The only relation it states is "same columns".
Two tables with the same column names, in the same order and the same delimiter, may come from one
export, from one analysis, or from unrelated work that used one program. This package never says
"same experiment", and no function here feeds an experiment.

The package reads what a dive stored, not the rule's claims: the table-schema digest
(``identity.tabular_schema``), the column count and the column names. So the file page shows its
lines when the rule that groups tables with the same columns is switched off. Where the rule is on,
its classes equal this package's classes exactly (a test holds it).

The component is optional. The engine reaches it only through :data:`COLUMN_INDEXES`. To remove it,
delete this package, its route module's line in ``api/v1/__init__.py`` and its block in the
inspector.

The functions another part of the engine calls:

- :func:`columns_of_file` gives one table's page: the tables with the same columns, the tables
  whose columns contain its own (with the added columns named) and the tables whose columns it
  contains (with the missing columns named);
- :func:`same_column_summary` gives the figures for the quality report: the classes of two or more
  tables, the tables in them, and the classes whose tables lie in more than one folder, each beside
  the number of tables that were counted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ingence.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.columns.stored import ColumnProjection, ColumnSummary, FileColumns
    from ingence.core.settings import ColumnSettings

__all__ = [
    "COLUMN_INDEXES",
    "ColumnIndex",
    "column_engine",
    "columns_of_file",
    "same_column_summary",
]

#: The index registered by default.
DEFAULT_INDEX = "stored_columns"


class ColumnIndex(Protocol):
    """Reads the column names a dive stored and answers questions about them."""

    version: str

    def projection(
        self, project: Path, run_id: str, settings: ColumnSettings | None = None
    ) -> ColumnProjection:
        """The run's projection, built once per run and settings (held in memory)."""
        ...


#: Every column index, reachable only through here.
COLUMN_INDEXES: Registry[ColumnIndex] = Registry("column_index")


class _StoredColumns:
    """The column names, counts and digests the dive stored, indexed by name."""

    version = "1"

    def projection(
        self, project: Path, run_id: str, settings: ColumnSettings | None = None
    ) -> ColumnProjection:
        from ingence.columns.stored import column_projection

        return column_projection(project, run_id, settings)


@COLUMN_INDEXES.register(
    DEFAULT_INDEX,
    version="1",
    summary="Tables with the same columns and tables whose columns contain another's, from the "
    "digests and names a dive stored",
)
def _build_stored_columns() -> ColumnIndex:
    return _StoredColumns()


def column_engine(name: str = DEFAULT_INDEX) -> ColumnIndex:
    """The column index of that name; raises ``ComponentNotAvailableError`` saying why not."""
    return COLUMN_INDEXES.get(name)


def columns_of_file(
    project: Path,
    run_id: str,
    entry_id: str,
    settings: ColumnSettings | None = None,
    limit: int | None = None,
) -> FileColumns:
    """Return one table's columns in a published run, and the other tables they relate it to.

    Raises ``ComponentNotAvailableError`` where the index is disabled, ``FileNotFoundError`` where
    the run is not a published run of the project. A file that is not a table is answered with the
    reason in words (``FileColumns.why_not``), never left out of the answer. ``limit`` is how
    many files each list names, where the caller wants other than the setting's.
    """
    from ingence.columns.stored import file_columns

    built = column_engine().projection(project, run_id, settings)
    return file_columns(built, entry_id, limit)


def same_column_summary(
    project: Path, run_id: str, settings: ColumnSettings | None = None
) -> ColumnSummary:
    """Return the figures for the quality report: classes of tables with the same columns.

    A class is the tables of one run whose stored column digest is the same, each with at least
    ``settings.columns.minimum_columns`` columns. Only a class of two or more tables is counted.
    The result gives the classes, the tables in them, the classes whose tables lie in more than
    one folder, and the denominators: the tables whose columns were read, and the files of the
    run's Index that are delimited tables by their extension. A figure with no denominator is not
    given.
    """
    from ingence.columns.stored import summary_of

    return summary_of(column_engine().projection(project, run_id, settings))
