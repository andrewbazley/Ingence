"""A project is a directory, opened safely and refused when it is not one."""

from __future__ import annotations

import pytest

from ingence import SCHEMA_VERSION
from ingence.core.ids import CaseSensitivity
from ingence.core.project import (
    CASE_FROM_A_FLIPPED_NAME,
    CASE_FROM_A_PAIR,
    CASE_UNSETTLED,
    NotAProjectError,
    Project,
    ProjectExistsError,
    discover,
    establish_case_sensitivity,
)
from ingence.core.settings import HashMode, HashSettings, Settings
from ingence.storage.layout import PROJECT_FILE


def make(tmp_path, **kwargs):
    return Project.create(tmp_path / "project", created_at="2026-01-01T00:00:00Z", **kwargs)


def test_creating_a_project_lays_out_everything_it_needs(tmp_path):
    project = make(tmp_path)
    for name in ("runs", ".tmp", "rulebook", "dictionary", "record"):
        assert (project.path / name).is_dir()
    assert (project.path / PROJECT_FILE).is_file()
    assert (project.path / "settings.yaml").is_file()


def test_a_second_project_in_the_same_place_is_refused(tmp_path):
    make(tmp_path)
    with pytest.raises(ProjectExistsError, match="already exists"):
        make(tmp_path)


def test_a_project_inside_its_own_data_pool_is_made(tmp_path):
    """A project may live anywhere: its census leaves it out, which
    ``tests/test_project_anywhere.py`` holds, so the engine's own output never becomes part
    of what it observes."""
    pool = tmp_path / "pool"
    (pool / "sub").mkdir(parents=True)
    project = Project.create(pool / "sub" / "project", created_at="t", roots=[pool])
    assert (project.path / PROJECT_FILE).is_file()


