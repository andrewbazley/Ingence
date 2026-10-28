"""Where Ingence may write a file or folder a person names: never into the folders it reads.

Source data is read-only. Several commands write a file or a folder where a person says (an
export, a report, a shapes table, a taught-answers section). Each asks this module first, so the
one rule is decided in one place:

- Refused: anywhere inside a folder the project reads (a *root*), and anywhere inside the project
  itself except its ``projections/exports`` folder.
- Allowed: the project's ``projections/exports`` folder, and anywhere outside both. A place
  outside both is the person's own choice.
- Never replaced without being asked: a name a person gives where anything already is (a file, or
  a link, one leading nowhere included) is refused unless the command is given ``--overwrite``
  (:func:`refuse_existing`). A command that writes several files checks every one before it writes
  any. A file under the project's own ``projections/exports`` is the engine's export, and the next
  export replaces it without being asked.

The nearest folder wins. A project may live inside the folder it reads, so the check judges a
destination by the first of {a root, the exports folder, the project} met on the way up from it. A
file in ``<root>/<project>/projections/exports`` meets the exports folder before the project and
the root, and is allowed.

Identity decides, never spelling. A path is never normalized first, because a ``..`` after a link
would then name a folder the system never reaches. The check compares a destination with each
root, the exports folder and the project by ``os.path.samefile`` (the same folder on disk,
whatever the case of its letters or the links that lead to it). It compares along the real folder
the file lands in and along the real path it reaches through a link standing at its own name. If
either walk refuses, the destination is refused. A root that cannot be reached (an unmounted
share) is compared by its spelling instead, so a folder is never made at the place the share will
appear.

A file is written beside itself and moved into place (:func:`replacing`,
:func:`write_replacing`). The write goes to a temporary file in the destination's own folder, then
``os.replace`` moves it. This has four effects. A second name for an indexed file (a hard link,
as a hard-linked backup snapshot holds) now leads to the new file, and the indexed file keeps its
bytes. A link at the destination is replaced by the file and never written through. The new file
keeps the permissions of the file it replaces. The window between asking and writing, in which a
folder could be turned into a link, shrinks to the moment of the move.

Asking writes nothing. A destination outside the project makes nothing in it. To compare a
destination inside the project with an exports folder that does not exist yet, the check makes
that folder (and any missing folder above it) for the length of the question, and takes away
whatever it made unless the destination is inside it.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import IO, Any, TextIO

    from ingence.core.project import Project

__all__ = [
    "EXPORTS_FOLDER",
    "DestinationRefusedError",
    "OutputExistsError",
    "ReadOnlyFileError",
    "Refusal",
    "check_output_file",
    "check_output_folder",
    "create_exclusive",
    "declared_roots",
    "exports_folder",
    "in_exports",
    "joined",
    "open_appending",
    "refusal_words",
    "refuse_existing",
    "refuse_read_only",
    "replacing",
    "typed_path",
    "where_refused",
    "write_atomically",
    "write_replacing",
    "writing_atomically",
]

#: The one folder inside a project that a person's export is written into (and anything beneath).
EXPORTS_FOLDER = ("projections", "exports")


class DestinationRefusedError(ValueError):
    """A file or folder was named where Ingence must not write. The message says why."""


class OutputExistsError(DestinationRefusedError):
    """A file a person named is already there, and the command was not asked to replace it."""


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a destination is refused: it lies in a root, or in the project outside its exports."""

    kind: Literal["root", "project"]
    #: The root the destination lies in, for ``kind == "root"``.
    root: Path | None
    #: The project's exports folder, where a person may write instead.
    exports: Path
    #: The project the destination was asked against.
    project: Path


def joined(location: Path) -> Path:
    """Return ``location`` made absolute against the working directory, and never normalized.

    The function does not remove a ``..`` by spelling. After a symbolic link, ``..`` names the
    folder above the link's target, not above the link, and only the system knows which. So the
    check and the write both leave it to the system, and every comparison here is by what the
    system reaches. The function does not expand ``~`` either. A command that expands it does so
    once, before it asks and before it writes.
    """
    return location if location.is_absolute() else Path.cwd() / location


