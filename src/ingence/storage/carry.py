"""Carry a run's tables, and what describes them, into the next run.

Three passes publish a run over the one before it: inference, a dive, and the fetch of a listing
root. Each rewrites a few tables and carries every other one as the current run holds it. The
carried tables include the claims, the findings and the rules. They do not state their own meaning.
The words the groups were made of (``groups.words_as``) are a file of the run's directory
(:data:`~ingence.grouping.lattice_words.WORDS_AS_FILE`). A pass that copied the tables and not the
file would publish claims that no longer say what they were made of. So one function does the
copying, and a pass that carries the tables carries what describes them.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from ingence.grouping.lattice_words import carry_reading
from ingence.storage.layout import TABLES, owned_path

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

__all__ = ["carry_run"]


def carry_run(previous: Path, staging: Path, *, rewritten: Collection[str]) -> None:
    """Copy every table of ``previous`` that the pass does not write afresh into ``staging``.

    The function also copies what the run says of how its claims were made. A table that spans runs
    (the Record) is not a run's, and a table the run does not hold is skipped. The copies are
    byte-identical, so a comparison across the pass reports only what it changed. Each folder
    copied into must be the staging's own and no link (``storage.layout.owned_path``), because a
    copy into an existing folder writes through a link.
    """
    for spec in TABLES:
        if spec.spans_runs or spec.name in rewritten:
            continue
        source = previous / spec.directory
        if source.is_dir():
            shutil.copytree(source, owned_path(staging, spec.directory), dirs_exist_ok=True)
    carry_reading(previous, staging)
    # The workflows the run suggested and applied are carried on the same terms.
    from ingence.infer.workflow_suggest import carry_workflows

    carry_workflows(previous, staging)
