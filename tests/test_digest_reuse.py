"""A reused reading counts only a byte digest as a digest.

A walk asks of each unchanged file whether the last run took the byte digest that the current hash
settings ask for: the whole-file one, or the sampled one. The answer must not come from every
identity reading by the part of the file the reading covered. An image's difference hash (read
over the whole image) would say "a whole-file digest is held", and a text sketch or a header
signature would say "a sampled one is held". Neither is a digest of the bytes, and only a byte
digest proves or nominates a copy. Every name here is invented.
"""

from __future__ import annotations

import importlib.util
import shutil
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ingence.core.project import Project
from ingence.core.schema import ReuseDecision
from ingence.core.settings import HashMode, HashSettings, Settings
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.scan.reuse import PreviousRecord

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 8)]


def _image(path: Path, shade: int) -> None:
    """A small image the difference hash reads, of a size no other file in the pool has."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (24, 24), color=shade).save(path, format="PNG")


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    _image(pool / "zeph_scans" / "zeph_007.png", 90)
    (pool / "zeph_scans" / "kelt_notes.txt").write_text("vorn brisa pilot3 notes\n" * 9)
    return pool


def _project(tmp_path: Path, pool: Path, settings: Settings | None = None) -> Project:
    return Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)


def _identities(project: Project, name: str) -> dict[str, str]:
    """The identity readings the current run holds of one file, method by scope."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT o.identity_method, o.identity_scope FROM observations o "
            "JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE e.name = ? AND NOT e.is_deleted AND o.identity_method IS NOT NULL",
            [name],
        ).fetchall()
    return {str(method): str(scope) for method, scope in rows}


def _previous(project: Project, name: str) -> PreviousRecord:
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query(
            "SELECT entry_id FROM entries WHERE name = ? AND NOT is_deleted", [name]
        ).fetchall()
        return catalog.previous_records(project.identity.roots[0].root_id)[str(row[0])]


needs_imagehash = pytest.mark.skipif(
    importlib.util.find_spec("imagehash") is None,
    reason="the difference hash needs the images extra (imagehash)",
)


@needs_imagehash
def test_an_image_s_difference_hash_is_not_a_whole_file_digest(tmp_path: Path) -> None:
    """The reproduction: a dive under ``need`` reads an image of a size nothing else has.

    It takes the difference hash (scope ``whole``) and no byte digest, because nothing needs one
    to tell the file apart. The text file takes a sketch of its head (scope ``sampled``) and no
    byte digest either. The record the next walk reads must say no digest is held of either.
    """
    project = _project(tmp_path, _pool(tmp_path))
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    assert _identities(project, "zeph_007.png") == {"image_dhash": "whole"}
    assert _identities(project, "kelt_notes.txt") == {"text_minhash": "sampled"}

    image = _previous(project, "zeph_007.png")
    text = _previous(project, "kelt_notes.txt")
    assert not image.has_full_hash, "a difference hash read as a whole-file byte digest"
    assert not image.has_quick_hash
    assert not text.has_quick_hash, "a text sketch read as a sampled byte digest"
    assert not text.has_full_hash


@needs_imagehash
def test_a_byte_digest_still_counts_as_held(tmp_path: Path) -> None:
    """The other side: a whole-file mode's digest is held, and the next walk reuses the file."""
    pool = _pool(tmp_path)
    project = _project(tmp_path, pool, Settings(hash=HashSettings(mode=HashMode.FULL)))
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    assert _identities(project, "zeph_007.png") == {
        "blake3_whole": "whole",
        "image_dhash": "whole",
    }
    assert _previous(project, "zeph_007.png").has_full_hash
    walked = run_census(project, run_id=RUNS[2], started_at="t")
    assert walked.counts.reuse[ReuseDecision.REUSE] == 2


def test_a_walk_after_a_dive_on_a_share_does_not_count_an_image_as_hashed(
    tmp_path: Path,
) -> None:
    """Where the class reached a person: the run record's count of reused files.

    A person asks for whole-file digests; the dive leaves a share unhashed, as the standard depth
    proposes. Neither file holds a byte digest, so the next walk finds neither record complete;
    the image was counted reused because its difference hash read as the digest.
    """
    pool = _pool(tmp_path)
    project = _project(tmp_path, pool, Settings(hash=HashSettings(mode=HashMode.FULL)))
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(
        project,
        run_id=RUNS[1],
        started_at="t",
        budget_seconds=60,
        filesystem_type=lambda _path: "smbfs",
    )
    assert "blake3_whole" not in _identities(project, "zeph_007.png")
    walked = run_census(project, run_id=RUNS[2], started_at="t")
    assert walked.counts.reuse[ReuseDecision.REUSE] == 0
    assert walked.counts.reuse[ReuseDecision.PREVIOUS_INCOMPLETE] == 2


@needs_imagehash
def test_a_copy_of_an_image_read_only_by_its_difference_hash_is_found_on_the_next_dive(
    tmp_path: Path,
) -> None:
    """The harm the class was suspected of: a digest skipped, and a copy unfound.

    The image was read once, by its difference hash alone. A byte-for-byte copy then appears in
    another folder; the next walk and dive must take both files' digests and the copy rule must
    claim the pair.
    """
    pool = _pool(tmp_path)
    project = _project(tmp_path, pool)
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    assert _identities(project, "zeph_007.png") == {"image_dhash": "whole"}

    (pool / "zeph_copies").mkdir()
    shutil.copyfile(pool / "zeph_scans" / "zeph_007.png", pool / "zeph_copies" / "zeph_007.png")
    run_census(project, run_id=RUNS[2], started_at="t")
    run_deepen(project, run_id=RUNS[3], started_at="t", budget_seconds=60)
    run_inference(project, run_id=RUNS[4], started_at="t")

    with open_catalog(project.path) as catalog:
        digested = catalog.query(
            "SELECT e.relative_path FROM observations o JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE o.identity_method = 'blake3_whole' AND NOT e.is_deleted ORDER BY 1"
        ).fetchall()
        claimed = catalog.query(
            "SELECT e.relative_path FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'identical_by_content_digest' "
            "AND c.resolution_status = 'resolved' ORDER BY 1"
        ).fetchall()
    pair = [("zeph_copies/zeph_007.png",), ("zeph_scans/zeph_007.png",)]
    assert digested == pair
    assert claimed == pair
