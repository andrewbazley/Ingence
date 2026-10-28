"""What each operating system gives a walk: a birth time, file numbers, case, links.

This module says, per system, what a directory listing and a ``stat`` carry. It also makes the one
``stat`` a walk makes per entry. The census reads each system's facts here instead of testing
``sys.platform`` or ``os.name`` itself, so the engine behaves the same across systems.

=========  ===============================  ==================  ===================  ===========
system     birth time                       file numbers        case (a default)     links
=========  ===============================  ==================  ===================  ===========
macOS      ``stat`` (``st_birthtime``)      in the listing      insensitive          symbolic
Linux      ``statx``, where the filesystem  in the listing      sensitive            symbolic
           keeps one (not NFS, some FUSE)
Windows    ``stat`` (``st_birthtime``,      not in the          insensitive          symbolic,
           Python 3.12)                     listing                                  junctions
=========  ===============================  ==================  ===================  ===========

- Birth time on Linux is read with ``statx`` (glibc 2.28 or later, through ``ctypes``, because
  Python 3.12's ``stat`` gives none). The call takes the place of the ``lstat`` the census already
  makes per entry, so no call is added. It uses the flags that ``lstat`` uses in the kernel: no
  link followed, no automount triggered, and a call interrupted by a signal is tried again. Where
  the filesystem keeps no birth time (NFS, some FUSE mounts), ``statx`` says so in its mask and
  the value is absent, never invented. Where ``statx`` itself is missing (an older C library, a
  sandbox that refuses the call), the walk falls back to ``lstat`` and states no birth time.
- File numbers: Windows' directory listing gives no file number and no device (``DirEntry.stat``
  sets both to zero, which the census stores as none). On Windows a move is therefore known by its
  birth time, never by its number.
- Case is a default only. Each root's own names decide it at ``init``.
- The change time is the creation time on Windows, and a change to the file's metadata elsewhere.
  A folder's listed times are a stale cache on Windows, so the walk calls ``stat`` on a folder
  again there.

The module reads what each system gives once per process. ``gives(platform)`` answers for any
system, so the tests cover every system's facts on every system. The moves (``scan/moves.py``)
read none of this. They read what two censuses recorded, never the machine they run on, so every
conclusion can be made again from the Index.
"""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "STATX_BTIME",
    "Gives",
    "StatxResult",
    "System",
    "gives",
    "lstat_entry",
    "lstat_path",
    "makedev",
    "parse_statx",
    "system_of",
]


class System(StrEnum):
    """The operating systems whose differences a walk needs to know."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    OTHER = "other"


class BirthTime(StrEnum):
    """How a file's birth time is read on a system."""

    #: In ``stat`` itself (``st_birthtime``): macOS, the BSDs, Windows from Python 3.12.
    STAT = "stat"
    #: With ``statx``, where the filesystem keeps one: Linux.
    STATX = "statx"
    #: Not at all.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Gives:
    """What one system gives a walk."""

    system: System
    birth_time: BirthTime
    #: Whether a directory listing's ``stat`` carries the file's number and its device.
    file_numbers_in_listing: bool
    #: Whether ``st_ctime`` is the creation time (Windows) rather than a metadata change.
    ctime_is_creation: bool
    #: Whether a listing's times for a folder are a cache that may be stale (Windows).
    folder_listing_stale: bool
    #: The case a new root is likely to have: a default only, each root's names decide.
    case_insensitive_by_default: bool
    #: The kinds of link the system makes.
    links: tuple[str, ...]


