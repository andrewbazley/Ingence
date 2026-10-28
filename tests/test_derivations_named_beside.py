"""Derivations a file's name gives from the files beside it.

Each is found from its own evidence, before any stage is read, and each is a proposal:

1. A name holding another file's whole name, extension included, is made from that file,
   whatever the file is. An image viewer names what it makes from ``zeph_007.tif`` as
   ``zeph_007.tif - C=0.tif``, and the movie need not be an instrument's own format.
2. A table named for a series beside it, with a word saying it holds results, summarises the
   series: one pair from each member of the series to the table.
3. An annotation named for an image beside it is made from that image, whatever the image's
   stage: ``vol07_labels.nii`` from ``vol07.nii``.
4. Two files of one name in sibling folders are related, directed where the folders' own words
   say which holds the source and which the product, and undirected where they say nothing.

Every name here is invented.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from ingence.core.project import Project
from ingence.rulebook.load import load_revisions
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.resolve import effective_rulebook
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.infer.run import InferenceReport

load_builtins()

CENSUS = "run_20260101T000001Z_111111111111"
FIRST = "run_20260101T000002Z_222222222222"
NAMED_FOR = "derivation_named_for_an_acquisition"
#: 1 March 2020 at noon UTC, as seconds since the epoch: one instant for every file unless a test
#: says otherwise, so no clock orders a pair.
NOON = 1_583_064_000


def _write(pool: Path, files: dict[str, str], times: dict[str, int] | None = None) -> None:
    for relative, text in files.items():
        path = pool / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        at = (times or {}).get(relative, NOON)
        os.utime(path, (at, at))


def _run(tmp_path: Path, files: dict[str, str]) -> tuple[Project, InferenceReport]:
    """A census and inference over an invented tree, the shipped book in force."""
    from ingence.infer.run import run_inference

    pool = tmp_path / "pool"
    _write(pool, files)
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=CENSUS, started_at="2026-01-01T00:00:01Z")
    report = run_inference(
        project,
        run_id=FIRST,
        started_at="2026-01-01T00:00:02Z",
        rulebook=effective_rulebook(load_revisions()),
    )
    return project, report


def _candidates(project: Project) -> list[tuple[str, str, str, str, list[str]]]:
    """Each candidate as (rule, source path, target path, primitive, support names)."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.rule_id, s.relative_path, t.relative_path, c.primitive, c.support_json "
            "FROM candidates c JOIN entries s ON s.entry_id = c.source_id "
            "JOIN entries t ON t.entry_id = c.target_id ORDER BY 1, 2, 3"
        ).fetchall()
    return [
        (rule, source, target, primitive, [item["name"] for item in json.loads(support)])
        for rule, source, target, primitive, support in rows
    ]


def _pairs(project: Project, primitive: str = "derives") -> set[tuple[str, str]]:
    return {
        (source, target) for _, source, target, held, _ in _candidates(project) if held == primitive
    }


# ---------------------------------------------------------------------------
# 1. A name holding another file's whole name, extension included.
# ---------------------------------------------------------------------------


def test_an_image_viewer_s_channel_names_link_to_their_movie(tmp_path: Path) -> None:
    """The viewer appends to the movie's whole name, extension included, and a tool prefixes its
    own word: each analysis file is proposed from the movie, one pair each, movie to file."""
    project, _ = _run(
        tmp_path,
        {
            "raw/zeph_007.nd2": "x",
            "analysis/Traj_zeph_007.nd2 - C=0-1.csv": "a,b\n1,2\n",
            "analysis/zeph_007.nd2 - C=0-3.tif": "y",
        },
    )

    named = [row for row in _candidates(project) if row[0] == NAMED_FOR]
    assert [(source, target) for _, source, target, _, _ in named] == [
        ("raw/zeph_007.nd2", "analysis/Traj_zeph_007.nd2 - C=0-1.csv"),
        ("raw/zeph_007.nd2", "analysis/zeph_007.nd2 - C=0-3.tif"),
    ]
    assert {primitive for _, _, _, primitive, _ in named} == {"derives"}


