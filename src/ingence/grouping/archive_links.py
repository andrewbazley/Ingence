"""Find which real file an archive's link stands for, from names and link text alone.

Archives hold many copies and backups, and many of them are only an archive link. A link of a copied
file counts as a copy of the file, though it appears elsewhere than the file's perceived original
location.

A link that an archive left (its text names another server's share, the census's
``archive_link``) is matched to the real files in the pool of its name, case folded. The name is
the link's own name or the last part of its text. Where the link's text names folders, they decide.
The match compares the folders that the link's path names just above the file with each
candidate's, from the nearest up. It takes the candidate that agrees on the most folders, where that
candidate agrees on at least one and no other candidate agrees on as many (basis ``name and path``).
No agreement, or a tie, matches nothing. Only a link whose text names no folder is matched on its
name alone, where one real file has that name (basis ``name``). Nothing is opened: neither the
link, which is never followed, nor the file.

A link states no size of the file it stands for, but a listing may. Where the listing declares that
size (``declared.size.target``, its source the archive), a real file of another known size is not
the link's copy. A match whose size agrees says so (basis ``name, path and declared size``, or
``name and declared size``) and cites the statement. A candidate whose own size is unknown is
neither refused nor vouched for by the statement. A refusal is said. A link that the declared size
left matching no file carries the reason, naming the size and citing the statement, and the rule's
abstention reads it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING

from ingence.core.links import is_network_share
from ingence.core.schema import EntryType, SymlinkState

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ingence.core.schema import Observation
    from ingence.infer.subjects import EntryRecords
    from ingence.storage.catalog import Catalog

__all__ = [
    "NAME",
    "NAME_AND_PATH",
    "NAME_AND_SIZE",
    "NAME_PATH_AND_SIZE",
    "declared_target_sizes",
    "with_archived_copies",
]

NAME = "name"
NAME_AND_PATH = "name and path"
NAME_AND_SIZE = "name and declared size"
NAME_PATH_AND_SIZE = "name, path and declared size"

_SEPARATORS = re.compile(r"[\\/]+")


def _is_archive_link(held: EntryRecords) -> bool:
    entry = held.entry
    return (
        entry.symlink_state is SymlinkState.OUTSIDE_ROOT
        and bool(entry.symlink_target)
        and is_network_share(str(entry.symlink_target))
    )


def _agreement(link_folders: Sequence[str], path: str) -> int:
    """How many folders, from the nearest up, the link's path and a file's path share."""
    folders = [part.casefold() for part in path.split("/")[:-1]]
    count = 0
    for mine, theirs in zip(reversed(link_folders), reversed(folders), strict=False):
        if mine != theirs:
            break
        count += 1
    return count


def declared_target_sizes(catalog: Catalog) -> dict[str, Observation]:
    """Every size a listing declares for the file a listed link stands for, by the link's entry."""
    from ingence.core.schema import Observation
    from ingence.listing.ingest import TARGET_SIZE_KEY
    from ingence.storage.index_store import record_from_row

    cursor = catalog.query(
        "SELECT * FROM observations WHERE key = ? AND value_integer IS NOT NULL "
        "ORDER BY observation_id",
        [TARGET_SIZE_KEY],
    )
    columns = [column[0] for column in cursor.description or ()]
    found: dict[str, Observation] = {}
    for row in cursor.fetchall():
        observation = record_from_row(dict(zip(columns, row, strict=True)), Observation)
        found.setdefault(observation.entry_id, observation)
    return found


def with_archived_copies(
    records: Sequence[EntryRecords], *, declared_sizes: Mapping[str, Observation] | None = None
) -> tuple[EntryRecords, ...]:
    """Mark each archive link with the real file it stands for, and that file with itself.

    ``declared_sizes`` holds, by a link's entry, the size its listing declares for the file behind
    it (:func:`declared_target_sizes`).
    """
    by_name: dict[str, list[EntryRecords]] = defaultdict(list)
    links: list[EntryRecords] = []
    for held in records:
        entry = held.entry
        if entry.entry_type is EntryType.FILE and entry.symlink_state in (
            None,
            SymlinkState.NOT_A_LINK,
        ):
            by_name[entry.name.casefold()].append(held)
        elif _is_archive_link(held):
            links.append(held)
    if not links:
        return tuple(records)
    matched: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    refused: dict[str, tuple[str, tuple[str, ...]]] = {}
    sizes = declared_sizes or {}
    for link in links:
        parts = [part for part in _SEPARATORS.split(str(link.entry.symlink_target)) if part]
        names = {link.entry.name.casefold()}
        if parts:
            names.add(parts[-1].casefold())
        candidates = sorted(
            {
                held.entry.entry_id: held for name in names for held in by_name.get(name, ())
            }.values(),
            key=lambda held: held.entry.relative_path,
        )
        said = sizes.get(link.entry.entry_id)
        if said is not None:
            # A real file of another known size is not the file the listing says the link is for.
            kept = [
                held
                for held in candidates
                if held.entry.size_bytes is None or held.entry.size_bytes == said.value_integer
            ]
            if len(kept) < len(candidates):
                others = len(candidates) - len(kept)
                refused[link.entry.entry_id] = (
                    f"its listing declares the file behind it as {said.value_integer} bytes, and "
                    f"{others} file{'' if others == 1 else 's'} of its name "
                    f"{'holds' if others == 1 else 'hold'} another size",
                    (said.observation_id,),
                )
            candidates = kept
        if not candidates:
            continue
        folders = [part.casefold() for part in parts[:-1]]
        scored = sorted(
            ((_agreement(folders, held.entry.relative_path), held) for held in candidates),
            key=lambda pair: (-pair[0], pair[1].entry.relative_path),
        )
        if not folders and len(candidates) == 1:
            found, basis = candidates[0], NAME
        else:
            # Where the link's text names folders, one must agree. A name alone is not the
            # same file where the archive says it came from another folder.
            if scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
                continue
            found, basis = scored[0][1], NAME_AND_PATH
        evidence: tuple[str, ...] = ()
        if said is not None and found.entry.size_bytes == said.value_integer:
            basis = NAME_AND_SIZE if basis == NAME else NAME_PATH_AND_SIZE
            evidence = (said.observation_id,)
        matched[link.entry.entry_id] = (found.entry.entry_id, basis, evidence)
        matched.setdefault(found.entry.entry_id, (found.entry.entry_id, basis, evidence))
        refused.pop(link.entry.entry_id, None)
    records = tuple(
        replace(
            held,
            archived_copy_refused=refused[held.entry.entry_id][0],
            archived_copy_evidence=refused[held.entry.entry_id][1],
        )
        if held.entry.entry_id in refused
        else held
        for held in records
    )
    return tuple(
        replace(
            held,
            archived_copy_of=matched[held.entry.entry_id][0],
            archived_copy_basis=matched[held.entry.entry_id][1],
            archived_copy_evidence=matched[held.entry.entry_id][2],
        )
        if held.entry.entry_id in matched
        else held
        for held in records
    )
