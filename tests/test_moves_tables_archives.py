"""Three behaviours that are easy to get wrong, each shown on invented data.

1. A moved file is not proved ``identical`` on a whole-file digest nobody has checked. A walk
   carries a dive's readings of a file that moved, the whole-file digest among them, to the file at
   its new path. Until a dive checks them, they are the departed file's readings. The grouping
   that proves a copy by that digest must not read them as the new file's, and the dive must check
   the strongest digest the readings hold, not the cheapest one. A whole-file digest a dive
   computed at the new path is the file's own and counts at once.
2. A table's row count is the count of the rows, or says it is a lower bound. It does not stop at
   the 200 rows sampled for column types and call itself exact.
3. Archive members are examined however deep the folders holding the archive lie. The limit on
   nesting counts containers inside containers, not folders.

Every name here is invented.
"""

from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from ingence.core.outcomes import IdentityScope
from ingence.core.project import Project
from ingence.core.schema import Entry, MovedBy, Observation
from ingence.core.settings import ExtractionSettings, HashMode, Settings
from ingence.core.tiers import ResolutionStatus
from ingence.extractors import load_builtins
from ingence.extractors.base import ExtractionContext
from ingence.extractors.limits import Limits
from ingence.extractors.tabular import TabularExtractor
from ingence.infer.run import run_inference
from ingence.scan.census import run_census
from ingence.scan.deepen import run_deepen
from ingence.scan.moves import UNCHECKED_MOVES
from ingence.storage.catalog import open_catalog

load_builtins()

RUNS = [f"run_20260101T00000{n}Z_{str(n) * 12}" for n in range(1, 10)]
#: An invented moment long before the walks, so a copy cannot be told from its file by its times.
LONG_AGO = 1_700_000_000 * 1_000_000_000
#: More than the two ends a sampled digest reads (64 KiB each), so a middle can differ unseen.
BEYOND_A_SAMPLE = 3 * 64 * 1024


def _settings(mode: HashMode) -> Settings:
    settings = Settings()
    return settings.model_copy(
        update={
            # Refusing copies off, so a different file alike in name, size and time is taken for
            # the departed one on every platform: the walk reads no bytes.
            "scan": settings.scan.model_copy(update={"moves_refuse_copies": False}),
            "hash": settings.hash.model_copy(update={"mode": mode}),
        }
    )


def _identical(project: Project) -> set[frozenset[str]]:
    """The sets of paths the whole-digest rule proved identical in the project's current run."""
    with open_catalog(project.path) as catalog:
        rows = catalog.query(
            "SELECT c.object_reference, e.relative_path FROM claims c "
            "JOIN entries e ON e.entry_id = c.subject "
            "WHERE c.rule_id = 'identical_by_content_digest' "
            "AND c.resolution_status = 'resolved' AND NOT e.is_deleted"
        ).fetchall()
    classes: dict[str, set[str]] = {}
    for key, path in rows:
        classes.setdefault(str(key), set()).add(str(path))
    return {frozenset(members) for members in classes.values() if len(members) > 1}


