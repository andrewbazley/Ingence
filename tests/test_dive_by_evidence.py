"""A dive chooses files by the evidence it still lacks, and hashes by need.

The engine never silently overrules the hash mode. Hashing is allowed whatever the dive depth,
and the engine hashes only what it needs to compare files and deduplicate them.

- A mode a person sets in ``settings.yaml`` wins over the depth; the run says who chose.
- Where nobody set one, the engine hashes what a comparison needs: a sample of each file that
  shares its size with another, and the whole of each file whose sample another file shares, so
  byte identity can be proven. A file of a size nothing else has needs no digest to be told apart.
- A file is read again when the evidence now asked for is missing: a depth deeper than it was
  read at (where its reading was partial), or a digest the mode in force asks for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingence.core.project import Project
from ingence.core.schema import RunRecord, ScanOutcome
from ingence.core.settings import ExtractionSettings, HashMode, HashSettings, Settings
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.scan.profiles import ProfileName, hash_choice
from ingence.storage.catalog import open_catalog
from ingence.storage.index_store import record_from_row

RUNS = [f"run_20260101T00000{index}Z_{str(index) * 12}" for index in range(1, 8)]


def _digests(project: Project) -> dict[str, set[str]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT e.name, o.identity_method FROM observations o JOIN entries e USING (entry_id) "
            "WHERE o.identity_method IN ('blake3_quick', 'blake3_whole')"
        ).fetchall()
        names = [
            row[0]
            for row in catalog.query(
                "SELECT name FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        ]
    found: dict[str, set[str]] = {name: set() for name in names}
    for name, method in rows:
        found[name].add(method)
    return found


def _dive(project: Project, run: str, profile: str = "standard", budget: float = 60):
    return run_deepen(project, run_id=run, started_at="t", profile=profile, budget_seconds=budget)


def _run(project: Project) -> RunRecord:
    with open_catalog(project.path) as catalog:
        (row,) = catalog.query("SELECT * FROM run").to_arrow_table().to_pylist()
    return record_from_row(row, RunRecord)


def _entries(project: Project) -> dict[str, tuple[str, str | None]]:
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT name, scan_outcome, extraction_profile FROM entries WHERE entry_type = 'file'"
        ).fetchall()
    return {name: (outcome, profile) for name, outcome, profile in rows}


def _pool(tmp_path: Path) -> Path:
    pool = tmp_path / "pool"
    (pool / "kelt").mkdir(parents=True)
    body = b"vorn plate reading\n" * 40
    (pool / "kelt" / "vorn_a.dat").write_bytes(body)
    (pool / "kelt" / "vorn_b.dat").write_bytes(body)
    # The same size, other bytes: its sample tells it apart, and no whole read is needed.
    (pool / "kelt" / "vorn_c.dat").write_bytes(b"x" + body[1:])
    # A size nothing else has: nothing to compare it with.
    (pool / "kelt" / "zib.dat").write_bytes(b"zib only\n")
    return pool


def _project(tmp_path: Path, **hashing) -> Project:
    return Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[_pool(tmp_path)],
        settings=Settings(hash=HashSettings(**hashing)),
    )


# ---------------------------------------------------------------------------
# Who chooses the digest.
# ---------------------------------------------------------------------------


def test_a_new_project_leaves_the_digest_to_need() -> None:
    assert Settings().hash.mode is HashMode.NEED


def test_a_settings_file_an_older_engine_wrote_leaves_it_to_need_too() -> None:
    """Before 1.22 every settings file carried ``none``, written by ``init`` and overruled by
    every dive: it was never a person's choice, so it is read as no choice at all."""
    older = Settings.model_validate({"schema_version": "1.21", "hash": {"mode": "none"}})
    assert older.hash.mode is HashMode.NEED
    chosen = Settings.model_validate({"schema_version": "1.22", "hash": {"mode": "none"}})
    assert chosen.hash.mode is HashMode.NONE


