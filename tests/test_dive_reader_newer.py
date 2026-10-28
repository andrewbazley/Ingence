"""A dive reads again a file whose kept reading an older version of its reader made.

A reading records the reader that made it and that reader's version. When a reader is fixed and its
version raised, no other command reads an old project's files with the new reader, so a table capped
at 200 rows by an old table reader would say 200 for good. So a dive chooses, beside the files it
never read (``unread``), read in part (``deeper``) or lacking a digest (``digest``), a file whose
kept reading came from an older version of a reader that read it (``reader_newer``).

- Each reader is compared on its own, and only on the files it read, which the stored readings
  say: a change to one reader never reads the whole pool again, and a file a reader opened by its
  content (a ZIP named ``.npz``) is read again like one it claims by extension.
- The readers that sign a file's content (a table's columns, an array's shape) are readers in this
  rule too.
- The file is read once, inside the dive's budget and in the dive's order, and its new reading
  records the new version, so the next dive reads nothing.
- A reading with no recorded version is older only for a reader whose version has risen above
  its first.
- A dive never reads a file again at a depth shallower than the reading it replaces.

Every name is invented.
"""

from __future__ import annotations

import dataclasses
import io
import zipfile
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

import ingence.scan.deepen as deepen_module
from ingence.cli.main import app
from ingence.core.project import Project
from ingence.core.schema import ScanOutcome
from ingence.core.settings import ExtractionSettings, Settings
from ingence.extractors import load_builtins
from ingence.extractors.archive import ArchiveExtractor
from ingence.extractors.base import EXTRACTORS
from ingence.extractors.keywords import KeywordExtractor
from ingence.extractors.nikon_nd2 import Nd2Extractor
from ingence.extractors.tabular import TabularExtractor
from ingence.identity import IDENTITY_METHODS
from ingence.identity import load_builtins as load_identity_builtins
from ingence.identity.structure import ArraySignature, TabularSchema
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

load_builtins()
load_identity_builtins()

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 9)]
#: What the table reader of version 1 did: it stopped at the rows sampled for column types and
#: marked the count exact.
OLD_CAP = 200
#: The table reader's version today, read before any test changes it.
NOW = TabularExtractor.version
#: The same for the archive reader and for the two signatures, read before any test changes them.
ARCHIVE_NOW = ArchiveExtractor.version
ARRAY_NOW = ArraySignature.version
SCHEMA_NOW = TabularSchema.version


def _table(rows: int) -> str:
    return "well,intensity\n" + "".join(f"A{n},{n}\n" for n in range(1, rows + 1))


def _older_tabular(patch: pytest.MonkeyPatch, version: str = "1") -> None:
    """Make the table reader what it was before: capped at 200 rows, and at an older version."""
    real = TabularExtractor.extract

    def capped(self: TabularExtractor, context: object) -> object:
        result = real(self, context)  # type: ignore[arg-type]
        readings = tuple(
            dataclasses.replace(reading, value=min(int(reading.value), OLD_CAP))  # type: ignore[arg-type]
            if reading.key == "table.row_count"
            else reading
            for reading in result.readings
        )
        return dataclasses.replace(result, readings=readings)

    patch.setattr(TabularExtractor, "extract", capped)
    patch.setattr(TabularExtractor, "version", version)


def _project(
    tmp_path: Path, files: dict[str, str | bytes], settings: Settings | None = None
) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    for name, body in files.items():
        if isinstance(body, bytes):
            (pool / name).write_bytes(body)
        else:
            (pool / name).write_text(body)
    # In this process, not in a read worker: a worker is a fresh interpreter, where a version these
    # tests patch onto a reader does not exist.
    chosen = settings or Settings()
    extraction = chosen.extraction.model_copy(update={"use_subprocess_isolation": False})
    chosen = chosen.model_copy(update={"extraction": extraction})
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=chosen)
    run_census(project, run_id=RUNS[0], started_at="t")
    return project