_GIVES: dict[System, Gives] = {
    System.MACOS: Gives(
        system=System.MACOS,
        birth_time=BirthTime.STAT,
        file_numbers_in_listing=True,
        ctime_is_creation=False,
        folder_listing_stale=False,
        case_insensitive_by_default=True,
        links=("symbolic",),
    ),
    System.LINUX: Gives(
        system=System.LINUX,
        birth_time=BirthTime.STATX,
        file_numbers_in_listing=True,
        ctime_is_creation=False,
        folder_listing_stale=False,
        case_insensitive_by_default=False,
        links=("symbolic",),
    ),
    System.WINDOWS: Gives(
        system=System.WINDOWS,
        birth_time=BirthTime.STAT,
        file_numbers_in_listing=False,
        ctime_is_creation=True,
        folder_listing_stale=True,
        case_insensitive_by_default=True,
        links=("symbolic", "junction"),
    ),
    System.OTHER: Gives(
        system=System.OTHER,
        birth_time=BirthTime.STAT,
        file_numbers_in_listing=True,
        ctime_is_creation=False,
        folder_listing_stale=False,
        case_insensitive_by_default=False,
        links=("symbolic",),
    ),
}


def system_of(platform: str) -> System:
    """The system a ``sys.platform`` value names."""
    if platform == "darwin":
        return System.MACOS
    if platform.startswith("linux"):
        return System.LINUX
    # Cygwin's Python is POSIX (``os.name`` is "posix"), so it does not count as Windows here.
    if platform == "win32":
        return System.WINDOWS
    return System.OTHER


def gives(platform: str | None = None) -> Gives:
    """What a system gives a walk: this one, or the one a ``sys.platform`` value names."""
    return _GIVES[system_of(sys.platform if platform is None else platform)]


# -- statx ------------------------------------------------------------------------------------

#: ``statx``'s arguments (``linux/stat.h``, ``fcntl.h``).
AT_FDCWD = -100
AT_SYMLINK_NOFOLLOW = 0x100
#: Never trigger an automount. The kernel's own ``lstat`` always adds this flag, and so does
#: glibc's ``statx``-based ``fstatat``. Without it a bare ``statx`` mounts an autofs or systemd
#: automount point it is asked about, where the ``lstat`` it stands in for recorded the point and
#: mounted nothing.
AT_NO_AUTOMOUNT = 0x800
AT_STATX_SYNC_AS_STAT = 0x0000
STATX_BASIC_STATS = 0x07FF
STATX_BTIME = 0x0800


class _StatxTimestamp(ctypes.Structure):
    _fields_ = (
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    )


class _Statx(ctypes.Structure):
    """``struct statx`` as the kernel fills it: 256 bytes, the same on every architecture."""

    _fields_ = (
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        ("spare2", ctypes.c_uint64 * 14),
    )


def makedev(major: int, minor: int) -> int:
    """A device number from its two halves, as glibc composes ``st_dev`` on Linux."""
    return (
        ((major & 0x00000FFF) << 8)
        | ((major & 0xFFFFF000) << 32)
        | (minor & 0x000000FF)
        | ((minor & 0xFFFFFF00) << 12)
    )


def _ns(stamp: _StatxTimestamp) -> int:
    return int(stamp.tv_sec) * 1_000_000_000 + int(stamp.tv_nsec)


class StatxResult:
    """What ``statx`` said about one entry, read the way the census reads an ``os.stat_result``.

    The result carries the fields a ``stat`` carries. It carries ``st_birthtime_ns`` only where the
    filesystem keeps a birth time (``STATX_BTIME`` in the mask). Otherwise the value is absent,
    never a zero.
    """

    __slots__ = (
        "st_atime_ns",
        "st_birthtime_ns",
        "st_blksize",
        "st_blocks",
        "st_ctime_ns",
        "st_dev",
        "st_gid",
        "st_ino",
        "st_mode",
        "st_mtime_ns",
        "st_nlink",
        "st_rdev",
        "st_size",
        "st_uid",
    )

    def __init__(self, found: _Statx) -> None:
        self.st_mode = int(found.stx_mode)
        self.st_ino = int(found.stx_ino)
        self.st_dev = makedev(int(found.stx_dev_major), int(found.stx_dev_minor))
        self.st_rdev = makedev(int(found.stx_rdev_major), int(found.stx_rdev_minor))
        self.st_nlink = int(found.stx_nlink)
        self.st_uid = int(found.stx_uid)
        self.st_gid = int(found.stx_gid)
        self.st_size = int(found.stx_size)
        self.st_blocks = int(found.stx_blocks)
        self.st_blksize = int(found.stx_blksize)
        self.st_atime_ns = _ns(found.stx_atime)
        self.st_mtime_ns = _ns(found.stx_mtime)
        self.st_ctime_ns = _ns(found.stx_ctime)
        if found.stx_mask & STATX_BTIME:
            self.st_birthtime_ns = _ns(found.stx_btime)

    @property
    def st_mtime(self) -> float:
        return self.st_mtime_ns / 1_000_000_000

    @property
    def st_ctime(self) -> float:
        return self.st_ctime_ns / 1_000_000_000

    @property
    def st_atime(self) -> float:
        return self.st_atime_ns / 1_000_000_000

    def __getattr__(self, name: str) -> Any:
        # A slot never set (the birth time a filesystem does not keep) reads as absent.
        if name == "st_birthtime_ns":
            return None
        raise AttributeError(name)