def _moved_by(project: Project, relative_path: str) -> str | None:
    with open_catalog(project.path) as catalog:
        row = catalog.query(
            "SELECT moved_by FROM entries WHERE relative_path = ? AND NOT is_deleted",
            [relative_path],
        ).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def _two_plates(base: Path, *, long_files: bool) -> Path:
    """Two plate tables holding the same bytes, in two batch folders."""
    pool = base / "pool"
    if long_files:
        half = "well,intensity\n" + "A1,204\n" * (BEYOND_A_SAMPLE // 14)
        body = f"{half}A1,1\n{half}"
    else:
        body = "well,intensity\n" + "A1,204\n" * 40
    for folder, name in (("batch_a", "plate_01.csv"), ("batch_b", "plate_02.csv")):
        (pool / folder).mkdir(parents=True)
        path = pool / folder / name
        path.write_text(body)
        os.utime(path, ns=(LONG_AGO, LONG_AGO))
    return pool


def _impostor(pool: Path, *, differ_in_the_middle: bool) -> Path:
    """A different file in the place of the one gone: alike in name, size and time, so a walk
    that reads no bytes takes it for the file that moved."""
    original = pool / "batch_a" / "plate_01.csv"
    text = original.read_text()
    size = len(text)
    original.unlink()
    (pool / "archive").mkdir()
    moved = pool / "archive" / "plate_01.csv"
    if differ_in_the_middle:
        middle = size // 2
        other = text[:middle] + "9" + text[middle + 1 :]
    else:
        other = ("well,intensity\nB2,17\n" * 400)[:size]
    assert len(other) == size
    moved.write_text(other)
    os.utime(moved, ns=(LONG_AGO, LONG_AGO))
    return moved


def test_a_moved_file_is_not_proved_identical_on_a_digest_nobody_checked(tmp_path: Path) -> None:
    """The walk gave the file at the new path the whole digest of the file that left. No dive has
    read it yet, so the digest says nothing about the bytes there, and no copy is proved on it."""
    pool = _two_plates(tmp_path, long_files=False)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.FULL)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    run_inference(project, run_id=RUNS[2], started_at="t")
    assert _identical(project) == {frozenset({"batch_a/plate_01.csv", "batch_b/plate_02.csv"})}

    _impostor(pool, differ_in_the_middle=False)
    run_census(project, run_id=RUNS[3], started_at="t")
    assert MovedBy(str(_moved_by(project, "archive/plate_01.csv"))) in UNCHECKED_MOVES
    run_inference(project, run_id=RUNS[4], started_at="t")
    assert _identical(project) == set(), "a digest read at another path proves nothing here"


def test_a_dive_checks_the_whole_digest_a_moved_file_carries_and_not_only_a_sample(
    tmp_path: Path,
) -> None:
    """The check compares the strongest digest the readings hold. If it compared the sampled one,
    the cheaper, a file holding both digests would keep the whole one unchecked. A copy differing
    only where a sample does not read would then pass and be proved identical to the other file."""
    pool = _two_plates(tmp_path, long_files=True)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.QUICK)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    project = project.save_settings(
        project.settings.model_copy(
            update={"hash": project.settings.hash.model_copy(update={"mode": HashMode.FULL})}
        )
    )
    run_deepen(project, run_id=RUNS[2], started_at="t", budget_seconds=60)
    run_inference(project, run_id=RUNS[3], started_at="t")
    assert _identical(project) == {frozenset({"batch_a/plate_01.csv", "batch_b/plate_02.csv"})}

    _impostor(pool, differ_in_the_middle=True)
    run_census(project, run_id=RUNS[4], started_at="t")
    run_deepen(project, run_id=RUNS[5], started_at="t", budget_seconds=60)
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.DIGEST_DIFFERS.value
    run_inference(project, run_id=RUNS[6], started_at="t")
    assert _identical(project) == set()


def test_a_moved_file_is_proved_identical_again_once_a_dive_checks_its_whole_digest(
    tmp_path: Path,
) -> None:
    """The other direction: a file that did move, with the bytes it had, is a copy of the other
    file, once a dive has compared its whole digest with the one it carried."""
    pool = _two_plates(tmp_path, long_files=False)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.FULL)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    (pool / "archive").mkdir()
    shutil.move(pool / "batch_a" / "plate_01.csv", pool / "archive" / "plate_01.csv")
    run_census(project, run_id=RUNS[2], started_at="t")
    run_inference(project, run_id=RUNS[3], started_at="t")
    assert _identical(project) == set(), "carried and unchecked"

    run_deepen(project, run_id=RUNS[4], started_at="t", budget_seconds=60)
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.WHOLE_DIGEST.value
    run_inference(project, run_id=RUNS[5], started_at="t")
    assert _identical(project) == {frozenset({"archive/plate_01.csv", "batch_b/plate_02.csv"})}


