"""Let two files of one folder that share a name identity be ordered by time.

The engine proposes a derivation from a name identity only across an experiment's folders. Two
files of one folder are siblings. A test about what a person answers on a proposed pair, and not
about where pairs are found, keeps its small one-folder pool. It turns ``across_folders_only``
off in its project, as a person's rule change is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import tune

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["allow_pairs_in_one_folder"]


def allow_pairs_in_one_folder(project_path: Path) -> int:
    """Set ``across_folders_only`` off on the identity's two rules, and return the change number."""
    return tune(
        project_path,
        "across_folders_only",
        False,
        rules=("derivation_within_name_identity", "related_within_name_identity"),
    )