@pytest.mark.parametrize(
    ("hashing", "profile", "expected"),
    [
        ({"mode": HashMode.FULL}, ProfileName.STANDARD, ("full", "settings")),
        ({"mode": HashMode.NONE}, ProfileName.DEEP, ("none", "settings")),
        ({"mode": HashMode.QUICK}, ProfileName.DEEP, ("quick", "settings")),
        ({}, ProfileName.STANDARD, ("need", "need")),
        ({}, ProfileName.DEEP, ("need", "need")),
        ({"need_from": "depth"}, ProfileName.STANDARD, ("quick", "profile")),
        ({"need_from": "depth"}, ProfileName.DEEP, ("adaptive", "profile")),
    ],
)
def test_a_mode_a_person_set_is_never_overruled_by_the_depth(hashing, profile, expected) -> None:
    choice = hash_choice(Settings(hash=HashSettings(**hashing)), profile)
    assert (choice.recorded, choice.set_by) == expected


def test_a_standard_dive_under_a_mode_a_person_set_computes_that_digest(tmp_path: Path) -> None:
    project = _project(tmp_path, mode=HashMode.FULL)
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "standard", 60)
    digests = _digests(project)
    assert all(found == {"blake3_whole"} for found in digests.values()), digests
    record = _run(project)
    assert (record.hash_mode, record.hash_mode_set_by) == ("full", "settings")


def test_by_need_the_dive_digests_what_a_comparison_needs(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_census(project, run_id=RUNS[0], started_at="t")
    report = _dive(project, RUNS[1], "standard", 60)
    # The dive says what it chose and why, as the terminal shows it.
    assert (report.hash_mode, report.hash_mode_set_by) == ("need", "need")
    assert (report.chosen_because, report.digested_by_need) == ({"unread": 4}, 2)
    digests = _digests(project)
    # Two files whose samples agree are read whole, so byte identity can be proven.
    assert digests["vorn_a.dat"] == {"blake3_quick", "blake3_whole"}
    assert digests["vorn_b.dat"] == {"blake3_quick", "blake3_whole"}
    # A file of a shared size whose sample differs is told apart by the sample.
    assert digests["vorn_c.dat"] == {"blake3_quick"}
    # A file of a size nothing else has needs nothing to be told apart.
    assert digests["zib.dat"] == set()
    record = _run(project)
    assert (record.hash_mode, record.hash_mode_set_by) == ("need", "need")


# ---------------------------------------------------------------------------
# Which files a dive reads.
# ---------------------------------------------------------------------------


def test_a_dive_records_the_depth_it_read_each_file_at(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "standard", 60)
    entries = _entries(project)
    assert {profile for _, profile in entries.values()} == {"standard"}
    # A walk over unchanged files keeps the depth they were read at with their readings, and so
    # does a rebind, which reads the names alone.
    run_census(project, run_id=RUNS[2], started_at="t")
    assert {profile for _, profile in _entries(project).values()} == {"standard"}
    run_census(project, run_id=RUNS[3], started_at="t", rebind=True)
    assert {profile for _, profile in _entries(project).values()} == {"standard"}


def test_a_second_dive_with_nothing_missing_reads_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "standard", 60)
    again = _dive(project, RUNS[2], "standard", 60)
    assert again.examined == 0


def test_a_digest_the_mode_now_asks_for_is_added_alone(tmp_path: Path) -> None:
    """A file lacking only a digest has that digest computed, and its readings stand."""
    project = _project(tmp_path)
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "standard", 60)
    assert _digests(project)["zib.dat"] == set()
    before = _readings(project)

    project = project.save_settings(Settings(hash=HashSettings(mode=HashMode.FULL)))
    again = _dive(project, RUNS[2], "standard", 60)
    # The two whose samples agreed were digested whole by need already; the other two lack it,
    # and get it without being read again.
    assert (again.examined, again.chosen_because, again.digested_alone) == (0, {"digest": 2}, 2)
    assert all("blake3_whole" in found for found in _digests(project).values())
    after = _readings(project)
    assert {name: count - before[name] for name, count in after.items()} == {
        "vorn_a.dat": 0,
        "vorn_b.dat": 0,
        "vorn_c.dat": 1,
        "zib.dat": 1,
    }


