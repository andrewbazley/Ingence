"""Comparing two censuses of what may be the same files: moved, copied, or reorganised.

The History page's notice and the folder page's one line read this package
(``api/routes/reorganisation.py``). Nothing here writes into a run, a project or the Record.

The package answers the question: if someone scans a filesystem, and then everything is jumbled
and moved around, what were the differences? The answer guards against misreading as new the data
that was analysed before. It is a flag ("we noticed this"), never a roadblock. The package answers
in the order it computes:

1. Which files are the same files (``fingerprints``, ``match``). An entry's identity is its root
   and its path (``core/ids.entry_id``), so a moved file is a new entry and a scan from another
   project is a different root altogether. Sameness is read from the file itself. The first test is
   the same place with the same size and time. After that come a digest of its bytes where a dive
   read one, then its name, size and modification time, then its size and time alone. Only where a
   side states no sizes at all do two more tests follow: its place alone, then its folder's name and
   its own. Both make a weaker kind of match. The report says so, and nothing is carried across it.
2. How the organisation differs (``structure``). Over the matched files, it counts how many kept
   their path, and whether one prefix rewrite explains the moves. It also counts how many pairs of
   files that shared a folder still do, and how many pairs sharing a folder now shared one before.
   That last measure answers the question.
3. What it means (``verdict``): a verdict in plain words with every cut shown beside it, and how
   the files look to have got here (the same tree again, another computer, a copy).
4. What follows a moved file (``moves``, ``carry``). A person's decisions are pinned to folders and
   files, and a moved folder leaves them pointing at a place that is gone. ``carry`` finds where
   the files under such a folder went, so the notice can say so and the folder's own page can say
   so in one line, while the difference stands. It reads and proposes. It writes nothing, and it
   never asks a person to repair anything.
5. What a person reads (``report``): one plain-dict function, :func:`report.compare_organisation`.
"""

from __future__ import annotations

__all__ = ["COMPARE_VERSION"]

COMPARE_VERSION = "3"
