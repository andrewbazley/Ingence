"""Whether a dive may hash across a network share: the depth proposes, a person decides.

A person's ``hash.mode`` stands at every depth. The depth preset fills only
``hash.skip_remote_filesystems``, and the setting is ``None`` unless a person sets it. The run says
who chose. Otherwise a person's ``false`` (hash across shares at every depth) or ``true`` (never,
even deep) would be overruled at every run, and the run would record the preset as though it were
theirs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from ingence.core.project import Project
from ingence.core.schema import RunRecord
from ingence.core.settings import SETTINGS_FILE, HashSettings, Settings
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.scan.profiles import ProfileName, apply_profile, remote_choice
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

if TYPE_CHECKING:
    from pathlib import Path

_CENSUS = "run_20260101T000001Z_111111111111"
_DIVE = "run_20260101T000002Z_222222222222"


def test_a_new_project_leaves_the_share_to_the_depth() -> None:
    assert Settings().hash.skip_remote_filesystems is None


@pytest.mark.parametrize(
    ("said", "profile", "expected"),
    [
        (None, ProfileName.STANDARD, (True, "profile", False)),
        (None, ProfileName.DEEP, (False, "profile", False)),
        # ``true`` is what ``init`` wrote into every file: no choice, whatever the version.
        (True, ProfileName.STANDARD, (True, "profile", False)),
        (True, ProfileName.DEEP, (False, "profile", False)),
        (False, ProfileName.STANDARD, (False, "settings", False)),
        ("always", ProfileName.DEEP, (True, "settings", True)),
    ],
)
def test_a_person_s_choice_stands_at_every_depth(
    said: bool | str | None, profile: ProfileName, expected: tuple[bool, str, bool]
) -> None:
    settings = Settings(hash=HashSettings(skip_remote_filesystems=said))  # type: ignore[arg-type]
    choice = remote_choice(settings, profile)
    assert (choice.skip, choice.set_by, choice.need_too) == expected
    assert apply_profile(settings, profile).hash.skip_remote_filesystems is expected[0]


def test_a_true_init_wrote_is_no_choice_at_any_version() -> None:
    """Every older settings file ``init`` wrote carries ``true``, at 1.22 as well as before, and
    nothing tells it from a person's. So ``true`` is not a person's choice, and a person who wants a
    share left alone says ``always``, a value no ``init`` ever wrote."""
    for version in ("1.21", "1.22"):
        written = Settings.model_validate(
            {"schema_version": version, "hash": {"skip_remote_filesystems": True}}
        )
        for depth in (ProfileName.STANDARD, ProfileName.DEEP):
            assert remote_choice(written, depth).set_by == "profile"
    said = Settings.model_validate({"hash": {"skip_remote_filesystems": "always"}})
    assert said.hash.skip_remote_filesystems == "always"


def _run(project: Project) -> RunRecord:
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    return record_from_row(row, RunRecord)


def _project(tmp_path: Path, **hashing: object) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("vorn zib\n")
    return Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(hash=HashSettings(**hashing)),  # type: ignore[arg-type]
    )


def test_the_dive_s_run_says_who_chose(tmp_path: Path) -> None:
    project = _project(tmp_path, skip_remote_filesystems=False)
    run_census(project, run_id=_CENSUS, started_at="t")
    report = run_deepen(project, run_id=_DIVE, started_at="t", budget_seconds=30.0)
    record = _run(project)
    assert (record.skip_remote_filesystems, record.skip_remote_filesystems_set_by) == (
        False,
        "settings",
    )
    assert (report.skip_remote_filesystems, report.skip_remote_filesystems_set_by) == (
        False,
        "settings",
    )


def test_a_dive_leaves_an_older_file_s_true_as_written_and_reads_it_as_no_choice(
    tmp_path: Path,
) -> None:
    """``true`` needs no rewriting to read as no choice, so the first dive's upgrade of an older
    file edits only the mode and the version."""
    project = _project(tmp_path)
    written = project.path / SETTINGS_FILE
    older = yaml.safe_load(written.read_text("utf-8"))
    older["schema_version"] = "1.21"
    # An older engine's init wrote every section, and the current engine's writes only what differs.
    older.setdefault("hash", {})["mode"] = "none"
    older["hash"]["skip_remote_filesystems"] = True
    written.write_text(yaml.safe_dump(older), encoding="utf-8")
    project = Project.open(project.path)
    run_census(project, run_id=_CENSUS, started_at="t")
    run_deepen(project, run_id=_DIVE, started_at="t", budget_seconds=30.0, profile="deep")
    now = yaml.safe_load(written.read_text("utf-8"))
    assert now["hash"]["skip_remote_filesystems"] is True
    assert now["hash"]["mode"] == "need"
    record = _run(project)
    assert (record.skip_remote_filesystems, record.skip_remote_filesystems_set_by) == (
        False,
        "profile",
    )


def test_settings_no_depth_was_applied_to_skip_a_share_as_before() -> None:
    from ingence.core.settings import HashMode
    from ingence.scan.reuse import digests_wanted

    unapplied = Settings(hash=HashSettings(mode=HashMode.FULL))
    assert unapplied.hash.skip_remote_filesystems is None
    wanted = {"extensions": frozenset({"txt"}), "size_bytes": 10, "settings": unapplied}
    assert digests_wanted(**wanted, is_remote=True) == frozenset()  # type: ignore[arg-type]
    assert digests_wanted(**wanted, is_remote=False) == {"whole"}  # type: ignore[arg-type]