def _readings(project: Project) -> dict[str, int]:
    with open_catalog(project.path) as catalog:
        return dict(
            catalog.query(
                "SELECT e.name, count(*) FROM observations o JOIN entries e USING (entry_id) "
                "WHERE e.entry_type = 'file' GROUP BY e.name"
            ).fetchall()
        )


def test_a_standard_need_dive_after_a_deep_full_one_keeps_the_depth_and_the_proof(
    tmp_path: Path,
) -> None:
    """Filling a missing digest does not re-extract both files at standard, replace their whole
    digests by samples (a proof by a nomination) or lower their depth."""
    pool = tmp_path / "pool"
    pool.mkdir()
    body = b"brisa trell line\n" * ((5 << 20) // 16)  # larger than standard's 4 MiB sample
    (pool / "brisa_a.txt").write_bytes(body)
    (pool / "brisa_b.txt").write_bytes(body[:-1] + b"X")  # the same size, other bytes
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(
            hash=HashSettings(mode=HashMode.FULL), extraction=ExtractionSettings(enabled=True)
        ),
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "deep", 120)
    held, readings = _digests(project), _readings(project)
    project = project.save_settings(
        project.settings.model_copy(
            update={"hash": project.settings.hash.model_copy(update={"mode": HashMode.NEED})}
        )
    )
    report = _dive(project, RUNS[2], "standard", 120)
    assert (report.examined, report.digested_alone, report.digested_by_need) == (0, 0, 0)
    assert _entries(project) == dict.fromkeys(held, ("extracted", "deep"))
    assert _digests(project) == held == {name: {"blake3_whole"} for name in held}
    assert _readings(project) == readings
    # And a deep dive after it has nothing to read again.
    assert _dive(project, RUNS[3], "deep", 120).examined == 0


def test_need_never_reads_a_file_the_hash_filters_leave_out(tmp_path: Path) -> None:
    """A need dive reads no file the hash filters leave out, even with samples held from before."""
    pool = tmp_path / "pool"
    pool.mkdir()
    body = b"vorn plate reading\n" * 40
    (pool / "vorn_a.raw").write_bytes(body)
    (pool / "vorn_b.raw").write_bytes(body)
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(hash=HashSettings(mode=HashMode.QUICK)),
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "standard", 60)
    project = project.save_settings(
        project.settings.model_copy(
            update={
                "hash": project.settings.hash.model_copy(
                    update={"mode": HashMode.NEED, "exclude_extensions": ("raw",)}
                )
            }
        )
    )
    report = _dive(project, RUNS[2], "standard", 60)
    assert report.digested_by_need == 0
    assert all("blake3_whole" not in methods for methods in _digests(project).values())


def test_need_reads_no_file_whole_above_the_whole_digest_ceiling(tmp_path: Path) -> None:
    """Above ``full_hash_max_bytes`` two agreeing samples stay a nomination, never read whole."""
    project = _project(tmp_path, full_hash_max_bytes=100)
    run_census(project, run_id=RUNS[0], started_at="t")
    report = _dive(project, RUNS[1], "standard", 60)
    assert (report.digested_by_need, report.nominated_by_need) == (0, 2)
    assert _digests(project)["vorn_a.dat"] == {"blake3_quick"}


def test_a_reading_from_before_the_depth_was_recorded_with_a_whole_digest_was_deep() -> None:
    """Before 1.22 only a deep dive took whole digests (standard's preset sampled), so an entry
    with no recorded depth and a whole digest was read deep, and is not read again for depth."""
    from ingence.core.schema import Entry, EntryType
    from ingence.scan.deepen import read_in_part

    large = Entry(
        entry_id="entry_zib",
        root_id="root_x",
        relative_path="zib.tif",
        name="zib.tif",
        entry_type=EntryType.FILE,
        extension="tif",
        size_bytes=5 << 20,
        scan_outcome=ScanOutcome.EXTRACTED,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )
    deep = 2
    assert read_in_part(large, rank=deep)
    assert not read_in_part(large, rank=deep, held_whole=True)


