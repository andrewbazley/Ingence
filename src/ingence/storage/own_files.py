"""Which entries are a folder's own files: the filesystem's, never a part from inside a file.

A container file is one file of its folder. A workbook, a word-processing document, a
presentation or any ZIP is one file. The parts a dive recovers from inside it
(``xl/charts/chart1.xml``, ``docProps/core.xml``, a member of an archive) become entries of their
own, marked ``derived``. A rule can then read them as evidence about the document, and an edge can
end at one. They are embedded in their container (``embeds``) and never contained by its folder.
A derived entry's parent is its container. Its ``relative_path`` is the container's own, and every
folder above the container is its ancestor too. A listing that places a file by its path, or
counts a folder's files through the ancestor closure, would count each part as one more file of
the folder, a file with no time of its own.

One definition keeps every listing and count a person reads at the same number. The condition
below is written into each query that lists or counts a folder's files, such as the inventory and a
pool's files by kind. The parts stay in the Index and stay reachable under their document.
"""

from __future__ import annotations

from ingence.core.schema import EntryKind

__all__ = ["filesystem_entry"]


def filesystem_entry(alias: str = "") -> str:
    """The SQL condition that an entry is one the filesystem holds, not a part recovered from
    inside a file.

    The condition is ``e.entry_kind = 'filesystem'`` for the alias ``e``, and unqualified for none.
    """
    column = f"{alias}.entry_kind" if alias else "entry_kind"
    return f"{column} = '{EntryKind.FILESYSTEM.value}'"
