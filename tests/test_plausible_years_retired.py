"""``dictionary.plausible_years``: loadable, read by nothing, removable once no project needs it.

Tokeniser 9 reads every date a name spells against ``dictionary.years``. ``plausible_years`` is
kept only so that a project written before it still loads. Removal waits until no stored project
needs it. The historical project fixtures are the stored projects this suite knows, so the first
test here fails the day none of them names the setting, which is the day it may go, with this file.
Those fixtures are frozen (written by the 1.20 and 1.21 engines, and never rewritten), so they
always name it. The tripwire fires only when they are retired, and it says nothing about a
laboratory's own projects, which a person checks before removing it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ingence.core.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "projects"
SOURCE = Path(__file__).parents[1] / "src" / "ingence"


def fixtures_naming_plausible_years(directory: Path) -> list[Path]:
    """Every stored project fixture's settings that name ``dictionary.plausible_years``."""
    found: list[Path] = []
    for path in sorted(directory.glob("*/settings.yaml")):
        held = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "plausible_years" in (held.get("dictionary") or {}):
            found.append(path)
    return found


def test_plausible_years_stays_while_a_stored_project_names_it() -> None:
    naming = fixtures_naming_plausible_years(FIXTURES)
    assert naming, (
        "no stored project fixture names dictionary.plausible_years any more: nothing needs it "
        "loadable, so remove it from core/settings.py (and this file)"
    )
    for path in naming:
        # Each still loads as the settings it was written as.
        Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_the_tripwire_fires_where_no_stored_project_names_it(tmp_path: Path) -> None:
    (tmp_path / "later").mkdir()
    (tmp_path / "later" / "settings.yaml").write_text(
        yaml.safe_dump({"dictionary": {"years": [2013, "present"]}}), encoding="utf-8"
    )
    assert fixtures_naming_plausible_years(tmp_path) == []


def test_nothing_in_the_engine_reads_plausible_years() -> None:
    readers = sorted(
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if "plausible_years" in path.read_text(encoding="utf-8")
    )
    assert readers == ["core/settings.py"]
