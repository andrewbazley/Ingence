"""A dive defers beneath a program too.

The dive spent part of its budget reading a copy of Fiji's own libraries, beneath which every rule
already defers. The classes built here:

- the programs are the Rulebook's: the folders the project revision's ``defer_beneath`` scope
  raises over the census the dive starts from, so one declaration governs inference and the dive;
- read last by default: every file beneath a program follows every other file, each half in
  the order it would have without the other, so nothing is lost where the budget has room;
- never, by a setting, which leaves them unexamined and counted; in turn, the order with no
  deferral.

Every name here is invented; the layout names are the packaging conventions themselves.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import write_revision
from ingence.core.project import Project
from ingence.core.schema import Entry, EntryType
from ingence.core.settings import BeneathPrograms
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen, spend_order
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

CENSUS = "run_20260101T000001Z_111111111111"
DIVE = "run_20260101T000002Z_222222222222"


def _entry(name: str, size: int) -> Entry:
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root_x",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=name.rsplit(".", 1)[-1],
        size_bytes=size,
        first_seen_run="r",
        last_seen_run="r",
    )


def test_deferred_files_follow_every_other_and_each_half_keeps_its_own_order() -> None:
    kept = [_entry(f"vorn_{n}.csv", n) for n in range(3)] + [_entry("kelt.tif", 5)]
    later = [_entry(f"lib_{n}.jar", n) for n in range(3)] + [_entry("notes.txt", 1)]
    names = {entry.entry_id for entry in later}
    ordered = spend_order([*later, *kept], deferred=lambda entry: entry.entry_id in names)
    assert ordered == [*spend_order(kept), *spend_order(later)]
    assert spend_order([*later, *kept]) != ordered, "in turn, the jars are interleaved"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _project(base: Path, *, revision: bool = True, setting: BeneathPrograms | None = None):
    """An installed Fiji beside a laboratory's experiment, dived with every file in reach."""
    pool = base / "pool"
    app = pool / "Fiji" / "Fiji.app"
    for number in range(4):
        _write(app / "jars" / f"vorn-library-{number}.jar", "PK")
    _write(app / "plugins" / "brisa_plugin.jar", "PK")
    _write(app / "luts" / "fire.lut", "lut")
    _write(app / "licenses" / "kelt-annotations.txt", "Apache License")
    data = pool / "kelt_experiments" / "vorn tamen"
    for number in range(3):
        _write(data / f"vorn_{number}.csv", "a,b\n1,2\n")
    _write(data / "notes.txt", "a note")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    if revision:
        write_revision(project.path)
    if setting is not None:
        project = project.save_settings(
            project.settings.model_copy(
                update={
                    "extraction": project.settings.extraction.model_copy(
                        update={"beneath_programs": setting}
                    )
                }
            )
        )
    run_census(project, run_id=CENSUS, started_at="t")
    return project


def _examined(project: Project) -> list[tuple[str, bool]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT relative_path, detail FROM journal_events WHERE event = 'entry.examined' "
            "ORDER BY sequence"
        ).fetchall()
    return [(path, bool(json.loads(detail).get("beneath_program"))) for path, detail in rows]


def _beneath_fiji(path: str) -> bool:
    return path.startswith("Fiji/Fiji.app/")


def test_by_default_a_program_s_files_are_read_after_every_other_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    report = run_deepen(project, run_id=DIVE, started_at="t", profile="standard")
    examined = _examined(project)
    flags = [_beneath_fiji(path) for path, _ in examined]
    assert flags == sorted(flags), "no program file before a laboratory's"
    assert any(flags), "a budget with room still reads them"
    assert [marked for _, marked in examined] == flags, "the journal says which were beneath"
    assert report.programs_found == 1
    assert report.files_beneath_programs == sum(flags) == 7
    assert report.left_beneath_programs == 0
    with open_catalog(project.path) as catalog:
        (detail,) = catalog.query(
            "SELECT detail FROM journal_events WHERE event = 'programs.deferred'"
        ).fetchone()
    said = json.loads(detail)
    assert said["folders"] == [["Fiji/Fiji.app", "installed_software_in_folder", ["Fiji"]]]
    assert said["beneath_programs"] == "read_last"


def test_never_leaves_a_program_s_files_unexamined_and_counts_them(tmp_path: Path) -> None:
    project = _project(tmp_path, setting=BeneathPrograms.NOT_READ)
    report = run_deepen(project, run_id=DIVE, started_at="t", profile="standard")
    examined = _examined(project)
    assert examined and not any(_beneath_fiji(path) for path, _ in examined)
    assert report.left_beneath_programs == report.files_beneath_programs == 7
    with open_catalog(project.path) as catalog:
        outcomes = {
            row[0]
            for row in catalog.query(
                "SELECT scan_outcome FROM entries WHERE entry_type = 'file' "
                "AND starts_with(relative_path, 'Fiji/Fiji.app/')"
            ).fetchall()
        }
    assert outcomes == {"metadata_only"}, "left as the census found them, unexamined"


@pytest.mark.parametrize(
    ("revision", "setting"), [(True, BeneathPrograms.READ_IN_TURN), (False, None)]
)
def test_in_turn_or_with_no_deferral_declared_the_order_is_the_old_one(
    tmp_path: Path, revision: bool, setting: BeneathPrograms | None
) -> None:
    """A Rulebook deferring beneath nothing, or the setting, reads a program's files in turn.

    The files stand among the others, as in a dive with no deferral.

    Every project's Rulebook defers, so the one deferring beneath nothing is the
    book at revision 3, handed to the dive.
    """
    from ingence.rulebook.load import load_revisions
    from ingence.rulebook.resolve import effective_rulebook

    project = _project(tmp_path, revision=revision, setting=setting)
    report = run_deepen(
        project,
        run_id=DIVE,
        started_at="t",
        profile="standard",
        rulebook=None if revision else effective_rulebook(load_revisions(), at=3),
    )
    flags = [_beneath_fiji(path) for path, _ in _examined(project)]
    assert flags != sorted(flags), "the program's files take their turns among the others"
    assert report.left_beneath_programs == 0
    with open_catalog(project.path) as catalog:
        found = catalog.query(
            "SELECT count(*) FROM journal_events WHERE event = 'programs.deferred'"
        ).fetchone()
    assert found == (0,)
