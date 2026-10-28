"""A dive does not hash a file on a network share where the setting says skip.

``hash.skip_remote_filesystems`` promises "never hash across a network share by default", so a
pass must tell a share from a local disk: ``digests_wanted`` takes an ``is_remote`` that every
caller passes. A dive asks, once for each root, what filesystem it lies on, and a root on a share
is not hashed where the setting says skip. A depth's proposal to skip applies to the modes that
hash every file, never to ``need`` (hash what comparing and deduplicating need, whatever the
depth), and a person's ``true`` skips everything on a share, ``need`` included.

No test reaches a real share: the dive's detector is injected, and the platform readers are read
against the test's own folder or a mount table written by the test.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.core.schema import RunRecord
from ingence.core.settings import HashMode, HashSettings, Settings
from ingence.scan import shares
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

_CENSUS = "run_20260101T000001Z_111111111111"
_DIVE = "run_20260101T000002Z_222222222222"


def _project(tmp_path: Path, **hashing: object) -> Project:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    body = b"vorn plate reading\n" * 40
    (pool / "kelt" / "vorn_a.dat").write_bytes(body)
    (pool / "kelt" / "vorn_b.dat").write_bytes(body)
    (pool / "kelt" / "zib.dat").write_bytes(b"zib only\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(hash=HashSettings(**hashing)),  # type: ignore[arg-type]
    )
    run_census(project, run_id=_CENSUS, started_at="t")
    return project


class _Share:
    """A detector that says every root is on an SMB share, and counts how often it was asked."""

    def __init__(self, kind: str | None = "smbfs") -> None:
        self.kind = kind
        self.asked: list[Path] = []

    def __call__(self, path: Path) -> str | None:
        self.asked.append(path)
        return self.kind


def _digests(project: Project) -> set[str]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT DISTINCT identity_method FROM observations "
            "WHERE identity_method IN ('blake3_quick', 'blake3_whole')"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _run(project: Project) -> RunRecord:
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    return record_from_row(row, RunRecord)


def _dive(project: Project, detector: object, profile: str = "standard") -> object:
    return run_deepen(
        project,
        run_id=_DIVE,
        started_at="t",
        profile=profile,
        budget_seconds=60.0,
        filesystem_type=detector,  # type: ignore[arg-type]
    )


def test_a_share_is_not_hashed_where_the_depth_proposes_skipping(tmp_path: Path) -> None:
    project = _project(tmp_path, mode=HashMode.FULL)
    share = _Share()
    report = _dive(project, share)
    assert _digests(project) == set()
    assert report.roots_on_shares == {"smbfs": 1}  # type: ignore[attr-defined]
    assert report.unhashed_on_shares == 3  # type: ignore[attr-defined]
    assert "depth" in (report.unhashed_on_shares_because or "")  # type: ignore[attr-defined]
    record = _run(project)
    assert (record.skip_remote_filesystems, record.skip_remote_filesystems_set_by) == (
        True,
        "profile",
    )


def test_the_share_is_asked_about_once_for_its_root_never_for_each_file(tmp_path: Path) -> None:
    project = _project(tmp_path, mode=HashMode.FULL)
    share = _Share()
    _dive(project, share)
    assert len(share.asked) == 1


def test_need_hashes_a_share_the_depth_proposes_skipping(tmp_path: Path) -> None:
    """``need`` is the default mode and copy by content is what a share dive is for: a depth's
    proposal to skip a share never withholds what comparing needs."""
    project = _project(tmp_path)
    report = _dive(project, _Share())
    # A sample of each file sharing a size, and the whole of the two whose samples agree.
    assert _digests(project) == {"blake3_quick", "blake3_whole"}
    assert report.roots_on_shares == {"smbfs": 1}  # type: ignore[attr-defined]
    assert report.unhashed_on_shares == 0  # type: ignore[attr-defined]
    record = _run(project)
    assert (record.skip_remote_filesystems, record.skip_remote_filesystems_set_by) == (
        True,
        "profile",
    )


def test_an_init_written_true_leaves_need_hashing_a_share(tmp_path: Path) -> None:
    """``init`` wrote ``true`` into every older settings file: it is not a person's choice."""
    project = _project(tmp_path, skip_remote_filesystems=True)
    report = _dive(project, _Share())
    assert _digests(project) == {"blake3_quick", "blake3_whole"}
    assert report.unhashed_on_shares == 0  # type: ignore[attr-defined]
    assert _run(project).skip_remote_filesystems_set_by == "profile"


def test_a_person_s_always_skips_need_on_a_share_too(tmp_path: Path) -> None:
    project = _project(tmp_path, skip_remote_filesystems="always")
    report = _dive(project, _Share())
    assert _digests(project) == set()
    assert report.unhashed_on_shares == 3  # type: ignore[attr-defined]
    assert "settings" in (report.unhashed_on_shares_because or "")  # type: ignore[attr-defined]
    record = _run(project)
    assert (record.skip_remote_filesystems, record.skip_remote_filesystems_set_by) == (
        True,
        "settings",
    )