def _in_process(project: Path) -> None:
    """Read in this process rather than in a read worker: a worker is a fresh interpreter, where a
    version this test patches onto a reader does not exist."""
    settings = project / "settings.yaml"
    settings.write_text(
        settings.read_text().rstrip("\n") + "\nextraction:\n  use_subprocess_isolation: false\n"
    )


def _dive(project: Project, run: str, profile: str = "standard", budget: float = 60):
    return run_deepen(project, run_id=run, started_at="t", profile=profile, budget_seconds=budget)


def _stored(project: Project, name: str, key: str, observer: str = "tabular") -> list[tuple]:
    """The value and version of each reading of one key a reader made of a file, by name."""
    with open_catalog(project.path) as catalog:
        return catalog.query(
            "SELECT coalesce(o.value_integer, CAST(o.value_boolean AS INTEGER)), "
            "o.observer_version FROM observations o JOIN entries e USING (entry_id) "
            "WHERE e.name = ? AND o.key = ? AND o.observer = ? ORDER BY o.observer_version",
            [name, key, observer],
        ).fetchall()


def _readers(project: Project, name: str) -> dict[str, set[str]]:
    """Each reader's recorded versions on one file, by name."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT o.observer, o.observer_version FROM observations o JOIN entries e "
            "USING (entry_id) WHERE e.name = ? AND o.observer IN "
            "('tabular', 'text', 'keywords', 'generic', 'archive', 'array_signature', "
            "'tabular_schema', 'text_minhash')",
            [name],
        ).fetchall()
    found: dict[str, set[str]] = {}
    for observer, version in rows:
        found.setdefault(observer, set()).add(version)
    return found


# ---------------------------------------------------------------------------
# 1. A capped table is read again, with the reader that counts every row.
# ---------------------------------------------------------------------------


def test_a_table_an_older_reader_capped_is_read_again_and_counted_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(
        tmp_path, {"HeLa_DMSO_001.csv": _table(450), "HeLa_notes.txt": "Plate read at 620 nm.\n"}
    )
    with monkeypatch.context() as older:
        _older_tabular(older)
        first = _dive(project, RUNS[1])
    assert first.chosen_because == {"unread": 2}
    # What the older reader left: the count stopped at the sample and said it was exact.
    assert _stored(project, "HeLa_DMSO_001.csv", "table.row_count") == [(OLD_CAP, "1")]

    second = _dive(project, RUNS[2])
    assert second.chosen_because == {"reader_newer": 1}
    assert second.reader_newer_by == {"tabular": 1}
    assert (second.examined, second.reader_newer_read) == (1, 1)
    # The new reading replaced the old: every row counted, by the version that counts them.
    assert _stored(project, "HeLa_DMSO_001.csv", "table.row_count") == [(450, NOW)]
    assert _stored(project, "HeLa_DMSO_001.csv", "table.row_count_is_exact") == [(1, NOW)]
    assert _readers(project, "HeLa_DMSO_001.csv")["tabular"] == {NOW}

    # And once only: the reading now carries the version of the reader that made it.
    third = _dive(project, RUNS[3])
    assert (third.examined, third.chosen_because, third.reader_newer_by) == (0, {}, {})


def test_the_journal_says_which_readers_chose_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1])
    _dive(project, RUNS[2])
    with open_catalog(project.path) as catalog:
        said = catalog.query(
            "SELECT json_extract_string(detail, '$.chosen_because.reader_newer'), "
            "json_extract_string(detail, '$.reader_newer_by.tabular') "
            "FROM journal_events WHERE event = 'run.started' AND run_id = ?",
            [RUNS[2]],
        ).fetchall()
    assert said == [("1", "1")]


# ---------------------------------------------------------------------------
# 2. A file whose reader did not change is not read again.
# ---------------------------------------------------------------------------


def test_a_changed_reader_reads_again_only_the_files_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The text file holds no reading by the table reader; that reader's change leaves it alone."""
    project = _project(
        tmp_path,
        {
            "HeLa_DMSO_001.csv": _table(450),
            "HeLa_DMSO_002.csv": _table(451),
            "HeLa_notes.txt": "Plate read at 620 nm.\n",
        },
    )
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1])
    before = _readers(project, "HeLa_notes.txt")

    second = _dive(project, RUNS[2])
    assert second.chosen_because == {"reader_newer": 2}
    assert second.examined == 2
    assert _readers(project, "HeLa_notes.txt") == before

    third = _dive(project, RUNS[3])
    assert (third.examined, third.chosen_because) == (0, {})


