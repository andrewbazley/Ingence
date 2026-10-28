"""What each operating system gives a walk, said in one place.

``core/platform.py`` says per system what a listing and a ``stat`` carry, and makes the one
``stat`` a walk makes per entry: on Linux with ``statx``, so a birth time is read where the
filesystem keeps one. The structure's parsing is tested on every system. What each system gives is
tested on that system and skipped elsewhere. Every name is invented.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

from ingence.core.outcomes import TimeSource
from ingence.core.platform import (
    STATX_BTIME,
    System,
    gives,
    lstat_entry,
    lstat_path,
    makedev,
    parse_statx,
    system_of,
)

SECOND = 1_000_000_000


def _statx_bytes(*, mask: int, times: dict[str, tuple[int, int]], dev: tuple[int, int]) -> bytes:
    """A ``struct statx`` as the kernel lays it out (``linux/stat.h``), with invented values, in
    the machine's own byte order as the kernel writes it (``=``, not ``<``: big-endian Linux)."""
    head = struct.pack(
        "=IIQIIIHHQQQQ",
        mask,
        4096,  # blksize
        0,  # attributes
        3,  # nlink
        501,  # uid
        20,  # gid
        0o100644,  # mode: a regular file
        0,
        424242,  # ino
        1234,  # size
        8,  # blocks
        0,  # attributes_mask
    )
    stamps = b"".join(
        struct.pack("=qIi", *times[name], 0) for name in ("atime", "btime", "ctime", "mtime")
    )
    tail = struct.pack("=IIII", 0, 0, *dev) + bytes(112)
    buffer = head + stamps + tail
    assert len(buffer) == 256
    return buffer


_TIMES = {
    "atime": (1_700_000_300, 5),
    "btime": (1_700_000_000, 123_456_789),
    "ctime": (1_700_000_200, 7),
    "mtime": (1_700_000_100, 999_999_999),
}


def test_the_statx_structure_is_read_field_by_field_on_every_system() -> None:
    read = parse_statx(_statx_bytes(mask=0x0FFF, times=_TIMES, dev=(8, 1)))
    assert (read.st_mode, read.st_ino, read.st_size, read.st_blocks) == (0o100644, 424242, 1234, 8)
    assert (read.st_nlink, read.st_uid, read.st_gid) == (3, 501, 20)
    assert read.st_mtime_ns == 1_700_000_100 * SECOND + 999_999_999
    assert read.st_ctime_ns == 1_700_000_200 * SECOND + 7
    assert read.st_atime_ns == 1_700_000_300 * SECOND + 5
    assert read.st_birthtime_ns == 1_700_000_000 * SECOND + 123_456_789
    assert read.st_dev == 2049  # major 8, minor 1, as Linux's stat gives it


def test_a_birth_time_the_filesystem_does_not_keep_is_absent_never_invented() -> None:
    """NFS and some FUSE mounts keep no birth time: statx leaves it out of its mask."""
    from ingence.scan.census import _creation_time

    read = parse_statx(_statx_bytes(mask=0x07FF & ~STATX_BTIME, times=_TIMES, dev=(8, 1)))
    assert getattr(read, "st_birthtime_ns", None) is None
    assert _creation_time(read) == (None, TimeSource.UNAVAILABLE)  # type: ignore[arg-type]
    kept = parse_statx(_statx_bytes(mask=0x0FFF, times=_TIMES, dev=(8, 1)))
    assert _creation_time(kept) == (  # type: ignore[arg-type]
        1_700_000_000 * SECOND + 123_456_789,
        TimeSource.BIRTHTIME,
    )


def test_a_short_structure_is_refused_in_words() -> None:
    with pytest.raises(ValueError, match="256 bytes"):
        parse_statx(bytes(100))


def test_device_numbers_are_composed_as_linux_composes_them() -> None:
    assert makedev(8, 1) == 2049
    assert makedev(0, 0) == 0
    assert makedev(259, 3) == (259 << 8) | 3
    # Past twelve bits of major and eight of minor, glibc's wider layout.
    assert makedev(0x1234, 0x5678) == (
        ((0x1234 & 0xFFF) << 8) | ((0x1234 & ~0xFFF) << 32) | 0x78 | ((0x5678 & ~0xFF) << 12)
    )
    if sys.platform.startswith("linux"):
        for major, minor in ((8, 1), (259, 3), (0x1234, 0x5678)):
            assert makedev(major, minor) == os.makedev(major, minor)


