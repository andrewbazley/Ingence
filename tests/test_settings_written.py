"""A settings file holds what somebody chose, never a default as if somebody had.

Writing every default into ``settings.yaml`` would make a value nobody chose read as a person's, and
a default that a later engine changes would never reach an older project. Every name here is
invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from ingence import SCHEMA_VERSION
from ingence.core.project import Project
from ingence.core.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path


def _file(project: Project) -> dict[str, object]:
    return dict(yaml.safe_load((project.path / "settings.yaml").read_text(encoding="utf-8")))


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "kelt.txt").write_text("a")
    return pool


def test_a_new_project_writes_no_default_as_a_choice(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    assert _file(project) == {"schema_version": SCHEMA_VERSION}
    assert Project.open(project.path).settings == Settings()


def test_a_new_project_writes_what_differs_from_the_defaults(tmp_path: Path) -> None:
    chosen = Settings.model_validate(
        {"dictionary": {"case_reading": {"whole_over_alone": 2.0}}, "jobs": {"retry_limit": 5}}
    )
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[_pool(tmp_path)], settings=chosen
    )
    assert _file(project) == {
        "dictionary": {"case_reading": {"whole_over_alone": 2.0}},
        "jobs": {"retry_limit": 5},
        "schema_version": SCHEMA_VERSION,
    }
    assert Project.open(project.path).settings == chosen


def test_a_default_a_person_wrote_stays_written_when_the_engine_saves(tmp_path: Path) -> None:
    """A person may write the default on purpose, to hold it against a later engine's change."""
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    held = Settings().jobs.retry_limit
    (project.path / "settings.yaml").write_text(
        yaml.safe_dump({"jobs": {"retry_limit": held}, "schema_version": SCHEMA_VERSION})
    )
    opened = Project.open(project.path)
    changed = opened.settings.model_copy(
        update={"scan": opened.settings.scan.model_copy(update={"workers": 3})}
    )
    saved = opened.save_settings(changed)
    assert _file(saved) == {
        "jobs": {"retry_limit": held},
        "scan": {"workers": 3},
        "schema_version": SCHEMA_VERSION,
    }


def test_a_setting_not_written_takes_the_engine_s_default(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    assert "dictionary" not in _file(project)
    reading = Project.open(project.path).settings.dictionary.case_reading.whole_over_alone
    assert reading == Settings().dictionary.case_reading.whole_over_alone


def test_fix_names_a_former_default_an_older_init_wrote_and_changes_nothing(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    written = {
        "dictionary": {"case_reading": {"whole_over_alone": 1.0}},
        "schema_version": SCHEMA_VERSION,
    }
    (project.path / "settings.yaml").write_text(yaml.safe_dump(written))
    before = (project.path / "settings.yaml").read_bytes()
    result = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert result.exit_code == 0, result.output
    assert "dictionary.case_reading.whole_over_alone: 1.0" in result.output
    assert (project.path / "settings.yaml").read_bytes() == before


def test_fix_names_the_jobs_stop_an_older_init_wrote(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ingence.cli.main import app

    project = Project.create(tmp_path / "project", created_at="t", roots=[_pool(tmp_path)])
    (project.path / "settings.yaml").write_text(
        yaml.safe_dump({"jobs": {"stop_silent": False}, "schema_version": SCHEMA_VERSION})
    )
    result = CliRunner().invoke(app, ["fix", "-p", str(project.path)])
    assert result.exit_code == 0, result.output
    assert "jobs.stop_silent: False" in result.output


def test_the_0_4_0_settings_take_their_defaults_and_hold_their_ranges() -> None:
    """The 0.4.0 settings take their defaults and hold their ranges.

    They are the content pool's size, the ceilings of a reading and a pass, the wait for a root, the
    domain skew, and the area level.
    """
    import pytest

    settings = Settings()
    assert settings.extraction.workers == "auto"
    assert settings.extraction.file_seconds == 60.0
    assert settings.extraction.total_seconds is None
    assert settings.extraction.root_wait_seconds == 300.0
    assert settings.extraction.root_loss_failures == 20
    assert settings.extraction.root_retry_seconds == 15.0
    skew = settings.dictionary.domain_skew
    assert (skew.minimum_files, skew.minimum_share, skew.minimum_ratio) == (20, 0.6, 3.0)
    assert skew.question_above_files == 20
    assert settings.themes.area_level == settings.themes.person_level == 1
    chosen = Settings.model_validate(
        {"extraction": {"workers": 8, "total_seconds": 900.0}, "dictionary": {"domain_skew": {}}}
    )
    assert chosen.extraction.workers == 8 and chosen.extraction.total_seconds == 900.0
    for wrong in (
        {"extraction": {"workers": 0}},
        {"extraction": {"workers": "some"}},
        {"extraction": {"file_seconds": 0}},
        {"extraction": {"root_wait_seconds": -1}},
        {"extraction": {"root_loss_failures": 0}},
        {"extraction": {"root_retry_seconds": 0}},
        {"dictionary": {"domain_skew": {"minimum_share": 1.5}}},
        {"dictionary": {"domain_skew": {"minimum_ratio": 0.5}}},
        {"themes": {"area_level": 9}},
    ):
        with pytest.raises(ValueError):
            Settings.model_validate(wrong)


def test_the_area_level_and_the_person_level_are_one_setting_under_two_names() -> None:
    """``themes.area_level`` and ``themes.person_level`` are one setting under two names.

    A file that sets one sets the other. One that sets both differently is refused by both names.
    Existing code keeps reading ``person_level``.
    """
    import pytest

    by_person = Settings.model_validate({"themes": {"person_level": 2}})
    assert (by_person.themes.person_level, by_person.themes.area_level) == (2, 2)
    by_area = Settings.model_validate({"themes": {"area_level": 3}})
    assert (by_area.themes.person_level, by_area.themes.area_level) == (3, 3)
    nobody = Settings.model_validate({"themes": {"person_level": None}})
    assert nobody.themes.area_level is None
    both = Settings.model_validate({"themes": {"person_level": 2, "area_level": 2}})
    assert both.themes.area_level == 2
    with pytest.raises(ValueError, match=r"themes\.area_level.*themes\.person_level"):
        Settings.model_validate({"themes": {"person_level": 2, "area_level": 3}})
