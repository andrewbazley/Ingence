"""A listing into Index entries and declared observations, for the census's replay seam.

The seam already exists. ``scan/census.replay_root`` is the census's own walker fed stored
``Entry`` records in place of ``scandir`` and ``stat`` (the rebind uses it). A listing is the same
shape -- rows describing files, no filesystem beneath -- so :func:`entries_from_listing` builds the
entries a listing states (with the folders its paths imply, since a listing names files only) and
the census replays them: every name is read, bound, shaped and characterised by the functions a walk
calls, and the run is published by the same commit.

Every fact a listing does not state is absent: no clock, mode, inode, device or link count, and a
size only where one was declared. The entry's kind is stated, not read off a mode.

What a listing does state is declared, never observed (``SourceScope.DECLARED``), one observation
per statement, the observer the listing's archive (``listing.<archive>``):

- no ``declared.size``: a declared size is the entry's ``size_bytes``, the one filesystem fact a
  listing carries, and the size every rule and the sampled digest read;
- ``declared.attribute.<name>``: each attribute, its name folded to lower case with runs of
  anything but letters and digits made one ``_`` (``Genotype/Line`` -> ``genotype_line``);
- ``declared.clock.<meaning>``: each time, with the field that said it. A clock is never an entry
  column, because an archive's write or an upload is a copy's arrival and not the file's
  modification, and a rule reads only a clock whose meaning it understands;
- ``declared.digest.<method>``: each digest of the whole content the archive states;
- ``declared.link.<relation>``: each link, only where ``listing.declared_links`` is
  ``recorded``. Links are withheld by default, because a declared link is the answer key a scored
  run is judged against. A link whose target is a placeholder is refused (``-``, ``n/a``,
  ``none``, ``null``, blank, from any converter), and the census counts them on the run record
  (:func:`placeholder_links`);
- ``declared.size.target``: the size a listing declares for the file a listed link stands for,
  with the field that said it. The link is the entry, a link with its text and no size of its own
  unless one is stated, so the file's size is never its column (as for a declared clock). A
  comparison or a copy rule reads it here, its source the archive's.
- ``declared.address.broken``: what the file's address answered, where it answered no file: its
  state (not found, moved, malformed), the status, when, the reading that found it and the address
  itself. The file stays an entry with everything else its listing states, and nothing is
  concluded from the statement except that its address answered so. A broken address says nothing
  about whether the file exists elsewhere. A remote reading answered *not found* or *moved*
  records the same key, its observer the reading's
  (:func:`ingence.listing.remote.publish_remote_readings`), so one rule reads both.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ingence.core.ids import CaseSensitivity, canonical_json, entry_id, observation_id
from ingence.core.outcomes import SourceScope
from ingence.core.schema import Entry, EntryType, Observation, SymlinkState, ValueKind
from ingence.core.tiers import ExtractionTier
from ingence.listing.format import folders_of, is_placeholder

if TYPE_CHECKING:
    from ingence.core.project import RootDeclaration
    from ingence.listing.format import Listing

__all__ = [
    "BROKEN_ADDRESS_KEY",
    "DECLARED_PREFIX",
    "TARGET_SIZE_KEY",
    "declared_key",
    "declared_observations",
    "entries_from_listing",
    "observer_of",
    "placeholder_links",
]

#: Every key a listing's statement is recorded under begins so.
DECLARED_PREFIX = "declared."
#: Bumped whenever a listing's statements would be recorded differently. Not for a placeholder
#: link refused: no observation of one changes, the refused ones are not written.
DECLARED_VERSION = "1"
#: The size a listing declares for the file a listed link stands for.
TARGET_SIZE_KEY = f"{DECLARED_PREFIX}size.target"
#: What a listed file's address answered, where it answered no file: stated by the
#: listing, or answered to a remote reading. Either way the archive's word, so declared.
BROKEN_ADDRESS_KEY = f"{DECLARED_PREFIX}address.broken"
_NOT_A_WORD = re.compile(r"[^0-9a-z]+")


def observer_of(archive: str) -> str:
    """The observer a listing's declared observations name: its archive."""
    return f"listing.{archive}"


