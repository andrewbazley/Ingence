"""A link's chain is followed hop by hop, and never past the root.

A census reads nothing outside the roots it was given. A link whose own text points outside is read
from that text. Suppose the check read only the link's own text and then let one ``stat`` follow
the whole chain. A link inside the root that points at a link that leaves it would then be
resolved through the outside target. On a share that means a dead server path and its timeout
(``a_in -> b_out -> ../outside``). So each hop's text is read with ``readlink`` and tested
against the root, each folder on the way is looked at with ``lstat`` inside the root only, and a
chain longer than the bound (a loop) is unreadable, never chased.

Every name is invented.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import SymlinkState
from ingence.scan.census import LINK_HOPS, run_census, walk_root
from ingence.storage.catalog import open_catalog

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def _pool(base: Path) -> tuple[Path, Path]:
    pool = base / "pool"
    (pool / "zib").mkdir(parents=True)
    (pool / "zib" / "kelt_01.csv").write_text("a,b\n")
    outside = base / "outside"
    outside.mkdir()
    (outside / "vorn.csv").write_text("a,b\n")
    return pool, outside


def _states(
    pool: Path, spied: pytest.MonkeyPatch | None = None
) -> tuple[dict[str, SymlinkState], list[tuple[str, bool]]]:
    """Each entry's link state from a walk, and, spied, every path the walk alone looked at."""
    project = Project.create(pool.parent / "project", created_at="t", roots=[pool])
    seen = _spy(spied) if spied is not None else []
    entries = [
        entry
        for chunk in walk_root(project.identity.roots[0], settings=project.settings, run_id="r")
        for entry in chunk.entries
    ]
    looked_at = list(seen)
    if spied is not None:
        spied.undo()
    return {entry.name: entry.symlink_state for entry in entries}, looked_at


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Every path the census stats or lstats, and whether it followed links."""
    seen: list[tuple[str, bool]] = []
    real_stat, real_lstat = os.stat, os.lstat

    def stat(path, *args, **kwargs):
        seen.append((os.fspath(path), bool(kwargs.get("follow_symlinks", True))))
        return real_stat(path, *args, **kwargs)

    def lstat(path, *args, **kwargs):
        seen.append((os.fspath(path), False))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr("ingence.scan.census.os.stat", stat)
    monkeypatch.setattr("ingence.scan.census.os.lstat", lstat)
    return seen


def test_a_link_to_a_link_leaving_the_root_is_outside_and_nothing_outside_is_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, outside = _pool(tmp_path)
    (pool / "b_out").symlink_to("../outside/vorn.csv")
    (pool / "a_in").symlink_to("b_out")
    (pool / "zib" / "dir_out").symlink_to("../../outside", target_is_directory=True)
    (pool / "e_in").symlink_to("zib/dir_out/vorn.csv")  # leaves through a folder on the way
    states, looked_at = _states(pool, monkeypatch)
    assert states["b_out"] is SymlinkState.OUTSIDE_ROOT  # its own text leaves
    assert states["a_in"] is SymlinkState.OUTSIDE_ROOT  # at the second hop
    assert states["e_in"] is SymlinkState.OUTSIDE_ROOT  # at a folder link on the way
    # Nothing under the outside folder is looked at, by any path spelled there.
    assert not [
        path for path, _ in looked_at if Path(os.path.normpath(path)).is_relative_to(outside)
    ]
    # Nothing in the pool was stat-ed through its links (the engine's own imports stat source).
    in_pool = [(p, f) for p, f in looked_at if Path(os.path.normpath(p)).is_relative_to(pool)]
    assert not [path for path, follows in in_pool if follows]
    assert any(Path(path).name == "b_out" for path, _ in looked_at)  # a hop inside was read


def test_a_chain_inside_the_root_resolves_and_a_dangling_hop_is_broken(tmp_path: Path) -> None:
    pool, _ = _pool(tmp_path)
    (pool / "c2").symlink_to("zib/kelt_01.csv")
    (pool / "c1").symlink_to(pool / "c2")  # absolute, inside
    (pool / "zib" / "up").symlink_to("..", target_is_directory=True)
    (pool / "c3").symlink_to("zib/up/zib/kelt_01.csv")  # through a folder link inside
    (pool / "d2").symlink_to("zib/never_was.csv")
    (pool / "d1").symlink_to("d2")
    states, _ = _states(pool)
    assert states["c1"] is SymlinkState.RESOLVES
    assert states["c2"] is SymlinkState.RESOLVES
    assert states["c3"] is SymlinkState.RESOLVES
    assert states["d1"] is SymlinkState.BROKEN
    assert states["d2"] is SymlinkState.BROKEN


def test_a_loop_and_a_chain_longer_than_the_bound_are_unreadable(tmp_path: Path) -> None:
    pool, _ = _pool(tmp_path)
    (pool / "l1").symlink_to("l2")
    (pool / "l2").symlink_to("l1")
    (pool / "self").symlink_to("self")
    chain = LINK_HOPS + 5
    (pool / f"n{chain}").symlink_to("zib/kelt_01.csv")
    for number in range(chain):
        (pool / f"n{number}").symlink_to(f"n{number + 1}")
    states, _ = _states(pool)
    assert states["l1"] is SymlinkState.UNREADABLE
    assert states["l2"] is SymlinkState.UNREADABLE
    assert states["self"] is SymlinkState.UNREADABLE
    assert states["n0"] is SymlinkState.UNREADABLE  # more hops than the bound
    assert states[f"n{chain - 1}"] is SymlinkState.RESOLVES
    assert states[f"n{chain - LINK_HOPS + 1}"] is SymlinkState.RESOLVES  # exactly the bound
    assert states[f"n{chain - LINK_HOPS}"] is SymlinkState.UNREADABLE  # one hop more


def test_a_rebind_keeps_what_the_walk_found_for_a_chain(tmp_path: Path) -> None:
    """The rebind reads no link: it carries the walk's state, and a link's own outside text."""
    pool, _ = _pool(tmp_path)
    (pool / "b_out").symlink_to("../outside/vorn.csv")
    (pool / "a_in").symlink_to("b_out")
    (pool / "c1").symlink_to("zib/kelt_01.csv")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_census(project, run_id="run_20260101T000002Z_000000000002", started_at="t", rebind=True)
    for run in ("run_20260101T000001Z_000000000001", "run_20260101T000002Z_000000000002"):
        with open_catalog(project.path, run_id=run) as catalog:
            states = dict(
                catalog.query(
                    "SELECT name, symlink_state FROM entries WHERE entry_type = 'symlink'"
                ).fetchall()
            )
        assert states == {"a_in": "outside_root", "b_out": "outside_root", "c1": "resolves"}, run


def test_a_dotdot_after_a_folder_link_is_taken_from_where_the_link_leads(tmp_path: Path) -> None:
    """``zdeep2/..`` is one up from where ``zdeep2`` leads. Read as text, it would be broken."""
    pool, _ = _pool(tmp_path)
    (pool / "other" / "inner").mkdir(parents=True)
    (pool / "other" / "only.csv").write_text("a,b\n")
    (pool / "zib" / "zdeep2").symlink_to("../other/inner", target_is_directory=True)
    (pool / "zib" / "phys2").symlink_to("zdeep2/../only.csv")
    assert (pool / "zib" / "phys2").read_text() == "a,b\n"  # the system reads it
    states, _ = _states(pool)
    assert states["phys2"] is SymlinkState.RESOLVES


def test_a_link_to_a_file_written_as_a_folder_is_broken(tmp_path: Path) -> None:
    """The system refuses ``file.csv/``, so the census reads the link as broken."""
    pool, _ = _pool(tmp_path)
    (pool / "slash_file").symlink_to("zib/kelt_01.csv/")
    (pool / "slash_folder").symlink_to("zib/")
    states, _ = _states(pool)
    assert states["slash_file"] is SymlinkState.BROKEN
    assert states["slash_folder"] is SymlinkState.RESOLVES


def test_a_folder_already_looked_at_is_not_looked_at_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Links deep in one folder cost one lstat each, not one per folder above."""
    pool, _ = _pool(tmp_path)
    deep = pool / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    for number in range(20):
        (deep / f"kelt_{number:02d}.csv").write_text("x")
        (deep / f"to_{number:02d}").symlink_to(f"kelt_{number:02d}.csv")
    states, looked_at = _states(pool, monkeypatch)
    assert all(states[f"to_{number:02d}"] is SymlinkState.RESOLVES for number in range(20))
    by_the_chains = [
        path
        for path, _ in looked_at
        if Path(os.path.normpath(path)).is_relative_to(pool / "a")
        and (Path(path).name.startswith("kelt_") or Path(path).name in {*"abcdef"})
    ]
    assert len(by_the_chains) <= 20 + 6  # each file once, each folder on the way once