def test_a_reader_with_no_reading_in_the_pool_chooses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised version of the ND2 reader, in a pool it read no file of, chooses no file."""
    project = _project(
        tmp_path, {"HeLa_DMSO_001.csv": _table(300), "HeLa_notes.txt": "Plate read.\n"}
    )
    _dive(project, RUNS[1])
    with monkeypatch.context() as newer:
        newer.setattr(Nd2Extractor, "version", "2")
        again = _dive(project, RUNS[2])
    assert (again.examined, again.chosen_because) == (0, {})


def test_a_reader_changed_after_the_dive_reads_its_files_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case a fix produces: readings made at today's version, the reader's version raised."""
    project = _project(
        tmp_path, {"HeLa_DMSO_001.csv": _table(300), "HeLa_notes.txt": "Plate read.\n"}
    )
    _dive(project, RUNS[1])
    with monkeypatch.context() as newer:
        newer.setattr(TabularExtractor, "version", str(int(NOW) + 1))
        again = _dive(project, RUNS[2])
        assert (again.chosen_because, again.reader_newer_by) == (
            {"reader_newer": 1},
            {"tabular": 1},
        )
        assert _readers(project, "HeLa_DMSO_001.csv")["tabular"] == {str(int(NOW) + 1)}
        assert _dive(project, RUNS[3]).examined == 0


def test_a_reading_by_a_newer_reader_than_today_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project last dived by a build with a newer reader is not read back to an older one."""
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as newer:
        newer.setattr(TabularExtractor, "version", str(int(NOW) + 6))
        _dive(project, RUNS[1])
    again = _dive(project, RUNS[2])
    assert (again.examined, again.chosen_because) == (0, {})
    assert _readers(project, "HeLa_DMSO_001.csv")["tabular"] == {str(int(NOW) + 6)}


def test_a_reader_the_settings_do_not_permit_chooses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file is read again for a reader that would read it today, and this one would not."""
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(450)})
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1])
    project = project.save_settings(Settings(extraction=ExtractionSettings(adapters=("text",))))
    skipped = _dive(project, RUNS[2])
    assert (skipped.examined, skipped.chosen_because) == (0, {})
    # Permitted again, the file is read.
    project = project.save_settings(Settings())
    assert _dive(project, RUNS[3]).chosen_because == {"reader_newer": 1}


# ---------------------------------------------------------------------------
# 3. The budget still bounds the dive.
# ---------------------------------------------------------------------------


def test_the_budget_bounds_the_rereads_and_the_next_dive_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sizes that differ, so no digest is taken for a comparison and each file costs one read.
    project = _project(tmp_path, {f"HeLa_DMSO_00{n}.csv": _table(450 + n) for n in range(1, 6)})
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1])

    real = deepen_module.deepen_entry

    def one_second_each(*arguments: object, **keywords: object) -> object:
        outcome = real(*arguments, **keywords)  # type: ignore[arg-type]
        outcome.seconds = 1.0  # type: ignore[attr-defined]
        return outcome

    def current(project: Project) -> int:
        with open_catalog(project.path) as catalog:
            row = catalog.query(
                "SELECT count(DISTINCT entry_id) FROM observations "
                f"WHERE observer = 'tabular' AND observer_version = '{NOW}'"
            ).fetchone()
        return int(row[0])

    assert current(project) == 0
    with monkeypatch.context() as clocked:
        clocked.setattr(deepen_module, "deepen_entry", one_second_each)
        second = _dive(project, RUNS[2], budget=2.5)
    # Five were chosen; the dive stopped inside its budget, after the third second.
    assert second.chosen_because == {"reader_newer": 5}
    assert (second.examined, second.reader_newer_read) == (3, 3)
    assert second.budget is not None
    assert second.budget.unreached == 2
    assert current(project) == 3

    third = _dive(project, RUNS[3])
    assert third.chosen_because == {"reader_newer": 2}
    assert (third.examined, third.reader_newer_read) == (2, 2)
    assert current(project) == 5

    assert _dive(project, RUNS[4]).examined == 0


