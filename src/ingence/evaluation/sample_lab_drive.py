"""The drive being built: its files, their times, and what a person who knew them would say.

One :class:`Drive` gathers a laboratory's files, folders and links in memory, together with
every answer the key will hold, before anything is written. The shapes in
:mod:`ingence.evaluation.sample_lab_shapes` add to it; :mod:`ingence.evaluation.sample_lab` turns it
into a pool, a key, a manifest and a datasheet.

Nothing here reads a filesystem. A path is a POSIX string relative to the pool. A time is an
aware UTC datetime that is clamped to the last moment a stamp may take
(:data:`LATEST`, the end of 2025), so no file claims to be newer than that.
"""

from __future__ import annotations

import datetime as dt
import random
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["LATEST", "Drive"]

#: No file or folder is stamped later than this: the collection ends in 2025.
LATEST = dt.datetime(2025, 12, 31, 18, 0, tzinfo=dt.UTC)


def _moved_in(entry: object, moved: Mapping[str, str]) -> object:
    """An entry of the unscored sections with every string that is a moved path made the new one."""
    if isinstance(entry, str):
        return moved.get(entry, entry)
    if isinstance(entry, dict):
        return {key: _moved_in(value, moved) for key, value in entry.items()}
    if isinstance(entry, (list, tuple)):
        return [_moved_in(item, moved) for item in entry]
    return entry