def declared_key(kind: str, name: str) -> str:
    """``declared.<kind>.<name>``, the name folded to lower case and joined by ``_``."""
    folded = _NOT_A_WORD.sub("_", name.casefold()).strip("_") or "unnamed"
    return f"{DECLARED_PREFIX}{kind}.{folded}"


def _relative_depth(relative: str) -> int:
    return 0 if relative == "." else relative.count("/") + 1


def entries_from_listing(
    listing: Listing, declaration: RootDeclaration, *, run_id: str
) -> list[Entry]:
    """The root's entry, every folder the listing's paths imply, and every listed file.

    In canonical path order, parents first, the root's own entry at ``.`` as a walk writes it. The
    identifiers are the ones a walk of the same tree under the same root would give, and a listing
    fetched again keeps them.
    """
    root = declaration.root_id
    case = declaration.case_sensitivity or CaseSensitivity.SENSITIVE

    def identifier(relative: str) -> str:
        return entry_id(root, relative, (), case_sensitivity=case)

    def listed(
        relative: str,
        kind: EntryType,
        size: int | None,
        parent: str | None,
        name: str,
        link: str | None = None,
    ) -> Entry:
        return Entry(
            entry_id=identifier(relative),
            root_id=root,
            relative_path=relative,
            name=name,
            parent_entry_id=parent,
            depth=_relative_depth(relative),
            entry_type=kind,
            size_bytes=size,
            # A listed link is a link, never followed: its text as written, and
            # whether a target inside the root exists is a question a listing leaves unanswered.
            symlink_target=link,
            symlink_state=SymlinkState.UNREADABLE if link is not None else SymlinkState.NOT_A_LINK,
            first_seen_run=run_id,
            last_seen_run=run_id,
        )

    label = declaration.label or f"{listing.header.archive}:{listing.header.identifier}"
    found = [listed(".", EntryType.DIRECTORY, None, None, label)]

    def parent_of(relative: str) -> str:
        folder = str(PurePosixPath(relative).parent)
        return identifier("." if folder in {"", "."} else folder)

    file_paths = {row.path for row in listing.rows}
    folders = folders_of(file_paths)
    for folder in folders:
        # A path listed as a file and holding other listed files is a folder (an archive may
        # list a folder it serves zipped and the files inside it too): its children are what the
        # listing holds, and a file with children would hide them from every reader. Its own row's
        # statements are still recorded on it, since the identifier is the path's.
        found.append(
            listed(folder, EntryType.DIRECTORY, None, parent_of(folder), PurePosixPath(folder).name)
        )
    implied = set(folders)
    for row in listing.rows:
        if row.path in implied:
            continue
        found.append(
            listed(
                row.path,
                EntryType.SYMLINK if row.link_target is not None else EntryType.FILE,
                row.size_bytes,
                parent_of(row.path),
                PurePosixPath(row.path).name,
                row.link_target,
            )
        )
    return found


def _declared(
    *,
    subject: str,
    run_id: str,
    observer: str,
    observed_at: str,
    key: str,
    text: str | None = None,
    structure: dict[str, object] | None = None,
    integer: int | None = None,
) -> Observation:
    return Observation(
        observation_id=observation_id(
            entry=subject, observer=observer, observer_version=DECLARED_VERSION, key=key
        ),
        entry_id=subject,
        run_id=run_id,
        observer=observer,
        observer_version=DECLARED_VERSION,
        observed_at=observed_at,
        key=key,
        value_kind=(
            ValueKind.STRUCTURE
            if structure is not None
            else ValueKind.INTEGER
            if integer is not None
            else ValueKind.TEXT
        ),
        value_text=text,
        value_integer=integer,
        value_structure=canonical_json(structure) if structure is not None else None,
        extraction_tier=ExtractionTier.LITERAL,
        source_scope=SourceScope.DECLARED,
    )


