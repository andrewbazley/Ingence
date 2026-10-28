"""Which character separates the fields of a delimited table, decided in one place.

The table reader (``extractors/tabular.py``) and the column-name digest
(``identity/structure.py``) both read a table's first line. Both ask this function, so a file
with a comma in a column name has one delimiter and one column count.

The rule is plain and deterministic. A file named ``.tsv`` or ``.tab`` is tab-delimited.
Otherwise the first line decides: the comma, the tab or the semicolon that occurs most often
outside double quotes. On a tie the comma wins, then the tab, so a line with none of them
reads as comma-delimited.

The module imports nothing from the engine, because the table reader runs in a child
process that pays for every import.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

__all__ = ["CANDIDATE_DELIMITERS", "TAB_EXTENSIONS", "detect_delimiter", "normalise_column"]

#: Extensions that name a tab-delimited table.
TAB_EXTENSIONS = frozenset({".tsv", ".tab"})

#: The delimiters a first line is read for, in the order a tie is settled.
CANDIDATE_DELIMITERS = (",", "\t", ";")

_QUOTED = re.compile(r'"[^"]*"')


def detect_delimiter(first_line: str, extensions: Collection[str]) -> str:
    """Return the delimiter of a table from its extensions and first line."""
    if any(extension in TAB_EXTENSIONS for extension in extensions):
        return "\t"
    unquoted = _QUOTED.sub("", first_line)
    return max(CANDIDATE_DELIMITERS, key=lambda candidate: unquoted.count(candidate))


def normalise_column(name: str) -> str:
    """Return a column name in the form two exports of one table would share.

    Case is folded and white space around and inside the name collapsed, because both vary
    between exports of one table. Nothing else changes: a renamed column is a different column.
    """
    return " ".join(name.strip().casefold().split())