def test_a_name_holding_a_movie_s_whole_name_links_to_it_when_the_movie_is_no_acquisition(
    tmp_path: Path,
) -> None:
    """A movie in a format anything writes (a TIFF stack) is no instrument's acquisition, and the
    whole name held, extension included, was evidence only where it reached one: so a folder of
    channel splits and tracks named for TIFF movies proposed nothing. The whole name is the
    evidence on its own."""
    project, _ = _run(
        tmp_path,
        {
            "movies/zeph_007.tif": "x",
            "movies/zeph_008.tif": "x",
            "analysis/Traj_zeph_007.tif - C=0-1.csv": "a,b\n1,2\n",
            "analysis/zeph_007.tif - C=0.tif": "y",
            "analysis/MAX_zeph_008.tif - C=1.tif": "y",
        },
    )

    named = [row for row in _candidates(project) if row[0] == NAMED_FOR]
    assert {(source, target) for _, source, target, _, _ in named} == {
        ("movies/zeph_007.tif", "analysis/Traj_zeph_007.tif - C=0-1.csv"),
        ("movies/zeph_007.tif", "analysis/zeph_007.tif - C=0.tif"),
        ("movies/zeph_008.tif", "analysis/MAX_zeph_008.tif - C=1.tif"),
    }
    supports = {name for _, _, _, _, support in named for name in support}
    assert "named_for_its_whole_file_name" in supports
    assert "named_for_the_acquisition" not in supports


def test_a_short_acquisition_name_held_whole_with_its_extension_links(tmp_path: Path) -> None:
    """``kelt.lif`` is too short to be matched by its stem alone (``01.nd2`` names nothing), and
    the acquisition was left out of its own group though a file held its whole name, extension
    included, which is long enough: the series exported from it was proposed from nothing."""
    project, _ = _run(
        tmp_path,
        {
            "raw/kelt.lif": "x",
            "analysis/kelt.lif - Series003 - C=0.tif": "y",
        },
    )

    assert _pairs(project) == {("raw/kelt.lif", "analysis/kelt.lif - Series003 - C=0.tif")}


# ---------------------------------------------------------------------------
# 3. An annotation named for an image beside it.
# ---------------------------------------------------------------------------


def test_an_annotation_named_for_an_image_beside_it_is_made_from_the_image(
    tmp_path: Path,
) -> None:
    """A volume that is no instrument's acquisition and its labels, and an image and its mask: the
    derivation wanted an acquisition at the source, so neither formed anything. The annotation word
    is read from the Dictionary's senses, and the image's stage is never asked."""
    project, _ = _run(
        tmp_path,
        {
            "vol07.nii": "v",
            "vol07_labels.nii": "l",
            "cellA.tif": "c",
            "cellA_mask.png": "m",
        },
    )

    assert _pairs(project) == {
        ("vol07.nii", "vol07_labels.nii"),
        ("cellA.tif", "cellA_mask.png"),
    }
    supports = {name for _, _, _, _, support in _candidates(project) for name in support}
    assert "an_annotation_named_for_its_image" in supports


def test_an_annotation_word_standing_between_two_annotation_senses_or_in_the_singular_counts(
    tmp_path: Path,
) -> None:
    """``annotation`` binds the annotation role and the annotated transformation equally, and
    both mean an annotation; ``label`` and ``masks`` are the Dictionary's spellings too."""
    project, _ = _run(
        tmp_path,
        {
            "zib.tif": "z",
            "zib_annotation.tif": "a",
            "kelt07.tif": "k",
            "kelt07_label.png": "l",
            "masks_vorn3.tif": "m",
            "vorn3.tif": "v",
        },
    )

    assert _pairs(project) == {
        ("zib.tif", "zib_annotation.tif"),
        ("kelt07.tif", "kelt07_label.png"),
        ("vorn3.tif", "masks_vorn3.tif"),
    }


def test_a_word_that_is_no_annotation_beside_an_image_forms_nothing(tmp_path: Path) -> None:
    """Two outputs of one image stay siblings unless their own evidence joins them.

    A word that does not mean an annotation names no derivation, and an annotation beside no
    image of its name forms nothing."""
    project, _ = _run(
        tmp_path,
        {
            "vol07.nii": "v",
            "vol07_brisa.nii": "b",
            "kelt_labels.nii": "l",
        },
    )

    assert _pairs(project) == set()