# ---------------------------------------------------------------------------
# 2. A table's row count.
# ---------------------------------------------------------------------------


def _context(path: Path, limits: Limits | None = None) -> ExtractionContext:
    return ExtractionContext(
        entry_id="entry_x",
        path=path,
        relative_path=path.name,
        extensions=frozenset({path.suffix}),
        size_bytes=path.stat().st_size,
        limits=limits or Limits(),
    )


def _values(path: Path, limits: Limits | None = None) -> dict[str, object]:
    result = TabularExtractor().extract(_context(path, limits))
    return {reading.key: reading.value for reading in result.readings}


def test_a_table_longer_than_the_sample_counts_every_row_it_read(tmp_path: Path) -> None:
    """The count reads past the 200 rows sampled for column types: all 450 rows are counted."""
    path = tmp_path / "plate_01.csv"
    path.write_text("well,intensity\n" + "".join(f"A{n},{n}\n" for n in range(1, 451)))
    values = _values(path)
    assert values["table.row_count_is_exact"] is True
    assert values["table.row_count"] == 450
    assert values["table.rows_sampled"] == 200


def test_a_table_cut_short_by_the_read_limit_states_a_lower_bound_and_not_a_count(
    tmp_path: Path,
) -> None:
    path = tmp_path / "plate_01.csv"
    path.write_text("well,intensity\n" + "".join(f"A{n},{n}\n" for n in range(1, 2001)))
    values = _values(path, Limits(max_bytes_read=4000))
    assert values["table.row_count_is_exact"] is False
    assert "table.row_count" not in values
    at_least = values["table.row_count_at_least"]
    assert isinstance(at_least, int)
    assert 200 < at_least < 2000


def test_a_row_count_is_stored_beside_its_exactness_by_a_dive(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_01.csv").write_text(
        "well,intensity\n" + "".join(f"A{n},{n}\n" for n in range(1, 301))
    )
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    with open_catalog(project.path) as catalog:
        stored = dict(
            catalog.query(
                "SELECT o.key, coalesce(CAST(o.value_integer AS VARCHAR), "
                "CAST(o.value_boolean AS VARCHAR)) FROM observations o "
                "JOIN entries e ON e.entry_id = o.entry_id "
                "WHERE e.name = 'plate_01.csv' AND o.key LIKE 'table.row%'"
            ).fetchall()
        )
    assert stored["table.row_count"] == "300"
    assert stored["table.row_count_is_exact"] in {"true", "True"}


# ---------------------------------------------------------------------------
# 3. Archive members, however deep the folders.
# ---------------------------------------------------------------------------


