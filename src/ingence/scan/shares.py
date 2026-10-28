"""Say whether a root lies on a network share, asking once for each root.

The setting ``hash.skip_remote_filesystems`` decides whether a dive hashes across a network
share. Hashing a share is slow and hammers it for every other user on it. To obey the setting, a
dive must tell a share from a local disk. It asks here what filesystem each root lies on, once
for each root and never for each file. A root on a share is not hashed where the setting says
skip (``scan.profiles.remote_choice``). A depth's proposal withholds only what the modes that
hash every file ask, never the digests that ``need`` asks for. A person's ``always`` withholds
every digest on a share, ``need``'s included. The values ``null`` and ``true`` leave the choice to
the depth. Older settings files hold ``true``, so a ``true`` is not read as a person's choice.

How each platform is asked, reading no file:

- macOS: ``statfs(2)``'s ``f_fstypename`` for the root (``smbfs``, ``nfs``, ``afpfs``,
  ``webdav``, ``apfs``...).
- Linux: the filesystem type of the longest mount point above the root in
  ``/proc/self/mountinfo`` (``cifs``, ``smb3``, ``nfs4``, ``fuse.sshfs``, ``ext4``...).
- Windows: ``GetDriveTypeW`` on the root's drive or UNC share, named ``drive_remote`` and the
  like.
- Anything else, or a call that fails: no name. The root is read as local (so it is
  hashed) and counted as unnamed, so the run says it could not tell.

Each root gets one answer. A share mounted beneath a local root is judged by the root. The
types read as shares are :data:`SHARE_TYPES`, a parameter of :func:`is_share`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import posixpath
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "SHARE_TYPES",
    "darwin_type",
    "filesystem_type",
    "is_share",
    "linux_type",
    "name_roots",
]

#: The filesystem types a network share is mounted as, compared case-folded: SMB and CIFS, NFS,
#: Apple's AFP, WebDAV, SSH through FUSE, NetWare, Windows' own name for a remote drive, the
#: cluster filesystems a laboratory's storage is served over (Lustre, GPFS, BeeGFS, Ceph,
#: GlusterFS), rclone and GNOME's virtual filesystem through FUSE, 9P, and macOS's FUSE (whose
#: mounts are nearly always remote).
SHARE_TYPES: frozenset[str] = frozenset(
    {
        "smbfs",
        "cifs",
        "smb2",
        "smb3",
        "nfs",
        "nfs4",
        "afpfs",
        "webdav",
        "davfs",
        "fuse.davfs",
        "sshfs",
        "fuse.sshfs",
        "ncpfs",
        "drive_remote",
        "lustre",
        "gpfs",
        "beegfs",
        "ceph",
        "glusterfs",
        "fuse.rclone",
        "fuse.gvfsd-fuse",
        "9p",
        "macfuse",
        "osxfuse",
    }
)

#: ``MFSTYPENAMELEN`` and ``MAXPATHLEN`` in macOS's ``sys/mount.h``.
_TYPE_NAME_BYTES = 16
_PATH_BYTES = 1024


class _DarwinStatfs(ctypes.Structure):
    """``struct statfs`` as macOS lays it out with 64-bit inodes (every macOS since 10.6)."""

    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * _TYPE_NAME_BYTES),
        ("f_mntonname", ctypes.c_char * _PATH_BYTES),
        ("f_mntfromname", ctypes.c_char * _PATH_BYTES),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def is_share(kind: str | None, *, share_types: frozenset[str] = SHARE_TYPES) -> bool:
    """Whether a filesystem type names a network share. No name is no share."""
    return kind is not None and kind.casefold() in share_types


def darwin_type(path: Path | str) -> str | None:
    """The filesystem type macOS's ``statfs`` names for ``path``, or None where it cannot."""
    library = ctypes.util.find_library("c")
    if library is None:
        return None
    libc = ctypes.CDLL(library, use_errno=True)
    # On Intel the 64-bit-inode layout has its own symbol; on Apple silicon it is the only one.
    call = getattr(libc, "statfs$INODE64", None) or libc.statfs
    call.argtypes = [ctypes.c_char_p, ctypes.POINTER(_DarwinStatfs)]
    call.restype = ctypes.c_int
    found = _DarwinStatfs()
    if call(os.fsencode(os.fspath(path)), ctypes.byref(found)) != 0:
        return None
    return found.f_fstypename.decode("utf-8", errors="replace") or None


def _unescaped(field: str) -> str:
    r"""A mount point as ``mountinfo`` writes it, its ``\040``-style octal escapes read."""
    out: list[str] = []
    index = 0
    while index < len(field):
        if field[index] == "\\" and field[index + 1 : index + 4].isdigit():
            out.append(chr(int(field[index + 1 : index + 4], 8)))
            index += 4
        else:
            out.append(field[index])
            index += 1
    return "".join(out)


def linux_type(path: Path | str, *, mountinfo: Path | str = "/proc/self/mountinfo") -> str | None:
    """The type of the longest mount point above ``path`` in a Linux mount table, or None.

    Where two mounts stack at one point, the later line wins (an automounter's ``autofs``,
    then the share mounted over it). A path there lies on what was mounted last.
    """
    try:
        lines = Path(mountinfo).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    # A Linux mount table names POSIX paths: resolved through links on a POSIX system, and only
    # normalised elsewhere (Windows' own path rules turn ``/mnt/x`` into a drive's path and match
    # nothing but the root).
    raw = os.fspath(path)
    wanted = os.path.realpath(raw) if os.name == "posix" else posixpath.normpath(raw)
    best: tuple[int, str] | None = None
    for line in lines:
        before, separator, after = line.partition(" - ")
        fields, rest = before.split(), after.split()
        if not separator or len(fields) < 5 or not rest:
            continue
        mounted = _unescaped(fields[4])
        above = mounted == "/" or wanted == mounted or wanted.startswith(mounted.rstrip("/") + "/")
        if above and (best is None or len(mounted) >= best[0]):
            best = (len(mounted), rest[0])
    return None if best is None else best[1]


def _windows_type(path: Path | str) -> str | None:
    """Windows' kind of drive for ``path``'s drive or UNC share, named, or None."""
    windll = getattr(ctypes, "windll", None)  # present on Windows alone
    drive = os.path.splitdrive(os.fspath(path))[0]
    if windll is None or not drive:
        return None
    kind = windll.kernel32.GetDriveTypeW(drive.rstrip("\\") + "\\")
    return {
        2: "drive_removable",
        3: "drive_fixed",
        4: "drive_remote",
        5: "drive_cdrom",
        6: "drive_ramdisk",
    }.get(int(kind))


def filesystem_type(path: Path | str) -> str | None:
    """The filesystem type ``path`` lies on, as this platform names it, or None where it cannot."""
    system = sys.platform  # read once, so every platform's branch is checked on every one
    if system == "darwin":
        return darwin_type(path)
    if system.startswith("linux"):
        return linux_type(path)
    if system == "win32":
        return _windows_type(path)
    return None


def name_roots(
    roots: Mapping[str, Path], *, detector: Callable[[Path], str | None] | None = None
) -> dict[str, str | None]:
    """Each root's filesystem type, asked once for each root; None where it could not be named.

    A detector that raises names nothing, and the root is read as local: the caller counts it.
    """
    asked = detector or filesystem_type
    named: dict[str, str | None] = {}
    for root_id, path in sorted(roots.items()):
        try:
            named[root_id] = asked(path)
        except Exception:  # an unnamed filesystem is counted, never a failed dive
            named[root_id] = None
    return named