# ---------------------------------------------------------------------------
# 2. A results table named for the series beside it.
# ---------------------------------------------------------------------------

SUMMARY = "derivation_summarising_a_series"


def _series(count: int, stem: str = "zeph") -> dict[str, str]:
    return {f"{stem}_{index:03d}.nd2": "x" for index in range(1, count + 1)}


def test_a_results_workbook_is_proposed_as_made_from_the_movies_beside_it(tmp_path: Path) -> None:
    """Ten movies and a workbook named for their shared stem with a word saying it holds results:
    one pair from each movie to the workbook, and none between two movies."""
    project, report = _run(tmp_path, {**_series(10), "zeph_results.xlsx": "r"})

    summarised = [row for row in _candidates(project) if row[0] == SUMMARY]
    assert {(source, target) for _, source, target, _, _ in summarised} == {
        (f"zeph_{index:03d}.nd2", "zeph_results.xlsx") for index in range(1, 11)
    }
    assert {primitive for _, _, _, primitive, _ in summarised} == {"derives"}
    assert {name for _, _, _, _, support in summarised for name in support} >= {
        "a_table_named_for_its_series"
    }
    tally = {tally.rule_id: tally for tally in report.proposals}[SUMMARY]
    assert tally.formed == tally.supported == 10
    assert tally.truncated == 0


def test_a_series_longer_than_the_bound_keeps_every_pair(tmp_path: Path) -> None:
    """Forty movies: each movie's one counterpart is the workbook, so the bound on a subject's
    candidates, 25, never cuts a supported pair."""
    project, report = _run(tmp_path, {**_series(40), "zeph_summary.csv": "s"})

    summarised = [row for row in _candidates(project) if row[0] == SUMMARY]
    assert len(summarised) == 40
    assert {tally.rule_id: tally for tally in report.proposals}[SUMMARY].truncated == 0


def test_a_table_whose_word_says_no_results_or_beside_no_series_forms_nothing(
    tmp_path: Path,
) -> None:
    """A word that says nothing about results (``zeph_brisa.xlsx``), a results table beside a
    series of another stem, and two numbered files that are a pair rather than a series."""
    project, _ = _run(
        tmp_path,
        {
            **_series(10),
            "zeph_brisa.xlsx": "b",
            "kelt_results.xlsx": "r",
            "vorn_001.nd2": "x",
            "vorn_002.nd2": "x",
            "vorn_results.xlsx": "r",
        },
    )

    assert [row for row in _candidates(project) if row[0] == SUMMARY] == []


# ---------------------------------------------------------------------------
# 4. Files of one name in sibling folders.
# ---------------------------------------------------------------------------

BESIDE = "derivation_of_one_name_in_sibling_folders"
RELATED_BESIDE = "related_by_one_name_in_sibling_folders"


def _beside(project: Project) -> list[tuple[str, str, str, str]]:
    return [
        (rule, source, target, primitive)
        for rule, source, target, primitive, _ in _candidates(project)
        if rule in (BESIDE, RELATED_BESIDE)
    ]


def test_an_image_and_its_mask_of_one_name_in_sibling_folders_are_directed(tmp_path: Path) -> None:
    project, _ = _run(tmp_path, {"images/a.tif": "1", "masks/a.tif": "2"})

    assert _beside(project) == [(BESIDE, "images/a.tif", "masks/a.tif", "derives")]


def test_raw_images_and_ground_truth_of_one_name_are_directed(tmp_path: Path) -> None:
    project, _ = _run(tmp_path, {"rawimages/a.tif": "1", "groundtruth/a.tif": "2"})

    assert _beside(project) == [(BESIDE, "rawimages/a.tif", "groundtruth/a.tif", "derives")]


def test_one_name_in_sibling_folders_whose_words_give_no_direction_is_related(
    tmp_path: Path,
) -> None:
    project, _ = _run(tmp_path, {"left/a.tif": "1", "right/a.tif": "2"})

    # Proposed once, from the end whose identifier sorts first: neither is the source.
    (found,) = _beside(project)
    assert (found[0], found[3]) == (RELATED_BESIDE, "concerns")
    assert {found[1], found[2]} == {"left/a.tif", "right/a.tif"}


