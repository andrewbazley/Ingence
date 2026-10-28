"""``ingence index --dry-run``: the pre-flight in the engine.

Built on synthetic trees with links written to foreign targets, as an archive leaves them: every
link's text characterised and never followed, no file opened, the rates and the metadata a census
would see, and nothing written anywhere. Every name here is invented.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from ingence.cli.main import app
from ingence.core.project import Project
from ingence.scan.dry_run import dry_run

if TYPE_CHECKING:
    from pathlib import Path

OLD = 1_577_836_800  # 2020-01-01


def _tree(base: Path) -> Path:
    root = base / "pool"
    (root / "kelt" / "tamen").mkdir(parents=True)
    (root / "vorn").mkdir()
    (root / "kelt" / "brisa_01.nd2").write_bytes(b"\x00" * 64)
    # Bytes, not text: Windows writes a text newline as two bytes, and the sizes are asserted.
    (root / "kelt" / "tamen" / "brisa_01_tracks.csv").write_bytes(b"x,y\n1,2\n")
    (root / "vorn" / "quolt.txt").write_bytes(b"")
    os.utime(root / "vorn" / "quolt.txt", (OLD, OLD))
    (root / "vorn" / ".DS_Store").write_bytes(b"x")
    try:
        (root / "kelt" / "brisa_02.nd2").symlink_to("/??/UNC/zeph-archive/lab/kelt/brisa_02.nd2")
        (root / "kelt" / "brisa_03.lif").symlink_to("//zeph-archive/lab/kelt/brisa_03.lif")
        (root / "vorn" / "near.csv").symlink_to("../kelt/tamen/brisa_01_tracks.csv")
    except OSError:
        pytest.skip("this platform cannot make a symbolic link without privileges")
    return root


def test_a_dry_run_reports_what_a_census_would_see_and_characterises_every_link(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    report = dry_run(root, exclude_globs=(".DS_Store",), workers=2)
    assert report["kinds"] == {"directory": 3, "file": 3, "symlink": 3}
    assert report["excluded"] == 1
    assert report["walk"]["stopped_by"] == "the whole tree"
    assert report["walk"]["entries"] == 9 and report["walk"]["entries_per_second"] > 0
    files = report["files"]
    assert (files["count"], files["zero_bytes"], files["total_bytes"]) == (3, 1, 72)
    assert files["modified_by_year"]["2020"] == 1
    assert files["extensions_top"] == {"nd2": 1, "csv": 1, "txt": 1}
    links = report["links"]
    assert links["written_as"] == {"network_share": 2, "relative": 1}
    assert links["archive_links_by_server_and_share"] == {"zeph-archive/lab": 2}
    assert links["archive_links_by_extension"] == {"nd2": 1, "lif": 1}
    assert links["share_of_files_and_links"] == 0.5


def test_a_dry_run_can_stop_early_and_says_so(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    report = dry_run(root, workers=1, limit=2)
    assert report["walk"]["stopped_by"] == "the limit of 2 entries"
    assert report["walk"]["entries"] >= 2


def test_the_command_writes_nothing_but_the_report_it_is_asked_for(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[root])

    def listing(base: Path) -> list[tuple[str, int, int]]:
        return sorted(
            (str(path.relative_to(base)), path.lstat().st_size, path.lstat().st_mtime_ns)
            for path in base.rglob("*")
        )

    before_root, before_project = listing(root), listing(project.path)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        app, ["index", "--dry-run", "-p", str(project.path), "--report", str(out)]
    )
    assert result.exit_code == 0, result.output
    # The report is a file written, so nothing but the folders the project reads is said unwritten.
    assert "Nothing was written" not in result.output
    assert "Ingence wrote nothing in your data folders" in result.output
    assert listing(root) == before_root, "the root is untouched"
    assert listing(project.path) == before_project, "no run is published"
    assert out.exists()


def test_a_dry_run_refuses_a_root_it_cannot_reach(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[root])
    root.rename(tmp_path / "gone")
    result = CliRunner().invoke(app, ["index", "--dry-run", "-p", str(project.path)])
    assert result.exit_code == 1


def test_the_command_says_what_it_found_in_plain_words(tmp_path: Path) -> None:
    """Counts with their nouns, sizes in kilobytes, links by where they point: no Python dicts."""
    root = _tree(tmp_path)
    project = Project.create(tmp_path / "project", created_at="t", roots=[root])
    result = CliRunner().invoke(app, ["index", "--dry-run", "-p", str(project.path)])
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert "{" not in said and "}" not in said, said
    assert "walked whole" in said
    assert "3 files, 3 folders, 3 links" in said
    # The hidden .DS_Store is left out, as a census leaves it out; the year now is the test's own.
    assert "files: 72 bytes, 1 empty, last changed in 2020 (1), " in said
    assert "left out, hidden names: 1" in said
    assert "links: 2 to another computer's share, 1 written from where it is" in said
    assert "median" not in said and "born after" not in said

    detailed = CliRunner().invoke(app, ["index", "--dry-run", "-p", str(project.path), "--verbose"])
    assert "a listing's median" in " ".join(detailed.output.split())