def typed_path(path: Path) -> Path:
    """A path a person typed, with a leading ``~`` made their home folder.

    A shell does not expand the ``~`` in ``--out=~/table.csv``, and a program that writes the path
    as it reached it then makes a folder named ``~``. A command calls this once, as the path
    arrives, so that the check and the write are given the same path. Nothing below it expands
    anything. Where no home folder is known the path stays as typed.
    """
    try:
        return path.expanduser()
    except RuntimeError:
        return path


def exports_folder(project: Path) -> Path:
    """The folder inside ``project`` that a person's export may be written into."""
    return project.joinpath(*EXPORTS_FOLDER)


def declared_roots(project: Project) -> list[Path]:
    """The folders a project reads, as paths: its roots, leaving out an archive's listing (which
    is a file list kept inside the project, not a folder of a person's)."""
    return [Path(root.mount_path) for root in project.identity.roots if root.listing is None]


class _Identities:
    """Which folder on disk a path is, asked once per path: ``(device, file number)`` or None."""

    def __init__(self) -> None:
        self._seen: dict[Path, tuple[int, int] | None] = {}

    def of(self, path: Path) -> tuple[int, int] | None:
        if path not in self._seen:
            try:
                found = os.stat(path)
            except OSError:
                self._seen[path] = None
            else:
                self._seen[path] = (found.st_dev, found.st_ino)
        return self._seen[path]

    def same(self, left: Path, right: Path) -> bool:
        wanted = self.of(right)
        return wanted is not None and self.of(left) == wanted


def _spelled_within(location: Path, folder: Path) -> bool:
    """Whether ``location`` is ``folder`` or beneath it by spelling, for a folder not reachable."""
    inside = PurePath(os.path.abspath(location))
    outer = PurePath(os.path.abspath(folder))
    return inside == outer or outer in inside.parents


def _walk(
    chain: Sequence[Path],
    identities: _Identities,
    *,
    project: Path,
    exports: Path,
    counts: bool,
    roots: Sequence[Path],
) -> Refusal | Literal["exports"] | None:
    """What the first of ``chain`` that is a root, the exports folder or the project decides.

    ``counts`` says whether the exports folder is the project's own (see :func:`where_refused`).
    Where it is not, no folder is taken for it.
    """
    for folder in chain:
        for root in roots:
            if identities.same(folder, root):
                return Refusal("root", root, exports, project)
        if counts and identities.same(folder, exports):
            return "exports"
        if identities.same(folder, project):
            return Refusal("project", None, exports, project)
    return None