def test_each_system_says_what_it_gives() -> None:
    assert system_of("darwin") is System.MACOS
    assert system_of("linux") is System.LINUX
    assert system_of("win32") is System.WINDOWS
    # Cygwin's Python is POSIX (``os.name`` is "posix"): its change time is no creation time.
    assert system_of("cygwin") is System.OTHER
    assert system_of("freebsd14") is System.OTHER
    mac, linux, windows = gives("darwin"), gives("linux"), gives("win32")
    assert (mac.birth_time, linux.birth_time, windows.birth_time) == ("stat", "statx", "stat")
    assert (mac.file_numbers_in_listing, linux.file_numbers_in_listing) == (True, True)
    assert windows.file_numbers_in_listing is False
    assert windows.ctime_is_creation and not mac.ctime_is_creation and not linux.ctime_is_creation
    assert windows.folder_listing_stale and not mac.folder_listing_stale
    assert mac.case_insensitive_by_default and windows.case_insensitive_by_default
    assert not linux.case_insensitive_by_default
    assert "junction" in windows.links and "junction" not in linux.links


def _one(tmp_path: Path) -> tuple[Path, os.DirEntry[str]]:
    (tmp_path / "kelt").mkdir(parents=True)
    made = tmp_path / "kelt" / "zib_trell.csv"
    made.write_text("a,b\n1,2\n", encoding="utf-8")
    with os.scandir(tmp_path / "kelt") as listing:
        (item,) = list(listing)
    return made, item


def test_the_walk_s_stat_carries_what_lstat_carries_on_every_system(tmp_path: Path) -> None:
    made, item = _one(tmp_path)
    read, plain = lstat_entry(item), os.lstat(made)
    for field in ("st_mode", "st_size", "st_mtime_ns"):
        assert getattr(read, field) == getattr(plain, field), field
    if gives().file_numbers_in_listing:
        assert (read.st_ino, read.st_dev) == (plain.st_ino, plain.st_dev)
    rooted = lstat_path(tmp_path)
    assert rooted.st_dev == os.lstat(tmp_path).st_dev


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="statx is Linux's")
def test_on_linux_the_walk_reads_statx_and_a_birth_time_where_one_is_kept(tmp_path: Path) -> None:
    from ingence.core.platform import StatxResult

    made, item = _one(tmp_path)
    read = lstat_entry(item)
    assert isinstance(read, StatxResult), "glibc 2.28 or later gives statx"
    plain = os.lstat(made)
    for field in ("st_mode", "st_ino", "st_dev", "st_nlink", "st_size", "st_blocks"):
        assert getattr(read, field) == getattr(plain, field), field
    for field in ("st_mtime_ns", "st_ctime_ns"):
        assert getattr(read, field) == getattr(plain, field), field
    born = getattr(read, "st_birthtime_ns", None)
    if born is not None:  # this filesystem keeps one (ext4, XFS, btrfs, tmpfs)
        assert 0 < born <= read.st_ctime_ns + SECOND


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's stat carries the birth time")
def test_on_macos_the_walk_s_stat_carries_the_birth_time(tmp_path: Path) -> None:
    _, item = _one(tmp_path)
    read = lstat_entry(item)
    assert isinstance(read, os.stat_result)
    assert getattr(read, "st_birthtime", None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' listing gives no file number")
def test_on_windows_the_listing_gives_no_file_number(tmp_path: Path) -> None:
    _, item = _one(tmp_path)
    read = lstat_entry(item)
    assert not read.st_ino
    assert not gives().file_numbers_in_listing


def test_a_census_records_a_birth_time_wherever_the_system_and_filesystem_keep_one(
    tmp_path: Path,
) -> None:
    """One answer on every system: the census's creation time is a birth time wherever the
    layer read one, and absent (never a change time dressed as one) where none is kept."""
    from ingence.core.project import Project
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    made, item = _one(tmp_path / "pool_parent")
    pool = made.parent
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT created_ns, created_time_source FROM entries WHERE name = 'zib_trell.csv'"
        ).fetchone()
    assert row is not None
    kept = getattr(lstat_entry(item), "st_birthtime_ns", None) or getattr(
        lstat_entry(item), "st_birthtime", None
    )
    if kept:
        assert row[1] == TimeSource.BIRTHTIME.value
        assert row[0]
    else:
        assert (row[0], row[1]) == (None, TimeSource.UNAVAILABLE.value)


