"""The engine's own files, recognised in a data pool by their layout and never by opening one.

An engine that indexes its own output as data ends up describing itself. A project kept
inside a pool it indexes would enter its own census, and every later run would index the run
before it. So the census recognises the engine's own directories while it walks, and leaves
them out of the Index. The run record lists what the census recognised, whether or not it
was indexed, so a gap in the Index has a stated reason.

The census recognises three things, each from names alone. The census never opens a file,
and recognition keeps that rule: a directory is recognised by which of its children exist
and what kind of object each is, and by nothing inside them.

- The installation. This is the directory the running engine is imported from and, for an
  editable install, the source directory the install's own metadata names. It is also the
  environment the engine runs in, where that is one of its own (a venv, uv's tool folder, a
  conda environment). So a person's ``ingence-env`` kept inside the folder they read never
  enters a census. The census reads all of these from the running engine, never from the
  pool, and compares them by device and inode, so one directory reached by two spellings is
  still recognised.
- A project. This is a directory holding ``project.json`` and ``runs``, beside either
  ``settings.yaml`` and the ``CURRENT`` pointer (a project that has published a run) or the
  ``rulebook``, ``dictionary`` and ``record`` folders that every project is made with (one
  that has not yet published a run). The names together make recognition specific. A
  ``project.json`` beside a ``settings.yaml`` is common enough outside this engine that
  neither is evidence alone.
- A run. This is a directory holding ``manifest.json`` beside the entry table's and the run
  record's directories, which every published run holds, whichever verb wrote it.

The project that a run writes is known another way. A person may keep a project on any
storage, drive, mount or server, so the engine must recognise and leave out its own files in
runs. Its names cannot identify it, because on its first run it has no pointer, and wherever
it is a root its names sit among the person's. So the engine knows it by its folder's device
and file number, or its path (:class:`OwnProject`), wherever it lies. Its folder is left out
whole where that holds nothing else the census would read. Otherwise each name it writes
(``storage.layout.PROJECT_NAMES``) is left out and the rest is data.

Recognition costs a ``stat`` of a child name or two for each directory walked, and nothing
for the installation, whose identity the walk has already read.
"""

from __future__ import annotations

import json
import os
import stat as stat_module
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ingence.core.schema import Entry, RecognisedAs, RunRecord
from ingence.core.settings import SETTINGS_FILE
from ingence.storage.layout import (
    CURRENT_POINTER,
    DICTIONARY_DIR,
    MANIFEST_FILE,
    PROJECT_FILE,
    RECORD_DIR,
    RULEBOOK_DIR,
    RUNS_DIR,
    is_project_name,
    spelled_otherwise,
    table_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "OwnProject",
    "environment_directory",
    "holds_other_names",
    "installation_directories",
    "recognise_directory",
]

#: The distribution the running engine was installed as.
_DISTRIBUTION = "ingence"

#: The table directories every published run holds, whichever verb wrote it.
_ENTRIES = table_for(Entry).directory
_RUN = table_for(RunRecord).directory
#: The folders every project is made with beside its runs, before its first run.
_MADE_WITH = (RULEBOOK_DIR, DICTIONARY_DIR, RECORD_DIR)


@dataclass(frozen=True, slots=True)
class OwnProject:
    """The project a run writes: its folder's path and, where it can be read, device and number.

    Either decides: the number where the walk spells the folder another way (a case the
    filesystem folds, a second mount of one volume), the path where a filesystem gives no
    stable number. A number is compared with its device, because it is unique on one volume
    only.
    """

    path: Path
    identity: tuple[int, int] | None = None

    @classmethod
    def of(cls, path: Path) -> OwnProject:
        """The project at ``path``, its number read once for the run."""
        resolved = path.expanduser().resolve()
        try:
            result = os.stat(resolved)
        except OSError:
            return cls(path=resolved)
        number = (int(result.st_dev), int(result.st_ino)) if result.st_ino else None
        return cls(path=resolved, identity=number)

    @property
    def text(self) -> str:
        """The folder's path as a walk spells the paths it lists, compared as text."""
        return str(self.path)

    def is_folder(self, path: str | Path, result: os.stat_result) -> bool:
        """Whether the directory at ``path``, whose own ``lstat`` is ``result``, is the project."""
        if not stat_module.S_ISDIR(result.st_mode):
            return False
        if os.fspath(path) == self.text:
            return True
        return (
            self.identity is not None
            and bool(result.st_ino)
            and (int(result.st_dev), int(result.st_ino)) == self.identity
        )