# ---------------------------------------------------------------------------
# A reading with no recorded version, and the order of versions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recorded", "today", "older"),
    [
        ("1", "2", True),
        ("2", "2", False),
        ("3", "2", False),
        ("9", "10", True),  # compared as numbers, not as text
        ("10", "9", False),
        ("2.1", "2.10", True),
        ("1", "1.0", False),
        ("", "3", True),
        (None, "3", True),
        ("", "1", False),
        ("  ", "2", True),
        ("beta", "3", True),  # in words against a number: read again once
        ("beta", "beta", False),
        ("beta+x", "beta", False),  # today's in words: never older, so never read again for good
        ("3", "beta", False),
        ("1", "", False),
        # A tag for the variant a reader ran (the text sketch's sample size) is not a version.
        ("1+4194304", "1", False),
        ("1+4194304", "2", True),
        ("2+4194304", "1", False),
    ],
)
def test_which_recorded_versions_are_older_than_the_readers_today(
    recorded: str | None, today: str, older: bool
) -> None:
    from ingence.scan.reuse import reader_version_is_older

    assert reader_version_is_older(recorded, today) is older


def test_a_reading_without_a_version_is_older_only_for_a_reader_that_has_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table reader is at its third version and the keyword reader at its first."""
    from ingence.scan.reuse import FIRST_READER_VERSION

    assert EXTRACTORS.get("keywords").version == FIRST_READER_VERSION
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(300)})
    with monkeypatch.context() as unversioned:
        unversioned.setattr(TabularExtractor, "version", "")
        unversioned.setattr(KeywordExtractor, "version", "")
        _dive(project, RUNS[1])
    assert _readers(project, "HeLa_DMSO_001.csv")["tabular"] == {""}
    assert _readers(project, "HeLa_DMSO_001.csv")["keywords"] == {""}

    again = _dive(project, RUNS[2])
    # Only the reader that has risen above its first version chose the file.
    assert again.reader_newer_by == {"tabular": 1}
    assert again.chosen_because == {"reader_newer": 1}
    # It was read again by every reader that reads a table, so none is unversioned now.
    assert _readers(project, "HeLa_DMSO_001.csv")["tabular"] == {NOW}


# ---------------------------------------------------------------------------
# A member of a container, and the depth.
# ---------------------------------------------------------------------------


def _zip_with(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_a_table_inside_an_archive_is_read_again_with_the_archive_that_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(
        tmp_path,
        {"HeLa_DMSO_001.zip": _zip_with({"plate_01.csv": _table(450).encode()})},
    )
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1])
    assert _stored(project, "plate_01.csv", "table.row_count") == [(OLD_CAP, "1")]
    # The archive's own reading is current: only its member's table reader changed.
    assert _readers(project, "HeLa_DMSO_001.zip")["archive"] == {EXTRACTORS.get("archive").version}

    second = _dive(project, RUNS[2])
    assert second.chosen_because == {"reader_newer": 1}
    assert second.reader_newer_by == {"tabular": 1}
    assert _stored(project, "plate_01.csv", "table.row_count") == [(450, NOW)]
    assert _dive(project, RUNS[3]).examined == 0


def test_a_dive_does_not_read_again_at_a_depth_shallower_than_the_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deep reading holds more than a standard one would, so a standard dive leaves it."""
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(450)})
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1], profile="deep")

    shallow = _dive(project, RUNS[2], profile="standard")
    assert (shallow.examined, shallow.chosen_because) == (0, {})
    assert _stored(project, "HeLa_DMSO_001.csv", "table.row_count") == [(OLD_CAP, "1")]

    deep = _dive(project, RUNS[3], profile="deep")
    assert deep.chosen_because == {"reader_newer": 1}
    assert _stored(project, "HeLa_DMSO_001.csv", "table.row_count") == [(450, NOW)]