def test_fifty_images_and_their_labels_are_fifty_directed_pairs(tmp_path: Path) -> None:
    files = {f"train/images/{index:03d}.png": "i" for index in range(1, 51)}
    files.update({f"train/labels/{index:03d}.png": "l" for index in range(1, 51)})
    project, report = _run(tmp_path, files)

    assert sorted(_beside(project)) == [
        (BESIDE, f"train/images/{index:03d}.png", f"train/labels/{index:03d}.png", "derives")
        for index in range(1, 51)
    ]
    tally = {tally.rule_id: tally for tally in report.proposals}[BESIDE]
    assert tally.truncated == 0


def test_one_name_in_folders_that_are_not_siblings_or_in_one_folder_forms_nothing(
    tmp_path: Path,
) -> None:
    """The identity of one name widens from one folder to sibling folders only.

    It never widens to the whole tree.

    A name every sample's folder holds is that layout's name, not one thing held twice. Two
    folders whose names agree but for a number are replicates of one layout, which groups are
    never made of. Two dated experiments each holding a file of one name are two experiments,
    whose copies the copy rules read. A README in each of two folders is each folder's own."""
    files = {"images/a.tif": "1", "kelt/masks/a.tif": "2"}
    files.update({f"sample{index}/image.tif": "s" for index in range(1, 9)})
    files.update({"day1/vorn.tif": "d", "day2/vorn.tif": "d"})
    files.update({"jorv/README.txt": "about jorv", "quen/README.txt": "about quen"})
    files.update(
        {
            "runs/2024-03-05_kelt/brisa_19.csv": "a,b\n1,2\n",
            "runs/2024-03-06_vorn/brisa_19.csv": "a,b\n1,2\n",
        }
    )
    project, _ = _run(tmp_path, files)

    assert _beside(project) == []


# ---------------------------------------------------------------------------
# Further cases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", ["nd2", "tif"])
def test_a_whole_name_never_links_two_dated_experiments(tmp_path: Path, extension: str) -> None:
    """A channel split is not proposed from another day's movie.

    A split in one day's experiment, named for a movie only the next day's experiment holds, is
    not proposed from that other day's movie. A whole name is looked for in the folders beside a
    file's only where the file lies beneath no experiment."""
    project, _ = _run(
        tmp_path,
        {
            f"days/2024-03-05_kelt/zeph_007.{extension} - C=0.tif": "split",
            "days/2024-03-05_kelt/kelt_notes.txt": "n",
            "days/2024-03-05_kelt/zeph_009.tif": "m9",
            f"days/2024-03-06_kelt/zeph_007.{extension}": "movie of another day",
            "days/2024-03-06_kelt/zeph_010.tif": "m10",
        },
    )

    assert [row for row in _candidates(project) if row[0] == NAMED_FOR] == []


def test_folders_that_differ_only_in_a_split_or_a_condition_relate_nothing(
    tmp_path: Path,
) -> None:
    """Train, test and val number their images alike, as do control and treated, and WT and KO.

    Each is a different image. Three people's folders each holding a results table and an analysis
    script share names that identify nothing."""
    files: dict[str, str] = {}
    for split in ("train", "test", "val"):
        for index in range(1, 7):
            files[f"split/ds/{split}/{index:04d}.png"] = f"{split}{index}"
    for condition in ("control", "treated"):
        for index in range(1, 5):
            files[f"cond/exp/{condition}/cell_{index:02d}.tif"] = f"{condition}{index}"
    for condition in ("WT", "KO"):
        for index in range(1, 4):
            files[f"cond2/exp/{condition}/img_{index:03d}.tif"] = f"{condition}{index}"
    for person in ("kelt", "vorn", "zib"):
        files[f"people/lab/{person}/results.csv"] = f"x\n{person}\n"
        files[f"people/lab/{person}/analysis.m"] = f"% {person}\n"
    project, _ = _run(tmp_path, files)

    assert _beside(project) == []


def test_one_field_in_two_channel_folders_is_still_related(tmp_path: Path) -> None:
    project, _ = _run(tmp_path, {"ch/proj/GFP/field_01.tif": "g", "ch/proj/RFP/field_01.tif": "r"})

    (found,) = _beside(project)
    assert (found[0], found[3]) == (RELATED_BESIDE, "concerns")