def test_windows_link_text_is_walked_as_outside_root_reads_it() -> None:
    """On Windows ``readlink`` gives an absolute link's text with ``\\\\?\\`` or
    ``\\??\\`` before it, which ``outside_root`` takes off. Taking the raw text would call
    every absolute link inside the root ``outside_root``. The test runs on every platform, with
    ``ntpath`` and a stub filesystem, because the link tests skip on Windows."""
    import stat as stat_module
    from types import SimpleNamespace

    from ingence.scan.census import follow_link_chain, outside_root

    root = "C:\\data\\pool"
    folders = {root, "C:\\data\\pool\\zib"}
    files = {"C:\\data\\pool\\zib\\kelt.csv"}

    def lstat(path: str) -> SimpleNamespace:
        if path in folders:
            return SimpleNamespace(st_mode=stat_module.S_IFDIR)
        if path in files:
            return SimpleNamespace(st_mode=stat_module.S_IFREG)
        raise FileNotFoundError(path)

    def leaves(link: str, target: str) -> bool:
        return outside_root(link, target, root, case_insensitive=True, windows=True)

    link = "C:\\data\\pool\\l"
    for target, expected in (
        ("zib\\kelt.csv", SymlinkState.RESOLVES),
        ("C:\\data\\pool\\zib\\kelt.csv", SymlinkState.RESOLVES),
        ("\\\\?\\C:\\data\\pool\\zib\\kelt.csv", SymlinkState.RESOLVES),
        ("\\??\\C:\\data\\pool\\zib\\kelt.csv", SymlinkState.RESOLVES),
        ("c:\\DATA\\Pool\\zib\\kelt.csv", SymlinkState.RESOLVES),
        ("\\??\\C:\\data\\pool\\zib\\gone.csv", SymlinkState.BROKEN),
        ("\\??\\C:\\data\\elsewhere\\kelt.csv", SymlinkState.OUTSIDE_ROOT),
        ("\\??\\UNC\\vorn\\share\\kelt.csv", SymlinkState.OUTSIDE_ROOT),
        ("..\\..\\x.csv", SymlinkState.OUTSIDE_ROOT),
    ):
        found = follow_link_chain(
            link,
            target,
            root,
            leaves=leaves,
            case_insensitive=True,
            windows=True,
            lstat=lstat,
            readlink=lambda path: (_ for _ in ()).throw(OSError(path)),
        )
        assert found is expected, target
