"""Report what a walk would find, from the filesystem's metadata alone, and write nothing.

``ingence index --dry-run`` is the pre-flight. Before a census of a tree nobody has sized, such as
a server folder or a drive just plugged in, it walks the tree the way a census does. It lists one
directory at a time, on the census's number of threads. It says what a person needs to decide
whether and how to run a census: how fast entries come, what kinds there are, how big and how old
the files are, which extensions occur, and what the links say. Every link's own text is
characterised as a census characterises it (another server's share, a drive letter, a path;
``core/links.py``). The walk never follows a link and never calls ``stat`` on it. No file is
opened.

Nothing is written: not to a root, not to the project, not to a run. The dry run returns the
report. The command line prints it and writes it where it is told.

The walk leaves out what a census leaves out: the engine's installation, another project or run,
and the project it is run for, wherever that lies. What it counts is therefore what a census would
index. Each exclusion is listed under ``left_out``.

The report predicts no archive share. Archiving follows each person's data, so a dry run counts
what it sees archived and never extrapolates. The walk can stop early (``limit`` entries, or
``seconds``) and then says so. A pre-flight cannot size a tree: earlier estimates were wrong by
three to twenty times. Its rates are measured and its totals are what it saw.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ingence.core.links import characterise_link_target
from ingence.core.platform import lstat_entry
from ingence.core.schema import RecognisedAs
from ingence.scan.recognise import (
    OwnProject,
    holds_other_names,
    installation_directories,
    recognise_directory,
)
from ingence.storage.layout import is_project_name, spelled_otherwise

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["DRY_RUN_SCHEMA", "dry_run"]

#: The report's own version, bumped whenever a field changes meaning.
DRY_RUN_SCHEMA = "dry-run-1"

#: How many of each list a report names.
_TOP = 30


class _Tally:
    """What the walk saw, gathered from its threads under one lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.kinds: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.extensions: Counter[str] = Counter()
        self.years: Counter[str] = Counter()
        self.link_shapes: Counter[str] = Counter()
        self.archive_shares: Counter[str] = Counter()
        self.archive_extensions: Counter[str] = Counter()
        self.sizes: list[int] = []
        self.listing_seconds: list[float] = []
        self.zero_byte_files = 0
        self.with_birth = 0
        self.born_after_modified = 0
        self.unenterable = 0
        self.excluded = 0
        #: Names left out for starting with a dot, as a census leaves them out.
        self.hidden = 0
        self.entries = 0
        #: The engine's own folders and the project's own names, as a census leaves them out.
        self.left_out: list[tuple[str, str]] = []


class _Engine:
    """What a walk leaves out as a census does: the engine's installation, the project it is run
    for, and hidden names unless they are read."""

    def __init__(
        self,
        project: Path | None,
        installation: frozenset[tuple[int, int]] | None,
        *,
        include_hidden: bool = True,
    ) -> None:
        self.own = OwnProject.of(project) if project is not None else None
        self.installation = installation_directories() if installation is None else installation
        self.include_hidden = include_hidden


def dry_run(
    root: Path,
    *,
    exclude_globs: Sequence[str] = (),
    workers: int = 8,
    limit: int = 0,
    seconds: float = 0.0,
    project: Path | None = None,
    installation: frozenset[tuple[int, int]] | None = None,
    include_hidden: bool = True,
) -> dict[str, Any]:
    """Walk one root's metadata as a census would and report what it saw, writing nothing.

    ``project`` is the project the dry run is for, left out wherever it lies in the root, as a
    census leaves it out; ``installation`` the engine's own directories, and ``None`` reads the
    running engine's. ``include_hidden`` off leaves out names starting with a dot, as a census
    with ``scan.include_hidden`` off does (the command passes the project's setting).
    """
    tally = _Tally()
    engine = _Engine(project, installation, include_hidden=include_hidden)
    started = time.perf_counter()
    top_level: set[str] = set()
    stopped_by = "the whole tree"
    root_is_project = engine.own is not None and _is_folder_of(engine.own, root)
    pending: list[tuple[str, int, bool]] = [(str(root), 0, root_is_project)]
    listed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        running: dict[Any, tuple[str, int]] = {}
        while pending or running:
            while pending and len(running) < max(1, workers):
                folder, depth, own = pending.pop()
                running[
                    pool.submit(_list, Path(folder), root, depth, exclude_globs, tally, engine, own)
                ] = (folder, depth)
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                folder, depth = running.pop(future)
                listed += 1
                children = future.result()
                if depth == 0:
                    top_level.update(PurePosixPath(child).name for child, _, _ in children)
                pending.extend((child, depth + 1, own) for child, _, own in children)
            if limit and tally.entries >= limit:
                stopped_by = f"the limit of {limit} entries"
                break
            if seconds and time.perf_counter() - started >= seconds:
                stopped_by = f"the limit of {seconds:g} s"
                break
        for future in running:
            future.cancel()
    elapsed = time.perf_counter() - started
    return _report(root, tally, elapsed, stopped_by, listed, len(pending), top_level)