def where_refused(location: Path, *, project: Path, roots: Sequence[Path]) -> Refusal | None:
    """Why ``location`` (a file or a folder, existing or not) may not be written, or None.

    ``roots`` are the folders the project reads (:func:`declared_roots`). Identity decides, along
    the real folder the file lands in and along the real path it reaches through a link at its own
    name. The first of a root, the exports folder or the project met going up decides each walk.
    If either walk refuses, the location is refused. A place outside the project makes nothing in
    it. A place inside it, with no exports folder yet, is asked again with the folder made (see the
    module's account), and the folder is taken away again unless the destination is inside it.

    The exports folder counts only where it is the project's own: where the system reaches
    ``projections/exports`` is ``<the project's real path>/projections/exports``. A link a person
    made there, leading anywhere else (into a root, say), is not the exports folder. Nothing is
    made for it, and a destination reached through it is judged by where it really lies.
    """
    target = joined(location)
    exports = exports_folder(project)
    counts = os.path.realpath(exports) == os.path.join(os.path.realpath(project), *EXPORTS_FOLDER)
    # Two places, each a real path with no link and no ``..`` left in it. Where the file lands: the
    # real folder its name is in, and its own name (a file is written, or a link at it replaced,
    # in that folder). And where the path leads through a link at the name itself, one that leads
    # nowhere yet included: the place a write through it would make the file.
    paths = (Path(os.path.realpath(target.parent)) / target.name, Path(os.path.realpath(target)))
    identities = _Identities()

    def verdicts() -> list[Refusal | Literal["exports"] | None]:
        found: list[Refusal | Literal["exports"] | None] = []
        for reached in paths:
            existing = reached
            while not os.path.exists(existing) and existing != existing.parent:
                existing = existing.parent
            found.append(
                _walk(
                    (existing, *existing.parents),
                    identities,
                    project=project,
                    exports=exports,
                    counts=counts,
                    roots=roots,
                )
            )
        return found

    decided = verdicts()
    made: list[Path] = []
    in_the_project = any(isinstance(item, Refusal) and item.kind == "project" for item in decided)
    if counts and in_the_project and os.path.isdir(project) and not os.path.exists(exports):
        # Deepest first, so each is empty when it is taken away.
        folder = exports
        while not os.path.exists(folder):
            made.append(folder)
            folder = folder.parent
        try:
            exports.mkdir(parents=True)
        except OSError:
            _take_away(made)
            made = []
        else:
            identities = _Identities()
            decided = verdicts()
    refusal = next((item for item in decided if isinstance(item, Refusal)), None)
    if refusal is None:
        # A root nobody can reach is compared by its spelling: the place it will appear is not
        # ours to fill.
        for root in roots:
            unreachable = identities.of(root) is None
            if unreachable and any(_spelled_within(spelled, root) for spelled in paths):
                refusal = Refusal("root", root, exports, project)
                break
    if refusal is not None or "exports" not in decided:
        _take_away(made)
    return refusal


def _take_away(made: Sequence[Path]) -> None:
    """Remove the empty folders the check made, deepest first, leaving any that is not empty."""
    for folder in made:
        try:
            folder.rmdir()
        except OSError:
            return


def refusal_words(refusal: Refusal, shown: Path) -> str:
    """A refusal in plain words: what was named, why not, and where it may go instead."""
    if refusal.kind == "root":
        return (
            f"{shown} is inside {refusal.root}, a folder this project reads, and Ingence never "
            f"writes into the folders it reads. Name a place outside it, or one under "
            f"{refusal.exports}."
        )
    return (
        f"{shown} is inside the project at {refusal.project}, whose files Ingence keeps for "
        f"itself. Name a place outside the project, or one under {refusal.exports}."
    )


def in_exports(location: Path, *, project: Path) -> bool:
    """Whether a file named ``location`` lands in the project's own exports folder, or beneath it:
    by the real folder its name is in, and only where ``projections/exports`` is the project's own
    (not a link a person made there)."""
    own = Path(os.path.realpath(project)).joinpath(*EXPORTS_FOLDER)
    if Path(os.path.realpath(exports_folder(project))) != own:
        return False
    landing = Path(os.path.realpath(joined(location).parent))
    return landing == own or own in landing.parents


def refuse_existing(paths: Sequence[Path], *, project: Path, overwrite: bool = False) -> None:
    """Refuse, before any is written, the files a person named where something already is.

    A file a person names never silently replaces one already there. Anything at a name (a file,
    or a link, one leading nowhere included: ``os.path.lexists``) refuses the command
    (:class:`OutputExistsError`). The function asks about every name the command would write before
    the command writes the first, so a refusal leaves nothing half done. ``overwrite`` (the
    commands' ``--overwrite``) replaces them. A file under the project's own exports folder is
    replaced without asking (:func:`in_exports`).
    """
    if overwrite:
        return
    taken = [
        path
        for path in paths
        if os.path.lexists(joined(path)) and not in_exports(path, project=project)
    ]
    if not taken:
        return
    found = [f"{_what_is_at(joined(path))} at {path}" for path in taken]
    if len(found) == 1:
        message = (
            f"There is already {found[0]}, so nothing was written. Give --overwrite to replace "
            "it, or name a new file."
        )
    else:
        message = (
            f"There is already {'; '.join(found)}, so nothing was written. Give --overwrite to "
            "replace them, or name a new place."
        )
    raise OutputExistsError(message)