def test_a_reading_whose_version_names_the_variant_it_ran_is_not_read_again(
    tmp_path: Path,
) -> None:
    """The text sketch records ``1+<sample size>`` when its sample is not the first one's. The
    second dive found that older than ``1`` and read the file again, for good."""
    project = _project(tmp_path, {"HeLa_notes.txt": "Plate read at 620 nm. " * 30})
    first = _dive(project, RUNS[1])
    assert first.chosen_because == {"unread": 1}
    assert "text_minhash" in _readers(project, "HeLa_notes.txt")
    for run in (RUNS[2], RUNS[3]):
        again = _dive(project, run)
        assert (again.examined, again.chosen_because) == (0, {})


# ---------------------------------------------------------------------------
# A file a reader opened by its content, and the readers that sign a file.
# ---------------------------------------------------------------------------


def test_a_zip_a_reader_opened_by_its_content_is_read_again_like_one_it_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive reader lists ``zip`` and not ``npz`` or ``jar``, and opens all three as ZIP."""
    project = _project(
        tmp_path,
        {
            "HeLa_stack_001.zip": _zip_with({"plate_01.csv": _table(300).encode()}),
            "HeLa_stack_002.npz": _zip_with({"plate_02.csv": _table(301).encode()}),
            "HeLa_tools_003.jar": _zip_with({"plate_03.csv": _table(302).encode()}),
        },
        # In this process, where the test changes the reader's version; its child process would
        # record the version the module was imported with.
        Settings(extraction=ExtractionSettings(use_subprocess_isolation=False)),
    )
    first = _dive(project, RUNS[1])
    assert first.chosen_because == {"unread": 3}
    for name in ("HeLa_stack_001.zip", "HeLa_stack_002.npz", "HeLa_tools_003.jar"):
        assert _readers(project, name)["archive"] == {ARCHIVE_NOW}, name

    bumped = str(int(ARCHIVE_NOW) + 1)
    with monkeypatch.context() as newer:
        newer.setattr(ArchiveExtractor, "version", bumped)
        second = _dive(project, RUNS[2])
        assert second.chosen_because == {"reader_newer": 3}
        assert second.reader_newer_by == {"archive": 3}
        for name in ("HeLa_stack_001.zip", "HeLa_stack_002.npz", "HeLa_tools_003.jar"):
            assert _readers(project, name)["archive"] == {bumped}, name
        assert _dive(project, RUNS[3]).examined == 0


def test_an_array_signed_by_an_older_method_is_signed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The array signature is a reader of the file's header: version 1 did not count dimensions."""
    numpy = pytest.importorskip("numpy")
    pool = tmp_path / "pool"
    pool.mkdir()
    numpy.save(pool / "HeLa_stack_001.npy", numpy.zeros((4, 5, 6), dtype=numpy.float32))
    project = Project.create(
        tmp_path / "project",
        created_at="t",
        roots=[pool],
        settings=Settings(extraction=ExtractionSettings(use_subprocess_isolation=False)),
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    with monkeypatch.context() as older:
        older.setattr(ArraySignature, "version", "1")
        _dive(project, RUNS[1])
    assert _readers(project, "HeLa_stack_001.npy")["array_signature"] == {"1"}

    second = _dive(project, RUNS[2])
    assert second.chosen_because == {"reader_newer": 1}
    assert second.reader_newer_by == {"array_signature": 1}
    assert _readers(project, "HeLa_stack_001.npy")["array_signature"] == {ARRAY_NOW}
    assert _dive(project, RUNS[3]).examined == 0


def test_a_table_whose_columns_an_older_method_signed_is_signed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(
        tmp_path, {"HeLa_DMSO_001.csv": _table(300), "HeLa_notes.txt": "Plate read at 620 nm.\n"}
    )
    with monkeypatch.context() as older:
        older.setattr(TabularSchema, "version", "1")
        _dive(project, RUNS[1])
    assert _readers(project, "HeLa_DMSO_001.csv")["tabular_schema"] == {"1"}

    second = _dive(project, RUNS[2])
    assert (second.chosen_because, second.reader_newer_by) == (
        {"reader_newer": 1},
        {"tabular_schema": 1},
    )
    assert _readers(project, "HeLa_DMSO_001.csv")["tabular_schema"] == {SCHEMA_NOW}
    assert _dive(project, RUNS[3]).examined == 0


def test_a_file_whose_new_reading_failed_is_not_counted_as_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, {"HeLa_DMSO_001.csv": _table(450)})
    with monkeypatch.context() as older:
        _older_tabular(older)
        _dive(project, RUNS[1])

    real = deepen_module.deepen_entry

    def fails(*arguments: object, **keywords: object) -> object:
        outcome = real(*arguments, **keywords)  # type: ignore[arg-type]
        outcome.outcome = ScanOutcome.CONTENT_ERROR  # type: ignore[attr-defined]
        return outcome

    with monkeypatch.context() as failing:
        failing.setattr(deepen_module, "deepen_entry", fails)
        second = _dive(project, RUNS[2])
    assert second.chosen_because == {"reader_newer": 1}
    # Chosen and tried, and the summary counts what was read: no file here.
    assert (second.examined, second.extracted, second.failed) == (1, 0, 1)
    assert second.reader_newer_read == 0