def _list(
    folder: Path,
    root: Path,
    depth: int,
    exclude_globs: Sequence[str],
    tally: _Tally,
    engine: _Engine,
    own: bool,
) -> list[tuple[str, int, bool]]:
    """List one folder, record each entry's metadata, and return the folders to list next.

    ``own`` says the folder is the project's own, walked because it is the root or holds a
    person's files: each name the project writes is left out.
    """
    began = time.perf_counter()
    children: list[tuple[str, int, bool]] = []
    seen: list[tuple[str, os.stat_result, str | None, bool]] = []
    try:
        with os.scandir(folder) as listing:
            for item in listing:
                relative = str(PurePosixPath(Path(item.path).relative_to(root).as_posix()))
                if own and (is_project_name(item.name) or spelled_otherwise(folder, item.name)):
                    _leave_out(tally, relative, RecognisedAs.THIS_PROJECT)
                    continue
                hidden = not engine.include_hidden and item.name.startswith(".")
                excluded = hidden or _excluded(relative, item.name, exclude_globs)
                if excluded and engine.own is not None and item.path == engine.own.text:
                    # As a census: the project, excluded or hidden, is named and not walked.
                    _leave_out(tally, relative, RecognisedAs.THIS_PROJECT)
                    continue
                if hidden:
                    with tally.lock:
                        tally.hidden += 1
                    continue
                if excluded:
                    with tally.lock:
                        tally.excluded += 1
                    continue
                try:
                    # The census's own one call per entry (``core/platform``: ``statx`` on Linux).
                    result = lstat_entry(item)
                except OSError as error:
                    with tally.lock:
                        tally.errors[_errno_name(error)] += 1
                    continue
                inside_own = engine.own is not None and engine.own.is_folder(item.path, result)
                if inside_own and not holds_other_names(
                    Path(item.path), partial(_read, relative, exclude_globs)
                ):
                    _leave_out(tally, relative, RecognisedAs.THIS_PROJECT)
                    continue
                recognised = (
                    None
                    if inside_own
                    else recognise_directory(
                        Path(item.path), result, installation=engine.installation
                    )
                )
                if recognised is not None:
                    _leave_out(tally, relative, recognised)
                    continue
                text = None
                if stat.S_ISLNK(result.st_mode):
                    try:
                        text = os.readlink(item.path)
                    except OSError as error:
                        text = f"<unreadable: {_errno_name(error)}>"
                seen.append((item.path, result, text, inside_own))
    except PermissionError:
        with tally.lock:
            tally.unenterable += 1
        return []
    except OSError as error:
        with tally.lock:
            tally.errors[_errno_name(error)] += 1
        return []
    listing_seconds = time.perf_counter() - began
    with tally.lock:
        tally.listing_seconds.append(listing_seconds)
        for path, result, text, inside_own in seen:
            tally.entries += 1
            mode = result.st_mode
            if stat.S_ISLNK(mode):
                tally.kinds["symlink"] += 1
                _link(text or "", tally)
            elif stat.S_ISDIR(mode):
                tally.kinds["directory"] += 1
                children.append((path, depth + 1, inside_own))
            elif stat.S_ISREG(mode):
                tally.kinds["file"] += 1
                _file(path, result, tally)
            else:
                tally.kinds["other"] += 1
    return children


def _is_folder_of(own: OwnProject, root: Path) -> bool:
    """Whether the root is the project's own folder; a root that cannot be read is not."""
    try:
        return own.is_folder(root, os.stat(root))
    except OSError:
        return False