def _fake_statx(answer: bytes | int):
    """A stand-in for the C library's ``statx``: fills the structure from ``answer``, or fails
    with that error number, so the Linux path is exercised on every system."""
    import ctypes

    calls: list[tuple[int, bytes, int, int]] = []

    def statx(dirfd: int, path: bytes, flags: int, mask: int, out: object) -> int:
        calls.append((dirfd, path, flags, mask))
        if isinstance(answer, int):
            ctypes.set_errno(answer)
            return -1
        target = out._obj  # type: ignore[attr-defined]  # what ctypes.byref points at
        ctypes.memmove(ctypes.addressof(target), answer, len(answer))
        return 0

    return statx, calls


def _calling(monkeypatch: pytest.MonkeyPatch, function: object) -> None:
    from ingence.core import platform

    monkeypatch.setattr(platform._CALL, "looked", True)
    monkeypatch.setattr(platform._CALL, "function", function)


def test_the_linux_call_s_answer_is_the_walk_s_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.core.platform import AT_SYMLINK_NOFOLLOW, StatxResult

    made, item = _one(tmp_path)
    statx, calls = _fake_statx(_statx_bytes(mask=0x0FFF, times=_TIMES, dev=(8, 1)))
    _calling(monkeypatch, statx)
    read = lstat_entry(item)
    assert isinstance(read, StatxResult)
    assert read.st_birthtime_ns == 1_700_000_000 * SECOND + 123_456_789
    (dirfd, path, flags, mask) = calls[0]
    assert (dirfd, path) == (-100, os.fsencode(str(made)))
    assert flags & AT_SYMLINK_NOFOLLOW, "a link is never followed"
    # The kernel's own lstat never triggers an automount (AT_NO_AUTOMOUNT, which glibc's
    # statx-based fstatat passes too). A walk listing an autofs or systemd automount point
    # records the point and mounts nothing.
    assert flags & 0x800, "an automount point is never mounted by a walk"
    assert mask & STATX_BTIME


def test_a_kernel_without_statx_falls_back_to_lstat_for_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    from ingence.core import platform

    _, item = _one(tmp_path)
    statx, calls = _fake_statx(errno.ENOSYS)
    _calling(monkeypatch, statx)
    assert isinstance(lstat_entry(item), os.stat_result)
    assert platform._CALL.function is None
    assert len(calls) == 1


def test_a_sandbox_refusing_statx_is_answered_by_lstat_call_by_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    from ingence.core import platform

    _, item = _one(tmp_path)
    statx, _ = _fake_statx(errno.EPERM)
    _calling(monkeypatch, statx)
    assert isinstance(lstat_entry(item), os.stat_result)
    assert platform._CALL.function is statx


def test_a_path_s_own_error_is_raised_as_lstat_would_raise_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    _, item = _one(tmp_path)
    statx, _ = _fake_statx(errno.ENOENT)
    _calling(monkeypatch, statx)
    with pytest.raises(FileNotFoundError):
        lstat_entry(item)


def test_the_preflight_reads_birth_times_through_the_same_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ingence index --dry-run`` walks as a census does, so it counts the birth times the
    census would record: Linux's from ``statx`` too (here a stand-in, on every system)."""
    from ingence.scan.dry_run import dry_run

    made, _ = _one(tmp_path)
    statx, calls = _fake_statx(_statx_bytes(mask=0x0FFF, times=_TIMES, dev=(8, 1)))
    _calling(monkeypatch, statx)
    said = dry_run(made.parent, workers=1)
    assert calls, "the pre-flight's one stat per entry is the layer's"
    assert said["files"]["with_birth_time"] == 1
    # Born at the time the stand-in states, before its modification time.
    assert said["files"]["born_after_modified"] == 0


def test_a_signal_during_statx_is_retried_as_lstat_retries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call interrupted by a signal (EINTR) is tried again, as Python's own stat does (PEP 475),
    never dropped as an unreadable entry."""
    import ctypes
    import errno

    from ingence.core.platform import StatxResult

    _, item = _one(tmp_path)
    filled, _ = _fake_statx(_statx_bytes(mask=0x0FFF, times=_TIMES, dev=(8, 1)))
    tries: list[int] = []

    def interrupted_once(*arguments: object) -> int:
        tries.append(1)
        if len(tries) == 1:
            ctypes.set_errno(errno.EINTR)
            return -1
        return filled(*arguments)

    _calling(monkeypatch, interrupted_once)
    assert isinstance(lstat_entry(item), StatxResult)
    assert len(tries) == 2
