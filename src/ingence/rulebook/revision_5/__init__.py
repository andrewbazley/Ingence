"""Revision 5 of the shipped Rulebook: the 0.4.0 engine's rules.

Rules here supersede earlier revisions and never edit them, as revision 4's header says. A rule
of revisions 0 to 4 whose reading or defeat conditions change is superseded and declared again
under a name of its own. So the superseded sentence stays readable beside the one in force, and
every past run still replays.

The revision is a package with one module for each topic. Each module exports ``RULES`` and
``CHANGES``. This module assembles them into one dict shaped exactly like
:data:`ingence.rulebook.revision_4.REVISION`:

- :mod:`~ingence.rulebook.revision_5.reading`: what a content pass reads and when it gives up;
- :mod:`~ingence.rulebook.revision_5.file_types`: file types and the programs that write them;
- :mod:`~ingence.rulebook.revision_5.names`: names and spellings;
- :mod:`~ingence.rulebook.revision_5.workflows`: workflows and stages;
- :mod:`~ingence.rulebook.revision_5.areas`: what an area's folder stands for;
- :mod:`~ingence.rulebook.revision_5.readouts`: the readouts that pages count from.

:func:`ingence.rulebook.load.load_revisions` loads the revision after revision 4.
"""

from __future__ import annotations

from ingence.rulebook.revision_5 import areas, file_types, names, reading, readouts, workflows

__all__ = ["REVISION"]

#: The modules of the revision, in the order their rules and changes are declared.
_TRACKS = (reading, file_types, names, workflows, areas, readouts)

REVISION: dict[str, object] = {
    "revision": 5,
    "summary": (
        "The 0.4.0 engine's rules, one module per track: what a content pass reads, file types "
        "and their writers, names and spellings, workflows and stages, areas and readouts. "
        "Supersedes what it changes and edits nothing earlier."
    ),
    "changes": [change for track in _TRACKS for change in track.CHANGES],
    "rules": [rule for track in _TRACKS for rule in track.RULES],
}