def declared_observations(
    listing: Listing,
    declaration: RootDeclaration,
    *,
    run_id: str,
    observed_at: str,
    links: bool = False,
) -> list[Observation]:
    """One observation per statement a listing makes about a file, all ``declared``.

    One key per attribute name: a name a row states twice keeps its values in order in one
    observation, so an observation's identity stays one per entry, key and observer.
    """
    observer = observer_of(listing.header.archive)
    case = declaration.case_sensitivity or CaseSensitivity.SENSITIVE
    listed = frozenset(row.path for row in listing.rows)
    found: list[Observation] = []
    for row in listing.rows:
        subject = entry_id(declaration.root_id, row.path, (), case_sensitivity=case)
        grouped: dict[str, list[tuple[str, str]]] = {}
        for name, value in row.attributes:
            grouped.setdefault(declared_key("attribute", name), []).append((name, value))
        for key, pairs in sorted(grouped.items()):
            found.append(
                _declared(
                    subject=subject,
                    run_id=run_id,
                    observer=observer,
                    observed_at=observed_at,
                    key=key,
                    text=pairs[0][1] if len(pairs) == 1 else None,
                    structure=(
                        None
                        if len(pairs) == 1
                        else {"values": [value for _, value in pairs], "name": pairs[0][0]}
                    ),
                )
            )
        by_meaning: dict[str, list[dict[str, object]]] = {}
        for clock in row.clocks:
            by_meaning.setdefault(clock.meaning.value, []).append(clock.as_json())
        for meaning, said in sorted(by_meaning.items()):
            found.append(
                _declared(
                    subject=subject,
                    run_id=run_id,
                    observer=observer,
                    observed_at=observed_at,
                    key=f"{DECLARED_PREFIX}clock.{meaning}",
                    structure={"said": said},
                )
            )
        by_method: dict[str, set[str]] = {}
        for digest in row.digests:
            by_method.setdefault(digest.method, set()).add(digest.value)
        for method, values in sorted(by_method.items()):
            found.append(
                _declared(
                    subject=subject,
                    run_id=run_id,
                    observer=observer,
                    observed_at=observed_at,
                    key=f"{DECLARED_PREFIX}digest.{method}",
                    # Two values for one method is a listing contradicting itself: kept whole, so
                    # the rule reading it sees the conflict rather than one side of it.
                    text=next(iter(values)) if len(values) == 1 else None,
                    structure=None if len(values) == 1 else {"values": sorted(values)},
                )
            )
        if row.link_target is not None and row.target_size_bytes is not None:
            found.append(
                _declared(
                    subject=subject,
                    run_id=run_id,
                    observer=observer,
                    observed_at=observed_at,
                    key=TARGET_SIZE_KEY,
                    integer=row.target_size_bytes,
                )
            )
        if row.address is not None:
            stated: dict[str, object] = row.address.as_json()
            if row.locator is not None:
                stated["address"] = row.locator
            found.append(
                _declared(
                    subject=subject,
                    run_id=run_id,
                    observer=observer,
                    observed_at=observed_at,
                    key=BROKEN_ADDRESS_KEY,
                    structure=stated,
                )
            )
        if links:
            by_relation: dict[str, list[dict[str, object]]] = {}
            for link in row.links:
                if _refused(link.target, listed):
                    continue
                by_relation.setdefault(link.relation.value, []).append(link.as_json())
            for relation, said in sorted(by_relation.items()):
                found.append(
                    _declared(
                        subject=subject,
                        run_id=run_id,
                        observer=observer,
                        observed_at=observed_at,
                        key=f"{DECLARED_PREFIX}link.{relation}",
                        structure={"said": said},
                    )
                )
    return found


def _refused(target: str, listed: frozenset[str]) -> bool:
    """Whether a link's target is a placeholder: one naming no listed path."""
    return is_placeholder(target) and target.strip() not in listed


def placeholder_links(listing: Listing) -> int:
    """How many of a listing's declared links name a placeholder and are refused.

    Counted whether or not the links are recorded, so a run says what its listing held.
    """
    listed = frozenset(row.path for row in listing.rows)
    return sum(1 for row in listing.rows for link in row.links if _refused(link.target, listed))
