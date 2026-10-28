"""Which target a person's answer to a proposal's question is written to.

A question that these rules raise is answered on the proposal's own target, the one the engine reads
back. A verdict written to ``question:<rule>:<value>`` would be read by nothing.

| Question (predicate) | Answered on |
|---|---|
| ``experiments_may_belong_together`` | ``group:<core terms>``, named in its reason |
| ``group_resolution_deviation`` | ``group:<the set its lattice question names>``, or |
| | ``group:<the core with the word, or without it>`` (a deviation question) |
| ``miscellaneous_files_to_approve`` | ``miscellaneous:<the folder's entry id>`` |
| ``software_outside_the_catalogue`` | ``program:<the folder's entry id>`` |
| ``domain_pack_would_read`` | ``pack:<domain>`` |

The event's payload names the question (``question``) and the word (``verdict``), so a page marks
the question's row answered.

Every other question keeps its own target. What each answer does:

- A confirm on a group's core resolves every experiment that holds the core. A deviation's confirm
  approves the core with the word added. For a word that is missing, it approves the smaller core
  without the word, so the experiment that lacks the word is resolved under a group of its own.
- A confirm on a folder keeps it as miscellaneous files, or puts it in program scope.
- A confirm on a pack loads it.
- A reject says no. Each answer stops its question.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ingence.grouping.resolutions import core_confirming, lattice_terms
from ingence.record.targets import group_target, miscellaneous_target, pack_target, program_target

if TYPE_CHECKING:
    from ingence.storage.catalog import Catalog

__all__ = ["CORE_MARK", "proposal_target"]

#: How a proposed group's reason names its core terms.
CORE_MARK = "core terms: "
#: A pack proposed ("load the ... pack") or, under every pack, one asked about ("keep the ...").
_PACK = re.compile(r"(?:load|keep) the (.+) pack")


def _raised_folder(catalog: Catalog, rule_id: str, raised: str) -> str | None:
    """The one folder a per-folder question was raised on, from its finding's scope."""
    rows = catalog.query(
        "SELECT DISTINCT subject FROM findings WHERE rule_id = ? AND list_contains(raised, ?)",
        [rule_id, raised],
    ).fetchall()
    folders = {str(subject).partition(":")[2] for (subject,) in rows if subject}
    return next(iter(folders)) if len(folders) == 1 else None


def proposal_target(
    catalog: Catalog, predicate: str | None, rule_id: str, raised: str, reason: str
) -> str | None:
    """The target an answer to this question is written to, or None to keep its own."""
    if predicate == "experiments_may_belong_together":
        core = reason.rsplit(CORE_MARK, 1)[1] if CORE_MARK in reason else ""
        return group_target(core.split()) if core.strip() else None
    if predicate == "group_resolution_deviation":
        # A lattice question names its whole set. A deviation question names its core and one word.
        confirming = lattice_terms(raised) or core_confirming(raised)
        return group_target(confirming) if confirming else None
    if predicate == "domain_pack_would_read":
        found = _PACK.fullmatch(raised)
        return pack_target(found.group(1)) if found else None
    if predicate in ("miscellaneous_files_to_approve", "software_outside_the_catalogue"):
        folder = _raised_folder(catalog, rule_id, raised)
        if folder is None:
            return None
        if predicate == "miscellaneous_files_to_approve":
            return miscellaneous_target(folder)
        return program_target(folder)
    return None