def _read(folder: str, exclude_globs: Sequence[str], name: str) -> bool:
    """Whether a name in the project's folder is one the settings' exclusions leave in."""
    return not _excluded(f"{folder}/{name}", name, exclude_globs)


def _leave_out(tally: _Tally, relative: str, what: RecognisedAs) -> None:
    with tally.lock:
        tally.left_out.append((relative, what.value))


def _file(path: str, result: os.stat_result, tally: _Tally) -> None:
    size = int(result.st_size)
    tally.sizes.append(size)
    if size == 0:
        tally.zero_byte_files += 1
    name = PurePosixPath(path).name
    tally.extensions[name.rsplit(".", 1)[1].casefold() if "." in name.strip(".") else ""] += 1
    tally.years[str(datetime.fromtimestamp(result.st_mtime, tz=UTC).year)] += 1
    # Nanoseconds where the layer read them (Linux's ``statx``), else the float macOS gives.
    birth_ns = getattr(result, "st_birthtime_ns", None)
    birth = birth_ns / 1_000_000_000 if birth_ns else getattr(result, "st_birthtime", None)
    if birth:
        tally.with_birth += 1
        if birth > result.st_mtime:
            tally.born_after_modified += 1


def _link(text: str, tally: _Tally) -> None:
    """Characterise a link from its own text, as the census does, and count what it says."""
    said = characterise_link_target(text)
    shape = str(said.get("written_as") or "unknown")
    tally.link_shapes[shape] += 1
    if shape == "network_share":
        tally.archive_shares[f"{said.get('server')}/{said.get('share')}"] += 1
        tally.archive_extensions[str(said.get("extension") or "")] += 1


def _excluded(relative: str, name: str, patterns: Sequence[str]) -> bool:
    """A census's exclusion reading: a pattern against the relative path and the basename."""
    return any(
        fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns
    )


def _errno_name(error: OSError) -> str:
    return os.strerror(error.errno) if error.errno else type(error).__name__


def _report(
    root: Path,
    tally: _Tally,
    elapsed: float,
    stopped_by: str,
    listed: int,
    left: int,
    top_level: set[str],
) -> dict[str, Any]:
    files = tally.kinds.get("file", 0)
    links = tally.kinds.get("symlink", 0)
    listing = sorted(tally.listing_seconds)
    return {
        "schema": DRY_RUN_SCHEMA,
        "root": str(root),
        "walk": {
            "entries": tally.entries,
            "seconds": round(elapsed, 2),
            # The high-resolution clock: Windows' monotonic one ticks every 15 ms or so, and a
            # small tree walked inside one tick would have no rate at all.
            "entries_per_second": round(tally.entries / elapsed, 1) if elapsed > 0 else None,
            "stopped_by": stopped_by,
            "folders_listed": listed,
            "folders_left_unlisted": left,
            "listing_seconds_median": round(statistics.median(listing), 4) if listing else None,
            "listing_seconds_max": round(listing[-1], 4) if listing else None,
            "top_level_entries": len(top_level),
        },
        "kinds": dict(tally.kinds),
        "excluded": tally.excluded,
        "excluded_hidden": tally.hidden,
        "left_out": [{"path": path, "as": what} for path, what in sorted(tally.left_out)],
        "unenterable_folders": tally.unenterable,
        "errors": dict(tally.errors),
        "files": {
            "count": files,
            "zero_bytes": tally.zero_byte_files,
            "total_bytes": sum(tally.sizes),
            "median_bytes": int(statistics.median(tally.sizes)) if tally.sizes else None,
            "with_birth_time": tally.with_birth,
            "born_after_modified": tally.born_after_modified,
            "modified_by_year": dict(sorted(tally.years.items())),
            "extensions_top": dict(tally.extensions.most_common(_TOP)),
        },
        "links": {
            "count": links,
            "share_of_files_and_links": round(links / (files + links), 4)
            if files + links
            else None,
            "written_as": dict(tally.link_shapes),
            "archive_links_by_server_and_share": dict(tally.archive_shares.most_common(_TOP)),
            "archive_links_by_extension": dict(tally.archive_extensions.most_common(_TOP)),
        },
    }