def _zip_with(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _members_found(base: Path, folders: tuple[str, ...]) -> dict[str, int]:
    """Dive a pool holding one archive at the end of ``folders``, with a table inside an archive
    inside it. Return how many member entries and nested members were recovered."""
    pool = base / "pool"
    where = pool.joinpath(*folders)
    where.mkdir(parents=True)
    inner = _zip_with({"plate_01.csv": b"well,intensity\nA1,204\nA2,199\n"})
    (where / "HeLa_DMSO_001.zip").write_bytes(
        _zip_with({"notes.txt": b"Plate read at 620 nm.\n", "inner.zip": inner})
    )
    settings = Settings(extraction=ExtractionSettings(enabled=True))
    project = Project.create(base / "project", created_at="t", roots=[pool], settings=settings)
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    with open_catalog(project.path) as catalog:
        names = {
            str(row[0])
            for row in catalog.query(
                "SELECT name FROM entries WHERE entry_kind = 'derived' AND NOT is_deleted"
            ).fetchall()
        }
        columns = catalog.query(
            "SELECT count(*) FROM observations o JOIN entries e ON e.entry_id = o.entry_id "
            "WHERE e.name = 'plate_01.csv' AND e.entry_kind = 'derived' "
            "AND o.key = 'table.column_names'"
        ).fetchone()
    return {"members": len(names), "nested_table_read": int(columns[0]) if columns else 0}


@pytest.mark.parametrize("folders", [(), ("2025",), ("2025", "HeLa", "plate_01", "run_a", "scans")])
def test_an_archive_is_opened_at_any_depth_of_folders(
    tmp_path: Path, folders: tuple[str, ...]
) -> None:
    found = _members_found(tmp_path, folders)
    assert found["members"] == 3, "the notes, the inner archive and the table inside it"
    assert found["nested_table_read"] == 1, "the table inside the inner archive was examined"


# ---------------------------------------------------------------------------
# The grouping predicate, on its own: which moves leave a whole-file digest unchecked.
# ---------------------------------------------------------------------------


def _digest(scope: IdentityScope | None, *, run: str = "r") -> Observation:
    return Observation(
        observation_id=f"o:digest:{run}",
        entry_id="e",
        run_id=run,
        observer="blake3_whole",
        observer_version="1",
        observed_at="t",
        key="identity.blake3_whole",
        value_text="abc123",
        identity_method="blake3_whole",
        identity_method_version="1",
        identity_scope=scope,
    )


def _moved_entry(moved_by: MovedBy | None) -> Entry:
    moved = {} if moved_by is None else {"moved_from": "e_before", "moved_by": moved_by}
    return Entry(
        entry_id="e",
        root_id="root",
        relative_path="archive/plate_01.csv",
        name="plate_01.csv",
        depth=1,
        entry_type="file",
        extension=".csv",
        compound_extension=".csv",
        first_seen_run="r",
        last_seen_run="r",
        **moved,
    )


@pytest.mark.parametrize(
    ("moved_by", "read"),
    [
        (None, True),
        (MovedBy.WHOLE_DIGEST, True),
        (MovedBy.COPIED_SAME_BYTES, True),
        (MovedBy.DIGEST_DIFFERS, True),
        (MovedBy.SAME_FILE, False),
        (MovedBy.BIRTH_TIME, False),
        (MovedBy.NAME_SIZE_TIME, False),
        (MovedBy.NEIGHBOURS, False),
        (MovedBy.COPIED, False),
        (MovedBy.SAMPLED_DIGEST, False),
    ],
)
def test_the_content_grouping_reads_a_whole_digest_only_where_no_unchecked_move_carried_it(
    moved_by: MovedBy | None, read: bool
) -> None:
    from ingence.infer.subjects import EntryObservations
    from ingence.rulebook.predicates.group import SameContentDigest

    predicate = SameContentDigest()
    parameters = {"digest_algorithm": "blake3_whole"}
    subject = EntryObservations(
        entry=_moved_entry(moved_by), observations=(_digest(IdentityScope.WHOLE),)
    )
    key = predicate.evaluate(subject, parameters)
    assert (key is not None) is read
    if not read:
        said = predicate.explain_abstention(subject, parameters)
        assert said.status is ResolutionStatus.UNEXAMINED
        assert "has not been checked since" in said.reason
    # A sampled digest only nominates, and is read wherever it lies.
    sampled = EntryObservations(
        entry=_moved_entry(moved_by), observations=(_digest(IdentityScope.SAMPLED),)
    )
    assert predicate.evaluate(sampled, parameters) is not None
    # A whole digest a later run computed at this path is the file's own, whatever the move says.
    fresh = EntryObservations(
        entry=_moved_entry(moved_by),
        observations=(_digest(IdentityScope.WHOLE, run="a_later_dive"),),
    )
    assert predicate.evaluate(fresh, parameters) is not None


# ---------------------------------------------------------------------------
# A digest a dive computed at the new path is the file's own.
# ---------------------------------------------------------------------------


def _with_mode(project: Project, mode: HashMode) -> Project:
    hashing = project.settings.hash.model_copy(update={"mode": mode})
    return project.save_settings(project.settings.model_copy(update={"hash": hashing}))


def test_a_whole_digest_a_dive_took_at_the_new_path_counts_at_once(tmp_path: Path) -> None:
    """A move confirmed by a sample carried no whole digest. The first dive to compute one, at the
    file's new path, is the first to prove the copy: it does not wait for a second dive."""
    pool = _two_plates(tmp_path, long_files=False)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.QUICK)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    (pool / "archive").mkdir()
    shutil.move(pool / "batch_a" / "plate_01.csv", pool / "archive" / "plate_01.csv")
    run_census(project, run_id=RUNS[2], started_at="t")
    run_deepen(project, run_id=RUNS[3], started_at="t", budget_seconds=60)
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.SAMPLED_DIGEST.value

    project = _with_mode(project, HashMode.FULL)
    run_deepen(project, run_id=RUNS[4], started_at="t", budget_seconds=60)
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.SAMPLED_DIGEST.value
    run_inference(project, run_id=RUNS[5], started_at="t")
    assert _identical(project) == {frozenset({"archive/plate_01.csv", "batch_b/plate_02.csv"})}