def test_a_split_beside_its_movie_in_each_sample_folder_compares_within_that_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a per-sample layout the pass does not grow with the square of the samples.

    A file's own folder is looked in first, and the folders beside it only where it holds none.
    Looking for every split's whole name in every sample's folder and sorting the matches would
    grow with the square of the samples."""
    import ingence.infer.experiment_places as places

    compared = {"count": 0}
    original = places._shared

    def counting(first, second):  # type: ignore[no-untyped-def]
        compared["count"] += 1
        return original(first, second)

    monkeypatch.setattr(places, "_shared", counting)
    files: dict[str, str] = {}
    for index in range(60):
        folder = f"plate/s{index:05d}"
        files[f"{folder}/image.tif"] = f"i{index}"
        files[f"{folder}/image.tif - C=0.tif"] = f"c0{index}"
        files[f"{folder}/image.tif - C=1.tif"] = f"c1{index}"
    project, _ = _run(tmp_path, files)

    pairs = _pairs(project)
    assert len(pairs) == 120
    assert all(source.rsplit("/", 1)[0] == target.rsplit("/", 1)[0] for source, target in pairs)
    assert compared["count"] <= 2 * len(files)


def test_a_backup_named_for_a_file_is_never_made_from_it(tmp_path: Path) -> None:
    """A backup, an original or an old copy holds its file's whole name and forms no derivation.

    The copy rules read such files. A derivation would propose the backup as processed from the
    file, and an original backwards."""
    project, _ = _run(
        tmp_path,
        {
            "bak/proj/data/zeph_table.csv": "a\n1\n",
            "bak/proj/data/zeph_table.csv.bak": "a\n0\n",
            "bak/proj/data/kelt_fit.py": "x=1\n",
            "bak/proj/data/kelt_fit.py.orig": "x=0\n",
            "bak/proj/scripts/track_vorn.py": "y=1\n",
            "bak/proj/old/track_vorn.py.bak": "y=0\n",
            "bak/proj/old/track_vorn.py.old": "y=-1\n",
            "acq/proj/zeph_019.nd2": "n",
            "acq/proj/zeph_019.nd2.bak": "b",
        },
    )

    assert [row for row in _candidates(project) if row[0] == NAMED_FOR] == []


def test_a_split_in_a_subfolder_links_to_the_movie_in_its_parent_folder(tmp_path: Path) -> None:
    """The movie in a project folder and the viewer's split in its analysis subfolder."""
    project, _ = _run(
        tmp_path,
        {
            "fiji/proj/zeph_017.tif": "movie",
            "fiji/proj/analysis/zeph_017.tif - C=0.tif": "split",
        },
    )

    assert _pairs(project) == {
        ("fiji/proj/zeph_017.tif", "fiji/proj/analysis/zeph_017.tif - C=0.tif")
    }


def test_raw_and_processed_folders_direct_one_name(tmp_path: Path) -> None:
    """``processed`` says a folder holds what was made from the raw one."""
    project, _ = _run(tmp_path, {"rp/proj/raw/vorn7.tif": "r", "rp/proj/processed/vorn7.tif": "p"})

    assert _beside(project) == [
        (BESIDE, "rp/proj/raw/vorn7.tif", "rp/proj/processed/vorn7.tif", "derives")
    ]


# ---------------------------------------------------------------------------
# Further cases, continued.
# ---------------------------------------------------------------------------