def installation_directories() -> frozenset[tuple[int, int]]:
    """Return the device and inode of each directory the running engine is installed from.

    The package directory always, the source directory an editable install's
    ``direct_url.json`` names where there is one, and the environment the engine runs in
    where it is one of its own. A directory that cannot be read is left out rather than
    guessed at.
    """
    environment = environment_directory(sys.prefix, sys.base_prefix)
    return _identities(
        (
            Path(__file__).resolve().parents[1],
            *_editable_source(),
            *((environment,) if environment is not None else ()),
        )
    )


def environment_directory(prefix: str, base_prefix: str) -> Path | None:
    """The environment a Python runs in, where it is one of its own, or None.

    A virtual environment (a venv, and the one ``uv tool install`` makes) has a prefix of its
    own; a conda environment, base included, holds ``conda-meta``. A system Python is neither,
    and its folder is never taken for the engine's.
    """
    if prefix != base_prefix:
        return Path(prefix)
    if (Path(prefix) / "conda-meta").is_dir():
        return Path(prefix)
    return None


def recognise_directory(
    path: Path, result: os.stat_result, *, installation: frozenset[tuple[int, int]]
) -> RecognisedAs | None:
    """Return what part of the engine a directory is, or None where it is none of them.

    ``result`` is the directory's own ``lstat``, which the walk has already read, so the
    installation costs nothing to recognise. A symbolic link is never recognised, because
    it is never followed.
    """
    if not stat_module.S_ISDIR(result.st_mode):
        return None
    if result.st_ino and (int(result.st_dev), int(result.st_ino)) in installation:
        return RecognisedAs.INSTALLATION
    if (
        _is_file(path / PROJECT_FILE)
        and _is_directory(path / RUNS_DIR)
        and (
            (_is_file(path / SETTINGS_FILE) and _is_file(path / CURRENT_POINTER))
            or all(_is_directory(path / name) for name in _MADE_WITH)
        )
    ):
        return RecognisedAs.PROJECT
    if (
        _is_file(path / MANIFEST_FILE)
        and _is_directory(path / _ENTRIES)
        and _is_directory(path / _RUN)
    ):
        return RecognisedAs.RUN
    return None


def holds_other_names(folder: Path, readable: Callable[[str], bool]) -> bool:
    """Whether the project's folder holds a name it does not write that a walk would read.

    The function lists the project's folder, which is the engine's own. A folder that cannot
    be listed holds nothing to read, and is left out whole. A hidden name is never a person's
    file there, whatever the hidden-name setting. The hidden name that a project's folder
    gathers is the file browser's own (``.DS_Store``), and the census and the dry run read it
    by this one rule.
    """
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return any(
        not name.startswith(".")
        and not is_project_name(name)
        and spelled_otherwise(folder, name) is None
        and readable(name)
        for name in names
    )


def _editable_source() -> tuple[Path, ...]:
    """Return the source directory an editable install of the engine names, or nothing.

    The metadata and URL modules are imported here, where the one file is read. At module
    scope they would put ``ssl``, ``http.client`` and ``email`` into every process that
    imports the census, which is the engine's always-on path, to read one small JSON file
    once per run.
    """
    from importlib import metadata
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    try:
        declared = metadata.distribution(_DISTRIBUTION).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return ()
    if not declared:
        return ()
    try:
        described = json.loads(declared)
    except ValueError:
        return ()
    if not isinstance(described, dict):
        return ()
    info, url = described.get("dir_info"), described.get("url")
    if not isinstance(info, dict) or not info.get("editable") or not isinstance(url, str):
        return ()
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return ()
    return (Path(url2pathname(parsed.path)),)


def _identities(directories: Iterable[Path]) -> frozenset[tuple[int, int]]:
    """Return the device and inode of each directory that can be read."""
    found: set[tuple[int, int]] = set()
    for directory in directories:
        with suppress(OSError):
            result = os.stat(directory)
            if stat_module.S_ISDIR(result.st_mode) and result.st_ino:
                found.add((int(result.st_dev), int(result.st_ino)))
    return frozenset(found)


def _mode(path: Path) -> int | None:
    """Return what kind of object a path names, without following a link or opening it."""
    try:
        return os.stat(path, follow_symlinks=False).st_mode
    except OSError:
        return None


def _is_file(path: Path) -> bool:
    mode = _mode(path)
    return mode is not None and stat_module.S_ISREG(mode)


def _is_directory(path: Path) -> bool:
    mode = _mode(path)
    return mode is not None and stat_module.S_ISDIR(mode)