def test_a_whole_digest_of_another_method_version_is_replaced_by_one_taken_at_the_new_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the whole-file method changes version, the carried digest cannot be compared with the
    file's own. If the dive only compared the carried digest, the file would be stuck: read whole
    at every dive, the result discarded, and its copy claim refused for good. So the dive reads the
    file again, and the file's own digest counts."""
    from ingence.identity.bytes import WholeDigest

    pool = _two_plates(tmp_path, long_files=False)
    # The read workers take digests in their own processes, where the patched version below
    # never reaches. Read in this process instead.
    settings = _settings(HashMode.FULL)
    settings = settings.model_copy(
        update={
            "extraction": settings.extraction.model_copy(update={"use_subprocess_isolation": False})
        }
    )
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool], settings=settings)
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    (pool / "archive").mkdir()
    shutil.move(pool / "batch_a" / "plate_01.csv", pool / "archive" / "plate_01.csv")
    run_census(project, run_id=RUNS[2], started_at="t")

    monkeypatch.setattr(WholeDigest, "version", "2")
    run_deepen(project, run_id=RUNS[3], started_at="t", budget_seconds=60)
    assert MovedBy(str(_moved_by(project, "archive/plate_01.csv"))) in UNCHECKED_MOVES
    run_inference(project, run_id=RUNS[4], started_at="t")
    assert _identical(project) == {frozenset({"archive/plate_01.csv", "batch_b/plate_02.csv"})}
    with open_catalog(project.path) as catalog:
        versions = catalog.query(
            "SELECT o.observer_version FROM observations o JOIN entries e "
            "ON e.entry_id = o.entry_id WHERE e.relative_path = 'archive/plate_01.csv' "
            "AND o.identity_method = 'blake3_whole'"
        ).fetchall()
    assert versions == [("2",)], "the carried digest of the old version is gone"


def _as_an_older_build_checked_moves(
    readable: object, held: dict[str, dict[str, str]], **rest: object
) -> dict[str, object]:
    """The move check of an older build: the cheapest digest the readings hold decides, so a
    sample alone confirms a move that carried a whole digest."""
    from ingence.scan.deepen import UNREAD_MOVES, MoveCheck

    check = rest["check"]
    found: dict[str, object] = {}
    for entry in readable:  # type: ignore[attr-defined]
        carried = held.get(entry.entry_id, {})
        kind = next((name for name in ("sampled", "whole") if carried.get(name)), None)
        if entry.moved_by not in UNREAD_MOVES or kind is None:
            continue
        now = check(entry, kind)  # type: ignore[operator]
        if now is not None:
            found[entry.entry_id] = MoveCheck(digest=kind, same=now[0] == carried[kind])
    return found