def test_crossed_conditions_and_splits_pair_only_within_one(tmp_path: Path) -> None:
    """``control_images`` and ``treated_masks`` both carry a word keeping folders apart.

    The words differ, so an image of one condition is never the source of the other's mask.
    ``control_images`` to ``control_masks`` stays directed, as does a nested ``control/images`` to
    ``control/masks``."""
    files: dict[str, str] = {}
    for condition in ("control", "treated"):
        for kind in ("images", "masks"):
            for index in (1, 2):
                files[f"xx/ds/{condition}_{kind}/c{index:02d}.tif"] = f"{condition}{kind}{index}"
            files[f"nest/ds/{condition}/{kind}/n01.tif"] = f"{condition}{kind}"
    for split in ("train", "test"):
        for kind in ("images", "labels"):
            files[f"tt/ds/{split}_{kind}/q07.png"] = f"{split}{kind}"
    files["cr/ds/train/r01.png"] = "t"
    files["cr/ds/test_masks/r01.png"] = "m"
    project, _ = _run(tmp_path, files)

    assert sorted((source, target) for _, source, target, _ in _beside(project)) == sorted(
        [
            *(
                (
                    f"xx/ds/{condition}_images/c{index:02d}.tif",
                    f"xx/ds/{condition}_masks/c{index:02d}.tif",
                )
                for condition in ("control", "treated")
                for index in (1, 2)
            ),
            *(
                (f"nest/ds/{condition}/images/n01.tif", f"nest/ds/{condition}/masks/n01.tif")
                for condition in ("control", "treated")
            ),
            *(
                (f"tt/ds/{split}_images/q07.png", f"tt/ds/{split}_labels/q07.png")
                for split in ("train", "test")
            ),
        ]
    )


def test_a_copy_in_another_format_is_made_from_its_file_and_a_backup_in_its_format_is_not(
    tmp_path: Path,
) -> None:
    """A ``.tif`` of an ``.nd2`` is a conversion and a master or source table is compiled data.

    Each is made from the movie. A backup keeps the held file's format (``zeph_041.nd2.bak``,
    ``zeph_041_old.nd2``) and forms nothing."""
    project, _ = _run(
        tmp_path,
        {
            "bw/proj/zeph_041.nd2": "n",
            "bw/proj/zeph_041_copy.tif": "c",
            "bw/proj/zeph_041_master.xlsx": "m",
            "bw/proj/zeph_041_old.tif": "o",
            "bw/proj/zeph_041_source.csv": "s",
            "bw/proj/zeph_041_tracks.csv": "t",
            "bw/proj/zeph_041.nd2.bak": "b",
            "bw/proj/zeph_041_old.nd2": "o2",
        },
    )

    assert {target for rule, _, target, _, _ in _candidates(project) if rule == NAMED_FOR} == {
        "bw/proj/zeph_041_copy.tif",
        "bw/proj/zeph_041_master.xlsx",
        "bw/proj/zeph_041_old.tif",
        "bw/proj/zeph_041_source.csv",
        "bw/proj/zeph_041_tracks.csv",
    }


def test_a_split_in_a_folder_links_to_the_movie_in_a_folder_within_it(tmp_path: Path) -> None:
    """The reverse of a split in a subfolder, its movie in ``p2/movies/``."""
    project, _ = _run(
        tmp_path,
        {
            "fiji/p2/movies/zeph_018.tif": "movie",
            "fiji/p2/zeph_018.tif - C=1.tif": "split",
        },
    )

    assert _pairs(project) == {("fiji/p2/movies/zeph_018.tif", "fiji/p2/zeph_018.tif - C=1.tif")}


# ---------------------------------------------------------------------------
# A source found upward: the movie in a raw branch, its splits in an analysis branch, under a
# common folder some levels above.
# ---------------------------------------------------------------------------

#: The invented layout: a person's folder, a year, a project, its raw and analysis branches.
PROJECT = "brel_lab/2023/zeph_project"


def _named_for_rows(project: Project) -> list[tuple[str, str, list[str]]]:
    return [
        (source, target, support)
        for rule, source, target, _, support in _candidates(project)
        if rule == NAMED_FOR
    ]


def test_a_split_in_an_analysis_branch_links_to_its_movie_in_the_raw_branch(
    tmp_path: Path,
) -> None:
    """The split names the movie's whole name; the movie's day folder and the split's set are
    experiments of their own, so the movie is in no scope the split looks in, but the project
    folder three levels above the split's folder holds exactly one file of that name. The support
    says how far up it was found."""
    project, _ = _run(
        tmp_path,
        {
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_007.nd2": "movie",
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_008.nd2": "another movie",
            f"{PROJECT}/analysis/2023-05-02_vorn/sub/Traj_zeph_007.nd2 - C=0-1.csv": "a,b\n1,2\n",
            f"{PROJECT}/analysis/2023-05-02_vorn/sub/zeph_007.nd2 - C=0-3.tif": "split",
        },
    )

    rows = _named_for_rows(project)
    assert {(source, target) for source, target, _ in rows} == {
        (
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_007.nd2",
            f"{PROJECT}/analysis/2023-05-02_vorn/sub/Traj_zeph_007.nd2 - C=0-1.csv",
        ),
        (
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_007.nd2",
            f"{PROJECT}/analysis/2023-05-02_vorn/sub/zeph_007.nd2 - C=0-3.tif",
        ),
    }
    assert all("found_3_folders_up" in support for _, _, support in rows)