def test_a_deeper_dive_reads_again_only_what_the_shallower_read_in_part(tmp_path: Path) -> None:
    """Standard reads a sample of 4 MiB; a file larger than that was read in part, and deep
    reads it again. A file within the sample was read whole, and deep has nothing to add."""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "brisa_large.txt").write_bytes(b"brisa trell line\n" * ((5 << 20) // 16))
    (pool / "brisa_small.txt").write_text("brisa\n")
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(enabled=True)),
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    _dive(project, RUNS[1], "standard", 120)
    assert _entries(project)["brisa_large.txt"][1] == "standard"

    deeper = _dive(project, RUNS[2], "deep", 120)
    assert deeper.examined == 1
    entries = _entries(project)
    assert entries["brisa_large.txt"] == (ScanOutcome.EXTRACTED.value, "deep")
    assert entries["brisa_small.txt"][1] == "standard"


def test_a_person_s_none_on_an_older_project_is_kept_once_said(tmp_path: Path) -> None:
    """An older file's ``none`` is nobody's; a ``none`` said since is a person's, and stays.

    Saved through the settings file or laid over it by the Dashboard, a file an older engine
    wrote still carries its old version: the choice must not be read back as the old default.
    """
    import yaml

    from ingence.core.settings import DASHBOARD_SETTINGS_FILE, SETTINGS_FILE

    project = _project(tmp_path)
    written = project.path / SETTINGS_FILE
    older = yaml.safe_load(written.read_text("utf-8"))
    older["schema_version"] = "1.21"
    older.setdefault("hash", {})["mode"] = "none"
    written.write_text(yaml.safe_dump(older), encoding="utf-8")
    assert Project.open(project.path).settings.hash.mode is HashMode.NEED

    # Said through the Dashboard's own file, laid over the older hand file.
    (project.path / DASHBOARD_SETTINGS_FILE).write_text(
        yaml.safe_dump({"hash": {"mode": "none"}}), encoding="utf-8"
    )
    assert Project.open(project.path).settings.hash.mode is HashMode.NONE
    (project.path / DASHBOARD_SETTINGS_FILE).unlink()

    # Said by saving the settings.
    reopened = Project.open(project.path)
    reopened.save_settings(
        reopened.settings.model_copy(
            update={"hash": reopened.settings.hash.model_copy(update={"mode": HashMode.NONE})}
        )
    )
    assert Project.open(project.path).settings.hash.mode is HashMode.NONE


def test_a_dive_writes_an_older_file_s_none_as_need_once_and_a_later_none_stands(
    tmp_path: Path,
) -> None:
    """If the upgrade were keyed on the hand file's own version, which nothing rewrites, a ``none``
    a person wrote into an older file would read as ``need`` forever. So the first dive writes the
    upgrade into the file, says so on its run and in its report, and a ``none`` written after it
    is the person's."""
    import yaml

    from ingence.core.settings import SETTINGS_FILE

    project = _project(tmp_path)
    written = project.path / SETTINGS_FILE
    older = yaml.safe_load(written.read_text("utf-8"))
    older["schema_version"] = "1.21"
    older.setdefault("hash", {})["mode"] = "none"
    written.write_text("# a person's comment\n" + yaml.safe_dump(older), encoding="utf-8")
    project = Project.open(project.path)
    run_census(project, run_id=RUNS[0], started_at="t")
    report = _dive(project, RUNS[1], "standard", 60)

    assert report.settings_upgraded
    text = written.read_text("utf-8")
    assert "# a person's comment" in text
    now = yaml.safe_load(text)
    assert (now["hash"]["mode"], now["schema_version"]) == ("need", Settings().schema_version)
    said = {(tally.source, tally.count) for tally in _run(project).warnings}
    assert ("settings", 1) in said

    now["hash"]["mode"] = "none"
    written.write_text(yaml.safe_dump(now), encoding="utf-8")
    project = Project.open(project.path)
    assert project.settings.hash.mode is HashMode.NONE
    again = _dive(project, RUNS[2], "standard", 60)
    assert not again.settings_upgraded
    assert (again.hash_mode, again.hash_mode_set_by) == ("none", "settings")
