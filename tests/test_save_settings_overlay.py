"""Saving settings never copies the Dashboard's committed values into the hand-edited file.

Where ``settings.dashboard.yaml`` exists, ``Project.save_settings`` writes the hand file's values
with only what the call changed laid on. Invented values throughout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from ingence.core.project import Project
from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE

if TYPE_CHECKING:
    from pathlib import Path


def test_a_saved_setting_leaves_the_dashboards_value_out_of_the_hand_file(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    (project.path / DASHBOARD_SETTINGS_FILE).write_text(
        yaml.safe_dump({"dictionary": {"plausible_years": [1995, 2030]}}), encoding="utf-8"
    )
    opened = Project.open(project.path)
    assert list(opened.settings.dictionary.plausible_years) == [1995, 2030]

    changed = opened.settings.model_copy(
        update={"dictionary": opened.settings.dictionary.model_copy(update={"glued_runs": "whole"})}
    )
    opened.save_settings(changed)

    hand = yaml.safe_load((project.path / SETTINGS_FILE).read_text(encoding="utf-8"))
    assert hand["dictionary"]["glued_runs"] == "whole"
    assert list(hand["dictionary"].get("plausible_years", ())) != [1995, 2030]
    reopened = Project.open(project.path)
    assert list(reopened.settings.dictionary.plausible_years) == [1995, 2030]
    assert reopened.settings.dictionary.glued_runs == "whole"