def parse_statx(buffer: bytes) -> StatxResult:
    """A ``struct statx`` as the kernel wrote it, read (the parsing tested on every system)."""
    if len(buffer) < ctypes.sizeof(_Statx):
        message = f"a statx structure is {ctypes.sizeof(_Statx)} bytes, not {len(buffer)}"
        raise ValueError(message)
    return StatxResult(_Statx.from_buffer_copy(buffer[: ctypes.sizeof(_Statx)]))


class _StatxCall:
    """``statx`` from the C library, looked up once, or None where there is none to call."""

    def __init__(self) -> None:
        self.function: Any = None
        self.looked = False

    def get(self) -> Any:
        if not self.looked:
            self.looked = True
            if system_of(sys.platform) is System.LINUX:
                try:
                    library = ctypes.CDLL(None, use_errno=True)
                    function = library.statx
                except (OSError, AttributeError):
                    function = None
                if function is not None:
                    function.argtypes = (
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_uint,
                        ctypes.POINTER(_Statx),
                    )
                    function.restype = ctypes.c_int
                self.function = function
        return self.function


_CALL = _StatxCall()

#: ``statx`` missing from the kernel: the walk uses ``lstat`` for the rest of the process.
_MISSING = errno.ENOSYS
#: ``statx`` refused by a sandbox (an older container's filter answers so): ``lstat`` answers
#: this call, and gives the path's own error where there is one.
_REFUSED = errno.EPERM


def _statx(path: str) -> StatxResult | None:
    """``statx`` of one path without following a link, or None where the call is unavailable."""
    function = _CALL.get()
    if function is None:
        return None
    found = _Statx()
    spelled = os.fsencode(path)
    while True:
        code = function(
            AT_FDCWD,
            spelled,
            AT_SYMLINK_NOFOLLOW | AT_NO_AUTOMOUNT | AT_STATX_SYNC_AS_STAT,
            STATX_BASIC_STATS | STATX_BTIME,
            ctypes.byref(found),
        )
        # A signal during the call: tried again, as Python's own stat does (PEP 475).
        if code == 0 or ctypes.get_errno() != errno.EINTR:
            break
    if code != 0:
        number = ctypes.get_errno()
        if number == _MISSING:
            _CALL.function = None
            return None
        if number == _REFUSED:
            return None
        raise OSError(number, os.strerror(number), path)
    return StatxResult(found)


def lstat_entry(item: os.DirEntry[str]) -> os.stat_result:
    """The one ``stat`` a walk makes of a listed entry, without following a link: ``statx`` on
    Linux, for the birth time, and the listing's own ``stat`` everywhere else. A
    :class:`StatxResult` carries every field the census reads of a ``stat_result``."""
    read = _statx(item.path)
    return cast("os.stat_result", read) if read is not None else item.stat(follow_symlinks=False)


def lstat_path(path: Path) -> os.stat_result:
    """The same for a path the walk was given (a root), not one it listed."""
    read = _statx(os.fspath(path))
    return cast("os.stat_result", read) if read is not None else path.lstat()
