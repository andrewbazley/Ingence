"""``GET /api/v1/ladder``: the word for each level of a project's ladder, by the kind of root.

A folder is read as a laboratory's: project, person, line of work, experiment, file. An archive's
listing climbs archive, theme, study, file, with no person level. Every folder and listing here is
invented.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import pytest

from ingence.api import v1
from ingence.api.plain_words import plain_words
from ingence.core.project import Project
from ingence.listing.format import Listing, ListingHeader, ListingRow, write_listing
from ingence.scan.census import run_census

RUN = "run_20260101T000001Z_111111111111"
_URI = "listing:bia:S-0001"


def _ask(project: Path) -> tuple[HTTPStatus, dict]:
    context = v1.context_for(project, settled=None, job_running=False)
    return v1.dispatch(context, "ladder", {})


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("ladder-lab")
    pool = base / "pool"
    for person in ("dana_whitfield", "sam_okafor"):
        folder = pool / person / "20240312 HeLa DAPI"
        folder.mkdir(parents=True)
        (folder / "HeLa_DAPI_01.nd2").write_text("x\n")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUN, started_at="t")
    return project.path


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("ladder-archive")
    project = Project.create(base / "project", created_at="t", listings=[_URI])
    header = ListingHeader(
        archive="bia",
        identifier="S-0001",
        fetched_at="2026-01-02T03:04:05Z",
        converter="test",
        converter_version="1",
    )
    rows = [ListingRow(path="actin/HeLa_actin_01.tif", size_bytes=5)]
    write_listing(Listing(header, rows), Path(project.identity.roots[0].mount_path))
    run_census(project, run_id=RUN, started_at="t")
    return project.path


def test_a_folder_climbs_the_labs_ladder_with_a_person_level(lab: Path) -> None:
    status, answer = _ask(lab)
    assert status == HTTPStatus.OK
    assert answer["kind"] == "lab"
    assert [level["kind"] for level in answer["levels"]] == [
        "root",
        "person",
        "theme",
        "experiment",
        "file",
    ]
    names = {level["kind"]: (level["name"], level["plural"]) for level in answer["levels"]}
    assert names["theme"] == ("theme", "themes")
    assert names["experiment"] == ("experiment", "experiments")
    # The level's word follows what the areas stand for: a person is never read from a name alone,
    # and each folder here holds one instrument's own file type, so the level is "instrument" until
    # a person says the folders are people's.
    assert names["person"] == ("instrument", "instruments")
    assert [root["kind"] for root in answer["roots"]] == ["lab"]
    from ingence.areas import write_area_kind
    from ingence.storage.catalog import open_catalog

    with open_catalog(lab, run_id=RUN) as catalog:
        folders = catalog.query(
            "SELECT entry_id FROM entries WHERE depth = 1 AND entry_type = 'directory'"
        ).fetchall()
    for (entry_id,) in folders:
        write_area_kind(lab, str(entry_id), "person", actor="Quenby")
    _, answered = _ask(lab)
    said = {level["kind"]: (level["name"], level["plural"]) for level in answered["levels"]}
    assert said["person"] == ("person", "people")


def test_a_listing_climbs_the_archives_ladder_with_studies_and_themes(archive: Path) -> None:
    status, answer = _ask(archive)
    assert status == HTTPStatus.OK
    assert answer["kind"] == "archive"
    assert [level["kind"] for level in answer["levels"]] == ["root", "theme", "experiment", "file"]
    names = {level["kind"]: (level["name"], level["plural"]) for level in answer["levels"]}
    assert names["root"] == ("archive", "archives")
    assert names["theme"] == ("theme", "themes")
    assert names["experiment"] == ("study", "studies")
    assert [root["kind"] for root in answer["roots"]] == ["archive"]
    # No sentence of the ladder calls a study an experiment or a theme a line of work.
    said = " ".join(f"{level['rows']} {level['axis']}" for level in answer["levels"])
    assert "line of work" not in said and "lines of work" not in said
    assert "person" not in said


def test_every_level_of_both_kinds_has_a_word_and_a_plural() -> None:
    words = plain_words()
    for kind in ("lab", "archive"):
        assert set(words.levels[kind]) == set(words.level_plurals[kind])
        assert all(word for word in words.levels[kind].values())
    assert "person" not in words.levels["archive"]


def test_a_lab_with_no_person_level_set_has_no_person_level(
    lab: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    import shutil

    copy = tmp_path_factory.mktemp("ladder-nobody") / "project"
    shutil.copytree(lab, copy)
    project = Project.open(copy)
    assert project.settings.themes.person_level == 1
    themes = project.settings.themes.model_copy(update={"person_level": None})
    project.save_settings(project.settings.model_copy(update={"themes": themes}))
    status, answer = _ask(copy)
    assert status == HTTPStatus.OK
    assert [level["kind"] for level in answer["levels"]] == ["root", "theme", "experiment", "file"]
    assert answer["kind"] == "lab"


def test_a_levels_broad_groups_are_named_with_the_levels_own_words(
    lab: Path, archive: Path
) -> None:
    lab_theme = next(level for level in _ask(lab)[1]["levels"] if level["kind"] == "theme")
    archive_theme = next(level for level in _ask(archive)[1]["levels"] if level["kind"] == "theme")
    assert lab_theme["broad"] == "broad themes"
    assert archive_theme["broad"] == "broad themes"
    others = [level for level in _ask(lab)[1]["levels"] if level["kind"] != "theme"]
    assert all(level["broad"] is None for level in others)


def test_an_address_the_engine_reads_as_no_listing_is_a_folders() -> None:
    from ingence.api.v1.ladder import _kind_of

    assert _kind_of("listing:bia:S-0001") == "archive"
    # Named as a listing but not one the engine parses (a name with a space, no identifier).
    assert _kind_of("listing:Bad Name:S-0001") == "lab"
    assert _kind_of("listing:bia") == "lab"
    assert _kind_of("/Users/example/pool") == "lab"