def test_a_local_root_is_hashed_as_before(tmp_path: Path) -> None:
    project = _project(tmp_path)
    report = _dive(project, _Share("apfs"))
    assert "blake3_quick" in _digests(project)
    assert report.roots_on_shares == {}  # type: ignore[attr-defined]


def test_a_person_s_false_hashes_the_share_at_every_depth(tmp_path: Path) -> None:
    project = _project(tmp_path, mode=HashMode.FULL, skip_remote_filesystems=False)
    share = _Share()
    _dive(project, share)
    assert _digests(project) == {"blake3_whole"}
    # Nothing to decide about hashing. The read pool asks once for each root, to size the read
    # workers (at most eight on a share).
    assert len(share.asked) == len(set(share.asked)) == 1


def test_the_deep_depth_proposes_hashing_the_share(tmp_path: Path) -> None:
    project = _project(tmp_path, mode=HashMode.FULL)
    _dive(project, _Share(), profile="deep")
    assert _digests(project) == {"blake3_whole"}


def test_a_filesystem_that_cannot_be_named_is_read_as_local_and_said(tmp_path: Path) -> None:
    project = _project(tmp_path, mode=HashMode.FULL)

    def failing(path: Path) -> str | None:
        raise OSError("no such call here")

    report = _dive(project, failing)
    assert _digests(project) == {"blake3_whole"}
    assert report.roots_unnamed == 1  # type: ignore[attr-defined]
    assert report.unnamed_said is None  # type: ignore[attr-defined]


def test_a_person_s_always_on_a_root_that_cannot_be_named_is_said(tmp_path: Path) -> None:
    """Their choice could not be applied to a root read as local: the terminal and the journal
    say so, rather than the digests quietly appearing."""
    import json

    project = _project(tmp_path, skip_remote_filesystems="always")
    report = _dive(project, _Share(None))
    assert report.roots_unnamed == 1  # type: ignore[attr-defined]
    said = report.unnamed_said  # type: ignore[attr-defined]
    assert said is not None and "could not be named" in said
    with open_catalog(project.path) as catalog:
        (detail,) = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'hash.shares'"
        ).fetchone()
    assert json.loads(detail)["unnamed_said"] == said


# -- the platform readers -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("smbfs", True),
        ("SMB3", True),
        ("cifs", True),
        ("nfs", True),
        ("nfs4", True),
        ("afpfs", True),
        ("webdav", True),
        ("fuse.sshfs", True),
        ("drive_remote", True),
        ("lustre", True),
        ("gpfs", True),
        ("beegfs", True),
        ("ceph", True),
        ("glusterfs", True),
        ("fuse.rclone", True),
        ("fuse.gvfsd-fuse", True),
        ("9p", True),
        ("macfuse", True),
        ("osxfuse", True),
        ("apfs", False),
        ("ext4", False),
        ("exfat", False),
        ("fuse.kelt", False),
        (None, False),
    ],
)
def test_which_filesystem_types_are_shares(kind: str | None, expected: bool) -> None:
    assert shares.is_share(kind) is expected


def test_the_linux_mount_table_names_the_longest_mount_above_a_path(tmp_path: Path) -> None:
    table = tmp_path / "mountinfo"
    table.write_text(
        "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
        "40 22 0:50 / /mnt/kelt\\040share rw,relatime shared:9 - cifs //vorn/zib rw,vers=3.0\n"
        "41 22 0:51 / /mnt/kelt rw shared:10 - nfs4 trell:/export rw\n"
    )
    assert shares.linux_type("/mnt/kelt share/brisa/x.tif", mountinfo=table) == "cifs"
    assert shares.linux_type("/mnt/kelt/brisa", mountinfo=table) == "nfs4"
    assert shares.linux_type("/mnt/keltish", mountinfo=table) == "ext4"
    assert shares.linux_type("/home/zib", mountinfo=table) == "ext4"


def test_a_mount_stacked_on_another_at_one_point_is_the_later_one(tmp_path: Path) -> None:
    """An automounter mounts ``autofs`` at a point and the share over it at the same point: the
    later line is what a path there lies on."""
    table = tmp_path / "mountinfo"
    table.write_text(
        "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
        "35 22 0:31 / /mnt/vorn rw,relatime shared:22 - autofs systemd-1 rw,fd=40\n"
        "120 35 0:52 / /mnt/vorn rw,relatime shared:60 - nfs4 trell:/export rw,vers=4.2\n"
    )
    assert shares.linux_type("/mnt/vorn/kelt", mountinfo=table) == "nfs4"


@pytest.mark.skipif(sys.platform != "darwin", reason="statfs's type name is read on macOS")
def test_the_macos_reader_names_a_local_folder_s_filesystem(tmp_path: Path) -> None:
    kind = shares.darwin_type(tmp_path)
    assert kind
    assert not shares.is_share(kind)
    assert shares.darwin_type("/") in {"apfs", "hfs"}