def _what_is_at(path: Path) -> str:
    if os.path.islink(path):
        return "a link" if os.path.exists(path) else "a link that leads nowhere"
    return "a folder" if os.path.isdir(path) else "a file"


def check_output_file(path: Path, *, project: Path, roots: Sequence[Path]) -> None:
    """Refuse a file that would be written inside a root or inside the project (its exports folder
    apart), or that names a folder. Nothing is written and no folder is left made."""
    refusal = where_refused(path, project=project, roots=roots)
    if refusal is not None:
        raise DestinationRefusedError(refusal_words(refusal, path))
    if os.path.isdir(joined(path)):
        message = f"{path} is a folder, and a file is wanted. Name a file in it, or elsewhere."
        raise DestinationRefusedError(message)


def check_output_folder(path: Path, *, project: Path, roots: Sequence[Path]) -> None:
    """Refuse a folder that files would be written into inside a root or inside the project (its
    exports folder apart), or that is a file. Nothing is written and no folder is left made."""
    refusal = where_refused(path, project=project, roots=roots)
    if refusal is not None:
        raise DestinationRefusedError(refusal_words(refusal, path))
    target = joined(path)
    if os.path.exists(target) and not os.path.isdir(target):
        message = f"{path} is a file, and a folder is wanted. Name a folder, or a new one."
        raise DestinationRefusedError(message)


class ReadOnlyFileError(PermissionError):
    """An existing file is not writable, so it is not replaced, as a shell redirect would refuse."""


def refuse_read_only(paths: Sequence[Path]) -> None:
    """Raise :class:`ReadOnlyFileError` for the first of ``paths`` that exists and is not writable.

    The caller asks about every file a command is about to write before it writes any, so that a
    refusal leaves nothing half done.
    """
    for path in paths:
        if os.path.exists(path) and not os.access(path, os.W_OK):
            raise ReadOnlyFileError(errno.EACCES, "read-only", str(path))


def _incoming(path: Path) -> Path:
    """A temporary name for the file that will replace ``path``, beside it and not guessable:
    ``.<name>.<process>.<eight hex digits>.incoming`` (``storage.layout.is_project_name`` knows the
    shape, so one a crash leaves at a project's top is never read as a person's file)."""
    return path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.incoming")


