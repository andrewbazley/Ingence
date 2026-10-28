"""The journal records everything, and never fails a run by trying."""

from __future__ import annotations

from ingence.core.schema import JournalEvent
from ingence.storage.index_store import read_table
from ingence.storage.journal import Journal, NullJournal, open_journal
from ingence.storage.layout import table_for


def test_events_are_written_as_queryable_rows(tmp_path):
    journal = open_journal(tmp_path, "run_1", enabled=True)
    journal.record("census", "run.started", roots=2)
    journal.record("census", "reused", entry_id="entry_x", relative_path="a/b.txt")
    assert journal.close() == 2

    table = read_table(tmp_path, table_for(JournalEvent))
    rows = table.to_pylist()
    assert [row["event"] for row in rows] == ["run.started", "reused"]
    assert rows[0]["detail"] == '{"roots":2}'
    assert rows[1]["relative_path"] == "a/b.txt"


def test_the_sequence_gives_a_total_order(tmp_path):
    """Several events share a timestamp at these rates. The sequence does not."""
    journal = Journal(tmp_path, "run_1")
    for index in range(20):
        journal.record("census", f"event_{index}")
    journal.close()
    rows = read_table(tmp_path, table_for(JournalEvent)).to_pylist()
    assert [row["sequence"] for row in rows] == list(range(1, 21))


def test_a_timed_block_records_a_start_a_finish_and_a_duration(tmp_path):
    journal = Journal(tmp_path, "run_1")
    with journal.timed("census", "directory", relative_path="a"):
        pass
    journal.close()
    rows = read_table(tmp_path, table_for(JournalEvent)).to_pylist()
    assert [row["event"] for row in rows] == ["directory.started", "directory.finished"]
    assert rows[1]["duration_ns"] is not None


def test_a_failure_inside_a_timed_block_is_recorded_and_re_raised(tmp_path):
    """A log that only records successes is useless exactly when it is needed."""
    journal = Journal(tmp_path, "run_1")
    try:
        with journal.timed("census", "directory"):
            raise ValueError("the share went away")
    except ValueError:
        pass
    journal.close()
    rows = read_table(tmp_path, table_for(JournalEvent)).to_pylist()
    assert rows[-1]["event"] == "directory.failed"
    assert "the share went away" in rows[-1]["detail"]


def test_journalling_never_raises(tmp_path):
    """A diagnostic that can fail a run is precisely backwards."""
    journal = Journal(tmp_path / "does" / "not" / "exist", "run_1")

    class Unserialisable:
        pass

    journal.record("census", "odd", value=Unserialisable())
    assert journal.write_failures >= 1


def test_the_null_journal_is_a_no_op_not_a_none(tmp_path):
    """Scattering None checks through the scan is where somebody forgets one."""
    journal = open_journal(tmp_path, "run_1", enabled=False)
    assert isinstance(journal, NullJournal)
    journal.record("census", "ignored")
    with journal.timed("census", "ignored"):
        pass
    assert journal.close() == 0
    assert not list(tmp_path.glob("*.parquet"))