def test_one_name_in_two_raw_branches_at_one_distance_links_nothing(tmp_path: Path) -> None:
    project, _ = _run(
        tmp_path,
        {
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_007.nd2": "movie one",
            f"{PROJECT}/raw/2023-05-09_vorn/zeph_007.nd2": "movie two",
            f"{PROJECT}/analysis/vorn_set/sub/zeph_007.nd2 - C=0-3.tif": "split",
        },
    )

    assert _named_for_rows(project) == []


def test_a_movie_beyond_the_bound_or_above_the_person_links_nothing(tmp_path: Path) -> None:
    """Six folders up is beyond the default bound; and a movie in another person's folder is
    never reached, whatever the bound."""
    project, _ = _run(
        tmp_path,
        {
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_008.nd2": "movie",
            f"{PROJECT}/analysis/a/b/c/d/e/zeph_008.nd2 - C=0.tif": "split",
            f"{PROJECT}/analysis/a/b/c/d/e/zeph_018.nd2 - C=0.tif": "split",
            "quen_lab/2023/zeph_009.nd2": "another person's movie",
            f"{PROJECT}/zeph_009.nd2 - C=0.tif": "split",
        },
    )

    assert _named_for_rows(project) == []


def test_a_name_embedded_and_absent_forms_nothing(tmp_path: Path) -> None:
    project, _ = _run(
        tmp_path,
        {
            f"{PROJECT}/raw/2023-05-02_vorn/zeph_010.nd2": "movie",
            f"{PROJECT}/analysis/vorn_set/sub/zeph_011.nd2 - C=0.tif": "split of a movie not here",
        },
    )

    assert _named_for_rows(project) == []


def test_a_split_never_takes_another_day_s_movie_and_its_own_day_breaks_a_tie(
    tmp_path: Path,
) -> None:
    """A split is not proposed from another day's movie found two folders up.

    The split of the sixth's analysis is not proposed from the fifth's movie. Where both days number
    from 001, the sixth's split would tie between the two movies. A source in another experiment is
    taken only where its experiment folder agrees with a folder between the split and the folder
    they share (the same name, or the same date and words), and that agreement breaks a tie."""
    project, _ = _run(
        tmp_path,
        {
            "brel_lab/p3/raw/2024-03-05_kelt/brisa_001.nd2": "movie of the fifth",
            "brel_lab/p3/raw/2024-03-05_kelt/brisa_002.nd2": "movie of the fifth",
            "brel_lab/p3/analysis/2024-03-06_kelt/brisa_001.nd2 - C=0.tif": "split of the sixth",
            "brel_lab/p2/raw/2024-03-05_kelt/vorn_001.nd2": "movie of the fifth",
            "brel_lab/p2/raw/2024-03-06_kelt/vorn_001.nd2": "movie of the sixth",
            "brel_lab/p2/analysis/2024-03-06_kelt/vorn_001.nd2 - C=0.tif": "split of the sixth",
        },
    )

    assert {(source, target) for source, target, _ in _named_for_rows(project)} == {
        (
            "brel_lab/p2/raw/2024-03-06_kelt/vorn_001.nd2",
            "brel_lab/p2/analysis/2024-03-06_kelt/vorn_001.nd2 - C=0.tif",
        )
    }


def test_the_tail_of_a_held_name_never_links_upward(tmp_path: Path) -> None:
    """With no ``kelt_008.nd2`` anywhere, the tail ``008.nd2`` of the name that the split holds
    does not link to a movie of that bare number two folders up."""
    project, _ = _run(
        tmp_path,
        {
            "brel_lab/p7/raw/other/008.nd2": "a movie numbered only",
            "brel_lab/p7/analysis/x/MAX_kelt_008.nd2 - C=0.tif": "split",
        },
    )

    assert _named_for_rows(project) == []