# ---------------------------------------------------------------------------
# What the command says.
# ---------------------------------------------------------------------------


def test_the_dive_says_how_many_files_it_read_again_and_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "HeLa_DMSO_001.csv").write_text(_table(450))
    (pool / "HeLa_notes.txt").write_text("Plate read at 620 nm.\n")
    runner = CliRunner()

    def run(*arguments: str) -> str:
        done = runner.invoke(app, list(arguments))
        assert done.exit_code == 0, done.output
        return " ".join(done.output.split())

    run("init", str(tmp_path / "project"), "--root", str(pool))
    _in_process(tmp_path / "project")
    run("index", "-p", str(tmp_path / "project"))
    with monkeypatch.context() as older:
        _older_tabular(older)
        first = run("dive", "-p", str(tmp_path / "project"), "--budget", "30")
    assert "again" not in first

    said = run("dive", "-p", str(tmp_path / "project"), "--budget", "30")
    assert "Read 1 file" in said
    assert "1 read again because the reader for their type changed" in said
    # The developer's words stay behind --verbose.
    assert "reader_newer" not in said
    assert "chosen because" not in said

    # Read again once, the next dive has nothing to read for it.
    third = run("dive", "-p", str(tmp_path / "project"), "--budget", "30")
    assert "Nothing left to read" in third


def test_verbose_names_the_reader_and_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "HeLa_DMSO_001.csv").write_text(_table(450))
    runner = CliRunner()

    def run(*arguments: str) -> str:
        done = runner.invoke(app, list(arguments))
        assert done.exit_code == 0, done.output
        return " ".join(done.output.split())

    run("init", str(tmp_path / "project"), "--root", str(pool))
    _in_process(tmp_path / "project")
    run("index", "-p", str(tmp_path / "project"))
    with monkeypatch.context() as older:
        _older_tabular(older)
        run("dive", "-p", str(tmp_path / "project"), "--budget", "30")
    detail = run("dive", "-p", str(tmp_path / "project"), "--budget", "30", "--verbose")
    assert "chosen because: 1 reader_newer" in detail
    assert "by reader: tabular 1; 1 of 1 read in this dive" in detail


# ---------------------------------------------------------------------------
# The two versions a reader carries agree.
# ---------------------------------------------------------------------------


def test_a_readers_registered_version_is_the_version_its_readings_carry() -> None:
    """The fingerprint reads the registered version and a reading records the reader's own. When
    they differ, a bump of one is not seen by the other (the archive reader's were a version
    apart), and a comparison that trusted the wrong one would read a file again for good."""
    for name in EXTRACTORS.names(available_only=True):
        assert EXTRACTORS.get(name).version == EXTRACTORS.declaration(name).version, name
    for name in IDENTITY_METHODS.names(available_only=True):
        assert IDENTITY_METHODS.get(name).version == IDENTITY_METHODS.declaration(name).version, (
            name
        )