def test_a_project_beside_its_data_pool_is_fine(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    assert len(project.identity.roots) == 1


def test_a_root_keeps_its_identity_when_the_mount_path_changes(tmp_path):
    """Relocation must not invalidate every entry identifier in every past run."""
    pool = tmp_path / "pool"
    pool.mkdir()
    first = Project.create(tmp_path / "a", created_at="t", roots=[pool])
    second = Project.create(tmp_path / "b", created_at="t", roots=[pool])
    assert first.identity.roots[0].root_id == second.identity.roots[0].root_id


def test_settings_round_trip_through_yaml(tmp_path):
    settings = Settings(hash=HashSettings(mode=HashMode.FULL, chunk_bytes=8192))
    project = make(tmp_path, settings=settings)
    reopened = Project.open(project.path)
    assert reopened.settings.hash.mode is HashMode.FULL
    assert reopened.settings.hash.chunk_bytes == 8192


def test_a_project_opens_on_defaults_when_settings_are_deleted(tmp_path):
    """Losing tunables is an inconvenience. Refusing to open would be worse."""
    project = make(tmp_path)
    (project.path / "settings.yaml").unlink()
    assert Project.open(project.path).settings == Settings()


def test_opening_something_that_is_not_a_project_says_so(tmp_path):
    with pytest.raises(NotAProjectError, match="not an Ingence project"):
        Project.open(tmp_path)


def test_a_project_written_by_a_newer_schema_is_refused_not_misread(tmp_path):
    """Reading forward is guesswork, and quiet guesswork is the worst kind."""
    project = make(tmp_path)
    text = (project.path / PROJECT_FILE).read_text(encoding="utf-8")
    # Derived from the current version rather than written as a literal. A
    # literal turns this into a test that passes by replacing nothing the first
    # time the schema is bumped, which is the quiet way a guard stops guarding.
    forward = text.replace(f'"schema_version":"{SCHEMA_VERSION}"', '"schema_version":"99.0"')
    assert forward != text
    (project.path / PROJECT_FILE).write_text(forward, encoding="utf-8")
    with pytest.raises(NotAProjectError, match="Upgrade Ingence"):
        Project.open(project.path)


def test_discovery_walks_upward(tmp_path):
    project = make(tmp_path)
    deep = project.path / "runs" / "nested" / "deeper"
    deep.mkdir(parents=True)
    assert discover(deep).path == project.path


def test_discovery_fails_clearly_when_there_is_no_project(tmp_path):
    with pytest.raises(NotAProjectError, match="no Ingence project found"):
        discover(tmp_path)


class _Stat:
    """What ``lstat`` reports, as far as the case evidence reads it."""

    def __init__(self, device: int, inode: int) -> None:
        self.st_dev = device
        self.st_ino = inode


def test_two_names_differing_only_in_case_prove_a_root_sensitive(tmp_path):
    """The first way: the filesystem is holding two files an insensitive one could not."""
    listing = {tmp_path: [("Plate.csv", False), ("plate.csv", False)]}
    label = establish_case_sensitivity(
        tmp_path, list_names=listing.__getitem__, stat=lambda _: _Stat(1, 1)
    )
    assert label == (CaseSensitivity.SENSITIVE, CASE_FROM_A_PAIR)


def test_a_flipped_name_that_is_the_same_file_proves_a_root_insensitive(tmp_path):
    """The second way: a name met in the root, looked up with its case flipped, is itself."""
    listing = {tmp_path: [("Plate.csv", False)]}
    # Keyed by the name as a string: on Windows two paths differing only in case are equal, so a
    # table keyed by path would hold one entry and every fake here would say the same thing.
    seen = {"Plate.csv": _Stat(7, 42), "pLATE.CSV": _Stat(7, 42)}
    label = establish_case_sensitivity(
        tmp_path, list_names=listing.__getitem__, stat=lambda path: seen[path.name]
    )
    assert label == (CaseSensitivity.INSENSITIVE, CASE_FROM_A_FLIPPED_NAME)


def test_nothing_settling_it_leaves_a_root_unknown_which_keeps_files_apart(tmp_path):
    """The third way: a flipped name absent and no pair in reach is no proof either way."""
    listing = {tmp_path: [("Plate.csv", False), ("sub", True)], tmp_path / "sub": [("1", False)]}

    def lookup(path):
        if path.name == "pLATE.CSV":
            raise FileNotFoundError(path)
        return _Stat(7, 42)

    label = establish_case_sensitivity(tmp_path, list_names=listing.__getitem__, stat=lookup)
    assert label == (CaseSensitivity.UNKNOWN, CASE_UNSETTLED)
    # A root with nothing to flip and no pair is unknown too, and a different file at the
    # flipped name -- the same inode nowhere -- proves nothing insensitive.
    only_digits = {tmp_path: [("1", False), ("2", False)]}
    assert establish_case_sensitivity(tmp_path, list_names=only_digits.__getitem__)[0] is (
        CaseSensitivity.UNKNOWN
    )
    distinct = {"Plate.csv": _Stat(7, 42), "pLATE.CSV": _Stat(7, 43)}
    listing = {tmp_path: [("Plate.csv", False)]}
    assert establish_case_sensitivity(
        tmp_path, list_names=listing.__getitem__, stat=lambda path: distinct[path.name]
    ) == (CaseSensitivity.UNKNOWN, CASE_UNSETTLED)


def test_a_root_s_label_is_read_from_its_own_names_and_writes_nothing(tmp_path):
    """On a real filesystem: the root's own answer, recorded with how, and the root untouched.

    Where the filesystem ignores case (the two names written below are one file) the label says
    so, so no identifier a project on it has written can change. Where it does not, a pair
    settles it.
    """
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "Plate.csv").write_text("x")
    (pool / "plate.csv").write_text("y")
    before = sorted(path.name for path in pool.iterdir())
    project = make(tmp_path, roots=[pool])
    declaration = project.identity.roots[0]
    assert sorted(path.name for path in pool.iterdir()) == before
    if len(before) == 1:
        assert declaration.case_sensitivity is CaseSensitivity.INSENSITIVE
        assert declaration.case_established_by == CASE_FROM_A_FLIPPED_NAME
    else:
        assert (declaration.case_sensitivity, declaration.case_established_by) == (
            CaseSensitivity.SENSITIVE,
            CASE_FROM_A_PAIR,
        )
    # And the census carries how it was established into the run's own root record.
    from ingence.scan.census import run_census
    from ingence.storage.catalog import open_catalog

    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    with open_catalog(project.path) as catalog:
        recorded = catalog.query(
            "SELECT case_sensitivity, case_established_by FROM roots"
        ).fetchone()
    assert recorded == (declaration.case_sensitivity.value, declaration.case_established_by)


def test_a_root_declared_before_the_label_field_existed_keeps_the_label_it_had(tmp_path):
    """An identity file without the field opens with it absent, and the label is not revisited,
    because it decides every entry identifier the project has written."""
    import json

    pool = tmp_path / "pool"
    pool.mkdir()
    project = make(tmp_path, roots=[pool])
    written = json.loads((project.path / PROJECT_FILE).read_text())
    written["roots"][0]["case_sensitivity"] = "insensitive"
    del written["roots"][0]["case_established_by"]
    (project.path / PROJECT_FILE).write_text(json.dumps(written))
    reopened = Project.open(project.path).identity.roots[0]
    assert reopened.case_sensitivity is CaseSensitivity.INSENSITIVE
    assert reopened.case_established_by is None
