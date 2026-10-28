"""An archive's link to a file that exists elsewhere in the pool is a copy of it.

An archive leaves a link where a file used to be. A link whose name matches a real file elsewhere
in the pool is claimed a copy of that file. Where the link's text gives a path, the link's folders
must also match that file's. The claim rests on name and path and never opens either file.
Every name is invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str = "kelt data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _copies(tmp_path: Path) -> dict[str, str]:
    pool = tmp_path / "pool"
    _write(pool / "kelt run" / "kelt_vorn_01.nd2")
    _write(pool / "kelt run" / "kelt_vorn_02.nd2")
    # Two real files of one name: the link's path says which one it stands for.
    _write(pool / "zib day1" / "zib_movie.nd2")
    _write(pool / "zib day2" / "zib_movie.nd2")
    backup = pool / "backup"
    backup.mkdir(parents=True)
    (backup / "kelt_vorn_01.nd2").symlink_to("/??/UNC/vorn-archive/lab/kelt run/kelt_vorn_01.nd2")
    (backup / "zib_movie.nd2").symlink_to("/??/UNC/vorn-archive/lab/zib day2/zib_movie.nd2")
    (backup / "brisa_only_here.nd2").symlink_to("/??/UNC/vorn-archive/lab/brisa_only_here.nd2")
    # One real file of its name, but the link says it came from another folder: not a copy.
    _write(pool / "trell day" / "trell_01.nd2")
    (backup / "trell_01.nd2").symlink_to("/??/UNC/vorn-archive/lab/sorn day/trell_01.nd2")
    # A link outside the root that is not an archive's names nothing held here as a copy.
    (backup / "kelt_vorn_02.nd2").symlink_to("/private/var/elsewhere/kelt_vorn_02.nd2")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_000000000001", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_000000000002", started_at="t")
    with open_catalog(created.path) as catalog:
        rows = catalog.query(
            "SELECT e.relative_path, c.object_reference FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'archive_link_copy_of_a_file' AND c.resolution_status = 'resolved'"
        ).fetchall()
    return dict(rows)


def test_an_archive_link_is_a_copy_of_the_file_of_its_name_and_path(tmp_path: Path) -> None:
    key = _copies(tmp_path)
    assert key["backup/kelt_vorn_01.nd2"] == key["kelt run/kelt_vorn_01.nd2"]
    assert key["backup/zib_movie.nd2"] == key["zib day2/zib_movie.nd2"]  # its path decides
    assert "zib day1/zib_movie.nd2" not in key
    assert "backup/brisa_only_here.nd2" not in key  # nothing of its name is held here
    assert "backup/kelt_vorn_02.nd2" not in key  # not an archive's link
    assert "backup/trell_01.nd2" not in key  # its folder disagrees with the only file's
    assert "kelt run/kelt_vorn_02.nd2" not in key
    assert len(set(key.values())) == 2