@dataclass
class Drive:
    """The files and the answers, gathered before anything is written."""

    random: random.Random
    files: dict[str, bytes] = field(default_factory=dict)
    times: dict[str, dt.datetime] = field(default_factory=dict)
    #: What kind of file each is (``nd2``, ``csv``, ``python_script``...), for the manifest.
    kinds: dict[str, str] = field(default_factory=dict)
    folders: set[str] = field(default_factory=set)
    empty_folders: set[str] = field(default_factory=set)
    #: Symbolic links to make: the link's path and the text it holds as its target.
    links: dict[str, str] = field(default_factory=dict)
    link_times: dict[str, dt.datetime] = field(default_factory=dict)
    #: The last moment anything under a member's folder may be stamped (a departed member's). A
    #: folder is named by its path from the top, for example ``Lab Alumni/Maria Chen``.
    caps: dict[str, dt.datetime] = field(default_factory=dict)
    counter: int = 0
    #: The share of experiments with many more replicates than usual (none in the small tier).
    busy_share: float = 0.0
    _contents: dict[bytes, str] = field(default_factory=dict)
    _lowered: set[str] = field(default_factory=set)
    _lowered_empty: set[str] = field(default_factory=set)
    # -- what the key says ----------------------------------------------------------------------
    values: dict[str, dict[str, str | None]] = field(default_factory=dict)
    undecidable_values: dict[str, set[str]] = field(default_factory=dict)
    pairs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    undecidable_pairs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    groups: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    named_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    declined: dict[str, set[str]] = field(default_factory=dict)
    #: What makes an experiment folder the same experiment as another: its project, the words of
    #: its name, and the instrument's format, with the day it was made.
    signatures: dict[str, tuple[tuple[str, str, str], str]] = field(default_factory=dict)
    #: Files never saved again under a copy-of name (a summary workbook is one).
    never_copied: set[str] = field(default_factory=set)
    #: What each file replaced by an archive link was.
    archived_kinds: dict[str, str] = field(default_factory=dict)
    #: What the scorer does not yet score, section by section (see ``sample_lab`` for the sections).
    unscored: dict[str, list[object]] = field(default_factory=dict)

    # -- files ----------------------------------------------------------------------------------

    def token(self) -> bytes:
        """Eight bytes no other call returns, so no two files agree by accident."""
        self.counter += 1
        return struct.pack("<I", self.counter) + self.random.randbytes(4)

    def stamp(self, moment: dt.datetime, path: str = "") -> dt.datetime:
        """``moment``, but never later than the end of 2025 or than a departed member's last day."""
        first, _, rest = path.partition("/")
        cap = self.caps.get(first)
        if cap is None and rest:
            cap = self.caps.get(f"{first}/{rest.partition('/')[0]}")
        return min(moment, cap or LATEST)

    def file(
        self,
        path: str,
        data: bytes,
        moment: dt.datetime,
        *,
        kind: str,
        copy_of: str | None = None,
    ) -> str:
        """Add a file. Bytes shared with an earlier file are refused unless this is its copy."""
        if path in self.files:
            message = f"the generator wrote {path} twice"
            raise ValueError(message)
        lowered = path.casefold()
        if lowered in self._lowered:
            message = f"the generator wrote {path} twice, differing only in case"
            raise ValueError(message)
        if data and copy_of is None and data in self._contents:
            message = f"{path} holds the bytes of {self._contents[data]} and is not its copy"
            raise ValueError(message)
        self._lowered.add(lowered)
        self.files[path] = data
        self.times[path] = self.stamp(moment, path)
        self.kinds[path] = kind
        if data and data not in self._contents:
            self._contents[data] = path
        parent = path.rpartition("/")[0]
        while parent:
            self.folders.add(parent)
            parent = parent.rpartition("/")[0]
        return path

    def folder(self, path: str, moment: dt.datetime) -> None:
        """Add an empty folder."""
        if path in self.folders or path in self.empty_folders:
            return
        self.empty_folders.add(path)
        self._lowered_empty.add(path.casefold())
        self.times[path + "/"] = self.stamp(moment, path)
        parent = path.rpartition("/")[0]
        while parent:
            self.folders.add(parent)
            parent = parent.rpartition("/")[0]

    def link(self, path: str, target: str, moment: dt.datetime) -> None:
        """Plan a symbolic link at ``path`` holding ``target`` as text (it is made when written)."""
        self.links[path] = target
        self.link_times[path] = self.stamp(moment, path)
        parent = path.rpartition("/")[0]
        while parent:
            self.folders.add(parent)
            parent = parent.rpartition("/")[0]

    def archive(self, path: str, target: str) -> None:
        """Replace a file by a link to ``target``, which is on another server and so is not there.

        The link keeps the file's name and time. Its content and its place in every answer are
        gone: the key accepts any answer about it.
        """
        data = self.files.pop(path)
        self.link_times[path] = self.times.pop(path)
        self.links[path] = target
        if self._contents.get(data) == path:
            del self._contents[data]
        kind = self.kinds.pop(path)
        self.archived_kinds[path] = kind
        for family in ("stage", "replicate", "derivation", "reference", "empty_file"):
            self.decline(path, family)
        for table in (self.values, self.undecidable_values):
            for entries in table.values():
                if isinstance(entries, dict):
                    entries.pop(path, None)
                else:
                    entries.discard(path)

    def taken(self, path: str) -> bool:
        """Whether a file or a folder is already at ``path`` (case aside)."""
        lowered = path.casefold()
        return lowered in self._lowered or lowered in self._lowered_empty or path in self.folders

    def rename(
        self, moved: Mapping[str, str], rewritten: Mapping[str, bytes] | None = None
    ) -> None:
        """Give paths new names, and some files new bytes, wherever the drive and its key name them.

        ``moved`` maps an old path (a file, a folder, an empty folder or a link) to its new one;
        ``rewritten`` maps a new file path to its new bytes. Every list, every answer and every
        pair that named an old path names the new one afterward. Nothing about a folder's own name
        is kept: a group's name is a set of words and stays as it was. Used by the vocabulary tier
        (:mod:`ingence.evaluation.sample_lab_vocabulary`), which has made sure no new path is taken.
        """
        new_bytes = dict(rewritten or {})

        def at(path: str) -> str:
            return moved.get(path, path)

        def of_marker(path: str) -> str:
            return at(path[:-1]) + "/" if path.endswith("/") else at(path)

        self.files = {at(path): new_bytes.get(at(path), data) for path, data in self.files.items()}
        self.times = {of_marker(path): moment for path, moment in self.times.items()}
        self.kinds = {at(path): kind for path, kind in self.kinds.items()}
        self.folders = {at(path) for path in self.folders}
        self.empty_folders = {at(path) for path in self.empty_folders}
        self.links = {at(path): target for path, target in self.links.items()}
        self.link_times = {at(path): moment for path, moment in self.link_times.items()}
        self.signatures = {at(path): held for path, held in self.signatures.items()}
        self._contents = {}
        for path, data in self.files.items():
            if data:
                self._contents.setdefault(data, path)
        self._lowered = {path.casefold() for path in self.files}
        self._lowered_empty = {path.casefold() for path in self.empty_folders}
        self.values = {
            family: {at(path): value for path, value in entries.items()}
            for family, entries in self.values.items()
        }
        self.undecidable_values = {
            family: {at(path) for path in paths}
            for family, paths in self.undecidable_values.items()
        }
        self.pairs = {
            family: {(at(first), at(second)) for first, second in pairs}
            for family, pairs in self.pairs.items()
        }
        self.undecidable_pairs = {
            family: {(at(first), at(second)) for first, second in pairs}
            for family, pairs in self.undecidable_pairs.items()
        }
        self.groups = {
            family: [tuple(at(path) for path in members) for members in groups]
            for family, groups in self.groups.items()
        }
        self.named_groups = {
            name: tuple(at(path) for path in members) for name, members in self.named_groups.items()
        }
        self.declined = {
            family: {at(path) for path in paths} for family, paths in self.declined.items()
        }
        self.never_copied = {at(path) for path in self.never_copied}
        self.archived_kinds = {at(path): kind for path, kind in self.archived_kinds.items()}
        self.unscored = {
            section: [_moved_in(entry, moved) for entry in entries]
            for section, entries in self.unscored.items()
        }

    # -- the key --------------------------------------------------------------------------------

    def value(self, family: str, path: str, value: str | None) -> None:
        self.values.setdefault(family, {})[path] = value

    def undecidable(self, family: str, path: str) -> None:
        self.undecidable_values.setdefault(family, set()).add(path)

    def pair(self, family: str, first: str, second: str) -> None:
        self.pairs.setdefault(family, set()).add((first, second))

    def pair_undecidable(self, family: str, first: str, second: str) -> None:
        self.undecidable_pairs.setdefault(family, set()).add((first, second))

    def group(self, family: str, members: tuple[str, ...], *, name: str | None = None) -> None:
        self.groups.setdefault(family, []).append(members)
        if name is not None:
            self.named_groups[name] = tuple(sorted(members))

    def decline(self, paths: str | Iterable[str], *families: str) -> None:
        """Leave ``paths`` out of the score in each family: any answer about them is acceptable."""
        chosen = [paths] if isinstance(paths, str) else list(paths)
        for family in families:
            self.declined.setdefault(family, set()).update(chosen)

    def note(self, section: str, entry: object) -> None:
        """Add an entry to a section of what the scorer does not yet score."""
        self.unscored.setdefault(section, []).append(entry)