def create_exclusive(
    path: Path, *, binary: bool = True, newline: str | None = None, mode: int = 0o666
) -> tuple[IO[Any], Path]:
    """Create the temporary file for ``path`` as a new file that follows no link, and open it.

    Beside ``path``, under a name no other writer uses (:func:`_incoming`), created exclusively (a
    name already taken, by a file or a link, is an error and is never opened, truncated or written
    through) and without following a link, with ``mode`` before the system's mask. A fixed
    temporary name opened with truncation would destroy whatever stood there, and would write
    through a link or a hard link into the file it led to. Every replacing write starts from this
    function. It returns the open handle and the temporary name.
    """
    incoming = _incoming(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(incoming, flags, mode)
    try:
        if binary:
            return os.fdopen(descriptor, "wb"), incoming
        return os.fdopen(descriptor, "w", encoding="utf-8", newline=newline), incoming
    except BaseException:
        os.close(descriptor)
        with contextlib.suppress(OSError):
            incoming.unlink()
        raise


@contextlib.contextmanager
def writing_atomically(
    path: Path, *, binary: bool = False, newline: str | None = None
) -> Iterator[IO[Any]]:
    """Open one of the engine's own files to write that replaces ``path`` whole, or not at all.

    The function writes a new file beside ``path`` (:func:`create_exclusive`), flushes it to disk,
    moves it onto ``path`` with ``os.replace`` and flushes the folder. A reader sees the whole
    previous file or the whole new one, and nothing standing at any name is written through. On an
    error the temporary file is removed and ``path`` is as it was. A file a person names is written
    with :func:`replacing`, which also refuses one that is read-only.
    """
    from ingence.storage.commit import fsync_directory

    handle, incoming = create_exclusive(path, binary=binary, newline=newline)
    try:
        with handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(incoming, path)
    except BaseException:
        with contextlib.suppress(OSError):
            incoming.unlink()
        raise
    # Windows opens no folder to flush it, so there the move is as durable as the system makes it
    # (``storage.commit.fsync_directory`` says why).
    fsync_directory(path.parent)


def write_atomically(path: Path, data: str | bytes) -> None:
    """Write ``data`` as the whole of the engine's own file ``path``, as :func:`writing_atomically`
    does: text as UTF-8, with the platform's line endings as ``write_text`` gives them."""
    with writing_atomically(path, binary=isinstance(data, bytes)) as handle:
        handle.write(data)


def open_appending(path: Path, *, binary: bool = False) -> IO[Any]:
    """Open one of the engine's own files to add to its end, made where it is not there yet.

    Opened without following a link standing at its name (``O_NOFOLLOW``, where the system has
    it), so a line is never added to the file a link leads to. The folder is the caller's to
    check (``storage.layout.owned_path``).
    """
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o666)
    try:
        if binary:
            return os.fdopen(descriptor, "ab")
        return os.fdopen(descriptor, "a", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


def _begin(path: Path, *, binary: bool, newline: str | None = None) -> tuple[IO[Any], Path, bool]:
    """Create the temporary file for a file a person names, ``path`` (:func:`create_exclusive`).

    Where ``path`` exists the temporary file is made readable by its owner alone until it has the
    permissions of ``path``. Where it does not, the file is made with the default permissions of the
    system. The function returns the open handle, the temporary name, and whether ``path`` exists.
    A file that exists and is not writable is not replaced (:class:`ReadOnlyFileError`). Nothing is
    made, and the file is as it was.
    """
    existed = os.path.exists(path)
    refuse_read_only((path,))
    handle, incoming = create_exclusive(
        path, binary=binary, newline=newline, mode=0o600 if existed else 0o666
    )
    return handle, incoming, existed


def _move_into_place(path: Path, incoming: Path, *, existed: bool) -> None:
    """Give the new file the permissions of the file it replaces, where there was one, and move it
    onto that file. A private file (owner only) stays private when it is written again."""
    if existed:
        with contextlib.suppress(OSError):
            shutil.copymode(path, incoming)
    os.replace(incoming, path)


@contextlib.contextmanager
def replacing(path: Path, *, newline: str | None = None) -> Iterator[TextIO]:
    """Open a text file to write that replaces ``path`` whole, or not at all.

    The file is written under a temporary name in ``path``'s own folder, created as a new file that
    follows no link, and moved onto ``path`` when the block ends without error. On an error the file
    is removed and ``path`` is as it was. Replacing ``path`` makes a new file of it: a hard link to
    it, or a link standing at it, is not written through. The new file has the permissions ``path``
    had, so a private file stays private.
    """
    handle, incoming, existed = _begin(path, binary=False, newline=newline)
    try:
        with handle:
            yield cast("TextIO", handle)
        _move_into_place(path, incoming, existed=existed)
    except BaseException:
        with contextlib.suppress(OSError):
            incoming.unlink()
        raise


def write_replacing(path: Path, data: str | bytes) -> None:
    """Write ``data`` as the whole of ``path`` the way :func:`replacing` does."""
    binary = isinstance(data, bytes)
    handle, incoming, existed = _begin(path, binary=binary)
    try:
        with handle:
            handle.write(data)
        _move_into_place(path, incoming, existed=existed)
    except BaseException:
        with contextlib.suppress(OSError):
            incoming.unlink()
        raise