def test_a_move_an_older_build_confirmed_by_a_sample_is_not_trusted_after_a_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a project an older build made, a file moved and changed in its middle (size and time
    kept) is confirmed by its sample while a whole digest of the old bytes is carried. A walk
    before the next dive must keep that marker, or the old digest is read as the new file's and
    the copy claim comes back. No claim holds at any point, and the dive refutes the move."""
    from ingence.scan import deepen

    pool = _two_plates(tmp_path, long_files=True)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.QUICK)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    project = _with_mode(project, HashMode.FULL)
    run_deepen(project, run_id=RUNS[2], started_at="t", budget_seconds=60)
    (pool / "archive").mkdir()
    moved = pool / "archive" / "plate_01.csv"
    shutil.move(pool / "batch_a" / "plate_01.csv", moved)
    text = moved.read_text()
    middle = len(text) // 2
    moved.write_text(text[:middle] + "9" + text[middle + 1 :])
    os.utime(moved, ns=(LONG_AGO, LONG_AGO))
    run_census(project, run_id=RUNS[3], started_at="t")
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.SAME_FILE.value

    with monkeypatch.context() as older:
        older.setattr(deepen, "_check_moves", _as_an_older_build_checked_moves)
        run_deepen(project, run_id=RUNS[4], started_at="t", budget_seconds=60)
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.SAMPLED_DIGEST.value

    # The usual order: a walk, then inference, then a dive.
    run_census(project, run_id=RUNS[5], started_at="t")
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.SAMPLED_DIGEST.value
    run_inference(project, run_id=RUNS[6], started_at="t")
    assert _identical(project) == set()
    run_deepen(project, run_id=RUNS[7], started_at="t", budget_seconds=60)
    assert _moved_by(project, "archive/plate_01.csv") == MovedBy.DIGEST_DIFFERS.value
    run_inference(project, run_id=RUNS[8], started_at="t")
    assert _identical(project) == set()


# ---------------------------------------------------------------------------
# The compare tier that says "the same bytes".
# ---------------------------------------------------------------------------


def test_a_comparison_does_not_call_an_unchecked_carried_digest_the_same_bytes(
    tmp_path: Path,
) -> None:
    import duckdb

    from ingence.compare.match import Side, attach_both, match_files

    pool = _two_plates(tmp_path, long_files=False)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.FULL)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    _impostor(pool, differ_in_the_middle=False)
    run_census(project, run_id=RUNS[2], started_at="t")
    run_deepen(project, run_id=RUNS[3], started_at="t", budget_seconds=60)

    def matched(after: str) -> dict[str, int]:
        connection = duckdb.connect()
        attach_both(connection, Side(project.path, RUNS[1]), Side(project.path, after))
        return dict(match_files(connection).matched)

    walked = matched(RUNS[2])
    assert walked.get("bytes", 0) == 0, "the digest was read from the file that left"
    assert walked.get("named") == 1
    # The dive found other bytes there: still not the same bytes.
    assert matched(RUNS[3]).get("bytes", 0) == 0


def test_a_comparison_calls_a_checked_move_the_same_bytes(tmp_path: Path) -> None:
    import duckdb

    from ingence.compare.match import Side, attach_both, match_files

    pool = _two_plates(tmp_path, long_files=False)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[pool], settings=_settings(HashMode.FULL)
    )
    run_census(project, run_id=RUNS[0], started_at="t")
    run_deepen(project, run_id=RUNS[1], started_at="t", budget_seconds=60)
    (pool / "archive").mkdir()
    shutil.move(pool / "batch_a" / "plate_01.csv", pool / "archive" / "plate_01.csv")
    run_census(project, run_id=RUNS[2], started_at="t")
    run_deepen(project, run_id=RUNS[3], started_at="t", budget_seconds=60)
    connection = duckdb.connect()
    attach_both(connection, Side(project.path, RUNS[1]), Side(project.path, RUNS[3]))
    assert match_files(connection).matched.get("bytes") == 1


# ---------------------------------------------------------------------------
# The move check, path by path.
# ---------------------------------------------------------------------------


def _checked(
    moved_by: MovedBy,
    answers: dict[str, tuple[str, str] | None],
    *,
    spend_after: str | None = None,
    carried: dict[str, str] | None = None,
) -> tuple[list[str], object, set[str]]:
    """Run the move check on one invented entry. Return the digests computed, the verdict and the
    entries whose whole digest was of another method version."""
    from ingence.scan.deepen import Budget, _check_moves

    entry = Entry(
        entry_id="new",
        root_id="r",
        relative_path="archive/plate_01.csv",
        name="plate_01.csv",
        size_bytes=40,
        first_seen_run="r",
        last_seen_run="r",
        moved_from="old",
        moved_by=moved_by,
    )
    held = {"new": carried or {"sampled": "s", "whole": "w"}}
    budget = Budget(granted_seconds=60)
    calls: list[str] = []
    stale: set[str] = set()

    def check(_: Entry, kind: str) -> tuple[str, str] | None:
        calls.append(kind)
        if spend_after == kind:
            budget.spend(1000)
        return answers[kind]

    found = _check_moves(
        [entry],
        held,
        versions={(key, kind): "1" for key in held for kind in held[key]},
        enabled=True,
        check=check,
        budget=budget,
        token=None,
        stale=stale,
    )
    return calls, found.get("new"), stale


def test_a_move_a_sample_confirmed_is_checked_again_by_its_whole_digest_alone() -> None:
    from ingence.scan.deepen import MoveCheck

    calls, verdict, _ = _checked(
        MovedBy.SAMPLED_DIGEST, {"sampled": ("s", "1"), "whole": ("w", "1")}
    )
    assert calls == ["whole"], "the sample is not read again"
    assert verdict == MoveCheck(digest="whole", same=True)
    _, refuted, _ = _checked(MovedBy.SAMPLED_DIGEST, {"sampled": ("s", "1"), "whole": ("x", "1")})
    assert refuted == MoveCheck(digest="whole", same=False)
    # A sample-confirmed move with no whole digest carried has nothing more to check.
    calls, verdict, _ = _checked(MovedBy.SAMPLED_DIGEST, {}, carried={"sampled": "s"})
    assert (calls, verdict) == ([], None)


def test_a_whole_digest_of_another_method_version_confirms_nothing_and_is_named() -> None:
    calls, verdict, stale = _checked(
        MovedBy.NAME_SIZE_TIME, {"sampled": ("s", "1"), "whole": ("w", "2")}
    )
    assert calls == ["sampled", "whole"]
    assert verdict is None, "a sample alone does not confirm a move that carried a whole digest"
    assert stale == {"new"}
    # Where the sample differs, the move is refuted without reading the whole file.
    calls, verdict, stale = _checked(MovedBy.NAME_SIZE_TIME, {"sampled": ("x", "1")})
    assert calls == ["sampled"]
    assert verdict is not None
    assert stale == set()


def test_a_budget_that_runs_out_between_the_sample_and_the_whole_digest_confirms_nothing() -> None:
    calls, verdict, stale = _checked(
        MovedBy.NAME_SIZE_TIME,
        {"sampled": ("s", "1"), "whole": ("w", "1")},
        spend_after="sampled",
    )
    assert calls == ["sampled"]
    assert (verdict, stale) == (None, set())


# ---------------------------------------------------------------------------
# Row counts: an empty line is no row.
# ---------------------------------------------------------------------------


def test_empty_lines_are_not_counted_as_rows(tmp_path: Path) -> None:
    path = tmp_path / "plate_01.csv"
    path.write_text("well,intensity\nA1,1\n\nA2,2\n\n\n")
    values = _values(path)
    assert values["table.row_count"] == 2
    assert values["table.rows_sampled"] == 2
    assert "table.ragged_rows" not in values
