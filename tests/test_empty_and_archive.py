"""An empty file beside its siblings is a real, empty output; an archive link is its own kind.

Two cases follow from how people store results:

- A tracking batch can hold empty files that are real output, from movies where the
  tracking or filtering settings found or saved nothing. Proximity decides: if there are other
  replicates from the same experiment, stored in the same folder or near it, the engine treats the
  file as a real file that is empty.
- Archived files that are links have a file type designation of their own. They are
  characterised on the first run and never opened.

The classes, over the grammar of names and link texts: an empty file whose name, but for its
numbers, a file holding bytes shares, in its folder or a folder beside it, is ``empty_output``; a
link whose text names another server's share is ``archive_link``, and the census records what the
text says -- the server, the share, the folder there, the name and its extension -- and a rebind
of the stored census says it again with the root gone. Every name here is invented.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import write_revision
from ingence.core.links import characterise_link_target, is_network_share
from ingence.core.project import Project
from ingence.infer.run import run_inference
from ingence.record.verdicts import VERDICTS, kind_for
from ingence.scan.census import LINK_TARGET_KEY, run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

OLD = 1_577_836_800  # 2020-01-01, before the date revision 4 names
ARCHIVED = "/??/UNC/vorn-archive/lab/kelt/brisa_01.nd2"


def _pool(base: Path) -> Path:
    pool = base / "pool"
    tracks = pool / "brisa tracking"
    tracks.mkdir(parents=True)
    (tracks / "Traj_cell1.csv").write_text("x,y\n1,2\n")
    (tracks / "Traj_cell2.csv").write_text("")  # a movie where nothing was found
    replicates = pool / "kelt replicates"
    for number, text in ((1, "x,y\n1,2\n"), (2, "")):
        (replicates / f"movie{number}").mkdir(parents=True)
        (replicates / f"movie{number}" / "spots.xml").write_text(text)
    # A tool writing one movie's tracks twice, and finding nothing: the table empty beside the
    # text, with no other table in the folder.
    twice = pool / "tamen tracks"
    twice.mkdir()
    (twice / "Traj_quolt 4.nd2_crop.tif.txt").write_text("frame x y\n1 2 3\n")
    (twice / "Traj_quolt 4.nd2_crop.tif.csv").write_text("")
    # A tracking batch over a session's movies: a brightfield movie held nothing to track, and its
    # table is empty beside the others', the names differing by a word.
    batch = pool / "zib batch"
    batch.mkdir()
    (batch / "Traj_zib gem-1.nd2_crop.csv").write_text("x,y\n1,2\n")
    (batch / "Traj_zib gem-2.nd2_crop.csv").write_text("x,y\n3,4\n")
    (batch / "Traj_zib bf-1.nd2_crop.csv").write_text("")
    lonely = pool / "vorn notes"
    lonely.mkdir()
    (lonely / "untitled.txt").write_text("")
    os.utime(lonely / "untitled.txt", (OLD, OLD))
    (lonely / "draft.txt").write_text("")
    (pool / "archived").mkdir()
    (pool / "archived" / "brisa_01.nd2").symlink_to(ARCHIVED)
    (pool / "archived" / "elsewhere.csv").symlink_to("/private/var/vorn/elsewhere.csv")
    return pool


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    base = tmp_path_factory.mktemp("empty-archive")
    try:
        pool = _pool(base)
    except OSError:
        pytest.skip("this platform cannot make a symbolic link without privileges")
    created = Project.create(base / "project", created_at="t", roots=[pool])
    write_revision(created.path)
    run_census(created, run_id="run_20260101T000001Z_111111111111", started_at="t")
    run_inference(created, run_id="run_20260101T000002Z_222222222222", started_at="t")
    return created


def _raised(project: Project) -> dict[str, set[str]]:
    with open_catalog(project.path) as catalog:
        return {
            str(path): set(raised)
            for path, raised in catalog.query(
                "SELECT e.relative_path, f.raised FROM findings f JOIN entries e "
                "ON e.entry_id = f.subject WHERE f.rule_id = 'content_unreadable'"
            ).fetchall()
        }


def test_an_empty_file_beside_a_sibling_holding_bytes_is_an_empty_output(project: Project) -> None:
    raised = _raised(project)
    assert raised["brisa tracking/Traj_cell2.csv"] == {"empty_output"}
    # Its sibling in a folder beside its own: replicates kept one to a folder.
    assert raised["kelt replicates/movie2/spots.xml"] == {"empty_output"}


def test_an_empty_table_beside_its_own_name_as_text_holding_bytes_is_an_empty_output(
    project: Project,
) -> None:
    """Version 4: the extension is set aside with the numbers."""
    assert _raised(project)["tamen tracks/Traj_quolt 4.nd2_crop.tif.csv"] == {"empty_output"}


def test_an_empty_table_of_a_batch_whose_other_tables_hold_data_is_an_empty_output(
    project: Project,
) -> None:
    """Version 5: a batch is known by its template, where the movies differ by a word."""
    assert _raised(project)["zib batch/Traj_zib bf-1.nd2_crop.csv"] == {"empty_output"}


def test_an_empty_replicate_among_replicates_holding_data_is_no_data_point(
    project: Project,
) -> None:
    with open_catalog(project.path) as catalog:
        concluded = {
            str(path)
            for (path,) in catalog.query(
                "SELECT e.relative_path FROM claims c JOIN entries e ON e.entry_id = c.subject "
                "WHERE c.rule_id = 'empty_replicate_is_no_data_point' "
                "AND c.object_reference = 'no_data_point' AND c.resolution_status = 'resolved'"
            ).fetchall()
        }
    assert {
        "brisa tracking/Traj_cell2.csv",
        "kelt replicates/movie2/spots.xml",
        "zib batch/Traj_zib bf-1.nd2_crop.csv",
    } <= concluded
    assert "vorn notes/untitled.txt" not in concluded
    assert "vorn notes/draft.txt" not in concluded
    assert not any(path.startswith("zib batch/Traj_zib gem") for path in concluded)


def test_an_empty_file_with_no_such_sibling_is_empty_or_old_and_empty(project: Project) -> None:
    raised = _raised(project)
    assert raised["vorn notes/untitled.txt"] == {"empty_and_old"}
    assert raised["vorn notes/draft.txt"] == {"empty"}


def test_a_link_to_another_server_s_share_is_an_archive_link(project: Project) -> None:
    raised = _raised(project)
    assert raised["archived/brisa_01.nd2"] == {"archive_link"}
    assert raised["archived/elsewhere.csv"] == {"link_outside_the_root"}


def test_an_archive_link_is_characterised_from_its_text_and_counts_as_its_format(
    project: Project,
) -> None:
    """The file it stands for, the server, the share and the folder there; never opened."""
    with open_catalog(project.path) as catalog:
        (text,) = catalog.query(
            "SELECT o.value_structure FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = 'brisa_01.nd2' AND o.key = ?",
            [LINK_TARGET_KEY],
        ).fetchone()
        formats = catalog.query(
            "SELECT c.object_reference FROM claims c JOIN entries e ON e.entry_id = c.subject "
            "WHERE e.name = 'brisa_01.nd2' AND c.rule_id = ?",
            ["format_from_extension_unless_contradicted"],
        ).fetchall()
    assert json.loads(text) == {
        "written_as": "network_share",
        "server": "vorn-archive",
        "share": "lab",
        "folder": "kelt",
        "name": "brisa_01.nd2",
        "extension": "nd2",
    }
    assert formats == [("image_format",)], "an archived ND2 is still an ND2"


def test_a_rebind_characterises_the_links_again_with_the_root_gone(
    project: Project, tmp_path: Path
) -> None:
    """Derived offline from the stored census (the root is not needed)."""
    copy = tmp_path / "copy"
    shutil.copytree(project.path, copy)
    root = project.identity.roots[0].mount_path
    moved = tmp_path / "unmounted"
    shutil.move(root, moved)
    try:
        rebound = run_census(
            Project.open(copy),
            run_id="run_20260101T000003Z_333333333333",
            started_at="t",
            rebind=True,
        )
        assert rebound.published
        with open_catalog(copy) as catalog:
            count = catalog.query(
                "SELECT count(*) FROM observations WHERE key = ?", [LINK_TARGET_KEY]
            ).fetchone()[0]
        assert count == 2
    finally:
        shutil.move(moved, root)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\\??\\UNC\\vorn-archive\\lab\\a\\b.lif", ("network_share", "vorn-archive", "lab", "lif")),
        ("\\\\?\\UNC\\vorn-archive\\lab\\c.czi", ("network_share", "vorn-archive", "lab", "czi")),
        ("\\\\vorn-archive\\lab\\d.tif", ("network_share", "vorn-archive", "lab", "tif")),
        ("//vorn-archive/lab/e.nd2", ("network_share", "vorn-archive", "lab", "nd2")),
        ("C:\\kelt\\f.nd2", ("drive", None, "C:", "nd2")),
        ("../kelt/g.csv", ("relative", None, None, "csv")),
    ],
)
def test_every_spelling_of_a_link_s_text_is_read(text: str, expected: tuple) -> None:
    found = characterise_link_target(text)
    assert (found["written_as"], found["server"], found["share"], found["extension"]) == expected
    assert is_network_share(text) is (expected[0] == "network_share")


def test_a_person_says_placeholder_or_empty_output_and_the_older_words_still_import() -> None:
    """The word stub is retired as the one offered: without a sharp definition it was a trap."""
    assert VERDICTS["placeholder"] is VERDICTS["stub"]
    assert VERDICTS["empty_output"] is VERDICTS["not_stub"]


@pytest.mark.parametrize(
    ("word", "raised", "decides"),
    [
        ("empty_output", "empty_output", "confirm"),
        ("empty_output", "empty_and_old", "reject"),
        ("empty_output", "empty", "confirm"),
        ("placeholder", "empty_and_old", "confirm"),
        ("placeholder", "empty_output", "reject"),
        ("archive_link", "archive_link", "confirm"),
        ("archive_link", "link_outside_the_root", "confirm"),
        ("archive_link", "empty_output", "reject"),
        ("not_stub", "empty_output", "confirm"),
        ("placeholder", None, "confirm"),
        ("empty_output", None, "reject"),
        ("correct", "empty_output", "confirm"),
    ],
)
def test_a_word_naming_what_a_file_is_decides_by_what_the_question_raised(
    word: str, raised: str | None, decides: str
) -> None:
    """An answer of "empty output" to a question raised as an empty output agrees with it.

    If the words decided the same way whatever was raised, a reviewer agreeing with an
    ``empty_output`` finding would record a rejection of it.
    """
    assert kind_for(word, raised=raised).value == decides
